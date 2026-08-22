"""Cross-build texture rebasing on synthetic XPPs. No retail bytes."""

from __future__ import annotations

import struct

import pytest

from infamous_xpp_textures.cli import main
from infamous_xpp_textures.heap import heap_bytes, read_records
from infamous_xpp_textures.pack import pack_chains
from infamous_xpp_textures.psarc import build_archive
from infamous_xpp_textures.rebase import RebaseError, rebase_texture_edits
from infamous_xpp_textures.xpp import parse_xpp


def _multi_xpp(chains: list[bytes]) -> bytes:
    descriptor_size = len(chains) * 0x70
    heap_size = (len(chains) - 1) * 128 + 8
    descriptors = bytearray(descriptor_size)
    texels = bytearray(heap_size)
    for index, chain in enumerate(chains):
        assert len(chain) == 8
        descriptor = index * 0x70
        address = descriptor_size + index * 128
        struct.pack_into(">III", descriptors, descriptor + 0x24, 4, 4, 1)
        struct.pack_into(">I", descriptors, descriptor + 0x40, address)
        struct.pack_into(">I", descriptors, descriptor + 0x44, 0x00018600)
        struct.pack_into(">I", descriptors, descriptor + 0x58, (4 << 16) | 4)
        texels[index * 128 : index * 128 + 8] = chain

    payload = bytes(descriptors + texels)
    data_offset = 0x88 + 28 + 32
    output = bytearray(data_offset + len(payload))
    output[:4] = b"PACK"
    struct.pack_into(">HH", output, 4, 8, 0x70)
    struct.pack_into(">I", output, 0x18, 0x70)
    struct.pack_into(">I", output, 0x1C, data_offset - 0x70)
    struct.pack_into(">I", output, 0x28, data_offset)
    struct.pack_into(">I", output, 0x2C, len(payload))
    struct.pack_into(">QQQ", output, 0x70, 1, 2, 0)
    struct.pack_into(">7I", output, 0x88, 0, len(payload), 0, 0, 0, 0, 2)
    struct.pack_into(">4I", output, 0x88 + 28, 0x03100000, descriptor_size, 0, 0)
    struct.pack_into(
        ">4I", output, 0x88 + 28 + 16, 0x0D800000, heap_size, descriptor_size, 0
    )
    output[data_offset:] = payload
    return bytes(output)


def _chains(data: bytes) -> list[bytes]:
    package = parse_xpp(data, len(data))
    records = read_records(data, package)
    texels = heap_bytes(data, package)
    return [
        texels[record.heap_offset : record.heap_offset + record.chain_bytes]
        for record in records
    ]


def test_rebase_maps_by_retail_chain_identity_not_ordinal():
    chain_a = bytes(range(8))
    chain_b = bytes(range(8, 16))
    source_retail = _multi_xpp([chain_a, chain_b])
    promoted = bytes(range(40))
    source_candidate = pack_chains(source_retail, {0: (8, 8, 2, promoted)})
    target_retail = _multi_xpp([chain_b, chain_a])

    output, report = rebase_texture_edits(
        source_retail, source_candidate, target_retail
    )

    records = read_records(output, parse_xpp(output, len(output)))
    assert (records[0].width, records[0].height, records[0].mips) == (4, 4, 1)
    assert (records[1].width, records[1].height, records[1].mips) == (8, 8, 2)
    assert _chains(output) == [chain_b, promoted]
    assert report["mapped_record_count"] == 1
    assert report["same_ordinal_mapping_count"] == 0
    assert report["target_unchanged_records_verified"] == 1
    assert report["direct_source_package_transfer_used"] is False


def test_rebase_detects_same_size_texture_edit():
    source_retail = _multi_xpp([bytes(8), bytes([1]) * 8])
    changed = bytes([9]) * 8
    source_candidate = pack_chains(source_retail, {0: (4, 4, 1, changed)})
    target_retail = _multi_xpp([bytes([1]) * 8, bytes(8)])

    output, report = rebase_texture_edits(
        source_retail, source_candidate, target_retail
    )

    assert _chains(output) == [bytes([1]) * 8, changed]
    assert report["source_changed_records"] == 1
    assert report["target_validation"]["promoted_records"] == 0


def test_rebase_zero_change_control_is_byte_identical():
    source_retail = _multi_xpp([bytes(8)])
    target_retail = _multi_xpp([bytes([2]) * 8])

    output, report = rebase_texture_edits(
        source_retail,
        source_retail,
        target_retail,
        allow_zero_change=True,
    )

    assert output == target_retail
    assert report["selected_record_count"] == 0
    assert report["output_byte_identical_to_target"] is True


def test_rebase_rejects_missing_target_identity():
    source_retail = _multi_xpp([bytes(8)])
    source_candidate = pack_chains(
        source_retail, {0: (4, 4, 1, bytes([1]) * 8)}
    )

    with pytest.raises(RebaseError, match="no exact target retail identity"):
        rebase_texture_edits(
            source_retail, source_candidate, _multi_xpp([bytes([2]) * 8])
        )


