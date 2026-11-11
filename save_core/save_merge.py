"""Plan a game's save union and export it to a new, verified memory card.

Source images are only opened with rb. Export never replaces an existing
file and never writes a source card or a RetroArch cloud-sync manifest.
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path

from .backends.base import SaveBundle


@dataclass(frozen=True)
class InputCard:
    path: Path
    sha256: str


@dataclass(frozen=True)
class MergeRow:
    side: str
    card: str
    source_slot: int
    destination_slot: int
    duplicate: bool
    playtime: str


@dataclass(frozen=True)
class MergePlan:
    game_id: str
    title: str
    inputs: tuple
    rows: tuple
    saves: tuple


def digest(data):
    return hashlib.sha256(data).hexdigest()


def files(save):
    if isinstance(save, SaveBundle):
        return save.file_data()
    from .backends.ps2.bundles import from_native
    return from_native(save).file_data()


def stable_read(path):
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f'{path.name} changed while reading. Refresh and try again.')
    return data


def plan_merge(game, directories):
    from .backends.ps2.merge import plan_merge as plan
    return plan(game, directories)


def _build_card(plan, with_ecc):
    from .backends.ps2.merge import _build_card as build
    return build(plan, with_ecc)


def export_merge(plan, output, with_ecc=True):
    from .backends.ps2.merge import export_merge as export
    return export(plan, output, with_ecc)
