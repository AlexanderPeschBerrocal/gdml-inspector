"""Configurable grouping of objects into subsystems and layers (chunks)."""

import json
import os
import subprocess
import sys

import pytest

from gdml_inspector import cli, lazy
from gdml_inspector.grouping import Grouping, GroupingError, load_rules

SCENE = """
<volume name="ecal1_vol"><materialref ref="G4_Fe"/><solidref ref="shape"/></volume>
<volume name="hcal1_vol"><materialref ref="G4_Fe2"/><solidref ref="shape"/></volume>
<volume name="si_vol_0x1a2b"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="fe_vol"><materialref ref="G4_Fe"/><solidref ref="shape"/></volume>
<volume name="calo"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="inner1_0x9f"><volumeref ref="si_vol_0x1a2b"/>
    <position x="5" unit="mm"/></physvol>
  <physvol name="inner2"><volumeref ref="fe_vol"/>
    <position x="15" unit="mm"/></physvol>
</volume>
<volume name="plate_vol"><materialref ref="G4_Al"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="ecal_a"><volumeref ref="ecal1_vol"/>
    <position x="-50" unit="mm"/></physvol>
  <physvol name="hcal_b"><volumeref ref="hcal1_vol"/>
    <position x="50" y="150" unit="mm"/></physvol>
  <physvol name="calo"><volumeref ref="calo"/>
    <position x="200" y="-30" unit="mm"/></physvol>
  <physvol name="plate"><volumeref ref="plate_vol"/>
    <position x="300" y="200" z="100" unit="mm"/></physvol>
</volume>"""


@pytest.fixture
def scene(write_gdml):
    return write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>', structure=SCENE
    )


def export(path, out, *options, expect=0):
    code = cli.main(
        [
            "--lazy",
            str(path),
            "--backend",
            "python",
            "--redact-paths",
            "--out",
            str(out),
            *options,
        ]
    )
    assert code == expect
    return lazy.read_existing_manifest(out) if expect == 0 else None


def layers(manifest):
    return sorted({(c["subsystem"], c["layer"]) for c in manifest["chunks"]})


def placements(out, manifest):
    from .test_lazy_placements import expand_placements

    return expand_placements(out, manifest)


def by_path(out, manifest):
    """object path -> (subsystem, layer) it was grouped into."""
    result = {}
    for obj in placements(out, manifest)["objects"]:
        chunk = manifest["chunks"][obj["chunkIndex"]]
        result[obj["path"]] = (chunk["subsystem"], chunk["layer"])
    return result


def test_the_default_is_the_original_cocoa_grouping(tmp_path, scene):
    default = export(scene, tmp_path / "a" / "v.html")
    explicit = export(scene, tmp_path / "b" / "v.html", "--grouping", "cocoa")
    assert layers(default) == layers(explicit)
    assert ("ECAL", "Barrel layer 1") in layers(default)
    assert ("HCAL", "Barrel layer 1") in layers(default)
    assert default["provenance"]["settings"]["grouping"]["strategy"] == "cocoa"
    assert (tmp_path / "a" / "v.html").read_bytes() == (
        tmp_path / "b" / "v.html"
    ).read_bytes()


def test_hierarchy_groups_by_path_below_the_world(tmp_path, scene):
    out = tmp_path / "v.html"
    manifest = export(scene, out, "--grouping", "hierarchy", "--group-depth", "2")
    grouped = by_path(out, manifest)
    assert grouped["world/ecal_a"] == ("ecal_a", "(direct)")
    assert grouped["world/calo/inner1_0x9f"] == ("calo", "inner1_")  # address stripped
    assert grouped["world/calo/inner2"] == ("calo", "inner2")
    one = export(
        scene,
        tmp_path / "d1" / "v.html",
        "--grouping",
        "hierarchy",
        "--group-depth",
        "1",
    )
    assert {s for s, _l in layers(one)} == {"ecal_a", "hcal_b", "calo", "plate"}
    assert {layer for _s, layer in layers(one)} == {"(direct)"}


