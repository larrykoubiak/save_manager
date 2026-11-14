"""GTA III US: eight named save files under a shared memory-card directory.

US ELF save/load routines and the re3 MemoryCard implementation agree on
slot-prefix filenames, length-prefixed blocks, and an additive file checksum.
Relocating a slot changes only its filename; progress bytes stay untouched.
"""

import hashlib
import re
import struct

from .internal_slots import InternalSlotsPlugin


DIRECTORY = 'BASLUS-20062GTA30000'
SAVE_SIZE = 201752
GAME_SIZE = 201729
SLOT_COUNT = 8


def slot_number(name):
    match = re.fullmatch(r'([1-8]) ([\x20-\x7e]{1,29})', name)
    return int(match[1]) if match and '/' not in name and '\\' not in name else None


def validate(data):
    if not isinstance(data, bytes) or len(data) != SAVE_SIZE:
        raise ValueError('Expected a 201,752-byte GTA III US save.')
    if struct.unpack_from('<I', data, 4)[0] != GAME_SIZE:
        raise ValueError('Unsupported GTA III save header.')
    if sum(data[:-4]) != struct.unpack_from('<I', data, len(data)-4)[0]:
        raise ValueError('GTA III checksum mismatch.')
    offset, count = 0, 0
    while offset < len(data)-4:
        size = struct.unpack_from('<I', data, offset)[0]
        if not 0 < size <= 65536:
            raise ValueError('Invalid GTA III save block size.')
        offset += 4 + ((size+3) & ~3)
        count += 1
        if offset > len(data)-4 or count > 8:
            raise ValueError('Invalid GTA III save block layout.')
    if offset != len(data)-4 or struct.unpack_from('<I', data)[0] < 0x100:
        raise ValueError('Invalid GTA III save block layout.')


class GTAIIIUS(InternalSlotsPlugin):
    name = 'Grand Theft Auto III (US PS2)'
    slot_count = SLOT_COUNT
    slot_error_name = 'GTA III'
    slot_file_changes = True

    @staticmethod
    def supports(directory):
        return directory == DIRECTORY

    @classmethod
    def select_files(cls, directory, files):
        if not cls.supports(directory) or not isinstance(files, dict) or directory not in files:
            raise ValueError('GTA III shared data and slot files are required.')
        return dict(files)

    @staticmethod
    def slot_filename(files, number):
        matches = [name for name in files if slot_number(name) == number]
        if len(matches) > 1:
            raise ValueError(f'Multiple GTA III files claim slot {number}.')
        return matches[0] if matches else None

    @classmethod
    def slots(cls, directory, files):
        cls.select_files(directory, files)
        result = []
        for number in range(1, SLOT_COUNT+1):
            filename = cls.slot_filename(files, number)
            state, label, error, fingerprint = 'empty', '', '', None
            if filename:
                try:
                    validate(files[filename])
                    state = 'occupied'
                    seconds = struct.unpack_from('<I', files[filename], 44)[0] // 1000
                    hours, remainder = divmod(seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    label = f'{filename[2:]} · Playtime {hours}:{minutes:02}:{seconds:02}'
                    fingerprint = hashlib.sha256(files[filename]).hexdigest()
                except ValueError as problem:
                    state, error = 'invalid', str(problem)
            result.append(dict(number=number,state=state,label=label,sha256=fingerprint,error=error))
        return result

    @classmethod
    def _read_slot(cls, directory, files, number):
        filename = cls.slot_filename(files, number)
        return {'data': files[filename], 'mission': filename[2:].encode('ascii')}

    @staticmethod
    def _slot_playtime(parts):
        return struct.unpack_from('<I', parts['data'], 44)[0] // 1000

    @classmethod
    def _write_slot(cls, directory, destination, number, parts):
        result = dict(destination)
        old = cls.slot_filename(result, number)
        if old:
            del result[old]
        filename = f"{number} " + parts['mission'].decode('ascii')
        if slot_number(filename) != number:
            raise ValueError('Invalid GTA III mission filename.')
        result[filename] = parts['data']
        return result

    @classmethod
    def summary(cls, directory, files):
        occupied = [slot for slot in cls.slots(directory, files) if slot['state']=='occupied']
        return {'summary': ' · '.join(f"Slot {slot['number']}: {slot['label']}" for slot in occupied) or 'No valid saves'}
