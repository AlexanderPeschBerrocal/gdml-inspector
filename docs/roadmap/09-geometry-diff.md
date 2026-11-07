# Compare two GDML geometries with semantic change reports and synchronised views

## Problem

A useful inspection workflow is determining what changed between nominal and modified detector geometries. Comparing generated images manually misses placement, material, and solid-parameter changes; comparing raw XML is noisy when Geant4 regenerates address suffixes or reorders definitions.

## Proposed workflow

Load geometry A and B, match logical definitions and physical placement paths, and classify added/removed volumes, changed materials, changed solid parameters, and moved/rotated/scaled placements. Provide synchronised side-by-side views and an optional overlay with distinct colours. Let users select a change, focus both cameras, and inspect the before/after properties.

Define stable matching rules with configurable tolerances and explicit ambiguity reporting. Removing address suffixes can aid matching but must not collapse distinct volumes silently. Compare original semantic geometry where supported, not tessellation arrays alone.

## Acceptance criteria

- Identical geometries with reordered definitions do not produce false changes.
- Known translation, rotation, material, dimension, addition, and removal edits are classified independently.
- Tolerances and normalised units are recorded in the report.
- Ambiguous repeated names require visible resolution or remain unmatched; no silent nearest-name pairing.
- Unsupported constructs and different approximation/backends are highlighted as comparison limitations.
- Camera, clipping, and selection can be synchronised between both versions.
- JSON and human-readable reports export independently of the live viewer, with source digests.

## Verification and dependencies

Maintain pairs of tiny fixtures differing in exactly one property, plus combined changes and renamed address-suffixed identifiers. Requires reliable scene identity, transforms, and structured diagnostics. A visual difference is not automatically a material-budget difference; quantitative ray/material comparisons belong in the optional material-tools integration.
