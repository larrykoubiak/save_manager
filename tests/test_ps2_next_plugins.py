"""Safe relocation of GTA III files, Burnout careers and Proving Ground careers."""

from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest

from game_plugins import plugin_for
from game_plugins.library import extract_slots
from game_plugins.ps2.gta3 import GTAIIIUS as GTA, DIRECTORY as GTA_DIR, SAVE_SIZE as GTA_SIZE
from game_plugins.ps2.burnout3 import Burnout3US as Burnout, DIRECTORY as BURNOUT_DIR, SLOT_OFFSET, SLOT_SIZE, CHECKSUM_OFFSET, checksum
from game_plugins.ps2.thpg import THPGCareerUS as THPG
from game_plugins.ps2.thps4 import crc, directory_for, fullwidth
from save_core.backends import get_backend
from save_core.backends.base import SaveBundle, SaveFile
from save_core.backends.ps2.bundles import from_native
from save_core.backends.ps2.operations import plan_slot_copy
from save_core.backends.ps2.inspection import inspect_save
from save_core.save_copy import apply_copy
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file
from test_thps4 import payload as thps_payload


def gta_payload(marker=1):
    data = bytearray(GTA_SIZE)
    offset = 0
    sizes = [0x7f00,0x5fe0,0xb84c,0xc350]
    sizes.append(GTA_SIZE-4-5*4-sum(sizes))
    for size in sizes:
        struct.pack_into('<I',data,offset,size);offset += size+4
    struct.pack_into('<I',data,4,201729)
    struct.pack_into('<I',data,44,marker*100000)
    data[1000] = marker
    struct.pack_into('<I',data,len(data)-4,sum(data[:-4]))
    return bytes(data)


def game_save(directory, files):
    result = generic_save(directory,b'')
    template = list(result.get_file(0)[0])
    header = list(result.get_directory());header[2]=len(files);result.set_directory(header)
    for index,(name,data) in enumerate(files.items()):
        entry = list(template);entry[8],entry[2]=name.encode(),len(data)
        result.set_file(index,entry,data)
    return result


def burnout_payload(name='Den', marker=1):
    data = bytearray(61772);struct.pack_into('<I',data,0,16)
    data[100] = marker  # Shared records must stay with the destination.
    for slot in range(3):
        offset = SLOT_OFFSET+slot*SLOT_SIZE
        data[offset+8:offset+26] = 'BURNOUT\0'.encode('utf-16le').ljust(18,b'\0')
    struct.pack_into('<II',data,SLOT_OFFSET,marker,1234)
    data[SLOT_OFFSET+8:SLOT_OFFSET+26]=name.encode('utf-16le').ljust(18,b'\0')
    struct.pack_into('<I',data,CHECKSUM_OFFSET,checksum(data))
    return bytes(data)


