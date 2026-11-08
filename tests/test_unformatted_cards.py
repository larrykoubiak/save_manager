import hashlib
from pathlib import Path
import tarfile
import tempfile
import unittest

from save_core.compare_saves import scan


class UnformattedCardTest(unittest.TestCase):
    def test_valid_card_is_read_and_erased_cards_are_reported_unchanged(self):
        fixture = Path(__file__).resolve().parents[1] / 'vendor/mymcplusplus/test/data.tar.gz'
        with tempfile.TemporaryDirectory() as directory, tarfile.open(fixture) as archive:
            directory = Path(directory)
            good = directory / 'Mcd001.ps2'
            good.write_bytes(archive.extractfile('mc01.ps2').read())
            blank = directory / 'Mcd002.ps2'
            blank.write_bytes(bytes([255]) * 8650752)
            zero = directory / 'zero.ps2'
            zero.write_bytes(bytes(8388608))
            before = {p: hashlib.sha256(p.read_bytes()).digest() for p in (good, blank, zero)}
            skipped = []
            paths, records = scan(directory, '.ps2', skipped=skipped)
            self.assertEqual(set(paths), {good, blank, zero})
            self.assertTrue(records)
            self.assertEqual({r['card'] for r in records}, {'Mcd001.ps2'})
            self.assertEqual(set(skipped), {blank, zero})
            self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).digest() for p in paths})

    def test_mixed_damaged_image_is_not_mistaken_for_erased_card(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / 'damaged.ps2'
            data = bytearray([255]) * 8650752
            data[-1] = 1
            path.write_bytes(data)
            skipped = []
            with self.assertRaisesRegex(ValueError, 'Cannot read.*damaged.ps2'):
                scan(directory, '.ps2', skipped=skipped)
            self.assertEqual(skipped, [])

    def test_empty_file_remains_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / 'empty.ps2').touch()
            with self.assertRaisesRegex(ValueError, 'Cannot read.*empty.ps2'):
                scan(directory, '.ps2')
