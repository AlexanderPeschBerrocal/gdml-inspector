"""Export GDML geometry to glTF 2.0 (.gltf + .bin) or self-contained GLB.

Builds a true node hierarchy (not a flat world-space scene like the other
two exporters): each GDML placement becomes a glTF node with its own local
transform, nested under its parent exactly as in the source GDML, so
repeated logical shapes share one glTF mesh by reference instead of each
being baked into world space separately. Units are converted from this
project's native millimetres to glTF's metres; GDML's conventional Z-up
axes are left as-is (see docs/limitations.md) rather than guessing a
"helpful" remap.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import tempfile
from array import array
from collections.abc import Sequence
from pathlib import Path
from typing import cast
from urllib.parse import quote

from . import geometry as base
from .pyg4ometry_backend import Pyg4ometrySceneBuilder

_COMPONENT_TYPE_FLOAT = 5126
_TARGET_ARRAY_BUFFER = 34962
_MODE_TRIANGLES = 4

_GLB_VERSION = 2
_GLB_CHUNK_TYPE_JSON = b"JSON"
_GLB_CHUNK_TYPE_BIN = b"BIN\x00"


class GltfMixin:
    """Records each placement's local transform and each volume's render
    decision, keyed by path, as traverse()/place_physvols() already compute
    both and discard them after folding into the cumulative world matrix
    the other exporters use. Building the actual node tree from these flat,
    path-keyed records happens afterward in build_gltf(), not here."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._placements: dict[str, tuple[str, base.Mat4, bool]] = {}
        self._volume_at_path: dict[str, tuple[base.VolumeDef, bool]] = {}

    def _on_placement(
        self, path: str, parent_path: str, local_matrix: base.Mat4, is_assembly: bool
    ) -> None:
        self._placements[path] = (parent_path, local_matrix, is_assembly)

    def _on_volume(self, path: str, volume: base.VolumeDef, render: bool) -> None:
        self._volume_at_path[path] = (volume, render)


class GltfGDMLSceneBuilder(GltfMixin, base.GDMLSceneBuilder):
    pass


class GltfPyg4ometrySceneBuilder(GltfMixin, Pyg4ometrySceneBuilder):
    pass


GltfBuilder = GltfGDMLSceneBuilder | GltfPyg4ometrySceneBuilder


def _flatten_matrix(m: base.Mat4) -> list[float]:
    """Row-major Mat4 -> glTF's required flat column-major 16-float array,
    converting this project's millimetres to glTF's metres in the
    translation column only (rotation/scale are dimensionless)."""
    return [
        m[0][0],
        m[1][0],
        m[2][0],
        0.0,
        m[0][1],
        m[1][1],
        m[2][1],
        0.0,
        m[0][2],
        m[1][2],
        m[2][2],
        0.0,
        m[0][3] * 0.001,
        m[1][3] * 0.001,
        m[2][3] * 0.001,
        1.0,
    ]


