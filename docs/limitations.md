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
  volume/assembly reference, or nesting beyond 256 levels, raises an
  actionable error instead of exhausting the Python call stack.
- The optional pyg4ometry backend can improve solid meshes while still using
  these local placement rules. A successful export is not a fidelity guarantee.

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
- `--reuse-assets` checks the stored source filename and existence of referenced
  chunks, not a content digest. It refreshes HTML/UI without validating that
  geometry or build settings are unchanged. Rebuild after changing those.

## Distribution and provenance

Exports have no remote CDN dependencies. Single HTML exports can be opened
offline, whereas chunked exports must be served over HTTP with their asset
directory alongside them. Absolute source paths are retained in embedded
metadata by the inherited exporters; inspect them before sharing artifacts.
Use `--backend python` or `--backend pyg4ometry` to avoid the environment-dependent
choice of `auto`. Fully deterministic exports, source digests, metadata redaction,
and versioned scene contracts are roadmap items.

The dependency-free backend and packaged HTML are covered by automated tests.
The optional pyg4ometry dependency is not required for that test suite and has
not been certified against every pyg4ometry version or GDML dialect.
