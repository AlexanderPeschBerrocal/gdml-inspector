# Make offline exports reproducible, shareable, and restorable with saved view state

## Problem and current behaviour

Standalone HTML already embeds all assets and lazy HTML uses relative binary chunks, so portable exports are an existing strength. However, exports embed absolute source paths, backend auto-selection depends on the environment, asset reuse checks filenames rather than geometry contents, and there is no saved camera/filter/clipping state or documented stable scene schema.

## Proposed work

Define versioned scene/manifest metadata with tool/backend versions, input digest, resolved build settings, units, and diagnostic summary. Make reproducible output possible for the same input/options without injecting timestamps or machine paths into geometry content. Provide a metadata-redaction policy for shared artifacts.

Save and restore camera, projection, visibility/filters, selection, clipping, colours, and theme. Support a view-state sidecar or embedded state with schema/version checking. For lazy exports, provide a portable directory/archive bundle and validate assets using digests rather than a basename. Preserve convenient single-file HTML output.

## Acceptance criteria

- Two builds in different directories with explicit identical settings produce equivalent normalised artifacts; define whether byte-for-byte equality is guaranteed.
- No network/CDN fetch is required beyond relative lazy assets.
- Exported artifacts can omit absolute local paths without losing filename/digest provenance.
- Saved views restore deterministically and report unknown/stale placement IDs.
- Reuse refuses changed geometry/settings or missing/corrupt chunks with an actionable message.
- Bundles can be moved to a different URL/path, including spaces and Unicode, and still load.
- Old schema versions either migrate or fail with clear compatibility guidance.

## Verification and dependencies

Test fresh environments, relocated bundles, independent exports with different source directories, source edits retaining the same filename, and invalid view-state files. Coordinate identifiers and view settings with hierarchy, filtering, clipping, and picking work.
