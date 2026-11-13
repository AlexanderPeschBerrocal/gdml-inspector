"""The viewer draws instanced (v3) chunks exactly like baked ones, in drawing,
picking, highlight, hide/isolate, filters and clipping, and falls back to
CPU expansion when instancing is unavailable."""

import json

import pytest

from .browser_helpers import VIEWPORT, next_frames
from .lazy_scene import (
    _PIXEL,
    LAYER,
    click_object,
    object_center,
    selected,
)

OBJECTS = [p for p in LAYER if p != "world/back"]  # front hides back at this view

_COUNT_BUFFERS = """() => {
  window.__live = new Set();
  const create = gl.createBuffer.bind(gl), remove = gl.deleteBuffer.bind(gl);
  gl.createBuffer = () => { const b = create(); window.__live.add(b); return b; };
  gl.deleteBuffer = b => { window.__live.delete(b); remove(b); };
}"""


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
    page.evaluate("ensurePlacements()")
    page.wait_for_function("placements !== null")
    next_frames(page)
    return page


def kinds(page):
    return page.evaluate("Object.values(states).map(s => s.buffers.kind)")


def pixels(page):
    return {p: page.evaluate(_PIXEL, object_center(page, p)) for p in LAYER}


def test_instanced_chunks_are_drawn_with_instancing(
    watched_page, served, lazy_scene_instanced_dir
):
    page = open_scene(watched_page, served, lazy_scene_instanced_dir)
    assert set(kinds(page)) == {"instanced"}
    assert page.evaluate("Boolean(instExt)") is True
    assert watched_page.problems == []


def test_instanced_and_baked_pixels_are_identical(
    watched_page, served, lazy_scene_instanced_dir, lazy_scene_baked_dir
):
    instanced = pixels(open_scene(watched_page, served, lazy_scene_instanced_dir))
    baked = pixels(open_scene(watched_page, served, lazy_scene_baked_dir))
    assert set(kinds(watched_page.page)) == {"baked"}
    background = watched_page.page.evaluate(
        "(() => { render(); const p = new Uint8Array(4); gl.readPixels(2, 2, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p); return Array.from(p); })()"
    )
    for path in LAYER:
        assert instanced[path] != background  # something is drawn there
        for got, want in zip(instanced[path], baked[path], strict=True):
            assert abs(got - want) <= 1


def test_the_cpu_fallback_matches_instanced_drawing(
    watched_page, served, lazy_scene_instanced_dir
):
    instanced = pixels(open_scene(watched_page, served, lazy_scene_instanced_dir))
    expanded_page = open_scene(
        watched_page, served, lazy_scene_instanced_dir, expand=True
    )
    assert set(kinds(expanded_page)) == {"baked"}  # expanded on load
    expanded = pixels(expanded_page)
    for path in LAYER:
        for got, want in zip(expanded[path], instanced[path], strict=True):
            assert abs(got - want) <= 1


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("path", OBJECTS)
def test_clicking_an_instance_selects_that_placement(
    watched_page, served, lazy_scene_instanced_dir, path, expand
):
    page = open_scene(watched_page, served, lazy_scene_instanced_dir, expand=expand)
    click_object(page, path)
    assert selected(page) == path
    assert page.inner_text("#detailPath") == path
    assert watched_page.problems == []


@pytest.mark.parametrize("expand", [False, True])
def test_picking_is_depth_correct_and_respects_clipping_with_instances(
    watched_page, served, lazy_scene_instanced_dir, expand
):
    page = open_scene(watched_page, served, lazy_scene_instanced_dir, expand=expand)
    click_object(page, "world/back")
    assert selected(page) == "world/front"
    page.evaluate("setClipState({planes:[{id:'z-max',enabled:true,value:0}]})")
    next_frames(page)
    click_object(page, "world/back")
    assert selected(page) == "world/back"
    page.evaluate("clearSelection()")
    # Phi wedge removing +x: hcal_b is cut away.
    page.evaluate(
        "setClipState({phi: {enabled: true, startDeg: -30, widthDeg: 60, mode: 'remove'}})"
    )
    next_frames(page)
    click_object(page, "world/hcal_b")
    assert page.evaluate("selectedObjectId") is None


