"""A small, documented Python API for reading a GDML scene.

This is the repo-local slice of issue #12: a public entry point other tools
can import to select placements/materials and obtain meshes/transforms and
bounds, without depending on this project's browser-specific HTML export or
on Streamlit/Plotly/ROOT. Importing this module pulls in no optional
dependency; ``Geometry.load(backend="pyg4ometry")`` probes for pyg4ometry
itself, only when that backend is actually requested. The separate ROOT
Explorer adapter and shared-fixture parity work are a different repository's
change, not part of this module (see docs/limitations.md).

Units and conventions
----------------------
All lengths are millimetres, this project's native/unconverted GDML unit
(unlike ``gltf.py``, which converts to metres for glTF's own convention).
Every :class:`Placement` and :class:`Mesh` is in this same unit. A
:data:`~gdml_inspector.geometry.Mat4` is row-major, with translation at
``matrix[row][3]`` -- exactly ``geometry.py``'s existing internal
convention, applied with ``geometry.transform_point``/``transform_mesh``.

Placement and mesh identities
------------------------------
:attr:`Geometry.placements` contains only placements that actually
rendered: ``include_containers``/``include_world`` control which, exactly
as they do for every other export mode in this project. :attr:`Geometry.solids`
is **not** a 1:1 reflection of every ``<solid>`` in the source GDML -- only
solids actually used by a placement that rendered appear (a Boolean solid's
operand-only refs, and solids behind a hidden/unrendered container, never
appear). Two placements that reference the same solid share the *same*
:class:`Mesh` object (``is``, not just ``==``), so a consumer can cache or
upload each mesh once. That identity (the GDML solid ref string) is only
meaningful *within* one :meth:`Geometry.load` call, not as a cross-call
cache key: quality and backend are fixed per load, so a different quality
setting produces a different mesh for the same ref in a different
``Geometry`` instance.

Resource-budget semantics
--------------------------
``max_objects``/``max_vertices`` behave exactly as in every other export
mode: traversal stops early and a ``"limit-reached"`` diagnostic
(``category="budget"``) appears in :attr:`Geometry.diagnostics` -- this is
not raised as an exception. Check ``.diagnostics`` to detect a truncated
result.

Error behaviour
-----------------
:meth:`Geometry.load` raises ``ValueError`` for malformed GDML structure
(missing ``<solids>``/``<structure>``, a cyclic volume/assembly reference,
or nesting beyond 256 levels -- unchanged from ``geometry.py``'s existing
``parse()``/``traverse()``/``place_physvols()``). A missing file raises the
natural ``FileNotFoundError``/``xml.etree.ElementTree.ParseError`` -- this
is a library call, not a CLI, so there is no exit-code translation.
``backend="pyg4ometry"`` without pyg4ometry installed raises
``ModuleNotFoundError`` directly.

Compatibility
--------------
Supports the same Python versions as the package (``requires-python`` in
pyproject.toml). Breaking changes to the dataclasses in this module are
documented in CHANGELOG.md along with migration guidance.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import geometry as base
from .pyg4ometry_backend import Pyg4ometrySceneBuilder


@dataclass(frozen=True)
class Mesh:
    """A solid's local-space triangle mesh, untransformed, in millimetres."""

    triangles: tuple[base.Triangle, ...]
    solid_type: str


@dataclass(frozen=True)
class Placement:
    """One rendered placement: a world-space transform plus the identity of
    the material/solid it uses."""

    path: str
    semantic_path: str
    volume: str
    material: str
    solid: str
    matrix: base.Mat4


@dataclass(frozen=True)
class Bounds:
    min: tuple[float, float, float]
    max: tuple[float, float, float]


@dataclass(frozen=True)
class Geometry:
    placements: tuple[Placement, ...]
    solids: dict[str, Mesh]
    diagnostics: tuple[base.Diagnostic, ...]
    bounds: Bounds
    source: Path
    backend: str

    @classmethod
    def load(
        cls,
        gdml: Path,
        *,
        backend: str = "auto",
        quality: str = "medium",
        include_containers: bool = False,
        include_world: bool = False,
        max_objects: int = 100_000,
        max_vertices: int = 5_000_000,
    ) -> Geometry:
        """Parse and traverse ``gdml``, returning a typed, immutable view.

        ``backend="auto"`` silently falls back to the pure-Python backend
        if pyg4ometry is not importable; ``backend="pyg4ometry"`` lets
        ``ModuleNotFoundError`` propagate unguarded if it is missing (this
        probes for pyg4ometry itself rather than deferring to
        ``Pyg4ometrySceneBuilder.parse()``, which wraps a missing import in
        ``RuntimeError`` instead); ``backend="python"`` never imports
        pyg4ometry at all. Unlike the CLI modes, nothing is printed to
        stdout -- this is a library call.
        """
        builder_cls = ApiGDMLSceneBuilder
        if backend in {"auto", "pyg4ometry"}:
            try:
                import pyg4ometry  # type: ignore  # noqa: F401

                builder_cls = ApiPyg4ometrySceneBuilder
            except ModuleNotFoundError:
                if backend == "pyg4ometry":
                    raise

        builder = builder_cls(
            gdml,
            quality=quality,
            include_containers=include_containers,
            include_world=include_world,
            max_objects=max_objects,
            max_vertices=max_vertices,
        )
        builder.build()

        placements = tuple(
            Placement(
                path=obj["path"],
                semantic_path=base.semantic_path(obj["path"]),
                volume=obj["volume"],
                material=obj["material"],
                solid=obj["solid"],
                matrix=builder._matrices[obj["id"]],
            )
            for obj in builder.scene.objects
        )
        return cls(
            placements=placements,
            solids=dict(builder._solids),
            diagnostics=tuple(builder.scene.diagnostics.values()),
            bounds=Bounds(tuple(builder.scene.bbox_min), tuple(builder.scene.bbox_max)),
            source=gdml,
            backend=builder.backend_name,
        )


class ApiMixin:
    """Records each rendered placement's world matrix and each solid's
    shared local-space mesh, as ``add_volume()`` already computes both and
    discards them after baking vertices into the flat ``self.scene`` lists
    the other exporters use."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._matrices: dict[int, base.Mat4] = {}
        self._solids: dict[str, Mesh] = {}

    def add_volume(self, volume: base.VolumeDef, matrix: base.Mat4, path: str) -> None:
        obj_id = len(self.scene.objects)
        super().add_volume(volume, matrix, path)
        if len(self.scene.objects) == obj_id:
            return  # solid_mesh() returned nothing (unsupported/missing); already diagnosed
        self._matrices[obj_id] = matrix
        if volume.solid not in self._solids:
            tris, solid_type = self.solid_mesh(volume.solid)
            self._solids[volume.solid] = Mesh(tuple(tris), solid_type)


class ApiGDMLSceneBuilder(ApiMixin, base.GDMLSceneBuilder):
    pass


class ApiPyg4ometrySceneBuilder(ApiMixin, Pyg4ometrySceneBuilder):
    pass


def world_triangles(
    geometry: Geometry, placement: Placement
) -> tuple[base.Triangle, ...]:
    """``placement``'s mesh triangles transformed into world space."""
    triangles = list(geometry.solids[placement.solid].triangles)
    return tuple(base.transform_mesh(triangles, placement.matrix))
