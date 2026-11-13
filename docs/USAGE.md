# Usage and recovery

## Browse

Start the web app or Docker container as described in [web setup](WEB.md).
Add a named device and one or more mounted card folders, then **Scan now**.
The **Save library** groups distinct saves by platform and game serial.
Each game has one set of memory-card port settings. Original card positions
are kept as source information; destination slots are chosen when building
target memory cards. Expand a game to browse and download its saves.
Version history settings apply across platforms and retain old history in
addition to distinct copies still found on active sources.

The **Cards** table compares saves across device columns. **Manage** opens
locations, scan settings and **View cards**, including capacity information.

Scanning is read-only. Offline devices retain their last known inventory.
Dates use each card's own clock; matching dates do not prove matching content.
Completely blank standard-size images appear unformatted and are not modified.

## Transfer

**Transfer…** copies a whole save directory. Conflicts stop by default;
replacement is explicit. **Keep both** uses an unused slot for supported FFX
International saves. Nested source directories are currently refused.

Ar tonelico US lists occupied and empty internal slots in each device cell.
Choose **Copy…** beside a slot, then a destination card and slot. The target
must already contain an Ar tonelico US save; same-card copies are supported.
Other slots, configuration and icons stay intact. **Whole save…** remains
separate. Ar tonelico copying has been confirmed on real PS2 hardware with OPL.

Preview validates an in-memory candidate and reopens it to check preservation.
Close every game/emulator using either card, confirm that the cards are idle,
and apply. Changed card hashes invalidate the preview. Before replacement,
the app verifies a local backup and staged candidate, then atomically renames
and reads back the destination. A refused rename never falls back to truncation.

## Backups and recovery

Web backups are under `<data-dir>/backups/`, or `/data/backups/` in Docker's
persistent data volume. Each operation has an original destination image and
`copy.json` containing paths, hashes, action and any slot numbers. Older
pre-web backups remain in their existing locations, including
`~/.local/share/ps2-save-manager/backups/` and `experiments/copy-backups/`.

To restore, close every game/emulator using the card, read `copy.json` to
identify its destination, and copy the original image back there. Keep the
backup. If a failure occurred after replacement, inspect the destination and
backup before retrying; the original may already have been replaced.

To back up application state, stop the service and copy its complete data
directory, including the SQLite database and backups. Do not put the SQLite
volume on SMB/NFS storage.

## Console and cloud

```bash
ps2-save-report --emulator-dir /path/to/memcards --vmc-dir /path/to/VMC
ps2-save-report --emulator-dir /path/to/memcards --vmc-dir /path/to/VMC --csv /tmp/save-comparison.csv
```

The console compares exact internal directory names without writing cards.
CSV output cannot overwrite an input card.

Mount network storage on the host before exposing it to Docker. Read-back
verifies the mounted file, not remote upload completion. Transfers publish mounted RetroArch cloud-manifest changes when the complete
cloud root is exposed, but do not run client sync, configure OPL or propagate deletions.
Polling updates inventory; transfers remain reviewed actions.
