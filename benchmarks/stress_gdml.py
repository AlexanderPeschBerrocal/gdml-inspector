#!/usr/bin/env python3
"""Redistributable synthetic GDML for stress and benchmark runs.

Writes a deterministic GDML file with a chosen number of placements, how many
distinct logical volumes they share (repetition = placements / logical), a flat
or nested layout, and names that spread the objects over several lazy layers.
Nothing detector-specific is embedded, so huge detector files never need to be
committed.

    python benchmarks/stress_gdml.py out.gdml --placements 20000 --logical 50
    python benchmarks/stress_gdml.py out.gdml --placements 5000 --layout nested --group-size 20

Shapes cycle through boxes, tubes and cones (all natively meshed) with
dimensions that vary per logical volume, so distinct logical volumes really are
distinct solids. To exercise "one solid larger than a chunk", export with
``--chunk-vertices`` below a tube's vertex count (a tube is a few hundred
vertices at ``--quality high``).
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

# Name prefixes the lazy exporter's layer heuristics recognise, so objects
# spread over several subsystems/layers instead of one.
FAMILIES = ("ecal1", "hcal1", "pixel", "strip", "plate")


def solid_xml(index: int) -> tuple[str, str]:
    """(name, xml) of the solid for logical volume ``index``."""
    size = 1.0 + (index % 7) * 0.5
    kind = index % 3
    if kind == 0:
        return f"shape_{index}", (
            f'<box name="shape_{index}" x="{2 * size}" y="{3 * size}" z="{4 * size}" lunit="mm"/>'
        )
    if kind == 1:
        return f"shape_{index}", (
            f'<tube name="shape_{index}" rmin="0" rmax="{size}" z="{4 * size}" '
            'startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>'
        )
    return f"shape_{index}", (
        f'<cone name="shape_{index}" rmin1="0" rmax1="{size}" rmin2="0" rmax2="{size / 2}" '
        f'z="{3 * size}" startphi="0" deltaphi="360" aunit="deg" lunit="mm"/>'
    )


def build(
    placements: int,
    logical: int,
    layout: str,
    group_size: int,
    single_layer: bool = False,
) -> str:
    if placements < 1 or logical < 1 or group_size < 1:
        raise ValueError("placements, logical and group-size must be at least 1")
    families = FAMILIES[:1] if single_layer else FAMILIES
    solids, volumes = [], []
    for index in range(logical):
        name, xml = solid_xml(index)
        solids.append(xml)
        family = families[index % len(families)]
        volumes.append(
            f'<volume name="{family}_vol_{index}"><materialref ref="Mat_{index % 5}"/>'
            f'<solidref ref="{name}"/></volume>'
        )
    side = max(1, math.ceil(placements ** (1 / 3)))
    pitch = 20.0

    def position(i: int) -> str:
        x, y, z = i % side, (i // side) % side, i // (side * side)
        return f'<position x="{x * pitch}" y="{y * pitch}" z="{z * pitch}" unit="mm"/>'

    physvols = []
    if layout == "flat":
        for i in range(placements):
            ref = f"{families[(i % logical) % len(families)]}_vol_{i % logical}"
            physvols.append(
                f'<physvol name="p{i}"><volumeref ref="{ref}"/>{position(i)}</physvol>'
            )
        world_body = "\n  ".join(physvols)
        extra = ""
    else:
        groups = math.ceil(placements / group_size)
        group_volumes = []
        for g in range(groups):
            members = []
            for k in range(min(group_size, placements - g * group_size)):
                i = g * group_size + k
                ref = f"{families[(i % logical) % len(families)]}_vol_{i % logical}"
                members.append(
                    f'<physvol name="p{i}"><volumeref ref="{ref}"/>'
                    f'<position x="{(k % 10) * 4.0}" y="{(k // 10) * 4.0}" z="0" unit="mm"/></physvol>'
                )
            group_volumes.append(
                f'<volume name="group_{g}"><materialref ref="Galactic"/><solidref ref="group_box"/>'
                + "".join(members)
                + "</volume>"
            )
            physvols.append(
                f'<physvol name="g{g}"><volumeref ref="group_{g}"/>{position(g)}</physvol>'
            )
        world_body = "\n  ".join(physvols)
        extra = "\n".join(group_volumes)
    materials = "".join(
        f'<material name="Mat_{i}" Z="{i + 6}"><D value="{1 + i}" unit="g/cm3"/><atom value="{12 + i}"/></material>'
        for i in range(5)
    )
    world_size = (side + 2) * pitch * 2
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<gdml>
<define/>
<materials>
<material name="Galactic" Z="1"><D value="1e-25" unit="g/cm3"/><atom value="1.01"/></material>
{materials}
</materials>
<solids>
<box name="world_box" x="{world_size}" y="{world_size}" z="{world_size}" lunit="mm"/>
<box name="group_box" x="60" y="60" z="20" lunit="mm"/>
{chr(10).join(solids)}
</solids>
<structure>
{chr(10).join(volumes)}
{extra}
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  {world_body}
</volume>
</structure>
<setup name="Default" version="1.0"><world ref="world"/></setup>
</gdml>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--placements", type=int, default=1000, help="number of placed objects"
    )
    parser.add_argument(
        "--logical",
        type=int,
        default=20,
        help="distinct logical volumes shared by the placements (repetition = placements / logical)",
    )
    parser.add_argument("--layout", choices=("flat", "nested"), default="flat")
    parser.add_argument(
        "--single-layer",
        action="store_true",
        help="name every volume so the lazy exporter puts all objects in one layer",
    )
    parser.add_argument(
        "--group-size", type=int, default=50, help="children per container when nested"
    )
    args = parser.parse_args(argv)
    try:
        text = build(
            args.placements,
            args.logical,
            args.layout,
            args.group_size,
            args.single_layer,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    args.output.write_text(text, encoding="utf-8")
    print(
        f"Wrote {args.output}: {args.placements} placements, {args.logical} logical volumes, {args.layout} layout"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
