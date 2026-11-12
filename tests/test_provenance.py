"""Export provenance, path redaction and reproducibility (issue #10).

Guarantee under test: with the same tool and backend versions, input bytes
and filename, explicit options, output filename and --redact-paths, a
standalone HTML and a lazy HTML plus every chunk are byte-for-byte
identical regardless of directory or working directory. Without
--redact-paths they differ only in the embedded path."""

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from gdml_inspector import __version__, cli, lazy

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"
OPTIONS = ["--backend", "python", "--quality", "fast"]


def export(directory, *extra, lazy_mode=False, name="viewer.html", gdml=None):
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "detector.gdml"
    if gdml is None:
        shutil.copy(EXAMPLE, source)
    else:
        source.write_bytes(gdml)
    output = directory / name
    args = [*(["--lazy"] if lazy_mode else []), str(source), *OPTIONS, *extra]
    assert cli.main([*args, "--out", str(output)]) == 0
    return source, output


def embedded_scene(html_path):
    document = html_path.read_text(encoding="utf-8")
    text = document.split("const SCENE = ", 1)[1].split(";\nconst HIERARCHY =", 1)[0]
    return json.loads(text)


def chunk_files(html_path):
    manifest = lazy.read_existing_manifest(html_path)
    return {
        c["url"]: (html_path.parent / c["url"]).read_bytes() for c in manifest["chunks"]
    }


def test_standalone_provenance_block(tmp_path):
    source, output = export(tmp_path / "a")
    meta = embedded_scene(output)["meta"]
    assert meta["schemaVersion"] == 2
    provenance = meta["provenance"]
    assert provenance["tool"] == {"name": "gdml-inspector", "version": __version__}
    assert provenance["backend"] == {"name": "python", "version": __version__}
    assert provenance["input"]["file"] == "detector.gdml"
    assert (
        provenance["input"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert provenance["input"]["path"] == str(source)
    assert provenance["settings"] == {
        "quality": "fast",
        "includeContainers": False,
        "includeWorld": False,
        "maxObjects": 100_000,
        "maxVertices": 5_000_000,
    }
    assert provenance["units"] == {"length": "mm", "angle": "rad"}


def test_lazy_provenance_block_and_chunk_digests(tmp_path):
    source, output = export(tmp_path / "a", lazy_mode=True)
    manifest = lazy.read_existing_manifest(output)
    assert manifest["schemaVersion"] == 2
    assert (
        manifest["provenance"]["input"]["sha256"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert manifest["provenance"]["settings"]["chunkVertices"] == 500_000
    for chunk in manifest["chunks"]:
        data = (output.parent / chunk["url"]).read_bytes()
        assert chunk["sha256"] == hashlib.sha256(data).hexdigest()
        assert chunk["bytes"] == len(data)


@pytest.mark.parametrize("lazy_mode", [False, True])
def test_redaction_removes_the_path_but_keeps_filename_and_digest(tmp_path, lazy_mode):
    directory = tmp_path / "secret-project-dir"
    source, output = export(directory, "--redact-paths", lazy_mode=lazy_mode)
    document = output.read_text(encoding="utf-8")
    assert str(directory) not in document
    assert "secret-project-dir" not in document
    if lazy_mode:
        meta = lazy.read_existing_manifest(output)
    else:
        meta = embedded_scene(output)["meta"]
    assert meta["source"] == "detector.gdml" and meta["sourceFile"] == "detector.gdml"
    assert "path" not in meta["provenance"]["input"]
    assert (
        meta["provenance"]["input"]["sha256"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )


def test_default_still_embeds_the_path(tmp_path):
    source, output = export(tmp_path / "keep")
    assert embedded_scene(output)["meta"]["source"] == str(source)


def test_standalone_is_byte_identical_across_directories_and_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, first = export(tmp_path / "one" / "deeper", "--redact-paths")
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    _, second = export(tmp_path / "two", "--redact-paths")
    assert first.read_bytes() == second.read_bytes()


def test_lazy_is_byte_identical_including_every_chunk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, first = export(tmp_path / "one", "--redact-paths", lazy_mode=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    _, second = export(tmp_path / "x" / "two", "--redact-paths", lazy_mode=True)
    assert first.read_bytes() == second.read_bytes()
    assert chunk_files(first) == chunk_files(second)
    assert chunk_files(first)


@pytest.mark.parametrize("lazy_mode", [False, True])
def test_without_redaction_outputs_differ_only_in_the_path(tmp_path, lazy_mode):
    source_a, first = export(tmp_path / "one", lazy_mode=lazy_mode)
    source_b, second = export(tmp_path / "two", lazy_mode=lazy_mode)
    normalised = [
        output.read_text(encoding="utf-8").replace(str(source), "<SOURCE>")
        for source, output in ((source_a, first), (source_b, second))
    ]
    assert normalised[0] == normalised[1]
    assert first.read_bytes() != second.read_bytes()
    if lazy_mode:
        assert chunk_files(first) == chunk_files(second)


def test_explicit_options_change_the_output(tmp_path):
    _, fast = export(tmp_path / "a", "--redact-paths")
    directory = tmp_path / "b"
    directory.mkdir()
    shutil.copy(EXAMPLE, directory / "detector.gdml")
    other = directory / "viewer.html"
    args = [
        str(directory / "detector.gdml"),
        "--backend",
        "python",
        "--quality",
        "high",
    ]
    assert cli.main([*args, "--redact-paths", "--out", str(other)]) == 0
    assert fast.read_bytes() != other.read_bytes()


def test_no_network_url_is_embedded(tmp_path):
    for lazy_mode in (False, True):
        _, output = export(tmp_path / str(lazy_mode), lazy_mode=lazy_mode)
        assert re.findall(r"https?://", output.read_text(encoding="utf-8")) == []


def test_bundle_zip_is_deterministic_and_round_trips(tmp_path):
    first_source, first = export(
        tmp_path / "one",
        "--redact-paths",
        "--bundle",
        str(tmp_path / "one.zip"),
        lazy_mode=True,
    )
    export(
        tmp_path / "two",
        "--redact-paths",
        "--bundle",
        str(tmp_path / "two.zip"),
        lazy_mode=True,
    )
    assert (tmp_path / "one.zip").read_bytes() == (tmp_path / "two.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "one.zip") as archive:
        names = archive.namelist()
        assert names[0] == "viewer.html"
        assert names[1:] == sorted(names[1:])
        assert all(
            info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
        )
        archive.extractall(tmp_path / "unzipped")
    assert (tmp_path / "unzipped" / "viewer.html").read_bytes() == first.read_bytes()
    unzipped = tmp_path / "unzipped" / "viewer.html"
    assert chunk_files(unzipped) == chunk_files(first)
