"""Shared scene and helpers for the lazy-viewer browser tests."""

import json
import re
import shutil
import struct

from .browser_helpers import VIEWPORT, next_frames

QUALITY = ["--backend", "python", "--quality", "medium"]
LAYER = {
    "world/ecal_a": "ECAL\x1fBarrel layer 1",
    "world/ecal_c": "ECAL\x1fBarrel layer 1",  # shares a chunk with ecal_a
    "world/ecal_d": "ECAL\x1fBarrel layer 1",  # a second mesh in that chunk
    "world/hcal_b": "HCAL\x1fBarrel layer 1",
    "world/front": "Other\x1ffront_box",
    "world/back": "Other\x1fback_box",
    "world/group/inner": "Tracker\x1fBarrel r≈30 mm",  # "inner" is a tracker token
}


GDML_TEXT = """<?xml version="1.0"?>
<gdml><define/><materials/>
<solids>
<box name="world_box" x="1000" y="1000" z="1000" lunit="mm"/>
<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<box name="overlap_shape" x="20" y="20" z="6" lunit="mm"/>
<box name="wide_shape" x="6" y="3" z="3" lunit="mm"/>
</solids>
<structure>
<volume name="ecal1_vol"><materialref ref="G4_Fe"/><solidref ref="shape"/></volume>
<volume name="ecal1_wide"><materialref ref="G4_Fe"/><solidref ref="wide_shape"/></volume>
<volume name="hcal1_vol"><materialref ref="G4_Fe"/><solidref ref="shape"/></volume>
<volume name="child"><materialref ref="G4_Fe"/><solidref ref="shape"/></volume>
<volume name="container"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="inner"><volumeref ref="child"/>
    <position x="5" y="30" z="0" unit="mm"/></physvol>
</volume>
<volume name="empty_container"><materialref ref="Galactic"/><solidref ref="world_box"/></volume>
<volume name="front_box"><materialref ref="G4_Fe"/><solidref ref="overlap_shape"/></volume>
<volume name="back_box"><materialref ref="G4_Fe"/><solidref ref="overlap_shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="ecal_a"><volumeref ref="ecal1_vol"/>
    <position x="-50" y="0" z="0" unit="mm"/></physvol>
  <physvol name="ecal_c"><volumeref ref="ecal1_vol"/>
    <position x="-80" y="0" z="0" unit="mm"/></physvol>
  <physvol name="ecal_d"><volumeref ref="ecal1_wide"/>
    <position x="-65" y="0" z="0" unit="mm"/></physvol>
  <physvol name="hcal_b"><volumeref ref="hcal1_vol"/>
    <position x="50" y="0" z="0" unit="mm"/></physvol>
  <physvol name="group"><volumeref ref="container"/></physvol>
  <physvol name="empty"><volumeref ref="empty_container"/></physvol>
  <physvol name="front"><volumeref ref="front_box"/>
    <position x="0" y="0" z="100" unit="mm"/></physvol>
  <physvol name="back"><volumeref ref="back_box"/>
    <position x="0" y="0" z="-100" unit="mm"/></physvol>
</volume>
</structure>
<setup name="Default" version="1.0"><world ref="world"/></setup>
</gdml>
"""


def copy_of(lazy_dir, tmp_path):

    target = tmp_path / "export"
    shutil.copytree(lazy_dir, target)
    return target


def open_lazy(watched_page, served, directory, *, load="all", fetch_placements=True):
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(directory)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    if load == "all":
        page.click("#loadAll")
        page.wait_for_function("Object.values(states).every(s => s.loaded)")
    page.click("#viewXYPos")
    if fetch_placements:
        page.evaluate("ensurePlacements()")
        page.wait_for_function("placements !== null")
    next_frames(page)
    return page


_LAZY_PROJECT = """([wx, wy, wz]) => {
  const mvp = cameraMvp();
  const tx=mvp[0]*wx+mvp[4]*wy+mvp[8]*wz+mvp[12], ty=mvp[1]*wx+mvp[5]*wy+mvp[9]*wz+mvp[13], tw=mvp[3]*wx+mvp[7]*wy+mvp[11]*wz+mvp[15];
  const rect = canvas.getBoundingClientRect();
  return [(tx/tw*0.5+0.5)*rect.width + rect.left, (-ty/tw*0.5+0.5)*rect.height + rect.top];
}"""


def object_center(page, path):
    bbox = page.evaluate(
        f"placements.objects.find(o => o.path === {json.dumps(path)}).bbox"
    )
    return [(bbox[i] + bbox[i + 3]) / 2 for i in range(3)]


def click_object(page, path):
    sx, sy = page.evaluate(_LAZY_PROJECT, object_center(page, path))
    page.mouse.click(sx, sy)
    page.wait_for_function("true")  # let the click handler's microtasks run
    next_frames(page)


def selected(page):
    page.wait_for_function(
        "selectedObjectId === null || selectedPath !== null || !placements"
    )
    return page.evaluate("selectedPath")


_PIXEL = """([wx, wy, wz]) => {
  render();
  const mvp = cameraMvp();
  const tx=mvp[0]*wx+mvp[4]*wy+mvp[8]*wz+mvp[12], ty=mvp[1]*wx+mvp[5]*wy+mvp[9]*wz+mvp[13], tw=mvp[3]*wx+mvp[7]*wy+mvp[11]*wz+mvp[15];
  const px = new Uint8Array(4);
  gl.readPixels(Math.floor((tx/tw*0.5+0.5)*canvas.width), Math.floor((ty/tw*0.5+0.5)*canvas.height), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return Array.from(px);
}"""


def distance_to_highlight(pixel):
    return sum((a - b) ** 2 for a, b in zip(pixel[:3], (255, 209, 51), strict=True))


def downgrade_to_v1(export):
    """Rewrite an export as the previous format: v1 chunks, no placements."""
    for chunk in export.glob("*.assets/*.bin"):
        data = chunk.read_bytes()
        magic, _version, vertices, pos, colour, _id, _end = struct.unpack(
            "<8s6I", data[:32]
        )
        body = data[: colour + vertices * 4]
        chunk.write_bytes(
            struct.pack("<8s6I", magic, 1, vertices, pos, colour, len(body), len(body))
            + body[32:]
        )
    for placements in export.glob("*.assets/placements.json"):
        placements.unlink()
    html = export / "v.html"
    text = html.read_text(encoding="utf-8")
    match = re.search(r"const MANIFEST = (.*?);\nconst canvas = ", text, re.DOTALL)
    manifest = json.loads(match.group(1))
    manifest.pop("placements")
    manifest.pop("chunkFormat")
    for entry in manifest["chunks"]:
        entry.pop("sha256", None)
    html.write_text(
        text[: match.start(1)] + json.dumps(manifest) + text[match.end(1) :],
        encoding="utf-8",
    )
