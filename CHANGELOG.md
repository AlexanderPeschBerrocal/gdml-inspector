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
to keep in sync by hand. To bump the version: edit `pyproject.toml`, then
run `uv lock` so the lockfile and your editable install pick it up —
`tests/test_version.py`'s `test_declared_installed_and_runtime_versions_agree`
exists specifically to catch forgetting that second step, and the CI
isolated-wheel check verifies the same equality from an installed wheel
outside the source checkout. Full release automation (tagging, dry runs,
automated bumping) is issue #22's separate scope; this is only the
authoritative-source policy that tooling will build on.

## [0.3.0] - 2026-11-08

### Added

- `--version` on `--diff` and `--gltf` (previously only `standalone`/
  `--lazy` had it; `--diff`/`--gltf --version` used to fail with a
  confusing "required arguments" error instead of reporting a version).

### Fixed

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
