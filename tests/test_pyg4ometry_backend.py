"""Integration coverage for the optional pyg4ometry backend -- skipped
entirely when the package isn't installed (see .github/workflows/
pyg4ometry.yml for the dedicated CI job that installs it). This is a
different, additive layer to tests/test_diagnostics.py's own
`_diagnose_fallback()` shape test, which needs no real pyg4ometry at all:
here the real package actually meshes real GDML files."""

import pytest

pytest.importorskip("pyg4ometry")

from gdml_inspector import lazy, standalone
from gdml_inspector.pyg4ometry_backend import Pyg4ometrySceneBuilder

from .helpers import DEFAULT_OPTIONS


def pyg4ometry_builder(path, **options):
    return Pyg4ometrySceneBuilder(path, **(DEFAULT_OPTIONS | options))


def test_pyg4ometry_backend_reports_identity_and_analytic_bounds(single_volume):
    path = single_volume('<box name="shape" x="2" y="4" z="6" lunit="mm"/>')
    scene = pyg4ometry_builder(path).build()
    meta = scene["meta"]
    assert meta["backend"] == "pyg4ometry"
    assert meta["objects"] == 1
    assert meta["vertices"] > 0
    assert meta["vertices"] % 3 == 0
    assert meta["bbox"] == pytest.approx([-1, -2, -3, 1, 2, 3])
    assert meta["warnings"] == []


def test_pyg4ometry_backend_tessellates_a_boolean_solid_unlike_the_python_approximation(
    single_volume,
):
    # Same fixture as test_parser.py's own
    # test_boolean_fallback_is_identified_as_an_approximation, which pins
    # the Python backend's first-operand approximation at 36 vertices with
    # an "approximated" warning for this exact shape -- this is the
    # concrete case that motivates the extra at all.
    path = single_volume("""
<box name="a" x="2" y="4" z="6" lunit="mm"/>
<box name="b" x="1" y="1" z="1" lunit="mm"/>
<subtraction name="shape"><first ref="a"/><second ref="b"/></subtraction>""")
    scene = pyg4ometry_builder(path).build()
    meta = scene["meta"]
    assert meta["backend"] == "pyg4ometry"
    assert meta["vertices"] > 36
    assert meta["warnings"] == []


def test_standalone_cli_actually_selects_the_pyg4ometry_backend(single_volume, capsys):
    path = single_volume('<box name="shape" x="2" y="4" z="6" lunit="mm"/>')
    exit_code = standalone.main(
        [str(path), "--backend", "pyg4ometry", "--validate-only"]
    )
    assert exit_code == 0
    assert "Using pyg4ometry backend" in capsys.readouterr().out


def test_lazy_cli_actually_selects_the_pyg4ometry_backend(single_volume, capsys):
    path = single_volume('<box name="shape" x="2" y="4" z="6" lunit="mm"/>')
    exit_code = lazy.main([str(path), "--backend", "pyg4ometry", "--validate-only"])
    assert exit_code == 0
    assert "Using pyg4ometry backend" in capsys.readouterr().out