def test_hierarchy_depth_three_extends_the_layer_path(tmp_path, scene):
    out = tmp_path / "v.html"
    manifest = export(scene, out, "--grouping", "hierarchy", "--group-depth", "3")
    assert by_path(out, manifest)["world/calo/inner2"] == (
        "calo",
        "inner2",
    )  # no deeper
    deep = by_path(out, manifest)
    assert deep["world/plate"] == ("plate", "(direct)")


def test_material_groups_by_material_and_volume(tmp_path, scene):
    out = tmp_path / "v.html"
    manifest = export(scene, out, "--grouping", "material")
    grouped = by_path(out, manifest)
    assert grouped["world/ecal_a"] == ("G4_Fe", "ecal1_vol")
    assert grouped["world/calo/inner2"] == ("G4_Fe", "fe_vol")
    assert grouped["world/calo/inner1_0x9f"] == ("G4_Si", "si_vol_")
    assert grouped["world/plate"] == ("G4_Al", "plate_vol")


def test_spatial_grouping_uses_grid_cells_including_negative_ones(tmp_path, scene):
    out = tmp_path / "v.html"
    manifest = export(scene, out, "--grouping", "spatial", "--grid-cell", "100")
    grouped = by_path(out, manifest)
    assert {s for s, _l in grouped.values()} == {"Grid 100 mm"}
    assert grouped["world/ecal_a"][1] == "x-1 y0 z0"  # centre at x = -50
    assert grouped["world/hcal_b"][1] == "x0 y1 z0"
    assert grouped["world/plate"][1] == "x3 y2 z1"
    assert grouped["world/calo/inner2"][1] == "x2 y-1 z0"


def test_none_puts_everything_in_one_layer(tmp_path, scene):
    manifest = export(scene, tmp_path / "v.html", "--grouping", "none")
    assert layers(manifest) == [("All", "All")]


def test_every_grouping_draws_the_same_scene(tmp_path, scene):
    reference = None
    for name, extra in (
        ("cocoa", []),
        ("hierarchy", []),
        ("material", []),
        ("spatial", ["--grid-cell", "100"]),
        ("none", []),
    ):
        out = tmp_path / name / "v.html"
        manifest = export(scene, out, "--grouping", name, *extra)
        objects = [
            (o["path"], o["material"], o["solid"], o["bbox"])
            for o in placements(out, manifest)["objects"]
        ]
        total = (manifest["objects"], manifest["vertices"], manifest["triangles"])
        if reference is None:
            reference = (objects, total)
        assert (objects, total) == reference, name


