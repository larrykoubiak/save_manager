# Current scope

Save Manager is a standalone web application with SQLite storage and Docker
packaging. Storage implementations are internal packages behind a common
protocol. The PS2 backend adapts the mymc++ reader with its attribution
preserved. The Tk proof of concept has been removed.

Available features:

- Device-independent save library grouped by platform and game serial, with saves grouped separately from their revisions.
- Content-addressed local save storage, configurable version retention (two previous versions by default), downloads and library version selection.
- Named devices with root folders and relative locations per system, manual or scheduled scans.
- Last-known offline inventory and content/version observations.
- Device-column comparison table, PS2-inspired theme and animated save icons.
- Raw/ECC PS2 card scanning, capacity, reviewed whole-save transfers and backups.
- Raw PS1 card scanning (including subfolders), bitmap icons, multi-block copy/delete and automatic sync.
- Signed PSP VMP read/write support and verified FTP transfers, with offline inventory and fresh downloads before transfers.
- Per-game PSP sync with shared PS1 cards; FFIX VMP signing and save loading
  were confirmed on a PSP-3010 running 6.61 ARK-4 Infinity on November 14, 2026.
- Platform-grouped game plugins, including FFIX and Chrono Cross numbered PS1 saves.
- Location removal that preserves files, backups and transfer history.
- Ar tonelico US slot enumeration and selected-slot copying, including same-card
  and explicit replacement. User-confirmed on real PS2 hardware through OPL
  on November 8, 2026.
- FFX International unused-slot copying. Slot 1 to slot 3 was user-confirmed on
  real PS2 hardware through OPL on November 7, 2026.
- Read-only console/CSV reporting; shared Python inspection, merge/export and
  consolidation-selection helpers.

Future work includes additional container formats and more game slot adapters. Opt-in sync already tracks per-card baselines and offline updates. Existing FFX merge and
consolidation helpers are not yet exposed as complete web workflows.

Private consolidated cards and analysis remain under ignored `experiments/`;
this cleanup does not deploy them. Games must be closed during transfers.
The app cannot lock a remote console out of its card. Other game regions,
other headered PS1 containers and native PSP save formats are not supported.

See [web setup and API](WEB.md) and [plugin evidence](../game_plugins/README.md).
