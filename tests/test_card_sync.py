"""Whole-card format conversion and stale-write protection."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from save_core.backends import get_backend
from save_core.card_sync import plan_card_copy
from save_core.save_copy import apply_copy
from test_ps1 import fixture, payload
from test_ftp import vmp
from test_save_copy import generic_save
from test_save_merge import card_file


class CardSyncTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_ps2_converts_both_directions_without_changing_progress(self):
        backend = get_backend('ps2')
        source, target = self.root/'source.bin', self.root/'target.ps2'
        card_file(source,[generic_save('GAME',b'progress')],False)
        card_file(target,[generic_save('OLD',b'old')],True)
        original = target.read_bytes()
        plan = plan_card_copy('ps2',source,target)
        self.assertEqual(len(plan.candidate), len(original))
        self.assertEqual(backend.card_data(plan.candidate),source.read_bytes())
        backup = apply_copy(plan,self.root/'backups')
        self.assertEqual(backup.read_bytes(),original)
        plan = plan_card_copy('ps2',target,source)
        self.assertEqual(plan.action,'Already identical')
        self.assertEqual(plan.candidate,source.read_bytes())
        # Equivalent ECC cards preserve their spare bytes rather than rewriting them.
        self.assertEqual(backend.card_image(backend.card_data(target.read_bytes()),target.read_bytes()),target.read_bytes())
        with self.assertRaisesRegex(ValueError,'same capacity'):
            backend.card_image(source.read_bytes()[:512], original)

    def test_explicit_whole_copy_initializes_a_completely_blank_ps2_target(self):
        source, target = self.root/'source.bin', self.root/'empty.ps2'
        card_file(source,[generic_save('GAME',b'progress')],False)
        target.write_bytes(b'\xff' * (8 * 1024 * 1024 // 512 * 528))
        plan = plan_card_copy('ps2', source, target)
        backend = get_backend('ps2')
        self.assertEqual(backend.card_data(plan.candidate), source.read_bytes())
        self.assertEqual(len(plan.candidate), target.stat().st_size)
        damaged = bytearray(target.read_bytes());damaged[1000] = 0
        target.write_bytes(damaged)
        with self.assertRaisesRegex(ValueError,'raw or ECC'):
            plan_card_copy('ps2', source, target)

    def test_ps1_card_copy_publishes_cloud_manifest_and_preserves_backup(self):
        source = self.root/'source.mcr'
        source.write_bytes(fixture([('NEW',(1,),payload('New save'))]))
        target = self.root/'cloud/saves/PCSX-ReARMed/pcsx-card1.mcd'
        target.parent.mkdir(parents=True)
        original = fixture([('OLD',(2,),payload('Old save'))])
        target.write_bytes(original)
        manifest = self.root/'cloud/manifest.server'
        manifest.write_text(json.dumps([dict(path='saves/PCSX-ReARMed/pcsx-card1.mcd',hash=hashlib.md5(original).hexdigest())]))
        plan = plan_card_copy('ps1',source,target)
        backup = apply_copy(plan,self.root/'backups')
        self.assertEqual(target.read_bytes(),source.read_bytes())
        self.assertEqual(backup.read_bytes(),original)
        self.assertEqual(json.loads(manifest.read_text())[0]['hash'],hashlib.md5(source.read_bytes()).hexdigest())

    def test_psp_can_be_read_but_signed_destinations_remain_protected(self):
        backend = get_backend('ps1')
        raw = fixture([('SAVE',(1,),payload())])
        self.assertEqual(backend.card_data(vmp(raw)),raw)
        with self.assertRaisesRegex(ValueError,'read-only'):
            backend.card_image(raw,vmp(raw))

    def test_changed_destination_blocks_apply_before_backup_or_write(self):
        source, target = self.root/'source.mcr', self.root/'target.mcr'
        source.write_bytes(fixture([('SAVE',(1,),payload('New'))]))
        target.write_bytes(fixture([('SAVE',(1,),payload('Old'))]))
        plan = plan_card_copy('ps1',source,target)
        changed = fixture([('SAVE',(1,),payload('Played elsewhere'))])
        target.write_bytes(changed)
        with self.assertRaisesRegex(ValueError,'changed'):
            apply_copy(plan,self.root/'backups')
        self.assertEqual(target.read_bytes(),changed)
        self.assertFalse((self.root/'backups').exists())
