"""Independent PSP games reconcile offline changes without replacing shared cards."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from save_core.backends.ps1 import PS1Backend, vmp
from save_core.backends.ps1.card import Card
from save_core.game_sync import game_contents, game_fingerprint, plan_game_copy
from save_core.save_copy import apply_copy
from save_web.service import Service
from test_ftp import vmp as container
from test_ftp_write import WritableFTP
from test_ps1 import fixture, payload

A, B, OTHER = 'BASLUS-12345A', 'BASLUS-23456B', 'BASLUS-34567C'
GA, GB = 'SLUS-12345', 'SLUS-23456'


class GameSyncTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);local=self.root/'local';local.mkdir()
        self.shared=local/'shared.mcr';self.replica=local/'replica.mcr'
        self.original=fixture([(A,(1,),payload('A old')),(B,(2,),payload('B old')),(OTHER,(3,),payload('Other'))])
        self.shared.write_bytes(self.original);self.replica.write_bytes(self.original)
        self.paths=('/PSP/SAVEDATA/SLUS12345/SCEVMC0.VMP','/PSP/SAVEDATA/SLUS23456/SCEVMC0.VMP')
        self.state=dict(online=True,reads=[],files={path:container(self.original) for path in self.paths})
        mock=patch('save_web.transports.FTP',side_effect=lambda **kwargs:WritableFTP(self.state,**kwargs))
        mock.start();self.addCleanup(mock.stop)
        self.service=Service(self.root/'data');self.addCleanup(self.service.close)
        self.service.sync.settle_seconds=0
        self.desktop=self.service.create_device(dict(name='Desktop',root_path=str(local)))
        self.service.save_location(self.desktop['id'],dict(name='Shared cards',path='.',suffix='.mcr',backend='ps1',writable=True))
        self.psp=self.service.create_device(dict(name='PSP',transport='ftp',root_path='/',ftp_host='psp.local'))
        self.service.save_location(self.psp['id'],dict(name='PS1 games',path='PSP/SAVEDATA',suffix='.vmp',backend='ps1',writable=True))
        self.scan();self.scan()
        cards=self.service.inventory()['cards']
        shared=[c['id'] for c in cards if c['filename'].endswith('.mcr')]
        self.portable=sorted([c for c in cards if c['filename'].endswith('.VMP')],key=lambda c:c['filename'])
        self.group=self.service.sync.configure(dict(name='Shared card 1',card_ids=shared))
        self.tick()
        self.assertEqual(self.service.sync.group(self.group['id'])['status'],'synced')

    def scan(self):
        for device in (self.desktop,self.psp):
            job=self.service.queue_scan(device['id']);self.service.run_scan(job['id'])

    def tick(self):
        self.service.sync.next_tick=0;self.service.sync.tick()

    def configure(self):
        links=[]
        for game,card in zip((GA,GB),self.portable):
            links.append(self.service.game_sync.configure(dict(game_key=game,group_id=self.group['id'],portable_card_id=card['id'],initial_source='shared')))
        self.tick();self.scan();self.scan();self.tick()
        self.assertTrue(all(self.service.game_sync.link(link['id'])['status']=='synced' for link in links))
        return links

    def converge(self):
        for _ in range(10):
            self.scan();self.scan();self.tick()
            links=self.service.db.rows('SELECT status FROM game_sync_links')
            self.assertFalse(any(x['status']=='conflict' for x in links))
            if all(x['status']=='synced' for x in links) and self.service.sync.group(self.group['id'])['status']=='synced':
                return
        self.fail(str(self.service.db.rows('SELECT status,message FROM game_sync_links')))

    def test_initialization_removes_mirrored_other_games_and_preserves_original_backup(self):
        self.configure()
        for path,name in zip(self.paths,(A,B)):
            data=self.state['files'][path]
            self.assertTrue(vmp.verify(data))
            self.assertEqual(set(Card(PS1Backend.card_data(data)).entries),{name})
        self.assertEqual(self.shared.read_bytes(),self.original)
        transfers=self.service.db.rows("SELECT * FROM transfers WHERE action='Automatic game sync'")
        self.assertEqual(len(transfers),2)
        for transfer in transfers:
            self.assertEqual(Path(transfer['backup']).read_bytes(),container(self.original))

    def test_initial_link_does_not_rewrite_an_already_matching_dedicated_card(self):
        target=self.root/'dedicated.vmp'
        original=container(fixture([(A,(4,),payload('A old'))]))
        target.write_bytes(original)
        plan=plan_game_copy(self.shared,target,GA,initialize_portable=True)
        self.assertEqual(plan.action,'Already identical')
        self.assertEqual(plan.candidate,original)
        self.assertEqual(target.read_bytes(),original)

    def test_two_offline_psp_games_and_an_unrelated_shared_edit_all_survive(self):
        self.configure()
        for path,name,label in zip(self.paths,(A,B),('A new','B new')):
            self.state['files'][path]=vmp.wrap(fixture([(name,(1,),payload(label))]),self.state['files'][path])
        self.state['online']=False
        # Change a different game on the shared device before the PSP reconnects.
        card=Card(self.shared.read_bytes());meta=card.metadata(card.entries[OTHER]);card.delete(OTHER);card.put(OTHER,payload('Other new'),meta)
        self.shared.write_bytes(card.data)
        for _ in range(3):self.scan();self.scan();self.tick()
        self.state['online']=True
        self.converge()
        card=Card(self.shared.read_bytes())
        for name,label in ((A,'A new'),(B,'B new'),(OTHER,'Other new')):
            self.assertEqual(card.payload(card.entries[name]),payload(label))
        self.assertEqual(self.shared.read_bytes(),self.replica.read_bytes())
        self.assertTrue(all(vmp.verify(self.state['files'][path]) for path in self.paths))

    def test_same_game_changes_on_both_devices_require_source_selection(self):
        links=self.configure()
        card=Card(self.shared.read_bytes());meta=card.metadata(card.entries[A]);card.delete(A);card.put(A,payload('A shared'),meta);self.shared.write_bytes(card.data)
        self.state['files'][self.paths[0]]=vmp.wrap(fixture([(A,(1,),payload('A PSP'))]),self.state['files'][self.paths[0]])
        for _ in range(4):self.scan();self.scan();self.tick()
        self.assertEqual(self.service.game_sync.link(links[0]['id'])['status'],'conflict')
        self.service.game_sync.update(links[0]['id'],{'source':'portable'})
        self.converge()
        self.assertEqual(game_contents(self.shared.read_bytes(),GA)[A][0],payload('A PSP'))

    def test_offline_shared_device_does_not_block_available_game_sync(self):
        offline=self.root/'offline';offline.mkdir()
        (offline/'card.mcr').write_bytes(self.original)
        device=self.service.create_device(dict(name='Offline console',root_path=str(offline)))
        location=self.service.save_location(device['id'],dict(name='PS1',path='.',suffix='.mcr',backend='ps1',writable=True))
        for _ in range(2):
            job=self.service.queue_scan(device['id']);self.service.run_scan(job['id'])
        card=self.service.db.one('SELECT id FROM cards WHERE location_id=?',(location['id'],))
        ids=[m['card_id'] for m in self.service.sync.members(self.group)]+[card['id']]
        self.group=self.service.sync.configure(dict(group_id=self.group['id'],name='Shared card 1',card_ids=ids))
        self.tick()
        with self.service.db.connect() as db:
            db.execute("UPDATE locations SET status='offline' WHERE id=?",(location['id'],))
        self.tick()
        self.assertEqual(self.service.sync.group(self.group['id'])['status'],'pending')
        links=self.configure()
        self.state['files'][self.paths[0]]=vmp.wrap(fixture([(A,(1,),payload('PSP current'))]),self.state['files'][self.paths[0]])
        self.scan();self.scan();self.tick()
        self.assertTrue(any(m['pending_scan'] for m in self.service.sync.members(self.group)))
        self.assertFalse(self.service.game_sync.shared_ready(self.service.sync.group(self.group['id'])))
        for _ in range(5):self.scan();self.scan();self.tick()
        self.assertTrue(all(self.service.game_sync.link(link['id'])['status']=='synced' for link in links))
        self.assertEqual(game_contents(self.shared.read_bytes(),GA)[A][0],payload('PSP current'))
        self.assertEqual(self.shared.read_bytes(),self.replica.read_bytes())
        self.assertEqual((offline/'card.mcr').read_bytes(),self.original)
        self.assertEqual(self.service.sync.group(self.group['id'])['status'],'pending')

    def test_deleting_one_games_saves_preserves_the_other_games(self):
        self.configure()
        from save_core.backends.ps1.card import blank_card
        self.state['files'][self.paths[0]]=vmp.wrap(blank_card(),self.state['files'][self.paths[0]])
        self.converge()
        card=Card(self.shared.read_bytes())
        self.assertNotIn(A,card.entries)
        self.assertEqual(card.payload(card.entries[B]),payload('B old'))
        self.assertEqual(card.payload(card.entries[OTHER]),payload('Other'))

    def test_per_game_link_cannot_also_be_whole_card_sync_member(self):
        self.configure()
        with self.assertRaisesRegex(ValueError,'per-game'):
            self.service.sync.configure(dict(name='Wrong whole group',card_ids=[c['id'] for c in self.portable]))
        self.service.game_sync.update(1,{'enabled':False})
        self.assertFalse(self.service.game_sync.link(1)['enabled'])

    def test_initial_psp_choice_imports_progress_then_prepares_a_dedicated_card(self):
        newer=fixture([(A,(1,),payload('PSP current')),(B,(2,),payload('B old')),(OTHER,(3,),payload('Other'))])
        self.state['files'][self.paths[0]]=container(newer)
        self.scan();self.scan()
        self.service.game_sync.configure(dict(game_key=GA,group_id=self.group['id'],portable_card_id=self.portable[0]['id'],initial_source='portable'))
        self.converge()
        self.assertEqual(game_contents(self.shared.read_bytes(),GA)[A][0],payload('PSP current'))
        self.assertEqual(set(Card(PS1Backend.card_data(self.state['files'][self.paths[0]])).entries),{A})
        self.assertEqual(game_contents(self.shared.read_bytes(),GB)[B][0],payload('B old'))

    def test_insufficient_space_never_evicts_another_games_saves(self):
        full=fixture([(A,(1,),payload('A old')),(OTHER,tuple(range(2,16)),payload('Other',blocks=14))])
        self.shared.write_bytes(full)
        source=self.root/'larger.mcr';source.write_bytes(fixture([(A,(1,2),payload('A larger',blocks=2))]))
        with self.assertRaisesRegex(ValueError,'Not enough free space'):
            plan_game_copy(source,self.shared,GA)
        self.assertEqual(self.shared.read_bytes(),full)

    def test_core_rejects_a_stale_destination_without_touching_other_games(self):
        source=self.root/'source.mcr';source.write_bytes(fixture([(A,(1,),payload('A new'))]))
        plan=plan_game_copy(source,self.shared,GA)
        changed=fixture([(A,(1,),payload('Played elsewhere')),(OTHER,(2,),payload('Other changed'))]);self.shared.write_bytes(changed)
        with self.assertRaisesRegex(ValueError,'changed'):
            apply_copy(plan,self.root/'backups')
        self.assertEqual(self.shared.read_bytes(),changed)
        self.assertFalse((self.root/'backups').exists())
