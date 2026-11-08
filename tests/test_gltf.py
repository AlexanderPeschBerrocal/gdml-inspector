"""glTF/GLB export: an independently-decoded binary container (mirroring
test_chunks.py's own GDMLCHN1 precedent, never reusing the writer's code),
hierarchy/instancing/material correctness, and the CLI."""

import json
import struct

import pytest

from gdml_inspector import gltf

from .helpers import gltf_document

# --- Independent GLB decoder (no gltf.py code reused) -----------------------


def decode_glb(path):
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF"
    assert version == 2
    assert length == len(data)

    offset = 12
    json_length, json_type = struct.unpack_from("<I4s", data, offset)
    offset += 8
    assert json_type == b"JSON"
    assert json_length % 4 == 0
    json_bytes = data[offset : offset + json_length]
    offset += json_length

    bin_length, bin_type = struct.unpack_from("<I4s", data, offset)
    offset += 8
    assert bin_type == b"BIN\x00"
    assert bin_length % 4 == 0
    bin_bytes = data[offset : offset + bin_length]
    offset += bin_length

    assert offset == len(data), "trailing bytes after the declared BIN chunk"
    return json.loads(json_bytes), bin_bytes


def assert_structurally_valid(document, binary_length):
    """A hand-written check of the specific required-field subset this
    exporter uses, standing in for a full Khronos validator/schema (not
    available in this dependency-free, offline test environment; see
    docs/limitations.md)."""
    assert document["asset"]["version"] == "2.0"
    assert document["scene"] in range(len(document["scenes"]))
    for index in document["scenes"][document["scene"]]["nodes"]:
        assert 0 <= index < len(document["nodes"])
    node_count = len(document["nodes"])
    for node in document["nodes"]:
        if "mesh" in node:
            assert 0 <= node["mesh"] < len(document["meshes"])
        for child_index in node.get("children", []):
            assert 0 <= child_index < node_count
        if "matrix" in node:
            assert len(node["matrix"]) == 16
            assert "translation" not in node and "rotation" not in node
    for mesh in document["meshes"]:
        for primitive in mesh["primitives"]:
            assert primitive["mode"] == 4
            assert "indices" not in primitive
            position = primitive["attributes"]["POSITION"]
            assert 0 <= position < len(document["accessors"])
            assert 0 <= primitive["material"] < len(document["materials"])
    for accessor in document["accessors"]:
        assert accessor["componentType"] == 5126
        assert accessor["type"] == "VEC3"
        assert accessor["count"] % 3 == 0, "non-indexed triangles need 3n vertices"
        assert "min" in accessor and len(accessor["min"]) == 3
        assert "max" in accessor and len(accessor["max"]) == 3
        view = document["bufferViews"][accessor["bufferView"]]
        assert view["byteOffset"] + view["byteLength"] <= binary_length
    for view in document["bufferViews"]:
        buffer = document["buffers"][view["buffer"]]
        assert view["byteOffset"] + view["byteLength"] <= buffer["byteLength"]


def read_positions(document, binary_data, accessor_index):
    accessor = document["accessors"][accessor_index]
    view = document["bufferViews"][accessor["bufferView"]]
    offset = view["byteOffset"]
    count = accessor["count"]
    floats = struct.unpack_from(f"<{count * 3}f", binary_data, offset)
    return [floats[i : i + 3] for i in range(0, len(floats), 3)]


# --- Fixtures ----------------------------------------------------------------


def test_glb_header_and_structure_are_valid(example_gdml, tmp_path):
    gltf_json, binary_data, _builder = gltf_document(example_gdml)
    path = tmp_path / "out.glb"
    gltf.write_glb(gltf_json, binary_data, path)
    document, bin_bytes = decode_glb(path)
    assert document == gltf_json
    assert bin_bytes == binary_data
    assert_structurally_valid(document, len(bin_bytes))


