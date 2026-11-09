from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from save_core.compare_saves import scan
from game_plugins.ffx_slots import prepare_slot
from save_core.save_copy import apply_copy, plan_copy, plan_delete, tree_snapshot
from test_ffx_slots import source_save
from test_save_merge import card_file
from vendor.mymcplusplus.mymcplusplus.ps2mc import ps2mc, PS2MC_FAT_ALLOCATED_BIT, PS2MC_FAT_CHAIN_END
from vendor.mymcplusplus.mymcplusplus.save.ps2save import PS2SaveFile


def generic_save(name, payload):
    template = source_save().get_directory().copy()
    template[8] = name.encode()
    template[2] = 1
    save = PS2SaveFile()
    save.set_directory(template)
    entry = template.copy()
    entry[0], entry[2], entry[8] = 0x8417, len(payload), b'data.bin'
    save.set_file(0, entry, payload)
    return save


def snapshot(path):
    with path.open('rb') as image:
        card = ps2mc(image)
        try:
            return tree_snapshot(card)
        finally:
            card.close()


class SaveCopyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.emulator = self.base / 'emulator'
        self.vmc = self.base / 'vmc'
        self.emulator.mkdir()
        self.vmc.mkdir()
        self.source = self.emulator / 'source.ps2'
        self.destination = self.vmc / 'target.bin'
        card_file(self.source, [source_save(), generic_save('OTHER-GAME', b'untouched')], True)
        card_file(self.destination, [generic_save('OTHER-VMC', b'also untouched')], False)
        _, records = scan(self.emulator, '.ps2')
        self.record = next(r for r in records if r['save'].startswith('BISLPS'))
        self.backups = self.base / 'backups'

    def test_delete_preserves_other_saves_and_card_format_with_full_backup(self):
        for ecc in (False, True):
            with self.subTest(ecc=ecc):
                card_file(self.destination, [generic_save('DELETE-ME', b'remove'),
                                            generic_save('DELETE-ME-KEEP', b'preserve')], ecc)
                original = self.destination.read_bytes()
                before = snapshot(self.destination)
                plan = plan_delete(self.destination, {'save':'DELETE-ME'})
                self.assertEqual(self.destination.read_bytes(), original)
                backup = apply_copy(plan, self.backups)
                self.assertEqual(backup.read_bytes(), original)
                self.assertEqual(len(self.destination.read_bytes()), len(original))
                self.assertEqual(snapshot(self.destination), {k:v for k,v in before.items()
                                 if k != '/DELETE-ME' and not k.startswith('/DELETE-ME/')})
                with self.destination.open('rb') as image:
                    card = ps2mc(image)
                    try:
                        self.assertTrue(card.check())
                    finally:
                        card.close()

    def test_delete_rejects_missing_save_and_unsafe_directory_names(self):
        original = self.destination.read_bytes()
        for name in ('', '.', '..', '/', 'OTHER-VMC/data.bin', 'missing'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                plan_delete(self.destination, {'save':name})
        self.assertEqual(self.destination.read_bytes(), original)

    def test_changed_card_cannot_apply_delete_preview(self):
        plan = plan_delete(self.destination, {'save':'OTHER-VMC'})
        changed = self.destination.read_bytes() + b'changed'
        self.destination.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'changed since'):
            apply_copy(plan, self.backups)
        self.assertEqual(self.destination.read_bytes(), changed)
        self.assertFalse(self.backups.exists())

    def test_both_directions_preserve_other_saves_format_and_backup(self):
        source_before, target_before = self.source.read_bytes(), self.destination.read_bytes()
        original_tree = snapshot(self.destination)
        plan = plan_copy(self.source, self.record, self.destination)
        self.assertEqual(self.destination.read_bytes(), target_before)
        backup = apply_copy(plan, self.backups)
        self.assertEqual(backup.read_bytes(), target_before)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertEqual(self.destination.stat().st_size, 8388608)
        changed_tree = snapshot(self.destination)
        for key, value in original_tree.items():
            self.assertEqual(changed_tree[key], value)
        # Raw VMC -> ECC shared card, preserving an existing unrelated game.
        target = self.emulator / 'reverse.ps2'
        card_file(target, [generic_save('REVERSE-GAME', b'preserve')], True)
        _, records = scan(self.vmc, '.bin')
        record = next(r for r in records if r['save'].startswith('BISLPS'))
        reverse_original = target.read_bytes()
        reverse_plan = plan_copy(self.destination, record, target)
        apply_copy(reverse_plan, self.backups)
        self.assertEqual(target.stat().st_size, 8650752)
        self.assertEqual(snapshot(target)['/REVERSE-GAME/data.bin'][1], b'preserve')
        self.assertNotEqual(target.read_bytes(), reverse_original)
        self.assertEqual(self.destination.read_bytes(), plan.candidate)

    def test_generic_save_needs_no_game_plugin(self):
        _, records = scan(self.emulator, '.ps2')
        record = next(r for r in records if r['save'] == 'OTHER-GAME')
        plan = plan_copy(self.source, record, self.destination)
        apply_copy(plan, self.backups)
        self.assertEqual(snapshot(self.destination)['/OTHER-GAME/data.bin'][1], b'untouched')

    def test_identical_save_is_noop(self):
        card_file(self.destination, [source_save()], False)
        plan = plan_copy(self.source, self.record, self.destination)
        before = self.destination.read_bytes()
        self.assertEqual(plan.action, 'Already identical')
        self.assertIsNone(apply_copy(plan, self.backups))
        self.assertEqual(before, self.destination.read_bytes())
        self.assertFalse(self.backups.exists())

    def test_conflict_stop_replace_and_ffx_keep_both(self):
        other = source_save()
        # Different icon bytes produce a real directory conflict without changing the payload checksum.
        entry, _ = other.get_file(2)
        other.set_file(2, entry, b'other icon')
        card_file(self.destination, [other, prepare_slot(source_save(), 2)], False)
        before = self.destination.read_bytes()
        with self.assertRaisesRegex(ValueError, 'already occupies'):
            plan_copy(self.source, self.record, self.destination)
        self.assertEqual(before, self.destination.read_bytes())
        kept = plan_copy(self.source, self.record, self.destination, 'keep_both')
        self.assertEqual(kept.destination_save, 'BISLPS-25088FF090602')
        apply_copy(kept, self.backups)
        contents = snapshot(self.destination)
        self.assertEqual(contents['/BISLPS-25088FF090600/01090600.ico'][1], b'other icon')
        self.assertEqual(contents['/BISLPS-25088FF090602/01090602.ico'][1], b'icon model')
        replaced = plan_copy(self.source, self.record, self.destination, 'replace')
        self.assertEqual(replaced.action, 'Replace existing save')
        apply_copy(replaced, self.backups)
        self.assertEqual(snapshot(self.destination)['/BISLPS-25088FF090600/01090600.ico'][1], b'icon model')

    def test_generic_keep_both_rejected_but_explicit_replace_works(self):
        card_file(self.destination, [generic_save('OTHER-GAME', b'different')], False)
        _, records = scan(self.emulator, '.ps2')
        record = next(r for r in records if r['save'] == 'OTHER-GAME')
        with self.assertRaisesRegex(ValueError, 'not supported'):
            plan_copy(self.source, record, self.destination, 'keep_both')
        apply_copy(plan_copy(self.source, record, self.destination, 'replace'), self.backups)
        self.assertEqual(snapshot(self.destination)['/OTHER-GAME/data.bin'][1], b'untouched')

    def test_changed_source_or_destination_invalidates_preview(self):
        for path in (self.source, self.destination):
            with self.subTest(path=path):
                plan = plan_copy(self.source, self.record, self.destination)
                original = path.read_bytes()
                with path.open('ab') as handle:
                    handle.write(b'changed')
                with self.assertRaisesRegex(ValueError, 'changed since the preview'):
                    apply_copy(plan, self.backups)
                self.assertFalse(self.backups.exists())
                path.write_bytes(original)
                _, records = scan(self.emulator, '.ps2')
                self.record = next(r for r in records if r['save'].startswith('BISLPS'))

    def test_failed_rename_leaves_original_and_verified_backup(self):
        plan = plan_copy(self.source, self.record, self.destination)
        original = self.destination.read_bytes()
        with patch('save_core.save_copy.os.replace', side_effect=OSError('rename refused')):
            with self.assertRaisesRegex(RuntimeError, 'Destination was not replaced'):
                apply_copy(plan, self.backups)
        self.assertEqual(self.destination.read_bytes(), original)
        backups = list(self.backups.glob('*/target.bin'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertFalse(list(self.vmc.glob('*-copy-*')))

    def test_staging_works_when_mount_cannot_rename_hidden_files(self):
        import os
        plan = plan_copy(self.source, self.record, self.destination)
        replace = os.replace

        def webdav_replace(source, destination):
            if Path(source).name.startswith('.'):
                raise FileNotFoundError('WebDAV directory refresh hid the staging file')
            return replace(source, destination)

        with patch('save_core.save_copy.os.replace', side_effect=webdav_replace):
            apply_copy(plan, self.backups)
        self.assertEqual(self.destination.read_bytes(), plan.candidate)
        self.assertFalse(list(self.vmc.glob('*-copy-*')))

    def test_backup_failure_prevents_destination_write(self):
        plan = plan_copy(self.source, self.record, self.destination)
        original = self.destination.read_bytes()
        with patch('save_core.save_copy.os.fsync', side_effect=OSError('backup flush failed')):
            with self.assertRaisesRegex(OSError, 'backup flush failed'):
                apply_copy(plan, self.backups)
        self.assertEqual(self.destination.read_bytes(), original)

    def test_destination_change_during_staging_prevents_replace(self):
        import os
        plan = plan_copy(self.source, self.record, self.destination)
        real_fsync = os.fsync
        calls = 0

        def change_after_staging(fd):
            nonlocal calls
            real_fsync(fd)
            calls += 1
            if calls == 2:
                with self.destination.open('ab') as handle:
                    handle.write(b'concurrent write')

        with patch('save_core.save_copy.os.fsync', side_effect=change_after_staging), patch('save_core.save_copy.os.replace') as replace:
            with self.assertRaisesRegex(RuntimeError, 'changed since the preview'):
                apply_copy(plan, self.backups)
        replace.assert_not_called()
        self.assertTrue(self.destination.read_bytes().endswith(b'concurrent write'))

    def test_bad_source_checksum_is_refused(self):
        bad = source_save()
        entry, data = bad.get_file(0)
        corrupted = bytearray(data)
        corrupted[252] ^= 1
        bad.set_file(0, entry, bytes(corrupted))
        card_file(self.source, [bad], True)
        _, records = scan(self.emulator, '.ps2')
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            plan_copy(self.source, records[0], self.destination)

    def add_orphan(self, path):
        with path.open('r+b') as image:
            card = ps2mc(image)
            try:
                free = next(i for i in range(card.allocatable_cluster_end)
                            if not card.lookup_fat(i) & PS2MC_FAT_ALLOCATED_BIT)
                card.set_fat(free, PS2MC_FAT_CHAIN_END)
            finally:
                card.close()

    def test_orphan_source_allocation_warns_and_copy_preserves_source(self):
        self.add_orphan(self.source)
        _, records = scan(self.emulator, '.ps2')
        record = next(r for r in records if r['save'].startswith('BISLPS'))
        before = self.source.read_bytes()
        plan = plan_copy(self.source, record, self.destination)
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn('1 allocated but unreferenced', plan.warnings[0])
        apply_copy(plan, self.backups)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertIn('/' + record['save'], snapshot(self.destination))

    def test_orphan_destination_still_refused(self):
        self.add_orphan(self.destination)
        before = self.destination.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Destination filesystem check failed'):
            plan_copy(self.source, self.record, self.destination)
        self.assertEqual(self.destination.read_bytes(), before)

    def test_crosslinked_source_still_refused(self):
        with self.source.open('r+b') as image:
            card = ps2mc(image)
            try:
                save = card.export_save_file('/' + self.record['save'])
                first_cluster = save.get_file(0)[0][4]
                card.set_fat(first_cluster, first_cluster | PS2MC_FAT_ALLOCATED_BIT)
            finally:
                card.close()
        record = dict(self.record)
        record.pop('card_version')
        before = self.destination.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Source filesystem check failed'):
            plan_copy(self.source, record, self.destination)
        self.assertEqual(self.destination.read_bytes(), before)

    def test_blank_full_and_same_cards_refused(self):
        with self.assertRaisesRegex(ValueError, 'different destination'):
            plan_copy(self.source, self.record, self.source)
        self.destination.write_bytes(b'\xff' * 8388608)
        with self.assertRaisesRegex(ValueError, 'unformatted'):
            plan_copy(self.source, self.record, self.destination)
        card_file(self.destination, [generic_save('OTHER-VMC', b'keep')], False)
        before = self.destination.read_bytes()
        with patch.object(ps2mc, 'get_free_space', return_value=0):
            with self.assertRaisesRegex(ValueError, 'Not enough free space'):
                plan_copy(self.source, self.record, self.destination)
        self.assertEqual(self.destination.read_bytes(), before)
