#!/usr/bin/env python3
"""Chunked GDML export, layer grouping, and lazy-viewer command-line interface."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import struct
import sys
import tempfile
from array import array
from collections import Counter, OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from . import __version__
from . import geometry as base
from .pyg4ometry_backend import Pyg4ometrySceneBuilder
from .server import serve_file

MAGIC = b"GDMLCHN1"
FORMAT_VERSION = 1
HEADER = struct.Struct("<8s6I")
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


def canonical_material(name: str) -> str:
    return base.semantic_name(name)


def bbox_union(
    target_min: list[float],
    target_max: list[float],
    lo: Sequence[float],
    hi: Sequence[float],
) -> None:
    for axis in range(3):
        target_min[axis] = min(target_min[axis], float(lo[axis]))
        target_max[axis] = max(target_max[axis], float(hi[axis]))


def classify_chunk(
    volume: base.VolumeDef,
    path: str,
    lo: Sequence[float],
    hi: Sequence[float],
    radial_min: float,
    radial_max: float,
) -> tuple[str, str]:
    """Return a human-readable subsystem and layer for one placed volume."""

    semantic_volume = base.semantic_name(volume.name)
    semantic_solid = base.semantic_name(volume.solid)
    semantic_path = "/".join(base.semantic_name(part) for part in path.split("/"))
    material = canonical_material(volume.material)
    text = f"{semantic_volume} {semantic_solid} {semantic_path} {material}".lower()

    def calo_layer(prefix: str) -> str:
        match = re.search(rf"{prefix.lower()}(\d+)(?:_(\d+))?", text)
        index = match.group(1) if match else "?"
        if match and match.group(2):
            index += f".{match.group(2)}"
        region = "Endcap" if "endcap" in text else "Barrel"
        side = ""
        if "forward" in text:
            side = " +z"
        elif "back" in text or "backward" in text:
            side = " -z"
        return f"{region}{side} layer {index}"

    if "ecal" in text:
        return "ECAL", calo_layer("ecal")
    if "hcal" in text:
        return "HCAL", calo_layer("hcal")
    if "irongap" in text or "iron_gap" in text:
        region = "Endcap" if "endcap" in text else "Barrel"
        side = ""
        if "forward" in text:
            side = " +z"
        elif "back" in text or "backward" in text:
            side = " -z"
        return "Calo support", f"{region}{side} iron gap"

    tracker_tokens = ("inner", "trk", "pixel", "pix", "strip", "silicon", "g4_si")
    if any(token in text for token in tracker_tokens):
        z_span = float(hi[2]) - float(lo[2])
        radial_span = max(radial_max - radial_min, 1.0e-9)
        z_center = 0.5 * (float(lo[2]) + float(hi[2]))
        radial_center = 0.5 * (radial_min + radial_max)
        is_endcap = "endcap" in text or (
            abs(z_center) > 1.0 and z_span < 4.0 * radial_span
        )
        if is_endcap:
            side = "+z" if z_center >= 0.0 else "-z"
            layer = f"Endcap {side} |z|≈{round(abs(z_center))} mm"
        else:
            layer = f"Barrel r≈{round(radial_center)} mm"
        return "Tracker", layer

    label = base.semantic_name(volume.name)
    if material.lower().startswith(("galactic", "g4_galactic")):
        label = "Vacuum/container"
    return "Other", label


@dataclass
class Chunk:
    subsystem: str
    layer: str
    part: int
    positions: array = field(default_factory=lambda: array("f"))
    colors: array = field(default_factory=lambda: array("B"))
    object_count: int = 0
    bbox_min: list[float] = field(default_factory=lambda: [float("inf")] * 3)
    bbox_max: list[float] = field(default_factory=lambda: [float("-inf")] * 3)

    @property
    def vertex_count(self) -> int:
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
    """Mixin that changes the original builder from one scene to layer chunks."""

    def __init__(self, *args, chunk_vertices: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.chunk_vertices = max(3, int(chunk_vertices))
        self.chunks: OrderedDict[str, Chunk] = OrderedDict()
        self.chunk_parts: dict[str, list[str]] = {}
        self.total_vertices = 0
        self.total_objects = 0
        self.material_counts: Counter[str] = Counter()
        self.global_bbox_min = [float("inf")] * 3
        self.global_bbox_max = [float("-inf")] * 3
        self.stopped_at_limit = False

    def _chunk_for(self, subsystem: str, layer: str, incoming_vertices: int) -> Chunk:
        base_key = f"{subsystem}\x1f{layer}"
        keys = self.chunk_parts.setdefault(base_key, [])
        if keys:
            candidate = self.chunks[keys[-1]]
            if candidate.vertex_count + incoming_vertices <= self.chunk_vertices:
                return candidate
        part = len(keys) + 1
        chunk = Chunk(subsystem=subsystem, layer=layer, part=part)
        self.chunks[chunk.key] = chunk
        keys.append(chunk.key)
        return chunk

    def add_volume(self, volume: base.VolumeDef, matrix: base.Mat4, path: str) -> None:
        tris, _ = self.solid_mesh(volume.solid)
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
        if (
            self.total_objects >= self.max_objects
            or self.total_vertices + vertex_count > self.max_vertices
        ):
            self.stopped_at_limit = True
            return

        subsystem, layer = classify_chunk(volume, path, lo, hi, radial_min, radial_max)
        chunk = self._chunk_for(subsystem, layer, vertex_count)
        material = canonical_material(volume.material) or "(none)"
        rgba = base.color_for_material(volume.material)

        chunk.positions.extend(positions)
        chunk.colors.extend(array("B", rgba) * vertex_count)
        chunk.object_count += 1
        bbox_union(chunk.bbox_min, chunk.bbox_max, lo, hi)
        bbox_union(self.global_bbox_min, self.global_bbox_max, lo, hi)
        self.material_counts[material] += 1
        self.total_vertices += vertex_count
        self.total_objects += 1

    def place_physvols(
        self,
        physvols: list[base.ET.Element],
        matrix: base.Mat4,
        path: str,
        depth: int = 0,
        ancestors: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        """Traverse the daughters placed by ``physvols`` in a mother at ``matrix``."""
        for placement in physvols:
            ref_el = base.child(placement, "volumeref")
            if ref_el is None:
                continue
            ref = ref_el.attrib.get("ref", "")
            placement_name = placement.attrib.get("name", ref)
            child_matrix = base.matmul(
                matrix,
                base.placement_matrix(
                    placement, self.positions, self.rotations, self.scales
                ),
            )
            # Like Geant4, a reference names an assembly before a logical volume.
            # An assembly's physvols are placed relative to its own placement.
            assembly = self.assemblies.get(ref)
            if assembly is not None:
                key = ("assembly", ref)
                if key in ancestors:
                    raise ValueError(f"Cyclic GDML assembly reference: {ref}")
                if depth + 1 >= base.MAX_NESTING_DEPTH:
                    raise ValueError(
                        "GDML volume nesting exceeds the supported depth of "
                        f"{base.MAX_NESTING_DEPTH}"
                    )
                self.place_physvols(
                    base.children(assembly, "physvol"),
                    child_matrix,
                    f"{path}/{placement_name}",
                    depth + 1,
                    ancestors | {key},
                )
            else:
                child_volume = self.volumes.get(ref)
                if child_volume is None:
                    self.scene.diagnose(
                        "missing-volume-reference",
                        "error",
                        "reference",
                        "physvol",
                        f"Missing volume reference: {ref}",
                        identifier=ref,
                    )
                    continue
                self.traverse(
                    child_volume,
                    child_matrix,
                    f"{path}/{placement_name}",
                    depth + 1,
                    ancestors,
                )
            if self.stopped_at_limit:
                return

    def traverse(
        self,
        volume: base.VolumeDef,
        matrix: base.Mat4,
        path: str,
        depth: int = 0,
        ancestors: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        if (
            self.total_objects >= self.max_objects
            or self.total_vertices >= self.max_vertices
        ):
            self.stopped_at_limit = True
            return
        key = ("volume", volume.name)
        if key in ancestors:
            raise ValueError(f"Cyclic GDML volume reference: {volume.name}")
        if depth >= base.MAX_NESTING_DEPTH:
            raise ValueError(
                f"GDML volume nesting exceeds the supported depth of {base.MAX_NESTING_DEPTH}"
            )
        ancestors = ancestors | {key}
        placements = base.children(volume.element, "physvol")
        unsupported = {
            tag: base.children(volume.element, tag)
            for tag in base.UNSUPPORTED_DAUGHTERS
        }
        for tag, elements in unsupported.items():
            if elements:
                self.scene.diagnose(
                    "unsupported-daughter",
                    "warning",
                    "unsupported",
                    tag,
                    f"Unsupported GDML daughter construct {tag!r} in volume "
                    f"{volume.name!r}; not expanded",
                    identifier=volume.name,
                )
        is_container = (
            bool(placements)
            or volume.material.startswith("Galactic")
            or any(unsupported[tag] for tag in base.CONTAINER_DAUGHTERS)
        )
        should_render = (self.include_containers or not is_container) and (
            self.include_world or volume.name != self.world
        )
        if should_render:
            self.add_volume(volume, matrix, path)
        self.place_physvols(placements, matrix, path, depth, ancestors)

    def build_chunks(self) -> None:
        self.parse()
        world = self.volumes.get(self.world)
        if world is None:
            raise ValueError(f"World volume {self.world!r} was not found")
        self.traverse(world, base.identity(), self.world)
        if self.global_bbox_min[0] == float("inf"):
            self.global_bbox_min = [-1.0, -1.0, -1.0]
            self.global_bbox_max = [1.0, 1.0, 1.0]
        if self.stopped_at_limit:
            self.scene.diagnose(
                "limit-reached",
                "warning",
                "budget",
                "export",
                "Stopped at configured object/vertex limit",
            )


class LazyGDMLSceneBuilder(LazyBuilderMixin, base.GDMLSceneBuilder):
    pass


class LazyPyg4ometrySceneBuilder(LazyBuilderMixin, Pyg4ometrySceneBuilder):
    pass


def little_endian_bytes(values: array) -> bytes:
    if sys.byteorder == "little":
        return values.tobytes()
    copied = array(values.typecode, values)
    copied.byteswap()
    return copied.tobytes()


def write_chunk(chunk: Chunk, path: Path) -> int:
    positions = little_endian_bytes(chunk.positions)
    colors = little_endian_bytes(chunk.colors)
    position_offset = HEADER.size
    color_offset = position_offset + len(positions)
    id_offset = color_offset + len(colors)
    total_size = id_offset
    header = HEADER.pack(
        MAGIC,
        FORMAT_VERSION,
        chunk.vertex_count,
        position_offset,
        color_offset,
        id_offset,
        total_size,
    )
    path.write_bytes(header + positions + colors)
    return total_size


def chunk_sort_key(chunk: Chunk) -> tuple[int, str, int]:
    return SUBSYSTEM_ORDER.get(chunk.subsystem, 99), chunk.layer, chunk.part


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
        byte_size = write_chunk(chunk, asset_dir / binary_name)
        chunk_entries.append(
            {
                "key": chunk.key,
                "baseKey": chunk.base_key,
                "subsystem": chunk.subsystem,
                "layer": chunk.layer,
                "part": chunk.part,
                "url": f"{asset_dir.name}/{binary_name}",
                "objects": chunk.object_count,
                "vertices": chunk.vertex_count,
                "triangles": chunk.triangle_count,
                "bytes": byte_size,
                "bbox": [*chunk.bbox_min, *chunk.bbox_max],
            }
        )

    material_colors = {
        name: list(base.color_for_material(name)) for name in builder.material_counts
    }
    return {
        "format": "gdml-lazy-viewer-v1",
        "source": str(builder.gdml),
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
        "warnings": base.format_warnings(builder.scene.diagnostics),
        "diagnostics": base.diagnostics_list(builder.scene.diagnostics),
        "diagnosticSummary": base.diagnostics_summary(builder.scene.diagnostics),
        "defaultLoad": list(default_load),
    }


def write_html(manifest: dict, output: Path) -> None:
    title = html.escape(str(manifest["sourceFile"]))
    manifest_json = json.dumps(manifest, separators=(",", ":")).replace("</", "<\\/")
    output.write_text(
        files("gdml_inspector.assets")
        .joinpath("lazy.html")
        .read_text(encoding="utf-8")
        .replace("__TITLE__", title)
        .replace("__MANIFEST__", manifest_json),
        encoding="utf-8",
    )


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


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a lazy-loading, phi-cut-enabled HTML viewer for GDML geometry.",
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
        "--serve", action="store_true", help="Serve the viewer on localhost"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser with --serve"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not args.gdml.is_file():
        print(f"GDML file not found: {args.gdml}", file=sys.stderr)
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
        missing_assets = [
            str(chunk.get("url", ""))
            for chunk in manifest.get("chunks", [])
            if not (output.parent / str(chunk.get("url", ""))).is_file()
        ]
        if missing_assets:
            print(
                f"Could not reuse {output}: {len(missing_assets)} binary chunk(s) are missing; "
                f"first missing asset: {missing_assets[0]}",
                file=sys.stderr,
            )
            return 4
        if args.default_load:
            manifest["defaultLoad"] = list(args.default_load)
        write_html(manifest, output)
        print(
            f"Rewrote {output} using {len(manifest['chunks']):,} existing binary chunks."
        )
        if args.serve:
            serve_file(output, args.port, open_browser=not args.no_browser)
        return 0

    builder_cls = LazyGDMLSceneBuilder
    if args.backend in {"auto", "pyg4ometry"}:
        try:
            import pyg4ometry  # type: ignore  # noqa: F401

            builder_cls = LazyPyg4ometrySceneBuilder
            print("Using pyg4ometry backend for solid tessellation.")
        except ModuleNotFoundError:
            if args.backend == "pyg4ometry":
                print(
                    "pyg4ometry is not installed in this environment.", file=sys.stderr
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
        chunk_vertices=args.chunk_vertices,
    )
    builder.build_chunks()
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
        write_html(manifest, output)
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
