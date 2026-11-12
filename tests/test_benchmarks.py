"""The stress generator and benchmark runner (benchmarks/) stay working."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from gdml_inspector import cli

ROOT = Path(__file__).resolve().parents[1] / "benchmarks"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(ROOT))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT))
    return module


@pytest.fixture(scope="module")
def stress():
    return load("stress_gdml")


@pytest.fixture(scope="module")
def runner(stress):
    return load("run_benchmarks")


def test_generator_is_deterministic(stress):
    assert stress.build(50, 7, "flat", 10) == stress.build(50, 7, "flat", 10)
    assert stress.build(50, 7, "flat", 10) != stress.build(50, 8, "flat", 10)


@pytest.mark.parametrize("layout", ["flat", "nested"])
@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_generated_scene_exports_every_placement_in_both_modes(
    stress, tmp_path, layout, mode
):
    gdml = tmp_path / "stress.gdml"
    assert (
        stress.main(
            [
                str(gdml),
                "--placements",
                "60",
                "--logical",
                "7",
                "--layout",
                layout,
                "--group-size",
                "25",
            ]
        )
        == 0
    )
    out = tmp_path / "v.html"
    assert cli.main([*mode, str(gdml), "--backend", "python", "--out", str(out)]) == 0
    if mode:
        from gdml_inspector.lazy import read_existing_manifest

        manifest = read_existing_manifest(out)
        assert manifest["objects"] == 60
        # Repeated placements share a handful of logical volumes but stay distinct.
        assert len({c["subsystem"] for c in manifest["chunks"]}) >= 2
    else:
        text = out.read_text(encoding="utf-8")
        assert (
            json.loads(
                text.split("const SCENE = ", 1)[1].split(";\nconst HIERARCHY", 1)[0]
            )["meta"]["objects"]
            == 60
        )


def test_generator_rejects_nonsense(stress, tmp_path, capsys):
    assert stress.main([str(tmp_path / "x.gdml"), "--placements", "0"]) == 2
    assert "error" in capsys.readouterr().err


def test_runner_reports_time_rss_and_sizes_for_both_modes(runner, tmp_path):
    json_out = tmp_path / "results.json"
    assert (
        runner.main(["--sizes", "small", "--quality", "fast", "--json", str(json_out)])
        == 0
    )
    rows = json.loads(json_out.read_text(encoding="utf-8"))
    assert [r["mode"] for r in rows] == ["standalone", "lazy"]
    for row in rows:
        assert row["seconds"] > 0
        assert row["peakRssBytes"] > 10 * 2**20  # a Python process, in bytes
        assert row["outputBytes"] > 0
    lazy = rows[1]
    assert lazy["objects"] == 2000
    assert 0 < lazy["initialTransferBytes"] < lazy["outputBytes"]
    table = runner.markdown(rows)
    assert table.count("\n") == 4 and "standalone" in table and "lazy" in table
