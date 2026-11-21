// Weighted blended order-independent transparency (McGuire and Bavoil, 2013),
// shared by both viewers.
//
// Opaque surfaces are drawn into an offscreen colour + depth target. Translucent
// surfaces are then drawn twice against that depth (no depth writes): once
// accumulating `rgb * alpha * w` and `alpha * w` (additive blending), once
// accumulating the revealage `product(1 - alpha)` (WebGL1 has no multiple render
// targets, so two passes). A full-screen composite blends
//     average colour * (1 - revealage) + opaque colour * revealage.
// The result does not depend on the order surfaces are drawn in. The weight `w`
// favours nearer surfaces (see OIT_GLSL), so it is a plausible blend, not an
// exact layer-by-layer one; the offscreen targets have no multisampling.
//
// A viewer's fragment shader includes OIT_GLSL and sets `u_pass`:
//   0 normal drawing (used when this technique is off or unsupported)
//   1 opaque pass       2 accumulation pass       3 revealage pass
const OIT_GLSL = `
uniform float u_pass; uniform vec3 u_eye; uniform vec3 u_dir; uniform float u_oit_scale;
// Returns false when the fragment must be dropped in this pass; otherwise sets
// \`out_color\` to what the pass writes.
bool oitOutput(vec3 world, vec4 color, out vec4 out_color) {
  out_color = color;
  if (u_pass < 0.5) return true;
  bool opaque = color.a >= 0.999;
  if (u_pass < 1.5) { out_color = vec4(color.rgb, 1.0); return opaque; }
  if (opaque) return false;
  if (u_pass < 2.5) {
    float depth = max(dot(world - u_eye, u_dir) / u_oit_scale, 0.0);
    float w = color.a * clamp(1.0 / (1e-3 + 0.1 * depth * depth * depth * depth), 0.01, 1000.0);
    out_color = vec4(color.rgb * color.a * w, color.a * w);
    return true;
  }
  out_color = vec4(color.a);
  return true;
}`;

// The render-target pixel type that can be drawn to here, or null.
function oitFloatType(gl) {
  const candidates = [];
  const half = gl.getExtension('OES_texture_half_float');
  if (half && gl.getExtension('EXT_color_buffer_half_float')) candidates.push(half.HALF_FLOAT_OES);
  if (gl.getExtension('OES_texture_float') && gl.getExtension('WEBGL_color_buffer_float')) candidates.push(gl.FLOAT);
  for (const type of candidates) {
    const texture = gl.createTexture(), fbo = gl.createFramebuffer();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 4, 4, 0, gl.RGBA, type, null);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
    const complete = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.deleteFramebuffer(fbo); gl.deleteTexture(texture);
    if (complete) return type;
  }
  return null;
}

function createOit(gl) {
  const type = oitFloatType(gl);
  const oit = {supported: type !== null};
  if (!oit.supported) return oit;

  let width = 0, height = 0;
  let depth = null, scene = null, accum = null, reveal = null;
  function target(pixelType) {
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, width, height, 0, gl.RGBA, pixelType, null);
    for (const [name, value] of [[gl.TEXTURE_MIN_FILTER, gl.NEAREST], [gl.TEXTURE_MAG_FILTER, gl.NEAREST], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) {
      gl.texParameteri(gl.TEXTURE_2D, name, value);
    }
    const fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, depth);
    return {texture, fbo};
  }
  function release(t) { if (t) { gl.deleteFramebuffer(t.fbo); gl.deleteTexture(t.texture); } }
  function resize(w, h) {
    if (w === width && h === height && scene) return;
    release(scene); release(accum); release(reveal);
    if (depth) gl.deleteRenderbuffer(depth);
    width = w; height = h;
    depth = gl.createRenderbuffer();
    gl.bindRenderbuffer(gl.RENDERBUFFER, depth);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, w, h);
    scene = target(gl.UNSIGNED_BYTE); accum = target(type); reveal = target(type);
  }

  const compile = (kind, source) => {
    const s = gl.createShader(kind); gl.shaderSource(s, source); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  };
  const program = gl.createProgram();
  gl.attachShader(program, compile(gl.VERTEX_SHADER, 'attribute vec2 a_quad; varying vec2 v_uv; void main() { v_uv = a_quad * 0.5 + 0.5; gl_Position = vec4(a_quad, 0.0, 1.0); }'));
  gl.attachShader(program, compile(gl.FRAGMENT_SHADER, `precision highp float; varying vec2 v_uv;
uniform sampler2D u_scene; uniform sampler2D u_accum; uniform sampler2D u_reveal;
void main() {
  vec3 opaque = texture2D(u_scene, v_uv).rgb;
  vec4 sum = texture2D(u_accum, v_uv);
  float revealage = texture2D(u_reveal, v_uv).r;
  vec3 average = sum.rgb / max(sum.a, 1e-5);
  gl_FragColor = vec4(mix(average, opaque, revealage), 1.0);
}`));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  const quadLoc = gl.getAttribLocation(program, 'a_quad');
  const quad = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);

  // Pass 1: opaque surfaces into the colour + depth target.
  oit.begin = (w, h) => { resize(w, h); gl.bindFramebuffer(gl.FRAMEBUFFER, scene.fbo); gl.viewport(0, 0, w, h); };
  // Pass 2: translucent colour sums against the opaque depth.
  oit.beginAccumulate = () => {
    gl.bindFramebuffer(gl.FRAMEBUFFER, accum.fbo);
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST); gl.depthMask(false);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
  };
  // Pass 3: the product of (1 - alpha).
  oit.beginReveal = () => {
    gl.bindFramebuffer(gl.FRAMEBUFFER, reveal.fbo);
    gl.clearColor(1, 1, 1, 1); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ZERO, gl.ONE_MINUS_SRC_COLOR);
  };
  // Blend everything onto the canvas.
  oit.composite = () => {
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, width, height);
    gl.disable(gl.DEPTH_TEST); gl.disable(gl.BLEND); gl.depthMask(true);
    gl.useProgram(program);
    for (let i = 0; i < 8; i++) gl.disableVertexAttribArray(i); // only the quad's array may be enabled
    [[scene, 'u_scene'], [accum, 'u_accum'], [reveal, 'u_reveal']].forEach(([t, name], unit) => {
      gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t.texture);
      gl.uniform1i(gl.getUniformLocation(program, name), unit);
    });
    gl.activeTexture(gl.TEXTURE0);
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.enableVertexAttribArray(quadLoc); gl.vertexAttribPointer(quadLoc, 2, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    gl.disableVertexAttribArray(quadLoc);
  };
  return oit;
}
