"""Error paths and optional-backend absence without a native installation."""

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
