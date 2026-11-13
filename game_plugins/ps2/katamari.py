"""Katamari Damacy US slots, derived from SLUS_210.08 save/load routines.

Each slot is a 7,064-byte game record with an 8-byte Namco CRC/valid header.
The slot menu checks record+4 for occupancy. Options are a separate file.
"""

import hashlib
import struct
import zlib

from .internal_slots import InternalSlotsPlugin


DIRECTORY = 'BASLUS-21008Opt'
SLOT_COUNT = 3
SLOT_SIZE = 7072
OPTIONS_SIZE = 1008


def validation_error(data, size):
    if not isinstance(data, bytes) or len(data) != size:
        return f'Expected a {size:,}-byte Katamari file'
    if data[4] != 1:
        return 'Invalid file header'
    if struct.unpack_from('<I', data)[0] != zlib.crc32(data[8:]):
        return 'Checksum mismatch'
    return ''


class KatamariDamacyUS(InternalSlotsPlugin):
    name = 'Katamari Damacy (US PS2)'
    directory = DIRECTORY
    slot_size = SLOT_SIZE
    options_size = OPTIONS_SIZE
    slot_count = SLOT_COUNT
    slot_error_name = 'Katamari'

    @staticmethod
    def _read_slot(save_directory, data, number):
        return {'data': data[f'katamari_save{number:02}']}

    @staticmethod
    def _write_slot(save_directory, destination, number, parts):
        # Filenames select the position; preserve the record and shared options.
        return {**destination, f'katamari_save{number:02}': parts['data']}

    @classmethod
    def supports(cls, save_directory):
        return save_directory == cls.directory

    @classmethod
    def payload_names(cls, save_directory):
        return (save_directory, *(f'katamari_save{n:02}' for n in range(1, cls.slot_count + 1)))

    @classmethod
    def slots(cls, save_directory, data):
        if not cls.supports(save_directory):
            raise ValueError('This Katamari game/region layout is unsupported.')
        if not isinstance(data, dict):
            raise ValueError('Katamari options and slot files are required.')
        error = validation_error(data.get(save_directory), cls.options_size)
        if error:
            raise ValueError('Katamari options: ' + error)
        result = []
        for number in range(1, cls.slot_count + 1):
            payload = data.get(f'katamari_save{number:02}')
            error = validation_error(payload, cls.slot_size)
            # The game's menu checks +4 in the record, after the 8-byte
            # library header; a zero record ID denotes an unused slot.
            state = ('invalid' if error else 'occupied'
                     if struct.unpack_from('<I', payload, 12)[0] else 'empty')
            result.append(dict(number=number, state=state,
                               label='Saved game' if state == 'occupied' else '',
                               sha256=hashlib.sha256(payload).hexdigest() if state == 'occupied' else None,
                               error=error))
        return result
