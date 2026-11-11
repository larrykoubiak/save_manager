"""Device roots resolve relative, independently typed locations."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from save_core.storage_paths import location_path
from save_web.database import Database, SCHEMA
from save_web.service import Service
from test_ps1 import fixture, payload
from test_save_copy import generic_save
from test_save_merge import card_file


class DeviceRootsTest(unittest.TestCase):
    def test_mixed_system_locations_and_relocated_device(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base/'device'
            (root/'POPS'/'Game').mkdir(parents=True)
            (root/'VMC').mkdir()
            ps1 = root/'POPS'/'Game'/'SLOT0.VMC'
            ps1.write_bytes(fixture([('TEST', (3,), payload())]))
            card_file(root/'VMC'/'card.bin', [generic_save('TEST', b'PS2 save')], True)
            service = Service(base/'data')
            self.addCleanup(service.close)
            device = service.create_device(dict(name='PS2', root_path=str(root)))
            for folder, backend, suffix in [('POPS', 'ps1', '.vmc'), ('VMC', 'ps2', '.bin')]:
                service.save_location(device['id'], dict(name=folder, path=folder, backend=backend, suffix=suffix))
            def scan():
                job = service.queue_scan(device['id'])
                service.run_scan(job['id'])
                self.assertEqual(service.db.one('SELECT status FROM jobs WHERE id=?', (job['id'],))['status'], 'done')
            scan()
            before = service.inventory()
            self.assertEqual({l['path'] for l in before['locations']}, {'POPS', 'VMC'})
            self.assertEqual(len(before['saves']), 2)
            for card in before['cards']:
                service._card(card['id'])
            moved = base/'moved'
            root.rename(moved)
            service.update_device(device['id'], dict(root_path=str(moved)))
            for card in before['cards']:
                with self.assertRaisesRegex(ValueError, 'Scan this location'):
                    service._card(card['id'])
            scan()
            self.assertEqual([c['id'] for c in service.inventory()['cards']], [c['id'] for c in before['cards']])
            for card in before['cards']:
                self.assertTrue(service._card(card['id'])[2].is_relative_to(moved))
            for path in ('../escape', str(moved/'absolute')):
                with self.assertRaises(ValueError):
                    service.save_location(device['id'], dict(name='Invalid', path=path))
            (moved/'alias').symlink_to(base, target_is_directory=True)
            with self.assertRaises(ValueError):
                service.save_location(device['id'], dict(name='Link', path='alias/elsewhere'))
            second_root = base/'second'
            second_root.mkdir()
            second = service.create_device(dict(name='Second device', root_path=str(second_root)))
            service.save_location(second['id'], dict(name='VMC', path='VMC'))
            with self.assertRaisesRegex(ValueError, 'overlap'):
                service.update_device(second['id'], dict(root_path=str(moved)))

    def test_version_seven_migration_preserves_ids_and_effective_paths(self):
        old = SCHEMA.replace("    root_path TEXT NOT NULL DEFAULT '/',\n", '')
        old = old.replace('name TEXT NOT NULL, path TEXT NOT NULL, suffix TEXT NOT NULL,',
                          'name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, suffix TEXT NOT NULL,')
        old = old.replace('last_seen TEXT,\n    UNIQUE(device_id,path)', 'last_seen TEXT')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'inventory.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(old)
                db.execute("INSERT INTO devices(id,name,kind) VALUES (1,'PS2','PS2'),(2,'RetroArch','Other')")
                db.execute("INSERT INTO locations(id,device_id,name,path,suffix,backend) VALUES (7,1,'VMC','/devices/ps2/VMC','.bin','ps2'),(8,1,'POPS','/devices/ps2/POPS','.vmc','ps1'),(9,2,'VMC','/devices/retro/VMC','.mcr','ps1')")
                db.execute("INSERT INTO cards(id,location_id,filename,sha256,size,mtime_ns,formatted,last_seen) VALUES (11,8,'Game/SLOT0.VMC','hash',131072,1,1,'now')")
                db.execute('PRAGMA user_version=7')
            migrated = Database(path)
            locations = migrated.rows('SELECT l.*,d.root_path FROM locations l JOIN devices d ON d.id=l.device_id ORDER BY l.id')
            self.assertEqual([l['path'] for l in locations], ['VMC', 'POPS', 'VMC'])
            self.assertEqual([str(location_path(l)) for l in locations], ['/devices/ps2/VMC', '/devices/ps2/POPS', '/devices/retro/VMC'])
            self.assertEqual(migrated.one('SELECT location_id FROM cards WHERE id=11')['location_id'], 8)
            Database(path)  # Migration is idempotent.
            with migrated.connect() as db:
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_root_move_checks_all_sibling_locations_at_new_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            service = Service(base/'data')
            self.addCleanup(service.close)
            root = base/'device'
            device = service.create_device(dict(name='Console', root_path=str(root)))
            for name in ('A', 'B'):
                service.save_location(device['id'], dict(name=name, path=name))
            moved = root/'A'
            service.update_device(device['id'], dict(root_path=str(moved)))
            locations = service.db.rows('SELECT l.*,d.root_path FROM locations l JOIN devices d ON d.id=l.device_id')
            self.assertEqual({location_path(l) for l in locations}, {moved/'A', moved/'B'})
            self.assertTrue(all(l['status']=='never' for l in locations))

    def test_failed_table_rebuild_rolls_back_and_can_retry(self):
        connect = sqlite3.connect
        for table in ('sync_groups', 'locations'):
            with self.subTest(table=table), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'inventory.sqlite3'
                schema = SCHEMA
                if table=='sync_groups':
                    schema = schema.replace("    backend TEXT NOT NULL DEFAULT 'ps2', UNIQUE(backend,directory)",
                                            '    UNIQUE(directory)')
                with closing(connect(path)) as db, db:
                    db.executescript(schema)
                    db.execute("INSERT INTO devices(id,name,kind) VALUES (1,'Console','Other')")
                    db.execute("INSERT INTO locations(id,device_id,name,path,suffix) VALUES (7,1,'Cards','/device/cards','.ps2')")
                    db.execute("INSERT INTO sync_groups(id,directory,updated) VALUES (3,'GAME','now')")
                    db.execute('PRAGMA user_version=7')

                class InterruptedMigration(sqlite3.Connection):
                    def execute(self, sql, parameters=()):
                        if sql.startswith('INSERT INTO '+table+'_new'):
                            raise sqlite3.OperationalError('Interrupted migration')
                        return super().execute(sql, parameters)

                with patch('save_web.database.sqlite3.connect',
                           side_effect=lambda *args, **kwargs: connect(*args, **kwargs, factory=InterruptedMigration)):
                    with self.assertRaisesRegex(sqlite3.OperationalError, 'Interrupted migration'):
                        Database(path)
                with closing(connect(path)) as db, db:
                    self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name=?", (table+'_new',)).fetchone())
                    self.assertEqual(db.execute('SELECT id FROM locations').fetchone()[0], 7)
                    self.assertEqual(db.execute('SELECT id FROM sync_groups').fetchone()[0], 3)
                migrated = Database(path)
                with migrated.connect() as db:
                    self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
