# Third-party code

The PS1 backend is implemented in Save Manager using Martin Korth's
[PSX-SPX memory card data-format reference](https://problemkaputt.de/psxspx-memory-card-data-format.htm)
for directory chains, checksums, sector replacements, titles and bitmap icons.

The internal PS2 backend in `save_core/backends/ps2/native/` adapts the PS2
filesystem, ECC, directory metadata and icon decoders from
[mymc++ by Adubbz](https://github.com/Adubbz/mymcplusplus), based on mymc+ by
Florian Märkl and mymc by Ross Ridge. Original copyright and license headers
are retained. The root `LICENSE` contains GPLv3; the application and this
adapted code are distributed under GPLv3 or later.

The code was imported from our fork's commit
`ded056d195728e072de94dec0b813867784630dd`, which includes upstream 3.2.0
(`3247c9c`) and fixes for erased directory slots, read-only orphan reporting,
animation keys, truncated icons and incomplete textures. Save Manager now
owns this internal implementation; it does not require a mymc++ installation
or Git submodule. Upstream's GUI, CLI, packaging and unused save-archive
readers are not bundled. The private PS2 save-record helper was reduced to
its directory/file representation, and ECC diagnostics use debug logging.

The icon decoder retains the first animation key and handles zero-key tracks
without overreading. The Katamari zero-run behavior and animation track layout were checked
against the independent [PS2-ICON-PARSER implementation](https://github.com/DISC-Manchester/PS2-ICON-PARSER/blob/new/icon.js)
and cached local samples. No code from that implementation is bundled.

FFX field offsets, checksum behavior, and slot-label transformations were
established using the references linked in `game_plugins/README.md`. The
Python implementations here do not import or execute FFXED. Ar tonelico
format notes document original analysis of a privately held game executable.
No game executable, disc image, BIOS, or private save is distributed.

FFIX US field offsets, text encoding, CRC and slot labels were cross-checked
against [gjoerulv's Memoria save editor](https://github.com/gjoerulv/Memoria_FF9SaveEditor)
and [nintendogamer15's FFIX Save Editor](https://github.com/nintendogamer15/ffix-save-editor).
The Python plugin is implemented here from those format facts; neither editor
nor its game-data tables is bundled or executed.

Chrono Cross US slot IDs, preview offsets and byte-sum checksum were
cross-checked against [RyudoSynbios's Game Tools Collection](https://github.com/RyudoSynbios/game-tools-collection)
and private saves. The Python adapter is implemented from those format facts;
no editor code or game-data tables from that project are bundled.

PSP VMP signature derivation in `save_core/backends/ps1/vmp.py` follows
[dots-tb's vita-mcr2vmp](https://github.com/dots-tb/vita-mcr2vmp/blob/master/src/main.c)
and [Bucanero's psv_resign.c](https://github.com/bucanero/ps2vmc-tool/blob/main/src/psv_resign.c),
under GPLv3 or later. The Python adapter preserves the destination header and
uses PyCryptodome for AES and Python's standard library for HMAC-SHA1. Known
answers were cross-checked with the independent C converter; its source and
executable are not bundled.
