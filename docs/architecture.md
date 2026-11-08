# Architecture

The initial extraction preserves the rendering algorithms and modes from COCOA
while removing the dependency on colocated scripts or a particular working
directory. No COCOA installation, ROOT Explorer installation, or external asset
directory is required.

| Module | Responsibility |
| --- | --- |
| `geometry` | XML helpers, primitive meshes, volume definitions, scene metadata, traversal |
| `pyg4ometry_backend` | Optional tessellation, mesh adapters, backend diagnostics |
| `standalone` | Single-file scene export and its argument parser |
| `lazy` | Layer classification, binary chunks, manifests, asset reuse and lazy CLI |
| `diff` | Compares two built scenes and reports semantic placement differences |
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

HTML resources are loaded through `importlib.resources`, including when run from
an installed wheel. No browser package manager or remote JavaScript CDN is used.
The local server binds to `127.0.0.1`, serves the output directory without
changing process working directory, and closes its socket when interrupted.

The scene/manifest interfaces remain provisional. The roadmap calls for a
validated scene graph, parser consolidation, instancing, and a small public
geometry API usable by ROOT Explorer. Those are follow-up work, not features
claimed by this initial extraction.
