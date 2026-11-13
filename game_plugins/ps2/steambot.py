"""US Steambot Chronicles slots, derived from SLUS_213.44's save routines.

Files wrap a 0x107d0-byte record in a length/checksum/magic envelope.
The first 0x107 blocks use a fixed byte substitution with a block offset.
Shared settings are separate; incomplete saves may supply valid source slots.
"""

import hashlib
import struct

from .internal_slots import InternalSlotsPlugin


DIRECTORY = 'BASLUS-21344'
SLOT_COUNT = 12
SLOT_SIZE = 0x107f0
BODY_SIZE = 0x107d0
COMMON_SIZE = 0x7dc
MAGIC = 0x6219
# Format's byte substitution table (SLUS_213.44, address 0x4dcfa0).
TABLE = bytes.fromhex(
    "74343c7f9ec57aa2197c0eca2e369455200ae999ad2d320f255f4cbe30111bb6"
    "7626613590da382c9f4d70778cbfb74fb4ee438b6506fc2fecf38f49f059d8dd"
    "df05c63784b59269e586983fc840a05c9a3accef0d419c545808c227b915de75"
    "04a642ebba97182af29bf49dbd0b4651fd53f8c022dba850475bf1811ed1d967"
    "d7011d23c7afc3e0f78a8dcb13a96b03a3f53ecd953379ff026856a59317823b"
    "73e77d71914b07b8c46a786cb031729600b11f855ac128ed6fabaeeaf6e4a4b3"
    "d42416fe21f9aaa7c9e3d6bc5d446ed57efbfa39cecf3d88d2a14e64e68e6280"
    "e80983485e7b6389ac578729d00c4ad3bb14e11a6db22b4510e2521cdc661260"
)
INVERSE = bytes(TABLE.index(value) for value in range(256))


def decode(data, common=False):
    size, body_start, body_size = (COMMON_SIZE, 4, 2000) if common else (SLOT_SIZE, 16, BODY_SIZE)
    if not isinstance(data, bytes) or len(data) != size:
        raise ValueError(f'Expected a {size:,}-byte Steambot file')
    plain = bytearray(data)
    for start in range(0, (size // 256) * 256, 256):
        block = start // 256 + 1
        plain[start:start + 256] = bytes((INVERSE[value] - block) & 255
                                        for value in data[start:start + 256])
    if struct.unpack_from('<I', plain)[0] != size:
        raise ValueError('Stored file length mismatch')
    end = body_start + body_size
    stored, magic = struct.unpack_from('<II', plain, end)
    if magic != MAGIC:
        raise ValueError('Save boundary marker mismatch')
    calculated = sum(struct.unpack_from('<' + 'I' * (body_size // 4), plain, body_start)) & 0xffffffff
    if stored != calculated:
        raise ValueError('Checksum mismatch')
    return bytes(plain)


class SteambotChroniclesUS(InternalSlotsPlugin):
    name = 'Steambot Chronicles (US PS2)'
    slot_count = SLOT_COUNT
    slot_error_name = 'Steambot'
    invalid_destination_message = 'Destination contains invalid slots. Create a complete save in-game first.'

    @staticmethod
    def _read_slot(save_directory, data, number):
        return {'data': data[f'FILE_{number - 1:02}']}

    @staticmethod
    def _slot_playtime(parts):
        ticks = struct.unpack_from('<i', decode(parts['data']), 0x402c)[0]
        return min(ticks, 0x66ff2ff) // 30

    @staticmethod
    def _write_slot(save_directory, destination, number, parts):
        return {**destination, f'FILE_{number - 1:02}': parts['data']}

    @classmethod
    def _validate_destination(cls, save_directory, destination):
        try:
            decode(destination.get(save_directory), common=True)
        except ValueError as error:
            raise ValueError('Destination shared settings are missing or invalid. Create a complete save in-game first.') from error

    @staticmethod
    def supports(save_directory):
        return save_directory == DIRECTORY

    @staticmethod
    def payload_names(save_directory):
        return (save_directory, *(f'FILE_{number:02}' for number in range(SLOT_COUNT)))

    @classmethod
    def slots(cls, save_directory, data):
        if not cls.supports(save_directory):
            raise ValueError('This Steambot game/region layout is unsupported.')
        if not isinstance(data, dict):
            raise ValueError('Steambot slot files are required.')
        result = []
        for number in range(1, SLOT_COUNT + 1):
            error, label, fingerprint = '', '', None
            try:
                plain = decode(data.get(f'FILE_{number - 1:02}'))
                occupied = plain[0x4010]
                if occupied not in (0, 1):
                    raise ValueError('Invalid slot status')
                state = 'occupied' if occupied else 'empty'
                if occupied:
                    ticks = struct.unpack_from('<i', plain, 0x402c)[0]
                    if ticks < 0:
                        raise ValueError('Invalid playtime')
                    # The game's menu divides by 108000/hour, 1800/minute.
                    seconds = min(ticks, 0x66ff2ff) // 30
                    hours, remainder = divmod(seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    label = f'{hours}:{minutes:02}:{seconds:02} played'
                    fingerprint = hashlib.sha256(plain[16:16 + BODY_SIZE]).hexdigest()
            except ValueError as problem:
                state, error = 'invalid', str(problem)
            result.append(dict(number=number, state=state, label=label, sha256=fingerprint, error=error))
        return result

    @classmethod
    def validate_save(cls, save_directory, data):
        """Whole-save transfers require shared settings and every slot file."""
        slots = cls.slots(save_directory, data)
        try:
            decode(data.get(save_directory), common=True)
        except ValueError as error:
            raise ValueError('Steambot shared settings are missing or invalid: ' + str(error)) from error
        if any(slot['state'] == 'invalid' for slot in slots):
            raise ValueError('Source validation failed: invalid save slots')

    @classmethod
    def summary(cls, save_directory, data):
        slots = cls.slots(save_directory, data)
        problems = []
        try:
            decode(data.get(save_directory), common=True)
        except ValueError:
            problems.append('shared settings missing or invalid')
        invalid = [str(slot['number']) for slot in slots if slot['state'] == 'invalid']
        if invalid:
            problems.append('missing or invalid slots: ' + ', '.join(invalid))
        return {'row_title': 'Steambot Chronicles',
                'summary': ('Incomplete save: ' + '; '.join(problems) + '. Valid slots can be copied out.') if problems else ''}
