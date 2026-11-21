"""How the lazy exporter groups objects into subsystems and layers (chunks).

A *grouping* maps every accepted object to a ``(subsystem, layer)`` pair; the
viewer's layer tree and the chunk files follow it. Strategies:

``cocoa``       the original name/shape heuristics (the default; unchanged)
``hierarchy``   by placement path, ``--group-depth`` levels below the world
``material``    subsystem is the material, layer the logical volume
``spatial``     a regular grid of ``--grid-cell`` millimetres
``none``        one layer

``--grouping-rules FILE.json`` adds an ordered list of rules, tried first; an
object no rule matches falls through to the chosen strategy. Rule patterns are
literal text or ``fnmatch`` wildcards (``*``, ``?``, ``[seq]``): never regular
expressions, so no pattern can run away. Nothing here depends on the hash seed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from . import geometry as base

GROUPING_STRATEGIES = ("cocoa", "hierarchy", "material", "spatial", "none")
RULES_VERSION = 1
RULE_FIELDS = ("path", "name", "volume", "material", "solid")
_RULE_KEYS = {"match", "subsystem", "layer", "exact"}


class GroupingError(ValueError):
    """A grouping option or rules file that cannot be used; the message says why."""


def canonical_material(name: str) -> str:
    return base.semantic_name(name)


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


@dataclass(frozen=True)
class Rule:
    match: tuple[tuple[str, str], ...]  # (field, pattern), all must match
    subsystem: str
    layer: str | None  # None: the fallback strategy's layer
    exact: bool

    def matches(self, fields: dict[str, tuple[str, str]]) -> bool:
        for name, pattern in self.match:
            raw, semantic = fields[name]
            if self.exact:
                if pattern not in (raw, semantic):
                    return False
            elif not (fnmatchcase(raw, pattern) or fnmatchcase(semantic, pattern)):
                return False
        return True


def load_rules(path: Path) -> tuple[list[Rule], str]:
    """The rules in ``path`` and the sha256 of the file's bytes. Every problem
    is collected into one GroupingError so a file can be fixed in one pass."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise GroupingError(
            f"cannot read the grouping rules {path}: {error}"
        ) from error
    digest = hashlib.sha256(data).hexdigest()
    try:
        document = json.loads(data)
    except ValueError as error:
        raise GroupingError(f"{path} is not valid JSON: {error}") from error
    problems: list[str] = []
    if not isinstance(document, dict):
        raise GroupingError(f'{path} must be a JSON object with a "rules" list')
    unknown = sorted(set(document) - {"version", "rules"})
    if unknown:
        problems.append(f"unknown top-level key(s) {unknown}")
    if document.get("version", RULES_VERSION) != RULES_VERSION:
        problems.append(f'"version" must be {RULES_VERSION}')
    raw_rules = document.get("rules")
    if not isinstance(raw_rules, list):
        raise GroupingError(f'{path}: "rules" must be a list')
    rules: list[Rule] = []
    for index, entry in enumerate(raw_rules):
        where = f"rule {index}"
        before = len(problems)
        if not isinstance(entry, dict):
            problems.append(f"{where} must be an object")
            continue
        extra = sorted(set(entry) - _RULE_KEYS)
        if extra:
            problems.append(f"{where} has unknown key(s) {extra}")
        match = entry.get("match")
        if not isinstance(match, dict) or not match:
            problems.append(f'{where} needs a non-empty "match" object')
            continue
        bad_fields = sorted(set(match) - set(RULE_FIELDS))
        if bad_fields:
            problems.append(
                f"{where} matches unknown field(s) {bad_fields}; use {list(RULE_FIELDS)}"
            )
        if any(not isinstance(v, str) or v == "" for v in match.values()):
            problems.append(f"{where}: every match pattern must be a non-empty string")
        subsystem, layer = entry.get("subsystem"), entry.get("layer")
        for label, value in (("subsystem", subsystem), ("layer", layer)):
            if isinstance(value, str) and any(ord(c) < 32 for c in value):
                problems.append(f"{where}: {label} must not contain control characters")
        if not isinstance(subsystem, str) or not subsystem:
            problems.append(f'{where} needs a non-empty "subsystem" string')
        if layer is not None and (not isinstance(layer, str) or not layer):
            problems.append(f'{where}: "layer" must be a non-empty string when given')
        if not isinstance(entry.get("exact", False), bool):
            problems.append(f'{where}: "exact" must be true or false')
        if len(problems) == before:
            rules.append(
                Rule(
                    tuple((k, match[k]) for k in RULE_FIELDS if k in match),
                    str(subsystem),
                    layer,
                    bool(entry.get("exact", False)),
                )
            )
    if problems:
        raise GroupingError(f"{path}: " + "; ".join(problems))
    return rules, digest


