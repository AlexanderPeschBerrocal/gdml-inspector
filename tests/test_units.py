"""Explicit supported units, checked against physical conversion factors."""

import math
import xml.etree.ElementTree as ET

import pytest

from gdml_inspector import geometry

from .helpers import bounds, lazy_builder, scene_builder, vertices


@pytest.mark.parametrize(
    ("unit", "factor"),
    [
        ("mm", 1),
        ("millimeter", 1),
        ("cm", 10),
        ("m", 1000),
        ("um", 0.001),
        ("nm", 1e-6),
    ],
)
@pytest.mark.parametrize("attribute", ["lunit", "unit"])
def test_length_conversion_to_millimetres(unit, factor, attribute):
    assert geometry.length({"x": "2.5", attribute: unit}, "x") == pytest.approx(
        2.5 * factor
    )


@pytest.mark.parametrize(
    ("unit", "value"),
    [("deg", 90), ("degree", 90), ("rad", math.pi / 2), ("radian", math.pi / 2)],
)
def test_explicit_angle_conversion_to_radians(unit, value):
    assert geometry.angle({"phi": str(value), "aunit": unit}, "phi") == pytest.approx(
        math.pi / 2
    )


def test_unitless_lengths_are_millimetres():
    assert geometry.length({"x": "2.5"}, "x") == 2.5


@pytest.mark.parametrize(
    ("unit", "coordinates"),
    [("mm", (10, -20, 30)), ("cm", (1, -2, 3)), ("m", (0.01, -0.02, 0.03))],
)
def test_position_coordinates_use_their_declared_unit(unit, coordinates):
    x, y, z = coordinates
    element = ET.fromstring(f'<position x="{x}" y="{y}" z="{z}" unit="{unit}"/>')
    assert geometry.parse_vec(element, geometry.LEN_UNITS) == pytest.approx(
        (10, -20, 30)
    )


def test_box_dimensions_are_full_lengths_not_half_lengths():
    triangles = geometry.mesh_box({"x": "2", "y": "4", "z": "6", "lunit": "cm"})
    assert bounds(vertices(triangles)) == pytest.approx([-10, -20, -30, 10, 20, 30])


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
def test_mixed_solid_and_placement_units_have_millimetre_world_bounds(
    single_volume, mode
):
    path = single_volume(
        '<box name="shape" x="2" y="4" z="6" lunit="cm"/>',
        placement='<position x="0.1" y="0.2" z="0.3" unit="m"/>',
    )
    # Centre (100,200,300) mm and half-lengths (10,20,30) mm.
    expected = [90, 180, 270, 110, 220, 330]
    if mode == "standalone":
        assert scene_builder(path).build()["meta"]["bbox"] == pytest.approx(expected)
    else:
        builder = lazy_builder(path)
        builder.build_chunks()
        assert builder.global_bbox_min + builder.global_bbox_max == pytest.approx(
            expected
        )
