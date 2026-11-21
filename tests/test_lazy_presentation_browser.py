"""Lazy-viewer presentation: the wireframe overlay and the light/dark theme,
matching the standalone viewer's controls."""

import json

import pytest

from .browser_helpers import VIEWPORT, next_frames
from .lazy_scene import click_object, selected

_PIXEL_AT = """([wx, wy, wz]) => {
  render();
  const mvp = cameraMvp();
  const tx=mvp[0]*wx+mvp[4]*wy+mvp[8]*wz+mvp[12], ty=mvp[1]*wx+mvp[5]*wy+mvp[9]*wz+mvp[13], tw=mvp[3]*wx+mvp[7]*wy+mvp[11]*wz+mvp[15];
  const px = new Uint8Array(4);
  gl.readPixels(Math.floor((tx/tw*0.5+0.5)*canvas.width), Math.floor((ty/tw*0.5+0.5)*canvas.height), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return Array.from(px);
}"""
_CORNER = """() => { render(); const p = new Uint8Array(4); gl.readPixels(2, 2, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p); return Array.from(p); }"""

FRONT_CENTRE = [0, 0, 100]  # on the diagonal of the front box's face
FRONT_OFF_EDGE = [5, -5, 100]  # inside a triangle, away from every edge


def open_scene(watched_page, served, directory, *, expand=False):
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(directory)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    if expand:
        page.evaluate("forceExpand = true")
    page.click("#loadAll")
    page.wait_for_function("Object.values(states).every(s => s.loaded)")
    page.click("#viewXYPos")
    page.uncheck("#coordinateAxes")  # the axes would cross the sampled pixels
    # Fill the view with the 20 x 20 mm front box so its edges are many pixels apart.
    page.evaluate(
        "viewCenter = [0, 0, 100]; dist = 30; panX = panY = 0; requestRender()"
    )
    next_frames(page)
    return page


def pixel(page, point):
    return page.evaluate(_PIXEL_AT, point)


def set_wireframe(page, on):
    page.set_checked("#wireframe", on)
    next_frames(page)


@pytest.mark.parametrize(
    "fixture", ["lazy_scene_baked_dir", "lazy_scene_instanced_dir"]
)
@pytest.mark.parametrize("expand", [False, True])
def test_wireframe_draws_triangle_edges_over_the_fill(
    watched_page, served, request, fixture, expand
):
    page = open_scene(
        watched_page, served, request.getfixturevalue(fixture), expand=expand
    )
    fill_centre, fill_off = pixel(page, FRONT_CENTRE), pixel(page, FRONT_OFF_EDGE)
    set_wireframe(page, True)
    assert pixel(page, FRONT_CENTRE) != fill_centre  # the diagonal passes through it
    assert pixel(page, FRONT_OFF_EDGE) == fill_off  # the face interior is untouched
    set_wireframe(page, False)
    assert pixel(page, FRONT_CENTRE) == fill_centre
    assert watched_page.problems == []


def test_the_overlay_follows_the_scene_hiding_clipping_and_picking(
    watched_page, served, lazy_scene_dir
):
    page = open_scene(watched_page, served, lazy_scene_dir)
    set_wireframe(page, True)
    with_lines = pixel(page, FRONT_CENTRE)
    # Hiding the object takes its lines with it (the back box behind shows instead).
    page.evaluate("ensurePlacements()")
    page.wait_for_function("placements !== null")
    page.evaluate(
        "hierarchyHidden.add(placements.objects.find(o => o.path === 'world/front').id); recomputeVisibility()"
    )
    next_frames(page)
    assert pixel(page, FRONT_CENTRE) != with_lines
    page.evaluate("hierarchyHidden.clear(); recomputeVisibility()")
    next_frames(page)
    assert pixel(page, FRONT_CENTRE) == with_lines
    # Clipping removes them too.
    page.evaluate("setClipState({planes:[{id:'z-max',enabled:true,value:50}]})")
    next_frames(page)
    assert pixel(page, FRONT_CENTRE) != with_lines
    page.evaluate("setClipState({})")
    next_frames(page)
    # Picking and highlight are unaffected by the overlay.
    page.evaluate("viewCenter = center.slice(); dist = radius * 1.35; requestRender()")
    next_frames(page)
    click_object(page, "world/hcal_b")
    assert selected(page) == "world/hcal_b"
    assert watched_page.problems == []


