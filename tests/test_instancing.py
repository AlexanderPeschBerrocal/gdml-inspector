"""Instanced chunk layout (format v3): decoded independently of the writer and
compared with the baked layout, mode semantics and the reuse/provenance rules."""

import json
import struct

import pytest

from gdml_inspector import cli, lazy

MESH = struct.Struct("<II4BII")


def export(path, out, *options):
    assert (
        cli.main(
            ["--lazy", str(path), "--backend", "python", "--out", str(out), *options]
        )
        == 0
    )
    return lazy.read_existing_manifest(out)


def chunk_bytes(out, manifest, entry):
    return (
        out.parent / f"{out.stem}.assets" / entry["url"].rsplit("/", 1)[-1]
    ).read_bytes()


def decode_instanced(data):
    """(meshes, instances) read straight from the documented byte layout."""
    magic, version, meshes, instances, vertices, pos, mat, ids, end = (
        struct.unpack_from("<8s8I", data)
    )
    assert (magic, version, end) == (b"GDMLCHN1", 3, len(data))
    table = [MESH.unpack_from(data, 40 + i * 20) for i in range(meshes)]
    positions = struct.unpack_from(f"<{vertices * 3}f", data, pos)
    matrices = struct.unpack_from(f"<{instances * 12}f", data, mat)
    id_values = struct.unpack_from(f"<{instances}I", data, ids)
    assert pos == 40 + 20 * meshes and mat == pos + 12 * vertices
    assert ids == mat + 48 * instances and end == ids + 4 * instances
    return table, positions, matrices, id_values


def decode_baked(data):
    magic, version, vertices, pos, color, ids, end = struct.unpack_from("<8s6I", data)
    assert (magic, version, end) == (b"GDMLCHN1", 2, len(data))
    return (
        struct.unpack_from(f"<{vertices * 3}f", data, pos),
        data[color:ids],
        struct.unpack_from(f"<{vertices}I", data, ids),
    )


def world_vertices_by_object(data):
    """object id -> world-space vertices, from an instanced chunk."""
    table, positions, matrices, ids = decode_instanced(data)
    result = {}
    for first, count, *_rest, inst_first, inst_count in (
        (t[0], t[1], t[2:6], t[6], t[7]) for t in table
    ):
        for k in range(inst_count):
            m = matrices[(inst_first + k) * 12 : (inst_first + k) * 12 + 12]
            points = []
            for i in range(count):
                x, y, z = positions[(first + i) * 3 : (first + i) * 3 + 3]
                points.append(
                    (
                        m[0] * x + m[1] * y + m[2] * z + m[3],
                        m[4] * x + m[5] * y + m[6] * z + m[7],
                        m[8] * x + m[9] * y + m[10] * z + m[11],
                    )
                )
            result[ids[inst_first + k] - 1] = points
    return result


def world_vertices_baked(data):
    positions, _colors, ids = decode_baked(data)
    result = {}
    for i, encoded in enumerate(ids):
        result.setdefault(encoded - 1, []).append(tuple(positions[i * 3 : i * 3 + 3]))
    return result


@pytest.fixture
def repeated(write_gdml):
    """Eight placements of one box, two of a tube: heavy repetition."""
    placements = "".join(
        f'<physvol name="b{i}"><volumeref ref="ecal1_leaf"/>'
        f'<position x="{i * 10}" y="0" z="0" unit="mm"/>'
        f'<rotation z="{i * 15}" unit="deg"/></physvol>'
        for i in range(8)
    ) + "".join(
        f'<physvol name="t{i}"><volumeref ref="ecal1_pipe"/>'
        f'<position x="{i * 10}" y="40" z="0" unit="mm"/></physvol>'
        for i in range(2)
    )
    return write_gdml(
        solids="""<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<tube name="tshape" rmin="0" rmax="3" z="10" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>""",
        structure=f"""
<volume name="ecal1_leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="ecal1_pipe"><materialref ref="G4_Si"/><solidref ref="tshape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>{placements}</volume>""",
    )