def write_rules(tmp_path, rules, name="rules.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"version": 1, "rules": rules}), encoding="utf-8")
    return path


def test_rules_apply_in_order_and_unmatched_objects_fall_through(tmp_path, scene):
    rules = write_rules(
        tmp_path,
        [
            {"match": {"path": "world/calo/*"}, "subsystem": "Calo", "layer": "inner"},
            {"match": {"material": "G4_Fe"}, "subsystem": "Iron"},
            {
                "match": {"material": "G4_*"},
                "subsystem": "Other stuff",
                "layer": "misc",
            },
        ],
    )
    out = tmp_path / "v.html"
    manifest = export(
        scene, out, "--grouping", "material", "--grouping-rules", str(rules)
    )
    grouped = by_path(out, manifest)
    # Matches the first rule although it is also iron (first match wins).
    assert grouped["world/calo/inner2"] == ("Calo", "inner")
    # Iron with no layer given: the strategy's own layer (the volume) is kept.
    assert grouped["world/ecal_a"] == ("Iron", "ecal1_vol")
    assert grouped["world/hcal_b"] == ("Other stuff", "misc")
    assert grouped["world/plate"] == ("Other stuff", "misc")


def test_rule_fields_combine_with_and_and_exact_is_literal(tmp_path, scene):
    rules = write_rules(
        tmp_path,
        [
            {"match": {"material": "G4_Fe", "volume": "hcal*"}, "subsystem": "never"},
            {
                "match": {"path": "world/calo/inner1_0x9f"},
                "subsystem": "Exact",
                "exact": True,
            },
            {
                "match": {"path": "world/calo/inner1_"},
                "subsystem": "Semantic",
                "exact": True,
            },
        ],
    )
    out = tmp_path / "v.html"
    manifest = export(scene, out, "--grouping", "none", "--grouping-rules", str(rules))
    grouped = by_path(out, manifest)
    assert grouped["world/ecal_a"] == ("All", "All")  # iron but not an hcal volume
    assert grouped["world/calo/inner1_0x9f"] == ("Exact", "All")


def test_a_pattern_also_matches_the_name_without_its_address(tmp_path, scene):
    rules = write_rules(
        tmp_path, [{"match": {"volume": "si_vol_"}, "subsystem": "Silicon"}]
    )
    out = tmp_path / "v.html"
    manifest = export(scene, out, "--grouping", "none", "--grouping-rules", str(rules))
    assert by_path(out, manifest)["world/calo/inner1_0x9f"][0] == "Silicon"


def test_known_subsystems_sort_first_then_custom_ones_by_name(tmp_path, scene):
    rules = write_rules(
        tmp_path,
        [
            {"match": {"path": "world/ecal_a"}, "subsystem": "Zeta"},
            {"match": {"path": "world/hcal_b"}, "subsystem": "Tracker"},
            {"match": {"path": "world/plate"}, "subsystem": "Alpha"},
        ],
    )
    manifest = export(
        scene, tmp_path / "v.html", "--grouping", "none", "--grouping-rules", str(rules)
    )
    order = []
    for chunk in manifest["chunks"]:
        if chunk["subsystem"] not in order:
            order.append(chunk["subsystem"])
    # Tracker has a fixed rank; the custom ones follow by name, each subsystem's
    # chunks together (they used to interleave when sorted by layer alone).
    assert order == ["Tracker", "All", "Alpha", "Zeta"]
    subsystems = [c["subsystem"] for c in manifest["chunks"]]
    assert subsystems == sorted(subsystems, key=order.index)


def test_awkward_subsystem_and_layer_names_still_make_loadable_chunks(tmp_path, scene):
    rules = write_rules(
        tmp_path,
        [
            {
                "match": {"path": "world/plate"},
                "subsystem": "Ünï/ sub #1",
                "layer": "a%b c",
            }
        ],
    )
    out = tmp_path / "v.html"
    manifest = export(scene, out, "--grouping", "none", "--grouping-rules", str(rules))
    entry = next(c for c in manifest["chunks"] if c["subsystem"] == "Ünï/ sub #1")
    from urllib.parse import unquote

    assert (out.parent / unquote(entry["url"])).is_file()
    assert entry["layer"] == "a%b c"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("not json", "not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"rules": 3}', '"rules" must be a list'),
        ('{"rules": [3]}', "rule 0 must be an object"),
        ('{"rules": [{"subsystem": "A"}]}', 'rule 0 needs a non-empty "match"'),
        ('{"rules": [{"match": {"colour": "x"}, "subsystem": "A"}]}', "unknown field"),
        (
            '{"rules": [{"match": {"path": "x"}}]}',
            'rule 0 needs a non-empty "subsystem"',
        ),
        ('{"rules": [{"match": {"path": ""}, "subsystem": "A"}]}', "non-empty string"),
        (
            '{"rules": [{"match": {"path": "x"}, "subsystem": "A", "why": 1}]}',
            "unknown key",
        ),
        ('{"version": 9, "rules": []}', '"version" must be 1'),
        (
            '{"rules": [{"match": {"path": "x"}, "subsystem": "a\\u001fb"}]}',
            "control characters",
        ),
    ],
)
def test_bad_rules_files_are_rejected_with_a_clear_message(
    tmp_path, scene, capsys, document, message
):
    path = tmp_path / "rules.json"
    path.write_text(document, encoding="utf-8")
    code = cli.main(
        [
            "--lazy",
            str(scene),
            "--backend",
            "python",
            "--out",
            str(tmp_path / "v.html"),
            "--grouping-rules",
            str(path),
        ]
    )
    assert code == 2
    error = capsys.readouterr().err
    assert "Invalid grouping" in error and message in error
    assert not (tmp_path / "v.html").exists()


