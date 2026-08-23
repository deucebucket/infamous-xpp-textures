import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

import infamous_xpp_textures.cli as cli
import infamous_xpp_textures.source_correlation as source
from infamous_xpp_textures.cli import main
from infamous_xpp_textures.source_correlation import SourceCorrelationError


def _sha(value: int) -> str:
    return f"{value:064x}"


def _wrapped_character_xpp() -> bytes:
    payload = bytearray(0x900)
    record = 0x20
    struct.pack_into(
        ">15I",
        payload,
        record,
        0x400,
        0x400,
        0x480,
        0x480,
        0x500,
        0x500,
        0x580,
        0x580,
        0x00020000,
        0x000C0000,
        0x600,
        0x600,
        0x00110022,
        0x00330044,
        0x0005000C,
    )
    struct.pack_into(">6H", payload, 0x600, 0, 1, 2, 2, 3, 0)
    struct.pack_into(
        ">12I",
        payload,
        record + 0x58,
        4,
        1,
        0,
        0,
        0x00680000,
        0,
        0x01430020,
        0x01010100,
        0x02430038,
        0x01010100,
        0x03430050,
        0x01010100,
    )
    payload[0x400:0x428] = bytes(range(40))
    payload[0x480:0x482] = bytes((0x05, 0x70))
    segment_count = 1
    chunk_count = 2
    data_offset = 0x88 + segment_count * 28 + chunk_count * 16
    result = bytearray(data_offset + len(payload))
    result[:4] = b"PACK"
    struct.pack_into(">HH", result, 4, 8, 0x70)
    words = [0] * 10
    words[4] = 0x70
    words[5] = data_offset - 0x70
    words[8] = data_offset
    words[9] = len(payload)
    struct.pack_into(">10I", result, 8, *words)
    struct.pack_into(">QQQ", result, 0x70, segment_count, chunk_count, 0)
    struct.pack_into(">7I", result, 0x88, 0, len(payload), 0, 0, 0, 0, chunk_count)
    chunk_table = 0x88 + 28
    struct.pack_into(">4I", result, chunk_table, 0x01100000, 0x180, 0, 0)
    struct.pack_into(">4I", result, chunk_table + 16, 0x0B800000, 0x400, 0x400, 0)
    result[data_offset:] = payload
    return bytes(result)


def _block(number: int, payload_file: str, payload: bytes, *, first=0, stride=10):
    return SimpleNamespace(
        number=number,
        payload_file=payload_file,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_bytes=len(payload),
        stride=stride,
        range_first=first,
        range_count=len(payload) // stride,
    )


def _event(number: int, blocks: tuple, *, index_payload: bytes | None = None):
    payload = index_payload or struct.pack(">3H", 0, 1, 2)
    return SimpleNamespace(
        number=number,
        blocks=blocks,
        index_sha256=hashlib.sha256(payload).hexdigest(),
        index_count=len(payload) // 2,
        index_bytes=len(payload),
        index_payload_file=f"index-{number}-{hashlib.sha256(payload).hexdigest()[:8]}.bin",
        test_index_payload=payload,
    )


