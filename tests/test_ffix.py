"""FFIX slot numbering, integrity, byte preservation and reviewed transfers."""

from pathlib import Path
import struct
import tempfile
import unittest

from game_plugins import plugin_for
from game_plugins.ps1.ffix import FinalFantasyIXUS as Plugin, PREFIX, CHECKSUM_OFFSET, checksum
from save_core.backends import get_backend
from save_core.backends.base import SaveBundle, SaveFile
from save_core.backends.ps1.card import Card, blank_card
from save_core.save_copy import apply_copy
from test_ps1 import fixture, payload


def ffix_save(slot=1, marker=17):
    data = bytearray([marker])*8192
    data[:512] = bytes(512)
    data[:4] = b'SC\x11\x01'
    title = ('ＦＦ９／ＦＩＬＥ' + f'{slot:02}'.translate(str.maketrans('0123456789','０１２３４５６７８９')) + '／０６：２０').encode('shift_jis')
    data[4:4+len(title)] = title
    data[0x105] = 8
    data[0x106:0x10e] = bytes((0x29,0x38,0x33,0x30,0x3d,0x34,0xff,0xff))
    data[0x110:0x12c] = bytes([0xff])*28
    struct.pack_into('<I',data,0x12c,22812*60)
    struct.pack_into('<H',data,CHECKSUM_OFFSET,checksum(data))
    return bytes(data)


def save_id(slot):
    return PREFIX + f'{slot-1:02}'


