"""Synthetic PS1 cards based on PSX-SPX frame and block layouts."""

import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from save_core.backends import get_backend
from save_core.backends.ps1.card import Card, Metadata, BLOCK, SIZE, blank_card
from save_core.backends.ps1.icons import encode_gif
from save_core.save_copy import apply_copy


def seal(frame):
    frame = bytearray(frame)
    value = 0
    for byte in frame[:127]:
        value ^= byte
    frame[127] = value
    return bytes(frame)


def payload(title='Example RPG', blocks=1, frames=2, fill=0x77):
    data = bytearray([fill])*(blocks*BLOCK)
    data[:512] = bytes(512)
    data[:4] = b'SC'+bytes((0x10+frames,blocks))
    title = title.encode('shift_jis')
    data[4:4+len(title)] = title
    struct.pack_into('<16H',data,96,0x8000,0,0x001f,0x03e0,0x7c00,*([0]*11))
    for number in range(frames):
        data[128*(number+1):128*(number+2)] = bytes([0x10 if number==0 else 0x22 if number==1 else 0x33])*128
    return bytes(data)


def fixture(entries=()):
    data = bytearray(blank_card())
    for name,blocks,content in entries:
        assert len(content)==len(blocks)*BLOCK
        for index,block in enumerate(blocks):
            frame = bytearray(128)
            struct.pack_into('<I',frame,0,0x51 if index==0 else 0x53 if index==len(blocks)-1 else 0x52)
            if index==0:
                struct.pack_into('<I',frame,4,len(content))
                frame[10:10+len(name)] = name.encode('ascii')
            struct.pack_into('<H',frame,8,blocks[index+1]-1 if index+1<len(blocks) else 0xffff)
            frame[40] = 0x40+index  # Unknown save metadata must survive relocation.
            data[block*128:(block+1)*128] = seal(frame)
            data[block*BLOCK:(block+1)*BLOCK] = content[index*BLOCK:(index+1)*BLOCK]
    return bytes(data)


