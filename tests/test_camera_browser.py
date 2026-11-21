"""Projection, scale bar and fit/reset in both viewers."""

import math
import re

import pytest

from .browser_helpers import VIEWPORT, next_frames

VIEWS = ["viewXYPos", "viewXYNeg", "viewXZPos", "viewXZNeg", "viewYZPos", "viewYZNeg"]
SCREEN = """([wx, wy, wz]) => {
  const mvp = cameraMvp();
  const tx=mvp[0]*wx+mvp[4]*wy+mvp[8]*wz+mvp[12], ty=mvp[1]*wx+mvp[5]*wy+mvp[9]*wz+mvp[13], tw=mvp[3]*wx+mvp[7]*wy+mvp[11]*wz+mvp[15];
  const rect = canvas.getBoundingClientRect();
  return [(tx/tw*0.5+0.5)*rect.width + rect.left, (-ty/tw*0.5+0.5)*rect.height + rect.top];
}"""


class Viewer:
    """The two viewers behind one small interface for these tests."""

    def __init__(self, kind, watched_page, served, lazy_dir, standalone_html):
        self.kind = kind
        self.page = watched_page.page
        self.watched = watched_page
        self.page.set_viewport_size(VIEWPORT)
        if kind == "standalone":
            self.page.goto(standalone_html.resolve().as_uri())
            self.page.wait_for_selector("#hierarchyTree .tree-row")
            self.objects = "SCENE.objects"
        else:
            self.page.goto(f"{served(lazy_dir)}/v.html")
            self.page.wait_for_selector("input[data-layer-key]")
            self.page.click("#loadAll")
            self.page.wait_for_function("Object.values(states).every(s => s.loaded)")
            self.page.evaluate("ensurePlacements()")
            self.page.wait_for_function("placements !== null")
            self.page.evaluate("ensureAllDetails()")
            self.objects = "placements.objects"
        self.page.uncheck("#coordinateAxes")
        next_frames(self.page)

    def project(self, point):
        return self.page.evaluate(SCREEN, point)

    def centre(self, path):
        bbox = self.page.evaluate(f"{self.objects}.find(o => o.path === '{path}').bbox")
        return [(bbox[i] + bbox[i + 3]) / 2 for i in range(3)]

    def projection(self, name):
        self.page.select_option("#projection", name)
        next_frames(self.page)

    def click_object(self, path):
        x, y = self.project(self.centre(path))
        self.page.mouse.click(x, y)
        next_frames(self.page)

    def selected(self):
        if self.kind == "lazy":
            self.page.wait_for_function(
                "selectedObjectId === null || selectedPath !== null"
            )
        return self.page.evaluate("selectedPath")

    def hide_all_but(self, keep):
        if self.kind == "standalone":
            self.page.evaluate(
                "paths => { for (const o of SCENE.objects) if (!paths.includes(o.path)) hierarchyHidden.add(o.id); applyFilters(); }",
                keep,
            )
        else:
            self.page.evaluate("ensurePlacements()")
            self.page.wait_for_function("placements !== null")
            self.page.evaluate(
                "paths => { for (const o of placements.objects) if (!paths.includes(o.path)) hierarchyHidden.add(o.id); recomputeVisibility(); }",
                keep,
            )
        next_frames(self.page)

    def move_camera(self, statement):
        """Change the camera state; the lazy viewer only redraws on request."""
        self.page.evaluate(
            f"{statement}; if (typeof requestRender === 'function') requestRender()"
        )
        next_frames(self.page)

    def wait_idle(self):
        if self.kind == "lazy":
            self.page.wait_for_function("fitInFlight === 0 && focusInFlight === 0")


@pytest.fixture(params=["standalone", "lazy"])
def viewer(request, watched_page, served, lazy_scene_dir, lazy_scene_standalone):
    return Viewer(
        request.param, watched_page, served, lazy_scene_dir, lazy_scene_standalone
    )


def screen_vectors(viewer, view):
    """Where each world axis points on screen (pixels) in `view`."""
    viewer.page.click(f"#{view}")
    next_frames(viewer.page)
    ox, oy = viewer.project([0, 0, 0])
    result = {}
    for axis, vector in (("x", [30, 0, 0]), ("y", [0, 30, 0]), ("z", [0, 0, 30])):
        x, y = viewer.project(vector)
        result[axis] = (x - ox, y - oy)
    return result