def _wire(monkeypatch, tmp_path: Path):
    page1 = tmp_path / "page1"
    page2 = tmp_path / "page2"
    page1.mkdir()
    page2.mkdir()
    a = b"A" * 10
    b = b"B" * 10
    c = b"C" * 10
    x = b"X" * 10
    hair = b"H" * 8 + b"I" * 8
    retail_a = struct.pack(">3H", 0, 1, 2)
    retail_x = struct.pack(">6H", 0, 1, 2, 2, 1, 0)
    retail_hair = struct.pack(">3H", 0, 1, 0)
    payloads = {
        (page1, "full-a.bin"): a + b + c,
        (page1, "shared.bin"): b + c,
        (page1, "other.bin"): b"Q" * 12,
        (page2, "full-x.bin"): x + b + c,
        (page2, "hair.bin"): hair,
    }
    records = [
        {
            "record_offset": 100,
            "vertex_count": 3,
            "index_count": 3,
            "index_sha256": hashlib.sha256(retail_a).hexdigest(),
            "index_payload": retail_a,
            "stream_zero_offset": 1000,
            "stream_zero_bytes": 30,
            "stream_zero_sha256": hashlib.sha256(a + b + c).hexdigest(),
            "payload": a + b + c,
        },
        {
            "record_offset": 200,
            "vertex_count": 3,
            "index_count": 6,
            "index_sha256": hashlib.sha256(retail_x).hexdigest(),
            "index_payload": retail_x,
            "stream_zero_offset": 2000,
            "stream_zero_bytes": 30,
            "stream_zero_sha256": hashlib.sha256(x + b + c).hexdigest(),
            "payload": x + b + c,
        },
        {
            "record_offset": 300,
            "vertex_count": 2,
            "index_count": 3,
            "index_sha256": hashlib.sha256(retail_hair).hexdigest(),
            "index_payload": retail_hair,
            "stream_zero_offset": 3000,
            "payload": hair,
        },
    ]
    first = {
        1: _event(
            1,
            (_block(1, "full-a.bin", a + b + c),),
            index_payload=retail_a,
        ),
        2: _event(2, (_block(1, "shared.bin", b + c, first=1),)),
        3: _event(3, (_block(1, "other.bin", b"Q" * 12, stride=12),)),
    }
    second = {
        1: _event(
            1,
            (_block(1, "full-x.bin", x + b + c),),
            index_payload=retail_x[:6],
        ),
        2: _event(
            2,
            (_block(1, "hair.bin", hair, stride=8),),
            index_payload=retail_hair,
        ),
    }
    for bundle, events in ((page1, first), (page2, second)):
        for event in events.values():
            payloads[(bundle, event.index_payload_file)] = event.test_index_payload
    monkeypatch.setattr(
        source,
        "_source_records",
        lambda _data: (
            {
                "source_sha256": _sha(500),
                "source_size": 30,
                "xpp_version": 8,
                "character_record_count": 3,
                "contract_coverage": "3/3",
            },
            records,
        ),
    )
    monkeypatch.setattr(
        source,
        "_load_page_chain",
        lambda *_args: (
            [{"page": 1}, {"page": 2}],
            [first, second],
            _sha(600),
        ),
    )
    monkeypatch.setattr(
        source,
        "_read_payload",
        lambda bundle, filename, _size, _digest: payloads[(bundle, filename)],
    )
    return page1, page2


def test_extracts_bounded_stream_zero_source_record():
    metadata, records = source._source_records(_wrapped_character_xpp())
    assert metadata["character_record_count"] == 1
    assert metadata["contract_coverage"] == "1/1"
    assert records[0]["record_offset"] == 0x20
    assert records[0]["vertex_count"] == 4
    assert records[0]["stream_zero_end_limit"] > records[0]["stream_zero_offset"]
    assert records[0]["payload"][:40] == bytes(range(40))


def test_classifies_unique_ambiguous_and_unmatched_events(monkeypatch, tmp_path: Path):
    page1, page2 = _wire(monkeypatch, tmp_path)
    args = (b"owned", "owned.xpp", (page1, page2), tmp_path / "allow", (None, None))
    first = source.correlate_paged_draws_to_xpp(*args)
    second = source.correlate_paged_draws_to_xpp(*args)
    assert first == second
    assert first["captured_draws"] == 5
    assert first["unique_exact_source_binding_events"] == 3
    assert first["ambiguous_source_binding_events"] == 1
    assert first["unmatched_runtime_events"] == 1
    assert first["mapped_source_record_count"] == 3
    assert first["source_record_coverage"] == "3/3"
    assert first["exact_full_index_source_record_offsets"] == [100, 300]
    assert first["runtime_index_subset_events"] == 3
    assert first["runtime_index_rejected_events"] == 0
    assert first["gates"]["runtime_index_retail_subset_validation"] is True
    assert [row["record_offset"] for row in first["record_index_coverage"]] == [
        100,
        200,
        300,
    ]
    record_200 = first["record_index_coverage"][1]
    assert record_200["union"]["covered_retail_triangle_occurrences"] == 1
    assert record_200["union"]["unobserved_retail_triangle_occurrences"] == 1
    statuses = [item["status"] for item in first["events"]]
    assert statuses == [
        "unique-exact-xpp-stream-zero-slice",
        "ambiguous-exact-xpp-stream-zero-slice",
        "no-exact-xpp-stream-zero-slice",
        "unique-exact-xpp-stream-zero-slice",
        "unique-exact-xpp-stream-zero-slice",
    ]
    assert first["events"][1]["mapping"] is None
    assert len(first["events"][1]["ambiguous_candidates"]) == 2
    assert first["events"][4]["mapping"]["stream_zero_record_bytes"] == 8
    assert first["gates"]["human_component_identity"] is False
    assert first["gates"]["full_character"] is False
    rendered = json.dumps(first, sort_keys=True)
    assert '"payload"' not in rendered
    assert "AAAAAAAAAA" not in rendered


