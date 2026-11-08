"""Optional pyg4ometry tessellation and mesh compatibility adapters."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO, cast

from .geometry import GDMLSceneBuilder, Triangle, Vec3, add_tri


def maybe_call(obj: object, names: Sequence[str]) -> object | None:
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if callable(value):
                try:
                    # pyg4ometry's return type varies by release; narrowed to
                    # object at this boundary rather than trusted as Any.
                    return cast(object, value())
                except TypeError:
                    continue
            return cast(object, value)
    return None


def numeric_triplet(value: object) -> Vec3 | None:
    seen: set[int] = set()
    return _numeric_triplet(value, seen)


def _numeric_triplet(value: object, seen: set[int]) -> Vec3 | None:
    if value is None:
        return None
    obj_id = id(value)
    if obj_id in seen:
        return None
    seen.add(obj_id)

    if hasattr(value, "eval"):
        try:
            value = value.eval()
        except TypeError:
            pass

    if hasattr(value, "pos"):
        point = _numeric_triplet(value.pos, seen)
        if point is not None:
            return point

    if all(hasattr(value, axis) for axis in ("x", "y", "z")):
        try:
            coords = []
            for axis in ("x", "y", "z"):
                attr = getattr(value, axis)
                coords.append(float(attr() if callable(attr) else attr))
            return (coords[0], coords[1], coords[2])
        except (TypeError, ValueError):
            pass

    if hasattr(value, "tolist"):
        try:
            value = value.tolist()
        except TypeError:
            pass

    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except (TypeError, ValueError):
            return None

    if not isinstance(value, (str, bytes)):
        try:
            # pyg4ometry return values are untyped at this boundary; narrowed
            # to object, so list() needs an explicit escape here.
            seq = list(value)  # type: ignore[call-overload]
        except TypeError:
            seq = []
        if len(seq) >= 3:
            try:
                return (float(seq[0]), float(seq[1]), float(seq[2]))
            except (TypeError, ValueError):
                pass
    return None


def triangulate_vertex_face_mesh(vertices: object, faces: object) -> list[Triangle]:
    # pyg4ometry's vertex/face containers are untyped at this boundary;
    # narrowed to object, so list() needs an explicit escape here.
    verts_raw = list(vertices)  # type: ignore[call-overload]
    verts: list[Vec3] = []
    for item in verts_raw:
        point = numeric_triplet(item)
        if point is not None:
            verts.append(point)
    if not verts:
        return []

    tris: list[Triangle] = []
    if hasattr(faces, "tolist"):
        faces = faces.tolist()
    if isinstance(faces, tuple):
        faces = list(faces)

    face_list: list[object]
    if isinstance(faces, list) and faces and all(isinstance(x, int) for x in faces):
        face_list = []
        i = 0
        while i < len(faces):
            n = int(faces[i])
            face_list.append(faces[i + 1 : i + 1 + n])
            i += n + 1
    else:
        # faces may still be an arbitrary untyped iterable here.
        face_list = list(faces)  # type: ignore[call-overload]

    for face in face_list:
        if hasattr(face, "tolist"):
            face = face.tolist()
        if not isinstance(face, (list, tuple)) or len(face) < 3:
            continue
        try:
            indices = [int(x) for x in face]
        except (TypeError, ValueError):
            continue
        for i in range(1, len(indices) - 1):
            a, b, c = indices[0], indices[i], indices[i + 1]
            if 0 <= a < len(verts) and 0 <= b < len(verts) and 0 <= c < len(verts):
                add_tri(tris, verts[a], verts[b], verts[c])
    return tris


def parse_off_mesh(path: Path) -> list[Triangle]:
    """Parse a simple OFF mesh written by pyg4ometry/pycgal."""

    def next_data_line(handle: TextIO) -> str:
        while True:
            line = handle.readline()
            if not line:
                return ""
            line = line.strip()
            if line and not line.startswith("#"):
                return line

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        header = next_data_line(handle)
        if header == "OFF":
            counts_line = next_data_line(handle)
        elif header.startswith("OFF"):
            counts_line = header[3:].strip()
        else:
            raise ValueError(f"Not an OFF mesh: {header!r}")

        counts = counts_line.split()
        if len(counts) < 2:
            raise ValueError(f"Malformed OFF counts line: {counts_line!r}")
        n_vertices = int(counts[0])
        n_faces = int(counts[1])

        vertices: list[Vec3] = []
        for _ in range(n_vertices):
            parts = next_data_line(handle).split()
            if len(parts) < 3:
                raise ValueError("Malformed OFF vertex line")
            vertices.append((float(parts[0]), float(parts[1]), float(parts[2])))

        tris: list[Triangle] = []
        for _ in range(n_faces):
            parts = next_data_line(handle).split()
            if len(parts) < 4:
                continue
            n = int(parts[0])
            indices = [int(x) for x in parts[1 : 1 + n]]
            if len(indices) < 3:
                continue
            for i in range(1, len(indices) - 1):
                a, b, c = indices[0], indices[i], indices[i + 1]
                if (
                    0 <= a < len(vertices)
                    and 0 <= b < len(vertices)
                    and 0 <= c < len(vertices)
                ):
                    add_tri(tris, vertices[a], vertices[b], vertices[c])
    return tris


def extract_triangles_via_off(mesh: object) -> list[Triangle]:
    write_off = getattr(mesh, "writeOff", None)
    if not callable(write_off):
        return []
    fd, name = tempfile.mkstemp(suffix=".off")
    os.close(fd)
    path = Path(name)
    try:
        try:
            write_off(str(path))
        except TypeError:
            write_off(path)
        if not path.exists() or path.stat().st_size == 0:
            return []
        return parse_off_mesh(path)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def extract_triangles_from_pyg4ometry_mesh(mesh: object) -> list[Triangle]:
    """Best-effort adapter for pyg4ometry/pycsg mesh objects.

    pyg4ometry has changed mesh internals across releases. This adapter accepts
    the common forms: vertex/face arrays, polygon lists, and objects exposing a
    conversion method such as toVerticesAndPolygons().
    """

    for method in (
        "toVerticesAndPolygons",
        "toVerticesAndFaces",
        "toVerticesAndTriangles",
    ):
        converted = maybe_call(mesh, [method])
        if isinstance(converted, tuple) and len(converted) >= 2:
            tris = triangulate_vertex_face_mesh(converted[0], converted[1])
            if tris:
                return tris
        if isinstance(converted, dict):
            vertices = (
                converted.get("vertices")
                or converted.get("verts")
                or converted.get("points")
            )
            faces = (
                converted.get("polygons")
                or converted.get("faces")
                or converted.get("triangles")
            )
            if vertices is not None and faces is not None:
                tris = triangulate_vertex_face_mesh(vertices, faces)
                if tris:
                    return tris

    vertices = maybe_call(mesh, ["vertices", "verts", "vertexList", "points"])
    faces = maybe_call(mesh, ["faces", "facets", "triangles", "polygons"])
    if vertices is not None and faces is not None:
        tris = triangulate_vertex_face_mesh(vertices, faces)
        if tris:
            return tris

    polygons = maybe_call(mesh, ["polygons"])
    if polygons is None and isinstance(mesh, (list, tuple)):
        polygons = mesh
    polygon_tris: list[Triangle] = []
    if polygons is not None:
        # polygons/raw_vertices are untyped pyg4ometry containers at this
        # boundary; narrowed to object, so list() needs an explicit escape.
        for polygon in list(polygons):  # type: ignore[call-overload]
            raw_vertices = maybe_call(polygon, ["vertices", "verts", "points"])
            if raw_vertices is None and isinstance(polygon, (list, tuple)):
                raw_vertices = polygon
            if raw_vertices is None:
                continue
            verts = [
                p
                for p in (
                    numeric_triplet(v)
                    for v in list(raw_vertices)  # type: ignore[call-overload]
                )
                if p is not None
            ]
            for i in range(1, len(verts) - 1):
                add_tri(polygon_tris, verts[0], verts[i], verts[i + 1])
    if polygon_tris:
        return polygon_tris

    return extract_triangles_via_off(mesh)


def short_repr(value: object, limit: int = 240) -> str:
    text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def public_attrs(value: object, limit: int = 80) -> list[str]:
    return [name for name in dir(value) if not name.startswith("_")][:limit]


def describe_object(label: str, value: object, depth: int = 0) -> list[str]:
    indent = "  " * depth
    lines = [
        f"{indent}{label}: type={type(value).__module__}.{type(value).__name__}",
        f"{indent}{label}: repr={short_repr(value)}",
        f"{indent}{label}: attrs={', '.join(public_attrs(value))}",
    ]
    return lines


def debug_pyg4ometry_mesh(gdml_path: Path, solid_name: str | None = None) -> int:
    try:
        from pyg4ometry import gdml as pyg4_gdml
    except ModuleNotFoundError:
        print(
            "pyg4ometry is not installed in this Python environment.", file=sys.stderr
        )
        return 3

    try:
        reader = pyg4_gdml.Reader(str(gdml_path), reduceNISTMaterialsToPredefined=True)
    except TypeError:
        reader = pyg4_gdml.Reader(str(gdml_path))
    registry = reader.getRegistry()
    solids = getattr(registry, "solidDict", {})
    if not solids:
        print("No pyg4ometry solids found in registry.", file=sys.stderr)
        return 4

    if solid_name:
        solid = solids.get(solid_name)
        if solid is None:
            print(f"Solid not found: {solid_name}", file=sys.stderr)
            print("First available solids:")
            for name in list(solids)[:20]:
                print(f"  {name}")
            return 4
    else:
        solid_name, solid = next(iter(solids.items()))

    print(f"Inspecting solid: {solid_name}")
    for line in describe_object("solid", solid):
        print(line)
    mesh_func = getattr(solid, "mesh", None)
    if not callable(mesh_func):
        print("solid.mesh is not callable.")
        return 5

    mesh = mesh_func()
    for line in describe_object("mesh", mesh):
        print(line)
    converted = maybe_call(mesh, ["toVerticesAndPolygons"])
    if converted is not None:
        for line in describe_object("mesh.toVerticesAndPolygons()", converted):
            print(line)
        if isinstance(converted, tuple):
            print(f"toVerticesAndPolygons tuple length: {len(converted)}")
            for i, item in enumerate(converted[:2]):
                item_len: int | str
                try:
                    item_len = len(item)
                except TypeError:
                    item_len = "unknown"
                print(
                    f"  item {i}: type={type(item).__module__}.{type(item).__name__}, len={item_len}, repr={short_repr(item)}"
                )
    tris = extract_triangles_from_pyg4ometry_mesh(mesh)
    print(f"extract_triangles_from_pyg4ometry_mesh -> {len(tris)} triangles")
    if tris:
        print(f"first triangle: {tris[0]}")
        return 0

    try:
        off_tris = extract_triangles_via_off(mesh)
        print(f"extract_triangles_via_off -> {len(off_tris)} triangles")
        if off_tris:
            print(f"first OFF triangle: {off_tris[0]}")
            return 0
    except Exception as exc:
        print(f"extract_triangles_via_off failed: {exc}")

    polygons = maybe_call(mesh, ["polygons"])
    if polygons is not None:
        try:
            # polygons is an untyped pyg4ometry container narrowed to object.
            polygon_list = list(polygons)  # type: ignore[call-overload]
        except TypeError:
            polygon_list = []
        print(f"polygons: {len(polygon_list)}")
        if polygon_list:
            poly = polygon_list[0]
            for line in describe_object("polygon[0]", poly):
                print(line)
            raw_vertices = maybe_call(poly, ["vertices", "verts", "points"])
            print(f"polygon[0] vertices attr: {short_repr(raw_vertices)}")
            if raw_vertices is not None:
                try:
                    # Same as above: untyped pyg4ometry container.
                    verts = list(raw_vertices)  # type: ignore[call-overload]
                except TypeError:
                    verts = []
                print(f"polygon[0] vertex count: {len(verts)}")
                if verts:
                    vertex = verts[0]
                    for line in describe_object("vertex[0]", vertex):
                        print(line)
                    print(f"numeric_triplet(vertex[0]) -> {numeric_triplet(vertex)}")
                    if hasattr(vertex, "pos"):
                        pos = vertex.pos
                        for line in describe_object("vertex[0].pos", pos):
                            print(line)
                        print(
                            f"numeric_triplet(vertex[0].pos) -> {numeric_triplet(pos)}"
                        )

    vertices = maybe_call(mesh, ["vertices", "verts", "vertexList", "points"])
    faces = maybe_call(mesh, ["faces", "facets", "triangles", "polygons"])
    print(f"mesh vertices attr: {short_repr(vertices)}")
    print(f"mesh faces attr: {short_repr(faces)}")
    return 6


class Pyg4ometrySceneBuilder(GDMLSceneBuilder):
    """Scene builder that asks pyg4ometry to tessellate each GDML solid.

    The XML traversal remains local because it gives stable names/paths and lets
    the same WebGL frontend work without depending on pyg4ometry in the browser.
    """

    def parse(self) -> None:
        self.backend_name = "pyg4ometry"
        super().parse()
        try:
            from pyg4ometry import gdml as pyg4_gdml
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "pyg4ometry is not installed. Install it in this environment or use --backend python."
            ) from exc

        try:
            reader = pyg4_gdml.Reader(
                str(self.gdml), reduceNISTMaterialsToPredefined=True
            )
        except TypeError:
            reader = pyg4_gdml.Reader(str(self.gdml))
        registry = reader.getRegistry()
        solid_dict = getattr(registry, "solidDict", None)
        if solid_dict is None:
            raise RuntimeError(
                "Could not find pyg4ometry registry.solidDict after reading GDML."
            )
        self.pyg4ometry_solids = solid_dict

    def _diagnose_fallback(self, ref: str, message: str) -> None:
        self.scene.diagnose(
            "pyg4ometry-fallback",
            "warning",
            "backend",
            "solid",
            message,
            identifier=ref,
            backend="pyg4ometry",
        )

    def solid_mesh(self, ref: str) -> tuple[list[Triangle], str]:
        key = f"pyg4ometry:{ref}"
        if key in self.mesh_cache:
            return self.mesh_cache[key]
        solid = getattr(self, "pyg4ometry_solids", {}).get(ref)
        if solid is None:
            self._diagnose_fallback(ref, f"pyg4ometry missing solid reference: {ref}")
            return super().solid_mesh(ref)
        mesh_func = getattr(solid, "mesh", None)
        if not callable(mesh_func):
            self._diagnose_fallback(
                ref, f"pyg4ometry solid has no mesh() method: {ref}"
            )
            return super().solid_mesh(ref)
        try:
            mesh = mesh_func()
            tris = extract_triangles_from_pyg4ometry_mesh(mesh)
        except Exception as exc:
            self._diagnose_fallback(ref, f"pyg4ometry mesh failed for {ref}: {exc}")
            return super().solid_mesh(ref)
        if not tris:
            self._diagnose_fallback(
                ref, f"pyg4ometry returned no extractable triangles for {ref}"
            )
            return super().solid_mesh(ref)
        kind = f"pyg4ometry/{type(solid).__name__}"
        self.mesh_cache[key] = (tris, kind)
        return self.mesh_cache[key]
