"""Cross-game preservation and rejection checks for numbered PS1 saves."""

import struct
import unittest

from game_plugins.ps1.ffix import FinalFantasyIXUS
from game_plugins.ps1.chrono_cross import ChronoCrossUS
from game_plugins.ps1.tales_of_phantasia import TalesOfPhantasiaTranslated
from save_core.backends.base import SaveBundle, SaveFile
from test_ffix import ffix_save, save_id as ffix_id
from test_chrono_cross import chrono_save, save_id as chrono_id
from test_tales_of_phantasia import tales_save, save_id as tales_id


GAMES = (
    (FinalFantasyIXUS, ffix_save, ffix_id),
    (ChronoCrossUS, chrono_save, chrono_id),
    (TalesOfPhantasiaTranslated, tales_save, tales_id),
)


class NumberedPS1Test(unittest.TestCase):
    def test_noop_retains_identity_and_copy_retains_opaque_metadata(self):
        for plugin, fixture, save_id in GAMES:
            with self.subTest(game=plugin.name):
                data = fixture()
                bundle = SaveBundle(save_id(1), (SaveFile('data', data, b'file metadata'),), b'card metadata')
                self.assertIs(plugin.prepare_slot(bundle, 1), bundle)
                copied = plugin.prepare_slot(bundle, 15)
                self.assertEqual(copied.save_id, save_id(15))
                self.assertEqual(copied.metadata, bundle.metadata)
                self.assertEqual(copied.files[0].metadata, bundle.files[0].metadata)
                self.assertEqual(bundle.file_data(), {'data': data})

    def test_rejects_unexpected_files_even_when_destination_is_unchanged(self):
        for plugin, fixture, save_id in GAMES:
            for files in ((SaveFile('other', fixture()),),
                          (SaveFile('data', fixture()), SaveFile('extra', b'opaque'))):
                with self.subTest(game=plugin.name, files=[f.name for f in files]):
                    bundle = SaveBundle(save_id(1), files)
                    with self.assertRaisesRegex(ValueError, 'file set'):
                        plugin.prepare_slot(bundle, 1)

    def test_valid_checksum_cannot_bypass_slot_label_verification(self):
        for plugin, fixture, save_id in GAMES:
            with self.subTest(game=plugin.name):
                data = bytearray(fixture())
                data[4:68] = bytes(64)
                # Tales' title prefix is also part of its layout signature.
                if plugin is TalesOfPhantasiaTranslated:
                    data[4:0x32] = fixture()[4:0x32]
                layout = plugin.layout
                struct.pack_into(layout.checksum_format, data, layout.checksum_offset, layout.checksum(data))
                bundle = SaveBundle(save_id(1), (SaveFile('data', bytes(data)),))
                with self.assertRaisesRegex(ValueError, 'slot label'):
                    plugin.prepare_slot(bundle, 2)
