"""Real headless-browser verification of the lazy viewer's diagnostics
panel -- the first Playwright coverage of assets/lazy.html in this
project. file:// loading cannot fetch binary chunks (see docs/
limitations.md), but the diagnostics panel reads only the inlined
manifest, so it is fully testable without --serve."""

import json

import pytest

from gdml_inspector import cli


@pytest.fixture
def viewer_path(tmp_path, write_gdml):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="&lt;img src=x onerror=window.__pwned=1&gt;"/><solidref ref="shape"/>
  <replicavol number="2"><volumeref ref="leaf"/></replicavol>
</volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed"><volumeref ref="leaf"/></physvol>
  <physvol><volumeref ref="does_not_exist"/></physvol>
</volume>""",
    )
    output = tmp_path / "viewer.html"
    assert (
        cli.main(["--lazy", str(path), "--backend", "python", "--out", str(output)])
        == 0
    )
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
    chunk_fetch_errors = [e for e in errors if "fetch" not in e.lower()]
    assert chunk_fetch_errors == []


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


def test_a_diagnostic_free_fixture_shows_the_default_message(
    tmp_path, write_gdml, page
):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed"><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    output = tmp_path / "viewer.html"
    assert (
        cli.main(["--lazy", str(path), "--backend", "python", "--out", str(output)])
        == 0
    )
    page.goto(output.resolve().as_uri())
    page.wait_for_selector("#warnings")
    assert page.inner_text("#warnings") == "No geometry warnings."
