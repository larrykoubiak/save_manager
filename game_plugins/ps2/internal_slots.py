"""Common workflow for PS2 saves containing several internal save positions."""

from ..library import LibrarySlot
from .. import slot_data


class InternalSlotsPlugin:
    """Games own parsing and byte placement; this helper owns copy policy.

    ``_read_slot`` returns a named set of bytes for one position, and
    ``_write_slot`` returns a new payload with those bytes at another position.
    Neither hook may mutate its inputs. ``slots`` supplies validation and a
    position-independent fingerprint of the saved progress.
    """

    slot_count: int
    slot_error_name: str
    invalid_destination_message = 'Destination contains invalid slots. Resolve them before copying.'

    @classmethod
    def library_identity(cls, bundle, number):
        """Use the same position-independent fingerprint verified during copies."""
        data = slot_data(cls, bundle.save_id, bundle.file_data())
        info = next(slot for slot in cls.slots(bundle.save_id, data) if slot['number'] == number)
        if info['state'] != 'occupied':
            raise ValueError('Cannot identify an empty or invalid save.')
        return {'progress': bytes.fromhex(info['sha256'])}

    @classmethod
    def library_slots(cls, bundle):
        data = slot_data(cls, bundle.save_id, bundle.file_data())
        result = []
        for info in cls.slots(bundle.save_id, data):
            if info['state'] != 'occupied':
                continue
            number = info['number']
            parts = cls._read_slot(bundle.save_id, data, number)
            result.append(LibrarySlot(str(number), number, info['label'], parts, cls._slot_playtime(parts)))
        return result

    @staticmethod
    def _slot_playtime(parts):
        return None

    @classmethod
    def _validate_destination(cls, save_directory, destination):
        """Optional checks for shared data outside the individual positions."""

    @classmethod
    def copy_slot(cls, save_directory, source, source_slot, destination, destination_slot, replace=False):
        for number in (source_slot, destination_slot):
            if type(number) is not int or not 1 <= number <= cls.slot_count:
                raise ValueError(f'{cls.slot_error_name} slots must be integers between 1 and {cls.slot_count}.')
        if type(replace) is not bool:
            raise ValueError('Slot replacement must be explicitly true or false.')
        source_info = cls.slots(save_directory, source)[source_slot - 1]
        destination_info = cls.slots(save_directory, destination)
        if source_info['state'] != 'occupied':
            raise ValueError('Source slot is empty or invalid: ' + (source_info['error'] or str(source_slot)))
        if any(slot['state'] == 'invalid' for slot in destination_info):
            raise ValueError(cls.invalid_destination_message)
        cls._validate_destination(save_directory, destination)
        parts = cls._read_slot(save_directory, source, source_slot)
        result = cls._write_slot(save_directory, destination, destination_slot, parts)
        # A byte-identical copy is safe even when the destination is occupied.
        if result == destination:
            return result
        if destination_info[destination_slot - 1]['state'] == 'occupied' and not replace:
            raise ValueError(f'Destination slot {destination_slot} is occupied. Choose replacement explicitly.')
        checked = cls.slots(save_directory, result)[destination_slot - 1]
        if checked['state'] != 'occupied' or checked['sha256'] != source_info['sha256']:
            raise ValueError('Copied slot verification failed.')
        return result
