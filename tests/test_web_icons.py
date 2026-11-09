"""Synthetic morph models prove GIF motion, stable framing and offline caching."""

import importlib.util
import io
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

from save_core.save_merge import digest
from save_web.database import SCHEMA
from save_web.icons import CACHE_VERSION, IconCache, encode_gif, loop_seconds, pose_at, validate_model
from save_web.service import Service


def animated_model():
    header = struct.pack('<5I', 0x10000, 2, 2, 0, 3)
    vertices = b''
    for x, y in ((-2000, -2000), (0, -2000), (-2000, 2000)):
        vertices += struct.pack('<4h', x, y, 0, 0) + struct.pack('<4h', x + 4000, y, 0, 0)
        vertices += struct.pack('<4h2h4B', 0, 0, 4096, 0, 0, 0, 255, 50, 0, 255)
    animation = struct.pack('<IIfII', 1, 60, 1.0, 0, 2)
    animation += struct.pack('<IIff', 0, 1, 0, 1)
    animation += struct.pack('<II', 1, 3)
    animation += b''.join(struct.pack('<ff', t, value) for t, value in ((0, 0), (30, 1), (60, 0)))
    return header + vertices + animation


@unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow is installed with the web extra')
class AnimatedIconTest(unittest.TestCase):
    def test_interpolation_wraps_without_mutating_keys(self):
        icon = validate_model(animated_model())
        counts = [len(frame.keys) for frame in icon.frames]
        self.assertEqual(pose_at(icon, 0)[0], -2000)
        self.assertEqual(pose_at(icon, 30)[0], 0)  # equal contributions from both shapes
        self.assertEqual(pose_at(icon, 60), pose_at(icon, 0))
        self.assertEqual([len(frame.keys) for frame in icon.frames], counts)

    def test_gif_reproduces_motion_with_shared_camera_and_loop_timing(self):
        from PIL import Image
        gif = Image.open(io.BytesIO(encode_gif(animated_model())))
        self.assertEqual(gif.size, (64, 64))
        self.assertTrue(gif.is_animated)
        self.assertEqual(gif.info['loop'], 0)
        frames, duration = [], 0
        for i in range(gif.n_frames):
            gif.seek(i)
            duration += gif.info['duration']
            frame = gif.convert('RGBA')
            frames.append(frame.getchannel('A').getbbox())
            self.assertEqual(frame.getpixel((0, 0))[3], 0)
        self.assertEqual(duration, 1000)
        self.assertGreater(max(box[0] for box in frames), min(box[0] for box in frames))

    def test_transparent_background_keeps_background_coloured_geometry_opaque(self):
        from PIL import Image
        data = bytearray(animated_model())
        for vertex in range(3):
            offset = 20 + vertex * 32 + 28
            data[offset:offset + 4] = bytes((241, 244, 248, 255))
        gif = Image.open(io.BytesIO(encode_gif(bytes(data), animated=False))).convert('RGBA')
        alpha = gif.getchannel('A')
        self.assertIsNotNone(alpha.getbbox())
        self.assertIn(255, alpha.getdata())
        self.assertEqual(gif.getpixel((0, 0))[3], 0)

    def test_playback_speed_scales_gif_duration_without_slowing_sampled_frames(self):
        from PIL import Image
        for speed, expected_ms in ((0.5, 2000), (1.0, 1000), (2.0, 500)):
            with self.subTest(speed=speed):
                model = bytearray(animated_model())
                offset = 20 + 3 * (2 * 8 + 16)
                struct.pack_into('<f', model, offset + 8, speed)
                icon = validate_model(bytes(model))
                self.assertEqual(loop_seconds(icon), expected_ms / 1000)
                gif = Image.open(io.BytesIO(encode_gif(bytes(model))))
                duration = 0
                for i in range(gif.n_frames):
                    gif.seek(i)
                    duration += gif.info['duration']
                self.assertEqual(duration, expected_ms)

    def test_still_and_placeholder_are_valid_single_frame_gifs(self):
        from PIL import Image
        for data in [encode_gif(animated_model(), animated=False), encode_gif()]:
            gif = Image.open(io.BytesIO(data))
            self.assertEqual(gif.format, 'GIF')
            self.assertEqual(gif.n_frames, 1)

    def test_static_gta_style_dummy_track_does_not_hide_geometry(self):
        from test_icon_texture import model
        data = bytearray(model(0x02, b''))
        struct.pack_into('<I', data, 20 + 3 * 24 + 20, 1)  # unused track references nonexistent shape 1
        icon = validate_model(bytes(data))
        self.assertEqual(pose_at(icon, 0), list(icon.vertex_data))
        self.assertNotEqual(encode_gif(bytes(data)), encode_gif())

    def test_models_are_cached_without_reading_live_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(directory)
            self.addCleanup(service.close)
            model = animated_model()
            key = digest(model)
            with service.db.connect() as db:
                db.execute('INSERT INTO icons(sha256,model) VALUES (?,?)', (key,model))
            cache = IconCache(service)
            # A previously cached fallback must not survive a renderer fix.
            (cache.directory / f'v1-{key}-animated.gif').write_bytes(encode_gif())
            first = cache.get(key)
            self.assertNotEqual(first, encode_gif())
            with patch('save_web.icons.encode_gif', side_effect=AssertionError('Should use disk cache')):
                self.assertEqual(cache.get(key), first)
            self.assertTrue((Path(directory) / 'icons' / f'{CACHE_VERSION}-{key}-animated.gif').is_file())
            with self.assertRaises(LookupError):
                cache.get('../outside')
            bad = b'broken model'
            with service.db.connect() as db:
                db.execute('INSERT INTO icons(sha256,model) VALUES (?,?)', (digest(bad),bad))
            self.assertEqual(cache.get(digest(bad)), cache.get('placeholder'))


class IconMigrationTest(unittest.TestCase):
    def test_version_two_adds_slot_observations_and_transfer_positions(self):
        import re
        old_schema = re.sub(r'CREATE TABLE IF NOT EXISTS save_slots \(.*?\);', '', SCHEMA, flags=re.S)
        old_schema = old_schema.replace(', source_slot INTEGER, destination_slot INTEGER', '')
        with tempfile.TemporaryDirectory() as directory:
            with sqlite3.connect(Path(directory) / 'inventory.sqlite3') as db:
                db.executescript(old_schema)
                db.execute('PRAGMA user_version=2')
                db.execute("INSERT INTO devices(name,kind) VALUES ('Existing library','PS2')")
            service = Service(directory)
            self.addCleanup(service.close)
            self.assertEqual(service.inventory()['devices'][0]['name'], 'Existing library')
            self.assertEqual(service.inventory()['slots'], [])
            with service.db.connect() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 5)
                columns = {row[1] for row in db.execute('PRAGMA table_info(transfers)')}
                self.assertTrue({'source_slot','destination_slot'} <= columns)

    def test_version_one_inventory_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            with sqlite3.connect(Path(directory) / 'inventory.sqlite3') as db:
                db.executescript(SCHEMA.replace('    icon_hash TEXT,\n', ''))
                db.execute('PRAGMA user_version=1')
                db.execute("INSERT INTO devices(name,kind) VALUES ('Original PS2','PS2')")
            service = Service(directory)
            self.addCleanup(service.close)
            self.assertEqual(service.inventory()['devices'][0]['name'], 'Original PS2')
            with service.db.connect() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 5)
                self.assertIn('icon_hash', [row[1] for row in db.execute('PRAGMA table_info(saves)')])
