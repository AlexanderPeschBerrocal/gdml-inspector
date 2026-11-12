# GDML Inspector

Inspect and share Geant4 GDML geometries in the browser.

GDML Inspector converts a GDML file into an offline WebGL viewer. It provides a
single HTML export for small geometries and an HTML + binary-chunk export with
lazy browser loading for larger detectors. The default installation has **no
runtime dependencies**: ROOT, Geant4, VTK, and a JavaScript build toolchain are
not required. An optional pyg4ometry backend provides additional solid meshing.

Extracted from the two COCOA GDML viewers.

## Install and run

Python 3.10 or newer is required. Install from this checkout:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
gdml-inspector examples/detector.gdml --backend python --serve
```

For development, use the locked uv setup described below. The package is not
yet published to PyPI.

Export a portable single file:

```bash
gdml-inspector detector.gdml --backend python --out exports/detector.html
```

Open that HTML directly in a WebGL-capable browser; it needs no CDN or network
connection. To serve it locally, add `--serve`. On a remote/headless machine,
add `--no-browser` and forward the selected localhost port (default: 8765).
The local server serves the output directory, so choose a dedicated export
directory when sharing its contents.

For a larger geometry:

```bash
gdml-inspector detector.gdml --lazy --backend python \
  --out exports/detector.html --default-load Tracker --serve
