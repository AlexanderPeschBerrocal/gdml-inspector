"""Per-placement metadata is held in columns, not one dict per placement, and
exports do not depend on the process (hash seed)."""

import os
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

from gdml_inspector import cli, hierarchy, lazy
from gdml_inspector.hierarchy import HierarchyColumns

from .helpers import DEFAULT_OPTIONS, hierarchy_nodes
from .test_benchmarks import load

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"


def test_node_dicts_keep_their_documented_key_order(nested_box):
    _scene, nodes = hierarchy_nodes(nested_box())
    base = [
        "path",
        "parentPath",
        "name",
        "semanticName",
        "volume",
        "semanticVolume",
        "isAssembly",
        "rendered",
    ]
    assert list(nodes["world"]) == base  # the root: no transform, nothing rendered
    assert list(nodes["world/parent"]) == [*base, "localMatrix"]
    assert list(nodes["world/parent/child"]) == [
        *base,
        "localMatrix",
        "objectId",
        "globalMatrix",
    ]


def test_matrices_are_always_floats(nested_box):
    _scene, nodes = hierarchy_nodes(nested_box())
    for node in nodes.values():
        for key in ("localMatrix", "globalMatrix"):
            assert all(type(v) is float for v in node.get(key, []))


def test_the_lazy_export_never_builds_the_per_node_dicts(
    tmp_path, monkeypatch, example_gdml
):
    def refuse(self):
        raise AssertionError("the lazy exporter must read the columns directly")

    monkeypatch.setattr(HierarchyColumns, "materialize", refuse)
    out = tmp_path / "v.html"
    assert (
        cli.main(
            ["--lazy", str(example_gdml), "--backend", "python", "--out", str(out)]
        )
        == 0
    )


def test_columns_cost_a_fraction_of_a_dict_per_placement(tmp_path):
    stress = load("stress_gdml")
    path = tmp_path / "s.gdml"
    path.write_text(stress.build(1500, 30, "flat", 50), encoding="utf-8")
    settings = DEFAULT_OPTIONS | {
        "chunk_vertices": 500_000,
        "max_objects": 100_000,
        "max_vertices": 500_000_000,
    }
    builder = lazy.LazyGDMLSceneBuilder(path, **settings)
    builder.parse()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        builder.traverse(
            builder.volumes[builder.world], hierarchy.base.identity(), builder.world
        )
        grown = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    chunks = sum(lazy.chunk_memory(c) for c in builder.chunks.values())
    per_placement = (grown - chunks) / builder.total_objects
    # Geometry excluded, the whole per-placement bookkeeping (tree, objects,
    # paths, caches) stays well under the ~2.3 KB the dict records cost.
    assert per_placement < 900, per_placement


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_exports_are_identical_across_processes_with_different_hash_seeds(
    tmp_path, mode
):
    """Set iteration order depends on the process's hash seed; nothing in an
    export may."""
    results = []
    for seed in ("1", "2", "3"):
        directory = tmp_path / seed
        directory.mkdir()
        env = {**os.environ, "PYTHONHASHSEED": seed}
        command = [
            sys.executable,
            "-m",
            "gdml_inspector",
            *mode,
            str(EXAMPLE),
            "--backend",
            "python",
            "--quality",
            "fast",
            "--redact-paths",
            "--include-containers",
            "--out",
            str(directory / "v.html"),
        ]
        subprocess.run(command, env=env, check=True, capture_output=True)
        files = {"v.html": (directory / "v.html").read_bytes()}
        assets = directory / "v.assets"
        if assets.exists():
            files |= {p.name: p.read_bytes() for p in sorted(assets.iterdir())}
        results.append(files)
    assert results[0] == results[1] == results[2]


def test_a_solids_parameters_follow_the_files_attribute_order(write_gdml):
    path = write_gdml(
        solids='<tube name="shape" z="10" rmax="3" rmin="0" deltaphi="360" startphi="0" aunit="deg" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="p"><volumeref ref="leaf"/></physvol></volume>""",
    )
    builder = hierarchy.HierarchyGDMLSceneBuilder(path, **DEFAULT_OPTIONS)
    builder.build()
    assert list(builder.solids_table()["shape"]["params"]) == [
        "z",
        "rmax",
        "rmin",
        "deltaphi",
        "startphi",
    ]
