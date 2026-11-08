"""Structured diagnostics: aggregation, serialization, and every emitted code."""

from gdml_inspector.geometry import (
    Scene,
    diagnostics_list,
    diagnostics_matching,
    diagnostics_summary,
    format_warnings,
)

from .helpers import lazy_builder, scene_builder


def test_repeated_diagnostics_aggregate_by_code_construct_and_identifier():
    scene = Scene()
    scene.diagnose(
        "missing-volume-reference",
        "error",
        "reference",
        "physvol",
        "Missing volume reference: absent",
        identifier="absent",
    )
    scene.diagnose(
        "missing-volume-reference",
        "error",
        "reference",
        "physvol",
        "Missing volume reference: absent",
        identifier="absent",
    )
    scene.diagnose(
        "missing-volume-reference",
        "error",
        "reference",
        "physvol",
        "Missing volume reference: other",
        identifier="other",
    )
    entries = diagnostics_list(scene.diagnostics)
    assert len(entries) == 2
    by_identifier = {entry["identifier"]: entry for entry in entries}
    assert by_identifier["absent"]["count"] == 2
    assert by_identifier["other"]["count"] == 1
    assert format_warnings(scene.diagnostics) == [
        "Missing volume reference: absent (x2)",
        "Missing volume reference: other",
    ]


def test_diagnostics_summary_tallies_occurrences_by_severity_and_category():
    scene = Scene()
    scene.diagnose("a", "error", "reference", "physvol", "a")
    scene.diagnose("a", "error", "reference", "physvol", "a")
    scene.diagnose("b", "warning", "numeric", "box", "b")
    summary = diagnostics_summary(scene.diagnostics)
    assert summary["bySeverity"] == {"error": 2, "warning": 1}
    assert summary["byCategory"]["reference"] == 2
    assert summary["byCategory"]["numeric"] == 1
    assert summary["byCategory"]["unsupported"] == 0


def test_diagnostics_matching_filters_by_severity_or_category():
    entries = [
        {"severity": "error", "category": "reference"},
        {"severity": "warning", "category": "numeric"},
        {"severity": "warning", "category": "budget"},
    ]
    assert diagnostics_matching(entries, {"error"}) == [entries[0]]
    assert diagnostics_matching(entries, {"numeric"}) == [entries[1]]
    assert diagnostics_matching(entries, {"error", "budget"}) == [
        entries[0],
        entries[2],
    ]
    assert diagnostics_matching(entries, {"backend"}) == []


def test_pyg4ometry_fallback_is_diagnosed_without_the_package_installed():
    from gdml_inspector.pyg4ometry_backend import Pyg4ometrySceneBuilder

    builder = Pyg4ometrySceneBuilder.__new__(Pyg4ometrySceneBuilder)
    builder.scene = Scene()
    builder._diagnose_fallback("shape", "pyg4ometry mesh failed for shape: boom")
    assert diagnostics_list(builder.scene.diagnostics) == [
        {
            "code": "pyg4ometry-fallback",
            "severity": "warning",
            "category": "backend",
            "construct": "solid",
            "identifier": "shape",
            "backend": "pyg4ometry",
            "message": "pyg4ometry mesh failed for shape: boom",
            "count": 1,
        }
    ]


_DIAGNOSING_SOLIDS = """
<box name="shape" x="unknown_name" y="4" z="6" lunit="mm"/>
<sphere name="odd" rmin="0" rmax="2"/>"""
_DIAGNOSING_STRUCTURE = """
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="odd_leaf"><materialref ref="G4_Si"/><solidref ref="odd"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="leaf"/></physvol>
  <physvol><volumeref ref="odd_leaf"/></physvol>
  <physvol><volumeref ref="absent"/></physvol>
  <physvol><volumeref ref="absent"/></physvol>
</volume>"""
_EXPECTED_DIAGNOSTICS = [
    {
        "code": "missing-volume-reference",
        "severity": "error",
        "category": "reference",
        "construct": "physvol",
        "identifier": "absent",
        "backend": "",
        "message": "Missing volume reference: absent",
        "count": 2,
    },
    {
        "code": "unevaluable-expression",
        "severity": "warning",
        "category": "numeric",
        "construct": "box",
        "identifier": "x",
        "backend": "",
        "message": "Could not evaluate GDML expression x='unknown_name' on <box>; "
        "using its default value.",
        "count": 1,
    },
    {
        "code": "unsupported-solid-type",
        "severity": "error",
        "category": "unsupported",
        "construct": "sphere",
        "identifier": "odd",
        "backend": "python",
        "message": "Unsupported solid type sphere: odd",
        "count": 1,
    },
]


def test_each_triggering_construct_produces_its_documented_diagnostic(write_gdml):
    path = write_gdml(solids=_DIAGNOSING_SOLIDS, structure=_DIAGNOSING_STRUCTURE)
    scene = scene_builder(path).build()
    assert scene["meta"]["diagnostics"] == _EXPECTED_DIAGNOSTICS
    assert scene["meta"]["diagnosticSummary"] == {
        "bySeverity": {"error": 3, "warning": 1},
        "byCategory": {
            "unsupported": 1,
            "reference": 2,
            "budget": 0,
            "numeric": 1,
            "backend": 0,
        },
    }


def test_lazy_mode_produces_the_same_structured_diagnostics(write_gdml):
    path = write_gdml(solids=_DIAGNOSING_SOLIDS, structure=_DIAGNOSING_STRUCTURE)
    builder = lazy_builder(path)
    builder.build_chunks()
    assert diagnostics_list(builder.scene.diagnostics) == _EXPECTED_DIAGNOSTICS


def test_duplicate_definition_and_limit_reached_have_their_documented_shape(
    write_gdml,
):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="leaf"/></physvol>
  <physvol><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    scene = scene_builder(path, max_objects=1).build()
    diagnostics = {d["code"]: d for d in scene["meta"]["diagnostics"]}
    assert diagnostics["duplicate-definition"]["identifier"] == "leaf"
    assert diagnostics["duplicate-definition"]["category"] == "reference"
    assert diagnostics["limit-reached"]["category"] == "budget"
    assert diagnostics["limit-reached"]["severity"] == "warning"
