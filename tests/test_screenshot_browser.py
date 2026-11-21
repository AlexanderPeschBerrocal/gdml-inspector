"""Screenshot export in both viewers: explicit resolution, no page panels,
exact framing, a faithful scale bar, real transparency, and the shown state."""

import math

import pytest

from .browser_helpers import next_frames
from .png import pixel, read_png
from .test_camera_browser import Viewer


@pytest.fixture(params=["standalone", "lazy"])
def viewer(request, watched_page, served, lazy_scene_dir, lazy_scene_standalone):
    v = Viewer(
        request.param, watched_page, served, lazy_scene_dir, lazy_scene_standalone
    )
    v.page.click("#viewXYPos")
    next_frames(v.page)
    return v


def has_ink(rows, channels, x0, y0, x1, y1):
    """Whether any pixel in the box is not plain white."""
    return any(
        pixel(rows, channels, x, y)[:3] != (255, 255, 255)
        for y in range(y0, y1)
        for x in range(x0, x1)
    )


def configure(
    page,
    *,
    width=640,
    height=360,
    background="white",
    scale=False,
    axes=False,
    caption=False,
    legend=False,
):
    page.fill("#shotWidth", str(width))
    page.fill("#shotHeight", str(height))
    page.select_option("#shotBackground", background)
    page.set_checked("#shotScale", scale)
    page.set_checked("#shotAxes", axes)
    page.set_checked("#shotCaption", caption)
    page.set_checked("#shotLegend", legend)


def take(page, tmp_path, **options):
    """Click Save PNG and return (decoded PNG, downloaded file name, the page's record)."""
    configure(page, **options)
    with page.expect_download() as download:
        page.click("#shotSave")
    target = tmp_path / download.value.suggested_filename
    download.value.save_as(target)
    width, height, channels, rows = read_png(target.read_bytes())
    return (width, height, channels, rows), download.value.suggested_filename


def record(page):
    return page.evaluate(
        "({width: lastScreenshot.width, height: lastScreenshot.height, lines: lastScreenshot.lines, legend: lastScreenshot.legend, scaleBar: lastScreenshot.scaleBar})"
    )


def output_pixel(page, point, width, height):
    """Where `point` lands in a width x height image of the current view (the
    camera's own maths at the output's aspect ratio, not the window's)."""
    x, y = page.evaluate(
        """([point, width, height]) => {
          const m = cameraMatrix(cameraState(), width / height, 1, 1e6);
          const [wx, wy, wz] = point;
          const tx = m[0]*wx+m[4]*wy+m[8]*wz+m[12], ty = m[1]*wx+m[5]*wy+m[9]*wz+m[13], tw = m[3]*wx+m[7]*wy+m[11]*wz+m[15];
          return [(tx/tw*0.5+0.5)*width, (-ty/tw*0.5+0.5)*height];
        }""",
        [point, width, height],
    )
    return round(x), round(y)


def on_screen_state(page):
    return page.evaluate(
        "({w: canvas.width, h: canvas.height, cw: canvas.clientWidth, ch: canvas.clientHeight, dist, projection})"
    )


@pytest.mark.parametrize("size", [(640, 360), (300, 900), (1280, 720)])
def test_the_png_has_the_requested_pixel_size_whatever_the_window(
    viewer, tmp_path, size
):
    before = on_screen_state(viewer.page)
    (width, height, channels, _rows), name = take(
        viewer.page, tmp_path, width=size[0], height=size[1]
    )
    assert (width, height) == size and channels in (3, 4)
    assert name.endswith(f"-{size[0]}x{size[1]}.png")
    # The visible canvas was only borrowed for the render.
    assert on_screen_state(viewer.page) == before
    assert viewer.watched.problems == []


def test_panels_and_annotations_are_absent_unless_asked_for(viewer, tmp_path):
    plain, _ = take(viewer.page, tmp_path, background="white")
    width, height, channels, rows = plain
    # Nothing but the scene: every corner is the plain background.
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        assert pixel(rows, channels, x, y)[:3] == (255, 255, 255)
    annotated, _ = take(
        viewer.page, tmp_path, background="white", caption=True, scale=True, legend=True
    )
    _w, _h, c2, rows2 = annotated
    # Each annotation puts ink in its own corner (the geometry sits in the middle).
    assert has_ink(rows2, c2, 0, 0, 160, 60)  # caption, top left
    assert has_ink(rows2, c2, width - 160, height - 60, width, height)  # scale bar
    assert has_ink(rows2, c2, 0, height - 60, 100, height)  # legend, bottom left
    assert not has_ink(rows, channels, 0, 0, 160, 60)  # and none without them


