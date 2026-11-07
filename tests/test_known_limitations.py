"""Desired behaviour for tracked gaps, not passing snapshots of incorrect output.

Only assertion failures are expected: unrelated crashes remain failures.
Strict XPASS requires removing the marker when a linked issue fixes the gap.
"""

import math

import pytest

from gdml_inspector import geometry

from .helpers import scene_builder


@pytest.mark.xfail(
    raises=AssertionError, reason="#1: physvol uses the inverse GDML rotation"
)
def test_gdml_quarter_turn_uses_the_inverse_placement_rotation(nested_box):
    # Geant4 G4GDMLReadStructure::PhysvolRead constructs an inverse rotation.
    # Child (5,0,0) turns to (0,-5,0), then adds parent (10,20,30).
    # Half-extents after the quarter turn are (2,1,3).
    scene = scene_builder(nested_box(90)).build()
    assert scene["meta"]["bbox"] == pytest.approx([8, 14, 27, 12, 16, 33])


@pytest.mark.xfail(raises=AssertionError, reason="#1: named positions are unresolved")
def test_named_position_reference_moves_the_placed_volume(single_volume):
    path = single_volume(
        placement='<positionref ref="shift"/>',
        define='<position name="shift" x="10" y="20" z="30" unit="mm"/>',
    )
    assert scene_builder(path).build()["meta"]["bbox"] == [9, 18, 27, 11, 22, 33]


@pytest.mark.xfail(
    raises=AssertionError, reason="#1: unitless solid angles should use radians"
)
def test_unitless_solid_angle_is_in_radians():
    assert geometry.angle({"deltaphi": str(math.pi / 2)}, "deltaphi") == pytest.approx(
        math.pi / 2
    )


@pytest.mark.xfail(raises=AssertionError, reason="#1: assemblies are not expanded")
def test_assembly_preserves_its_child_placement(write_gdml):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<assembly name="group"><physvol><volumeref ref="leaf"/>
  <position x="10" y="0" z="0" unit="mm"/>
</physvol></assembly>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="group"/></physvol>
</volume>""",
    )
    scene = scene_builder(path).build()
    assert scene["meta"]["objects"] == 1
    assert scene["meta"]["bbox"] == [9, -2, -3, 11, 2, 3]


@pytest.mark.xfail(
    raises=AssertionError, reason="#7: standalone vertex budget can overshoot"
)
def test_standalone_vertex_budget_is_a_hard_cap(single_volume):
    scene = scene_builder(single_volume(), max_vertices=35).build()
    assert scene["meta"]["vertices"] <= 35


@pytest.mark.xfail(
    raises=AssertionError, reason="#8: unknown length units are silently accepted"
)
def test_unknown_length_unit_has_an_actionable_diagnostic(single_volume):
    path = single_volume('<box name="shape" x="2" y="4" z="6" lunit="furlong"/>')
    try:
        scene = scene_builder(path).build()
    except ValueError as error:
        assert "unit" in str(error).lower()
    else:
        assert any("unit" in warning.lower() for warning in scene["meta"]["warnings"])
