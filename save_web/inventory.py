"""Inventory dispatch for configured storage backends."""

from save_core.backends import get_backend
from save_core.storage_paths import location_root, card_path


def snapshot_location(location):
    return get_backend(location.get('backend', 'ps2')).snapshot_location(location)


__all__ = ['location_root', 'card_path', 'snapshot_location']
