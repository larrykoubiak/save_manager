"""Backend contracts and lossless PS2/native save-bundle boundaries."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from save_core.backends import get_backend, SaveBundle, SaveFile
from save_core.backends.ps2.bundles import from_native, to_native
from save_core.save_copy import apply_copy
from save_core.save_merge import files
from test_ffx_slots import source_save
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file


class BackendTest(unittest.TestCase):
    def test_bundle_roundtrip_preserves_ps2_metadata_and_renames_at_boundary(self):
        native = source_save()
        bundle = from_native(native)
        roundtrip = to_native(bundle)
        self.assertEqual(roundtrip.get_directory(), native.get_directory())
        self.assertEqual(list(roundtrip), list(native))
        renamed = replace(bundle, save_id='RENAMED', files=tuple(
            replace(file, name='payload.bin', data=file.data + b'new') if index == 0 else file
            for index, file in enumerate(bundle.files)))
        converted = to_native(renamed)
        self.assertEqual(converted.get_directory()[8], b'RENAMED')
        entry, contents = converted.get_file(0)
        self.assertEqual(entry[8], b'payload.bin')
        self.assertEqual(entry[2], len(contents))
        self.assertEqual(contents, native.get_file(0)[1] + b'new')
        for index in (0, 1, 3, 4, 5, 6, 7):
            self.assertEqual(entry[index], native.get_file(0)[0][index])
        self.assertEqual(bundle.save_id, native.get_directory()[8].decode())

    def test_single_file_bundle_does_not_require_card_metadata(self):
        bundle = SaveBundle('Example RPG', (SaveFile('game.srm', bytes(8192)),))
        self.assertEqual(files(bundle), {'game.srm': bytes(8192)})
        with self.assertRaisesRegex(ValueError, 'PS2 storage metadata'):
            to_native(bundle)
        with self.assertRaises(ValueError):
            SaveBundle('duplicate', (SaveFile('same', b'a'), SaveFile('same', b'b')))

    def test_ps2_backend_reads_copies_and_deletes_through_shared_contract(self):
        backend = get_backend('ps2')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root/'source.ps2', root/'target.bin'
            card_file(source, [source_save()], True)
            card_file(destination, [generic_save('OTHER', b'preserve')], False)
            backend.validate(source.read_bytes(), source)
            bundle = backend.read_save(source, 'BISLPS-25088FF090600')
            self.assertIsInstance(bundle, SaveBundle)
            plan = backend.plan_copy(source, {'save':bundle.save_id}, destination)
            backup = apply_copy(plan, root/'backups')
            self.assertTrue(backup.is_file())
            self.assertEqual(backend.read_save(destination, bundle.save_id).file_data(), bundle.file_data())
            before = destination.read_bytes()
            plan = backend.plan_delete(destination, {'save':bundle.save_id})
            self.assertEqual(destination.read_bytes(), before)
            apply_copy(plan, root/'backups')
            self.assertEqual(snapshot(destination)['/OTHER/data.bin'][1], b'preserve')
            self.assertNotIn('/'+bundle.save_id, snapshot(destination))
        with self.assertRaisesRegex(ValueError, 'Unsupported storage backend'):
            get_backend('unknown')
