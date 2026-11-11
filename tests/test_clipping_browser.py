"""Clipping planes and phi wedge (issue #5), verified three ways: the shared
reference implementation against analytic points, the real GPU output of
both viewers against analytic expectations, and the two viewers against each
other. Uses the bundled synthetic detector (tubes about the z axis)."""

import math
import shutil
from pathlib import Path

import pytest

from gdml_inspector import cli
from gdml_inspector._assets import read_asset

from .browser_helpers import VIEWPORT, next_frames, set_range

pytestmark = pytest.mark.usefixtures("webgl_renderer")

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"
# Explicit and identical for both exporters: --lazy defaults to "fast" and the
# standalone export to "medium", and different tessellations of the same
# circles would be mistaken for different kept regions.
QUALITY = "medium"


@pytest.fixture(scope="module")
def standalone_html(tmp_path_factory):
    output = tmp_path_factory.mktemp("clip-standalone") / "viewer.html"
    args = [
        str(EXAMPLE),
        "--backend",
        "python",
        "--quality",
        QUALITY,
        "--out",
        str(output),
    ]
    assert cli.main(args) == 0
    return output


@pytest.fixture(scope="module")
def lazy_template(tmp_path_factory):
    directory = tmp_path_factory.mktemp("clip-lazy")
    output = directory / "lazy.html"
    args = ["--lazy", str(EXAMPLE), "--backend", "python", "--quality", QUALITY]
    args += ["--out", str(output)]
    assert cli.main(args) == 0
    return directory


def open_standalone(page, html):
    page.set_viewport_size(VIEWPORT)
    page.goto(html.resolve().as_uri())
    page.wait_for_selector("#hierarchyTree .tree-row")
    page.click("#viewXYPos")
    page.uncheck("#coordinateAxes")
    next_frames(page)
    return page


def open_lazy(page, served, template, tmp_path):
    directory = tmp_path / "lazy"
    shutil.copytree(template, directory)
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(directory)}/lazy.html")
    page.wait_for_selector("input[data-layer-key]")
    page.click("#loadAll")
    page.wait_for_function(
        "MANIFEST.chunks.every(c => states[c.key].loaded && states[c.key].visible)"
    )
    page.click("#viewXYPos")
    page.uncheck("#coordinateAxes")
    page.wait_for_function("!renderPending")
    return page


# Renders one frame and returns the canvas coverage as a coarse boolean grid
# (a cell is covered when at least half its pixels differ from the clear
# colour), plus the grid size.
COVERAGE_JS = """(cell) => {
  render();
  const w = canvas.width, h = canvas.height;
  const buf = new Uint8Array(w * h * 4);
  gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
  const bg = [buf[0], buf[1], buf[2]];
  const cols = Math.floor(w / cell), rows = Math.floor(h / cell);
  const grid = [];
  for (let r = 0; r < rows; r++) {
    const line = [];
    for (let c = 0; c < cols; c++) {
      let covered = 0;
      for (let y = 0; y < cell; y++) for (let x = 0; x < cell; x++) {
        // readPixels rows run bottom-up; flip so row 0 is the top.
        const i = (((h - 1 - (r * cell + y)) * w) + (c * cell + x)) * 4;
        if (Math.abs(buf[i] - bg[0]) > 3 || Math.abs(buf[i + 1] - bg[1]) > 3 || Math.abs(buf[i + 2] - bg[2]) > 3) covered++;
      }
      line.push(covered * 2 >= cell * cell ? 1 : 0);
    }
    grid.push(line);
  }
  return {grid, cols, rows};
}"""
CELL = 8


def coverage(page):
    return page.evaluate(COVERAGE_JS, CELL)


def covered_cells(result):
    return [
        (r, c)
        for r, row in enumerate(result["grid"])
        for c, value in enumerate(row)
        if value
    ]


def screen_angle_deg(result, r, c):
    """Azimuth of a cell about the canvas centre, for a camera on +z looking
    down -z: screen right is +x, screen up is +y."""
    cx, cy = result["cols"] / 2, result["rows"] / 2
    return math.degrees(math.atan2(-((r + 0.5) - cy), (c + 0.5) - cx))


def apply_state(page, **parts):
    page.evaluate("(parts) => setClipState(parts)", parts)


# --- The shared reference implementation, against analytic points -------------


