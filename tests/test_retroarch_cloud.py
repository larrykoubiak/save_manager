"""A cloud-card transfer must publish its manifest or restore the old card."""

import hashlib
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from save_core.save_copy import CopyPlan, CopyApplyError, apply_copy
from save_core.retroarch_cloud import manifest_for
from save_core.save_merge import digest


class RetroArchCloudTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        folder=self.root/'cloud/saves/PCSX-ReARMed';folder.mkdir(parents=True)
        self.card=folder/'pcsx-card1.mcd';self.card.write_bytes(b'empty card')
        self.source=self.root/'source.mcr';self.source.write_bytes(b'game save')
        self.manifest=self.root/'cloud/manifest.server'
        self.rows=[dict(path='saves/other.srm',hash=''),dict(path='saves/PCSX-ReARMed/pcsx-card1.mcd',hash=hashlib.md5(b'empty card').hexdigest())]
        self.manifest.write_text(json.dumps(self.rows))
        self.plan=CopyPlan(self.source,self.card,digest(b'game save'),digest(b'empty card'),'SAVE','SAVE','Copy',b'card with saves',b'empty card')

    def test_copy_publishes_md5_and_keeps_other_entries_and_backup(self):
        original=self.manifest.read_bytes()
        backup=apply_copy(self.plan,self.root/'backups')
        rows=json.loads(self.manifest.read_bytes())
        self.assertIn(self.rows[0],rows)
        self.assertEqual([r['path'] for r in rows],sorted([r['path'] for r in rows],key=str.lower))
        self.assertIn(dict(path='saves/PCSX-ReARMed/pcsx-card1.mcd',hash=hashlib.md5(b'card with saves').hexdigest()),rows)
        self.assertEqual((backup.parent/'retroarch-manifest.server.json').read_bytes(),original)
        self.assertEqual(backup.read_bytes(),b'empty card')

    def test_identical_card_still_repairs_stale_manifest(self):
        self.rows[1]['hash']='1'*32
        self.manifest.write_text(json.dumps(self.rows))
        plan=replace(self.plan,action='Already identical',candidate=b'')
        backup=apply_copy(plan,self.root/'backups')
        self.assertEqual(self.card.read_bytes(),b'empty card')
        self.assertTrue(manifest_for(self.card).matches(b'empty card'))
        self.assertEqual(backup.read_bytes(),b'empty card')

    def test_failed_manifest_publish_restores_card_and_reports_failure(self):
        original=self.manifest.read_bytes()
        with patch('save_core.retroarch_cloud.atomic_write',side_effect=OSError('Metadata upload failed')):
            with self.assertRaises(CopyApplyError) as caught:
                apply_copy(self.plan,self.root/'backups')
        self.assertEqual(self.card.read_bytes(),b'empty card')
        self.assertEqual(self.manifest.read_bytes(),original)
        self.assertEqual(Path(caught.exception.backup).read_bytes(),b'empty card')

    def test_malformed_manifest_stops_before_card_write(self):
        self.manifest.write_text('bad JSON')
        with self.assertRaisesRegex(ValueError,'Invalid RetroArch'):
            apply_copy(self.plan,self.root/'backups')
        self.assertEqual(self.card.read_bytes(),b'empty card')

    def test_concurrent_manifest_change_is_preserved_and_card_restored(self):
        from save_core.retroarch_cloud import RetroArchManifest
        real=RetroArchManifest.publish
        changed=json.dumps([dict(path='saves/another.srm',hash='1'*32)]).encode()
        def concurrent(manifest,data):
            manifest.path.write_bytes(changed)
            return real(manifest,data)
        with patch.object(RetroArchManifest,'publish',concurrent):
            with self.assertRaises(CopyApplyError):
                apply_copy(self.plan,self.root/'backups')
        self.assertEqual(self.manifest.read_bytes(),changed)
        self.assertEqual(self.card.read_bytes(),b'empty card')

    def test_missing_card_entry_is_added_and_ordinary_folders_are_unchanged(self):
        self.manifest.write_text(json.dumps([self.rows[0]]))
        apply_copy(self.plan,self.root/'backups')
        self.assertEqual(len(json.loads(self.manifest.read_text())),2)
        self.assertIsNone(manifest_for(self.source))
