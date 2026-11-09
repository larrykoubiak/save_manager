"""Synthetic Katamari slots and complete-card copies; no private game data."""

from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from game_plugins import plugin_for
from game_plugins.katamari import KatamariDamacyUS as Plugin, DIRECTORY, SLOT_SIZE, OPTIONS_SIZE
from save_core.save_copy import apply_copy, plan_slot_copy, plan_copy
from save_web.inventory import snapshot_location
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file


def wrapped(payload):
    return struct.pack('<II', zlib.crc32(payload), 1) + payload


def fixture():
    empty = wrapped(bytes(SLOT_SIZE - 8))
    record = bytearray(SLOT_SIZE - 8)
    struct.pack_into('<I', record, 4, 0x12345678)
    record[100:110] = b'progress01'
    return {DIRECTORY: wrapped(b'options!' + bytes(OPTIONS_SIZE - 16)),
            'katamari_save01': wrapped(record), 'katamari_save02': empty, 'katamari_save03': empty}


def game_save(data):
    save = generic_save(DIRECTORY, b'')
    template = list(save.get_file(0)[0])
    directory = list(save.get_directory())
    directory[2] = len(data)
    save.set_directory(directory)
    for index, (name, payload) in enumerate(data.items()):
        entry = list(template)
        entry[8], entry[2] = name.encode(), len(payload)
        save.set_file(index, entry, payload)
    return save


class KatamariSlotTest(unittest.TestCase):
    def test_verified_occupancy_and_relocation_preserve_options_other_slots_and_inputs(self):
        self.assertIs(plugin_for(DIRECTORY), Plugin)
        self.assertIsNone(plugin_for('BASLUS-21230Opt'))
        source = fixture()
        slots = Plugin.slots(DIRECTORY, source)
        self.assertEqual([s['state'] for s in slots], ['occupied', 'empty', 'empty'])
        target = {**source, DIRECTORY: wrapped(b'localcfg' + bytes(OPTIONS_SIZE - 16))}
        result = Plugin.copy_slot(DIRECTORY, source, 1, target, 2)
        self.assertEqual(source, fixture())
        self.assertEqual(result, {**target, 'katamari_save02': source['katamari_save01']})
        self.assertEqual(Plugin.slots(DIRECTORY, result)[1]['sha256'], slots[0]['sha256'])
        self.assertEqual(Plugin.copy_slot(DIRECTORY, source, 1, result, 2), result)
        changed = bytearray(source['katamari_save01'][8:])
        changed[100] ^= 1
        different = {**source, 'katamari_save01': wrapped(changed)}
        with self.assertRaisesRegex(ValueError, 'occupied'):
            Plugin.copy_slot(DIRECTORY, different, 1, source, 1)
        self.assertEqual(Plugin.copy_slot(DIRECTORY, different, 1, source, 1, True)['katamari_save01'], different['katamari_save01'])

    def test_corruption_missing_files_empty_sources_and_wrong_region_refused(self):
        data = fixture()
        damaged = {**data, 'katamari_save01': data['katamari_save01'][:-1] + b'X'}
        self.assertEqual(Plugin.slots(DIRECTORY, damaged)[0]['state'], 'invalid')
        for src, dst, slot in ((damaged, data, 1), (data, damaged, 1), (data, data, 2)):
            with self.assertRaises(ValueError):
                Plugin.copy_slot(DIRECTORY, src, slot, dst, 3)
        for number in (0, 4, True, 1.0, '1'):
            with self.assertRaises(ValueError):
                Plugin.copy_slot(DIRECTORY, data, 1, data, number)
        for invalid in ({}, {**data, DIRECTORY: bytes(OPTIONS_SIZE)}, {**data, 'katamari_save03': b''}):
            with self.assertRaises(ValueError):
                Plugin.copy_slot(DIRECTORY, data, 1, invalid, 3)
        with self.assertRaises(ValueError):
            Plugin.slots('BASLUS-21230Opt', data)
        with self.assertRaises(ValueError):
            Plugin.copy_slot(DIRECTORY, data, 1, data, 2, replace=1)

    def test_raw_ecc_backup_same_card_whole_save_and_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source.ps2', root / 'target.bin'
            card_file(source, [game_save(fixture())], True)
            card_file(target, [game_save(fixture()), generic_save('OTHER-GAME', b'preserve')], False)
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
                if path != '/' + DIRECTORY + '/katamari_save03':
                    self.assertEqual(after[path], before[path])
            apply_copy(plan_slot_copy(source, {'save': DIRECTORY}, 1, source, 2), root / 'backups')
            records = snapshot_location(dict(path=str(root), suffix='.ps2', marker=''))[0]['saves']
            self.assertEqual([s['state'] for s in records[0]['slots']], ['occupied', 'occupied', 'empty'])
            apply_copy(plan_copy(source, {'save': DIRECTORY}, target, 'replace'), root / 'backups')
            self.assertEqual(snapshot(target)['/' + DIRECTORY + '/katamari_save02'][1], fixture()['katamari_save01'])
