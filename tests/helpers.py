"""Test setup and independent geometric measurements, with no NumPy required."""

import math
from collections import Counter

from gdml_inspector.geometry import GDMLSceneBuilder
from gdml_inspector.lazy import LazyGDMLSceneBuilder

DEFAULT_OPTIONS = {
    "quality": "fast",
    "include_containers": False,
    "include_world": False,
    "max_objects": 100,
    "max_vertices": 100_000,
}


def scene_builder(path, **options):
    return GDMLSceneBuilder(path, **(DEFAULT_OPTIONS | options))


def lazy_builder(path, **options):
    settings = DEFAULT_OPTIONS | {"chunk_vertices": 72} | options
    return LazyGDMLSceneBuilder(path, **settings)


def vertices(triangles):
    return [point for triangle in triangles for point in triangle]


def bounds(points):
    return [min(point[axis] for point in points) for axis in range(3)] + [
        max(point[axis] for point in points) for axis in range(3)
    ]


def triangle_area(triangle):
    a, b, c = triangle
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    cross = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    return math.sqrt(sum(component**2 for component in cross)) / 2


def assert_valid_triangles(triangles):
    assert triangles, "The solid must produce some triangles"
    for triangle in triangles:
        assert len(triangle) == 3
        assert all(math.isfinite(value) for point in triangle for value in point)
        assert triangle_area(triangle) > 1e-12, f"Degenerate triangle: {triangle}"


def assert_closed_edges(triangles):
    # Curved seams differ by floating-point roundoff at phi=0 and phi=2*pi.
    # These fixtures have millimetre-scale dimensions; 9 decimals resolves seams
    # without merging distinct vertices at their chosen tessellation quality.
    edges = Counter()
    for triangle in triangles:
        points = [tuple(round(v, 9) for v in point) for point in triangle]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edges[tuple(sorted((points[a], points[b])))] += 1
    assert all(count == 2 for count in edges.values()), "Open or non-manifold edges"