def test_rebase_rejects_ambiguous_target_identity():
    source_retail = _multi_xpp([bytes(8)])
    source_candidate = pack_chains(
        source_retail, {0: (4, 4, 1, bytes([1]) * 8)}
    )

    with pytest.raises(RebaseError, match="2 target retail identities"):
        rebase_texture_edits(
            source_retail, source_candidate, _multi_xpp([bytes(8), bytes(8)])
        )


def test_rebase_rejects_duplicate_selected_source_identity():
    source_retail = _multi_xpp([bytes(8), bytes(8)])
    source_candidate = pack_chains(
        source_retail, {0: (4, 4, 1, bytes([1]) * 8)}
    )

    with pytest.raises(RebaseError, match="source retail identities are not unique"):
        rebase_texture_edits(
            source_retail, source_candidate, _multi_xpp([bytes(8)])
        )


def test_rebase_rejects_selected_unchanged_record():
    source_retail = _multi_xpp([bytes(8), bytes([1]) * 8])
    source_candidate = pack_chains(
        source_retail, {0: (4, 4, 1, bytes([2]) * 8)}
    )

    with pytest.raises(RebaseError, match="no texture edit"):
        rebase_texture_edits(
            source_retail,
            source_candidate,
            _multi_xpp([bytes([1]) * 8, bytes(8)]),
            include_indices={1},
        )


def test_texture_rebase_cli_does_not_publish_failed_output(tmp_path):
    source_retail = tmp_path / "source-retail.xpp"
    source_candidate = tmp_path / "source-candidate.xpp"
    target_retail = tmp_path / "target-retail.xpp"
    output = tmp_path / "output.xpp"
    source_retail.write_bytes(_multi_xpp([bytes(8)]))
    source_candidate.write_bytes(
        pack_chains(source_retail.read_bytes(), {0: (4, 4, 1, bytes([1]) * 8)})
    )
    target_retail.write_bytes(_multi_xpp([bytes([2]) * 8]))

    result = main(
        [
            "texture-rebase",
            "--source-retail",
            str(source_retail),
            "--source-candidate",
            str(source_candidate),
            "--target-retail",
            str(target_retail),
            "--out",
            str(output),
        ]
    )

    assert result == 1
    assert not output.exists()


def test_texture_rebase_cli_reads_target_directly_from_psarc(tmp_path):
    source_retail = tmp_path / "source-retail.xpp"
    source_candidate = tmp_path / "source-candidate.xpp"
    target_archive = tmp_path / "target.psarc_s"
    output = tmp_path / "output.xpp"
    chain_a = bytes(8)
    chain_b = bytes([1]) * 8
    source_retail.write_bytes(_multi_xpp([chain_a, chain_b]))
    source_candidate.write_bytes(
        pack_chains(source_retail.read_bytes(), {0: (4, 4, 1, bytes([2]) * 8)})
    )
    target_archive.write_bytes(
        build_archive(["/target.xpp"], [_multi_xpp([chain_b, chain_a])])
    )

    result = main(
        [
            "texture-rebase",
            "--source-retail",
            str(source_retail),
            "--source-candidate",
            str(source_candidate),
            "--target-psarc",
            str(target_archive),
            "--target-entry",
            "/target.xpp",
            "--out",
            str(output),
            "--json",
        ]
    )

    assert result == 0
    assert _chains(output.read_bytes()) == [chain_b, bytes([2]) * 8]


def test_texture_rebase_cli_refuses_to_overwrite_target_retail(tmp_path):
    source_retail = tmp_path / "source-retail.xpp"
    source_candidate = tmp_path / "source-candidate.xpp"
    target_retail = tmp_path / "target-retail.xpp"
    source_retail.write_bytes(_multi_xpp([bytes(8)]))
    source_candidate.write_bytes(
        pack_chains(source_retail.read_bytes(), {0: (4, 4, 1, bytes([1]) * 8)})
    )
    original_target = _multi_xpp([bytes(8)])
    target_retail.write_bytes(original_target)

    result = main(
        [
            "texture-rebase",
            "--source-retail",
            str(source_retail),
            "--source-candidate",
            str(source_candidate),
            "--target-retail",
            str(target_retail),
            "--out",
            str(target_retail),
        ]
    )

    assert result == 1
    assert target_retail.read_bytes() == original_target


def test_texture_rebase_cli_refuses_to_overwrite_target_psarc(tmp_path):
    source_retail = tmp_path / "source-retail.xpp"
    source_candidate = tmp_path / "source-candidate.xpp"
    target_archive = tmp_path / "target.psarc_s"
    source_retail.write_bytes(_multi_xpp([bytes(8)]))
    source_candidate.write_bytes(
        pack_chains(source_retail.read_bytes(), {0: (4, 4, 1, bytes([1]) * 8)})
    )
    original_archive = build_archive(["/target.xpp"], [_multi_xpp([bytes(8)])])
    target_archive.write_bytes(original_archive)

    result = main(
        [
            "texture-rebase",
            "--source-retail",
            str(source_retail),
            "--source-candidate",
            str(source_candidate),
            "--target-psarc",
            str(target_archive),
            "--target-entry",
            "/target.xpp",
            "--out",
            str(target_archive),
        ]
    )

    assert result == 1
    assert target_archive.read_bytes() == original_archive
