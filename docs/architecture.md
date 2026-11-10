# Architecture

The initial extraction preserves the rendering algorithms and modes from COCOA
while removing the dependency on colocated scripts or a particular working
directory. No COCOA installation, ROOT Explorer installation, or external asset
directory is required.

| Module | Responsibility |
| --- | --- |
| `geometry` | XML helpers, primitive meshes, volume definitions, scene metadata, traversal |
| `pyg4ometry_backend` | Optional tessellation, mesh adapters, backend diagnostics |
| `standalone` | Single-file scene export, the physical-volume hierarchy tree, and its argument parser |
| `lazy` | Layer classification, binary chunks, manifests, asset reuse and lazy CLI |
| `diff` | Compares two built scenes and reports semantic placement differences |
| `gltf` | Exports a hierarchical glTF 2.0 / GLB model for use outside this project |
| `api` | Small, documented public Python API: `Geometry.load()`, placements/meshes/bounds/diagnostics |
| `server` | Local HTTP server with explicit document root and resource cleanup |
| `cli` | Installed entry points, mode dispatch, concise error reporting |
| `assets/*.html` | Offline browser UI, WebGL shaders and interaction code |

`gdml-inspector --lazy` and `gdml-inspector-lazy` reach the same lazy exporter.
`python -m gdml_inspector` reaches the same dispatcher as `gdml-inspector`.
The single-file template embeds a JSON scene; the lazy template embeds a JSON
manifest with relative chunk URLs. JSON embedded in either script escapes `</`
so GDML names cannot close its script element.

Both exporters share one `traverse()`/`place_physvols()` placement-tree walk
(cycle detection, the nesting-depth limit, container/assembly expansion,
unsupported-daughter diagnostics) defined once on `GDMLSceneBuilder`. The
optional backend only overrides solid meshing, so it inherits the same
placement limitations and the same walk. The lazy builders specialise two
things through a small mixin: volume accumulation (`add_volume()`, into
binary chunks instead of the flat scene lists, with its own precise
pre-commit vertex-budget check) and the two small hooks
(`_budget_exceeded()`/`_record_budget_exceeded()`) the shared walk calls to
decide when to stop and how to report it, since lazy's vertices never touch
`self.scene` and so can't be counted the same way. `diff`'s builders
specialise only `add_volume()`/`to_dict()`, to additionally record each
object's placement matrix and a solids-by-ref parameter table; they inherit
the shared walk unchanged. The lazy binary format does not retain per-object
IDs; precise lazy picking requires an extension to that format.

`gltf`'s builders specialise two further no-op hooks on the shared walk:
`_on_placement()` (each placement's own local matrix and tree position,
called from `place_physvols()`) and `_on_volume()` (a volume's render/
container decision, called from `traverse()`). Both exist because the other
three exporters only need the cumulative world-space matrix `add_volume()`
already receives, while a hierarchical glTF node tree needs each
placement's un-flattened local transform and needs an empty pivot node for
a hidden container even though `add_volume()` is never called for it. The
hooks are no-ops by default, so every existing consumer is unaffected; only
`gltf.GltfMixin` overrides them.

`standalone.HierarchyMixin` is the second consumer of those same two hooks:
it records one `HierarchyNodeDict` per path (parent path, logical-volume
identity, render/assembly flags, each placement's own local transform from
`_on_placement()`, and -- by also overriding `add_volume()`, the same way
`DiffMixin`/`ApiMixin` already do -- the composed global transform for
rendered nodes), including non-rendered container and assembly nodes, for
the single-file viewer's hierarchy and picking-inspector panels. It also
exposes `solids_table()`, each parsed solid's kind and own declared
parameters keyed by GDML ref (`SolidInfo`, promoted from `diff.py` to
`geometry.py` since it is just `tag_name()`/`solid_params()` combined --
not diff-specific logic). It is kept independent of `ObjectDict`/
`SceneDict` -- a node's own rendered mesh, if any, is cross-referenced by
`path` against `scene.objects` only after `build()` finishes -- and
embedded in the output HTML as separate, top-level JSON values
(`const HIERARCHY`, `const SOLIDS`) alongside `const SCENE`, rather than as
new keys on the existing scene dict, so that dict's shape stays unaffected.

The standalone viewer's object picking (`assets/standalone.html`) uses a
WebGL1 ID buffer rather than vertex sampling: every triangle's object id is
encoded as a colour and drawn, once per click, into an offscreen
framebuffer with depth testing enabled (the main pass runs with depth
testing disabled, for its own unrelated translucency/draw-order reasons),
then read back at the exact clicked pixel. This is pixel-exact and
depth-correct, and reuses the existing per-vertex visibility buffer and
phi-cut uniforms so hidden/filtered/clipped surfaces are unpickable and
don't block picking of what's behind them, for free. A persistent
`selectedObjectId` drives both a highlight tint (a new `a_highlight`
vertex attribute on the main shader) and the "Picked Object" inspector
panel, kept in sync with the hierarchy tree's own `selectedPath` in both
directions. The panel itself (search, expand/collapse, hide/isolate/focus,
keyboard navigation, picking, the inspector) lives entirely in
`assets/standalone.html`; the lazy
viewer does not yet have an equivalent, since the lazy manifest has no
per-placement data to build one from (see
[limitations.md](limitations.md)).

`api`'s builders specialise only `add_volume()`, like `diff`, since its
public `Geometry` has no hierarchy concept: it records each rendered
placement's world matrix into a side table (rather than mutating
`scene.objects` in place, so it never depends on that dict's shape) and
caches one local-space `Mesh` per solid ref the first time a placement
uses it, reusing `solid_mesh()`'s own cache so repeated placements cost no
extra meshing. `Geometry`/`Placement`/`Mesh`/`Bounds` are the actual public
contract — not `scene.objects`'/`to_dict()`'s internal dict shapes, which
remain free to change. See `gdml_inspector.api`'s module docstring for the
full units/identities/resource-budget/error-behaviour contract and
`CHANGELOG.md` for its versioning policy.

Static type checking (mypy, `uv run --no-sync mypy` after `uv sync --group
typing`; see [testing.md](testing.md)) checks every mixin's own method
bodies against a real contract rather than reporting every `self.scene`/
`super().add_volume()` access as undefined: `DiffMixin`/`ApiMixin` declare
`GDMLSceneBuilder` as their base purely for mypy's benefit (the runtime MRO
is unchanged, since `DiffGDMLSceneBuilder(DiffMixin, GDMLSceneBuilder)`
already lists a `GDMLSceneBuilder` subtype as the other base, and C3
linearization collapses the duplicate), while `LazyBuilderMixin`, which
never calls `super().add_volume()`/`super().to_dict()`, only needs its
expected attributes declared, not a real base. `gltf.GltfMixin` needs
neither, since its two hook methods only touch dicts it owns itself.

HTML resources are loaded through `importlib.resources`, including when run from
an installed wheel. No browser package manager or remote JavaScript CDN is used.
The local server binds to `127.0.0.1`, serves the output directory without
changing process working directory, and closes its socket when interrupted.

The scene/manifest interfaces used by the HTML exporters remain provisional
and specific to those formats. `gdml_inspector.api` is the small public
geometry API the roadmap called for, covering the repo-local half of issue
#12: reading placements/materials/meshes/bounds/diagnostics from Python.
The separate ROOT Explorer adapter that would consume it, and shared
fixtures proving parity between the two, are cross-repository follow-up
work, not claimed here.
