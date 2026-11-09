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
    from .dothack import DotHackInfectionUS
    from .katamari import KatamariDamacyUS
    from .steambot import SteambotChroniclesUS
    from .thps4 import THPS4CareerUS
    for plugin in (FFXInternational, ArTonelicoUS, DotHackInfectionUS, KatamariDamacyUS, SteambotChroniclesUS, THPS4CareerUS):
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


def slot_data(plugin, save_directory, save_files):
    """Select a plugin's single payload or its named set of slot/index files."""
    if callable(getattr(plugin, 'payload_names', None)):
        return {name: save_files.get(name, b'') for name in plugin.payload_names(save_directory)}
    return save_files.get(plugin.payload_name(save_directory), b'')
