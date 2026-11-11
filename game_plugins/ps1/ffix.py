"""Final Fantasy IX US: one PS1 block per numbered save, with no card I/O.

Layout and CRC cross-checked against gjoerulv's Memoria save editor and
nintendogamer15's FFIX Save Editor; see ../README.md for references.
"""

from dataclasses import replace
import re
import struct


PREFIX = 'BASLUS-0125100000-'
SAVE_SIZE = 8192
CHECKSUM_OFFSET = 0x13fe
SLOT_COUNT = 15


def checksum(data):
    value = 0xffff
    for byte in data[:CHECKSUM_OFFSET]:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x8408 if value & 1 else 0)
    return value


def decode_text(data):
    result = []
    punctuation = {0x0a: '+', 0x0b: '-', 0x0f: ' ', 0x2b: '!', 0x2c: '?',
                   0x2e: ':', 0x2f: '.', 0x4b: ',', 0x4c: '/', 0x4f: '&', 0x7e: '’'}
    for byte in data:
        if byte == 0xff:
            break
        if byte <= 9:
            result.append(chr(ord('0') + byte))
        elif 0x10 <= byte <= 0x29:
            result.append(chr(ord('A') + byte - 0x10))
        elif 0x30 <= byte <= 0x49:
            result.append(chr(ord('a') + byte - 0x30))
        else:
            result.append(punctuation.get(byte, '?'))
    return ''.join(result).strip()


class FinalFantasyIXUS:
    name = 'Final Fantasy IX (US PS1)'
    backend_id = 'ps1'
    card_slots = True
    slot_count = SLOT_COUNT

    @staticmethod
    def supports(save_id):
        return re.fullmatch(re.escape(PREFIX) + r'(?:0[0-9]|1[0-4])', save_id) is not None

    @staticmethod
    def payload_name(save_id):
        return 'data'

    @staticmethod
    def slot_number(save_id):
        if not FinalFantasyIXUS.supports(save_id):
            raise ValueError('Unsupported FFIX game/region save ID.')
        return int(save_id[-2:]) + 1

    @classmethod
    def validate_save(cls, save_id, data):
        cls.slot_number(save_id)
        if not isinstance(data, bytes) or len(data) != SAVE_SIZE:
            raise ValueError('Expected an 8,192-byte FFIX save.')
        if data[:4] != b'SC\x11\x01':
            raise ValueError('Unsupported FFIX save header.')
        if struct.unpack_from('<H', data, CHECKSUM_OFFSET)[0] != checksum(data):
            raise ValueError('FFIX checksum mismatch.')

    @classmethod
    def summary(cls, save_id, data):
        result = {'row_title': f'Final Fantasy IX · Slot {cls.slot_number(save_id)}'}
        try:
            cls.validate_save(save_id, data)
        except ValueError as error:
            return {**result, 'summary': 'Progress unavailable: ' + str(error)}
        seconds = struct.unpack_from('<I', data, 0x12c)[0] // 60
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        leader = decode_text(data[0x106:0x10e])
        location = decode_text(data[0x110:0x12c])
        progress = [f'{leader or "Party leader"} Lv{data[0x105]}', location,
                    f'Playtime {hours}:{minutes:02}:{seconds:02}']
        return {**result, 'summary': ' · '.join(part for part in progress if part)}

    @classmethod
    def prepare_slot(cls, source, destination_slot):
        if type(destination_slot) is not int or not 1 <= destination_slot <= SLOT_COUNT:
            raise ValueError('FFIX destination slot must be between 1 and 15.')
        old_slot = cls.slot_number(source.save_id)
        files = source.file_data()
        if len(source.files) != 1 or set(files) != {'data'}:
            raise ValueError('Unexpected FFIX save file set.')
        data = files['data']
        cls.validate_save(source.save_id, data)
        if old_slot == destination_slot:
            return source
        digits = str.maketrans('0123456789', '０１２３４５６７８９')
        old_label = ('ＦＩＬＥ' + f'{old_slot:02}'.translate(digits)).encode('shift_jis')
        new_label = ('ＦＩＬＥ' + f'{destination_slot:02}'.translate(digits)).encode('shift_jis')
        title = data[4:68]
        if title.count(old_label) != 1:
            raise ValueError('FFIX slot label does not match its save ID.')
        payload = bytearray(data)
        payload[4:68] = title.replace(old_label, new_label)
        struct.pack_into('<H', payload, CHECKSUM_OFFSET, checksum(payload))
        save_id = PREFIX + f'{destination_slot - 1:02}'
        cls.validate_save(save_id, bytes(payload))
        return replace(source, save_id=save_id,
                       files=(replace(source.files[0], data=bytes(payload)),))
