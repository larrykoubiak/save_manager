"""Chrono Cross US: numbered one-block saves and byte-only slot copying.

Offsets and checksum cross-checked against RyudoSynbios/Game Tools Collection
and four distinct private saves. Compressed game-state bytes are never edited.
"""

from dataclasses import replace
import re
import struct


PREFIX = 'BASLUSP01041USCHRO'
SAVE_SIZE = 8192
CHECKSUM_END = 0x1ff8
CHECKSUM_OFFSET = 0x1ffc
SLOT_COUNT = 15


def checksum(data):
    return sum(data[:CHECKSUM_END]) & 0xffffffff


def slot_label(number):
    digits = f'{number:02}'.translate(str.maketrans('0123456789','０１２３４５６７８９'))
    return ('ＣＨＲＯＮＯ　ＣＲＯＳＳ　' + digits).encode('shift_jis')


class ChronoCrossUS:
    name = 'Chrono Cross (US PS1)'
    backend_id = 'ps1'
    card_slots = True
    slot_count = SLOT_COUNT

    @staticmethod
    def supports(save_id):
        return re.fullmatch(re.escape(PREFIX) + r'(?:0[0-9]|1[0-4])',save_id) is not None

    @staticmethod
    def payload_name(save_id):
        return 'data'

    @classmethod
    def slot_number(cls, save_id):
        if not cls.supports(save_id):
            raise ValueError('Unsupported Chrono Cross game/region save ID.')
        return int(save_id[-2:]) + 1

    @classmethod
    def validate_save(cls, save_id, data):
        cls.slot_number(save_id)
        if not isinstance(data,bytes) or len(data)!=SAVE_SIZE:
            raise ValueError('Expected an 8,192-byte Chrono Cross save.')
        if data[:4]!=b'SC\x11\x01':
            raise ValueError('Unsupported Chrono Cross save header.')
        if struct.unpack_from('<I',data,CHECKSUM_OFFSET)[0]!=checksum(data):
            raise ValueError('Chrono Cross checksum mismatch.')

    @classmethod
    def summary(cls, save_id, data):
        result = {'row_title':f'Chrono Cross · Slot {cls.slot_number(save_id)}'}
        try:
            cls.validate_save(save_id,data)
        except ValueError as error:
            return {**result,'summary':'Progress unavailable: ' + str(error)}
        seconds = struct.unpack_from('<I',data,0x588)[0] // 60
        hours,remainder = divmod(seconds,3600)
        minutes,seconds = divmod(remainder,60)
        stars = data[0x5a4]
        return {**result,'summary':f'{stars} star{"s" if stars!=1 else ""} · Playtime {hours}:{minutes:02}:{seconds:02}'}

    @classmethod
    def prepare_slot(cls, source, destination_slot):
        if type(destination_slot) is not int or not 1<=destination_slot<=SLOT_COUNT:
            raise ValueError('Chrono Cross destination slot must be between 1 and 15.')
        old_slot = cls.slot_number(source.save_id)
        files = source.file_data()
        if len(source.files)!=1 or set(files)!={'data'}:
            raise ValueError('Unexpected Chrono Cross save file set.')
        data = files['data']
        cls.validate_save(source.save_id,data)
        if old_slot==destination_slot:
            return source
        old_label,new_label = slot_label(old_slot),slot_label(destination_slot)
        # Game preview fields follow the title inside this 64-byte area;
        # replace only the fixed-width label and retain every other byte.
        title = data[4:68]
        if title.count(old_label)!=1:
            raise ValueError('Chrono Cross slot label does not match its save ID.')
        payload = bytearray(data)
        payload[4:68] = title.replace(old_label,new_label)
        struct.pack_into('<I',payload,CHECKSUM_OFFSET,checksum(payload))
        save_id = PREFIX + f'{destination_slot-1:02}'
        cls.validate_save(save_id,bytes(payload))
        return replace(source,save_id=save_id,files=(replace(source.files[0],data=bytes(payload)),))
