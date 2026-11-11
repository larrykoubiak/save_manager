"""PS1 16-colour bitmap icons with the BIOS PAL animation cadence."""

import io
import struct


def encode_gif(data, animated=True, size=64):
    from PIL import Image
    if len(data)<128 or data[:2]!=b'SC' or data[2] not in (0x11,0x12,0x13):
        raise ValueError('Invalid PS1 icon header.')
    count = data[2]-0x10
    if len(data)!=128*(1+count):
        raise ValueError('Truncated PS1 icon frames.')
    colours = struct.unpack_from('<16H',data,96)
    palette = [0,0,0]+[round(((colour>>shift)&31)*255/31) for colour in colours for shift in (0,5,10)]
    palette += [0]*(768-len(palette))
    frames = []
    for number in range(count if animated else 1):
        pixels = []
        for byte in data[128*(number+1):128*(number+2)]:
            for index in (byte&15,byte>>4):
                pixels.append(index+1 if colours[index] else 0)
        frame = Image.frombytes('P',(16,16),bytes(pixels))
        frame.putpalette(palette)
        frames.append(frame.resize((size,size),Image.Resampling.NEAREST))
    output = io.BytesIO()
    duration = 320 if count==2 else 220 if count==3 else 100
    frames[0].save(output,format='GIF',save_all=True,append_images=frames[1:],duration=duration,
                   transparency=0,background=0,disposal=2,loop=0,optimize=False)
    return output.getvalue()
