# Unify material, volume-name, path, and solid filters across viewer modes

## Problem and current behaviour

The single-file export has material toggles, free-text metadata search, and a solid-type selector. Lazy mode currently offers a material legend and subsystem/layer loading controls. Users cannot express the same selection in both modes, and detector-name heuristics limit portability.

## Proposed work

Create one selection model with material, logical-volume name, physical placement name/path, solid type, and optional subsystem groups. Support clear include/exclude semantics, a visible active-filter summary, reset-all, invert/isolate actions, and saved selections. Start with literal matching; any regex or wildcard mode must be explicit and handle invalid patterns safely.

Keep original identifiers available even if labels strip Geant4 address suffixes. Allow optional user-defined grouping rules or a sidecar configuration for detector-specific subsystems instead of embedding further CMS/ATLAS/COCOA naming assumptions in the core.

## Acceptance criteria

- Equivalent filters select the same placement IDs in standalone and lazy viewers.
- Material matching uses stable material identity rather than rendering colour.
- Filtering unloaded geometry works from metadata and does not fetch every chunk merely to evaluate a selection.
- The interface reports matched versus currently loaded/visible object counts and explains an empty selection.
- Volume/path filters distinguish two physical placements of the same logical volume.
- Changing or resetting filters does not leave stale picks, wireframe buffers, or loading controls.
- Saved selections can be restored with a clearly reported warning for unknown identifiers.

## Verification and dependencies

Use a fixture with repeated placements, similarly named materials, unusual Unicode names, and a subset spanning multiple chunks. Coordinate with the hierarchy and reproducible-view-state issues. Define whether multiple filter categories combine with AND and how includes/excludes compose before implementing the UI.
