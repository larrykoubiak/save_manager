import struct
from pathlib import Path
import tempfile
import unittest

from save_core.compare_saves import scan
from game_plugins import merger_for_game, plugin_for, slot_number
from game_plugins.ar_tonelico import ArTonelicoUS, SAVE_SIZE, SLOT_COUNT, SLOT_SIZE, checksum
from save_core.save_copy import plan_copy
from save_core.save_inspection import inspect_save
from test_save_copy import generic_save
from test_save_merge import card_file


DIRECTORY = 'BASLUS-21445AT'


def fixture():
    empty = b'\xff' + bytes(SLOT_SIZE - 1)
    data = bytearray(empty * SLOT_COUNT)
    # Fixed checksum vectors for synthetic records; no private saves bundled.
    for index, ticks, money, level, expected in (
            (0, 28519, 500, 10, 0xffffde19), (4, 603784, 586, 21, 0xffffe011)):
        record = bytearray(SLOT_SIZE)
        record[0] = index
        for offset, value in ((1, expected), (5, 0x7fffffff), (SLOT_SIZE - 4, 0x7fffffff),
                              (0x39, ticks), (0x41, money)):
            struct.pack_into('<I', record, offset, value)
        struct.pack_into('<H', record, 0x51, level)
        data[index * SLOT_SIZE:(index + 1) * SLOT_SIZE] = record
    return bytes(data)


class ArTonelicoTest(unittest.TestCase):
    def test_two_internal_slots_golden_checksums_and_values(self):
        data = fixture()
        self.assertEqual(checksum(data[:SLOT_SIZE]), 0xffffde19)
        self.assertEqual(checksum(data[4 * SLOT_SIZE:5 * SLOT_SIZE]), 0xffffe011)
        result = ArTonelicoUS.decode(DIRECTORY, data)
        sections = {s.title: dict(s.fields) for s in result.sections}
        self.assertEqual(sections['Progress']['Slot numbers'], '1, 5')
        self.assertEqual(sections['Progress']['Occupied slots'], '2 / 12')
        self.assertEqual(sections['Slot 1']['Playtime'], '0:07:55')
        self.assertEqual(sections['Slot 5']['Playtime'], '2:47:43')
        self.assertEqual(sections['Slot 5']['Money'], '586')
        self.assertEqual(sections['Slot 5']['Lyner level'], '21')
        self.assertEqual(data, fixture())

    def test_last_slot_and_empty_gamefile(self):
        empty = b'\xff' + bytes(SLOT_SIZE - 1)
        data = bytearray(empty * SLOT_COUNT)
        record = bytearray(fixture()[:SLOT_SIZE])
        record[0] = 11
        struct.pack_into('<I', record, 1, checksum(record))
        data[-SLOT_SIZE:] = record
        sections = {s.title: dict(s.fields) for s in ArTonelicoUS.decode(DIRECTORY, data).sections}
        self.assertEqual(sections['Progress']['Slot numbers'], '12')
        self.assertEqual(sections['Slot 12']['Playtime'], '0:07:55')
        sections = {s.title: dict(s.fields) for s in ArTonelicoUS.decode(DIRECTORY, empty * SLOT_COUNT).sections}
        self.assertEqual(sections['Progress']['Occupied slots'], '0 / 12')

    def test_corrupt_slot_is_not_decoded_and_other_verified_slots_remain_visible(self):
        data = bytearray(fixture())
        data[0x39] ^= 1
        sections = {s.title: dict(s.fields) for s in ArTonelicoUS.decode(DIRECTORY, data).sections}
        self.assertNotIn('Progress', sections)
        self.assertEqual(sections['Slot 1']['Status'], 'Checksum mismatch')
        self.assertNotIn('Playtime', sections['Slot 1'])
        self.assertEqual(sections['Slot 5']['Playtime'], '2:47:43')
        self.assertEqual(sections['Integrity']['Invalid slots'], '1')

    def test_valid_checksum_does_not_override_bad_slot_id_or_boundary(self):
        for offset in (0, 5, SLOT_SIZE - 4):
            data = bytearray(fixture())
            data[offset] ^= 1
            struct.pack_into('<I', data, 1, checksum(data[:SLOT_SIZE]))
            sections = {s.title: dict(s.fields) for s in ArTonelicoUS.decode(DIRECTORY, data).sections}
            self.assertNotIn('Progress', sections)
            self.assertNotIn('Playtime', sections['Slot 1'])

    def test_registry_rejects_other_regions_and_does_not_enable_merge_or_directory_slots(self):
        self.assertIs(plugin_for(DIRECTORY), ArTonelicoUS)
        self.assertIsNone(plugin_for('BASLUS-21788AT2'))
        self.assertIsNone(plugin_for('BESLES-54586AT'))
        self.assertIsNone(slot_number(DIRECTORY))
        self.assertIsNone(merger_for_game('SLUS-21445'))
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            ArTonelicoUS.decode('BISLPS-25604AT', fixture())
        with self.assertRaisesRegex(ValueError, 'Expected'):
            ArTonelicoUS.decode(DIRECTORY, fixture()[:-1])

    def test_inspection_and_copy_use_gamefile_not_configuration_file(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source, target = folder / 'source.ps2', folder / 'target.bin'
            save = generic_save(DIRECTORY, fixture())
            entry, data = save.get_file(0)
            entry[8] = b'GAMEFILE'
            save.set_file(0, entry, data)
            card_file(source, [save], True)
            card_file(target, [], False)
            _, records = scan(folder, '.ps2')
            decoded = inspect_save(source, records[0], 'Emulator')
            self.assertIn('Slot 5', [s.title for s in decoded.sections])
            plan = plan_copy(source, records[0], target)
            self.assertTrue(plan.candidate)
            self.assertEqual(source.stat().st_size, 8650752)
            bad = bytearray(data)
            bad[0x39] ^= 1
            save.set_file(0, entry, bytes(bad))
            card_file(source, [save], True)
            _, records = scan(folder, '.ps2')
            with self.assertRaisesRegex(ValueError, 'slot validation failed'):
                plan_copy(source, records[0], target)
