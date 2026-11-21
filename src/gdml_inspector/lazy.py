#!/usr/bin/env python3
"""Chunked GDML export, layer grouping, and lazy-viewer command-line interface."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import shutil
import struct
import sys
import tempfile
import zipfile
from array import array
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from . import __version__
from . import geometry as base
from ._assets import read_asset, render_template
from .grouping import (
    GROUPING_STRATEGIES,
    Grouping,
    GroupingError,
    canonical_material,
    classify_chunk,  # noqa: F401  (re-exported: it used to live here)
)
from .hierarchy import (
    ASSEMBLY,
    HAS_GLOBAL,
    HAS_LOCAL,
    RENDERED,
    HierarchyColumns,
    HierarchyMixin,
    HierarchyNodeDict,
)
from .pyg4ometry_backend import Pyg4ometrySceneBuilder
from .server import serve_file
from .viewstate import load_view_state, stale_warning

MAGIC = b"GDMLCHN1"
# 1: positions + colours. 2: adds one uint32 per vertex (objectId + 1, 0 = no
# object) in the id section the v1 header already reserved. The viewer reads
# both; the writer emits 2 whenever a chunk carries ids.
FORMAT_VERSION = 2
# 3: instanced. One copy of each distinct mesh (local coordinates, one colour)
# plus, per placement, a 3x4 matrix and its object id; the viewer draws each
# mesh once per instance. Written only for a chunk where that is clearly
# smaller (--instancing auto) or always (--instancing always); otherwise the
# baked v2 layout above is used, so a chunk of one-off shapes costs nothing.
INSTANCED_VERSION = 3
HEADER = struct.Struct("<8s6I")
# magic, version, meshes, instances, mesh vertices, positions offset, matrices
# offset, ids offset, end offset; the mesh table follows the header directly.
INSTANCED_HEADER = struct.Struct("<8s8I")
# first vertex, vertex count, rgba, first instance, instance count
INSTANCED_MESH = struct.Struct("<II4BII")
# Auto-instancing keeps a chunk instanced only if it is at most this fraction
# of the baked size, so a layer with little repetition stays on the simple path.
INSTANCING_RATIO = 0.75
INSTANCING_MODES = ("auto", "off", "always")
PLACEMENTS_NAME = "placements.json"
# 2: a compact, columnar index (hierarchy + the fields filters and the tree
# need) plus per-chunk detail shards (bounding boxes and transforms), so a
# viewer fetches only what it uses. The viewer still reads the original
# single-file form (no `format` in the manifest entry).
PLACEMENTS_FORMAT = 2
SHARD_NAME = "placements.shard-{index:04d}.json"
CONTAINERS_SHARD_NAME = "placements.containers.json"
assert array("I").itemsize == 4, "the chunk id section needs 32-bit unsigned items"
SUBSYSTEM_ORDER = {
    "Tracker": 0,
    "ECAL": 1,
    "Calo support": 2,
    "HCAL": 3,
    "Other": 4,
}


def safe_name(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_.")
    return text[:120] or "chunk"


def bbox_union(
    target_min: list[float],
    target_max: list[float],
    lo: Sequence[float],
    hi: Sequence[float],
) -> None:
    for axis in range(3):
        target_min[axis] = min(target_min[axis], float(lo[axis]))
        target_max[axis] = max(target_max[axis], float(hi[axis]))


@dataclass
class ObjectColumns:
    """What the placements asset needs to know about each accepted object, as
    columns (id == position): its path, material, solid and solid type (shared
    string references), tessellated bounds in one flat array and the chunk part
    that holds it. A dict per object cost about 0.9 KB; this costs roughly 100
    bytes."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.material: list[str] = []
        self.solid: list[str] = []
        self.solid_type: list[str] = []
        self.bbox = array("d")
        self.chunk = array("i")  # position in LazyBuilderMixin.chunk_keys

    def __len__(self) -> int:
        return len(self.paths)

    def append(
        self,
        path: str,
        material: str,
        solid: str,
        solid_type: str,
        lo: Sequence[float],
        hi: Sequence[float],
        chunk: int,
    ) -> None:
        self.paths.append(path)
        self.material.append(material)
        self.solid.append(solid)
        self.solid_type.append(solid_type)
        self.bbox.extend(lo)
        self.bbox.extend(hi)
        self.chunk.append(chunk)


@dataclass
class FrozenChunk:
    """What is kept of a chunk part once it has been written to disk and its
    arrays released: enough to describe it in the manifest."""

    path: Path
    vertices: int
    size: int
    sha256: str
    layout: str
    meshes: int | None


@dataclass
class Chunk:
    subsystem: str
    layer: str
    part: int
    positions: array = field(default_factory=lambda: array("f"))
    colors: array = field(default_factory=lambda: array("B"))
    ids: array = field(default_factory=lambda: array("I"))
    # Instanced description of the same objects (see write_instanced_chunk):
    # one entry per distinct (solid, colour) in `mesh_keys`, and per object its
    # mesh index, id + 1 and 3x4 matrix. Kept beside the baked arrays so the
    # layout is chosen when the chunk is written.
    mesh_keys: dict[tuple[str, tuple[int, ...]], int] = field(default_factory=dict)
    mesh_tris: list[list] = field(default_factory=list)
    mesh_rgba: list[tuple[int, ...]] = field(default_factory=list)
    instance_mesh: array = field(default_factory=lambda: array("I"))
    instance_ids: array = field(default_factory=lambda: array("I"))
    instance_matrices: array = field(default_factory=lambda: array("f"))
    object_count: int = 0
    bbox_min: list[float] = field(default_factory=lambda: [float("inf")] * 3)
    bbox_max: list[float] = field(default_factory=lambda: [float("-inf")] * 3)
    frozen: FrozenChunk | None = None

    @property
    def vertex_count(self) -> int:
        if self.frozen is not None:
            return self.frozen.vertices
        return len(self.positions) // 3

    @property
    def triangle_count(self) -> int:
        return self.vertex_count // 3

    @property
    def base_key(self) -> str:
        return f"{self.subsystem}\x1f{self.layer}"

    @property
    def key(self) -> str:
        return f"{self.base_key}\x1f{self.part}"


