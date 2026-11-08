"""Group card saves by game serial, keeping every slot and card copy."""

from pathlib import Path
import re


SERIAL = re.compile(r'(S[A-Z]{3})[-_]?(\d{3})[._-]?(\d{2})', re.IGNORECASE)


def game_id(name):
    match = SERIAL.search(name)
    if match:
        return match[1].upper() + '-' + match[2] + match[3]
    # Never merge unrelated entries just because their serial is unknown.
    return name


def disc_titles(vmc_dir):
    """Use OPL disc filenames as optional display titles; never read the ISOs."""
    titles = {}
    for folder in ('DVD', 'CD'):
        directory = Path(vmc_dir).parent / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in ('.iso', '.zso'):
                continue
            match = SERIAL.match(path.stem)
            if match:
                title = path.stem[match.end():].lstrip('._- ')
                if title:
                    titles.setdefault(game_id(path.stem), title)
    return titles


def fallback_title(record):
    title = record['title'] or record['save']
    # Remove common slot/location suffixes from the game row only. The detail
    # panes always retain the complete, unmodified save title.
    return re.split(r'\s*[\[<]\d+[\]>]|/\d{2}(?:\s|$)', title, maxsplit=1)[0].strip() or title


def group_games(emulator, vmc, titles=None):
    titles = titles or {}
    games = {}
    for side, records in (('Emulator', emulator), ('VMC', vmc)):
        for record in records:
            serial = game_id(record['save'])
            game = games.setdefault(serial, {'id': serial, 'Emulator': [], 'VMC': []})
            game[side].append(record)
    for serial, game in games.items():
        records = game['Emulator'] + game['VMC']
        game['title'] = titles.get(serial) or fallback_title(records[0])
        game['present'] = ('Both' if game['Emulator'] and game['VMC']
                           else 'Emulator only' if game['Emulator'] else 'VMC only')
        game['icon'] = next((r['icon'] for r in records if r.get('icon')), None)
        game['search'] = ' '.join([serial, game['title']] +
                                  [r[key] for r in records for key in ('save', 'title', 'card')]).casefold()
        for side in ('Emulator', 'VMC'):
            game[side].sort(key=lambda record: (record['save'], record['card']))
    return sorted(games.values(), key=lambda game: (game['title'].casefold(), game['id']))
