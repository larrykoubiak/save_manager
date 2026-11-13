"""New PS2 adapters: byte preservation, integrity checks and offline backfill."""

from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from game_plugins import plugin_for
from game_plugins.ps2.monster_hunter import MonsterHunterUS as Hunter, DIRECTORY as MH_DIRECTORY, SAVE_SIZE, SLOT_OFFSET, SLOT_SIZE, BODY_END, decode
from game_plugins.ps2.we_love_katamari import WeLoveKatamariUS as Katamari, DIRECTORY as K_DIRECTORY, SLOT_SIZE as K_SIZE
from save_core.backends.base import SaveBundle, SaveFile
from save_core.save_copy import apply_copy, plan_slot_copy
from save_web.service import Service
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file


def hunter_save(name='Hunter', seconds=1234, seed=123, patch_byte=17):
    plain = bytearray(SAVE_SIZE)
    struct.pack_into('<4H', plain, 0, 0x100, seed, 0, 0x5963)
    plain[16:32] = bytes([patch_byte]) * 16
    plain[SLOT_OFFSET] = 1
    encoded = name.encode('ascii')
    plain[SLOT_OFFSET + 8:SLOT_OFFSET + 8 + len(encoded)] = encoded
    struct.pack_into('<I', plain, SLOT_OFFSET + 0x5dc, seconds)
    plain[-8:] = b'preserve'
    return encode_hunter(plain)


