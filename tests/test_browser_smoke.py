"""End-to-end browser smoke tests for both viewers (issue #18).

Uses the synthetic ``examples/detector.gdml`` only, no CDN assets, and a
loopback server the test owns. Tests wait on observable state (a loaded
chunk, a finished frame, a linked program), never fixed sleeps. Screenshots
are review artifacts, not pixel-equality assertions; see docs/testing.md.
"""

import os
import shutil
from pathlib import Path

import pytest

from gdml_inspector import cli

from .browser_helpers import VIEWPORT, next_frames, set_range

pytestmark = pytest.mark.usefixtures("webgl_renderer")

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"


@pytest.fixture(scope="module")
def standalone_html(tmp_path_factory):
    output = tmp_path_factory.mktemp("standalone") / "viewer.html"
    assert cli.main([str(EXAMPLE), "--backend", "python", "--out", str(output)]) == 0
    return output


@pytest.fixture(scope="module")
def lazy_template(tmp_path_factory):
    directory = tmp_path_factory.mktemp("lazy-template")
    output = directory / "lazy.html"
    args = ["--lazy", str(EXAMPLE), "--backend", "python", "--out", str(output)]
    assert cli.main(args) == 0
    return directory


@pytest.fixture
def lazy_dir(lazy_template, tmp_path):
    """A private copy per test, so a test may corrupt its own bundle."""
    directory = tmp_path / "lazy"
    shutil.copytree(lazy_template, directory)
    return directory


def open_standalone(watched, html):
    watched.page.set_viewport_size(VIEWPORT)
    watched.page.goto(html.resolve().as_uri())
    watched.page.wait_for_selector("#hierarchyTree .tree-row")
    next_frames(watched.page)
    return watched.page


def open_lazy(watched, served, directory):
    watched.page.set_viewport_size(VIEWPORT)
    watched.page.goto(f"{served(directory)}/lazy.html")
    watched.page.wait_for_selector("input[data-layer-key]")
    watched.page.wait_for_function("!renderPending")
    return watched.page


def layer_key(page, index=0):
    return page.evaluate(
        "(i) => [...document.querySelectorAll('input[data-layer-key]')][i].dataset.layerKey",
        index,
    )


def chunk_state(page, key):
    return page.evaluate(
        """(key) => MANIFEST.chunks.filter(c => c.baseKey === key).map(c => ({
          loaded: states[c.key].loaded,
          visible: states[c.key].visible,
          loading: states[c.key].loading,
          error: states[c.key].error,
          buffers: states[c.key].buffers !== null,
        }))""",
        key,
    )


# --- Standalone --------------------------------------------------------------


def test_standalone_shaders_link_and_the_page_is_clean(watched_page, standalone_html):
    page = open_standalone(watched_page, standalone_html)
    assert page.evaluate("gl.getProgramParameter(prog, gl.LINK_STATUS)") is True
    assert page.evaluate("gl.getProgramParameter(idProg, gl.LINK_STATUS)") is True
    assert page.evaluate("gl.getError()") == 0
    watched_page.assert_clean()


def test_standalone_camera_presets(watched_page, standalone_html):
    page = open_standalone(watched_page, standalone_html)
    page.click("#viewXZPos")
    assert page.evaluate("[yaw, pitch]") == [0, pytest.approx(1.5707963, abs=1e-6)]
    page.click("#viewYZNeg")
    assert page.evaluate("[yaw, pitch]") == [pytest.approx(-1.5707963, abs=1e-6), 0]
    page.click("#reset")
    assert page.evaluate("[yaw, pitch]") == [-0.65, 0.45]
    next_frames(page)
    watched_page.assert_clean()


def test_standalone_display_controls(watched_page, standalone_html):
    page = open_standalone(watched_page, standalone_html)
    set_range(page, "#opacity", 30)
    page.check("#wireframe")
    page.wait_for_function("wireBuffers !== null")
    page.uncheck("#coordinateAxes")
    page.wait_for_function(
        "document.getElementById('axisLabelX').style.display === 'none'"
    )
    page.check("#darkMode")
    assert page.evaluate("document.body.classList.contains('dark')") is True
    page.check("#clipPhiEnabled")
    set_range(page, "#clipPhiStart", -90)
    set_range(page, "#clipPhiWidth", 120)
    assert page.input_value("#clipPhiStartNumber") == "-90"
    assert page.input_value("#clipPhiWidthNumber") == "120"
    next_frames(page)
    assert page.evaluate("gl.getError()") == 0
    watched_page.assert_clean()


def test_standalone_material_toggles_change_visibility(watched_page, standalone_html):
    page = open_standalone(watched_page, standalone_html)
    page.click("#noMaterials")
    assert page.evaluate("objectVisible.every(v => v === 0)") is True
    page.click("#allMaterials")
    assert page.evaluate("objectVisible.every(v => v === 1)") is True
    watched_page.assert_clean()


