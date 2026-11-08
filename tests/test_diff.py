"""Backend-only geometry diff: matching, independent change classification,
and the #9-recommended fixture strategy (reordering, address-suffix renames,
and one-property-at-a-time edits)."""

import math

import pytest

from gdml_inspector import diff, geometry

from .helpers import diff_builder


def _placed_leaf_gdml(
    write_gdml,
    *,
    solid='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
    material="G4_Si",
    placement="",
):
    return write_gdml(
        solids=solid,
        structure=f"""
<volume name="leaf"><materialref ref="{material}"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed"><volumeref ref="leaf"/>{placement}</physvol>
</volume>""",
    )


def _report(path_a, path_b, tolerance=1e-6):
    scene_a = diff_builder(path_a).build()
    scene_b = diff_builder(path_b).build()
    return diff.build_report(path_a, path_b, scene_a, scene_b, tolerance)


def _only_changed_aspects(entry):
    return {k for k in entry if k not in {"pathA", "pathB", "semanticPath"}}


# --- Unit tests for the shared geometry.py utilities -----------------------


def test_semantic_path_strips_every_segments_address_suffix():
    assert geometry.semantic_path("world0x1/group0x2/leaf0x3") == "world/group/leaf"


def test_solid_params_normalizes_units_and_only_present_attributes():
    import xml.etree.ElementTree as ET

    box = ET.fromstring('<box x="2" y="4" z="6" lunit="cm"/>')
    assert geometry.solid_params(box) == {"x": 20.0, "y": 40.0, "z": 60.0}

    tube = ET.fromstring(
        '<tube rmin="1" rmax="3" z="4" startphi="0" deltaphi="90" aunit="deg"/>'
    )
    params = geometry.solid_params(tube)
    assert params["deltaphi"] == pytest.approx(math.pi / 2)
    assert "lowX" not in params

    boolean = ET.fromstring('<union><first ref="a"/><second ref="b"/></union>')
    assert geometry.solid_params(boolean) == {}


def test_source_digest_is_a_sha256_of_the_exact_file_bytes(tmp_path):
    path = tmp_path / "f.txt"
    path.write_bytes(b"hello")
    import hashlib

    expected = "sha256:" + hashlib.sha256(b"hello").hexdigest()
    assert geometry.source_digest(path) == expected


# --- Unit tests for diff.decompose_matrix -----------------------------------


def test_decompose_matrix_identity():
    translation, scale, rotation = diff.decompose_matrix(
        [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]
    )
    assert translation == [0, 0, 0]
    assert scale == pytest.approx([1, 1, 1])
    assert rotation == pytest.approx([1, 0, 0, 0, 1, 0, 0, 0, 1])


def test_decompose_matrix_separates_translation_from_a_pure_rotation():
    m = geometry.matmul(geometry.translate(10, 20, 30), geometry.rot_z(math.pi / 2))
    flat = [v for row in m[:3] for v in row]
    translation, scale, rotation = diff.decompose_matrix(flat)
    assert translation == pytest.approx([10, 20, 30])
    assert scale == pytest.approx([1, 1, 1])
    # rot_z(90deg) column-major: col0=(0,1,0), col1=(-1,0,0), col2=(0,0,1)
    assert rotation == pytest.approx([0, 1, 0, -1, 0, 0, 0, 0, 1], abs=1e-12)


def test_decompose_matrix_separates_scale_from_rotation():
    m = geometry.scale_matrix(2, 3, 1)
    flat = [v for row in m[:3] for v in row]
    translation, scale, rotation = diff.decompose_matrix(flat)
    assert translation == [0, 0, 0]
    assert scale == pytest.approx([2, 3, 1])
    assert rotation == pytest.approx([1, 0, 0, 0, 1, 0, 0, 0, 1])


# --- Matching algorithm ------------------------------------------------------


def test_match_objects_pairs_by_raw_path_first():
    a = [{"path": "world/a"}, {"path": "world/b"}]
    b = [{"path": "world/b"}, {"path": "world/a"}]
    matched, added, removed, ambiguous = diff.match_objects(a, b)
    assert len(matched) == 2
    assert added == removed == ambiguous == []


