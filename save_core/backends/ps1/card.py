"""Raw PS1 memory cards, implemented from the PSX-SPX data-format reference.

https://problemkaputt.de/psxspx-memory-card-data-format.htm
No emulator implementation is imported or bundled.
"""

from dataclasses import dataclass
from functools import reduce
import operator
import struct

SIZE = 131072
BLOCK = 8192
FRAME = 128
FREE = {0xa0, 0xa1, 0xa2, 0xa3}
ACTIVE = {0x51, 0x52, 0x53}


def checksum(frame):
    return reduce(operator.xor, frame[:127], 0)


def checked_frame(frame):
    frame = bytearray(frame)
    frame[127] = checksum(frame)
    return bytes(frame)


def blank_card():
    """Produce a formatted empty image for new files and synthetic fixtures."""
    data = bytearray(b'\xff' * SIZE)
    header = bytearray(FRAME)
    header[:2] = b'MC'
    data[:FRAME] = checked_frame(header)
    for block in range(1, 16):
        frame = bytearray(FRAME)
        frame[0] = 0xa0
        frame[8:10] = b'\xff\xff'
        data[block*FRAME:(block+1)*FRAME] = checked_frame(frame)
    for number in range(16, 36):
        frame = bytearray(FRAME)
        frame[:4] = b'\xff' * 4
        frame[8:10] = b'\xff\xff'
        data[number*FRAME:(number+1)*FRAME] = checked_frame(frame)
    data[63*FRAME:64*FRAME] = data[:FRAME]
    return bytes(data)


@dataclass(frozen=True)
class Entry:
    name: str
    size: int
    blocks: tuple[int, ...]


@dataclass(frozen=True)
class Metadata:
    size: int
    frames: tuple[bytes, ...]


