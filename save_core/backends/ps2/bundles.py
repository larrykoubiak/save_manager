"""Translate private PS2 directory records at the generic save boundary."""

from ..base import SaveBundle, SaveFile
from .native.native_save import PS2SaveFile


def from_native(save):
    if isinstance(save, SaveBundle):
        return save
    directory = tuple(save.get_directory())
    return SaveBundle(directory[8].decode('ascii'), tuple(
        SaveFile(entry[8].decode('ascii'), bytes(data), tuple(entry))
        for entry, data in save), directory)


def to_native(save):
    if isinstance(save, PS2SaveFile):
        return save
    if not isinstance(save, SaveBundle) or not isinstance(save.metadata, tuple) or len(save.metadata) != 9:
        raise ValueError('This save does not contain PS2 storage metadata.')
    directory = list(save.metadata)
    directory[2], directory[8] = len(save.files), save.save_id.encode('ascii')
    result = PS2SaveFile()
    result.set_directory(directory)
    for number, file in enumerate(save.files):
        if not isinstance(file.metadata, tuple) or len(file.metadata) != 9:
            raise ValueError('A save file is missing its PS2 storage metadata.')
        entry = list(file.metadata)
        entry[2], entry[8] = len(file.data), file.name.encode('ascii')
        result.set_file(number, entry, file.data)
    return result
