# Inherited limitations

This release packages the existing COCOA viewers. It does not establish full
GDML conformance. Inspect the warnings and compare important geometry against a
trusted source before drawing conclusions from a rendered alignment; see
[reference-validation.md](reference-validation.md) for one such comparison,
run once against ROOT's TGeo GDML importer.

The [testing guide](testing.md) distinguishes independently verified operations
from two strict expected-failure regressions for tracked gaps. A passing base
suite is not a claim that those gaps have been fixed.

## Parsing and placements

- Pure Python supports numeric `box`, `tube`, `cutTube`, `cone`, and approximate
  Boolean meshing. Other solid types are skipped with warnings.
- Ordinary nested `volume`/`physvol` placements and `assembly` groups are
  traversed, with an assembly's physvols placed relative to its own placement.
  `replicavol`/`paramvol`/`divisionvol`/`file` daughters are reported with a
  warning but not expanded; a replica or division still draws its mother as
  an approximation of the whole, while a parameterised or external daughter
  hides its mother as an unrendered container, like any other container
  volume.
- Positions, rotations, and scales are read inline or resolved by name through
  `positionref`/`rotationref`/`scaleref` against `<define>`; a broken
  reference raises rather than silently using the identity transform. A
  numeric attribute that is not a plain literal is evaluated as a GDML
  expression (named `<constant>`/`<variable>` references, `+ - * / % ^`,
  parentheses, and a small set of math functions, including the predefined
  `pi`) without using Python's `eval`; one that still cannot be evaluated is
  reported with a warning and falls back to its default like any other
  unparseable value. `<quantity>`/`<matrix>` defines are not supported.
  Unknown units can silently fall back.
- Physvol placement rotation uses Geant4's inverse convention, reconciled with
  ROOT Explorer and independently confirmed against ROOT's own GDML importer
  (see [reference-validation.md](reference-validation.md)); an optional
  scale, typically a reflection, is applied before it. Angles without an
  aunit/unit default to radians, per the GDML schema, for both rotations and
  solid-angle parameters. A Boolean solid's optional `firstposition`/
  `firstrotation` and its second operand's `position`/`rotation` both use the
  rotation as given, not inverted.
- A duplicate solid/volume/assembly/`<define>` name is reported with a
  warning, but the later definition still wins; there is no multi-definition
  indexing like ROOT Explorer's segmented-detector lookup. A cyclic
  volume/assembly reference, excessive nesting, or a cyclic Boolean solid
  `first`/`second` operand reference, each bounded at 256 levels, raises an
  actionable error instead of exhausting the Python call stack.
- The optional pyg4ometry backend can improve solid meshes while still using
  these local placement rules. A successful export is not a fidelity guarantee.

## Diagnostics

- Every warning is also a structured diagnostic (code, severity, category,
  construct, identifier, backend, occurrence count) in `meta.diagnostics`,
  with per-severity/per-category totals in `meta.diagnosticSummary`.
  `meta.warnings` keeps its original human-readable strings for
  compatibility. `--strict` (optionally widened with `--fail-on`),
  `--report`, and `--validate-only` read this from the CLI. Both browser
  viewers now render this structured list directly (filterable by severity
  and category, with a JSON download button), instead of only the older
  flattened `meta.warnings` strings.
- "Show in hierarchy" (standalone viewer only; the lazy diagnostics panel has
  no such link yet) is derived purely client-side from a diagnostic's `code`
  and (for `duplicate-definition`) `construct` -- not every diagnostic
  names something that still exists as a placement or solid (an
  unresolvable numeric expression's identifier is an attribute key, and a
  missing-reference diagnostic names something that was never placed), so
  only `unsupported-daughter` (a volume name) and
  `missing-solid-reference`/`boolean-approximated`/
  `unsupported-solid-type`/`pyg4ometry-fallback`/`duplicate-definition`
  with `construct=="solid"` (a solid ref) offer a link.
- `--strict`/`--fail-on`/`--report` are not applied by `--reuse-assets`,
  which only rewrites the HTML shell around existing binary chunks without
  re-parsing the source GDML.
- A diagnostic's `identifier` is the raw GDML name/ref/attribute key, not a
  resolved placement path; several physvols sharing one broken reference
  aggregate into a single diagnostic with a count, not one per placement.

