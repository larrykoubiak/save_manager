"""Game adapters for save slots, byte-only transformations and optional inspection."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    title: str
    fields: tuple


@dataclass(frozen=True)
class Breakdown:
    status: str
    sections: tuple
    notes: tuple = ()


def plugins_for(backend_id):
    if backend_id == 'ps2':
        from .ps2 import PLUGINS
    elif backend_id == 'ps1':
        from .ps1 import PLUGINS
    else:
        return ()
    return PLUGINS


def plugin_for(save_directory, backend_id='ps2'):
    return next((plugin for plugin in plugins_for(backend_id) if plugin.supports(save_directory)), None)


def slot_number(save_directory, backend_id='ps2'):
    plugin = plugin_for(save_directory, backend_id)
    read_slot = getattr(plugin, 'slot_number', None)
    return read_slot(save_directory) if read_slot else None


def merger_for_game(game_id, backend_id='ps2'):
    return next((plugin for plugin in plugins_for(backend_id)
                 if game_id in getattr(plugin, 'merge_game_ids', ())), None)


def slot_data(plugin, save_directory, save_files):
    """Select a plugin's single payload or its named set of slot/index files."""
    if callable(getattr(plugin, 'payload_names', None)):
        return {name: save_files.get(name, b'') for name in plugin.payload_names(save_directory)}
    return save_files.get(plugin.payload_name(save_directory), b'')
