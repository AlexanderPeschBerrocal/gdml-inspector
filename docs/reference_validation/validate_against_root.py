"""Cross-validate gdml-inspector's placement conventions against ROOT's TGeo
GDML importer, an independent reader from a different codebase.

Run inside an environment with PyROOT available, for example:

    source /path/to/a/checkout/with/setup_cvmfs.sh
    python3 docs/reference_validation/validate_against_root.py

This script is deliberately NOT part of the pytest suite or CI:
gdml-inspector is dependency-free by design (see docs/testing.md) and ROOT is
not installed in that environment. It exists to document, once, that the
placement conventions resolved across issue #1 (physvol rotation inversion,
named transform references, assembly expansion, scale/reflection, and
Boolean first-operand transforms) agree with an independent GDML reader, as
called for by that issue's acceptance criteria. gdml-inspector's own pytest
suite is what actually guards these conventions on every change; the
fixtures below mirror (rather than import) those regressions so this script
stays runnable on its own.

Caveat found while writing this: ROOT's TGDMLParse (as of ROOT 6.38, bundled
in CVMFS's LCG_109 view) silently drops an inline <position>/<rotation>/
<scale> element inside a physvol unless it has a `name` attribute, logging
"Position  not defined" and treating the transform as absent. GDML does not
require a name on inline elements, and Geant4's own G4GDMLReadStructure has
no such restriction. Every inline transform below is named purely to work
around this ROOT-side quirk; the name has no semantic effect on either
reader, and gdml-inspector treats named and unnamed inline transforms
identically.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import ROOT  # noqa: E402

from gdml_inspector.geometry import GDMLSceneBuilder  # noqa: E402

ROOT.gErrorIgnoreLevel = ROOT.kWarning

MATERIALS = """
<material name="Galactic" Z="1"><D value="1e-25" unit="g/cm3"/><atom value="1.01"/></material>
<material name="G4_Si" Z="14"><D value="2.33" unit="g/cm3"/><atom value="28.0855"/></material>"""


def gdml_document(*, define: str, solids: str, structure: str) -> str:
    return f"""<?xml version="1.0"?>
<gdml>
  <define>{define}</define>
  <materials>{MATERIALS}</materials>
  <solids>
    <box name="world_box" x="1000" y="1000" z="1000" lunit="mm"/>
    {solids}
  </solids>
  <structure>{structure}</structure>
  <setup name="Default" version="1.0"><world ref="world"/></setup>
</gdml>
"""


def gdml_inspector_bbox(gdml_text: str) -> list[float]:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".gdml", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(gdml_text)
        path = Path(handle.name)
    try:
        scene = GDMLSceneBuilder(
            path,
            quality="fast",
            include_containers=False,
            include_world=False,
            max_objects=100,
            max_vertices=100_000,
        ).build()
    finally:
        path.unlink()
    assert scene["meta"]["warnings"] == [], (
        f"unexpected warnings: {scene['meta']['warnings']}"
    )
    return scene["meta"]["bbox"]


def root_bbox(
    gdml_text: str, node_path: list[int], half_extent_mm: tuple[float, float, float]
) -> list[float]:
    """Return the AABB of a box with ``half_extent_mm`` at the global transform
    ROOT computes for ``node_path`` (daughter indices from the top volume),
    using ROOT's own rotation matrix and translation, independently of any
    gdml-inspector code."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".gdml", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(gdml_text)
        path = Path(handle.name)
    try:
        manager = ROOT.TGeoManager.Import(str(path))
        volume = manager.GetTopVolume()
        combined = ROOT.TGeoHMatrix()
        for index in node_path:
            node = volume.GetNode(index)
            combined.Multiply(node.GetMatrix())
            volume = node.GetVolume()
    finally:
        path.unlink()
    t = combined.GetTranslation()
    translation = [t[0] * 10.0, t[1] * 10.0, t[2] * 10.0]  # cm -> mm
    r = combined.GetRotationMatrix()
    hx, hy, hz = half_extent_mm
    # Standard AABB-of-a-transformed-box formula: it holds for any rotation
    # or reflection, not only the 90/180-degree cases exercised here.
    half = [
        abs(r[0]) * hx + abs(r[1]) * hy + abs(r[2]) * hz,
        abs(r[3]) * hx + abs(r[4]) * hy + abs(r[5]) * hz,
        abs(r[6]) * hx + abs(r[7]) * hy + abs(r[8]) * hz,
    ]
    return [translation[i] - half[i] for i in range(3)] + [
        translation[i] + half[i] for i in range(3)
    ]


def assert_close(label: str, actual: list[float], expected: list[float]) -> None:
    ok = all(abs(a - e) < 1e-6 for a, e in zip(actual, expected, strict=True))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}: gdml-inspector={actual} root={expected}")
    assert ok, f"{label}: gdml-inspector={actual} vs ROOT={expected}"


