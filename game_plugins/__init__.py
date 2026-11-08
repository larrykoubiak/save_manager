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


def plugin_for(save_directory):
    from .ffx import FFXInternational
    from .ar_tonelico import ArTonelicoUS
    for plugin in (FFXInternational, ArTonelicoUS):
        if plugin.supports(save_directory):
            return plugin
    return None


def slot_number(save_directory):
    plugin = plugin_for(save_directory)
    read_slot = getattr(plugin, 'slot_number', None)
    return read_slot(save_directory) if read_slot else None


def merger_for_game(game_id):
    from .ffx import FFXInternational
    for plugin in (FFXInternational,):
        if game_id in plugin.merge_game_ids:
            return plugin
    return None
