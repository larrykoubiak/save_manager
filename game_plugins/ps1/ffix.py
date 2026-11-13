"""Final Fantasy IX US: one PS1 block per numbered save, with no card I/O.

Layout and CRC cross-checked against gjoerulv's Memoria save editor and
nintendogamer15's FFIX Save Editor; see ../README.md for references.
"""


from .numbered import NumberedSaveLayout, NumberedSavePlugin, fullwidth_digits


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
    layout = NumberedSaveLayout(
        prefix=PREFIX, suffixes=tuple(f'{n:02}' for n in range(SLOT_COUNT)),
        header=b'SC\x11\x01', size=SAVE_SIZE,
        checksum_offset=CHECKSUM_OFFSET, checksum_format='<H', checksum=checksum,
        playtime_offset=0x12c, slot_label=slot_label, progress=progress,
    )
