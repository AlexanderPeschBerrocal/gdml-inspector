"""The viewer's layer tree follows whatever grouping the export used."""

import json

import pytest

from gdml_inspector import cli

from .browser_helpers import VIEWPORT, next_frames
from .test_grouping import SCENE


@pytest.fixture(scope="module")
def grouped_export(tmp_path_factory):
    directory = tmp_path_factory.mktemp("grouping-browser")
    gdml = directory / "scene.gdml"
    gdml.write_text(
        f"""<?xml version="1.0"?>
<gdml><define/><materials/>
<solids><box name="world_box" x="1000" y="1000" z="1000" lunit="mm"/>
<box name="shape" x="2" y="4" z="6" lunit="mm"/></solids>
<structure>{SCENE}</structure>
<setup name="Default" version="1.0"><world ref="world"/></setup></gdml>""",
        encoding="utf-8",
    )
    rules = directory / "rules.json"
    rules.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "match": {"path": "world/calo/*"},
                        "subsystem": "Calo bits",
                        "layer": "inner",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out = directory / "export" / "v.html"
    out.parent.mkdir()
    assert (
        cli.main(
            [
                "--lazy",
                str(gdml),
                "--backend",
                "python",
                "--grouping",
                "hierarchy",
                "--group-depth",
                "1",
                "--grouping-rules",
                str(rules),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    return out.parent


def open_page(watched_page, served, directory):
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(directory)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    return page


def test_the_layer_tree_shows_the_chosen_grouping(watched_page, served, grouped_export):
    page = open_page(watched_page, served, grouped_export)
    subsystems = page.eval_on_selector_all(
        ".subsystem-head", "els => els.map(e => e.textContent.trim())"
    )
    assert subsystems == ["Calo bits", "ecal_a", "hcal_b", "plate"]
    layers = page.eval_on_selector_all(
        "input[data-layer-key]", "els => els.map(e => e.dataset.layerKey)"
    )
    assert "Calo bits\x1finner" in layers and "ecal_a\x1f(direct)" in layers


def test_loading_picking_and_saved_views_work_with_a_custom_grouping(
    watched_page, served, grouped_export
):
    from .lazy_scene import _LAZY_PROJECT, object_center, selected

    page = open_page(watched_page, served, grouped_export)
    page.click("#loadAll")
    page.wait_for_function("Object.values(states).every(s => s.loaded)")
    page.click("#viewXYPos")
    page.evaluate("ensurePlacements()")
    page.wait_for_function("placements !== null")
    next_frames(page)
    sx, sy = page.evaluate(_LAZY_PROJECT, object_center(page, "world/hcal_b"))
    page.mouse.click(sx, sy)
    assert selected(page) == "world/hcal_b"
    state = page.evaluate("saveViewState()")
    assert {"subsystem": "Calo bits", "layer": "inner"} in state["layers"]["selected"]
    page.evaluate("setLayer('Calo bits\\x1finner', false)")
    page.wait_for_function(
        "MANIFEST.chunks.filter(c => c.subsystem === 'Calo bits').every(c => !states[c.key].loaded)"
    )
    assert watched_page.problems == []
    page.evaluate("state => loadViewState(state, 'test')", state)
    page.wait_for_function(
        "MANIFEST.chunks.filter(c => c.subsystem === 'Calo bits').every(c => states[c.key].loaded)"
    )