def test_rejects_bad_source_label(monkeypatch, tmp_path: Path):
    page1, page2 = _wire(monkeypatch, tmp_path)
    with pytest.raises(SourceCorrelationError, match="plain filename"):
        source.correlate_paged_draws_to_xpp(
            b"owned", "a/b.xpp", (page1, page2), tmp_path / "allow", (None, None)
        )


def test_does_not_match_a_slice_past_the_bounded_source_record(
    monkeypatch, tmp_path: Path
):
    page = tmp_path / "page"
    page.mkdir()
    payload = b"A" * 20
    event = _event(1, (_block(1, "crosses.bin", payload),))
    monkeypatch.setattr(
        source,
        "_source_records",
        lambda _data: (
            {
                "source_sha256": _sha(500),
                "source_size": 10,
                "xpp_version": 8,
                "character_record_count": 1,
                "contract_coverage": "1/1",
            },
            [
                {
                    "record_offset": 100,
                    "vertex_count": 2,
                    "index_count": 3,
                    "index_sha256": hashlib.sha256(
                        event.test_index_payload
                    ).hexdigest(),
                    "index_payload": event.test_index_payload,
                    "stream_zero_offset": 1000,
                    "payload": b"A" * 10,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        source,
        "_load_page_chain",
        lambda *_args: ([{"page": 1}], [{1: event}], _sha(600)),
    )
    monkeypatch.setattr(source, "_read_payload", lambda *_args: payload)

    report = source.correlate_paged_draws_to_xpp(
        b"owned", "owned.xpp", (page,), tmp_path / "allow", (None,)
    )

    assert report["unique_exact_source_binding_events"] == 0
    assert report["unmatched_runtime_events"] == 1
    assert report["events"][0]["status"] == "no-exact-xpp-stream-zero-slice"


def test_source_binding_does_not_admit_out_of_retail_triangle_multiset(
    monkeypatch, tmp_path: Path
):
    page = tmp_path / "page"
    page.mkdir()
    vertex_payload = b"A" * 30
    retail_indices = struct.pack(">3H", 0, 1, 2)
    runtime_indices = struct.pack(">3H", 0, 2, 1)
    event = _event(
        1,
        (_block(1, "vertices.bin", vertex_payload),),
        index_payload=runtime_indices,
    )
    monkeypatch.setattr(
        source,
        "_source_records",
        lambda _data: (
            {
                "source_sha256": _sha(500),
                "source_size": 30,
                "xpp_version": 8,
                "character_record_count": 1,
                "contract_coverage": "1/1",
            },
            [
                {
                    "record_offset": 100,
                    "vertex_count": 3,
                    "index_count": 3,
                    "index_sha256": hashlib.sha256(retail_indices).hexdigest(),
                    "index_payload": retail_indices,
                    "stream_zero_offset": 1000,
                    "payload": vertex_payload,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        source,
        "_load_page_chain",
        lambda *_args: ([{"page": 1}], [{1: event}], _sha(600)),
    )
    payloads = {
        "vertices.bin": vertex_payload,
        event.index_payload_file: runtime_indices,
    }
    monkeypatch.setattr(
        source,
        "_read_payload",
        lambda _bundle, filename, _size, _digest: payloads[filename],
    )

    report = source.correlate_paged_draws_to_xpp(
        b"owned", "owned.xpp", (page,), tmp_path / "allow", (None,)
    )

    coverage = report["events"][0]["mapping"]["runtime_index_coverage"]
    assert coverage["status"] == "not-admitted"
    assert coverage["safe_for_retail_coverage_union"] is False
    assert "exceeds the retail record" in coverage["reason"]
    assert report["runtime_index_subset_events"] == 0
    assert report["runtime_index_rejected_events"] == 1
    assert report["record_index_coverage"] == []
    assert report["gates"]["runtime_index_retail_subset_validation"] is False


def test_partial_vertex_slice_rejects_an_index_that_escapes_the_slice(
    monkeypatch, tmp_path: Path
):
    page = tmp_path / "page"
    page.mkdir()
    source_vertices = b"A" * 40
    captured_vertices = source_vertices[:30]
    retail_indices = struct.pack(">3H", 0, 1, 3)
    event = _event(
        1,
        (_block(1, "vertices.bin", captured_vertices),),
        index_payload=retail_indices,
    )
    monkeypatch.setattr(
        source,
        "_source_records",
        lambda _data: (
            {
                "source_sha256": _sha(500),
                "source_size": 40,
                "xpp_version": 8,
                "character_record_count": 1,
                "contract_coverage": "1/1",
            },
            [
                {
                    "record_offset": 100,
                    "vertex_count": 4,
                    "index_count": 3,
                    "index_sha256": hashlib.sha256(retail_indices).hexdigest(),
                    "index_payload": retail_indices,
                    "stream_zero_offset": 1000,
                    "payload": source_vertices,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        source,
        "_load_page_chain",
        lambda *_args: ([{"page": 1}], [{1: event}], _sha(600)),
    )
    payloads = {
        "vertices.bin": captured_vertices,
        event.index_payload_file: retail_indices,
    }
    monkeypatch.setattr(
        source,
        "_read_payload",
        lambda _bundle, filename, _size, _digest: payloads[filename],
    )

    report = source.correlate_paged_draws_to_xpp(
        b"owned", "owned.xpp", (page,), tmp_path / "allow", (None,)
    )

    coverage = report["events"][0]["mapping"]["runtime_index_coverage"]
    assert coverage["status"] == "not-admitted"
    assert "escape the exact mapped vertex range" in coverage["reason"]
    assert report["runtime_index_subset_events"] == 0
    assert report["runtime_index_rejected_events"] == 1


def test_rejects_source_bounds(monkeypatch):
    with pytest.raises(SourceCorrelationError, match="empty"):
        source._source_records(b"")
    monkeypatch.setattr(source, "MAX_XPP_SOURCE_BYTES", 1)
    with pytest.raises(SourceCorrelationError, match="64 MiB"):
        source._source_records(b"xx")


def test_cli_writes_bounded_report_and_refuses_overwrite(
    monkeypatch, tmp_path: Path, capsys
):
    xpp = tmp_path / "owned.xpp"
    xpp.write_bytes(b"owned")
    page1 = tmp_path / "page1"
    page2 = tmp_path / "page2"
    page1.mkdir()
    page2.mkdir()
    exclusion = tmp_path / "page1.tsv"
    exclusion.write_bytes(b"capture_key\n")
    output = tmp_path / "source.json"

    def fake_census(data, name, bundles, allowlist, exclusions):
        assert data == b"owned"
        assert name == "owned.xpp"
        assert bundles == (page1, page2)
        assert allowlist == tmp_path / "allow"
        assert exclusions == (None, exclusion)
        return {"kind": "test-source-census", "mapped_source_record_count": 1}

    monkeypatch.setattr(cli, "correlate_paged_draws_to_xpp", fake_census)
    args = [
        "runtime-xpp-source-census",
        "--xpp",
        str(xpp),
        "--page-bundle",
        str(page1),
        "--page-capture-key-exclusion",
        "-",
        "--page-bundle",
        str(page2),
        "--page-capture-key-exclusion",
        str(exclusion),
        "--texture-allowlist",
        str(tmp_path / "allow"),
        "--json-out",
        str(output),
    ]
    assert main(args) == 0
    assert '"mapped_source_record_count": 1' in output.read_text(encoding="utf-8")
    assert main(args) == 1
    assert "refusing to overwrite" in capsys.readouterr().err


def test_cli_rejects_oversized_report(monkeypatch, tmp_path: Path, capsys):
    xpp = tmp_path / "owned.xpp"
    xpp.write_bytes(b"owned")
    page1 = tmp_path / "page1"
    page2 = tmp_path / "page2"
    page1.mkdir()
    page2.mkdir()
    output = tmp_path / "source.json"
    monkeypatch.setattr(
        cli, "correlate_paged_draws_to_xpp", lambda *_args: {"kind": "too-large"}
    )
    monkeypatch.setattr(cli, "MAX_SOURCE_CORRELATION_REPORT_BYTES", 1)
    assert (
        main(
            [
                "runtime-xpp-source-census",
                "--xpp",
                str(xpp),
                "--page-bundle",
                str(page1),
                "--page-capture-key-exclusion",
                "-",
                "--page-bundle",
                str(page2),
                "--page-capture-key-exclusion",
                str(tmp_path / "page1.tsv"),
                "--texture-allowlist",
                str(tmp_path / "allow"),
                "--json-out",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()
    assert "exceeds its byte bound" in capsys.readouterr().err
