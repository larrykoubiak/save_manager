"""US Xenogears file/summary digits and byte sum; see xenogears-ex-machina."""

import unicodedata

from .numbered import NumberedSaveLayout, NumberedSavePlugin, fullwidth_digits


PREFIX = 'BASLUS-00664'
SLOT_COUNT = 15
CHECKSUM_OFFSET = 0x1fff


def checksum(data):
    return sum(data[0x100:CHECKSUM_OFFSET]) & 0xff


def slot_label(number):
    return fullwidth_digits(f'{number:02}').encode('shift_jis')


def progress(data):
    title = data[0x24:0x44].split(b'\0', 1)[0].decode('shift_jis', errors='replace')
    return (unicodedata.normalize('NFKC', title).strip(),)


class XenogearsUS(NumberedSavePlugin):
    name = 'Xenogears (US PS1)'
    title = 'Xenogears'
    error_name = 'Xenogears'
    library_family = PREFIX
    slot_count = SLOT_COUNT
    layout = NumberedSaveLayout(
        # The game adds the index to ASCII '0': positions 11..15 use : ; < = >.
        prefix=PREFIX, suffixes=tuple(chr(ord('0') + n) for n in range(SLOT_COUNT)),
        header=b'SC\x11\x01' + 'ＸＥＮＯＧＥＡＲＳ／Ｎｏ．'.encode('shift_jis'),
        checksum_offset=CHECKSUM_OFFSET, checksum_format='B', checksum=checksum,
        playtime_offset=0x100, slot_label=slot_label, label_offset=0x1e,
        validate_label=True, slot_index_offset=0x123, progress=progress, library_key='whole',
    )
