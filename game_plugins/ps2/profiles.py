"""Read-only progress for PS2 games that keep one profile per directory.

Offsets follow the US executables: Crazy Taxi SaveBackupData/CheckRanking;
DOA2 0x26f6ec (mode counters), 0x37a448 (XOR); Tenkaichi 0x11d0a8
(play clock), 0x11a0c8 (checksum); SC2 0x1cb1d0 (decode), 0x2266c0
(record serialization). No progress flags or profile data are rewritten.
"""

import re
import struct
from functools import reduce
from operator import xor

from .. import Breakdown, Section
from ..library import LibrarySlot


def clock(seconds):
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}'


class Profile:
    """Expose one whole profile, retaining every file in its library bundle."""

    @classmethod
    def supports(cls, directory):
        return directory == cls.directory

    @classmethod
    def payload_name(cls, directory):
        return cls.filename

    @classmethod
    def progress(cls, directory, data):
        if not cls.supports(directory) or len(data) != cls.payload_size:
            raise ValueError('Unsupported profile layout.')
        return cls.read_progress(data)

    @classmethod
    def validate_save(cls, directory, data):
        cls.progress(directory, data)

    @classmethod
    def summary(cls, directory, data):
        fields, seconds = cls.progress(directory, data)
        result = {'summary': ' · '.join(f'{name}: {value}' for name, value in fields)}
        if seconds is not None:
            result['playtime_seconds'] = seconds
        return result

    @classmethod
    def decode(cls, directory, data):
        try:
            fields, _ = cls.progress(directory, data)
        except ValueError as error:
            return Breakdown(str(error), (), ())
        return Breakdown('Profile progress', (Section('Progress', tuple(fields)),), ())

    @classmethod
    def library_slots(cls, bundle):
        files = bundle.file_data()
        fields, seconds = cls.progress(bundle.save_id, files.get(cls.filename, b''))
        if getattr(cls, 'mirror', None) and files.get(cls.mirror) != files[cls.filename]:
            raise ValueError('Soulcalibur II profile and backup differ; inspect both before choosing.')
        label = ' · '.join(f'{name}: {value}' for name, value in fields)
        return [LibrarySlot('whole', None, label, files, seconds)]


class CrazyTaxiUS(Profile):
    name = 'Crazy Taxi profile (US PS2)'
    directory = filename = 'BASLUS-20202CZYTAXI'
    payload_size = 9745

    @staticmethod
    def read_progress(data):
        if not data.startswith(b'Crazy Taxi Data\0') or data[0x12:0x1c] != b'CRAZY TAXI':
            raise ValueError('Unsupported Crazy Taxi profile header.')
        arcade, original, cleared = -1, -1, 0
        for character in range(4):
            start = 0x70 + character * 0x788
            flags = struct.unpack_from('<I', data, start + 4)[0]
            if flags & ~0xffff:
                raise ValueError('Unsupported Crazy Box completion flags.')
            cleared |= flags
            arcade = max(arcade, *(struct.unpack_from('<i', data, start + 8 + record * 12)[0]
                                    for record in range(40)))
            original = max(original, *(struct.unpack_from('<i', data, start + 0x1e8 + record * 12)[0]
                                        for record in range(30)))
        fields = [('Best Arcade fare', 'Unplayed' if arcade < 0 else f'${arcade / 100:,.2f}'),
                  ('Crazy Box stages', f'{cleared.bit_count()}/16')]
        if original >= 0:
            fields.append(('Best Original fare', f'${original / 100:,.2f}'))
        return fields, None


class DOA2HardcoreUS(Profile):
    name = 'Dead or Alive 2 Hardcore profile (US PS2)'
    directory = filename = 'BASLUS-20071'
    payload_size = 6521

    @staticmethod
    def read_progress(data):
        if data[0x1964:0x1973] != b'DEAD OR ALIVE 2':
            raise ValueError('Unsupported DOA2 profile footer.')
        if reduce(xor, data[:-1], 0) != data[-1]:
            raise ValueError('DOA2 profile checksum mismatch.')
        matches = sum(struct.unpack_from('<I', data, offset)[0]
                      for offset in (0xaf4, 0xafc, 0xb04, 0xb0c))
        return [('Recorded matches', str(matches))], None


class TenkaichiUS(Profile):
    name = 'DBZ Budokai Tenkaichi profile (US PS2)'
    directory = filename = 'BASLUS-21227DBZT'
    payload_size = 16384

    @staticmethod
    def read_progress(data):
        high, low, ticks = struct.unpack_from('<3I', data)
        checksum = sum(struct.unpack_from('<8188H', data, 8)) & 0xffff
        if high > 255 or low > 255 or checksum != (high << 8 | low):
            raise ValueError('Tenkaichi profile checksum mismatch.')
        seconds = ticks // 60
        return [('Time played', clock(seconds))], seconds


class KOFOrochiUS(Profile):
    name = 'The King of Fighters: The Orochi Saga profile (US PS2)'
    directory = filename = 'BASLUS-21554PROFILE'
    payload_size = 2432

    @staticmethod
    def read_progress(data):
        if not data.startswith(b'IE Profile Version: \n33\n'):
            raise ValueError('Unsupported KOF profile version.')
        values = re.findall(rb'Count Achievements:\n(\d+)\n', data)
        played = re.findall(rb"'KING OF FIGHTERS '(?:94|95|96|97|98)' Times Played\n(\d+)\n", data)
        if len(values) != 1 or len(played) != 5:
            raise ValueError('KOF progress fields are missing or ambiguous.')
        return [('Achievements', str(int(values[0]))),
                ('Games played', str(sum(map(int, played))))], None


def rotate16(value, bits):
    return ((value >> bits) | (value << (16 - bits))) & 0xffff


def sc2_unmask(data, seed, rotation):
    result = bytearray()
    for value in data:
        result.append(value ^ (seed & 255))
        seed = (rotate16(seed, rotation) * 5 + 1) & 0xffff
    return bytes(result)


def sc2_decode(data):
    # Retail SC2 stores an encrypted 8182-byte body, u16 seed and BESTGAME.
    trailer = struct.unpack_from('<H', data, 8182)[0]
    if sc2_unmask(data[8184:], trailer, 5) != b'BESTGAME':
        raise ValueError('Soulcalibur II profile closing marker mismatch.')
    seed = rotate16(trailer, 3) ^ 0xeb7d
    body = sc2_unmask(data[:8182], seed, 1)
    if (0xa2f1 + 7 * sum(body)) & 0xffff != seed:
        raise ValueError('Soulcalibur II profile checksum mismatch.')
    if struct.unpack_from('<I', body)[0] != 2:
        raise ValueError('Unsupported Soulcalibur II profile version.')
    return body


class SoulcaliburIIUS(Profile):
    name = 'Soulcalibur II profile (US PS2)'
    directory = filename = 'BASLUS-20643SC2'
    mirror = filename + '_1'
    payload_size = 8192

    @staticmethod
    def read_progress(data):
        body = sc2_decode(data)
        # Serialization table 0x40fcd8: play-record block begins at 0x173a.
        # Its first u32 accumulates elapsed frames divided by the refresh rate.
        seconds = struct.unpack_from('<I', body, 0x173a)[0]
        return [('Time played', clock(seconds))], seconds
