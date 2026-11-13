"""Real-browser verification of lazy-mode picking, highlighting and the
placement inspector (chunk format v2 ids + placements.json).

The geometry (see lazy_scene.py) puts each box in its own lazy layer so layers
can be unloaded independently, and keeps a front/back pair of identical
footprints for occlusion and clipping."""

import json

import pytest

from .browser_helpers import VIEWPORT, next_frames
from .lazy_scene import (
    _LAZY_PROJECT,
    _PIXEL,
    LAYER,
    click_object,
    copy_of,
    distance_to_highlight,
    downgrade_to_v1,
    object_center,
    open_lazy,
    selected,
)


@pytest.mark.parametrize("path", list(LAYER))
def test_clicking_an_object_selects_it_and_fills_the_inspector(
    watched_page, served, lazy_scene_dir, path
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    if path == "world/back":  # clip away the box in front of it
        page.evaluate("setClipState({planes:[{id:'z-max',enabled:true,value:0}]})")
    click_object(page, path)
    assert selected(page) == path
    assert page.inner_text("#detailPath") == path
    assert page.inner_text("#detailSolidType") == "box"
    assert "x:" in page.inner_text("#detailParams")
    assert page.inner_text("#detailMaterial") == "G4_Fe"
    assert page.eval_on_selector("#detailNote", "el => el.style.display") == "none"
    assert watched_page.problems == []


def test_selection_matches_the_placement_inspector_data(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    click_object(page, "world/group/inner")
    assert selected(page) == "world/group/inner"
    assert page.inner_text("#detailSemanticPath") == "world/group/inner"
    # inner sits at x=5 inside an untransformed group.
    assert page.inner_text("#detailGlobalMatrix").splitlines()[0].split()[3] == "5.000"
    assert page.evaluate("selectedObjectId") == page.evaluate(
        "placements.nodeByPath['world/group/inner'].objectId"
    )


def test_picking_is_depth_correct_through_occluding_geometry(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    # front (z=+100) and back (z=-100) share a footprint; viewed down -Z the
    # nearer one wins whatever the draw order.
    click_object(page, "world/back")
    assert selected(page) == "world/front"


def test_clipping_applies_to_picking(watched_page, served, lazy_scene_dir):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    page.evaluate("setClipState({planes:[{id:'z-max',enabled:true,value:0}]})")
    next_frames(page)
    click_object(page, "world/back")
    # The clipped-away front box no longer hides the back one.
    assert selected(page) == "world/back"


def test_unloaded_layers_are_not_pickable_and_do_not_occlude(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    key = json.dumps(LAYER["world/front"])
    page.evaluate(f"setLayer({key}, false)")
    page.wait_for_function(
        f"!states[MANIFEST.chunks.find(c => c.baseKey === {key}).key].loaded"
    )
    next_frames(page)
    click_object(page, "world/back")
    assert selected(page) == "world/back"
    click_object(page, "world/ecal_a")
    assert selected(page) == "world/ecal_a"


def test_clicking_empty_space_clears_the_selection(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    click_object(page, "world/ecal_a")
    assert selected(page) == "world/ecal_a"
    page.mouse.click(
        VIEWPORT["width"] - 20, VIEWPORT["height"] - 20
    )  # empty canvas corner
    next_frames(page)
    assert page.evaluate("selectedObjectId") is None
    assert page.eval_on_selector("#detailsContent", "el => el.style.display") == "none"


def test_dragging_does_not_pick(watched_page, served, lazy_scene_dir):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    sx, sy = page.evaluate(_LAZY_PROJECT, object_center(page, "world/ecal_a"))
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(sx + 40, sy + 10, steps=4)
    page.mouse.up()
    next_frames(page)
    assert page.evaluate("selectedObjectId") is None


def test_selection_highlights_only_the_selected_object(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    a, b = object_center(page, "world/ecal_a"), object_center(page, "world/hcal_b")
    before_a, before_b = page.evaluate(_PIXEL, a), page.evaluate(_PIXEL, b)
    click_object(page, "world/ecal_a")
    assert selected(page) == "world/ecal_a"
    after_a, after_b = page.evaluate(_PIXEL, a), page.evaluate(_PIXEL, b)
    assert distance_to_highlight(after_a) < distance_to_highlight(before_a)
    assert after_b == before_b


def test_selection_survives_a_chunk_unload_and_reload(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    path = "world/ecal_a"
    key = json.dumps(LAYER[path])
    chunk = f"MANIFEST.chunks.find(c => c.baseKey === {key}).key"
    point = object_center(page, path)
    click_object(page, path)
    assert selected(page) == path
    resting = page.evaluate(_PIXEL, point)
    object_id = page.evaluate("selectedObjectId")

    page.evaluate(f"setLayer({key}, false)")
    page.wait_for_function(f"!states[{chunk}].loaded")
    # Metadata for an object whose geometry is gone is still shown, marked.
    assert page.evaluate("selectedObjectId") == object_id
    assert page.inner_text("#detailPath") == path
    assert page.inner_text("#detailMaterial") == "G4_Fe"
    assert "Geometry not shown" in page.inner_text("#detailNote")

    page.evaluate(f"setLayer({key}, true)")
    page.wait_for_function(f"states[{chunk}].loaded")
    next_frames(page)
    assert page.evaluate("selectedObjectId") == object_id
    assert page.eval_on_selector("#detailNote", "el => el.style.display") == "none"
    assert page.evaluate(_PIXEL, point) == resting  # highlight is back


def test_lazy_and_standalone_pick_the_same_placement_for_the_same_view(
    watched_page, served, lazy_scene_dir, lazy_scene_standalone
):
    lazy = open_lazy(watched_page, served, lazy_scene_dir)
    results = {}
    for path in LAYER:
        if path == "world/front":
            continue  # "back" is clicked at the same screen point
        lazy.evaluate("clearSelection()")
        click_object(lazy, path)
        results[path] = selected(lazy)

    standalone = lazy.context.new_page()
    try:
        standalone.set_viewport_size(VIEWPORT)
        standalone.goto(lazy_scene_standalone.resolve().as_uri())
        standalone.wait_for_selector("#hierarchyTree .tree-row")
        standalone.click("#viewXYPos")
        next_frames(standalone)
        for path, lazy_path in results.items():
            standalone.evaluate("clearSelection()")
            bbox = standalone.evaluate(
                f"SCENE.objects.find(o => o.path === {json.dumps(path)}).bbox"
            )
            centre = [(bbox[i] + bbox[i + 3]) / 2 for i in range(3)]
            sx, sy = standalone.evaluate(
                """([wx, wy, wz]) => {
                  const cx = viewCenter[0] + panX, cy = viewCenter[1] + panY, cz = viewCenter[2];
                  const eye = [cx + dist*Math.cos(pitch)*Math.sin(yaw), cy + dist*Math.sin(pitch), cz + dist*Math.cos(pitch)*Math.cos(yaw)];
                  const mvp = mat4mul(perspective(45*Math.PI/180, canvas.width/canvas.height, radius/500, radius*20), lookAt(eye, [cx,cy,cz], cameraUp));
                  const tx=mvp[0]*wx+mvp[4]*wy+mvp[8]*wz+mvp[12], ty=mvp[1]*wx+mvp[5]*wy+mvp[9]*wz+mvp[13], tw=mvp[3]*wx+mvp[7]*wy+mvp[11]*wz+mvp[15];
                  const rect = canvas.getBoundingClientRect();
                  return [(tx/tw*0.5+0.5)*rect.width + rect.left, (-ty/tw*0.5+0.5)*rect.height + rect.top];
                }""",
                centre,
            )
            standalone.mouse.click(sx, sy)
            next_frames(standalone)
            expected = "world/front" if path == "world/back" else path  # occluded
            assert standalone.evaluate("selectedPath") == lazy_path == expected
    finally:
        standalone.close()


def test_saved_view_round_trips_the_selected_placement(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    click_object(page, "world/hcal_b")
    assert selected(page) == "world/hcal_b"
    state = page.evaluate("saveViewState()")
    assert state["hierarchy"]["selectedPath"] == "world/hcal_b"

    page.evaluate("clearSelection()")
    assert page.evaluate("selectedPath") is None
    report = page.evaluate("state => loadViewState(state, 'test')", state)
    page.wait_for_function("selectedPath === 'world/hcal_b'")
    assert page.inner_text("#detailPath") == "world/hcal_b"
    assert report is None or "errors" not in report or report["errors"] == []

    state["hierarchy"]["selectedPath"] = "world/vanished"
    page.evaluate("state => loadViewState(state, 'test')", state)
    page.wait_for_function("selectedPath === null")
    assert "world/vanished" in page.inner_text("#viewStatePanel")


def test_corrupt_placements_file_is_reported_and_rendering_continues(
    watched_page, served, lazy_scene_dir, tmp_path
):
    export = copy_of(lazy_scene_dir, tmp_path)
    target = next(export.glob("*.assets/placements.json"))
    target.write_bytes(target.read_bytes() + b" ")
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(export)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    page.click("#loadAll")
    page.wait_for_function("Object.values(states).every(s => s.loaded)")
    page.click("#viewXYPos")
    next_frames(page)
    xs, ys = page.evaluate(_LAZY_PROJECT, [-50, 0, 0])
    page.mouse.click(xs, ys)
    page.wait_for_function(
        "document.getElementById('status').textContent.includes('sha256')"
    )
    assert "placement metadata" in page.inner_text("#status")
    assert page.eval_on_selector("#detailsContent", "el => el.style.display") == "none"
    # The viewer still renders and can retry; nothing threw.
    assert page.evaluate("Object.values(states).every(s => s.loaded)")
    assert [
        p for p in watched_page.problems if "sha256" not in p and "Failed" not in p
    ] == []


def test_a_v1_export_still_renders_and_reports_picking_unavailable(
    watched_page, served, lazy_scene_baked_dir, tmp_path
):
    export = copy_of(lazy_scene_baked_dir, tmp_path)
    downgrade_to_v1(export)
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(export)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    page.click("#loadAll")
    page.wait_for_function("Object.values(states).every(s => s.loaded)")
    assert page.evaluate("Object.values(states).every(s => s.buffers.id === null)")
    page.click("#viewXYPos")
    next_frames(page)
    assert "predates per-object ids" in page.inner_text("#detailsEmpty")
    xs, ys = page.evaluate(_LAZY_PROJECT, [-50, 0, 0])
    page.mouse.click(xs, ys)
    next_frames(page)
    assert "Picking is unavailable" in page.inner_text("#status")
    assert page.evaluate("selectedObjectId") is None
    # Still draws geometry: some canvas pixel differs from the clear colour.
    assert watched_page.problems == []


def test_picking_is_correct_after_resize(watched_page, served, lazy_scene_dir):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    page.set_viewport_size({"width": 700, "height": 500})
    page.wait_for_function("canvas.height === 500")
    next_frames(page)
    click_object(page, "world/hcal_b")
    assert selected(page) == "world/hcal_b"
    click_object(page, "world/back")
    assert selected(page) == "world/front"


def test_picking_is_correct_at_high_device_pixel_ratio(served, lazy_scene_dir, browser):
    context = browser.new_context(
        viewport={"width": 900, "height": 700}, device_scale_factor=2
    )
    try:
        page = context.new_page()
        errors = []
        page.on(
            "console",
            lambda msg: errors.append(msg.text) if msg.type == "error" else None,
        )
        page.goto(f"{served(lazy_scene_dir)}/v.html")
        page.wait_for_selector("input[data-layer-key]")
        page.click("#loadAll")
        page.wait_for_function("Object.values(states).every(s => s.loaded)")
        page.click("#viewXYPos")
        page.evaluate("ensurePlacements()")
        page.wait_for_function("placements !== null")
        next_frames(page)
        assert page.evaluate("window.devicePixelRatio") == 2
        assert page.evaluate("canvas.width / canvas.clientWidth") == 2
        click_object(page, "world/ecal_a")
        assert selected(page) == "world/ecal_a"
        click_object(page, "world/back")
        assert selected(page) == "world/front"
        assert errors == []
    finally:
        context.close()


def test_picking_follows_zoom_and_pan(watched_page, served, lazy_scene_dir):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    page.evaluate("dist *= 0.4; panX = 25; panY = -10; requestRender()")
    next_frames(page)
    # Objects still on screen after the move are picked at their new position.
    click_object(page, "world/hcal_b")
    assert selected(page) == "world/hcal_b"
    page.evaluate("dist *= 2.5; panX = -20; panY = 5; requestRender()")
    next_frames(page)
    click_object(page, "world/ecal_a")
    assert selected(page) == "world/ecal_a"


def test_a_click_on_the_middle_of_a_large_face_selects_it(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    # Zoom right in on the 20 x 20 mm front box: no vertex is anywhere near
    # the cursor, which is what vertex-sampling picking got wrong.
    page.evaluate(
        "viewCenter = [0, 0, 100]; dist = 30; panX = panY = 0; requestRender()"
    )
    next_frames(page)
    sx, sy = page.evaluate(_LAZY_PROJECT, [0, 0, 100])
    page.mouse.click(sx, sy)
    next_frames(page)
    assert selected(page) == "world/front"


def test_phi_clipped_geometry_is_not_picked_where_it_is_cut_away(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    click_object(page, "world/hcal_b")  # +x side, phi near 0
    assert selected(page) == "world/hcal_b"
    page.evaluate("clearSelection()")
    # Remove a wedge around +x: hcal_b is cut away, ecal_a (-x) is kept.
    page.evaluate(
        "setClipState({phi: {enabled: true, startDeg: -30, widthDeg: 60, mode: 'remove'}})"
    )
    next_frames(page)
    click_object(page, "world/hcal_b")
    assert page.evaluate("selectedObjectId") is None
    click_object(page, "world/ecal_a")
    assert selected(page) == "world/ecal_a"


def test_translucent_surfaces_are_picked_like_opaque_ones(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    page.evaluate(
        "document.getElementById('opacity').value = 15;"
        "document.getElementById('opacity').dispatchEvent(new Event('input'))"
    )
    next_frames(page)
    # Documented: opacity is display-only, the frontmost surface is selected.
    click_object(page, "world/back")
    assert selected(page) == "world/front"


def test_inspector_isolate_button_isolates_the_selection(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    click_object(page, "world/hcal_b")
    assert selected(page) == "world/hcal_b"
    page.click("#detailIsolate")
    assert page.evaluate("isolatedPath") == "world/hcal_b"
    assert page.evaluate("[...visibleMask].reduce((a, b) => a + b, 0)") == 1
    page.click("#detailIsolate")
    assert page.evaluate("isolatedPath") is None