def test_match_objects_pairs_a_sole_leftover_on_each_side():
    # Address-suffix-insensitive grouping puts both under "world/leaf", but
    # raw paths differ; with nothing else to choose between, they pair.
    a = [{"path": "world/leaf0x1"}]
    b = [{"path": "world/leaf0x2"}]
    matched, added, removed, ambiguous = diff.match_objects(a, b)
    assert matched == [(a[0], b[0])]
    assert added == removed == ambiguous == []


def test_match_objects_flags_repeated_raw_paths_as_ambiguous():
    # Two anonymous placements share the same raw path on each side; there
    # is no principled way to say which corresponds to which.
    a = [{"path": "world/leaf"}, {"path": "world/leaf"}]
    b = [{"path": "world/leaf"}, {"path": "world/leaf"}]
    matched, added, removed, ambiguous = diff.match_objects(a, b)
    assert matched == []
    assert ambiguous == [
        {
            "semanticPath": "world/leaf",
            "pathsA": ["world/leaf", "world/leaf"],
            "pathsB": ["world/leaf", "world/leaf"],
        }
    ]


def test_match_objects_reports_pure_additions_and_removals():
    a = [{"path": "world/gone"}]
    b = [{"path": "world/new"}]
    matched, added, removed, ambiguous = diff.match_objects(a, b)
    assert matched == ambiguous == []
    assert added == b
    assert removed == a


# --- End-to-end report classification ---------------------------------------


def test_translation_only_change_is_classified_as_translation(write_gdml):
    path_a = _placed_leaf_gdml(
        write_gdml, placement='<position x="0" y="0" z="0" unit="mm"/>'
    )
    path_b = _placed_leaf_gdml(
        write_gdml, placement='<position x="10" y="0" z="0" unit="mm"/>'
    )
    report = _report(path_a, path_b)
    assert report["summary"] == {
        "added": 0,
        "removed": 0,
        "changed": 1,
        "ambiguous": 0,
        "unchanged": 1,
    }
    (entry,) = report["changed"]
    assert _only_changed_aspects(entry) == {"translation"}
    assert entry["translation"]["delta"] == pytest.approx(10.0)


def test_rotation_only_change_is_classified_as_orientation(write_gdml):
    path_a = _placed_leaf_gdml(write_gdml)
    path_b = _placed_leaf_gdml(
        write_gdml, placement='<rotation x="0" y="0" z="90" unit="deg"/>'
    )
    report = _report(path_a, path_b)
    (entry,) = report["changed"]
    assert _only_changed_aspects(entry) == {"orientation"}


def test_scale_magnitude_change_is_classified_as_scale(write_gdml):
    path_a = _placed_leaf_gdml(write_gdml)
    path_b = _placed_leaf_gdml(write_gdml, placement='<scale x="2" y="1" z="1"/>')
    report = _report(path_a, path_b)
    (entry,) = report["changed"]
    assert _only_changed_aspects(entry) == {"scale"}
    assert entry["scale"]["to"] == pytest.approx([2, 1, 1])


def test_reflection_without_a_magnitude_change_is_orientation_not_scale(write_gdml):
    # A reflection's column length is still 1, so it is a handedness change,
    # not a magnitude change -- and it must not be confused with a plain
    # 180-degree rotation, which also flips a column's sign with no
    # reflection involved at all.
    path_a = _placed_leaf_gdml(write_gdml)
    path_b = _placed_leaf_gdml(write_gdml, placement='<scale x="-1" y="1" z="1"/>')
    report = _report(path_a, path_b)
    (entry,) = report["changed"]
    assert _only_changed_aspects(entry) == {"orientation"}


def test_180_degree_rotation_is_orientation_not_a_false_scale_change(write_gdml):
    # A 180-degree rotation flips a column's sign along its own axis too,
    # exactly like a reflection does; it must still be classified as
    # orientation, not misread as a scale change.
    path_a = _placed_leaf_gdml(write_gdml)
    path_b = _placed_leaf_gdml(
        write_gdml, placement='<rotation x="0" y="0" z="180" unit="deg"/>'
    )
    report = _report(path_a, path_b)
    (entry,) = report["changed"]
    assert _only_changed_aspects(entry) == {"orientation"}


