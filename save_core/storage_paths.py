"""Mounted location and container path validation, independent of format."""

from pathlib import Path
import os


def location_path(location):
    """Resolve a location against its device root, including offline folders."""
    if 'root_path' not in location:
        return Path(location['path'])  # Standalone backend callers.
    root = Path(location['root_path']).resolve()
    relative = Path(location['path'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Location folder must be relative to its device root.')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Location folders cannot contain symlinks.')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Location folder must stay inside its device root.')
    return path


def location_root(location):
    root = location_path(location)
    if not root.is_dir():
        raise FileNotFoundError('Location is unavailable: ' + str(root))
    if location['marker'] and not (root / location['marker']).is_file():
        raise FileNotFoundError('Availability marker is missing: ' + location['marker'])
    return root.resolve()


def card_path(location, filename):
    root = location_root(location)
    relative = Path(filename)
    if not filename or relative.is_absolute() or '..' in relative.parts or relative == Path('.'):
        raise ValueError('Card path must stay inside its location.')
    path = root
    # No card symlinks: atomic replacement must refer to the configured image.
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Card must be a regular file; its path cannot contain symlinks.')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Card path must stay inside its location.')
    if not path.is_file():
        raise FileNotFoundError('Card is unavailable: ' + filename)
    return path


def card_files(location, suffixes):
    """Discover cards recursively without following directory symlinks."""
    root = location_root(location)
    def failed(error):
        raise error
    paths = []
    for folder, directories, files in os.walk(root, followlinks=False, onerror=failed):
        directories[:] = [name for name in directories if not (Path(folder) / name).is_symlink()]
        for name in files:
            path = Path(folder) / name
            if path.suffix.lower() in suffixes:
                paths.append(card_path(location, path.relative_to(root).as_posix()))
    return sorted(paths)