def test_axis_views_keep_their_orientation_when_the_projection_changes(viewer):
    perspective = {v: screen_vectors(viewer, v) for v in VIEWS}
    viewer.projection("orthographic")
    assert viewer.page.evaluate("projection") == "orthographic"
    orthographic = {v: screen_vectors(viewer, v) for v in VIEWS}
    for view in VIEWS:
        shown = 0
        for axis in "xyz":
            for component in (0, 1):
                exact = orthographic[view][axis][component]
                if abs(exact) < 10:
                    continue  # along the line of sight (perspective parallax there)
                shown += 1
                assert math.copysign(
                    1, perspective[view][axis][component]
                ) == math.copysign(1, exact), (view, axis)
        assert shown == 2  # two axes are visible in every axis view
    # And the convention itself: looking down -z, +x is to the right and +y is up
    # (screen y grows downward).
    assert orthographic["viewXYPos"]["x"][0] > 10
    assert orthographic["viewXYPos"]["y"][1] < -10
    assert viewer.watched.problems == []


def test_the_target_plane_looks_the_same_in_both_projections(viewer):
    viewer.page.click("#viewXYPos")
    next_frames(viewer.page)
    target = viewer.page.evaluate("cameraTarget(cameraState())")
    here = viewer.project([target[0] + 40, target[1] + 25, target[2]])
    viewer.projection("orthographic")
    there = viewer.project([target[0] + 40, target[1] + 25, target[2]])
    assert there == pytest.approx(here, abs=0.01)


def test_orthographic_has_no_perspective_foreshortening(viewer):
    viewer.page.click("#viewXYPos")
    next_frames(viewer.page)

    def width(z):
        left, _ = viewer.project([-10, 0, z])
        right, _ = viewer.project([10, 0, z])
        return right - left

    near, far = width(100), width(-100)  # the "front" and "back" boxes' depths
    assert near > far * 1.2  # perspective: the nearer box looks bigger
    viewer.projection("orthographic")
    assert width(100) == pytest.approx(width(-100), abs=0.01)


def test_the_scale_bar_matches_the_geometry_and_the_zoom(viewer):
    viewer.page.click("#viewXYPos")
    next_frames(viewer.page)

    def check():
        target = viewer.page.evaluate("cameraTarget(cameraState())")
        a = viewer.project(target)
        b = viewer.project([target[0] + 100, target[1], target[2]])
        pixels_per_100mm = b[0] - a[0]
        label = viewer.page.inner_text("#scaleBarLabel")
        match = re.match(r"([\d.]+) (µm|mm|m)\b", label)
        assert match, label
        mm = float(match.group(1)) * {"µm": 1e-3, "mm": 1, "m": 1000}[match.group(2)]
        expected = mm * pixels_per_100mm / 100
        shown = float(
            viewer.page.eval_on_selector("#scaleBarLine", "e => e.style.width").rstrip(
                "px"
            )
        )
        assert shown == pytest.approx(expected, abs=1.5), (label, shown, expected)
        assert 40 < shown < 260  # a readable length
        return label

    first = check()
    assert "(at the target)" in first  # perspective: only exact at the target plane
    viewer.move_camera("dist *= 0.05")
    zoomed = check()
    assert zoomed != first
    viewer.projection("orthographic")
    assert "(at the target)" not in check()
    viewer.move_camera("dist *= 20")
    check()


def test_fit_selection_centres_the_selected_placement(viewer):
    assert viewer.page.is_disabled("#fitSelection")
    viewer.page.click("#viewXYPos")
    viewer.click_object("world/hcal_b")
    assert viewer.selected() == "world/hcal_b"
    assert viewer.page.is_enabled("#fitSelection")
    before = viewer.page.evaluate("dist")
    viewer.page.click("#fitSelection")
    viewer.wait_idle()
    viewer.page.wait_for_function(f"dist < {before}")
    box = viewer.page.evaluate("viewCenter")
    assert box == pytest.approx(viewer.centre("world/hcal_b"), abs=1e-6)


def test_fit_all_frames_only_what_is_shown_and_never_an_empty_view(viewer):
    viewer.hide_all_but(["world/hcal_b"])
    viewer.page.click("#fitAll")
    viewer.wait_idle()
    viewer.page.wait_for_function("dist < radius")
    assert viewer.page.evaluate("viewCenter") == pytest.approx(
        viewer.centre("world/hcal_b"), abs=1e-6
    )
    viewer.hide_all_but([])  # nothing at all is shown
    viewer.page.click("#fitAll")
    viewer.wait_idle()
    assert viewer.page.evaluate("viewCenter") == pytest.approx(
        viewer.page.evaluate("center"), abs=1e-6
    )
    assert viewer.page.evaluate("dist") == pytest.approx(
        viewer.page.evaluate("radius") * 1.35, rel=1e-6
    )


