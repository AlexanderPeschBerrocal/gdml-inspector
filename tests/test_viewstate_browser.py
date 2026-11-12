"""Saved view state (issue #10), end to end in a real browser: a view saved
from one page restores identically in a fresh one, stale entries are
reported by name, invalid files change nothing, and an embedded
--view-state is applied on load."""

import copy
import json
import shutil
from pathlib import Path
from urllib.parse import quote

import pytest

from gdml_inspector import cli

from .browser_helpers import VIEWPORT, next_frames, set_range
from .test_clipping_browser import CELL, COVERAGE_JS

pytestmark = pytest.mark.usefixtures("webgl_renderer")

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"
BUILD = ["--backend", "python", "--quality", "medium"]


@pytest.fixture(scope="module")
def standalone_html(tmp_path_factory):
    out = tmp_path_factory.mktemp("vs-standalone") / "viewer.html"
    assert cli.main([str(EXAMPLE), *BUILD, "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def lazy_template(tmp_path_factory):
    directory = tmp_path_factory.mktemp("vs-lazy")
    assert (
        cli.main(
            ["--lazy", str(EXAMPLE), *BUILD, "--out", str(directory / "lazy.html")]
        )
        == 0
    )
    return directory


def open_standalone(page, html):
    page.set_viewport_size(VIEWPORT)
    page.goto(html.resolve().as_uri())
    page.wait_for_selector("#hierarchyTree .tree-row")
    next_frames(page)
    return page


def open_lazy(page, served, template, tmp_path):
    directory = tmp_path / "lazy"
    shutil.copytree(template, directory)
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(directory)}/lazy.html")
    page.wait_for_selector("input[data-layer-key]")
    page.wait_for_function("!renderPending")
    return page


def customise_standalone(page):
    """Change something in every section the standalone view state covers."""
    page.click("#viewXZPos")
    page.evaluate("yaw = 0.31; pitch = 0.12; dist = dist * 0.7; panX = 3; panY = -2")
    page.uncheck("#coordinateAxes")
    page.check("#wireframe")
    page.check("#darkMode")
    set_range(page, "#opacity", 42)
    page.evaluate(
        "setClipState({phi: {enabled: true, startDeg: 150, widthDeg: 60, mode: 'remove'},"
        " planes: [{id: 'x-min', enabled: true, value: -40}]})"
    )
    material = page.evaluate("materialNames[0]")
    page.eval_on_selector(f'input[data-material="{material}"]', "el => el.click()")
    page.fill("#search", "e")
    page.query_selector('[data-path="world/support"] input[type=checkbox]').click()
    page.evaluate("selectObject(0)")  # select + highlight world/pixel_barrel
    page.fill("#hierarchySearch", "strip")
    next_frames(page)


def coverage(page):
    return page.evaluate(COVERAGE_JS, CELL)


def load_file(page, tmp_path, state, name="view.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps(state) if not isinstance(state, str) else state, encoding="utf-8"
    )
    page.set_input_files("#viewFile", str(path))
    page.wait_for_function(
        "(name) => window.lastViewReport && window.lastViewReport.source === name",
        arg=name,
    )
    return page.evaluate("window.lastViewReport")


def test_standalone_view_restores_identically_in_a_fresh_page(
    watched_page, browser, standalone_html, tmp_path
):
    first = open_standalone(watched_page.page, standalone_html)
    customise_standalone(first)
    saved = first.evaluate("saveViewState()")
    before = coverage(first)
    assert (
        saved["viewer"] == "standalone"
        and saved["hierarchy"]["selectedPath"] == "world/pixel_barrel"
    )
    assert saved["hierarchy"]["hiddenPaths"] == ["world/support"]
    assert saved["savedFor"]["inputSha256"] and saved["savedFor"]["schemaVersion"] == 2

    context = browser.new_context()
    try:
        second = open_standalone(context.new_page(), standalone_html)
        report = load_file(second, tmp_path, saved)
        assert report["unknown"] == [] and report["errors"] == []
        assert {"camera", "display", "clipping", "filters", "hierarchy"} <= set(
            report["applied"]
        )
        assert second.evaluate("saveViewState()") == saved
        assert coverage(second) == before
        assert "Applied view.json" in second.inner_text("#viewMessage")
    finally:
        context.close()
    watched_page.assert_clean()


def test_stale_and_unknown_entries_are_reported_by_name(
    watched_page, standalone_html, tmp_path
):
    page = open_standalone(watched_page.page, standalone_html)
    customise_standalone(page)
    state = page.evaluate("saveViewState()")
    stale = copy.deepcopy(state)
    stale["savedFor"]["inputSha256"] = "0" * 64
    stale["hierarchy"]["hiddenPaths"] += ["world/gone"]
    stale["hierarchy"]["selectedPath"] = "world/missing"
    stale["hierarchy"]["expandedPaths"] += ["world/ghost"]
    stale["hierarchy"]["isolatedPath"] = "world/vanished"
    stale["filters"]["disabledMaterials"] += ["NoSuchMaterial"]
    stale["filters"]["solidType"] = "nonexistent-solid-type"

    fresh = open_standalone(page.context.new_page(), standalone_html)
    report = load_file(fresh, tmp_path, stale)
    unknown = {(u["kind"], u["value"]) for u in report["unknown"]}
    assert unknown == {
        ("hidden placement", "world/gone"),
        ("selected placement", "world/missing"),
        ("expanded placement", "world/ghost"),
        ("isolated placement", "world/vanished"),
        ("material", "NoSuchMaterial"),
        ("solid type", "nonexistent-solid-type"),
    }
    assert any("different geometry" in w for w in report["warnings"])
    message = fresh.inner_text("#viewMessage")
    assert "no longer exist" in message and "world/gone" in message
    # Everything that does exist was still restored.
    assert fresh.evaluate("[yaw, pitch]") == [0.31, 0.12]
    assert fresh.evaluate("hierarchyHidden.size") == 1
    watched_page.assert_clean()


@pytest.mark.parametrize(
    "label, mutate, expected",
    [
        ("not json", "{this is not json", "Not valid JSON"),
        ("not an object", "[1, 2]", "expected a JSON object"),
        (
            "wrong format",
            {"format": "something-else", "version": 1},
            "Not a gdml-inspector view-state",
        ),
        (
            "newer version",
            {"format": "gdml-inspector-view", "version": 99, "viewer": "standalone"},
            "newer gdml-inspector",
        ),
        (
            "bad version",
            {"format": "gdml-inspector-view", "version": "one", "viewer": "standalone"},
            "Invalid view-state version",
        ),
        (
            "bad viewer",
            {"format": "gdml-inspector-view", "version": 1, "viewer": "web"},
            '"viewer" must be',
        ),
        (
            "bad camera",
            {
                "format": "gdml-inspector-view",
                "version": 1,
                "viewer": "standalone",
                "camera": {
                    "yaw": 0,
                    "pitch": 0,
                    "dist": -5,
                    "pan": [0, 0],
                    "up": [0, 1, 0],
                },
            },
            '"camera" needs',
        ),
        (
            "bad projection",
            {
                "format": "gdml-inspector-view",
                "version": 1,
                "viewer": "standalone",
                "projection": {"type": "orthographic", "fovDeg": 0},
            },
            "no other projection",
        ),
        (
            "bad display",
            {
                "format": "gdml-inspector-view",
                "version": 1,
                "viewer": "standalone",
                "display": {"theme": "neon"},
            },
            '"display" has an invalid',
        ),
    ],
)
def test_an_invalid_file_changes_nothing(
    watched_page, standalone_html, tmp_path, label, mutate, expected
):
    page = open_standalone(watched_page.page, standalone_html)
    customise_standalone(page)
    before = page.evaluate("saveViewState()")
    report = load_file(page, tmp_path, mutate, name=f"{label.replace(' ', '-')}.json")
    assert any(expected in e for e in report["errors"]), report
    assert report["applied"] == []
    assert page.evaluate("saveViewState()") == before
    assert "nothing was changed" in page.inner_text("#viewMessage")
    watched_page.assert_clean()


def test_a_partly_invalid_file_is_rejected_whole(
    watched_page, standalone_html, tmp_path
):
    # Valid camera but invalid display: neither may be applied.
    page = open_standalone(watched_page.page, standalone_html)
    before = page.evaluate("saveViewState()")
    state = copy.deepcopy(before)
    state["camera"]["yaw"] = 1.234
    state["display"]["theme"] = "neon"
    report = load_file(page, tmp_path, state)
    assert report["errors"] and report["applied"] == []
    assert page.evaluate("yaw") != 1.234
    assert page.evaluate("saveViewState()") == before


def test_unknown_keys_are_ignored_with_a_warning(
    watched_page, standalone_html, tmp_path
):
    page = open_standalone(watched_page.page, standalone_html)
    state = page.evaluate("saveViewState()")
    state["futureThing"] = {"x": 1}
    report = load_file(page, tmp_path, state)
    assert report["errors"] == []
    assert any('unknown entry "futureThing"' in w for w in report["warnings"])


def test_an_embedded_view_state_is_applied_on_load(
    watched_page, standalone_html, tmp_path
):
    source = open_standalone(watched_page.page, standalone_html)
    customise_standalone(source)
    state = source.evaluate("saveViewState()")
    saved = tmp_path / "wanted.view.json"
    saved.write_text(json.dumps(state), encoding="utf-8")
    out = tmp_path / "embedded.html"
    assert (
        cli.main([str(EXAMPLE), *BUILD, "--view-state", str(saved), "--out", str(out)])
        == 0
    )
    page = source.context.new_page()
    page.on("pageerror", lambda e: watched_page.problems.append(str(e)))
    page.set_viewport_size(VIEWPORT)
    page.goto(out.resolve().as_uri())
    page.wait_for_function("window.lastViewReport")
    assert page.evaluate("saveViewState()") == state
    assert page.evaluate("window.lastViewReport.source") == "embedded view state"
    watched_page.assert_clean()


def test_save_view_downloads_the_sidecar_file(watched_page, standalone_html, tmp_path):
    page = open_standalone(watched_page.page, standalone_html)
    customise_standalone(page)
    with page.expect_download() as info:
        page.click("#viewSave")
    download = info.value
    assert download.suggested_filename == "detector.view.json"
    target = tmp_path / "saved.json"
    download.save_as(target)
    assert json.loads(target.read_text()) == page.evaluate("saveViewState()")


# --- Lazy ----------------------------------------------------------------


def wait_layers_loaded(page, count):
    page.wait_for_function(
        "(n) => Object.values(states).filter(s => s.loaded && s.visible).length === n && !renderPending",
        arg=count,
    )


def test_lazy_view_restores_identically_in_a_fresh_page(
    watched_page, served, lazy_template, tmp_path, browser
):
    first = open_lazy(watched_page.page, served, lazy_template, tmp_path / "a")
    checks = first.query_selector_all("input[data-layer-key]")
    checks[0].check()
    checks[1].check()
    wait_layers_loaded(first, 2)
    first.click("#viewYZPos")
    first.evaluate("dist = dist * 0.8; panX = 4")
    first.uncheck("#coordinateAxes")
    set_range(first, "#opacity", 55)
    first.evaluate("setClipState({planes: [{id: 'y-max', enabled: true, value: 20}]})")
    saved = first.evaluate("saveViewState()")
    assert len(saved["layers"]["selected"]) == 2 and saved["viewer"] == "lazy"
    before = coverage(first)

    context = browser.new_context()
    try:
        second = open_lazy(context.new_page(), served, lazy_template, tmp_path / "b")
        report = load_file(second, tmp_path, saved)
        assert report["unknown"] == [] and report["errors"] == []
        wait_layers_loaded(second, 2)
        assert second.evaluate("saveViewState()") == saved
        assert coverage(second) == before
    finally:
        context.close()
    watched_page.assert_clean()


def test_lazy_reports_a_layer_that_no_longer_exists(
    watched_page, served, lazy_template, tmp_path
):
    page = open_lazy(watched_page.page, served, lazy_template, tmp_path)
    state = page.evaluate("saveViewState()")
    state["layers"] = {"selected": [{"subsystem": "Tracker", "layer": "Gone"}]}
    report = load_file(page, tmp_path, state)
    assert report["unknown"] == [{"kind": "layer", "value": "Tracker / Gone"}]
    assert "Tracker / Gone" in page.inner_text("#viewMessage")


def test_a_standalone_view_applies_only_the_shared_settings_in_lazy(
    watched_page, standalone_html, served, lazy_template, tmp_path, browser
):
    source = open_standalone(watched_page.page, standalone_html)
    customise_standalone(source)
    state = source.evaluate("saveViewState()")
    context = browser.new_context()
    try:
        page = open_lazy(context.new_page(), served, lazy_template, tmp_path)
        report = load_file(page, tmp_path, state)
        assert report["errors"] == []
        assert {"camera", "clipping", "display"} <= set(report["applied"])
        assert {"filters", "hierarchy"} <= set(report["ignored"])
        assert any("saved from the standalone viewer" in w for w in report["warnings"])
        assert page.evaluate("[yaw, pitch]") == [0.31, 0.12]
        assert (
            page.evaluate("getClipState().planes.find(p => p.id === 'x-min').enabled")
            is True
        )
    finally:
        context.close()


def test_lazy_embedded_view_state_wins_over_default_load(
    watched_page, served, tmp_path
):
    source = tmp_path / "detector.gdml"
    shutil.copy(EXAMPLE, source)
    out = tmp_path / "bundle.html"
    state = {
        "format": "gdml-inspector-view",
        "version": 1,
        "viewer": "lazy",
        "camera": {
            "yaw": 0.4,
            "pitch": 0.3,
            "dist": 800,
            "pan": [0, 0],
            "up": [0, 1, 0],
        },
        "layers": {"selected": [{"subsystem": "Other", "layer": "support"}]},
    }
    view = tmp_path / "v.json"
    view.write_text(json.dumps(state), encoding="utf-8")
    args = [
        "--lazy",
        str(source),
        *BUILD,
        "--default-load",
        "all",
        "--view-state",
        str(view),
        "--out",
        str(out),
    ]
    assert cli.main(args) == 0
    page = watched_page.page
    page.goto(f"{served(tmp_path)}/{quote(out.name)}")
    page.wait_for_function("window.lastViewReport")
    page.wait_for_function(
        "!renderPending && Object.values(states).every(s => !s.loading)"
    )
    selected = page.evaluate("saveViewState().layers.selected")
    assert selected == [{"subsystem": "Other", "layer": "support"}]
    assert page.evaluate("[yaw, pitch]") == [0.4, 0.3]
    watched_page.assert_clean()