def encode_hunter(plain):
    result = bytearray(plain)
    words = struct.unpack_from('<' + 'H' * ((BODY_END - 8)//2), plain, 8)
    struct.pack_into('<H', result, 4, sum(words) & 0xffff)
    key = struct.unpack_from('<H', result, 2)[0]
    for number, word in enumerate(words):
        struct.pack_into('<H', result, 8 + number*2, word ^ key)
        key = ((key or 1) * 176) % 65363
    return bytes(result)


def katamari_save():
    def wrap(data):
        return struct.pack('<II', zlib.crc32(data), 1) + data
    record = bytearray(K_SIZE - 8)
    struct.pack_into('<I', record, 4, 3)
    return {K_DIRECTORY:wrap(bytes(1000)), 'katamari_save01':wrap(record),
            'katamari_save02':wrap(bytes(K_SIZE - 8)), 'katamari_save03':wrap(bytes(K_SIZE - 8))}


def native_save(directory, files):
    save = generic_save(directory, b'')
    entry = list(save.get_file(0)[0])
    record = list(save.get_directory()); record[2] = len(files); save.set_directory(record)
    for number, (name, data) in enumerate(files.items()):
        item = entry.copy(); item[8] = name.encode(); item[2] = len(data)
        save.set_file(number, item, data)
    return save


class NewPS2SlotsTest(unittest.TestCase):
    def test_hunter_copy_preserves_destination_patch_settings_seed_padding_and_other_characters(self):
        source = hunter_save('Alice', 4935, seed=0, patch_byte=11)
        dest = hunter_save('Bob', 1517, seed=999, patch_byte=22)
        slots = Hunter.slots(MH_DIRECTORY, source)
        self.assertEqual([s['state'] for s in slots], ['occupied','empty','empty'])
        self.assertIn('Alice · 1:22:15', slots[0]['label'])
        result = Hunter.copy_slot(MH_DIRECTORY, source, 1, dest, 3)
        a, b = decode(dest), decode(result)
        start = SLOT_OFFSET + 2*SLOT_SIZE
        self.assertEqual(a[:4], b[:4])
        self.assertEqual(a[6:start], b[6:start])
        self.assertEqual(a[start + SLOT_SIZE:], b[start + SLOT_SIZE:])
        self.assertEqual(b[start:start + SLOT_SIZE], decode(source)[SLOT_OFFSET:SLOT_OFFSET + SLOT_SIZE])
        self.assertEqual(Hunter.copy_slot(MH_DIRECTORY, source, 1, result, 3), result)
        with self.assertRaisesRegex(ValueError, 'occupied'):
            Hunter.copy_slot(MH_DIRECTORY, source, 1, dest, 1)
        replaced = Hunter.copy_slot(MH_DIRECTORY, source, 1, dest, 1, True)
        self.assertIn('Alice', Hunter.slots(MH_DIRECTORY, replaced)[0]['label'])
        self.assertEqual(source, hunter_save('Alice', 4935, seed=0, patch_byte=11))
        self.assertEqual(dest, hunter_save('Bob', 1517, seed=999, patch_byte=22))

    def test_hunter_rejects_corruption_wrong_region_invalid_status_and_bad_copy_requests(self):
        data = hunter_save()
        self.assertIs(plugin_for(MH_DIRECTORY), Hunter)
        self.assertIsNone(plugin_for('BESLES-52707MH'))
        for damaged in (data[:-1], b'XX'+data[2:], data[:100]+bytes([data[100]^1])+data[101:]):
            with self.assertRaises(ValueError):
                Hunter.copy_slot(MH_DIRECTORY, damaged, 1, data, 2)
            with self.assertRaises(ValueError):
                Hunter.copy_slot(MH_DIRECTORY, data, 1, damaged, 2)
        plain = bytearray(decode(data)); plain[SLOT_OFFSET+SLOT_SIZE] = 2
        damaged = encode_hunter(plain)
        with self.assertRaisesRegex(ValueError, 'invalid slots'):
            Hunter.copy_slot(MH_DIRECTORY, data, 1, damaged, 3)
        for number in (0,4,True,1.0):
            with self.assertRaises(ValueError):Hunter.copy_slot(MH_DIRECTORY, data, 1, data, number)
        with self.assertRaises(ValueError):Hunter.copy_slot(MH_DIRECTORY, data, 2, data, 3)
        with self.assertRaises(ValueError):Hunter.copy_slot(MH_DIRECTORY, data, 1, data, 3, replace=1)
        with self.assertRaises(ValueError):Hunter.slots('BISLPM-65495MH',data)

    def test_we_love_katamari_preserves_options_other_slots_and_library_identity(self):
        data = katamari_save()
        self.assertIs(plugin_for(K_DIRECTORY), Katamari)
        dest = {**data, K_DIRECTORY:struct.pack('<II',zlib.crc32(b'X'*1000),1)+b'X'*1000}
        result = Katamari.copy_slot(K_DIRECTORY,data,1,dest,3)
        self.assertEqual(result,{**dest,'katamari_save03':data['katamari_save01']})
        self.assertEqual(Katamari.copy_slot(K_DIRECTORY,data,1,result,3),result)
        bundle = SaveBundle(K_DIRECTORY,tuple(SaveFile(name,value) for name,value in data.items()))
        slots = Katamari.library_slots(bundle)
        self.assertEqual([(s.key,s.number,s.files) for s in slots],[('1',1,{'data':data['katamari_save01']})])
        broken = {**data,'katamari_save02':bytes(K_SIZE)}
        with self.assertRaises(ValueError):Katamari.copy_slot(K_DIRECTORY,data,1,broken,3)
        from game_plugins.ps2.katamari import KatamariDamacyUS
        with self.assertRaises(ValueError):KatamariDamacyUS.slots(K_DIRECTORY,data)

    def test_card_copy_uses_backup_and_preserves_unrelated_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source.ps2'; target=root/'target.bin'
            a=hunter_save('Alice');b=hunter_save('Bob',seed=999)
            card_file(source,[native_save(MH_DIRECTORY,{MH_DIRECTORY:a})],True)
            card_file(target,[native_save(MH_DIRECTORY,{MH_DIRECTORY:b}),generic_save('OTHER',b'keep')],False)
            before=target.read_bytes();old=snapshot(target)
            plan=plan_slot_copy(source,{'save':MH_DIRECTORY},1,target,2)
            backup=apply_copy(plan,root/'backups')
            self.assertEqual(backup.read_bytes(),before)
            after=snapshot(target)
            self.assertEqual(after['/OTHER/data.bin'],old['/OTHER/data.bin'])
            self.assertIn('Alice',Hunter.slots(MH_DIRECTORY,after['/'+MH_DIRECTORY+'/'+MH_DIRECTORY][1])[1]['label'])

    def test_offline_whole_save_history_is_expanded_once_without_rewriting_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cards=root/'cards';cards.mkdir()
            card=cards/'hunter.ps2'
            card_file(card,[native_save(MH_DIRECTORY,{MH_DIRECTORY:hunter_save('Alice')})],True)
            service=Service(root/'data');self.addCleanup(service.close)
            device=service.create_device(dict(name='Old cards',root_path=str(cards)))
            location=service.save_location(device['id'],dict(name='PS2',path='.',suffix='.ps2',backend='ps2'))
            with patch('game_plugins.plugins_for',return_value=()):
                job=service.queue_scan(device['id']);service.run_scan(job['id'])
            originals=service.db.rows('SELECT * FROM library_versions ORDER BY id')
            heads=service.db.rows('SELECT id,current_version_id FROM library_slots ORDER BY id')
            service.delete_location(device['id'],location['id'])
            card.unlink()
            service.library.index_plugins()
            game=next(g for g in service.library.inventory()['games'] if g['game_key']=='SLUS-20896')
            self.assertEqual(len(game['saves']),1)
            self.assertIn('Alice',game['saves'][0]['label'])
            self.assertEqual(game['saves'][0]['playtime_seconds'],1234)
            for row in originals:self.assertEqual(service.db.one('SELECT * FROM library_versions WHERE id=?',(row['id'],)),row)
            for row in heads:self.assertEqual(service.db.one('SELECT id,current_version_id FROM library_slots WHERE id=?',(row['id'],)),row)
            versions=service.db.rows('SELECT * FROM library_versions ORDER BY id')
            service.library.index_plugins()
            self.assertEqual(service.db.rows('SELECT * FROM library_versions ORDER BY id'),versions)
            for row in originals:self.assertTrue(service.library.download(row['id']))
            self.assertTrue(service.library.download(game['saves'][0]['id']))
            # A newer device save can supersede the derived copy. Pruning that
            # history must not cause it to reappear at the next startup.
            derived_id=game['saves'][0]['id']
            card_file(card,[native_save(MH_DIRECTORY,{MH_DIRECTORY:hunter_save('Alice',9999)})],True)
            service.save_location(device['id'],dict(name='Current',path='.',suffix='.ps2',backend='ps2'))
            job=service.queue_scan(device['id']);service.run_scan(job['id'])
            service.library.settings({'previous_versions':0})
            with self.assertRaises(LookupError):service.db.one('SELECT id FROM library_versions WHERE id=?',(derived_id,))
            versions=service.db.rows('SELECT * FROM library_versions ORDER BY id')
            service.library.index_plugins()
            self.assertEqual(service.db.rows('SELECT * FROM library_versions ORDER BY id'),versions)
