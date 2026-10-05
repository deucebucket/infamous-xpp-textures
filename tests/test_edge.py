import struct

from infamous_xpp_textures.edge import header_from_words


def _f32(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", value))[0]


def test_skinned_header_accepts_hollow_ambulance_shape():
    words = [0] * 24
    words[0] = 10041
    words[1] = (120 << 16) | 3
    words[2] = 0x2EF70
    words[4:10] = [_f32(v) for v in (-19.5, -30.1, -30.1, 19.5, 30.1, 30.1)]
    words[13] = 0
    words[19] = 0xFFFFFFFF
    words[21] = 126
    header = header_from_words(tuple(words), 0x21D0)
    assert header is not None
    assert header.triangle_count == 120
    assert header.vertex_count == 126
    assert header.oid == 10041


def test_skinned_header_rejects_filled_static_pointers():
    words = [0] * 24
    words[1] = (10 << 16) | 3
    words[4:10] = [_f32(v) for v in (0, 0, 0, 1, 1, 1)]
    words[13] = 0x1000
    words[19] = 0x2000
    words[21] = 8
    assert header_from_words(tuple(words)) is None
