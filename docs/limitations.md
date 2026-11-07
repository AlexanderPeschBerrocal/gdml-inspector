# Inherited limitations

This release packages the existing COCOA viewers. It does not establish full
GDML conformance. Inspect the warnings and compare important geometry against a
trusted source before drawing conclusions from a rendered alignment.

The [testing guide](testing.md) distinguishes independently verified operations
from six strict expected-failure regressions for tracked gaps. A passing base
suite is not a claim that those gaps have been fixed.

## Parsing and placements

- Pure Python supports numeric `box`, `tube`, `cutTube`, `cone`, and approximate
  Boolean meshing. Other solid types are skipped with warnings.
- Only ordinary nested `volume`/`physvol` placements are traversed. Assemblies,
  replicas, divisions, parameterisation, and external-file references are not
  expanded. Those constructs do not yet consistently emit warnings.
- Inline numeric positions and rotations are read. Named transform references,
  scale/reflection, and constants/expressions are not resolved. Unparseable
  numeric values fall back to defaults; unknown units can silently fall back.
- The inherited helpers use degrees as the missing solid-angle default, but
  radians for unitless inline rotations. Their placement rotation composition
  has not been reconciled with the Geant4 conventions handled in ROOT Explorer.
  Prefer explicit units; explicit units alone do not fix rotation conventions.
- Duplicate definitions can overwrite earlier definitions. Deep/cyclic
  references do not yet receive targeted diagnostics. The CLI reports a failed
  recursion as an error, but there is no complete reference-graph validator.
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
