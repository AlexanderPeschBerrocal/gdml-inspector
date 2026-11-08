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
