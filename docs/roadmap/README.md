# Feature roadmap

The following GitHub issues track follow-up work. These are planned features;
the README's capability table describes what the initial extraction implements.
Each issue includes the current limitation, proposed behaviour, acceptance
criteria, and verification/dependency notes. The adjacent Markdown files retain
local copies of the issue descriptions as initially filed.

- [#1: Consolidate GDML parsing with ROOT Explorer and validate geometry fidelity](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/1) — [local description](01-gdml-fidelity.md)
- [#2: Add a searchable physical-volume hierarchy with isolate, hide, and focus actions](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/2) — [local description](02-hierarchy.md)
- [#3: Unify material, volume-name, path, and solid filters across viewer modes](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/3) — [local description](03-filters.md)
- [#4: Implement depth-correct object picking and a detailed placement inspector](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/4) — [local description](04-picking.md)
- [#5: Add Cartesian clipping planes and consistent phi cutaway controls](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/5) — [local description](05-clipping.md)
- [#6: Improve rendering quality, transparency, wireframe, and camera presentation](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/6) — [local description](06-rendering.md)
- [#7: Scale large geometries with instancing, bounded preprocessing, and robust lazy loading](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/7) — [local description](07-performance.md)
- [#8: Expose structured fidelity diagnostics, exportable warnings, and strict validation](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/8) — [local description](08-diagnostics.md)
- [#9: Compare two GDML geometries with semantic change reports and synchronised views](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/9) — [local description](09-geometry-diff.md)
- [#10: Make offline exports reproducible, shareable, and restorable with saved view state](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/10) — [local description](10-portable-exports.md)
- [#11: Export geometry to glTF/GLB for external rendering and presentation tools](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/11) — [local description](11-gltf.md)
- [#12: Expose a small geometry API and migrate ROOT Explorer to the shared implementation](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/12) — [local description](12-root-explorer.md)
- [#13: Support optional material-map and ray-budget overlays through an external tools interface](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/13) — [local description](13-material-overlays.md)

Start with parser fidelity (#1) and structured diagnostics (#8), then define stable
scene identities for hierarchy, picking, and sharing. Rendering and clipping can
progress alongside that work. Geometry comparison and external integrations
benefit from the resulting stable core. Material computation remains in DRIFT or
separate material tools; #13 concerns optional visual overlays.
