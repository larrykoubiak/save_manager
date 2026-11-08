import struct
import unittest

from vendor.mymcplusplus.mymcplusplus.ps2icon import Corrupt, FileTooSmall, Icon


def model(texture_type, texture):
    header = struct.pack('<5I', 0x10000, 1, texture_type, 0x3f800000, 3)
    vertices = b''.join(struct.pack('<4h4h2h4B', x, y, 0, 0,
                                    0, 0, 4096, 0, 0, 0, 255, 255, 255, 255)
                        for x, y in ((0, 0), (4096, 0), (0, 4096)))
    animation = struct.pack('<IIfII', 1, 1, 0.0, 0, 1) + struct.pack('<4I', 0, 1, 0, 0)
    return header + vertices + animation + texture


class IconTextureTest(unittest.TestCase):
    def test_ffx_type_six_and_type_seven_have_the_same_raw_texture(self):
        pixels = struct.pack('<H', 0x7fff) * (128 * 128)
        for flags in (0x06, 0x07):
            self.assertEqual(Icon(model(flags, pixels)).texture, pixels)

    def test_compression_bit_still_decodes_rle(self):
        rle = struct.pack('<IHH', 4, 128 * 128, 0x7fff)
        self.assertEqual(Icon(model(0x0e, rle)).texture, struct.pack('<H', 0x7fff) * (128 * 128))

    def test_textureless_model_uses_white(self):
        self.assertEqual(Icon(model(0x02, b'')).texture, [0xffff])

    def test_katamari_zero_length_runs_do_not_consume_a_pixel(self):
        stream = struct.pack('<7H', 16, 0x9afa, 0, 16368, 0x928d, 0, 0)
        icon = Icon(model(0x0f, struct.pack('<I', len(stream)) + stream))
        self.assertEqual(icon.texture, struct.pack('<H', 0x9afa) * 16 + struct.pack('<H', 0x928d) * 16368)

    def test_long_literal_runs_use_a_signed_pixel_count(self):
        pixels = struct.pack('<512H', *range(512))
        stream = struct.pack('<H', 0xfe00) + pixels + struct.pack('<HH', 16384 - 512, 0x7fff)
        icon = Icon(model(0x0e, struct.pack('<I', len(stream)) + stream))
        self.assertEqual(icon.texture, pixels + struct.pack('<H', 0x7fff) * (16384 - 512))

    def test_rle_rejects_incomplete_truncated_and_overflowing_textures(self):
        for stream in (struct.pack('<H', 0), struct.pack('<H', 1), struct.pack('<HH', 16385, 0)):
            with self.subTest(stream=stream), self.assertRaises(Corrupt):
                Icon(model(0x0e, struct.pack('<I', len(stream)) + stream))

    def test_animation_keeps_first_key_and_allows_zero_key_tracks(self):
        prefix = model(0x06, b'')[:20 + 3 * 24]
        animation = struct.pack('<IIfII', 1, 8, 1.0, 0, 2)
        animation += struct.pack('<II', 0, 0)
        animation += struct.pack('<IIff', 0, 1, 3.0, 0.75)
        pixels = struct.pack('<H', 0x7fff) * 16384
        icon = Icon(prefix + animation + pixels)
        self.assertEqual(icon.frames[0].keys, [])
        self.assertEqual([(key.time, key.value) for key in icon.frames[1].keys], [(3.0, 0.75)])
        self.assertEqual(icon.texture, pixels)

    def test_truncated_raw_texture_is_not_silently_accepted(self):
        with self.assertRaises(FileTooSmall):
            Icon(model(0x06, bytes(32767)))
