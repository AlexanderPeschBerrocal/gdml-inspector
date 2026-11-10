"""Portable HTML/resource regressions retained from the initial extraction."""

import json

import pytest

from gdml_inspector import cli


def test_single_file_export_from_different_working_directory(
    tmp_path, monkeypatch, example_gdml
):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "nested" / "viewer.html"
    assert (
        cli.main([str(example_gdml), "--backend", "python", "--out", str(output)]) == 0
    )
    document = output.read_text()
    embedded = document.split("const SCENE = ", 1)[1].split(";\nconst HIERARCHY =", 1)[
        0
    ]
    assert json.loads(embedded)["meta"]["objects"] == 5
    assert "GDML Inspector" in document
    assert "__SCENE__" not in document
    assert "__HIERARCHY__" not in document
    assert "<script src=" not in document


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_embedded_gdml_names_cannot_close_script(tmp_path, mode, example_gdml):
    source = tmp_path / "escape.gdml"
    source.write_text(example_gdml.read_text().replace("G4_Si", "&lt;/script&gt;"))
    output = tmp_path / "escape.html"
    assert (
        cli.main([*mode, str(source), "--backend", "python", "--out", str(output)]) == 0
    )
    document = output.read_text()
    assert document.count("</script>") == 1
    assert "<\\/script>" in document
