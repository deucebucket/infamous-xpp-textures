import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

import infamous_xpp_textures.cli as cli
import infamous_xpp_textures.screen_page_merge as page_merge
from infamous_xpp_textures.cli import main
from infamous_xpp_textures.screen_replay import ScreenReplayError


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _matrix():
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _glb_document(payload: bytes) -> dict:
    _magic, _version, _length = struct.unpack_from("<III", payload)
    json_size, json_kind = struct.unpack_from("<II", payload, 12)
    assert json_kind == 0x4E4F534A
    return json.loads(payload[20 : 20 + json_size].decode("ascii"))


def test_global_page_palette_is_unique_across_the_full_bound():
    colors = [page_merge._diagnostic_color(index) for index in range(17 * 16)]
    assert len(set(colors)) == len(colors)
    assert all(0.0 <= component <= 1.0 for color in colors for component in color)


def _wire(monkeypatch, tmp_path: Path, *, overlap=False, first_limit=1):
    page1 = tmp_path / "page1"
    page2 = tmp_path / "page2"
    page1.mkdir(parents=True)
    page2.mkdir(parents=True)
    (page1 / "capture.complete").write_bytes(b"page-one-complete\n")
    (page2 / "capture.complete").write_bytes(b"page-two-complete\n")
    key1 = "a" * 64
    key2 = key1 if overlap else "b" * 64
    block = SimpleNamespace(range_count=3, attributes=({"attribute": 0},))
    exclusion = tmp_path / "page1-keys.tsv"
    exclusion.write_text(f"capture_key\n{key1}\n", encoding="ascii")
    events = {
        page1: {1: SimpleNamespace(capture_key=key1, blocks=(block,), index_count=3)},
        page2: {1: SimpleNamespace(capture_key=key2, blocks=(block,), index_count=3)},
    }

    def fake_load(bundle, _allowlist, capture_key_exclusion=None):
        if bundle == page1:
            return (
                {
                    "format": "if1-texture-bound-topology-v3",
                    "captured_draws": 1,
                    "capture_limit_reached": first_limit,
                },
                events[page1],
                "c" * 64,
            )
        assert bundle == page2
        assert capture_key_exclusion is not None
        return (
            {
                "format": "if1-texture-bound-topology-v4",
                "captured_draws": 1,
                "capture_limit_reached": 0,
                "exclusion_manifest_sha256": _sha(capture_key_exclusion.read_bytes()),
            },
            events[page2],
            "c" * 64,
        )

    monkeypatch.setattr(page_merge, "_load_bundle", fake_load)
    monkeypatch.setattr(
        page_merge, "_event_payloads", lambda _bundle, _event: (b"p", b"c")
    )
    monkeypatch.setattr(
        page_merge,
        "extract_output_affine",
        lambda _program, _constants: _matrix(),
    )
    monkeypatch.setattr(
        page_merge,
        "_event_geometry",
        lambda _bundle, _event: (
            None,
            (0, 1, 2),
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        ),
    )
    return page1, page2, exclusion


def test_merges_exact_page_chain_deterministically(monkeypatch, tmp_path):
    page1, page2, exclusion = _wire(monkeypatch, tmp_path)
    first = tmp_path / "first.glb"
    second = tmp_path / "second.glb"
    arguments = (
        (page1, page2),
        tmp_path / "allowlist",
        ((1,), (1,)),
        (None, exclusion),
    )
    first_report = page_merge.export_screen_replay_pages_glb(*arguments, first)
    second_report = page_merge.export_screen_replay_pages_glb(*arguments, second)
    assert first.read_bytes() == second.read_bytes()
    assert first_report == second_report
    assert first_report["page_count"] == 2
    assert first_report["selected_draws"] == 2
    assert first_report["captured_unique_keys"] == 2
    assert first_report["gates"]["exact_cumulative_exclusion_chain"] is True
    assert first_report["gates"]["full_character"] is False
    document = _glb_document(first.read_bytes())
    evidence = document["asset"]["extras"]["infamousPagedScreenReplay"]
    assert document["asset"]["generator"].startswith("xpp-tool 2.17.0")
    assert evidence["exactCaptureKeyChainProved"] is True
    assert [node["extras"]["page"] for node in document["nodes"]] == [1, 2]
    colors = [
        material["pbrMetallicRoughness"]["baseColorFactor"]
        for material in document["materials"]
    ]
    assert colors[0] != colors[1]


def test_rejects_overlapping_page_keys(monkeypatch, tmp_path):
    page1, page2, exclusion = _wire(monkeypatch, tmp_path, overlap=True)
    with pytest.raises(ScreenReplayError, match="overlap prior pages"):
        page_merge.export_screen_replay_pages_glb(
            (page1, page2),
            tmp_path / "allowlist",
            ((1,), (1,)),
            (None, exclusion),
            tmp_path / "bad.glb",
        )


