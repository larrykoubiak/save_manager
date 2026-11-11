"""Chrono Cross slot limits, checksums and game-state preservation."""

from pathlib import Path
import struct
import tempfile
import unittest

from game_plugins import plugin_for
from game_plugins.ps1.chrono_cross import ChronoCrossUS as Plugin, PREFIX, CHECKSUM_OFFSET, checksum, slot_label
from save_core.backends import get_backend
from save_core.backends.ps1.card import Card, blank_card
from save_core.save_copy import apply_copy
from test_ftp import vmp
from test_ps1 import fixture, payload


def save_id(slot):
    return PREFIX + f'{slot-1:02}'


def chrono_save(slot=1,marker=19):
    data=bytearray([marker])*8192
    data[:512]=bytes(512);data[:4]=b'SC\x11\x01'
    label=slot_label(slot);data[4:4+len(label)]=label
    struct.pack_into('<I',data,0x37,1801439+2)
    data[0x3f]=10
    struct.pack_into('<I',data,0x588,1801439)
    data[0x5a4]=10
    data[0x1ff8:0x1ffc]=b'\x06\x01\0\0'
    struct.pack_into('<I',data,CHECKSUM_OFFSET,checksum(data))
    return bytes(data)


class ChronoCrossTest(unittest.TestCase):
    def test_registry_numbering_summary_and_fixed_checksum_vector(self):
        self.assertIs(plugin_for(save_id(1),'ps1'),Plugin)
        self.assertIsNone(plugin_for(save_id(1),'ps2'))
        for slot in range(1,16):self.assertEqual(Plugin.slot_number(save_id(slot)),slot)
        self.assertIsNone(plugin_for(PREFIX+'15','ps1'))
        self.assertIsNone(plugin_for('BISLPSP02364CHRONO00','ps1'))
        self.assertEqual(checksum(bytes(range(256))*32),1_042_468)
        info=Plugin.summary(save_id(1),chrono_save())
        self.assertEqual(info['row_title'],'Chrono Cross · Slot 1')
        self.assertEqual(info['summary'],'10 stars · Playtime 8:20:23')
        bad=bytearray(chrono_save());bad[0x600]^=1
        self.assertIn('checksum mismatch',Plugin.summary(save_id(1),bytes(bad))['summary'])

    def test_all_fifteen_destinations_from_raw_and_vmp_preserve_other_bytes(self):
        backend=get_backend('ps1')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'target.mcr'
            original=fixture([(save_id(2),(4,),chrono_save(2,marker=32)),('OTHER',(11,),payload())])
            target.write_bytes(original);before=Card(original)
            source_image=fixture([(save_id(1),(7,),chrono_save())])
            for extension,data in (('mcr',source_image),('VMP',vmp(source_image))):
                source=root/('source.'+extension);source.write_bytes(data)
                for slot in range(1,16):
                    with self.subTest(extension=extension,slot=slot):
                        plan=backend.plan_slot_copy(source,{'save':save_id(1)},1,target,slot,'replace')
                        checked=Card(plan.candidate);copied=checked.payload(checked.entries[save_id(slot)])
                        Plugin.validate_save(save_id(slot),copied)
                        allowed=set(range(30,34))|set(range(CHECKSUM_OFFSET,CHECKSUM_OFFSET+4))
                        changed={i for i,(a,b) in enumerate(zip(chrono_save(),copied)) if a!=b}
                        self.assertTrue(changed<=allowed)
                        self.assertEqual(copied[0x5ac:0x1ffc],chrono_save()[0x5ac:0x1ffc])
                        self.assertEqual(checked.signature('OTHER'),before.signature('OTHER'))
                        if slot!=2:self.assertEqual(checked.signature(save_id(2)),before.signature(save_id(2)))
                        self.assertEqual(plan.destination_save,save_id(slot))
                        self.assertEqual(target.read_bytes(),original)
                self.assertEqual(source.read_bytes(),data)

    def test_conflicts_keep_both_same_card_and_verified_backup(self):
        backend=get_backend('ps1')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.mcr';target=root/'target.mcr'
            source.write_bytes(fixture([(save_id(1),(5,),chrono_save())]))
            original=fixture([(save_id(1),(4,),chrono_save(marker=42))]);target.write_bytes(original)
            with self.assertRaisesRegex(ValueError,'occupied'):
                backend.plan_slot_copy(source,{'save':save_id(1)},1,target,1)
            plan=backend.plan_copy(source,{'save':save_id(1)},target,'keep_both')
            self.assertEqual(plan.destination_save,save_id(2))
            backup=apply_copy(plan,root/'backups');self.assertEqual(backup.read_bytes(),original)
            self.assertEqual(backend.plan_slot_copy(source,{'save':save_id(1)},1,target,2).action,'Already identical')
            apply_copy(backend.plan_slot_copy(source,{'save':save_id(1)},1,source,15),root/'backups')
            self.assertEqual(set(Card(source.read_bytes()).entries),{save_id(1),save_id(15)})
            with self.assertRaisesRegex(ValueError,'different destination'):
                backend.plan_slot_copy(source,{'save':save_id(1)},1,source,1)

    def test_invalid_payloads_slots_and_full_card_never_write(self):
        backend=get_backend('ps1')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.mcr';target=root/'target.mcr'
            source.write_bytes(fixture([(save_id(1),(4,),chrono_save())]));target.write_bytes(blank_card())
            for slot in (0,16,True,'2'):
                with self.subTest(slot=slot),self.assertRaises(ValueError):
                    backend.plan_slot_copy(source,{'save':save_id(1)},1,target,slot)
            with self.assertRaisesRegex(ValueError,'Source slot'):
                backend.plan_slot_copy(source,{'save':save_id(1)},2,target,3)
            target.write_bytes(fixture([('FULL',tuple(range(1,16)),payload(blocks=15))]))
            with self.assertRaisesRegex(ValueError,'Not enough free space'):
                backend.plan_slot_copy(source,{'save':save_id(1)},1,target,3)
            target.write_bytes(blank_card());bad=bytearray(chrono_save());bad[0x600]^=1
            source.write_bytes(fixture([(save_id(1),(4,),bytes(bad))]))
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                backend.plan_copy(source,{'save':save_id(1)},target)
            self.assertEqual(target.read_bytes(),blank_card())


class ChronoCrossWebTest(unittest.TestCase):
    def test_slot_inventory_preview_and_apply(self):
        from test_ps1_web import PS1WebTest
        case=PS1WebTest();case.setUp();self.addCleanup(case.doCleanups)
        case.cards[0].write_bytes(fixture([(save_id(1),(7,),chrono_save())]));case.scan(0)
        save=next(s for s in case.service.inventory()['saves'] if s['directory']==save_id(1))
        self.assertEqual(save['card_slots'],dict(number=1,count=15,family=PREFIX,valid=True))
        status,preview=case.request('/api/transfers/preview',dict(save_id=save['id'],source_slot=1,destination_card_id=case.ids[1],destination_slot=15))
        self.assertEqual(status,201,preview)
        self.assertEqual(preview['destination_save'],save_id(15))
        self.assertEqual(case.request(f"/api/transfers/{preview['id']}/apply",{'cards_idle':True})[0],200)
        case.scan(1)
        copied=next(s for s in case.service.inventory()['saves'] if s['directory']==save_id(15))
        self.assertEqual(copied['card_slots']['number'],15)
        self.assertIn('8:20:23',copied['summary'])
