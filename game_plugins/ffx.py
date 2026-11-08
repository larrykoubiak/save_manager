"""Read-only decoder for the PS2 FFX International saves tested here.

Field offsets and checksum behavior cross-checked against FFXED and the
FFX checksum utility. See game_plugins/README.md for references and scope.
"""

import re
import struct

from . import Breakdown, Section


SAVE_SIZE = 25848
CHARACTERS = ('Tidus', 'Yuna', 'Auron', 'Kimahri', 'Wakka', 'Lulu', 'Rikku')


def _checksum_table():
    table = []
    for byte in range(256):
        value = byte << 8
        for _ in range(8):
            value = ((value << 1) ^ (0x1021 if value & 0x8000 else 0)) & 0xffff
        table.append(value)
    # The game/reference table's final entry is zero, NOT the standard
    # CCITT entry 0x1ef0. A generic CRC-CCITT function gives wrong results.
    table[255] = 0
    return tuple(table)


CHECKSUM_TABLE = _checksum_table()


def checksum(data):
    if len(data) != SAVE_SIZE:
        raise ValueError(f'Expected {SAVE_SIZE:,} bytes; found {len(data):,}. This layout is unsupported.')
    value = 0xffff
    for offset in range(0x40, SAVE_SIZE):
        byte = 0 if offset in (0x64f4, 0x64f5) else data[offset]
        value = ((value << 8) ^ CHECKSUM_TABLE[((value >> 8) ^ byte) & 255]) & 0xffff
    return value ^ 0xffff


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]


class FFXInternational:
    name = 'Final Fantasy X International (PS2)'
    payload_size = SAVE_SIZE
    merge_game_ids = ('SLPS-25088',)

    @staticmethod
    def prepare_slot(source, slot):
        from .ffx_slots import prepare_slot
        return prepare_slot(source, slot)

    @staticmethod
    def supports(save_directory):
        return re.fullmatch(r'BISLPS-25088FF0906[0-9]{2}', save_directory) is not None

    @staticmethod
    def payload_name(save_directory):
        return save_directory

    @staticmethod
    def slot_number(save_directory):
        return int(save_directory[-2:]) + 1

    @classmethod
    def decode(cls, save_directory, data):
        if not cls.supports(save_directory):
            raise ValueError('This FFX region/save layout is not supported by this plugin.')
        computed = checksum(data)
        first, second = u16(data, 0x1a), u16(data, 0x64f4)
        valid = first == second == computed
        integrity = Section('Integrity', (
            ('Plugin', cls.name),
            ('Payload size', f'{len(data):,} bytes'),
            ('Checksum', 'Verified — both copies match' if valid else 'Mismatch — payload not decoded'),
            ('Stored / calculated', f'{first:04X}, {second:04X} / {computed:04X}'),
        ))
        if not valid:
            return Breakdown('Checksum mismatch', (integrity,),
                             ('The plugin could not verify this payload. Progress fields were not decoded.',))
        seconds = u32(data, 252)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds_remainder = divmod(remainder, 60)
        overview = Section('Progress', (
            ('Playtime', f'{hours}:{minutes:02}:{seconds_remainder:02}'),
            ('Gil', f'{u32(data, 15752):,}'),
            ('Battles fought', f'{u32(data, 15700):,}'),
            ('Story progress marker', str(u16(data, 3116))),
            ('Room ID', str(u16(data, 250))),
            ('Spawn point ID', str(data[248])),
            ('Save slot', str(cls.slot_number(save_directory))),
        ))
        characters = []
        for index, name in enumerate(CHARACTERS):
            base = 22032 + index * 148
            fields = [
                ('Current HP', f'{u32(data, base + 24):,}'),
                ('Current MP', f'{u32(data, base + 28):,}'),
                ('Base HP', f'{u32(data, base):,}'),
                ('Base MP', f'{u32(data, base + 4):,}'),
                ('Ability points', f'{u32(data, base + 20):,}'),
                ('Sphere level', str(data[base + 55])),
            ]
            stats = ('Strength', 'Defense', 'Magic', 'Magic defense', 'Agility', 'Luck', 'Evasion', 'Accuracy')
            fields += [('Base ' + stat.lower(), str(data[base + 8 + i])) for i, stat in enumerate(stats)]
            characters.append(Section(name, tuple(fields)))
        return Breakdown('FFX decoded · checksum verified', (overview, integrity, *characters), (
            'Story and room markers are internal IDs, not completion percentages.',
            'Character data includes stored values for characters not yet recruited. Base stats exclude bonuses.',
        ))
