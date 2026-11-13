"""Placement details (bounding boxes and transforms) live in per-chunk shards:
the index serves the tree, filters and picking, and a shard is fetched only
when an inspector or a focus needs it."""

import hashlib
import json
import shutil

from .browser_helpers import VIEWPORT, next_frames
from .lazy_scene import click_object, open_lazy, selected


def requests_to(page):
    seen = []
    page.on("request", lambda r: seen.append(r.url.rsplit("/", 1)[-1]))
    return seen


def shards(seen):
    return sorted(u for u in seen if u.startswith("placements.shard-"))


def chunk_index(page, path):
    return page.evaluate(
        "path => placements.objects.find(o => o.path === path).chunkIndex", path
    )


def test_the_tree_and_filters_never_fetch_a_shard(watched_page, served, lazy_scene_dir):
    seen = requests_to(watched_page.page)
    page = open_lazy(watched_page, served, lazy_scene_dir, fetch_placements=False)
    page.click("#loadHierarchy")
    page.wait_for_selector("#hierarchyTree .tree-row")
    page.fill("#search", "ecal")
    page.fill("#hierarchySearch", "hcal")
    page.query_selector('[data-path="world/hcal_b"] input[type=checkbox]').click()
    next_frames(page)
    assert "placements.json" in seen
    assert shards(seen) == [] and "placements.containers.json" not in seen


def test_picking_fetches_only_the_shard_of_the_picked_objects_chunk(
    watched_page, served, lazy_scene_dir
):
    seen = requests_to(watched_page.page)
    page = open_lazy(watched_page, served, lazy_scene_dir, fetch_placements=True)
    # Not click_object(): that helper loads every shard to find the object.
    centre = page.evaluate(
        """async () => {
          await ensureAllDetails();
          const b = placements.objects.find(o => o.path === 'world/hcal_b').bbox;
          return [(b[0]+b[3])/2, (b[1]+b[4])/2, (b[2]+b[5])/2];
        }"""
    )
    page.evaluate(
        "window.__reset = () => { for (const k of [...placements.shards.keys()]) placements.shards.delete(k); placements.containers = null; for (const o of placements.objects) delete o.bbox; for (const n of placements.nodes) { delete n.localMatrix; delete n.globalMatrix; } }; window.__reset()"
    )
    seen.clear()
    from .lazy_scene import _LAZY_PROJECT

    sx, sy = page.evaluate(_LAZY_PROJECT, centre)
    page.mouse.click(sx, sy)
    page.wait_for_function("selectedPath === 'world/hcal_b'")
    page.wait_for_function(
        "document.getElementById('detailBounds').textContent.startsWith('min')"
    )
    index = chunk_index(page, "world/hcal_b")
    assert shards(seen) == [f"placements.shard-{index:04d}.json"]
    assert "placements.containers.json" not in seen
    assert page.inner_text("#detailBounds").startswith("min")
    assert "loading" not in page.inner_text("#detailGlobalMatrix")
    assert watched_page.problems == []


def test_the_inspector_says_loading_until_the_shard_arrives(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir, fetch_placements=True)
    held = []
    page.route("**/placements.shard-*.json", lambda route: held.append(route))
    page.evaluate("selectPath('world/ecal_a')")
    page.wait_for_function("selectedPath === 'world/ecal_a'")
    page.wait_for_function(
        "document.getElementById('detailPath').textContent === 'world/ecal_a'"
    )
    assert page.inner_text("#detailBounds") == "loading…"
    assert page.inner_text("#detailLocalMatrix") == "loading…"
    assert page.inner_text("#detailMaterial") == "G4_Fe"  # from the index, no wait
    while not held:
        next_frames(page)
    for route in held:
        route.continue_()
    page.wait_for_function(
        "document.getElementById('detailBounds').textContent.startsWith('min')"
    )


