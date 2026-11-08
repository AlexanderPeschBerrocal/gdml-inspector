"""Supported object/vertex limits; the standalone overshoot is a known failure."""

import pytest

from .helpers import lazy_builder, scene_builder


def summary(path, mode, **options):
    if mode == "standalone":
        meta = scene_builder(path, **options).build()["meta"]
        return meta["objects"], meta["vertices"], meta["warnings"]
    builder = lazy_builder(path, **options)
    builder.build_chunks()
    return builder.total_objects, builder.total_vertices, builder.scene.warning_messages


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
    assert any("limit" in warning for warning in builder.scene.warning_messages)


def test_lazy_budget_exceeded_respects_the_stopped_at_limit_flag(example_gdml):
    # add_volume() can reject a single oversized object outright (its own
    # precise pre-commit check on total_vertices + this object's count),
    # which leaves total_objects/total_vertices unchanged and only sets
    # stopped_at_limit. A later budget check re-deriving purely from those
    # counts would miss that rejection and wrongly let a further, smaller
    # sibling be attempted; _budget_exceeded() must honour the flag too.
    builder = lazy_builder(example_gdml, max_objects=100, max_vertices=100_000)
    assert not builder._budget_exceeded()
    builder.stopped_at_limit = True
    assert builder._budget_exceeded()