def test_reset_restores_a_usable_view_after_extreme_pan_and_zoom(viewer):
    viewer.move_camera("panX = 1e7; panY = -3e7; dist = radius * 1e-9")
    viewer.page.click("#reset" if viewer.kind == "standalone" else "#resetView")
    next_frames(viewer.page)
    state = viewer.page.evaluate("cameraState()")
    assert state["panX"] == state["panY"] == 0
    assert state["dist"] == pytest.approx(viewer.page.evaluate("radius") * 1.35)
    x, y = viewer.project(viewer.centre("world/hcal_b"))
    box = viewer.page.evaluate("canvas.getBoundingClientRect().toJSON()")
    assert (
        box["x"] < x < box["x"] + box["width"]
        and box["y"] < y < box["y"] + box["height"]
    )
    assert all(math.isfinite(v) for v in (x, y))


def test_wheel_zoom_stays_within_sane_limits(viewer):
    box = viewer.page.evaluate("canvas.getBoundingClientRect().toJSON()")
    viewer.page.mouse.move(box["x"] + 200, box["y"] + 200)
    for _ in range(8):
        viewer.page.mouse.wheel(0, -100000)
    next_frames(viewer.page)
    radius = viewer.page.evaluate("radius")
    assert viewer.page.evaluate("dist") >= radius * 0.01 * 0.999
    for _ in range(8):
        viewer.page.mouse.wheel(0, 100000)
    next_frames(viewer.page)
    assert viewer.page.evaluate("dist") <= radius * 50 * 1.001


def test_picking_and_selection_work_in_orthographic_projection(viewer):
    viewer.page.click("#viewXYPos")
    viewer.projection("orthographic")
    for path in ("world/ecal_a", "world/hcal_b", "world/ecal_c"):
        viewer.click_object(path)
        assert viewer.selected() == path
    # Depth-correct in orthographic too: "front" hides "back" at the same x, y.
    viewer.click_object("world/back")
    assert viewer.selected() == "world/front"
    # Highlight follows the pick.
    if viewer.kind == "standalone":
        assert viewer.page.evaluate("selectedObjectId") == viewer.page.evaluate(
            "SCENE.objects.find(o => o.path === 'world/front').id"
        )
    else:
        assert viewer.page.evaluate("selectedObjectId") == viewer.page.evaluate(
            "placements.objects.find(o => o.path === 'world/front').id"
        )
    assert viewer.watched.problems == []


def test_clipping_and_camera_changes_do_not_break_picking(viewer):
    viewer.page.click("#viewXYPos")
    viewer.projection("orthographic")
    viewer.page.evaluate("setClipState({planes:[{id:'z-max',enabled:true,value:0}]})")
    next_frames(viewer.page)
    viewer.click_object("world/back")
    assert viewer.selected() == "world/back"  # the front box is clipped away
    viewer.page.evaluate("setClipState({})")
    viewer.page.click("#fitAll")
    viewer.wait_idle()
    next_frames(viewer.page)
    viewer.click_object("world/hcal_b")
    assert viewer.selected() == "world/hcal_b"


def test_the_projection_is_saved_and_restored(viewer):
    viewer.projection("orthographic")
    state = viewer.page.evaluate("saveViewState()")
    assert state["projection"] == {"type": "orthographic", "fovDeg": 45}
    viewer.projection("perspective")
    viewer.page.evaluate("state => loadViewState(state, 'test')", state)
    viewer.page.wait_for_function("projection === 'orthographic'")
    assert viewer.page.input_value("#projection") == "orthographic"


def test_a_view_saved_in_one_viewer_sets_the_projection_in_the_other(
    watched_page, served, lazy_scene_dir, lazy_scene_standalone
):
    standalone = Viewer(
        "standalone", watched_page, served, lazy_scene_dir, lazy_scene_standalone
    )
    standalone.projection("orthographic")
    state = standalone.page.evaluate("saveViewState()")
    lazy = Viewer("lazy", watched_page, served, lazy_scene_dir, lazy_scene_standalone)
    report = lazy.page.evaluate(
        """async state => {
          const result = {applied: [], ignored: [], unknown: [], warnings: [], errors: []};
          await applyViewSections(validateViewState(state).state, result);
          return result;
        }""",
        state,
    )
    assert "projection" in report["applied"]
    assert lazy.page.evaluate("projection") == "orthographic"
