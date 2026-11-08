#!/usr/bin/env python3
"""List saves and their stored update dates without writing to memory cards."""

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import unicodedata

from vendor.mymcplusplus.mymcplusplus.ps2iconsys import IconSys
from vendor.mymcplusplus.mymcplusplus.ps2mc import ps2mc
from vendor.mymcplusplus.mymcplusplus.ps2mc_dir import mode_is_dir, mode_is_file
from .card_usage import card_capacity
from .app_info import APP_NAME, __version__


class UnformattedCard(ValueError):
    """An entirely erased/zero-filled card, not an unreadable formatted save."""


def erased_image(image):
    """Recognize only completely blank images with a supported card size."""
    try:
        image.seek(0)
        fill = image.read(1)
        if fill not in (b'\xff', b'\0'):
            return False
        image.seek(0, 2)
        size = image.tell()
        valid_sizes = {mb * 1024 * 1024 * stride // 512
                       for mb in (8, 16, 32, 64) for stride in (512, 528)}
        if size not in valid_sizes:
            return False
        image.seek(0)
        while chunk := image.read(256 * 1024):
            if chunk != fill * len(chunk):
                return False
        return True
    finally:
        image.seek(0)


def card_time(tod):
    """Keep the card's clock as recorded; its timezone is unknown."""
    second, minute, hour, day, month, year = tod
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def entries(card, path):
    directory = card.dir_open(path, 'rb')
    try:
        return [entry for entry in directory
                if entry[8] not in (b'.', b'..')
                and (mode_is_dir(entry[0]) or mode_is_file(entry[0]))]
    finally:
        directory.close()


def updated(card, path, entry, ancestors=frozenset()):
    """Include contained files: the save directory date alone can be stale."""
    dates = [card_time(entry[6])]
    if mode_is_dir(entry[0]):
        cluster = entry[4]
        if cluster in ancestors:
            raise ValueError('Directory cycle in ' + path)
        for child in entries(card, path):
            child_path = path + '/' + child[8].decode('ascii')
            dates.append(updated(card, child_path, child, ancestors | {cluster}))
    return max((date for date in dates if date is not None), default=None)


def read_card(path, usage=None):
    records = []
    # The backing image itself is read-only, including during library cleanup.
    with path.open('rb') as image:
        if erased_image(image):
            raise UnformattedCard('Card is completely blank and unformatted: ' + str(path))
        card = ps2mc(image)
        try:
            if usage is not None:
                usage.update(card_capacity(card))
            for entry in entries(card, '/'):
                if not mode_is_dir(entry[0]):
                    continue
                save = entry[8].decode('ascii')
                directory = '/' + save
                icon = card.get_icon_sys(directory)
                icon_sys = IconSys(icon) if icon else None
                title = ' '.join(icon_sys.get_title('utf-8')) if icon_sys else ''
                record = {
                    'save': save,
                    'title': ' '.join(unicodedata.normalize('NFKC', title).split()),
                    'card': path.name,
                    'updated': updated(card, directory, entry),
                }
                records.append(record)
        finally:
            card.close()
    return records


def scan(directory, suffix, progress=None, skipped=None, card_info=None):
    if not directory.is_dir():
        raise ValueError('Card directory does not exist: ' + str(directory))
    paths = sorted(p for p in directory.iterdir()
                   if p.is_file() and p.suffix.lower() == suffix)
    if not paths:
        raise ValueError('No ' + suffix + ' cards in ' + str(directory))
    records = []
    for index, path in enumerate(paths, 1):
        if progress:
            progress(path, index, len(paths))
        try:
            before = path.stat()
            blank = False
            usage = {'card': path.name, 'path': path, 'formatted': False}
            try:
                card_records = read_card(path, usage=usage if card_info is not None else None)
            except UnformattedCard:
                blank = True
                card_records = []
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('Card changed during the scan; retry when it is idle')
            for record in card_records:
                record['card_version'] = (after.st_size, after.st_mtime_ns)
            records.extend(card_records)
            if card_info is not None:
                card_info.append(usage)
            if blank and skipped is not None:
                skipped.append(path)
        except Exception as error:
            raise ValueError(f'Cannot read {path}: {error}') from error
    return paths, records


def date_text(record):
    date = record['updated']
    return date.isoformat(sep=' ') if date else 'unknown'


def compare(emulator, vmc):
    groups = defaultdict(lambda: {'Emulator': [], 'VMC': []})
    for side, records in [('Emulator', emulator), ('VMC', vmc)]:
        for record in records:
            groups[record['save']][side].append(record)
    rows = []
    for save, sides in sorted(groups.items()):
        all_records = sides['Emulator'] + sides['VMC']
        rows.append({
            'Save directory': save,
            'Title': ' / '.join(dict.fromkeys(r['title'] for r in all_records if r['title'])),
            'Present on': 'Both' if all(sides.values()) else ('Emulator only' if sides['Emulator'] else 'VMC only'),
            'Emulator updated': '; '.join(date_text(r) for r in sides['Emulator']),
            'Emulator card': '; '.join(r['card'] for r in sides['Emulator']),
            'VMC updated': '; '.join(date_text(r) for r in sides['VMC']),
            'VMC card': '; '.join(r['card'] for r in sides['VMC']),
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=f'{APP_NAME} {__version__}')
    parser.add_argument('--emulator-dir', '--lrps2-dir', dest='emulator_dir', type=Path, required=True,
                        help='Directory containing PCSX2/LRPS2 .ps2 cards')
    parser.add_argument('--vmc-dir', type=Path, required=True, help='Directory containing PS2SMB .bin cards')
    parser.add_argument('--csv', type=Path, help='Write a side-by-side CSV report')
    args = parser.parse_args()
    skipped = []
    try:
        emulator_paths, emulator = scan(args.emulator_dir, '.ps2', skipped=skipped)
        vmc_paths, vmc = scan(args.vmc_dir, '.bin', skipped=skipped)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
    rows = compare(emulator, vmc)
    if args.csv and any(args.csv.resolve() == path.resolve()
                        or (args.csv.exists() and args.csv.samefile(path))
                        for path in emulator_paths + vmc_paths):
        parser.exit(1, 'CSV output must not overwrite a memory card.\n')
    print(f'Emulator: {len(emulator)} saves across {len(emulator_paths)} cards ({args.emulator_dir})')
    print(f'VMC: {len(vmc)} saves across {len(vmc_paths)} cards ({args.vmc_dir})')
    for path in skipped:
        print(f'Skipped unformatted card (no saves): {path}')
    for status in ('Both', 'Emulator only', 'VMC only'):
        print(f'{status}: {sum(row["Present on"] == status for row in rows)} save directories')
    print('\nDates are the newest stored modification time in each save directory or its contents.')
    print('They use the card clock with no timezone conversion; matching dates do not prove equal contents.')
    print('Saves are matched by exact internal directory name. System/configuration entries are included.')
    for row in rows:
        print('\n' + row['Save directory'] + (' — ' + row['Title'] if row['Title'] else ''))
        for side in ('Emulator', 'VMC'):
            records = [r for r in (emulator if side == 'Emulator' else vmc) if r['save'] == row['Save directory']]
            print('  ' + side + ': ' + ('; '.join(date_text(r) + ' [' + r['card'] + ']' for r in records) or '—'))
    if args.csv:
        columns = ['Save directory', 'Title', 'Present on', 'Emulator updated', 'Emulator card', 'VMC updated', 'VMC card']
        with args.csv.open('w', newline='', encoding='utf-8') as output:
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == '__main__':
    main()
