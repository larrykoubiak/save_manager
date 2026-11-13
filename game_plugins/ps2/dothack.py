"""US .hack//Infection slots: twelve files and a shared 28-byte-per-slot index.

Format reference: Toyz/piney_apples, docs/formats/save.md (ccSaveDataInfo).
Unused records may contain uninitialized bytes after their zero status flag.
"""

import hashlib
import struct

from .internal_slots import InternalSlotsPlugin


DIRECTORY = 'BASLUS-20267DOTHACK'
SLOT_COUNT = 12
SLOT_SIZE = 34096
INDEX_SIZE = 28


class DotHackInfectionUS(InternalSlotsPlugin):
    name = '.hack//Infection (US PS2)'
    slot_count = SLOT_COUNT
    slot_error_name = '.hack'

    @staticmethod
    def _read_slot(save_directory, data, number):
        return {'index': data[save_directory][(number - 1) * INDEX_SIZE:number * INDEX_SIZE],
                'data': data[f'dhdata{number:02}']}

    @staticmethod
    def _slot_playtime(parts):
        return struct.unpack_from('<i', parts['index'], 24)[0] // 60

    @staticmethod
    def _write_slot(save_directory, destination, number, parts):
        start = (number - 1) * INDEX_SIZE
        return {**destination, f'dhdata{number:02}': parts['data'],
                save_directory: destination[save_directory][:start] + parts['index'] + destination[save_directory][start + INDEX_SIZE:]}

    @staticmethod
    def supports(save_directory):
        return save_directory == DIRECTORY

    @staticmethod
    def payload_names(save_directory):
        return (save_directory, *(f'dhdata{n:02}' for n in range(1, SLOT_COUNT + 1)))

    @classmethod
    def slots(cls, save_directory, data):
        if not cls.supports(save_directory):
            raise ValueError('This .hack region/game layout is unsupported.')
        if not isinstance(data, dict) or any(not isinstance(data.get(name), bytes)
                for name in cls.payload_names(save_directory)):
            raise ValueError('The .hack index and all twelve slot files are required.')
        index = data[save_directory]
        if len(index) != INDEX_SIZE * SLOT_COUNT:
            raise ValueError('Expected a 336-byte .hack slot index.')
        result = []
        for number in range(1, SLOT_COUNT + 1):
            record = index[(number - 1) * INDEX_SIZE:number * INDEX_SIZE]
            payload = data[f'dhdata{number:02}']
            problems = []
            if len(payload) != SLOT_SIZE:
                problems.append('Expected a 34,096-byte Infection slot file')
            if record[0] not in (0, 1):
                problems.append('Invalid slot status')
            label = ''
            if record[0] == 1:
                ticks = struct.unpack_from('<i', record, 24)[0]
                if record[1] >= 100 or record[2] >= 5 or record[3] >= 2:
                    problems.append('Invalid slot preview fields')
                if not 0 <= ticks <= 0x0cdfe5c4:
                    problems.append('Invalid playtime')
                if struct.unpack_from('<H', record, 22)[0] != (sum(payload) & 0xffff):
                    problems.append('Checksum mismatch')
                if not problems:
                    name = record[4:22].split(b'\0', 1)[0].decode('shift_jis', errors='replace')
                    hours, remainder = divmod(ticks // 60, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    label = f'{name} · Lv {record[1]} · {hours}:{minutes:02}:{seconds:02} played'
                    if record[2]:
                        label += ' · Cleared'
            state = 'invalid' if problems else 'occupied' if record[0] == 1 else 'empty'
            result.append(dict(number=number, state=state, label=label,
                               sha256=hashlib.sha256(record + payload).hexdigest() if state == 'occupied' else None,
                               error='; '.join(problems)))
        return result
