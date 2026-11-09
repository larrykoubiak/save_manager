# Save Manager

A standalone web app for managing memory-card saves across your devices.
Compare copies in a device-column table, browse animated PS2 icons, and copy
whole saves or supported individual slots with a preview and verified backup.

## Run

Requires Python 3.10+. Linux is the initial supported platform.

```bash
git submodule update --init --recursive
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/save-manager
```

Open <http://127.0.0.1:8080>. `save-manager-web` and `ps2-save-manager` are
aliases for the same web application.

For Docker, copy the example and edit the card-folder paths in your local
override, then start the app:

```bash
cp compose.override.yaml.example compose.override.yaml
# Edit compose.override.yaml with your host card folders.
docker compose up --build -d
```

Open <http://127.0.0.1:8081>. Set `SAVE_MANAGER_PORT` to choose another host
port. An optional `cloudflared` service provides remote access through your
Cloudflare Tunnel. See [web setup](docs/WEB.md) for mounts, authentication
and tunnel configuration.

## Features

- Named devices with multiple mounted locations, manual scans or polling.
- SQLite inventory with last-known copies retained when devices are offline.
- PS2 `.ps2` / `.bin` cards, raw/ECC formats, capacity and animated save icons.
- Reviewed whole-save copying with conflict detection and atomic replacement.
- Ar tonelico US: all 12 slots, copying into empty or explicitly replaced slots,
  including on the same card; user-confirmed on real hardware through OPL.
- .hack//Infection US: browse 12 slots with playtime and copy individual slots
  between existing saves, preserving the other slots and shared index entries.
- Katamari Damacy US: browse three slots and copy one between existing saves
  while retaining the destination options.
- Steambot Chronicles US: browse 12 slots with playtime, and copy verified
  slots from incomplete saves into complete destination saves.
- THPS4 US careers: keep conflicting versions as separately named careers
  (for example, Larry and Larry 2), preserving their game-state data.
- FFX International/Undub: keep both copies using an unused native slot;
  the transformation has a user-confirmed physical-PS2 case.
- Activity history and original-card backups. Polling only scans.

PS1 formats, native device connectors and automatic bidirectional sync are
future work. Game plugins focus on save slots and safe copying; optional
inspection/merge helpers remain in the shared backend.

Close games/emulators using either card before applying a transfer. Backups
live under the application's data directory (`/data/backups` in Docker).
See [usage and recovery](docs/USAGE.md).

The read-only console/CSV utility is also available:

```bash
ps2-save-report --emulator-dir /path/to/memcards --vmc-dir /path/to/VMC
```

## Project and dependencies

This repository belongs to Save Manager. The mymc++ reader, original project
files and locally preserved upstream Git history live under
[`vendor/`](vendor/README.md). The root repository starts its own history and
has no remote configured. The reader is a submodule pinned to our fork. Clone
with `git clone --recurse-submodules <app-repository>` or initialize submodules
as shown above before installing/building. Attribution and GPLv3-or-later licensing remain in
[THIRD_PARTY.md](THIRD_PARTY.md) and [LICENSE](LICENSE).

Private saves, backups and analysis under ignored `experiments/` are excluded
from distributions and Docker builds.

Further details: [development](docs/DEVELOPMENT.md), [current scope](docs/STATUS.md),
[plugin evidence](game_plugins/README.md), [changelog](CHANGELOG.md).
