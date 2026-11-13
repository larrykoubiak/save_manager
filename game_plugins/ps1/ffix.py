"""Final Fantasy IX US: one PS1 block per numbered save, with no card I/O.

Layout and CRC cross-checked against gjoerulv's Memoria save editor and
nintendogamer15's FFIX Save Editor; see ../README.md for references.
"""

from dataclasses import replace
import struct

from .numbered import NumberedSaveLayout, NumberedSavePlugin, fullwidth_digits


PREFIX = 'BASLUS-0125100000-'
SAVE_SIZE = 8192
CHECKSUM_OFFSET = 0x13fe
SLOT_COUNT = 15
MENU_ROW_OFFSET = 0x17c
MENU_VISIBLE_ROWS = 3


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


def slot_label(number):
    return ('ＦＩＬＥ' + fullwidth_digits(f'{number:02}')).encode('shift_jis')


def progress(data):
    leader = decode_text(data[0x106:0x10e])
    return (f'{leader or "Party leader"} Lv{data[0x105]}', decode_text(data[0x110:0x12c]))


class FinalFantasyIXUS(NumberedSavePlugin):
    name = 'Final Fantasy IX (US PS1)'
    title = 'Final Fantasy IX'
    error_name = 'FFIX'
    library_family = PREFIX
    slot_count = SLOT_COUNT
    relocation_offsets = (MENU_ROW_OFFSET,)
    layout = NumberedSaveLayout(
        prefix=PREFIX, suffixes=tuple(f'{n:02}' for n in range(SLOT_COUNT)),
        header=b'SC\x11\x01', size=SAVE_SIZE,
        checksum_offset=CHECKSUM_OFFSET, checksum_format='<H', checksum=checksum,
        playtime_offset=0x12c, slot_label=slot_label, progress=progress,
    )

    @classmethod
    def prepare_slot(cls, source, destination_slot):
        copied = super().prepare_slot(source, destination_slot)
        data = copied.files[0].data
        # The preview stores the selected row within the three-row load menu.
        # FFIX restores its first visible file as destination_index - row.
        # Retaining row 1 when moving FILE02 to FILE01 underflows to 255 and
        # the menu calls a function pointer outside its save-status table.
        index = destination_slot - 1
        minimum = max(0, index - (SLOT_COUNT - MENU_VISIBLE_ROWS))
        maximum = min(MENU_VISIBLE_ROWS - 1, index)
        row = max(minimum, min(data[MENU_ROW_OFFSET], maximum))
        if row == data[MENU_ROW_OFFSET]:
            return copied
        payload = bytearray(data)
        payload[MENU_ROW_OFFSET] = row
        struct.pack_into('<H', payload, CHECKSUM_OFFSET, checksum(payload))
        cls.validate_save(copied.save_id, bytes(payload))
        return replace(copied, files=(replace(copied.files[0], data=bytes(payload)),))
