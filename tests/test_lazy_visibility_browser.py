"""Real-browser verification of lazy hide/isolate: a per-vertex visibility
buffer built from the chunk ids, applied to drawing and picking alike."""

import json

from .lazy_scene import (
    _PIXEL,
    LAYER,
    click_object,
    object_center,
    open_lazy,
    selected,
)

_CORNER = """() => {
  render();
  const px = new Uint8Array(4);
  gl.readPixels(2, 2, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return Array.from(px);
}"""


def row(page, path):
    return page.query_selector(f'#hierarchyTree [data-path="{path}"]')


def checkbox(page, path):
    return row(page, path).query_selector("input[type=checkbox]")


def open_tree(watched_page, served, directory, **kwargs):
    page = open_lazy(watched_page, served, directory, fetch_placements=False, **kwargs)
    page.click("#loadHierarchy")
    page.wait_for_selector("#hierarchyTree .tree-row")
    return page


def shown(page, path):
    """True when the object's centre pixel differs from the background."""
    return page.evaluate(_PIXEL, object_center(page, path)) != page.evaluate(_CORNER)


def test_hiding_a_row_removes_only_that_objects_pixels(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    assert shown(page, "world/ecal_a") and shown(page, "world/hcal_b")
    checkbox(page, "world/ecal_a").click()
    assert not shown(page, "world/ecal_a")
    assert shown(page, "world/ecal_c")  # same chunk, still drawn
    assert shown(page, "world/hcal_b")
    checkbox(page, "world/ecal_a").click()
    assert shown(page, "world/ecal_a")
    assert watched_page.problems == []


def test_a_hidden_object_is_not_pickable_and_does_not_block_what_is_behind(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    click_object(page, "world/back")
    assert selected(page) == "world/front"  # occluded by the nearer box
    checkbox(page, "world/front").click()
    click_object(page, "world/back")
    assert selected(page) == "world/back"


def test_hiding_the_selection_clears_it(watched_page, served, lazy_scene_dir):
    page = open_tree(watched_page, served, lazy_scene_dir)
    click_object(page, "world/hcal_b")
    assert selected(page) == "world/hcal_b"
    checkbox(page, "world/hcal_b").click()
    assert page.evaluate("selectedObjectId") is None
    assert page.eval_on_selector("#detailsContent", "el => el.style.display") == "none"


def test_a_container_checkbox_hides_its_whole_subtree_with_tristate(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    row(page, "world/group").query_selector(".tree-toggle").click()
    assert shown(page, "world/group/inner")
    checkbox(page, "world/group").click()
    assert not shown(page, "world/group/inner")
    assert checkbox(page, "world/group/inner").is_checked() is False
    checkbox(page, "world/group/inner").click()  # show it again individually
    assert shown(page, "world/group/inner")
    # A container with no rendered geometry has nothing to toggle.
    assert checkbox(page, "world/empty").is_disabled()
    # World is partially hidden while one child is hidden.
    checkbox(page, "world/ecal_a").click()
    assert page.evaluate("hierarchyTree.rowElements.get('world').check.indeterminate")


def test_isolate_shows_only_that_subtree_and_clear_restores(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    row(page, "world/hcal_b").query_selector("text=Isolate").click()
    assert shown(page, "world/hcal_b")
    assert not shown(page, "world/ecal_a")
    assert not shown(page, "world/front")
    assert "tree-isolated" in row(page, "world/hcal_b").get_attribute("class")
    assert "tree-filtered" in row(page, "world/ecal_a").get_attribute("class")
    # Picking respects isolation too.
    click_object(page, "world/ecal_a")
    assert page.evaluate("selectedObjectId") is None
    page.click("#hierarchyClearIsolation")
    assert shown(page, "world/ecal_a") and shown(page, "world/front")
    assert (
        page.eval_on_selector("#hierarchyClearIsolation", "el => el.style.display")
        == "none"
    )
    # The Isolate button toggles.
    row(page, "world/hcal_b").query_selector("text=Isolate").click()
    row(page, "world/hcal_b").query_selector("text=Isolate").click()
    assert page.evaluate("isolatedPath") is None


def test_hidden_state_survives_unload_and_applies_to_a_layer_loaded_later(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir, load="none")
    checkbox(page, "world/hcal_b").click()  # hide before its geometry exists
    key = json.dumps(LAYER["world/hcal_b"])
    chunk = f"states[MANIFEST.chunks.find(c => c.baseKey === {key}).key]"
    for layer in ("world/hcal_b", "world/ecal_a"):
        page.evaluate(f"setLayer({json.dumps(LAYER[layer])}, true)")
    page.wait_for_function(f"{chunk}.loaded")
    page.wait_for_function("Object.values(states).filter(s => s.loaded).length === 2")
    page.click("#viewXYPos")
    assert not shown(page, "world/hcal_b")
    assert shown(page, "world/ecal_a")
    # Unload and reload: still hidden.
    page.evaluate(f"setLayer({key}, false)")
    page.wait_for_function(f"!{chunk}.loaded")
    page.evaluate(f"setLayer({key}, true)")
    page.wait_for_function(f"{chunk}.loaded")
    assert not shown(page, "world/hcal_b")
    checkbox(page, "world/hcal_b").click()
    assert shown(page, "world/hcal_b")


def test_saved_view_round_trips_hidden_and_isolated_placements(
    watched_page, served, lazy_scene_dir
):
    page = open_tree(watched_page, served, lazy_scene_dir)
    checkbox(page, "world/ecal_a").click()
    state = page.evaluate("saveViewState()")
    assert state["hierarchy"]["hiddenPaths"] == ["world/ecal_a"]
    row(page, "world/hcal_b").query_selector("text=Isolate").click()
    isolated = page.evaluate("saveViewState()")
    assert isolated["hierarchy"]["isolatedPath"] == "world/hcal_b"

    fresh = open_lazy(watched_page, served, lazy_scene_dir, fetch_placements=False)
    fresh.evaluate("state => loadViewState(state, 'test')", isolated)
    fresh.wait_for_function("isolatedPath === 'world/hcal_b'")
    assert shown(fresh, "world/hcal_b") and not shown(fresh, "world/front")
    assert fresh.evaluate("[...hierarchyHidden].length") == 1
    assert fresh.evaluate("saveViewState().hierarchy.hiddenPaths") == ["world/ecal_a"]

    isolated["hierarchy"]["hiddenPaths"].append("world/gone")
    isolated["hierarchy"]["isolatedPath"] = "world/vanished"
    fresh.evaluate("state => loadViewState(state, 'test')", isolated)
    fresh.wait_for_function(
        "document.getElementById('viewStatePanel').innerText.includes('world/vanished')"
    )
    assert fresh.evaluate("isolatedPath") is None
    assert "world/gone" in fresh.inner_text("#viewStatePanel")


def test_lazy_and_standalone_hide_the_same_placements(
    watched_page, served, lazy_scene_dir, lazy_scene_standalone
):
    lazy = open_tree(watched_page, served, lazy_scene_dir)
    row(lazy, "world/group").query_selector(".tree-toggle").click()
    checkbox(lazy, "world/group").click()
    checkbox(lazy, "world/front").click()
    lazy_state = lazy.evaluate("saveViewState()")
    assert lazy_state["hierarchy"]["hiddenPaths"] == [
        "world/front",
        "world/group/inner",
    ]

    standalone = lazy.context.new_page()
    try:
        standalone.goto(lazy_scene_standalone.resolve().as_uri())
        standalone.wait_for_selector("#hierarchyTree .tree-row")
        standalone.evaluate("state => loadViewState(state, 'lazy')", lazy_state)
        standalone.wait_for_function("hierarchyHidden.size === 2")
        assert standalone.evaluate("saveViewState().hierarchy.hiddenPaths") == [
            "world/front",
            "world/group/inner",
        ]
    finally:
        standalone.close()
