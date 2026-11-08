# Reference validation against ROOT's TGeo GDML importer

This documents a one-time, manual comparison of gdml-inspector's placement
conventions against an independent GDML reader, as called for by
[#1](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/1)'s
acceptance criteria ("a documented subset is compared against trusted
Geant4/TGeo or pyg4ometry references"). It is not part of the automated test
suite: gdml-inspector is dependency-free by design (see [testing.md](testing.md)),
and ROOT/Geant4 are not installed in that environment.

## Method

[`reference_validation/validate_against_root.py`](reference_validation/validate_against_root.py)
builds five small GDML fixtures, each mirroring one analytic regression
already in gdml-inspector's own pytest suite (one per placement convention
resolved across #1: physvol rotation inversion, named transform references,
assembly expansion, scale/reflection, and Boolean first-operand transforms).
For each fixture it:

1. Builds the scene with `gdml_inspector.geometry.GDMLSceneBuilder` directly
   (imported from source; no package install needed, since the base backend
   has no runtime dependencies).
2. Imports the same file with ROOT's `TGeoManager::Import()` and reads the
   global transform ROOT itself computed for the placed node (translation
   and rotation matrix), independently of any gdml-inspector code.
3. Projects ROOT's transform onto the same box's analytic bounding box using
   the standard AABB-of-a-transformed-box formula, and compares it to
   gdml-inspector's reported bbox.

Run it inside any environment with PyROOT available, for example:

```bash
source /path/to/a/checkout/with/setup_cvmfs.sh   # or any ROOT/PyROOT setup
python3 docs/reference_validation/validate_against_root.py
```

## Results

Run against ROOT 6.38.00 / PyROOT (CVMFS `LCG_109` view, 2026-11-08):

```
[PASS] physvol rotation inversion (90deg): gdml-inspector=[8.0, 14.0, 27.0, 12.0, 16.0, 33.0] root=[8.0, 14.0, 27.0, 12.0, 16.0, 33.0]
[PASS] named positionref resolution: gdml-inspector=[9.0, 18.0, 27.0, 11.0, 22.0, 33.0] root=[9.0, 18.0, 27.0, 11.0, 22.0, 33.0]
[PASS] assembly expansion: gdml-inspector=[9.0, -2.0, -3.0, 11.0, 2.0, 3.0] root=[9.0, -2.0, -3.0, 11.0, 2.0, 3.0]
[PASS] scale/reflection composition: gdml-inspector=[4.0, 18.0, 27.0, 6.0, 22.0, 33.0] root=[4.0, 18.0, 27.0, 6.0, 22.0, 33.0]
[PASS] Boolean first-operand firstposition (as given, not inverted): gdml-inspector=[-5.0, 0.0, 0.0] root=[-5.0, 0.0, 0.0]

All 5 checks agree with ROOT's TGeo GDML importer.
```

All five conventions agree with ROOT's independent GDML reader to floating-point
precision. This specifically confirms:

- **Rotation inversion** (#30): ROOT's own rotation matrix for the 90-degree
  case is `[[0,1,0],[-1,0,0],[0,0,1]]`, exactly `Rz(-90°)` — the same inverse
  this codebase applies in `placement_matrix()`.
- **Named transform references** (#31): a `positionref` resolves to the exact
  translation ROOT reports for the referenced `<define>` entry.
- **Assembly expansion** (#31): ROOT expands an `<assembly>`'s physvols
  relative to the assembly's own placement in its mother, the same convention
  `place_physvols()` uses; a cross-check this codebase had no way to make
  before this validation, since assemblies have no analogue in a flat
  placement tree.
- **Scale/reflection** (#32): ROOT reports the combined matrix as a
  reflection (`refl=1`, determinant -1) with the same `diag(-1, 1, 1)`
  component gdml-inspector's `scale_matrix()` produces, composed in the same
  order (scale, then rotation, then translation).
- **Boolean first-operand transforms** (#32): ROOT's `TGeoCompositeShape`
  applies a `firstposition` to its first operand exactly as given, not
  inverted — the same convention `solid_mesh()` uses, and distinct from the
  physvol rotation convention above.

## A caveat found while validating

ROOT's `TGDMLParse` (as of ROOT 6.38) silently drops an inline
`<position>`/`<rotation>`/`<scale>` element inside a `<physvol>` unless it
has a `name` attribute, logging `Position  not defined` and treating the
transform as absent (zero translation, identity rotation/scale). GDML does
not require a name on inline elements, and Geant4's own
`G4GDMLReadStructure` has no such restriction — nor does gdml-inspector's own
parser, which treats named and unnamed inline transforms identically. Every
fixture in the validation script names its inline transforms purely to work
around this ROOT-side limitation.

## What this does not cover

- Subtraction/intersection meshing itself is not compared: gdml-inspector
  openly approximates these by their (transformed) first operand and warns
  about it (see [limitations.md](limitations.md)); there is no claim of CSG
  fidelity to compare.
- Named `rotationref`/`scaleref` resolution, duplicate-definition warnings,
  and cyclic-reference rejection are exercised by gdml-inspector's own test
  suite but not independently re-verified against ROOT here, since they are
  either pure lookups (no new geometric convention to cross-check) or
  gdml-inspector-side safety nets with no ROOT equivalent to compare against.
- pyg4ometry was not used for this round; `TGeoManager::Import()` was readily
  available in the same environment and already gave an independent reader
  from a different codebase.