@dataclass
class Grouping:
    """The chosen strategy and its options, plus optional rules."""

    strategy: str = "cocoa"
    depth: int = 2
    cell: float = 500.0
    rules: list[Rule] = field(default_factory=list)
    rules_name: str | None = None
    rules_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.strategy not in GROUPING_STRATEGIES:
            raise GroupingError(
                f"unknown grouping {self.strategy!r}; choose one of {GROUPING_STRATEGIES}"
            )
        if self.depth < 1:
            raise GroupingError("--group-depth must be at least 1")
        if not (self.cell > 0 and math.isfinite(self.cell)):
            raise GroupingError("--grid-cell must be a positive number of millimetres")

    @classmethod
    def from_options(
        cls, strategy: str, depth: int, cell: float, rules_file: Path | None
    ) -> Grouping:
        grouping = cls(strategy, depth, cell)
        if rules_file is not None:
            grouping.rules, grouping.rules_sha256 = load_rules(rules_file)
            grouping.rules_name = rules_file.name
        return grouping

    def describe(self) -> dict[str, object]:
        """What the provenance records, so a different grouping is detected."""
        return {
            "strategy": self.strategy,
            "groupDepth": self.depth,
            "gridCell": self.cell,
            "rules": None
            if self.rules_name is None
            else {
                "file": self.rules_name,
                "sha256": self.rules_sha256,
                "count": len(self.rules),
            },
        }

    def assign(
        self,
        volume: base.VolumeDef,
        path: str,
        lo: Sequence[float],
        hi: Sequence[float],
        radial_min: float,
        radial_max: float,
    ) -> tuple[str, str]:
        subsystem, layer = self._assign(volume, path, lo, hi, radial_min, radial_max)
        # \x1f separates a layer's key parts everywhere downstream.
        return subsystem.replace("\x1f", " "), layer.replace("\x1f", " ")

    def _assign(
        self,
        volume: base.VolumeDef,
        path: str,
        lo: Sequence[float],
        hi: Sequence[float],
        radial_min: float,
        radial_max: float,
    ) -> tuple[str, str]:
        subsystem, layer = self._strategy(volume, path, lo, hi, radial_min, radial_max)
        if self.rules:
            name = path.rsplit("/", 1)[-1]
            fields = {
                "path": (path, base.semantic_path(path)),
                "name": (name, base.semantic_name(name)),
                "volume": (volume.name, base.semantic_name(volume.name)),
                "material": (volume.material, canonical_material(volume.material)),
                "solid": (volume.solid, base.semantic_name(volume.solid)),
            }
            for rule in self.rules:
                if rule.matches(fields):
                    return rule.subsystem, rule.layer or layer
        return subsystem, layer

    def _strategy(
        self,
        volume: base.VolumeDef,
        path: str,
        lo: Sequence[float],
        hi: Sequence[float],
        radial_min: float,
        radial_max: float,
    ) -> tuple[str, str]:
        if self.strategy == "cocoa":
            return classify_chunk(volume, path, lo, hi, radial_min, radial_max)
        if self.strategy == "hierarchy":
            parts = [base.semantic_name(p) for p in path.split("/")]
            below = parts[1:] or parts  # drop the world volume's own name
            layers = below[1 : self.depth]
            return below[0], "/".join(layers) if layers else "(direct)"
        if self.strategy == "material":
            return (
                canonical_material(volume.material) or "(none)",
                base.semantic_name(volume.name),
            )
        if self.strategy == "spatial":
            cells = [
                math.floor(((lo[axis] + hi[axis]) / 2) / self.cell) for axis in range(3)
            ]
            return (
                f"Grid {self.cell:g} mm",
                "x{} y{} z{}".format(*cells),
            )
        return "All", "All"
