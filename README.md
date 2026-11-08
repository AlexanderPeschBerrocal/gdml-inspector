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
| Opacity and shader-based phi wedge | Yes | Yes; keep/remove modes |
| Material toggles, name/metadata search, solid-type filtering | Yes | Material legend only |
| Approximate click inspection of object metadata | Yes | Not yet |
| Wireframe overlay and light/dark theme | Yes | Not yet |
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

## Development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13.6,
then use the committed lockfile:

```bash
uv sync --locked
uv run --no-sync pytest
uv run --no-sync python -m ruff check .
uv run --no-sync python -m ruff format --check .
uv run --no-sync python -m build --no-isolation
```

To see package and branch coverage, run
`uv run --no-sync pytest --cov=gdml_inspector --cov-report=term-missing`.
The [testing guide](docs/testing.md) explains the analytic fixtures, deterministic
invariant checks, tracked expected failures, and initial coverage baseline.

`.python-version` selects Python 3.12.16 for development; the package still
supports Python 3.10 and newer. The default `dev` group includes the separate
`test`, `lint`, `build`, and `hooks` groups. Optional pyg4ometry is not installed
unless requested with `uv sync --locked --extra pyg4ometry`. Ordinary pip
installations continue to use the package metadata independently of `uv.lock`.

See the [contributor guide](docs/contributing.md) for individual groups, pip
development setup, dependency updates, compatibility checks, and the branch/PR
workflow. Development tools are no longer exposed as `.[dev]` or `.[test]`
package extras.

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