# --- Lazy (served over real HTTP) ---------------------------------------------


def test_lazy_shaders_link_and_the_page_is_clean(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    assert page.evaluate("gl.getProgramParameter(program, gl.LINK_STATUS)") is True
    assert page.evaluate("gl.getError()") == 0
    watched_page.assert_clean()


def test_lazy_selecting_a_layer_fetches_its_chunk(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    key = layer_key(page)
    assert all(not c["loaded"] for c in chunk_state(page, key))
    with page.expect_response(lambda r: r.url.endswith(".bin") and r.ok):
        page.check("input[data-layer-key]")
    page.wait_for_function(
        "(key) => MANIFEST.chunks.filter(c => c.baseKey === key)"
        ".every(c => states[c.key].loaded && states[c.key].visible)",
        arg=key,
    )
    assert page.inner_text("#loadedChunks").startswith("1/")
    watched_page.assert_clean()


def test_lazy_hiding_a_layer_unloads_it_by_default(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    key = layer_key(page)
    page.check("input[data-layer-key]")
    page.wait_for_function(
        "(key) => MANIFEST.chunks.filter(c => c.baseKey === key).every(c => states[c.key].loaded)",
        arg=key,
    )
    page.uncheck("input[data-layer-key]")
    page.wait_for_function(
        "(key) => MANIFEST.chunks.filter(c => c.baseKey === key).every(c => !states[c.key].loaded)",
        arg=key,
    )
    assert all(not c["visible"] and not c["buffers"] for c in chunk_state(page, key))
    assert page.inner_text("#loadedChunks").startswith("0/")
    watched_page.assert_clean()


def test_lazy_keeps_hidden_chunks_loaded_when_release_is_off(
    watched_page, served, lazy_dir
):
    page = open_lazy(watched_page, served, lazy_dir)
    key = layer_key(page)
    page.uncheck("#releaseHidden")
    page.check("input[data-layer-key]")
    page.wait_for_function(
        "(key) => MANIFEST.chunks.filter(c => c.baseKey === key).every(c => states[c.key].loaded)",
        arg=key,
    )
    page.uncheck("input[data-layer-key]")
    page.wait_for_function(
        "(key) => MANIFEST.chunks.filter(c => c.baseKey === key).every(c => !states[c.key].visible)",
        arg=key,
    )
    assert all(c["loaded"] and not c["visible"] for c in chunk_state(page, key))
    # Reselecting reuses the loaded chunk: it becomes visible again.
    page.check("input[data-layer-key]")
    page.wait_for_function(
        "(key) => MANIFEST.chunks.filter(c => c.baseKey === key).every(c => states[c.key].visible)",
        arg=key,
    )
    watched_page.assert_clean()


def test_lazy_unload_and_reload_round_trip(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    key = layer_key(page)
    for _ in range(2):
        page.check("input[data-layer-key]")
        page.wait_for_function(
            "(key) => MANIFEST.chunks.filter(c => c.baseKey === key)"
            ".every(c => states[c.key].loaded && states[c.key].visible)",
            arg=key,
        )
        page.uncheck("input[data-layer-key]")
        page.wait_for_function(
            "(key) => MANIFEST.chunks.filter(c => c.baseKey === key).every(c => !states[c.key].loaded)",
            arg=key,
        )
    watched_page.assert_clean()


def test_lazy_deselecting_while_a_chunk_is_loading_leaves_nothing_visible(
    watched_page, served, lazy_dir
):
    # Hold every fetch in the page, deselect the layer while its chunk is
    # still "loading", then let the fetch finish: the late arrival must not
    # resurrect visible geometry for a layer the user already hid.
    page = watched_page.page
    page.add_init_script(
        """
        window.__held = [];
        const realFetch = window.fetch.bind(window);
        window.fetch = (...args) => new Promise(resolve => {
          window.__held.push(() => resolve(realFetch(...args)));
        });
        window.__release = () => { while (window.__held.length) window.__held.shift()(); };
        """
    )
    open_lazy(watched_page, served, lazy_dir)
    key = layer_key(page)
    page.check("input[data-layer-key]")
    page.wait_for_function("window.__held.length > 0")
    assert any(c["loading"] for c in chunk_state(page, key))
    page.uncheck("input[data-layer-key]")
    page.evaluate("window.__release()")
    page.wait_for_function(
        "(key) => MANIFEST.chunks.filter(c => c.baseKey === key).every(c => !states[c.key].loading)",
        arg=key,
    )
    assert all(not c["visible"] for c in chunk_state(page, key))
    watched_page.assert_clean()


def test_lazy_view_and_cut_controls(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    page.click("#viewXZPos")
    assert page.evaluate("[yaw, pitch]") == [0, pytest.approx(1.5707963, abs=1e-6)]
    page.click("#resetView")
    assert page.evaluate("[yaw, pitch]") == [-0.65, 0.45]
    set_range(page, "#opacity", 40)
    page.check("#clipPhiEnabled")
    page.select_option("#clipPhiMode", "keep")
    set_range(page, "#clipPhiStart", 45)
    set_range(page, "#clipPhiWidth", 200)
    assert page.input_value("#clipPhiStartNumber") == "45"
    assert page.input_value("#clipPhiWidthNumber") == "200"
    page.uncheck("#coordinateAxes")
    page.click("#loadAll")
    page.wait_for_function(
        "MANIFEST.chunks.every(c => states[c.key].loaded && states[c.key].visible)"
    )
    page.click("#hideAll")
    page.wait_for_function("MANIFEST.chunks.every(c => !states[c.key].visible)")
    page.wait_for_function("!renderPending")
    assert page.evaluate("gl.getError()") == 0
    watched_page.assert_clean()


# --- The harness itself must notice breakage -----------------------------------


def test_harness_detects_an_uncaught_script_error(
    watched_page, standalone_html, tmp_path
):
    broken = tmp_path / "broken.html"
    broken.write_text(
        standalone_html.read_text(encoding="utf-8").replace(
            "const canvas = ", "throw new Error('deliberate boom');\nconst canvas = ", 1
        ),
        encoding="utf-8",
    )
    watched_page.page.goto(broken.resolve().as_uri())
    watched_page.page.wait_for_function("document.readyState === 'complete'")
    assert any("deliberate boom" in p for p in watched_page.problems)
    with pytest.raises(AssertionError, match="deliberate boom"):
        watched_page.assert_clean()
    watched_page.problems.clear()  # expected failure; do not write a failure log


def test_harness_detects_a_shader_that_fails_to_compile(
    watched_page, standalone_html, tmp_path
):
    broken = tmp_path / "broken-shader.html"
    text = standalone_html.read_text(encoding="utf-8")
    assert "gl_FragColor = vec4(tinted, v_col.a);" in text
    broken.write_text(
        text.replace(
            "gl_FragColor = vec4(tinted, v_col.a);",
            "gl_FragColor = vec4(tinted v_col.a);",
        ),
        encoding="utf-8",
    )
    watched_page.page.goto(broken.resolve().as_uri())
    watched_page.page.wait_for_function("document.readyState === 'complete'")
    assert watched_page.problems, "a shader compile failure went unnoticed"
    watched_page.problems.clear()


def test_harness_detects_a_missing_lazy_chunk(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    key = layer_key(page)
    chunk = next(c for c in page.evaluate("MANIFEST.chunks") if c["baseKey"] == key)
    (lazy_dir / chunk["url"]).unlink()
    page.check("input[data-layer-key]")
    page.wait_for_function(
        "(key) => MANIFEST.chunks.filter(c => c.baseKey === key).some(c => states[c.key].error)",
        arg=key,
    )
    assert any("HTTP 404" in p for p in watched_page.problems)
    assert any(c["error"] for c in chunk_state(page, key))
    assert "Could not load" in page.inner_text("#status")
    watched_page.problems.clear()


# --- Screenshots: review artifacts, never committed ------------------------------


def screenshot_dir():
    directory = Path(os.environ.get("GDML_SCREENSHOT_DIR", "test-results/screenshots"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def test_standalone_overview_and_cutaway_screenshots(watched_page, standalone_html):
    page = open_standalone(watched_page, standalone_html)
    page.click("#reset")
    next_frames(page)
    overview = screenshot_dir() / "standalone-overview.png"
    page.screenshot(path=overview)
    page.check("#clipPhiEnabled")
    set_range(page, "#clipPhiStart", -45)
    set_range(page, "#clipPhiWidth", 90)
    next_frames(page)
    cutaway = screenshot_dir() / "standalone-cutaway.png"
    page.screenshot(path=cutaway)
    assert overview.stat().st_size > 1000 and cutaway.stat().st_size > 1000
    # The wedge visibly removes geometry, so the two renderings differ.
    assert overview.read_bytes() != cutaway.read_bytes()
    watched_page.assert_clean()


def test_lazy_overview_and_cutaway_screenshots(watched_page, served, lazy_dir):
    page = open_lazy(watched_page, served, lazy_dir)
    page.click("#loadAll")
    page.wait_for_function(
        "MANIFEST.chunks.every(c => states[c.key].loaded && states[c.key].visible)"
    )
    page.click("#resetView")
    page.wait_for_function("!renderPending")
    overview = screenshot_dir() / "lazy-overview.png"
    page.screenshot(path=overview)
    page.check("#clipPhiEnabled")
    set_range(page, "#clipPhiStart", -45)
    set_range(page, "#clipPhiWidth", 90)
    page.wait_for_function("!renderPending")
    cutaway = screenshot_dir() / "lazy-cutaway.png"
    page.screenshot(path=cutaway)
    assert overview.stat().st_size > 1000 and cutaway.stat().st_size > 1000
    assert overview.read_bytes() != cutaway.read_bytes()
    watched_page.assert_clean()
