"""Storage contracts shared by card images and standalone game-save files."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from save_core.save_copy import CopyPlan


@dataclass(frozen=True)
class SaveFile:
    name: str
    data: bytes
    metadata: object = None


@dataclass(frozen=True)
class SaveBundle:
    """Game files with opaque metadata preserved by their storage backend."""

    save_id: str
    files: tuple[SaveFile, ...]
    metadata: object = None

    def __post_init__(self):
        if not self.save_id or len({file.name for file in self.files}) != len(self.files):
            raise ValueError('Save identity and unique file names are required.')

    def file_data(self) -> dict[str, bytes]:
        return {file.name: file.data for file in self.files}


@dataclass(frozen=True)
class BackendCapabilities:
    capacity: bool = False
    icons: bool = False
    delete: bool = False
    file_updates: bool = False


class SaveBackend(Protocol):
    """Backends plan changes; shared application code backs up and applies them.

    A standalone SRAM file exposes one save bundle. Internal game slots remain
    the responsibility of game plugins, independent of the storage format.
    """

    backend_id: str
    suffixes: tuple[str, ...]
    capabilities: BackendCapabilities

    def snapshot_location(self, location: Mapping[str, Any]) -> list[dict]: ...

    def read_save(self, path: Path, save_id: str) -> SaveBundle: ...

    def validate(self, data: bytes, name: str | Path) -> None: ...

    def plan_copy(self, source: Path, record: Mapping[str, Any], destination: Path,
                  conflict: str = 'stop') -> 'CopyPlan': ...

    def plan_delete(self, path: Path, record: Mapping[str, Any]) -> 'CopyPlan': ...

    def plan_slot_copy(self, source: Path, record: Mapping[str, Any], source_slot: int,
                       destination: Path, destination_slot: int,
                       conflict: str = 'stop') -> 'CopyPlan': ...
