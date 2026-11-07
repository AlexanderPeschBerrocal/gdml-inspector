# Add Cartesian clipping planes and consistent phi cutaway controls

## Problem and current behaviour

Both viewers already have shader-based phi cutouts; lazy mode can keep or remove a wedge. Users also need longitudinal/transverse sections and arbitrary slices to inspect supports and nested structures without hiding entire volumes.

## Proposed work

Make phi controls consistent between modes and add global x/y/z clipping planes, paired planes for slabs, and optionally an arbitrary plane defined by normal and offset. Show axis, sign, origin, and units explicitly. Provide common transverse/longitudinal presets, reset actions, and an optional visible plane gizmo.

Keep shader clipping interactive without retessellating the whole geometry. Treat clipped surfaces as open cuts unless an explicit capping algorithm is implemented; do not make a fragment discard appear to be a quantitative solid section.

## Acceptance criteria

- Standalone and lazy exports show the same kept region for identical settings.
- Phi wedges handle wraparound across -180/180 degrees and agree on the global +z convention.
- Multiple enabled planes have documented intersection semantics.
- Picking, wireframe, and selection highlighting use the same clipping rules as filled surfaces.
- Sliders and numeric entry stay synchronised; positions use the normalised geometry length unit.
- Clipping state is serialisable with saved views.
- The UI states whether cut faces are capped and does not imply capping when none exists.

## Verification and dependencies

Use analytic boxes/tubes with known extents, wedges spanning zero and wraparound, and nested off-origin placements. Verify both visible pixels and picking after clipping. This can reuse existing phi shaders but should share conventions and state definitions between frontends.
