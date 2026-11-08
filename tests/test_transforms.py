"""Active matrix algebra; GDML passive-placement gaps live in known limitations."""

import math

import pytest

from gdml_inspector import geometry

from .helpers import lazy_builder, scene_builder


@pytest.mark.parametrize(
    ("rotation", "point", "expected"),
    [
        (geometry.rot_x, (0, 1, 0), (0, 0, 1)),
        (geometry.rot_x, (0, 0, 1), (0, -1, 0)),
        (geometry.rot_y, (1, 0, 0), (0, 0, -1)),
        (geometry.rot_y, (0, 0, 1), (1, 0, 0)),
        (geometry.rot_z, (1, 0, 0), (0, 1, 0)),
        (geometry.rot_z, (0, 1, 0), (-1, 0, 0)),
    ],
)
def test_active_quarter_turns_map_basis_vectors(rotation, point, expected):
    actual = geometry.transform_point(rotation(math.pi / 2), point)
    assert actual == pytest.approx(expected, abs=1e-12)


def test_identity_and_translation_act_on_points():
    point = (2, -3, 5)
    assert geometry.transform_point(geometry.identity(), point) == point
    assert geometry.transform_point(geometry.translate(10, 20, 30), point) == (
        12,
        17,
        35,
    )


def test_composition_order_is_not_commutative():
    translation = geometry.translate(10, 0, 0)
    rotation = geometry.rot_z(math.pi / 2)
    assert geometry.transform_point(
        geometry.matmul(translation, rotation), (1, 0, 0)
    ) == (pytest.approx((10, 1, 0), abs=1e-12))
    assert geometry.transform_point(
        geometry.matmul(rotation, translation), (1, 0, 0)
    ) == (pytest.approx((0, 11, 0), abs=1e-12))


def test_active_euler_helper_applies_x_then_y_then_z():
    # (1,2,3) -> Rx: (1,-3,2) -> Ry: (2,-3,-1) -> Rz: (3,2,-1).
    actual = geometry.transform_point(
        geometry.euler_xyz(math.pi / 2, math.pi / 2, math.pi / 2), (1, 2, 3)
    )
    assert actual == pytest.approx((3, 2, -1), abs=1e-12)


@pytest.mark.parametrize(
    ("rotation_deg", "expected"),
    [
        (0, [14, 18, 27, 16, 22, 33]),
        (180, [4, 18, 27, 6, 22, 33]),
        (90, [8, 14, 27, 12, 16, 33]),
    ],
)
def test_nested_placements_have_analytic_world_bounds(
    nested_box, rotation_deg, expected
):
    # Geant4 G4GDMLReadStructure::PhysvolRead constructs an inverse rotation:
    # a quarter turn sends the child (5,0,0) to (0,-5,0), not (0,5,0), before
    # adding the parent offset (10,20,30).
    scene = scene_builder(nested_box(rotation_deg)).build()
    assert scene["meta"]["objects"] == 1
    assert scene["meta"]["warnings"] == []
    assert scene["objects"][0]["path"] == "world/parent/child"
    assert scene["objects"][0]["bbox"] == pytest.approx(expected, abs=1e-10)
    assert scene["meta"]["bbox"] == pytest.approx(expected, abs=1e-10)


_NESTED_SOLIDS = """
<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<box name="envelope" x="100" y="100" z="100" lunit="mm"/>"""
_NESTED_STRUCTURE = """
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="group"><materialref ref="Galactic"/><solidref ref="envelope"/>
  <physvol name="child"><volumeref ref="leaf"/>
    <position x="5" y="0" z="0" unit="mm"/>
  </physvol>
</volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="parent"><volumeref ref="group"/>
    <position x="10" y="20" z="30" unit="mm"/>
    {scale}
  </physvol>
</volume>"""


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
def test_scale_reflects_the_placed_subtree_before_translation(write_gdml, mode):
    # A symmetric box alone can't reveal a reflection by its bbox; the child's
    # off-centre (5,0,0) placement makes the x = -1 reflection observable.
    path = write_gdml(
        solids=_NESTED_SOLIDS,
        structure=_NESTED_STRUCTURE.format(scale='<scale x="-1" y="1" z="1"/>'),
    )
    expected = [4, 18, 27, 6, 22, 33]
    if mode == "standalone":
        assert scene_builder(path).build()["meta"]["bbox"] == pytest.approx(expected)
    else:
        builder = lazy_builder(path)
        builder.build_chunks()
        assert builder.global_bbox_min + builder.global_bbox_max == pytest.approx(
            expected
        )


def test_named_scale_reference_reflects_the_placed_subtree(write_gdml):
    path = write_gdml(
        solids=_NESTED_SOLIDS,
        define='<scale name="flip" x="-1" y="1" z="1"/>',
        structure=_NESTED_STRUCTURE.format(scale='<scaleref ref="flip"/>'),
    )
    scene = scene_builder(path).build()
    assert scene["meta"]["bbox"] == pytest.approx([4, 18, 27, 6, 22, 33])
