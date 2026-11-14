"""VMP signatures agree with the independent dots-tb C converter."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from save_core.backends.ps1 import PS1Backend, vmp
from save_core.backends.ps1.card import blank_card
from save_core.card_sync import plan_card_copy
from save_core.save_copy import apply_copy
from save_web.service import Service
from test_ftp import vmp as container
from test_ftp_write import WritableFTP
from test_ps1 import fixture, payload


class VMPTest(unittest.TestCase):
    def test_upstream_c_known_answer(self):
        original = bytearray(128) + blank_card()
        original[:4] = b'\0PMV'
        struct.pack_into('<I', original, 4, 128)
        signed = vmp.wrap(blank_card(), original)
        # vita-mcr2vmp's unmodified C executable, signing this public blank card.
        self.assertEqual(signed[32:52].hex(), 'ab0a9dc64d2cb0018a94bf30f55888a755891c0a')
        self.assertTrue(vmp.verify(signed))
        self.assertEqual(vmp.wrap(blank_card(), signed), signed)

    def test_payload_header_and_signature_are_independently_checked(self):
        original = container(blank_card())
        changed = fixture([('GAME',(1,),payload())])
        signed = vmp.wrap(changed, original)
        self.assertEqual(signed[:32], original[:32])
        self.assertEqual(signed[52:128], original[52:128])
        self.assertEqual(signed[128:], changed)
        for offset in (12, 32, 512):
            bad = bytearray(signed);bad[offset] ^= 1
            self.assertFalse(vmp.verify(bad))
            with self.assertRaises(ValueError):
                PS1Backend().validate(bad, 'card.vmp')
        with self.assertRaisesRegex(ValueError, 'header'):
            vmp.wrap(changed, original[:-1])

    def test_manual_copy_and_delete_preserve_signed_container_and_original_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);source=root/'raw.mcr';dest=root/'card.vmp'
            source.write_bytes(fixture([('GAME',(1,),payload())]))
            original=container(blank_card());dest.write_bytes(original)
            backend=PS1Backend()
            plan=backend.plan_copy(source, {'save':'GAME'}, dest)
            backup=apply_copy(plan, root/'backups')
            self.assertEqual(backup.read_bytes(), original)
            backend.validate(dest.read_bytes(),dest)
            plan=backend.plan_delete(dest, {'save':'GAME'})
            self.assertTrue(vmp.verify(plan.candidate))
            self.assertEqual(plan.candidate[:32], original[:32])

    def test_game_folders_sync_both_directions_with_shared_card(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);local=root/'local';local.mkdir()
            shared=local/'shared.mcr'
            progress=fixture([('GAME',(1,),payload('Shared progress'))]);shared.write_bytes(progress)
            paths=('/PSP/SAVEDATA/GAME_A/SCEVMC0.VMP','/PSP/SAVEDATA/GAME_B/SCEVMC0.VMP')
            originals={path:container(blank_card()) for path in paths}
            state=dict(online=True, reads=[], files=dict(originals))
            with patch('save_web.transports.FTP',side_effect=lambda **kwargs:WritableFTP(state,**kwargs)):
                service=Service(root/'data')
                try:
                    service.sync.settle_seconds=0
                    desktop=service.create_device(dict(name='Desktop',root_path=str(local)))
                    service.save_location(desktop['id'],dict(name='Shared',path='.',suffix='.mcr',backend='ps1',writable=True))
                    psp=service.create_device(dict(name='PSP',transport='ftp',root_path='/',ftp_host='psp.local'))
                    service.save_location(psp['id'],dict(name='Games',path='PSP/SAVEDATA',suffix='.vmp',backend='ps1',writable=True))
                    def scan():
                        for device in (desktop,psp):
                            job=service.queue_scan(device['id']);service.run_scan(job['id'])
                            self.assertEqual(service.db.one('SELECT status FROM jobs WHERE id=?',(job['id'],))['status'],'done')
                    def tick():
                        service.sync.next_tick=0;service.sync.tick()
                    scan();scan()
                    cards=service.inventory()['cards'];source=next(c['id'] for c in cards if c['filename']=='shared.mcr')
                    group=service.sync.configure(dict(name='Shared card 1',card_ids=[c['id'] for c in cards]))
                    service.sync.resolve(group['id'],dict(source_card_id=source));tick()
                    for path in paths:
                        self.assertEqual(PS1Backend.card_data(state['files'][path]),progress)
                        self.assertTrue(vmp.verify(state['files'][path]))
                    scan();scan();tick()
                    self.assertEqual(service.sync.group(group['id'])['status'],'synced')
                    newer=fixture([('GAME',(1,),payload('Played on PSP'))])
                    state['files'][paths[0]]=vmp.wrap(newer,state['files'][paths[0]])
                    scan();scan();tick();scan();scan();tick()
                    self.assertEqual(shared.read_bytes(),newer)
                    self.assertEqual(PS1Backend.card_data(state['files'][paths[1]]),newer)
                    self.assertEqual(service.sync.group(group['id'])['status'],'synced')
                    self.assertTrue(all(vmp.verify(state['files'][path]) for path in paths))
                    self.assertFalse(any(path.endswith(('.tmp','.bak')) for path in state['files']))
                finally:
                    service.close()
