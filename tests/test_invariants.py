"""Bounded, deterministic metamorphic checks of geometry mathematics."""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gdml_inspector import geometry

from .helpers import bounds, vertices

BOUNDED = settings(max_examples=40, derandomize=True, database=None, deadline=None)
POINT = st.tuples(*(st.integers(-100, 100) for _ in range(3)))
ANGLES = st.tuples(*(st.integers(-180, 180) for _ in range(3)))


@BOUNDED
@given(point=POINT, angles=ANGLES)
def test_active_rotation_preserves_distance_from_origin(point, angles):
    matrix = geometry.euler_xyz(*(math.radians(angle) for angle in angles))
    actual = geometry.transform_point(matrix, point)
    assert math.dist(actual, (0, 0, 0)) == pytest.approx(
        math.dist(point, (0, 0, 0)), abs=1e-10
    )


@BOUNDED
@given(point=POINT, translation=POINT, angles=ANGLES)
def test_matrix_product_agrees_with_sequential_affine_actions(
    point, translation, angles
):
    rotation = geometry.euler_xyz(*(math.radians(angle) for angle in angles))
    shift = geometry.translate(*translation)
    actual = geometry.transform_point(geometry.matmul(shift, rotation), point)
    rotated = geometry.transform_point(rotation, point)
    expected = tuple(rotated[i] + translation[i] for i in range(3))
    assert actual == pytest.approx(expected, abs=1e-10)


@BOUNDED
@given(
    lengths=st.tuples(*(st.integers(1, 100) for _ in range(3))),
    unit=st.sampled_from(["mm", "cm", "m", "um", "nm"]),
)
def test_equivalent_length_units_give_the_same_box_geometry(lengths, unit):
    # Independent conversion factors, not the production unit table.
    factor = {"mm": 1, "cm": 10, "m": 1000, "um": 0.001, "nm": 1e-6}[unit]
    attrs = {
        name: str(value / factor) for name, value in zip("xyz", lengths, strict=True)
    }
    triangles = geometry.mesh_box(attrs | {"lunit": unit})
    expected = [-value / 2 for value in lengths] + [value / 2 for value in lengths]
    assert bounds(vertices(triangles)) == pytest.approx(expected, abs=1e-10)
