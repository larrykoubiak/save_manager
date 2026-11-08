"""Choose versions for consolidation without modifying source save objects."""

from dataclasses import dataclass

from .compare_saves import card_time
from game_plugins import plugin_for


@dataclass(frozen=True)
class Selection:
    kept: tuple
    archived: tuple
    notes: tuple


def recorded_update(item):
    """Newest internal modification date, including files and duplicate copies."""
    save = item['save']
    dates = [card_time(save.get_directory()[6])]
    dates.extend(card_time(entry[6]) for entry, _ in save)
    dates.extend(card_time(source[key]) for source in item.get('sources', ())
                 for key in ('updated', 'latest_updated') if source.get(key))
    return max((date for date in dates if date is not None), default=None)


def select_versions(items, preferred_sources=None):
    """Use newest dates for non-plugin conflicts; preserve plugin versions.

    Explicit source choices take priority. Distinct directory names represent
    independent saves/slots and are never discarded just for sharing a game ID.
    Tied or missing dates retain all newest candidates instead of guessing.
    Callers archive excluded versions before publishing consolidated cards.
    """
    preferred_sources = preferred_sources or {}
    groups = {}
    for item in items:
        groups.setdefault(item['name'], []).append(item)
    kept, archived, notes = [], [], []
    for name, copies in groups.items():
        preferred = preferred_sources.get(name)
        if preferred:
            winners = [item for item in copies if any(s['side'] == preferred for s in item['sources'])]
            if len(winners) != 1:
                raise ValueError(f'Expected one distinct {preferred} version of {name}; found {len(winners)}.')
        elif len(copies) == 1 or plugin_for(name):
            winners = copies
        else:
            timestamps = [recorded_update(item) for item in copies]
            newest = max((date for date in timestamps if date is not None), default=None)
            winners = [item for item, date in zip(copies, timestamps) if date == newest]
            # An undated version might be newer; retain it rather than discard it.
            winners += [item for item, date in zip(copies, timestamps) if date is None and newest is not None]
            if len(winners) > 1:
                notes.append(f'{name}: tied or missing dates; retained {len(winners)} versions for review.')
        kept.extend(winners)
        archived.extend(item for item in copies if all(item is not winner for winner in winners))
    return Selection(tuple(kept), tuple(archived), tuple(notes))
