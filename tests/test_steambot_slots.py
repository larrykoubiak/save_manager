"""Synthetic Steambot envelopes, incomplete-source recovery and card transfers."""

from pathlib import Path
import struct
import tempfile
import unittest

from game_plugins import plugin_for
from game_plugins.steambot import SteambotChroniclesUS as Plugin, DIRECTORY, SLOT_SIZE, BODY_SIZE, COMMON_SIZE, TABLE, decode
from save_core.save_copy import apply_copy, plan_copy, plan_slot_copy
from save_core.save_inspection import inspect_save
from save_web.inventory import snapshot_location
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file


def encode(body, common=False):
    size, start = (COMMON_SIZE, 4) if common else (SLOT_SIZE, 16)
    result = bytearray(size)
    struct.pack_into('<I', result, 0, size)
    result[start:start + len(body)] = body
    checksum = sum(struct.unpack('<' + 'I' * (len(body) // 4), body)) & 0xffffffff
    struct.pack_into('<II', result, start + len(body), checksum, 0x6219)
    for offset in range(0, size // 256 * 256, 256):
        block = offset // 256 + 1
        result[offset:offset + 256] = bytes(TABLE[(value + block) & 255] for value in result[offset:offset + 256])
    return bytes(result)


def fixture():
    body = bytearray(BODY_SIZE)
    body[0x4000] = 1
    struct.pack_into('<i', body, 0x401c, 205139)
    empty = encode(bytes(BODY_SIZE))
    return {DIRECTORY: encode(b'settings' + bytes(1992), True),
            **{f'FILE_{i:02}': encode(body) if i == 0 else empty for i in range(12)}}


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


class SteambotSlotTest(unittest.TestCase):
    def test_envelope_checksum_occupancy_and_30_tick_playtime(self):
        data = fixture()
        self.assertIs(plugin_for(DIRECTORY), Plugin)
        self.assertIsNone(plugin_for('BISLPS-25457'))
        # Header bytes independently observed in both real US saves.
        self.assertEqual(data['FILE_00'][:16], bytes.fromhex('14193c34' + '34' * 12))
        slots = Plugin.slots(DIRECTORY, data)
        self.assertEqual([slot['state'] for slot in slots], ['occupied'] + ['empty'] * 11)
        self.assertEqual(slots[0]['label'], '1:53:57 played')
        self.assertEqual(Plugin.summary(DIRECTORY, data)['summary'], '')
        self.assertEqual(decode(data['FILE_00'])[0x10700:0x107e0], bytes(224))

    def test_valid_slot_can_leave_incomplete_source_but_destination_must_be_complete(self):
        data = fixture()
        incomplete = {name: payload for name, payload in data.items() if name not in (DIRECTORY, 'FILE_11')}
        slots = Plugin.slots(DIRECTORY, incomplete)
        self.assertEqual(slots[0]['state'], 'occupied')
        self.assertEqual(slots[-1]['state'], 'invalid')
        self.assertIn('Incomplete save', Plugin.summary(DIRECTORY, incomplete)['summary'])
        result = Plugin.copy_slot(DIRECTORY, incomplete, 1, data, 3)
        self.assertEqual(result, {**data, 'FILE_02': data['FILE_00']})
        self.assertEqual(data, fixture())
        self.assertEqual(Plugin.copy_slot(DIRECTORY, incomplete, 1, result, 3), result)
        with self.assertRaisesRegex(ValueError, 'Destination contains invalid'):
            Plugin.copy_slot(DIRECTORY, data, 1, incomplete, 2)
        no_settings = {**data, DIRECTORY: b''}
        with self.assertRaisesRegex(ValueError, 'shared settings'):
            Plugin.copy_slot(DIRECTORY, data, 1, no_settings, 2)

    def test_corrupt_empty_invalid_sources_and_explicit_replacement(self):
        data = fixture()
        bad = bytearray(data['FILE_00'])
        bad[0x10700] = 99  # Unencrypted tail still participates in the checksum.
        corrupted = {**data, 'FILE_00': bytes(bad)}
        self.assertEqual(Plugin.slots(DIRECTORY, corrupted)[0]['state'], 'invalid')
        for src, dst, number in ((corrupted, data, 1), (data, corrupted, 1), (data, data, 2)):
            with self.assertRaises(ValueError):
                Plugin.copy_slot(DIRECTORY, src, number, dst, 3)
        different_body = bytearray(BODY_SIZE)
        different_body[0x4000] = 1
        different_body[0x401c] = 120
        other = {**data, 'FILE_00': encode(different_body)}
        with self.assertRaisesRegex(ValueError, 'occupied'):
            Plugin.copy_slot(DIRECTORY, other, 1, data, 1)
        self.assertEqual(Plugin.copy_slot(DIRECTORY, other, 1, data, 1, True)['FILE_00'], other['FILE_00'])
        for number in (0, 13, True, 1.0):
            with self.assertRaises(ValueError):
                Plugin.copy_slot(DIRECTORY, data, 1, data, number)

    def test_whole_save_rejects_bad_settings_but_valid_slots_can_be_recovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source.ps2', root / 'target.bin'
            data = fixture()
            corrupt = bytearray(data[DIRECTORY])
            corrupt[100] ^= 1
            for common in (None, bytes(corrupt)):
                with self.subTest(missing=common is None):
                    incomplete = dict(data)
                    if common is None:
                        del incomplete[DIRECTORY]
                    else:
                        incomplete[DIRECTORY] = common
                    card_file(source, [game_save(incomplete)], True)
                    card_file(target, [game_save(data)], False)
                    original = target.read_bytes()
                    with self.assertRaisesRegex(ValueError, 'shared settings'):
                        plan_copy(source, {'save': DIRECTORY}, target, 'replace')
                    self.assertEqual(target.read_bytes(), original)
                    plan = plan_slot_copy(source, {'save': DIRECTORY}, 1, target, 2)
                    apply_copy(plan, root / 'backups')
                    after = snapshot(target)
                    self.assertEqual(after['/' + DIRECTORY + '/FILE_01'][1], data['FILE_00'])
                    self.assertEqual(after['/' + DIRECTORY + '/' + DIRECTORY][1], data[DIRECTORY])

    def test_whole_save_requires_all_slots_and_accepts_complete_save(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source.ps2', root / 'target.bin'
            data = fixture()
            card_file(target, [generic_save('OTHER-GAME', b'untouched')], False)
            original = snapshot(target)
            incomplete = dict(data)
            del incomplete['FILE_11']
            card_file(source, [game_save(incomplete)], True)
            with self.assertRaisesRegex(ValueError, 'invalid save slots'):
                plan_copy(source, {'save': DIRECTORY}, target)
            card_file(source, [game_save(data)], True)
            plan = plan_copy(source, {'save': DIRECTORY}, target)
            apply_copy(plan, root / 'backups')
            after = snapshot(target)
            for key, value in original.items():
                self.assertEqual(after[key], value)
            for filename, payload in data.items():
                self.assertEqual(after['/' + DIRECTORY + '/' + filename][1], payload)

    def test_incomplete_ecc_source_to_raw_destination_preserves_other_files_and_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source.ps2', root / 'target.bin'
            data = fixture()
            incomplete = {name: payload for name, payload in data.items() if name not in (DIRECTORY, 'FILE_11')}
            card_file(source, [game_save(incomplete)], True)
            card_file(target, [game_save(data), generic_save('OTHER-GAME', b'untouched')], False)
            source_bytes, target_bytes = source.read_bytes(), target.read_bytes()
            before = snapshot(target)
            plan = plan_slot_copy(source, {'save': DIRECTORY}, 1, target, 2)
            self.assertEqual(target.read_bytes(), target_bytes)
            backup = apply_copy(plan, root / 'backups')
            self.assertEqual(backup.read_bytes(), target_bytes)
            self.assertEqual(source.read_bytes(), source_bytes)
            after = snapshot(target)
            for path in before:
                self.assertEqual(after[path][0], before[path][0])
                if path != '/' + DIRECTORY + '/FILE_01':
                    self.assertEqual(after[path], before[path])
            records = snapshot_location(dict(path=str(root), suffix='.ps2', marker=''))[0]['saves']
            self.assertIn('Incomplete save', records[0]['summary'])
            self.assertEqual(records[0]['slots'][0]['label'], '1:53:57 played')
            report = inspect_save(source, {'save': DIRECTORY, 'title': 'Steambot', 'updated': None}, 'Test')
            self.assertIn('1:53:57 played', str(report))
            with self.assertRaises(ValueError):
                plan_slot_copy(target, {'save': DIRECTORY}, 1, source, 2)
