"""Read-only FTP scans, source freshness, offline retention and credential privacy."""

from ftplib import error_perm
import hashlib
import json
from contextlib import closing
from pathlib import Path, PurePosixPath
import sqlite3
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from save_core.backends import get_backend
from save_core.backends.ps1.card import Card, blank_card
from save_web.database import Database, SCHEMA
from save_web.service import Service
from save_web.transports import directory_entries, ftp_source_file
from test_ps1 import fixture, payload


def vmp(data):
    header=bytearray(128);header[:4]=b'\0PMV';struct.pack_into('<I',header,4,128)
    header[12:32]=bytes(range(20))
    return bytes(header)+data


class ReadOnlyFTP:
    def __init__(self,state,timeout=8):
        self.state=state;self.sock=None;self.folder=PurePosixPath('/')

    def connect(self,host,port,timeout):
        if not self.state['online']:raise ConnectionRefusedError('offline')
        self.sock=SimpleNamespace(settimeout=lambda value:None)
        return '220 Ready'

    def login(self,username,password):
        if self.state.get('bad_login'):raise error_perm('530 '+password)

    def cwd(self,path):
        self.folder=PurePosixPath(path)
        if not any(PurePosixPath(name).is_relative_to(self.folder) for name in self.state['files']):
            raise error_perm('550 Missing folder')

    def retrlines(self,command,callback):
        assert command=='LIST'
        entries={}
        for filename,data in self.state['files'].items():
            path=PurePosixPath(filename)
            if path.is_relative_to(self.folder):
                parts=path.relative_to(self.folder).parts
                if parts:entries[parts[0]]=('d',0) if len(parts)>1 else ('-',len(data))
        for name,(kind,size) in sorted(entries.items()):
            callback(f'{kind}rwxrwxrwx 1 psp psp {size} Jan 01 1970 {name}')

    def retrbinary(self,command,callback):
        assert command.startswith('RETR '),'FTP writes must never be sent'
        path=command[5:];self.state['reads'].append(path)
        if path==self.state.get('fail_read'):raise ConnectionResetError('download interrupted')
        if path not in self.state['files']:raise error_perm('550 Missing file')
        callback(self.state['files'][path])

    def quit(self):
        self.sock=None
        raise EOFError('PSP closes without a QUIT reply')

    def close(self):self.sock=None


class FTPTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.source='/PSP/SAVEDATA/SCUS12345/SCEVMC0.VMP'
        self.second='/PSP/SAVEDATA/SCUS12345/SCEVMC1.VMP'
        self.state=dict(online=True,reads=[],files={self.source:vmp(fixture([('GAME',(4,),payload())])),
            self.second:vmp(blank_card()),'/PSP/SAVEDATA/Native PSP/DATA.BIN':b'not a PS1 card'})
        self.mock=patch('save_web.transports.FTP',side_effect=lambda **kwargs:ReadOnlyFTP(self.state,**kwargs))
        self.mock.start();self.addCleanup(self.mock.stop)
        self.service=Service(self.root/'data');self.addCleanup(self.service.close)
        self.device=self.service.create_device(dict(name='PSP',kind='PSP',transport='ftp',root_path='/',ftp_host='psp.local',ftp_password='private-password'))
        self.location=self.service.save_location(self.device['id'],dict(name='PS1 cards',path='PSP/SAVEDATA',backend='ps1',suffix='.vmp'))
        local=self.root/'local';local.mkdir();self.target=local/'card.mcr';self.target.write_bytes(blank_card())
        self.local=self.service.create_device(dict(name='Emulator',root_path=str(local)))
        self.service.save_location(self.local['id'],dict(name='Cards',path='.',backend='ps1',suffix='.mcr',writable=True))
        self.scan(self.local)

    def scan(self,device=None):
        job=self.service.queue_scan((device or self.device)['id']);self.service.run_scan(job['id'])
        return self.service.db.one('SELECT * FROM jobs WHERE id=?',(job['id'],))

    def test_local_scan_errors_keep_their_diagnostics(self):
        with self.service.db.connect() as db:
            db.execute("UPDATE locations SET marker='online.marker' WHERE id=?", (self.location['id'],))
        job = self.scan()
        self.assertEqual(job['status'], 'failed')
        self.assertIn('availability marker is missing', job['error'])
        with self.service.db.connect() as db:
            db.execute("UPDATE locations SET marker='' WHERE id=?", (self.location['id'],))
        with patch('save_web.transports.Path.write_bytes', side_effect=OSError('Disk full')):
            job = self.scan()
        self.assertEqual(job['status'], 'failed')
        self.assertIn('Disk full', job['error'])
        self.assertNotIn('FTP unavailable', job['error'])

    def test_failed_cache_write_removes_temporary_file(self):
        device = self.service.db.one('SELECT * FROM devices WHERE id=?', (self.device['id'],))
        filename = self.source.removeprefix('/PSP/SAVEDATA/')
        expected = hashlib.sha256(self.state['files'][self.source]).hexdigest()
        cache = self.root/'cache'
        real_temporary = tempfile.NamedTemporaryFile
        def failed_temporary(**kwargs):
            handle = real_temporary(**kwargs)
            handle.write = lambda data: (_ for _ in ()).throw(OSError('Disk full'))
            return handle
        with patch('save_web.transports.tempfile.NamedTemporaryFile', side_effect=failed_temporary):
            with self.assertRaisesRegex(OSError, 'Disk full'):
                ftp_source_file(device, self.location, filename, expected, cache)
        self.assertFalse([path for path in cache.rglob('*') if path.is_file()])

    def test_ps3_vm1_vm2_ftp_sources_share_remote_folder(self):
        from test_save_merge import card_file
        from test_save_copy import generic_save
        ps2 = self.root/'PS2MEMCARD.VM2'
        card_file(ps2,[generic_save('PS2SAVE',b'PS3 virtual card')],True)
        self.state['files']['/PSP/SAVEDATA/PS2MEMCARD.VM2']=ps2.read_bytes()
        self.state['files']['/PSP/SAVEDATA/PS1MEMCARD.VM1']=fixture([('PS1SAVE',(3,),payload())])
        self.service.save_location(self.device['id'],dict(suffix='.vm1'),self.location['id'])
        second=self.service.save_location(self.device['id'],dict(name='PS2 cards',path='PSP/SAVEDATA',backend='ps2',suffix='.vm2'))
        self.assertEqual(self.scan()['status'],'done')
        cards=[c for c in self.service.inventory()['cards'] if c['location_id'] in (self.location['id'],second['id']) and c['present']]
        self.assertEqual({c['filename'] for c in cards},{'PS1MEMCARD.VM1','PS2MEMCARD.VM2'})
        self.assertTrue(all(c['read_only'] for c in cards))
        games=self.service.library.inventory()['games']
        self.assertTrue(any(g['backend']=='ps2' and g['game_key']=='PS2SAVE' for g in games))
        self.assertEqual(self.scan()['status'],'done')
        self.assertEqual(len(self.service.db.rows('SELECT id FROM locations WHERE device_id=? AND deleted=0',(self.device['id'],))),2)

    def test_credentials_hidden_and_connection_validation(self):
        self.assertNotIn('ftp_password',self.device)
        self.assertTrue(self.device['has_ftp_password'])
        self.assertNotIn('private-password',json.dumps(self.service.inventory()))
        updated=self.service.update_device(self.device['id'],dict(name='Portable PSP'))
        self.assertNotIn('ftp_password',updated)
        self.assertEqual(self.service.db.one('SELECT ftp_password FROM devices WHERE id=?',(self.device['id'],))['ftp_password'],'private-password')
        self.assertEqual((self.root/'data/inventory.sqlite3').stat().st_mode & 0o777,0o600)
        for body in (dict(ftp_port=True),dict(ftp_port=0),dict(ftp_host='ftp://psp.local'),dict(root_path='/../etc'),dict(ftp_username='bad\r\nSTOR')):
            with self.subTest(body=body),self.assertRaises(ValueError):
                self.service.update_device(self.device['id'],body)
        with self.assertRaisesRegex(ValueError,'read-only'):
            self.service.save_location(self.device['id'],dict(name='Unsafe',path='other',backend='ps1',suffix='.vmp',writable=True))
        with self.assertRaises(ValueError):
            self.service.save_location(self.device['id'],dict(name='Escape',path='../outside',backend='ps1',suffix='.vmp'))

    def test_scan_vmp_copy_source_and_readonly_destination(self):
        before=dict(self.state['files'])
        self.assertEqual(self.scan()['status'],'done')
        inventory=self.service.inventory();remote=[c for c in inventory['cards'] if c['location_id']==self.location['id']]
        self.assertEqual(len(remote),2);self.assertTrue(all(c['read_only'] for c in remote))
        save=next(s for s in inventory['saves'] if s['directory']=='GAME')
        target=next(c for c in inventory['cards'] if c['filename']=='card.mcr')
        self.assertEqual(next(c for c in remote if c['filename'].endswith('SCEVMC0.VMP'))['sha256'],hashlib.sha256(before[self.source]).hexdigest())
        preview=self.service.preview(dict(save_id=save['id'],destination_card_id=target['id']))
        reads=len(self.state['reads'])
        result=self.service.apply(preview['id'],dict(cards_idle=True))
        self.assertGreater(len(self.state['reads']),reads)
        self.assertEqual(Path(result['backup']).read_bytes(),blank_card())
        card=Card(self.target.read_bytes());self.assertEqual(card.payload(card.entries['GAME']),payload())
        self.assertEqual(self.state['files'],before)
        with self.assertRaisesRegex(ValueError,'read-only'):
            self.service._card(remote[0]['id'],writable=True)

    def test_offline_and_partial_download_keep_last_inventory(self):
        self.scan();before=self.service.inventory()
        ids=[c['id'] for c in before['cards'] if c['location_id']==self.location['id']]
        self.state['online']=False
        self.assertEqual(self.scan()['status'],'failed')
        after=self.service.inventory()
        self.assertEqual([c['id'] for c in after['cards'] if c['location_id']==self.location['id']],ids)
        self.assertEqual(next(l for l in after['locations'] if l['id']==self.location['id'])['status'],'offline')
        self.assertEqual(after['saves'],before['saves'])
        self.state['online']=True;self.state['fail_read']=self.second
        self.assertEqual(self.scan()['status'],'failed')
        self.assertEqual(self.service.inventory()['saves'],before['saves'])
        self.state.pop('fail_read')
        self.assertEqual(self.scan()['status'],'done')

    def test_changed_or_disconnected_source_never_uses_old_cache(self):
        self.scan();inventory=self.service.inventory()
        save=next(s for s in inventory['saves'] if s['directory']=='GAME')
        target=next(c for c in inventory['cards'] if c['filename']=='card.mcr')
        preview=self.service.preview(dict(save_id=save['id'],destination_card_id=target['id']))
        self.state['files'][self.source]=vmp(fixture([('GAME',(4,),payload('New progress'))]))
        with self.assertRaisesRegex(ValueError,'changed since'):
            self.service.apply(preview['id'],dict(cards_idle=True))
        self.assertEqual(self.target.read_bytes(),blank_card())
        self.scan()
        preview=self.service.preview(dict(save_id=save['id'],destination_card_id=target['id']))
        self.state['online']=False
        with self.assertRaises(OSError):self.service.apply(preview['id'],dict(cards_idle=True))
        self.assertEqual(self.target.read_bytes(),blank_card())
        self.assertEqual(next(l for l in self.service.inventory()['locations'] if l['id']==self.location['id'])['status'],'offline')

    def test_login_error_does_not_echo_password(self):
        self.state['bad_login']=True
        job=self.scan()
        self.assertEqual(job['status'],'failed')
        self.assertIn('login rejected',job['error'])
        self.assertNotIn('private-password',json.dumps(self.service.inventory()))

    def test_local_vmp_writes_are_refused_and_source_payload_works(self):
        backend=get_backend('ps1');source=self.root/'card.vmp';source.write_bytes(self.state['files'][self.source])
        bundle=backend.read_save(source,'GAME');self.assertEqual(bundle.file_data()['data'],payload())
        before=source.read_bytes()
        with self.assertRaisesRegex(ValueError,'read-only'):backend.plan_delete(source,{'save':'GAME'})
        with self.assertRaisesRegex(ValueError,'read-only'):backend.plan_copy(self.target,{'save':'GAME'},source)
        self.assertEqual(source.read_bytes(),before)
        bad=bytearray(before);struct.pack_into('<I',bad,4,64)
        with self.assertRaisesRegex(ValueError,'header'):backend.validate(bytes(bad),'card.vmp')

    def test_listing_rejects_traversal_and_supports_windows(self):
        class Listing:
            def cwd(self,path):pass
            def retrlines(self,command,callback):
                for line in self.lines:callback(line)
        listing=Listing();listing.lines=['11-11-26  01:00PM       <DIR>          Games','11-11-26  01:00PM       131200         card.VMP']
        self.assertEqual(directory_entries(listing,'/'),[('Games','d',0),('card.VMP','-',131200)])
        listing.lines=['-rwxrwxrwx 1 psp psp 131200 Jan 01 1970 ../escape.VMP']
        with self.assertRaises(ValueError):directory_entries(listing,'/')

    def test_version_nine_adds_connection_defaults_without_changing_locations(self):
        old=SCHEMA.replace("    transport TEXT NOT NULL DEFAULT 'filesystem', ftp_host TEXT NOT NULL DEFAULT '',\n",'')
        old=old.replace("    ftp_port INTEGER NOT NULL DEFAULT 21, ftp_username TEXT NOT NULL DEFAULT 'anonymous',\n",'')
        old=old.replace("    ftp_password TEXT NOT NULL DEFAULT '',\n",'')
        path=self.root/'legacy.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(old)
            db.execute("INSERT INTO devices(id,name,kind,root_path) VALUES (7,'PS2','PS2','/devices/ps2')")
            db.execute("INSERT INTO locations(id,device_id,name,path,suffix) VALUES (8,7,'VMC','VMC','.bin')")
            db.execute("INSERT INTO cards(id,location_id,filename,sha256,size,mtime_ns,formatted,last_seen) VALUES (9,8,'generic.bin','hash',8388608,1,1,'old')")
            db.execute('PRAGMA user_version=9')
        migrated=Database(path)
        device=migrated.one('SELECT * FROM devices WHERE id=7')
        self.assertEqual(device['transport'],'filesystem');self.assertEqual(device['root_path'],'/devices/ps2')
        self.assertEqual(device['ftp_password'],'')
        self.assertEqual(migrated.one('SELECT path FROM locations WHERE id=8')['path'],'VMC')
        self.assertEqual(migrated.one('SELECT location_id FROM cards WHERE id=9')['location_id'],8)
        with migrated.connect() as db:self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
