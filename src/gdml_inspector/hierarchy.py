"""The world-to-placement tree and the solids table, recorded during the shared
traversal. Used by the single-file viewer and by the lazy export's placements
asset, so both describe the same hierarchy identically."""

from __future__ import annotations

from array import array
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from typing_extensions import NotRequired

from . import geometry as base
from .geometry import SolidInfo, semantic_name, solid_params, tag_name
from .pyg4ometry_backend import Pyg4ometrySceneBuilder


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
    already uses, dropping the always-implicit [0, 0, 0, 1] fourth row. Always
    floats, so a transform reads the same whichever way it was stored."""
    return [float(v) for row in matrix[:3] for v in row]


_ZERO_MATRIX = array("d", [0.0] * 12)
RENDERED, ASSEMBLY, HAS_LOCAL, HAS_GLOBAL = 1, 2, 4, 8


class HierarchyColumns:
    """The placement tree as parallel columns instead of one dict per node.

    A node costs a path string (shared with the traversal's own), a parent
    index, a volume-name reference, a flag byte, an object id and two 3x4
    matrices in flat arrays: a few hundred bytes where a dict of dicts and lists
    cost about 1.4 KB. Nodes are numbered in creation (pre-)order, so a parent's
    index is always below its children's. ``materialize()`` rebuilds the
    HierarchyNodeDict records on demand (the single-file viewer's embed and the
    tests); the lazy exporter reads the columns directly."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.index: dict[str, int] = {}
        self.parent = array("i")
        self.volume: list[str | None] = []  # None: never traversed, so the name
        self.flags = bytearray()
        self.object_id = array("i")
        self.local = array("d")
        self.global_ = array("d")

    def __len__(self) -> int:
        return len(self.paths)

    def node(self, path: str) -> int:
        existing = self.index.get(path)
        if existing is not None:
            return existing
        i = len(self.paths)
        self.paths.append(path)
        self.index[path] = i
        self.parent.append(-1)
        self.volume.append(None)
        self.flags.append(0)
        self.object_id.append(-1)
        self.local.extend(_ZERO_MATRIX)
        self.global_.extend(_ZERO_MATRIX)
        return i

    def name(self, i: int) -> str:
        return self.paths[i].rsplit("/", 1)[-1]

    def segment(self, i: int) -> str:
        """The path below the parent (the whole path for the root)."""
        parent = self.parent[i]
        return (
            self.paths[i]
            if parent < 0
            else self.paths[i][len(self.paths[parent]) + 1 :]
        )

    def volume_name(self, i: int) -> str:
        return self.volume[i] or self.name(i)

    def set_matrix(self, target: array, i: int, matrix: base.Mat4) -> None:
        target[i * 12 : i * 12 + 12] = array(
            "d", [float(v) for row in matrix[:3] for v in row]
        )

    def matrix(self, source: array, i: int) -> list[float]:
        return list(source[i * 12 : i * 12 + 12])

    def materialize(self) -> list[HierarchyNodeDict]:
        nodes: list[HierarchyNodeDict] = []
        for i, path in enumerate(self.paths):
            name = self.name(i)
            volume = self.volume_name(i)
            parent = self.parent[i]
            flags = self.flags[i]
            node: HierarchyNodeDict = {
                "path": path,
                "parentPath": None if parent < 0 else self.paths[parent],
                "name": name,
                "semanticName": semantic_name(name),
                "volume": volume,
                "semanticVolume": semantic_name(volume),
                "isAssembly": bool(flags & ASSEMBLY),
                "rendered": bool(flags & RENDERED),
            }
            if flags & HAS_LOCAL:
                node["localMatrix"] = self.matrix(self.local, i)
            if self.object_id[i] >= 0:
                node["objectId"] = self.object_id[i]
            if flags & HAS_GLOBAL:
                node["globalMatrix"] = self.matrix(self.global_, i)
            nodes.append(node)
        return nodes


class HierarchyMixin(base.GDMLSceneBuilder):
    """Records the full placement tree -- including non-rendered container
    and assembly nodes that add_volume() never sees -- keyed by path, the
    same way GltfMixin records flat per-path records for its own node
    tree. Building the nested JSON array happens afterward in
    hierarchy_nodes(), not here.

    Declares GDMLSceneBuilder as its base purely so mypy can check this
    mixin's own super() calls and self.solids access against a real contract, matching diff.py's DiffMixin precedent; the actual
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
        self.hierarchy = HierarchyColumns()

    def _on_placement(
        self, path: str, parent_path: str, local_matrix: base.Mat4, is_assembly: bool
    ) -> None:
        super()._on_placement(path, parent_path, local_matrix, is_assembly)
        h = self.hierarchy
        i = h.node(path)
        h.parent[i] = h.node(parent_path)
        h.flags[i] = (h.flags[i] & ~ASSEMBLY) | (ASSEMBLY if is_assembly else 0)
        h.set_matrix(h.local, i, local_matrix)
        h.flags[i] |= HAS_LOCAL

    def _on_volume(self, path: str, volume: base.VolumeDef, render: bool) -> None:
        super()._on_volume(path, volume, render)
        h = self.hierarchy
        i = h.node(path)
        h.volume[i] = volume.name
        h.flags[i] = (h.flags[i] & ~RENDERED) | (RENDERED if render else 0)

    def _on_object_added(
        self, path: str, volume: base.VolumeDef, matrix: base.Mat4, object_id: int
    ) -> None:
        super()._on_object_added(path, volume, matrix, object_id)
        h = self.hierarchy
        i = h.node(path)
        h.object_id[i] = object_id
        h.set_matrix(h.global_, i, matrix)
        h.flags[i] |= HAS_GLOBAL

    def hierarchy_nodes(self) -> list[HierarchyNodeDict]:
        """The tree as a flat, pre-order list; each rendered node already
        carries its objectId and global matrix. Built on demand from the
        columns (the lazy exporter reads ``self.hierarchy`` directly)."""
        return self.hierarchy.materialize()

    def solids_table(self) -> dict[str, SolidInfo]:
        """Each parsed solid's kind and own declared parameters, keyed by
        GDML ref and shared across every placement that references it --
        the same construction diff.py's DiffMixin.to_dict() already does."""
        return {
            ref: {"type": tag_name(el), "params": solid_params(el)}
            for ref, el in self.solids.items()
        }


class HierarchyGDMLSceneBuilder(HierarchyMixin, base.GDMLSceneBuilder):
    pass


class HierarchyPyg4ometrySceneBuilder(HierarchyMixin, Pyg4ometrySceneBuilder):
    pass
