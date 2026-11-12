// Saved view state, shared by both viewers (injected at build time like
// clipping.js and common.js).
//
// A view state is a small JSON document (a "sidecar" file or one embedded at
// export time with --view-state). It stores placement PATHS and layer names,
// never numeric ids, so it survives a re-export; entries that no longer exist
// are reported rather than silently dropped. A file is validated completely
// before anything is applied, so a bad file never leaves the view half-changed.

const VIEW_STATE_FORMAT = 'gdml-inspector-view';
const VIEW_STATE_VERSION = 1;
// Older versions would be upgraded here, one step at a time. None exist yet.
const VIEW_STATE_MIGRATIONS = {};

function viewIsNumber(v) { return typeof v === 'number' && Number.isFinite(v); }
function viewIsVector(v, n) { return Array.isArray(v) && v.length === n && v.every(viewIsNumber); }
function viewIsStringList(v) { return Array.isArray(v) && v.every(s => typeof s === 'string'); }

// Returns {ok, errors, warnings, state}. `state` is a sanitised copy holding
// only the known sections; unknown keys are dropped with a warning. A missing
// section simply means "not covered": applying it leaves that part alone.
function validateViewState(raw, bbox) {
  const errors = [], warnings = [];
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return {ok: false, errors: ['Not a view-state file: expected a JSON object.'], warnings, state: null};
  }
  if (raw.format !== VIEW_STATE_FORMAT) {
    return {ok: false, errors: [`Not a gdml-inspector view-state file (format ${JSON.stringify(raw.format)}, expected "${VIEW_STATE_FORMAT}").`], warnings, state: null};
  }
  let doc = raw;
  if (!Number.isInteger(doc.version) || doc.version < 1) {
    return {ok: false, errors: [`Invalid view-state version ${JSON.stringify(doc.version)}.`], warnings, state: null};
  }
  if (doc.version > VIEW_STATE_VERSION) {
    return {ok: false, errors: [`This view-state file is version ${doc.version}; this viewer reads up to ${VIEW_STATE_VERSION}. It was saved by a newer gdml-inspector: update gdml-inspector, or re-save the view with this version.`], warnings, state: null};
  }
  while (doc.version < VIEW_STATE_VERSION) {
    const migrate = VIEW_STATE_MIGRATIONS[doc.version];
    if (!migrate) {
      return {ok: false, errors: [`View-state version ${doc.version} cannot be migrated to ${VIEW_STATE_VERSION}: re-save the view with a current gdml-inspector.`], warnings, state: null};
    }
    doc = migrate(doc);
    warnings.push(`Migrated an older view-state file (version ${raw.version}) to version ${VIEW_STATE_VERSION}.`);
  }
  if (doc.viewer !== 'standalone' && doc.viewer !== 'lazy') {
    errors.push(`"viewer" must be "standalone" or "lazy", got ${JSON.stringify(doc.viewer)}.`);
  }
  const known = ['format', 'version', 'viewer', 'savedFor', 'camera', 'projection', 'display', 'clipping', 'filters', 'hierarchy', 'layers'];
  for (const key of Object.keys(doc)) if (!known.includes(key)) warnings.push(`Ignored unknown entry "${key}".`);

  const state = {format: VIEW_STATE_FORMAT, version: VIEW_STATE_VERSION, viewer: doc.viewer};
  const savedFor = doc.savedFor;
  state.savedFor = {
    schemaVersion: savedFor && Number.isInteger(savedFor.schemaVersion) ? savedFor.schemaVersion : null,
    inputSha256: savedFor && typeof savedFor.inputSha256 === 'string' ? savedFor.inputSha256 : null,
    sourceFile: savedFor && typeof savedFor.sourceFile === 'string' ? savedFor.sourceFile : null,
  };

  if (doc.camera !== undefined) {
    const c = doc.camera;
    if (!c || !viewIsNumber(c.yaw) || !viewIsNumber(c.pitch) || !viewIsNumber(c.dist) || !(c.dist > 0)
      || !viewIsVector(c.pan, 2) || !viewIsVector(c.up, 3) || (c.viewCenter !== undefined && !viewIsVector(c.viewCenter, 3))) {
      errors.push('"camera" needs numeric yaw, pitch, a positive dist, pan [x, y], up [x, y, z] (and optionally viewCenter [x, y, z]).');
    } else {
      state.camera = {yaw: c.yaw, pitch: c.pitch, dist: c.dist, pan: c.pan.slice(), up: c.up.slice()};
      if (c.viewCenter) state.camera.viewCenter = c.viewCenter.slice();
    }
  }
  if (doc.projection !== undefined) {
    const p = doc.projection;
    if (!p || p.type !== 'perspective' || !viewIsNumber(p.fovDeg)) {
      errors.push('"projection" must be {"type": "perspective", "fovDeg": number}; no other projection exists.');
    } else {
      if (p.fovDeg !== 45) warnings.push(`Projection field of view ${p.fovDeg} degrees differs from the viewer's fixed 45; the viewer keeps 45.`);
      state.projection = {type: 'perspective', fovDeg: p.fovDeg};
    }
  }
  if (doc.display !== undefined) {
    const d = doc.display;
    const bad = !d || (d.opacity !== undefined && !viewIsNumber(d.opacity))
      || ['wireframe', 'axes', 'releaseHidden'].some(k => d[k] !== undefined && typeof d[k] !== 'boolean')
      || (d.theme !== undefined && d.theme !== 'light' && d.theme !== 'dark');
    if (bad) errors.push('"display" has an invalid opacity (number), wireframe/axes/releaseHidden (booleans) or theme ("light"/"dark").');
    else state.display = Object.fromEntries(['opacity', 'wireframe', 'axes', 'releaseHidden', 'theme'].filter(k => d[k] !== undefined).map(k => [k, d[k]]));
  }
  if (doc.clipping !== undefined) {
    if (!doc.clipping || typeof doc.clipping !== 'object') errors.push('"clipping" must be an object.');
    else state.clipping = sanitizeClipState(doc.clipping, bbox);
  }
  if (doc.filters !== undefined) {
    const f = doc.filters;
    if (!f || !viewIsStringList(f.disabledMaterials || []) || (f.solidType !== undefined && typeof f.solidType !== 'string') || (f.search !== undefined && typeof f.search !== 'string')) {
      errors.push('"filters" needs disabledMaterials (list of names), solidType and search (strings).');
    } else state.filters = {disabledMaterials: (f.disabledMaterials || []).slice(), solidType: f.solidType || '', search: f.search || ''};
  }
  if (doc.hierarchy !== undefined) {
    const h = doc.hierarchy;
    const nullableString = v => v === null || v === undefined || typeof v === 'string';
    if (!h || !viewIsStringList(h.hiddenPaths || []) || !viewIsStringList(h.expandedPaths || []) || !nullableString(h.isolatedPath) || !nullableString(h.selectedPath) || (h.search !== undefined && typeof h.search !== 'string')) {
      errors.push('"hierarchy" needs hiddenPaths/expandedPaths (lists of placement paths), isolatedPath/selectedPath (path or null) and search (string).');
    } else {
      state.hierarchy = {hiddenPaths: (h.hiddenPaths || []).slice(), expandedPaths: (h.expandedPaths || []).slice(), isolatedPath: h.isolatedPath || null, selectedPath: h.selectedPath || null, search: h.search || ''};
    }
  }
  if (doc.layers !== undefined) {
    const l = doc.layers;
    if (!l || !Array.isArray(l.selected) || !l.selected.every(e => e && typeof e.subsystem === 'string' && typeof e.layer === 'string')) {
      errors.push('"layers" needs selected: a list of {"subsystem", "layer"}.');
    } else state.layers = {selected: l.selected.map(e => ({subsystem: e.subsystem, layer: e.layer}))};
  }
  return {ok: errors.length === 0, errors, warnings, state: errors.length === 0 ? state : null};
}