class Card:
    def __init__(self, data):
        if len(data) != SIZE:
            raise ValueError(f'Expected a raw 128 KiB PS1 card; found {len(data):,} bytes.')
        self.data = bytearray(data)
        self.remapped = {}
        if data[:2] != b'MC' or checksum(data[:FRAME]) != data[127]:
            raise ValueError('Invalid PS1 card header or header checksum.')
        for number in range(16, 36):
            frame = data[number*FRAME:(number+1)*FRAME]
            if checksum(frame) != frame[127]:
                raise ValueError('Invalid PS1 broken-sector table checksum.')
            sector = struct.unpack_from('<I', frame)[0]
            if sector != 0xffffffff:
                if not 64 <= sector < SIZE//FRAME or sector in self.remapped:
                    raise ValueError('Invalid or unsupported PS1 broken-sector mapping.')
                self.remapped[sector] = number + 20
        self.entries = {}
        self.states = {}
        for block in range(1,16):
            frame = self.frame(block)
            if checksum(frame) != frame[127]:
                raise ValueError(f'PS1 directory checksum mismatch in block {block}.')
            state = struct.unpack_from('<I',frame)[0]
            if state not in FREE | ACTIVE:
                raise ValueError(f'Invalid PS1 allocation state in block {block}.')
            self.states[block] = state
        owned = set()
        for block, state in self.states.items():
            if state != 0x51:
                continue
            first = self.frame(block)
            raw_name = first[10:31]
            end = raw_name.find(b'\0')
            if end <= 0 or any(not 0x20 <= c <= 0x7f for c in raw_name[:end]):
                raise ValueError('Invalid PS1 save filename.')
            name = raw_name[:end].decode('ascii')
            if name in self.entries:
                raise ValueError('Duplicate PS1 save filename: '+name)
            size = struct.unpack_from('<I', first, 4)[0]
            chain = []
            current = block
            while True:
                if current in owned or current not in self.states:
                    raise ValueError('PS1 block chain is cyclic, cross-linked or outside the card.')
                frame = self.frame(current)
                link = struct.unpack_from('<H', frame, 8)[0]
                expected = 0x51 if not chain else 0x53 if link == 0xffff else 0x52
                if self.states[current] != expected:
                    raise ValueError('Invalid PS1 block chain allocation state.')
                owned.add(current)
                chain.append(current)
                if link == 0xffff:
                    break
                if link > 14:
                    raise ValueError('PS1 block chain points outside the card.')
                current = link + 1
            if not 0 < size <= 15*BLOCK or (size+BLOCK-1)//BLOCK != len(chain):
                raise ValueError('PS1 save size does not match its allocated blocks.')
            self.entries[name] = Entry(name,size,tuple(chain))
        if owned != {block for block,state in self.states.items() if state in ACTIVE}:
            raise ValueError('PS1 card contains orphaned active blocks.')

    def frame(self, number):
        physical = self.remapped.get(number,number)
        return bytes(self.data[physical*FRAME:(physical+1)*FRAME])

    def write_frame(self, number, data):
        if len(data) != FRAME:
            raise ValueError('A PS1 sector must contain 128 bytes.')
        physical = self.remapped.get(number,number)
        self.data[physical*FRAME:(physical+1)*FRAME] = data

    def payload(self, entry):
        return b''.join(self.frame(block*64+sector) for block in entry.blocks for sector in range(64))

    def metadata(self, entry):
        return Metadata(entry.size,tuple(self.frame(block) for block in entry.blocks))

    def free_blocks(self):
        return [block for block,state in self.states.items() if state in FREE]

    def delete(self, name):
        if name not in self.entries:
            raise ValueError('Save is missing. Scan and preview again.')
        for index,block in enumerate(self.entries[name].blocks):
            frame = bytearray(self.frame(block))
            struct.pack_into('<I',frame,0,0xa1 if index==0 else 0xa3 if index==len(self.entries[name].blocks)-1 else 0xa2)
            self.write_frame(block,checked_frame(frame))
        self.__init__(bytes(self.data))

    def put(self, name, data, metadata):
        if name in self.entries:
            raise ValueError('A PS1 save already uses this filename.')
        try:
            filename = name.encode('ascii')
        except UnicodeEncodeError as error:
            raise ValueError('PS1 filenames must be ASCII.') from error
        if not 1 <= len(filename) <= 20 or b'\0' in filename or any(not 0x20 <= c <= 0x7f for c in filename):
            raise ValueError('Invalid PS1 save filename.')
        if not data or len(data)%BLOCK or not isinstance(metadata,Metadata) or len(metadata.frames)!=len(data)//BLOCK:
            raise ValueError('Invalid PS1 save bundle or metadata.')
        count = len(data)//BLOCK
        if not 0 < metadata.size <= len(data) or (metadata.size+BLOCK-1)//BLOCK != count:
            raise ValueError('PS1 save length does not match its metadata.')
        blocks = self.free_blocks()[:count]
        if len(blocks)<count:
            raise ValueError(f'Not enough free space: this save needs {count} PS1 block(s) ({count*8} KiB); only {len(blocks)} block(s) are free.')
        for index,block in enumerate(blocks):
            frame = bytearray(metadata.frames[index])
            if len(frame)!=FRAME:
                raise ValueError('Invalid PS1 directory metadata.')
            struct.pack_into('<I',frame,0,0x51 if index==0 else 0x53 if index==count-1 else 0x52)
            if index==0:
                struct.pack_into('<I',frame,4,metadata.size)
                # Preserve bytes after the terminator, which may contain old
                # directory data even on otherwise valid memory cards.
                frame[10:11+len(filename)] = filename+b'\0'
            struct.pack_into('<H',frame,8,blocks[index+1]-1 if index+1<count else 0xffff)
            self.write_frame(block,checked_frame(frame))
            payload = data[index*BLOCK:(index+1)*BLOCK]
            for sector in range(64):
                self.write_frame(block*64+sector,payload[sector*FRAME:(sector+1)*FRAME])
        self.__init__(bytes(self.data))

    def signature(self, name):
        entry = self.entries[name]
        # Allocation pointers and their checksums change on another card.
        frames = tuple(frame[:8]+b'\0\0'+frame[10:127] for frame in self.metadata(entry).frames)
        return entry.size,frames,self.payload(entry)