class FFIXTest(unittest.TestCase):
    def test_relocation_keeps_saved_menu_viewport_within_all_fifteen_positions(self):
        # A real FILE02 save with row=1 crashed after moving to FILE01:
        # FFIX restored first_visible = 0 - 1 and rendered save index 255.
        data = bytearray(ffix_save(slot=2))
        data[0x17c] = 1
        struct.pack_into('<H',data,CHECKSUM_OFFSET,checksum(data))
        source = SaveBundle(save_id(2),(SaveFile('data',bytes(data),b'file metadata'),),b'card metadata')
        self.assertIs(Plugin.prepare_slot(source,2),source)
        for destination in range(1,16):
            with self.subTest(destination=destination):
                result = Plugin.prepare_slot(source,destination)
                payload = result.file_data()['data']
                row = payload[0x17c]
                self.assertTrue(0 <= row < 3)
                self.assertTrue(0 <= destination - 1 - row <= 12)
                self.assertEqual(result.metadata,source.metadata)
                self.assertEqual(result.files[0].metadata,source.files[0].metadata)
                allowed = {20,21,22,23,0x17c,CHECKSUM_OFFSET,CHECKSUM_OFFSET+1}
                self.assertTrue({i for i,(a,b) in enumerate(zip(data,payload)) if a!=b} <= allowed)
                Plugin.validate_save(result.save_id,payload)
        self.assertEqual(source.file_data()['data'],bytes(data))

    def test_same_position_repairs_previously_renumbered_menu_row(self):
        data = bytearray(ffix_save(slot=1))
        data[0x17c] = 1
        struct.pack_into('<H',data,CHECKSUM_OFFSET,checksum(data))
        bundle = SaveBundle(save_id(1),(SaveFile('data',bytes(data)),))
        repaired = Plugin.prepare_slot(bundle,1)
        self.assertEqual(repaired.files[0].data[0x17c],0)
        self.assertIs(Plugin.prepare_slot(repaired,1),repaired)
        Plugin.validate_save(save_id(1),repaired.files[0].data)

    def test_registry_crc_and_summary(self):
        self.assertIs(plugin_for(save_id(1),'ps1'),Plugin)
        self.assertIsNone(plugin_for(save_id(1),'ps2'))
        self.assertIsNone(plugin_for('BESLES-0296500000-00','ps1'))
        self.assertIsNone(plugin_for(PREFIX+'15','ps1'))
        self.assertEqual(checksum(b'123456789'),0x6f91)
        info=Plugin.summary(save_id(1),ffix_save())
        self.assertEqual(info['row_title'],'Final Fantasy IX · Slot 1')
        self.assertIn('Zidane Lv8',info['summary'])
        self.assertIn('6:20:12',info['summary'])
        bad=bytearray(ffix_save());bad[0x500]^=1
        self.assertIn('checksum mismatch',Plugin.summary(save_id(1),bytes(bad))['summary'])

    def test_copy_to_new_and_occupied_slots_preserves_other_saves(self):
        backend=get_backend('ps1')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'a.mcr';target=root/'b.mcr'
            source.write_bytes(fixture([(save_id(1),(7,),ffix_save())]))
            target.write_bytes(fixture([(save_id(2),(4,),ffix_save(2,marker=42)),('OTHER',(11,),payload())]))
            original=target.read_bytes();before=Card(original)
            for invalid in (0,16,True,'3'):
                with self.subTest(slot=invalid),self.assertRaises(ValueError):
                    backend.plan_slot_copy(source,{'save':save_id(1)},1,target,invalid)
            with self.assertRaisesRegex(ValueError,'Source slot'):
                backend.plan_slot_copy(source,{'save':save_id(1)},2,target,3)
            plan=backend.plan_slot_copy(source,{'save':save_id(1)},1,target,3)
            self.assertEqual(target.read_bytes(),original)
            self.assertEqual((plan.source_save,plan.destination_save),(save_id(1),save_id(3)))
            self.assertEqual((plan.source_slot,plan.destination_slot),(1,3))
            backup=apply_copy(plan,root/'backups')
            self.assertEqual(backup.read_bytes(),original)
            after=Card(target.read_bytes())
            for name in (save_id(2),'OTHER'):
                self.assertEqual(after.signature(name),before.signature(name))
            copied=after.payload(after.entries[save_id(3)])
            Plugin.validate_save(save_id(3),copied)
            allowed={20,21,22,23,CHECKSUM_OFFSET,CHECKSUM_OFFSET+1}
            self.assertTrue({i for i,(a,b) in enumerate(zip(ffix_save(),copied)) if a!=b}<=allowed)
            self.assertIn('FILE03',copied[4:68].decode('shift_jis').replace('Ｆ','F').replace('Ｉ','I').replace('Ｌ','L').replace('Ｅ','E').replace('０','0').replace('３','3'))
            self.assertEqual(backend.plan_slot_copy(source,{'save':save_id(1)},1,target,3).action,'Already identical')
            with self.assertRaisesRegex(ValueError,'occupied'):
                backend.plan_slot_copy(source,{'save':save_id(1)},1,target,2)
            replaced=backend.plan_slot_copy(source,{'save':save_id(1)},1,target,2,'replace')
            apply_copy(replaced,root/'backups')
            self.assertEqual(source.read_bytes(),fixture([(save_id(1),(7,),ffix_save())]))

    def test_same_card_keep_both_full_card_and_invalid_source(self):
        backend=get_backend('ps1')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);a=root/'a.mcr';b=root/'b.mcr'
            a.write_bytes(fixture([(save_id(1),(3,),ffix_save())]))
            plan=backend.plan_slot_copy(a,{'save':save_id(1)},1,a,15)
            apply_copy(plan,root/'backups')
            self.assertEqual(set(Card(a.read_bytes()).entries),{save_id(1),save_id(15)})
            with self.assertRaisesRegex(ValueError,'different destination'):
                backend.plan_slot_copy(a,{'save':save_id(1)},1,a,1)
            b.write_bytes(fixture([(save_id(1),(5,),ffix_save(marker=42))]))
            kept=backend.plan_copy(a,{'save':save_id(1)},b,'keep_both')
            self.assertEqual(kept.destination_save,save_id(2))
            apply_copy(kept,root/'backups')
            b.write_bytes(fixture([('FULL',tuple(range(1,16)),payload(blocks=15))]))
            with self.assertRaisesRegex(ValueError,'Not enough free space'):
                backend.plan_slot_copy(a,{'save':save_id(1)},1,b,3)
            bad=bytearray(ffix_save());bad[600]^=1
            a.write_bytes(fixture([(save_id(1),(3,),bytes(bad))]));b.write_bytes(blank_card())
            original=b.read_bytes()
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                backend.plan_slot_copy(a,{'save':save_id(1)},1,b,3)
            self.assertEqual(b.read_bytes(),original)


class FFIXWebTest(unittest.TestCase):
    def test_preview_apply_and_card_slot_inventory(self):
        from test_ps1_web import PS1WebTest
        case=PS1WebTest();case.setUp();self.addCleanup(case.doCleanups)
        case.cards[0].write_bytes(fixture([(save_id(1),(7,),ffix_save())]))
        case.scan(0)
        inventory=case.service.inventory()
        save=next(s for s in inventory['saves'] if s['directory']==save_id(1))
        self.assertEqual(save['card_slots'],dict(number=1,count=15,family=PREFIX,valid=True))
        status,preview=case.request('/api/transfers/preview',dict(save_id=save['id'],source_slot=1,destination_card_id=case.ids[1],destination_slot=3))
        self.assertEqual(status,201,preview)
        self.assertEqual(preview['destination_save'],save_id(3))
        self.assertEqual(case.request(f"/api/transfers/{preview['id']}/apply",{'cards_idle':True})[0],200)
        case.scan(1)
        self.assertIn(save_id(3),Card(case.cards[1].read_bytes()).entries)
        copied=next(s for s in case.service.inventory()['saves'] if s['directory']==save_id(3))
        self.assertEqual(copied['card_slots']['number'],3)
