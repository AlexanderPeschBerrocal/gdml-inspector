# Expose structured fidelity diagnostics, exportable warnings, and strict validation

## Problem and current behaviour

Both viewers show warning text, but some unsupported constructs and numeric fallbacks are silent. Warnings are not consistently linked to placement/solid identities, and users can mistake a visually plausible export for a complete geometry. Limit truncation and optional-backend fallback need clear reporting.

## Proposed work

Represent diagnostics with stable codes, severity, construct, source identifier/path, occurrence count, backend, and a concise explanation of what was skipped or approximated. Surface a fidelity summary in the CLI and UI; allow filtering and downloading JSON/text reports. Link a warning to the affected inspector/tree node when possible.

Add a validation-only command and configurable strict mode that fails on specified severities or approximation categories. Classify unsupported GDML separately from invalid references and resource-budget omissions. Avoid claiming a clean report proves conformance unless the parser actively checks all encountered constructs.

## Acceptance criteria

- Unsupported solids/daughters, unresolved transforms, malformed numeric values, duplicate names, cycles, and truncation are represented explicitly.
- pyg4ometry-to-Python meshing fallback is visible per affected solid.
- Reports survive both export modes and contain enough context to find the source construct.
- A strict invocation has a documented nonzero exit code and never silently produces a success report for incomplete geometry.
- Warning lists stay usable when many placements share the same unsupported solid through aggregation and expansion.
- Reports distinguish parsed, meshed, skipped, filtered, unloaded, and truncated counts.
- Diagnostic text and file-derived identifiers are escaped safely in HTML.

## Verification and dependencies

Create small fixtures intentionally triggering each code and assert stable machine-readable output as well as readable UI summaries. Depends on systematic parser construct detection; coordinate validation command syntax with the existing export CLI.
