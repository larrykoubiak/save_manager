from copy import deepcopy
import unittest

from game_plugins.ps2.ffx import clone_slot
from save_core.backends.ps2.bundles import from_native, to_native
from save_core.backends.ps2.native.native_save import PS2SaveFile
from test_ffx_plugin import fixture


def source_save():
    name = 'BISLPS-25088FF090600'
    icon = bytearray(964)
    icon[:4] = b'PS2D'
    title = 'ＦＦ１０ ［０１］ Besaid'.encode('shift_jis')
    icon[192:192 + len(title)] = title
    for offset in (260, 324, 388):
        icon[offset:offset + 12] = b'01090600.ico'
    directory = [0x8427, 0, 3, (0, 0, 12, 1, 1, 2026), 0, 0, (0, 0, 12, 1, 1, 2026), 0, name.encode()]
    save = PS2SaveFile()
    save.set_directory(directory)
    for i, (filename, data) in enumerate(((name, fixture()), ('icon.sys', bytes(icon)), ('01090600.ico', b'icon model'))):
        entry = directory.copy()
        entry[0], entry[2], entry[8] = 0x8417, len(data), filename.encode()
        save.set_file(i, entry, data)
    return save


class FFXSlotTest(unittest.TestCase):
    def test_clone_preserves_payload_model_source_and_dates(self):
        source = source_save()
        before = deepcopy(source)
        result = to_native(clone_slot(from_native(source), 3))
        self.assertEqual(result.dirent[8], b'BISLPS-25088FF090602')
        files = {ent[8]: data for ent, data in result}
        self.assertEqual(files[b'BISLPS-25088FF090602'], fixture())
        self.assertEqual(files[b'01090602.ico'], b'icon model')
        icon = files[b'icon.sys']
        self.assertIn('［０３］'.encode('shift_jis'), icon[192:260])
        for offset in (260, 324, 388):
            self.assertEqual(icon[offset:offset + 12], b'01090602.ico')
        self.assertEqual(source.dirent, before.dirent)
        self.assertEqual(list(source), list(before))
        self.assertEqual(result.dirent[3], source.dirent[3])
        self.assertEqual(result.dirent[6], source.dirent[6])

    def test_invalid_same_slots_and_unrecognized_metadata_are_rejected(self):
        for slot in (0, 100, 1):
            with self.assertRaises(ValueError):
                clone_slot(from_native(source_save()), slot)
        source = source_save()
        ent, data = source.get_file(1)
        source.set_file(1, ent, bytes(964))
        with self.assertRaises(ValueError):
            clone_slot(from_native(source), 3)
