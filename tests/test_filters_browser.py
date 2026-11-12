"""One filter model, two viewers: the same filter must select the same
placements in standalone and lazy exports, from metadata alone."""

import json

import pytest

from gdml_inspector import cli

from .browser_helpers import VIEWPORT, next_frames
from .lazy_scene import QUALITY

GDML = """<?xml version="1.0" encoding="UTF-8"?>
<gdml><define/><materials/>
<solids>
<box name="world_box" x="1000" y="1000" z="1000" lunit="mm"/>
<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<tube name="tube_shape" rmin="0" rmax="3" z="10" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>
</solids>
<structure>
<volume name="ecal1_vol"><materialref ref="G4_Fe"/><solidref ref="shape"/></volume>
<volume name="hcal1_vol"><materialref ref="G4_Fe2"/><solidref ref="shape"/></volume>
<volume name="Ünïcode_τ_vol"><materialref ref="Σilicon"/><solidref ref="tube_shape"/></volume>
<volume name="plate_vol"><materialref ref="G4_Fe_Alloy"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="ecal_a"><volumeref ref="ecal1_vol"/>
    <position x="-50" y="0" z="0" unit="mm"/></physvol>
  <physvol name="ecal_c"><volumeref ref="ecal1_vol"/>
    <position x="-80" y="0" z="0" unit="mm"/></physvol>
  <physvol name="hcal_b"><volumeref ref="hcal1_vol"/>
    <position x="50" y="0" z="0" unit="mm"/></physvol>
  <physvol name="tau"><volumeref ref="Ünïcode_τ_vol"/>
    <position x="0" y="40" z="0" unit="mm"/></physvol>
  <physvol name="plate"><volumeref ref="plate_vol"/>
    <position x="0" y="-40" z="0" unit="mm"/></physvol>
</volume>
</structure>
<setup name="Default" version="1.0"><world ref="world"/></setup>
</gdml>
"""

ALL = [
    "world/ecal_a",
    "world/ecal_c",
    "world/hcal_b",
    "world/tau",
    "world/plate",
]

# (filter state, expected matching placement paths)
CASES = {
    "none": ({}, ALL),
    "path-distinguishes-placements-of-one-volume": (
        {"search": "path:world/ecal_a"},
        ["world/ecal_a"],
    ),
    "volume-name-matches-both-placements": (
        {"search": "volume:ecal1_vol"},
        ["world/ecal_a", "world/ecal_c"],
    ),
    "placement-name": ({"search": "name:hcal_b"}, ["world/hcal_b"]),
    "plain-text-any-field": ({"search": "ecal"}, ["world/ecal_a", "world/ecal_c"]),
    "literal-substring-of-similar-materials": (
        {"search": "material:G4_Fe"},
        ["world/ecal_a", "world/ecal_c", "world/hcal_b", "world/plate"],
    ),
    "solid-type": ({"solidType": "tube"}, ["world/tau"]),
    "solid-ref": ({"search": "solid:tube_shape"}, ["world/tau"]),
    "type-scope": (
        {"search": "type:box"},
        ["world/ecal_a", "world/ecal_c", "world/hcal_b", "world/plate"],
    ),
    "unicode-case-insensitive": ({"search": "ÜNÏCODE_Τ"}, ["world/tau"]),
    "unicode-material": ({"search": "material:σilicon"}, ["world/tau"]),
    "exclude": (
        {"search": "ecal", "exclude": "path:world/ecal_c"},
        ["world/ecal_a"],
    ),
    "exclude-beats-include": (
        {"search": "G4_Fe", "exclude": "material:G4_Fe_Alloy"},
        ["world/ecal_a", "world/ecal_c", "world/hcal_b"],
    ),
    "disabled-materials-are-exact": (
        {"disabledMaterials": ["G4_Fe"]},
        ["world/hcal_b", "world/tau", "world/plate"],
    ),
    "categories-combine-with-and": (
        {"search": "ecal", "solidType": "tube"},
        [],
    ),
    "invert": (
        {"search": "ecal", "invert": True},
        ["world/hcal_b", "world/tau", "world/plate"],
    ),
    "regex-characters-are-literal": ({"search": "world/(ecal.*"}, []),
    "unknown-prefix-is-plain-text": ({"search": "foo:bar"}, []),
}


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    directory = tmp_path_factory.mktemp("filters")
    gdml = directory / "scene.gdml"
    gdml.write_text(GDML, encoding="utf-8")
    lazy = directory / "lazy"
    lazy.mkdir()
    assert cli.main(["--lazy", str(gdml), *QUALITY, "--out", str(lazy / "v.html")]) == 0
    standalone = directory / "s.html"
    assert cli.main([str(gdml), *QUALITY, "--out", str(standalone)]) == 0
    return lazy, standalone


