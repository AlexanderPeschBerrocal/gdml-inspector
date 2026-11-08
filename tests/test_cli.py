"""Error paths and optional-backend absence without a native installation."""

import json
import sys

import pytest

from gdml_inspector import cli


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_missing_input_returns_nonzero(tmp_path, mode):
    assert cli.main([*mode, str(tmp_path / "missing.gdml")]) == 2


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_malformed_xml_reports_error(tmp_path, capsys, mode):
    invalid = tmp_path / "invalid.gdml"
    invalid.write_text("<gdml>")
    assert cli.main([*mode, str(invalid), "--backend", "python"]) == 1
    assert "gdml-inspector:" in capsys.readouterr().err


def test_empty_explicit_argv_does_not_use_process_arguments(monkeypatch, example_gdml):
    monkeypatch.setattr("sys.argv", ["gdml-inspector", str(example_gdml)])
    with pytest.raises(SystemExit) as error:
        cli.main([])
    assert error.value.code == 2


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_explicit_missing_optional_backend_reports_failure(
    mode, example_gdml, monkeypatch, tmp_path, capsys
):
    monkeypatch.setitem(sys.modules, "pyg4ometry", None)
    assert (
        cli.main(
            [
                *mode,
                str(example_gdml),
                "--backend",
                "pyg4ometry",
                "--out",
                str(tmp_path / "out.html"),
            ]
        )
        == 3
    )
    assert "pyg4ometry is not installed" in capsys.readouterr().err


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_auto_backend_exports_without_native_dependencies(
    mode, example_gdml, monkeypatch, tmp_path, capsys
):
    monkeypatch.setitem(sys.modules, "pyg4ometry", None)
    output = tmp_path / "out.html"
    assert (
        cli.main([*mode, str(example_gdml), "--backend", "auto", "--out", str(output)])
        == 0
    )
    assert output.is_file()
    assert "pure-Python backend" in capsys.readouterr().out


@pytest.fixture
def broken_reference_gdml(write_gdml):
    return write_gdml(
        solids="",
        structure="""<volume name="world"><materialref ref="Galactic"/>
<solidref ref="world_box"/>
<physvol><volumeref ref="absent"/></physvol></volume>""",
    )


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_strict_exits_nonzero_for_an_error_severity_diagnostic(
    mode, broken_reference_gdml, tmp_path, capsys
):
    output = tmp_path / "out.html"
    assert (
        cli.main(
            [
                *mode,
                str(broken_reference_gdml),
                "--backend",
                "python",
                "--validate-only",
                "--strict",
                "--out",
                str(output),
            ]
        )
        == 5
    )
    assert "Strict validation failed" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_strict_still_writes_the_viewer_without_validate_only(
    mode, broken_reference_gdml, tmp_path
):
    output = tmp_path / "out.html"
    assert (
        cli.main(
            [
                *mode,
                str(broken_reference_gdml),
                "--backend",
                "python",
                "--strict",
                "--out",
                str(output),
            ]
        )
        == 5
    )
    assert output.is_file()


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_strict_passes_a_clean_geometry(mode, example_gdml, tmp_path):
    output = tmp_path / "out.html"
    assert (
        cli.main(
            [
                *mode,
                str(example_gdml),
                "--backend",
                "python",
                "--strict",
                "--out",
                str(output),
            ]
        )
        == 0
    )
    assert output.is_file()


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_validate_only_does_not_write_a_viewer(mode, example_gdml, tmp_path):
    output = tmp_path / "out.html"
    assert (
        cli.main(
            [
                *mode,
                str(example_gdml),
                "--backend",
                "python",
                "--validate-only",
                "--out",
                str(output),
            ]
        )
        == 0
    )
    assert not output.exists()


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_report_writes_the_structured_diagnostics_json(
    mode, broken_reference_gdml, tmp_path
):
    report = tmp_path / "report.json"
    assert (
        cli.main(
            [
                *mode,
                str(broken_reference_gdml),
                "--backend",
                "python",
                "--validate-only",
                "--report",
                str(report),
            ]
        )
        == 0
    )
    diagnostics = json.loads(report.read_text())
    assert diagnostics == [
        {
            "code": "missing-volume-reference",
            "severity": "error",
            "category": "reference",
            "construct": "physvol",
            "identifier": "absent",
            "backend": "",
            "message": "Missing volume reference: absent",
            "count": 1,
        }
    ]


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_fail_on_extends_strict_beyond_error_severity(mode, single_volume, tmp_path):
    # An unresolvable expression is only a warning/numeric diagnostic, so
    # --strict alone should pass; --fail-on numeric should also catch it.
    path = single_volume('<box name="shape" x="unknown_name" y="4" z="6" lunit="mm"/>')
    base_args = [
        *mode,
        str(path),
        "--backend",
        "python",
        "--validate-only",
        "--strict",
        "--out",
        str(tmp_path / "out.html"),
    ]
    assert cli.main(base_args) == 0
    assert cli.main([*base_args, "--fail-on", "numeric"]) == 5