def test_instanced_chunk_decodes_to_exactly_the_baked_geometry(tmp_path, repeated):
    baked_out, inst_out = tmp_path / "b.html", tmp_path / "i.html"
    baked = export(repeated, baked_out, "--instancing", "off")
    inst = export(repeated, inst_out, "--instancing", "always")
    assert {c["layout"] for c in baked["chunks"]} == {"baked"}
    assert {c["layout"] for c in inst["chunks"]} == {"instanced"}
    # Same chunks, same logical sizes, in either layout.
    assert [(c["key"], c["vertices"], c["objects"]) for c in baked["chunks"]] == [
        (c["key"], c["vertices"], c["objects"]) for c in inst["chunks"]
    ]
    for b_entry, i_entry in zip(baked["chunks"], inst["chunks"], strict=True):
        expected = world_vertices_baked(chunk_bytes(baked_out, baked, b_entry))
        actual = world_vertices_by_object(chunk_bytes(inst_out, inst, i_entry))
        assert actual.keys() == expected.keys()
        for object_id, points in expected.items():
            assert len(actual[object_id]) == len(points)
            for got, want in zip(actual[object_id], points, strict=True):
                assert got == pytest.approx(want, abs=1e-3)


def test_each_distinct_mesh_is_stored_once_and_instances_are_grouped(
    tmp_path, repeated
):
    out = tmp_path / "i.html"
    manifest = export(
        repeated, out, "--instancing", "always", "--chunk-vertices", "100000"
    )
    sizes = {}
    for entry in manifest["chunks"]:
        table, positions, _m, ids = decode_instanced(chunk_bytes(out, manifest, entry))
        sizes[entry["layer"]] = (len(table), len(positions) // 3, len(ids))
        # Instances of one mesh are contiguous, covering every instance once.
        spans = sorted((t[6], t[7]) for t in table)
        assert spans[0][0] == 0
        assert all(a[0] + a[1] == b[0] for a, b in zip(spans, spans[1:], strict=False))
        assert sum(count for _first, count in spans) == len(ids)
        assert len(set(ids)) == len(ids)
    total_meshes = sum(m for m, _v, _i in sizes.values())
    total_instances = sum(i for _m, _v, i in sizes.values())
    assert (total_meshes, total_instances) == (2, 10)  # one box + one tube, ten objects
    assert manifest["chunkFormat"] == 3
    assert all(c["meshes"] >= 1 for c in manifest["chunks"])


def test_instanced_layout_is_much_smaller_when_shapes_repeat(tmp_path, repeated):
    baked = export(repeated, tmp_path / "b.html", "--instancing", "off")
    inst = export(repeated, tmp_path / "i.html", "--instancing", "always")
    assert sum(c["bytes"] for c in inst["chunks"]) < 0.6 * sum(
        c["bytes"] for c in baked["chunks"]
    )


def test_auto_instances_repeated_chunks_and_leaves_one_off_shapes_baked(
    tmp_path, write_gdml
):
    # ten copies of one tube (clear saving) and one unique box (no saving).
    copies = "".join(
        f'<physvol name="t{i}"><volumeref ref="hcal1_pipe"/>'
        f'<position x="{i * 10}" y="0" z="0" unit="mm"/></physvol>'
        for i in range(10)
    )
    path = write_gdml(
        solids="""<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<tube name="tshape" rmin="0" rmax="3" z="10" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>""",
        structure=f"""
<volume name="hcal1_pipe"><materialref ref="G4_Si"/><solidref ref="tshape"/></volume>
<volume name="ecal1_one"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>{copies}
  <physvol name="single"><volumeref ref="ecal1_one"/></physvol></volume>""",
    )
    out = tmp_path / "a.html"
    manifest = export(path, out, "--instancing", "auto", "--chunk-vertices", "100000")
    layouts = {c["subsystem"]: c["layout"] for c in manifest["chunks"]}
    assert layouts == {
        "HCAL": "instanced",
        "ECAL": "baked",
    }  # lone ECAL box stays baked


@pytest.mark.parametrize("mode", ["auto", "off", "always"])
def test_the_object_table_does_not_depend_on_the_layout(tmp_path, repeated, mode):
    reference = tmp_path / "ref.html"
    export(repeated, reference, "--instancing", "off")
    other = tmp_path / "other.html"
    export(repeated, other, "--instancing", mode)
    assets = lambda out: json.loads(  # noqa: E731
        (out.parent / f"{out.stem}.assets" / "placements.json").read_text()
    )
    assert assets(reference) == assets(other)


def test_meshes_are_shared_only_for_the_same_solid_and_colour(tmp_path, write_gdml):
    # All four objects land in one ECAL chunk. p1 and p2 share a solid and a
    # material; p3 has the same solid but another material (colour); p4 has the
    # same material as p1 but another solid.
    path = write_gdml(
        solids="""<box name="a" x="2" y="2" z="2" lunit="mm"/>
<box name="b" x="3" y="3" z="3" lunit="mm"/>""",
        structure="""
<volume name="ecal1_si"><materialref ref="G4_Si"/><solidref ref="a"/></volume>
<volume name="ecal1_si2"><materialref ref="G4_Si"/><solidref ref="a"/></volume>
<volume name="ecal1_fe"><materialref ref="G4_Fe"/><solidref ref="a"/></volume>
<volume name="ecal1_big"><materialref ref="G4_Si"/><solidref ref="b"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="p1"><volumeref ref="ecal1_si"/></physvol>
  <physvol name="p2"><volumeref ref="ecal1_si2"/><position x="10" unit="mm"/></physvol>
  <physvol name="p3"><volumeref ref="ecal1_fe"/><position x="20" unit="mm"/></physvol>
  <physvol name="p4"><volumeref ref="ecal1_big"/><position x="30" unit="mm"/></physvol>
</volume>""",
    )
    out = tmp_path / "m.html"
    manifest = export(path, out, "--instancing", "always", "--chunk-vertices", "100000")
    (entry,) = manifest["chunks"]
    table, _positions, _matrices, ids = decode_instanced(
        chunk_bytes(out, manifest, entry)
    )
    assert len(ids) == 4
    assert sorted(t[7] for t in table) == [1, 1, 2]  # p1+p2 share; p3 and p4 do not


def test_instancing_is_recorded_and_reuse_refuses_a_different_explicit_choice(
    tmp_path, repeated, capsys
):
    out = tmp_path / "r.html"
    manifest = export(repeated, out, "--instancing", "always")
    assert manifest["provenance"]["settings"]["instancing"] == "always"
    base = [
        "--lazy",
        str(repeated),
        "--backend",
        "python",
        "--out",
        str(out),
        "--reuse-assets",
    ]
    assert cli.main([*base, "--instancing", "off"]) == 4
    assert "instancing" in capsys.readouterr().err
    assert cli.main([*base, "--instancing", "always"]) == 0
    assert cli.main(base) == 0  # not passed, so not compared


def test_the_export_is_byte_for_byte_reproducible_with_instancing(tmp_path, repeated):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir(), second.mkdir()
    a = export(repeated, first / "v.html", "--instancing", "always", "--redact-paths")
    b = export(repeated, second / "v.html", "--instancing", "always", "--redact-paths")
    for x, y in zip(a["chunks"], b["chunks"], strict=True):
        assert chunk_bytes(first / "v.html", a, x) == chunk_bytes(
            second / "v.html", b, y
        )
    assert (first / "v.html").read_bytes() == (second / "v.html").read_bytes()


def test_a_bare_chunk_without_an_instance_table_is_never_instanced(tmp_path):
    from array import array

    chunk = lazy.Chunk(subsystem="s", layer="l", part=1)
    chunk.positions = array("f", [0, 0, 0, 1, 0, 0, 0, 1, 0])
    chunk.colors = array("B", [1, 2, 3, 255] * 3)
    assert lazy.use_instancing(chunk, "always") is False
    lazy.write_chunk(chunk, tmp_path / "c.bin", "always")
    assert struct.unpack_from("<8sI", (tmp_path / "c.bin").read_bytes())[1] == 1


def test_an_unknown_instancing_mode_is_rejected(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--lazy", "x.gdml", "--instancing", "sometimes"])
    assert "invalid choice" in capsys.readouterr().err


def test_auto_keeps_a_chunk_baked_when_instancing_would_not_save_enough():
    from array import array

    # Two instances of a two-triangle mesh: 272 bytes baked, 236 instanced.
    chunk = lazy.Chunk(subsystem="s", layer="l", part=1)
    triangle = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    chunk.mesh_tris = [[triangle, triangle]]
    chunk.mesh_rgba = [(1, 2, 3, 255)]
    chunk.instance_mesh = array("I", [0, 0])
    chunk.instance_ids = array("I", [1, 2])
    chunk.instance_matrices = array("f", [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0] * 2)
    chunk.positions = array("f", [0.0] * 36)
    chunk.object_count = 2
    assert lazy.baked_size(chunk) == 272 and lazy.instanced_size(chunk) == 236
    assert lazy.use_instancing(chunk, "auto") is False
    assert lazy.use_instancing(chunk, "always") is True
    assert lazy.use_instancing(chunk, "off") is False
    # Three instances (392 baked, 288 instanced) cross the 75% line.
    chunk.instance_mesh.append(0)
    chunk.instance_ids.append(3)
    chunk.instance_matrices.extend([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0])
    chunk.positions.extend([0.0] * 18)
    chunk.object_count = 3
    assert lazy.instanced_size(chunk) <= 0.75 * lazy.baked_size(chunk)
    assert lazy.use_instancing(chunk, "auto") is True
