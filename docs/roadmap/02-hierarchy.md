# Add a searchable physical-volume hierarchy with isolate, hide, and focus actions

## Problem and current behaviour

The single-file viewer exposes a flat rendered-object list indirectly through filters and picking. The lazy viewer presents heuristic subsystem/layer groups, which are useful for COCOA but are not the GDML hierarchy. Users need to inspect detector assemblies and repeated placements without knowing detector-specific naming conventions.

## Proposed experience

Display the actual world-to-placement tree, retaining logical-volume identity separately from each physical placement path. Expand/collapse branches, search by volume/placement name or full path, and show matching ancestors. Each node should support hide/show subtree, isolate subtree, and focus the camera on its bounds. Link selection between the tree, viewport, and metadata inspector.

Represent non-rendered containers and assemblies without forcing their meshes visible. Repeated logical volumes must remain distinct placements. Preserve original names; cleaned labels may be secondary display text. Keep the heuristic subsystem grouping available as an alternative organisation, with an obvious indication that it is inferred.

## Acceptance criteria

- A nested fixture with one logical volume placed twice produces two correct, independently selectable branches.
- Hide/isolate operations affect exactly the selected subtree and cooperate with material and clipping filters.
- Camera focus uses the selected placement's global bounds and has sensible handling for empty/container nodes.
- Large trees use virtualisation or incremental expansion rather than adding every row to the DOM.
- Lazy mode can reveal metadata for unloaded objects and fetch geometry only when needed.
- Search and node actions are keyboard accessible, and the UI distinguishes hidden, filtered, unloaded, and unsupported geometry.

## Verification and dependencies

Build on the shared validated scene graph and stable placement identifiers. Test tree actions with nested assemblies, repeated logical volumes, and an object excluded by another active filter. Define tri-state checkbox behaviour explicitly so parents do not misleadingly appear fully visible.
