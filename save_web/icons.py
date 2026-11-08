"""Render PS2 morph animation to GIF on demand, using the desktop CPU rasterizer.

Morph interpolation uses the stored keyframes. Timing interprets frame_length
as 60 Hz display frames, scaled by anim_speed, as in ps2vmc-tool's renderer.
"""

import io
import logging
import math
from pathlib import Path
import re
import struct
import threading

from save_core.save_icons import placeholder, render_pose
from vendor.mymcplusplus.mymcplusplus.ps2icon import Icon

CACHE_VERSION = 'v3'


def validate_model(data):
    if not 20 <= len(data) <= 2 * 1024 * 1024:
        raise ValueError('Unsupported icon size')
    _, shapes, _, _, vertices = struct.unpack_from('<5I', data)
    if not 1 <= shapes <= 64 or not 3 <= vertices <= 15000 or shapes * vertices > 300000:
        raise ValueError('Unsupported icon geometry')
    icon = Icon(data)
    # Single-pose icons do not use animation tracks. GTA3 has a dummy track
    # referring to shape 1 despite only containing shape 0.
    if shapes > 1 and any(frame.shape_id >= shapes or any(not math.isfinite(key.time) or not math.isfinite(key.value)
                                          for key in frame.keys) for frame in icon.frames):
        raise ValueError('Invalid animation keys')
    return icon


def pose_at(icon, tick):
    if icon.animation_shapes == 1:
        return list(icon.vertex_data[:icon.vertex_count * 3])
    duration = float(icon.frame_length)
    tick = tick % duration if duration > 0 else 0
    weights = {}
    for frame in icon.frames:
        keys = [(key.time, key.value) for key in frame.keys]
        if not keys:
            if frame.shape_id == 0:
                keys = [(0.0, 1.0)]
            else:
                continue
        last_time, last_value = max(((t if t <= tick else t - duration, value) for t, value in keys), key=lambda item: item[0])
        next_time, next_value = min(((t if t >= tick else t + duration, value) for t, value in keys), key=lambda item: item[0])
        progress = (tick - last_time) / (next_time - last_time) if next_time > last_time else 0
        weights[frame.shape_id] = max(0.0, last_value * (1 - progress) + next_value * progress)
    total = sum(weights.values())
    weights = {shape: weight / total for shape, weight in weights.items()} if total > 0 else {0: 1.0}
    return [sum(weight * icon.vertex_data[shape * icon.vertex_count * 3 + i] for shape, weight in weights.items())
            for i in range(icon.vertex_count * 3)]


def loop_seconds(icon):
    """Use native speed; guard against zero/invalid speed and pathological loops."""
    speed = icon.anim_speed
    if not math.isfinite(speed) or speed < 1e-6:
        speed = 1.0
    return min(60.0, max(0.02, icon.frame_length / (60.0 * speed)))


def projection_bounds(icon):
    # Frame-independent camera framing across all model shapes.
    xs, ys = [], []
    yaw, pitch = -.18, .12
    for i in range(icon.animation_shapes * icon.vertex_count):
        x, y, z = [icon.vertex_data[i * 3 + k] / 4096 for k in range(3)]
        x, z = x * math.cos(yaw) + z * math.sin(yaw), -x * math.sin(yaw) + z * math.cos(yaw)
        y = y * math.cos(pitch) + z * math.sin(pitch)
        xs.append(x)
        ys.append(y)
    return min(xs), max(xs), min(ys), max(ys)


def encode_gif(data=None, animated=True, size=64):
    from PIL import Image
    icon = validate_model(data) if data else None
    moving = icon and animated and icon.animation_shapes > 1 and icon.frame_length > 0
    seconds = loop_seconds(icon) if moving else 0
    # Full loop, bounded CPU cost. Long loops use fewer samples, not faster playback.
    count = min(96, max(2, round(seconds * 20)), max(2, 150000 // icon.vertex_count)) if moving else 1
    bounds = projection_bounds(icon) if icon else None
    frames = []
    for index in range(count):
        ppm, mask = render_pose(icon, pose_at(icon, index * icon.frame_length / count), size, bounds, with_mask=True) if icon else placeholder(size, with_mask=True)
        with Image.open(io.BytesIO(ppm)) as image:
            # Reserve palette index 0 for the rasterizer's uncovered pixels.
            # A coverage mask preserves real geometry with background-like colours.
            quantized = image.convert('RGB').quantize(colors=255)
            frame = Image.frombytes('P', (size, size), bytes(value + 1 if alpha else 0 for value, alpha in zip(quantized.tobytes(), mask)))
            frame.putpalette([0, 0, 0] + quantized.getpalette()[:255 * 3])
            frame.info['transparency'] = 0
            frames.append(frame)
    output = io.BytesIO()
    # Rounded cumulative timing avoids length drift from GIF's 10 ms clock.
    durations = [max(20, round((i + 1) * seconds * 100 / count) * 10 - round(i * seconds * 100 / count) * 10)
                 for i in range(count)] if moving else 100
    frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, disposal=2, transparency=0, background=0, optimize=False)
    return output.getvalue()


class IconCache:
    def __init__(self, service):
        self.db = service.db
        self.directory = service.data_dir / 'icons'
        self.directory.mkdir(exist_ok=True)
        self.lock = threading.Lock()

    def get(self, key, animated=True):
        if key != 'placeholder' and not re.fullmatch('[0-9a-f]{64}', key):
            raise LookupError('Icon not found.')
        path = self.directory / f'{CACHE_VERSION}-{key}-{"animated" if animated else "still"}.gif'
        with self.lock:
            if path.is_file():
                return path.read_bytes()
            model = None if key == 'placeholder' else self.db.one('SELECT model FROM icons WHERE sha256=?', (key,))['model']
            try:
                data = encode_gif(model, animated)
            except Exception as error:
                # Malformed or unsupported icons get a valid neutral image.
                logging.getLogger(__name__).warning('Cannot render save icon %s; using placeholder: %s', key, error)
                data = encode_gif()
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(data)
            temporary.replace(path)
            return data