def test_a_container_row_fetches_the_containers_shard_only(
    watched_page, served, lazy_scene_dir
):
    seen = requests_to(watched_page.page)
    page = open_lazy(watched_page, served, lazy_scene_dir, fetch_placements=False)
    page.click("#loadHierarchy")
    page.wait_for_selector("#hierarchyTree .tree-row")
    page.query_selector('[data-path="world/group"]').click()
    page.wait_for_function("placements.containers === true")
    assert "placements.containers.json" in seen and shards(seen) == []
    assert "(world root)" not in page.inner_text("#detailLocalMatrix")
    assert page.inner_text("#detailLocalMatrix").strip() != "loading…"


def test_focus_fetches_only_the_shards_of_the_focused_subtree(
    watched_page, served, lazy_scene_dir
):
    seen = requests_to(watched_page.page)
    page = open_lazy(watched_page, served, lazy_scene_dir, fetch_placements=False)
    page.click("#loadHierarchy")
    page.wait_for_selector("#hierarchyTree .tree-row")
    index = chunk_index(page, "world/hcal_b")
    page.query_selector('[data-path="world/hcal_b"]').query_selector(
        "text=Focus"
    ).click()
    page.wait_for_function("focusInFlight === 0")
    assert shards(seen) == [f"placements.shard-{index:04d}.json"]
    # The whole scene needs every chunk's boxes.
    page.query_selector('[data-path="world"]').query_selector("text=Focus").click()
    page.wait_for_function("focusInFlight === 0")
    assert len(shards(seen)) == page.evaluate("MANIFEST.chunks.length")


def test_a_corrupt_shard_is_reported_locally_and_retried_on_the_next_try(
    watched_page, served, lazy_scene_dir, tmp_path
):
    export = tmp_path / "export"
    shutil.copytree(lazy_scene_dir, export)
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(export)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    page.evaluate("ensurePlacements()")
    page.wait_for_function("placements !== null")
    index = chunk_index(page, "world/hcal_b")
    shard = next(export.glob(f"*.assets/placements.shard-{index:04d}.json"))
    good = shard.read_bytes()
    shard.write_bytes(good + b" ")
    page.evaluate("selectPath('world/hcal_b')")
    page.wait_for_function(
        "document.getElementById('status').textContent.includes('sha256')"
    )
    assert "placements.shard" in page.inner_text("#status")
    assert page.inner_text("#detailBounds") == "loading…"
    assert page.inner_text("#detailMaterial") == "G4_Fe"
    shard.write_bytes(good)
    page.evaluate("clearSelection(); selectPath('world/hcal_b')")
    page.wait_for_function(
        "document.getElementById('detailBounds').textContent.startsWith('min')"
    )


def test_an_export_with_the_single_file_placements_still_works(
    watched_page, served, lazy_scene_dir, tmp_path
):
    from gdml_inspector import lazy

    from .test_lazy_placements import expand_placements

    export = tmp_path / "legacy"
    shutil.copytree(lazy_scene_dir, export)
    html = export / "v.html"
    manifest = lazy.read_existing_manifest(html)
    document = expand_placements(html, manifest)
    for object_ in document["objects"]:
        object_.pop("chunkIndex")
    entry = manifest["placements"]
    target = next(export.glob("*.assets/placements.json"))
    data = json.dumps(document, separators=(",", ":")).encode()
    target.write_bytes(data)
    manifest["placements"] = {
        "url": entry["url"],
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "nodes": len(document["nodes"]),
        "objects": len(document["objects"]),
    }
    lazy.write_html(manifest, html)
    seen = requests_to(watched_page.page)
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(export)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    page.click("#loadAll")
    page.wait_for_function("Object.values(states).every(s => s.loaded)")
    page.click("#viewXYPos")
    page.evaluate("ensurePlacements()")
    page.wait_for_function("placements !== null")
    click_object(page, "world/hcal_b")
    assert selected(page) == "world/hcal_b"
    assert page.inner_text("#detailBounds").startswith("min")
    assert shards(seen) == [] and watched_page.problems == []
