// Shared "Picked Object" inspector (injected into both viewers), so the two
// show the identical panel from different data sources.
//
//   const inspector = buildInspector(document.getElementById('details'), {
//     emptyText: 'Click a point in the detector view.',
//     actions: [[{id: 'detailFocus', label: 'Focus', onClick: info => ...}]],
//   });
//   inspector.show(info);   inspector.clear();
//
// `info` (all optional but path): {path, name, semanticPath, volume,
// semanticVolume, material, solidType, params, bounds, approximated,
// localMatrix, globalMatrix, note}. Matrices are flat row-major 3x4 lists.

function inspectorSemanticPath(nodeByPath, path) {
  const segments = [];
  let p = path;
  while (p) {
    const node = nodeByPath[p];
    if (!node) break;
    segments.unshift(node.semanticName);
    p = node.parentPath;
  }
  return segments.join('/');
}

function inspectorMatrixRows(flat) {
  const rows = [];
  for (let r = 0; r < 3; r++) {
    rows.push(flat.slice(r * 4, r * 4 + 4).map(v => v.toFixed(3)).join('  '));
  }
  return rows.join('\n');
}
function inspectorParams(params) {
  const keys = Object.keys(params);
  if (!keys.length) return '(none -- Boolean solid; see Accuracy Notes)';
  return keys.map(k => `${k}: ${params[k].toFixed(3)}`).join('\n');
}
function inspectorBounds(bbox) {
  return `min: ${bbox.slice(0, 3).map(v => v.toFixed(3)).join(', ')}\n`
    + `max: ${bbox.slice(3, 6).map(v => v.toFixed(3)).join(', ')}`;
}

function buildInspector(root, {emptyText = 'Click a point in the detector view.', actions = []} = {}) {
  root.innerHTML = `
    <div id="detailsEmpty"></div>
    <div id="detailsContent" style="display:none">
      <div class="inspector-field"><span>Path</span><b id="detailPath"></b></div>
      <div class="inspector-field"><span>Semantic path</span><b id="detailSemanticPath"></b></div>
      <div class="inspector-field"><span>Volume</span><b id="detailVolume"></b></div>
      <div class="inspector-field"><span>Material</span><b id="detailMaterial"></b></div>
      <div class="inspector-field"><span>Solid type</span><b id="detailSolidType"></b></div>
      <div id="detailNote" class="warn" style="display:none"></div>
      <div id="detailApproximated" class="warn" style="display:none">This solid's geometry is approximated -- see Accuracy Notes below.</div>
      <div class="inspector-section">Declared parameters (mm / rad)</div>
      <div id="detailParams" class="mono"></div>
      <div class="inspector-section">Tessellated bounds (mm)</div>
      <div id="detailBounds" class="mono"></div>
      <div class="inspector-section">Local transform (mm)</div>
      <div id="detailLocalMatrix" class="mono"></div>
      <div class="inspector-section">Global transform (mm)</div>
      <div id="detailGlobalMatrix" class="mono"></div>
      <div class="small-actions">
        <button id="detailCopyPath" type="button">Copy path</button>
        <button id="detailCopyName" type="button">Copy name</button>
      </div>
    </div>`;
  const $ = id => root.querySelector('#' + id);
  $('detailsEmpty').textContent = emptyText;
  let current = null;
  $('detailCopyPath').onclick = () => {
    if (current) navigator.clipboard.writeText(current.path).catch(() => {});
  };
  $('detailCopyName').onclick = () => {
    if (current && current.name) navigator.clipboard.writeText(current.name).catch(() => {});
  };
  for (const group of actions) {
    const row = document.createElement('div');
    row.className = 'small-actions';
    for (const action of group) {
      const button = document.createElement('button');
      button.type = 'button';
      button.id = action.id;
      button.textContent = action.label;
      button.onclick = () => { if (current) action.onClick(current); };
      row.appendChild(button);
    }
    $('detailsContent').appendChild(row);
  }

  function clear() {
    current = null;
    $('detailsEmpty').style.display = '';
    $('detailsContent').style.display = 'none';
  }
  function show(info) {
    if (!info) { clear(); return; }
    current = info;
    $('detailsEmpty').style.display = 'none';
    $('detailsContent').style.display = '';
    $('detailPath').textContent = info.path;
    $('detailSemanticPath').textContent = info.semanticPath || '';
    $('detailVolume').textContent = info.volume === undefined ? '' : (info.semanticVolume || info.volume)
      + (info.semanticVolume && info.semanticVolume !== info.volume ? ` (${info.volume})` : '');
    $('detailMaterial').textContent = info.material;
    $('detailSolidType').textContent = info.solidType;
    $('detailParams').textContent = info.params;
    $('detailBounds').textContent = info.bounds;
    $('detailApproximated').style.display = info.approximated ? '' : 'none';
    $('detailNote').textContent = info.note || '';
    $('detailNote').style.display = info.note ? '' : 'none';
    $('detailLocalMatrix').textContent = info.localMatrix;
    $('detailGlobalMatrix').textContent = info.globalMatrix;
  }
  return {show, clear};
}

// Builds `info` from a hierarchy node plus, for a rendered placement, its
// object record and solid table entry. Shared so standalone and lazy format
// every field identically. `approximatedSolids` is a Set of solid refs.
function inspectorInfo(nodeByPath, node, object, solids, approximatedSolids, note) {
  const info = {
    path: node.path,
    name: node.name,
    semanticPath: inspectorSemanticPath(nodeByPath, node.path),
    volume: node.volume,
    semanticVolume: node.semanticVolume,
    localMatrix: node.localMatrix ? inspectorMatrixRows(node.localMatrix) : '(world root)',
    globalMatrix: node.globalMatrix ? inspectorMatrixRows(node.globalMatrix) : '--',
    note,
  };
  if (node.rendered && object) {
    const solidInfo = solids[object.solid] || {type: object.solidType, params: {}};
    info.material = object.material;
    info.solidType = object.solidType;
    info.params = inspectorParams(solidInfo.params);
    info.bounds = inspectorBounds(object.bbox);
    info.approximated = approximatedSolids.has(object.solid);
  } else {
    info.material = `(none -- ${node.isAssembly ? 'assembly' : 'container'}, not rendered)`;
    info.solidType = '--';
    info.params = '--';
    info.bounds = '--';
    info.approximated = false;
  }
  return info;
}