def polar(angle_deg, radius=100.0, z=0.0):
    a = math.radians(angle_deg)
    return [radius * math.cos(a), radius * math.sin(a), z]


def plane(plane_id, value):
    return {"id": plane_id, "enabled": True, "value": value}


KEEPS_CASES = [
    # phi wedge spanning wraparound at +-180 degrees (start 170, width 40:
    # covers 170..210 == -150).
    (
        "wrap-remove-179",
        {"phi": {"enabled": True, "startDeg": 170, "widthDeg": 40}},
        polar(179),
        False,
    ),
    (
        "wrap-remove--170",
        {"phi": {"enabled": True, "startDeg": 170, "widthDeg": 40}},
        polar(-170),
        False,
    ),
    (
        "wrap-remove--151",
        {"phi": {"enabled": True, "startDeg": 170, "widthDeg": 40}},
        polar(-151),
        False,
    ),
    (
        "wrap-outside--149",
        {"phi": {"enabled": True, "startDeg": 170, "widthDeg": 40}},
        polar(-149),
        True,
    ),
    (
        "wrap-outside-0",
        {"phi": {"enabled": True, "startDeg": 170, "widthDeg": 40}},
        polar(0),
        True,
    ),
    (
        "wrap-keep-179",
        {"phi": {"enabled": True, "startDeg": 170, "widthDeg": 40, "mode": "keep"}},
        polar(179),
        True,
    ),
    (
        "wrap-keep-0",
        {"phi": {"enabled": True, "startDeg": 170, "widthDeg": 40, "mode": "keep"}},
        polar(0),
        False,
    ),
    # wedge spanning zero
    (
        "zero-remove--20",
        {"phi": {"enabled": True, "startDeg": -45, "widthDeg": 90}},
        polar(-20),
        False,
    ),
    (
        "zero-remove-40",
        {"phi": {"enabled": True, "startDeg": -45, "widthDeg": 90}},
        polar(40),
        False,
    ),
    (
        "zero-outside-90",
        {"phi": {"enabled": True, "startDeg": -45, "widthDeg": 90}},
        polar(90),
        True,
    ),
    # the wedge bounds are inclusive
    (
        "start-inclusive",
        {"phi": {"enabled": True, "startDeg": 0, "widthDeg": 90}},
        [100, 0, 0],
        False,
    ),
    (
        "disabled-phi-ignored",
        {"phi": {"enabled": False, "startDeg": 0, "widthDeg": 90}},
        [100, 1, 0],
        True,
    ),
    # axis planes: min keeps >=, max keeps <=, both inclusive
    ("x-min-on", {"planes": [plane("x-min", 10)]}, [10, 0, 0], True),
    ("x-min-below", {"planes": [plane("x-min", 10)]}, [9.999, 0, 0], False),
    ("x-max-on", {"planes": [plane("x-max", 10)]}, [10, 0, 0], True),
    ("x-max-above", {"planes": [plane("x-max", 10)]}, [10.001, 0, 0], False),
    ("y-min", {"planes": [plane("y-min", -3)]}, [0, -4, 0], False),
    ("z-max", {"planes": [plane("z-max", 7)]}, [0, 0, 8], False),
    # slab = a min and a max on one axis
    (
        "slab-inside",
        {"planes": [plane("z-min", -5), plane("z-max", 5)]},
        [0, 0, 4.9],
        True,
    ),
    (
        "slab-above",
        {"planes": [plane("z-min", -5), plane("z-max", 5)]},
        [0, 0, 5.1],
        False,
    ),
    (
        "slab-below",
        {"planes": [plane("z-min", -5), plane("z-max", 5)]},
        [0, 0, -5.1],
        False,
    ),
    # arbitrary plane: normal is normalised, offset is along the unit normal
    (
        "custom-diagonal-keep",
        {
            "planes": [
                {"id": "custom", "enabled": True, "normal": [1, 1, 0], "offset": 0}
            ]
        },
        [1, -0.5, 0],
        True,
    ),
    (
        "custom-diagonal-drop",
        {
            "planes": [
                {"id": "custom", "enabled": True, "normal": [1, 1, 0], "offset": 0}
            ]
        },
        [-1, 0.5, 0],
        False,
    ),
    (
        "custom-unnormalised-offset",
        {
            "planes": [
                {"id": "custom", "enabled": True, "normal": [2, 0, 0], "offset": 3}
            ]
        },
        [3.1, 0, 0],
        True,
    ),
    (
        "custom-unnormalised-offset-drop",
        {
            "planes": [
                {"id": "custom", "enabled": True, "normal": [2, 0, 0], "offset": 3}
            ]
        },
        [2.9, 0, 0],
        False,
    ),
    (
        "custom-zero-normal-ignored",
        {
            "planes": [
                {"id": "custom", "enabled": True, "normal": [0, 0, 0], "offset": 5}
            ]
        },
        [0, 0, 0],
        True,
    ),
    # intersection semantics: a point must pass EVERY enabled condition
    (
        "intersect-plane-drops",
        {
            "planes": [plane("x-min", 0)],
            "phi": {"enabled": True, "startDeg": -45, "widthDeg": 90},
        },
        [-10, 10, 0],
        False,
    ),
    (
        "intersect-phi-drops",
        {
            "planes": [plane("x-min", 0)],
            "phi": {"enabled": True, "startDeg": -45, "widthDeg": 90},
        },
        [10, 0, 0],
        False,
    ),
    (
        "intersect-both-keep",
        {
            "planes": [plane("x-min", 0)],
            "phi": {"enabled": True, "startDeg": -45, "widthDeg": 90},
        },
        [0, 10, 0],
        True,
    ),
]


