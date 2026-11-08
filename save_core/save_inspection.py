"""Read a selected save copy and pass its payload to a game plugin."""

from pathlib import Path

from .compare_saves import date_text, entries
from game_plugins import Breakdown, Section, plugin_for
from vendor.mymcplusplus.mymcplusplus.ps2mc import ps2mc
from vendor.mymcplusplus.mymcplusplus.ps2mc_dir import mode_is_file


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
            if plugin:
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
