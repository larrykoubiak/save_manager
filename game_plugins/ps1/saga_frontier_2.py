"""US SaGa Frontier 2: SLUS_009.33 save/load routines 8005d69c/8005e19c.

The save preview (8005dd6c) reads the timer at 0x544, year at 0x574,
location/scenario at 0x184 and leader at 0x558. Its renderer (8005e734)
divides the timer into hours and minutes using 216000 and 3600 ticks.
"""

import struct

from .numbered import NumberedSaveLayout, NumberedSavePlugin, fullwidth_digits


PREFIX = 'BASLUSP00933-SAGA2-'
SLOT_COUNT = 15
CHECKSUM_OFFSET = 0x181
PLAYTIME_OFFSET = 0x544


def checksum(data):
    return (sum(data[0x180:0x2000]) - data[CHECKSUM_OFFSET]) & 0xff


def slot_label(number):
    return fullwidth_digits(f'{number:02}').encode('shift_jis')


def validate_format(data):
    if data[0x50:0x56] != b'\x01\0CRD0' or data[0x180] != 0x4b:
        raise ValueError('Unsupported SaGa Frontier 2 save signature.')


def decode_text(data):
    """Decode the verified Latin glyph ranges; do not invent other glyphs."""
    result = []
    for byte in data:
        if byte == 0:
            break
        if byte == 0xff:
            result.append(' ')
        elif 0x80 <= byte <= 0x89:
            result.append(chr(ord('0') + byte - 0x80))
        elif 0x8a <= byte <= 0xa3:
            result.append(chr(ord('A') + byte - 0x8a))
        elif 0xa4 <= byte <= 0xbd:
            result.append(chr(ord('a') + byte - 0xa4))
        else:
            result.append('?')
    return ''.join(result).strip()


def progress(data):
    return (str(struct.unpack_from('<H', data, 0x574)[0]),
            decode_text(data[0x184:0x198]), decode_text(data[0x558:0x56c]))


class SaGaFrontier2US(NumberedSavePlugin):
    name = 'SaGa Frontier 2 (US PS1)'
    title = 'SaGa Frontier 2'
    error_name = 'SaGa Frontier 2'
    library_family = PREFIX
    slot_count = SLOT_COUNT
    layout = NumberedSaveLayout(
        prefix=PREFIX, suffixes=tuple(f'{n:X}' for n in range(SLOT_COUNT)),
        header=b'SC\x11\x01' + 'ＳａＧａ　Ｆｒｏｎｔｉｅｒ　２　　ＤＡＴＡ'.encode('shift_jis'),
        checksum_offset=CHECKSUM_OFFSET, checksum_format='B', checksum=checksum,
        playtime_offset=PLAYTIME_OFFSET, slot_label=slot_label, label_offset=0x2e,
        validate_label=True, validate_extra=validate_format, progress=progress, library_key='whole',
    )
