"""Save bytes survive offline sources, slot changes and bounded version pruning."""

import io
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from game_plugins.library import extract_slots
from save_core.backends import get_backend
from save_core.backends.base import SaveBundle, SaveFile
from save_core.backends.serialization import read_bundle
from save_core.backends.ps2.bundles import from_native
from save_web.library import ObjectStore
from save_web.service import Service
import test_ps1_web
from test_ps1 import fixture as card_fixture
from test_ffix import ffix_save, save_id, checksum, CHECKSUM_OFFSET
from test_ar_tonelico import fixture as ar_fixture, DIRECTORY as AR_DIRECTORY
from test_ar_tonelico_slots import game_save as ar_save
from test_dothack_slots import fixture as hack_fixture, game_save as hack_save
from test_katamari_slots import fixture as katamari_fixture, game_save as katamari_save
from test_steambot_slots import fixture as steambot_fixture, game_save as steambot_save
from test_ffx_plugin import fixture as ffx_fixture


def ffix(seconds, marker=17):
    data = bytearray(ffix_save(marker=marker))
    struct.pack_into('<I',data,0x12c,seconds*60)
    struct.pack_into('<H',data,CHECKSUM_OFFSET,checksum(data))
    return bytes(data)


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.case = test_ps1_web.PS1WebTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.service = self.case.service
        self.cards = self.case.cards

    def test_serial_groups_named_saves_and_migrates_settings_without_losing_data(self):
        from save_web.database import Database
        from test_save_copy import generic_save
        from test_save_merge import card_file
        names=('BASLUS-20573Config','BASLUS-20573SaIGbLlA')
        card_file(self.cards[2],[generic_save(names[0],b'configuration'),generic_save('BASLUS-21227DBZT',b'US')],True)
        card_file(self.cards[3],[generic_save(names[1],b'career'),generic_save('BESLES-53200DBZT',b'EU')],True)
        self.case.scan(2);self.case.scan(3)
        games=self.service.library.inventory()['games']
        game=next(g for g in games if g['game_key']=='SLUS-20573')
        self.assertEqual(len(game['saves']),2)
        self.assertEqual(len([g for g in games if g['serial'] in ('SLUS-21227','SLES-53200')]),2)
        slots=game['slots'];before={s['id']:s['current']['content_hash'] for s in slots}
        versions=self.service.db.rows('SELECT * FROM library_versions ORDER BY id')
        sources=self.service.db.rows('SELECT * FROM library_sources ORDER BY save_id,slot_id')
        with self.service.db.connect() as db:
            db.execute("UPDATE library_games SET game_key=?,title='Sims config',card_port_note='First note' WHERE id=?",(names[0],game['id']))
            db.execute("UPDATE library_slots SET slot_key='whole' WHERE id=?",(slots[0]['id'],))
            second=db.execute("INSERT INTO library_games(backend,game_key,title,card_ports,card_port_note) VALUES ('ps2',?,'Sims life MARION','both','Tested on PS3')",(names[1],)).lastrowid
            db.execute("UPDATE library_slots SET game_id=?,slot_key='whole' WHERE id=?",(second,slots[1]['id']))
            db.execute('PRAGMA user_version=13')
        Database(self.service.db.path)
        games=self.service.library.inventory()['games']
        merged=next(g for g in games if g['game_key']=='SLUS-20573')
        self.assertEqual(merged['title'],'Sims')
        self.assertEqual(merged['card_ports'],'both')
        self.assertIn('First note',merged['card_port_note'])
        self.assertIn('Tested on PS3',merged['card_port_note'])
        self.assertEqual({s['id']:s['current']['content_hash'] for s in merged['slots']},before)
        self.assertEqual(self.service.db.rows('SELECT * FROM library_versions ORDER BY id'),versions)
        self.assertEqual(self.service.db.rows('SELECT * FROM library_sources ORDER BY save_id,slot_id'),sources)
        self.case.scan(2);self.case.scan(3)
        merged=next(g for g in self.service.library.inventory()['games'] if g['game_key']=='SLUS-20573')
        self.assertEqual(len(merged['saves']),2)
        self.assertEqual({s['id'] for s in merged['slots']},set(before))
        self.service.library.game_settings(merged['id'],{'card_ports':'port1'})
        self.assertEqual(next(g for g in self.service.library.inventory()['games'] if g['game_key']=='SLUS-20573')['card_ports'],'port1')
        for save in merged['saves']:
            self.service.library.download(save['id'])

    def test_game_port_settings_are_isolated_and_survive_scans_and_restart(self):
        self.scan_ffix()
        games = self.service.library.inventory()['games']
        game = next(g for g in games if g['title'].startswith('Final Fantasy IX'))
        other = next(g for g in games if g['id']!=game['id'])
        self.assertEqual(game['card_ports'],'unknown')
        url = f"/api/library/games/{game['id']}"
        version = self.slot()['current']['id']
        for ports in ('port1','both','unknown'):
            status, response = self.case.request(url,{'card_ports':ports,'card_port_note':'Tested by me'},'PUT')
            self.assertEqual(status,200,response)
            self.assertEqual(response['card_ports'],ports)
        self.case.request(url,{'card_ports':'both','card_port_note':'Manual: https://example.test/ffix\nTested by me'},'PUT')
        self.case.scan(0)
        games = self.service.library.inventory()['games']
        saved = next(g for g in games if g['id']==game['id'])
        self.assertEqual(saved['card_ports'],'both')
        self.assertIn('Tested by me',saved['card_port_note'])
        self.assertEqual(next(g for g in games if g['id']==other['id'])['card_ports'],'unknown')
        self.assertEqual(self.slot()['current']['id'],version)
        for body in ({'card_ports':'port2'}, {'card_ports':True}, {'card_port_note':'x'*1001}, {'card_port_note':[]}, {'card_port_note':'bad\0note'}):
            self.assertEqual(self.case.request(url,body,'PUT')[0],400,body)
        self.assertEqual(self.case.request('/api/library/games/999999',{'card_ports':'both'},'PUT')[0],404)
        self.service.close()
        reopened = Service(self.service.data_dir)
        self.addCleanup(reopened.close)
        saved = reopened.db.one('SELECT * FROM library_games WHERE id=?',(game['id'],))
        self.assertEqual(saved['card_ports'],'both')
        self.assertIn('Tested by me',saved['card_port_note'])

    def scan_ffix(self, index=0, seconds=3600, marker=17):
        self.cards[index].write_bytes(card_fixture([(save_id(1),(3,),ffix(seconds,marker))]))
        self.case.scan(index)
        job = self.service.db.rows('SELECT * FROM jobs ORDER BY id DESC LIMIT 1')[0]
        self.assertEqual(job['status'],'done',job['error'])
        return self.slot()

    def slot(self):
        game = next(g for g in self.service.library.inventory()['games'] if g['title'].startswith('Final Fantasy IX'))
        return game['slots'][0]

    def test_import_independent_of_card_filename_and_offline_source(self):
        slot = self.scan_ffix()
        self.scan_ffix(1)
        self.assertEqual(len(self.slot()['versions']),1)
        self.assertEqual(len(self.slot()['sources']),2)
        self.assertEqual(slot['number'],1)
        self.assertEqual(slot['current']['playtime_seconds'],3600)
        location = self.case.locations[0]
        self.service.delete_location(self.case.devices[0],location['id'])
        self.cards[1].unlink()
        self.case.scan(1)
        version = self.slot()['current']
        self.assertEqual(version['id'],slot['current']['id'])
        exported = self.service.library.download(version['id'])
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            manifest = json.loads(archive.read('save.json'))
            read = lambda key: archive.read('objects/'+key)
            bundle = read_bundle(manifest['bundle'],read)
            self.assertEqual(bundle.file_data()['data'],ffix(3600))
            self.assertEqual(bundle.save_id,save_id(1))
            self.assertEqual(manifest['slot_number'],1)
        # Also prove data persists over a service restart, without a reachable source.
        self.service.close()
        reopened = Service(self.service.data_dir)
        self.addCleanup(reopened.close)
        self.assertEqual(len(reopened.library.download(version['id'])),len(exported))

    def test_live_alternatives_survive_zero_history_and_missing_versions_are_recaptured(self):
        self.scan_ffix(0,3600)
        self.scan_ffix(1,7200)
        current = self.slot()['current']['id']
        other = next(v for v in self.slot()['versions'] if v['id']!=current)
        # Simulate an alternative discarded by the previous retention policy.
        with self.service.db.connect() as db:
            db.execute('DELETE FROM library_versions WHERE id=?',(other['id'],))
        self.service.library.collect()
        self.case.scan(0)
        self.assertEqual(self.slot()['current']['id'],current)
        self.assertEqual(len(self.slot()['versions']),2)
        self.service.library.settings({'previous_versions':0})
        self.assertEqual(len(self.slot()['versions']),2)
        for n in range(2):
            folder=self.case.root/f'extra-{n}';folder.mkdir()
            (folder/'shared.mcd').write_bytes(card_fixture([(save_id(1),(3,),ffix(10000+n,30+n))]))
            device=self.service.create_device(dict(name=f'Extra {n}',root_path=str(folder)))
            self.service.save_location(device['id'],dict(name='Cards',path='.',backend='ps1',suffix='.mcd'))
            job=self.service.queue_scan(device['id']);self.service.run_scan(job['id'])
        self.assertEqual(len(self.slot()['versions']),4)
        for version in self.slot()['versions']:
            self.service.library.download(version['id'])

    def test_changed_source_wins_once_and_stale_sources_do_not_oscillate(self):
        self.scan_ffix(0,7200)
        self.scan_ffix(1,3600)
        self.assertEqual(self.slot()['current']['playtime_seconds'],7200)
        self.scan_ffix(1,1800,18)  # A deliberate earlier save is still a new change.
        current = self.slot()['current']
        self.assertEqual(current['playtime_seconds'],1800)
        self.case.scan(0)
        self.assertEqual(self.slot()['current']['id'],current['id'])
        self.assertEqual(len(self.slot()['versions']),3)

    def test_initial_discovery_prefers_playtime_and_history_is_bounded(self):
        self.scan_ffix(0,1800)
        self.scan_ffix(1,7200)
        self.assertEqual(self.slot()['current']['playtime_seconds'],7200)
        for marker in range(18,23):
            self.scan_ffix(1,7200+marker,marker)
        slot = self.slot()
        self.assertEqual(len(slot['versions']),4)
        previous = next(v for v in slot['versions'] if v['id']!=slot['current']['id'])
        status, response = self.case.request(f"/api/library/slots/{slot['id']}/restore", {'version_id':previous['id']})
        self.assertEqual(status,200,response)
        self.assertEqual(self.slot()['current']['id'],previous['id'])
        before = self.cards[1].read_bytes()
        status, response = self.case.request('/api/library/settings',{'previous_versions':0},'PUT')
        self.assertEqual(status,200,response)
        self.assertEqual(len(self.slot()['versions']),3)
        self.assertEqual(self.cards[1].read_bytes(),before)
        # Every object on disk is now reachable; no pruned save bytes remain.
        objects = self.service.library.objects
        keys = {row['content_hash'] for row in self.service.db.rows('SELECT content_hash FROM library_versions')}
        keys.update(row['bundle_hash'] for row in self.service.db.rows('SELECT bundle_hash FROM library_versions'))
        for key in list(keys):
            keys.update(file['object'] for file in objects.load(key)['files'])
        self.assertEqual({p.name for p in objects.root.iterdir()},keys)
        for bad in (-1,101,True,'2'):
            self.assertEqual(self.case.request('/api/library/settings',{'previous_versions':bad},'PUT')[0],400)

    def test_ps2_slots_change_independently_and_preserve_backend_context(self):
        payload = ar_fixture()
        self.cards[2].write_bytes(b'')
        from test_save_merge import card_file
        card_file(self.cards[2],[ar_save(payload)],True)
        self.case.scan(2)
        game = next(g for g in self.service.library.inventory()['games'] if g['title'].startswith('Ar tonelico'))
        self.assertEqual(len(game['slots']),2)
        before = {s['number']:s['current']['id'] for s in game['slots']}
        # Change only slot 1, retaining slot 2 exactly.
        from game_plugins.ps2.ar_tonelico import SLOT_SIZE, checksum as ar_checksum
        changed = bytearray(payload)
        changed[0x41] ^= 1
        struct.pack_into('<I',changed,1,ar_checksum(changed[:SLOT_SIZE]))
        card_file(self.cards[2],[ar_save(bytes(changed))],True)
        self.case.scan(2)
        game = next(g for g in self.service.library.inventory()['games'] if g['title'].startswith('Ar tonelico'))
        after = {s['number']:s['current']['id'] for s in game['slots']}
        self.assertNotEqual(before[1],after[1])
        self.assertEqual(before[5],after[5])
        version = game['slots'][0]['current']
        bundle = read_bundle(self.service.library.objects.load(version['bundle_hash']),self.service.library.objects.get)
        original = get_backend('ps2').read_save(self.cards[2],AR_DIRECTORY)
        self.assertEqual(bundle,original)

    def test_incomplete_game_container_still_imports_verified_slots(self):
        from test_save_merge import card_file
        data = steambot_fixture()
        del data['FILE_01']
        card_file(self.cards[2],[steambot_save(data)],True)
        self.case.scan(2)
        library = self.service.library.inventory()
        game = next(g for g in library['games'] if g['title'].startswith('Steambot'))
        self.assertEqual([s['number'] for s in game['slots']],[1])
        self.assertTrue(game['slots'][0]['current']['playtime_seconds']>0)
        self.assertTrue(any('Slot 2' in issue['error'] for issue in library['errors']))

    def test_whole_save_fallback_and_platform_isolation(self):
        games = self.service.library.inventory()['games']
        self.assertEqual({g['backend'] for g in games},{'ps1','ps2'})
        self.assertTrue(all(g['slots'][0]['number'] is None for g in games))
        self.assertTrue(all(g['saves'][0]['label'] for g in games))
        self.assertEqual(self.case.request('/api/library')[0],200)
        self.assertEqual(self.case.request('/api/library/versions/999999/download')[0],404)

    def test_invalid_plugin_data_keeps_previous_good_version(self):
        self.scan_ffix()
        previous = self.slot()['current']['id']
        broken = bytearray(ffix(7200));broken[0x500]^=1
        self.cards[0].write_bytes(card_fixture([(save_id(1),(3,),bytes(broken))]))
        self.case.scan(0)
        self.assertEqual(self.slot()['current']['id'],previous)
        self.assertIn('checksum',self.service.library.inventory()['errors'][0]['error'].lower())
        self.assertFalse(self.slot()['sources'][0]['present'])


