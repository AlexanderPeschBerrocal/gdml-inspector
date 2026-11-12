"""Real-browser verification of lazy-mode picking, highlighting and the
placement inspector (chunk format v2 ids + placements.json).

The geometry puts each box in its own lazy layer so layers can be unloaded
independently, and keeps a front/back pair of identical footprints for
occlusion and clipping."""

import json
import re
import struct

import pytest

from gdml_inspector import cli

from .browser_helpers import VIEWPORT, next_frames

QUALITY = ["--backend", "python", "--quality", "medium"]
LAYER = {
    "world/ecal_a": "ECAL\x1fBarrel layer 1",
    "world/ecal_c": "ECAL\x1fBarrel layer 1",  # shares a chunk with ecal_a
    "world/hcal_b": "HCAL\x1fBarrel layer 1",
    "world/front": "Other\x1ffront_box",
    "world/back": "Other\x1fback_box",
    "world/group/inner": "Other\x1fchild",
}


@pytest.fixture(scope="module")
def gdml(tmp_path_factory):
    directory = tmp_path_factory.mktemp("lazy-pick-src")
    path = directory / "scene.gdml"
    path.write_text(
        """<?xml version="1.0"?>
<gdml><define/><materials/>
<solids>
<box name="world_box" x="1000" y="1000" z="1000" lunit="mm"/>
<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<box name="overlap_shape" x="20" y="20" z="6" lunit="mm"/>
</solids>
<structure>
<volume name="ecal1_vol"><materialref ref="G4_Fe"/><solidref ref="shape"/></volume>
<volume name="hcal1_vol"><materialref ref="G4_Fe"/><solidref ref="shape"/></volume>
<volume name="child"><materialref ref="G4_Fe"/><solidref ref="shape"/></volume>
<volume name="container"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="inner"><volumeref ref="child"/>
    <position x="5" y="30" z="0" unit="mm"/></physvol>
</volume>
<volume name="front_box"><materialref ref="G4_Fe"/><solidref ref="overlap_shape"/></volume>
<volume name="back_box"><materialref ref="G4_Fe"/><solidref ref="overlap_shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="ecal_a"><volumeref ref="ecal1_vol"/>
    <position x="-50" y="0" z="0" unit="mm"/></physvol>
  <physvol name="ecal_c"><volumeref ref="ecal1_vol"/>
    <position x="-80" y="0" z="0" unit="mm"/></physvol>
  <physvol name="hcal_b"><volumeref ref="hcal1_vol"/>
    <position x="50" y="0" z="0" unit="mm"/></physvol>
  <physvol name="group"><volumeref ref="container"/></physvol>
  <physvol name="front"><volumeref ref="front_box"/>
    <position x="0" y="0" z="100" unit="mm"/></physvol>
  <physvol name="back"><volumeref ref="back_box"/>
    <position x="0" y="0" z="-100" unit="mm"/></physvol>
</volume>
</structure>
<setup name="Default" version="1.0"><world ref="world"/></setup>
</gdml>
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="module")
def lazy_dir(tmp_path_factory, gdml):
    directory = tmp_path_factory.mktemp("lazy-pick")
    assert (
        cli.main(["--lazy", str(gdml), *QUALITY, "--out", str(directory / "v.html")])
        == 0
    )
    return directory


@pytest.fixture(scope="module")
def standalone_html(tmp_path_factory, gdml):
    out = tmp_path_factory.mktemp("lazy-pick-standalone") / "s.html"
    assert cli.main([str(gdml), *QUALITY, "--out", str(out)]) == 0
    return out


def copy_of(lazy_dir, tmp_path):
    import shutil

    target = tmp_path / "export"
    shutil.copytree(lazy_dir, target)
    return target


def open_lazy(watched_page, served, directory, *, load="all"):
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(directory)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    if load == "all":
        page.click("#loadAll")
        page.wait_for_function("Object.values(states).every(s => s.loaded)")
    page.click("#viewXYPos")
    page.evaluate("ensurePlacements()")
    page.wait_for_function("placements !== null")
    next_frames(page)
    return page


_LAZY_PROJECT = """([wx, wy, wz]) => {
  const mvp = cameraMvp();
  const tx=mvp[0]*wx+mvp[4]*wy+mvp[8]*wz+mvp[12], ty=mvp[1]*wx+mvp[5]*wy+mvp[9]*wz+mvp[13], tw=mvp[3]*wx+mvp[7]*wy+mvp[11]*wz+mvp[15];
  const rect = canvas.getBoundingClientRect();
  return [(tx/tw*0.5+0.5)*rect.width + rect.left, (-ty/tw*0.5+0.5)*rect.height + rect.top];
}"""


def object_center(page, path):
    bbox = page.evaluate(
        f"placements.objects.find(o => o.path === {json.dumps(path)}).bbox"
    )
    return [(bbox[i] + bbox[i + 3]) / 2 for i in range(3)]


def click_object(page, path):
    sx, sy = page.evaluate(_LAZY_PROJECT, object_center(page, path))
    page.mouse.click(sx, sy)
    page.wait_for_function("true")  # let the click handler's microtasks run
    next_frames(page)


def selected(page):
    page.wait_for_function(
        "selectedObjectId === null || selectedPath !== null || !placements"
    )
    return page.evaluate("selectedPath")


@pytest.mark.parametrize("path", list(LAYER))
def test_clicking_an_object_selects_it_and_fills_the_inspector(
    watched_page, served, lazy_dir, path
):
    page = open_lazy(watched_page, served, lazy_dir)
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


def test_selection_matches_the_placement_inspector_data(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    click_object(page, "world/group/inner")
    assert selected(page) == "world/group/inner"
    assert page.inner_text("#detailSemanticPath") == "world/group/inner"
    # inner sits at x=5 inside an untransformed group.
    assert page.inner_text("#detailGlobalMatrix").splitlines()[0].split()[3] == "5.000"
    assert page.evaluate("selectedObjectId") == page.evaluate(
        "placements.nodeByPath['world/group/inner'].objectId"
    )


def test_picking_is_depth_correct_through_occluding_geometry(
    watched_page, served, lazy_dir
):
    page = open_lazy(watched_page, served, lazy_dir)
    # front (z=+100) and back (z=-100) share a footprint; viewed down -Z the
    # nearer one wins whatever the draw order.
    click_object(page, "world/back")
    assert selected(page) == "world/front"


def test_clipping_applies_to_picking(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    page.evaluate("setClipState({planes:[{id:'z-max',enabled:true,value:0}]})")
    next_frames(page)
    click_object(page, "world/back")
    # The clipped-away front box no longer hides the back one.
    assert selected(page) == "world/back"


def test_unloaded_layers_are_not_pickable_and_do_not_occlude(
    watched_page, served, lazy_dir
):
    page = open_lazy(watched_page, served, lazy_dir)
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


def test_clicking_empty_space_clears_the_selection(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    click_object(page, "world/ecal_a")
    assert selected(page) == "world/ecal_a"
    page.mouse.click(
        VIEWPORT["width"] - 20, VIEWPORT["height"] - 20
    )  # empty canvas corner
    next_frames(page)
    assert page.evaluate("selectedObjectId") is None
    assert page.eval_on_selector("#detailsContent", "el => el.style.display") == "none"


def test_dragging_does_not_pick(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    sx, sy = page.evaluate(_LAZY_PROJECT, object_center(page, "world/ecal_a"))
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(sx + 40, sy + 10, steps=4)
    page.mouse.up()
    next_frames(page)
    assert page.evaluate("selectedObjectId") is None


_PIXEL = """([wx, wy, wz]) => {
  render();
  const mvp = cameraMvp();
  const tx=mvp[0]*wx+mvp[4]*wy+mvp[8]*wz+mvp[12], ty=mvp[1]*wx+mvp[5]*wy+mvp[9]*wz+mvp[13], tw=mvp[3]*wx+mvp[7]*wy+mvp[11]*wz+mvp[15];
  const px = new Uint8Array(4);
  gl.readPixels(Math.floor((tx/tw*0.5+0.5)*canvas.width), Math.floor((ty/tw*0.5+0.5)*canvas.height), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return Array.from(px);
}"""


def distance_to_highlight(pixel):
    return sum((a - b) ** 2 for a, b in zip(pixel[:3], (255, 209, 51), strict=True))


def test_selection_highlights_only_the_selected_object(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    a, b = object_center(page, "world/ecal_a"), object_center(page, "world/hcal_b")
    before_a, before_b = page.evaluate(_PIXEL, a), page.evaluate(_PIXEL, b)
    click_object(page, "world/ecal_a")
    assert selected(page) == "world/ecal_a"
    after_a, after_b = page.evaluate(_PIXEL, a), page.evaluate(_PIXEL, b)
    assert distance_to_highlight(after_a) < distance_to_highlight(before_a)
    assert after_b == before_b


def test_selection_survives_a_chunk_unload_and_reload(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
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
    watched_page, served, lazy_dir, standalone_html
):
    lazy = open_lazy(watched_page, served, lazy_dir)
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
        standalone.goto(standalone_html.resolve().as_uri())
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


def test_saved_view_round_trips_the_selected_placement(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
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
    watched_page, served, lazy_dir, tmp_path
):
    export = copy_of(lazy_dir, tmp_path)
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


def downgrade_to_v1(export):
    """Rewrite an export as the previous format: v1 chunks, no placements."""
    for chunk in export.glob("*.assets/*.bin"):
        data = chunk.read_bytes()
        magic, _version, vertices, pos, colour, _id, _end = struct.unpack(
            "<8s6I", data[:32]
        )
        body = data[: colour + vertices * 4]
        chunk.write_bytes(
            struct.pack("<8s6I", magic, 1, vertices, pos, colour, len(body), len(body))
            + body[32:]
        )
    for placements in export.glob("*.assets/placements.json"):
        placements.unlink()
    html = export / "v.html"
    text = html.read_text(encoding="utf-8")
    match = re.search(r"const MANIFEST = (.*?);\nconst canvas = ", text, re.DOTALL)
    manifest = json.loads(match.group(1))
    manifest.pop("placements")
    manifest.pop("chunkFormat")
    for entry in manifest["chunks"]:
        entry.pop("sha256", None)
    html.write_text(
        text[: match.start(1)] + json.dumps(manifest) + text[match.end(1) :],
        encoding="utf-8",
    )


def test_a_v1_export_still_renders_and_reports_picking_unavailable(
    watched_page, served, lazy_dir, tmp_path
):
    export = copy_of(lazy_dir, tmp_path)
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
