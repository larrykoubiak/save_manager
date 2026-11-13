# Game plugins

Plugins primarily identify a game's save slots and support copying an
individual save into a chosen destination slot. They receive bytes and never
write live cards themselves. The shared card layer owns preview, preservation
checks, backup and atomic replacement. Decoding extra progress/stat fields is
optional supporting information.

The registry in `__init__.py` selects a platform package (`ps1/`, `ps2/`),
then an adapter by save ID. Each game has one module; FFX decoding and slot
transformations both live in `ps2/ffx.py`. Each plugin
identifies its payload filename and expected size and returns a `Breakdown` containing named sections,
string fields, and notes for inspection. Plugins have no card-writing API.
`save_core/save_inspection.py` reads the selected card with `rb`, invokes the plugin, and
adds generic source and file information. Unsupported games still show that
generic information.

## Save library extraction

Plugins may implement `library_slots(bundle)` returning `LibrarySlot` records
from `game_plugins/library.py`: stable slot key/number, label, isolated files
and optional integer playtime in seconds. `library_family` groups numbered
save files belonging to the same compatible game edition. The library retains
the full original `SaveBundle` with each version for backend metadata and
shared settings. Unsupported games remain whole-save entries.

New internal-slot plugins also index retained whole-save library bundles,
including offline sources. Original versions stay available for download;
the library lists their extracted progress instead of duplicate whole backups.

## Internal PS2 slots

Plugins for internal PS2 slots share `ps2/internal_slots.py`. `InternalSlotsPlugin` handles slot-number checks,
replacement policy, copy verification and library extraction. Each game
implements `slots()`, `_read_slot()` and `_write_slot()`; reads return named
slot bytes, and writes return a new payload without mutating either input.
The `slots()` fingerprint must describe progress independently of position.
Optional `_slot_playtime()` and `_validate_destination()` hooks handle timers
and shared settings. File names, offsets, checksums and encoding stay in the
game module. FFX and THPS4 use separate whole-save relocation workflows.

## Numbered PS1 saves

The numbered PS1 game modules share `ps1/numbered.py`: validation,
playtime formatting, library extraction and byte-only slot renumbering. Each
game supplies a `NumberedSaveLayout` with filename suffixes, header, checksum,
playtime and title fields, plus optional progress decoding and extra validation.
Game modules retain their checksum algorithms and format references. Library
keys and backend metadata are preserved. Games with internal slots or several
payload files keep their own implementations.

The additional US layouts below were verified against eight distinct private
library saves, including all 15 renumbered destinations for each sample. These
checks use private snapshots; live cards were not modified. In-game testing of
renumbered copies remains outstanding.

| Game | Filename suffixes | Integrity / slot index | Playtime |
| --- | --- | --- | --- |
| FFT | `A`–`O` | MSB-first parity bits at `0x118`, blocks 3–59; index `0x100` | Seconds at `0x120` |
| SaGa Frontier 2 | `0`–`9`, `A`–`E` | Byte sum of `0x180..0x1fff` excluding stored sum `0x181` | 60 Hz ticks at `0x544` |
| Tomba! | `00`–`14` | XOR `0x200..0xbfe`, stored `0xbff`; index `0x217` | 60 Hz ticks at `0x208` |
| Xenogears | ASCII `0`–`>` | Byte sum `0x100..0x1ffe`, stored `0x1fff`; index `0x123` | 60 Hz ticks at `0x100` |

Each retains its existing `whole` library key. Tomba's native title is zero-based;
the UI consistently uses positions 1–15. Xenogears' last five filename characters
are deliberately `:;<=>`, not decimal numbers. These are names inside a card,
not host filesystem paths. MGS has its own named-save adapter below.