class ExtractionTest(unittest.TestCase):
    def test_multi_file_plugins_and_ffx_return_isolated_records(self):
        for backend,bundle,count in [
            ('ps2',from_native(hack_save(hack_fixture())),2),
            ('ps2',from_native(katamari_save(katamari_fixture())),1),
            ('ps2',from_native(steambot_save(steambot_fixture())),1),
            ('ps2',SaveBundle('BISLPS-25088FF090600',(SaveFile('BISLPS-25088FF090600',ffx_fixture()),)),1),
        ]:
            with self.subTest(save=bundle.save_id):
                _,_,slots = extract_slots(backend,bundle,bundle.save_id)
                self.assertEqual(len(slots),count)
                self.assertTrue(all(slot.number is not None and slot.files for slot in slots))

    def test_schema_eleven_migrates_existing_games_with_unknown_ports(self):
        from save_web.database import Database, SCHEMA
        from save_web.library import SCHEMA as LIBRARY_SCHEMA
        old = LIBRARY_SCHEMA.replace("    card_ports TEXT NOT NULL DEFAULT 'unknown', card_port_note TEXT NOT NULL DEFAULT '',\n",'')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'inventory.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(SCHEMA+old)
                db.execute("INSERT INTO library_games(id,backend,game_key,title) VALUES (42,'ps1','ffix-us','FFIX')")
                db.execute('PRAGMA user_version=11')
            migrated = Database(path)
            game = migrated.one('SELECT * FROM library_games WHERE id=42')
            self.assertEqual((game['title'],game['card_ports'],game['card_port_note']),('FFIX','unknown',''))
            with migrated.connect() as db:
                db.execute("UPDATE library_games SET card_ports='both',card_port_note='Tested by me' WHERE id=42")
            migrated = Database(path)
            self.assertEqual(migrated.one('SELECT card_ports FROM library_games WHERE id=42')['card_ports'],'both')
            with migrated.connect() as db:
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_object_storage_failure_does_not_leave_partial_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ObjectStore(directory)
            with patch('save_web.library.os.replace',side_effect=OSError('Disk full')):
                with self.assertRaisesRegex(OSError,'Disk full'):
                    store.put(b'important save')
            self.assertFalse(list(Path(directory).iterdir()))
            key=store.put(b'important save')
            store.path(key).write_bytes(b'broken')
            with self.assertRaisesRegex(ValueError,'integrity'):
                store.get(key)
