"""Monster Hunter US character slots, derived from SLUS_208.96.

0x23f320/0x23f3f0 encode/check 0x8dbc halfwords using the stored seed.
0x23f730 moves 0x6e8-byte characters at 0x10260, independently of shared
patch/settings data. 0x23f110 displays each record's name and seconds timer.
"""

import hashlib
import struct

from .internal_slots import InternalSlotsPlugin


DIRECTORY = 'BASLUS-20896MH'
SAVE_SIZE = 72584
BODY_END = 8 + 0x8dbc * 2
SLOT_OFFSET = 0x10260
SLOT_SIZE = 0x6e8
SLOT_COUNT = 3


def _crypt(data):
    result = bytearray(data)
    key = struct.unpack_from('<H', data, 2)[0]
    for offset in range(8, BODY_END, 2):
        word = struct.unpack_from('<H', data, offset)[0] ^ key
        struct.pack_into('<H', result, offset, word)
        key = ((key or 1) * 0xb0) % 0xff53
    return bytes(result)


def _checksum(plain):
    return sum(struct.unpack_from('<' + 'H' * 0x8dbc, plain, 8)) & 0xffff


def decode(data):
    if not isinstance(data, bytes) or len(data) != SAVE_SIZE:
        raise ValueError(f'Expected a {SAVE_SIZE:,}-byte Monster Hunter US save.')
    if data[:2] != b'\x00\x01' or data[6:8] != b'cY':
        raise ValueError('Unsupported Monster Hunter save header.')
    plain = _crypt(data)
    if struct.unpack_from('<H', plain, 4)[0] != _checksum(plain):
        raise ValueError('Monster Hunter checksum mismatch.')
    return plain


class MonsterHunterUS(InternalSlotsPlugin):
    name = 'Monster Hunter (US PS2)'
    slot_count = SLOT_COUNT
    slot_error_name = 'Monster Hunter'
    payload_size = SAVE_SIZE

    @staticmethod
    def supports(save_directory):
        return save_directory == DIRECTORY

    @staticmethod
    def payload_name(save_directory):
        return save_directory

    @staticmethod
    def _read_slot(save_directory, data, number):
        start = SLOT_OFFSET + (number - 1) * SLOT_SIZE
        return {'record': decode(data)[start:start + SLOT_SIZE]}

    @staticmethod
    def _slot_playtime(parts):
        return struct.unpack_from('<I', parts['record'], 0x5dc)[0]

    @staticmethod
    def _write_slot(save_directory, destination, number, parts):
        plain = bytearray(decode(destination))
        start = SLOT_OFFSET + (number - 1) * SLOT_SIZE
        plain[start:start + SLOT_SIZE] = parts['record']
        struct.pack_into('<H', plain, 4, _checksum(plain))
        return _crypt(plain)

    @classmethod
    def slots(cls, save_directory, data):
        if not cls.supports(save_directory):
            raise ValueError('This Monster Hunter game/region layout is unsupported.')
        plain = decode(data)
        result = []
        for number in range(1, SLOT_COUNT + 1):
            start = SLOT_OFFSET + (number - 1) * SLOT_SIZE
            record = plain[start:start + SLOT_SIZE]
            state = 'empty' if record[0] == 0 else 'occupied' if record[0] == 1 else 'invalid'
            label = ''
            if state == 'occupied':
                name = record[8:24].split(b'\0', 1)[0].decode('shift_jis', errors='replace')
                hours, remainder = divmod(cls._slot_playtime({'record': record}), 3600)
                minutes, seconds = divmod(remainder, 60)
                label = f'{name} · {hours}:{minutes:02}:{seconds:02} played'
            result.append(dict(number=number, state=state, label=label,
                               sha256=hashlib.sha256(record).hexdigest() if state == 'occupied' else None,
                               error='Invalid character status' if state == 'invalid' else ''))
        return result
