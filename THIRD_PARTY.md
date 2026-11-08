# Third-party code

The PS2 filesystem reader, save-container support, and icon model decoder in
`vendor/mymcplusplus/` come from [mymc++ by Adubbz](https://github.com/Adubbz/mymcplusplus),
based on mymc+ by Florian Märkl and mymc by Ross Ridge. The upstream project
is retained with its original documentation, tests, packaging, and copyright
headers. Its version is 3.2.0. Save Manager is a standalone web application
with its own repository; mymc++ is a vendored dependency.

The vendored reader is based on upstream commit
`3247c9c`, merged into the local vendor branch on November 8, 2026. The original
history and pre-merge branch are preserved in the local submodule metadata
under `.git/modules/vendor/mymcplusplus/`. The app pins our fork's `ded056d`
through `.gitmodules` and its gitlink; see [vendor notes](vendor/README.md).
Local changes cover erased directory-slot handling, optional reporting of
orphaned allocation during read-only extraction, corrected animation key
parsing, and strict rejection of truncated icons/incomplete textures.
Texture flags and RLE decoding now use upstream's implementation.
The root `LICENSE` contains GPLv3;
the application and bundled derived code are distributed under GPLv3 or later.

The icon decoder retains the first animation key and handles zero-key tracks
without overreading. The Katamari zero-run behavior and animation track layout were checked
against the independent [PS2-ICON-PARSER implementation](https://github.com/DISC-Manchester/PS2-ICON-PARSER/blob/new/icon.js)
and cached local samples. No code from that implementation is bundled.

FFX field offsets, checksum behavior, and slot-label transformations were
established using the references linked in `game_plugins/README.md`. The
Python implementations here do not import or execute FFXED. Ar tonelico
format notes document original analysis of a privately held game executable.
No game executable, disc image, BIOS, or private save is distributed.
