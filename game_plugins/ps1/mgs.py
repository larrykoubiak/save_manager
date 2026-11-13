"""US MGS story saves: three CRC32-protected snapshots and named copies.

Layout and filename generation checked against FoxdieTeam/mgs_reversing
libgcl/variable.c and menu/datasave.c, plus three private version-0x5f saves.
The reference version 0x60 has the same SAVE_FILE/SAVE_DATA layout.
"""

from dataclasses import replace
import re
import struct
import unicodedata
import zlib


PREFIX = 'BASLUS-00594'
SAVE_SIZE = 8192
SNAPSHOT_SIZE = 0xa40
SNAPSHOT_OFFSETS = (0x100, 0xb40, 0x1580)
DIFFICULTIES = {-1: 'Very easy', 0: 'Easy', 1: 'Normal', 2: 'Hard', 3: 'Extreme'}


def filename_info(save_id):
    suffix = save_id[len(PREFIX):]
    clear = chr(ord(suffix[1]) & 0x3f) == ':'
    level = (-1 if ord(suffix[1]) & 0x40 else
             ((ord(suffix[3]) & 0x40) >> 5) | ((ord(suffix[4]) & 0x40) >> 6))
    hours = None if clear else (ord(suffix[1]) & 0x3f) - ord('0')
    if hours is not None:
        hours = hours * 10 + ord(suffix[2]) - ord('0')
    minutes = ((ord(suffix[3]) & 0x3f) - ord('0')) * 10 + ((ord(suffix[4]) & 0x3f) - ord('0'))
    return dict(clear=clear, level=level, hours=hours, minutes=minutes, disc=(ord(suffix[5]) - ord('@')) // 8 + 1)


class MetalGearSolidUS:
    name = 'Metal Gear Solid (US PS1)'
    backend_id = 'ps1'

    @staticmethod
    def supports(save_id):
        # Hours, minutes, difficulty, clear/disc flags, save type, duplicate A–P.
        # G denotes story data; VR-training and photo files have other layouts.
        return re.fullmatch(re.escape(PREFIX) + r'G[0-9:p-z][0-9@-Z][0-9p-y]{2}[@-O][@-O][A-P]', save_id) is not None

    @staticmethod
    def payload_name(save_id):
        return 'data'

    @classmethod
    def validate_save(cls, save_id, data):
        if not cls.supports(save_id):
            raise ValueError('Unsupported Metal Gear Solid story save ID.')
        if not isinstance(data, bytes) or len(data) != SAVE_SIZE:
            raise ValueError('Expected an 8,192-byte Metal Gear Solid save.')
        if data[:4] != b'SC\x11\x01' or not data[4:68].startswith('ＭＧＳ'.encode('shift_jis')):
            raise ValueError('Unsupported Metal Gear Solid save header.')
        for offset in SNAPSHOT_OFFSETS:
            size, stored, version, variables = struct.unpack_from('<4I', data, offset)
            if size != SNAPSHOT_SIZE or version not in (0x5f, 0x60) or variables != 0x800:
                raise ValueError('Unsupported Metal Gear Solid snapshot layout.')
            if stored != zlib.crc32(data[offset + 8:offset + SNAPSHOT_SIZE]):
                raise ValueError('Metal Gear Solid snapshot checksum mismatch.')
            if data[offset:offset + SNAPSHOT_SIZE] != data[0x100:0xb40]:
                raise ValueError('Metal Gear Solid snapshots disagree.')
        ticks = struct.unpack_from('<I', data, 0x110)[0]
        level, disc = struct.unpack_from('<h', data, 0x142)[0], struct.unpack_from('<h', data, 0x148)[0]
        if ticks > 0x7fffffff or level not in DIFFICULTIES or disc not in (1, 2):
            raise ValueError('Unsupported Metal Gear Solid progress fields.')
        identity = filename_info(save_id)
        if identity['level'] != level or identity['disc'] != disc:
            raise ValueError('Metal Gear Solid filename does not match its difficulty or disc.')
        if not identity['clear']:
            if not save_id[len(PREFIX) + 2].isdigit():
                raise ValueError('Unsupported Metal Gear Solid filename time encoding.')
            hours, remainder = divmod(ticks // 60, 3600)
            minutes = remainder // 60
            if hours > 99:
                hours = minutes = 99
            if (identity['hours'], identity['minutes']) != (hours, minutes):
                raise ValueError('Metal Gear Solid filename does not match its playtime.')

    @classmethod
    def summary(cls, save_id, data):
        result = {'row_title': 'Metal Gear Solid'}
        try:
            cls.validate_save(save_id, data)
        except ValueError as error:
            return {**result, 'summary': 'Progress unavailable: ' + str(error)}
        level = struct.unpack_from('<h', data, 0x142)[0]
        disc = struct.unpack_from('<h', data, 0x148)[0]
        seconds = struct.unpack_from('<I', data, 0x110)[0] // 60
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        title = unicodedata.normalize('NFKC', data[4:68].split(b'\0', 1)[0].decode('shift_jis', errors='replace'))
        match = re.search(r'(?:\d{2}:\d{2}|CLEAR)\s+(.+)', title)
        location = match[1].strip() if match else data[0x120:0x130].split(b'\0', 1)[0].decode('ascii', errors='replace')
        parts = [DIFFICULTIES[level], f'Disc {disc}', location]
        if filename_info(save_id)['clear']:
            parts.append('Cleared game')
        parts.append(f'Playtime {hours}:{minutes:02}:{seconds:02}')
        return {**result, 'summary': ' · '.join(part for part in parts if part)}

    @classmethod
    def library_slots(cls, bundle):
        from ..library import LibrarySlot
        data = bundle.file_data()['data']
        cls.validate_save(bundle.save_id, data)
        return [LibrarySlot('whole', None, cls.summary(bundle.save_id, data)['summary'],
                            {'data': data}, struct.unpack_from('<I', data, 0x110)[0] // 60)]

    @classmethod
    def prepare_copy(cls, source, occupied_directories):
        files = source.file_data()
        if len(source.files) != 1 or set(files) != {'data'}:
            raise ValueError('Unexpected Metal Gear Solid save file set.')
        cls.validate_save(source.save_id, files['data'])
        if source.save_id not in occupied_directories:
            return source, 'Copy'
        # Match the game's collision search: alter only the final character.
        # Time, difficulty, run flags, title and all three snapshots stay intact.
        for letter in 'ABCDEFGHIJKLMNOP':
            name = source.save_id[:-1] + letter
            if name not in occupied_directories:
                cls.validate_save(name, files['data'])
                return replace(source, save_id=name), f'Keep both as {name}'
        raise ValueError('No unused Metal Gear Solid duplicate filename remains.')
