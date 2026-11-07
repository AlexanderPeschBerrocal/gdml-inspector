"""Supported object/vertex limits; the standalone overshoot is a known failure."""

import pytest

from .helpers import lazy_builder, scene_builder


def summary(path, mode, **options):
    if mode == "standalone":
        meta = scene_builder(path, **options).build()["meta"]
        return meta["objects"], meta["vertices"], meta["warnings"]
    builder = lazy_builder(path, **options)
    builder.build_chunks()
    return builder.total_objects, builder.total_vertices, list(builder.scene.warnings)


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
def test_object_budget_stops_before_next_placement(repeated_boxes, mode):
    objects, count, warnings = summary(repeated_boxes(3), mode, max_objects=1)
    assert objects == 1
    assert count == 36
    assert any("limit" in warning for warning in warnings)


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
@pytest.mark.parametrize(("budget", "expected_objects"), [(36, 1), (72, 2)])
def test_vertex_budget_stops_at_complete_object_boundary(
    repeated_boxes, mode, budget, expected_objects
):
    objects, count, warnings = summary(repeated_boxes(3), mode, max_vertices=budget)
    assert objects == expected_objects
    assert count == budget
    assert count % 3 == 0
    assert any("limit" in warning for warning in warnings)


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
def test_zero_object_budget_produces_no_geometry(repeated_boxes, mode):
    objects, count, _ = summary(repeated_boxes(2), mode, max_objects=0)
    assert objects == count == 0


def test_lazy_vertex_budget_rejects_an_object_that_would_exceed_it(single_volume):
    builder = lazy_builder(single_volume(), max_vertices=35)
    builder.build_chunks()
    assert builder.total_objects == builder.total_vertices == 0
    assert builder.chunks == {}
    assert builder.stopped_at_limit
    assert any("limit" in warning for warning in builder.scene.warnings)
