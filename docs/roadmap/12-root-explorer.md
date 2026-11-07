# Expose a small geometry API and migrate ROOT Explorer to the shared implementation

## Problem and motivation

ROOT Explorer currently owns a separate GDML parser/mesher for Plotly event overlays. GDML Inspector now owns the standalone WebGL viewers. A shared geometry implementation would let fixes to transforms, units, supported solids, and warnings benefit both applications without copying code between repositories.

## Proposed work

After consolidating fidelity, define a small documented Python API for reading a GDML scene, selecting placements/materials, obtaining meshes/transforms and bounds, and consuming structured diagnostics. Keep browser-specific HTML export and Plotly/Streamlit integration outside that core API.

Design an adapter in ROOT Explorer that translates the shared scene into its existing overlay traces and preserves its cache invalidation, source identity, scaling/offset settings, and resource limits. Work out a release/dependency policy before changing the consuming repository. This issue authorises planning here; the separate ROOT Explorer migration should be reviewed in its own change.

## Acceptance criteria

- The public API has documented units, placement/mesh identities, resource-budget semantics, and error behaviour.
- Importing the core does not require Streamlit, Plotly, ROOT, or optional pyg4ometry.
- Shared fixtures prove parity between Inspector exports and the ROOT Explorer adapter.
- A published/versioned dependency or documented development installation replaces source copying.
- ROOT Explorer retains event overlay behaviour, change detection, warnings, and coordinate conversion.
- Python-version compatibility is explicit for both packages.
- Version changes document scene/API compatibility and provide migration guidance.

## Verification and dependencies

Use the existing ROOT Explorer geometry tests as a regression reference after checking their licensing/attribution. Depends on parser consolidation, diagnostics, and a sufficiently stable scene model; avoid prematurely making the current internal dictionaries a permanent public contract.
