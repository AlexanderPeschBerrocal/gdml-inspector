"""Single-file HTML export and command-line interface."""

from __future__ import annotations

import argparse
import html
import json
import sys
import tempfile
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from typing_extensions import NotRequired

from . import __version__
from . import geometry as base
from .geometry import (
    DIAGNOSTIC_CATEGORIES,
    DIAGNOSTIC_SEVERITIES,
    QUALITY_SEGMENTS,
    STRICT_EXIT_CODE,
    GDMLSceneBuilder,
    SceneDict,
    SolidInfo,
    diagnostics_matching,
    semantic_name,
    solid_params,
    tag_name,
)
from .pyg4ometry_backend import Pyg4ometrySceneBuilder, debug_pyg4ometry_mesh
from .server import serve_file


class HierarchyNodeDict(TypedDict):
    """One node of the world-to-placement tree, independent of whether it
    has a rendered mesh. Keyed by path like GltfMixin's node tree, with the
    same pre-existing limitation: two anonymous physvols placing the same
    volume under the same mother, with no explicit physvol name, collide
    on path (see docs/limitations.md)."""

    path: str
    parentPath: str | None
    name: str
    semanticName: str
    volume: str
    semanticVolume: str
    isAssembly: bool
    rendered: bool
    objectId: NotRequired[int]
    localMatrix: NotRequired[list[float]]
    globalMatrix: NotRequired[list[float]]


def _flatten_matrix(matrix: base.Mat4) -> list[float]:
    """The same 3x4 row-major flattening diff.py's own ObjectDict["matrix"]
    already uses, dropping the always-implicit [0, 0, 0, 1] fourth row."""
    return [v for row in matrix[:3] for v in row]


class HierarchyMixin(base.GDMLSceneBuilder):
    """Records the full placement tree -- including non-rendered container
    and assembly nodes that add_volume() never sees -- keyed by path, the
    same way GltfMixin records flat per-path records for its own node
    tree. Building the nested JSON array happens afterward in
    hierarchy_nodes(), not here.

    Declares GDMLSceneBuilder as its base purely so mypy can check this
    mixin's own super().add_volume() call and self.solids access against a
    real contract, matching diff.py's DiffMixin precedent; the actual
    runtime MRO is unaffected (C3 linearization collapses the duplicate)."""

    def __init__(
        self,
        gdml: Path,
        quality: str,
        include_containers: bool,
        include_world: bool,
        max_objects: int,
        max_vertices: int,
    ) -> None:
        super().__init__(
            gdml, quality, include_containers, include_world, max_objects, max_vertices
        )
        self._hierarchy: dict[str, HierarchyNodeDict] = {}

    def _hierarchy_node(self, path: str) -> HierarchyNodeDict:
        existing = self._hierarchy.get(path)
        if existing is not None:
            return existing
        name = path.rsplit("/", 1)[-1]
        node: HierarchyNodeDict = {
            "path": path,
            "parentPath": None,
            "name": name,
            "semanticName": semantic_name(name),
            "volume": name,
            "semanticVolume": semantic_name(name),
            "isAssembly": False,
            "rendered": False,
        }
        self._hierarchy[path] = node
        return node

    def _on_placement(
        self, path: str, parent_path: str, local_matrix: base.Mat4, is_assembly: bool
    ) -> None:
        super()._on_placement(path, parent_path, local_matrix, is_assembly)
        node = self._hierarchy_node(path)
        node["parentPath"] = parent_path
        node["isAssembly"] = is_assembly
        node["localMatrix"] = _flatten_matrix(local_matrix)

    def _on_volume(self, path: str, volume: base.VolumeDef, render: bool) -> None:
        super()._on_volume(path, volume, render)
        node = self._hierarchy_node(path)
        node["volume"] = volume.name
        node["semanticVolume"] = semantic_name(volume.name)
        node["rendered"] = render

    def add_volume(self, volume: base.VolumeDef, matrix: base.Mat4, path: str) -> None:
        count_before = len(self.scene.objects)
        super().add_volume(volume, matrix, path)
        if len(self.scene.objects) > count_before:
            self._hierarchy_node(path)["globalMatrix"] = _flatten_matrix(matrix)

    def hierarchy_nodes(self, scene: SceneDict) -> list[HierarchyNodeDict]:
        """The tree as a flat, pre-order list (dicts preserve insertion
        order, and nodes are inserted exactly in traversal order), with
        each rendered node's objectId resolved against the built scene."""
        object_ids = {obj["path"]: obj["id"] for obj in scene["objects"]}
        nodes = list(self._hierarchy.values())
        for node in nodes:
            object_id = object_ids.get(node["path"])
            if object_id is not None:
                node["objectId"] = object_id
        return nodes

    def solids_table(self) -> dict[str, SolidInfo]:
        """Each parsed solid's kind and own declared parameters, keyed by
        GDML ref and shared across every placement that references it --
        the same construction diff.py's DiffMixin.to_dict() already does."""
        return {
            ref: {"type": tag_name(el), "params": solid_params(el)}
            for ref, el in self.solids.items()
        }


