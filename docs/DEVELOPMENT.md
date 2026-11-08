# Development

`save_web/` owns the browser UI, WSGI API, SQLite inventory and scan scheduler.
`save_core/` provides shared card scanning, copy/merge, capacity,
inspection and selection logic. `game_plugins/` provides byte-only game slot
adapters. `vendor/mymcplusplus/` is the patched third-party reader.

## Run and check

```bash
git submodule update --init --recursive
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/save-manager
.venv/bin/python -m unittest discover -s tests
node --check save_web/static/app.js
docker compose config --quiet
```

Tests run without a display server. They use temporary synthetic cards and
the upstream public fixture archive, never live mounts or private saves.
CI tests Python 3.10, 3.13 and 3.14, builds distributions, and checks Docker.

## Build

```bash
.venv/bin/python -m pip install build
.venv/bin/python -m build
```

Check the installed wheel from outside the checkout so source imports cannot
mask missing files. Version comes from `save_core/app_info.py`. Keep the vendored
reader's version separate. Release archives must exclude private experiments,
credentials, database/backups, caches, Git metadata and build output.

The standalone root repository uses `main` and has no upstream remote. The
reader is a submodule pinned to our fork's `vendor-local` commit. CI checks
out submodules recursively. Use `git -C vendor/mymcplusplus` to work on the
reader; after pushing its changes, stage the new gitlink in the app repository.
The original history/backups remain in the local submodule Git metadata under
`.git/modules/vendor/mymcplusplus/`. See [vendor notes](../vendor/README.md).

Shared backend inspection, selection and FFX export helpers remain usable
from Python. The Tk frontend and its GUI-only modules/tests have been removed.
See [WEB.md](WEB.md) for API, data and container behavior.