def test_dimension_only_change_reports_just_the_changed_parameter(write_gdml):
    path_a = _placed_leaf_gdml(write_gdml)
    path_b = _placed_leaf_gdml(
        write_gdml, solid='<box name="shape" x="2" y="5" z="6" lunit="mm"/>'
    )
    report = _report(path_a, path_b)
    (entry,) = report["changed"]
    assert _only_changed_aspects(entry) == {"dimensions"}
    assert entry["dimensions"] == {"y": {"from": 4.0, "to": 5.0}}


def test_material_only_change_is_classified_as_material(write_gdml):
    path_a = _placed_leaf_gdml(write_gdml, material="G4_Si")
    path_b = _placed_leaf_gdml(write_gdml, material="Galactic")
    report = _report(path_a, path_b)
    (entry,) = report["changed"]
    assert _only_changed_aspects(entry) == {"material"}
    assert entry["material"] == {"from": "G4_Si", "to": "Galactic"}


def test_solid_type_change_is_reported_without_a_dimension_comparison(write_gdml):
    path_a = _placed_leaf_gdml(write_gdml)
    path_b = _placed_leaf_gdml(
        write_gdml,
        solid='<tube name="shape" rmin="0" rmax="2" z="4" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>',
    )
    report = _report(path_a, path_b)
    (entry,) = report["changed"]
    assert _only_changed_aspects(entry) == {"solidType"}
    assert entry["solidType"] == {"from": "box", "to": "tube"}


def test_added_and_removed_placements_are_classified_independently(write_gdml):
    path_a = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="stays"><volumeref ref="leaf"/></physvol>
  <physvol name="removed_one"><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    path_b = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="stays"><volumeref ref="leaf"/></physvol>
  <physvol name="added_one"><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    report = _report(path_a, path_b)
    assert [o["path"] for o in report["removed"]] == ["world/removed_one"]
    assert [o["path"] for o in report["added"]] == ["world/added_one"]
    assert report["changed"] == []


def test_reordered_definitions_produce_no_difference(write_gdml):
    path_a = write_gdml(
        solids="""
<box name="a" x="2" y="4" z="6" lunit="mm"/>
<box name="b" x="1" y="1" z="1" lunit="mm"/>""",
        structure="""
<volume name="first"><materialref ref="G4_Si"/><solidref ref="a"/></volume>
<volume name="second"><materialref ref="G4_Si"/><solidref ref="b"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="one"><volumeref ref="first"/></physvol>
  <physvol name="two"><volumeref ref="second"/></physvol>
</volume>""",
    )
    path_b = write_gdml(
        solids="""
<box name="b" x="1" y="1" z="1" lunit="mm"/>
<box name="a" x="2" y="4" z="6" lunit="mm"/>""",
        structure="""
<volume name="second"><materialref ref="G4_Si"/><solidref ref="b"/></volume>
<volume name="first"><materialref ref="G4_Si"/><solidref ref="a"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="one"><volumeref ref="first"/></physvol>
  <physvol name="two"><volumeref ref="second"/></physvol>
</volume>""",
    )
    report = _report(path_a, path_b)
    assert report["summary"]["changed"] == 0
    assert report["summary"]["added"] == 0
    assert report["summary"]["removed"] == 0
    assert report["summary"]["ambiguous"] == 0


def test_address_suffix_only_rename_produces_no_difference(write_gdml):
    path_a = write_gdml(
        solids='<box name="shape0x1" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf0x1"><materialref ref="G4_Si"/><solidref ref="shape0x1"/></volume>
<volume name="world0x1"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed0x1"><volumeref ref="leaf0x1"/></physvol>
</volume>""",
        world="world0x1",
    )
    path_b = write_gdml(
        solids='<box name="shape0xAB" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf0xAB"><materialref ref="G4_Si"/><solidref ref="shape0xAB"/></volume>
<volume name="world0xAB"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed0xAB"><volumeref ref="leaf0xAB"/></physvol>
</volume>""",
        world="world0xAB",
    )
    report = _report(path_a, path_b)
    assert report["summary"] == {
        "added": 0,
        "removed": 0,
        "changed": 0,
        "ambiguous": 0,
        "unchanged": 2,
    }


