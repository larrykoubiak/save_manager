"""US FFT numbered saves; parity/layout verified against adamrt/fft_decomp."""

from .numbered import NumberedSaveLayout, NumberedSavePlugin, fullwidth_digits


PREFIX = 'BASCUS-94221FFT'
SLOT_COUNT = 15
CHECKSUM_OFFSET = 0x118


def checksum(data):
    # The loader checks MSB-first parity bits for 128-byte blocks 3..59.
    # Bits 0..2 and 60..63 are ignored; preserve them exactly as stored.
    value = int.from_bytes(data[0x118:0x120], 'big')
    for block in range(3, 60):
        parity = sum(byte.bit_count() for byte in data[block * 128:(block + 1) * 128]) & 1
        mask = 1 << (63 - block)
        value = (value & ~mask) | (parity << (63 - block))
    return value


def slot_label(number):
    return fullwidth_digits(f'{number:02}').encode('shift_jis')


def validate_format(data):
    if data[0x117] != 4:
        raise ValueError('Unsupported FFT save format version.')


def progress(data):
    return (f'Level {data[0x113]}',)


class FinalFantasyTacticsUS(NumberedSavePlugin):
    name = 'Final Fantasy Tactics (US PS1)'
    title = 'Final Fantasy Tactics'
    error_name = 'FFT'
    library_family = PREFIX
    slot_count = SLOT_COUNT
    layout = NumberedSaveLayout(
        prefix=PREFIX, suffixes=tuple(chr(ord('A') + n) for n in range(SLOT_COUNT)),
        header=b'SC\x11\x01' + 'ＦＦＴ　ＦＩＬＥ'.encode('shift_jis'),
        checksum_offset=CHECKSUM_OFFSET, checksum_format='>Q', checksum=checksum,
        playtime_offset=0x120, ticks_per_second=1, slot_label=slot_label,
        label_offset=0x14, validate_label=True, slot_index_offset=0x100,
        validate_extra=validate_format, progress=progress, library_key='whole',
    )
