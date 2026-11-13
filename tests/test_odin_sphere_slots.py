"""Odin Sphere's internal slots, independent checksums and byte-only copying."""

from pathlib import Path
import struct
import tempfile
import unittest

from game_plugins import plugin_for
from game_plugins.ps2.odin_sphere import OdinSphereUS as Plugin, DIRECTORY, SLOT_SIZE, SAVE_SIZE, PLAYTIME_OFFSET, DATE_OFFSET, FLAGS_OFFSET
from save_core.backends.base import SaveBundle, SaveFile
from save_core.save_copy import apply_copy, plan_slot_copy
from test_ps2_new_slots import native_save
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file


def record(ticks=None, marker=17, flags=None):
    data=bytearray(SLOT_SIZE)
    data[16:24]=bytes([marker])*8
    if ticks is not None:
        struct.pack_into('<I',data,PLAYTIME_OFFSET,ticks)
        struct.pack_into('<HBB',data,DATE_OFFSET,26,10,21)
    struct.pack_into('<I',data,FLAGS_OFFSET,(1 if ticks is not None else 0) if flags is None else flags)
    struct.pack_into('<I',data,0,sum(data[4:]))
    return bytes(data)


def fixture(ticks=90019, marker=17, options=0x12345678):
    return struct.pack('<II',4,options)+record(ticks,marker)+record(marker=33)*4


class OdinSphereSlotsTest(unittest.TestCase):
    def test_occupancy_ntsc_clock_date_and_library_extraction(self):
        data=fixture()
        self.assertEqual(len(data),SAVE_SIZE)
        self.assertIs(plugin_for(DIRECTORY),Plugin)
        self.assertIsNone(plugin_for('BESLES-54932'))
        slots=Plugin.slots(DIRECTORY,data)
        self.assertEqual([s['state'] for s in slots],['occupied']+['empty']*4)
        self.assertEqual(slots[0]['label'],'0:25:01 played · 2026-10-21')
        self.assertIn('0:06:42',Plugin.slots(DIRECTORY,fixture(24143))[0]['label'])
        bundle=SaveBundle(DIRECTORY,(SaveFile(DIRECTORY,data),SaveFile('icon.sys',b'preserve')))
        extracted=Plugin.library_slots(bundle)
        self.assertEqual(len(extracted),1)
        self.assertEqual(extracted[0].files,{'record':data[8:8+SLOT_SIZE]})
        self.assertEqual((extracted[0].number,extracted[0].playtime_seconds),(1,1501))
        flagged=data[:8]+record(90019,flags=0x101)+data[8+SLOT_SIZE:]
        self.assertEqual(Plugin.slots(DIRECTORY,flagged)[0]['state'],'occupied')

    def test_relocation_preserves_header_other_positions_inputs_and_occupancy_rules(self):
        source=fixture();dest=fixture(24143,marker=99,options=7)
        for position in range(2,6):
            result=Plugin.copy_slot(DIRECTORY,source,1,dest,position)
            start=8+(position-1)*SLOT_SIZE
            self.assertEqual(result[:start],dest[:start])
            self.assertEqual(result[start+SLOT_SIZE:],dest[start+SLOT_SIZE:])
            self.assertEqual(result[start:start+SLOT_SIZE],source[8:8+SLOT_SIZE])
            self.assertEqual(Plugin.slots(DIRECTORY,result)[position-1]['sha256'],Plugin.slots(DIRECTORY,source)[0]['sha256'])
            self.assertEqual(Plugin.copy_slot(DIRECTORY,source,1,result,position),result)
        self.assertEqual(source,fixture())
        self.assertEqual(dest,fixture(24143,marker=99,options=7))
        with self.assertRaisesRegex(ValueError,'occupied'):Plugin.copy_slot(DIRECTORY,source,1,dest,1)
        replaced=Plugin.copy_slot(DIRECTORY,source,1,dest,1,True)
        self.assertEqual(replaced,source[:4]+dest[4:8]+source[8:8+SLOT_SIZE]+dest[8+SLOT_SIZE:])

    def test_corruption_unsupported_layouts_and_bad_copy_arguments_are_rejected(self):
        data=fixture()
        damaged=bytearray(data);damaged[-100]^=1;damaged=bytes(damaged)
        self.assertEqual(Plugin.slots(DIRECTORY,damaged)[4]['state'],'invalid')
        with self.assertRaisesRegex(ValueError,'invalid slots'):Plugin.copy_slot(DIRECTORY,data,1,damaged,2)
        # An unrelated damaged source position does not prevent recovery.
        self.assertEqual(Plugin.copy_slot(DIRECTORY,damaged,1,data,2)[8+SLOT_SIZE:8+2*SLOT_SIZE],data[8:8+SLOT_SIZE])
        for bad in (data[:-1],b'\x03'+data[1:]):
            with self.assertRaises(ValueError):Plugin.slots(DIRECTORY,bad)
        with self.assertRaises(ValueError):Plugin.slots('BISLPM-66474',data)
        for position in (0,6,True,1.0,'1'):
            with self.assertRaises(ValueError):Plugin.copy_slot(DIRECTORY,data,1,data,position)
        with self.assertRaises(ValueError):Plugin.copy_slot(DIRECTORY,data,2,data,3)
        with self.assertRaises(ValueError):Plugin.copy_slot(DIRECTORY,data,1,data,3,replace=1)

    def test_complete_card_transfer_preserves_icons_and_unrelated_game_and_backs_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source.ps2';target=root/'target.bin'
            a=fixture();b=fixture(24143,marker=99,options=7)
            card_file(source,[native_save(DIRECTORY,{DIRECTORY:a,'view.ico':b'source icon'})],True)
            card_file(target,[native_save(DIRECTORY,{DIRECTORY:b,'view.ico':b'destination icon'}),generic_save('OTHER',b'keep')],False)
            before=target.read_bytes();old=snapshot(target)
            backup=apply_copy(plan_slot_copy(source,{'save':DIRECTORY},1,target,5),root/'backups')
            self.assertEqual(backup.read_bytes(),before)
            after=snapshot(target)
            for path in old:
                if path!='/'+DIRECTORY+'/'+DIRECTORY:self.assertEqual(after[path],old[path])
            self.assertIn('0:25:01',Plugin.slots(DIRECTORY,after['/'+DIRECTORY+'/'+DIRECTORY][1])[4]['label'])
