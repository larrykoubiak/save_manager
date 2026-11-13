"""Odin Sphere US: five checksum-protected records in one version-4 file.

Derived from SLUS_215.77: validation 0x29ab20, checksum 0x29bc90,
record packing 0x29ac10, and load-menu occupancy/time/date 0x33e0e0.
"""

from datetime import date
import hashlib
import struct

from .internal_slots import InternalSlotsPlugin


DIRECTORY = 'BASLUS-21577'
SLOT_COUNT = 5
SLOT_SIZE = 0x4914
SAVE_SIZE = 8 + SLOT_COUNT * SLOT_SIZE
PLAYTIME_OFFSET = 0x4904
DATE_OFFSET = 0x4908
FLAGS_OFFSET = 0x4910
TICKS_PER_SECOND = struct.unpack('<f', struct.pack('<I', 0x426fc28f))[0]


def _float32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


class OdinSphereUS(InternalSlotsPlugin):
    name = 'Odin Sphere (US PS2)'
    slot_count = SLOT_COUNT
    slot_error_name = 'Odin Sphere'
    payload_size = SAVE_SIZE

    @staticmethod
    def supports(save_directory):
        return save_directory == DIRECTORY

    @staticmethod
    def payload_name(save_directory):
        return save_directory

    @staticmethod
    def _read_slot(save_directory, data, number):
        start = 8 + (number - 1) * SLOT_SIZE
        return {'record': data[start:start + SLOT_SIZE]}

    @staticmethod
    def _slot_playtime(parts):
        ticks = struct.unpack_from('<I', parts['record'], PLAYTIME_OFFSET)[0]
        # The EE menu converts to float, divides by 59.94f, then truncates.
        return int(_float32(_float32(ticks) / TICKS_PER_SECOND))

    @staticmethod
    def _write_slot(save_directory, destination, number, parts):
        start = 8 + (number - 1) * SLOT_SIZE
        # No embedded position: retain the entire verified source record.
        return destination[:start] + parts['record'] + destination[start + SLOT_SIZE:]

    @classmethod
    def slots(cls, save_directory, data):
        if not cls.supports(save_directory):
            raise ValueError('This Odin Sphere game/region layout is unsupported.')
        if not isinstance(data, bytes) or len(data) != SAVE_SIZE:
            raise ValueError(f'Expected a {SAVE_SIZE:,}-byte Odin Sphere save.')
        if struct.unpack_from('<I', data)[0] != 4:
            raise ValueError('Unsupported Odin Sphere save version.')
        result = []
        for number in range(1, SLOT_COUNT + 1):
            parts = cls._read_slot(save_directory, data, number)
            record = parts['record']
            valid = struct.unpack_from('<I', record)[0] == sum(record[4:])
            occupied = bool(struct.unpack_from('<I', record, FLAGS_OFFSET)[0] & 1)
            state = 'invalid' if not valid else 'occupied' if occupied else 'empty'
            label = ''
            if state == 'occupied':
                hours, remainder = divmod(cls._slot_playtime(parts), 3600)
                minutes, seconds = divmod(remainder, 60)
                label = f'{hours}:{minutes:02}:{seconds:02} played'
                year, month, day = struct.unpack_from('<HBB', record, DATE_OFFSET)
                try:
                    label += ' · ' + date(2000 + year, month, day).isoformat()
                except ValueError:
                    # An unset console clock must not hide otherwise valid progress.
                    pass
            result.append(dict(number=number, state=state, label=label,
                               sha256=hashlib.sha256(record).hexdigest() if state == 'occupied' else None,
                               error='' if valid else 'Checksum mismatch'))
        return result
