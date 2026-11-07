# Implement depth-correct object picking and a detailed placement inspector

## Problem and current behaviour

The standalone viewer chooses objects by sampling projected mesh vertices near a click. This can select an occluded object or miss the middle of a large face. Lazy chunks do not retain per-object picking IDs, and the lazy viewer has no equivalent inspector.

## Proposed experience

Click a visible surface to select the frontmost eligible placement, highlight it, and show its physical path, logical volume, material, solid type/parameters, local and global transforms, dimensions, and global bounds. Label original solid parameters separately from tessellated bounding-box dimensions. Provide copy-name/path and focus/isolate actions, with links into the hierarchy.

Use an ID-buffer or ray/triangle picking approach that respects active filters and clipping. Decide and document how translucent surfaces are selected. Retain stable object IDs and metadata in lazy export; version the chunk format if its binary layout changes.

## Acceptance criteria

- Clicking the centre of a large face selects it even when no sampled vertex is nearby.
- Occluded, hidden, unloaded, and clipped surfaces are not accidentally selected.
- Picking works after zoom/pan, resize, and on high-DPI displays.
- Both modes identify the same placement for an equivalent visible view.
- The inspector explains approximate or unsupported solids and labels all coordinate units.
- Selection survives a chunk reload when its placement ID is still present and clears predictably when filtered out.
- Metadata and highlighting remain responsive on a representative large scene.

## Verification and dependencies

Test overlapping boxes, repeated logical volumes, transparent shells, and phi-clipped geometry. Requires stable placement identity and sufficient scene metadata; coordinate binary-format changes with performance and portable export work.
