"""Order-independent transparency (weighted blended OIT) in both viewers: nested
translucent tubes, thin disks and overlapping boxes look the same whatever order
they were placed in, and the old order-dependent drawing is still available."""

import pytest

from gdml_inspector import cli

from .browser_helpers import VIEWPORT, next_frames
from .lazy_scene import QUALITY
from .png import pixel, read_png

HEADER = """<?xml version="1.0"?>
<gdml><define/><materials/>
<solids>
<box name="world_box" x="1000" y="1000" z="1000" lunit="mm"/>
<tube name="t30" rmin="26" rmax="30" z="60" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>
<tube name="t20" rmin="16" rmax="20" z="60" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>
<tube name="t10" rmin="0" rmax="10" z="60" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>
<tube name="disk" rmin="0" rmax="34" z="0.6" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>
<box name="slab_a" x="30" y="14" z="14" lunit="mm"/>
<box name="slab_b" x="14" y="30" z="14" lunit="mm"/>
</solids>
<structure>
<volume name="ecal1_outer"><materialref ref="Material_ECAL"/><solidref ref="t30"/></volume>
<volume name="ecal1_middle"><materialref ref="Material_HCAL"/><solidref ref="t20"/></volume>
<volume name="ecal1_inner"><materialref ref="G4_Si"/><solidref ref="t10"/></volume>
<volume name="ecal1_disk"><materialref ref="G4_Fe"/><solidref ref="disk"/></volume>
<volume name="ecal1_slab_a"><materialref ref="Material_ECAL"/><solidref ref="slab_a"/></volume>
<volume name="ecal1_slab_b"><materialref ref="Material_HCAL"/><solidref ref="slab_b"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
"""
PLACEMENTS = {
    "outer": '<physvol name="outer"><volumeref ref="ecal1_outer"/></physvol>',
    "middle": '<physvol name="middle"><volumeref ref="ecal1_middle"/></physvol>',
    "inner": '<physvol name="inner"><volumeref ref="ecal1_inner"/></physvol>',
    "disk": '<physvol name="disk"><volumeref ref="ecal1_disk"/><position z="12" unit="mm"/></physvol>',
    "slab_a": '<physvol name="slab_a"><volumeref ref="ecal1_slab_a"/><position x="40" z="-5" unit="mm"/></physvol>',
    "slab_b": '<physvol name="slab_b"><volumeref ref="ecal1_slab_b"/><position x="40" y="4" z="2" unit="mm"/></physvol>',
}
ORDER = list(PLACEMENTS)


def scene_text(order):
    body = "\n".join(PLACEMENTS[name] for name in order)
    return (
        HEADER
        + body
        + """
</volume>
</structure>
<setup name="Default" version="1.0"><world ref="world"/></setup></gdml>"""
    )


@pytest.fixture(scope="module")
def exports(tmp_path_factory):
    """The same scene placed in two opposite orders, in both viewers."""
    root = tmp_path_factory.mktemp("oit")
    result = {}
    for label, order in (("forward", ORDER), ("reverse", ORDER[::-1])):
        gdml = root / f"{label}.gdml"
        gdml.write_text(scene_text(order), encoding="utf-8")
        standalone = root / f"{label}.html"
        assert (
            cli.main([str(gdml), *QUALITY, "--redact-paths", "--out", str(standalone)])
            == 0
        )
        for mode in ("off", "always"):
            directory = root / f"{label}-{mode}"
            directory.mkdir()
            assert (
                cli.main(
                    [
                        "--lazy",
                        str(gdml),
                        *QUALITY,
                        "--redact-paths",
                        "--instancing",
                        mode,
                        "--out",
                        str(directory / "v.html"),
                    ]
                )
                == 0
            )
            result[(label, f"lazy-{mode}")] = directory
        result[(label, "standalone")] = standalone
    return result


def open_view(watched_page, served, kind, source):
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    if kind == "standalone":
        page.goto(source.resolve().as_uri())
        page.wait_for_selector("#hierarchyTree .tree-row")
    else:
        page.goto(f"{served(source)}/v.html")
        page.wait_for_selector("input[data-layer-key]")
        page.click("#loadAll")
        page.wait_for_function("Object.values(states).every(s => s.loaded)")
    page.uncheck("#coordinateAxes")
    page.evaluate(
        "viewCenter = [20, 0, 0]; dist = 150; panX = panY = 0; yaw = -0.5; pitch = 0.4; cameraUp = [0, 1, 0]; if (typeof requestRender === 'function') requestRender()"
    )
    next_frames(page)
    return page


