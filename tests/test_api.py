"""The public gdml_inspector.api module: placement/material/matrix identity,
mesh sharing by solid ref, bounds, diagnostics, resource-budget semantics,
and backend error behaviour. Repo-local slice of issue #12 -- no ROOT
Explorer adapter or shared-fixture parity here, see docs/limitations.md."""

import subprocess
import sys

import pytest

from gdml_inspector import api

from .helpers import api_geometry


def test_placements_report_path_material_and_solid(single_volume):
    path = single_volume(placement='<position x="10" y="20" z="30" unit="mm"/>')
    geometry = api_geometry(path)
    (placement,) = geometry.placements
    assert placement.path == "world/placed"
    assert placement.semantic_path == "world/placed"
    assert placement.volume == "leaf"
    assert placement.material == "G4_Si"
    assert placement.solid == "shape"
    assert (placement.matrix[0][3], placement.matrix[1][3], placement.matrix[2][3]) == (
        10.0,
        20.0,
        30.0,
    )


def test_repeated_placements_share_one_mesh_object(write_gdml):
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
    geometry = api_geometry(path)
    one = next(p for p in geometry.placements if p.path.endswith("one"))
    two = next(p for p in geometry.placements if p.path.endswith("two"))
    assert geometry.solids[one.solid] is geometry.solids[two.solid]
    assert len(geometry.solids) == 1


def test_bounds_match_the_placed_boxs_world_extent(single_volume):
    path = single_volume('<box name="shape" x="2" y="4" z="6" lunit="mm"/>')
    geometry = api_geometry(path)
    assert geometry.bounds.min == pytest.approx((-1.0, -2.0, -3.0))
    assert geometry.bounds.max == pytest.approx((1.0, 2.0, 3.0))


def test_world_triangles_match_the_bounds(single_volume):
    path = single_volume('<box name="shape" x="2" y="4" z="6" lunit="mm"/>')
    geometry = api_geometry(path)
    (placement,) = geometry.placements
    points = [
        point for tri in api.world_triangles(geometry, placement) for point in tri
    ]
    xs, ys, zs = zip(*points, strict=True)
    assert (min(xs), max(xs)) == pytest.approx((-1.0, 1.0))
    assert (min(ys), max(ys)) == pytest.approx((-2.0, 2.0))
    assert (min(zs), max(zs)) == pytest.approx((-3.0, 3.0))


def test_unsupported_solid_diagnostic_is_reported(single_volume):
    path = single_volume('<sphere name="shape" rmin="0" rmax="2"/>')
    geometry = api_geometry(path)
    assert geometry.placements == ()
    assert any(d.code == "unsupported-solid-type" for d in geometry.diagnostics)


def test_object_budget_truncates_with_a_diagnostic_not_an_exception(repeated_boxes):
    path = repeated_boxes(count=5)
    geometry = api_geometry(path, max_objects=2)
    assert len(geometry.placements) == 2
    assert any(d.code == "limit-reached" for d in geometry.diagnostics)


def test_include_containers_and_include_world_match_other_export_modes(nested_box):
    path = nested_box()
    default = api_geometry(path)
    default_paths = {p.path for p in default.placements}
    assert "world" not in default_paths
    assert "world/parent" not in default_paths  # "group" is a Galactic container

    everything = api_geometry(path, include_containers=True, include_world=True)
    everything_paths = {p.path for p in everything.placements}
    assert "world" in everything_paths
    assert "world/parent" in everything_paths
    assert "world/parent/child" in everything_paths


def test_backend_pyg4ometry_raises_module_not_found_when_unavailable(
    example_gdml, monkeypatch
):
    monkeypatch.setitem(sys.modules, "pyg4ometry", None)
    with pytest.raises(ModuleNotFoundError):
        api.Geometry.load(example_gdml, backend="pyg4ometry")


def test_backend_auto_falls_back_silently_when_pyg4ometry_is_unavailable(
    example_gdml, monkeypatch, capsys
):
    monkeypatch.setitem(sys.modules, "pyg4ometry", None)
    geometry = api.Geometry.load(example_gdml, backend="auto")
    assert geometry.backend == "python"
    assert capsys.readouterr() == ("", "")


def test_importing_api_does_not_require_pyg4ometry_streamlit_or_plotly():
    # gdml_inspector.pyg4ometry_backend is fine to have loaded (it has no
    # top-level pyg4ometry import itself); the actual optional packages
    # must not be pulled in just by importing gdml_inspector.api. Checked in
    # a fresh interpreter: this process may already have imported pyg4ometry
    # for an unrelated test when it is installed.
    code = (
        "import sys, gdml_inspector.api;"
        "bad = [n for n in ('pyg4ometry', 'streamlit', 'plotly') if n in sys.modules];"
        "sys.exit(1 if bad else 0)"
    )
    assert subprocess.run([sys.executable, "-I", "-c", code]).returncode == 0
