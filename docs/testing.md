# Geometry tests and coverage

The base suite exercises the bundled Python backend with original synthetic
GDML inputs and standard Python numerical checks. It needs no ROOT, Geant4,
pyg4ometry, detector downloads, or browser. Dependencies are in the local `test`
group; none are runtime requirements of the viewer.
[`reference-validation.md`](reference-validation.md) documents a separate,
manual, one-time comparison of the placement conventions below against ROOT's
GDML importer, using a script that is not part of this suite for the same
reason.

```bash
uv sync --locked --no-default-groups --group test
uv run --no-sync pytest
uv run --no-sync pytest --cov=gdml_inspector --cov-report=term-missing --cov-report=xml --cov-report=html
```

The second test invocation also writes `coverage.xml` and
`htmlcov/index.html`. For ordinary development, `uv sync --locked` includes the
same test group alongside the other development tools. To run just the
numerical checks, use `uv run --no-sync pytest tests/test_units.py
tests/test_transforms.py tests/test_meshes.py tests/test_invariants.py`.

## Static type checking

```bash
uv sync --locked --no-default-groups --group typing
uv run --no-sync mypy
```

Scope is explicitly `files = ["src/gdml_inspector"]` in `[tool.mypy]`
(`pyproject.toml`) -- not `tests/`. `disallow_untyped_defs`/`warn_return_any`/
`warn_unused_ignores` are on, and the only suppression is a single
`[[tool.mypy.overrides]]` for `pyg4ometry.*` (it ships no inline types or
stubs); installing the `typing` group alone never installs pyg4ometry
itself. This is a gradual rollout, not full coverage: `geometry.py`'s
`ObjectDict`/`SceneDict`/`DiagnosticDict` TypedDicts type the core scene
model precisely (catching, for example, a `str`-typed field being passed
an `int`), and `diff.py`'s consumption of that same shape is typed with
it; `lazy.py`'s manifest/chunk-entry dict and `diff.py`'s own comparison-
report dict are a different, separate shape and remain `dict`/
`dict[str, object]`, explicitly deferred rather than left as a silent gap
(see [limitations](limitations.md)).

## Optional pyg4ometry backend

```bash
uv sync --locked --extra pyg4ometry
uv run --no-sync pytest tests/test_pyg4ometry_backend.py
```

A dedicated CI job (`.github/workflows/pyg4ometry.yml`) installs the extra
and runs this file alone, separately from the base-package matrix in
`ci.yml` — the extra is never installed merely to obtain this job's own
coverage, and `tests/test_pyg4ometry_backend.py` itself starts with
`pytest.importorskip("pyg4ometry")` so the base-package matrix (and
ordinary local development without the extra) cleanly skips it rather
than failing. Tested combination: CPython 3.12.16 (the committed
`.python-version`) on `ubuntu-latest` (Linux x86_64); pyg4ometry also
publishes macOS and Windows wheels, but only Linux is exercised in CI,
matching every other workflow's runner.

What it checks: backend identity (`meta["backend"] == "pyg4ometry"`) and
analytic bounds for a plain box, so an unexpected silent fallback to the
Python mesher would change the numbers or add a warning either way; a
Boolean-solid (`<subtraction>`) fixture shared with `test_parser.py`'s own
Python-backend approximation test, asserting pyg4ometry produces more
triangle data and no approximation warning for the exact case where the
Python backend is known to just return the first operand unchanged — the
concrete, reproducible reason this backend exists; and that `--backend
pyg4ometry` actually engages the backend through both the `standalone` and
`--lazy` CLI entry points, not just the builder class directly. The
absence-of-pyg4ometry CLI failure path (`--backend pyg4ometry` without the
package installed) is already covered by the base-package suite
(`tests/test_api.py`, the standalone/diff/gltf/lazy CLI exit-code-3 tests)
without needing the package at all. `tests/test_diagnostics.py`'s own
`_diagnose_fallback()` test is a different, narrower layer: it checks the
diagnostic's shape via `Pyg4ometrySceneBuilder.__new__()`, with no real
pyg4ometry involved.

## Independent expectations

