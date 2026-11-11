"""Slot-level regressions on synthetic payloads and complete raw/ECC cards."""

import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

from save_core.compare_saves import scan
from game_plugins.ps2.ar_tonelico import ArTonelicoUS as Plugin, SLOT_SIZE, SLOT_COUNT, checksum
from save_core.save_copy import apply_copy, plan_slot_copy, tree_snapshot
from test_ar_tonelico import fixture, DIRECTORY
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file
from save_core.backends.ps2.native.ps2mc import ps2mc


def game_save(payload, configuration=b'destination preferences and unlocks'):
    save = generic_save(DIRECTORY, payload)
    entry, data = save.get_file(0)
    directory = list(save.get_directory())
    directory[2] = 2
    save.set_directory(directory)
    entry = list(entry)
    entry[8] = b'GAMEFILE'
    save.set_file(0, entry, data)
    config = list(entry)
    config[8], config[2] = DIRECTORY.encode(), len(configuration)
    save.set_file(1, config, configuration)
    return save


def empty_payload():
    return (b'\xff' + bytes(SLOT_SIZE - 1)) * SLOT_COUNT


class SlotPluginTest(unittest.TestCase):
    def test_relocation_rewrites_only_index_checksum_and_selected_destination_record(self):
        source, destination = fixture(), fixture()
        result = Plugin.copy_slot(DIRECTORY, source, 1, destination, 12)
        self.assertEqual(source, fixture())
        self.assertEqual(destination, fixture())
        self.assertEqual(result[:-SLOT_SIZE], destination[:-SLOT_SIZE])
        copied = result[-SLOT_SIZE:]
        self.assertEqual(copied[0], 11)
        self.assertEqual(copied[5:], source[5:SLOT_SIZE])
        self.assertEqual(struct.unpack_from('<I', copied, 1)[0], checksum(copied))
        slots = Plugin.slots(DIRECTORY, result)
        self.assertEqual([s['number'] for s in slots if s['state'] == 'occupied'], [1, 5, 12])
        self.assertEqual(slots[11]['sha256'], slots[0]['sha256'])

    def test_occupied_destination_needs_explicit_replace_and_identical_is_noop(self):
        source = fixture()
        with self.assertRaisesRegex(ValueError, 'occupied'):
            Plugin.copy_slot(DIRECTORY, source, 1, source, 5)
        result = Plugin.copy_slot(DIRECTORY, source, 1, source, 5, replace=True)
        self.assertEqual(result[:4 * SLOT_SIZE], source[:4 * SLOT_SIZE])
        self.assertEqual(result[5 * SLOT_SIZE:], source[5 * SLOT_SIZE:])
        self.assertEqual(Plugin.copy_slot(DIRECTORY, source, 1, result, 5), result)

    def test_invalid_empty_out_of_range_and_wrong_layout_refused(self):
        for slot in (0, 13, True, 1.0, '1', None):
            with self.subTest(slot=slot), self.assertRaises(ValueError):
                Plugin.copy_slot(DIRECTORY, fixture(), slot, fixture(), 2)
            with self.subTest(destination_slot=slot), self.assertRaises(ValueError):
                Plugin.copy_slot(DIRECTORY, fixture(), 1, fixture(), slot)
        with self.assertRaisesRegex(ValueError, 'empty or invalid'):
            Plugin.copy_slot(DIRECTORY, fixture(), 2, fixture(), 3)
        with self.assertRaises(ValueError):
            Plugin.copy_slot('BESLES-54586AT', fixture(), 1, fixture(), 3)
        with self.assertRaises(ValueError):
            Plugin.copy_slot(DIRECTORY, fixture()[:-1], 1, fixture(), 3)
        bad = bytearray(fixture())
        bad[0x39] ^= 1
        with self.assertRaisesRegex(ValueError, 'empty or invalid'):
            Plugin.copy_slot(DIRECTORY, bad, 1, fixture(), 3)
        with self.assertRaisesRegex(ValueError, 'Destination contains invalid'):
            Plugin.copy_slot(DIRECTORY, fixture(), 1, bad, 3)
        # A verified source slot remains usable even if another source slot is bad.
        result = Plugin.copy_slot(DIRECTORY, bad, 5, empty_payload(), 3)
        self.assertEqual(Plugin.slots(DIRECTORY, result)[2]['state'], 'occupied')


class SlotCardTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source.ps2'
        self.target = self.root / 'target.bin'
        card_file(self.source, [game_save(fixture(), b'source preferences')], True)
        card_file(self.target, [game_save(fixture()), generic_save('OTHER-GAME', b'untouched')], False)
        self.record = scan(self.root, '.ps2')[1][0]

    def test_preview_backup_reopen_preserve_other_slots_configuration_and_files(self):
        before, source_bytes = self.target.read_bytes(), self.source.read_bytes()
        original_tree = snapshot(self.target)
        plan = plan_slot_copy(self.source, self.record, 1, self.target, 12)
        self.assertEqual(self.target.read_bytes(), before)
        backup = apply_copy(plan, self.root / 'backups')
        self.assertEqual(backup.read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(self.target.stat().st_size, len(before))
        after = snapshot(self.target)
        payload_path = '/' + DIRECTORY + '/GAMEFILE'
        for path in original_tree:
            if path != payload_path:
                self.assertEqual(after[path], original_tree[path])
        self.assertEqual(after[payload_path][1][:-SLOT_SIZE], fixture()[:-SLOT_SIZE])
        self.assertEqual(Plugin.slots(DIRECTORY, after[payload_path][1])[-1]['state'], 'occupied')
        report = json.loads((backup.parent / 'copy.json').read_text())
        self.assertEqual((report['source_slot'], report['destination_slot']), (1, 12))
        # Reverse raw -> ECC also keeps the source and all other files intact.
        reverse = plan_slot_copy(self.target, {'save':DIRECTORY}, 12, self.source, 2)
        apply_copy(reverse, self.root / 'backups')
        self.assertEqual(self.source.stat().st_size, len(source_bytes))

    def test_same_card_copy_and_replacement(self):
        plan = plan_slot_copy(self.source, self.record, 1, self.source, 2)
        apply_copy(plan, self.root / 'backups')
        payload = snapshot(self.source)['/' + DIRECTORY + '/GAMEFILE'][1]
        self.assertEqual([s['number'] for s in Plugin.slots(DIRECTORY,payload) if s['state']=='occupied'], [1,2,5])
        with self.assertRaisesRegex(ValueError, 'occupied'):
            plan_slot_copy(self.source, {'save':DIRECTORY}, 5, self.source, 2)
        replacement = plan_slot_copy(self.source, {'save':DIRECTORY}, 5, self.source, 2, 'replace')
        apply_copy(replacement, self.root / 'backups')
        self.assertEqual(plan_slot_copy(self.source, {'save':DIRECTORY}, 5, self.source, 2).action, 'Already identical')

    def test_stale_cards_and_missing_destination_save_refused(self):
        plan = plan_slot_copy(self.source, self.record, 1, self.target, 2)
        before = self.target.read_bytes()
        self.target.write_bytes(before + b'concurrent change')
        with self.assertRaisesRegex(ValueError, 'changed since the preview'):
            apply_copy(plan, self.root / 'backups')
        self.assertFalse((self.root / 'backups').exists())
        card_file(self.target, [], False)
        with self.assertRaisesRegex(ValueError, 'existing Ar tonelico'):
            plan_slot_copy(self.source, self.record, 1, self.target, 2)
