"""Four numbered PS1 layouts: preservation, integrity and library enrichment."""

from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from game_plugins import plugin_for
from game_plugins.ps1.fft import FinalFantasyTacticsUS
from game_plugins.ps1.saga_frontier_2 import SaGaFrontier2US
from game_plugins.ps1.tomba import TombaUS
from game_plugins.ps1.xenogears import XenogearsUS
from save_core.backends import get_backend
from save_core.backends.base import SaveBundle, SaveFile
from save_core.backends.ps1.card import Card, blank_card
from test_ftp import vmp
from test_ps1 import fixture, payload


GAMES = (FinalFantasyTacticsUS, SaGaFrontier2US, TombaUS, XenogearsUS)


def save_id(plugin, number=1):
    return plugin.layout.prefix + plugin.layout.suffixes[number - 1]


def game_save(plugin, number=1, marker=17):
    layout = plugin.layout
    data = bytearray([marker] * 8192)
    data[:512] = bytes(512)
    data[:len(layout.header)] = layout.header
    label = layout.slot_label(number)
    data[layout.label_offset:layout.label_offset + len(label)] = label
    if layout.slot_index_offset is not None:
        data[layout.slot_index_offset] = number - 1
    if layout.playtime_offset is not None:
        struct.pack_into('<I', data, layout.playtime_offset, 27321 * layout.ticks_per_second + layout.ticks_per_second - 1)
    if plugin is FinalFantasyTacticsUS:
        data[0x113] = 9
        data[0x117] = 4
    elif plugin is SaGaFrontier2US:
        data[0x50:0x56] = b'\x01\0CRD0'
        data[0x180] = 0x4b
        data[0x184:0x198] = bytes.fromhex('a0a8b6b7aca4').ljust(20, b'\0')  # Westia
        data[0x558:0x56c] = bytes.fromhex('a0acafff94b1acaaabb7b6').ljust(20, b'\0')  # Wil Knights
        struct.pack_into('<H', data, 0x574, 1235)
    elif plugin is XenogearsUS:
        data[0x24:0x44] = bytes(32)
        title = 'Ｍｏｕｎｔａｉｎ　Ｐａｔｈ'.encode('shift_jis')
        data[0x24:0x24 + len(title)] = title
    struct.pack_into(layout.checksum_format, data, layout.checksum_offset, layout.checksum(data))
    return bytes(data)


