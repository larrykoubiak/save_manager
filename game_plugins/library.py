"""Device-independent slot extraction. Plugins own all game-format offsets."""

from dataclasses import dataclass, replace

from save_core.save_games import game_serial

from . import plugin_for


@dataclass(frozen=True)
class LibrarySlot:
    key: str
    number: int | None
    label: str
    files: dict[str, bytes]
    playtime_seconds: int | None = None


def extract_slots(backend, bundle, title):
    plugin = plugin_for(bundle.save_id, backend)
    extract = getattr(plugin, 'library_slots', None)
    if extract:
        slots = extract(bundle)
        game_key = game_serial(bundle.save_id) or bundle.save_id
        return game_key, plugin.name, [replace(slot,key=bundle.save_id+':'+slot.key) for slot in slots]
    # Unknown layouts and named careers retain their complete file set.
    return game_serial(bundle.save_id) or bundle.save_id, plugin.name if plugin else title or bundle.save_id, [
        LibrarySlot(bundle.save_id+':whole', None, title or 'Whole save', bundle.file_data())]
