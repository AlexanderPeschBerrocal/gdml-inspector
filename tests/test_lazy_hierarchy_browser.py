"""Real-browser verification of the lazy viewer's hierarchy tree: built from
placements.json (so it lists unloaded layers too), on demand, with selection,
focus, search, keyboard navigation and saved-view support."""

import json

from .browser_helpers import VIEWPORT, next_frames
from .lazy_scene import (
    _LAZY_PROJECT,
    LAYER,
    click_object,
    copy_of,
    downgrade_to_v1,
    object_center,
    open_lazy,
    selected,
)


def open_tree(watched_page, served, directory, **kwargs):
    page = open_lazy(watched_page, served, directory, fetch_placements=False, **kwargs)
    page.click("#loadHierarchy")
    page.wait_for_selector("#hierarchyTree .tree-row")
    return page


def row(page, path):
    return page.query_selector(f'#hierarchyTree [data-path="{path}"]')


def test_placements_are_not_fetched_until_the_hierarchy_is_requested(
    watched_page, served, lazy_scene_dir
):
    requests = []
    watched_page.page.on("request", lambda r: requests.append(r.url))
    page = open_lazy(watched_page, served, lazy_scene_dir, fetch_placements=False)
    assert not any("placements.json" in url for url in requests)
    assert page.eval_on_selector("#hierarchyTree", "el => el.style.display") == "none"
    page.click("#loadHierarchy")
    page.wait_for_selector("#hierarchyTree .tree-row")
    assert any("placements.json" in url for url in requests)
    assert page.eval_on_selector("#loadHierarchy", "el => el.style.display") == "none"
    assert watched_page.problems == []


def test_tree_lists_placements_of_layers_that_are_not_loaded(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir, load="none")
    assert page.evaluate("Object.values(states).every(s => !s.loaded)")
    assert row(page, "world") is not None
    for path in ("world/ecal_a", "world/hcal_b", "world/group", "world/front"):
        assert row(page, path) is not None
    # Collapsed: the child of the container is not in the DOM yet.
    assert row(page, "world/group/inner") is None
    row(page, "world/group").query_selector(".tree-toggle").click()
    assert row(page, "world/group/inner") is not None
    assert "(container)" in row(page, "world/group").inner_text()


def test_tree_paths_match_the_standalone_hierarchy_in_order(
    watched_page, served, lazy_scene_dir, lazy_scene_standalone
):
    page = open_lazy(watched_page, served, lazy_scene_dir, fetch_placements=True)
    lazy_paths = page.evaluate("placements.nodes.map(n => n.path)")
    standalone = page.context.new_page()
    try:
        standalone.goto(lazy_scene_standalone.resolve().as_uri())
        standalone.wait_for_selector("#hierarchyTree .tree-row")
        assert standalone.evaluate("HIERARCHY.map(n => n.path)") == lazy_paths
    finally:
        standalone.close()


def test_clicking_a_row_selects_that_placement(watched_page, served, lazy_scene_dir):
    page = open_tree(watched_page, served, lazy_scene_dir)
    row(page, "world/ecal_a").click()
    assert page.evaluate("selectedPath") == "world/ecal_a"
    assert page.inner_text("#detailPath") == "world/ecal_a"
    assert page.inner_text("#detailMaterial") == "G4_Fe"
    assert "tree-selected" in row(page, "world/ecal_a").get_attribute("class")
    assert page.evaluate("selectedObjectId") == page.evaluate(
        "placements.nodeByPath['world/ecal_a'].objectId"
    )


