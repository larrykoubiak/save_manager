# Device dashboard

Save Manager stores its device inventory in SQLite and serves a browser UI
through Waitress. PS1 and PS2 readers are internal storage backends. A read-only report
command and shared backend helpers are also available.

## Run locally

```bash
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/save-manager
```

Open <http://127.0.0.1:8080>. Defaults:

- Bind address: `127.0.0.1:8080`; change with `--host` / `--port`.
- Data: `${XDG_DATA_HOME:-~/.local/share}/ps2-save-manager/web`.
  Override with `--data-dir` or `SAVE_MANAGER_DATA`.
- Authentication: optional `SAVE_MANAGER_TOKEN`. When set, the browser asks
  for HTTP Basic credentials: use any username and the token as the password.

The app is intended for a personal machine or trusted home network. Before
exposing it beyond loopback, configure a token and HTTPS through a reverse
proxy. Anyone with access can configure locations visible to the server.
Same-origin checks protect browser mutations; there is no CORS access.

## Docker

The Docker build includes the internal storage backends from this checkout.

Keep machine-specific bind mounts in the ignored `compose.override.yaml`:

```bash
cp compose.override.yaml.example compose.override.yaml
# Edit the source path and container target in compose.override.yaml.
docker compose up --build -d
```

Compose automatically combines the shared configuration with your local override.
The example uses `/path/to/your/device` on the host and `/devices/emulator`
inside the container. It refuses to create a missing host folder, so a typo
fails visibly. Mount each device root once; add its system folders as relative locations in the UI.
The shared `compose.yaml` runs without an override when no card mounts are needed.

Open <http://127.0.0.1:8081>. To choose another host port, set
`SAVE_MANAGER_PORT`, for example `SAVE_MANAGER_PORT=9090 docker compose up --build -d`.
The container continues listening on its internal port 8080.
Set the device root to its container path (such as `/devices/emulator`), then
add relative locations such as `VMC` and `POPS`. The named volume stores the
SQLite database and transfer backups across container recreation.

The container runs as UID/GID **10001**. For transfers, set `read_only: false` on
the relevant bind mount in your local override and grant this user write access to both the image
and its parent directory (replacement uses a temporary file and rename).
Alternatively set `SAVE_MANAGER_UID` and `SAVE_MANAGER_GID` in a local `.env`
file to match `id -u` / `id -g`. The persistent `/data` volume must also be
owned by that user, including existing database files and backups. Do not recursively change ownership
of shared save folders just to run the app.

Keep filesystem network storage mounted on the host. FTP devices connect
directly from the container without a host mount. The app does not discover,
mount, or wake consoles. Plugging a handheld into another computer does not
make it visible here automatically.

For a PSP, select **FTP** when adding the device, enter its host/port and
username (`anonymous` by default), and use `/` as the remote root. Add a PS1
location at `PSP/SAVEDATA` with the `.vmp` filter. Start FTP on the handheld,
disconnect other FTP clients, and click **Scan now**; manual-only is the default.
FTP locations are read-only sources. Valid scans are cached locally, failed
scans retain the last inventory, and copies re-fetch the source before preview
and apply so an offline cache is never silently used. Passwords remain in the
private SQLite database and are omitted from API responses; an empty password
field in the UI keeps the existing password unless **Clear saved password** is checked.

## Remote access through Cloudflare Tunnel

