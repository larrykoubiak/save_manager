"""Named-career preservation using synthetic THPS4 data and raw/ECC cards."""

from copy import deepcopy
from pathlib import Path
import struct
import tempfile
import unittest

from game_plugins import plugin_for
from save_core.backends.ps2.bundles import from_native, to_native
from game_plugins.ps2.thps4 import THPS4CareerUS as Plugin, SAVE_SIZE, NAME_KEY, career_info, crc, directory_for, fullwidth, progress_info
from save_core.save_copy import apply_copy, plan_copy
from save_web.inventory import snapshot_location
from test_save_copy import generic_save, snapshot
from test_save_merge import card_file
from save_core.backends.ps2.native.native_save import PS2SaveFile


def payload(name='Larry', body=b'progress to preserve'):
    summary = struct.pack('<I', 0x461a4611) + b'\xf9\x01\x02\x10' + struct.pack('<I', 0x46f71d38) + b'\x03' + NAME_KEY + name.encode() + b'\0\0'
    data = bytearray(b'\xa5' * SAVE_SIZE)
    used = 20 + len(summary) + len(body)
    struct.pack_into('<5I', data, 0, 0, crc(summary), len(summary), used, 24)
    data[20:used] = summary + body
    struct.pack_into('<I', data, 0, crc(data[:used]))
    return bytes(data)


def career(name='Larry', body=b'progress to preserve'):
    directory = directory_for(name)
    template_save = generic_save(directory, b'')
    entry = list(template_save.get_file(0)[0])
    header = list(template_save.get_directory())
    header[2] = 3
    icon = bytearray(964)
    icon[:4] = b'PS2D'
    struct.pack_into('<H', icon, 6, 26)
    title = fullwidth('THPS4 Career:' + name)
    icon[192:192 + len(title)] = title
    for offset in (260, 324, 388):
        icon[offset:offset + 9] = b'thps4.ico'
    result = PS2SaveFile()
    result.set_directory(header)
    for i, (filename, data) in enumerate(((directory, payload(name, body)), ('icon.sys', bytes(icon)), ('thps4.ico', b'preserved icon'))):
        item = list(entry)
        item[8], item[2] = filename.encode(), len(data)
        result.set_file(i, item, data)
    return result


def files(save):
    return {entry[8].decode(): data for entry, data in save}


class THPS4Test(unittest.TestCase):
    def test_game_filename_examples_and_checksum_validation(self):
        self.assertEqual(directory_for('Larry'), 'BASLUS-20504sakozfha')
        self.assertEqual(directory_for('Den'), 'BASLUS-20504qyftmuna')
        self.assertIs(plugin_for(directory_for('Larry')), Plugin)
        self.assertFalse(Plugin.supports('BASLUS-20504sakozfhb'))
        self.assertEqual(career_info(directory_for('Larry'), payload())[0], 'Larry')
        progress = dict(Plugin.decode(directory_for('Larry'), payload()).sections[0].fields)
        self.assertEqual(progress['Goals completed'], '3')
        self.assertEqual(progress['Cash'], '$513')
        self.assertIn('3 goals completed · $513 cash', Plugin.summary(directory_for('Larry'), payload())['summary'])
        corrupted = bytearray(payload())
        corrupted[50] ^= 1
        self.assertNotIn('Progress', [s.title for s in Plugin.decode(directory_for('Larry'), corrupted).sections])
        with self.assertRaises(ValueError):
            career_info(directory_for('Den'), payload())

    def test_progress_integer_encodings_and_unsupported_fields_are_not_guessed(self):
        for kind, format, value in ((1, '<i', 100000), (14, '<b', 50),
                                    (15, '<h', 2000), (16, '<B', 200), (17, '<H', 3600)):
            summary = bytes([kind]) + struct.pack('<I', crc(b'cash')) + struct.pack(format, value)
            summary += bytes([18]) + struct.pack('<I', crc(b'numgoalsbeaten'))
            summary += NAME_KEY + b'Larry\0\0'
            self.assertEqual(progress_info(summary), {'cash': value, 'goals': 0})
            self.assertEqual(progress_info(summary[:3]), {})
        self.assertEqual(progress_info(bytes([99]) + bytes(8)), {})
        self.assertEqual(progress_info(bytes([1]) + struct.pack('<Ii', crc(b'cash'), -1)), {})

    def test_keep_both_renames_only_identity_and_checksums_not_game_state(self):
        source = career()
        before = deepcopy(source)
        result, label = Plugin.prepare_copy(from_native(source), {directory_for('Larry')})
        result = to_native(result)
        self.assertEqual(label, 'Copy as career “Larry 2”')
        new_directory = directory_for('Larry 2')
        self.assertEqual(result.get_directory()[8], new_directory.encode())
        output = files(result)
        old = files(source)[directory_for('Larry')]
        name, summary, _, _, used = career_info(new_directory, output[new_directory])
        self.assertEqual(name, 'Larry 2')
        self.assertEqual(output[new_directory][20 + len(summary):used], b'progress to preserve')
        self.assertEqual(output[new_directory][used:], old[used:])
        self.assertEqual(output['thps4.ico'], files(source)['thps4.ico'])
        self.assertEqual(output['icon.sys'][:192], files(source)['icon.sys'][:192])
        self.assertEqual(output['icon.sys'][260:], files(source)['icon.sys'][260:])
        self.assertIn(fullwidth('Larry 2'), output['icon.sys'][192:260])
        self.assertEqual(source.get_directory(), before.get_directory())
        self.assertEqual(list(source), list(before))
        third, _ = Plugin.prepare_copy(from_native(source), {directory_for('Larry'), new_directory})
        third = to_native(third)
        self.assertEqual(third.get_directory()[8], directory_for('Larry 3').encode())
        with self.assertRaises(ValueError):
            Plugin.prepare_copy(from_native(source), {directory_for('Larry' + f' {n}') for n in range(2, 100)})

    def test_raw_ecc_backup_preserves_existing_careers_and_other_saves(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source.ps2', root / 'target.bin'
            card_file(source, [career(body=b'older distinct progress')], True)
            card_file(target, [career(body=b'newer distinct progress'), career('Den'), generic_save('OTHER-GAME', b'untouched')], False)
            source_bytes, target_bytes = source.read_bytes(), target.read_bytes()
            before = snapshot(target)
            plan = plan_copy(source, {'save': directory_for('Larry')}, target, 'keep_both')
            self.assertEqual(plan.action, 'Copy as career “Larry 2”')
            self.assertEqual(target.read_bytes(), target_bytes)
            backup = apply_copy(plan, root / 'backups')
            self.assertEqual(backup.read_bytes(), target_bytes)
            self.assertEqual(source.read_bytes(), source_bytes)
            after = snapshot(target)
            for path in before:
                # Parent root metadata is not part of the logical save snapshot.
                self.assertEqual(after[path], before[path])
            new_directory = directory_for('Larry 2')
            data = after['/' + new_directory + '/' + new_directory][1]
            _, summary, _, _, used = career_info(new_directory, data)
            self.assertEqual(data[20 + len(summary):used], b'older distinct progress')
            records = snapshot_location(dict(path=str(root), suffix='.bin', marker=''))[0]['saves']
            self.assertEqual(len([s for s in records if s['save'].startswith('BASLUS-20504')]), 3)
            self.assertIn('Career “Larry 2”', next(s['summary'] for s in records if s['save'] == new_directory))