| Test module | Contract checked |
| --- | --- |
| `test_units.py` | Explicit length/angle conversion factors, positions, full box lengths, unitless length/angle defaults, and safe GDML expression evaluation |
| `test_transforms.py` | Active basis-vector rotations, composition order, Euler helper order, nested placement bounds, named/inline scale reflection |
| `test_meshes.py` | Box area, tube/cone radii and extents, cut-plane equations, nondegenerate triangles and closed edges |
| `test_invariants.py` | Rotation distance preservation, affine composition, unit-equivalent boxes |
| `test_parser.py` | Small analytic scenes, repeated placements, namespaces, selection flags, named transform references, constant/variable expression chains, assembly placement, Boolean first-operand transforms, duplicate-definition and cyclic-reference diagnostics, unsupported-daughter-construct warnings, missing references and approximation warnings |
| `test_chunks.py` | Independently decoded little-endian payloads, translated vertices, packing, asset reuse |
| `test_limits.py` | Object caps, complete-object vertex boundaries, empty budgets, lazy overshoot rejection |
| `test_diagnostics.py` | Diagnostic aggregation by code/construct/identifier, severity/category summaries, every emitted code's shape in both export modes |
| `test_diff.py` | Placement matching (raw-path, sole-leftover, repeated-path ambiguity), independent translation/orientation/scale/dimension/material/solid-type classification, reordered-definition and address-suffix-rename non-changes, Boolean-solid limitations, `--diff` CLI exit codes and `--report` |
| `test_gltf.py` | An independently decoded GLB container (header, JSON/BIN chunk framing and padding) and hand-written structural glTF checks; millimetre-to-metre unit conversion for vertices and node translations, mesh-index sharing across repeated placements, a hidden container's empty pivot node with correctly nested children, reflected-placement `doubleSided` materials, alpha-mode selection, `.gltf`+`.bin` external output, unsupported-solid diagnostics surviving into `--report`, `--gltf` CLI exit codes |
| `test_api.py` | `gdml_inspector.api`: placement path/material/matrix identity, mesh-object sharing by solid ref, bounds, unsupported-solid and budget-limit diagnostics, `include_containers`/`include_world` parity with other export modes, `backend="pyg4ometry"` raising `ModuleNotFoundError` (not a wrapped `RuntimeError`) when missing, silent `backend="auto"` fallback, and that importing the module pulls in no optional dependency |
| `test_cli.py`, `test_version.py`, `test_exports.py` | Error/absence paths, launcher version options, portable HTML, script escaping, `--strict`/`--fail-on`/`--report`/`--validate-only` |
| `test_release.py` | `scripts/release.py`/`scripts/check_release.py` exercised against a disposable copy of this repo's own working tree: a clean release round trip, dirty-tree/wrong-branch/duplicate-tag rejections (each asserted to leave zero trace), and a tampered-committed-version detection |
| `test_pyg4ometry_backend.py` | Skipped unless the optional pyg4ometry extra is installed (dedicated CI job only): backend identity, analytic bounds, real Boolean-solid tessellation vs. the Python backend's approximation, and `--backend pyg4ometry` through both CLI entry points |
| `test_known_limitations.py` | Desired behaviour for tracked gaps, marked as strict expected failures |

`conftest.py` generates small self-contained files in pytest's temporary
directories. `helpers.py` supplies builder setup and independent geometric
measurements; expected bounds and dimensions are calculated from the fixtures,
not from a second invocation of the production transform functions. The
existing example detector remains an export/packaging regression fixture.

Hypothesis checks use at most 40 examples per property, bounded integer inputs,
`derandomize=True`, `database=None`, and no timing deadline. They do not create
an example database or depend on the contents of a developer's previous runs.
The three properties complement the fixed analytic examples; they do not prove
general GDML compliance.

Curved surfaces use an explicit 24-segment resolution in the fixed mesh tests.
Full-tube bounds include cardinal-angle samples. Surface equations use explicit
units and millimetre-scale dimensions, and floating-point comparisons state
their tolerances. Edge closure uses nine-decimal seam matching for those
fixtures. It does not establish outward triangle winding, exact CSG behaviour,
or a globally valid detector geometry.

The chunk tests decode the v1 protocol independently of the production header
constant: `<8s6I` is a 32-byte header with magic `GDMLCHN1`, version, vertex
count, position offset, colour offset, end/ID offset, and total byte size.
Position triples are little-endian float32 and colours are RGBA bytes. Packing
tests deliberately use coincident placements to exercise a common layer; that
fixture is not a physical detector overlap check.

## Known limitations are expected failures

An active rotation matrix helper and a GDML physical placement have different
roles. The active-helper tests use ordinary right-handed point rotations.
For a physical placement, Geant4's
[GDML structure reader](https://github.com/Geant4/geant4/blob/master/source/persistency/gdml/src/G4GDMLReadStructure.cc)
uses the inverse of the supplied rotation. Its
[solid reader](https://github.com/Geant4/geant4/blob/master/source/persistency/gdml/src/G4GDMLReadSolids.cc)
also provides the reference for unitless solid angles. These conventions were
checked against the reference source, without copying its implementation or
requiring Geant4 during testing.

Two tests assert desired behaviour that the current implementation does not
yet provide:

- [#7](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/7): a hard standalone vertex budget when one object would exceed it.
- [#8](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/8): an actionable diagnostic for an unknown length unit.

Those tests actually run. Each expects only an assertion failure, so unrelated
crashes remain failures. `xfail_strict = true` makes an unexpected pass fail
the suite: when an implementation fixes a gap, remove that test's marker and
keep its numerical expectation as a normal regression test. Do not change the
expected output to match a known bug. Other limitations remain documented in
[limitations.md](limitations.md); this is not an exhaustive conformance suite.

## Initial coverage baseline

Measured for this test-suite addition on Python 3.12.16 with the locked test
dependencies: **88 passed, 6 expected failures**. The pytest case count includes
each property test once, rather than counting each generated example.

| Scope | Combined statement/branch coverage |
| --- | ---: |
| Complete Python package | 58.3% |
| `geometry.py` | 98.2% |
| `lazy.py` | 81.0% |
| `standalone.py` | 80.2% |

Package totals are 693/1094 executable statements (63.3%) and 162/372 branches
(43.5%). Coverage includes every Python package module, including the mostly
unexercised optional backend and local HTTP server. Nothing is omitted to raise
the total. Executed lines from expected-failure tests also count as coverage;
high execution coverage does not establish correct physics or geometry.

The baseline is informational, not a fail-under threshold. Use the missing-line
report to select meaningful regressions. Native backend tests belong to
[#24](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/24), browser
rendering/interaction checks to
[#18](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/18), and
broader version consistency to
[#20](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/20).

CI reports package/branch coverage for every matrix interpreter and uploads
`coverage.xml` and the HTML report as `coverage-python-<version>` artifacts,
including on test failure when reports exist. Artifacts are retained for
14 days. The independent source/wheel build and installed-wheel export checks
remain separate from the unit tests.