class _GltfDocument:
    """Accumulates the glTF JSON structure and its single binary buffer."""

    def __init__(self) -> None:
        self.nodes: list[dict[str, object]] = []
        self.meshes: list[dict[str, object]] = []
        self.materials: list[dict[str, object]] = []
        self.accessors: list[dict[str, object]] = []
        self.buffer_views: list[dict[str, object]] = []
        self.buffer = bytearray()
        self._position_accessors: dict[str, int] = {}
        self._mesh_indices: dict[tuple[str, str], int] = {}
        self._material_indices: dict[str, int] = {}

    def _add_buffer_view(self, data: bytes, target: int) -> int:
        offset = len(self.buffer)
        self.buffer.extend(data)
        while len(self.buffer) % 4:
            self.buffer.append(0)
        index = len(self.buffer_views)
        self.buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(data),
                "target": target,
            }
        )
        return index

    def _position_accessor(self, builder: GltfBuilder, solid_ref: str) -> int | None:
        """The POSITION accessor for one solid's local-space mesh, in
        metres; shared by every (solid, material) mesh built from it, so
        repeated logical shapes never duplicate vertex bytes."""
        if solid_ref in self._position_accessors:
            return self._position_accessors[solid_ref]
        tris, _kind = builder.solid_mesh(solid_ref)
        if not tris:
            return None
        flat = array("f")
        mins = [math.inf, math.inf, math.inf]
        maxs = [-math.inf, -math.inf, -math.inf]
        for triangle in tris:
            for point in triangle:
                scaled = (point[0] * 0.001, point[1] * 0.001, point[2] * 0.001)
                flat.extend(scaled)
                for axis in range(3):
                    mins[axis] = min(mins[axis], scaled[axis])
                    maxs[axis] = max(maxs[axis], scaled[axis])
        view_index = self._add_buffer_view(
            base.little_endian_bytes(flat), _TARGET_ARRAY_BUFFER
        )
        index = len(self.accessors)
        self.accessors.append(
            {
                "bufferView": view_index,
                "componentType": _COMPONENT_TYPE_FLOAT,
                "count": len(flat) // 3,
                "type": "VEC3",
                "min": mins,
                "max": maxs,
            }
        )
        self._position_accessors[solid_ref] = index
        return index

    def _material_index(self, material_name: str) -> int:
        if material_name in self._material_indices:
            return self._material_indices[material_name]
        r, g, b, a = base.color_for_material(material_name)
        material: dict[str, object] = {
            "name": material_name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [r / 255, g / 255, b / 255, a / 255],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            # A GDML <scale> reflection flips triangle winding with no
            # compensating normal (none are emitted); without this, most
            # viewers back-face-cull or badly light a mirrored mesh.
            "doubleSided": True,
        }
        if a < 255:
            material["alphaMode"] = "BLEND"
        index = len(self.materials)
        self.materials.append(material)
        self._material_indices[material_name] = index
        return index

    def get_or_create_mesh(
        self, builder: GltfBuilder, solid_ref: str, material_name: str
    ) -> int | None:
        key = (solid_ref, material_name)
        if key in self._mesh_indices:
            return self._mesh_indices[key]
        accessor_index = self._position_accessor(builder, solid_ref)
        if accessor_index is None:
            return None
        material_index = self._material_index(material_name)
        index = len(self.meshes)
        self.meshes.append(
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": accessor_index},
                        "material": material_index,
                        "mode": _MODE_TRIANGLES,
                    }
                ]
            }
        )
        self._mesh_indices[key] = index
        return index

    def to_json(self, root_node_index: int, buffer_uri: str | None = None) -> dict:
        buffer: dict[str, object] = {"byteLength": len(self.buffer)}
        if buffer_uri is not None:
            buffer["uri"] = buffer_uri
        return {
            "asset": {"version": "2.0", "generator": "gdml-inspector"},
            "scene": 0,
            "scenes": [{"nodes": [root_node_index]}],
            "nodes": self.nodes,
            "meshes": self.meshes,
            "materials": self.materials,
            "accessors": self.accessors,
            "bufferViews": self.buffer_views,
            "buffers": [buffer],
        }


def build_gltf(builder: GltfBuilder) -> tuple[dict, bytes]:
    """Parse, walk, and assemble a glTF document from a GltfGDMLSceneBuilder
    or GltfPyg4ometrySceneBuilder instance. Drives traverse() directly
    (not build()): the flat world-space accumulation the other exporters
    need is irrelevant here, and traverse()'s existing budget/cycle/depth/
    diagnostic logic already does everything this needs by calling into it,
    via the no-op-by-default _on_placement()/_on_volume() hooks this
    builder overrides."""
    builder.parse()
    world_volume = builder.volumes[builder.world]
    builder.traverse(world_volume, base.identity(), builder.world)

    doc = _GltfDocument()
    node_index: dict[str, int] = {}

    def ensure_node(path: str) -> int:
        if path in node_index:
            return node_index[path]
        node: dict[str, object] = {"name": path.rsplit("/", 1)[-1]}
        entry = builder._volume_at_path.get(path)
        if entry is not None:
            volume, render = entry
            if render:
                mesh_index = doc.get_or_create_mesh(
                    builder, volume.solid, volume.material
                )
                if mesh_index is not None:
                    node["mesh"] = mesh_index
        index = len(doc.nodes)
        doc.nodes.append(node)
        node_index[path] = index
        return index

    root_index = ensure_node(builder.world)
    for path, (parent_path, local_matrix, _is_assembly) in builder._placements.items():
        child_index = ensure_node(path)
        doc.nodes[child_index]["matrix"] = _flatten_matrix(local_matrix)
        parent_index = ensure_node(parent_path)
        parent_node = doc.nodes[parent_index]
        children = cast(list, parent_node.setdefault("children", []))
        children.append(child_index)

    return doc.to_json(root_index), bytes(doc.buffer)