def test_repeated_identically_named_placements_are_ambiguous_not_guessed(write_gdml):
    structure = """
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="leaf"/><position x="0" y="0" z="0" unit="mm"/></physvol>
  <physvol><volumeref ref="leaf"/><position x="10" y="0" z="0" unit="mm"/></physvol>
</volume>"""
    path_a = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>', structure=structure
    )
    moved_structure = structure.replace('z="0" unit="mm"/>', 'z="5" unit="mm"/>')
    path_b = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure=moved_structure,
    )
    report = _report(path_a, path_b)
    assert report["summary"]["changed"] == 0
    assert report["summary"]["ambiguous"] == 1
    (entry,) = report["ambiguous"]
    assert entry["semanticPath"] == "world/leaf"
    assert len(entry["pathsA"]) == 2
    assert len(entry["pathsB"]) == 2


def test_boolean_solid_is_reported_as_a_limitation_not_silently_unchanged(write_gdml):
    structure = """
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed"><volumeref ref="leaf"/></physvol>
</volume>"""
    solids = """
<box name="a" x="2" y="4" z="6" lunit="mm"/>
<box name="b" x="1" y="1" z="1" lunit="mm"/>
<union name="shape"><first ref="a"/><second ref="b"/></union>"""
    path_a = write_gdml(solids=solids, structure=structure)
    path_b = write_gdml(solids=solids, structure=structure)
    report = _report(path_a, path_b)
    assert report["summary"]["changed"] == 0
    assert any("Boolean union" in item for item in report["limitations"])


def test_tolerance_treats_small_differences_as_unchanged(write_gdml):
    path_a = _placed_leaf_gdml(
        write_gdml, placement='<position x="0" y="0" z="0" unit="mm"/>'
    )
    path_b = _placed_leaf_gdml(
        write_gdml, placement='<position x="0.0000001" y="0" z="0" unit="mm"/>'
    )
    report = _report(path_a, path_b, tolerance=1e-3)
    assert report["summary"]["changed"] == 0


# --- CLI --------------------------------------------------------------------


def test_cli_exit_code_0_for_identical_geometries(example_gdml):
    assert diff.main([str(example_gdml), str(example_gdml), "--backend", "python"]) == 0


def test_cli_exit_code_1_when_differences_are_found(write_gdml, capsys):
    path_a = _placed_leaf_gdml(write_gdml, material="G4_Si")
    path_b = _placed_leaf_gdml(write_gdml, material="Galactic")
    assert diff.main([str(path_a), str(path_b), "--backend", "python"]) == 1
    out = capsys.readouterr().out
    assert "Changed: 1" in out


def test_cli_exit_code_2_for_a_missing_file(tmp_path, example_gdml, capsys):
    missing = tmp_path / "missing.gdml"
    assert diff.main([str(missing), str(example_gdml), "--backend", "python"]) == 2
    assert "GDML file A not found" in capsys.readouterr().err


def test_cli_report_writes_the_full_json_structure(write_gdml, tmp_path):
    import json

    path_a = _placed_leaf_gdml(write_gdml, material="G4_Si")
    path_b = _placed_leaf_gdml(write_gdml, material="Galactic")
    report_path = tmp_path / "report.json"
    assert (
        diff.main(
            [
                str(path_a),
                str(path_b),
                "--backend",
                "python",
                "--report",
                str(report_path),
            ]
        )
        == 1
    )
    loaded = json.loads(report_path.read_text())
    assert loaded["summary"]["changed"] == 1
    assert loaded["changed"][0]["material"] == {"from": "G4_Si", "to": "Galactic"}
    assert loaded["digestA"].startswith("sha256:")
