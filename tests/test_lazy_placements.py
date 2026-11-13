"""The lazy export's placements metadata (a compact index plus per-chunk detail
shards) and per-vertex object ids: the foundation for hierarchy, picking and
filters in lazy mode.

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


def _decoded(url):
    from urllib.parse import unquote

    return unquote(url)


def read_json(output, entry):
    return json.loads((output.parent / _decoded(entry["url"])).read_text())


def expand_placements(output, manifest):
    """Rebuild the full per-node and per-object records from the index and
    shards, following only the documented layout (independent of the writer)."""
    entry = manifest["placements"]
    index = read_json(output, entry)
    assert index["format"] == 2
    strings = index["strings"]
    n, o = index["nodes"], index["objects"]
    nodes = []
    for i in range(len(n["name"])):
        parent = n["parent"][i]
        path = (
            n["name"][i] if parent < 0 else nodes[parent]["path"] + "/" + n["name"][i]
        )
        name = path.rsplit("/", 1)[-1]
        node = {
            "path": path,
            "parentPath": None if parent < 0 else nodes[parent]["path"],
            "name": name,
            "semanticName": name if n["semName"][i] < 0 else strings[n["semName"][i]],
            "volume": strings[n["volume"][i]],
            "semanticVolume": strings[n["semVolume"][i]],
            "isAssembly": bool(n["flags"][i] & 2),
            "rendered": bool(n["flags"][i] & 1),
        }
        if n["object"][i] >= 0:
            node["objectId"] = n["object"][i]
        nodes.append(node)
    objects = []
    for j in range(len(o["node"])):
        node = nodes[o["node"][j]]
        objects.append(
            {
                "id": j,
                "name": node["name"],
                "semanticName": node["semanticName"],
                "volume": node["volume"],
                "semanticVolume": node["semanticVolume"],
                "material": strings[o["material"][j]],
                "solid": strings[o["solid"][j]],
                "solidType": strings[o["solidType"][j]],
                "path": node["path"],
                "chunk": manifest["chunks"][o["chunk"][j]]["baseKey"],
                "chunkIndex": o["chunk"][j],
            }
        )
    for shard_entry in entry["shards"]:
        shard = read_json(output, shard_entry)
        for k, object_id in enumerate(shard["objects"]):
            objects[object_id]["bbox"] = shard["bbox"][k]
            node = nodes[shard["nodes"][k]]
            if shard["local"][k] is not None:
                node["localMatrix"] = shard["local"][k]
            node["globalMatrix"] = shard["global"][k]
    containers = read_json(output, entry["containers"])
    for k, node_index in enumerate(containers["nodes"]):
        if containers["local"][k] is not None:
            nodes[node_index]["localMatrix"] = containers["local"][k]
    return {"nodes": nodes, "objects": objects, "solids": index["solids"]}


@pytest.fixture
def lazy_export(tmp_path):
    source = tmp_path / "detector.gdml"
    shutil.copy(EXAMPLE, source)
    output = tmp_path / "viewer.html"
    assert cli.main(["--lazy", str(source), *BUILD, "--out", str(output)]) == 0
    manifest = lazy.read_existing_manifest(output)
    return source, output, manifest, expand_placements(output, manifest)


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


def test_every_shard_describes_exactly_the_objects_of_its_chunk(lazy_export):
    _source, output, manifest, placements = lazy_export
    entry = manifest["placements"]
    assert len(entry["shards"]) == len(manifest["chunks"])
    for index, (chunk, shard_entry) in enumerate(
        zip(manifest["chunks"], entry["shards"], strict=True)
    ):
        shard = read_json(output, shard_entry)
        in_chunk = [o["id"] for o in placements["objects"] if o["chunkIndex"] == index]
        assert shard["objects"] == in_chunk
        assert (
            len(shard["bbox"])
            == len(shard["local"])
            == len(shard["global"])
            == len(in_chunk)
        )
        assert len(shard["objects"]) == chunk["objects"]
    containers = read_json(output, entry["containers"])
    rendered = {n["path"] for n in placements["nodes"] if "objectId" in n}
    assert {placements["nodes"][i]["path"] for i in containers["nodes"]}.isdisjoint(
        rendered
    )
    assert len(containers["nodes"]) + manifest["objects"] == len(placements["nodes"])


def test_the_index_is_far_smaller_than_the_single_file_form(tmp_path, repeated_boxes):
    path = repeated_boxes(count=300)
    output = tmp_path / "v.html"
    assert cli.main(["--lazy", str(path), *BUILD, "--out", str(output)]) == 0
    manifest = lazy.read_existing_manifest(output)
    placements = expand_placements(output, manifest)
    single_file = len(json.dumps(placements, separators=(",", ":")))
    index = manifest["placements"]["bytes"]
    shards = sum(s["bytes"] for s in manifest["placements"]["shards"])
    assert index < 0.25 * single_file
    assert index + shards < single_file  # the whole is not larger either


def test_awkward_placement_names_round_trip_through_the_index(tmp_path, write_gdml):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf_0x1a2b"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="a/b"><volumeref ref="leaf_0x1a2b"/></physvol>
  <physvol name="Ünï_0x3c4d"><volumeref ref="leaf_0x1a2b"/>
    <position x="10" unit="mm"/></physvol>
</volume>""",
    )
    output = tmp_path / "v.html"
    assert cli.main(["--lazy", str(path), *BUILD, "--out", str(output)]) == 0
    manifest = lazy.read_existing_manifest(output)
    mine = expand_placements(output, manifest)
    reference = lazy.LazyGDMLSceneBuilder(
        path,
        quality="fast",
        include_containers=False,
        include_world=False,
        max_objects=1000,
        max_vertices=10**7,
        chunk_vertices=500_000,
    )
    reference.build_chunks()
    assert mine["nodes"] == reference.hierarchy_nodes()
    by_path = {n["path"]: n for n in mine["nodes"]}
    assert by_path["world/a/b"]["name"] == "b"  # the name is the last path segment
    assert by_path["world/Ünï_0x3c4d"]["semanticName"] == "Ünï_"


