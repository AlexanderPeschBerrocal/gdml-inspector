# Geometry tests and coverage

The base suite exercises the bundled Python backend with original synthetic
GDML inputs and standard Python numerical checks. It needs no ROOT, Geant4,
pyg4ometry, detector downloads, or browser. Dependencies are in the local `test`
group; none are runtime requirements of the viewer.

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

## Independent expectations

| Test module | Contract checked |
| --- | --- |
| `test_units.py` | Explicit length/angle conversion factors, positions, and full box lengths |
| `test_transforms.py` | Active basis-vector rotations, composition order, Euler helper order, nested placement bounds |
| `test_meshes.py` | Box area, tube/cone radii and extents, cut-plane equations, nondegenerate triangles and closed edges |
| `test_invariants.py` | Rotation distance preservation, affine composition, unit-equivalent boxes |
| `test_parser.py` | Small analytic scenes, repeated placements, namespaces, selection flags, missing references and approximation warnings |
| `test_chunks.py` | Independently decoded little-endian payloads, translated vertices, packing, asset reuse |
| `test_limits.py` | Object caps, complete-object vertex boundaries, empty budgets, lazy overshoot rejection |
| `test_cli.py`, `test_version.py`, `test_exports.py` | Error/absence paths, launcher version options, portable HTML, script escaping |
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

Six tests assert desired behaviour that the current implementation does not
yet provide:

- [#1](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/1): inverse physical-placement rotation, named positions, unitless solid-angle units, and assembly expansion.
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
