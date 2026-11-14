"""FTP card replacements keep verified backups and never truncate the live file."""

from ftplib import error_perm
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from save_core.card_sync import plan_card_copy
from save_web.service import Service
from save_web.transports import ftp_apply_copy
from test_ftp import ReadOnlyFTP
from test_ps1 import fixture, payload


class WritableFTP(ReadOnlyFTP):
    def storbinary(self, command, source):
        name = command.removeprefix('STOR ')
        assert name.endswith('.tmp')
        data = source.read()
        self.state['files'][name] = data
        if self.state.get('fail_upload'):
            raise ConnectionResetError('Upload interrupted')
        if self.state.get('corrupt_upload'):
            self.state['files'][name] = b'bad upload'
        if self.state.get('edit_during_upload'):
            self.state['files'][self.state['remote']] = self.state['edit_during_upload']

    def rename(self, source, destination):
        if self.state.get('fail_install') and source.endswith('.tmp'):
            raise error_perm('550 Rename refused')
        if destination in self.state['files']:
            raise error_perm('550 Destination already exists')
        self.state['files'][destination] = self.state['files'].pop(source)

    def delete(self, filename):
        self.state['files'].pop(filename,None)


class FTPWriteTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.remote = '/dev_hdd0/savedata/vmc/PS1MEMCARD.VM1'
        self.original = fixture([('OLD',(1,),payload('Old save'))])
        self.new = fixture([('NEW',(1,),payload('New save'))])
        self.state = dict(online=True,reads=[],files={self.remote:self.original},remote=self.remote)
        mock = patch('save_web.transports.FTP',side_effect=lambda **kwargs:WritableFTP(self.state,**kwargs))
        mock.start();self.addCleanup(mock.stop)
        self.service = Service(self.root/'data');self.addCleanup(self.service.close)
        self.service.sync.settle_seconds = 0
        self.device = self.service.create_device(dict(name='PS3',transport='ftp',root_path='/dev_hdd0/savedata/vmc',ftp_host='ps3.local'))
        self.location = self.service.save_location(self.device['id'],dict(name='PS1',path='.',backend='ps1',suffix='.vm1',writable=True))
        local = self.root/'local';local.mkdir()
        self.source = local/'shared.mcr';self.source.write_bytes(self.new)
        self.local = self.service.create_device(dict(name='Desktop',root_path=str(local)))
        self.service.save_location(self.local['id'],dict(name='PS1',path='.',backend='ps1',suffix='.mcr',writable=True))
        self.scan()
        self.card = self.service.db.one('SELECT * FROM cards WHERE location_id=?',(self.location['id'],))
        self.cached = self.service._card(self.card['id'],writable=True)[2]
        self.plan = plan_card_copy('ps1',self.source,self.cached)
        self.private_device = self.service.db.one('SELECT * FROM devices WHERE id=?',(self.device['id'],))

    def scan(self):
        for device in (self.device,self.local):
            job = self.service.queue_scan(device['id']);self.service.run_scan(job['id'])
            self.assertEqual(self.service.db.one('SELECT status FROM jobs WHERE id=?',(job['id'],))['status'],'done')

    def apply(self):
        return ftp_apply_copy(self.private_device,self.location,self.card['filename'],self.plan,self.root/'backups')

    def test_success_replaces_remote_and_preserves_local_original(self):
        backup = self.apply()
        self.assertEqual(self.state['files'][self.remote],self.new)
        self.assertEqual(backup.read_bytes(),self.original)
        self.assertEqual(set(self.state['files']),{self.remote})

    def test_upload_corruption_interruption_and_rename_failure_preserve_original(self):
        for failure in ('fail_upload','corrupt_upload','fail_install'):
            with self.subTest(failure=failure):
                self.cached.write_bytes(self.original)
                self.state[failure] = True
                with self.assertRaises(Exception) as caught:
                    self.apply()
                self.assertEqual(self.state['files'][self.remote],self.original)
                self.assertEqual(Path(caught.exception.backup).read_bytes(),self.original)
                self.assertEqual(set(self.state['files']),{self.remote})
                self.state[failure] = False

    def test_concurrent_remote_edit_is_preserved(self):
        changed = fixture([('ELSEWHERE',(1,),payload('Played elsewhere'))])
        self.state['edit_during_upload'] = changed
        with self.assertRaisesRegex(Exception,'changed during upload'):
            self.apply()
        self.assertEqual(self.state['files'][self.remote],changed)

    def test_sync_dispatches_to_ftp_and_scan_updates_library(self):
        self.scan()
        local = self.service.db.one('SELECT id FROM cards WHERE location_id!=?',(self.location['id'],))['id']
        group = self.service.sync.configure({'name':'PS1 shared card 1','card_ids':[local,self.card['id']]})
        self.assertEqual(group['status'],'conflict')
        self.service.sync.resolve(group['id'],{'source_card_id':local})
        self.service.sync.next_tick = 0;self.service.sync.tick()
        self.assertEqual(self.state['files'][self.remote],self.new)
        self.scan();self.scan()
        self.service.sync.next_tick = 0;self.service.sync.tick()
        self.assertEqual(self.service.sync.group(group['id'])['status'],'synced')
        remote_card = self.service.db.one('SELECT * FROM cards WHERE id=?',(self.card['id'],))
        self.assertEqual(remote_card['sha256'],self.service.db.one('SELECT sha256 FROM cards WHERE id=?',(local,))['sha256'])
        self.assertFalse(self.service.inventory()['cards'][0]['read_only'])

    def test_manual_save_transfer_uses_the_remote_writer_too(self):
        save = self.service.db.one("SELECT * FROM saves WHERE directory='NEW'")
        preview = self.service.preview({'save_id':save['id'],'destination_card_id':self.card['id']})
        done = self.service.apply(preview['id'],{'cards_idle':True})
        self.assertEqual(done['status'],'done')
        self.assertNotEqual(self.state['files'][self.remote],self.original)
        self.assertEqual(Path(done['backup']).read_bytes(),self.original)
