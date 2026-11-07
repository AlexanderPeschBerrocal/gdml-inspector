# Consolidate GDML parsing with ROOT Explorer and validate geometry fidelity

## Problem and current behaviour

The extracted viewers share one parser, but ROOT Explorer maintains another GDML parser that has already gained named transforms, scales/reflections, assemblies, and more explicit diagnostics. Maintaining both independently will reproduce the current divergence. The extracted parser currently reads ordinary physvol placements and a small solid subset; numeric expressions can fall back silently, missing angle units are handled inconsistently, and pyg4ometry only replaces solid meshing, not placement traversal.

## Proposed work

Establish one validated geometry representation and one authoritative parser/transform implementation in this repository. Review ROOT Explorer's parser and tests and port applicable behaviour with its source attribution intact. Resolve GDML positionref/rotationref/scaleref and Boolean first-operand transforms, apply Geant4's correct placement-versus-Boolean rotation conventions, support assemblies, and evaluate supported constants/expressions safely without Python eval. Distinguish unsupported constructs from malformed input. Define a policy for replicas, divisions, parameterised/external placements, duplicate names, cycles, and nesting limits.

Keep the dependency-free subset explicit. Document where the optional pyg4ometry backend changes meshing and where it cannot repair traversal. Do not imply full GDML support simply because one backend can mesh more solids.

## Acceptance criteria

- Analytic fixtures verify dimensions and global coordinates for translated/rotated nested placements, reflected scales, named references, assemblies, and Boolean operands.
- Unit defaults and conversion rules are consistent and tested, including unitless angles and unsupported units.
- Broken references, duplicate definitions, cycles, and unsupported daughter constructs produce actionable diagnostics.
- Both export modes consume the same scene graph and retain identical placements.
- A documented subset is compared against trusted Geant4/TGeo or pyg4ometry references; approximate meshes are identified.
- Existing COCOA geometries continue to export, with intentional fidelity changes documented.

## Verification and dependencies

Use small, redistributable fixtures with known coordinates; visual screenshots alone are insufficient. This is foundational for hierarchy browsing, precise picking, geometry comparison, and eventual ROOT Explorer integration. Preserve the current CLI while the internal API evolves.
