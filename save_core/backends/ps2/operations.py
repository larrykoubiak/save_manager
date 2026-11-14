"""PS2 save mutations staged and verified entirely in memory."""

import os
from pathlib import Path

from .scanner import entries, erased_image
from .bundles import from_native, to_native
from .native.ps2mc import ps2mc
from .native.ps2mc_dir import mode_is_dir, mode_is_file
from game_plugins import plugin_for, slot_data
from save_core.save_copy import CopyPlan, image_file
from save_core.save_merge import digest, files, stable_read


def tree_snapshot(card, path='/', seen=frozenset()):
    """Logical files and metadata, excluding allocation details and root dates."""
    result = {}
    for entry in entries(card, path):
        name = path.rstrip('/') + '/' + entry[8].decode('ascii')
        # Mode, attributes, creation/modification dates and names must survive.
        metadata = tuple(entry[i] for i in (0, 1, 3, 6, 7, 8))
        if mode_is_dir(entry[0]):
            if entry[4] in seen:
                raise ValueError('Directory cycle in ' + name)
            result[name] = (metadata, None)
            result.update(tree_snapshot(card, name, seen | {entry[4]}))
        else:
            handle = card.open(name, 'rb')
            try:
                data = handle.read()
            finally:
                handle.close()
            if len(data) != entry[2]:
                raise ValueError('Truncated file in ' + name)
            result[name] = (metadata, data)
    return result


def simple_save(card, name):
    contents = entries(card, '/' + name)
    if not contents or any(not mode_is_file(e[0]) for e in contents):
        raise ValueError('Only non-empty saves containing regular files can be copied; nested directories are unsupported.')
    return card.export_save_file('/' + name)


def plan_delete(path, record):
    """Remove one save directory in memory and verify every retained entry."""
    path = Path(path).resolve()
    name = record['save']
    if not isinstance(name, str) or not name or name in ('.', '..') or '/' in name or '\\' in name:
        raise ValueError('Choose one save directory to delete.')
    version = record.get('card_version')
    def check_version():
        current = path.stat()
        if version and (current.st_size, current.st_mtime_ns) != version:
            raise ValueError('Card changed since the scan. Scan and preview again.')
    check_version()
    original = stable_read(path)
    check_version()
    with image_file(original, path) as image:
        if erased_image(image):
            raise ValueError('Card is unformatted.')
        card = ps2mc(image)
        try:
            if not card.check():
                raise ValueError('Card filesystem check failed.')
            before = tree_snapshot(card)
            root = '/' + name
            if root not in before or before[root][1] is not None:
                raise ValueError('Save directory is missing. Scan and preview again.')
            expected = {key: value for key, value in before.items()
                        if key != root and not key.startswith(root + '/')}
            card.rmdir(root)
            card.flush()
            candidate = image.getvalue()
        finally:
            card.close()
    with image_file(candidate, path) as image:
        card = ps2mc(image)
        try:
            if not card.check() or tree_snapshot(card) != expected:
                raise ValueError('Deletion verification failed; card is unchanged.')
        finally:
            card.close()
    return CopyPlan(path, path, digest(original), digest(original), name, name,
                    'Delete save', candidate, original)


