"""Launcher version coverage; broader metadata consistency is issue #20."""

import pytest

from gdml_inspector import __version__, cli


@pytest.mark.parametrize("command", [cli.main, cli.lazy_main])
def test_version_option_reports_runtime_version(command, capsys):
    with pytest.raises(SystemExit) as error:
        command(["--version"])
    assert error.value.code == 0
    assert capsys.readouterr().out.strip().endswith(f" {__version__}")
