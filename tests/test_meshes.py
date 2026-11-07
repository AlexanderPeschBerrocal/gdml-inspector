"""Independent surface equations, areas, and edge closure for supported solids."""

import math
import xml.etree.ElementTree as ET

import pytest

from gdml_inspector import geometry

from .helpers import (
    assert_closed_edges,
    assert_valid_triangles,
    bounds,
    triangle_area,
    vertices,
)


def mesh(xml, segments=24):
    return geometry.mesh_solid_primitive(ET.fromstring(xml), segments)


def test_box_has_six_rectangular_faces_with_the_expected_total_area():
    triangles = mesh('<box x="2" y="4" z="6" lunit="mm"/>')
    assert_valid_triangles(triangles)
    assert_closed_edges(triangles)
    assert len(triangles) == 12
    assert bounds(vertices(triangles)) == [-1, -2, -3, 1, 2, 3]
    assert sum(map(triangle_area, triangles)) == pytest.approx(88)


@pytest.mark.parametrize("inner_radius", [0, 1])
def test_full_tube_radii_length_and_closed_surface(inner_radius):
    triangles = mesh(
        f'<tube rmin="{inner_radius}" rmax="3" z="10" '
        'startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>'
    )
    assert_valid_triangles(triangles)
    assert_closed_edges(triangles)
    points = vertices(triangles)
    assert bounds(points) == pytest.approx([-3, -3, -5, 3, 3, 5])
    for x, y, z in points:
        assert abs(z) == 5
        radius = math.hypot(x, y)
        assert radius == pytest.approx(inner_radius) or radius == pytest.approx(3)


@pytest.mark.parametrize(
    "angles", ['deltaphi="90" aunit="deg"', f'deltaphi="{math.pi / 2}" aunit="rad"']
)
def test_hollow_quarter_tube_closes_its_two_radial_faces(angles):
    triangles = mesh(f'<tube rmin="1" rmax="3" z="4" startphi="0" {angles}/>')
    assert_valid_triangles(triangles)
    assert_closed_edges(triangles)
    assert bounds(vertices(triangles)) == pytest.approx([0, 0, -2, 3, 3, 2], abs=1e-12)
    for x, y, _ in vertices(triangles):
        assert x >= -1e-12 and y >= -1e-12
        assert 1 - 1e-12 <= math.hypot(x, y) <= 3 + 1e-12


def test_hollow_cone_has_the_specified_radii_at_each_end():
    triangles = mesh(
        '<cone rmin1="1" rmax1="2" rmin2="2" rmax2="4" z="8" '
        'startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>'
    )
    assert_valid_triangles(triangles)
    assert_closed_edges(triangles)
    assert bounds(vertices(triangles)) == pytest.approx([-4, -4, -4, 4, 4, 4])
    for x, y, z in vertices(triangles):
        assert abs(z) == 4
        inner, outer = (1, 2) if z < 0 else (2, 4)
        radius = math.hypot(x, y)
        assert radius == pytest.approx(inner) or radius == pytest.approx(outer)


def test_cut_tube_vertices_lie_on_its_declared_planes():
    triangles = mesh(
        '<cutTube rmin="1" rmax="3" z="8" startphi="0" deltaphi="360" '
        'aunit="deg" lunit="mm" lowX="0.25" lowY="0" lowZ="-1" '
        'highX="-0.5" highY="0" highZ="1"/>'
    )
    assert_valid_triangles(triangles)
    assert_closed_edges(triangles)
    on_low = on_high = 0
    for x, y, z in vertices(triangles):
        radius = math.hypot(x, y)
        assert radius == pytest.approx(1) or radius == pytest.approx(3)
        low = math.isclose(0.25 * x - (z + 4), 0, abs_tol=1e-12)
        high = math.isclose(-0.5 * x + (z - 4), 0, abs_tol=1e-12)
        assert low or high
        on_low += low
        on_high += high
    assert on_low and on_high


def test_degenerate_triangles_are_not_added():
    triangles = []
    geometry.add_tri(triangles, (0, 0, 0), (1, 1, 1), (2, 2, 2))
    geometry.add_tri(triangles, (0, 0, 0), (0, 0, 0), (1, 0, 0))
    assert triangles == []


@pytest.mark.parametrize("z", [0, -1])
def test_nonpositive_tube_length_produces_no_triangles(z):
    assert mesh(f'<tube rmin="0" rmax="3" z="{z}" deltaphi="360" aunit="deg"/>') == []