def check_rotation_inversion() -> None:
    # Mirrors tests/test_transforms.py::test_nested_placements_have_analytic_world_bounds
    gdml = gdml_document(
        define="",
        solids="""
<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<box name="envelope" x="100" y="100" z="100" lunit="mm"/>""",
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="group"><materialref ref="Galactic"/><solidref ref="envelope"/>
  <physvol name="child"><volumeref ref="leaf"/>
    <position name="child_pos" x="5" y="0" z="0" unit="mm"/>
  </physvol>
</volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="parent"><volumeref ref="group"/>
    <position name="parent_pos" x="10" y="20" z="30" unit="mm"/>
    <rotation name="parent_rot" x="0" y="0" z="90" unit="deg"/>
  </physvol>
</volume>""",
    )
    assert_close(
        "physvol rotation inversion (90deg)",
        gdml_inspector_bbox(gdml),
        root_bbox(gdml, [0, 0], (1.0, 2.0, 3.0)),
    )


def check_named_position_reference() -> None:
    # Mirrors tests/test_parser.py::test_named_position_reference_moves_the_placed_volume
    gdml = gdml_document(
        define='<position name="shift" x="10" y="20" z="30" unit="mm"/>',
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed"><volumeref ref="leaf"/><positionref ref="shift"/></physvol>
</volume>""",
    )
    assert_close(
        "named positionref resolution",
        gdml_inspector_bbox(gdml),
        root_bbox(gdml, [0], (1.0, 2.0, 3.0)),
    )


def check_assembly_expansion() -> None:
    # Mirrors tests/test_parser.py::test_assembly_placements_are_relative_to_the_assembly_itself
    gdml = gdml_document(
        define="",
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<assembly name="group"><physvol><volumeref ref="leaf"/>
  <position name="leaf_pos" x="10" y="0" z="0" unit="mm"/>
</physvol></assembly>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="group"/></physvol>
</volume>""",
    )
    assert_close(
        "assembly expansion",
        gdml_inspector_bbox(gdml),
        root_bbox(gdml, [0, 0], (1.0, 2.0, 3.0)),
    )


def check_scale_reflection() -> None:
    # Mirrors tests/test_transforms.py::test_scale_reflects_the_placed_subtree_before_translation
    gdml = gdml_document(
        define="",
        solids="""
<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<box name="envelope" x="100" y="100" z="100" lunit="mm"/>""",
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="group"><materialref ref="Galactic"/><solidref ref="envelope"/>
  <physvol name="child"><volumeref ref="leaf"/>
    <position name="child_pos" x="5" y="0" z="0" unit="mm"/>
  </physvol>
</volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="parent"><volumeref ref="group"/>
    <position name="parent_pos" x="10" y="20" z="30" unit="mm"/>
    <scale name="flip" x="-1" y="1" z="1"/>
  </physvol>
</volume>""",
    )
    assert_close(
        "scale/reflection composition",
        gdml_inspector_bbox(gdml),
        root_bbox(gdml, [0, 0], (1.0, 2.0, 3.0)),
    )


def check_boolean_first_operand() -> None:
    # Mirrors tests/test_parser.py::test_union_first_operand_transform_is_applied_before_combining
    gdml = gdml_document(
        define="",
        solids="""
<box name="a" x="2" y="4" z="6" lunit="mm"/>
<box name="b" x="4" y="2" z="2" lunit="mm"/>
<union name="shape">
  <first ref="a"/>
  <firstposition name="fp" x="-5" y="0" z="0" unit="mm"/>
  <second ref="b"/>
</union>""",
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    expected_bbox = gdml_inspector_bbox(gdml)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".gdml", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(gdml)
        path = Path(handle.name)
    try:
        manager = ROOT.TGeoManager.Import(str(path))
        shape = manager.GetTopVolume().GetNode(0).GetVolume().GetShape()
        bool_node = shape.GetBoolNode()
        ft = bool_node.GetLeftMatrix().GetTranslation()
        first_translation_mm = [ft[0] * 10.0, ft[1] * 10.0, ft[2] * 10.0]
    finally:
        path.unlink()

    # ROOT applies a Boolean solid's firstposition as given, not inverted,
    # the same convention solid_mesh() uses (see geometry.py's comment citing
    # G4GDMLReadSolids::BooleanRead).
    assert_close(
        "Boolean first-operand firstposition (as given, not inverted)",
        first_translation_mm,
        [-5.0, 0.0, 0.0],
    )
    print(f"  (gdml-inspector's combined bbox for the same fixture: {expected_bbox})")


def main() -> None:
    checks = [
        check_rotation_inversion,
        check_named_position_reference,
        check_assembly_expansion,
        check_scale_reflection,
        check_boolean_first_operand,
    ]
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            failures += 1
            print(f"  -> {error}")
    if failures:
        print(f"\n{failures} of {len(checks)} checks disagreed with ROOT.")
        sys.exit(1)
    print(f"\nAll {len(checks)} checks agree with ROOT's TGeo GDML importer.")


if __name__ == "__main__":
    main()
