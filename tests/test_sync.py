"""Sync exercises use synthetic cards in temporary folders."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from save_web.service import Service
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file


class SyncTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.service = Service(self.root/'data')
        self.addCleanup(lambda:self.service.close())
        self.service.sync.settle_seconds = 0
        self.devices = []
        self.locations = []
        self.cards = []
        for name in ('a','b','c'):
            path = self.root/name
            path.mkdir()
            card_file(path/'card.ps2',[generic_save('GAME',b'initial'),generic_save('KEEP',name.encode())],True)
            device = self.service.create_device({'name':name})
            location = self.service.save_location(device['id'],{'name':name,'path':str(path),'writable':True})
            self.devices.append(device['id']);self.locations.append(location['id']);self.cards.append(path/'card.ps2')
            self.scan(len(self.devices)-1)
        self.ids = [self.service.db.one('SELECT id FROM cards WHERE location_id=?',(key,))['id'] for key in self.locations]
        self.group = self.service.sync.configure({'directory':'GAME','card_ids':self.ids})

    def scan(self, index):
        job = self.service.queue_scan(self.devices[index])
        self.service.run_scan(job['id'])

    def settle(self):
        for index in range(3):
            self.scan(index);self.scan(index)

    def tick(self):
        self.service.sync.next_tick = 0
        self.service.sync.tick()
        return self.service.sync.group(self.group['id'])

    def initialize(self):
        self.settle()
        self.assertEqual(self.tick()['status'],'synced')

    def edit(self, index, payload):
        card_file(self.cards[index],[generic_save('GAME',payload),generic_save('KEEP',chr(97+index).encode())],True)

    def payload(self, index):
        return snapshot(self.cards[index])['/GAME/data.bin'][1]

    def test_one_changed_copy_syncs_both_targets_with_backups_and_no_loop(self):
        self.initialize()
        originals = [p.read_bytes() for p in self.cards]
        self.edit(0,b'progress')
        self.scan(0)
        self.assertEqual(self.tick()['status'],'settling')
        self.assertEqual(self.cards[1].read_bytes(),originals[1])
        self.scan(0)
        self.assertEqual(self.tick()['status'],'pending')
        self.assertEqual([self.payload(i) for i in range(3)],[b'progress']*3)
        for index in range(3):
            self.assertEqual(snapshot(self.cards[index])['/KEEP/data.bin'][1],chr(97+index).encode())
        transfers = self.service.db.rows("SELECT * FROM transfers WHERE action='Automatic sync'")
        self.assertEqual(len(transfers),2)
        for transfer in transfers:
            index = self.ids.index(transfer['destination_card_id'])
            self.assertEqual(Path(transfer['backup']).read_bytes(),originals[index])
        self.settle()
        self.assertEqual(self.tick()['status'],'synced')
        self.tick()
        self.assertEqual(len(self.service.db.rows('SELECT * FROM transfers')),2)

    def test_independent_changes_conflict_and_explicit_source_resolves(self):
        self.initialize()
        self.edit(0,b'first');self.edit(1,b'second')
        self.settle()
        before = [p.read_bytes() for p in self.cards]
        self.assertEqual(self.tick()['status'],'conflict')
        self.assertEqual([p.read_bytes() for p in self.cards],before)
        self.service.sync.resolve(self.group['id'],{'source_card_id':self.ids[1]})
        self.tick()
        self.assertEqual([self.payload(i) for i in range(3)],[b'second']*3)

    def test_offline_update_survives_restart_and_catches_up(self):
        self.initialize()
        self.cards[2].parent.rename(self.root/'offline')
        self.scan(2)
        self.edit(0,b'new')
        self.scan(0);self.scan(0)
        self.assertEqual(self.tick()['status'],'pending')
        self.assertEqual(self.payload(1),b'new')
        self.service.close()
        self.service = Service(self.root/'data')
        self.service.sync.settle_seconds = 0
        (self.root/'offline').rename(self.cards[2].parent)
        self.settle()
        self.tick()
        self.assertEqual(self.payload(2),b'new')
        self.settle()
        self.assertEqual(self.tick()['status'],'synced')

    def test_edit_on_returning_stale_device_is_conflict(self):
        self.initialize()
        self.cards[2].parent.rename(self.root/'offline')
        self.scan(2)
        self.edit(0,b'new')
        self.scan(0);self.scan(0);self.tick()
        (self.root/'offline').rename(self.cards[2].parent)
        self.edit(2,b'offline progress')
        self.settle()
        self.assertEqual(self.tick()['status'],'conflict')
        self.assertEqual(self.payload(2),b'offline progress')

    def test_local_delete_is_not_propagated_or_silently_recreated(self):
        self.initialize()
        card_file(self.cards[1],[generic_save('KEEP',b'b')],True)
        self.settle()
        self.assertEqual(self.tick()['status'],'blocked')
        self.assertNotIn('/GAME',snapshot(self.cards[1]))
        self.assertEqual(self.payload(0),b'initial')

    def test_full_readonly_and_changed_destination_do_not_overwrite(self):
        self.initialize()
        self.edit(0,b'new');self.settle()
        self.service.save_location(self.devices[1],{'writable':False},self.locations[1])
        self.assertEqual(self.tick()['status'],'blocked')
        self.assertEqual(self.payload(1),b'initial')
        self.service.save_location(self.devices[1],{'writable':True},self.locations[1])
        with patch('save_core.backends.ps2.PS2Backend.plan_copy',side_effect=ValueError('Not enough free space')):
            self.assertEqual(self.tick()['status'],'blocked')
        self.assertEqual(self.payload(1),b'initial')
        # Change a card after its scan: hash validation prevents a stale write.
        self.service.sync.retry(self.group['id'])
        self.edit(1,b'last-second edit')
        self.assertEqual(self.tick()['status'],'blocked')
        self.assertEqual(self.payload(1),b'last-second edit')

    def test_disabled_groups_do_not_write_and_enabled_groups_schedule_scans(self):
        self.initialize()
        self.service.sync.set_enabled(self.group['id'],{'enabled':False})
        self.edit(0,b'new');self.settle();self.tick()
        self.assertEqual(self.payload(1),b'initial')
        with patch.object(self.service,'queue_scan') as queue:
            self.service.sync.enqueue_due();queue.assert_not_called()
            self.service.sync.set_enabled(self.group['id'],{'enabled':True})
            self.service.sync.enqueue_due()
            self.assertEqual(queue.call_count,3)

    def test_initial_differences_require_source_and_new_target_can_receive_save(self):
        self.edit(1,b'other')
        card_file(self.cards[2],[generic_save('KEEP',b'c')],True)
        self.settle()
        self.group=self.service.sync.configure({'directory':'GAME','card_ids':self.ids})
        self.assertEqual(self.group['status'],'conflict')
        self.tick()
        self.assertEqual(self.payload(1),b'other')
        self.service.sync.resolve(self.group['id'],{'source_card_id':self.ids[0]})
        self.tick()
        self.assertEqual([self.payload(i) for i in range(3)],[b'initial']*3)

    def test_stale_scans_and_actual_settling_window_block_writes(self):
        self.initialize()
        self.edit(0,b'new');self.settle()
        self.service.sync.settle_seconds=30
        self.assertEqual(self.tick()['status'],'settling')
        self.service.sync.settle_seconds=0
        with self.service.db.connect() as db:
            db.execute('UPDATE sync_observations SET checked=0')
        self.assertEqual(self.tick()['status'],'settling')
        self.assertEqual(self.payload(1),b'initial')

    def test_unconfigured_saves_are_never_synced(self):
        self.initialize()
        card_file(self.cards[0],[generic_save('GAME',b'initial'),generic_save('UNTRACKED',b'private')],True)
        self.settle();self.tick()
        self.assertNotIn('/UNTRACKED',snapshot(self.cards[1]))

    def test_changed_source_after_scan_is_not_captured(self):
        self.initialize()
        self.edit(0,b'observed');self.settle()
        self.edit(0,b'not yet scanned')
        self.assertEqual(self.tick()['status'],'blocked')
        self.assertEqual(self.payload(1),b'initial')

    def test_failed_write_keeps_backup_and_retries_are_delayed(self):
        import os
        self.initialize()
        self.edit(0,b'new');self.settle()
        original_replace = os.replace
        def refused(source,destination):
            if Path(destination) in self.cards:
                raise OSError('mount refused rename')
            return original_replace(source,destination)
        with patch('save_core.save_copy.os.replace',side_effect=refused) as replace:
            self.assertEqual(self.tick()['status'],'blocked')
            calls = replace.call_count
            self.tick()
            self.assertEqual(replace.call_count,calls)
        transfers = self.service.db.rows('SELECT * FROM transfers')
        self.assertEqual(len(transfers),2)
        for transfer in transfers:
            self.assertEqual(transfer['status'],'failed')
            self.assertTrue(Path(transfer['backup']).is_file())
        self.assertEqual(self.payload(1),b'initial')
        self.service.sync.retry(self.group['id']);self.tick()
        self.assertEqual(self.payload(1),b'new')

    def test_worker_runs_sync_between_continuously_queued_slow_scans(self):
        import time
        service = self.service
        clock = [time.time()]
        scans = []

        def slow_scan(key):
            with service.db.connect() as db:
                db.execute("UPDATE jobs SET status='done' WHERE id=?", (key,))
            scans.append(key)
            clock[0] += service.sync.scan_seconds + 1  # Scans become due before the queue drains.
            if len(scans) == 5:
                service.stop_event.set()

        with patch('save_web.sync.time.time', side_effect=lambda:clock[0]), \
             patch.object(service, 'run_scan', side_effect=slow_scan), \
             patch.object(service.sync, 'tick') as tick:
            service._worker()
        self.assertEqual(len(scans), 5)
        self.assertEqual(tick.call_count, 5)
        self.assertTrue(service.db.rows("SELECT id FROM jobs WHERE status='queued'"))
