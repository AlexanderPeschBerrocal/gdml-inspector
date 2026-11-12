// Shared depth-correct picking (injected into both viewers).
//
// An exact, depth-tested render-to-texture pass: every vertex carries its
// object id as a colour (`objectId + 1` in the low three bytes, so a cleared
// (0,0,0,0) pixel decodes to "no object"), the scene is drawn once per click
// into an offscreen framebuffer under the same clipping the visible pass
// uses, and the clicked pixel is read back. Ids are limited to 24 bits.
//
//   const picker = createPicker(gl, {visibility: true});
//   const id = picker.pick(devX, devY, {
//     width, height, mvp, clip: clipData,
//     draw: locs => { ...bind locs.pos / locs.idColor [/ locs.vis] and drawArrays },
//   });   // -> object id, or -1 for nothing
//
// `draw` supplies the geometry, so each viewer decides what is pickable
// (the standalone viewer: visible objects; the lazy viewer: loaded, visible
// chunks). The picker restores the viewport, framebuffer and program.
function createPicker(gl, {visibility = false} = {}) {
  function compile(type, source) {
    const s = gl.createShader(type); gl.shaderSource(s, source); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  const visDecl = visibility ? 'attribute float a_vis; varying float v_vis;' : '';
  const visSet = visibility ? 'v_vis = a_vis;' : '';
  const visTest = visibility ? 'if (v_vis < 0.5) discard;' : '';
  const vs = compile(gl.VERTEX_SHADER, `
attribute vec3 a_pos; attribute vec4 a_id_color; ${visDecl}
uniform mat4 u_mvp;
varying vec4 v_id_color; varying vec3 v_pos;
void main() {
  gl_Position = u_mvp * vec4(a_pos, 1.0);
  v_id_color = a_id_color;
  v_pos = a_pos;
  ${visSet}
}`);
  const fs = compile(gl.FRAGMENT_SHADER, CLIP_FRAGMENT_PRECISION + `
varying vec4 v_id_color; varying vec3 v_pos; ${visibility ? 'varying float v_vis;' : ''}
` + CLIP_GLSL + `
void main() {
  ${visTest}
  if (clipDiscard(v_pos)) discard;
  gl_FragColor = v_id_color;
}`);
  const program = gl.createProgram();
  gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  const locs = {
    pos: gl.getAttribLocation(program, 'a_pos'),
    idColor: gl.getAttribLocation(program, 'a_id_color'),
    vis: visibility ? gl.getAttribLocation(program, 'a_vis') : -1,
  };
  const uMvp = gl.getUniformLocation(program, 'u_mvp');
  const clipLoc = clipLocations(gl, program);

  let fbo = null, texture = null, depth = null, fboWidth = 0, fboHeight = 0;
  function ensureFramebuffer(width, height) {
    if (fbo && fboWidth === width && fboHeight === height) return;
    if (!fbo) { fbo = gl.createFramebuffer(); texture = gl.createTexture(); depth = gl.createRenderbuffer(); }
    fboWidth = width; fboHeight = height;
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindRenderbuffer(gl.RENDERBUFFER, depth);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, width, height);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, depth);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  function pick(devX, devY, {width, height, mvp, clip, draw}) {
    if (devX < 0 || devY < 0 || devX >= width || devY >= height) return -1;
    const previousProgram = gl.getParameter(gl.CURRENT_PROGRAM);
    ensureFramebuffer(width, height);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      return -1;
    }
    gl.viewport(0, 0, width, height);
    gl.disable(gl.BLEND);
    gl.enable(gl.DEPTH_TEST);
    gl.depthMask(true);
    gl.clearColor(0, 0, 0, 0);
    gl.clearDepth(1.0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(program);
    gl.uniformMatrix4fv(uMvp, false, mvp);
    uploadClip(gl, clipLoc, clip);
    draw(locs);
    const pixel = new Uint8Array(4);
    gl.readPixels(devX, devY, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, width, height);
    gl.useProgram(previousProgram);
    const encoded = pixel[0] + pixel[1] * 256 + pixel[2] * 65536;
    return encoded === 0 ? -1 : encoded - 1;
  }

  return {program, locs, pick};
}

// Bind a per-vertex id attribute: a buffer of normalised RGBA bytes
// (objectId + 1, little-endian) or, when the data has none (a chunk exported
// before per-object ids), the constant "no object".
function bindIdAttribute(gl, loc, buffer) {
  if (loc < 0) return;
  if (buffer) {
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 4, gl.UNSIGNED_BYTE, true, 0, 0);
  } else {
    gl.disableVertexAttribArray(loc);
    gl.vertexAttrib4f(loc, 0, 0, 0, 0);
  }
}