def test_clicking_a_container_row_selects_without_highlighting_geometry(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    row(page, "world/group").click()
    assert page.evaluate("selectedPath") == "world/group"
    assert page.evaluate("selectedObjectId") is None
    assert "container" in page.inner_text("#detailMaterial")


def test_selecting_an_unloaded_placement_marks_its_geometry_as_not_shown(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir, load="none")
    row(page, "world/hcal_b").click()
    assert page.inner_text("#detailPath") == "world/hcal_b"
    assert "Geometry not shown" in page.inner_text("#detailNote")
    # Loading its layer clears the note and the selection stays on it.
    key = json.dumps(LAYER["world/hcal_b"])
    page.evaluate(f"setLayer({key}, true)")
    page.wait_for_function(
        f"states[MANIFEST.chunks.find(c => c.baseKey === {key}).key].loaded"
    )
    assert page.eval_on_selector("#detailNote", "el => el.style.display") == "none"
    assert page.evaluate("selectedPath") == "world/hcal_b"


def test_picking_builds_the_tree_and_reveals_the_picked_row(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    assert page.evaluate("hierarchyTree") is None
    click_object(page, "world/group/inner")
    assert selected(page) == "world/group/inner"
    page.wait_for_selector('#hierarchyTree [data-path="world/group/inner"]')
    # The collapsed container was expanded to reveal the pick.
    assert "tree-selected" in row(page, "world/group/inner").get_attribute("class")
    assert row(page, "world/group").get_attribute("aria-expanded") == "true"


def test_keyboard_navigation_and_expansion(watched_page, served, lazy_scene_dir):
    page = open_tree(watched_page, served, lazy_scene_dir)
    row(page, "world").focus()
    page.keyboard.press("ArrowDown")
    first_child = page.evaluate("document.activeElement.dataset.path")
    assert first_child.startswith("world/")
    page.evaluate("hierarchyTree.focusRow('world/group')")
    page.keyboard.press("ArrowRight")  # expand
    assert row(page, "world/group/inner") is not None
    page.keyboard.press("ArrowRight")  # step into the first child
    assert page.evaluate("document.activeElement.dataset.path") == "world/group/inner"
    page.keyboard.press("ArrowLeft")  # back to the parent
    assert page.evaluate("document.activeElement.dataset.path") == "world/group"
    page.keyboard.press("Enter")  # toggles the row's visibility checkbox
    assert page.evaluate("hierarchyHidden.size") == 1
    page.keyboard.press("Enter")
    assert page.evaluate("hierarchyHidden.size") == 0


def test_search_expands_to_matches_and_dims_the_rest(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    page.fill("#hierarchySearch", "inner")
    assert row(page, "world/group/inner") is not None
    assert "tree-match" in row(page, "world/group/inner").get_attribute("class")
    assert "tree-dim" in row(page, "world/ecal_a").get_attribute("class")
    page.fill("#hierarchySearch", "")
    assert "tree-dim" not in (row(page, "world/ecal_a").get_attribute("class") or "")


def test_search_also_matches_solid_names(watched_page, served, lazy_scene_dir):
    page = open_tree(watched_page, served, lazy_scene_dir)
    page.fill("#hierarchySearch", "overlap_shape")
    assert "tree-match" in row(page, "world/front").get_attribute("class")
    assert "tree-dim" in row(page, "world/ecal_a").get_attribute("class")


def test_focus_centres_the_camera_on_the_placement(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    before = page.evaluate("dist")
    row(page, "world/hcal_b").query_selector("text=Focus").click()
    page.wait_for_function("focusInFlight === 0")
    centre = object_center(page, "world/hcal_b")
    view = page.evaluate("viewCenter")
    assert all(abs(a - b) < 1e-6 for a, b in zip(view, centre, strict=True))
    assert page.evaluate("dist") < before
    next_frames(page)
    # The focused object now sits at the middle of the canvas.
    sx, sy = page.evaluate(_LAZY_PROJECT, centre)
    box = page.evaluate("canvas.getBoundingClientRect().toJSON()")
    assert abs(sx - (box["x"] + box["width"] / 2)) < 1.5
    assert abs(sy - (box["y"] + box["height"] / 2)) < 1.5


def test_focus_on_a_container_uses_its_rendered_descendants(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    row(page, "world/group").query_selector("text=Focus").click()
    page.wait_for_function("focusInFlight === 0")
    assert page.evaluate("viewCenter") == object_center(page, "world/group/inner")


def test_focus_on_an_empty_container_falls_back_to_the_nearest_rendered_ancestor(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    row(page, "world/empty").query_selector("text=Focus").click()
    page.wait_for_function("focusInFlight === 0")
    box = page.evaluate(
        """(() => {
          const b = placements.objects.map(o => o.bbox);
          return [0, 1, 2].map(i => (Math.min(...b.map(x => x[i])) + Math.max(...b.map(x => x[i + 3]))) / 2);
        })()"""
    )
    view = page.evaluate("viewCenter")
    assert all(abs(a - b) < 1e-6 for a, b in zip(view, box, strict=True))
    # Framed like a real focus (1.6x the subtree's extent), not the reset view (1.35x).
    assert abs(page.evaluate("dist / radius") - 1.6) < 1e-6


def test_inspector_focus_button_focuses_the_selection(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    click_object(page, "world/hcal_b")
    assert selected(page) == "world/hcal_b"
    page.click("#detailFocus")
    page.wait_for_function("focusInFlight === 0")
    assert page.evaluate("viewCenter") == object_center(page, "world/hcal_b")


def test_reset_view_restores_the_default_centre(watched_page, served, lazy_scene_dir):
    page = open_tree(watched_page, served, lazy_scene_dir)
    default = page.evaluate("center")
    row(page, "world/hcal_b").query_selector("text=Focus").click()
    page.wait_for_function("focusInFlight === 0")
    assert page.evaluate("viewCenter") != default
    page.click("#resetView")
    assert page.evaluate("viewCenter") == default


def test_saved_view_round_trips_tree_expansion_selection_and_focus(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    row(page, "world/group").query_selector(".tree-toggle").click()
    row(page, "world/group/inner").click()
    row(page, "world/hcal_b").query_selector("text=Focus").click()
    page.wait_for_function("focusInFlight === 0")
    state = page.evaluate("saveViewState()")
    assert "world/group" in state["hierarchy"]["expandedPaths"]
    assert state["hierarchy"]["selectedPath"] == "world/group/inner"
    assert state["camera"]["viewCenter"] == object_center(page, "world/hcal_b")

    fresh = open_lazy(
        watched_page, served, lazy_scene_dir, fetch_placements=False, load="none"
    )
    fresh.evaluate("state => loadViewState(state, 'test')", state)
    fresh.wait_for_function("selectedPath === 'world/group/inner'")
    fresh.wait_for_selector('#hierarchyTree [data-path="world/group/inner"]')
    assert fresh.evaluate("viewCenter") == state["camera"]["viewCenter"]
    assert "tree-selected" in row(fresh, "world/group/inner").get_attribute("class")

    state["hierarchy"]["expandedPaths"].append("world/ghost")
    fresh.evaluate("state => loadViewState(state, 'test')", state)
    fresh.wait_for_function(
        "document.getElementById('viewStatePanel').innerText.includes('world/ghost')"
    )


def test_a_v1_export_disables_the_hierarchy_with_an_explanation(
    watched_page, served, lazy_scene_baked_dir, tmp_path
):
    export = copy_of(lazy_scene_baked_dir, tmp_path)
    downgrade_to_v1(export)
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(export)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    assert page.is_disabled("#loadHierarchy")
    assert "predates" in page.inner_text("#hierarchyNote")
    assert watched_page.problems == []