class MoreNumberedPS1Test(unittest.TestCase):
    def test_registration_numbering_checksum_vectors_and_playtime(self):
        vector = bytes(range(256)) * 32
        for plugin, expected in zip(GAMES, (15, 63, 255, 129)):
            with self.subTest(game=plugin.name):
                self.assertIs(plugin_for(save_id(plugin), 'ps1'), plugin)
                self.assertIsNone(plugin_for(save_id(plugin), 'ps2'))
                self.assertIsNone(plugin_for(save_id(plugin) + 'extra', 'ps1'))
                self.assertEqual(plugin.layout.checksum(vector), expected)
                for number in range(1, 16):
                    self.assertEqual(plugin.slot_number(save_id(plugin, number)), number)
                bundle = SaveBundle(save_id(plugin), (SaveFile('data', game_save(plugin)),))
                slot, = plugin.library_slots(bundle)
                self.assertEqual((slot.key, slot.number), ('whole', 1))
                self.assertEqual(slot.playtime_seconds, 27321)
                if plugin is SaGaFrontier2US:
                    self.assertIn('1235 · Westia · Wil Knights', slot.label)
                self.assertIn('7:35:21', slot.label)
        self.assertEqual(save_id(XenogearsUS, 11), 'BASLUS-00664:')
        self.assertEqual(save_id(XenogearsUS, 15), 'BASLUS-00664>')
        self.assertEqual(save_id(SaGaFrontier2US, 11), 'BASLUSP00933-SAGA2-A')
        self.assertEqual(TombaUS.layout.slot_label(1), '００'.encode('shift_jis'))
        self.assertIsNone(plugin_for('BESLES-01370G005p@DA', 'ps1'))

    def test_all_positions_preserve_every_unrelated_byte_and_metadata(self):
        for plugin in GAMES:
            layout = plugin.layout
            data = game_save(plugin)
            bundle = SaveBundle(save_id(plugin), (SaveFile('data', data, b'file metadata'),), b'card metadata')
            self.assertIs(plugin.prepare_slot(bundle, 1), bundle)
            allowed = set(range(layout.label_offset, layout.label_offset + 4))
            allowed.update(range(layout.checksum_offset, layout.checksum_offset + struct.calcsize(layout.checksum_format)))
            if layout.slot_index_offset is not None:
                allowed.add(layout.slot_index_offset)
            for number in range(1, 16):
                with self.subTest(game=plugin.name, number=number):
                    copied = plugin.prepare_slot(bundle, number)
                    result = copied.files[0].data
                    plugin.validate_save(copied.save_id, result)
                    self.assertEqual(copied.save_id, save_id(plugin, number))
                    self.assertEqual((copied.metadata, copied.files[0].metadata), (bundle.metadata, bundle.files[0].metadata))
                    self.assertTrue({i for i, (a, b) in enumerate(zip(data, result)) if a != b} <= allowed)
                    self.assertEqual(plugin.playtime_seconds(result), plugin.playtime_seconds(data))
            self.assertEqual(bundle.files[0].data, data)

    def test_raw_and_vmp_sources_keep_both_and_reject_conflicts_without_writing(self):
        backend = get_backend('ps1')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root/'source.mcr', root/'target.mcr'
            for plugin in GAMES:
                image = fixture([(save_id(plugin), (7,), game_save(plugin))])
                original = fixture([(save_id(plugin), (4,), game_save(plugin, marker=35)), ('OTHER', (11,), payload())])
                for extension in ('mcr', 'VMP'):
                    with self.subTest(game=plugin.name, extension=extension):
                        source = root/('source.' + extension)
                        source.write_bytes(vmp(image) if extension == 'VMP' else image)
                        target.write_bytes(original)
                        plan = backend.plan_copy(source, {'save': save_id(plugin)}, target, 'keep_both')
                        self.assertEqual(plan.destination_save, save_id(plugin, 2))
                        self.assertEqual(Card(plan.candidate).signature('OTHER'), Card(original).signature('OTHER'))
                        with self.assertRaisesRegex(ValueError, 'occupied'):
                            backend.plan_slot_copy(source, {'save': save_id(plugin)}, 1, target, 1)
                        target.write_bytes(blank_card())
                        plan = backend.plan_slot_copy(source, {'save': save_id(plugin)}, 1, target, 15)
                        card = Card(plan.candidate)
                        plugin.validate_save(save_id(plugin, 15), card.payload(card.entries[save_id(plugin, 15)]))
                        self.assertEqual(target.read_bytes(), blank_card())
                        self.assertEqual(source.read_bytes(), vmp(image) if extension == 'VMP' else image)

    def test_corrupt_payloads_slot_markers_and_destinations_are_rejected(self):
        for plugin in GAMES:
            data = game_save(plugin)
            bundle = SaveBundle(save_id(plugin), (SaveFile('data', data),))
            for number in (0, 16, True, '2'):
                with self.subTest(game=plugin.name, destination=number), self.assertRaises(ValueError):
                    plugin.prepare_slot(bundle, number)
            for offset in (0, 0x600):
                damaged = bytearray(data)
                damaged[offset] ^= 1
                with self.subTest(game=plugin.name, offset=offset), self.assertRaises(ValueError):
                    plugin.validate_save(bundle.save_id, bytes(damaged))
            if plugin.layout.slot_index_offset is not None:
                damaged = bytearray(data)
                damaged[plugin.layout.slot_index_offset] = 14
                layout = plugin.layout
                struct.pack_into(layout.checksum_format, damaged, layout.checksum_offset, layout.checksum(damaged))
                with self.assertRaisesRegex(ValueError, 'stored slot number'):
                    plugin.validate_save(bundle.save_id, bytes(damaged))

    def test_fft_matches_only_loader_checked_parity_bits(self):
        data = bytearray(game_save(FinalFantasyTacticsUS))
        # The loader deliberately ignores parity for the header/summary and the
        # four unused bits. Do not rewrite them while moving the save.
        data[0x118] ^= 0xe0
        data[0x11f] ^= 0x0f
        bundle = SaveBundle(save_id(FinalFantasyTacticsUS), (SaveFile('data', bytes(data)),))
        copied = FinalFantasyTacticsUS.prepare_slot(bundle, 15)
        self.assertEqual(copied.files[0].data[0x118:0x120], data[0x118:0x120])
        self.assertEqual(copied.files[0].data[0x100], 14)
        for offset in (0x180, 0x1dff):
            damaged = bytearray(data)
            damaged[offset] ^= 1
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                FinalFantasyTacticsUS.validate_save(bundle.save_id, bytes(damaged))

    def test_plugin_upgrade_retains_library_versions_settings_and_metadata(self):
        from test_ps1_web import PS1WebTest
        case = PS1WebTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.cards[0].write_bytes(fixture([(save_id(p), (i + 2,), game_save(p)) for i, p in enumerate(GAMES)]))
        with patch('game_plugins.library.plugin_for', return_value=None):
            case.scan(0)
        library = case.service.library
        before = {g['game_key']: g for g in library.inventory()['games']}
        game = before['SLUS-00933']
        library.game_settings(game['id'], {'card_ports': 'both', 'card_port_note': 'Tested on hardware'})
        case.scan(0)
        for game in library.inventory()['games']:
            if game['game_key'] not in ('SCUS-94221', 'SLUS-00933', 'SCUS-94236', 'SLUS-00664'):
                continue
            old = before[game['game_key']]
            self.assertIn(game['title'], [plugin.name for plugin in GAMES])
            self.assertEqual(len(game['saves']), 1)
            self.assertEqual(game['saves'][0]['id'], old['saves'][0]['id'])
            self.assertEqual(game['slots'][0]['id'], old['slots'][0]['id'])
            self.assertEqual(game['slots'][0]['number'], 1)
            if game['game_key'] == 'SLUS-00933':
                self.assertIn('1235 · Westia · Wil Knights', game['saves'][0]['label'])
                self.assertEqual((game['card_ports'], game['card_port_note']), ('both', 'Tested on hardware'))
            self.assertEqual(game['saves'][0]['playtime_seconds'], 27321)
            library.download(game['saves'][0]['id'])
        for save in case.service.inventory()['saves']:
            plugin = plugin_for(save['directory'], 'ps1')
            if plugin not in GAMES:
                continue
            self.assertEqual(save['card_slots'], dict(number=1, count=15, family=plugin.library_family, valid=True))
            status, preview = case.request('/api/transfers/preview', dict(
                save_id=save['id'], source_slot=1, destination_card_id=case.ids[1], destination_slot=15))
            self.assertEqual(status, 201, preview)
            self.assertEqual(preview['destination_save'], save_id(plugin, 15))
