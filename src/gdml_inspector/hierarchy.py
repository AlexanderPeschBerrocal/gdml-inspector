"""The world-to-placement tree and the solids table, recorded during the shared
traversal. Used by the single-file viewer and by the lazy export's placements
asset, so both describe the same hierarchy identically."""

from __future__ import annotations

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
    already uses, dropping the always-implicit [0, 0, 0, 1] fourth row."""
    return [v for row in matrix[:3] for v in row]


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
        self._hierarchy: dict[str, HierarchyNodeDict] = {}

    def _hierarchy_node(self, path: str) -> HierarchyNodeDict:
        existing = self._hierarchy.get(path)
        if existing is not None:
            return existing
        name = path.rsplit("/", 1)[-1]
        node: HierarchyNodeDict = {
            "path": path,
            "parentPath": None,
            "name": name,
            "semanticName": semantic_name(name),
            "volume": name,
            "semanticVolume": semantic_name(name),
            "isAssembly": False,
            "rendered": False,
        }
        self._hierarchy[path] = node
        return node

    def _on_placement(
        self, path: str, parent_path: str, local_matrix: base.Mat4, is_assembly: bool
    ) -> None:
        super()._on_placement(path, parent_path, local_matrix, is_assembly)
        node = self._hierarchy_node(path)
        node["parentPath"] = parent_path
        node["isAssembly"] = is_assembly
        node["localMatrix"] = _flatten_matrix(local_matrix)

    def _on_volume(self, path: str, volume: base.VolumeDef, render: bool) -> None:
        super()._on_volume(path, volume, render)
        node = self._hierarchy_node(path)
        node["volume"] = volume.name
        node["semanticVolume"] = semantic_name(volume.name)
        node["rendered"] = render

    def _on_object_added(
        self, path: str, volume: base.VolumeDef, matrix: base.Mat4, object_id: int
    ) -> None:
        super()._on_object_added(path, volume, matrix, object_id)
        node = self._hierarchy_node(path)
        node["objectId"] = object_id
        node["globalMatrix"] = _flatten_matrix(matrix)

    def hierarchy_nodes(self) -> list[HierarchyNodeDict]:
        """The tree as a flat, pre-order list (dicts preserve insertion
        order, and nodes are inserted exactly in traversal order); each
        rendered node already carries its objectId and global matrix."""
        return list(self._hierarchy.values())

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
