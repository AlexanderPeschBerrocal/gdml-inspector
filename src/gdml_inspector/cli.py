"""Installed command-line entry points for both viewer modes."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from xml.etree.ElementTree import ParseError

from . import diff, gltf, lazy, standalone


def _run(command: Callable, argv: Sequence[str]) -> int:
    try:
        return command(argv)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, ParseError, RecursionError) as exc:
        print(f"gdml-inspector: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    options_end = args.index("--") if "--" in args else len(args)
    if "--gltf" in args[:options_end]:
        args.remove("--gltf")
        return _run(gltf.main, args)
    if "--diff" in args[:options_end]:
        args.remove("--diff")
        return _run(diff.main, args)
    if "--lazy" in args[:options_end]:
        args.remove("--lazy")
        return _run(lazy.main, args)
    return _run(standalone.main, args)


def lazy_main(argv: Sequence[str] | None = None) -> int:
    return _run(lazy.main, list(sys.argv[1:] if argv is None else argv))
