"""Burnout 3 US: three 1,180-byte careers inside the version-16 container.

Layout cross-checked against Takedawn's PS2-save research and both archived
US saves. Shared settings and records stay with the destination container.
"""

import hashlib
import struct

from .internal_slots import InternalSlotsPlugin


DIRECTORY = 'BASLUS-21050'
SAVE_SIZE = 61772
SLOT_OFFSET = 0xe374
SLOT_SIZE = 0x49c
CHECKSUM_OFFSET = 0xf148


def checksum(data):
    return sum(struct.unpack_from('<I', data, offset)[0]
               for offset in range(0, CHECKSUM_OFFSET, 4)) & 0xffffffff


def validate(data):
    if not isinstance(data, bytes) or len(data) != SAVE_SIZE or struct.unpack_from('<I', data)[0] != 16:
        raise ValueError('Unsupported Burnout 3 US save layout.')
    if checksum(data) != struct.unpack_from('<I', data, CHECKSUM_OFFSET)[0]:
        raise ValueError('Burnout 3 checksum mismatch.')


class Burnout3US(InternalSlotsPlugin):
    name = 'Burnout 3: Takedown (US PS2)'
    slot_count = 3
    slot_error_name = 'Burnout 3'

    @staticmethod
    def supports(directory):
        return directory == DIRECTORY

    @staticmethod
    def payload_name(directory):
        return DIRECTORY

    @classmethod
    def validate_save(cls, directory, data):
        if not cls.supports(directory):
            raise ValueError('Unsupported Burnout 3 game/region.')
        validate(data)

    @classmethod
    def _read_slot(cls, directory, data, number):
        offset = SLOT_OFFSET + (number-1) * SLOT_SIZE
        return {'record': data[offset:offset+SLOT_SIZE]}

    @classmethod
    def _write_slot(cls, directory, destination, number, parts):
        result = bytearray(destination)
        offset = SLOT_OFFSET + (number-1) * SLOT_SIZE
        result[offset:offset+SLOT_SIZE] = parts['record']
        struct.pack_into('<I', result, CHECKSUM_OFFSET, checksum(result))
        return bytes(result)

    @classmethod
    def slots(cls, directory, data):
        cls.validate_save(directory, data)
        result = []
        for number in range(1, 4):
            record = cls._read_slot(directory, data, number)['record']
            occupied = any(record[:8])
            raw_name = record[8:26]
            try:
                name = raw_name.decode('utf-16le').split('\0',1)[0]
                error = '' if name and all(c.isprintable() for c in name) else 'Invalid Burnout 3 profile name.'
            except UnicodeDecodeError:
                name, error = '', 'Invalid Burnout 3 profile name.'
            result.append(dict(number=number,state='invalid' if error else 'occupied' if occupied else 'empty',
                               label=f'Career “{name}”' if occupied and not error else '',
                               sha256=hashlib.sha256(record).hexdigest() if occupied and not error else None,error=error))
        return result

    @classmethod
    def summary(cls, directory, data):
        return {'summary': ' · '.join(slot['label'] for slot in cls.slots(directory,data) if slot['state']=='occupied')}
