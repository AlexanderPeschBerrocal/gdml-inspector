"""Version consistency across declared metadata, the installed
distribution, the runtime ``__version__``, and every CLI entry point.
pyproject.toml's version field is the one authoritative value;
__init__.py always derives from the installed distribution rather than
duplicating it (see CHANGELOG.md)."""

import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest

import gdml_inspector
from gdml_inspector import __version__, cli

if sys.version_info >= (3, 11):
    import tomllib as _toml
else:
    import tomli as _toml


def _declared_version() -> str:
    path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with path.open("rb") as handle:
        return _toml.load(handle)["project"]["version"]


def test_declared_installed_and_runtime_versions_agree():
    assert _declared_version() == installed_version("gdml-inspector") == __version__


def test_installed_version_falls_back_to_an_unmistakable_sentinel_when_uninstalled(
    monkeypatch,
):
    def raise_not_found(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(gdml_inspector, "_distribution_version", raise_not_found)
    assert gdml_inspector.installed_version() == "0+unknown"


@pytest.mark.parametrize(
    ("command", "args"),
    [
        (cli.main, ["--version"]),
        (cli.lazy_main, ["--version"]),
        (cli.main, ["--lazy", "--version"]),
        (cli.main, ["--diff", "--version"]),
        (cli.main, ["--gltf", "--version"]),
    ],
)
def test_version_option_reports_runtime_version_for_every_mode(command, args, capsys):
    with pytest.raises(SystemExit) as error:
        command(args)
    assert error.value.code == 0
    assert capsys.readouterr().out.strip().endswith(f" {__version__}")


def test_python_dash_m_invocation_reports_the_same_version():
    result = subprocess.run(
        [sys.executable, "-m", "gdml_inspector", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().endswith(f" {__version__}")
