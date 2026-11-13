"""MGS story-save integrity, naming, keep-both copies and library upgrades."""

from pathlib import Path
from dataclasses import replace
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from game_plugins import plugin_for
from game_plugins.ps1.mgs import MetalGearSolidUS as Plugin, PREFIX, SNAPSHOT_OFFSETS, SNAPSHOT_SIZE
from game_plugins.ps1.saga_frontier_2 import SaGaFrontier2US, decode_text
from save_core.backends import get_backend
from save_core.backends.base import SaveBundle, SaveFile
from save_core.backends.ps1.card import Card
from save_core.save_copy import apply_copy
from test_ftp import vmp
from test_ps1 import fixture, payload


def save_id(seconds=3006, level=1, disc=1, letter='A', clear=False):
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours > 99:
        hours = minutes = 99
    hour1 = ord(':') if clear else ord('0') + hours // 10
    if level == -1:
        hour1 |= 0x40
    difficulty = max(level, 0)
    return (PREFIX + 'G' + chr(hour1) + ('@' if clear else str(hours % 10)) +
            chr(ord('0') + minutes // 10 + ((difficulty & 2) << 5)) +
            chr(ord('0') + minutes % 10 + ((difficulty & 1) << 6)) +
            chr(ord('@') + (disc - 1) * 8) + ('A' if clear else 'D') + letter)


def mgs_save(seconds=3006, level=1, disc=1, marker=17):
    data = bytearray([marker] * 8192)
    data[:256] = bytes(256)
    data[:4] = b'SC\x11\x01'
    title = 'ＭＧＳ　［ＮＭ］　００：５０　Ｃｅｌｌ'.encode('shift_jis')
    data[4:4 + len(title)] = title
    snapshot = bytearray([marker] * SNAPSHOT_SIZE)
    struct.pack_into('<4I', snapshot, 0, SNAPSHOT_SIZE, 0, 0x5f, 0x800)
    struct.pack_into('<I', snapshot, 0x10, seconds * 60 + 12)
    snapshot[0x20:0x30] = b's03a'.ljust(16, b'\0')
    struct.pack_into('<h', snapshot, 0x42, level)
    struct.pack_into('<h', snapshot, 0x48, disc)
    struct.pack_into('<I', snapshot, 4, zlib.crc32(snapshot[8:]))
    for offset in SNAPSHOT_OFFSETS:
        data[offset:offset + SNAPSHOT_SIZE] = snapshot
    return bytes(data)


class MGSTest(unittest.TestCase):
    def test_registry_clocks_difficulty_disc_and_clear_names(self):
        self.assertIs(plugin_for(save_id(), 'ps1'), Plugin)
        self.assertIsNone(plugin_for(save_id(), 'ps2'))
        for unsupported in (save_id().replace(PREFIX, 'BESLES-01370'), PREFIX + 'VRTRAINING', save_id() + 'extra'):
            self.assertIsNone(plugin_for(unsupported, 'ps1'))
        for level in (-1, 0, 1, 2, 3):
            for disc in (1, 2):
                for seconds in (0, 3599, 3600, 360001):
                    with self.subTest(level=level, disc=disc, seconds=seconds):
                        data = mgs_save(seconds, level, disc)
                        Plugin.validate_save(save_id(seconds, level, disc), data)
                        Plugin.validate_save(save_id(seconds, level, disc, clear=True), data)
        info = Plugin.summary(save_id(), mgs_save())
        self.assertEqual(info, dict(row_title='Metal Gear Solid', summary='Normal · Disc 1 · Cell · Playtime 0:50:06'))
        bundle = SaveBundle(save_id(), (SaveFile('data', mgs_save()),))
        slot, = Plugin.library_slots(bundle)
        self.assertEqual((slot.key, slot.number, slot.playtime_seconds), ('whole', None, 3006))
        self.assertEqual(slot.files, bundle.file_data())

    def test_all_snapshot_checksums_and_identity_fields_are_required(self):
        original = mgs_save()
        for offset in SNAPSHOT_OFFSETS:
            damaged = bytearray(original)
            damaged[offset + 0x200] ^= 1
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                Plugin.validate_save(save_id(), bytes(damaged))
            struct.pack_into('<I', damaged, offset + 4, zlib.crc32(damaged[offset + 8:offset + SNAPSHOT_SIZE]))
            with self.assertRaisesRegex(ValueError, 'snapshots disagree'):
                Plugin.validate_save(save_id(), bytes(damaged))
        for wrong_id in (save_id(seconds=2800), save_id(level=2), save_id(disc=2)):
            with self.assertRaisesRegex(ValueError, 'filename'):
                Plugin.validate_save(wrong_id, original)
        for data in (original[:-1], bytearray(original), b'\0' * 8192):
            with self.assertRaises(ValueError):
                Plugin.validate_save(save_id(), data)
        damaged = bytearray(original)
        struct.pack_into('<I', damaged, 0x108, 0x61)
        with self.assertRaisesRegex(ValueError, 'snapshot layout'):
            Plugin.validate_save(save_id(), bytes(damaged))

    def test_keep_both_changes_only_collision_letter_and_preserves_metadata(self):
        bundle = SaveBundle(save_id(), (SaveFile('data', mgs_save(), b'file metadata'),), b'card metadata')
        self.assertIs(Plugin.prepare_copy(bundle, set())[0], bundle)
        occupied = set()
        for letter in 'ABCDEFGHIJKLMNOP':
            occupied.add(save_id(letter=letter))
            if letter == 'P':
                with self.assertRaisesRegex(ValueError, 'No unused'):
                    Plugin.prepare_copy(bundle, occupied)
                break
            renamed, _ = Plugin.prepare_copy(bundle, occupied)
            self.assertEqual(renamed.save_id, save_id(letter=chr(ord(letter) + 1)))
            self.assertIs(renamed.files, bundle.files)
            self.assertIs(renamed.metadata, bundle.metadata)
            Plugin.validate_save(renamed.save_id, renamed.files[0].data)
        invalid = SaveBundle(save_id(), (*bundle.files, SaveFile('extra', b'data')))
        with self.assertRaisesRegex(ValueError, 'file set'):
            Plugin.prepare_copy(invalid, {save_id()})

    def test_raw_and_signed_sources_preserve_existing_saves_and_make_verified_backup(self):
        backend = get_backend('ps1')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = fixture([(save_id(), (7,), mgs_save())])
            original = fixture([(save_id(), (4,), mgs_save(marker=35)), ('OTHER', (11,), payload())])
            for extension in ('mcr', 'VMP'):
                source, target = root/('source.' + extension), root/'target.mcr'
                source.write_bytes(vmp(image) if extension == 'VMP' else image)
                target.write_bytes(original)
                with self.subTest(extension=extension):
                    with self.assertRaisesRegex(ValueError, 'different PS1 save'):
                        backend.plan_copy(source, {'save': save_id()}, target)
                    plan = backend.plan_copy(source, {'save': save_id()}, target, 'keep_both')
                    self.assertEqual((plan.source_save, plan.destination_save), (save_id(), save_id(letter='B')))
                    candidate, before = Card(plan.candidate), Card(original)
                    for name in (save_id(), 'OTHER'):
                        self.assertEqual(candidate.signature(name), before.signature(name))
                    copied = candidate.payload(candidate.entries[save_id(letter='B')])
                    self.assertEqual(copied, mgs_save())
                    self.assertEqual(target.read_bytes(), original)
                    backup = apply_copy(plan, root/'backups')
                    self.assertEqual(backup.read_bytes(), original)
                    self.assertEqual(Card(target.read_bytes()).payload(Card(target.read_bytes()).entries[save_id(letter='B')]), mgs_save())
                    self.assertEqual(source.read_bytes(), vmp(image) if extension == 'VMP' else image)

    def test_library_upgrade_and_web_keep_both_without_artificial_slot_numbers(self):
        from test_ps1_web import PS1WebTest
        case = PS1WebTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.cards[0].write_bytes(fixture([(save_id(), (7,), mgs_save())]))
        case.cards[1].write_bytes(fixture([(save_id(), (4,), mgs_save(marker=35))]))
        with patch('game_plugins.library.plugin_for', return_value=None):
            case.scan(0)
        library = case.service.library
        game = next(g for g in library.inventory()['games'] if g['game_key'] == 'SLUS-00594')
        old = game['saves'][0]
        case.scan(0)
        game = next(g for g in library.inventory()['games'] if g['game_key'] == 'SLUS-00594')
        self.assertEqual(game['title'], Plugin.name)
        self.assertEqual(game['saves'][0]['id'], old['id'])
        self.assertEqual(game['saves'][0]['playtime_seconds'], 3006)
        self.assertIsNone(game['slots'][0]['number'])
        case.scan(1)
        save = next(s for s in case.service.inventory()['saves'] if s['card_id'] == case.ids[0] and s['directory'] == save_id())
        self.assertTrue(save['can_keep_both'])
        self.assertNotIn('card_slots', save)
        status, preview = case.request('/api/transfers/preview', dict(
            save_id=save['id'], destination_card_id=case.ids[1], conflict='keep_both'))
        self.assertEqual(status, 201, preview)
        self.assertEqual(preview['destination_save'], save_id(letter='B'))
        self.assertEqual(case.request(f"/api/transfers/{preview['id']}/apply", {'cards_idle': True})[0], 200)
        library.download(old['id'])


class SaGaProgressTest(unittest.TestCase):
    def test_existing_saga_records_gain_time_without_changing_history(self):
        from test_ps1_more_numbered import game_save, save_id
        from test_ps1_web import PS1WebTest
        case = PS1WebTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.cards[0].write_bytes(fixture([(save_id(SaGaFrontier2US), (7,), game_save(SaGaFrontier2US))]))
        previous = replace(SaGaFrontier2US.layout, playtime_offset=None, progress=None)
        with patch.object(SaGaFrontier2US, 'layout', previous):
            case.scan(0)
        library = case.service.library
        game = next(g for g in library.inventory()['games'] if g['game_key'] == 'SLUS-00933')
        old = game['saves'][0]
        self.assertIsNone(old['playtime_seconds'])
        case.scan(0)
        game = next(g for g in library.inventory()['games'] if g['game_key'] == 'SLUS-00933')
        self.assertEqual(len(game['saves']), 1)
        self.assertEqual(game['saves'][0]['id'], old['id'])
        self.assertEqual(game['saves'][0]['content_hash'], old['content_hash'])
        self.assertEqual(game['saves'][0]['playtime_seconds'], 27321)
        self.assertIn('1235 · Westia · Wil Knights', game['saves'][0]['label'])

    def test_font_decoding_and_verified_clock_fields(self):
        from test_ps1_more_numbered import game_save, save_id
        self.assertEqual(decode_text(bytes.fromhex('90b8b6b7a4b9a8ff8bb2b5b1')), 'Gustave Born')
        self.assertEqual(decode_text(bytes.fromhex('a0acafff94b1acaaabb7b600')), 'Wil Knights')
        self.assertEqual(decode_text(b'\x80\x89\x01\0\x8a'), '09?')
        data = game_save(SaGaFrontier2US)
        summary = SaGaFrontier2US.summary(save_id(SaGaFrontier2US), data)['summary']
        self.assertEqual(summary, '1235 · Westia · Wil Knights · Playtime 7:35:21')
