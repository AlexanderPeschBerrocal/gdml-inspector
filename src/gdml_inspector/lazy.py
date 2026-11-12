#!/usr/bin/env python3
"""Chunked GDML export, layer grouping, and lazy-viewer command-line interface."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import struct
import sys
import tempfile
import zipfile
from array import array
from collections import Counter, OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote, unquote

from . import __version__
from . import geometry as base
from ._assets import read_asset
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
    semantic_path = base.semantic_path(path)
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

    def __init__(self, *args: object, chunk_vertices: int, **kwargs: object) -> None:
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

    def _budget_exceeded(self) -> bool:
        # OR in stopped_at_limit: add_volume() can reject a single
        # oversized object outright (its own precise pre-commit check)
        # without incrementing total_objects/total_vertices at all, which a
        # pure count-based check would miss, letting a later, smaller
        # sibling be attempted when traversal should already have stopped.
        return (
            self.stopped_at_limit
            or self.total_objects >= self.max_objects
            or self.total_vertices >= self.max_vertices
        )

    def _record_budget_exceeded(self) -> None:
        self.stopped_at_limit = True

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


def write_chunk(chunk: Chunk, path: Path) -> int:
    positions = base.little_endian_bytes(chunk.positions)
    colors = base.little_endian_bytes(chunk.colors)
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
        digest = hashlib.sha256((asset_dir / binary_name).read_bytes()).hexdigest()
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
        "schemaVersion": base.SCHEMA_VERSION,
        "provenance": builder.provenance(chunkVertices=builder.chunk_vertices),
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
        # Static shared code first: GDML-derived JSON inserted afterwards is
        # never scanned for template tokens.
        .replace("__CLIPPING_JS__", read_asset("clipping.js"))
        .replace("__COMMON_JS__", read_asset("common.js"))
        .replace("__TITLE__", title)
        .replace("__MANIFEST__", manifest_json),
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
}


def chunk_path(output: Path, chunk: dict) -> Path:
    """Where a manifest chunk entry lives on disk (its url is percent-encoded)."""
    return output.parent / unquote(str(chunk.get("url", "")))


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
    built_with = provenance.get("backend", {}).get("name")
    if "backend" in explicit and args.backend not in ("auto", built_with):
        return (
            f"--backend {args.backend} differs from the backend this viewer was "
            f"built with ({built_with!r}). Rebuild without --reuse-assets."
        )
    problems = []
    for chunk in manifest.get("chunks", []):
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
            f"Could not reuse {output}: {len(problems)} binary chunk(s) are missing "
            f"or corrupt; first: {problems[0]}. Rebuild without --reuse-assets."
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
        unquote(str(chunk["url"])) for chunk in manifest["chunks"]
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

    builder = builder_cls(
        args.gdml,
        quality=args.quality,
        include_containers=args.include_containers,
        include_world=args.include_world,
        max_objects=args.max_objects,
        max_vertices=args.max_vertices,
        chunk_vertices=args.chunk_vertices,
    )
    builder.redact_paths = args.redact_paths
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