@pytest.mark.parametrize(
    "label, parts, point, expected", KEEPS_CASES, ids=[c[0] for c in KEEPS_CASES]
)
def test_reference_keeps_the_analytic_region(
    page, standalone_html, label, parts, point, expected
):
    page.goto(standalone_html.resolve().as_uri())
    kept = page.evaluate(
        "([parts, point]) => clipKeeps(sanitizeClipState(parts, null), point)",
        [parts, point],
    )
    assert kept is expected, label


def test_state_is_sanitised_and_round_trips(page, standalone_html):
    page.goto(standalone_html.resolve().as_uri())
    page.wait_for_selector("#clipPanel")
    garbage = page.evaluate(
        "sanitizeClipState({phi: {enabled: 'yes', startDeg: 9999, widthDeg: -4, mode: 'x'},"
        " planes: [{id: 'x-min', enabled: true, value: 'abc'}, {id: 'bogus', enabled: true}],"
        " extra: 1}, null)"
    )
    assert garbage["phi"] == {
        "enabled": False,
        "startDeg": 180,
        "widthDeg": 1,
        "mode": "remove",
    }
    assert [p["id"] for p in garbage["planes"]] == [
        "x-min",
        "x-max",
        "y-min",
        "y-max",
        "z-min",
        "z-max",
        "custom",
    ]
    assert "extra" not in garbage
    state = {
        "version": 1,
        "unit": "mm",
        "phi": {"enabled": True, "startDeg": 170, "widthDeg": 40, "mode": "keep"},
        "planes": [
            {"id": "x-min", "enabled": True, "value": 12.5},
            {"id": "custom", "enabled": True, "normal": [1, 2, 3], "offset": -4},
        ],
    }
    page.evaluate("(s) => setClipState(s)", state)
    back = page.evaluate("getClipState()")
    assert back["phi"] == state["phi"]
    by_id = {p["id"]: p for p in back["planes"]}
    assert by_id["x-min"] == {"id": "x-min", "enabled": True, "value": 12.5}
    assert by_id["custom"]["normal"] == [1, 2, 3] and by_id["custom"]["offset"] == -4
    assert page.evaluate("JSON.stringify(getClipState())") == page.evaluate(
        "(() => { const s = getClipState(); setClipState(s); return JSON.stringify(getClipState()); })()"
    )


# --- Real GPU output ----------------------------------------------------------


def assert_left_half_empty_right_covered(result):
    cells = covered_cells(result)
    centre = result["cols"] / 2
    assert any(c >= centre for _r, c in cells), "right half should keep geometry"
    assert not [(r, c) for r, c in cells if c < centre - 1], "left half must be clipped"


def test_standalone_plane_clips_the_gpu_output(watched_page, standalone_html):
    page = open_standalone(watched_page.page, standalone_html)
    assert len(covered_cells(coverage(page))) > 100
    page.click("#clipPresetLongitudinal")  # keep x >= centre (0)
    assert_left_half_empty_right_covered(coverage(page))
    watched_page.assert_clean()


