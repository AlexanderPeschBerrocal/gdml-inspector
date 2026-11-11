// Shared clipping/cutaway definition for the standalone and lazy viewers.
// One source of truth, injected into both templates at build time, so the
// state schema, the kept-region semantics, the GLSL and the control panel
// cannot drift apart.
//
// Semantics (all positions in mm, the normalised geometry unit; global axes):
//   phi wedge  azimuth atan2(y, x) in [start, start + width], counter-
//              clockwise about +z, bounds inclusive, wrapping across +-180
//              degrees. "remove" drops the wedge, "keep" keeps only it.
//   x/y/z-min  keeps coordinate >= value;  x/y/z-max keeps <= value
//              (a min + max pair on one axis is a slab).
//   custom     keeps n.p >= offset, n normalised (zero normal = ignored).
//   The kept region is the INTERSECTION of the phi condition and every
//   enabled plane. Clipped surfaces are open cuts: nothing is capped.

const CLIP_PLANE_IDS = ['x-min', 'x-max', 'y-min', 'y-max', 'z-min', 'z-max'];
const CLIP_MAX_PLANES = 7;
const CLIP_UNIT = 'mm';

// Positions are hundreds of mm; mediump would make plane edges wobble on
// GPUs where it is a 16-bit float.
const CLIP_FRAGMENT_PRECISION = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
`;

const CLIP_GLSL = `
uniform float u_phi_enabled; uniform float u_phi_start; uniform float u_phi_width; uniform float u_phi_keep;
uniform float u_plane_count; uniform vec4 u_planes[7];
bool clipDiscard(vec3 p) {
  for (int i = 0; i < 7; i++) {
    if (float(i) >= u_plane_count) break;
    if (dot(u_planes[i].xyz, p) + u_planes[i].w < 0.0) return true;
  }
  if (u_phi_enabled > 0.5) {
    const float two_pi = 6.28318530718;
    float rel = mod(atan(p.y, p.x) - u_phi_start + two_pi, two_pi);
    bool inside = rel <= u_phi_width;
    if (u_phi_keep > 0.5 ? !inside : inside) return true;
  }
  return false;
}
`;

function clipCentre(bbox) {
  return bbox ? [(bbox[0] + bbox[3]) / 2, (bbox[1] + bbox[4]) / 2, (bbox[2] + bbox[5]) / 2] : [0, 0, 0];
}
function clipExtent(bbox) {
  return bbox ? [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]].map(v => v || 1) : [1, 1, 1];
}

function defaultClipState(bbox) {
  const c = clipCentre(bbox);
  const axisIndex = {x: 0, y: 1, z: 2};
  return {
    version: 1,
    unit: CLIP_UNIT,
    phi: {enabled: false, startDeg: -45, widthDeg: 90, mode: 'remove'},
    planes: [
      ...CLIP_PLANE_IDS.map(id => ({id, enabled: false, value: c[axisIndex[id[0]]]})),
      {id: 'custom', enabled: false, normal: [0, 0, 1], offset: c[2]},
    ],
  };
}

function clipFinite(value, fallback) {
  const n = typeof value === 'string' ? parseFloat(value) : value;
  return typeof n === 'number' && Number.isFinite(n) ? n : fallback;
}

// Accepts anything (e.g. a saved view's JSON) and returns a valid state:
// unknown keys dropped, bad values replaced by defaults, angles clamped.
function sanitizeClipState(input, bbox) {
  const base = defaultClipState(bbox);
  if (!input || typeof input !== 'object') return base;
  const phi = input.phi && typeof input.phi === 'object' ? input.phi : {};
  base.phi = {
    enabled: phi.enabled === true,
    startDeg: Math.min(180, Math.max(-180, clipFinite(phi.startDeg, base.phi.startDeg))),
    widthDeg: Math.min(359, Math.max(1, clipFinite(phi.widthDeg, base.phi.widthDeg))),
    mode: phi.mode === 'keep' ? 'keep' : 'remove',
  };
  const given = Array.isArray(input.planes) ? input.planes : [];
  for (const plane of base.planes) {
    const source = given.find(p => p && p.id === plane.id);
    if (!source) continue;
    plane.enabled = source.enabled === true;
    if (plane.id === 'custom') {
      const normal = Array.isArray(source.normal) ? source.normal.map(v => clipFinite(v, NaN)) : [];
      if (normal.length === 3 && normal.every(Number.isFinite)) plane.normal = normal;
      plane.offset = clipFinite(source.offset, plane.offset);
    } else {
      plane.value = clipFinite(source.value, plane.value);
    }
  }
  return base;
}

// The packed form both the GPU and the JS reference implementation use.
function clipUniformData(state) {
  const planes = [];
  for (const plane of state.planes) {
    if (!plane.enabled) continue;
    if (plane.id === 'custom') {
      const length = Math.hypot(...plane.normal);
      if (length < 1e-12) continue;
      const [nx, ny, nz] = plane.normal.map(v => v / length);
      planes.push(nx, ny, nz, -plane.offset);
    } else {
      const sign = plane.id.endsWith('min') ? 1 : -1;
      const n = [0, 0, 0];
      n[{x: 0, y: 1, z: 2}[plane.id[0]]] = sign;
      planes.push(n[0], n[1], n[2], -sign * plane.value);
    }
  }
  return {
    phi: {
      enabled: state.phi.enabled,
      start: state.phi.startDeg * Math.PI / 180,
      width: state.phi.widthDeg * Math.PI / 180,
      keep: state.phi.mode === 'keep',
    },
    planeCount: planes.length / 4,
    planes,
  };
}

// Pure-JS reference of clipDiscard(), for tests and any non-GPU consumer.
function clipKeeps(state, point) {
  const data = clipUniformData(state);
  for (let i = 0; i < data.planeCount; i++) {
    const o = i * 4;
    if (data.planes[o] * point[0] + data.planes[o + 1] * point[1] + data.planes[o + 2] * point[2] + data.planes[o + 3] < 0) return false;
  }
  if (data.phi.enabled) {
    const twoPi = 2 * Math.PI;
    const rel = (((Math.atan2(point[1], point[0]) - data.phi.start + twoPi) % twoPi) + twoPi) % twoPi;
    const inside = rel <= data.phi.width;
    if (data.phi.keep ? !inside : inside) return false;
  }
  return true;
}

function clipLocations(gl, program) {
  return {
    phiEnabled: gl.getUniformLocation(program, 'u_phi_enabled'),
    phiStart: gl.getUniformLocation(program, 'u_phi_start'),
    phiWidth: gl.getUniformLocation(program, 'u_phi_width'),
    phiKeep: gl.getUniformLocation(program, 'u_phi_keep'),
    planeCount: gl.getUniformLocation(program, 'u_plane_count'),
    planes: gl.getUniformLocation(program, 'u_planes'),
  };
}

// `data` null disables clipping (used for the coordinate axes). The target
// program must already be in use.
function uploadClip(gl, locations, data) {
  gl.uniform1f(locations.phiEnabled, data && data.phi.enabled ? 1 : 0);
  gl.uniform1f(locations.phiStart, data ? data.phi.start : 0);
  gl.uniform1f(locations.phiWidth, data ? data.phi.width : 0);
  gl.uniform1f(locations.phiKeep, data && data.phi.keep ? 1 : 0);
  gl.uniform1f(locations.planeCount, data ? data.planeCount : 0);
  const padded = new Float32Array(CLIP_MAX_PLANES * 4);
  if (data) padded.set(data.planes);
  gl.uniform4fv(locations.planes, padded);
}

const CLIP_PANEL_CSS = `
.clip-row { display: flex; align-items: center; gap: 6px; margin: 4px 0; font-size: 12px; }
.clip-row > label { display: flex; align-items: center; gap: 4px; flex: 0 0 74px; }
.clip-row input[type=range] { flex: 1; min-width: 0; margin: 0; }
.clip-row input[type=number] { width: 74px; flex: none; margin: 0; }
.clip-row select { flex: 1; margin: 0; }
.clip-unit { flex: none; color: var(--muted, #9cafc6); width: 22px; }
.clip-sub { margin: 8px 0 2px; font-size: 11px; color: var(--muted, #9cafc6); text-transform: uppercase; letter-spacing: .04em; }
.clip-actions { display: flex; flex-wrap: wrap; gap: 5px; margin: 6px 0; }
.clip-actions button { flex: 1 1 45%; font-size: 11px; padding: 5px 4px; margin: 0; }
.clip-note { font-size: 11px; line-height: 1.35; color: var(--muted, #9cafc6); margin: 6px 0; }
`;

// Builds the same controls in both viewers. The markup is constant (no
// GDML-derived strings). `onChange(data)` receives the packed uniform data
// after every edit. Returns a controller and installs the public
// getClipState()/setClipState() used for saved views.
function buildClipPanel(root, {bbox, onChange}) {
  const style = document.createElement('style');
  style.textContent = CLIP_PANEL_CSS;
  document.head.appendChild(style);

  const centre = clipCentre(bbox);
  const extent = clipExtent(bbox);
  const axisIndex = {x: 0, y: 1, z: 2};
  let state = defaultClipState(bbox);
  const rangeFor = axis => {
    const i = axisIndex[axis];
    const lo = (bbox ? bbox[i] : -1) - 0.1 * extent[i];
    const hi = (bbox ? bbox[i + 3] : 1) + 0.1 * extent[i];
    return {lo, hi, step: (hi - lo) / 1000};
  };
  const diagonal = Math.hypot(...extent);

  const planeRow = id => {
    const axis = id[0];
    const sign = id.endsWith('min') ? '≥' : '≤';
    const r = rangeFor(axis);
    return `<div class="clip-row">
      <label><input type="checkbox" id="clipPlane-${id}-enabled"> ${axis} ${sign}</label>
      <input type="range" id="clipPlane-${id}-slider" min="${r.lo}" max="${r.hi}" step="${r.step}">
      <input type="number" id="clipPlane-${id}-number" step="any"><span class="clip-unit">${CLIP_UNIT}</span></div>`;
  };
  root.innerHTML = `
    <div class="clip-sub">Azimuthal wedge (phi, about global +z)</div>
    <div class="clip-row"><label><input type="checkbox" id="clipPhiEnabled"> Enable</label>
      <select id="clipPhiMode"><option value="remove">Remove wedge</option><option value="keep">Keep wedge only</option></select></div>
    <div class="clip-row"><label>Start</label>
      <input type="range" id="clipPhiStart" min="-180" max="180" step="1">
      <input type="number" id="clipPhiStartNumber" step="any"><span class="clip-unit">°</span></div>
    <div class="clip-row"><label>Width</label>
      <input type="range" id="clipPhiWidth" min="1" max="359" step="1">
      <input type="number" id="clipPhiWidthNumber" step="any"><span class="clip-unit">°</span></div>
    <div class="clip-sub">Clipping planes (global coordinates, ${CLIP_UNIT})</div>
    ${CLIP_PLANE_IDS.map(planeRow).join('')}
    <div class="clip-sub">Arbitrary plane: keeps n·p ≥ offset</div>
    <div class="clip-row"><label><input type="checkbox" id="clipPlane-custom-enabled"> n</label>
      <input type="number" id="clipCustomNx" step="any" title="normal x">
      <input type="number" id="clipCustomNy" step="any" title="normal y">
      <input type="number" id="clipCustomNz" step="any" title="normal z"></div>
    <div class="clip-row"><label>offset</label>
      <input type="range" id="clipCustomOffsetSlider" min="${-diagonal}" max="${diagonal}" step="${diagonal / 1000}">
      <input type="number" id="clipCustomOffset" step="any"><span class="clip-unit">${CLIP_UNIT}</span></div>
    <div class="clip-actions">
      <button type="button" id="clipPresetTransverse" title="Keep z ≥ bounding-box centre">Transverse cut</button>
      <button type="button" id="clipPresetLongitudinal" title="Keep x ≥ bounding-box centre">Longitudinal cut</button>
      <button type="button" id="clipPresetSlab" title="Keep z within ±5% of the extent around the centre">Slab</button>
      <button type="button" id="clipReset">Reset clipping</button></div>
    <div class="clip-note" id="clipNote">The kept region is the wedge condition AND every enabled plane. Cut faces are open, not capped: this shows surfaces only and is not a quantitative solid section.</div>`;

  const $ = id => document.getElementById(id);
  const planeOf = id => state.planes.find(p => p.id === id);
  const fmt = v => String(Math.round(v * 1e6) / 1e6);

  function refresh() {
    $('clipPhiEnabled').checked = state.phi.enabled;
    $('clipPhiMode').value = state.phi.mode;
    $('clipPhiStart').value = state.phi.startDeg;
    $('clipPhiStartNumber').value = fmt(state.phi.startDeg);
    $('clipPhiWidth').value = state.phi.widthDeg;
    $('clipPhiWidthNumber').value = fmt(state.phi.widthDeg);
    for (const id of CLIP_PLANE_IDS) {
      const plane = planeOf(id);
      $(`clipPlane-${id}-enabled`).checked = plane.enabled;
      $(`clipPlane-${id}-slider`).value = plane.value;
      $(`clipPlane-${id}-number`).value = fmt(plane.value);
    }
    const custom = planeOf('custom');
    $('clipPlane-custom-enabled').checked = custom.enabled;
    $('clipCustomNx').value = fmt(custom.normal[0]);
    $('clipCustomNy').value = fmt(custom.normal[1]);
    $('clipCustomNz').value = fmt(custom.normal[2]);
    $('clipCustomOffsetSlider').value = custom.offset;
    $('clipCustomOffset').value = fmt(custom.offset);
  }
  function commit() {
    onChange(clipUniformData(state));
  }
  function replace(next) {
    state = sanitizeClipState(next, bbox);
    refresh();
    commit();
  }

  // A slider and its numeric box edit the same value; either one updates
  // the other, and the state keeps the exact number typed even when it lies
  // outside the slider's range.
  function link(slider, number, read, write) {
    slider.addEventListener('input', () => { write(parseFloat(slider.value)); number.value = fmt(read()); commit(); });
    number.addEventListener('input', () => {
      const v = parseFloat(number.value);
      if (!Number.isFinite(v)) return;
      write(v);
      slider.value = v;
      commit();
    });
  }
  $('clipPhiEnabled').addEventListener('change', e => { state.phi.enabled = e.target.checked; commit(); });
  $('clipPhiMode').addEventListener('change', e => { state.phi.mode = e.target.value === 'keep' ? 'keep' : 'remove'; commit(); });
  link($('clipPhiStart'), $('clipPhiStartNumber'), () => state.phi.startDeg, v => { state.phi.startDeg = Math.min(180, Math.max(-180, v)); });
  link($('clipPhiWidth'), $('clipPhiWidthNumber'), () => state.phi.widthDeg, v => { state.phi.widthDeg = Math.min(359, Math.max(1, v)); });
  for (const id of CLIP_PLANE_IDS) {
    $(`clipPlane-${id}-enabled`).addEventListener('change', e => { planeOf(id).enabled = e.target.checked; commit(); });
    link($(`clipPlane-${id}-slider`), $(`clipPlane-${id}-number`), () => planeOf(id).value, v => { planeOf(id).value = v; });
  }
  $('clipPlane-custom-enabled').addEventListener('change', e => { planeOf('custom').enabled = e.target.checked; commit(); });
  ['Nx', 'Ny', 'Nz'].forEach((name, i) => $(`clipCustom${name}`).addEventListener('input', e => {
    const v = parseFloat(e.target.value);
    if (!Number.isFinite(v)) return;
    planeOf('custom').normal[i] = v;
    commit();
  }));
  link($('clipCustomOffsetSlider'), $('clipCustomOffset'), () => planeOf('custom').offset, v => { planeOf('custom').offset = v; });

  const planesOff = () => state.planes.forEach(p => { p.enabled = false; });
  const preset = apply => () => { planesOff(); apply(); refresh(); commit(); };
  $('clipPresetTransverse').addEventListener('click', preset(() => {
    Object.assign(planeOf('z-min'), {enabled: true, value: centre[2]});
  }));
  $('clipPresetLongitudinal').addEventListener('click', preset(() => {
    Object.assign(planeOf('x-min'), {enabled: true, value: centre[0]});
  }));
  $('clipPresetSlab').addEventListener('click', preset(() => {
    Object.assign(planeOf('z-min'), {enabled: true, value: centre[2] - 0.05 * extent[2]});
    Object.assign(planeOf('z-max'), {enabled: true, value: centre[2] + 0.05 * extent[2]});
  }));
  $('clipReset').addEventListener('click', () => replace(defaultClipState(bbox)));

  window.getClipState = () => JSON.parse(JSON.stringify(state));
  window.setClipState = next => replace(next);
  refresh();
  commit();
  return {getState: window.getClipState, setState: window.setClipState};
}
