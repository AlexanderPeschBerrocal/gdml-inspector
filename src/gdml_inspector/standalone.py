"""Single-file HTML export and command-line interface."""

from __future__ import annotations

import argparse
import html
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from . import __version__
from ._assets import read_asset, render_template
from .geometry import (
    DIAGNOSTIC_CATEGORIES,
    DIAGNOSTIC_SEVERITIES,
    QUALITY_SEGMENTS,
    STRICT_EXIT_CODE,
    SceneDict,
    SolidInfo,
    diagnostics_matching,
)
from .hierarchy import (
    HierarchyGDMLSceneBuilder,
    HierarchyNodeDict,
    HierarchyPyg4ometrySceneBuilder,
)
from .pyg4ometry_backend import debug_pyg4ometry_mesh
from .server import serve_file
from .viewstate import load_view_state, stale_warning


def write_html(
    scene: SceneDict,
    hierarchy: list[HierarchyNodeDict],
    solids: dict[str, SolidInfo],
    output: Path,
    view_state: dict[str, object] | None = None,
) -> None:
    def embed(value: object) -> str:
        return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")

    output.write_text(
        render_template(
            "standalone.html",
            {
                "CLIPPING_JS": read_asset("clipping.js"),
                "PICKING_JS": read_asset("picking.js"),
                "INSPECTOR_JS": read_asset("inspector.js"),
                "COMMON_JS": read_asset("common.js"),
                "VIEWSTATE_JS": read_asset("viewstate.js"),
                "TITLE": html.escape(Path(scene["meta"]["source"]).name),
                "SCENE": embed(scene),
                "HIERARCHY": embed(hierarchy),
                "SOLIDS": embed(solids),
                "VIEW_STATE": embed(view_state),
            },
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
        help="Omit the absolute source path from the HTML (the filename and "
        "sha256 digest are kept); with the same explicit options and input the "
        "output is then byte-for-byte reproducible",
    )
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

    view_state = None
    if args.view_state is not None:
        try:
            view_state = load_view_state(args.view_state)
        except ValueError as exc:
            print(f"gdml-inspector: --view-state: {exc}", file=sys.stderr)
            return 2

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
    builder.redact_paths = args.redact_paths
    scene = builder.build()
    meta = scene["meta"]
    if args.validate_only:
        print(f"Validated {args.gdml}; not writing a viewer (--validate-only).")
    else:
        if view_state is not None:
            digest = str(builder.provenance()["input"]["sha256"])  # type: ignore[index]
            note = stale_warning(view_state, digest)
            if note:
                print(f"Warning: {note}.", file=sys.stderr)
        write_html(
            scene,
            builder.hierarchy_nodes(),
            builder.solids_table(),
            output,
            view_state,
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
