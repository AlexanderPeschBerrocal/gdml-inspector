# Changelog

All notable changes to this project are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/).

## Versioning policy

This package does not yet promise full semantic versioning (pre-1.0), but
the public programmatic surface — `gdml_inspector.api`'s `Geometry`,
`Placement`, `Mesh`, `Bounds` dataclasses, `Geometry.load()`, and
`world_triangles()` — is treated as a real compatibility boundary, not an
afterthought:

- **Breaking** (bumps at least the minor version pre-1.0): removing or
  renaming a field on `Geometry`/`Placement`/`Mesh`/`Bounds`; changing the
  unit convention (currently millimetres) or the `Mat4` layout (currently
  row-major, translation at `matrix[row][3]`); changing mesh-identity
  semantics (currently: placements sharing a GDML solid ref share the same
  `Mesh` object by identity within one `Geometry.load()` call); changing
  which exceptions `Geometry.load()` raises for a given failure.
- **Non-breaking**: adding a new field, dataclass, or keyword argument with
  a default that preserves existing call sites; adding a new diagnostic
  `code`.
- A breaking change gets its own entry below under a **Migration** heading
  showing the old access pattern next to the new one, not just a one-line
  mention. The command-line interface (`--lazy`/`--diff`/`--gltf`/plain
  export flags) is versioned independently and is not covered by this
  policy; it has its own backward-compatibility expectations documented in
  `docs/limitations.md`.

Full release tooling (tagging, changelog automation, version validation) is
tracked separately as issue #22; this file is maintained by hand until then.

## Package version

