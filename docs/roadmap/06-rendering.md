# Improve rendering quality, transparency, wireframe, and camera presentation

## Problem and current behaviour

The extracted viewers provide useful orbit/pan/zoom and six axis views. Standalone mode also has wireframe and light/dark themes; lazy mode lacks that parity. Transparency and triangulation edges can make dense detectors difficult to interpret, and screenshots should have clear scale and orientation.

## Proposed work

Introduce consistent rendering and camera controls across modes: opaque/translucent/wireframe presets, per-selection opacity or material colouring, configurable background, axes, a length scale indicator, perspective/orthographic projection, and reliable fit-to-selection/fit-all. Distinguish actual solid edges from all triangle edges when possible. Improve transparent surface ordering or choose a documented technique with known limits.

Keep scientific legibility central: thin silicon layers should remain distinguishable, colours should be editable and accessible, and clipping should remain fast. Add a clean screenshot export with source, units, and optional legend. Do not introduce remote browser dependencies for these controls.

## Acceptance criteria

- Wireframe and basic presentation settings work consistently in both export modes.
- Axis views preserve a clear coordinate convention, including after switching projection.
- A scale indicator reflects camera/projection and geometry units correctly.
- Transparent nested shells render predictably; remaining ordering artefacts are documented.
- A reset restores a usable view after extreme pan/zoom or an empty filter.
- Exported screenshots capture the rendered state at explicit output resolution without UI panels unless requested.
- Camera/filter/clipping changes do not break picking or selection highlighting.

## Verification and dependencies

Use nested translucent tubes, thin disks, overlapping meshes, and both light/dark backgrounds for browser regression images. Coordinate saved settings with portable-view export and selection metadata with the inspector issue.
