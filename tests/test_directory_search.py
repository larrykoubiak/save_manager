import unittest
import io

from vendor.mymcplusplus.mymcplusplus.ps2mc import ps2mc
from vendor.mymcplusplus.mymcplusplus.ps2mc_dir import DF_EXISTS
from vendor.mymcplusplus.mymcplusplus.ps2mc_dir import unpack_dirent
from test_ffx_slots import source_save
from save_core.save_merge import files


class Directory(list):
    def tell(self):
        return 0


class DirectorySearchTest(unittest.TestCase):
    def test_filesystem_check_and_export_ignore_completely_erased_directory_padding(self):
        image = io.BytesIO()
        image.name = '<erased directory padding regression>'
        card = ps2mc(image, params=(False, 512, 16, 16384))
        source = source_save()
        name = source.get_directory()[8].decode('ascii')
        try:
            card.import_save_file(source, False)
            directory = card.dir_open('/' + name, 'r+b')
            try:
                directory.write_raw_ent(len(directory), unpack_dirent(b'\xff' * 512), False)
            finally:
                directory.close()
            self.assertTrue(card.check())
            self.assertEqual(files(card.export_save_file('/' + name)), files(source))
        finally:
            card.close()
        original = image.getvalue()
        reopened = ps2mc(image)
        try:
            self.assertTrue(reopened.check())
            self.assertEqual(files(reopened.export_save_file('/' + name)), files(source))
        finally:
            reopened.close()
        self.assertEqual(image.getvalue(), original)

    def test_lookup_skips_erased_and_deleted_slots(self):
        erased = [0xffff] + [None] * 7 + [b'\xff' * 448]
        deleted = [0] + [None] * 7 + [b'icon.sys']
        active = [DF_EXISTS] + [None] * 7 + [b'icon.sys']
        directory = Directory([erased, deleted, active])

        self.assertEqual(ps2mc.search_directory(None, directory, 'icon.sys'), (2, active))
        self.assertEqual(ps2mc.search_directory(None, directory, 'missing'), (None, None))
