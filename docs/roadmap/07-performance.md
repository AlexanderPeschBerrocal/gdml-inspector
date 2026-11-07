# Scale large geometries with instancing, bounded preprocessing, and robust lazy loading

## Problem and current behaviour

Lazy export already fetches binary chunks by subsystem/layer and can unload hidden GPU geometry. However, Python builds all meshes before writing, repeated placements duplicate triangle positions, the renderer has limited resource accounting, and a single object can exceed the nominal chunk size. The standalone vertex cap can overshoot by its final whole object.

## Proposed work

Separate reusable solid meshes from placement transforms and evaluate browser instancing for repeated shapes. Stream or incrementally write preprocessing output so large exports do not require the entire expanded scene in memory. Define hard object/vertex/memory budgets, splitting only on valid triangle boundaries and reporting exactly what was omitted.

Improve browser loading with bounded concurrent fetches, progress/error/retry states, cancellation or stale-request suppression, and explicit CPU/GPU cache eviction. Use detector-independent hierarchy/material/spatial chunking or configurable grouping; keep current COCOA grouping as a preset. Consider level-of-detail only after measuring bottlenecks.

## Acceptance criteria

- Benchmarks report preprocessing time, peak RSS, output bytes, initial transfer, frame rate, and loaded GPU/CPU bytes on documented synthetic and detector examples.
- Repeated placements reuse mesh payload where practical while preserving distinct IDs and transforms.
- Caps and chunk sizes have precise documented semantics, including one oversized solid.
- Rapidly toggling layers does not resurrect hidden geometry or leak buffers.
- A missing/corrupt chunk produces a local actionable error and can be retried.
- Material/path filtering and picking remain correct with instancing and chunk reloads.
- Small offline exports retain the simple installation experience.

## Verification and dependencies

Include a redistributable stress generator rather than committing huge detector files. Coordinate scene/chunk versioning with precise picking and reproducible export. Set performance targets from baseline measurements rather than unsupported claims about complete CMS/ATLAS support.