def full(state):
    return {
        "search": "",
        "exclude": "",
        "solidType": "",
        "disabledMaterials": [],
        "invert": False,
        **state,
    }


def open_standalone(page, sources):
    page.set_viewport_size(VIEWPORT)
    page.goto(sources[1].resolve().as_uri())
    page.wait_for_selector("#hierarchyTree .tree-row")
    return page


def open_lazy(watched_page, served, sources, *, load="all"):
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(sources[0])}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    if load == "all":
        page.click("#loadAll")
        page.wait_for_function("Object.values(states).every(s => s.loaded)")
    next_frames(page)
    return page


def standalone_matches(page, state):
    page.evaluate("s => { filterPanel.set(s); applyFilters(); }", full(state))
    return page.evaluate(
        "SCENE.objects.filter((o, i) => objectVisible[i] === 1).map(o => o.path)"
    )


def lazy_matches(page, state):
    page.evaluate(
        "async s => { filterPanel.set(s); await ensurePlacements(); recomputeVisibility(); }",
        full(state),
    )
    page.wait_for_function("placements !== null && filterMatchMask !== null")
    return page.evaluate(
        "placements.objects.filter((o, i) => filterMatchMask[i] === 1).map(o => o.path)"
    )


@pytest.mark.parametrize("name", list(CASES))
def test_both_viewers_select_exactly_the_expected_placements(
    name, watched_page, served, sources
):
    state, expected = CASES[name]
    lazy = open_lazy(watched_page, served, sources)
    assert sorted(lazy_matches(lazy, state)) == sorted(expected)
    standalone = lazy.context.new_page()
    try:
        open_standalone(standalone, sources)
        assert sorted(standalone_matches(standalone, state)) == sorted(expected)
    finally:
        standalone.close()
    assert watched_page.problems == []


def test_filtering_unloaded_layers_works_from_metadata_without_fetching_chunks(
    watched_page, served, sources
):
    requests = []
    watched_page.page.on("request", lambda r: requests.append(r.url))
    page = open_lazy(watched_page, served, sources, load="none")
    page.fill("#search", "ecal")
    page.wait_for_function("filterMatchMask !== null")
    assert not any(url.endswith(".bin") for url in requests)
    counts = page.inner_text("#filterCounts")
    assert "2 of 5 match" in counts and "0 shown" in counts
    # Matching, but nothing loaded: the panel says why.
    assert "layers are not loaded" in page.inner_text("#filterEmpty")
    # Loading the matching layer shows them without touching the filter.
    page.evaluate(f"setLayer({json.dumps('ECAL' + chr(31) + 'Barrel layer 1')}, true)")
    page.wait_for_function("Object.values(states).some(s => s.loaded)")
    page.wait_for_function(
        "document.getElementById('filterCounts').innerText.includes('2 shown')"
    )
    assert page.is_hidden("#filterEmpty")


def test_nothing_matching_is_explained_in_both_viewers(watched_page, served, sources):
    lazy = open_lazy(watched_page, served, sources)
    standalone = lazy.context.new_page()
    try:
        open_standalone(standalone, sources)
        for page, apply in ((lazy, lazy_matches), (standalone, standalone_matches)):
            assert apply(page, {"search": "ecal", "solidType": "tube"}) == []
            text = page.inner_text("#filterEmpty")
            assert "Nothing matches" in text
            assert 'search "ecal" AND solid type tube' in text
            assert page.inner_text("#filterCounts").startswith("0 of 5 match")
    finally:
        standalone.close()


