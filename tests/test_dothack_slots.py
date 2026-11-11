"""Synthetic .hack slot/index and complete-card regressions; no private saves."""

from pathlib import Path
import struct
import tempfile
import unittest

from game_plugins import plugin_for
from game_plugins.ps2.dothack import DotHackInfectionUS as Plugin, DIRECTORY, SLOT_SIZE
from save_core.compare_saves import scan
from save_core.save_copy import apply_copy, plan_copy, plan_slot_copy
from save_web.inventory import snapshot_location
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file


def fixture():
    # Empty records/files may contain arbitrary uninitialized bytes. Only the
    # status flag marks an unused slot; occupied data must pass its checksum.
    index = bytearray((b'\0' + b'\x93' * 27) * 12)
    files = {f'dhdata{n:02}': b'\xa7' * SLOT_SIZE for n in range(1, 13)}
    for number, ticks in ((1, 318060), (2, 322620)):
        payload = bytes([number]) * SLOT_SIZE
        files[f'dhdata{number:02}'] = payload
        record = bytearray(28)
        record[0:2] = b'\1\1'
        record[4:8] = b'Kite'
        struct.pack_into('<H', record, 22, sum(payload) & 0xffff)
        struct.pack_into('<i', record, 24, ticks)
        index[(number - 1) * 28:number * 28] = record
    return {DIRECTORY: bytes(index), **files}


def game_save(data):
    save = generic_save(DIRECTORY, b'')
    template = list(save.get_file(0)[0])
    directory = list(save.get_directory())
    directory[2] = len(data) + 1
    save.set_directory(directory)
    for i, (name, payload) in enumerate({**data, 'settings': b'preserve settings'}.items()):
        entry = list(template)
        entry[8], entry[2] = name.encode(), len(payload)
        save.set_file(i, entry, payload)
    return save


class DotHackSlotTest(unittest.TestCase):
    def test_slots_use_index_occupancy_checksum_and_frame_timer(self):
        self.assertIs(plugin_for(DIRECTORY), Plugin)
        self.assertIsNone(plugin_for('BASLUS-20562DOTHACK'))
        slots = Plugin.slots(DIRECTORY, fixture())
        self.assertEqual([s['state'] for s in slots], ['occupied'] * 2 + ['empty'] * 10)
        self.assertEqual(slots[0]['label'], 'Kite · Lv 1 · 1:28:21 played')
        self.assertNotEqual(slots[0]['sha256'], slots[1]['sha256'])

    def test_copy_changes_only_target_file_and_its_index_record(self):
        original = fixture()
        result = Plugin.copy_slot(DIRECTORY, original, 1, original, 3)
        self.assertEqual(original, fixture())
        for name in original:
            if name not in (DIRECTORY, 'dhdata03'):
                self.assertEqual(result[name], original[name])
        self.assertEqual(result['dhdata03'], original['dhdata01'])
        self.assertEqual(result[DIRECTORY][:56], original[DIRECTORY][:56])
        self.assertEqual(result[DIRECTORY][84:], original[DIRECTORY][84:])
        self.assertEqual(result[DIRECTORY][56:84], original[DIRECTORY][:28])
        self.assertEqual(Plugin.slots(DIRECTORY, result)[2]['sha256'], Plugin.slots(DIRECTORY, original)[0]['sha256'])
        self.assertEqual(Plugin.copy_slot(DIRECTORY, original, 1, result, 3), result)
        with self.assertRaisesRegex(ValueError, 'occupied'):
            Plugin.copy_slot(DIRECTORY, original, 1, original, 2)
        self.assertEqual(Plugin.copy_slot(DIRECTORY, original, 1, original, 2, True)['dhdata02'], original['dhdata01'])

    def test_invalid_checksums_missing_files_layouts_and_empty_sources_refused(self):
        source = fixture()
        bad = {**source, 'dhdata01': b'\0' * SLOT_SIZE}
        self.assertEqual(Plugin.slots(DIRECTORY, bad)[0]['state'], 'invalid')
        for src, dst, number in ((bad, source, 1), (source, bad, 1), (source, source, 3)):
            with self.assertRaises(ValueError):
                Plugin.copy_slot(DIRECTORY, src, number, dst, 4)
        for number in (True, 0, 13, 1.0):
            with self.assertRaises(ValueError):
                Plugin.copy_slot(DIRECTORY, source, 1, source, number)
        for data in ({}, {**source, DIRECTORY: source[DIRECTORY][:-1]}, {**source, 'dhdata03': b''}):
            with self.assertRaises(ValueError):
                Plugin.copy_slot(DIRECTORY, source, 1, data, 3)
        with self.assertRaises(ValueError):
            Plugin.slots('BASLUS-20562DOTHACK', source)

    def test_raw_ecc_preview_apply_backup_same_card_and_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source.ps2', root / 'target.bin'
            card_file(source, [game_save(fixture())], True)
            card_file(target, [game_save(fixture()), generic_save('OTHER-GAME', b'untouched')], False)
            source_bytes, target_bytes = source.read_bytes(), target.read_bytes()
            before = snapshot(target)
            plan = plan_slot_copy(source, {'save': DIRECTORY}, 1, target, 3)
            self.assertEqual(target.read_bytes(), target_bytes)
            backup = apply_copy(plan, root / 'backups')
            self.assertEqual(backup.read_bytes(), target_bytes)
            self.assertEqual(source.read_bytes(), source_bytes)
            after = snapshot(target)
            for path in before:
                self.assertEqual(after[path][0], before[path][0])
                if path not in ('/' + DIRECTORY + '/' + DIRECTORY, '/' + DIRECTORY + '/dhdata03'):
                    self.assertEqual(after[path], before[path])
            # The same transformation also works when source and target share
            # an ECC card; all other payloads/index records remain unchanged.
            same = plan_slot_copy(source, {'save': DIRECTORY}, 2, source, 12)
            apply_copy(same, root / 'backups')
            location = dict(path=str(root), suffix='.ps2', marker='')
            records = snapshot_location(location)[0]['saves']
            self.assertEqual([s['number'] for s in records[0]['slots'] if s['state'] == 'occupied'], [1, 2, 12])
            # Whole-save transfer remains available alongside internal copies.
            whole = plan_copy(source, {'save': DIRECTORY}, target, 'replace')
            apply_copy(whole, root / 'backups')
            self.assertEqual(snapshot(target)['/' + DIRECTORY + '/dhdata12'][1], fixture()['dhdata02'])
