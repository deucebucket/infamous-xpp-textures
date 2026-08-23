import json
import math
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

import infamous_xpp_textures.screen_replay as screen_replay
from infamous_xpp_textures.screen_replay import (
    ScreenReplayError,
    export_screen_replay_glb,
    project_position_to_ndc,
)


def _matrix():
    return [
        [2.0, 0.0, 0.0, 0.0],
        [0.0, 3.0, 0.0, 0.0],
        [0.0, 0.0, 4.0, 0.0],
        [0.0, 0.0, 1.0, 2.0],
    ]


def _glb_document(payload: bytes) -> dict:
    _magic, _version, _length = struct.unpack_from("<III", payload)
    json_size, json_kind = struct.unpack_from("<II", payload, 12)
    assert json_kind == 0x4E4F534A
    return json.loads(payload[20 : 20 + json_size].decode("ascii"))


def _wire(monkeypatch, *, bundle_format="if1-texture-bound-topology-v3", positions=None):
    event = SimpleNamespace()
    monkeypatch.setattr(
        screen_replay,
        "_load_bundle",
        lambda _bundle, _allowlist: (
            {"format": bundle_format},
            {1: event},
            "a" * 64,
        ),
    )
    monkeypatch.setattr(screen_replay, "_event_payloads", lambda _bundle, _event: (b"p", b"c"))
    monkeypatch.setattr(
        screen_replay,
        "extract_output_affine",
        lambda _program, _constants: _matrix(),
    )
    monkeypatch.setattr(
        screen_replay,
        "_event_geometry",
        lambda _bundle, _event: (
            None,
            (0, 1, 2),
            positions or [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        ),
    )


def test_projects_clip_coordinates_with_exact_homogeneous_divide():
    assert project_position_to_ndc(_matrix(), (1.0, 2.0, 3.0)) == (
        0.4,
        1.2,
        2.4,
    )


@pytest.mark.parametrize(
    ("matrix", "message"),
    [
        ([[1.0, 0.0, 0.0, 0.0]] * 3 + [[0.0, 0.0, 0.0, 0.0]], "zero W"),
        ([[math.inf, 0.0, 0.0, 0.0]] + [[0.0, 1.0, 0.0, 0.0]] * 3, "non-finite"),
    ],
)
def test_rejects_invalid_clip_coordinates(matrix, message):
    with pytest.raises(ScreenReplayError, match=message):
        project_position_to_ndc(matrix, (1.0, 2.0, 3.0))


def test_exports_deterministic_screenshot_aligned_glb(monkeypatch, tmp_path: Path):
    _wire(monkeypatch)
    first = tmp_path / "first.glb"
    second = tmp_path / "second.glb"

    report1 = export_screen_replay_glb(Path("bundle"), Path("allow"), (1,), first)
    report2 = export_screen_replay_glb(Path("bundle"), Path("allow"), (1,), second)

    assert first.read_bytes() == second.read_bytes()
    assert report1 == report2
    assert report1["selected_events"] == [1]
    assert report1["vertices"] == 3
    assert report1["triangles"] == 1
    assert report1["gates"]["screenshot_aligned"] is True
    assert report1["gates"]["static_shader_reference"] is True
    assert report1["gates"]["component_ownership"] is False
    assert report1["gates"]["world_space"] is False
    assert report1["gates"]["mod_ready"] is False
    document = _glb_document(first.read_bytes())
    evidence = document["asset"]["extras"]["infamousScreenReplay"]
    assert document["asset"]["generator"].startswith("xpp-tool 2.16.0")
    assert evidence["screenshotAligned"] is True
    assert evidence["componentOwnershipProved"] is False
    assert evidence["fullCharacterProved"] is False


def test_preserves_v2_without_fragment_reference_claim(monkeypatch, tmp_path: Path):
    _wire(monkeypatch, bundle_format="if1-texture-bound-topology-v2")
    report = export_screen_replay_glb(
        Path("bundle"), Path("allow"), (1,), tmp_path / "v2.glb"
    )
    assert report["gates"]["static_shader_reference"] is False


def test_rejects_invalid_event_selections(monkeypatch, tmp_path: Path):
    _wire(monkeypatch)
    for events, message in (
        ((), "unique and non-empty"),
        ((1, 1), "unique and non-empty"),
        ((0,), "positive integers"),
        ((2,), "absent"),
    ):
        with pytest.raises(ScreenReplayError, match=message):
            export_screen_replay_glb(
                Path("bundle"), Path("allow"), events, tmp_path / f"{events}.glb"
            )


def test_rejects_overwrite_and_degenerate_geometry(monkeypatch, tmp_path: Path):
    _wire(monkeypatch)
    output = tmp_path / "exists.glb"
    output.write_bytes(b"keep")
    with pytest.raises(ScreenReplayError, match="already exists"):
        export_screen_replay_glb(Path("bundle"), Path("allow"), (1,), output)
    assert output.read_bytes() == b"keep"

    _wire(
        monkeypatch,
        positions=[(0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (2.0, 2.0, 0.0)],
    )
    with pytest.raises(ScreenReplayError, match="only degenerate"):
        export_screen_replay_glb(
            Path("bundle"), Path("allow"), (1,), tmp_path / "degenerate.glb"
        )
