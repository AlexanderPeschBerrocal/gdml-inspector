"""--reuse-assets must refuse anything that could show stale or damaged
geometry, with a message naming the cause (issue #10), and still allow the
legitimate cheap rewrite."""

import json
import shutil
from pathlib import Path

import pytest

from gdml_inspector import cli, lazy

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"
BUILD = ["--backend", "python", "--quality", "fast"]


@pytest.fixture
def built(tmp_path):
    source = tmp_path / "detector.gdml"
    shutil.copy(EXAMPLE, source)
    output = tmp_path / "viewer.html"
    assert cli.main(["--lazy", str(source), *BUILD, "--out", str(output)]) == 0
    return source, output


def reuse(source, output, *extra):
    return cli.main(
        ["--lazy", str(source), "--out", str(output), "--reuse-assets", *extra]
    )


def chunk_paths(output):
    manifest = lazy.read_existing_manifest(output)
    return [output.parent / c["url"] for c in manifest["chunks"]]


def test_a_plain_rewrite_still_works_and_leaves_chunks_untouched(built):
    source, output = built
    before = {p: p.read_bytes() for p in chunk_paths(output)}
    assert reuse(source, output, "--default-load", "all") == 0
    assert lazy.read_existing_manifest(output)["defaultLoad"] == ["all"]
    assert {p: p.read_bytes() for p in chunk_paths(output)} == before


def test_matching_explicit_options_are_accepted(built):
    source, output = built
    assert (
        reuse(
            source,
            output,
            "--quality",
            "fast",
            "--backend",
            "python",
            "--chunk-vertices",
            "500000",
        )
        == 0
    )


def test_an_edited_source_with_the_same_filename_is_refused(built, capsys):
    source, output = built
    source.write_text(source.read_text() + "\n<!-- edited -->\n")
    assert reuse(source, output) == 4
    assert "has changed since" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra, flag",
    [
        (["--quality", "high"], "--quality"),
        (["--include-containers"], "--include-containers"),
        (["--include-world"], "--include-world"),
        (["--max-objects", "7"], "--max-objects"),
        (["--max-vertices", "7"], "--max-vertices"),
        (["--chunk-vertices", "99"], "--chunk-vertices"),
        (["--backend", "pyg4ometry"], "--backend"),
    ],
)
def test_a_changed_explicit_option_is_refused(built, capsys, extra, flag):
    source, output = built
    assert reuse(source, output, *extra) == 4
    error = capsys.readouterr().err
    assert flag in error and "differs" in error and "Rebuild" in error


def test_a_missing_chunk_is_refused(built, capsys):
    source, output = built
    chunk_paths(output)[0].unlink()
    assert reuse(source, output) == 4
    error = capsys.readouterr().err
    assert "missing" in error and "Rebuild" in error


def test_a_truncated_chunk_is_refused(built, capsys):
    source, output = built
    path = chunk_paths(output)[0]
    path.write_bytes(path.read_bytes()[:-4])
    assert reuse(source, output) == 4
    assert "bytes, expected" in capsys.readouterr().err


def test_a_bit_flipped_chunk_of_the_same_size_is_refused(built, capsys):
    source, output = built
    path = chunk_paths(output)[0]
    data = bytearray(path.read_bytes())
    data[-1] ^= 0x01
    path.write_bytes(bytes(data))
    assert reuse(source, output) == 4
    assert "sha256 does not match" in capsys.readouterr().err


def test_a_pre_digest_manifest_is_refused_with_guidance(built, capsys):
    source, output = built
    manifest = lazy.read_existing_manifest(output)
    del manifest["schemaVersion"], manifest["provenance"]
    lazy.write_html(manifest, output)
    assert reuse(source, output) == 4
    error = capsys.readouterr().err
    assert "predates schema 2" in error and "without --reuse-assets" in error


def test_a_newer_schema_is_refused_with_guidance(built, capsys):
    source, output = built
    manifest = lazy.read_existing_manifest(output)
    manifest["schemaVersion"] = 99
    lazy.write_html(manifest, output)
    assert reuse(source, output) == 4
    assert "newer gdml-inspector" in capsys.readouterr().err


def test_reuse_can_apply_the_redaction_policy(built):
    source, output = built
    assert str(source) in output.read_text(encoding="utf-8")
    assert reuse(source, output, "--redact-paths") == 0
    document = output.read_text(encoding="utf-8")
    assert str(source.parent) not in document
    manifest = lazy.read_existing_manifest(output)
    assert manifest["source"] == "detector.gdml"
    assert "path" not in manifest["provenance"]["input"]
    assert manifest["provenance"]["input"]["sha256"]


def test_chunk_urls_are_percent_encoded_and_reuse_follows_them(tmp_path):
    source = tmp_path / "detector.gdml"
    shutil.copy(EXAMPLE, source)
    output = tmp_path / "my view #1 %ünï.html"
    assert cli.main(["--lazy", str(source), *BUILD, "--out", str(output)]) == 0
    manifest = lazy.read_existing_manifest(output)
    for chunk in manifest["chunks"]:
        assert "%20" in chunk["url"] and "%23" in chunk["url"] and "%25" in chunk["url"]
        assert " " not in chunk["url"] and "#" not in chunk["url"]
    assert reuse(source, output, "--default-load", "all") == 0
    json.dumps(manifest)
