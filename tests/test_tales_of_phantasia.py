"""Translated PS1 Tales saves: playtime, renumbering and library upgrades."""

from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from game_plugins import plugin_for
from game_plugins.ps1.tales_of_phantasia import (
    TalesOfPhantasiaTranslated as Plugin, PREFIX, TITLE_PREFIX, NUMBER_OFFSET,
    PLAYTIME_OFFSET, CHECKSUM_OFFSET, checksum, slot_label,
)
from save_core.backends import get_backend
from save_core.backends.base import SaveBundle, SaveFile
from save_core.backends.ps1.card import Card, blank_card
from test_ftp import vmp
from test_ps1 import fixture, payload


def save_id(number):
    return PREFIX + chr(ord('A') + number - 1)


def tales_save(number=1, ticks=143923):
    data = bytearray(bytes(range(256)) * 32)
    data[:4] = b'SC\x12\x01'
    data[4:NUMBER_OFFSET] = TITLE_PREFIX
    data[NUMBER_OFFSET:NUMBER_OFFSET + 4] = slot_label(number)
    data[0x36:0x44] = '　　　０：３９'.encode('shift_jis')
    struct.pack_into('<I', data, 0x508, ticks)
    struct.pack_into('<I', data, PLAYTIME_OFFSET, ticks)
    struct.pack_into('<I', data, CHECKSUM_OFFSET, checksum(data))
    return bytes(data)


class TalesOfPhantasiaTest(unittest.TestCase):
    def test_registry_playtime_and_checksums(self):
        self.assertIs(plugin_for(save_id(1), 'ps1'), Plugin)
        self.assertIsNone(plugin_for(save_id(1), 'ps2'))
        for number in range(1, 16):
            self.assertEqual(Plugin.slot_number(save_id(number)), number)
        for name in (PREFIX + 'P', PREFIX + 'AA', PREFIX + '@', 'BISLPSP01771.TOP.A'):
            self.assertIsNone(plugin_for(name, 'ps1'))
        self.assertEqual(checksum(bytes(range(256)) * 32), 1_043_466)
        data = tales_save()
        self.assertEqual(Plugin.summary(save_id(1), data)['summary'], 'Playtime 0:39:58')
        slot = Plugin.library_slots(SaveBundle(save_id(1), (SaveFile('data', data),)))[0]
        self.assertEqual((slot.key, slot.number, slot.playtime_seconds), ('whole', 1, 2398))
        self.assertEqual(slot.files, {'data': data})

    def test_every_destination_preserves_game_bytes_and_metadata(self):
        data = tales_save()
        bundle = SaveBundle(save_id(1), (SaveFile('data', data, {'file': 'opaque'}),), {'blocks': [7]})
        self.assertIs(Plugin.prepare_slot(bundle, 1), bundle)
        for number in range(1, 16):
            copy = Plugin.prepare_slot(bundle, number)
            result = copy.file_data()['data']
            Plugin.validate_save(copy.save_id, result)
            self.assertEqual(copy.save_id, save_id(number))
            self.assertEqual(copy.metadata, bundle.metadata)
            self.assertEqual(copy.files[0].metadata, bundle.files[0].metadata)
            changed = {i for i, (a, b) in enumerate(zip(data, result)) if a != b}
            self.assertTrue(changed <= set(range(0x32, 0x36)) | set(range(0x1ffc, 0x2000)))
            self.assertEqual(result[0x36:0x1ffc], data[0x36:0x1ffc])
        self.assertEqual(bundle.file_data()['data'], data)

    def test_raw_and_signed_sources_empty_targets_and_conflicts(self):
        backend = get_backend('ps1')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for extension in ('mcr', 'VMP'):
                source, target = root / ('source.' + extension), root / 'target.mcr'
                image = fixture([(save_id(1), (7,), tales_save())])
                source.write_bytes(vmp(image) if extension == 'VMP' else image)
                target.write_bytes(blank_card())
                plan = backend.plan_slot_copy(source, {'save': save_id(1)}, 1, target, 15)
                card = Card(plan.candidate)
                self.assertEqual(set(card.entries), {save_id(15)})
                Plugin.validate_save(save_id(15), card.payload(card.entries[save_id(15)]))
                self.assertEqual(target.read_bytes(), blank_card())
                occupied = fixture([(save_id(1), (4,), tales_save(ticks=31946)), ('OTHER', (11,), payload())])
                target.write_bytes(occupied)
                plan = backend.plan_copy(source, {'save': save_id(1)}, target, 'keep_both')
                self.assertEqual(plan.destination_save, save_id(2))
                self.assertEqual(Card(plan.candidate).signature('OTHER'), Card(occupied).signature('OTHER'))
                with self.assertRaisesRegex(ValueError, 'occupied'):
                    backend.plan_slot_copy(source, {'save': save_id(1)}, 1, target, 1)
                self.assertEqual(target.read_bytes(), occupied)

    def test_invalid_layouts_and_targets_are_rejected(self):
        data = tales_save()
        bundle = SaveBundle(save_id(1), (SaveFile('data', data),))
        for number in (0, 16, True, '2'):
            with self.assertRaises(ValueError):
                Plugin.prepare_slot(bundle, number)
        with self.assertRaisesRegex(ValueError, 'slot label'):
            Plugin.validate_save(save_id(2), data)
        for offset in (0, 0x600, 0x1ffc):
            damaged = bytearray(data)
            damaged[offset] ^= 1
            with self.assertRaises(ValueError):
                Plugin.validate_save(save_id(1), bytes(damaged))
        damaged = bytearray(data)
        damaged[0x508] ^= 1
        struct.pack_into('<I', damaged, CHECKSUM_OFFSET, checksum(damaged))
        with self.assertRaisesRegex(ValueError, 'playtime fields'):
            Plugin.validate_save(save_id(1), bytes(damaged))
        for bad in (data[:-1], data + b'\0', bytearray(data)):
            with self.assertRaises(ValueError):
                Plugin.validate_save(save_id(1), bad)

    def test_new_plugin_enriches_existing_library_without_duplicate_or_new_head(self):
        from test_ps1_web import PS1WebTest
        case = PS1WebTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.cards[0].write_bytes(fixture([(save_id(1), (7,), tales_save())]))
        with patch('game_plugins.library.plugin_for', return_value=None):
            case.scan(0)
        library = case.service.library
        game = next(g for g in library.inventory()['games'] if g['game_key'] == 'SLPS-01770')
        version = game['saves'][0]
        old_slot = game['slots'][0]
        self.assertIsNone(version['playtime_seconds'])
        case.scan(0)
        game = next(g for g in library.inventory()['games'] if g['game_key'] == 'SLPS-01770')
        self.assertEqual(len(game['saves']), 1)
        self.assertEqual(game['saves'][0]['id'], version['id'])
        self.assertEqual(game['saves'][0]['playtime_seconds'], 2398)
        self.assertEqual(game['slots'][0]['id'], old_slot['id'])
        self.assertEqual(game['slots'][0]['number'], 1)
        self.assertEqual(game['slots'][0]['current']['id'], old_slot['current']['id'])
        library.download(version['id'])
        save = next(s for s in case.service.inventory()['saves'] if s['directory'] == save_id(1))
        self.assertEqual(save['card_slots'], dict(number=1, count=15, family=PREFIX, valid=True))
        status, preview = case.request('/api/transfers/preview', dict(
            save_id=save['id'], source_slot=1, destination_card_id=case.ids[1], destination_slot=15))
        self.assertEqual(status, 201, preview)
        self.assertEqual(preview['destination_save'], save_id(15))