def test_all_problems_in_a_rules_file_are_reported_at_once(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {"match": {"colour": "x"}, "subsystem": "A"},
                    {"match": {"path": "x"}},
                ]
            }
        )
    )
    with pytest.raises(GroupingError) as raised:
        load_rules(path)
    text = str(raised.value)
    assert "rule 0" in text and "rule 1" in text


def test_hostile_looking_patterns_are_just_text(tmp_path, scene):
    rules = write_rules(
        tmp_path,
        [{"match": {"path": "(a+)+$" + "a" * 40 + "["}, "subsystem": "Never"}],
    )
    export(
        scene, tmp_path / "v.html", "--grouping", "none", "--grouping-rules", str(rules)
    )


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--grouping", "hierarchy", "--group-depth", "0"], "--group-depth"),
        (["--grouping", "spatial", "--grid-cell", "0"], "--grid-cell"),
        (["--grouping", "spatial", "--grid-cell", "nan"], "--grid-cell"),
        (["--grouping-rules", "missing.json"], "cannot read"),
    ],
)
def test_bad_grouping_options_exit_with_a_message(
    tmp_path, scene, capsys, options, message
):
    code = cli.main(
        [
            "--lazy",
            str(scene),
            "--backend",
            "python",
            "--out",
            str(tmp_path / "v.html"),
            *options,
        ]
    )
    assert code == 2 and message in capsys.readouterr().err


def test_the_grouping_is_recorded_and_checked_by_reuse(tmp_path, scene, capsys):
    rules = write_rules(
        tmp_path, [{"match": {"path": "world/plate"}, "subsystem": "P"}]
    )
    out = tmp_path / "v.html"
    manifest = export(
        scene,
        out,
        "--grouping",
        "hierarchy",
        "--group-depth",
        "1",
        "--grouping-rules",
        str(rules),
    )
    recorded = manifest["provenance"]["settings"]["grouping"]
    assert recorded["strategy"] == "hierarchy" and recorded["groupDepth"] == 1
    assert recorded["rules"]["count"] == 1 and len(recorded["rules"]["sha256"]) == 64
    base = [
        "--lazy",
        str(scene),
        "--backend",
        "python",
        "--out",
        str(out),
        "--reuse-assets",
    ]
    assert cli.main(base) == 0  # nothing about grouping passed: not compared
    assert (
        cli.main(
            [
                *base,
                "--grouping",
                "hierarchy",
                "--group-depth",
                "1",
                "--grouping-rules",
                str(rules),
            ]
        )
        == 0
    )
    assert cli.main([*base, "--grouping", "material"]) == 4
    assert "--grouping" in capsys.readouterr().err
    assert cli.main([*base, "--group-depth", "3"]) == 4
    other = write_rules(
        tmp_path, [{"match": {"path": "world/plate"}, "subsystem": "Q"}], "other.json"
    )
    assert cli.main([*base, "--grouping-rules", str(other)]) == 4
    assert "rules" in capsys.readouterr().err


def test_grouping_is_identical_across_processes_with_different_hash_seeds(
    tmp_path, scene
):
    rules = write_rules(tmp_path, [{"match": {"material": "G4_*"}, "subsystem": "G4"}])
    runs = []
    for seed in ("1", "2"):
        directory = tmp_path / seed
        directory.mkdir()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "gdml_inspector",
                "--lazy",
                str(scene),
                "--backend",
                "python",
                "--redact-paths",
                "--grouping",
                "hierarchy",
                "--grouping-rules",
                str(rules),
                "--out",
                str(directory / "v.html"),
            ],
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        runs.append(
            {p.name: p.read_bytes() for p in sorted((directory / "v.assets").iterdir())}
        )
    assert runs[0] == runs[1]


def test_grouping_unit_behaviour_without_an_export():
    grouping = Grouping("hierarchy", depth=2)
    volume = lazy.base.VolumeDef("v_0x1", "G4_Fe", "s", None, 0)  # type: ignore[arg-type]
    assert grouping.assign(volume, "world/a_0x2/b", [0, 0, 0], [1, 1, 1], 0, 1) == (
        "a_",
        "b",
    )
    with pytest.raises(GroupingError):
        Grouping("sideways")
