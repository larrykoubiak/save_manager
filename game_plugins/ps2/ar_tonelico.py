"""Ar tonelico US slot reader and byte-only slot copier, derived from SLUS_214.45.

The executable's serialization table at 0x590b28 describes 38 fields totaling
0x6f46 bytes per slot. GAMEFILE holds 12 slots. Checksum: 0x13d684;
timer-to-seconds conversion: 0x13ac7c. See README.md for evidence and scope.
"""

import struct
import hashlib

from .. import Breakdown, Section
from .internal_slots import InternalSlotsPlugin


SLOT_SIZE = 0x6f46
SLOT_COUNT = 12
SAVE_SIZE = SLOT_SIZE * SLOT_COUNT
SENTINEL = 0x7fffffff


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def checksum(record):
    """Game's shifted-byte sum/XOR, with its stored checksum treated as zero."""
    if len(record) != SLOT_SIZE:
        raise ValueError(f'Expected {SLOT_SIZE:,} bytes per Ar tonelico slot.')
    total = mixed = 0
    for offset, byte in enumerate(record):
        shifted = (0 if 1 <= offset < 5 else byte) << (offset % 4)
        total = (total + shifted) & 0xffffffff
        mixed ^= shifted
    return ~(total ^ mixed) & 0xffffffff


def playtime(ticks):
    seconds = ticks // 60  # The game itself divides the stored timer by 0x3c.
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours}:{minutes:02}:{seconds:02}'


class ArTonelicoUS(InternalSlotsPlugin):
    name = 'Ar tonelico: Melody of Elemia (US PS2)'
    payload_size = SAVE_SIZE
    slot_count = SLOT_COUNT
    slot_error_name = 'Ar tonelico'

    @staticmethod
    def _read_slot(save_directory, data, number):
        return {'record': data[(number - 1) * SLOT_SIZE:number * SLOT_SIZE]}

    @staticmethod
    def _slot_playtime(parts):
        return u32(parts['record'], 0x39) // 60

    @staticmethod
    def _write_slot(save_directory, destination, number, parts):
        start = (number - 1) * SLOT_SIZE
        record = bytearray(parts['record'])
        record[0] = number - 1
        struct.pack_into('<I', record, 1, checksum(record))
        return destination[:start] + bytes(record) + destination[start + SLOT_SIZE:]

    @staticmethod
    def supports(save_directory):
        return save_directory == 'BASLUS-21445AT'

    @staticmethod
    def payload_name(save_directory):
        return 'GAMEFILE'

    @classmethod
    def slots(cls, save_directory, data):
        """List all physical positions without exposing game-editing fields."""
        if not cls.supports(save_directory):
            raise ValueError('This Ar tonelico region/save layout is unsupported.')
        if len(data) != SAVE_SIZE:
            raise ValueError(f'Expected {SAVE_SIZE:,} bytes; found {len(data):,}.')
        result = []
        for index in range(SLOT_COUNT):
            record = data[index * SLOT_SIZE:(index + 1) * SLOT_SIZE]
            error = ''
            if record[0] == 0xff:
                state = 'empty'
            else:
                problems = []
                if record[0] != index:
                    problems.append('Stored slot ID does not match its position')
                if u32(record, 5) != SENTINEL or u32(record, SLOT_SIZE - 4) != SENTINEL:
                    problems.append('Record boundary markers do not match')
                if u32(record, 1) != checksum(record):
                    problems.append('Checksum mismatch')
                error = '; '.join(problems)
                state = 'invalid' if error else 'occupied'
            result.append({'number': index + 1, 'state': state,
                           'label': playtime(u32(record, 0x39)) + ' played' if state == 'occupied' else '',
                           # Slot position/checksum change on relocation, game state does not.
                           'sha256': hashlib.sha256(record[5:]).hexdigest() if state == 'occupied' else None,
                           'error': error})
        return result

    @classmethod
    def decode(cls, save_directory, data):
        if not cls.supports(save_directory):
            raise ValueError('This Ar tonelico region/save layout is unsupported.')
        if len(data) != SAVE_SIZE:
            raise ValueError(f'Expected {SAVE_SIZE:,} bytes; found {len(data):,}. This layout is unsupported.')
        empty, occupied, invalid, sections = [], [], [], []
        for index in range(SLOT_COUNT):
            slot = index + 1
            record = data[index * SLOT_SIZE:(index + 1) * SLOT_SIZE]
            if record[0] == 0xff:
                empty.append(slot)
                continue
            occupied.append(slot)
            stored, calculated = u32(record, 1), checksum(record)
            reasons = []
            if record[0] != index:
                reasons.append('Stored slot ID does not match its position')
            if u32(record, 5) != SENTINEL or u32(record, SLOT_SIZE - 4) != SENTINEL:
                reasons.append('Record boundary markers do not match')
            if stored != calculated:
                reasons.append('Checksum mismatch')
            integrity = [('Checksum', f'{stored:08X} / {calculated:08X} (stored / calculated)')]
            if reasons:
                invalid.append(slot)
                sections.append(Section(f'Slot {slot}', (('Status', '; '.join(reasons)), *integrity)))
                continue
            # These offsets come from named PlayTime/Money/Chara fields in the
            # game's serialization table; no variable-length/compressed data.
            level = struct.unpack_from('<H', record, 0x51)[0]
            sections.append(Section(f'Slot {slot}', (
                ('Status', 'Verified'), ('Playtime', playtime(u32(record, 0x39))),
                ('Money', f'{u32(record, 0x41):,}'), ('Lyner level', str(level)),
                *integrity,
            )))
        summary = Section('Slot summary' if invalid else 'Progress', (
            ('Occupied slots', f'{len(occupied)} / {SLOT_COUNT}'),
            ('Slot numbers', ', '.join(map(str, occupied)) or 'None'),
            ('Empty slots', ', '.join(map(str, empty)) or 'None'),
        ))
        integrity = Section('Integrity', (
            ('Plugin', cls.name), ('Payload size', f'{len(data):,} bytes'),
            ('Slot size', f'{SLOT_SIZE:,} bytes'),
            ('Verified occupied slots', str(len(occupied) - len(invalid))),
            ('Invalid slots', ', '.join(map(str, invalid)) or 'None'),
        ))
        status = ('Ar tonelico: slot validation failed' if invalid else
                  f'Ar tonelico decoded · {len(occupied)} occupied slots verified')
        return Breakdown(status, (summary, integrity, *sections), (
            'All 12 internal slots are checked. Each occupied slot has its own section; empty positions are listed above.',
            'Playtime uses the game’s 60-tick-per-second timer and may differ from real time with fast-forward.',
            'US layout. Individual slot copying was confirmed in-game on real PS2 hardware through OPL on November 8, 2026.',
        ))
