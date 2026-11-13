"""Tales of Phantasia PS1 (Absolute Zero translation), one block per slot.

Layout checked against three private saves and the translated executable:
8001bec4 formats the title, 8001c0c4 names slots A–O, and 8001b3b4
sums bytes before the final little-endian u32. No game-state fields are edited.
"""

from .numbered import NumberedSaveLayout, NumberedSavePlugin, fullwidth_digits


PREFIX = 'BISLPSP01770.TOP.'
SAVE_SIZE = 8192
SLOT_COUNT = 15
CHECKSUM_OFFSET = 0x1ffc
TITLE_PREFIX = 'Ｔａｌｅｓ　ｏｆ　Ｐｈａｎｔａｓｉａ：　Ｎｏ．'.encode('shift_jis')
NUMBER_OFFSET = 0x32
PLAYTIME_OFFSET = 0x11cc


def checksum(data):
    return sum(data[:CHECKSUM_OFFSET]) & 0xffffffff


def slot_label(number):
    return fullwidth_digits(f'{number:2d}').encode('shift_jis')


def validate_playtime(data):
    if data[0x508:0x50c] != data[PLAYTIME_OFFSET:PLAYTIME_OFFSET + 4]:
        raise ValueError('Tales of Phantasia playtime fields disagree.')


class TalesOfPhantasiaTranslated(NumberedSavePlugin):
    name = 'Tales of Phantasia'
    title = 'Tales of Phantasia'
    error_name = 'Tales of Phantasia'
    library_family = PREFIX
    slot_count = SLOT_COUNT
    layout = NumberedSaveLayout(
        prefix=PREFIX, suffixes=tuple(chr(ord('A') + n) for n in range(SLOT_COUNT)),
        header=b'SC\x12\x01' + TITLE_PREFIX, size=SAVE_SIZE,
        checksum_offset=CHECKSUM_OFFSET, checksum_format='<I', checksum=checksum,
        playtime_offset=PLAYTIME_OFFSET, slot_label=slot_label,
        label_offset=NUMBER_OFFSET, validate_label=True, validate_extra=validate_playtime,
        library_key='whole',  # Preserve the existing whole-save library lineage.
    )
