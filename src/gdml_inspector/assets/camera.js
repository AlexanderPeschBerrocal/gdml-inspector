// Shared camera maths (injected into both viewers): the matrices, the
// perspective/orthographic choice, the length scale bar and the fit rule.
//
// The camera orbits `viewCenter + pan` at distance `dist`, looking with `up`.
// A projection change never moves the eye: the orthographic view is sized so
// the plane through the target shows exactly what the perspective one does
// there, which keeps axis views, picking and the scale bar consistent.

const CAMERA_FOV_DEG = 45;

function mat4mul(a, b) {
  const o = new Float32Array(16);
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) for (let k = 0; k < 4; k++) o[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k];
  return o;
}
function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
}
function orthographic(halfHeight, aspect, near, far) {
  const halfWidth = halfHeight * aspect, nf = 1 / (near - far);
  return new Float32Array([1 / halfWidth, 0, 0, 0, 0, 1 / halfHeight, 0, 0, 0, 0, 2 * nf, 0, 0, 0, (far + near) * nf, 1]);
}
function lookAt(eye, target, up) {
  let zx = eye[0] - target[0], zy = eye[1] - target[1], zz = eye[2] - target[2];
  const zl = Math.hypot(zx, zy, zz); zx /= zl; zy /= zl; zz /= zl;
  let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
  const xl = Math.hypot(xx, xy, xz); xx /= xl; xy /= xl; xz /= xl;
  const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
  return new Float32Array([xx, yx, zx, 0, xy, yy, zy, 0, xz, yz, zz, 0, -(xx * eye[0] + xy * eye[1] + xz * eye[2]), -(yx * eye[0] + yy * eye[1] + yz * eye[2]), -(zx * eye[0] + zy * eye[1] + zz * eye[2]), 1]);
}

// camera: {yaw, pitch, dist, panX, panY, viewCenter, up, projection ('perspective' | 'orthographic')}
function cameraTarget(camera) {
  return [camera.viewCenter[0] + camera.panX, camera.viewCenter[1] + camera.panY, camera.viewCenter[2]];
}
function cameraEye(camera) {
  const [cx, cy, cz] = cameraTarget(camera);
  return [
    cx + camera.dist * Math.cos(camera.pitch) * Math.sin(camera.yaw),
    cy + camera.dist * Math.sin(camera.pitch),
    cz + camera.dist * Math.cos(camera.pitch) * Math.cos(camera.yaw),
  ];
}
function cameraMatrix(camera, aspect, near, far) {
  const fov = CAMERA_FOV_DEG * Math.PI / 180;
  const projection = camera.projection === 'orthographic'
    ? orthographic(camera.dist * Math.tan(fov / 2), aspect, near, far)
    : perspective(fov, aspect, near, far);
  return mat4mul(projection, lookAt(cameraEye(camera), cameraTarget(camera), camera.up));
}

// Millimetres per CSS pixel on the plane through the target (the only plane
// where a perspective view has a single scale; an orthographic view has it everywhere).
function millimetresPerPixel(camera, heightPx) {
  return 2 * camera.dist * Math.tan(CAMERA_FOV_DEG * Math.PI / 360) / heightPx;
}
// The 1-2-5 length (in mm) closest to `targetPx` pixels wide, and its label.
function scaleBarFor(camera, heightPx, targetPx = 110) {
  const perPixel = millimetresPerPixel(camera, heightPx);
  const wanted = perPixel * targetPx;
  const decade = Math.pow(10, Math.floor(Math.log10(wanted)));
  const length = [1, 2, 5, 10].map(m => m * decade).reduce((best, v) => (Math.abs(v - wanted) < Math.abs(best - wanted) ? v : best));
  return {mm: length, px: length / perPixel, label: formatLength(length)};
}
function formatLength(mm) {
  const trim = v => String(Number(v.toPrecision(3)));
  if (mm >= 1000) return `${trim(mm / 1000)} m`;
  if (mm >= 1) return `${trim(mm)} mm`;
  return `${trim(mm * 1000)} µm`;
}

// The camera target and distance that frame a box [x0, y0, z0, x1, y1, z1]:
// 1.6 times its diagonal (the sphere around it fits with margin), never closer
// than `minimum`. Used by fit-all, fit-selection and the hierarchy's Focus.
function fitBox(box, minimum) {
  const centre = [(box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2];
  const diagonal = Math.hypot(box[3] - box[0], box[4] - box[1], box[5] - box[2]);
  // A single point (no extent) gets a small fixed window rather than zero.
  return {centre, dist: Math.max(1.6 * (diagonal || minimum * 2.5), minimum)};
}
