"""Shared byte-only workflow for PS1 games with one file per numbered save."""

from dataclasses import dataclass, replace
import struct
from typing import Callable


def fullwidth_digits(text):
    return text.translate(str.maketrans(' 0123456789', '　０１２３４５６７８９'))


@dataclass(frozen=True)
class NumberedSaveLayout:
    prefix: str
    suffixes: tuple[str, ...]
    header: bytes
    checksum_offset: int
    checksum_format: str
    checksum: Callable[[bytes], int]
    playtime_offset: int | None
    slot_label: Callable[[int], bytes]
    size: int = 8192
    ticks_per_second: int = 60
    # Some games use a fixed field, others put their label among preview text.
    label_offset: int | None = None
    validate_label: bool = False
    validate_extra: Callable[[bytes], None] | None = None
    progress: Callable[[bytes], tuple[str, ...]] | None = None
    # Keep existing library identities when adding or changing an adapter.
    library_key: str | None = None
    # A few games repeat the zero-based file position inside their payload.
    slot_index_offset: int | None = None


class NumberedSavePlugin:
    """One shallow base for the existing plugin API; each game owns its layout.

    This deliberately does not handle PS2 containers, internal records or card
    I/O. Checksum algorithms and unusual game checks stay in their modules.
    """

    backend_id = 'ps1'
    card_slots = True
    layout: NumberedSaveLayout
    name: str
    title: str
    error_name: str

    @classmethod
    def supports(cls, save_id):
        return save_id.startswith(cls.layout.prefix) and save_id[len(cls.layout.prefix):] in cls.layout.suffixes

    @staticmethod
    def payload_name(save_id):
        return 'data'

    @classmethod
    def slot_number(cls, save_id):
        if not cls.supports(save_id):
            raise ValueError(f'Unsupported {cls.error_name} save ID.')
        return cls.layout.suffixes.index(save_id[len(cls.layout.prefix):]) + 1

    @classmethod
    def _label_matches(cls, data, number):
        label = cls.layout.slot_label(number)
        offset = cls.layout.label_offset
        return (data[4:68].count(label) == 1 if offset is None
                else data[offset:offset + len(label)] == label)

    @classmethod
    def validate_save(cls, save_id, data):
        number = cls.slot_number(save_id)
        layout = cls.layout
        if not isinstance(data, bytes) or len(data) != layout.size:
            raise ValueError(f'Expected an {layout.size:,}-byte {cls.error_name} save.')
        if not data.startswith(layout.header):
            raise ValueError(f'Unsupported {cls.error_name} save layout.')
        if struct.unpack_from(layout.checksum_format, data, layout.checksum_offset)[0] != layout.checksum(data):
            raise ValueError(f'{cls.error_name} checksum mismatch.')
        if layout.validate_label and not cls._label_matches(data, number):
            raise ValueError(f'{cls.error_name} slot label does not match its save ID.')
        if layout.slot_index_offset is not None and data[layout.slot_index_offset] != number - 1:
            raise ValueError(f'{cls.error_name} stored slot number does not match its save ID.')
        if layout.validate_extra:
            layout.validate_extra(data)

    @classmethod
    def playtime_seconds(cls, data):
        if cls.layout.playtime_offset is None:
            return None
        return struct.unpack_from('<I', data, cls.layout.playtime_offset)[0] // cls.layout.ticks_per_second

    @classmethod
    def summary(cls, save_id, data):
        result = {'row_title': f'{cls.title} · Slot {cls.slot_number(save_id)}'}
        try:
            cls.validate_save(save_id, data)
        except ValueError as error:
            return {**result, 'summary': 'Progress unavailable: ' + str(error)}
        progress = cls.layout.progress(data) if cls.layout.progress else ()
        seconds = cls.playtime_seconds(data)
        if seconds is not None:
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            progress = (*progress, f'Playtime {hours}:{minutes:02}:{seconds:02}')
        return {**result, 'summary': ' · '.join(part for part in progress if part) or 'Saved game'}

    @classmethod
    def library_slots(cls, bundle):
        from ..library import LibrarySlot
        data = bundle.file_data()['data']
        cls.validate_save(bundle.save_id, data)
        number = cls.slot_number(bundle.save_id)
        return [LibrarySlot(cls.layout.library_key or str(number), number,
                            cls.summary(bundle.save_id, data)['summary'], {'data': data}, cls.playtime_seconds(data))]

    @classmethod
    def library_identity(cls, bundle, number=None):
        """Compare progress independently of the filename and numbered preview.

        Normalize through the same validated relocation path used for copies;
        never alter the archived bundle or its checksum.
        """
        return cls.prepare_slot(bundle, 1).file_data()

    @classmethod
    def prepare_slot(cls, source, destination_slot):
        layout = cls.layout
        if type(destination_slot) is not int or not 1 <= destination_slot <= len(layout.suffixes):
            raise ValueError(f'{cls.error_name} destination slot must be between 1 and {len(layout.suffixes)}.')
        old_slot = cls.slot_number(source.save_id)
        files = source.file_data()
        if len(source.files) != 1 or set(files) != {'data'}:
            raise ValueError(f'Unexpected {cls.error_name} save file set.')
        data = files['data']
        cls.validate_save(source.save_id, data)
        if old_slot == destination_slot:
            return source
        if not cls._label_matches(data, old_slot):
            raise ValueError(f'{cls.error_name} slot label does not match its save ID.')
        old_label, new_label = layout.slot_label(old_slot), layout.slot_label(destination_slot)
        if len(old_label) != len(new_label):
            raise ValueError('Numbered save labels must retain their byte length.')
        payload = bytearray(data)
        if layout.label_offset is None:
            # Preserve any other preview fields inside the 64-byte title.
            payload[4:68] = data[4:68].replace(old_label, new_label)
        else:
            payload[layout.label_offset:layout.label_offset + len(new_label)] = new_label
        if layout.slot_index_offset is not None:
            payload[layout.slot_index_offset] = destination_slot - 1
        struct.pack_into(layout.checksum_format, payload, layout.checksum_offset, layout.checksum(payload))
        save_id = layout.prefix + layout.suffixes[destination_slot - 1]
        cls.validate_save(save_id, bytes(payload))
        return replace(source, save_id=save_id, files=(replace(source.files[0], data=bytes(payload)),))
