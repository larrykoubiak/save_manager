"""Experimental FFX International slot cloning, with no filesystem I/O.

Adapt the directory/payload/icon names and the slot label/references in
icon.sys. Preserve the entire game-state payload and icon model byte-for-byte.
Based on the renaming performed by FFXED's SaveDataManager slot editor.
The caller must import the returned save only into an unused destination slot.
"""

from vendor.mymcplusplus.mymcplusplus.save.ps2save import PS2SaveFile
from .ffx import FFXInternational, checksum, u16


def clone_slot(source, destination_slot):
    if type(destination_slot) is not int or not 1 <= destination_slot <= 99:
        raise ValueError('FFX destination slot must be between 1 and 99.')
    directory = source.get_directory()
    old_name = directory[8].decode('ascii')
    if not FFXInternational.supports(old_name):
        raise ValueError('Unsupported FFX save directory.')
    old_slot = FFXInternational.slot_number(old_name)
    if destination_slot == old_slot:
        raise ValueError('Choose a different destination slot.')
    return prepare_slot(source, destination_slot)


def prepare_slot(source, destination_slot):
    """Validate and copy to a slot, including when its number stays the same."""
    if type(destination_slot) is not int or not 1 <= destination_slot <= 99:
        raise ValueError('FFX destination slot must be between 1 and 99.')
    directory = source.get_directory()
    old_name = directory[8].decode('ascii')
    if not FFXInternational.supports(old_name):
        raise ValueError('Unsupported FFX save directory.')
    old_slot = FFXInternational.slot_number(old_name)
    new_name = old_name[:-2] + f'{destination_slot - 1:02}'
    old_icon = f'010906{old_slot - 1:02}.ico'
    new_icon = f'010906{destination_slot - 1:02}.ico'
    files = {ent[8].decode('ascii'): data for ent, data in source}
    if len(source) != 3 or set(files) != {old_name, old_icon, 'icon.sys'}:
        raise ValueError('Unexpected FFX file set; slot cloning is unsupported for this save.')
    payload = files[old_name]
    verified_checksum = checksum(payload)
    if not u16(payload, 26) == u16(payload, 25844) == verified_checksum:
        raise ValueError('The source FFX payload checksums do not match.')
    icon_sys = bytearray(files['icon.sys'])
    if len(icon_sys) != 964 or icon_sys[:4] != b'PS2D':
        raise ValueError('Unsupported icon.sys structure.')
    digits = str.maketrans('0123456789', '０１２３４５６７８９')
    old_label = f'［{old_slot:02}］'.translate(digits).encode('shift_jis')
    new_label = f'［{destination_slot:02}］'.translate(digits).encode('shift_jis')
    title = bytes(icon_sys[192:260])
    if title.count(old_label) != 1:
        raise ValueError('The FFX slot label could not be identified in icon.sys.')
    icon_sys[192:260] = title.replace(old_label, new_label)
    for offset in (260, 324, 388):
        field = bytes(icon_sys[offset:offset + 64])
        if field.split(b'\0', 1)[0] != old_icon.encode('ascii'):
            raise ValueError('Unexpected icon filename references in icon.sys.')
        icon_sys[offset:offset + len(old_icon)] = new_icon.encode('ascii')
    result = PS2SaveFile()
    new_directory = list(directory)
    new_directory[8] = new_name.encode('ascii')
    result.set_directory(new_directory)
    for index, (entry, data) in enumerate(source):
        entry = list(entry)
        name = entry[8].decode('ascii')
        entry[8] = {old_name: new_name, old_icon: new_icon}.get(name, name).encode('ascii')
        result.set_file(index, entry, bytes(icon_sys) if name == 'icon.sys' else data)
    return result
