"""US .hack//Infection slots: twelve files and a shared 28-byte-per-slot index.

Format reference: Toyz/piney_apples, docs/formats/save.md (ccSaveDataInfo).
Unused records may contain uninitialized bytes after their zero status flag.
"""

import hashlib
import struct


DIRECTORY = 'BASLUS-20267DOTHACK'
SLOT_COUNT = 12
SLOT_SIZE = 34096
INDEX_SIZE = 28


class DotHackInfectionUS:
    name = '.hack//Infection (US PS2)'
    slot_count = SLOT_COUNT

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

    @classmethod
    def copy_slot(cls, save_directory, source, source_slot, destination, destination_slot, replace=False):
        for number in (source_slot, destination_slot):
            if type(number) is not int or not 1 <= number <= SLOT_COUNT:
                raise ValueError('.hack slots must be integers between 1 and 12.')
        if type(replace) is not bool:
            raise ValueError('Slot replacement must be explicitly true or false.')
        source_info = cls.slots(save_directory, source)[source_slot - 1]
        destination_info = cls.slots(save_directory, destination)
        if source_info['state'] != 'occupied':
            raise ValueError('Source slot is empty or invalid: ' + (source_info['error'] or str(source_slot)))
        if any(slot['state'] == 'invalid' for slot in destination_info):
            raise ValueError('Destination contains invalid slots. Resolve them before copying.')
        source_record = source[save_directory][(source_slot - 1) * INDEX_SIZE:source_slot * INDEX_SIZE]
        start = (destination_slot - 1) * INDEX_SIZE
        target = f'dhdata{destination_slot:02}'
        payload = source[f'dhdata{source_slot:02}']
        if destination[save_directory][start:start + INDEX_SIZE] == source_record and destination[target] == payload:
            return dict(destination)
        if destination_info[destination_slot - 1]['state'] == 'occupied' and not replace:
            raise ValueError(f'Destination slot {destination_slot} is occupied. Choose replacement explicitly.')
        result = dict(destination)
        result[target] = payload
        result[save_directory] = destination[save_directory][:start] + source_record + destination[save_directory][start + INDEX_SIZE:]
        checked = cls.slots(save_directory, result)[destination_slot - 1]
        if checked['state'] != 'occupied' or checked['sha256'] != source_info['sha256']:
            raise ValueError('Copied slot verification failed.')
        return result