class NextPluginTest(unittest.TestCase):
    def test_gta_slot_relocation_preserves_bytes_and_handles_conflicts(self):
        a={GTA_DIR:b'shared A',"1 'MISSION A'...":gta_payload(1)}
        b={GTA_DIR:b'shared B',"1 'MISSION B'...":gta_payload(2)}
        for number in range(1,9):
            if number==1:
                with self.assertRaisesRegex(ValueError,'occupied'):
                    GTA.copy_slot(GTA_DIR,a,1,b,number)
            result=GTA.copy_slot(GTA_DIR,a,1,b,number,True)
            self.assertEqual(result[f"{number} 'MISSION A'..."],a["1 'MISSION A'..."])
            self.assertEqual(result[GTA_DIR],b[GTA_DIR])
            self.assertEqual(GTA.copy_slot(GTA_DIR,a,1,result,number),result)
        bad=bytearray(a["1 'MISSION A'..."]);bad[100]^=1
        self.assertEqual(GTA.slots(GTA_DIR,{**a,"1 'MISSION A'...":bytes(bad)})[0]['state'],'invalid')
        with self.assertRaisesRegex(ValueError,'Multiple'):
            GTA.slots(GTA_DIR,{**a,"1 'DUPLICATE'...":gta_payload(3)})

    def test_gta_raw_ecc_plans_add_and_replace_one_file_preserving_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.ps2';target=root/'target.bin'
            card_file(source,[game_save(GTA_DIR,{GTA_DIR:b'shared source',"1 'MISSION A'...":gta_payload(1)})],True)
            card_file(target,[game_save(GTA_DIR,{GTA_DIR:b'shared target',"1 'MISSION B'...":gta_payload(2)}),generic_save('KEEP',b'other game')],False)
            original=target.read_bytes();before=snapshot(target)
            inspected=inspect_save(source,{'save':GTA_DIR,'title':'GTA III','updated':None},'Test')
            self.assertEqual(inspected.status,'Save slots')
            for number in (1,2,8):
                plan=plan_slot_copy(source,{'save':GTA_DIR},1,target,number,'replace')
                output=root/'candidate.bin';output.write_bytes(plan.candidate)
                after=snapshot(output)
                self.assertEqual(after['/KEEP'],before['/KEEP'])
                self.assertEqual(after['/KEEP/data.bin'],before['/KEEP/data.bin'])
                self.assertEqual(after['/'+GTA_DIR],before['/'+GTA_DIR])
                self.assertEqual(after['/'+GTA_DIR+'/'+GTA_DIR],before['/'+GTA_DIR+'/'+GTA_DIR])
                self.assertEqual(after[f"/{GTA_DIR}/{number} 'MISSION A'..."][1],gta_payload(1))
                if number!=1:self.assertEqual(after[f"/{GTA_DIR}/1 'MISSION B'..."],before[f"/{GTA_DIR}/1 'MISSION B'..."])
                else:self.assertNotIn(f"/{GTA_DIR}/1 'MISSION B'...",after)
            backup=apply_copy(plan,root/'backups');self.assertEqual(backup.read_bytes(),original)
            slots=GTA.library_slots(get_backend('ps2').read_save(target,GTA_DIR))
            self.assertEqual(len(slots),2)
            self.assertEqual(target.stat().st_size,len(original))

    def test_burnout_careers_copy_without_overwriting_shared_records_or_other_careers(self):
        a,b=burnout_payload('Larry',2),burnout_payload('Den',1)
        self.assertIs(plugin_for(BURNOUT_DIR),Burnout)
        self.assertEqual([s['state'] for s in Burnout.slots(BURNOUT_DIR,b)],['occupied','empty','empty'])
        for number in range(1,4):
            result=Burnout.copy_slot(BURNOUT_DIR,a,1,b,number,True)
            start=SLOT_OFFSET+(number-1)*SLOT_SIZE
            self.assertEqual(result[:start],b[:start])
            self.assertEqual(result[start+SLOT_SIZE:CHECKSUM_OFFSET],b[start+SLOT_SIZE:CHECKSUM_OFFSET])
            self.assertEqual(Burnout.slots(BURNOUT_DIR,result)[number-1]['label'],'Career “Larry”')
            self.assertEqual(Burnout.copy_slot(BURNOUT_DIR,a,1,result,number),result)
        with tempfile.TemporaryDirectory() as folder:
            card=Path(folder)/'card.ps2'
            card_file(card,[game_save(BURNOUT_DIR,{BURNOUT_DIR:a})],True)
            self.assertEqual(inspect_save(card,{'save':BURNOUT_DIR,'title':'Burnout','updated':None},'Test').status,'Save slots')
        damaged=bytearray(a);damaged[200]^=1
        with self.assertRaisesRegex(ValueError,'checksum'):Burnout.slots(BURNOUT_DIR,bytes(damaged))
        bundle=SaveBundle(BURNOUT_DIR,(SaveFile(BURNOUT_DIR,a),))
        self.assertEqual(extract_slots('ps2',bundle,'Burnout')[2][0].label,'Career “Larry”')

    def test_proving_ground_named_careers_validate_and_keep_both(self):
        name='Den';directory=directory_for(name,THPG.prefix)
        data=bytearray(thps_payload(name)).ljust(THPG.payload_size,b'\xa5')
        struct.pack_into('<I',data,16,76)
        used=struct.unpack_from('<I',data,12)[0]
        struct.pack_into('<I',data,0,crc(bytes(4)+data[4:used]))
        icon=bytearray(964);icon[:4]=b'PS2D'
        title=fullwidth('THPG:'+name);icon[192:192+len(title)]=title
        bundle=from_native(game_save(directory,{directory:bytes(data),'icon.sys':bytes(icon),'thpg.ico':b'icon'}))
        self.assertIs(plugin_for(directory),THPG)
        self.assertIn('Den',THPG.library_slots(bundle)[0].label)
        renamed,_=THPG.prepare_copy(bundle,{directory})
        self.assertNotEqual(renamed.save_id,directory)
        self.assertIn('Den 2',THPG.summary(renamed.save_id,renamed.file_data()[renamed.save_id])['summary'])
        old_summary=struct.unpack_from('<I',data,8)[0]
        new_data=renamed.file_data()[renamed.save_id];new_summary=struct.unpack_from('<I',new_data,8)[0]
        new_used=struct.unpack_from('<I',new_data,12)[0]
        self.assertEqual(data[20+old_summary:used],new_data[20+new_summary:new_used])
        self.assertEqual(bundle.file_data()[directory],bytes(data))