def live_png(page):
    """The on-screen canvas as PNG bytes, read in the same task that draws it."""
    import base64

    url = page.evaluate(
        "(() => { (typeof drawFrame === 'function' ? drawFrame : render)(); return canvas.toDataURL('image/png'); })()"
    )
    return base64.b64decode(url.split(",", 1)[1])


def test_the_framing_matches_the_on_screen_view(viewer, tmp_path):
    state = on_screen_state(viewer.page)
    # The canvas's own size and theme background, nothing added: the screenshot is
    # the view, pixel for pixel (a different size just changes the output's aspect).
    shot, _ = take(
        viewer.page, tmp_path, width=state["w"], height=state["h"], background="theme"
    )
    width, height, channels, rows = shot
    lw, lh, lc, live = read_png(live_png(viewer.page))
    assert (width, height) == (lw, lh)
    differing = sum(
        1
        for y in range(height)
        for x in range(width)
        if pixel(rows, channels, x, y)[:3] != pixel(live, lc, x, y)[:3]
    )
    assert differing == 0, differing


def test_the_scale_bar_in_the_image_matches_the_geometry(viewer, tmp_path):
    (width, height, channels, rows), _ = take(
        viewer.page, tmp_path, width=1280, height=720, background="white", scale=True
    )
    bar = record(viewer.page)["scaleBar"]
    dist = viewer.page.evaluate("dist")
    mm_per_px = 2 * dist * math.tan(math.radians(22.5)) / height
    assert bar["px"] == pytest.approx(bar["mm"] / mm_per_px, abs=0.5)
    # The bar itself is a dark horizontal run; measure it in the bottom-right panel.
    longest = 0
    for y in range(height - 80, height):
        run = best = 0
        for x in range(width - 400, width):
            dark = sum(pixel(rows, channels, x, y)[:3]) < 200
            run = run + 1 if dark else 0
            best = max(best, run)
        longest = max(longest, best)
    assert longest == pytest.approx(bar["px"], abs=3)
    assert "(at the target)" in bar["label"]
    viewer.projection("orthographic")
    take(viewer.page, tmp_path, width=1280, height=720, background="white", scale=True)
    assert "(at the target)" not in record(viewer.page)["scaleBar"]["label"]


def test_transparent_background_has_real_alpha_and_exact_colours(viewer, tmp_path):
    viewer.page.evaluate(
        "document.getElementById('opacity').value = 50; document.getElementById('opacity').dispatchEvent(new Event('input')); if (typeof requestRender === 'function') requestRender()"
    )
    next_frames(viewer.page)
    (width, height, channels, rows), _ = take(
        viewer.page, tmp_path, background="transparent"
    )
    assert channels == 4
    assert pixel(rows, channels, 1, 1)[3] == 0  # empty space is fully transparent
    (_w, _h, c2, white_rows), _ = take(viewer.page, tmp_path, background="white")
    seen = 0
    for path in ("world/ecal_a", "world/hcal_b", "world/front"):
        px, py = output_pixel(viewer.page, viewer.centre(path), width, height)
        r, g, b, a = pixel(rows, channels, px, py)
        if a == 0:
            continue
        seen += 1
        assert 40 < a < 255  # the 50% opacity survived
        # The recovered colour, laid back over white, is the render on white.
        over = tuple(round(v * a / 255 + 255 * (1 - a / 255)) for v in (r, g, b))
        assert over == pytest.approx(pixel(white_rows, c2, px, py)[:3], abs=3)
    assert seen >= 2


def test_the_shown_state_is_what_is_captured(viewer, tmp_path):
    def shows(path, rows, channels, width, height):
        px, py = output_pixel(viewer.page, viewer.centre(path), width, height)
        return pixel(rows, channels, px, py)[:3] != (255, 255, 255)

    (w, h, c, rows), _ = take(viewer.page, tmp_path, background="white")
    assert shows("world/hcal_b", rows, c, w, h)
    viewer.hide_all_but(["world/ecal_a"])
    (w, h, c, rows), _ = take(viewer.page, tmp_path, background="white")
    assert not shows("world/hcal_b", rows, c, w, h)
    assert shows("world/ecal_a", rows, c, w, h)
    # Clipping applies as well.
    viewer.page.evaluate("setClipState({planes:[{id:'x-min',enabled:true,value:0}]})")
    next_frames(viewer.page)
    (w, h, c, rows), _ = take(viewer.page, tmp_path, background="white")
    assert not shows("world/ecal_a", rows, c, w, h)  # it sits at x < 0