class HierarchyGDMLSceneBuilder(HierarchyMixin, GDMLSceneBuilder):
    pass


class HierarchyPyg4ometrySceneBuilder(HierarchyMixin, Pyg4ometrySceneBuilder):
    pass


def write_html(
    scene: SceneDict,
    hierarchy: list[HierarchyNodeDict],
    solids: dict[str, SolidInfo],
    output: Path,
) -> None:
    title = html.escape(Path(scene["meta"]["source"]).name)
    output.write_text(
        files("gdml_inspector.assets")
        .joinpath("standalone.html")
        .read_text(encoding="utf-8")
        .replace("__TITLE__", title)
        .replace(
            "__SCENE__", json.dumps(scene, separators=(",", ":")).replace("</", "<\\/")
        )
        .replace(
            "__HIERARCHY__",
            json.dumps(hierarchy, separators=(",", ":")).replace("</", "<\\/"),
        )
        .replace(
            "__SOLIDS__",
            json.dumps(solids, separators=(",", ":")).replace("</", "<\\/"),
        ),
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gdml-inspector",
        description="GDML Inspector: build a standalone HTML/WebGL geometry viewer.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--lazy",
        action="store_true",
        help="Export a lazy-loading viewer instead; use --lazy --help for its options",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Compare two GDML files instead; use --diff --help for its options",
    )
    parser.add_argument(
        "--gltf",
        action="store_true",
        help="Export glTF/GLB instead; use --gltf --help for its options",
    )
    parser.add_argument("gdml", type=Path, help="Input GDML file")
    parser.add_argument("--out", type=Path, default=None, help="Output HTML path")
    parser.add_argument(
        "--backend",
        choices=["auto", "python", "pyg4ometry"],
        default="auto",
        help="Geometry tessellation backend. 'pyg4ometry' gives more accurate boolean solids when installed.",
    )
    parser.add_argument("--quality", choices=sorted(QUALITY_SEGMENTS), default="medium")
    parser.add_argument(
        "--include-containers",
        action="store_true",
        help="Draw container/logical volumes too",
    )
    parser.add_argument(
        "--include-world", action="store_true", help="Draw the world volume"
    )
    parser.add_argument("--max-objects", type=int, default=100_000)
    parser.add_argument("--max-vertices", type=int, default=5_000_000)
    parser.add_argument(
        "--strict",
        action="store_true",
        help=f"Exit {STRICT_EXIT_CODE} if any diagnostic matches --fail-on "
        "(default: any error-severity diagnostic)",
    )
    parser.add_argument(
        "--fail-on",
        action="append",
        default=[],
        choices=sorted({*DIAGNOSTIC_SEVERITIES, *DIAGNOSTIC_CATEGORIES}),
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
        help="Parse and report diagnostics without writing the HTML viewer",
    )
    parser.add_argument(
        "--debug-pyg4ometry-mesh",
        action="store_true",
        help="Print pyg4ometry mesh internals for one solid and exit.",
    )
    parser.add_argument(
        "--debug-solid",
        default="",
        help="Solid name to inspect with --debug-pyg4ometry-mesh.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Serve the HTML on localhost after writing it",
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
    if args.debug_pyg4ometry_mesh:
        return debug_pyg4ometry_mesh(args.gdml, args.debug_solid or None)

    output = args.out or (
        Path(tempfile.gettempdir()) / f"{args.gdml.stem}.gdml_viewer.html"
    )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    builder_cls: (
        type[HierarchyGDMLSceneBuilder] | type[HierarchyPyg4ometrySceneBuilder]
    ) = HierarchyGDMLSceneBuilder
    if args.backend in {"auto", "pyg4ometry"}:
        try:
            import pyg4ometry  # noqa: F401

            builder_cls = HierarchyPyg4ometrySceneBuilder
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
    scene = builder.build()
    meta = scene["meta"]
    if args.validate_only:
        print(f"Validated {args.gdml}; not writing a viewer (--validate-only).")
    else:
        write_html(
            scene, builder.hierarchy_nodes(scene), builder.solids_table(), output
        )
        print(f"Wrote {output}")
    print(
        f"Objects: {meta['objects']:,}  Triangles: {meta['triangles']:,}  Vertices: {meta['vertices']:,}"
    )
    warnings = meta["warnings"]
    if warnings:
        print("Warnings:")
        for warning in warnings[:8]:
            print(f"  - {warning}")
        if len(warnings) > 8:
            print(f"  - ... {len(warnings) - 8} more")
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(meta["diagnostics"], indent=2),
            encoding="utf-8",
        )
        print(f"Wrote diagnostics report {args.report}")
    if args.strict:
        fail_on = set(args.fail_on) or {"error"}
        blocking = diagnostics_matching(meta["diagnostics"], fail_on)
        if blocking:
            print(
                f"Strict validation failed: {len(blocking)} diagnostic(s) match "
                f"{sorted(fail_on)}.",
                file=sys.stderr,
            )
            for diagnostic in blocking[:8]:
                print(f"  - {diagnostic['message']}", file=sys.stderr)
            return STRICT_EXIT_CODE
    if args.validate_only:
        return 0
    if args.serve:
        serve_file(output, args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
