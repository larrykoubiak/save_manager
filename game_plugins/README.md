# Game plugins

Plugins primarily identify a game's save slots and support copying an
individual save into a chosen destination slot. They receive bytes and never
write live cards themselves. The shared card layer owns preview, preservation
checks, backup and atomic replacement. Decoding extra progress/stat fields is
optional supporting information.

The registry in `__init__.py` selects an adapter by save directory. Each plugin
identifies its payload filename and expected size and returns a `Breakdown` containing named sections,
string fields, and notes for inspection. Plugins have no card-writing API.
`save_core/save_inspection.py` reads the selected card with `rb`, invokes the plugin, and
adds generic source and file information. Unsupported games still show that
generic information.

## Final Fantasy X International

Current recognition is deliberately limited to `BISLPS-25088FF0906xx`, the PS2
International/Undub saves present in this setup. The payload filename equals
the save directory and must contain exactly 25,848 bytes. Other regions and
HD/remaster formats are not claimed as supported.

References used to establish the format:

- [FFXED field definitions](https://github.com/gabacode/FFXED/blob/main/src/ffxed/FFXSaveEditor.java)
- [FFXED byte reading and checksum routine](https://github.com/gabacode/FFXED/blob/main/src/ffxed/data/SaveDataManager.java)
- [Independent checksum implementation](https://github.com/BitPatty/ffx-checksum-repair-tool/blob/master/ChecksumUtility/FFXChecksumUtility.cs)

The FFXED repository describes itself as a restructured decompilation of the
original editor. These sources are format references; the Python decoder is
implemented here rather than importing or running the editor.

All field values are little-endian. Decoded fields include seconds at 252,
gil at 15752, battle count at 15700, the story marker at 3116 (16 bits), room
at 250 (16 bits), and spawn point at 248 (8 bits). Character records start
at 22032 with a 148-byte stride; labels distinguish current HP/MP from base
values rather than calling the base values maximums.

Both checksums at 0x001a and 0x64f4 must match before progress fields are shown.
The checksum covers [0x40, 0x64f8), treating bytes 0x64f4–0x64f5 as zero,
with initial/final XOR 0xffff. Its table uses polynomial 0x1021 but the final
table entry is **zero**, not standard CRC-CCITT's 0x1ef0. Standard CRC routines
therefore do not work. The routine reproduced the stored checksums in all
three existing saves: 0x5a1a, 0xfbd7, and 0x2eed.

Checksums verify bytes, not story consistency. Numeric progress/room IDs are
shown without inventing chapter names or completion percentages. The decoder does not edit files. The optional slot adapter returns new
in-memory saves for the merge exporter; neither plugin performs filesystem I/O.

## Experimental FFX slot cloning

`ffx_slots.py` is a separate, experimental transformation for the verified
International layout. It returns a new in-memory save object and performs no
filesystem I/O. It updates the directory/payload/icon filenames and the slot
label plus three icon filename references in `icon.sys`, following the
[FFXED slot editor](https://github.com/gabacode/FFXED/blob/main/src/ffxed/data/SaveDataManager.java).
The game-state payload and icon model remain byte-for-byte identical, so
the payload checksums do not need changing. Unexpected filenames, labels,
layouts, or source checksums cause it to refuse the operation.

A local VMC candidate has been prepared with the LRPS2 slot 1 copied to
slot 3, leaving the destination's existing slots 1 and 2 intact. Filesystem,
content-preservation, metadata, and checksum checks pass. On November 7, 2026,
the user confirmed that the copied slot 3 works in FFX on the physical PS2
through OPL. This verifies that particular slot 1 → slot 3 International/Undub
case, not every region or slot combination. The shared backend uses this adapter for merge export to a new card, preserving
each distinct payload and assigning unused slots when needed. It never
installs the card or modifies source cards. Both ECC and raw output formats
are reopened and checked by the exporter; only the specific physical-PS2
slot-cloning case above has been validated in-game.

## Ar tonelico: Melody of Elemia (US)

The read-only decoder recognizes exactly `BASLUS-21445AT` (`SLUS-21445`).
It reads the 341,832-byte `GAMEFILE`, not the separate 1,435-byte configuration
file named after the directory. It checks all 12 internal slot positions and
shows each occupied slot separately, with playtime, money, and Lyner's level.
Empty positions are listed in the summary. Other regions and sequels are
not assumed compatible. The plugin also provides individual-slot
copying through byte-only `slots()` / `copy_slot()` methods; it does not write
cards itself. Automatic merging remains unsupported.

This layout was established by inspecting the user's own US executable,
`SLUS_214.45`, read from their existing ISO. Executable SHA-256:
`1168d79ca760088c8e0d7a90c7bd03e7654dbe558357454166b335fc9ab8c12a`.
The executable and private save samples remain under ignored `experiments/`;
neither is distributed with this plugin. No usable public save-format
specification was found during the initial research.

The serialization descriptor table at virtual address `0x590b28` contains
38 `(RAM address, byte count, field label)` triples. Their byte counts sum
to `0x6f46` (28,486), and `GAMEFILE` contains 12 such records. The serializer
at `0x13c1cc` copies those fields consecutively without alignment padding.
Relevant offsets within each record are:

| Offset | Size | Field |
| --- | --- | --- |
| `0x0000` | 1 | Zero-based slot index; `0xff` marks an unused position |
| `0x0001` | 4 | Checksum, little-endian |
| `0x0005` | 4 | Start marker `0x7fffffff` |
| `0x0009` | 48 | SaveGameIndex summary |
| `0x0039` | 4 | PlayTime |
| `0x003d` | 4 | ManaClock (not exposed) |
| `0x0041` | 4 | Money |
| `0x0045` | 12 | DivePoint data (not exposed) |
| `0x0051` | `0x720` | Chara block; first little-endian u16 is Lyner's level |
| `0x6f42` | 4 | End marker `0x7fffffff` |

The checksum routine at `0x13d684` shifts each byte left by its record
offset modulo 4, accumulates both a 32-bit sum and an XOR, and returns
the bitwise complement of their XOR. The stored checksum bytes at offsets
1–4 are treated as zero. The record reader at `0x13c440` checks the boundary
markers and checksum. The plugin also checks the slot index against its
physical position; malformed occupied records show integrity information
without decoded progress. Other verified slots remain visible, but an
invalid slot makes the complete payload fail validation for copying.

The playtime accessor at `0x13ac7c` divides the timer by 60 to obtain seconds.
Observed samples:

| Source | Slot | Playtime | Money | Lyner level | Checksum |
| --- | --- | --- | --- | --- | --- |
| Copyparty Emulator | 1 | 0:07:55 | 500 | 10 | `ff70ec79` |
| Local PCSX2 / VMC | 1 | 2:47:43 | 586 | 21 | `ff75804f` |

The user independently confirmed about 2:47 playtime for the local PCSX2
save. Its decoded values and checksum match the VMC copy. Only occupied
slot 1 exists in these real samples; synthetic tests cover multiple slots,
slot 12, empty positions, corrupt checksums, and malformed record boundaries.
### Individual-slot copying

`copy_slot()` copies one occupied, verified record into a chosen position in
an existing destination `GAMEFILE`. It rewrites only the record's zero-based
slot index and checksum; bytes 5 through the end of that record are preserved.
Every other destination record is byte-identical. Occupied destinations require
explicit replacement unless the relocated record is already identical.
The source slot must validate; all occupied destination slots must validate.
Empty and invalid positions are listed separately by `slots()`.

Further executable inspection confirms the configuration descriptor table at
`0x590ad0` serializes 1,435 bytes: data version, checksum, boundary marker,
7-byte Config, 1,410-byte OMAKE, 2-byte GameDataNo, and end marker. The record
loader at `0x13c834` reads one `GAMEFILE` record at the selected index multiplied
by the record size, then calls the record checker at `0x13c440`. The copier
leaves the destination configuration, icons, directory metadata, and all other
card files untouched. It requires an existing destination save; it does not
construct a new game container or migrate settings/unlocks from the source.

The card layer builds and reopens an in-memory candidate, verifies the payload
and all other files/metadata, then uses the existing preview/hash checks,
verified backup, atomic replacement and read-back workflow. Same-card copies
are supported. Copied container dates are retained; slot changes are tracked
by hashes. Slot fingerprints exclude the position byte and checksum, allowing
matching game states to be recognized after relocation.

Synthetic tests cover empty, occupied and invalid positions, both directions
between raw/ECC cards, same-card copying, replacement, backup, stale previews,
and configuration/other-slot preservation. The two archived real payloads also
pass relocation checks into all 12 positions without changing their originals.
On November 8, 2026, the user confirmed that a copied Ar tonelico slot loaded
successfully in-game on real PS2 hardware through OPL. The exact slot numbers
and card formats were not specified; this confirms the reported workflow,
not every slot/format combination or other regions. No private save or
executable is distributed.
