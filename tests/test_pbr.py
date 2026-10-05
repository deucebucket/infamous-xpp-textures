from infamous_xpp_textures.pbr import derive_pbr


def test_derived_maps_match_size():
    w, h = 8, 4
    rgba = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            rgba[o] = x * 30
            rgba[o + 1] = y * 40
            rgba[o + 2] = 200
            rgba[o + 3] = 255
    maps = derive_pbr(w, h, bytes(rgba))
    assert maps.width == w and maps.height == h
    assert len(maps.normal) == w * h * 4
    assert len(maps.orm) == w * h * 4
    # normals are mostly blue (facing the camera)
    mid = (2 * w + 4) * 4
    assert maps.normal[mid + 2] > 200
    # near-white police/ambulance paint stays dielectric
    paint = bytearray(w * h * 4)
    for i in range(0, len(paint), 4):
        paint[i : i + 4] = b"\xe0\xe2\xe4\xff"
    painted = derive_pbr(w, h, bytes(paint))
    assert painted.orm[2] < 40
    # mid-gray bare metal may raise metal
    bare = bytearray(w * h * 4)
    for i in range(0, len(bare), 4):
        bare[i : i + 4] = b"\x9a\x9c\x9e\xff"
    metal = derive_pbr(w, h, bytes(bare))
    assert metal.orm[2] > 40
