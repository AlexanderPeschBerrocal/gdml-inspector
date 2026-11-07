"""Regression tests for the extraction's package and export boundaries."""

import json
from pathlib import Path

import pytest

from gdml_inspector import cli, geometry, lazy

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"


def build_scene(path=EXAMPLE):
    return geometry.GDMLSceneBuilder(
        path,
        quality="fast",
        include_containers=False,
        include_world=False,
        max_objects=100,
        max_vertices=100_000,
    ).build()


def test_geometry_survives_extraction():
    scene = build_scene()
    assert scene["meta"]["objects"] == 5
    assert scene["meta"]["warnings"] == []
    assert scene["meta"]["materials"] == {"G4_Si": 4, "G4_Fe": 1}
    assert len(scene["positions"]) == scene["meta"]["vertices"] * 3
    assert len(scene["colors"]) == scene["meta"]["vertices"] * 4
    assert len(scene["ids"]) == scene["meta"]["vertices"]
    endcaps = [o for o in scene["objects"] if o["volume"] == "pixel_endcap"]
    assert sorted((o["bbox"][2], o["bbox"][5]) for o in endcaps) == [
        (-351.5, -348.5),
        (348.5, 351.5),
    ]


def test_single_file_export_from_different_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "nested" / "viewer.html"
    assert cli.main([str(EXAMPLE), "--backend", "python", "--out", str(output)]) == 0
    document = output.read_text()
    embedded = document.split("const SCENE = ", 1)[1].split(";\nconst canvas =", 1)[0]
    assert json.loads(embedded)["meta"]["objects"] == 5
    assert "GDML Inspector" in document
    assert "__SCENE__" not in document
    assert "<script src=" not in document


def test_lazy_export_chunk_contents_and_reuse(tmp_path, monkeypatch):
    output = tmp_path / "lazy.html"
    args = [str(EXAMPLE), "--backend", "python", "--out", str(output)]
    assert (
        cli.main(
            ["--lazy", *args, "--chunk-vertices", "600", "--default-load", "Tracker"]
        )
        == 0
    )
    manifest = lazy.read_existing_manifest(output)
    assert manifest["objects"] == 5
    assert manifest["defaultLoad"] == ["Tracker"]
    assert manifest["chunks"]
    before = {}
    for chunk in manifest["chunks"]:
        path = output.parent / chunk["url"]
        binary = path.read_bytes()
        magic, version, vertices, positions, colors, end, total = (
            lazy.HEADER.unpack_from(binary)
        )
        assert magic == lazy.MAGIC
        assert version == lazy.FORMAT_VERSION
        assert vertices == chunk["vertices"]
        assert positions == lazy.HEADER.size
        assert colors - positions == vertices * 3 * 4
        assert end - colors == vertices * 4
        assert total == len(binary) == chunk["bytes"]
        before[path] = binary

    def unexpected_build(*args, **kwargs):
        pytest.fail("Asset reuse must not rebuild geometry")

    monkeypatch.setattr(lazy.LazyGDMLSceneBuilder, "build_chunks", unexpected_build)
    assert cli.lazy_main([*args, "--reuse-assets", "--default-load", "all"]) == 0
    assert lazy.read_existing_manifest(output)["defaultLoad"] == ["all"]
    assert all(path.read_bytes() == binary for path, binary in before.items())
    next(iter(before)).unlink()
    assert cli.lazy_main([*args, "--reuse-assets"]) == 4


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_missing_input_returns_nonzero(tmp_path, mode):
    assert cli.main([*mode, str(tmp_path / "missing.gdml")]) == 2


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_malformed_xml_reports_error(tmp_path, capsys, mode):
    invalid = tmp_path / "invalid.gdml"
    invalid.write_text("<gdml>")
    assert cli.main([*mode, str(invalid), "--backend", "python"]) == 1
    assert "gdml-inspector:" in capsys.readouterr().err


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_embedded_gdml_names_cannot_close_script(tmp_path, mode):
    source = tmp_path / "escape.gdml"
    source.write_text(EXAMPLE.read_text().replace("G4_Si", "&lt;/script&gt;"))
    output = tmp_path / "escape.html"
    assert (
        cli.main([*mode, str(source), "--backend", "python", "--out", str(output)]) == 0
    )
    document = output.read_text()
    assert document.count("</script>") == 1
    assert "<\\/script>" in document


def test_empty_explicit_argv_does_not_use_process_arguments(monkeypatch):
    monkeypatch.setattr("sys.argv", ["gdml-inspector", str(EXAMPLE)])
    with pytest.raises(SystemExit) as error:
        cli.main([])
    assert error.value.code == 2


def test_version(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["--version"])
    assert error.value.code == 0
    assert "0.1.0" in capsys.readouterr().out