```

This writes `exports/detector.html` and `exports/detector.assets/*.bin`. Keep
both together and open through HTTP; browsers cannot fetch those chunks from
`file://`. `gdml-inspector-lazy` is an equivalent dedicated entry point.
Use `--default-load all` to load every subsystem initially. The lazy viewer
otherwise starts with no geometry loaded.

Omitting `--out` writes into the system temporary directory and prints the
destination. `python -m gdml_inspector` supports the same options as the main CLI.

## Existing capabilities

| | Single HTML | Lazy export |
| --- | --- | --- |
| Orbit, pan, zoom, six axis views, coordinate axes | Yes | Yes |
| Opacity | Yes | Yes |
| Clipping: shader-based phi wedge (keep/remove) plus x/y/z planes, slabs and an arbitrary plane; open (uncapped) cuts | Yes | Yes |
| Material toggles, scoped literal search, exclude, solid-type filter, invert | Yes | Yes (from metadata, no chunk fetch; needs a v2 export) |
| Depth-correct click picking, highlight and placement inspector | Yes | Yes (needs a v2 export) |
| Wireframe overlay and light/dark theme | Yes | Not yet |
| Physical-volume hierarchy tree with search and focus | Yes (hide/isolate too) | Yes, hide/isolate too (needs a v2 export) |
| Subsystem/layer toggles and unloading hidden geometry | Not applicable | Yes |
| Structured diagnostics (code, severity, category, construct, backend) | Yes | Yes |

The subsystem grouping currently uses COCOA-inspired name and shape heuristics;
it is not the complete GDML volume hierarchy. Lazy loading reduces browser
transfer/GPU work; Python still builds all selected chunks before serving them.

## Backends and fidelity

`--backend python` uses the bundled parser and approximate mesher.
`--backend auto` (the inherited default) selects pyg4ometry when importable and
otherwise uses Python. Select a backend explicitly for reproducible exports.
Install the optional backend with:

```bash
python -m pip install '.[pyg4ometry]'
gdml-inspector detector.gdml --backend pyg4ometry --serve
```

The pure Python backend meshes `box`, `tube`, `cutTube`, and `cone`. Boolean
`union`/`subtraction`/`intersection` apply an optional `firstposition`/
`firstrotation` to their first operand; union combines meshes without
removing interior faces, and subtraction/intersection approximate the result
by their (transformed) first operand with warnings. Unsupported solids are
skipped with warnings. Placement traversal handles nested `physvol` volumes
and `assembly` groups, with positions, rotations, and scales given inline or
resolved by name through `positionref`/`rotationref`/`scaleref`. A cyclic
volume/assembly reference, or nesting beyond 256 levels, raises an actionable
error, and a duplicate solid/volume/assembly/`<define>` name is reported with
a warning. `replicavol`/`paramvol`/`divisionvol`/`file` daughters are
reported with a warning but not expanded. A numeric attribute that is not a
plain literal is evaluated as a GDML expression (named constants/variables,
basic arithmetic, and a few math functions, without Python's `eval`); one
that still fails to evaluate is reported with a warning and falls back to
its default. `<quantity>`/`<matrix>` defines and several other GDML
constructs are not fully supported.

The optional backend improves solid tessellation but uses the same local XML
placement traversal. It does not make the entire GDML reader complete. Physvol
placement rotation matches Geant4's inverse convention used by ROOT Explorer,
an optional scale is applied before it, and angles without an aunit/unit
default to radians per the GDML schema. See
[limitations](docs/limitations.md) before interpreting a geometry. These
rendered meshes are not quantitative material maps or authoritative geometry
validation.

Use `--quality fast|medium|high`, `--max-objects`, and `--max-vertices` to control
export size; lazy mode also provides `--chunk-vertices`. See
`gdml-inspector --help` and `gdml-inspector --lazy --help` for mode-specific
options, asset reuse, and optional backend debugging.

## Diagnostics and validation

Every warning above is also a structured diagnostic: a stable `code`,
`severity` (`error`/`warning`), `category` (`unsupported`/`reference`/
`budget`/`numeric`/`backend`), the GDML `construct` involved, an `identifier`
where one applies, the `backend` that produced it, and an occurrence `count`.
`--report path.json` writes that list; it is also embedded in every export as
`meta.diagnostics`, alongside `meta.diagnosticSummary`'s per-severity and
per-category totals. `--validate-only` parses and reports without writing a
viewer. `--strict` exits 5 if any diagnostic is `error` severity;
`--fail-on` (repeatable) extends that to specific severities or categories,
e.g. `--fail-on budget` to also fail on a truncated export. A `--strict`
export without `--validate-only` still writes its output for inspection; it
just does not exit 0.

"Save view" in either viewer writes a `.view.json` (camera, filters, selection,
clipping, theme, keyed by placement path rather than numeric id); "Load view"
restores it and reports any entries that no longer exist, and `--view-state
FILE` embeds one as the initial view. See `docs/limitations.md`.

Exports record their provenance (tool/backend versions, input name and sha256,
resolved settings, units). Add `--redact-paths` before sharing an artifact to
drop the absolute source path while keeping the filename and digest; with
explicit options it makes the output byte-for-byte reproducible. For lazy
exports, `--bundle out.zip` also writes a deterministic archive, and
`--reuse-assets` verifies the source digest, build options and every chunk's
sha256 before refreshing the HTML.

## Comparing two geometries

```bash
gdml-inspector --diff nominal.gdml modified.gdml --report diff.json
```

Matches placements by path, ignoring Geant4's regenerated `0x...` address
suffixes on every path segment and tolerant of reordered `<solids>`/
`<structure>` definitions, then classifies each matched pair's **material**,
**solid type**, **dimensions** (the solid's own numeric parameters, matched
by name), **translation**, and **orientation** (rotation and/or reflection,
reported together since a pure reflection and an ordinary 180-degree
rotation are not reliably distinguishable from a composed transform alone;
see [limitations](docs/limitations.md)) independently, plus **added**/
**removed** placements. Solids are compared by their own resolved numeric
parameters, not by name, so differently-named-but-identical definitions in
each file still compare equal. Repeated anonymous placements that cannot be
matched one-to-one are reported as **ambiguous** rather than paired by
guesswork.
`--tolerance` (default `1e-6`, mm/radians) sets how large a numeric
difference counts as unchanged. The exit code follows the Unix `diff`
convention (not `--strict`'s severity model, a different question): `0` no
differences, `1` differences found, `2` a file is missing, `3` the
requested backend is unavailable. `--report PATH` writes the full
comparison as JSON; a human-readable summary always prints to stdout.
Synchronised side-by-side viewing of both geometries is not implemented —
this is a reporting tool, not a viewer feature.

## Exporting to glTF/GLB

```bash
gdml-inspector --gltf detector.gdml --backend python --out exports/detector.glb
```

Writes a self-contained binary glTF (`.glb`); use `--out model.gltf` instead
for the plain-text `.gltf` + sibling `.bin` form. Unlike the single-file and
lazy exporters, this builds a true node hierarchy: each GDML placement is a
glTF node nested under its parent, so a repeated logical volume shares one
glTF mesh by reference instead of being baked into world space once per
placement. `--include-containers`, `--include-world`, `--quality`,
`--max-objects`, `--max-vertices`, `--backend`, and `--report` work the same
as the other export modes; see [limitations](docs/limitations.md) for the
unit/axis/normal choices made by this exporter (millimetre-to-metre
conversion only, no Y-up axis remap, no per-vertex normals) and for how its
correctness is checked without an installed glTF validator.

## Python API

```python
from pathlib import Path
from gdml_inspector.api import Geometry, world_triangles

geometry = Geometry.load(Path("detector.gdml"), backend="python")
for placement in geometry.placements:
    mesh = geometry.solids[placement.solid]  # shared across repeated placements
    triangles = world_triangles(geometry, placement)  # mm, world-space
    print(placement.path, placement.material, len(mesh.triangles))
print(geometry.bounds, len(geometry.diagnostics))
```

`gdml_inspector.api` is a small, documented, stable entry point for reading
placements, materials, meshes, bounds, and diagnostics from Python, without
pulling in Streamlit, Plotly, ROOT, or the optional pyg4ometry backend
unless `backend="pyg4ometry"` is explicitly requested. See its module
docstring and [limitations](docs/limitations.md) for units, identities,
resource-budget semantics, and error behaviour, and
[CHANGELOG.md](CHANGELOG.md) for its versioning policy.

## Development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13.6,
then use the committed lockfile:

```bash
uv sync --locked
uv run --no-sync pytest
uv run --no-sync python -m ruff check .
uv run --no-sync python -m ruff format --check .
uv run --no-sync mypy
uv run --no-sync python -m build --no-isolation
```

To see package and branch coverage, run
`uv run --no-sync pytest --cov=gdml_inspector --cov-report=term-missing`.
The [testing guide](docs/testing.md) explains the analytic fixtures, deterministic
invariant checks, tracked expected failures, and initial coverage baseline.

`.python-version` selects Python 3.12.16 for development; the package still
supports Python 3.10 and newer. The default `dev` group includes the separate
`test`, `lint`, `typing`, `build`, and `hooks` groups. Optional pyg4ometry is not installed
unless requested with `uv sync --locked --extra pyg4ometry`. Ordinary pip
installations continue to use the package metadata independently of `uv.lock`.

See the [contributor guide](docs/contributing.md) for individual groups, pip
development setup, dependency updates, compatibility checks, and the branch/PR
workflow. Development tools are no longer exposed as `.[dev]` or `.[test]`
package extras. See [benchmarks](docs/benchmarks.md) for the stress generator, benchmark runner and a measured baseline, and [releasing.md](docs/releasing.md) for how a version is
prepared, validated, and published.

To enable local lint/format fixes before each commit:

```bash
uv run --no-sync pre-commit install
uv run --no-sync pre-commit run --all-files
```

Hooks use the locked Ruff offline without syncing the environment. After a hook
changes a file, review the diff and stage it again before retrying the commit.

The `src/gdml_inspector` package separates geometry/meshing, the optional backend,
single-file and lazy exporters, local serving, and bundled HTML templates.
Both templates ship inside the wheel. Python APIs are provisional during the
initial extraction. [Architecture](docs/architecture.md) describes the boundaries;
the [roadmap](docs/roadmap/README.md) links detailed GitHub feature issues.