def test_box_dimensions_round_trip_in_metres(single_volume, tmp_path):
    path = single_volume('<box name="shape" x="2" y="4" z="6" lunit="mm"/>')
    gltf_json, binary_data, _builder = gltf_document(path)
    out = tmp_path / "out.glb"
    gltf.write_glb(gltf_json, binary_data, out)
    document, bin_bytes = decode_glb(out)
    assert_structurally_valid(document, len(bin_bytes))

    leaf = next(n for n in document["nodes"] if n["name"] == "placed")
    points = read_positions(
        document,
        bin_bytes,
        document["meshes"][leaf["mesh"]]["primitives"][0]["attributes"]["POSITION"],
    )
    xs, ys, zs = zip(*points, strict=True)
    assert (min(xs), max(xs)) == pytest.approx((-0.001, 0.001))
    assert (min(ys), max(ys)) == pytest.approx((-0.002, 0.002))
    assert (min(zs), max(zs)) == pytest.approx((-0.003, 0.003))


def test_translation_is_converted_from_millimetres_to_metres(single_volume, tmp_path):
    path = single_volume(placement='<position x="10" y="20" z="30" unit="mm"/>')
    gltf_json, binary_data, _builder = gltf_document(path)
    out = tmp_path / "out.glb"
    gltf.write_glb(gltf_json, binary_data, out)
    document, _ = decode_glb(out)
    leaf = next(n for n in document["nodes"] if n["name"] == "placed")
    m = leaf["matrix"]
    assert (m[12], m[13], m[14]) == pytest.approx((0.01, 0.02, 0.03))
    assert (m[0], m[1], m[2]) == pytest.approx((1, 0, 0))  # untouched by unit scale


def test_repeated_placement_shares_one_mesh_index(write_gdml, tmp_path):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="one"><volumeref ref="leaf"/></physvol>
  <physvol name="two"><volumeref ref="leaf"/>
    <position x="10" y="0" z="0" unit="mm"/>
  </physvol>
</volume>""",
    )
    gltf_json, binary_data, _builder = gltf_document(path)
    out = tmp_path / "out.glb"
    gltf.write_glb(gltf_json, binary_data, out)
    document, bin_bytes = decode_glb(out)
    assert_structurally_valid(document, len(bin_bytes))

    one = next(n for n in document["nodes"] if n["name"] == "one")
    two = next(n for n in document["nodes"] if n["name"] == "two")
    assert one["mesh"] == two["mesh"]
    assert len(document["meshes"]) == 1
    assert len(document["accessors"]) == 1


def test_hidden_container_is_an_empty_pivot_with_nested_children(write_gdml, tmp_path):
    path = write_gdml(
        solids="""
<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<box name="envelope" x="100" y="100" z="100" lunit="mm"/>""",
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="group"><materialref ref="Galactic"/><solidref ref="envelope"/>
  <physvol name="child"><volumeref ref="leaf"/>
    <position x="10" y="0" z="0" unit="mm"/>
  </physvol>
</volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="parent"><volumeref ref="group"/>
    <position x="0" y="20" z="30" unit="mm"/>
  </physvol>
</volume>""",
    )
    gltf_json, binary_data, _builder = gltf_document(path)
    out = tmp_path / "out.glb"
    gltf.write_glb(gltf_json, binary_data, out)
    document, bin_bytes = decode_glb(out)
    assert_structurally_valid(document, len(bin_bytes))

    world = document["nodes"][document["scenes"][document["scene"]]["nodes"][0]]
    assert "mesh" not in world
    parent = document["nodes"][world["children"][0]]
    assert parent["name"] == "parent"
    assert "mesh" not in parent  # Galactic container, hidden by default
    assert (
        parent["matrix"][12],
        parent["matrix"][13],
        parent["matrix"][14],
    ) == pytest.approx((0.0, 0.02, 0.03))
    child = document["nodes"][parent["children"][0]]
    assert child["name"] == "child"
    assert "mesh" in child
    assert (
        child["matrix"][12],
        child["matrix"][13],
        child["matrix"][14],
    ) == pytest.approx((0.01, 0.0, 0.0))


def test_reflection_is_in_the_matrix_and_material_is_double_sided(write_gdml, tmp_path):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed"><volumeref ref="leaf"/>
    <scale x="-1" y="1" z="1"/>
  </physvol>