def test_standalone_phi_wedge_clips_the_gpu_output(watched_page, standalone_html):
    page = open_standalone(watched_page.page, standalone_html)
    apply_state(page, phi={"enabled": True, "startDeg": -45, "widthDeg": 90})
    result = coverage(page)
    cells = covered_cells(result)
    assert len(cells) > 100
    for r, c in cells:
        angle = screen_angle_deg(result, r, c)
        assert not (-45 + 6 < angle < 45 - 6), f"cell at {angle:.0f} deg inside wedge"
    outside = [screen_angle_deg(result, r, c) for r, c in cells]
    assert any(abs(a) > 100 for a in outside)
    watched_page.assert_clean()


def test_standalone_phi_wedge_wraps_across_180_on_the_gpu(
    watched_page, standalone_html
):
    page = open_standalone(watched_page.page, standalone_html)
    apply_state(
        page, phi={"enabled": True, "startDeg": 150, "widthDeg": 60}
    )  # 150..210
    result = coverage(page)
    cells = covered_cells(result)
    for r, c in cells:
        angle = screen_angle_deg(result, r, c)
        wrapped = angle % 360
        assert not (150 + 6 < wrapped < 210 - 6), (
            f"cell at {angle:.0f} deg inside wedge"
        )
    assert any(abs(screen_angle_deg(result, r, c)) < 90 for r, c in cells)
    watched_page.assert_clean()


def test_standalone_keep_mode_keeps_only_the_wedge(watched_page, standalone_html):
    page = open_standalone(watched_page.page, standalone_html)
    apply_state(
        page, phi={"enabled": True, "startDeg": -45, "widthDeg": 90, "mode": "keep"}
    )
    result = coverage(page)
    cells = covered_cells(result)
    assert cells
    for r, c in cells:
        angle = screen_angle_deg(result, r, c)
        assert -45 - 6 < angle < 45 + 6, f"cell at {angle:.0f} deg outside kept wedge"
    watched_page.assert_clean()


def test_standalone_slab_leaves_only_a_band(watched_page, standalone_html):
    page = open_standalone(watched_page.page, standalone_html)
    page.click("#viewYZPos")  # camera on +x, screen vertical = z
    next_frames(page)
    full = covered_cells(coverage(page))
    page.click("#clipPresetSlab")
    band = covered_cells(coverage(page))
    assert band
    rows = lambda cells: max(r for r, _c in cells) - min(r for r, _c in cells)  # noqa: E731
    assert rows(band) < 0.35 * rows(full)
    watched_page.assert_clean()


def test_standalone_wireframe_obeys_the_same_clipping(watched_page, standalone_html):
    page = open_standalone(watched_page.page, standalone_html)
    page.check("#wireframe")
    page.click("#clipPresetLongitudinal")
    assert_left_half_empty_right_covered(coverage(page))
    watched_page.assert_clean()


def test_standalone_highlight_obeys_the_same_clipping(watched_page, standalone_html):
    page = open_standalone(watched_page.page, standalone_html)
    page.evaluate("selectObject(4)")  # the support tube, tinted
    assert page.evaluate("selectedObjectId") == 4
    page.click("#clipPresetLongitudinal")
    assert_left_half_empty_right_covered(coverage(page))
    watched_page.assert_clean()


def test_standalone_picking_uses_the_same_clipping(watched_page, standalone_html):
    page = open_standalone(watched_page.page, standalone_html)
    scan = """() => {
      const left = [], right = [];
      for (let x = 0; x < canvas.width; x += 12) for (let y = 0; y < canvas.height; y += 12) {
        const id = pickAt(x, y);
        if (id >= 0) (x < canvas.width / 2 - 8 ? left : right).push(id);
      }
      return {left: left.length, right: right.length};
    }"""
    before = page.evaluate(scan)
    assert before["left"] > 0 and before["right"] > 0
    page.click("#clipPresetLongitudinal")
    after = page.evaluate(scan)
    assert after["left"] == 0, "a clipped surface must not be pickable"
    assert after["right"] > 0
    watched_page.assert_clean()


