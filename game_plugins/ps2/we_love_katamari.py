"""We Love Katamari US: three independent Namco CRC-wrapped save records.

The wrapper, filenames and occupancy field match Katamari Damacy; each
record is larger. Shared options remain local when copying a position.
"""

from .katamari import KatamariDamacyUS


DIRECTORY = 'BASLUS-21230Opt'
SLOT_SIZE = 11944


class WeLoveKatamariUS(KatamariDamacyUS):
    name = 'We Love Katamari (US PS2)'
    directory = DIRECTORY
    slot_size = SLOT_SIZE
