"""The lazy export's placements asset and per-vertex object ids (chunk format
v2): the foundation for hierarchy, picking and filters in lazy mode.

The guarantee that matters is cross-mode identity: for the same input and
settings the lazy export describes every placement exactly as the single-file
export does, and the ids stored in the chunks point into that description."""

import hashlib
import json
import shutil
import struct
import zipfile
from pathlib import Path

import pytest

from gdml_inspector import cli, lazy

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"
BUILD = ["--backend", "python", "--quality", "fast"]
IDENTITY_KEYS = [
    "id",
    "name",
    "semanticName",
    "volume",
    "semanticVolume",
    "material",
    "solid",
    "solidType",
    "bbox",
    "path",
]


@pytest.fixture
def lazy_export(tmp_path):
    source = tmp_path / "detector.gdml"
    shutil.copy(EXAMPLE, source)
    output = tmp_path / "viewer.html"
    assert cli.main(["--lazy", str(source), *BUILD, "--out", str(output)]) == 0
    manifest = lazy.read_existing_manifest(output)
    placements = json.loads(
        (output.parent / _decoded(manifest["placements"]["url"])).read_text()
    )
    return source, output, manifest, placements


def _decoded(url):
    from urllib.parse import unquote

    return unquote(url)


def standalone_scene(tmp_path):
    out = tmp_path / "standalone.html"
    assert cli.main([str(EXAMPLE), *BUILD, "--out", str(out)]) == 0
    document = out.read_text(encoding="utf-8")
    scene = json.loads(
        document.split("const SCENE = ", 1)[1].split(";\nconst HIERARCHY =", 1)[0]
    )
    hierarchy = json.loads(
        document.split("const HIERARCHY = ", 1)[1].split(";\nconst SOLIDS =", 1)[0]
    )
    solids = json.loads(document.split("const SOLIDS = ", 1)[1].split(";\n", 1)[0])
    return scene, hierarchy, solids


def test_manifest_records_the_placements_asset(lazy_export):
    _source, output, manifest, _placements = lazy_export
    entry = manifest["placements"]
    data = (output.parent / _decoded(entry["url"])).read_bytes()
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["bytes"] == len(data)
    assert entry["objects"] == manifest["objects"]
    assert manifest["chunkFormat"] == 2


def test_objects_are_described_exactly_as_the_single_file_export_does(
    lazy_export, tmp_path
):
    _source, _output, manifest, placements = lazy_export
    scene, hierarchy, solids = standalone_scene(tmp_path / "s")
    lazy_objects = placements["objects"]
    assert len(lazy_objects) == len(scene["objects"]) == manifest["objects"]
    for lazy_object, standalone_object in zip(
        lazy_objects, scene["objects"], strict=True
    ):
        assert {k: lazy_object[k] for k in IDENTITY_KEYS} == {
            k: standalone_object[k] for k in IDENTITY_KEYS
        }
    assert [o["id"] for o in lazy_objects] == list(range(len(lazy_objects)))
    assert placements["nodes"] == hierarchy
    assert placements["solids"] == solids


def test_each_object_names_the_chunk_that_holds_its_vertices(lazy_export):
    _source, output, manifest, placements = lazy_export
    owner = {}
    for chunk in manifest["chunks"]:
        binary = (output.parent / _decoded(chunk["url"])).read_bytes()
        count, id_start = (
            struct.unpack_from("<I", binary, 12)[0],
            struct.unpack_from("<I", binary, 24)[0],
        )
        for value in set(struct.unpack_from(f"<{count}I", binary, id_start)):
            owner[value - 1] = chunk["baseKey"]
    assert set(owner) == set(range(manifest["objects"]))
    for obj in placements["objects"]:
        assert obj["chunk"] == owner[obj["id"]]


def test_rendered_nodes_link_to_objects_and_carry_matrices(lazy_export):
    _source, _output, _manifest, placements = lazy_export
    by_id = {o["id"]: o for o in placements["objects"]}
    rendered = [n for n in placements["nodes"] if n["rendered"] and "objectId" in n]
    assert len(rendered) == len(by_id)
    for node in rendered:
        assert by_id[node["objectId"]]["path"] == node["path"]
        assert len(node["globalMatrix"]) == 12


def test_the_placements_file_never_contains_an_absolute_path(lazy_export):
    source, output, manifest, _placements = lazy_export
    text = (output.parent / _decoded(manifest["placements"]["url"])).read_text()
    assert str(source.parent) not in text


def test_reuse_verifies_the_placements_asset(lazy_export, capsys):
    source, output, manifest, _placements = lazy_export
    args = ["--lazy", str(source), "--out", str(output), "--reuse-assets"]
    assert cli.main(args) == 0
    path = output.parent / _decoded(manifest["placements"]["url"])
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    assert cli.main(args) == 4
    error = capsys.readouterr().err
    assert "placements" in error and "sha256" in error
    path.unlink()
    assert cli.main(args) == 4
    assert "missing" in capsys.readouterr().err


def test_a_manifest_from_before_object_ids_is_refused_with_guidance(
    lazy_export, capsys
):
    source, output, manifest, _placements = lazy_export
    del manifest["chunkFormat"], manifest["placements"]
    lazy.write_html(manifest, output)
    assert (
        cli.main(["--lazy", str(source), "--out", str(output), "--reuse-assets"]) == 4
    )
    error = capsys.readouterr().err
    assert "predates per-object ids" in error and "Rebuild" in error


def test_the_bundle_contains_the_placements_asset_and_the_whole_export_is_reproducible(
    tmp_path,
):
    names = []
    for index in ("one", "two"):
        directory = tmp_path / index
        directory.mkdir()
        shutil.copy(EXAMPLE, directory / "detector.gdml")
        archive = tmp_path / f"{index}.zip"
        args = [
            "--lazy",
            str(directory / "detector.gdml"),
            *BUILD,
            "--redact-paths",
            "--bundle",
            str(archive),
            "--out",
            str(directory / "viewer.html"),
        ]
        assert cli.main(args) == 0
        names.append(zipfile.ZipFile(archive).namelist())
    assert any(n.endswith("/placements.json") for n in names[0])
    assert (tmp_path / "one.zip").read_bytes() == (tmp_path / "two.zip").read_bytes()