Distinct from the `gdml_inspector.api` policy above, this is the package's
own version (`pyproject.toml`'s `version` field, what `--version` reports,
what `pip show gdml-inspector` shows): **Semantic Versioning**, pre-1.0
(`0.x` — a breaking change may still land in a minor bump until a
deliberate `1.0.0`), not CalVer. `pyproject.toml`'s `version` field is the
one authoritative value in the repo; `gdml_inspector.__version__` is
always derived from the installed distribution's metadata
(`importlib.metadata.version("gdml-inspector")`), never a second literal
to keep in sync by hand. `tests/test_version.py`'s
`test_declared_installed_and_runtime_versions_agree` exists specifically
to catch a stale editable install, and the CI isolated-wheel check
verifies the same equality from an installed wheel outside the source
checkout. The version is bumped exclusively via `scripts/release.py`
(never by hand-editing `pyproject.toml`); see
[docs/releasing.md](docs/releasing.md) for the full, reviewed release
process (issue #22) this now builds on top of this policy.

## [0.3.0] - 2026-11-08

### Added

- Lazy viewer: hide and isolate (row checkboxes, Isolate, Clear isolation)
  through a per-vertex visibility buffer built from the chunk ids. It affects
  drawing and picking, survives unloading a layer, and hidden/isolated
  placements are saved in and restored from view-state files, including ones
  saved from the standalone viewer.
- Lazy viewer: a hierarchy tree built on demand from `placements.json`, with
  selection, Focus (also from the inspector), search, keyboard navigation and
  saved-view support for expansion and camera centre. The tree component
  (`tree.js`) is now shared with the standalone viewer.
- Lazy viewer: depth-correct click picking, selection highlight and the
  placement inspector, sharing `picking.js` and `inspector.js` with the
  standalone viewer. Placement details come from `placements.json` (fetched
  and digest-checked on first use), so they are available for objects whose
  layer is unloaded; selection survives unloading and reloading a layer, and
  saved lazy views record `selectedPath`. Exports made before chunk format v2
  still render, with picking reported as unavailable.
- Lazy chunk format v2: each chunk stores a per-vertex object id, and a new
  `<stem>.assets/placements.json` asset (hierarchy nodes, object table,
  solids) is recorded in the manifest with its digest, verified by
  `--reuse-assets` and included in `--bundle`. Foundation for lazy picking
  (below), hierarchy and filters; the viewer reads both v1 and v2 chunks.
  `--reuse-assets` on older output requires a rebuild.
- Saved view state in both viewers. "Save view" writes a versioned
  `.view.json` (camera, projection, opacity, axes, clipping and, per viewer:
  filters, hidden/isolated/selected placements and tree state, or the selected
  layers; theme and wireframe in the standalone viewer); "Load view" restores
  it deterministically and lists any placement, material, solid type or layer
  that no longer exists, plus a warning if it was saved for different geometry.
  Files are validated completely first, so an invalid or newer-version file is
  rejected with the reason and changes nothing. `--view-state FILE` embeds a
  view as the initial state at export time. Completes issue #10.
- Export provenance and reproducibility. Every export now records
  `schemaVersion: 2` and a `provenance` block (tool and backend versions, the
  input's name and sha256, the resolved settings, units); lazy chunks record a
  sha256 and the viewer verifies them. `--redact-paths` drops the absolute
  source path while keeping filename and digest, and with explicit options
  makes standalone and lazy output byte-for-byte reproducible across
  directories (guarantee in `docs/limitations.md`). `--reuse-assets` now
  refuses an edited same-named source, a changed explicit option, and missing,
  truncated or corrupt chunks, instead of reusing them silently. Lazy chunk
  URLs are percent-encoded so bundles load from any path, `--bundle` writes a
  deterministic zip, and viewers give clear guidance for old or newer schema
  versions. A first part of issue #10; saved view state follows separately.
- Clipping in both viewers: x/y/z clipping planes (a min and a max on one axis
  make a slab), an arbitrary plane given by normal and offset, transverse/
  longitudinal/slab presets and reset, alongside the phi wedge. The two
  viewers now share one definition of the controls, state, kept-region
  semantics and shader code (`assets/clipping.js`), so identical settings keep
  the same region; the standalone viewer gains keep/remove wedge mode and the
  lazy viewer gains numeric entry. Slider and numeric entry stay in sync,
  positions are in mm, and the state is serialisable for saved views. Clipping
  applies identically to surfaces, wireframe, highlighting and picking. Cuts
  are open (not capped) and the UI says so; see `docs/limitations.md`.
- A structured, filterable diagnostics panel in both viewers, replacing
  the older flat warnings-text dump: filter by severity and category, and
  download the full structured report as JSON directly from the viewer.
  In the standalone viewer, a diagnostic that names a still-existing
  volume or solid (not every one does -- see `docs/limitations.md`) gets
  a "Show in hierarchy" button that reveals the affected node(s) using the
  existing hierarchy search. Finishes the browser-UI half of issue #8;
  the backend/CLI half (`meta.diagnostics`, `--strict`, `--report`, etc.)
  already shipped in an earlier release.
- Depth-correct object picking and a detailed placement inspector in the
  standalone viewer (`gdml-inspector`, not yet `--lazy`): clicking now
  renders an offscreen, depth-tested WebGL ID buffer and reads back the
  exact pixel instead of sampling nearby vertices, so occluded, hidden,
  filtered, and phi-clipped surfaces are never selected and a click in the
  middle of a large face always works. The picked object is highlighted in
  the viewport and kept in sync with the hierarchy panel in both
  directions. The inspector now shows physical and semantic paths, the
  logical volume, material, the solid's own declared parameters (labelled
  separately from its tessellated bounding box), local and global
  transforms, an approximated-geometry note, and copy-path/copy-name/
  focus/isolate actions. Selection clears automatically if the selected
  object becomes hidden by a filter. A first, scoped slice of issue #4;
  lazy-mode picking is tracked separately, for the same reason lazy-mode
  hierarchy support is (see `docs/limitations.md`).
- A searchable physical-volume hierarchy panel in the standalone viewer
  (`gdml-inspector`, not yet `--lazy`): the actual world-to-placement tree,
  including non-rendered container/assembly nodes, with expand/collapse,
  per-node hide/isolate, camera focus on a subtree's bounds, and keyboard
  navigation. Hide/isolate cooperate with the existing material/solid-type/
  search filters. A first, scoped slice of issue #2; lazy-mode support is
  tracked separately, since the lazy manifest has no per-placement data to
  build a tree from today (see `docs/limitations.md`).
- `--version` on `--diff` and `--gltf` (previously only `standalone`/
  `--lazy` had it; `--diff`/`--gltf --version` used to fail with a
  confusing "required arguments" error instead of reporting a version).

### Fixed

- HTML templates were filled by sequential string replacement, so GDML-derived
  JSON inserted for one placeholder was then scanned for the next: a material
  or placement named like a placeholder (for example `__SCENE__`) was replaced
  inside the embedded data. Templates are now filled in a single pass.
- The standalone viewer injected GDML-controlled names (material names,
  the warnings list, and the hierarchy tree's placement/volume labels)
  into the page as unescaped HTML. A material, volume, or placement name
  containing HTML-special characters could inject markup into the
  generated viewer; the lazy viewer already escaped its own equivalents
  correctly. Fixed by applying the same escaping standalone-wide.
- `python -m gdml_inspector --version` used to print `__main__.py
  <version>` (argparse's default `prog` is `sys.argv[0]`'s basename, which
  for `-m` invocation is `__main__.py`); every CLI mode now sets an
  explicit `prog`, so `--version` output is stable and consistent
  (`gdml-inspector <version>`, `gdml-inspector-lazy <version>`,
  `gdml-inspector --diff <version>`, `gdml-inspector --gltf <version>`)
  regardless of invocation method.
- `gdml_inspector.__version__` no longer duplicates `pyproject.toml`'s
  version as a second hand-maintained literal; see "Package version" above.

## [0.2.0] - 2026-11-08

### Added

- `gdml_inspector.api`: a small, documented, stable Python API for reading
  a GDML scene — `Geometry.load()` returns an immutable `Geometry` with
  typed `placements` (world-space matrix, material, solid identity),
  `solids` (shared local-space meshes, deduplicated by GDML solid ref),
  `bounds`, and `diagnostics`. Importing this module requires no
  Streamlit/Plotly/ROOT/pyg4ometry; see its module docstring for the full
  unit/identity/resource-budget/error-behaviour contract. This is the
  repo-local slice of issue #12 — the ROOT Explorer adapter and
  shared-fixture parity work are a separate, cross-repository change, not
  included here.

## [0.1.0] - unreleased prior to this changelog

Initial extraction from COCOA: single-file and lazy-loading WebGL viewers,
the optional pyg4ometry backend, structured diagnostics, `--diff` geometry
comparison, and `--gltf` export. See `git log` for the detailed history
predating this file.
