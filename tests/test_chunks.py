"""Independent decoding of the v1 binary format and asset reuse regressions."""

import struct
from array import array

import pytest

from gdml_inspector import cli, lazy

from .helpers import lazy_builder


def test_chunk_bytes_match_the_little_endian_format(tmp_path):
    chunk = lazy.Chunk(subsystem="test", layer="layer", part=1)
    positions = [1, 2, 3, 4, 2, 3, 1, 6, 3]
    colors = [10, 20, 30, 255] * 3
    chunk.positions = array("f", positions)
    chunk.colors = array("B", colors)
    output = tmp_path / "chunk.bin"
    assert lazy.write_chunk(chunk, output) == 80
    payload = output.read_bytes()
    assert struct.unpack_from("<8s6I", payload) == (b"GDMLCHN1", 1, 3, 32, 68, 80, 80)
    assert struct.unpack_from("<9f", payload, 32) == tuple(positions)
    assert payload[68:] == bytes(colors)


def test_a_chunk_without_ids_still_writes_the_v1_layout(tmp_path):
    chunk = lazy.Chunk(subsystem="test", layer="layer", part=1)
    chunk.positions = array("f", [1, 2, 3, 4, 2, 3, 1, 6, 3])
    chunk.colors = array("B", [10, 20, 30, 255] * 3)
    path = tmp_path / "old.bin"
    lazy.write_chunk(chunk, path)
    assert struct.unpack_from("<8s6I", path.read_bytes())[1] == 1


def test_a_chunk_with_ids_writes_the_v2_layout(tmp_path):
    chunk = lazy.Chunk(subsystem="test", layer="layer", part=1)
    chunk.positions = array("f", [1, 2, 3, 4, 2, 3, 1, 6, 3])
    chunk.colors = array("B", [10, 20, 30, 255] * 3)
    chunk.ids = array("I", [7, 7, 7])
    path = tmp_path / "new.bin"
    assert lazy.write_chunk(chunk, path) == 92
    payload = path.read_bytes()
    assert struct.unpack_from("<8s6I", payload) == (b"GDMLCHN1", 2, 3, 32, 68, 80, 92)
    assert struct.unpack_from("<3I", payload, 80) == (7, 7, 7)


def test_lazy_export_chunk_contents_and_reuse(tmp_path, monkeypatch, example_gdml):
    output = tmp_path / "lazy.html"
    args = [str(example_gdml), "--backend", "python", "--out", str(output)]
    assert (
        cli.main(
            ["--lazy", *args, "--chunk-vertices", "600", "--default-load", "Tracker"]
        )
        == 0
    )
    manifest = lazy.read_existing_manifest(output)
    assert manifest["objects"] == 5
    assert manifest["defaultLoad"] == ["Tracker"]
    assert manifest["chunks"]
    assert manifest["chunkFormat"] == 2
    before = {}
    seen_ids = set()
    for chunk in manifest["chunks"]:
        path = output.parent / chunk["url"]
        binary = path.read_bytes()
        magic, version, count, positions, colors, id_start, total = struct.unpack_from(
            "<8s6I", binary
        )
        assert magic == b"GDMLCHN1" and version == 2
        assert count == chunk["vertices"]
        assert positions == 32
        assert colors - positions == count * 3 * 4
        assert id_start - colors == count * 4
        assert total - id_start == count * 4  # one uint32 object id per vertex
        assert total == len(binary) == chunk["bytes"]
        ids = struct.unpack_from(f"<{count}I", binary, id_start)
        assert all(value >= 1 for value in ids), "0 is reserved for 'no object'"
        # The three vertices of a triangle, and every vertex of an object,
        # share one id, and an object's vertices are contiguous in its chunk.
        runs = [ids[0]] + [b for a, b in zip(ids, ids[1:], strict=False) if a != b]
        assert len(runs) == len(set(runs)) == chunk["objects"]
        seen_ids.update(value - 1 for value in ids)
        before[path] = binary
    assert seen_ids == set(range(manifest["objects"])), "ids are dense across chunks"

    def unexpected_build(*args, **kwargs):
        pytest.fail("Asset reuse must not rebuild geometry")

    monkeypatch.setattr(lazy.LazyGDMLSceneBuilder, "build_chunks", unexpected_build)
    assert cli.lazy_main([*args, "--reuse-assets", "--default-load", "all"]) == 0
    assert lazy.read_existing_manifest(output)["defaultLoad"] == ["all"]
    assert all(path.read_bytes() == binary for path, binary in before.items())
    next(iter(before)).unlink()
    assert cli.lazy_main([*args, "--reuse-assets"]) == 4


def test_chunking_preserves_complete_objects_and_global_bounds(repeated_boxes):
    # Coincident placements exercise one layer: a packing fixture, not an
    # overlap-free physical detector model.
    builder = lazy_builder(repeated_boxes(3, spacing=0), chunk_vertices=72)
    builder.build_chunks()
    assert builder.total_objects == 3
    assert builder.total_vertices == 108
    assert [chunk.vertex_count for chunk in builder.chunks.values()] == [72, 36]
    assert [chunk.object_count for chunk in builder.chunks.values()] == [2, 1]
    assert builder.global_bbox_min == [-1, -2, -3]
    assert builder.global_bbox_max == [1, 2, 3]


def test_binary_vertices_preserve_translated_box_bounds(single_volume, tmp_path):
    path = single_volume(placement='<position x="10" y="20" z="30" unit="mm"/>')
    builder = lazy_builder(path)
    builder.build_chunks()
    manifest = lazy.write_assets(builder, tmp_path / "view.html", [])
    assert manifest["bbox"] == [9, 18, 27, 11, 22, 33]
    assert manifest["objects"] == 1
    chunk = manifest["chunks"][0]
    data = (tmp_path / chunk["url"]).read_bytes()
    values = struct.unpack_from(f"<{chunk['vertices'] * 3}f", data, 32)
    for axis, low, high in ((0, 9, 11), (1, 18, 22), (2, 27, 33)):
        assert min(values[axis::3]) == low
        assert max(values[axis::3]) == high


def test_missing_embedded_manifest_is_rejected(tmp_path):
    path = tmp_path / "other.html"
    path.write_text("<html>No viewer manifest</html>")
    with pytest.raises(ValueError, match="manifest"):
        lazy.read_existing_manifest(path)
