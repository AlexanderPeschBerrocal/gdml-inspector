"""Real headless-browser verification of the standalone viewer's
diagnostics panel: structured filtering, JSON download, linking a
diagnostic to its hierarchy node, and -- the concrete regression this
guards against -- that GDML-controlled names can never inject markup into
the generated HTML."""

import json

import pytest

from gdml_inspector import cli


@pytest.fixture
def viewer_path(tmp_path, write_gdml):
    """A world with: a material name that is a classic HTML-injection
    payload (valid XML via &lt;/&gt; entities, decoded by ElementTree into
    the raw characters exactly like any real GDML file), and a volume with
    an unsupported daughter construct (an "unsupported-daughter"
    diagnostic, identifier = the volume's own name -- linkable to its
    hierarchy node)."""
    # Valid XML via entities, decoded by ElementTree into the raw
    # characters -- exactly as any real GDML file's attribute values are.
    payload = "&lt;img src=x onerror=window.__pwned=1&gt;"
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure=f"""
<volume name="leaf"><materialref ref="{payload}"/><solidref ref="shape"/>
  <replicavol number="2"><volumeref ref="leaf"/></replicavol>
</volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed"><volumeref ref="leaf"/></physvol>
  <physvol><volumeref ref="does_not_exist"/></physvol>
</volume>""",
    )
    output = tmp_path / "viewer.html"
    assert cli.main([str(path), "--backend", "python", "--out", str(output)]) == 0
    return output


@pytest.fixture
def opened(page, viewer_path):
    errors = []
    page.on(
        "console",
        lambda msg: errors.append(msg.text) if msg.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(viewer_path.resolve().as_uri())
    page.wait_for_selector("#warnings")
    return page, errors


def test_malicious_material_name_never_executes_or_creates_an_element(opened):
    page, errors = opened
    assert page.evaluate("window.__pwned") is None
    assert page.evaluate("document.querySelectorAll('img').length") == 0
    assert "<img" in page.inner_text("#materials")
    assert errors == []


def test_diagnostics_panel_renders_both_structured_diagnostics(opened):
    page, _errors = opened
    text = page.inner_text("#warnings")
    assert "Unsupported GDML daughter construct 'replicavol'" in text
    assert "Missing volume reference: does_not_exist" in text
    assert len(page.query_selector_all(".diagnostic-row")) == 2


def test_severity_and_category_filters_narrow_the_list(opened):
    page, _errors = opened
    assert page.evaluate(
        "Array.from(document.getElementById('diagnosticSeverity').options).map(o => o.value)"
    ) == ["", "error", "warning"]
    assert page.evaluate(
        "Array.from(document.getElementById('diagnosticCategory').options).map(o => o.value)"
    ) == ["", "reference", "unsupported"]

    page.select_option("#diagnosticCategory", "unsupported")
    assert "Unsupported GDML daughter" in page.inner_text("#warnings")
    assert "Missing volume reference" not in page.inner_text("#warnings")

    # warning + reference matches neither real diagnostic -- a genuine
    # miss, not a fabricated filter combination.
    page.select_option("#diagnosticSeverity", "warning")
    page.select_option("#diagnosticCategory", "reference")
    assert page.inner_text("#warnings") == "No diagnostics match this filter."


def test_download_button_produces_the_diagnostics_json(opened, tmp_path):
    page, _errors = opened
    with page.expect_download() as download_info:
        page.click("#downloadDiagnostics")
    download = download_info.value
    saved = tmp_path / "downloaded.json"
    download.save_as(saved)
    data = json.loads(saved.read_text())
    codes = {d["code"] for d in data}
    assert codes == {"unsupported-daughter", "missing-volume-reference"}
    daughter = next(d for d in data if d["code"] == "unsupported-daughter")
    assert daughter["identifier"] == "leaf"


def test_show_in_hierarchy_reveals_the_affected_volume(opened):
    page, _errors = opened
    # Only the unsupported-daughter diagnostic is linkable; the dangling
    # missing-volume-reference one names something that was never placed.
    buttons = page.query_selector_all(".diagnostic-link")
    assert len(buttons) == 1
    assert buttons[0].inner_text() == "Show in hierarchy"
    buttons[0].click()
    assert page.eval_on_selector("#hierarchySearch", "el => el.value") == "leaf"
    matches = page.query_selector_all(".tree-match")
    assert len(matches) == 1
    assert matches[0].get_attribute("data-path") == "world/placed"


def test_solid_identified_diagnostic_is_also_linkable_via_search(
    tmp_path, write_gdml, page
):
    # boolean-approximated's identifier is a SOLID ref, not a volume name;
    # confirm matchesHierarchySearch's solid-ref extension finds it too.
    path = write_gdml(
        solids="""
<box name="a" x="2" y="4" z="6" lunit="mm"/>
<box name="b" x="4" y="2" z="2" lunit="mm"/>
<subtraction name="carved"><first ref="a"/><second ref="b"/></subtraction>""",
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="carved"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed"><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    output = tmp_path / "viewer.html"
    assert cli.main([str(path), "--backend", "python", "--out", str(output)]) == 0
    page.goto(output.resolve().as_uri())
    page.wait_for_selector("#warnings")
    button = page.query_selector(".diagnostic-link")
    assert button is not None
    button.click()
    assert page.eval_on_selector("#hierarchySearch", "el => el.value") == "carved"
    matches = page.query_selector_all(".tree-match")
    assert len(matches) == 1
    assert matches[0].get_attribute("data-path") == "world/placed"


def test_a_diagnostic_free_fixture_shows_no_filters_and_the_default_message(
    tmp_path, single_volume, page
):
    path = single_volume()
    output = tmp_path / "viewer.html"
    assert cli.main([str(path), "--backend", "python", "--out", str(output)]) == 0
    page.goto(output.resolve().as_uri())
    page.wait_for_selector("#warnings")
    assert page.inner_text("#warnings") == "No geometry warnings."
    assert page.query_selector(".diagnostic-link") is None