function viewStateEnvelope(viewer, sections, saved) {
  return {format: VIEW_STATE_FORMAT, version: VIEW_STATE_VERSION, viewer, savedFor: saved, ...sections};
}

// The human-readable outcome of applying a view.
function formatViewReport(report) {
  const lines = [];
  if (report.errors && report.errors.length) {
    lines.push(`Could not load ${report.source}; nothing was changed.`, ...report.errors);
    return lines.join('\n');
  }
  lines.push(`Applied ${report.source}: ${report.applied.join(', ') || 'nothing'}.`);
  if (report.ignored.length) lines.push(`Not applicable in this viewer: ${report.ignored.join(', ')}.`);
  if (report.unknown.length) {
    const shown = report.unknown.slice(0, 8).map(u => `${u.kind} ${JSON.stringify(u.value)}`);
    lines.push(`${report.unknown.length} entr${report.unknown.length === 1 ? 'y' : 'ies'} no longer exist${report.unknown.length === 1 ? 's' : ''} in this geometry and were skipped: ${shown.join('; ')}${report.unknown.length > 8 ? '; ...' : ''}.`);
  }
  for (const w of report.warnings) lines.push(w);
  return lines.join('\n');
}

// Builds Save/Load controls. `capture()` returns the viewer's sections;
// `apply(state, report)` applies a validated state (may be async), pushing
// section names to report.applied, section names it cannot honour to
// report.ignored and {kind, value} to report.unknown. Installs
// window.saveViewState()/loadViewState(raw, label) and window.lastViewReport.
function buildViewStatePanel(root, {viewer, saved, bbox, capture, apply}) {
  root.innerHTML = `
    <div class="clip-actions">
      <button type="button" id="viewSave">Save view</button>
      <button type="button" id="viewLoad">Load view</button>
    </div>
    <input type="file" id="viewFile" accept=".json,application/json" hidden>
    <div class="clip-note" id="viewMessage" role="status">Saves camera, filters, selection, clipping and theme to a <code>.view.json</code> file.</div>`;
  const message = document.getElementById('viewMessage');

  function snapshot() { return viewStateEnvelope(viewer, capture(), saved); }

  async function load(raw, label) {
    const report = {source: label, applied: [], ignored: [], unknown: [], warnings: [], errors: []};
    const checked = validateViewState(raw, bbox);
    report.warnings.push(...checked.warnings);
    if (!checked.ok) {
      report.errors = checked.errors;
    } else {
      const state = checked.state;
      if (state.viewer !== viewer) report.warnings.push(`This view was saved from the ${state.viewer} viewer; only the settings the ${viewer} viewer shares are applied.`);
      if (state.savedFor.inputSha256 && saved.inputSha256 && state.savedFor.inputSha256 !== saved.inputSha256) {
        report.warnings.push(`This view was saved for different geometry (input sha256 ${state.savedFor.inputSha256.slice(0, 12)}..., this file is ${saved.inputSha256.slice(0, 12)}...): some entries may be stale.`);
      }
      await apply(state, report);
    }
    window.lastViewReport = report;
    message.textContent = formatViewReport(report);
    message.style.whiteSpace = 'pre-wrap';
    return report;
  }

  document.getElementById('viewSave').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(snapshot(), null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = (saved.sourceFile || 'view').replace(/\.[^.]+$/, '') + '.view.json';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  });
  const fileInput = document.getElementById('viewFile');
  document.getElementById('viewLoad').addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files[0];
    fileInput.value = '';
    if (!file) return;
    let raw;
    try {
      raw = JSON.parse(await file.text());
    } catch (error) {
      const report = {source: file.name, applied: [], ignored: [], unknown: [], warnings: [], errors: [`Not valid JSON: ${error.message}`]};
      window.lastViewReport = report;
      message.textContent = formatViewReport(report);
      return;
    }
    await load(raw, file.name);
  });

  window.saveViewState = snapshot;
  window.loadViewState = load;
  return {snapshot, load};
}
