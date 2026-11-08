# Current scope

Save Manager is a standalone web application with SQLite storage and Docker
packaging. The mymc++ reader is a Git submodule under vendor, pinned to our
fork's patched commit. The Tk proof of concept has been removed.

Available features:

- Named devices, multiple mounted locations, manual or scheduled scans.
- Last-known offline inventory and content/version observations.
- Device-column comparison table, PS2-inspired theme and animated save icons.
- Raw/ECC PS2 card scanning, capacity, reviewed whole-save transfers and backups.
- Ar tonelico US slot enumeration and selected-slot copying, including same-card
  and explicit replacement. User-confirmed on real PS2 hardware through OPL
  on November 8, 2026.
- FFX International unused-slot copying. Slot 1 to slot 3 was user-confirmed on
  real PS2 hardware through OPL on November 7, 2026.
- Read-only console/CSV reporting; shared Python inspection, merge/export and
  consolidation-selection helpers.

Future work includes PS1 formats, more game slot adapters, native device
connectors and persistent three-way sync policies. Existing FFX merge and
consolidation helpers are not yet exposed as complete web workflows.

Private consolidated cards and analysis remain under ignored `experiments/`;
this cleanup does not deploy them. Games must be closed during transfers.
The app cannot lock a remote console out of its card. Other game regions,
nested saves and writes to unformatted destinations are not supported.

See [web setup and API](WEB.md) and [plugin evidence](../game_plugins/README.md).
