"""Chrono Cross US: numbered one-block saves and byte-only slot copying.

Offsets and checksum cross-checked against RyudoSynbios/Game Tools Collection
and four distinct private saves. Compressed game-state bytes are never edited.
"""

from .numbered import NumberedSaveLayout, NumberedSavePlugin, fullwidth_digits


PREFIX = 'BASLUSP01041USCHRO'
SAVE_SIZE = 8192
CHECKSUM_END = 0x1ff8
CHECKSUM_OFFSET = 0x1ffc
SLOT_COUNT = 15


def checksum(data):
    return sum(data[:CHECKSUM_END]) & 0xffffffff


def slot_label(number):
    return ('ＣＨＲＯＮＯ　ＣＲＯＳＳ　' + fullwidth_digits(f'{number:02}')).encode('shift_jis')


def progress(data):
    stars = data[0x5a4]
    return (f'{stars} star{"s" if stars != 1 else ""}',)


class ChronoCrossUS(NumberedSavePlugin):
    name = 'Chrono Cross (US PS1)'
    title = 'Chrono Cross'
    error_name = 'Chrono Cross'
    library_family = PREFIX
    slot_count = SLOT_COUNT
    layout = NumberedSaveLayout(
        prefix=PREFIX, suffixes=tuple(f'{n:02}' for n in range(SLOT_COUNT)),
        header=b'SC\x11\x01', size=SAVE_SIZE,
        checksum_offset=CHECKSUM_OFFSET, checksum_format='<I', checksum=checksum,
        playtime_offset=0x588, slot_label=slot_label, progress=progress,
    )
