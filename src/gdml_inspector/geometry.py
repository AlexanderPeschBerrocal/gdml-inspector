"""GDML scene graph, primitive meshing, and shared geometry helpers."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

Vec3 = tuple[float, float, float]
Mat4 = list[list[float]]
Triangle = tuple[Vec3, Vec3, Vec3]

LEN_UNITS = {
    None: 1.0,
    "": 1.0,
    "mm": 1.0,
    "millimeter": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "um": 0.001,
    "nm": 1e-6,
}
# Like Geant4's GDML reader, angles without a unit are in radians.
ANGLE_UNITS = {
    None: 1.0,
    "": 1.0,
    "deg": math.pi / 180.0,
    "degree": math.pi / 180.0,
    "rad": 1.0,
    "radian": 1.0,
}
QUALITY_SEGMENTS = {"fast": 12, "medium": 24, "high": 48}

MATERIAL_COLORS = {
    "Material_ECAL": (55, 130, 245, 185),
    "Material_HCAL": (235, 150, 35, 190),
    "G4_Si": (80, 195, 120, 220),
    "G4_Fe": (160, 165, 175, 225),
    "Galactic": (220, 230, 255, 18),
}


def tag_name(el: ET.Element | str) -> str:
    tag = el.tag if isinstance(el, ET.Element) else el
    return tag.rsplit("}", 1)[-1]


def child(el: ET.Element, name: str) -> ET.Element | None:
    for item in list(el):
        if tag_name(item) == name:
            return item
    return None


def children(el: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in list(el) if tag_name(item) == name]


def f(attrs: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(attrs.get(key, default))
    except (TypeError, ValueError):
        return default


def length(attrs: dict[str, str], key: str, default: float = 0.0) -> float:
    return f(attrs, key, default) * LEN_UNITS.get(
        attrs.get("lunit") or attrs.get("unit"), 1.0
    )


def angle(attrs: dict[str, str], key: str, default_deg: float = 0.0) -> float:
    """Return an angle in radians.

    A missing or unparseable attribute uses ``default_deg`` degrees; a present
    value without an aunit/unit falls back to radians, per the GDML schema.
    """
    try:
        value = float(attrs[key])
    except (KeyError, TypeError, ValueError):
        return math.radians(default_deg)
    return value * ANGLE_UNITS.get(attrs.get("aunit") or attrs.get("unit"), 1.0)


def identity() -> Mat4:
    return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def matmul(a: Mat4, b: Mat4) -> Mat4:
    out = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        for k in range(4):
            aik = a[i][k]
            if aik:
                for j in range(4):
                    out[i][j] += aik * b[k][j]
    return out


def translate(x: float, y: float, z: float) -> Mat4:
    m = identity()
    m[0][3], m[1][3], m[2][3] = x, y, z
    return m


def rot_x(a: float) -> Mat4:
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]]


def rot_y(a: float) -> Mat4:
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]]


def rot_z(a: float) -> Mat4:
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def euler_xyz(rx: float, ry: float, rz: float) -> Mat4:
    return matmul(rot_z(rz), matmul(rot_y(ry), rot_x(rx)))


def transform_point(m: Mat4, p: Vec3) -> Vec3:
    x, y, z = p
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
        m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
        m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3],
    )


def transform_mesh(tris: list[Triangle], m: Mat4) -> list[Triangle]:
    return [
        (transform_point(m, a), transform_point(m, b), transform_point(m, c))
        for a, b, c in tris
    ]


def parse_vec(el: ET.Element | None, unit_map: dict[str | None, float]) -> Vec3:
    if el is None:
        return (0.0, 0.0, 0.0)
    unit = unit_map.get(el.attrib.get("unit"), 1.0)
    return (
        f(el.attrib, "x") * unit,
        f(el.attrib, "y") * unit,
        f(el.attrib, "z") * unit,
    )


def parse_scale(el: ET.Element | None) -> Vec3:
    if el is None:
        return (1.0, 1.0, 1.0)
    return (f(el.attrib, "x", 1.0), f(el.attrib, "y", 1.0), f(el.attrib, "z", 1.0))


def scale_matrix(sx: float, sy: float, sz: float) -> Mat4:
    m = identity()
    m[0][0], m[1][1], m[2][2] = sx, sy, sz
    return m


def transform_element(
    parent: ET.Element, tag: str, definitions: dict[str, ET.Element] | None = None
) -> ET.Element | None:
    """Resolve an inline or named transform; never silently ignore a broken reference."""
    inline = child(parent, tag)
    if inline is not None:
        return inline
    reference = child(parent, f"{tag}ref")
    if reference is None:
        return None
    name = reference.attrib.get("ref", "")
    if definitions is None or name not in definitions:
        raise ValueError(f"Missing GDML {tag} reference: {name}")
    return definitions[name]


def placement_matrix(
    physvol: ET.Element,
    positions: dict[str, ET.Element] | None = None,
    rotations: dict[str, ET.Element] | None = None,
    scales: dict[str, ET.Element] | None = None,
) -> Mat4:
    """Return the transform that places a daughter volume in its mother.

    Like Geant4's GDML reader (G4GDMLReadStructure::PhysvolRead), the placement
    uses the inverse of the rotation matrix built from the x, y and z angles: a
    physvol rotation describes the rotation of the daughter's frame, so the
    volume itself is turned the opposite way. Boolean operands are different
    and use the rotation as given (see GDMLSceneBuilder.solid_mesh). An
    optional scale, typically a reflection such as x = -1, is applied before
    the rotation.
    """
    pos = parse_vec(transform_element(physvol, "position", positions), LEN_UNITS)
    rx, ry, rz = parse_vec(
        transform_element(physvol, "rotation", rotations), ANGLE_UNITS
    )
    sx, sy, sz = parse_scale(transform_element(physvol, "scale", scales))
    # (Rz Ry Rx)^-1 = Rx(-x) Ry(-y) Rz(-z)
    inverse_rotation = matmul(rot_x(-rx), matmul(rot_y(-ry), rot_z(-rz)))
    scale = scale_matrix(sx, sy, sz)
    return matmul(translate(*pos), matmul(inverse_rotation, scale))


def add_tri(tris: list[Triangle], a: Vec3, b: Vec3, c: Vec3) -> None:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    if nx * nx + ny * ny + nz * nz > 1e-16:
        tris.append((a, b, c))


def add_quad(tris: list[Triangle], a: Vec3, b: Vec3, c: Vec3, d: Vec3) -> None:
    add_tri(tris, a, b, c)
    add_tri(tris, a, c, d)


def mesh_tube_like(
    rmin0: float,
    rmax0: float,
    rmin1: float,
    rmax1: float,
    zlen: float,
    start: float,
    delta: float,
    segments: int,
    low_plane: Vec3 | None = None,
    high_plane: Vec3 | None = None,
) -> list[Triangle]:
    if zlen <= 0 or max(rmax0, rmax1) <= 0:
        return []
    frac = min(1.0, abs(delta) / (2 * math.pi))
    n = max(3, int(math.ceil(segments * frac)))
    full = abs(delta) >= 2 * math.pi * 0.999
    phis = [start + delta * i / n for i in range(n + 1)]
    z0, z1 = -zlen / 2.0, zlen / 2.0

    def z_at(base: float, plane: Vec3 | None, x: float, y: float) -> float:
        if plane is None or abs(plane[2]) < 1e-12:
            return base
        return base - (plane[0] * x + plane[1] * y) / plane[2]

    lo_o: list[Vec3] = []
    hi_o: list[Vec3] = []
    lo_i: list[Vec3] = []
    hi_i: list[Vec3] = []
    for phi in phis:
        co, si = math.cos(phi), math.sin(phi)
        x, y = rmax0 * co, rmax0 * si
        lo_o.append((x, y, z_at(z0, low_plane, x, y)))
        x, y = rmax1 * co, rmax1 * si
        hi_o.append((x, y, z_at(z1, high_plane, x, y)))
        x, y = rmin0 * co, rmin0 * si
        lo_i.append((x, y, z_at(z0, low_plane, x, y)))
        x, y = rmin1 * co, rmin1 * si
        hi_i.append((x, y, z_at(z1, high_plane, x, y)))

    tris: list[Triangle] = []
    has_hole = max(rmin0, rmin1) > 1e-9
    for i in range(n):
        j = i + 1
        add_quad(tris, lo_o[i], lo_o[j], hi_o[j], hi_o[i])
        if has_hole:
            add_quad(tris, lo_i[j], lo_i[i], hi_i[i], hi_i[j])
            add_quad(tris, lo_i[i], lo_i[j], lo_o[j], lo_o[i])
            add_quad(tris, hi_i[j], hi_i[i], hi_o[i], hi_o[j])
        else:
            add_tri(tris, (0, 0, z0), lo_o[j], lo_o[i])
            add_tri(tris, (0, 0, z1), hi_o[i], hi_o[j])
    if not full and has_hole:
        add_quad(tris, lo_i[0], lo_o[0], hi_o[0], hi_i[0])
        add_quad(tris, lo_o[-1], lo_i[-1], hi_i[-1], hi_o[-1])
    return tris


def mesh_box(attrs: dict[str, str]) -> list[Triangle]:
    x, y, z = length(attrs, "x") / 2, length(attrs, "y") / 2, length(attrs, "z") / 2
    v = [
        (-x, -y, -z),
        (x, -y, -z),
        (x, y, -z),
        (-x, y, -z),
        (-x, -y, z),
        (x, -y, z),
        (x, y, z),
        (-x, y, z),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    tris: list[Triangle] = []
    for a, b, c, d in faces:
        add_quad(tris, v[a], v[b], v[c], v[d])
    return tris


def mesh_solid_primitive(el: ET.Element, segments: int) -> list[Triangle]:
    attrs = el.attrib
    kind = tag_name(el)
    if kind == "box":
        return mesh_box(attrs)
    if kind == "tube":
        return mesh_tube_like(
            length(attrs, "rmin"),
            length(attrs, "rmax"),
            length(attrs, "rmin"),
            length(attrs, "rmax"),
            length(attrs, "z"),
            angle(attrs, "startphi"),
            angle(attrs, "deltaphi", 360),
            segments,
        )
    if kind == "cutTube":
        low = (f(attrs, "lowX"), f(attrs, "lowY"), f(attrs, "lowZ", -1.0))
        high = (f(attrs, "highX"), f(attrs, "highY"), f(attrs, "highZ", 1.0))
        return mesh_tube_like(
            length(attrs, "rmin"),
            length(attrs, "rmax"),
            length(attrs, "rmin"),
            length(attrs, "rmax"),
            length(attrs, "z"),
            angle(attrs, "startphi"),
            angle(attrs, "deltaphi", 360),
            segments,
            low,
            high,
        )
    if kind == "cone":
        return mesh_tube_like(
            length(attrs, "rmin1"),
            length(attrs, "rmax1"),
            length(attrs, "rmin2"),
            length(attrs, "rmax2"),
            length(attrs, "z"),
            angle(attrs, "startphi"),
            angle(attrs, "deltaphi", 360),
            segments,
        )
    return []


def color_for_material(name: str) -> tuple[int, int, int, int]:
    for key, rgba in MATERIAL_COLORS.items():
        if name.startswith(key):
            return rgba
    h = 2166136261
    for ch in name:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return (80 + (h & 127), 80 + ((h >> 8) & 127), 80 + ((h >> 16) & 127), 195)


def semantic_name(name: str) -> str:
    return re.sub(r"0x[0-9a-fA-F]+$", "", name)


@dataclass
class VolumeDef:
    name: str
    material: str
    solid: str
    element: ET.Element
    seq: int


@dataclass
class Scene:
    positions: list[float] = field(default_factory=list)
    colors: list[int] = field(default_factory=list)
    ids: list[int] = field(default_factory=list)
    objects: list[dict[str, object]] = field(default_factory=list)
    warnings: dict[str, int] = field(default_factory=dict)
    bbox_min: list[float] = field(default_factory=lambda: [float("inf")] * 3)
    bbox_max: list[float] = field(default_factory=lambda: [float("-inf")] * 3)

    def warn(self, message: str) -> None:
        self.warnings[message] = self.warnings.get(message, 0) + 1


class GDMLSceneBuilder:
    def __init__(
        self,
        gdml: Path,
        quality: str,
        include_containers: bool,
        include_world: bool,
        max_objects: int,
        max_vertices: int,
    ) -> None:
        self.gdml = gdml
        self.quality = quality
        self.segments = QUALITY_SEGMENTS[quality]
        self.include_containers = include_containers
        self.include_world = include_world
        self.max_objects = max_objects
        self.max_vertices = max_vertices
        self.root = ET.parse(gdml).getroot()
        self.solids: dict[str, ET.Element] = {}
        self.volumes: dict[str, VolumeDef] = {}
        self.assemblies: dict[str, ET.Element] = {}
        self.positions: dict[str, ET.Element] = {}
        self.rotations: dict[str, ET.Element] = {}
        self.scales: dict[str, ET.Element] = {}
        self.world = ""
        self.mesh_cache: dict[str, tuple[list[Triangle], str]] = {}
        self.scene = Scene()
        self.backend_name = "python"

    def parse(self) -> None:
        solids_el = child(self.root, "solids")
        structure = child(self.root, "structure")
        if solids_el is None or structure is None:
            raise ValueError("GDML must contain <solids> and <structure> sections.")
        define = child(self.root, "define")
        if define is not None:
            for el in children(define, "position"):
                if "name" in el.attrib:
                    self.positions[el.attrib["name"]] = el
            for el in children(define, "rotation"):
                if "name" in el.attrib:
                    self.rotations[el.attrib["name"]] = el
            for el in children(define, "scale"):
                if "name" in el.attrib:
                    self.scales[el.attrib["name"]] = el
        for el in list(solids_el):
            if "name" in el.attrib:
                self.solids[el.attrib["name"]] = el
        for el in children(structure, "assembly"):
            if "name" in el.attrib:
                self.assemblies[el.attrib["name"]] = el
        counts: dict[str, int] = {}
        for el in children(structure, "volume"):
            name = el.attrib.get("name", f"volume_{len(counts)}")
            seq = counts.get(name, 0)
            counts[name] = seq + 1
            mat = child(el, "materialref")
            solid = child(el, "solidref")
            self.volumes[name] = VolumeDef(
                name=name,
                material=mat.attrib.get("ref", "") if mat is not None else "",
                solid=solid.attrib.get("ref", "") if solid is not None else "",
                element=el,
                seq=seq,
            )
        setup = child(self.root, "setup")
        world = child(setup, "world") if setup is not None else None
        self.world = (
            world.attrib.get("ref", "")
            if world is not None
            else next(reversed(self.volumes))
        )

    def solid_mesh(self, ref: str) -> tuple[list[Triangle], str]:
        if ref in self.mesh_cache:
            return self.mesh_cache[ref]
        el = self.solids.get(ref)
        if el is None:
            self.scene.warn(f"Missing solid reference: {ref}")
            self.mesh_cache[ref] = ([], "missing")
            return self.mesh_cache[ref]
        kind = tag_name(el)
        if kind in {"box", "tube", "cutTube", "cone"}:
            tris = mesh_solid_primitive(el, self.segments)
        elif kind in {"subtraction", "intersection", "union"}:
            first = child(el, "first")
            first_ref = first.attrib.get("ref", "") if first is not None else ""
            tris, subkind = self.solid_mesh(first_ref)
            # Like the second operand, the first operand's optional transform
            # uses the rotation as given (G4GDMLReadSolids::BooleanRead).
            first_m = matmul(
                translate(*parse_vec(child(el, "firstposition"), LEN_UNITS)),
                euler_xyz(*parse_vec(child(el, "firstrotation"), ANGLE_UNITS)),
            )
            tris = transform_mesh(tris, first_m)
            if kind == "union":
                second = child(el, "second")
                tris2, _ = self.solid_mesh(
                    second.attrib.get("ref", "") if second is not None else ""
                )
                m = matmul(
                    translate(*parse_vec(child(el, "position"), LEN_UNITS)),
                    euler_xyz(*parse_vec(child(el, "rotation"), ANGLE_UNITS)),
                )
                tris = tris + transform_mesh(tris2, m)
            else:
                self.scene.warn(
                    f"{kind} solid {ref} approximated by first operand {first_ref}"
                )
            kind = f"{kind}/{subkind}"
        else:
            tris = []
            self.scene.warn(f"Unsupported solid type {kind}: {ref}")
        self.mesh_cache[ref] = (tris, kind)
        return self.mesh_cache[ref]

    def add_volume(self, volume: VolumeDef, matrix: Mat4, path: str) -> None:
        tris, solid_type = self.solid_mesh(volume.solid)
        if not tris:
            return
        obj_id = len(self.scene.objects)
        rgba = color_for_material(volume.material)
        first = len(self.scene.positions) // 3
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for tri in tris:
            for point in tri:
                tp = transform_point(matrix, point)
                self.scene.positions.extend(tp)
                self.scene.colors.extend(rgba)
                self.scene.ids.append(obj_id)
                for i in range(3):
                    lo[i] = min(lo[i], tp[i])
                    hi[i] = max(hi[i], tp[i])
                    self.scene.bbox_min[i] = min(self.scene.bbox_min[i], tp[i])
                    self.scene.bbox_max[i] = max(self.scene.bbox_max[i], tp[i])
        count = len(self.scene.positions) // 3 - first
        self.scene.objects.append(
            {
                "id": obj_id,
                "name": path.rsplit("/", 1)[-1],
                "semanticName": semantic_name(path.rsplit("/", 1)[-1]),
                "volume": volume.name,
                "semanticVolume": semantic_name(volume.name),
                "material": volume.material or "(none)",
                "solid": volume.solid,
                "solidType": solid_type,
                "firstVertex": first,
                "vertexCount": count,
                "bbox": [*lo, *hi],
                "path": path,
            }
        )

    def place_physvols(
        self, physvols: list[ET.Element], matrix: Mat4, path: str
    ) -> None:
        """Traverse the daughters placed by ``physvols`` in a mother at ``matrix``."""
        for pv in physvols:
            ref_el = child(pv, "volumeref")
            if ref_el is None:
                continue
            ref = ref_el.attrib.get("ref", "")
            pv_name = pv.attrib.get("name", ref)
            child_matrix = matmul(
                matrix,
                placement_matrix(pv, self.positions, self.rotations, self.scales),
            )
            # Like Geant4, a reference names an assembly before a logical volume.
            # An assembly's physvols are placed relative to its own placement.
            assembly = self.assemblies.get(ref)
            if assembly is not None:
                self.place_physvols(
                    children(assembly, "physvol"), child_matrix, f"{path}/{pv_name}"
                )
            else:
                child_vol = self.volumes.get(ref)
                if child_vol is None:
                    self.scene.warn(f"Missing volume reference: {ref}")
                    continue
                self.traverse(child_vol, child_matrix, f"{path}/{pv_name}")
            if (
                len(self.scene.objects) >= self.max_objects
                or len(self.scene.positions) // 3 >= self.max_vertices
            ):
                self.scene.warn("Stopped at configured object/vertex limit")
                return

    def traverse(self, volume: VolumeDef, matrix: Mat4, path: str) -> None:
        if (
            len(self.scene.objects) >= self.max_objects
            or len(self.scene.positions) // 3 >= self.max_vertices
        ):
            return
        phys = children(volume.element, "physvol")
        container = bool(phys) or volume.material.startswith("Galactic")
        render = (self.include_containers or not container) and (
            self.include_world or volume.name != self.world
        )
        if render:
            self.add_volume(volume, matrix, path)
        self.place_physvols(phys, matrix, path)

    def build(self) -> dict[str, object]:
        self.parse()
        world = self.volumes.get(self.world)
        if world is None:
            raise ValueError(f"World volume {self.world!r} was not found.")
        self.traverse(world, identity(), self.world)
        if self.scene.bbox_min[0] == float("inf"):
            self.scene.bbox_min = [-1, -1, -1]
            self.scene.bbox_max = [1, 1, 1]
        return self.to_dict()

    def to_dict(self) -> dict[str, object]:
        materials: dict[str, int] = {}
        material_colors: dict[str, list[int]] = {}
        solid_types: dict[str, int] = {}
        bases: dict[str, int] = {}
        for obj in self.scene.objects:
            materials[str(obj["material"])] = materials.get(str(obj["material"]), 0) + 1
            material_colors[str(obj["material"])] = list(
                color_for_material(str(obj["material"]))
            )
            solid_types[str(obj["solidType"])] = (
                solid_types.get(str(obj["solidType"]), 0) + 1
            )
            bases[str(obj["semanticVolume"])] = (
                bases.get(str(obj["semanticVolume"]), 0) + 1
            )
        return {
            "meta": {
                "source": str(self.gdml),
                "sourceFile": self.gdml.name,
                "backend": self.backend_name,
                "quality": self.quality,
                "world": self.world,
                "objects": len(self.scene.objects),
                "vertices": len(self.scene.positions) // 3,
                "triangles": len(self.scene.positions) // 9,
                "bbox": [*self.scene.bbox_min, *self.scene.bbox_max],
                "materials": materials,
                "materialColors": material_colors,
                "solidTypes": solid_types,
                "semanticVolumeCounts": dict(sorted(bases.items())),
                "warnings": [
                    f"{msg} (x{n})" if n > 1 else msg
                    for msg, n in sorted(self.scene.warnings.items())
                ],
            },
            "positions": self.scene.positions,
            "colors": self.scene.colors,
            "ids": self.scene.ids,
            "objects": self.scene.objects,
        }