def snapshot(page, tmp_path, name):
    page.fill("#shotWidth", "400")
    page.fill("#shotHeight", "300")
    page.select_option("#shotBackground", "white")
    for box in ("#shotScale", "#shotAxes", "#shotCaption", "#shotLegend"):
        page.uncheck(box)
    with page.expect_download() as download:
        page.click("#shotSave")
    path = tmp_path / f"{name}.png"
    download.value.save_as(path)
    width, height, channels, rows = read_png(path.read_bytes())
    return [
        pixel(rows, channels, x, y)[:3] for y in range(height) for x in range(width)
    ]


def differences(a, b):
    return [
        max(abs(p - q) for p, q in zip(u, v, strict=True))
        for u, v in zip(a, b, strict=True)
    ]


KINDS = ["standalone", "lazy-off", "lazy-always"]


@pytest.mark.parametrize("kind", KINDS)
def test_the_picture_does_not_depend_on_the_order_surfaces_were_placed_in(
    watched_page, served, exports, tmp_path, kind
):
    page = open_view(watched_page, served, kind, exports[("forward", kind)])
    assert page.evaluate("oit.supported")
    forward = snapshot(page, tmp_path, "forward")
    assert page.evaluate("lastFrameUsedOit") is True
    page = open_view(watched_page, served, kind, exports[("reverse", kind)])
    reverse = snapshot(page, tmp_path, "reverse")
    drawn = [p for p in forward if p != (255, 255, 255)]
    assert len(drawn) > 2000  # the nested tubes really are in the picture
    # Exactly the same picture, up to the rounding of half-float sums.
    assert max(differences(forward, reverse)) <= 2
    assert watched_page.problems == []


@pytest.mark.parametrize("kind", KINDS)
def test_the_old_drawing_depends_on_the_order_which_is_why_oit_exists(
    watched_page, served, exports, tmp_path, kind
):
    pictures = []
    for label in ("forward", "reverse"):
        page = open_view(watched_page, served, kind, exports[(label, kind)])
        page.uncheck("#oit")
        next_frames(page)
        pictures.append(snapshot(page, tmp_path, f"legacy-{label}"))
        assert page.evaluate("lastFrameUsedOit") is False
    far_apart = sum(1 for d in differences(*pictures) if d > 12)
    assert far_apart > 200, far_apart


@pytest.mark.parametrize("kind", KINDS)
def test_an_end_on_view_through_all_the_layers_draws_cleanly(
    watched_page, served, exports, tmp_path, kind
):
    """Seen along the axis every layer overlaps at the centre; the pixel there
    is a blend of all of them, not background, and nothing logs a GL error."""
    page = open_view(watched_page, served, kind, exports[("forward", kind)])
    page.evaluate(
        "yaw = 0; pitch = 0; dist = 150; viewCenter = [0, 0, 0]; if (typeof requestRender === 'function') requestRender()"
    )
    next_frames(page)
    both = snapshot(page, tmp_path, "both")
    centre = both[150 * 400 + 200]
    assert centre != (255, 255, 255)
    assert watched_page.problems == []


def test_unsupported_browsers_fall_back_to_the_old_drawing_and_say_so(
    watched_page, served, exports, tmp_path
):
    page = watched_page.page
    page.add_init_script(
        """(() => {
          const blocked = ['OES_texture_half_float', 'EXT_color_buffer_half_float', 'OES_texture_float', 'WEBGL_color_buffer_float'];
          const original = WebGLRenderingContext.prototype.getExtension;
          WebGLRenderingContext.prototype.getExtension = function (name) { return blocked.includes(name) ? null : original.call(this, name); };
        })()"""
    )
    page = open_view(watched_page, served, "lazy-off", exports[("forward", "lazy-off")])
    assert page.evaluate("oit.supported") is False
    assert page.is_disabled("#oit") and not page.is_checked("#oit")
    assert "half-float" in page.inner_text("#oitNote")
    picture = snapshot(page, tmp_path, "fallback")
    assert any(p != (255, 255, 255) for p in picture)  # still draws
    assert page.evaluate("lastFrameUsedOit") is False
    assert watched_page.problems == []