def test_lazy_plane_and_wedge_clip_the_gpu_output(
    watched_page, served, lazy_template, tmp_path
):
    page = open_lazy(watched_page.page, served, lazy_template, tmp_path)
    assert len(covered_cells(coverage(page))) > 100
    page.click("#clipPresetLongitudinal")
    assert_left_half_empty_right_covered(coverage(page))
    page.click("#clipReset")
    apply_state(page, phi={"enabled": True, "startDeg": 150, "widthDeg": 60})
    result = coverage(page)
    for r, c in covered_cells(result):
        wrapped = screen_angle_deg(result, r, c) % 360
        assert not (150 + 6 < wrapped < 210 - 6)
    watched_page.assert_clean()


# --- Both viewers keep the same region for identical settings --------------------


def test_both_viewers_keep_the_same_region(
    watched_page, standalone_html, served, lazy_template, tmp_path, browser
):
    state = {
        "phi": {"enabled": True, "startDeg": 30, "widthDeg": 100, "mode": "remove"},
        "planes": [plane("x-max", 120), plane("y-min", -80)],
    }
    page = open_standalone(watched_page.page, standalone_html)
    apply_state(page, **state)
    standalone = coverage(page)

    context = browser.new_context()
    try:
        other = context.new_page()
        lazy_page = open_lazy(other, served, lazy_template, tmp_path)
        apply_state(lazy_page, **state)
        lazy = coverage(lazy_page)
    finally:
        context.close()

    assert (standalone["cols"], standalone["rows"]) == (lazy["cols"], lazy["rows"])
    a = {tuple(cell) for cell in covered_cells(standalone)}
    b = {tuple(cell) for cell in covered_cells(lazy)}
    assert a and b
    iou = len(a & b) / len(a | b)
    assert iou > 0.97, f"kept regions differ (IoU {iou:.3f})"
    watched_page.assert_clean()


def test_both_templates_embed_the_identical_shared_clipping_code(
    standalone_html, lazy_template
):
    shared = read_asset("clipping.js")
    assert shared in standalone_html.read_text(encoding="utf-8")
    assert shared in (lazy_template / "lazy.html").read_text(encoding="utf-8")


# --- Panel behaviour -----------------------------------------------------------


def test_slider_and_number_entry_stay_synchronised(watched_page, standalone_html):
    # The slider snaps to its step (~0.26 mm here), so allow one step.
    page = open_standalone(watched_page.page, standalone_html)
    set_range(page, "#clipPlane-x-min-slider", 12)
    assert float(page.input_value("#clipPlane-x-min-number")) == pytest.approx(
        12, abs=0.5
    )
    page.fill("#clipPlane-x-min-number", "-30.5")
    assert float(page.input_value("#clipPlane-x-min-slider")) == pytest.approx(
        -30.5, abs=0.2
    )
    assert page.evaluate("getClipState().planes[0].value") == -30.5
    # A typed value beyond the slider's range is kept exactly, not clamped.
    page.fill("#clipPlane-x-min-number", "99999")
    assert page.evaluate("getClipState().planes[0].value") == 99999
    set_range(page, "#clipPhiStart", 100)
    assert page.input_value("#clipPhiStartNumber") == "100"
    page.fill("#clipPhiWidthNumber", "270")
    assert page.input_value("#clipPhiWidth") == "270"
    watched_page.assert_clean()


def test_presets_reset_and_the_cut_is_stated_open(watched_page, standalone_html):
    page = open_standalone(watched_page.page, standalone_html)
    enabled = "getClipState().planes.filter(p => p.enabled).map(p => p.id)"
    page.click("#clipPresetTransverse")
    assert page.evaluate(enabled) == ["z-min"]
    page.click("#clipPresetSlab")
    assert page.evaluate(enabled) == ["z-min", "z-max"]
    page.check("#clipPhiEnabled")
    page.click("#clipReset")
    assert page.evaluate(enabled) == []
    assert page.evaluate("getClipState().phi.enabled") is False
    note = page.inner_text("#clipNote")
    assert "not capped" in note and "not a quantitative solid section" in note
    assert "mm" in page.inner_text("#clipPanel")
    watched_page.assert_clean()


def test_lazy_shows_the_same_controls_and_statement(
    watched_page, served, lazy_template, tmp_path
):
    page = open_lazy(watched_page.page, served, lazy_template, tmp_path)
    for selector in (
        "#clipPhiMode",
        "#clipPhiStartNumber",
        "#clipPlane-z-max-number",
        "#clipReset",
    ):
        assert page.query_selector(selector) is not None
    assert "not capped" in page.inner_text("#clipNote")
    watched_page.assert_clean()
