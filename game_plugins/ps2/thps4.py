"""THPS4 US named careers; keep both by renaming the incoming save.

SLUS_205.04: lower-case CRC at 0x105aa0, base-26 filename at 0x1e15e0.
The summary and active file span each carry a CRC32 with no final XOR.
"""

import re
import struct
import unicodedata
import zlib

from .. import Breakdown, Section
from dataclasses import replace
from save_core.backends.base import SaveBundle


PREFIX = 'BASLUS-20504'
SAVE_SIZE = 40960
NAME_KEY = b'\x03\x9a\x16\xf4\xc3'  # string field: CRC("filename")


def crc(data):
    return zlib.crc32(data) ^ 0xffffffff


def directory_for(name):
    value = crc(name.lower().encode('ascii'))
    suffix = ''
    for _ in range(7):
        value, remainder = divmod(value, 26)
        suffix += chr(97 + remainder)
    return PREFIX + suffix + 'a'  # final letter selects career file type


def career_info(directory, data):
    if not THPS4CareerUS.supports(directory) or len(data) != SAVE_SIZE:
        raise ValueError('Unsupported THPS4 US career layout.')
    stored, summary_crc, summary_size, used, version = struct.unpack_from('<5I', data)
    if version != 24 or not 20 < 20 + summary_size < used <= len(data):
        raise ValueError('Unsupported THPS4 career header.')
    summary = data[20:20 + summary_size]
    if crc(summary) != summary_crc or crc(bytes(4) + data[4:used]) != stored:
        raise ValueError('THPS4 career checksum mismatch.')
    if summary.count(NAME_KEY) != 1:
        raise ValueError('THPS4 career name field is missing or ambiguous.')
    start = summary.index(NAME_KEY) + len(NAME_KEY)
    end = summary.find(b'\0', start)
    if end < 0 or summary[end:] != b'\0\0':
        raise ValueError('Unsupported THPS4 career name encoding.')
    try:
        name = summary[start:end].decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('Unsupported THPS4 career name encoding.') from error
    if not re.fullmatch(r'[A-Za-z0-9 _-]{1,24}', name) or directory_for(name) != directory:
        raise ValueError('THPS4 career name does not match its directory.')
    return name, summary, start, end, used


def progress_info(summary):
    """Read the game's quick-load cash/goals fields, including compact integers."""
    fields, offset = {}, 0
    integer_formats = {1: '<i', 14: '<b', 15: '<h', 16: '<B', 17: '<H'}
    while offset < len(summary):
        if summary[offset:offset + len(NAME_KEY)] == NAME_KEY:
            break
        if offset + 5 > len(summary):
            return {}
        kind = summary[offset]
        key = struct.unpack_from('<I', summary, offset + 1)[0]
        offset += 5
        if kind == 18:
            value = 0
        elif kind in integer_formats:
            format = integer_formats[kind]
            size = struct.calcsize(format)
            if offset + size > len(summary):
                return {}
            value = struct.unpack_from(format, summary, offset)[0]
            offset += size
        else:
            return {}
        if key in fields:
            return {}
        fields[key] = value
    cash, goals = fields.get(crc(b'cash')), fields.get(crc(b'numgoalsbeaten'))
    if cash is None or goals is None or cash < 0 or goals < 0:
        return {}
    return {'cash': cash, 'goals': goals}


def fullwidth(text):
    return ''.join('\u3000' if ch == ' ' else chr(ord(ch) + 0xfee0) for ch in text).encode('shift_jis')


class THPS4CareerUS:
    name = "Tony Hawk's Pro Skater 4 career (US PS2)"
    payload_size = SAVE_SIZE

    @staticmethod
    def supports(directory):
        return re.fullmatch(PREFIX + r'[a-z]{7}a', directory) is not None

    @staticmethod
    def payload_name(directory):
        return directory

    @classmethod
    def decode(cls, directory, data):
        try:
            name, summary, *_ = career_info(directory, data)
        except ValueError as error:
            return Breakdown(str(error), (), ())
        progress = progress_info(summary)
        fields = [('Career', name)]
        if progress:
            fields += [('Goals completed', str(progress['goals'])), ('Cash', f"${progress['cash']:,}")]
        return Breakdown('THPS4 career checksums verified', (Section('Progress', tuple(fields)),), ())

    @classmethod
    def summary(cls, directory, data):
        name, summary, *_ = career_info(directory, data)
        progress = progress_info(summary)
        label = f'Career “{name}”'
        if progress:
            label += f" · {progress['goals']} goals completed · ${progress['cash']:,} cash"
        else:
            label += ' · checksums verified; progress unavailable'
        return {'summary': label}

    @classmethod
    def prepare_copy(cls, source: SaveBundle, occupied_directories):
        """Return a separately named career and its transfer action label."""
        directory = source.save_id
        files = source.file_data()
        if len(source.files) != 3 or set(files) != {directory, 'icon.sys', 'thps4.ico'}:
            raise ValueError('Unexpected THPS4 career file set.')
        data = files[directory]
        old_name, summary, start, end, used = career_info(directory, data)
        for number in range(2, 100):
            suffix = f' {number}'
            name = old_name[:8 - len(suffix)].rstrip() + suffix
            new_directory = directory_for(name)
            if new_directory not in occupied_directories:
                break
        else:
            raise ValueError('No unused THPS4 career name remains.')
        new_summary = summary[:start] + name.encode('ascii') + summary[end:]
        body = data[20 + len(summary):used]
        new_used = 20 + len(new_summary) + len(body)
        if new_used > SAVE_SIZE:
            raise ValueError('Renamed THPS4 career exceeds its file size.')
        payload = bytearray(data)
        payload[20:new_used] = new_summary + body
        struct.pack_into('<I', payload, 0, 0)
        struct.pack_into('<III', payload, 4, crc(new_summary), len(new_summary), new_used)
        struct.pack_into('<I', payload, 0, crc(payload[:new_used]))
        checked_name, checked_summary, _, _, checked_used = career_info(new_directory, payload)
        if checked_name != name or payload[20 + len(checked_summary):checked_used] != body:
            raise ValueError('Renamed THPS4 career verification failed.')
        icon = bytearray(files['icon.sys'])
        if len(icon) != 964 or icon[:4] != b'PS2D':
            raise ValueError('Unsupported THPS4 icon metadata.')
        title = icon[192:260].split(b'\0', 1)[0].decode('shift_jis')
        if unicodedata.normalize('NFKC', title) != 'THPS4 Career:' + old_name:
            raise ValueError('THPS4 icon title does not match the career name.')
        new_title = fullwidth('THPS4 Career:' + name)
        if len(new_title) >= 68:
            raise ValueError('Renamed THPS4 icon title is too long.')
        icon[192:260] = new_title.ljust(68, b'\0')
        result = replace(source, save_id=new_directory, files=tuple(
            replace(file, name=new_directory if file.name == directory else file.name,
                    data=bytes(payload) if file.name == directory else bytes(icon) if file.name == 'icon.sys' else file.data)
            for file in source.files))
        return result, f'Copy as career “{name}”'
