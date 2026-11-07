"""Single-file HTML export and command-line interface."""

from __future__ import annotations

import argparse
import html
import json
import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Dict, Sequence

from . import __version__
from .geometry import GDMLSceneBuilder, QUALITY_SEGMENTS
from .pyg4ometry_backend import Pyg4ometrySceneBuilder, debug_pyg4ometry_mesh
from .server import serve_file


def write_html(scene: Dict[str, object], output: Path) -> None:
    title = html.escape(Path(str(scene["meta"]["source"])).name)  # type: ignore[index]
    output.write_text(
        files("gdml_inspector.assets")
        .joinpath("standalone.html")
        .read_text(encoding="utf-8")
        .replace("__TITLE__", title)
        .replace(
            "__SCENE__", json.dumps(scene, separators=(",", ":")).replace("</", "<\\/")
        ),
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GDML Inspector: build a standalone HTML/WebGL geometry viewer."
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--lazy",
        action="store_true",
        help="Export a lazy-loading viewer instead; use --lazy --help for its options",
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
    builder_cls = GDMLSceneBuilder
    if args.backend in {"auto", "pyg4ometry"}:
        try:
            import pyg4ometry  # type: ignore  # noqa: F401

            builder_cls = Pyg4ometrySceneBuilder
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
    write_html(scene, output)
    meta = scene["meta"]  # type: ignore[index]
    print(f"Wrote {output}")
    print(
        f"Objects: {meta['objects']:,}  Triangles: {meta['triangles']:,}  Vertices: {meta['vertices']:,}"
    )  # type: ignore[index]
    warnings = meta.get("warnings", [])  # type: ignore[union-attr]
    if warnings:
        print("Warnings:")
        for item in warnings[:8]:
            print(f"  - {item}")
        if len(warnings) > 8:
            print(f"  - ... {len(warnings) - 8} more")
    if args.serve:
        serve_file(output, args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
