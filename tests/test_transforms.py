"""Active matrix algebra; GDML passive-placement gaps live in known limitations."""

import math

import pytest

from gdml_inspector import geometry

from .helpers import scene_builder


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
    [(0, [14, 18, 27, 16, 22, 33]), (180, [4, 18, 27, 6, 22, 33])],
)
def test_nested_placements_have_analytic_world_bounds(
    nested_box, rotation_deg, expected
):
    # A half turn is its own inverse, so this also checks a supported GDML
    # rotation without legitimising the known +/-90-degree convention error.
    scene = scene_builder(nested_box(rotation_deg)).build()
    assert scene["meta"]["objects"] == 1
    assert scene["meta"]["warnings"] == []
    assert scene["objects"][0]["path"] == "world/parent/child"
    assert scene["objects"][0]["bbox"] == pytest.approx(expected, abs=1e-10)
    assert scene["meta"]["bbox"] == pytest.approx(expected, abs=1e-10)
