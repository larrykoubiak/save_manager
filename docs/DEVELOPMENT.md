# Development

`save_web/` owns the browser UI, WSGI API, SQLite inventory and scan scheduler.
`save_core/` provides shared card scanning, copy/merge, capacity,
inspection and selection logic. `game_plugins/` provides byte-only game slot
adapters. `save_core/backends/` owns storage implementations. PS2-specific
filesystem, capacity, mutation and icon code lives in its `ps2/` package.
Game transformations receive neutral `SaveBundle` objects, preserving opaque
backend metadata. Shared code handles backups, atomic replacement and sync.

## Run and check

```bash
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/save-manager
.venv/bin/python -m unittest discover -s tests
node --check save_web/static/app.js
docker compose config --quiet
```

Tests run without a display server. They use temporary synthetic cards and
never live mounts or private saves.
CI tests Python 3.10, 3.13 and 3.14, builds distributions, and checks Docker.

## Build

```bash
.venv/bin/python -m pip install build
.venv/bin/python -m build
```

Check the installed wheel from outside the checkout so source imports cannot
mask missing files. Version comes from `save_core/app_info.py`. Release archives must exclude private experiments,
credentials, database/backups, caches, Git metadata and build output.

Storage backends implement `SaveBackend` from `save_core/backends/base.py`;
register implementations in `save_core/backends/__init__.py`. A standalone
SRAM file can expose one save bundle while a game plugin interprets its slots.
The PS2 backend contains adapted mymc-family code with preserved attribution;
see [THIRD_PARTY.md](../THIRD_PARTY.md). No submodule checkout is needed.

Shared backend inspection, selection and FFX export helpers remain usable
from Python. The Tk frontend and its GUI-only modules/tests have been removed.
See [WEB.md](WEB.md) for API, data and container behavior.
