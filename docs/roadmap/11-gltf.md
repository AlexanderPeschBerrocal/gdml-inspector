# Export geometry to glTF/GLB for external rendering and presentation tools

## Motivation

Browser HTML is convenient for inspection, but users may want to reuse detector geometry in Blender, presentation pipelines, or other 3D applications. A standard scene export avoids forcing every downstream consumer to understand this viewer's private scene/chunk format.

## Proposed work

Add optional glTF 2.0/GLB export from the same validated scene graph and mesh data used by both viewers. Preserve hierarchy, repeated-mesh instancing, material colours/opacity, placement names, and useful metadata where the format permits. Make unit and coordinate-system conversion explicit: internal millimetres must map consistently into the exported format's metre-based convention.

Support exporting the complete supported geometry or a named/filtered subset using documented selection semantics. Record approximations and skipped constructs in an adjacent report or metadata. Evaluate minimal dependencies and keep the core viewer installation lightweight.

## Acceptance criteria

- An exported analytic fixture retains dimensions, relative transforms, hierarchy, and materials when loaded by an independent glTF consumer.
- Repeated logical shapes reuse meshes where possible.
- GLB is self-contained; external-buffer glTF uses portable relative paths.
- Output passes an appropriate glTF validator or schema checks.
- Unsupported solids and source approximation diagnostics remain discoverable.
- Coordinate-axis and unit choices are documented and round-trip tests check physical dimensions.
- Exporting a filtered scene does not accidentally include hidden unsupported data.

## Verification and dependencies

Use boxes and off-origin/rotated tubes with known sizes plus a repeated-placement fixture; compare in an independent importer. This is downstream of scene identity and transform fidelity. Treat glTF as a visual mesh interchange format, without implying it preserves every GDML analytic or physics property.
