"""Read a selected save copy and pass its payload to a game plugin."""

from pathlib import Path

from .scanner import date_text, entries
from game_plugins import Breakdown, Section, plugin_for, slot_data
from .native.ps2mc import ps2mc
from .native.ps2mc_dir import mode_is_file


def inspect_save(card_path, record, side):
    path = Path(card_path)
    before = path.stat()
    with path.open('rb') as image:
        card = ps2mc(image)
        try:
            directory = '/' + record['save']
            contents = entries(card, directory)
            files = Section('Files in this save', tuple(
                (entry[8].decode('ascii'), f'{entry[2]:,} bytes' if mode_is_file(entry[0]) else 'Directory')
                for entry in contents))
            plugin = plugin_for(record['save'])
            if callable(getattr(plugin, 'slots', None)) and not callable(getattr(plugin, 'decode', None)):
                data = {}
                if callable(getattr(plugin, 'payload_names', None)):
                    names = plugin.payload_names(record['save'])
                elif callable(getattr(plugin, 'select_files', None)):
                    names = tuple(entry[8].decode('ascii') for entry in contents if mode_is_file(entry[0]))
                else:
                    names = (plugin.payload_name(record['save']),)
                for name in names:
                    if not any(entry[8] == name.encode('ascii') and mode_is_file(entry[0]) for entry in contents):
                        continue
                    payload = card.open(directory + '/' + name, 'rb')
                    try:
                        data[name] = payload.read()
                    finally:
                        payload.close()
                slots = plugin.slots(record['save'], slot_data(plugin, record['save'], data))
                decoded = Breakdown('Save slots', (Section('Slots', tuple(
                    (f"Slot {slot['number']}", slot['label'] or slot['error'] or slot['state'])
                    for slot in slots)),), ())
            elif plugin:
                name = plugin.payload_name(record['save'])
                entry = next((e for e in contents if e[8] == name.encode('ascii') and mode_is_file(e[0])), None)
                if entry is None:
                    decoded = Breakdown('Game payload missing', (), ('The expected game-data file is absent.',))
                elif entry[2] != plugin.payload_size:
                    decoded = Breakdown('Unsupported save payload', (),
                                        (f'Expected {plugin.payload_size:,} bytes; found {entry[2]:,}. '
                                         'No progress fields were decoded.',))
                else:
                    payload = card.open(directory + '/' + name, 'rb')
                    try:
                        decoded = plugin.decode(record['save'], payload.read())
                    finally:
                        payload.close()
            else:
                decoded = Breakdown('No game decoder available yet', (),
                                    ('File information is available. This game’s progress data has not been decoded.',))
        finally:
            card.close()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('The memory card changed during inspection. Refresh and select the save again.')
    source = Section('Selected copy', (
        ('Source', side), ('Memory card', path.name), ('Save directory', record['save']),
        ('Title', record['title'] or 'Not available'), ('Recorded update', date_text(record)),
    ))
    return Breakdown(decoded.status, (source, *decoded.sections, files), decoded.notes)