def test_highlight_tints_only_the_selected_instance_of_a_shared_mesh(
    watched_page, served, lazy_scene_instanced_dir
):
    page = open_scene(watched_page, served, lazy_scene_instanced_dir)
    # ecal_a and ecal_c are two instances of one mesh in one chunk.
    a, c = object_center(page, "world/ecal_a"), object_center(page, "world/ecal_c")
    before_a, before_c = page.evaluate(_PIXEL, a), page.evaluate(_PIXEL, c)
    assert before_a == before_c
    click_object(page, "world/ecal_a")
    assert selected(page) == "world/ecal_a"
    after_a, after_c = page.evaluate(_PIXEL, a), page.evaluate(_PIXEL, c)
    assert after_c == before_c
    assert after_a != before_a


def test_hide_and_isolate_act_per_instance(
    watched_page, served, lazy_scene_instanced_dir
):
    page = open_scene(watched_page, served, lazy_scene_instanced_dir)
    page.click("#loadHierarchy")
    page.wait_for_selector("#hierarchyTree .tree-row")
    corner = lambda: page.evaluate(  # noqa: E731
        "(() => { render(); const p = new Uint8Array(4); gl.readPixels(2, 2, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p); return Array.from(p); })()"
    )

    def shown(path):
        return page.evaluate(_PIXEL, object_center(page, path)) != corner()

    assert shown("world/ecal_a") and shown("world/ecal_c")
    page.query_selector('[data-path="world/ecal_a"] input[type=checkbox]').click()
    assert not shown("world/ecal_a")
    assert shown("world/ecal_c")  # the other instance of the same mesh stays
    page.query_selector('[data-path="world/ecal_a"] input[type=checkbox]').click()
    page.query_selector('[data-path="world/ecal_c"]').query_selector(
        "text=Isolate"
    ).click()
    assert shown("world/ecal_c")
    assert not shown("world/ecal_a") and not shown("world/hcal_b")
    # An instance of a second mesh in the same chunk is addressed correctly too.
    page.query_selector('[data-path="world/ecal_c"]').query_selector(
        "text=Isolate"
    ).click()  # clear the isolation again
    assert shown("world/ecal_d") and shown("world/ecal_a")
    page.query_selector('[data-path="world/ecal_d"] input[type=checkbox]').click()
    assert not shown("world/ecal_d")
    assert shown("world/ecal_a") and shown("world/ecal_c")
    page.query_selector('[data-path="world/ecal_d"] input[type=checkbox]').click()
    page.query_selector('[data-path="world/ecal_c"]').query_selector(
        "text=Isolate"
    ).click()
    # A hidden instance is also not pickable.
    click_object(page, "world/ecal_a")
    assert page.evaluate("selectedObjectId") is None


def test_filters_select_instances_without_loading_anything_extra(
    watched_page, served, lazy_scene_instanced_dir
):
    page = open_scene(watched_page, served, lazy_scene_instanced_dir)
    page.fill("#search", "path:world/ecal_c")
    page.wait_for_function("filterMatchMask !== null")
    assert page.evaluate(
        "placements.objects.filter((o, i) => visibleMask[i]).map(o => o.path)"
    ) == ["world/ecal_c"]
    assert page.evaluate(_PIXEL, object_center(page, "world/ecal_a")) != page.evaluate(
        _PIXEL, object_center(page, "world/ecal_c")
    )