def test_rejects_nonexact_chain_and_nonfinal_short_page(monkeypatch, tmp_path):
    page1, page2, exclusion = _wire(monkeypatch, tmp_path)
    wrong = tmp_path / "wrong.tsv"
    wrong.write_text(f"capture_key\n{'e' * 64}\n", encoding="ascii")
    with pytest.raises(ScreenReplayError, match="exact cumulative"):
        page_merge.export_screen_replay_pages_glb(
            (page1, page2),
            tmp_path / "allowlist",
            ((1,), (1,)),
            (None, wrong),
            tmp_path / "bad-chain.glb",
        )

    page1, page2, exclusion = _wire(monkeypatch, tmp_path / "short", first_limit=0)
    with pytest.raises(ScreenReplayError, match="non-final page"):
        page_merge.export_screen_replay_pages_glb(
            (page1, page2),
            tmp_path / "allowlist",
            ((1,), (1,)),
            (None, exclusion),
            tmp_path / "short.glb",
        )


def test_rejects_page_count_selection_and_overwrite(monkeypatch, tmp_path):
    with pytest.raises(ScreenReplayError, match="2 through 17"):
        page_merge.export_screen_replay_pages_glb(
            (tmp_path / "only",),
            tmp_path / "allowlist",
            ((1,),),
            (None,),
            tmp_path / "one.glb",
        )
    page1, page2, exclusion = _wire(monkeypatch, tmp_path)
    output = tmp_path / "exists.glb"
    output.write_bytes(b"keep")
    with pytest.raises(ScreenReplayError, match="refusing to overwrite"):
        page_merge.export_screen_replay_pages_glb(
            (page1, page2),
            tmp_path / "allowlist",
            ((1,), (1,)),
            (None, exclusion),
            output,
        )
    assert output.read_bytes() == b"keep"

    with pytest.raises(ScreenReplayError, match="outside every immutable"):
        page_merge.export_screen_replay_pages_glb(
            (page1, page2),
            tmp_path / "allowlist",
            ((1,), (1,)),
            (None, exclusion),
            page1 / "inside.glb",
        )


def test_rejects_declared_geometry_over_merge_bound(monkeypatch, tmp_path):
    page1, page2, exclusion = _wire(monkeypatch, tmp_path)
    huge = SimpleNamespace(
        range_count=page_merge._MAX_MERGED_VERTICES,
        attributes=({"attribute": 0},),
    )
    original_load = page_merge._load_bundle

    def oversized_load(bundle, allowlist, capture_key_exclusion=None):
        completion, events, allowlist_sha256 = original_load(
            bundle, allowlist, capture_key_exclusion
        )
        if bundle == page1:
            events[1].blocks = (huge,)
        return completion, events, allowlist_sha256

    monkeypatch.setattr(page_merge, "_load_bundle", oversized_load)
    with pytest.raises(ScreenReplayError, match="bounded merge extent"):
        page_merge.export_screen_replay_pages_glb(
            (page1, page2),
            tmp_path / "allowlist",
            ((1,), (1,)),
            (None, exclusion),
            tmp_path / "oversized.glb",
        )


def test_cli_parses_aligned_page_arguments_and_writes_report(
    monkeypatch, tmp_path, capsys
):
    page1 = tmp_path / "page1"
    page2 = tmp_path / "page2"
    page1.mkdir()
    page2.mkdir()
    exclusion = tmp_path / "keys.tsv"
    exclusion.write_text(f"capture_key\n{'a' * 64}\n", encoding="ascii")
    output = tmp_path / "merged.glb"
    report_path = tmp_path / "merged.json"

    def fake_export(bundles, allowlist, selections, exclusions, destination):
        assert bundles == (page1, page2)
        assert allowlist == tmp_path / "allowlist"
        assert selections == ((1,), (1,))
        assert exclusions == (None, exclusion)
        destination.write_bytes(b"glb")
        return {"kind": "test-page-merge", "selected_draws": 2}

    monkeypatch.setattr(cli, "export_screen_replay_pages_glb", fake_export)
    args = [
        "runtime-screen-position-page-merge",
        "--page-bundle",
        str(page1),
        "--page-events",
        "1",
        "--page-capture-key-exclusion",
        "-",
        "--page-bundle",
        str(page2),
        "--page-events",
        "1",
        "--page-capture-key-exclusion",
        str(exclusion),
        "--texture-allowlist",
        str(tmp_path / "allowlist"),
        "--output",
        str(output),
        "--json-out",
        str(report_path),
    ]
    assert main(args) == 0
    parsed = json.loads(report_path.read_text(encoding="utf-8"))
    assert parsed == {"kind": "test-page-merge", "selected_draws": 2}
    assert json.loads(capsys.readouterr().out) == parsed
