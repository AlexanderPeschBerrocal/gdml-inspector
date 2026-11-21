// Clean screenshot export (injected into both viewers).
//
// The scene is rendered again at the requested pixel size with the viewer's own
// draw path (so hiding, clipping, filters, wireframe and instancing all apply),
// copied to a 2D canvas, annotated there (scale bar, axis labels, caption,
// legend) and saved as a PNG. The panels and buttons of the page are never part
// of it. A transparent background is recovered exactly from two renders on black
// and white (source-over blending is linear in the background).
//
// A viewer supplies `hooks`:
//   canvas, gl                    its WebGL canvas and context
//   drawAt(w, h, {background, axes})  draw one frame with an w x h drawing buffer;
//                                 background is [r, g, b] (0..1) or null (the theme's)
//   restore()                     redraw normally (called once, after capturing)
//   camera()                      the camera state (camera.js)
//   mvp()                         the model-view-projection matrix of that frame
//   axes()                        [{text, point, color}] world-space axis ends
//   info()                        caption lines
//   materials()                   (async) [{name, color}] to list in the legend
//   dark()                        whether the current theme is dark
const SCREENSHOT_MAX_SIDE = 8192;

function screenshotLimit(gl) {
  const view = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
  return Math.min(SCREENSHOT_MAX_SIDE, view[0], view[1], gl.getParameter(gl.MAX_RENDERBUFFER_SIZE));
}

function drawPanel(ctx, x, y, w, h, fill) {
  ctx.fillStyle = fill;
  const r = Math.min(8, h / 4);
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); ctx.fill();
}

// Draws the annotations onto `ctx` (an w x h canvas that holds the render).
async function annotateScreenshot(ctx, w, h, hooks, options, light, materials) {
  const drawn = {lines: [], legend: [], scaleBar: null};
  const font = Math.max(12, Math.min(40, Math.round(h / 55)));
  const pad = Math.round(font * 0.7);
  const ink = light ? '#e6edf7' : '#16202e';
  const panel = light ? 'rgba(9,22,38,0.72)' : 'rgba(255,255,255,0.78)';
  ctx.textBaseline = 'top';
  ctx.font = `${font}px system-ui, sans-serif`;

  if (options.axes) {
    const mvp = hooks.mvp();
    ctx.font = `bold ${font}px system-ui, sans-serif`;
    for (const axis of hooks.axes()) {
      const p = axis.point;
      const tx = mvp[0] * p[0] + mvp[4] * p[1] + mvp[8] * p[2] + mvp[12];
      const ty = mvp[1] * p[0] + mvp[5] * p[1] + mvp[9] * p[2] + mvp[13];
      const tw = mvp[3] * p[0] + mvp[7] * p[1] + mvp[11] * p[2] + mvp[15];
      if (tw <= 0) continue;
      const x = (tx / tw * 0.5 + 0.5) * w, y = (-ty / tw * 0.5 + 0.5) * h;
      ctx.fillStyle = axis.color;
      ctx.strokeStyle = light ? 'rgba(0,0,0,0.6)' : 'rgba(255,255,255,0.8)';
      ctx.lineWidth = Math.max(2, font / 5);
      ctx.strokeText(axis.text, x - font / 3, y - font / 2);
      ctx.fillText(axis.text, x - font / 3, y - font / 2);
    }
    ctx.font = `${font}px system-ui, sans-serif`;
  }

  if (options.caption) {
    const lines = hooks.info();
    drawn.lines = lines;
    const widths = lines.map(t => ctx.measureText(t).width);
    const boxW = Math.max(...widths) + 2 * pad, boxH = lines.length * font * 1.35 + 2 * pad - font * 0.35;
    drawPanel(ctx, pad, pad, boxW, boxH, panel);
    ctx.fillStyle = ink;
    lines.forEach((t, i) => ctx.fillText(t, 2 * pad, 2 * pad + i * font * 1.35));
  }

  if (options.legend && materials.length) {
    const shown = materials.slice(0, 14);
    drawn.legend = materials.map(m => m.name);
    const extra = materials.length - shown.length;
    const rows = shown.length + (extra > 0 ? 1 : 0);
    const swatch = font;
    const labels = shown.map(m => m.name).concat(extra > 0 ? [`+ ${extra} more`] : []);
    const textW = Math.max(...labels.map(t => ctx.measureText(t).width));
    const boxW = swatch + pad + textW + 2 * pad, boxH = rows * font * 1.4 + 2 * pad - font * 0.4;
    const x0 = pad, y0 = h - pad - boxH;
    drawPanel(ctx, x0, y0, boxW, boxH, panel);
    shown.forEach((m, i) => {
      const y = y0 + pad + i * font * 1.4;
      ctx.fillStyle = m.color; ctx.fillRect(x0 + pad, y, swatch, swatch);
      ctx.strokeStyle = ink; ctx.lineWidth = 1; ctx.strokeRect(x0 + pad, y, swatch, swatch);
      ctx.fillStyle = ink; ctx.fillText(m.name, x0 + 2 * pad + swatch, y);
    });
    if (extra > 0) { ctx.fillStyle = ink; ctx.fillText(labels[labels.length - 1], x0 + 2 * pad + swatch, y0 + pad + shown.length * font * 1.4); }
  }

  if (options.scaleBar) {
    const bar = scaleBarFor(hooks.camera(), h, Math.max(110, h / 8));
    const camera = hooks.camera();
    const label = bar.label + (camera.projection === 'perspective' ? ' (at the target)' : '');
    drawn.scaleBar = {mm: bar.mm, px: bar.px, label};
    const tw = Math.max(ctx.measureText(label).width, bar.px);
    const boxW = tw + 2 * pad, boxH = font * 1.4 + 3 * pad;
    const x0 = w - pad - boxW, y0 = h - pad - boxH;
    drawPanel(ctx, x0, y0, boxW, boxH, panel);
    ctx.fillStyle = ink;
    ctx.fillRect(x0 + boxW - pad - bar.px, y0 + pad, bar.px, Math.max(3, font / 5));
    ctx.fillRect(x0 + boxW - pad - bar.px, y0 + pad - font / 4, 2, font / 2);
    ctx.fillRect(x0 + boxW - pad - 2, y0 + pad - font / 4, 2, font / 2);
    ctx.textAlign = 'right';
    ctx.fillText(label, x0 + boxW - pad, y0 + 2 * pad);
    ctx.textAlign = 'left';
  }
  return drawn;
}