def plan_copy(source, record, destination, conflict='stop'):
    """Build and reopen a complete candidate in memory; no live-card writes."""
    if conflict not in ('stop', 'keep_both', 'replace'):
        raise ValueError('Unknown conflict policy.')
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if os.path.samefile(source, destination):
        raise ValueError('Choose a different destination card.')
    if record.get('card_version'):
        current = source.stat()
        if (current.st_size, current.st_mtime_ns) != record['card_version']:
            raise ValueError('Source changed since the scan. Refresh and try again.')
    source_data, original = stable_read(source), stable_read(destination)
    if record.get('card_version'):
        current = source.stat()
        if (current.st_size, current.st_mtime_ns) != record['card_version']:
            raise ValueError('Source changed since the scan. Refresh and try again.')
    warnings = []
    with image_file(source_data, source) as image:
        card = ps2mc(image)
        try:
            if not card.check(allow_lost_clusters=True, warnings=warnings):
                raise ValueError('Source filesystem check failed.')
            save = simple_save(card, record['save'])
            plugin = plugin_for(record['save'])
            # When a decoder exists, require its known checksum/layout checks.
            if callable(getattr(plugin, 'validate_save', None)):
                plugin.validate_save(record['save'], slot_data(plugin, record['save'], files(save)))
            elif callable(getattr(plugin, 'slots', None)):
                slots = plugin.slots(record['save'], slot_data(plugin, record['save'], files(save)))
                if any(slot['state'] == 'invalid' for slot in slots):
                    raise ValueError('Source slot validation failed: invalid save slots')
            elif plugin:
                decoded = plugin.decode(record['save'], files(save)[plugin.payload_name(record['save'])])
                if not any(s.title == 'Progress' for s in decoded.sections):
                    raise ValueError('Source validation failed: ' + decoded.status)
        finally:
            card.close()
    image = image_file(original, destination)
    with image:
        if erased_image(image):
            raise ValueError('Destination is unformatted. Format it in the emulator first; copying never formats cards.')
        card = ps2mc(image)
        name = record['save']
        action = 'Copy'
        try:
            if not card.check():
                raise ValueError('Destination filesystem check failed.')
            before = tree_snapshot(card)
            names = {entry[8].decode('ascii') for entry in entries(card, '/')}
            if name in names:
                existing = simple_save(card, name)
                if files(existing) == files(save):
                    return CopyPlan(source, destination, digest(source_data), digest(original),
                                    record['save'], name, 'Already identical', b'', original, tuple(warnings))
                if conflict == 'stop':
                    raise ValueError('A different save already occupies this directory. Choose keep both (supported games) or replace explicitly.')
                if conflict == 'keep_both':
                    named_adapter = getattr(plugin, 'prepare_copy', None)
                    if callable(named_adapter):
                        save, action = named_adapter(from_native(save), names)
                        save = to_native(save)
                        name = save.get_directory()[8].decode('ascii')
                        if name in names:
                            raise ValueError('Renamed save would overwrite an existing directory.')
                    else:
                        adapter = getattr(plugin, 'prepare_slot', None)
                        if adapter is None:
                            raise ValueError('Keeping both in different slots is not supported for this game.')
                        occupied = {plugin.slot_number(n) for n in names if plugin.supports(n)}
                        slot = next((n for n in range(1, 100) if n not in occupied), None)
                        if slot is None:
                            raise ValueError('No unused FFX slots remain.')
                        save = to_native(adapter(from_native(save), slot))
                        name = save.get_directory()[8].decode('ascii')
                        action = f'Copy to unused slot {slot}'
                else:
                    action = 'Replace existing save'
                    card.rmdir('/' + name)
            required = sum((len(data) + 1023) // 1024 * 1024 for _, data in save) + 5 * 1024
            if card.get_free_space() < required:
                raise ValueError('Not enough free space on the destination card.')
            card.import_save_file(save, False)
            if not card.check():
                raise ValueError('Candidate filesystem check failed.')
        finally:
            card.close()
        candidate = image.getvalue()
    if len(candidate) != len(original):
        raise ValueError('Candidate changed the card size.')
    with image_file(candidate, destination) as image:
        card = ps2mc(image)
        try:
            after = tree_snapshot(card)
            prefix = '/' + name
            other_before = {p: v for p, v in before.items() if p != prefix and not p.startswith(prefix + '/')}
            other_after = {p: v for p, v in after.items() if p != prefix and not p.startswith(prefix + '/')}
            if other_before != other_after:
                raise ValueError('Candidate changed another save or its metadata.')
            if files(simple_save(card, name)) != files(save) or not card.check():
                raise ValueError('Reopened candidate does not match the planned save.')
        finally:
            card.close()
    return CopyPlan(source, destination, digest(source_data), digest(original),
                    record['save'], name, action, candidate, original, tuple(warnings))


def _write_slot_files(card, plugin, directory, source_save, source_payload, source_slot,
                      destination_slot, original, candidate, before):
    """Allow a plugin to add/rename one slot file while protecting its siblings."""
    root = '/' + directory
    old_name = plugin.slot_filename(original, destination_slot)
    new_name = plugin.slot_filename(candidate, destination_slot)
    source_name = plugin.slot_filename(source_payload, source_slot)
    allowed = {old_name, new_name} - {None}
    changed = {name for name in original.keys() | candidate.keys() if original.get(name) != candidate.get(name)}
    if not new_name or not changed <= allowed:
        raise ValueError('Candidate changed files outside the selected slot.')
    for name in candidate:
        if not name or len(name.encode('ascii')) > 32 or any(c in name for c in '/\\\0') or not isinstance(candidate[name], bytes):
            raise ValueError('Invalid slot filename or contents.')
    source_entry = next(entry for entry, _ in source_save if entry[8].decode('ascii') == source_name)
    directory_entry = card.get_dirent(root)
    expected = dict(before)
    for name in original.keys() - candidate.keys():
        card.remove(root + '/' + name)
        del expected[root + '/' + name]
    for name in changed & candidate.keys():
        path = root + '/' + name
        if name in original and len(candidate[name]) != len(original[name]):
            raise ValueError('Candidate changed a slot file size.')
        handle = card.open(path, 'r+b' if name in original else 'wb')
        try:
            handle.write(candidate[name], _set_modified=False)
        finally:
            handle.close()
        if name not in original:
            entry = list(card.get_dirent(path))
            for index in (0,1,3,6,7):
                entry[index] = source_entry[index]
            card.set_dirent(path, entry)
            metadata = tuple(entry[index] for index in (0,1,3,6,7,8))
        else:
            metadata = before[path][0]
        expected[path] = (metadata, candidate[name])
    entry = list(card.get_dirent(root))
    for index in (0,1,3,6,7):
        entry[index] = directory_entry[index]
    card.set_dirent(root, entry)
    return expected


def plan_slot_copy(source, record, source_slot, destination, destination_slot, conflict='stop'):
    """Patch an internal slot in an existing save, preserving all other card data."""
    if conflict not in ('stop', 'replace'):
        raise ValueError('Slot conflicts support stop or explicit replacement.')
    source, destination = Path(source).resolve(), Path(destination).resolve()
    plugin = plugin_for(record['save'])
    if not callable(getattr(plugin, 'copy_slot', None)):
        raise ValueError('Individual internal-slot copying is unsupported for this game.')
    source_data = stable_read(source)
    current = source.stat()
    if record.get('card_version') and record['card_version'] != (current.st_size, current.st_mtime_ns):
        raise ValueError('Source changed since the scan. Refresh and try again.')
    original = source_data if source == destination else stable_read(destination)
    warnings = []
    name = record['save']
    with image_file(source_data, source) as image:
        card = ps2mc(image)
        try:
            if not card.check(allow_lost_clusters=True, warnings=warnings):
                raise ValueError('Source filesystem check failed.')
            source_save = simple_save(card, name)
            source_payload = slot_data(plugin, name, files(source_save))
        finally:
            card.close()
    with image_file(original, destination) as image:
        if erased_image(image):
            raise ValueError('Destination is unformatted. Format it in the emulator first.')
        card = ps2mc(image)
        try:
            if not card.check():
                raise ValueError('Destination filesystem check failed.')
            if name not in {entry[8].decode('ascii') for entry in entries(card, '/')}:
                raise ValueError(f'Destination needs an existing {plugin.name} save. Create one in-game first.')
            before = tree_snapshot(card)
            old_payload = slot_data(plugin, name, files(simple_save(card, name)))
            candidate_payload = plugin.copy_slot(name, source_payload, source_slot, old_payload,
                                                 destination_slot, replace=conflict == 'replace')
            if candidate_payload == old_payload:
                return CopyPlan(source, destination, digest(source_data), digest(original), name, name,
                                'Already identical', b'', original, tuple(warnings), source_slot, destination_slot)
            occupied = plugin.slots(name, old_payload)[destination_slot - 1]['state'] == 'occupied'
            action = (f'Replace slot {destination_slot} with slot {source_slot}' if occupied else
                      f'Copy slot {source_slot} to slot {destination_slot}')
            candidate_files = (candidate_payload if isinstance(candidate_payload, dict) else
                               {plugin.payload_name(name): candidate_payload})
            original_files = old_payload if isinstance(old_payload, dict) else {plugin.payload_name(name): old_payload}
            if getattr(plugin, 'slot_file_changes', False):
                expected = _write_slot_files(card, plugin, name, source_save, source_payload,
                                             source_slot, destination_slot, original_files, candidate_files, before)
            else:
                if candidate_files.keys() != original_files.keys():
                    raise ValueError('Candidate changed the slot file set.')
                for filename, payload in candidate_files.items():
                    if not isinstance(payload, bytes) or len(payload) != len(original_files[filename]):
                        raise ValueError('Candidate changed a slot file size.')
                    if payload == original_files[filename]:
                        continue
                    handle = card.open('/' + name + '/' + filename, 'r+b')
                    try:
                        # Fixed-size overwrites retain allocation and container dates.
                        handle.write(payload, _set_modified=False)
                    finally:
                        handle.close()
                expected = dict(before)
                for filename, payload in candidate_files.items():
                    path = '/' + name + '/' + filename
                    expected[path] = (before[path][0], payload)
            if not card.check():
                raise ValueError('Candidate filesystem check failed.')
        finally:
            card.close()
        candidate = image.getvalue()
    if len(candidate) != len(original):
        raise ValueError('Candidate changed the card size.')
    with image_file(candidate, destination) as image:
        card = ps2mc(image)
        try:
            after = tree_snapshot(card)
            if after != expected or not card.check():
                raise ValueError('Candidate changed another file, metadata, or planned slot data.')
            reopened = slot_data(plugin, name, files(simple_save(card, name)))
            if any(slot['state'] == 'invalid' for slot in plugin.slots(name, reopened)):
                raise ValueError('Reopened candidate contains an invalid slot.')
        finally:
            card.close()
    return CopyPlan(source, destination, digest(source_data), digest(original), name, name,
                    action, candidate, original, tuple(warnings), source_slot, destination_slot)
