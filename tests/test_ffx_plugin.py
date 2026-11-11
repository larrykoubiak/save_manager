import struct
import unittest

from game_plugins import plugin_for
from game_plugins.ps2.ffx import FFXInternational, checksum


def fixture():
    # Synthetic bytes; expected 0xabfe was calculated independently using
    # FFXED's reference table, including its nonstandard zero entry at 255.
    data = bytearray((i * 37 + 11) & 255 for i in range(25848))
    for offset, value in ((252, 4595), (15752, 690), (15700, 14),
                          (22032, 520), (22036, 12), (22056, 520), (22060, 12)):
        struct.pack_into('<I', data, offset, value)
    struct.pack_into('<H', data, 3116, 128)
    for offset in (26, 25844):
        struct.pack_into('<H', data, offset, 0xabfe)
    return bytes(data)


class FFXPluginTest(unittest.TestCase):
    def test_golden_checksum_and_decoded_values_without_mutation(self):
        data = fixture()
        self.assertEqual(checksum(data), 0xabfe)
        result = FFXInternational.decode('BISLPS-25088FF090600', data)
        sections = {s.title: dict(s.fields) for s in result.sections}
        self.assertEqual(sections['Progress']['Playtime'], '1:16:35')
        self.assertEqual(sections['Progress']['Gil'], '690')
        self.assertEqual(sections['Progress']['Battles fought'], '14')
        self.assertEqual(sections['Progress']['Story progress marker'], '128')
        self.assertEqual(sections['Tidus']['Current HP'], '520')
        self.assertEqual(sections['Progress']['Save slot'], '1')
        self.assertEqual(data, fixture())

    def test_corrupted_payload_does_not_show_trusted_progress(self):
        data = bytearray(fixture())
        data[252] ^= 1
        result = FFXInternational.decode('BISLPS-25088FF090600', bytes(data))
        self.assertEqual(result.status, 'Checksum mismatch')
        summary = FFXInternational.summary('BISLPS-25088FF090600', bytes(data))
        self.assertEqual(summary['summary'], 'Playtime unavailable: checksum mismatch')
        self.assertNotIn('Progress', [s.title for s in result.sections])

    def test_disagreeing_checksum_copies_are_rejected(self):
        data = bytearray(fixture())
        data[26] ^= 1
        self.assertEqual(FFXInternational.decode('BISLPS-25088FF090600', data).status, 'Checksum mismatch')

    def test_unrecognized_regions_games_and_sizes_are_not_assumed_compatible(self):
        self.assertIs(plugin_for('BISLPS-25088FF090601'), FFXInternational)
        self.assertIsNone(plugin_for('BASLUS-21577'))
        self.assertIsNone(plugin_for('BASCUS-97102FF090600'))
        with self.assertRaises(ValueError):
            FFXInternational.decode('BISLPS-25088FF090600', fixture()[:-1])
