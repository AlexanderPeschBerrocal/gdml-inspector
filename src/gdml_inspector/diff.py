"""Compare two GDML files and report semantic differences.

This is the backend-only slice of issue #9: a CLI mode that classifies
added/removed/moved/rotated/scaled/material/dimension changes between two
GDML files, independently of Geant4's regenerated ``0x...`` address suffixes
and of definition reordering. Synchronised side-by-side viewing is issue
#9's UI half and is not implemented here.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypedDict, cast

from . import geometry as base
from .pyg4ometry_backend import Pyg4ometrySceneBuilder

DEFAULT_TOLERANCE = 1e-6
_BOOLEAN_SOLID_TYPES = {"union", "subtraction", "intersection"}


class SolidInfo(TypedDict):
    type: str
    params: dict[str, float]


class DiffSceneDict(base.SceneDict):
    solids: dict[str, SolidInfo]


class AmbiguousDict(TypedDict):
    semanticPath: str
    pathsA: list[str]
    pathsB: list[str]


class DiffMixin(base.GDMLSceneBuilder):
    """Persists the per-object placement matrix and a solids dict that
    plain exports don't need, so the standalone/lazy viewers stay lean.

    Declares GDMLSceneBuilder as its base purely so mypy can check this
    mixin's own ``super().add_volume()``/``super().to_dict()`` calls and
    ``self.scene``/``self.solids`` accesses against a real contract: the
    actual runtime MRO is unaffected, since DiffGDMLSceneBuilder/
    DiffPyg4ometrySceneBuilder already list a GDMLSceneBuilder subtype as
    their other base, and C3 linearization collapses the duplicate."""

    def add_volume(self, volume: base.VolumeDef, matrix: base.Mat4, path: str) -> None:
        count_before = len(self.scene.objects)
        super().add_volume(volume, matrix, path)
        if len(self.scene.objects) > count_before:
            self.scene.objects[-1]["matrix"] = [v for row in matrix[:3] for v in row]

    def to_dict(self) -> DiffSceneDict:
        result = cast(DiffSceneDict, super().to_dict())
        result["solids"] = {
            ref: {"type": base.tag_name(el), "params": base.solid_params(el)}
            for ref, el in self.solids.items()
        }
        return result

    def build(self) -> DiffSceneDict:
        # build() itself is unchanged; only re-declared so its return type
        # reflects to_dict()'s override above (build() calls self.to_dict()
        # polymorphically, which mypy can't see through GDMLSceneBuilder's
        # own declared SceneDict return type).
        return cast(DiffSceneDict, super().build())


class DiffGDMLSceneBuilder(DiffMixin, base.GDMLSceneBuilder):
    pass


class DiffPyg4ometrySceneBuilder(DiffMixin, Pyg4ometrySceneBuilder):
    pass


def _vec_sub(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [x - y for x, y in zip(a, b, strict=True)]


def _vec_len(v: Sequence[float]) -> float:
    return math.sqrt(sum(c * c for c in v))


def decompose_matrix(
    matrix12: list[float],
) -> tuple[list[float], list[float], list[float]]:
    """Split a stored row-major 3x4 matrix into (translation, scale,
    normalized linear part flattened column-major).

    Each linear column's length is that axis's scale magnitude; normalizing
    columns by their own length isolates everything else -- rotation *and*
    reflection -- as "orientation", since a reflection has unit column
    length and so does not show up as a scale change (a magnitude change and
    a handedness flip are different, independently meaningful edits). This
    deliberately does not attempt to attribute a handedness change to one
    specific axis's signed scale: doing so from the composed matrix alone is
    ambiguous in general (an ordinary 180-degree rotation also flips a
    column's sign along its own axis, with no reflection involved), so
    guessing would misclassify a common rotation as a scale change. Exact
    (to floating-point precision) for anything this codebase's own
    scale_matrix()/rot_x/y/z() can produce, since placement_matrix() always
    composes a diagonal scale with an orthonormal rotation; an approximation,
    not a true polar decomposition, for a general affine matrix with shear.
    """
    translation = [matrix12[3], matrix12[7], matrix12[11]]
    columns = [
        [matrix12[0], matrix12[4], matrix12[8]],
        [matrix12[1], matrix12[5], matrix12[9]],
        [matrix12[2], matrix12[6], matrix12[10]],
    ]
    scale = [_vec_len(c) for c in columns]
    normalized: list[float] = []
    for column, length in zip(columns, scale, strict=True):
        if length > 1e-12:
            normalized.extend(v / length for v in column)
        else:
            normalized.extend((0.0, 0.0, 0.0))
    return translation, scale, normalized


def group_by_semantic_path(
    objects: list[base.ObjectDict],
) -> dict[str, list[base.ObjectDict]]:
    groups: dict[str, list[base.ObjectDict]] = {}
    for obj in objects:
        groups.setdefault(base.semantic_path(obj["path"]), []).append(obj)
    return groups


def match_objects(
    objects_a: list[base.ObjectDict], objects_b: list[base.ObjectDict]
) -> tuple[
    list[tuple[base.ObjectDict, base.ObjectDict]],
    list[base.ObjectDict],
    list[base.ObjectDict],
    list[AmbiguousDict],
]:
    """Match objects by address-suffix-insensitive placement path.

    Returns (matched, added, removed, ambiguous). Within a semantic-path
    group present on both sides: a raw path held by exactly one object on
    each side pairs immediately (handles unchanged geometry and reordered
    definitions). A raw path is NOT a safe pairing key on its own when it is
    not unique within one side's list -- anonymous repeated physvols (no
    ``name`` attribute) all share their volumeref as the path, so two
    placements can collide on the very same raw path; those go to the
    leftover pool instead of being paired by accidental dict-key collision.
    If exactly one object is then left on each side, it is paired too (the
    only remaining candidate, not a "nearest name" guess); anything else
    left over is reported ambiguous rather than guessed, per #9's acceptance
    criteria.
    """
    groups_a = group_by_semantic_path(objects_a)
    groups_b = group_by_semantic_path(objects_b)
    matched: list[tuple[base.ObjectDict, base.ObjectDict]] = []
    added: list[base.ObjectDict] = []
    removed: list[base.ObjectDict] = []
    ambiguous: list[AmbiguousDict] = []
    for key in sorted(set(groups_a) | set(groups_b)):
        a_list = groups_a.get(key, [])
        b_list = groups_b.get(key, [])
        if not a_list:
            added.extend(b_list)
            continue
        if not b_list:
            removed.extend(a_list)
            continue
        a_by_raw_path: dict[str, list[base.ObjectDict]] = {}
        for obj in a_list:
            a_by_raw_path.setdefault(obj["path"], []).append(obj)
        b_by_raw_path: dict[str, list[base.ObjectDict]] = {}
        for obj in b_list:
            b_by_raw_path.setdefault(obj["path"], []).append(obj)
        leftover_a: list[base.ObjectDict] = []
        leftover_b: list[base.ObjectDict] = []
        for raw_path in sorted(set(a_by_raw_path) | set(b_by_raw_path)):
            a_group = a_by_raw_path.get(raw_path, [])
            b_group = b_by_raw_path.get(raw_path, [])
            if len(a_group) == 1 and len(b_group) == 1:
                matched.append((a_group[0], b_group[0]))
            else:
                leftover_a.extend(a_group)
                leftover_b.extend(b_group)
        if len(leftover_a) == 1 and len(leftover_b) == 1:
            matched.append((leftover_a[0], leftover_b[0]))
        elif leftover_a or leftover_b:
            ambiguous.append(
                {
                    "semanticPath": key,
                    "pathsA": [obj["path"] for obj in leftover_a],
                    "pathsB": [obj["path"] for obj in leftover_b],
                }
            )
    return matched, added, removed, ambiguous


def classify_pair(
    obj_a: base.ObjectDict,
    obj_b: base.ObjectDict,
    solids_a: dict[str, SolidInfo],
    solids_b: dict[str, SolidInfo],
    tolerance: float,
    boolean_solids: set[tuple[str, str, str]],
) -> dict[str, object] | None:
    """Return the changed aspects of a matched pair, or None if unchanged
    within tolerance. Records any Boolean-typed solid involved into
    boolean_solids (label, ref, kind), since its dimensions are never
    comparable regardless of whether anything else changed."""
    changes: dict[str, object] = {}

    material_a = base.semantic_name(obj_a["material"])
    material_b = base.semantic_name(obj_b["material"])
    if material_a != material_b:
        changes["material"] = {"from": material_a, "to": material_b}

    missing_solid: SolidInfo = {"type": "missing", "params": {}}
    solid_a = solids_a.get(obj_a["solid"], missing_solid)
    solid_b = solids_b.get(obj_b["solid"], missing_solid)
    for label, obj, solid in (("A", obj_a, solid_a), ("B", obj_b, solid_b)):
        if solid["type"] in _BOOLEAN_SOLID_TYPES:
            boolean_solids.add((label, obj["solid"], solid["type"]))
    if solid_a["type"] != solid_b["type"]:
        changes["solidType"] = {"from": solid_a["type"], "to": solid_b["type"]}
    elif solid_a["type"] not in _BOOLEAN_SOLID_TYPES:
        params_a, params_b = solid_a["params"], solid_b["params"]
        dims = {}
        for key in sorted(set(params_a) | set(params_b)):
            va, vb = params_a.get(key), params_b.get(key)
            if va is None or vb is None or abs(va - vb) > tolerance:
                dims[key] = {"from": va, "to": vb}
        if dims:
            changes["dimensions"] = dims

    translation_a, scale_a, orientation_a = decompose_matrix(obj_a["matrix"])
    translation_b, scale_b, orientation_b = decompose_matrix(obj_b["matrix"])
    translation_delta = _vec_len(_vec_sub(translation_a, translation_b))
    if translation_delta > tolerance:
        changes["translation"] = {
            "from": translation_a,
            "to": translation_b,
            "delta": translation_delta,
        }
    if any(abs(x - y) > tolerance for x, y in zip(scale_a, scale_b, strict=True)):
        changes["scale"] = {"from": scale_a, "to": scale_b}
    if any(
        abs(x - y) > tolerance
        for x, y in zip(orientation_a, orientation_b, strict=True)
    ):
        changes["orientation"] = {"changed": True}

    return changes or None


def build_report(
    path_a: Path,
    path_b: Path,
    scene_a: DiffSceneDict,
    scene_b: DiffSceneDict,
    tolerance: float,
) -> dict[str, object]:
    objects_a, objects_b = scene_a["objects"], scene_b["objects"]
    solids_a, solids_b = scene_a["solids"], scene_b["solids"]
    matched, added, removed, ambiguous = match_objects(objects_a, objects_b)

    boolean_solids: set[tuple[str, str, str]] = set()
    changed: list[dict[str, object]] = []
    unchanged = 0
    for obj_a, obj_b in matched:
        changes = classify_pair(
            obj_a, obj_b, solids_a, solids_b, tolerance, boolean_solids
        )
        if changes is None:
            unchanged += 1
            continue
        changed.append(
            {
                "pathA": obj_a["path"],
                "pathB": obj_b["path"],
                "semanticPath": base.semantic_path(obj_a["path"]),
                **changes,
            }
        )

    limitations: list[str] = []
    backend_a, backend_b = scene_a["meta"]["backend"], scene_b["meta"]["backend"]
    if backend_a != backend_b:
        limitations.append(
            f"Backends differ ({backend_a} vs {backend_b}); mesh-derived "
            "comparisons may not be meaningful."
        )
    for label, ref, kind in sorted(boolean_solids):
        limitations.append(
            f"Source {label} solid {ref!r} is a Boolean {kind}; its "
            "dimensions were not compared."
        )
    for label, source_meta in (("A", scene_a["meta"]), ("B", scene_b["meta"])):
        for message in source_meta["warnings"]:
            limitations.append(f"Source {label}: {message}")

    return {
        "sourceA": str(path_a),
        "sourceB": str(path_b),
        "digestA": base.source_digest(path_a),
        "digestB": base.source_digest(path_b),
        "backendA": backend_a,
        "backendB": backend_b,
        "tolerance": tolerance,
        "units": {"length": "mm", "angle": "rad"},
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "ambiguous": len(ambiguous),
            "unchanged": unchanged,
        },
        "added": [
            {"path": o["path"], "material": o["material"], "solidType": o["solidType"]}
            for o in added
        ],
        "removed": [
            {"path": o["path"], "material": o["material"], "solidType": o["solidType"]}
            for o in removed
        ],
        "changed": changed,
        "ambiguous": ambiguous,
        "limitations": limitations,
    }


def _print_bucket(
    label: str, items: list[object], line: Callable[[object], object]
) -> None:
    if not items:
        return
    print(f"{label}:")
    for item in items[:8]:
        print(f"  - {line(item)}")
    if len(items) > 8:
        print(f"  - ... {len(items) - 8} more")


def print_summary(report: dict[str, object]) -> None:
    # report's own shape (build_report()'s return value) is deliberately
    # left as dict[str, object] in this PR, unlike scene.objects' ObjectDict
    # -- a different, separate contract explicitly deferred to a follow-up
    # (see docs/limitations.md); casts below bridge that untyped boundary
    # for this purely cosmetic CLI printing, not a scene/export contract.
    print(f"Comparing {report['sourceA']} vs {report['sourceB']}")
    print(f"  A: {report['digestA']}  backend={report['backendA']}")
    print(f"  B: {report['digestB']}  backend={report['backendB']}")
    s = cast(dict, report["summary"])
    print(
        f"Added: {s['added']}  Removed: {s['removed']}  Changed: {s['changed']}  "
        f"Ambiguous: {s['ambiguous']}  Unchanged: {s['unchanged']}"
    )
    _print_bucket("Added", cast(list, report["added"]), lambda o: cast(dict, o)["path"])
    _print_bucket(
        "Removed", cast(list, report["removed"]), lambda o: cast(dict, o)["path"]
    )
    _print_bucket(
        "Changed",
        cast(list, report["changed"]),
        lambda o: (
            f"{cast(dict, o)['semanticPath']}: "
            + ", ".join(
                k for k in cast(dict, o) if k not in {"pathA", "pathB", "semanticPath"}
            )
        ),
    )
    _print_bucket(
        "Ambiguous (not paired)",
        cast(list, report["ambiguous"]),
        lambda o: (
            f"{cast(dict, o)['semanticPath']}: {len(cast(dict, o)['pathsA'])} in A, "
            f"{len(cast(dict, o)['pathsB'])} in B"
        ),
    )
    _print_bucket("Limitations", cast(list, report["limitations"]), lambda item: item)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gdml-inspector --diff",
        description="Compare two GDML files and report added/removed/changed placements.",
    )
    parser.add_argument("gdml_a", type=Path, metavar="A.gdml")
    parser.add_argument("gdml_b", type=Path, metavar="B.gdml")
    parser.add_argument(
        "--backend", choices=["auto", "python", "pyg4ometry"], default="auto"
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help="Maximum absolute difference (mm or rad) treated as unchanged",
    )
    parser.add_argument("--max-objects", type=int, default=1_000_000)
    parser.add_argument("--max-vertices", type=int, default=50_000_000)
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write the full structured comparison as JSON to this path",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    for label, path in (("A", args.gdml_a), ("B", args.gdml_b)):
        if not path.is_file():
            print(f"GDML file {label} not found: {path}", file=sys.stderr)
            return 2

    builder_cls: type[DiffGDMLSceneBuilder] | type[DiffPyg4ometrySceneBuilder] = (
        DiffGDMLSceneBuilder
    )
    if args.backend in {"auto", "pyg4ometry"}:
        try:
            import pyg4ometry  # noqa: F401

            builder_cls = DiffPyg4ometrySceneBuilder
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

    def build(path: Path) -> DiffSceneDict:
        return builder_cls(
            path,
            quality="fast",
            include_containers=True,
            include_world=True,
            max_objects=args.max_objects,
            max_vertices=args.max_vertices,
        ).build()

    scene_a = build(args.gdml_a)
    scene_b = build(args.gdml_b)
    report = build_report(args.gdml_a, args.gdml_b, scene_a, scene_b, args.tolerance)
    print_summary(report)

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote diff report {args.report}")

    s = cast(dict, report["summary"])
    if s["added"] or s["removed"] or s["changed"] or s["ambiguous"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
