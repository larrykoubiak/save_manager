"""Katamari Damacy US slots, derived from SLUS_210.08 save/load routines.

Each slot is a 7,064-byte game record with an 8-byte Namco CRC/valid header.
The slot menu checks record+4 for occupancy. Options are a separate file.
"""

import hashlib
import struct
import zlib


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


class KatamariDamacyUS:
    name = 'Katamari Damacy (US PS2)'
    slot_count = SLOT_COUNT

    @staticmethod
    def supports(save_directory):
        return save_directory == DIRECTORY

    @staticmethod
    def payload_names(save_directory):
        return (save_directory, *(f'katamari_save{n:02}' for n in range(1, SLOT_COUNT + 1)))

    @classmethod
    def slots(cls, save_directory, data):
        if not cls.supports(save_directory):
            raise ValueError('This Katamari game/region layout is unsupported.')
        if not isinstance(data, dict):
            raise ValueError('Katamari options and slot files are required.')
        error = validation_error(data.get(save_directory), OPTIONS_SIZE)
        if error:
            raise ValueError('Katamari options: ' + error)
        result = []
        for number in range(1, SLOT_COUNT + 1):
            payload = data.get(f'katamari_save{number:02}')
            error = validation_error(payload, SLOT_SIZE)
            # The game's menu checks +4 in the record, after the 8-byte
            # library header; a zero record ID denotes an unused slot.
            state = ('invalid' if error else 'occupied'
                     if struct.unpack_from('<I', payload, 12)[0] else 'empty')
            result.append(dict(number=number, state=state,
                               label='Saved game' if state == 'occupied' else '',
                               sha256=hashlib.sha256(payload).hexdigest() if state == 'occupied' else None,
                               error=error))
        return result

    @classmethod
    def copy_slot(cls, save_directory, source, source_slot, destination, destination_slot, replace=False):
        for number in (source_slot, destination_slot):
            if type(number) is not int or not 1 <= number <= SLOT_COUNT:
                raise ValueError('Katamari slots must be integers between 1 and 3.')
        if type(replace) is not bool:
            raise ValueError('Slot replacement must be explicitly true or false.')
        source_info = cls.slots(save_directory, source)[source_slot - 1]
        destination_info = cls.slots(save_directory, destination)
        if source_info['state'] != 'occupied':
            raise ValueError('Source slot is empty or invalid: ' + (source_info['error'] or str(source_slot)))
        if any(slot['state'] == 'invalid' for slot in destination_info):
            raise ValueError('Destination contains invalid slots. Resolve them before copying.')
        payload = source[f'katamari_save{source_slot:02}']
        target = f'katamari_save{destination_slot:02}'
        if destination[target] == payload:
            return dict(destination)
        if destination_info[destination_slot - 1]['state'] == 'occupied' and not replace:
            raise ValueError(f'Destination slot {destination_slot} is occupied. Choose replacement explicitly.')
        result = dict(destination)
        # Filenames select the position. No slot number or checksum needs
        # rewriting; keep the complete source slot and destination options.
        result[target] = payload
        checked = cls.slots(save_directory, result)[destination_slot - 1]
        if checked['state'] != 'occupied' or checked['sha256'] != source_info['sha256']:
            raise ValueError('Copied slot verification failed.')
        return result