def _pad(data: bytes, pad_byte: bytes) -> bytes:
    remainder = len(data) % 4
    if remainder:
        data = data + pad_byte * (4 - remainder)
    return data


def write_glb(gltf_json: dict, binary_data: bytes, path: Path) -> None:
    json_bytes = _pad(
        json.dumps(gltf_json, separators=(",", ":")).encode("utf-8"), b" "
    )
    bin_bytes = _pad(binary_data, b"\x00")
    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    with path.open("wb") as handle:
        handle.write(struct.pack("<4sII", b"glTF", _GLB_VERSION, total_length))
        handle.write(struct.pack("<I4s", len(json_bytes), _GLB_CHUNK_TYPE_JSON))
        handle.write(json_bytes)
        handle.write(struct.pack("<I4s", len(bin_bytes), _GLB_CHUNK_TYPE_BIN))
        handle.write(bin_bytes)


def write_gltf_external(gltf_json: dict, binary_data: bytes, path: Path) -> None:
    bin_path = path.with_suffix(".bin")
    bin_path.write_bytes(binary_data)
    document = dict(gltf_json)
    document["buffers"] = [
        {"byteLength": len(binary_data), "uri": quote(bin_path.name)}
    ]
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gdml-inspector --gltf",
        description="Export GDML geometry to glTF 2.0 (.gltf + .bin) or GLB (.glb).",
    )
    parser.add_argument("gdml", type=Path, help="Input GDML file")
    parser.add_argument(
        "--out", type=Path, default=None, help="Output .glb or .gltf path"
    )
    parser.add_argument(
        "--backend", choices=["auto", "python", "pyg4ometry"], default="auto"
    )
    parser.add_argument(
        "--quality", choices=sorted(base.QUALITY_SEGMENTS), default="medium"
    )
    parser.add_argument(
        "--include-containers",
        action="store_true",
        help="Give container/logical volumes a node too, with a mesh",
    )
    parser.add_argument(
        "--include-world", action="store_true", help="Give the world volume a mesh"
    )
    parser.add_argument("--max-objects", type=int, default=100_000)
    parser.add_argument("--max-vertices", type=int, default=5_000_000)
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write the structured diagnostics list as JSON to this path",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not args.gdml.is_file():
        print(f"GDML file not found: {args.gdml}", file=sys.stderr)
        return 2

    output = args.out or (
        Path(tempfile.gettempdir()) / f"{args.gdml.stem}.gdml_model.glb"
    )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    builder_cls: type[GltfGDMLSceneBuilder] | type[GltfPyg4ometrySceneBuilder] = (
        GltfGDMLSceneBuilder
    )
    if args.backend in {"auto", "pyg4ometry"}:
        try:
            import pyg4ometry  # noqa: F401

            builder_cls = GltfPyg4ometrySceneBuilder
            print("Using pyg4ometry backend for solid tessellation.")
        except ModuleNotFoundError:
            if args.backend == "pyg4ometry":
                print(
                    "pyg4ometry is not installed in this Python environment. "
                    "Install it or rerun with --backend python.",
                    file=sys.stderr,
                )
                return 3
            print("pyg4ometry not installed; using pure-Python backend.")

    builder = builder_cls(
        args.gdml,
        quality=args.quality,
        include_containers=args.include_containers,
        include_world=args.include_world,
        max_objects=args.max_objects,
        max_vertices=args.max_vertices,
    )
    gltf_json, binary_data = build_gltf(builder)

    if output.suffix.lower() == ".gltf":
        write_gltf_external(gltf_json, binary_data, output)
    else:
        write_glb(gltf_json, binary_data, output)
    print(f"Wrote {output}")
    print(
        f"Nodes: {len(gltf_json['nodes']):,}  Meshes: {len(gltf_json['meshes']):,}  "
        f"Binary: {len(binary_data) / 1024:.1f} KiB"
    )

    warnings = base.format_warnings(builder.scene.diagnostics)
    if warnings:
        print("Warnings:")
        for item in warnings[:8]:
            print(f"  - {item}")
        if len(warnings) > 8:
            print(f"  - ... {len(warnings) - 8} more")

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(base.diagnostics_list(builder.scene.diagnostics), indent=2),
            encoding="utf-8",
        )
        print(f"Wrote diagnostics report {args.report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