The optional `cloudflared` service shares the app's Docker network and starts
once the app is healthy. Create a remotely managed tunnel in the
[Cloudflare dashboard](https://developers.cloudflare.com/tunnel/get-started/).
Put its connector token in the ignored local `.env`, together with an app
password (or configure Cloudflare Access for the hostname):

```dotenv
CLOUDFLARE_TUNNEL_TOKEN=your-tunnel-token
SAVE_MANAGER_TOKEN=your-private-app-password
COMPOSE_PROFILES=cloudflare
```

In the tunnel's published application route, point your chosen HTTPS hostname
to **HTTP `save-manager:8080`**. Leave the origin Host header unchanged so the
app's same-origin checks work. Then run `docker compose up -d cloudflared`
(or `docker compose up --build -d` to update the whole stack). A changed app
password requires recreating the app with `docker compose up -d save-manager`.
The password prompt accepts any username and `SAVE_MANAGER_TOKEN` as password.

The connector publishes no host ports and mounts no cards or inventory data.
Without the `cloudflare` profile, the stack continues to run locally.
`.env.example` lists these settings without credentials. Tunnel configuration
and hostname routing are managed in your Cloudflare account.

## Workflow

The **PS1** and **PS2** tabs open separate system pages, each with its own
sources, device columns, saves and sync groups. Pages have bookmarkable URLs
such as `#/systems/ps1/library` and `#/systems/ps2/sync`. Search and sort choices
stay separate while switching systems. Devices and activity remain shared;
one RetroArch device can have locations for several systems.

On a system page, **Add PS1/PS2 location…** fixes the system and asks for a
shared device and a new folder. The global Devices page can configure either
system. Then choose an extension or **All supported card files**.
PS1 currently reads raw 128 KiB card images, commonly `.mcr`, `.mcd`
and RetroArch `.srm`. PSP `.vmp` containers are read-only sources: their 128-byte
header is preserved, but signatures are not verified or regenerated yet.
Other headered formats such as `.gme` are not supported.
Cards must already be formatted. PS1 has 15 usable 8 KiB blocks; capacity and save
sizes are displayed in KiB. PS1 does not store save timestamps. Bitmap icons use
the BIOS animation cadence. Individual in-game slots require a game plugin.
Copies and sync groups stay within their platform, even when save names match.

1. **Add device**: choose a name, absolute root folder visible to the server, device type, and manual scans or a polling
   interval. The API accepts 60 seconds through seven days; the UI offers
   common intervals. Device type is a label, not a native save-format driver.
2. **Add location**: choose a folder relative to the device root (`.` for the root),
   its system, card-file extension, and whether destination writes are allowed.
   Multiple locations can belong to one device. PS1 scans include subfolders.
3. Optionally configure an **availability marker**: a filename such as
   `.save-manager-online` that you create on the actual mounted storage.
   If the marker disappears, a leftover empty mount folder is treated as
   offline. The app does not create markers or infer mount state.
4. **Scan now**, or wait for polling. Scans run in a single background
   worker. Repeated requests for the same queued/running device reuse its job.
5. The **Browser** table has one row per save directory and one column per
   device. Each cell lists all copies on that device with their card and date;
   row labels distinguish matching and different content. Search filters rows
   while keeping other devices' copies visible for comparison. Column headers
   provide **Scan now** and **Manage** for device settings, locations, and cards.
   The UI refreshes every four
   seconds. Offline locations retain their inventory, clearly marked with
   availability and last-observed dates. Matching hashes compare file names,
   directory structure, and bytes, independent of card allocation and dates.
   The **Devices** tab also uses a table for configuration and availability.

   Rows represent native save directories. In the supported FFX layout,
   individual slots have their own directories. Ar tonelico US stores all
   12 slots inside one directory's `GAMEFILE`; the web table currently shows
   that complete save as one row, listing its individual occupied slots and
   empty positions in each device's cell. Each occupied slot has a **Copy…**
   action; **Whole save…** remains a separate operation.
6. Choose **Transfer…**, select a destination, and **Preview transfer**.
   Different existing saves stop by default. Replacement must be selected
   explicitly; keep-both is limited to games the existing engine supports.
7. Close games/emulators using either image on every device, confirm this
   in the preview, and apply. The app validates the images again, creates a
   verified backup, replaces the destination atomically, and queues a rescan.

Use **Sync…** beside a save to opt into automatic sync across selected cards.
Enabled groups scan their devices every five minutes, require two unchanged scans
and at least 30 seconds of settling, then copy the changed save with a full
destination-card backup. The Sync tab shows pending, blocked and conflict states.
Different initial copies require choosing a source. Independently changed
copies stop automatic writes until you choose one. Offline targets catch up
from a persistent source snapshot when they return. Deletions are never
propagated, and removed tracked saves are not silently recreated.
This mode assumes one active gaming device at a time; it does not inspect
emulator processes. Pause a group to suspend its automatic writes.

A failed location scan retains that location's complete previous inventory.
Other reachable locations can still update, giving the device a partial
status. A successful empty scan marks previously seen cards/saves absent,
while keeping their database records. Nothing is deleted from other devices.

Save version history records **hash observations**, not recoverable save
payloads. Transfer backups contain the full original destination image and
the existing `copy.json` recovery record under `/data/backups` in Docker
(or `<data-dir>/backups` locally). Backups remain even if subsequent steps
fail. See [Usage and recovery](USAGE.md) for manual card recovery.

Previews live in memory, expire after ten minutes, and are limited to four
open plans. Restarting expires previews and marks unfinished scans or writes
interrupted. Inspect the transfer history and backup before retrying an
interrupted write. Run **one application process per data directory**; the
scheduler is embedded in that process. Use local storage for the SQLite
volume, rather than an SMB/NFS database file. To back up the application
data, stop the app and copy its data directory including backups.

### Ar tonelico slot copying

For the supported US layout, click **Copy…** beside an occupied slot, choose
a destination card and slot, then preview and confirm. The destination must
already contain an Ar tonelico US save: create one in-game first if needed.
The same card can be selected when its location allows writes. Empty target
slots are suggested; replacing an occupied slot must be selected explicitly.

Only the selected record is copied, with its slot ID/checksum adjusted. Other
slots, configuration, icons, file allocation and container dates are preserved.
Slot hashes identify matching content independently of slot position. Slot
metadata persists in SQLite for offline browsing; writes always revalidate
the current images. A successful scan is needed to populate slots in older
inventories. Invalid source slots cannot be copied, and destinations with
invalid slots are refused. Inspecting stats is not required for this workflow.

The copied record and complete card are verified in memory and after reopening,
and application uses the existing verified-backup/atomic-replacement workflow.
Byte-level checks pass for synthetic and archived real payloads. On November 8,
2026, the user confirmed a copied slot worked in-game on real PS2 hardware
through OPL. Exact slot numbers/formats were not reported; other regions are
unsupported. No automatic slot merging or deletion is provided.

### Save icons

Scans cache each save's original icon model in SQLite, deduplicated by SHA-256.
The table renders a 64×64 GIF on first request and stores it under
`<data-dir>/icons/`. Icons remain available when a device is offline, and
repeated requests reuse the cached GIF without reopening the card. Existing
inventories need one successful scan to populate the new icon data.

Morph keyframes use every stored key. Playback interprets frame length as
60 Hz display frames and applies the icon's animation speed, following the
[ps2vmc-tool renderer's timing convention](https://github.com/bucanero/ps2vmc-tool/blob/main/web-ps2/README.md#notes-on-the-3d-icons).
Rendering uses
the shared CPU rasterizer with one shared camera frame across all shapes;
Pillow encodes the GIF. A full animation loop is sampled with a bounded number
of frames (up to 96, reduced for complex models). Static models stay static;
missing or unsupported models use a neutral placeholder. This is a thumbnail
renderer, not a reproduction of the PS2 browser's lighting or background.

**Animate icons** switches to still images and defaults off for browsers with
`prefers-reduced-motion`. Background inventory refreshes preserve unchanged
table markup so icon animations and open detail sections do not restart.
Rendered cache filenames and image URLs include a renderer revision so fixes
to decoding or playback regenerate earlier cached placeholders and animations.
The PS2-inspired theme uses transparent GIF backgrounds. Transparency follows
the rasterizer's geometry coverage, preserving opaque model colours even when
they match the old background. The shared CPU renderer supplies the GIF frames and transparency masks.

## Local HTTP contract

API bodies and responses are JSON, except icon responses (`image/gif`). Mutations require
`Content-Type: application/json`, `X-Save-Manager: 1`, and an object body
(use `{}` when no fields are needed). A supplied `Origin` must match `Host`.
HTTP Basic authentication applies when `SAVE_MANAGER_TOKEN` is configured.

| Method | Path | Body / result |
| --- | --- | --- |
| GET | `/healthz` | Public liveness/database check; `{status: "ok"}` |
| GET | `/api/inventory` | Coherent snapshot: devices, locations, cards, saves, internal `slots`, latest 50 jobs and transfers |
| GET | `/api/icons/{sha256}.gif` | Authenticated, cached GIF from the scanned icon model; `?static=1` returns a still; `placeholder` gives a neutral icon |
| POST | `/api/devices` | `name`, `root_path`; optional `kind`, `poll_seconds` (0 means manual), `transport` (`filesystem`/`ftp`), `ftp_host`, `ftp_port`, `ftp_username`, `ftp_password`; 201 device |
| PUT | `/api/devices/{id}` | Update device fields; omitted `ftp_password` keeps the saved password; changing connection requires fresh scans; responses omit the password and expose `has_ftp_password` |
| POST | `/api/devices/{id}/locations` | `name`, `path` (relative to device root); optional `backend` (`ps1`/`ps2`, default `ps2`), `suffix` (or `*`), `writable`, `marker`; 201 location |
| PUT | `/api/devices/{id}/locations/{location}` | Update `name`, `writable`, `marker`; path/platform/format stay attached to their history |
| DELETE | `/api/devices/{id}/locations/{location}` | Remove registration and inventory from active views; retain disk files, backups and history; cancel previews and pause affected sync groups |
| POST | `/api/devices/{id}/scan` | `{}`; 202 queued or already active job |
| POST | `/api/transfers/preview` | `save_id`, `destination_card_id`, optional `conflict`: `stop`, `keep_both`, `replace`; internal slot copy additionally requires `source_slot`, `destination_slot` (1–12), supports only `stop`/`replace`; 201 preview |
| POST | `/api/transfers/{id}/apply` | `cards_idle: true`; 200 completed transfer with backup path or null for identical content |
| POST | `/api/transfers/{id}/cancel` | `{}`; cancel an unused preview |
| POST | `/api/sync/groups` | `directory`, `card_ids` (2–32 writable cards); configure or replace a sync group |
| PUT | `/api/sync/groups/{id}` | `enabled`: pause/resume a configured group |
| POST | `/api/sync/groups/{id}/resolve` | `source_card_id`: explicitly select a stable source copy |
| POST | `/api/sync/groups/{id}/retry` | `{}`; scan now and retry blocked updates |

Failures return `{ "error": "explanation" }`: 400 invalid input, conflict or
expired/stale preview; 401 authentication; 403 origin/header rejection;
404 unknown resource; 409 filesystem unavailable; 500 unexpected failure.
Inspect the queued job's status in `/api/inventory`; HTTP 202 does not mean
the scan has succeeded. A transfer that fails after preview records its
error in transfer history. IDs are server-owned: requests cannot supply
arbitrary source or destination filenames for transfers.

Schema version 10 uses `PRAGMA user_version`; older inventories migrate
automatically, including per-location platforms, platform-specific sync groups,
per-card baselines and stable observations.
Existing inventory/history is retained. A newer database is rejected.
SQLite has foreign keys, WAL journaling, per-request connections, atomic
location inventory commits, and a unique active scan per device.

## Scope and next work

This version supports mounted **PS1 raw 128 KiB** and **PS2 .ps2 / .bin** cards,
including raw/ECC PS2 handling, plus read-only FTP acquisition and PSP `.vmp`
sources. VMP signing/writes, other headered PS1 containers, native PSP/Vita/PS3
save formats and FTP uploads remain future work.

## Verification

```bash
python -m unittest discover -s tests
node --check save_web/static/app.js
docker compose config --quiet
docker build -t save-manager:local .
```

`tests/test_web.py` uses synthetic card images to exercise persistence,
offline/error/empty/partial scans, observed content versions, polling
deduplication, authenticated HTTP behavior, stale-card rejection, preview
lifecycle, transfer backup, unrelated-save preservation, and rescan queuing.
