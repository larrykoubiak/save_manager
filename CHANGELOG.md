# Changelog

## Unreleased

- Merge mymc++ upstream master `3247c9c` (3.2.0) into the local vendor branch,
  retaining card-validation and strict icon/keyframe fixes. Drop duplicate
  texture/RLE changes now provided upstream; keep upstream pushes disabled.

- Separate Save Manager into a fresh standalone repository and manage the
  reader as a pinned submodule of our fork, with recursive CI checkout and
  preserved local history. Remove the Tk frontend and desktop-only scan/UI
  helpers; package shared code under `save_core/`. Installation and CI now
  target the web app directly.

- Expose Ar tonelico US internal slots and selected-slot copying
  in the web app, including same-card copies, explicit occupied-slot replacement,
  checksum relocation, preserved destination settings, verified backups, and
  persistent slot observations/audit fields. User-confirmed in-game on real
  PS2 hardware through OPL on November 8, 2026; remove the unverified warning.

- Add a PS2-inspired browser theme with dark blue surfaces, an orb motif,
  selection highlights, and transparent animated icons while retaining the
  device-column comparison table.

- Fix Katamari icons' zero-length texture RLE runs, GTA3's static dummy
  animation track, and animation key parsing. Version the icon cache and
  browser URLs so previously cached placeholders are regenerated.
- Correct GIF playback to use a 60 Hz animation clock scaled by each icon's
  stored speed, preserving loop duration independently of thumbnail sampling.

- Replace device cards with plain comparison tables: one save per row and
  one device per column, with all copies and transfer actions in each cell.
- Restore save icons as on-demand, cached animated GIFs using PS2 morph
  keyframes; cache source models for offline use and support still images.

- SQLite device/location inventory with retained offline snapshots and
  observed save-content versions.
- Browser dashboard with multiple locations per device, manual scans,
  configurable scan polling, library comparisons, and activity history.
- Reviewed PS2 transfers reuse candidate validation, verified backups, and
  atomic replacement; destination cards are rescanned after transfer.
- Optional web authentication, non-root Docker image, persistent Compose
  volume, and installable `save-manager-web` entry point (`.[web]` extra).


## 0.1.0 — 2026-11-07

First version of the PS2 Save Manager Tk application, distinct from the
bundled upstream mymc++ 3.1.0 package.

- Browse Emulator/VMC save copies by game, with icons and card capacity.
- Copy whole saves with preview, explicit conflicts, space checks, verified
  original-card backups, and atomic destination replacement.
- Decode FFX International/Undub progress and export its distinct slots to a
  new ECC or raw card; relocate only through the verified slot adapter.
- Decode Ar tonelico US occupied/empty slots and verify per-slot checksums.
- Provide read-only console/CSV comparisons and a consolidation-selection
  helper with explicit source overrides and conservative date ties.
- Preserve the upstream code under `vendor/mymcplusplus/`; fix erased-slot
  lookup/check/export, icon texture flags, and read-only orphan reporting.
- Add app packaging, CLI/window version labels, developer/usage/scope docs,
  and CI tests/builds. Exclude private experiments from distributions.
- Store new backups in the user's data directory and guard window closure
  during active copy/export operations.

No automatic cloud sync, master-card deployment, general slot deletion, or
Ar tonelico merging is included. Private prepared card sets remain local.