References: [FFT save format](https://github.com/adamrt/fft_decomp/blob/master/include/fft/save.h)
and [save/load parity handling](https://github.com/adamrt/fft_decomp/blob/master/src/world/card_save.c);
[Tomba game state](https://github.com/hansbonini/psx_tomba/blob/main/include/game/state.h),
cross-checked with private US `DSPSUB2.BIN` save/load routines at `800e954c` and
`800e9c54`; [Xenogears save/load routines](https://github.com/brianblakely/xenogears-ex-machina/blob/master/src/decomp/src/slot39/menu_framework.c).
SaGa Frontier 2 was checked against private `SLUS_009.33` checksum/load routines
at `8005d69c`/`8005d6d8` and its save builder at `8005e19c`. The preview copier
`8005dd6c` and renderer `8005e734` verify the 60 Hz timer at `0x544`, year
at `0x574`, location/scenario text at `0x184`, and leader text at `0x558`.
The two private samples show `1220 / Gustave Born / Gustave / 0:03:17` and
`1235 / Westia / Wil Knights / 0:39:52`. Only known Latin glyphs are decoded;
unrecognized glyphs display `?` and stored bytes are never changed.

## Metal Gear Solid (US PS1)

`ps1/mgs.py` recognizes US story saves (`BASLUS-00594G...`). Each one-block
file contains three identical `0xa40`-byte snapshots at `0x100`, `0xb40` and
`0x1580`. Each snapshot has a CRC32 over its bytes `[8,0xa40)` and verified
layout versions `0x5f`/`0x60`. All three copies must agree and pass their CRCs.
The 60 Hz timer is at `0x110`, difficulty at `0x142`, disc at `0x148`; the
browser title supplies the location, with the stage code as a fallback.

Filename characters encode time, difficulty, run/disc flags, save type and a
final collision letter `A`–`P`. “Keep both” uses the game's first-free-letter
search, changing only that final letter. It preserves the full payload, title
and backend metadata. There is no artificial numbered-slot menu. The existing
`whole` library key and history are retained. VR training, photos and other
regions are outside this adapter; recovery of disagreeing snapshots is not
performed. Three private samples passed validation and filename-copy checks;
in-game validation of renamed copies remains outstanding.

References: [MGS snapshot layout/CRC](https://github.com/FoxdieTeam/mgs_reversing/blob/master/source/libgcl/variable.c),
[filename generation and repeated snapshots](https://github.com/FoxdieTeam/mgs_reversing/blob/master/source/menu/datasave.c),
[clock updates](https://github.com/FoxdieTeam/mgs_reversing/blob/master/source/game/gamed.c).

## Tales of Phantasia (translated PS1)

`ps1/tales_of_phantasia.py` supports the Absolute Zero v1.0 layout checked
against three private saves and the translated executable. Files
`BISLPSP01770.TOP.A` through `O` are 15 numbered, one-block saves. Playtime
is a 60 Hz counter at `0x11cc`, mirrored at `0x508`. Renumbering edits only
the filename, four title bytes at `0x32`, and the byte-sum checksum over
`[0,0x1ffc)` stored as a little-endian u32 at `0x1ffc`. Existing library
entries retain their identity and history. Other translations are unverified;
in-game validation of renumbered copies remains outstanding. Private saves
and executable bytes are excluded from the repository.

## Chrono Cross (US PS1)

`ps1/chrono_cross.py` recognizes `BASLUSP01041USCHRO00` through `14`: 15
numbered saves per card, one 8 KiB block each. It shows 60 Hz playtime at
`0x588` and stars at `0x5a4`. Copying to another slot changes the save ID,
fullwidth card-browser label and little-endian checksum at `0x1ffc` (byte sum
over `[0,0x1ff8)`); compressed state and reserved tail bytes stay unchanged.
All four distinct private samples passed checksum and renumbering checks for
every slot. In-game testing of renumbered copies remains outstanding.
Format references: Game Tools Collection's [layout](https://github.com/RyudoSynbios/game-tools-collection/blob/master/src/lib/templates/chrono-cross/saveEditor/template.ts)
and [checksum](https://github.com/RyudoSynbios/game-tools-collection/blob/master/src/lib/templates/chrono-cross/saveEditor/utils.ts).

## Final Fantasy IX (US PS1)

`ps1/ffix.py` recognizes `BASLUS-0125100000-00` through `-14`: each numbered
save is one 8 KiB file, not an internal slot in a larger save. It reads leader,
level, location and 60 Hz playtime, and copies to a chosen slot from 1 to 15.
Renumbering changes the save ID, fullwidth `FILEnn` title, saved menu row
at `0x17c` when necessary, and CRC; all other payload bytes are preserved.
The menu row must keep the restored three-row viewport within slots 1–15.
Failing to clamp it when moving FILE02 to FILE01 makes the game render index
255 and crash while checking the card. CRC-16 uses reflected polynomial `0x8408`, seed
`0xffff`, no final XOR, covering bytes before the little-endian checksum at
`0x13fe`. Both private USB samples reproduce their stored CRCs and title times.
Other regions and rerelease formats are not supported. The relocated
6:43:11 save and all 15 destination numbers were checked by running the US
Disc 1 Rev 1 game with PCSX-ReARMed against isolated card copies.

Format references: [Memoria SaveMap](https://github.com/gjoerulv/Memoria_FF9SaveEditor/blob/main/SaveMap.cs),
[Memoria slot-label/checksum handling](https://github.com/gjoerulv/Memoria_FF9SaveEditor/blob/main/SelectSave.cs),
and [FFIX Save Editor layout](https://github.com/nintendogamer15/ffix-save-editor/blob/main/src/FFIX.SaveEditor.Core/SaveLayout.cs).

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

The slot transformation in `ps2/ffx.py` handles the verified
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

## .hack//Infection (US PS2)

`BASLUS-20267DOTHACK` has twelve 34,096-byte `dhdata01`–`dhdata12`
files and a 336-byte index named after the directory. The plugin reads each
28-byte index entry's occupancy, name, level, playtime (60 ticks/second),
and 16-bit byte-sum checksum. Empty slots may retain arbitrary unused bytes.
Copying preserves the chosen payload and its index entry exactly, updating
only their destination positions. An existing destination save is required;
occupied slots need explicit replacement. Other regions/parts are unsupported.

Format reference: [piney_apples save research](https://github.com/Toyz/piney_apples/blob/main/docs/formats/save.md).
Validated with synthetic raw/ECC cards and read-only previews of the existing
US cards; in-game validation is still pending.

## Katamari Damacy (US PS2)

`BASLUS-21008Opt` contains three `katamari_save01`–`katamari_save03`
files (7,072 bytes each) and a separate 1,008-byte options file named after
the directory. Each has an eight-byte library header: CRC32 at +0 and a
valid byte at +4. The CRC covers all bytes after that header. The slot menu
uses the game record's +4 word (file offset 12) to distinguish occupied slots.
Copying changes only the selected slot file, preserving its bytes and the
destination options. Existing destination saves are required. Other regions
are unsupported; in-game validation is pending.

Derived from the user's US executable: CRC routine `0x1827b0`, header
write/read `0x183ec8` / `0x184734`, and overlay 0 slot checks `0x1048798`.
The overlay loads at `0x1038140`; its save descriptors at `0x1043638`
handle the three 7,064-byte records plus 1,000-byte options independently.
The [noclip extractor](https://github.com/magcius/noclip.website/blob/main/src/KatamariDamacy/tools/extractor.ts)
provided the overlay location/compression reference. Executable and save bytes
remain in the ignored local experiments directory; tests use synthetic data.

## We Love Katamari (US PS2)

`BASLUS-21230Opt` uses the Katamari family adapter with three 11,944-byte
`katamari_saveNN` records and a separate 1,008-byte options file. CRC32,
header and occupancy checks apply to each position. Copying preserves the
complete source record and all destination options and sibling slots.
Both archived US saves validate; live-card writes and in-game validation
remain pending. Other regions are unsupported.

## Odin Sphere (US PS2)

`BASLUS-21577` is a 93,548-byte version-4 file: eight shared header bytes
followed by five `0x4914`-byte save records. Each record stores the sum of its
bytes from +4 onward at +0. The load menu tests bit 0 of +`0x4910` for
occupancy. Copying preserves the complete source record, including its checksum,
and changes no shared header bytes or other positions.

The menu reads the timer at +`0x4904`, divides by the single-precision
`59.94f` constant (`0x426fc28f`), and truncates to seconds. Its calendar
fields at +`0x4908` are year-since-2000 (u16), month and day.
Derived from the private US executable: validation `0x29ab20`, checksum
`0x29bc90`, packing/unpacking `0x29ac10`/`0x29ad00`, and load-menu preview
`0x33e0e0`. Both archived saves validate and all five destinations preserve
unrelated bytes in offline copy checks. Synthetic raw/ECC-card tests also
check backups and icon preservation. In-game validation remains pending;
other regions and Leifthrasir are unsupported.

## Monster Hunter (US PS2)

`BASLUS-20896MH` is a 72,584-byte encoded file. Three 1,768-byte character
records begin at `0x10260`; their first byte controls occupancy. Names start
at +8 and the character menu reads playtime in seconds at +`0x5dc`.
The stored seed drives XOR with successive `(seed or 1) * 176 % 65363`
values. The checksum sums the decoded `0x8dbc` halfwords after the eight-byte
header; the final eight bytes are padding outside the encoded body.
Copying changes one character and the checksum while retaining the destination
seed, patch area, shared settings, other characters and padding.

Derived from the private US executable: encode/decode `0x23f320`/`0x23f3f0`,
character copying `0x23f730`, three-position dispatch `0x23f8f8`, and menu
`0x23f110`. [MH Oldschool's codec research](https://github.com/MH-Oldschool/mhpatcher/blob/main/src/dnas/savecodec.c)
provides an independent reference for the cipher. Both archived US saves
pass checksums and relocation checks. In-game validation remains pending;
no other regions or Monster Hunter sequels are recognized.

## Steambot Chronicles (US PS2)

`BASLUS-21344` has twelve `FILE_00`–`FILE_11` slots (67,568 bytes)
and a separate 2,012-byte shared settings file. A slot wraps a 0x107d0-byte
record at +16 with a stored file length, additive 32-bit word checksum,
and 0x6219 trailer. The first 263 full 256-byte blocks use a fixed byte
substitution plus the one-based block number; the tail remains plain.
The game checks the occupied byte at file +0x4010. Playtime at +0x402c
uses 30 ticks per second. Copying preserves the chosen encoded file exactly;
settings, other slots and filesystem metadata remain unchanged.

Derived from the user's US Undub executable: decode/check `0x31d6f4` and
`0x31d93c`, encode `0x31e590`, substitution table `0x4dcfa0`, shared settings
`0x31fc50` / `0x2943a0`, and playtime rendering `0x318920` (108000 ticks/hour,
1800/minute). Incomplete saves can supply individually valid source slots;
destinations require all twelve valid slot files and valid shared settings.
Synthetic raw/ECC transfers and read-only previews with both existing cards
are checked. In-game validation is pending; other regions are unsupported.

## Tony Hawk's Pro Skater 4 careers (US PS2)

Careers are separate named saves, not positions in a shared slot file.
`BASLUS-20504` plus seven little-endian base-26 letters of the lowercase
name CRC and a final `a` identifies a career. The 40,960-byte payload has
summary CRC at +4, summary length at +8, used length at +12, and version 24
at +16. The summary starts at +20. Both CRCs use CRC32 without the final
XOR; the whole used span treats its first checksum word as zero.

**Keep both** chooses an unused name such as `Larry 2`, rewrites only the
summary's filename, envelope lengths/checksums, payload filename, directory
and icon title. The serialized game state remains byte-identical; this does
not combine goals/unlocks within a career. The icon model and original dates
are preserved. Other regions and custom-skater/park saves are unsupported.

Derived from the user's US executable: lowercase CRC `0x105aa0`, filename
mapping `0x1e15e0`. Both checksums and generated names match the existing
Larry/Den saves. Synthetic card transfers and read-only real-card previews
pass; in-game validation is pending.

THPS4 copy labels include the quick-load summary's `cash` and `numGoalsBeaten`
fields. Compact integer types are verified against the executable's writer
at `0x18a9a0` and reader at `0x18b2f0`–`0x18b3c0`. Unknown summary encodings
leave progress unavailable. No playtime field has been verified.
