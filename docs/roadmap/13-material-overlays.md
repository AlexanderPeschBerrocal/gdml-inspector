# Support optional material-map and ray-budget overlays through an external tools interface

## Motivation and scope

Material studies need to relate geometry to reconstructed material fields, uncertainty, and integrated radiation/interaction lengths. COCOA already contains material tracing and voxelisation utilities, while DRIFT reconstructs material fields. A geometry viewer could display their results without taking ownership of the reconstruction or physics calculations.

## Proposed work

Design an optional import/overlay interface for externally computed scalar/voxel material fields, ray segments, and integrated budgets. Start with a documented portable data contract including units, coordinates, grid edges, source geometry digest, quantity definition, and provenance. Useful quantities include density, inverse radiation length, inverse nuclear interaction length, uncertainty, and truth-versus-reconstruction residuals.

Render slices, sparse voxels, ray profiles, or colour overlays alongside the geometry. Clearly distinguish local coefficients from line integrals and physical ground truth from inferred quantities. Keep computation in DRIFT or a separate material-tools package; optional adapters can live here without making those packages mandatory dependencies.

## Acceptance criteria

- One small synthetic field and one ray-budget fixture load with explicit coordinate/length units and align with the example geometry.
- Invalid grid shapes, missing units, nonfinite data, and incompatible geometry provenance receive clear diagnostics.
- Colour bars and legends identify quantity, units, transformation, and uncertainty semantics.
- Users can toggle geometry and overlays independently while clipping them consistently.
- Exported views retain overlay provenance and disclose any downsampling.
- Core installation and ordinary GDML export still work with no inference or ROOT dependencies.
- The adapter contract is documented with examples that DRIFT/material-tools can produce independently.

## Verification and dependencies

Test alignment and unit conversions numerically before screenshot testing. Do not compute X/X0 from tessellated visibility meshes or call a visual alignment a validated material map. Build on the reliable scene coordinate model and portable export metadata; quantitative material mapping remains a separate project.
