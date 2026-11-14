"""PS2 storage backend backed by internal mymc-derived primitives."""

from pathlib import Path

from ..base import BackendCapabilities
from .bundles import from_native


class PS2Backend:
    backend_id = 'ps2'
    suffixes = ('.ps2', '.bin', '.vm2')
    capabilities = BackendCapabilities(capacity=True, icons=True, delete=True, file_updates=True)

    @staticmethod
    def _card_geometry(data):
        from .native.ps2mc import PS2MC_MAGIC, unpack_superblock
        if len(data) < 0x154 or not data.startswith(PS2MC_MAGIC):
            # Entirely blank emulator cards can be initialized by an explicitly
            # selected formatted source. Never guess around a damaged header.
            for mb in (8, 16, 32, 64):
                for stride in (512, 528):
                    pages = mb * 1024 * 1024 // 512
                    if len(data) == pages * stride and data[:1] in (b'\0', b'\xff') and data == data[:1] * len(data):
                        return 512, pages, stride - 512
            raise ValueError('Expected a raw or ECC PS2 memory card.')
        header = unpack_superblock(data[:0x154])
        page_size, pages = header[2], header[3] * header[6]
        if page_size != 512 or not 1 <= pages <= 262144:
            raise ValueError('Unsupported PS2 memory-card geometry.')
        spare = ((page_size + 127) // 128) * 4
        if len(data) == pages * page_size:
            return page_size, pages, 0
        if len(data) == pages * (page_size + spare):
            return page_size, pages, spare
        raise ValueError('PS2 card length does not match its geometry.')

    @classmethod
    def card_data(cls, data):
        page_size, pages, spare = cls._card_geometry(data)
        if not spare:
            return data
        stride = page_size + spare
        return b''.join(data[i:i + page_size] for i in range(0, len(data), stride))

    @classmethod
    def card_image(cls, content, original):
        from .native.ps2mc_ecc import ecc_calculate_page
        page_size, pages, spare = cls._card_geometry(original)
        if len(content) != page_size * pages:
            raise ValueError('Whole-card sync requires cards of the same capacity.')
        if cls._card_geometry(content)[:2] != (page_size, pages):
            raise ValueError('Whole-card sync requires matching card geometry.')
        if not spare:
            return content
        # Avoid rewriting spare bytes (and triggering another sync) on equal cards.
        if cls.card_data(original) == content:
            return original
        result = bytearray()
        for i in range(0, len(content), page_size):
            page = content[i:i + page_size]
            ecc = b''.join(bytes(part) for part in ecc_calculate_page(page))
            result.extend(page + ecc + bytes(spare - len(ecc)))
        return bytes(result)

    @staticmethod
    def bundle_fingerprint(bundle):
        from .inventory import tree_fingerprint
        prefix = '/' + bundle.save_id
        tree = {prefix: (None, None)}
        for name, data in bundle.file_data().items():
            tree[prefix + '/' + name] = (None, data)
        return tree_fingerprint(tree, prefix)

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
