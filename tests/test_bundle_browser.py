"""Browser verification of what issue #10 promises about shared artifacts:
bundles load after being moved anywhere, corrupt chunks are caught by the
viewer itself, incompatible schemas get clear guidance, and nothing ever
touches the network."""

import shutil
from pathlib import Path
from urllib.parse import quote, urlparse

import pytest

from gdml_inspector import cli, lazy

from .browser_helpers import next_frames

pytestmark = pytest.mark.usefixtures("webgl_renderer")

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"
BUILD = ["--backend", "python", "--quality", "fast"]


def build_lazy(directory, name="viewer.html"):
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "detector.gdml"
    shutil.copy(EXAMPLE, source)
    output = directory / name
    assert cli.main(["--lazy", str(source), *BUILD, "--out", str(output)]) == 0
    return output


def build_standalone(directory):
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "detector.gdml"
    shutil.copy(EXAMPLE, source)
    output = directory / "viewer.html"
    assert cli.main([str(source), *BUILD, "--out", str(output)]) == 0
    return output


def url_for(base, root, path):
    return f"{base}/{quote(path.relative_to(root).as_posix())}"


def load_everything(page):
    page.wait_for_selector("input[data-layer-key]")
    page.click("#loadAll")
    page.wait_for_function(
        "MANIFEST.chunks.every(c => states[c.key].loaded && states[c.key].visible)"
    )


def test_a_bundle_moved_to_an_awkward_path_still_loads(watched_page, served, tmp_path):
    built = build_lazy(tmp_path / "build", name="my viewer ü #1 %.html")
    moved = tmp_path / "moved ünï %dir #2 [x]"
    shutil.move(str(built.parent), str(moved))
    html = moved / built.name
    page = watched_page.page
    page.goto(url_for(served(tmp_path), tmp_path, html))
    load_everything(page)
    assert page.evaluate("MANIFEST.chunks.every(c => c.url.includes('%'))")
    watched_page.assert_clean()


def test_the_viewer_rejects_a_corrupt_chunk_by_its_digest(
    watched_page, served, tmp_path
):
    html = build_lazy(tmp_path / "bundle")
    manifest = lazy.read_existing_manifest(html)
    victim = html.parent / manifest["chunks"][0]["url"]
    data = bytearray(victim.read_bytes())
    data[-1] ^= 0x01  # same size, so only the digest can notice
    victim.write_bytes(bytes(data))
    page = watched_page.page
    page.goto(url_for(served(tmp_path), tmp_path, html))
    page.wait_for_selector("input[data-layer-key]")
    page.check("input[data-layer-key]")
    page.wait_for_function("Object.values(states).some(s => s.error)")
    error = page.evaluate("Object.values(states).find(s => s.error).error")
    assert "sha256" in error and "corrupt" in error
    status = page.inner_text("#status")
    assert "Could not load" in status and "--serve" not in status
    # A rejected chunk never becomes loaded or drawable.
    assert page.evaluate(
        "Object.values(states).filter(s => s.error).every(s => !s.loaded && !s.buffers)"
    )
    watched_page.assert_clean()


@pytest.mark.parametrize("mode", ["lazy", "standalone"])
def test_a_newer_schema_is_refused_with_guidance(watched_page, served, tmp_path, mode):
    html = (
        build_lazy(tmp_path / "b")
        if mode == "lazy"
        else build_standalone(tmp_path / "b")
    )
    document = html.read_text(encoding="utf-8")
    assert document.count('"schemaVersion":2') == 1
    html.write_text(
        document.replace('"schemaVersion":2', '"schemaVersion":99'), encoding="utf-8"
    )
    page = watched_page.page
    page.goto(url_for(served(tmp_path), tmp_path, html))
    page.wait_for_selector("#compat", state="visible")
    text = page.inner_text("#compat")
    assert "newer gdml-inspector" in text and "update gdml-inspector" in text
    assert any("newer gdml-inspector" in p for p in watched_page.problems)
    watched_page.problems.clear()  # the refusal is the expected outcome


def test_an_old_schema_still_displays_with_a_notice(watched_page, served, tmp_path):
    html = build_lazy(tmp_path / "b")
    manifest = lazy.read_existing_manifest(html)
    del manifest["schemaVersion"]
    lazy.write_html(manifest, html)
    page = watched_page.page
    page.goto(url_for(served(tmp_path), tmp_path, html))
    page.wait_for_selector("#compat", state="visible")
    assert "provenance cannot be verified" in page.inner_text("#compat")
    load_everything(page)  # no digests recorded, so none are checked
    watched_page.assert_clean()


@pytest.mark.parametrize("mode", ["lazy", "standalone"])
def test_no_request_ever_leaves_the_local_origin(page, served, tmp_path, mode):
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    if mode == "lazy":
        html = build_lazy(tmp_path / "b")
        page.goto(url_for(served(tmp_path), tmp_path, html))
        load_everything(page)
    else:
        html = build_standalone(tmp_path / "b")
        page.goto(html.resolve().as_uri())
        page.wait_for_selector("#hierarchyTree .tree-row")
    next_frames(page)
    assert requests
    for url in requests:
        parsed = urlparse(url)
        assert (
            parsed.scheme in ("file", "blob", "data") or parsed.hostname == "127.0.0.1"
        ), url
