"""Read-only PS2 adapter. Locations are mounted folders, independent of device type."""

import hashlib
from pathlib import Path

from save_core.compare_saves import scan
from game_plugins import plugin_for, slot_data
from save_core.save_copy import image_file, tree_snapshot
from save_core.save_merge import digest, stable_read
from vendor.mymcplusplus.mymcplusplus.ps2mc import ps2mc
from vendor.mymcplusplus.mymcplusplus.ps2iconsys import IconSys


def location_root(location):
    root = Path(location['path'])
    if not root.is_dir():
        raise FileNotFoundError('Location is unavailable: ' + str(root))
    if location['marker'] and not (root / location['marker']).is_file():
        raise FileNotFoundError('Availability marker is missing: ' + location['marker'])
    return root.resolve()


def card_path(location, filename):
    root = location_root(location)
    path = root / filename
    # No card symlinks: atomic replacement must refer to the configured image.
    if Path(filename).name != filename or path.is_symlink() or path.resolve().parent != root:
        raise ValueError('Card must be a regular file directly inside its location.')
    if not path.is_file():
        raise FileNotFoundError('Card is unavailable: ' + filename)
    return path


def snapshot_location(location):
    root = location_root(location)
    suffix = location['suffix']
    paths = sorted(p for p in root.iterdir() if p.suffix.lower() == suffix and p.is_file())
    if not paths:
        # Empty, reachable folders are valid inventories, unlike offline folders.
        return []
    for path in paths:
        card_path(location, path.name)
    versions = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in paths}
    info = []
    scanned_paths, records = scan(root, suffix, card_info=info)
    if scanned_paths != paths:
        raise ValueError('Location changed during scan; retry when it is idle.')
    result = []
    for path, usage in zip(paths, info):
        before = path.stat()
        if versions[path.name] != (before.st_size, before.st_mtime_ns):
            raise ValueError('Card changed during scan; retry when it is idle.')
        data = stable_read(path)
        card_records = [r for r in records if r['card'] == path.name]
        if any(r['card_version'] != (before.st_size, before.st_mtime_ns) for r in card_records):
            raise ValueError('Card changed during scan; retry when it is idle.')
        if usage['formatted']:
            with image_file(data, path) as image:
                card = ps2mc(image)
                try:
                    tree = tree_snapshot(card)
                finally:
                    card.close()
            for record in card_records:
                prefix = '/' + record['save']
                hashed = hashlib.sha256()
                for name, (_, contents) in sorted(tree.items()):
                    if name == prefix or name.startswith(prefix + '/'):
                        relative = name[len(prefix):].encode('utf-8')
                        hashed.update(len(relative).to_bytes(4, 'big') + relative)
                        hashed.update(b'D' if contents is None else b'F' + len(contents).to_bytes(8, 'big') + contents)
                record['sha256'] = hashed.hexdigest()
                record['slots'] = []
                plugin = plugin_for(record['save'])
                if callable(getattr(plugin, 'slots', None)):
                    payload = slot_data(plugin, record['save'], {
                        name[len(prefix) + 1:]: contents for name, (_, contents) in tree.items()
                        if name.startswith(prefix + '/') and contents is not None})
                    try:
                        record['slots'] = plugin.slots(record['save'], payload)
                    except ValueError as error:
                        # Preserve generic inventory for an unsupported payload.
                        record['slot_error'] = str(error)
                if callable(getattr(plugin, 'summary', None)):
                    payload = slot_data(plugin, record['save'], {
                        name[len(prefix) + 1:]: contents for name, (_, contents) in tree.items()
                        if name.startswith(prefix + '/') and contents is not None})
                    try:
                        record.update(plugin.summary(record['save'], payload))
                    except ValueError as error:
                        record['summary'] = 'Progress unavailable: ' + str(error)
                record['icon_model'] = None
                # A missing/bad icon must never hide an otherwise readable save.
                try:
                    metadata = tree[prefix + '/icon.sys'][1]
                    filename = IconSys(metadata).icon_file_normal
                    if '/' in filename or filename in ('.', '..'):
                        raise ValueError('Invalid icon filename')
                    model = tree[prefix + '/' + filename][1]
                    if model and len(model) <= 2 * 1024 * 1024:
                        record['icon_model'] = model
                except Exception:
                    pass
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Card changed during scan; retry when it is idle.')
        result.append(dict(filename=path.name, sha256=digest(data), size=after.st_size,
                           mtime_ns=after.st_mtime_ns, formatted=usage['formatted'],
                           total_blocks=usage.get('total_blocks'), free_blocks=usage.get('free_blocks'),
                           saves=card_records))
    # Detect removal, insertion, or an unmount during the scan before committing.
    if sorted(p.name for p in location_root(location).iterdir()
              if p.suffix.lower() == suffix and p.is_file()) != [p.name for p in paths]:
        raise ValueError('Location changed during scan; retry when it is idle.')
    return result
