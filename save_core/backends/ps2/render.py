"""CPU rasterizer for PS2 icon poses and the web GIF encoder's coverage masks."""

import math
import struct

def render_pose(icon, coordinates=None, size=64, bounds=None, with_mask=False):
    """Rasterize one pose; optional shared projected bounds keep animations steady."""
    if not 3 <= icon.vertex_count <= 50000 or icon.animation_shapes < 1:
        raise ValueError('Unsupported icon geometry')
    vertices = []
    yaw, pitch = -.18, .12
    for i in range(icon.vertex_count):
        source = icon.vertex_data if coordinates is None else coordinates
        x, y, z = [source[i * 3 + k] / 4096 for k in range(3)]
        x, z = x * math.cos(yaw) + z * math.sin(yaw), -x * math.sin(yaw) + z * math.cos(yaw)
        y, z = y * math.cos(pitch) + z * math.sin(pitch), z * math.cos(pitch) - y * math.sin(pitch)
        vertices.append((x, y, z))
    xs, ys, _ = zip(*vertices)
    x0, x1, y0, y1 = bounds or (min(xs), max(xs), min(ys), max(ys))
    scale = (size - 10) / max(x1 - x0, y1 - y0, .001)
    cx, cy = (x1 + x0) / 2, (y1 + y0) / 2
    vertices = [((x - cx) * scale + size / 2, (y - cy) * scale + size / 2, z)
                for x, y, z in vertices]
    pixels = bytearray([241, 244, 248] * size * size)
    depths = [-float('inf')] * size * size
    texture = (struct.unpack('<16384H', icon.texture)
               if isinstance(icon.texture, (bytes, bytearray)) and len(icon.texture) == 32768 else None)
    for start in range(0, icon.vertex_count - 2, 3):
        a, b, c = vertices[start:start + 3]
        denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(denominator) < .00001:
            continue
        for y in range(max(0, math.floor(min(a[1], b[1], c[1]))),
                       min(size, math.ceil(max(a[1], b[1], c[1])))):
            for x in range(max(0, math.floor(min(a[0], b[0], c[0]))),
                           min(size, math.ceil(max(a[0], b[0], c[0])))):
                u = ((b[1] - c[1]) * (x + .5 - c[0]) + (c[0] - b[0]) * (y + .5 - c[1])) / denominator
                v = ((c[1] - a[1]) * (x + .5 - c[0]) + (a[0] - c[0]) * (y + .5 - c[1])) / denominator
                weights = (u, v, 1 - u - v)
                if min(weights) < -.0001:
                    continue
                depth = sum(w * p[2] for w, p in zip(weights, (a, b, c)))
                index = y * size + x
                if depth <= depths[index]:
                    continue
                alpha = sum(weights[j] * icon.color_data[(start + j) * 4 + 3] for j in range(3))
                if icon.enable_alpha and alpha < 8:
                    continue
                color = [255, 255, 255]
                if texture:
                    tx, ty = [int(sum(weights[j] * icon.normal_uv_data[(start + j) * 5 + k]
                                      for j in range(3)) / 4096 * 128) % 128 for k in (3, 4)]
                    value = texture[ty * 128 + tx]
                    color = [((value >> shift) & 31) * 255 / 31 for shift in (0, 5, 10)]
                for channel in range(3):
                    tint = sum(weights[j] * icon.color_data[(start + j) * 4 + channel] for j in range(3))
                    pixels[index * 3 + channel] = max(0, min(255, round(color[channel] * tint / 255)))
                depths[index] = depth
    ppm = f'P6\n{size} {size}\n255\n'.encode() + pixels
    if with_mask:
        return ppm, bytes(0 if depth == -float('inf') else 255 for depth in depths)
    return ppm


def placeholder(size=64, with_mask=False):
    """A neutral memory-card glyph for missing or unsupported game icons."""
    pixels = bytearray([241, 244, 248] * size * size)
    mask = bytearray(size * size)
    for y in range(size // 5, size * 4 // 5):
        for x in range(size // 4, size * 3 // 4):
            color = (102, 119, 141)
            if size * 2 // 5 < y < size * 3 // 5 and size // 3 < x < size * 2 // 3:
                color = (222, 229, 237)
            pixels[(y * size + x) * 3:(y * size + x) * 3 + 3] = bytes(color)
            mask[y * size + x] = 255
    ppm = f'P6\n{size} {size}\n255\n'.encode() + pixels
    return (ppm, bytes(mask)) if with_mask else ppm