def test_summary_reset_and_counts(watched_page, served, sources):
    page = open_lazy(watched_page, served, sources)
    assert page.inner_text("#filterSummary") == "No filters active."
    assert page.is_disabled("#resetFilters")
    lazy_matches(page, {"search": "ecal", "exclude": "ecal_c", "invert": True})
    summary = page.inner_text("#filterSummary")
    assert (
        'search "ecal"' in summary
        and 'exclude "ecal_c"' in summary
        and "inverted" in summary
    )
    page.click("#resetFilters")
    page.wait_for_function(
        "document.getElementById('filterCounts').innerText.startsWith('5 of 5')"
    )
    assert page.input_value("#search") == ""
    assert page.inner_text("#filterSummary") == "No filters active."


def test_a_pick_filtered_away_is_cleared_in_both_viewers(watched_page, served, sources):
    lazy = open_lazy(watched_page, served, sources)
    lazy.click("#viewXYPos")
    lazy.evaluate("ensurePlacements()")
    lazy.wait_for_function("placements !== null")
    lazy.evaluate("selectPath('world/hcal_b')")
    lazy.wait_for_function("selectedObjectId !== null")
    lazy.fill("#search", "ecal")
    lazy.wait_for_function("selectedObjectId === null")
    assert lazy.is_hidden("#detailsContent")

    standalone = lazy.context.new_page()
    try:
        open_standalone(standalone, sources)
        standalone.evaluate(
            "revealInHierarchy('world/hcal_b'); selectObject(SCENE.objects.find(o => o.path === 'world/hcal_b').id)"
        )
        assert standalone.evaluate("selectedObjectId") is not None
        standalone.fill("#search", "ecal")
        assert standalone.evaluate("selectedObjectId") is None
    finally:
        standalone.close()


def test_saved_view_round_trips_filters_and_reports_unknown_identifiers(
    watched_page, served, sources
):
    lazy = open_lazy(watched_page, served, sources)
    state = {
        "search": "path:world/ecal",
        "exclude": "ecal_c",
        "solidType": "box",
        "disabledMaterials": ["G4_Fe2"],
        "invert": True,
    }
    lazy_matches(lazy, state)
    saved = lazy.evaluate("saveViewState()")
    assert saved["filters"] == full(state)

    standalone = lazy.context.new_page()
    try:
        open_standalone(standalone, sources)
        standalone.evaluate("s => loadViewState(s, 'from lazy')", saved)
        standalone.wait_for_function("filterPanel.state().invert === true")
        assert standalone.evaluate("filterPanel.state()") == full(state)
        # The same saved filter selects the same placements in both.
        assert sorted(
            standalone.evaluate(
                "SCENE.objects.filter((o, i) => objectVisible[i] === 1).map(o => o.path)"
            )
        ) == sorted(
            lazy.evaluate(
                "placements.objects.filter((o, i) => filterMatchMask[i] === 1).map(o => o.path)"
            )
        )

        saved["filters"]["disabledMaterials"].append("Unobtainium")
        saved["filters"]["solidType"] = "torus"
        for page in (lazy, standalone):
            page.evaluate("s => loadViewState(s, 'stale')", saved)
            page.wait_for_function(
                "document.getElementById('viewStatePanel').innerText.includes('Unobtainium')"
            )
            text = page.inner_text("#viewStatePanel")
            assert "torus" in text
            assert page.input_value("#solidType") == ""
    finally:
        standalone.close()


def test_legend_escapes_material_names_and_shows_counts(watched_page, served, sources):
    page = open_lazy(watched_page, served, sources)
    labels = page.eval_on_selector_all(
        "#materials .material-item",
        "els => els.map(e => e.innerText.replace(/\\s+/g, ' ').trim())",
    )
    assert "Σilicon 1" in labels
    assert "G4_Fe 2" in labels
