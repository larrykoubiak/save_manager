import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from save_core.compare_saves import scan
from game_plugins.ffx import checksum
from game_plugins.ffx_slots import prepare_slot
from save_core.save_games import group_games
from save_core.save_merge import export_merge, files, plan_merge
from test_ffx_slots import source_save
from vendor.mymcplusplus.mymcplusplus.ps2mc import ps2mc


def card_file(path, saves, ecc):
    with path.open('w+b') as image:
        card = ps2mc(image, params=(ecc, 512, 16, 16384))
        try:
            for save in saves:
                card.import_save_file(save, False)
        finally:
            card.close()


class SaveMergeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.dirs = {side: self.base / side for side in ('Emulator', 'VMC')}
        for directory in self.dirs.values():
            directory.mkdir()
        first = source_save()
        second = source_save()
        entry, original = second.get_file(0)
        payload = bytearray(original)
        struct.pack_into('<I', payload, 252, 10000)
        value = checksum(payload)
        for offset in (26, 25844):
            struct.pack_into('<H', payload, offset, value)
        second.set_file(0, entry, bytes(payload))
        self.payloads = {original, bytes(payload)}
        card_file(self.dirs['Emulator'] / 'one.ps2', [first], True)
        card_file(self.dirs['VMC'] / 'two.bin', [second, prepare_slot(first, 3)], False)
        self.before = {path: hashlib.sha256(path.read_bytes()).digest()
                       for directory in self.dirs.values() for path in directory.iterdir()}
        _, left = scan(self.dirs['Emulator'], '.ps2')
        _, right = scan(self.dirs['VMC'], '.bin')
        self.game = group_games(left, right)[0]

    def test_union_deduplicates_and_relocates_conflicting_slots_in_both_formats(self):
        plan = plan_merge(self.game, self.dirs)
        self.assertEqual(len(plan.saves), 2)
        self.assertEqual([(row.destination_slot, row.duplicate) for row in plan.rows],
                         [(1, False), (2, False), (1, True)])
        for ecc, suffix, size in ((True, '.ps2', 8650752), (False, '.bin', 8388608)):
            output = export_merge(plan, self.base / ('merged' + suffix), ecc)
            self.assertEqual(output.stat().st_size, size)
            with output.open('rb') as image:
                card = ps2mc(image)
                try:
                    payloads = set()
                    for slot in (0, 1):
                        name = f'BISLPS-25088FF0906{slot:02}'
                        save = card.export_save_file('/' + name)
                        payloads.add(files(save)[name])
                        # Full metadata/icon validation works after reopening.
                        prepare_slot(save, slot + 1)
                    self.assertEqual(payloads, self.payloads)
                    self.assertTrue(card.check())
                finally:
                    card.close()
        for path, digest in self.before.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)

    def test_existing_files_and_changed_inputs_are_refused(self):
        plan = plan_merge(self.game, self.dirs)
        existing = self.base / 'existing.ps2'
        existing.write_bytes(b'keep me')
        with self.assertRaisesRegex(ValueError, 'never overwritten'):
            export_merge(plan, existing)
        self.assertEqual(existing.read_bytes(), b'keep me')
        source = next(iter(self.before))
        with self.assertRaisesRegex(ValueError, 'never overwritten'):
            export_merge(plan, source)
        with source.open('r+b') as handle:
            handle.seek(-1, 2)
            handle.write(b'X')
        output = self.base / 'stale.ps2'
        with self.assertRaisesRegex(ValueError, 'changed since the preview'):
            export_merge(plan, output)
        self.assertFalse(output.exists())
        with self.assertRaisesRegex(ValueError, 'changed since the scan'):
            plan_merge(self.game, self.dirs)

    def test_unsupported_games_are_refused(self):
        self.game['id'] = 'SLUS-00000'
        with self.assertRaisesRegex(ValueError, 'not supported'):
            plan_merge(self.game, self.dirs)
