"""PS1 web operations, sync isolation, icons and backend migration."""

import io
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from save_core.backends.ps1.card import Card, blank_card
from save_web.database import Database, SCHEMA
from save_web.server import Application
from save_web.service import Service
from test_ps1 import fixture, payload
from test_save_copy import generic_save
from test_save_merge import card_file


class PS1WebTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.service=Service(self.root/'data');self.addCleanup(lambda:self.service.close())
        self.service.sync.settle_seconds=0
        self.app=Application(self.service)
        self.devices=[];self.cards=[];self.locations=[];self.ids=[]
        for name,backend in [('ps1a','ps1'),('ps1b','ps1'),('ps2a','ps2'),('ps2b','ps2')]:
            folder=self.root/name;folder.mkdir()
            path=folder/('card.mcr' if backend=='ps1' else 'card.ps2')
            if backend=='ps1':path.write_bytes(fixture([('SAME',(3,),payload())]) if name=='ps1a' else blank_card())
            else:card_file(path,[generic_save('SAME',b'PS2 remains unchanged')],True)
            device=self.service.create_device({'name':name})
            location=self.service.save_location(device['id'],dict(name=name,path=str(folder),backend=backend,
                                                                  suffix='*' if backend=='ps1' else '.ps2',writable=True))
            self.devices.append(device['id']);self.cards.append(path);self.locations.append(location)
            self.scan(len(self.devices)-1)
            self.ids.append(self.service.db.one('SELECT id FROM cards WHERE location_id=?',(location['id'],))['id'])

    def scan(self,index):
        job=self.service.queue_scan(self.devices[index]);self.service.run_scan(job['id'])

    def settle(self):
        for index in range(4):self.scan(index);self.scan(index)

    def request(self,path,body=None,method=None):
        raw=json.dumps(body).encode() if body is not None else b''
        environ=dict(REQUEST_METHOD=method or ('POST' if body is not None else 'GET'),PATH_INFO=path,
                     HTTP_HOST='localhost',HTTP_X_SAVE_MANAGER='1',CONTENT_TYPE='application/json',
                     CONTENT_LENGTH=str(len(raw)))
        environ['wsgi.input']=io.BytesIO(raw);response=[]
        data=b''.join(self.app(environ,lambda status,headers:response.append((int(status[:3]),dict(headers)))))
        return response[0][0],json.loads(data) if 'application/json' in response[0][1]['Content-Type'] else data

    def save(self,index):
        return self.service.db.one("SELECT * FROM saves WHERE card_id=? AND directory='SAME' AND present=1",(self.ids[index],))

    def test_copy_delete_icons_and_cross_platform_refusal(self):
        save=self.save(0);original=self.cards[1].read_bytes()
        status,icon=self.request('/api/icons/'+save['icon_hash']+'.gif')
        self.assertEqual(status,200);self.assertTrue(icon.startswith(b'GIF'))
        body={'save_id':save['id'],'destination_card_id':self.ids[2]}
        self.assertEqual(self.request('/api/transfers/preview',body)[0],400)
        body['destination_card_id']=self.ids[1]
        status,preview=self.request('/api/transfers/preview',body)
        self.assertEqual(status,201,preview)
        self.assertEqual(self.cards[1].read_bytes(),original)
        self.assertEqual(self.request(f"/api/transfers/{preview['id']}/apply",{})[0],400)
        status,result=self.request(f"/api/transfers/{preview['id']}/apply",{'cards_idle':True})
        self.assertEqual(status,200,result)
        self.assertEqual(Path(result['backup']).read_bytes(),original)
        self.scan(1)
        status,preview=self.request('/api/transfers/preview',{'save_id':self.save(1)['id'],'action':'delete'})
        self.assertEqual(status,201,preview)
        self.assertEqual(self.request(f"/api/transfers/{preview['id']}/apply",{'cards_idle':True})[0],200)
        self.assertFalse(Card(self.cards[1].read_bytes()).entries)
        self.assertIn('SAME',Card(self.cards[0].read_bytes()).entries)

    def test_ps1_sync_catches_up_offline_without_touching_ps2(self):
        self.settle()
        group=self.service.sync.configure({'name':'Shared','card_ids':self.ids[:2]})
        self.assertEqual(group['backend'],'ps1')
        self.service.sync.resolve(group['id'],{'source_card_id':self.ids[0]})
        ps2=self.service.sync.configure({'name':'Shared','card_ids':self.ids[2:]})
        self.assertEqual(ps2['backend'],'ps2');self.assertNotEqual(ps2['id'],group['id'])
        before=[p.read_bytes() for p in self.cards[2:]]
        offline=self.root/'offline'
        self.cards[1].parent.rename(offline);self.scan(1)
        self.service.sync.next_tick=0;self.service.sync.tick()
        self.assertEqual(self.service.sync.group(group['id'])['status'],'pending')
        self.cards[0].write_bytes(fixture([('SAME',(8,),payload('New progress'))]))
        self.scan(0);self.scan(0)
        self.service.sync.next_tick=0;self.service.sync.tick()
        self.service.close();self.service=Service(self.root/'data');self.service.sync.settle_seconds=0;self.app=Application(self.service)
        offline.rename(self.cards[1].parent)
        self.settle();self.service.sync.next_tick=0;self.service.sync.tick()
        card=Card(self.cards[1].read_bytes())
        self.assertEqual(card.payload(card.entries['SAME']),payload('New progress'))
        self.assertEqual([p.read_bytes() for p in self.cards[2:]],before)
        with self.assertRaisesRegex(ValueError,'same storage backend'):
            self.service.sync.configure({'name':'Shared','card_ids':[self.ids[0],self.ids[2]]})

    def test_file_filter_can_change_without_replacing_location_history(self):
        location = self.locations[0]
        self.service.save_location(self.devices[0], {'suffix':'.mcr'}, location['id'])
        self.scan(0)
        save = self.save(0)
        shared = self.cards[0].with_name('pcsx-card1.mcd')
        shared.write_bytes(blank_card())
        before = self.cards[0].read_bytes()
        url = f"/api/devices/{self.devices[0]}/locations/{location['id']}"
        status, changed = self.request(url, {'suffix':'*'}, 'PUT')
        self.assertEqual(status, 200, changed)
        self.assertEqual(changed['id'], location['id'])
        self.assertEqual(changed['status'], 'never')
        with self.assertRaisesRegex(ValueError, 'Scan this location'):
            self.service._card(self.ids[0])
        self.scan(0)
        cards = self.service.db.rows('SELECT * FROM cards WHERE location_id=? AND present=1', (location['id'],))
        self.assertEqual({c['filename'] for c in cards}, {'card.mcr','pcsx-card1.mcd'})
        self.assertEqual(self.save(0)['id'], save['id'])
        self.assertEqual(self.save(0)['sha256'], save['sha256'])
        self.assertEqual(self.cards[0].read_bytes(), before)
        status, changed = self.request(url, {'suffix':'.mcd'}, 'PUT')
        self.assertEqual(status, 200, changed)
        self.scan(0)
        self.assertEqual(self.service.db.one('SELECT present FROM cards WHERE id=?', (self.ids[0],))['present'], 0)
        self.assertEqual(self.service.db.one('SELECT sha256 FROM saves WHERE id=?', (save['id'],))['sha256'], save['sha256'])

    def test_backend_is_part_of_location_identity(self):
        with self.assertRaisesRegex(ValueError,'new location'):
            self.service.save_location(self.devices[0],{'backend':'ps2','suffix':'.ps2'},self.locations[0]['id'])

    def test_remove_location_preserves_files_and_history_and_pauses_sync(self):
        status, done = self.request('/api/transfers/preview', {'save_id':self.save(0)['id'], 'destination_card_id':self.ids[1]})
        self.assertEqual(status, 201, done)
        self.assertEqual(self.request(f"/api/transfers/{done['id']}/apply", {'cards_idle':True})[0], 200)
        self.scan(1)
        group = self.service.sync.configure({'name':'Shared', 'card_ids':self.ids[:2]})
        status, preview = self.request('/api/transfers/preview', {'save_id':self.save(0)['id'], 'destination_card_id':self.ids[1]})
        self.assertEqual(status, 201, preview)
        before = [p.read_bytes() for p in self.cards]
        route = f"/api/devices/{self.devices[1]}/locations/{self.locations[1]['id']}"
        wrong = f"/api/devices/{self.devices[0]}/locations/{self.locations[1]['id']}"
        self.assertEqual(self.request(wrong, {}, 'DELETE')[0], 404)
        self.assertEqual(self.request(route, {}, 'DELETE')[0], 200)
        self.assertEqual([p.read_bytes() for p in self.cards], before)
        inventory = self.service.inventory()
        self.assertNotIn(self.locations[1]['id'], [l['id'] for l in inventory['locations']])
        self.assertNotIn(self.ids[1], [c['id'] for c in inventory['cards']])
        self.assertNotIn(self.ids[1], [s['card_id'] for s in inventory['saves']])
        transfer = next(t for t in inventory['transfers'] if t['id']==done['id'])
        self.assertEqual(transfer['status'], 'done')
        self.assertTrue(Path(transfer['backup']).is_file())
        self.assertEqual(transfer['destination_filename'], self.cards[1].name)
        self.assertEqual(next(t for t in inventory['transfers'] if t['id']==preview['id'])['status'], 'cancelled')
        self.assertNotIn(preview['id'], self.service.previews)
        self.assertFalse(self.service.sync.group(group['id'])['enabled'])
        self.assertNotIn(self.ids[1], [m['card_id'] for m in self.service.sync.members(group)])
        with self.assertRaisesRegex(ValueError, 'at least two'):
            self.service.sync.set_enabled(group['id'], {'enabled':True})
        with self.assertRaises(LookupError):
            self.service._card(self.ids[1])
        self.assertEqual(self.request(route, {}, 'DELETE')[0], 404)
        restored = self.service.save_location(self.devices[1], dict(name='Re-added', path=self.locations[1]['path'], backend='ps1', suffix='*'))
        self.assertNotEqual(restored['id'], self.locations[1]['id'])
        self.scan(1)
        self.assertEqual(len(self.service.inventory()['saves']), 4)
        self.assertEqual(self.service.db.one('SELECT count(*) n FROM save_versions WHERE save_id IN (SELECT id FROM saves WHERE card_id=?)', (self.ids[1],))['n'], 1)
        with self.service.db.connect() as db:
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_remove_device_preserves_library_and_files_and_rejects_reuse(self):
        device_id = self.devices[0]
        self.service.update_device(device_id, {'poll_seconds':300})
        extra = self.service.save_location(device_id, dict(name='Extra',path='extra',backend='ps1',suffix='*'))
        group = self.service.sync.configure({'name':'Shared','card_ids':self.ids[:2]})
        status, preview = self.request('/api/transfers/preview', {'save_id':self.save(0)['id'],'destination_card_id':self.ids[1]})
        self.assertEqual(status,201,preview)
        before = [p.read_bytes() for p in self.cards]
        with self.service.db.connect() as db:
            versions = [tuple(row) for row in db.execute('SELECT * FROM library_versions ORDER BY id')]
        job = self.service.queue_scan(device_id)
        route = f'/api/devices/{device_id}'
        self.assertEqual(self.request(route,{},'DELETE')[0],200)
        inventory = self.service.inventory()
        self.assertNotIn(device_id,[d['id'] for d in inventory['devices']])
        self.assertFalse(any(l['device_id']==device_id for l in inventory['locations']))
        self.assertNotIn(self.ids[0],[c['id'] for c in inventory['cards']])
        self.assertEqual(self.service.db.one('SELECT deleted FROM locations WHERE id=?',(extra['id'],))['deleted'],1)
        self.assertEqual([p.read_bytes() for p in self.cards],before)
        self.assertEqual(self.service.db.one('SELECT status FROM transfers WHERE id=?',(preview['id'],))['status'],'cancelled')
        self.assertFalse(self.service.sync.group(group['id'])['enabled'])
        self.service.enqueue_due(at=10**12)
        self.assertEqual(self.service.db.one('SELECT status FROM jobs WHERE id=?',(job['id'],))['status'],'cancelled')
        with self.service.db.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM library_versions ORDER BY id')],versions)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        self.assertEqual(self.request(route,{},'DELETE')[0],404)
        self.assertEqual(self.request(route,{'name':'Reuse'},'PUT')[0],404)
        self.assertEqual(self.request(route+'/scan',{})[0],404)
        self.assertEqual(self.request(route+'/locations',dict(name='Reuse',path='extra'))[0],404)
        empty = self.service.create_device(dict(name='Empty',root_path=str(self.cards[0].parent)))
        self.assertEqual(self.request(f"/api/devices/{empty['id']}",{},'DELETE')[0],200)

    def test_remove_last_location_cancels_queued_scan_and_stops_polling(self):
        job = self.service.queue_scan(self.devices[0])
        route = f"/api/devices/{self.devices[0]}/locations/{self.locations[0]['id']}"
        self.assertEqual(self.request(route, {}, 'DELETE')[0], 200)
        self.assertEqual(self.service.db.one('SELECT status FROM jobs WHERE id=?', (job['id'],))['status'], 'cancelled')
        with self.assertRaisesRegex(ValueError, 'Add a storage location'):
            self.service.queue_scan(self.devices[0])
        self.service.enqueue_due(at=10**12)
        self.assertEqual(self.service.db.one('SELECT status FROM jobs WHERE id=?', (job['id'],))['status'], 'cancelled')

    def test_nested_pops_cards_transfer_delete_and_sync(self):
        for index, game in enumerate(('Source game', 'Destination game')):
            path = self.cards[index].parent / game / 'SLOT0.VMC'
            path.parent.mkdir()
            self.cards[index].rename(path)
            self.cards[index] = path
            sibling = path.parent.parent / 'Other game' / 'SLOT0.VMC'
            sibling.parent.mkdir()
            sibling.write_bytes(blank_card())
            self.scan(index)
            self.ids[index] = self.service.db.one(
                'SELECT id FROM cards WHERE location_id=? AND filename=?',
                (self.locations[index]['id'], game + '/SLOT0.VMC'))['id']
            self.assertEqual(len(self.service.db.rows(
                'SELECT id FROM cards WHERE location_id=? AND present=1',
                (self.locations[index]['id'],))), 2)
        original = self.cards[1].read_bytes()
        status, preview = self.request('/api/transfers/preview', {
            'save_id': self.save(0)['id'], 'destination_card_id': self.ids[1]})
        self.assertEqual(status, 201, preview)
        status, result = self.request(f"/api/transfers/{preview['id']}/apply", {'cards_idle': True})
        self.assertEqual(status, 200, result)
        self.assertEqual(Path(result['backup']).read_bytes(), original)
        self.scan(1)
        status, preview = self.request('/api/transfers/preview', {'save_id': self.save(1)['id'], 'action': 'delete'})
        self.assertEqual(status, 201, preview)
        self.assertEqual(self.request(f"/api/transfers/{preview['id']}/apply", {'cards_idle': True})[0], 200)
        self.settle()
        group = self.service.sync.configure({'name': 'Shared', 'card_ids': self.ids[:2]})
        self.service.sync.resolve(group['id'],{'source_card_id':self.ids[0]})
        self.service.sync.next_tick = 0
        self.service.sync.tick()
        self.assertIn('SAME', Card(self.cards[1].read_bytes()).entries)
        self.assertFalse(Card((self.cards[1].parent.parent / 'Other game' / 'SLOT0.VMC').read_bytes()).entries)