def test_reuse_verifies_every_shard_and_the_containers_shard(lazy_export, capsys):
    source, output, manifest, _placements = lazy_export
    args = ["--lazy", str(source), "--out", str(output), "--reuse-assets"]
    entry = manifest["placements"]
    for victim in (entry["shards"][0], entry["containers"]):
        path = output.parent / _decoded(victim["url"])
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        assert cli.main(args) == 4
        assert victim["url"].rsplit("/", 1)[-1] in capsys.readouterr().err
        path.write_bytes(original)
        assert cli.main(args) == 0
    (output.parent / _decoded(entry["shards"][-1]["url"])).unlink()
    assert cli.main(args) == 4
    assert "missing" in capsys.readouterr().err


def test_an_export_with_the_single_file_placements_is_still_reusable(
    lazy_export,
):
    source, output, manifest, _placements = lazy_export
    for key in ("shards", "containers", "format"):
        del manifest["placements"][key]
    lazy.write_html(manifest, output)
    assert (
        cli.main(["--lazy", str(source), "--out", str(output), "--reuse-assets"]) == 0
    )


def test_the_bundle_contains_every_shard(lazy_export, tmp_path):
    source, output, manifest, _placements = lazy_export
    archive = tmp_path / "b.zip"
    assert (
        cli.main(
            [
                "--lazy",
                str(source),
                "--out",
                str(output),
                "--reuse-assets",
                "--bundle",
                str(archive),
            ]
        )
        == 0
    )
    names = set(zipfile.ZipFile(archive).namelist())
    entry = manifest["placements"]
    for asset in [*entry["shards"], entry["containers"]]:
        assert _decoded(asset["url"]) in names


def test_the_index_and_shards_never_contain_an_absolute_path(lazy_export):
    source, output, manifest, _placements = lazy_export
    entry = manifest["placements"]
    for asset in [entry, *entry["shards"], entry["containers"]]:
        assert (
            str(source.parent)
            not in (output.parent / _decoded(asset["url"])).read_text()
        )