class PS1Test(unittest.TestCase):
    def setUp(self):
        self.backend = get_backend('ps1')

    def test_nested_path_boundaries(self):
        from save_core.storage_paths import card_path
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            location = dict(path=directory, suffix='.vmc', marker='')
            (root/'game').mkdir()
            path = root/'game'/'SLOT0.VMC'
            path.write_bytes(blank_card())
            (root/'linked').symlink_to(root/'game', target_is_directory=True)
            self.assertEqual(card_path(location, 'game/SLOT0.VMC'), path)
            self.assertEqual([r['filename'] for r in self.backend.snapshot_location(location)], ['game/SLOT0.VMC'])
            for filename in ('../outside.vmc', str(path), 'linked/SLOT0.VMC', 'game/../game/SLOT0.VMC'):
                with self.subTest(filename=filename), self.assertRaises(ValueError):
                    card_path(location, filename)
            (root/'alias.vmc').symlink_to(path)
            with self.assertRaises(ValueError):
                self.backend.snapshot_location(location)

    def test_wrong_system_file_reports_relative_name_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)/'Other system'
            folder.mkdir()
            (folder/'game.srm').write_bytes(bytes(32768))
            with self.assertRaisesRegex(ValueError, r'Other system/game.srm: .*32,768 bytes'):
                self.backend.snapshot_location(dict(path=directory, suffix='.srm', marker=''))

    def test_layout_noncontiguous_chain_title_and_capacity(self):
        content = payload('ＲＰＧ',3)
        data = fixture([('BASLUS-01234TEST',(7,2,14),content)])
        card = Card(data)
        self.assertEqual(card.entries['BASLUS-01234TEST'].blocks,(7,2,14))
        self.assertEqual(card.payload(card.entries['BASLUS-01234TEST']),content)
        self.assertEqual(len(card.free_blocks()),12)
        self.assertEqual(data[:2],b'MC')
        self.assertEqual(data[127],0x0e)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'card.mcr';p.write_bytes(data)
            records=self.backend.snapshot_location(dict(path=directory,suffix='*',marker=''))
            self.assertEqual(records[0]['free_blocks'],96)
            self.assertEqual(records[0]['total_blocks'],120)
            save=records[0]['saves'][0]
            self.assertEqual(save['title'],'RPG')
            self.assertEqual(save['size_blocks'],24)
            self.assertIsNone(save['updated'])
            self.assertTrue(save['icon_model'].startswith(b'PS1I'))
            self.assertEqual(self.backend.read_save(p,'BASLUS-01234TEST').file_data(),{'data':content})
            self.assertEqual(p.read_bytes(),data)

    def test_empty_unformatted_and_supported_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name,data in [('empty.mcd',blank_card()),('blank.srm',bytes(SIZE)),('erased.mcr',b'\xff'*SIZE)]:
                (root/name).write_bytes(data)
            (root/'unrelated.txt').write_text('ignored')
            records=self.backend.snapshot_location(dict(path=directory,suffix='*',marker=''))
            self.assertEqual(len(records),3)
            self.assertEqual(sum(c['formatted'] for c in records),1)
            self.assertEqual(next(c['free_blocks'] for c in records if c['formatted']),120)
            self.assertFalse(any(c['saves'] for c in records))
        for data in (b'header'+blank_card(),blank_card()[:-1],bytes(SIZE)):
            with self.assertRaises(ValueError):self.backend.validate(data,'card')

    def test_corrupt_directory_chains_and_checksums_are_rejected(self):
        good=fixture([('GAME',(1,4,8),payload(blocks=3))])
        for kind in ('header','checksum','cycle','outside','wrong_state','size','orphan','duplicate','crosslink'):
            with self.subTest(kind=kind):
                bad=bytearray(good)
                if kind=='header':bad[127]^=1
                elif kind=='checksum':bad[128+40]^=1
                else:
                    block=1
                    frame=bytearray(bad[128:256])
                    if kind=='cycle':
                        block=8;frame=bytearray(bad[8*128:9*128]);struct.pack_into('<I',frame,0,0x52);struct.pack_into('<H',frame,8,0)
                    elif kind=='outside':struct.pack_into('<H',frame,8,15)
                    elif kind=='wrong_state':struct.pack_into('<I',frame,0,0x53)
                    elif kind=='size':struct.pack_into('<I',frame,4,BLOCK)
                    elif kind=='orphan':
                        block=2;frame=bytearray(128);struct.pack_into('<I',frame,0,0x53);frame[8:10]=b'\xff\xff'
                    elif kind in ('duplicate','crosslink'):
                        block=2;frame=bytearray(128);struct.pack_into('<IIH',frame,0,0x51,BLOCK if kind=='duplicate' else 3*BLOCK,0xffff if kind=='duplicate' else 3)
                        frame[10:15]=b'GAME\0' if kind=='duplicate' else b'NEXT\0'
                    bad[block*128:(block+1)*128]=seal(frame)
                with self.assertRaises(ValueError):Card(bytes(bad))

    def test_copy_relocates_chain_preserves_other_bytes_and_backs_up(self):
        content=payload(blocks=3)
        original=fixture([('OTHER',(2,),payload('Other',frames=1))])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'a.srm';target=root/'b.mcd'
            source_data=bytearray(fixture([('GAME',(7,3,15),content)]))
            frame=bytearray(source_data[7*128:8*128]);frame[30]=0x58
            source_data[7*128:8*128]=seal(frame)
            source_data=bytes(source_data)
            source.write_bytes(source_data);target.write_bytes(original)
            plan=self.backend.plan_copy(source,{'save':'GAME'},target)
            self.assertEqual(target.read_bytes(),original)
            backup=apply_copy(plan,root/'backups')
            self.assertEqual(backup.read_bytes(),original)
            self.assertEqual(source.read_bytes(),source_data)
            result=Card(target.read_bytes());before=Card(original)
            self.assertEqual(result.entries['GAME'].blocks,(1,3,4))
            self.assertEqual(result.payload(result.entries['GAME']),content)
            self.assertEqual(result.signature('OTHER'),before.signature('OTHER'))
            self.assertEqual(result.signature('GAME'),Card(source_data).signature('GAME'))
            self.assertEqual(target.stat().st_size,SIZE)

    def test_delete_is_recoverable_and_freed_blocks_are_reusable(self):
        original=fixture([('GAME',(1,4,8),payload(blocks=3)),('OTHER',(3,),payload('Other'))])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'a.mcr';target.write_bytes(original)
            plan=self.backend.plan_delete(target,{'save':'GAME'})
            backup=apply_copy(plan,root/'backups')
            after=Card(target.read_bytes())
            self.assertNotIn('GAME',after.entries)
            self.assertEqual([after.states[b] for b in (1,4,8)],[0xa1,0xa2,0xa3])
            self.assertEqual(after.signature('OTHER'),Card(original).signature('OTHER'))
            self.assertEqual(backup.read_bytes(),original)
            for block in (1,4,8):self.assertEqual(target.read_bytes()[block*BLOCK:(block+1)*BLOCK],original[block*BLOCK:(block+1)*BLOCK])
            source=root/'source.mcd';source.write_bytes(fixture([('NEW',(1,2,3),payload('New',3))]))
            apply_copy(self.backend.plan_copy(source,{'save':'NEW'},target),root/'backups')
            self.assertIn('NEW',Card(target.read_bytes()).entries)

    def test_conflicts_capacity_identical_and_stale_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'a.mcr';target=root/'b.mcr'
            source.write_bytes(fixture([('GAME',(1,2,3),payload('New',3))]))
            original=fixture([('GAME',(1,2,3),payload('Old',3)),('OTHER',tuple(range(4,16)),payload('Other',12))])
            target.write_bytes(original)
            with self.assertRaisesRegex(ValueError,'replacement'):self.backend.plan_copy(source,{'save':'GAME'},target)
            apply_copy(self.backend.plan_copy(source,{'save':'GAME'},target,'replace'),root/'backups')
            self.assertEqual(len(Card(target.read_bytes()).free_blocks()),0)
            plan=self.backend.plan_copy(source,{'save':'GAME'},target)
            self.assertEqual(plan.action,'Already identical')
            self.assertIsNone(apply_copy(plan,root/'backups'))
            source.write_bytes(fixture([('GAME',(1,2,3,4),payload('Bigger',4))]))
            before=target.read_bytes()
            with self.assertRaisesRegex(ValueError,'Not enough free space'):self.backend.plan_copy(source,{'save':'GAME'},target,'replace')
            self.assertEqual(target.read_bytes(),before)
            target.write_bytes(blank_card())
            plan=self.backend.plan_copy(source,{'save':'GAME'},target)
            target.write_bytes(fixture([('SURPRISE',(1,),payload())]))
            with self.assertRaisesRegex(ValueError,'changed since'):apply_copy(plan,root/'backups')
            with self.assertRaisesRegex(ValueError,'plugin'):self.backend.plan_slot_copy(source,{'save':'GAME'},1,target,2)

    def test_bad_sector_replacements_are_read_and_written_without_touching_table(self):
        source=bytearray(fixture([('GAME',(1,),payload())]))
        logical=64+5
        source[36*128:37*128]=source[logical*128:(logical+1)*128]
        source[logical*128:(logical+1)*128]=b'X'*128
        frame=bytearray(source[16*128:17*128]);struct.pack_into('<I',frame,0,logical)
        source[16*128:17*128]=seal(frame)
        self.assertEqual(Card(bytes(source)).payload(Card(bytes(source)).entries['GAME']),payload())
        target=bytearray(blank_card())
        frame=bytearray(target[16*128:17*128]);struct.pack_into('<I',frame,0,logical)
        target[16*128:17*128]=seal(frame)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);a=root/'a.mcr';b=root/'b.mcr';a.write_bytes(source);b.write_bytes(target)
            apply_copy(self.backend.plan_copy(a,{'save':'GAME'},b),root/'backups')
            card=Card(b.read_bytes())
            self.assertEqual(card.payload(card.entries['GAME']),payload())
            self.assertEqual(b.read_bytes()[16*128:36*128],target[16*128:36*128])

    def test_icons_have_native_cadence_and_transparent_vs_solid_black(self):
        from PIL import Image
        for frames,duration in ((2,320),(3,220)):
            with self.subTest(frames=frames),Image.open(io.BytesIO(encode_gif(payload(frames=frames)[:128*(frames+1)]))) as image:
                self.assertEqual(image.n_frames,frames)
                self.assertEqual(image.info['duration'],duration)
                rgba=image.convert('RGBA')
                self.assertEqual(rgba.getpixel((0,0)),(0,0,0,255))
                self.assertEqual(rgba.getpixel((4,0))[3],0)
            with Image.open(io.BytesIO(encode_gif(payload(frames=frames)[:128*(frames+1)],False))) as image:
                self.assertEqual(image.n_frames,1)

    def test_slot_plugins_receive_only_payload_bytes_and_preserve_other_data(self):
        class ByteSlots:
            @staticmethod
            def payload_name(save_id):return 'data'
            @staticmethod
            def copy_slot(save_id,source,source_slot,destination,destination_slot,replace=False):
                assert isinstance(source,bytes) and isinstance(destination,bytes)
                result=bytearray(destination)
                start=512+(destination_slot-1)*16
                result[start:start+16]=source[512+(source_slot-1)*16:512+source_slot*16]
                return bytes(result)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'a.mcr';target=root/'b.mcr'
            source.write_bytes(fixture([('GAME',(7,),payload('Source',fill=0x66))]))
            data=bytearray(fixture([('GAME',(4,),payload('Destination',fill=0x77))]))
            frame=bytearray(data[4*128:5*128]);frame[40]=0x5a;data[4*128:5*128]=seal(frame)
            target.write_bytes(data)
            before=Card(data);expected=bytearray(before.payload(before.entries['GAME']))
            expected[528:544]=bytes([0x66])*16
            with patch('game_plugins.plugin_for',return_value=ByteSlots):
                plan=self.backend.plan_slot_copy(source,{'save':'GAME'},1,target,2,'replace')
            self.assertEqual((plan.source_slot,plan.destination_slot),(1,2))
            apply_copy(plan,root/'backups')
            after=Card(target.read_bytes())
            self.assertEqual(after.payload(after.entries['GAME']),bytes(expected))
            self.assertEqual(after.signature('GAME')[:2],before.signature('GAME')[:2])
