"""Object/vertex limits are hard caps, and chunk sizes have exact semantics."""

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
    assert builder.budget_stop is not None
    assert any("limit" in warning for warning in builder.scene.warning_messages)


def test_budget_exceeded_honours_a_recorded_rejection(example_gdml):
    # add_volume() can reject a single oversized object outright, leaving the
    # object/vertex counts unchanged. Traversal must still stop: a later,
    # smaller sibling must not be attempted after a rejection.
    builder = lazy_builder(example_gdml, max_objects=100, max_vertices=100_000)
    assert not builder._budget_exceeded()
    builder.budget_stop = {"limit": "vertices"}
    assert builder._budget_exceeded()


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
@pytest.mark.parametrize("budget", range(1, 112, 5))
def test_vertex_budget_is_never_exceeded(repeated_boxes, mode, budget):
    _objects, count, _ = summary(repeated_boxes(3), mode, max_vertices=budget)
    assert count <= budget
    assert count % 36 == 0  # whole objects only


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
def test_reaching_a_limit_exactly_reports_nothing_omitted(repeated_boxes, mode):
    for options in ({"max_objects": 3}, {"max_vertices": 108}):
        objects, count, warnings = summary(repeated_boxes(3), mode, **options)
        assert (objects, count) == (3, 108)
        assert not any("limit" in warning for warning in warnings)


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
def test_limit_diagnostic_says_exactly_what_was_omitted(repeated_boxes, mode):
    path = repeated_boxes(3)
    if mode == "standalone":
        scene = scene_builder(path, max_vertices=80)
        scene.build()
        found = list(scene.scene.diagnostics.values())
    else:
        builder = lazy_builder(path, max_vertices=80)
        builder.build_chunks()
        found = list(builder.scene.diagnostics.values())
    (entry,) = [d for d in found if d.code == "limit-reached"]
    assert entry.count == 1  # reported once, however many traversal levels unwind
    message = entry.message
    assert "exported 2 objects / 72 vertices" in message
    assert "stopped before placement world/copy_2" in message
    assert "needs 36 vertices but only 8 of the 80-vertex limit remain" in message
    assert "never cut partway" in message
    assert entry.identifier == "world/copy_2"


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
def test_a_rejected_object_stops_traversal_even_if_a_later_one_would_fit(
    write_gdml, mode
):
    path = write_gdml(
        solids="""
<box name="small" x="2" y="2" z="2" lunit="mm"/>
<tube name="big" rmin="0" rmax="5" z="10" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>""",
        structure="""
<volume name="s"><materialref ref="G4_Si"/><solidref ref="small"/></volume>
<volume name="b"><materialref ref="G4_Si"/><solidref ref="big"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="first"><volumeref ref="s"/></physvol>
  <physvol name="oversized"><volumeref ref="b"/></physvol>
  <physvol name="after"><volumeref ref="s"/></physvol>
</volume>""",
    )
    # 36 vertices fit; the tube does not; the box after it is never visited.
    objects, count, _ = summary(path, mode, max_vertices=40)
    assert (objects, count) == (1, 36)


def test_an_oversized_object_gets_its_own_chunk_and_is_never_split(write_gdml):
    path = write_gdml(
        solids="""
<box name="small" x="2" y="2" z="2" lunit="mm"/>
<tube name="big" rmin="0" rmax="5" z="10" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>""",
        structure="""
<volume name="s"><materialref ref="G4_Si"/><solidref ref="small"/></volume>
<volume name="b"><materialref ref="G4_Si"/><solidref ref="big"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="a"><volumeref ref="s"/></physvol>
  <physvol name="oversized"><volumeref ref="b"/></physvol>
  <physvol name="c"><volumeref ref="s"/></physvol>
</volume>""",
    )
    builder = lazy_builder(path, chunk_vertices=40)
    builder.build_chunks()
    sizes = [(c.vertex_count, c.object_count) for c in builder.chunks.values()]
    assert builder.total_objects == 3
    # Nominal size is exceeded only by a chunk holding a single whole object,
    # and all three objects are exported, none truncated.
    assert any(count > 40 and objects == 1 for count, objects in sizes)
    assert all(count <= 40 or objects == 1 for count, objects in sizes)
    assert sum(count for count, _ in sizes) == builder.total_vertices


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
def test_a_stop_deep_in_the_tree_is_reported_once(nested_box, mode):
    path = nested_box()
    if mode == "standalone":
        builder = scene_builder(path, max_objects=0)
        builder.build()
    else:
        builder = lazy_builder(path, max_objects=0)
        builder.build_chunks()
    (entry,) = [
        d for d in builder.scene.diagnostics.values() if d.code == "limit-reached"
    ]
    assert entry.count == 1
    assert entry.identifier == "world/parent/child"
