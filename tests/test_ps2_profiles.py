import struct
import unittest
from functools import reduce
from operator import xor

from game_plugins import plugin_for
from game_plugins.ps2.profiles import (
    CrazyTaxiUS, DOA2HardcoreUS, TenkaichiUS, KOFOrochiUS,
    SoulcaliburIIUS, rotate16, sc2_unmask,
)


class ProfilesTest(unittest.TestCase):
    def test_crazy_taxi_uses_character_scores_and_box_flags(self):
        data = bytearray(CrazyTaxiUS.payload_size)
        data[:15] = b'Crazy Taxi Data'
        data[18:28] = b'CRAZY TAXI'
        struct.pack_into('<I', data, 0x74 + 0x788, 5)
        struct.pack_into('<i', data, 0x78 + 0x788, 169228)
        label = CrazyTaxiUS.summary(CrazyTaxiUS.directory, data)['summary']
        self.assertIn('$1,692.28', label)
        self.assertIn('2/16', label)

    def test_doa_checks_integrity_before_reading_mode_counters(self):
        data = bytearray(DOA2HardcoreUS.payload_size)
        data[0x1964:0x1973] = b'DEAD OR ALIVE 2'
        struct.pack_into('<I', data, 0xaf4, 15)
        data[-1] = reduce(xor, data[:-1], 0)
        self.assertEqual(DOA2HardcoreUS.read_progress(data)[0], [('Recorded matches', '15')])
        data[100] ^= 1
        with self.assertRaisesRegex(ValueError, 'checksum'):
            DOA2HardcoreUS.read_progress(data)

    def test_tenkaichi_clock_and_split_checksum(self):
        data = bytearray(TenkaichiUS.payload_size)
        struct.pack_into('<I', data, 8, 26838)
        checksum = sum(struct.unpack_from('<8188H', data, 8)) & 65535
        struct.pack_into('<II', data, 0, checksum >> 8, checksum & 255)
        self.assertEqual(TenkaichiUS.read_progress(data)[1], 447)
        data[9] ^= 1
        with self.assertRaisesRegex(ValueError, 'checksum'):
            TenkaichiUS.read_progress(data)

    def test_kof_ignores_selected_game_and_last_played(self):
        data = b'IE Profile Version: \n33\nGame: \n4\nLast Played:\n29\n'
        for year in range(94, 99):
            data += f"'KING OF FIGHTERS '{year}' Times Played\n0\n".encode()
        data += b'\0Count Achievements:\n0\n'
        data += bytes(KOFOrochiUS.payload_size - len(data))
        self.assertEqual(KOFOrochiUS.progress(KOFOrochiUS.directory, data)[0],
                         [('Achievements', '0'), ('Games played', '0')])
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
            KOFOrochiUS.read_progress(data.replace(b'Count Achievements:', b'Unknown Fieldxxxxx:'))

    def test_soulcalibur_checks_encrypted_body_and_closing_marker(self):
        body = bytearray(8182)
        struct.pack_into('<I', body, 0, 2)
        struct.pack_into('<I', body, 0x173a, 3634)
        seed = (0xa2f1 + 7 * sum(body)) & 65535
        trailer = rotate16(seed ^ 0xeb7d, 13)
        data = sc2_unmask(body, seed, 1) + struct.pack('<H', trailer)
        data += sc2_unmask(b'BESTGAME', trailer, 5)
        self.assertEqual(SoulcaliburIIUS.read_progress(data)[1], 3634)
        corrupted = bytearray(data)
        corrupted[500] ^= 1
        with self.assertRaisesRegex(ValueError, 'checksum'):
            SoulcaliburIIUS.read_progress(corrupted)
        corrupted = bytearray(data)
        corrupted[-1] ^= 1
        with self.assertRaisesRegex(ValueError, 'closing marker'):
            SoulcaliburIIUS.read_progress(corrupted)

    def test_profiles_expose_no_internal_slot_writer(self):
        for cls in (CrazyTaxiUS, DOA2HardcoreUS, TenkaichiUS, KOFOrochiUS, SoulcaliburIIUS):
            self.assertIs(plugin_for(cls.directory), cls)
            self.assertFalse(hasattr(cls, 'copy_slot'))


if __name__ == '__main__':
    unittest.main()