class LazyBuilderMixin:
    """Mixin that changes the original builder from one scene to layer chunks.

    Supplied by GDMLSceneBuilder/Pyg4ometrySceneBuilder in the final,
    multiply-inherited class (LazyGDMLSceneBuilder, etc.) -- declared here,
    with no value, only so mypy can check this mixin's own method bodies
    against its actual contract rather than reporting every access as
    undefined."""

    scene: base.Scene
    max_objects: int
    max_vertices: int
    gdml: Path
    backend_name: str
    quality: str
    world: str
    volumes: dict[str, base.VolumeDef]
    solid_mesh: Callable[..., tuple[list[base.Triangle], str]]
    parse: Callable[[], None]
    traverse: Callable[..., None]
    provenance: Callable[..., dict[str, object]]
    source_label: Callable[[], str]
    solids_table: Callable[[], dict[str, base.SolidInfo]]
    hierarchy: HierarchyColumns
    hierarchy_nodes: Callable[[], list[HierarchyNodeDict]]
    _on_object_added: Callable[..., None]
    _admit_object: Callable[[str, int, int, int], bool]

    def __init__(
        self,
        *args: object,
        chunk_vertices: int,
        instancing: str = "auto",
        memory_budget: int = 512,
        grouping: Grouping | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.grouping = grouping or Grouping()
        self.memory_budget = max(0, int(memory_budget))  # MiB; 0 = unlimited
        # Where closed parts are written while the scene is still being built
        # (set by the exporter; None keeps every part in memory until the end).
        self.spill_dir: Path | None = None
        self.open_bytes = 0  # array memory held by parts not yet written
        self._spills = 0
        if instancing not in INSTANCING_MODES:
            raise ValueError(f"instancing must be one of {INSTANCING_MODES}")
        self.chunk_vertices = max(3, int(chunk_vertices))
        self.instancing = instancing
        self.chunks: OrderedDict[str, Chunk] = OrderedDict()
        self.chunk_parts: dict[str, list[str]] = {}
        self.total_vertices = 0
        self.total_objects = 0
        self.material_counts: Counter[str] = Counter()
        # One entry per accepted object, in id order: what the placements asset
        # publishes so a viewer knows every object, loaded or not, without
        # fetching geometry.
        self.objects = ObjectColumns()
        self.chunk_keys: list[str] = []  # chunk part keys by creation order
        self._chunk_ids: dict[str, int] = {}
        self.global_bbox_min = [float("inf")] * 3
        self.global_bbox_max = [float("-inf")] * 3

    def _chunk_for(self, subsystem: str, layer: str, incoming_vertices: int) -> Chunk:
        base_key = f"{subsystem}\x1f{layer}"
        keys = self.chunk_parts.setdefault(base_key, [])
        if keys:
            candidate = self.chunks[keys[-1]]
            if (
                candidate.frozen is None
                and candidate.vertex_count + incoming_vertices <= self.chunk_vertices
            ):
                return candidate
            # This part is full (or was released early): it never grows again.
            self._close_part(candidate)
        part = len(keys) + 1
        chunk = Chunk(subsystem=subsystem, layer=layer, part=part)
        self.chunks[chunk.key] = chunk
        self._chunk_ids[chunk.key] = len(self.chunk_keys)
        self.chunk_keys.append(chunk.key)
        keys.append(chunk.key)
        return chunk

    def _close_part(self, chunk: Chunk) -> None:
        """Write a part that will not grow any more to the spill directory and
        release its arrays, so memory holds only the parts still being filled.
        The bytes are exactly what the final write would produce."""
        if chunk.frozen is not None or self.spill_dir is None:
            return
        data, layout, meshes = encode_chunk(chunk, self.instancing)
        self._spills += 1
        path = self.spill_dir / f"{self._spills:08d}.spill"
        path.write_bytes(data)
        freed = chunk_memory(chunk)
        vertices = chunk.vertex_count
        chunk.frozen = FrozenChunk(
            path, vertices, len(data), hashlib.sha256(data).hexdigest(), layout, meshes
        )
        chunk.positions = array("f")
        chunk.colors = array("B")
        chunk.ids = array("I")
        chunk.mesh_keys = {}
        chunk.mesh_tris = []
        chunk.mesh_rgba = []
        chunk.instance_mesh = array("I")
        chunk.instance_ids = array("I")
        chunk.instance_matrices = array("f")
        self.open_bytes -= freed

    def _enforce_memory_budget(self) -> None:
        """Past --memory-budget, write the largest still-open parts early. A
        layer then continues in a new part, so a budget that is hit changes
        where parts split (never what is drawn)."""
        limit = self.memory_budget * 1024 * 1024
        if not limit or self.spill_dir is None:
            return
        while self.open_bytes > limit:
            open_parts = [c for c in self.chunks.values() if c.frozen is None]
            if not open_parts:
                break
            self._close_part(max(open_parts, key=chunk_memory))

    def add_volume(self, volume: base.VolumeDef, matrix: base.Mat4, path: str) -> None:
        tris, solid_type = self.solid_mesh(volume.solid)
        if not tris:
            return

        positions = array("f")
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        radial_min = float("inf")
        radial_max = 0.0
        for tri in tris:
            for point in tri:
                transformed = base.transform_point(matrix, point)
                positions.extend(transformed)
                radius = math.hypot(transformed[0], transformed[1])
                radial_min = min(radial_min, radius)
                radial_max = max(radial_max, radius)
                for axis in range(3):
                    lo[axis] = min(lo[axis], transformed[axis])
                    hi[axis] = max(hi[axis], transformed[axis])

        vertex_count = len(positions) // 3
        if vertex_count == 0:
            return
        if not self._admit_object(
            path, vertex_count, self.total_objects, self.total_vertices
        ):
            return

        subsystem, layer = self.grouping.assign(
            volume, path, lo, hi, radial_min, radial_max
        )
        chunk = self._chunk_for(subsystem, layer, vertex_count)
        material = canonical_material(volume.material) or "(none)"
        rgba = base.color_for_material(volume.material)

        object_id = self.total_objects
        chunk.positions.extend(positions)
        chunk.colors.extend(array("B", rgba) * vertex_count)
        chunk.ids.extend(array("I", [object_id + 1]) * vertex_count)
        mesh_key = (volume.solid, tuple(rgba))
        mesh_index = chunk.mesh_keys.get(mesh_key)
        if mesh_index is None:
            mesh_index = len(chunk.mesh_tris)
            chunk.mesh_keys[mesh_key] = mesh_index
            chunk.mesh_tris.append(tris)
            chunk.mesh_rgba.append(tuple(rgba))
        chunk.instance_mesh.append(mesh_index)
        chunk.instance_ids.append(object_id + 1)
        chunk.instance_matrices.extend(
            array("f", [value for row in matrix[:3] for value in row])
        )
        chunk.object_count += 1
        self.open_bytes += vertex_count * (12 + 4 + 4) + (4 + 4 + 48)
        bbox_union(chunk.bbox_min, chunk.bbox_max, lo, hi)
        bbox_union(self.global_bbox_min, self.global_bbox_max, lo, hi)
        self.material_counts[material] += 1
        self.total_vertices += vertex_count
        self.total_objects += 1
        self.objects.append(
            path,
            volume.material or "(none)",
            volume.solid,
            solid_type,
            lo,
            hi,
            self._chunk_ids[chunk.key],
        )
        self._on_object_added(path, volume, matrix, object_id)
        self._enforce_memory_budget()

    def build_chunks(self) -> None:
        self.parse()
        world = self.volumes.get(self.world)
        if world is None:
            raise ValueError(f"World volume {self.world!r} was not found")
        self.traverse(world, base.identity(), self.world)
        if self.global_bbox_min[0] == float("inf"):
            self.global_bbox_min = [-1.0, -1.0, -1.0]
            self.global_bbox_max = [1.0, 1.0, 1.0]


class LazyGDMLSceneBuilder(LazyBuilderMixin, HierarchyMixin, base.GDMLSceneBuilder):
    pass


class LazyPyg4ometrySceneBuilder(
    LazyBuilderMixin, HierarchyMixin, Pyg4ometrySceneBuilder
):
    pass


def instanced_layout(chunk: Chunk) -> tuple[list[int], list[list[int]]]:
    """Meshes in first-use order and, per mesh, its instances (indices into
    the chunk's instance arrays) in object order. The writer groups instances
    by mesh so the viewer can draw each mesh with one instanced call."""
    members: list[list[int]] = [[] for _ in chunk.mesh_tris]
    for index, mesh in enumerate(chunk.instance_mesh):
        members[mesh].append(index)
    return list(range(len(chunk.mesh_tris))), members


def instanced_size(chunk: Chunk) -> int:
    """Bytes the chunk would take in the instanced layout."""
    mesh_vertices = sum(len(tris) * 3 for tris in chunk.mesh_tris)
    return (
        INSTANCED_HEADER.size
        + INSTANCED_MESH.size * len(chunk.mesh_tris)
        + 12 * mesh_vertices
        + (48 + 4) * len(chunk.instance_mesh)
    )


def baked_size(chunk: Chunk) -> int:
    return HEADER.size + chunk.vertex_count * (12 + 4 + 4)


def use_instancing(chunk: Chunk, mode: str) -> bool:
    """Whether ``chunk`` is written in the instanced layout under ``mode``."""
    if mode == "off" or not chunk.instance_mesh:
        return False
    if len(chunk.instance_mesh) != chunk.object_count:
        return False  # a hand-built chunk without a complete instance table
    if mode == "always":
        return True
    # auto: only where meshes really repeat and the saving is clear.
    return len(chunk.mesh_tris) < len(chunk.instance_mesh) and instanced_size(
        chunk
    ) <= INSTANCING_RATIO * baked_size(chunk)


def encode_instanced_chunk(chunk: Chunk) -> bytes:
    _meshes, members = instanced_layout(chunk)
    table = bytearray()
    positions = array("f")
    matrices = array("f")
    ids = array("I")
    first_instance = 0
    for mesh_index, indices in enumerate(members):
        flat = array("f")
        for tri in chunk.mesh_tris[mesh_index]:
            for point in tri:
                flat.extend(point)
        first_vertex = len(positions) // 3
        table += INSTANCED_MESH.pack(
            first_vertex,
            len(flat) // 3,
            *chunk.mesh_rgba[mesh_index],
            first_instance,
            len(indices),
        )
        positions.extend(flat)
        for index in indices:
            matrices.extend(chunk.instance_matrices[index * 12 : index * 12 + 12])
            ids.append(chunk.instance_ids[index])
        first_instance += len(indices)
    position_bytes = base.little_endian_bytes(positions)
    matrix_bytes = base.little_endian_bytes(matrices)
    id_bytes = base.little_endian_bytes(ids)
    position_offset = INSTANCED_HEADER.size + len(table)
    matrix_offset = position_offset + len(position_bytes)
    id_offset = matrix_offset + len(matrix_bytes)
    total_size = id_offset + len(id_bytes)
    header = INSTANCED_HEADER.pack(
        MAGIC,
        INSTANCED_VERSION,
        len(members),
        len(ids),
        len(positions) // 3,
        position_offset,
        matrix_offset,
        id_offset,
        total_size,
    )
    return header + bytes(table) + position_bytes + matrix_bytes + id_bytes


def chunk_memory(chunk: Chunk) -> int:
    """Bytes of array storage a part holds (zero once it is written)."""
    return sum(
        a.itemsize * len(a)
        for a in (
            chunk.positions,
            chunk.colors,
            chunk.ids,
            chunk.instance_mesh,
            chunk.instance_ids,
            chunk.instance_matrices,
        )
    )


def encode_chunk(chunk: Chunk, instancing: str) -> tuple[bytes, str, int | None]:
    """The chunk file's bytes, its layout name and, if instanced, its mesh count."""
    if use_instancing(chunk, instancing):
        return encode_instanced_chunk(chunk), "instanced", len(chunk.mesh_tris)
    positions = base.little_endian_bytes(chunk.positions)
    colors = base.little_endian_bytes(chunk.colors)
    has_ids = chunk.vertex_count > 0 and len(chunk.ids) == chunk.vertex_count
    ids = base.little_endian_bytes(chunk.ids) if has_ids else b""
    position_offset = HEADER.size
    color_offset = position_offset + len(positions)
    id_offset = color_offset + len(colors)
    total_size = id_offset + len(ids)
    header = HEADER.pack(
        MAGIC,
        FORMAT_VERSION if has_ids else 1,
        chunk.vertex_count,
        position_offset,
        color_offset,
        id_offset,
        total_size,
    )
    return header + positions + colors + ids, "baked", None


def write_chunk(chunk: Chunk, path: Path, instancing: str = "auto") -> int:
    data, _layout, _meshes = encode_chunk(chunk, instancing)
    path.write_bytes(data)
    return len(data)


def chunk_sort_key(chunk: Chunk) -> tuple[int, str, str, int]:
    """Known subsystems first in their fixed order; then every other subsystem
    by name, each one's layers kept together (sorting by layer alone interleaved
    custom subsystems that share the same rank)."""
    return (
        SUBSYSTEM_ORDER.get(chunk.subsystem, 99),
        chunk.subsystem,
        chunk.layer,
        chunk.part,
    )


def _asset_entry_bytes(asset_dir: Path, name: str, data: bytes) -> dict:
    (asset_dir / name).write_bytes(data)
    return {
        "url": quote(f"{asset_dir.name}/{name}"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def _asset_entry(asset_dir: Path, name: str, document: object) -> dict:
    data = json.dumps(document, separators=(",", ":")).encode("utf-8")
    return _asset_entry_bytes(asset_dir, name, data)


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _ints(values: Iterable[int]) -> str:
    """A JSON array of integers without building a list of int objects."""
    return "[" + ",".join(map(str, values)) + "]"


def write_placements(
    builder: LazyBuilderMixin, asset_dir: Path, chunk_index: dict[str, int]
) -> dict:
    """Every object's identity and metadata, split so each consumer fetches
    only what it needs. Entry ``objects[i]`` is object id ``i``, the same id
    stored per vertex in the chunks.

    *Index* (``placements.json``, columnar): the placement tree and, per
    object, the identifying fields filters and the hierarchy use (names,
    volume, material, solid, owning chunk). Repeated strings live once in a
    shared table. *Shards*: per chunk, the details only an inspector or a
    "focus" needs (bounding boxes, local and global transforms), plus one for
    the non-rendered containers. Their digests are in the manifest.

    Reads the builder's columns directly and writes the index as text, so no
    per-placement dict or list is built."""
    h = builder.hierarchy
    objects = builder.objects
    strings: dict[str, int] = {}

    def intern(text: str) -> int:
        return strings.setdefault(text, len(strings))

    names: list[str] = []  # the path below the parent (the whole path for the root)
    sem_names = array("i")  # string index, or -1 when the same as the last segment
    volumes = array("i")
    sem_volumes = array("i")
    flags = array("i")  # 1 rendered, 2 assembly
    for i in range(len(h)):
        names.append(h.segment(i))
        name = h.name(i)
        semantic = base.semantic_name(name)
        sem_names.append(-1 if semantic == name else intern(semantic))
        volume = h.volume_name(i)
        volumes.append(intern(volume))
        sem_volumes.append(intern(base.semantic_name(volume)))
        flags.append(h.flags[i] & (RENDERED | ASSEMBLY))

    remap = [chunk_index[key] for key in builder.chunk_keys]
    object_node = array("i")
    material = array("i")
    solid = array("i")
    solid_type = array("i")
    object_chunk = array("i")
    by_chunk: list[array] = [array("I") for _ in range(len(chunk_index))]
    for j in range(len(objects)):
        index = remap[objects.chunk[j]]
        object_node.append(h.index[objects.paths[j]])
        material.append(intern(objects.material[j]))
        solid.append(intern(objects.solid[j]))
        solid_type.append(intern(objects.solid_type[j]))
        object_chunk.append(index)
        by_chunk[index].append(j)

    shards: list[dict] = []
    for index in range(len(chunk_index)):
        ids = by_chunk[index]
        members = [object_node[j] for j in ids]
        shards.append(
            _asset_entry(
                asset_dir,
                SHARD_NAME.format(index=index),
                {
                    "objects": list(ids),
                    "bbox": [list(objects.bbox[j * 6 : j * 6 + 6]) for j in ids],
                    "nodes": members,
                    "local": [
                        h.matrix(h.local, n) if h.flags[n] & HAS_LOCAL else None
                        for n in members
                    ],
                    "global": [
                        h.matrix(h.global_, n) if h.flags[n] & HAS_GLOBAL else None
                        for n in members
                    ],
                },
            )
        )
    containers = [i for i in range(len(h)) if h.object_id[i] < 0]
    containers_entry = _asset_entry(
        asset_dir,
        CONTAINERS_SHARD_NAME,
        {
            "nodes": containers,
            "local": [
                h.matrix(h.local, i) if h.flags[i] & HAS_LOCAL else None
                for i in containers
            ],
        },
    )

    text = (
        f'{{"format":{PLACEMENTS_FORMAT},"schemaVersion":{base.SCHEMA_VERSION},'
        f'"strings":{_json(list(strings))},'
        f'"nodes":{{"name":{_json(names)},"parent":{_ints(h.parent)},'
        f'"semName":{_ints(sem_names)},"volume":{_ints(volumes)},'
        f'"semVolume":{_ints(sem_volumes)},"flags":{_ints(flags)},'
        f'"object":{_ints(h.object_id)}}},'
        f'"objects":{{"node":{_ints(object_node)},"material":{_ints(material)},'
        f'"solid":{_ints(solid)},"solidType":{_ints(solid_type)},'
        f'"chunk":{_ints(object_chunk)}}},'
        f'"solids":{_json(builder.solids_table())}}}'
    )
    index_entry = _asset_entry_bytes(asset_dir, PLACEMENTS_NAME, text.encode("utf-8"))
    return {
        **index_entry,
        "format": PLACEMENTS_FORMAT,
        "nodes": len(h),
        "objects": len(objects),
        "shards": shards,
        "containers": containers_entry,
    }


def placement_assets(manifest: dict) -> list[dict]:
    """Every file the placements metadata consists of (index, shards, the
    containers shard) as manifest-style entries; the legacy single file is one."""
    entry = manifest["placements"]
    return [
        {k: entry[k] for k in ("url", "sha256", "bytes")},
        *entry.get("shards", []),
        *([entry["containers"]] if "containers" in entry else []),
    ]


def write_assets(
    builder: LazyBuilderMixin,
    html_path: Path,
    default_load: Sequence[str],
) -> dict:
    asset_dir = html_path.parent / f"{html_path.stem}.assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    chunk_entries = []
    used_names: Counter[str] = Counter()
    for chunk in sorted(builder.chunks.values(), key=chunk_sort_key):
        stem = safe_name(f"{chunk.subsystem}_{chunk.layer}")
        used_names[stem] += 1
        suffix = used_names[stem]
        file_stem = f"{stem}.part{suffix:03d}"
        binary_name = f"{file_stem}.bin"
        frozen = chunk.frozen
        if frozen is not None:
            # Already written while the scene was being built: move into place.
            frozen.path.replace(asset_dir / binary_name)
            instanced = frozen.layout == "instanced"
            byte_size, digest, meshes = frozen.size, frozen.sha256, frozen.meshes
        else:
            data, layout, meshes = encode_chunk(chunk, builder.instancing)
            (asset_dir / binary_name).write_bytes(data)
            instanced = layout == "instanced"
            byte_size, digest = len(data), hashlib.sha256(data).hexdigest()
        chunk_entries.append(
            {
                "key": chunk.key,
                "baseKey": chunk.base_key,
                "subsystem": chunk.subsystem,
                "layer": chunk.layer,
                "part": chunk.part,
                # Percent-encoded per segment so the bundle loads from any
                # path or URL, including spaces, Unicode, '%' and '#'.
                "url": quote(f"{asset_dir.name}/{binary_name}"),
                "sha256": digest,
                "objects": chunk.object_count,
                # Logical size: what the chunk draws, whichever way it is stored.
                "vertices": chunk.vertex_count,
                "triangles": chunk.triangle_count,
                "layout": "instanced" if instanced else "baked",
                "meshes": meshes if instanced else None,
                "bytes": byte_size,
                "bbox": [*chunk.bbox_min, *chunk.bbox_max],
            }
        )

    chunk_index = {str(entry["key"]): i for i, entry in enumerate(chunk_entries)}
    placements = write_placements(builder, asset_dir, chunk_index)

    material_colors = {
        name: list(base.color_for_material(name)) for name in builder.material_counts
    }
    return {
        "format": "gdml-lazy-viewer-v1",
        "schemaVersion": base.SCHEMA_VERSION,
        "chunkFormat": INSTANCED_VERSION
        if any(c["layout"] == "instanced" for c in chunk_entries)
        else FORMAT_VERSION,
        "placements": placements,
        "provenance": builder.provenance(
            chunkVertices=builder.chunk_vertices,
            instancing=builder.instancing,
            memoryBudgetMiB=builder.memory_budget,
            grouping=builder.grouping.describe(),
        ),
        "source": builder.source_label(),
        "sourceFile": builder.gdml.name,
        "backend": builder.backend_name,
        "quality": builder.quality,
        "world": builder.world,
        "objects": builder.total_objects,
        "vertices": builder.total_vertices,
        "triangles": builder.total_vertices // 3,
        "bbox": [*builder.global_bbox_min, *builder.global_bbox_max],
        "chunks": chunk_entries,
        "materials": dict(sorted(builder.material_counts.items())),
        "materialColors": material_colors,
        "solidTypes": dict(sorted(Counter(builder.objects.solid_type).items())),
        "warnings": base.format_warnings(builder.scene.diagnostics),
        "diagnostics": base.diagnostics_list(builder.scene.diagnostics),
        "diagnosticSummary": base.diagnostics_summary(builder.scene.diagnostics),
        "defaultLoad": list(default_load),
    }


def write_html(manifest: dict, output: Path) -> None:
    output.write_text(
        render_template(
            "lazy.html",
            {
                "CLIPPING_JS": read_asset("clipping.js"),
                "CAMERA_JS": read_asset("camera.js"),
                "SCREENSHOT_JS": read_asset("screenshot.js"),
                "PICKING_JS": read_asset("picking.js"),
                "FILTERS_JS": read_asset("filters.js"),
                "TREE_JS": read_asset("tree.js"),
                "INSPECTOR_JS": read_asset("inspector.js"),
                "COMMON_JS": read_asset("common.js"),
                "VIEWSTATE_JS": read_asset("viewstate.js"),
                "TITLE": html.escape(str(manifest["sourceFile"])),
                "MANIFEST": json.dumps(manifest, separators=(",", ":")).replace(
                    "</", "<\\/"
                ),
            },
        ),
        encoding="utf-8",
    )


# --reuse-assets compares only options the user actually passed; each maps to
# where the build recorded it.
_SETTING_OPTIONS = {
    "quality": "quality",
    "include_containers": "includeContainers",
    "include_world": "includeWorld",
    "max_objects": "maxObjects",
    "max_vertices": "maxVertices",
    "chunk_vertices": "chunkVertices",
    "instancing": "instancing",
    "memory_budget": "memoryBudgetMiB",
}


def chunk_path(output: Path, chunk: dict) -> Path:
    """Where a manifest chunk entry lives on disk (its url is percent-encoded)."""
    return output.parent / unquote(str(chunk.get("url", "")))


def _grouping_mismatch(
    recorded: dict | None, args: argparse.Namespace, explicit: set[str]
) -> str | None:
    """Why the grouping the user asked for differs from the one the export was
    built with, or None. Only options actually passed are compared; an export
    from before groupings existed counts as the default (cocoa, no rules)."""
    asked = {"grouping", "group_depth", "grid_cell", "grouping_rules"} & explicit
    if not asked:
        return None
    built = recorded or Grouping().describe()
    try:
        wanted = Grouping.from_options(
            args.grouping, args.group_depth, args.grid_cell, args.grouping_rules
        ).describe()
    except GroupingError as error:
        return f"Invalid grouping: {error}"
    checks = {
        "grouping": ("--grouping", "strategy"),
        "group_depth": ("--group-depth", "groupDepth"),
        "grid_cell": ("--grid-cell", "gridCell"),
    }
    for dest, (flag, key) in checks.items():
        if dest in asked and wanted[key] != built.get(key):
            return (
                f"{flag} {wanted[key]!r} differs from the grouping this viewer was "
                f"built with ({built.get(key)!r}). Rebuild without --reuse-assets "
                "to apply it, or drop the option."
            )
    if "grouping_rules" in asked:
        wanted_rules = wanted["rules"]
        built_rules = built.get("rules")
        now = wanted_rules.get("sha256") if isinstance(wanted_rules, dict) else None
        then = built_rules.get("sha256") if isinstance(built_rules, dict) else None
        if now != then:
            return (
                "--grouping-rules differs from the rules this viewer was built "
                "with (a different file or contents). Rebuild without "
                "--reuse-assets to apply it, or drop the option."
            )
    return None


def check_reusable(
    manifest: dict, output: Path, args: argparse.Namespace, explicit: set[str]
) -> str | None:
    """Why ``output``'s existing chunks cannot be reused for ``args``, or
    None when they can. Checks the schema, the source file's sha256 (not its
    name), every explicitly passed build option, and each chunk's presence,
    size and sha256."""
    schema = manifest.get("schemaVersion", 1)
    if not isinstance(schema, int) or schema < base.SCHEMA_VERSION:
        return (
            f"{output} predates schema {base.SCHEMA_VERSION} (it records no input "
            "or chunk digests, so its chunks cannot be verified). Rebuild it once "
            "without --reuse-assets."
        )
    if schema > base.SCHEMA_VERSION:
        return (
            f"{output} was written by a newer gdml-inspector (schema {schema}, "
            f"this one reads {base.SCHEMA_VERSION}). Update gdml-inspector."
        )
    if manifest.get("chunkFormat", 1) < FORMAT_VERSION or "placements" not in manifest:
        return (
            f"{output} predates per-object ids and the placements asset (chunk "
            f"format {manifest.get('chunkFormat', 1)}, this gdml-inspector writes "
            f"{FORMAT_VERSION}). Rebuild it once without --reuse-assets."
        )
    provenance = manifest.get("provenance", {})
    recorded_digest = provenance.get("input", {}).get("sha256")
    current_digest = hashlib.sha256(args.gdml.read_bytes()).hexdigest()
    if recorded_digest != current_digest:
        return (
            f"{args.gdml} has changed since {output} was built (recorded sha256 "
            f"{str(recorded_digest)[:12]}..., now {current_digest[:12]}...). "
            "Rebuild without --reuse-assets."
        )
    settings = provenance.get("settings", {})
    for dest, key in _SETTING_OPTIONS.items():
        if dest in explicit and getattr(args, dest) != settings.get(key):
            flag = "--" + dest.replace("_", "-")
            return (
                f"{flag} {getattr(args, dest)!r} differs from the setting this viewer "
                f"was built with ({settings.get(key)!r}). Rebuild without "
                "--reuse-assets to apply it, or drop the option."
            )
    reason = _grouping_mismatch(settings.get("grouping"), args, explicit)
    if reason is not None:
        return reason
    built_with = provenance.get("backend", {}).get("name")
    if "backend" in explicit and args.backend not in ("auto", built_with):
        return (
            f"--backend {args.backend} differs from the backend this viewer was "
            f"built with ({built_with!r}). Rebuild without --reuse-assets."
        )
    problems = []
    for chunk in [*manifest.get("chunks", []), *placement_assets(manifest)]:
        path = chunk_path(output, chunk)
        if not path.is_file():
            problems.append(f"{chunk.get('url')}: missing")
            continue
        data = path.read_bytes()
        if len(data) != chunk.get("bytes"):
            problems.append(
                f"{chunk.get('url')}: {len(data)} bytes, expected {chunk.get('bytes')}"
            )
        elif hashlib.sha256(data).hexdigest() != chunk.get("sha256"):
            problems.append(
                f"{chunk.get('url')}: sha256 does not match (corrupt or edited)"
            )
    if problems:
        return (
            f"Could not reuse {output}: {len(problems)} asset(s) (binary chunks or "
            f"the placements file) are missing or corrupt; first: {problems[0]}. "
            "Rebuild without --reuse-assets."
        )
    return None


def redact_manifest(manifest: dict) -> None:
    """Apply the --redact-paths policy to an already-written manifest."""
    manifest["source"] = manifest.get("sourceFile", "")
    manifest.get("provenance", {}).get("input", {}).pop("path", None)


def write_bundle(manifest: dict, output: Path, archive: Path) -> None:
    """A deterministic zip of the viewer HTML and exactly the chunks its
    manifest lists: sorted entries, fixed timestamps and permissions, so the
    same export always produces the same archive."""
    names = [output.name] + sorted(
        unquote(str(entry["url"]))
        for entry in [*manifest["chunks"], *placement_assets(manifest)]
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in names:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            bundle.writestr(info, (output.parent / name).read_bytes())


def read_existing_manifest(output: Path) -> dict:
    """Read the embedded manifest from an HTML file generated by this viewer."""

    document = output.read_text(encoding="utf-8")
    prefix = "const MANIFEST = "
    suffix = ";\nconst canvas = "
    start = document.find(prefix)
    if start < 0:
        raise ValueError("embedded lazy-viewer manifest was not found")
    start += len(prefix)
    end = document.find(suffix, start)
    if end < 0:
        raise ValueError("end of embedded lazy-viewer manifest was not found")
    manifest = json.loads(document[start:end])
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "gdml-lazy-viewer-v1"
    ):
        raise ValueError("unsupported lazy-viewer manifest format")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        # This parser is reached both via the dedicated gdml-inspector-lazy
        # entry point and via `gdml-inspector --lazy`; the dedicated name
        # is used here since argparse can only show one.
        prog="gdml-inspector-lazy",
        description="Build a lazy-loading, clipping-enabled HTML viewer for GDML geometry.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("gdml", type=Path, help="Input GDML file")
    parser.add_argument("--out", type=Path, default=None, help="Output HTML path")
    parser.add_argument(
        "--backend", choices=("auto", "python", "pyg4ometry"), default="auto"
    )
    parser.add_argument(
        "--quality", choices=sorted(base.QUALITY_SEGMENTS), default="fast"
    )
    parser.add_argument("--include-containers", action="store_true")
    parser.add_argument("--include-world", action="store_true")
    parser.add_argument("--max-objects", type=int, default=500_000)
    parser.add_argument("--max-vertices", type=int, default=500_000_000)
    parser.add_argument(
        "--chunk-vertices",
        type=int,
        default=500_000,
        help="Maximum vertices per binary chunk; large layers are split into parts",
    )
    parser.add_argument(
        "--instancing",
        choices=INSTANCING_MODES,
        default="auto",
        help="Store repeated shapes once per chunk and draw them instanced "
        "(auto: only chunks where it saves at least 25%%; always; off: every "
        "placement's triangles are written out)",
    )
    parser.add_argument(
        "--grouping",
        choices=GROUPING_STRATEGIES,
        default="cocoa",
        help="How objects are grouped into subsystems and layers: cocoa (the "
        "original name and shape heuristics), hierarchy (by placement path, see "
        "--group-depth), material, spatial (a grid, see --grid-cell) or none",
    )
    parser.add_argument(
        "--group-depth",
        type=int,
        default=2,
        metavar="N",
        help="With --grouping hierarchy: path levels below the world that form "
        "the subsystem and layer (1 = subsystem only)",
    )
    parser.add_argument(
        "--grid-cell",
        type=float,
        default=500.0,
        metavar="MM",
        help="With --grouping spatial: the grid cell size in millimetres",
    )
    parser.add_argument(
        "--grouping-rules",
        type=Path,
        default=None,
        metavar="FILE.json",
        help="Ordered rules assigning subsystem and layer by fnmatch pattern on "
        "path, name, volume, material or solid; what no rule matches falls "
        "through to --grouping",
    )
    parser.add_argument(
        "--memory-budget",
        type=int,
        default=512,
        metavar="MIB",
        help="Export memory for chunk geometry still being filled. Past it, "
        "the largest open chunk parts are written early and their layer "
        "continues in a new part (0 = unlimited). Parts that fill up are always "
        "written at once, so memory holds only the open ones",
    )
    parser.add_argument(
        "--default-load",
        action="append",
        default=[],
        metavar="SUBSYSTEM",
        help="Subsystem to load when the page opens; repeat or use 'all'",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=f"Exit {base.STRICT_EXIT_CODE} if any diagnostic matches --fail-on "
        "(default: any error-severity diagnostic)",
    )
    parser.add_argument(
        "--fail-on",
        action="append",
        default=[],
        choices=sorted({*base.DIAGNOSTIC_SEVERITIES, *base.DIAGNOSTIC_CATEGORIES}),
        metavar="SEVERITY_OR_CATEGORY",
        help="With --strict, also fail on this diagnostic severity or "
        "category; repeatable. Defaults to 'error' alone.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write the structured diagnostics list as JSON to this path",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Parse and report diagnostics without writing the HTML viewer or assets",
    )
    parser.add_argument(
        "--reuse-assets",
        action="store_true",
        help="Rewrite and optionally serve an existing output HTML without rebuilding its binary chunks",
    )
    parser.add_argument(
        "--view-state",
        type=Path,
        default=None,
        metavar="FILE.view.json",
        help="Embed a saved view (the viewer's Save view button) as the "
        "initial camera, filters, selection, clipping and theme",
    )
    parser.add_argument(
        "--redact-paths",
        action="store_true",
        help="Omit the absolute source path from the output (the filename and "
        "sha256 digest are kept); with the same explicit options, input and "
        "output filename the output is then byte-for-byte reproducible",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        metavar="ARCHIVE.zip",
        help="Also write the HTML and its chunks as a deterministic zip archive",
    )
    parser.add_argument(
        "--serve", action="store_true", help="Serve the viewer on localhost"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser with --serve"
    )
    return parser


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


class _Unset(list):
    """Sentinel default. A list, so append-style options (--default-load)
    can still extend it; empty means "never passed"."""


def explicit_options(argv: Sequence[str]) -> set[str]:
    """The options actually given on the command line (not defaulted), so
    --reuse-assets can compare only what the user asked for. Parsed a second
    time with every default replaced by a sentinel, since options carrying
    their own default ignore argparse's `argument_default`."""
    parser = build_parser()
    parser.set_defaults(**dict.fromkeys(vars(parser.parse_args(argv)), _Unset()))
    return {
        dest
        for dest, value in vars(parser.parse_args(argv)).items()
        if not (isinstance(value, _Unset) and not value)
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(arguments)
    if not args.gdml.is_file():
        print(f"GDML file not found: {args.gdml}", file=sys.stderr)
        return 2
    view_state = None
    if args.view_state is not None:
        try:
            view_state = load_view_state(args.view_state)
        except ValueError as exc:
            print(f"gdml-inspector-lazy: --view-state: {exc}", file=sys.stderr)
            return 2
    output = args.out or (Path(tempfile.gettempdir()) / f"{args.gdml.stem}.lazy.html")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.reuse_assets:
        if not output.exists():
            print(f"Existing lazy-viewer HTML not found: {output}", file=sys.stderr)
            return 4
        try:
            manifest = read_existing_manifest(output)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Could not reuse {output}: {exc}", file=sys.stderr)
            return 4
        if manifest.get("sourceFile") != args.gdml.name:
            print(
                f"Existing viewer source is {manifest.get('sourceFile')!r}, "
                f"not {args.gdml.name!r}.",
                file=sys.stderr,
            )
            return 4
        reason = check_reusable(manifest, output, args, explicit_options(arguments))
        if reason is not None:
            print(reason, file=sys.stderr)
            return 4
        if args.default_load:
            manifest["defaultLoad"] = list(args.default_load)
        if args.redact_paths:
            redact_manifest(manifest)
        if view_state is not None:
            manifest["viewState"] = view_state
        write_html(manifest, output)
        if args.bundle is not None:
            write_bundle(manifest, output, args.bundle)
            print(f"Wrote bundle {args.bundle}")
        print(
            f"Rewrote {output} using {len(manifest['chunks']):,} existing binary chunks."
        )
        if args.serve:
            serve_file(output, args.port, open_browser=not args.no_browser)
        return 0

    builder_cls: type[LazyGDMLSceneBuilder] | type[LazyPyg4ometrySceneBuilder] = (
        LazyGDMLSceneBuilder
    )
    if args.backend in {"auto", "pyg4ometry"}:
        try:
            import pyg4ometry  # noqa: F401

            builder_cls = LazyPyg4ometrySceneBuilder
            print("Using pyg4ometry backend for solid tessellation.")
        except ModuleNotFoundError:
            if args.backend == "pyg4ometry":
                print(
                    "pyg4ometry is not installed in this environment.", file=sys.stderr
                )
                return 3
            print("pyg4ometry not installed; using pure-Python backend.")

    try:
        grouping = Grouping.from_options(
            args.grouping, args.group_depth, args.grid_cell, args.grouping_rules
        )
    except GroupingError as error:
        print(f"Invalid grouping: {error}", file=sys.stderr)
        return 2
    builder = builder_cls(
        args.gdml,
        quality=args.quality,
        include_containers=args.include_containers,
        include_world=args.include_world,
        max_objects=args.max_objects,
        max_vertices=args.max_vertices,
        chunk_vertices=args.chunk_vertices,
        instancing=args.instancing,
        memory_budget=args.memory_budget,
        grouping=grouping,
    )
    builder.redact_paths = args.redact_paths
    spill_dir = (
        None
        if args.validate_only
        else output.parent / f"{output.stem}.assets" / ".spill"
    )
    try:
        if spill_dir is not None:
            spill_dir.mkdir(parents=True, exist_ok=True)
            builder.spill_dir = spill_dir
        builder.build_chunks()
        return _finish_lazy_export(builder, args, output, view_state)
    finally:
        if spill_dir is not None:
            shutil.rmtree(spill_dir, ignore_errors=True)


def _finish_lazy_export(
    builder: LazyBuilderMixin,
    args: argparse.Namespace,
    output: Path,
    view_state: dict | None,
) -> int:
    if args.validate_only:
        diagnostics = base.diagnostics_list(builder.scene.diagnostics)
        warnings = base.format_warnings(builder.scene.diagnostics)
        print(f"Validated {args.gdml}; not writing a viewer (--validate-only).")
        print(
            f"Objects: {builder.total_objects:,}  "
            f"Triangles: {builder.total_vertices // 3:,}  "
            f"Vertices: {builder.total_vertices:,}"
        )
    else:
        manifest = write_assets(builder, output, args.default_load)
        if view_state is not None:
            digest = str(manifest["provenance"]["input"]["sha256"])
            note = stale_warning(view_state, digest)
            if note:
                print(f"Warning: {note}.", file=sys.stderr)
            manifest["viewState"] = view_state
        write_html(manifest, output)
        if args.bundle is not None:
            write_bundle(manifest, output, args.bundle)
            print(f"Wrote bundle {args.bundle}")
        diagnostics = manifest["diagnostics"]
        warnings = manifest["warnings"]
        asset_bytes = sum(int(chunk["bytes"]) for chunk in manifest["chunks"])
        print(f"Wrote {output}")
        print(f"Assets: {output.parent / (output.stem + '.assets')}")
        print(
            f"Subsystem chunks: {len(manifest['chunks']):,}  "
            f"Objects: {manifest['objects']:,}  Triangles: {manifest['triangles']:,}  "
            f"Binary: {asset_bytes / (1024 * 1024):.1f} MiB"
        )
        for subsystem in sorted(
            {chunk["subsystem"] for chunk in manifest["chunks"]},
            key=lambda x: SUBSYSTEM_ORDER.get(x, 99),
        ):
            entries = [
                chunk for chunk in manifest["chunks"] if chunk["subsystem"] == subsystem
            ]
            print(
                f"  {subsystem}: {len({entry['layer'] for entry in entries})} layers, "
                f"{sum(entry['triangles'] for entry in entries):,} triangles, "
                f"{len(entries)} chunks"
            )
    if warnings:
        print("Warnings:")
        for warning in warnings[:8]:
            print(f"  - {warning}")
        if len(warnings) > 8:
            print(f"  - ... {len(warnings) - 8} more")
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
        print(f"Wrote diagnostics report {args.report}")
    if args.strict:
        fail_on = set(args.fail_on) or {"error"}
        blocking = base.diagnostics_matching(diagnostics, fail_on)
        if blocking:
            print(
                f"Strict validation failed: {len(blocking)} diagnostic(s) match "
                f"{sorted(fail_on)}.",
                file=sys.stderr,
            )
            for item in blocking[:8]:
                print(f"  - {item['message']}", file=sys.stderr)
            return base.STRICT_EXIT_CODE
    if args.validate_only:
        return 0
    print(
        "Open through HTTP (use --serve); direct file:// loading cannot fetch chunks."
    )
    if args.serve:
        serve_file(output, args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
