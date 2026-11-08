"""Plan a game's save union and export it to a new, verified memory card.

Source images are only opened with rb. Export never replaces an existing
file and never writes a source card or a RetroArch cloud-sync manifest.
"""

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path

from game_plugins import merger_for_game
from vendor.mymcplusplus.mymcplusplus.ps2mc import ps2mc


@dataclass(frozen=True)
class InputCard:
    path: Path
    sha256: str


@dataclass(frozen=True)
class MergeRow:
    side: str
    card: str
    source_slot: int
    destination_slot: int
    duplicate: bool
    playtime: str


@dataclass(frozen=True)
class MergePlan:
    game_id: str
    title: str
    inputs: tuple
    rows: tuple
    saves: tuple


def digest(data):
    return hashlib.sha256(data).hexdigest()


def files(save):
    return {entry[8].decode('ascii'): data for entry, data in save}


def stable_read(path):
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f'{path.name} changed while reading. Refresh and try again.')
    return data


def plan_merge(game, directories):
    """Capture selected saves, verify them, and allocate one slot per payload."""
    plugin = merger_for_game(game['id'])
    if plugin is None:
        raise ValueError('Merging is not supported for this game yet.')
    inputs, opened, saves, rows, assigned = {}, {}, [], [], {}
    occupied = set()
    try:
        for side in ('Emulator', 'VMC'):
            for record in game[side]:
                path = (Path(directories[side]) / record['card']).resolve()
                if record.get('card_version'):
                    stat = path.stat()
                    if (stat.st_size, stat.st_mtime_ns) != record['card_version']:
                        raise ValueError(f'{path.name} changed since the scan. Refresh and preview again.')
                if path not in opened:
                    data = stable_read(path)
                    image = io.BytesIO(data)
                    image.name = str(path)
                    opened[path] = (ps2mc(image), image)
                    inputs[path] = InputCard(path, digest(data))
                card, _ = opened[path]
                name = record['save']
                if not plugin.supports(name):
                    raise ValueError('A save uses an unsupported game layout: ' + name)
                source = card.export_save_file('/' + name)
                payload = files(source)[plugin.payload_name(name)]
                decoded = plugin.decode(name, payload)
                sections = {section.title: dict(section.fields) for section in decoded.sections}
                if 'Progress' not in sections:
                    raise ValueError(f'{record["card"]}: {name}: {decoded.status}')
                fingerprint = digest(payload)
                source_slot = plugin.slot_number(name)
                duplicate = fingerprint in assigned
                if duplicate:
                    destination = assigned[fingerprint]
                else:
                    destination = source_slot if source_slot not in occupied and source_slot <= 99 else next(
                        (slot for slot in range(1, 100) if slot not in occupied), None)
                    if destination is None:
                        raise ValueError('More than 99 distinct saves; a single FFX card cannot contain this union.')
                    # Validation also runs for a save that keeps its slot number.
                    prepared = plugin.prepare_slot(source, destination)
                    saves.append(prepared)
                    assigned[fingerprint] = destination
                    occupied.add(destination)
                rows.append(MergeRow(side, record['card'], source_slot, destination,
                                     duplicate, sections['Progress']['Playtime']))
        if not saves:
            raise ValueError('No supported saves were found for the selected game.')
        return MergePlan(game['id'], game['title'], tuple(inputs.values()), tuple(rows), tuple(saves))
    finally:
        for card, image in opened.values():
            card.close()
            image.close()


def _build_card(plan, with_ecc):
    image = io.BytesIO()
    image.name = '<merged memory card>'
    card = ps2mc(image, params=(with_ecc, 512, 16, 16384))
    try:
        for save in plan.saves:
            required = sum((len(data) + 1023) // 1024 * 1024 for _, data in save) + 5 * 1024
            if card.get_free_space() < required:
                raise ValueError('The merged saves do not fit in an 8 MB memory card.')
            card.import_save_file(save, False)
        if not card.check():
            raise ValueError('The generated card failed filesystem validation.')
    finally:
        card.close()
    data = image.getvalue()
    image.close()
    check_image = io.BytesIO(data)
    check_image.name = '<verified merged memory card>'
    check_card = ps2mc(check_image)
    try:
        for expected in plan.saves:
            name = expected.get_directory()[8].decode('ascii')
            actual = check_card.export_save_file('/' + name)
            if files(actual) != files(expected):
                raise ValueError('The generated save does not match its planned contents: ' + name)
        if not check_card.check():
            raise ValueError('The reopened merged card failed filesystem validation.')
    finally:
        check_card.close()
        check_image.close()
    return data


def export_merge(plan, output, with_ecc=True):
    """Create a brand-new local card; source changes invalidate the preview."""
    output = Path(output).expanduser()
    extension = '.ps2' if with_ecc else '.bin'
    if output.suffix.lower() != extension:
        raise ValueError(f'Use {extension} for the selected output format.')
    if output.exists() or output.is_symlink():
        raise ValueError('Choose a new filename. Existing files are never overwritten.')
    for source in plan.inputs:
        if digest(stable_read(source.path)) != source.sha256:
            raise ValueError(f'{source.path.name} changed since the preview. Refresh and preview again.')
    data = _build_card(plan, with_ecc)
    created = False
    try:
        with output.open('xb') as handle:
            created = True
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if digest(output.read_bytes()) != digest(data):
            raise ValueError('The exported card failed read-back verification.')
    except BaseException:
        if created:
            output.unlink()
        raise
    return output