## Geometry comparison

- `--diff` is a CLI/JSON reporting tool only; there is no synchronised
  side-by-side viewer, camera sync, or overlay rendering (the UI half of
  issue #9). It always compares the entire structure
  (`include_containers`/`include_world` are not exposed as flags for this
  mode, unlike the viewer exports) since hiding containers would hide real
  structural differences.
- A placement's rotation and reflection are reported together as a single
  `orientation` change, not independently: a pure reflection has the same
  column length as an identity rotation, so it cannot be told apart from an
  ordinary 180-degree rotation by inspecting the composed world transform
  alone without risking misclassifying the (far more common) plain rotation
  as a scale/reflection change. `scale` only reports a *magnitude* change.
- Only `box`/`tube`/`cutTube`/`cone` solids have comparable numeric
  parameters. A Boolean solid (`union`/`subtraction`/`intersection`) is
  noted as a limitation instead of silently reporting unchanged dimensions.
- A placement that failed to mesh at all (missing solid reference,
  unsupported solid type) is invisible to the comparison the same way it is
  invisible to a normal export; it surfaces only indirectly, through each
  source's own diagnostics folded into the report's `limitations`.
- Matching is by placement path with address suffixes stripped from every
  segment, falling back to the sole remaining candidate when exactly one is
  left unmatched on each side; anything less certain is reported ambiguous,
  never guessed. It cannot tell two placements apart that are identical in
  every GDML-visible respect (same semantic path, same raw path, same
  properties) beyond reporting them as such.

## Geometry export (glTF/GLB)

- `--gltf` builds a true node hierarchy (each GDML placement is a glTF node
  nested under its parent) rather than the flat world-space scene the other
  two exporters produce, so a repeated logical volume shares one glTF mesh
  by reference instead of being baked into world space once per placement.
- There is no `gltf-validator`/`pygltflib` dependency available in this
  project's offline, dependency-free environment. Correctness is instead
  covered by an independent test-side GLB/glTF decoder (`tests/test_gltf.py`,
  mirroring `tests/test_chunks.py`'s own precedent for this project's custom
  binary chunk format) plus a hand-written check of the specific
  required-field subset this exporter emits. Neither is a substitute for
  running the output through the official Khronos validator or a real
  viewer (Blender, three.js) before relying on it downstream.
- GDML's conventional Z-up axes are exported as-is; there is no axis remap
  to glTF's typical Y-up convention. glTF does not strictly mandate Y-up,
  and remapping risks a handedness bug for comparatively little benefit, so
  this is a deliberate choice, not an oversight. A consumer expecting Y-up
  must rotate the scene itself.
- No per-vertex normals are emitted; every mesh is non-indexed triangle
  soup (`mode: TRIANGLES`, no `indices` accessor), matching how every other
  exporter here already produces flat triangle data. Viewers are expected
  to flat-shade or auto-generate normals, consistent with this project's
  already-faceted meshes.
- Every material is marked `doubleSided: true` unconditionally, since a
  GDML `<scale>` reflection flips triangle winding with no compensating
  normal and most viewers would otherwise back-face-cull or badly light a
  mirrored mesh; this trades a small render-cost increase for correctness
  on every mesh, not just reflected ones.
- A solid that fails to mesh (unsupported type, missing reference) gets no
  glTF mesh at all, same as the other exporters; the diagnostic is still
  recorded and reachable through `--report`, `--strict`/`--fail-on` are not
  exposed for this mode.
- Nodes are keyed by placement path. Two anonymous `<physvol>` elements (no
  `name` attribute) referencing the same volume under the same mother fall
  back to an identical path (the referenced volume's own name) and collide
  into a single node, silently merging what should be two placements. Real
  Geant4-generated GDML always auto-names placements uniquely (a pointer-
  suffixed name per placement), so this only affects hand-written fixtures
  with deliberately anonymous, repeated siblings.

## Physical-volume hierarchy

- Both viewers share one tree component (`tree.js`). The lazy viewer builds it
  from `placements.json`, on demand ("Load hierarchy", or automatically on the
  first pick), so it lists every placement including those whose layer is not
  loaded; its paths and order equal the standalone tree's for the same input.
  Row click selects (with the inspector, and a highlight when the geometry is
  shown), Focus frames the subtree, and search covers names, paths and solid
  refs.
- **Hide and isolate work in lazy too**, per placement or subtree, through a
  per-vertex visibility buffer built from the chunk ids. It applies to drawing
  and picking alike, survives unloading and reloading a layer, applies to a
  layer loaded later, and hiding the selection clears it. Costs: a private copy
  of each loaded chunk's ids (4 B/vertex of browser memory) plus 1 B/vertex on
  the GPU; recomputing after a change took about 30 ms for 4.4 million
  vertices. Chunks from a v1 export have no ids, so nothing in them can be
  hidden (the hierarchy is unavailable for such exports anyway). Hidden and
  isolated placements save by path and carry across viewers.
- A flat world is a flat tree: the real COCOA geometry has about 7,200
  placements directly under the world, so expanding it renders that many rows
  (about 0.7 s here). There is no virtualised scrolling.
- Shares the glTF exporter's path-keying limitation above: two anonymous,
  identically-unnamed placements of the same volume under the same mother
  collide into one tree node.
- The tree expands incrementally (an unexpanded subtree adds no rows to the
  DOM) rather than using virtualised/windowed scrolling, which the
  underlying roadmap issue allows as an alternative for large trees.
- Hide/isolate combine with the object filters below (AND): an object is
  shown only if the filter selects it and the hierarchy does not hide it.

## Object filters (both viewers)

One filter model (`filters.js`) and panel serve both viewers, so the same
filter selects the same placements in each (tested on the same scene).

- **Composition:** search, exclude, solid type and materials combine with AND.
  A placement is included unless its material is switched off; `exclude` beats
  everything else; "Invert selection" complements the final result. Hide/isolate
  from the hierarchy then apply on top.
- **Matching is literal**, case-insensitive substring matching, on original
  identifiers (Geant4 address suffixes kept) and their suffix-stripped forms.
  A term may be scoped with `material:`, `volume:`, `name:`, `path:`, `solid:`
  or `type:`; any other text, including an unknown `prefix:` and regex
  characters, is a plain substring. `path:` and `name:` tell two placements of
  one logical volume apart; `volume:` matches both. There is no regex or
  wildcard mode yet; one would have to be an explicit opt-in.
- **Materials are matched by name**, never by rendering colour; the toggles are
  exact, the search is substring (so `material:G4_Fe` also matches `G4_Fe2`).
- **No geometry needed.** The lazy viewer evaluates filters from
  `placements.json` (fetched and digest-checked on first use) and never
  fetches a chunk to do so; a filter can match placements whose layers are not
  loaded. The panel reports "matched of total" and "shown" separately and
  explains an empty result (nothing matches, or matches are hidden/not loaded).
- Changing or resetting a filter clears a selection that it filters away.
- The standalone text search is now a defined set of fields (path, name,
  volume, material, solid, solid type) instead of any substring of the object's
  JSON, so numbers such as bounding-box values no longer match.
- Lazy exports made before this change have no `solidTypes` in the manifest,
  so the solid-type menu is empty until re-exported (other filters work).
- Not done: optional user-defined subsystem groups or a sidecar configuration
  (the lazy layer heuristics are unchanged), and saved selections other than
  as part of a saved view.

## Clipping (both viewers)

- One shared definition (`assets/clipping.js`) drives both viewers, so the same
  settings keep the same region in standalone and lazy exports. Positions are
  global coordinates in mm, the normalised geometry unit.
- Phi wedge: global azimuth `atan2(y, x)` in `[start, start + width]`,
  counter-clockwise about +z, bounds inclusive, wrapping across +-180 degrees;
  `remove` drops the wedge, `keep` keeps only it. Width is 1-359 degrees.
- Planes: `x/y/z >=` keeps the coordinate above the value and `x/y/z <=` below
  it (a pair is a slab); the arbitrary plane keeps `n.p >= offset` with `n`
  normalised (a zero normal is ignored). **The kept region is the intersection
  of the phi condition and every enabled plane.**
- **Cuts are open, not capped.** Clipping discards fragments of the surface
  mesh; nothing fills the cut, so a cut view shows surfaces only and is not a
  quantitative solid section. The panel says so.
- Clipping applies to filled surfaces, the wireframe overlay, selection
  highlighting and picking alike (the lazy viewer has no wireframe overlay).
  The coordinate axes are never clipped.
- No visible plane gizmo (the issue marked it optional) and no saved-view UI
  yet: the state is serialisable (`getClipState()`/`setClipState()`) for issue
  #10 to use.
- Fragment precision is `highp` where the GPU provides it; on a GPU that only
  has 16-bit `mediump`, plane edges can wobble by a fraction of a millimetre at
  detector scale.
- The two exporters still default to different tessellation quality (`--lazy`
  to `fast`, standalone to `medium`), so the same circles are drawn as
  different polygons; pass the same `--quality` to compare them exactly.

## Saved view state (both viewers)

"Save view" writes a `<name>.view.json` sidecar; "Load view" reads one; and
`--view-state FILE` (both modes) embeds one as the initial view (in lazy mode
it wins over `--default-load`). The file is versioned (`format`, `version: 1`)
and records: camera (yaw, pitch, distance, pan, up, view centre), the
projection (always perspective, 45 degrees, recorded explicitly), opacity,
axes, the full clipping state, and the geometry it was saved for (schema
version, input sha256, filename). Both save the object filters (search,
exclude, solid type, materials, invert), hidden and isolated placements, the
selected placement and the hierarchy tree's expansion and search. Standalone
adds wireframe and theme; lazy adds the selected subsystem/layers and "unload
hidden".

- **Stable identifiers:** placements are stored by path and layers by
  subsystem/layer name, never by numeric id, so a view survives a re-export.
  Entries that no longer exist (placement, material, solid type, layer) are
  skipped and listed by name in the viewer, and a view saved for a different
  input digest is flagged as possibly stale; everything that still exists is
  restored. `--view-state` also prints that warning at export time.
- **Deterministic and never half-applied:** a file is validated completely
  first; any error (not JSON, wrong format, newer or invalid version, bad
  camera/projection/display/clipping/filters) rejects it with the reason and
  changes nothing. Applying resets every section the file covers before
  setting it, so loading the same file always produces the same view (a saved
  view loaded into a fresh page re-saves to an identical file). Unknown keys are
  ignored with a warning.
- **Versions:** only version 1 exists, so there is nothing to migrate yet; the
  validator has a migration table, and a newer file is refused with guidance to
  update gdml-inspector or re-save.
- **Across viewers:** a view loaded in the other viewer applies what both
  share (camera, projection, opacity, axes, clipping, filters, hierarchy) and
  says what it ignored (wireframe and theme in lazy; layers in standalone).
- **Not saved:** material colours (fixed by the backend, not user-editable),
  the diagnostics filters, and the scroll position of the hierarchy tree. Lazy layer names come from this
  tool's heuristic classification, so a changed classification can make a layer
  unknown; it is reported as such.

## Object picking and the placement inspector

- Both viewers share one picker (`picking.js`) and one inspector panel
  (`inspector.js`). The standalone viewer reads placement data from the
  embedded scene; the lazy viewer reads it from `placements.json`, fetched and
  sha256-checked on first click (or when a saved view selects a placement), so
  it costs nothing until used. If that file cannot be fetched or fails its
  digest, the status line says so, nothing is selected in the panel, and
  rendering is unaffected; a later click retries.
- Lazy picking sees only loaded, visible chunks, under the active clipping:
  an unloaded layer neither can be picked nor hides what is behind it.
  Selection is by object id, so it survives unloading and reloading the
  object's layer; while the layer is unloaded the inspector still shows the
  placement's details from `placements.json` and marks the geometry as not
  shown.
- A lazy export made before chunk format v2 (v1 chunks, no `placements.json`)
  still renders; picking is reported as unavailable and asks for a re-export.
- Lazy clicks that move more than a few pixels count as a drag, not a pick.
- The lazy viewer has no hierarchy tree yet, so there is no "show in
  hierarchy", focus or isolate on a lazy selection.
- Placement ids are encoded in 24 bits in the picking pass (about 16.7 million
  objects), far above the vertex budgets this tool accepts.
- Highlighting is a colour tint on the selected object's own surface, not
  a drawn outline -- WebGL1 has no single inexpensive pass for true
  silhouette outlining without a stencil buffer or extra geometry, and a
  tint is sufficient to show what is selected.
- A solid's declared parameters come from its own numeric GDML attributes
  (`solid_params()`); a Boolean solid (`union`/`subtraction`/
  `intersection`) has none of its own and shows an explicit "no
  parameters" note in the inspector rather than inventing dimensions for
  it, consistent with how Boolean solids are already approximated
  elsewhere (see "Geometry export" above and the parser section).

## Public API (`gdml_inspector.api`)

- All lengths are millimetres, this project's native/unconverted GDML
  unit; `Mat4` is row-major with translation at `matrix[row][3]`, the same
  convention `geometry.py` already uses internally.
- `Geometry.placements` contains only placements that actually rendered;
  `include_containers`/`include_world` control which, exactly as for every
  other export mode. `Geometry.solids` is not a 1:1 reflection of every
  `<solid>` in the source GDML, only of solids actually used by a rendered
  placement; a Boolean solid's operand-only refs and solids behind a
  hidden container never appear. Two placements sharing a GDML solid ref
  share the same `Mesh` object by identity, but only meaningfully within
  one `Geometry.load()` call — quality and backend are fixed per load, so
  it is not a cross-call cache key.
- `max_objects`/`max_vertices` truncate silently with a `"limit-reached"`
  diagnostic, like every other export mode; they do not raise.
  `Geometry.load()` otherwise raises the same `ValueError` cases
  `parse()`/`traverse()`/`place_physvols()`/`solid_mesh()` already raise
  elsewhere (cyclic references, excessive nesting, missing
  `<solids>`/`<structure>`), plus the natural `FileNotFoundError`/`ParseError`
  for a bad path, and `ModuleNotFoundError` for an explicitly-requested but
  missing pyg4ometry
  backend. There is no custom exception hierarchy.
- This module is the repo-local half of issue #12 only. It does not
  include a ROOT Explorer adapter, shared fixtures proving parity between
  Inspector exports and such an adapter, or any cross-repository release/
  dependency policy — those are explicitly deferred to a separate change
  in ROOT Explorer's own repository, per the issue's own text.

## Static type checking

- mypy's scope is `src/gdml_inspector`, not `tests/`. It covers the core
  scene model precisely (`geometry.py`'s `Scene.objects`/`to_dict()`,
  typed via `ObjectDict`/`SceneDict`/`DiagnosticDict`) and `diff.py`'s own
  consumption of that same shape, but not every dict this project produces:
  `lazy.py`'s manifest/chunk-entry dict and `diff.py`'s own comparison-
  report dict (`build_report()`'s return value) are a different, separate
  shape from `scene.objects` and remain `dict`/`dict[str, object]` — a
  deliberately deferred next slice of issue #16, not an oversight.
- The three exporter mixins (`DiffMixin`, `ApiMixin`, `LazyBuilderMixin`)
  rely on the final multiply-inherited class to supply `GDMLSceneBuilder`'s
  members; see [architecture.md](architecture.md) for how each is typed
  without a runtime behavior change. A fully structural fix (a `Protocol`-
  based common base for all exporter mixins) would be more "correct" but is
  real design work spanning every exporter module — out of scope here.
- pyg4ometry ships no inline types or stubs; the one `[[tool.mypy.
  overrides]]` entry for `pyg4ometry.*` is the only suppression in the
  config, and installing the `typing` dependency group never installs
  pyg4ometry itself.

## Rendering and size limits

- Union meshes can contain interior surfaces. Subtraction/intersection can look
  like the first operand in the Python backend or after backend fallback.
- Mesh quality is polygonal approximation of curves, not a physical tolerance.
- Single-file inspection samples projected vertices; it is not depth-correct
  triangle picking. Transparency uses the inherited rendering approach.
- The lazy viewer groups objects by heuristics, not detector-provided hierarchy.
  Its material list is a legend; it has no per-object picking or wireframe mode.
- Lazy mode precomputes the complete selected mesh in Python before writing it.
  It is lazy in browser transfer/rendering, not in Python preprocessing.
- Object/vertex limits stop traversal, but the inherited single-file vertex
  limit can be exceeded by the last whole object. A single large object can
  also exceed the nominal lazy chunk size. Do not treat these as hard memory
  budgets. Performance and diagnostics issues track improvements.
- `--reuse-assets` refreshes the HTML/UI around existing chunks and refuses,
  exit code 4 with the reason, when that could show stale or damaged geometry:
  the source file's sha256 differs from the one recorded (a same-named file
  edited in place), an explicitly passed build option differs from the
  recorded setting, a chunk is missing, has the wrong size or fails its sha256,
  or the viewer predates schema 2 (no digests recorded) or comes from a newer
  gdml-inspector. Options you do not pass are not compared, so a plain
  `--reuse-assets --default-load X` keeps working.

## Distribution and provenance

Exports have no remote CDN dependencies (a test asserts every request stays
on the local origin). Single HTML exports can be opened offline, whereas
chunked exports must be served over HTTP with their asset directory alongside
them.

**Lazy chunk format v2 and `placements.json`.** Chunks carry one little-endian
`uint32` per vertex (`objectId + 1`, 0 = none), costing +4 bytes/vertex (20% of
chunk bytes). The viewer reads v1 and v2 chunks. `placements.json` (digest and
size in the manifest, covered by `--reuse-assets` and `--bundle`) holds the
hierarchy nodes, the object table and the solids table so metadata exists for
objects whose chunk is not loaded. It is one file and is not compact: measured
on a real detector (7,405 objects, 7,481 nodes) it is 7.9 MiB, about 1.1
KB/object, because nodes and objects repeat paths, names and matrices. That is
acceptable at this scale but not for 100k+ objects; per-chunk metadata
splitting belongs to issue #7. `--reuse-assets` refuses output from before v2
and asks for a rebuild.

**Provenance.** Every export carries `schemaVersion: 2` and a `provenance`
block: tool and backend names/versions, the input file's name and sha256, the
resolved build settings, and the units (mm, rad). Lazy chunks additionally
record a sha256 and size each; the viewer verifies a fetched chunk against it
when the browser offers `crypto.subtle` (https or localhost) and reports a
mismatch as a chunk error. Output exported before schema 2 still displays, with
a notice; one from a newer gdml-inspector is refused with guidance.

**Path redaction.** By default the absolute source path is still embedded
(`source`, `provenance.input.path`) for local convenience. `--redact-paths`
(both modes, also on `--reuse-assets`) replaces it with the bare filename and
drops `input.path`; the filename and sha256 remain, so provenance is not lost.
Review artifacts before sharing them: names of volumes, materials and
placements come from the GDML and are embedded as-is.

**Reproducibility.** No timestamp or machine-specific value is ever written to
geometry content. With the same tool version, backend and backend version,
input bytes *and filename*, identical explicit options, the same *output*
filename (lazy chunk URLs contain `<stem>.assets`) and `--redact-paths`, the
standalone HTML, and the lazy HTML and every chunk, are **byte-for-byte
identical**, whatever the directory or working directory. Without
`--redact-paths` they differ only in the embedded path. Different tool or
backend versions differ by design, since they are recorded. Note that `--lazy`
defaults to `--quality fast` and standalone to `medium`: pass the options
explicitly when comparing builds.

**Bundles.** Lazy chunk URLs are percent-encoded, so a bundle moved to any path
or URL (spaces, Unicode, `%`, `#`) still loads. `--bundle ARCHIVE.zip` also
writes the HTML and exactly the chunks its manifest lists as a deterministic zip
(sorted entries, fixed timestamps and permissions); the directory itself is
equally portable. Single-file standalone output is unchanged.
Use `--backend python` or `--backend pyg4ometry` to avoid the environment-dependent
choice of `auto`. Fully deterministic exports, metadata redaction, and versioned
scene contracts are roadmap items; `--diff` reports already include a sha256
digest of each compared file, but standalone/lazy exports do not yet.

The dependency-free backend and packaged HTML are covered by automated tests.
The optional pyg4ometry dependency is not required for that test suite and has
not been certified against every pyg4ometry version or GDML dialect.