// Renders and annotates; resolves to {canvas, width, height}. Always restores the viewer.
async function captureScreenshot(hooks, options) {
  const limit = screenshotLimit(hooks.gl);
  const width = Math.round(options.width), height = Math.round(options.height);
  if (!(width >= 16 && height >= 16 && width <= limit && height <= limit)) {
    throw new Error(`The size must be between 16 and ${limit} pixels on each side (this GPU's limit, at most ${SCREENSHOT_MAX_SIDE}).`);
  }
  const materials = options.legend ? await hooks.materials() : [];
  const out = document.createElement('canvas');
  out.width = width; out.height = height;
  const ctx = out.getContext('2d', {willReadFrequently: options.background === 'transparent'});
  const colours = {white: [1, 1, 1], black: [0, 0, 0]};
  const grab = background => {
    hooks.drawAt(width, height, {background, axes: options.axes});
    const copy = document.createElement('canvas');
    copy.width = width; copy.height = height;
    copy.getContext('2d').drawImage(hooks.canvas, 0, 0);
    return copy;
  };
  let light, drawn;
  try {
    if (options.background === 'transparent') {
      const onBlack = grab(colours.black).getContext('2d').getImageData(0, 0, width, height);
      const onWhite = grab(colours.white).getContext('2d').getImageData(0, 0, width, height);
      const result = ctx.createImageData(width, height);
      const b = onBlack.data, wh = onWhite.data, o = result.data;
      for (let i = 0; i < o.length; i += 4) {
        // pixel on black = colour * alpha; pixel on white = colour * alpha + (1 - alpha)
        const alpha = Math.max(0, Math.min(1, 1 - (wh[i] - b[i]) / 255 / 3 - (wh[i + 1] - b[i + 1]) / 255 / 3 - (wh[i + 2] - b[i + 2]) / 255 / 3));
        o[i + 3] = Math.round(alpha * 255);
        if (alpha > 0) { o[i] = Math.min(255, b[i] / alpha); o[i + 1] = Math.min(255, b[i + 1] / alpha); o[i + 2] = Math.min(255, b[i + 2] / alpha); }
      }
      ctx.putImageData(result, 0, 0);
      light = hooks.dark();
    } else {
      const background = colours[options.background] || null;
      ctx.drawImage(grab(background), 0, 0);
      light = options.background === 'black' ? true : options.background === 'white' ? false : hooks.dark();
    }
    drawn = await annotateScreenshot(ctx, width, height, hooks, options, light, materials);
  } finally {
    hooks.restore();
  }
  // `lines`, `legend` and `scaleBar` describe exactly what was drawn, for tests and scripts.
  return {canvas: out, width, height, ...drawn};
}

function downloadCanvas(canvas, filename) {
  return new Promise(resolve => canvas.toBlob(blob => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    resolve(blob);
  }, 'image/png'));
}

// Builds the "Screenshot" controls inside `root`.
function buildScreenshotPanel(root, hooks, stem) {
  const limit = screenshotLimit(hooks.gl);
  root.innerHTML = `
    <div class="row"><label>Size (px)</label>
      <input id="shotWidth" type="number" min="16" max="${limit}" value="${Math.min(1920, limit)}" style="width:74px">
      <span>×</span>
      <input id="shotHeight" type="number" min="16" max="${limit}" value="${Math.min(1080, limit)}" style="width:74px"></div>
    <div class="row"><label>Background</label>
      <select id="shotBackground"><option value="theme">Current theme</option><option value="white">White</option><option value="black">Black</option><option value="transparent">Transparent</option></select></div>
    <div class="row"><label><input id="shotScale" type="checkbox" checked> Scale bar</label>
      <label><input id="shotAxes" type="checkbox"> Axes</label></div>
    <div class="row"><label><input id="shotCaption" type="checkbox" checked> Caption</label>
      <label><input id="shotLegend" type="checkbox"> Material legend</label></div>
    <button id="shotSave" type="button">Save PNG</button>
    <div id="shotStatus" class="muted">The page's panels are not included. Largest side: ${limit} px.</div>`;
  const $ = id => root.querySelector('#' + id);
  async function save() {
    const status = $('shotStatus');
    status.className = 'muted'; status.textContent = 'Rendering…';
    try {
      const options = {
        width: +$('shotWidth').value, height: +$('shotHeight').value, background: $('shotBackground').value,
        scaleBar: $('shotScale').checked, axes: $('shotAxes').checked, caption: $('shotCaption').checked, legend: $('shotLegend').checked,
      };
      const shot = await captureScreenshot(hooks, options);
      window.lastScreenshot = shot; // for tests and scripts: {canvas, width, height}
      await downloadCanvas(shot.canvas, `${stem}-${shot.width}x${shot.height}.png`);
      status.textContent = `Saved ${shot.width} × ${shot.height} px.`;
    } catch (error) {
      status.className = 'muted error'; status.textContent = String(error.message || error);
    }
  }
  $('shotSave').addEventListener('click', save);
  return {save};
}