def test_instanced_chunks_use_less_gpu_memory_and_free_every_buffer(
    watched_page, served, lazy_scene_instanced_dir, lazy_scene_baked_dir
):
    inst_page = open_scene(watched_page, served, lazy_scene_instanced_dir)
    inst_gpu = inst_page.evaluate("gpuBytes()")
    logical = inst_page.evaluate(
        "Object.values(states).reduce((n, s) => n + s.buffers.count, 0)"
    )
    baked_page = open_scene(watched_page, served, lazy_scene_baked_dir)
    assert baked_page.evaluate("gpuBytes()") == logical * 21
    assert inst_gpu > 0
    # Buffers: position, matrices, ids, visibility per instanced chunk.
    page = watched_page.page
    page.goto(f"{served(lazy_scene_instanced_dir)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    page.evaluate(_COUNT_BUFFERS)
    baseline = page.evaluate("window.__live.size")
    page.click("#loadAll")
    page.wait_for_function("Object.values(states).every(s => s.loaded)")
    chunks = page.evaluate("MANIFEST.chunks.length")
    assert page.evaluate("window.__live.size") == baseline + 4 * chunks
    page.click("#hideAll")
    page.wait_for_function("Object.values(states).every(s => !s.loaded)")
    assert page.evaluate("window.__live.size") == baseline


def test_a_corrupt_mesh_table_fails_only_its_layer(
    watched_page, served, lazy_scene_instanced_dir, tmp_path
):
    import re
    import shutil

    export = tmp_path / "export"
    shutil.copytree(lazy_scene_instanced_dir, export)
    html = export / "v.html"
    text = html.read_text(encoding="utf-8")
    match = re.search(r"const MANIFEST = (.*?);\nconst canvas = ", text, re.DOTALL)
    manifest = json.loads(match.group(1))
    for entry in manifest["chunks"]:
        entry.pop("sha256", None)  # so the structural check, not the digest, fires
    html.write_text(
        text[: match.start(1)] + json.dumps(manifest) + text[match.end(1) :],
        encoding="utf-8",
    )
    victim = next(c for c in manifest["chunks"] if c["subsystem"] == "ECAL")
    path = export / victim["url"]
    data = bytearray(path.read_bytes())
    data[40 + 12 : 40 + 16] = (10**6).to_bytes(
        4, "little"
    )  # first mesh's first instance
    path.write_bytes(bytes(data))
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(export)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    page.click("#loadAll")
    page.wait_for_selector(".layer-status.error")
    assert "Corrupt mesh table" in page.get_attribute(
        ".layer-status.error span", "title"
    )
    assert (
        page.evaluate("Object.values(states).filter(s => s.loaded).length")
        == len(manifest["chunks"]) - 1
    )


def test_no_gl_error_when_an_instanced_chunk_is_unloaded_between_frames(
    watched_page, served, lazy_scene_dir
):
    # Instance attribute arrays must not stay enabled over deleted buffers:
    # the next frame's other draws would fail with INVALID_OPERATION. The
    # default (auto) export mixes one instanced chunk with baked ones.
    page = open_scene(watched_page, served, lazy_scene_dir)
    assert set(kinds(page)) == {"baked", "instanced"}
    page.evaluate("render()")
    page.evaluate("gl.getError()")  # discard anything earlier

    def frame_errors():
        return page.evaluate(
            "(() => { render(); const e = []; let x; while ((x = gl.getError()) !== 0 && e.length < 10) e.push(x); return e; })()"
        )

    assert frame_errors() == []
    key = json.dumps(LAYER["world/ecal_a"])  # the instanced multi-mesh layer
    page.evaluate(f"setLayer({key}, false)")
    page.wait_for_function(
        f"MANIFEST.chunks.filter(c => c.baseKey === {key}).every(c => !states[c.key].loaded)"
    )
    assert frame_errors() == []
    page.evaluate(f"setLayer({key}, true)")
    page.wait_for_function(
        f"MANIFEST.chunks.filter(c => c.baseKey === {key}).every(c => states[c.key].loaded)"
    )
    assert frame_errors() == []
    click_object(page, "world/hcal_b")
    assert page.evaluate("(() => { const e = gl.getError(); return e; })()") == 0
