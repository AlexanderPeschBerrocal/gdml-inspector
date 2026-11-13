"""Parts that cannot grow any more are written to disk while the scene is still
being built, so memory holds only the parts still being filled -- without
changing a byte of the output unless --memory-budget is actually hit."""

import hashlib
import json
from pathlib import Path

import pytest

from gdml_inspector import cli, lazy

from .helpers import DEFAULT_OPTIONS
from .test_benchmarks import load


@pytest.fixture(scope="module")
def stress():
    return load("stress_gdml")


@pytest.fixture
def scene(tmp_path, stress):
    """2,000 placements over several layers (Tracker, ECAL, HCAL, Other)."""
    path = tmp_path / "scene.gdml"
    path.write_text(stress.build(2000, 40, "flat", 50), encoding="utf-8")
    return path


def export(path, out, *options):
    assert (
        cli.main(
            [
                "--lazy",
                str(path),
                "--backend",
                "python",
                "--quality",
                "fast",
                "--redact-paths",
                "--out",
                str(out),
                *options,
            ]
        )
        == 0
    )
    return lazy.read_existing_manifest(out)


def assets(out):
    directory = out.parent / f"{out.stem}.assets"
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def in_memory_export(path, out, **options):
    """The same export with nothing spilled: the reference."""
    settings = DEFAULT_OPTIONS | {"chunk_vertices": 500_000} | options
    builder = lazy.LazyGDMLSceneBuilder(path, **settings)
    builder.redact_paths = True
    builder.build_chunks()
    manifest = lazy.write_assets(builder, out, [])
    lazy.write_html(manifest, out)
    return builder


def test_spilling_full_parts_does_not_change_the_output(tmp_path, scene):
    # A small chunk size makes many parts fill and spill while building.
    spilled = tmp_path / "spilled" / "v.html"
    reference = tmp_path / "reference" / "v.html"
    spilled.parent.mkdir()
    reference.parent.mkdir()
    export(scene, spilled, "--chunk-vertices", "20000", "--max-objects", "100000")
    in_memory_export(
        scene,
        reference,
        chunk_vertices=20_000,
        max_objects=100_000,
        max_vertices=500_000_000,
    )
    assert assets(spilled) == assets(reference)
    assert spilled.read_bytes() == reference.read_bytes()
    manifest = lazy.read_existing_manifest(spilled)
    assert max(c["part"] for c in manifest["chunks"]) > 1  # parts really filled