</volume>""",
    )
    gltf_json, binary_data, _builder = gltf_document(path)
    out = tmp_path / "out.glb"
    gltf.write_glb(gltf_json, binary_data, out)
    document, bin_bytes = decode_glb(out)
    assert_structurally_valid(document, len(bin_bytes))

    node = next(n for n in document["nodes"] if n["name"] == "placed")
    assert (node["matrix"][0], node["matrix"][1], node["matrix"][2]) == pytest.approx(
        (-1, 0, 0)
    )
    material = document["materials"][
        document["meshes"][node["mesh"]]["primitives"][0]["material"]
    ]
    assert material["doubleSided"] is True


def test_alpha_mode_follows_material_opacity(monkeypatch):
    # No material in MATERIAL_COLORS is fully opaque (alpha=255); this
    # exercises the _GltfDocument material builder directly for both
    # branches: translucent materials get an explicit alphaMode, while a
    # fully opaque one gets none, which glTF treats as its OPAQUE default.
    doc = gltf._GltfDocument()
    translucent = doc.materials[doc._material_index("Galactic")]
    assert translucent["doubleSided"] is True
    assert translucent["alphaMode"] == "BLEND"

    monkeypatch.setattr(gltf.base, "color_for_material", lambda name: (1, 2, 3, 255))
    opaque = doc.materials[doc._material_index("Opaque_stub")]
    assert opaque["doubleSided"] is True
    assert "alphaMode" not in opaque


def test_unsupported_solid_diagnostic_is_reported(single_volume):
    path = single_volume('<sphere name="shape" rmin="0" rmax="2"/>')
    _gltf_json, _binary_data, builder = gltf_document(path)
    assert any(
        "Unsupported solid type sphere" in message
        for message in builder.scene.warning_messages
    )


def test_gltf_external_mode_writes_a_matching_sibling_bin_file(example_gdml, tmp_path):
    gltf_json, binary_data, _builder = gltf_document(example_gdml)
    out = tmp_path / "out.gltf"
    gltf.write_gltf_external(gltf_json, binary_data, out)
    document = json.loads(out.read_text())
    assert document["buffers"][0]["uri"] == "out.bin"
    bin_path = tmp_path / "out.bin"
    assert bin_path.read_bytes() == binary_data
    assert document["buffers"][0]["byteLength"] == len(binary_data)


# --- CLI ----------------------------------------------------------------


def test_cli_exports_a_valid_glb(example_gdml, tmp_path):
    out = tmp_path / "out.glb"
    assert gltf.main([str(example_gdml), "--backend", "python", "--out", str(out)]) == 0
    document, bin_bytes = decode_glb(out)
    assert_structurally_valid(document, len(bin_bytes))


def test_cli_exports_gltf_plus_bin_by_extension(example_gdml, tmp_path):
    out = tmp_path / "out.gltf"
    assert gltf.main([str(example_gdml), "--backend", "python", "--out", str(out)]) == 0
    assert out.is_file()
    assert (tmp_path / "out.bin").is_file()


def test_cli_missing_file_returns_2(tmp_path, capsys):
    missing = tmp_path / "missing.gdml"
    assert gltf.main([str(missing), "--backend", "python"]) == 2
    assert "not found" in capsys.readouterr().err


def test_cli_missing_backend_returns_3(example_gdml, monkeypatch, tmp_path, capsys):
    import sys

    monkeypatch.setitem(sys.modules, "pyg4ometry", None)
    out = tmp_path / "out.glb"
    assert (
        gltf.main([str(example_gdml), "--backend", "pyg4ometry", "--out", str(out)])
        == 3
    )
    assert "pyg4ometry is not installed" in capsys.readouterr().err


def test_cli_report_writes_structured_diagnostics(single_volume, tmp_path):
    path = single_volume('<sphere name="shape" rmin="0" rmax="2"/>')
    report = tmp_path / "report.json"
    out = tmp_path / "out.glb"
    gltf.main(
        [
            str(path),
            "--backend",
            "python",
            "--out",
            str(out),
            "--report",
            str(report),
        ]
    )
    diagnostics = json.loads(report.read_text())
    assert any(d["code"] == "unsupported-solid-type" for d in diagnostics)