def test_toggling_the_overlay_with_mixed_chunks_leaves_no_gl_error(
    watched_page, served, lazy_scene_dir
):
    page = open_scene(watched_page, served, lazy_scene_dir)
    assert set(page.evaluate("Object.values(states).map(s => s.buffers.kind)")) == {
        "baked",
        "instanced",
    }
    for on in (True, False, True):
        set_wireframe(page, on)
        errors = page.evaluate(
            "(() => { render(); const e = []; let x; while ((x = gl.getError()) !== 0 && e.length < 10) e.push(x); return e; })()"
        )
        assert errors == []


def test_dark_mode_changes_the_page_and_the_canvas_background(
    watched_page, served, lazy_scene_dir
):
    page = open_scene(watched_page, served, lazy_scene_dir)
    assert page.is_checked("#darkMode")
    dark = page.evaluate(_CORNER)
    page.uncheck("#darkMode")
    next_frames(page)
    assert "light" in page.get_attribute("body", "class")
    light = page.evaluate(_CORNER)
    assert (
        sum(light[:3]) > sum(dark[:3]) + 300
    )  # a pale background replaces the navy one
    assert (
        page.evaluate("getComputedStyle(document.body).backgroundColor")
        != "rgb(7, 17, 31)"
    )
    page.check("#darkMode")
    next_frames(page)
    assert page.evaluate(_CORNER) == dark


def test_the_wire_colour_follows_the_theme(watched_page, served, lazy_scene_baked_dir):
    page = open_scene(watched_page, served, lazy_scene_baked_dir)
    set_wireframe(page, True)
    page.uncheck("#darkMode")
    next_frames(page)
    light_fill = pixel(page, FRONT_OFF_EDGE)
    light_edge = pixel(page, FRONT_CENTRE)
    assert sum(light_edge[:3]) < sum(light_fill[:3])  # dark lines on a light theme
    page.check("#darkMode")
    next_frames(page)
    dark_fill = pixel(page, FRONT_OFF_EDGE)
    dark_edge = pixel(page, FRONT_CENTRE)
    assert sum(dark_edge[:3]) > sum(dark_fill[:3])  # light lines on a dark theme


def test_saved_views_round_trip_wireframe_and_theme_without_ignoring_them(
    watched_page, served, lazy_scene_dir
):
    page = open_scene(watched_page, served, lazy_scene_dir)
    set_wireframe(page, True)
    page.uncheck("#darkMode")
    state = page.evaluate("saveViewState()")
    assert (
        state["display"]["wireframe"] is True and state["display"]["theme"] == "light"
    )
    set_wireframe(page, False)
    page.check("#darkMode")
    report = page.evaluate(
        """async state => {
          const result = {applied: [], ignored: [], unknown: [], warnings: [], errors: []};
          await applyViewSections(validateViewState(state).state, result);
          return result;
        }""",
        state,
    )
    assert report["ignored"] == []
    assert page.is_checked("#wireframe") and not page.is_checked("#darkMode")


def test_a_standalone_view_sets_the_lazy_theme_and_wireframe(
    watched_page, served, lazy_scene_dir, lazy_scene_standalone
):
    standalone = watched_page.page
    standalone.goto(lazy_scene_standalone.resolve().as_uri())
    standalone.wait_for_selector("#hierarchyTree .tree-row")
    standalone.check("#wireframe")
    standalone.check("#darkMode")
    state = standalone.evaluate("saveViewState()")
    page = open_scene(watched_page, served, lazy_scene_dir)
    page.evaluate("state => loadViewState(state, 'standalone')", state)
    page.wait_for_function("document.getElementById('wireframe').checked === true")
    assert page.is_checked("#darkMode")
    assert json.dumps(state["display"]["wireframe"]) == "true"