def test_only_parts_still_being_filled_stay_in_memory(tmp_path, scene):
    seen = {"peak": 0}

    class Tracking(lazy.LazyGDMLSceneBuilder):
        def _enforce_memory_budget(self):
            seen["peak"] = max(seen["peak"], self.open_bytes)
            super()._enforce_memory_budget()

    spill = tmp_path / "spill"
    spill.mkdir()
    builder = Tracking(
        scene,
        **(
            DEFAULT_OPTIONS
            | {
                "chunk_vertices": 20_000,
                "max_objects": 100_000,
                "max_vertices": 500_000_000,
            }
        ),
    )
    builder.spill_dir = spill
    builder.build_chunks()
    open_parts = [c for c in builder.chunks.values() if c.frozen is None]
    frozen = [c for c in builder.chunks.values() if c.frozen is not None]
    assert frozen and open_parts
    assert all(lazy.chunk_memory(c) == 0 for c in frozen)
    # One open part per layer, each at most a chunk: that bounds the arrays.
    layers = {c.base_key for c in builder.chunks.values()}
    assert len(open_parts) <= len(layers)
    assert seen["peak"] <= len(layers) * (20_000 * 20 + 20_000 * 56 // 36 + 56) * 2
    everything = sum(c.vertex_count for c in builder.chunks.values())
    assert everything == builder.total_vertices
    assert builder.open_bytes == sum(lazy.chunk_memory(c) for c in open_parts)


def test_a_budget_that_is_hit_releases_parts_early_but_draws_the_same_scene(
    tmp_path, scene
):
    plain = tmp_path / "plain" / "v.html"
    budget = tmp_path / "budget" / "v.html"
    plain.parent.mkdir()
    budget.parent.mkdir()
    options = ("--max-objects", "100000", "--instancing", "off")
    a = export(scene, plain, *options)
    b = export(scene, budget, *options, "--memory-budget", "1")
    assert b["provenance"]["settings"]["memoryBudgetMiB"] == 1
    # Same objects, in the same order, with the same metadata.
    index = lambda out, m: json.loads(  # noqa: E731
        (out.parent / f"{out.stem}.assets" / "placements.json").read_text()
    )["nodes"]["object"]
    assert index(plain, a) == index(budget, b)
    assert (a["objects"], a["vertices"], a["triangles"]) == (
        b["objects"],
        b["vertices"],
        b["triangles"],
    )
    # ...but split into more parts, since layers had to continue elsewhere.
    assert len(b["chunks"]) > len(a["chunks"])
    for layer in {c["baseKey"] for c in a["chunks"]}:
        assert sum(c["vertices"] for c in a["chunks"] if c["baseKey"] == layer) == sum(
            c["vertices"] for c in b["chunks"] if c["baseKey"] == layer
        )
    # Every chunk file matches its manifest entry.
    for out, manifest in ((plain, a), (budget, b)):
        directory = out.parent
        for chunk in manifest["chunks"]:
            data = (directory / chunk["url"].replace("%20", " ")).read_bytes()
            assert len(data) == chunk["bytes"]
            assert hashlib.sha256(data).hexdigest() == chunk["sha256"]


def test_the_open_memory_never_exceeds_the_budget_by_more_than_one_object(
    tmp_path, scene
):
    peak = {"value": 0}

    class Tracking(lazy.LazyGDMLSceneBuilder):
        def _enforce_memory_budget(self):
            super()._enforce_memory_budget()
            peak["value"] = max(peak["value"], self.open_bytes)

    spill = tmp_path / "s"
    spill.mkdir()
    builder = Tracking(
        scene,
        **(
            DEFAULT_OPTIONS
            | {
                "chunk_vertices": 500_000,
                "max_objects": 100_000,
                "max_vertices": 500_000_000,
                "memory_budget": 1,
            }
        ),
    )
    builder.spill_dir = spill
    builder.build_chunks()
    one_object = 600 * 20 + 56  # a few hundred vertices at most
    assert peak["value"] <= 1024 * 1024 + one_object


def test_unlimited_budget_matches_the_default(tmp_path, scene):
    one = tmp_path / "one" / "v.html"
    two = tmp_path / "two" / "v.html"
    one.parent.mkdir()
    two.parent.mkdir()
    export(scene, one, "--max-objects", "100000")
    export(scene, two, "--max-objects", "100000", "--memory-budget", "0")
    a, b = assets(one), assets(two)
    assert a.keys() == b.keys()
    assert {k: v for k, v in a.items() if k.endswith(".bin")} == {
        k: v for k, v in b.items() if k.endswith(".bin")
    }


def test_the_budget_is_recorded_and_checked_by_reuse(tmp_path, scene, capsys):
    out = tmp_path / "v.html"
    manifest = export(scene, out, "--max-objects", "100000")
    assert manifest["provenance"]["settings"]["memoryBudgetMiB"] == 512
    base = [
        "--lazy",
        str(scene),
        "--backend",
        "python",
        "--out",
        str(out),
        "--reuse-assets",
    ]
    assert cli.main([*base, "--memory-budget", "64"]) == 4
    assert "memory" in capsys.readouterr().err.lower()
    assert cli.main([*base, "--memory-budget", "512"]) == 0
    assert cli.main(base) == 0


def test_no_spill_files_are_left_behind_and_validate_only_writes_nothing(
    tmp_path, scene
):
    out = tmp_path / "v.html"
    manifest = export(
        scene, out, "--chunk-vertices", "20000", "--max-objects", "100000"
    )
    directory = out.parent / f"{out.stem}.assets"
    assert not (directory / ".spill").exists()
    expected = {Path(c["url"]).name for c in manifest["chunks"]}
    expected |= {Path(manifest["placements"]["url"]).name}
    expected |= {Path(s["url"]).name for s in manifest["placements"]["shards"]}
    expected |= {Path(manifest["placements"]["containers"]["url"]).name}
    assert {p.name for p in directory.iterdir()} == {
        n.replace("%20", " ") for n in expected
    }
    checked = tmp_path / "checked" / "v.html"
    checked.parent.mkdir()
    assert (
        cli.main(
            [
                "--lazy",
                str(scene),
                "--backend",
                "python",
                "--validate-only",
                "--out",
                str(checked),
            ]
        )
        == 0
    )
    assert not (checked.parent / "v.assets").exists()


def test_a_failed_export_removes_its_spill_directory(tmp_path):
    bad = tmp_path / "bad.gdml"
    bad.write_text(
        "<gdml><define/><solids/><structure/><setup name='d' version='1'><world ref='nope'/></setup></gdml>"
    )
    out = tmp_path / "v.html"
    assert cli.main(["--lazy", str(bad), "--backend", "python", "--out", str(out)]) == 1
    assert not (tmp_path / "v.assets" / ".spill").exists()
