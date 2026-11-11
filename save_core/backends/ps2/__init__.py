"""PS2 storage backend backed by internal mymc-derived primitives."""

from pathlib import Path

from ..base import BackendCapabilities
from .bundles import from_native


class PS2Backend:
    backend_id = 'ps2'
    suffixes = ('.ps2', '.bin')
    capabilities = BackendCapabilities(capacity=True, icons=True, delete=True, file_updates=True)

    def snapshot_location(self, location):
        from .inventory import snapshot_location
        return snapshot_location(location)

    def read_save(self, path, save_id):
        from save_core.save_copy import image_file
        from .operations import simple_save
        from save_core.save_merge import stable_read
        from .native.ps2mc import ps2mc
        path = Path(path)
        data = stable_read(path)
        with image_file(data, path) as image:
            card = ps2mc(image)
            try:
                return from_native(simple_save(card, save_id))
            finally:
                card.close()

    def validate(self, data, name):
        from save_core.save_copy import image_file
        from .native.ps2mc import ps2mc
        with image_file(data, name) as image:
            card = ps2mc(image)
            try:
                if not card.check():
                    raise ValueError('PS2 filesystem check failed.')
            finally:
                card.close()

    def plan_copy(self, source, record, destination, conflict='stop'):
        from .operations import plan_copy
        return plan_copy(source, record, destination, conflict)

    def plan_delete(self, path, record):
        from .operations import plan_delete
        return plan_delete(path, record)

    def plan_slot_copy(self, source, record, source_slot, destination, destination_slot, conflict='stop'):
        from .operations import plan_slot_copy
        return plan_slot_copy(source, record, source_slot, destination, destination_slot, conflict)
