"""US Tomba: DSPSUB2 save builder 800e954c, XOR routines 800e9c54/94.

GAME is copied at +0x200: timer +8, remembered file index +0x17.
The card-browser title deliberately numbers files 00..14, one below our UI.
"""

from functools import reduce
from operator import xor

from .numbered import NumberedSaveLayout, NumberedSavePlugin, fullwidth_digits


PREFIX = 'BASCUS-94236TOMBA-'
SLOT_COUNT = 15
CHECKSUM_OFFSET = 0xbff


def checksum(data):
    return reduce(xor, data[0x200:CHECKSUM_OFFSET], 0)


def slot_label(number):
    return fullwidth_digits(f'{number - 1:02}').encode('shift_jis')


class TombaUS(NumberedSavePlugin):
    name = 'Tomba! (US PS1)'
    title = 'Tomba!'
    error_name = 'Tomba!'
    library_family = PREFIX
    slot_count = SLOT_COUNT
    layout = NumberedSaveLayout(
        prefix=PREFIX, suffixes=tuple(f'{n:02}' for n in range(SLOT_COUNT)),
        header=b'SC\x13\x01' + 'ＴＯＭＢＡ！　［'.encode('shift_jis'),
        checksum_offset=CHECKSUM_OFFSET, checksum_format='B', checksum=checksum,
        playtime_offset=0x208, slot_label=slot_label, label_offset=0x14,
        validate_label=True, slot_index_offset=0x217, library_key='whole',
    )