class BackendMigrationTest(unittest.TestCase):
    def test_version_six_preserves_memberships_and_pending_source(self):
        old=SCHEMA.replace("    backend TEXT NOT NULL DEFAULT 'ps2',\n",'')
        old=old.replace("    backend TEXT NOT NULL DEFAULT 'ps2', UNIQUE(backend,directory)\n",'')
        old=old.replace('id INTEGER PRIMARY KEY, directory TEXT NOT NULL,','id INTEGER PRIMARY KEY, directory TEXT NOT NULL UNIQUE,')
        old=old.replace('next_scan REAL NOT NULL DEFAULT 0, updated TEXT NOT NULL,\n);','next_scan REAL NOT NULL DEFAULT 0, updated TEXT NOT NULL\n);')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'inventory.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(old)
                db.execute("INSERT INTO devices(id,name,kind) VALUES (1,'Existing','PS2')")
                db.execute("INSERT INTO locations(id,device_id,name,path,suffix) VALUES (1,1,'Cards','/old','.ps2')")
                db.execute("INSERT INTO cards(id,location_id,filename,sha256,size,mtime_ns,formatted,last_seen) VALUES (1,1,'card.ps2','cardhash',8388608,1,1,'now')")
                db.execute("INSERT INTO sync_groups(id,directory,head_hash,source_card_id,snapshot,status,next_scan,updated) VALUES (9,'SAME','savehash',1,'/data/sync/9/source.bin','pending',1234,'now')")
                db.execute("INSERT INTO sync_members(group_id,card_id,baseline_hash,status,message,retry_after) VALUES (9,1,'savehash','pending','Old queue',999)")
                db.execute('PRAGMA user_version=6')
            migrated=Database(path)
            group=migrated.one('SELECT * FROM sync_groups WHERE id=9')
            self.assertEqual(group['backend'],'ps2');self.assertEqual(group['snapshot'],'/data/sync/9/source.bin')
            self.assertEqual(group['next_scan'],1234)
            self.assertFalse(group['enabled'])
            self.assertEqual(group['scope'],'save')
            member=migrated.one('SELECT * FROM sync_members WHERE group_id=9')
            self.assertEqual(member['baseline_hash'],'savehash');self.assertEqual(member['retry_after'],999)
            with migrated.connect() as db:
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
                db.execute("INSERT INTO sync_groups(directory,backend,updated) VALUES ('SAME','ps1','now')")
                self.assertEqual(db.execute('SELECT count(*) FROM sync_groups').fetchone()[0],2)