@pytest.fixture(scope="module")
def two_material_sources(tmp_path_factory):
    """The filters test's scene: five placements over four materials."""
    from gdml_inspector import cli

    from .lazy_scene import QUALITY
    from .test_filters_browser import GDML

    directory = tmp_path_factory.mktemp("shot-materials")
    gdml = directory / "scene.gdml"
    gdml.write_text(GDML, encoding="utf-8")
    lazy = directory / "lazy"
    lazy.mkdir()
    assert (
        cli.main(
            [
                "--lazy",
                str(gdml),
                *QUALITY,
                "--redact-paths",
                "--out",
                str(lazy / "v.html"),
            ]
        )
        == 0
    )
    standalone = directory / "s.html"
    assert (
        cli.main([str(gdml), *QUALITY, "--redact-paths", "--out", str(standalone)]) == 0
    )
    return lazy, standalone


@pytest.fixture(params=["standalone", "lazy"])
def multi(request, watched_page, served, two_material_sources):
    lazy, standalone = two_material_sources
    v = Viewer(request.param, watched_page, served, lazy, standalone)
    v.page.click("#viewXYPos")
    next_frames(v.page)
    return v


def test_caption_and_legend_describe_the_export_and_the_shown_materials(
    multi, tmp_path
):
    take(multi.page, tmp_path, caption=True, legend=True)
    info = record(multi.page)
    text = "\n".join(info["lines"])
    assert "mm" in text and "perspective" in text and "sha256" in text
    assert "gdml-inspector" in text
    assert (
        str(tmp_path) not in text and "/" not in info["lines"][0]
    )  # a name, not a path
    assert info["legend"] == ["G4_Fe", "G4_Fe2", "G4_Fe_Alloy", "Σilicon"]
    multi.hide_all_but(["world/hcal_b"])  # only G4_Fe2 remains
    take(multi.page, tmp_path, caption=True, legend=True)
    assert record(multi.page)["legend"] == ["G4_Fe2"]
    multi.projection("orthographic")
    take(multi.page, tmp_path, caption=True)
    assert "orthographic" in " ".join(record(multi.page)["lines"])


@pytest.mark.parametrize("size", [(10, 400), (400, 10), (99999, 400)])
def test_unusable_sizes_are_refused_without_disturbing_the_view(viewer, tmp_path, size):
    before = on_screen_state(viewer.page)
    configure(viewer.page, width=size[0], height=size[1])
    viewer.page.click("#shotSave")
    viewer.page.wait_for_function(
        "document.getElementById('shotStatus').classList.contains('error')"
    )
    assert "between 16 and" in viewer.page.inner_text("#shotStatus")
    assert on_screen_state(viewer.page) == before
    viewer.click_object("world/hcal_b")
    assert viewer.selected() == "world/hcal_b"  # picking still works


def test_the_view_works_normally_after_a_screenshot(viewer, tmp_path):
    take(
        viewer.page,
        tmp_path,
        width=1600,
        height=900,
        background="transparent",
        axes=True,
    )
    viewer.click_object("world/ecal_a")
    assert viewer.selected() == "world/ecal_a"
    assert (
        viewer.page.is_checked("#coordinateAxes") is False
    )  # the checkbox is back as it was
    viewer.page.evaluate(
        "dist *= 0.8; if (typeof requestRender === 'function') requestRender()"
    )
    next_frames(viewer.page)
    assert viewer.watched.problems == []


def test_the_axes_option_draws_the_axes_into_the_image(viewer, tmp_path):
    half = viewer.page.evaluate("axisLength / 2")
    (width, height, channels, rows), _ = take(
        viewer.page, tmp_path, width=1280, height=720, axes=False
    )
    px, py = output_pixel(viewer.page, [half, 0, 0], width, height)
    assert pixel(rows, channels, px, py)[:3] == (255, 255, 255)
    (_w, _h, c2, with_axes), _ = take(
        viewer.page, tmp_path, width=1280, height=720, axes=True
    )
    # The axis is a one-pixel GL line: look at the pixels around its computed position.
    around = [
        pixel(with_axes, c2, px + dx, py + dy)[:3]
        for dx in range(-2, 3)
        for dy in range(-2, 3)
    ]
    assert any(r > 200 and g < 150 and b < 150 for r, g, b in around)  # the red x axis
    assert all(
        pixel(rows, channels, px + dx, py + dy)[:3] == (255, 255, 255)
        for dx in range(-2, 3)
        for dy in range(-2, 3)
    )
    # The page's own checkbox is untouched by the option.
    assert viewer.page.is_checked("#coordinateAxes") is False
