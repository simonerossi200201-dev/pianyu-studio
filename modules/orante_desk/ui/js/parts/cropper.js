// 裁切编辑器：纯 canvas，无库。openCropper({tid,p,file,w,h,n_refs}) → {x,y,w,h,as_new}|null
window.openCropper = (opts) => {
  const t = window.t || ((k) => k);
  opts = opts || {};
  const imgW = Math.max(1, Number(opts.w) || 1);
  const imgH = Math.max(1, Number(opts.h) || 1);
  const nRefs = Number(opts.n_refs || 0) || 0;
  const MIN_SIDE = 256;
  const WARN_SIDE = 720;
  const RATIOS = [
    { id: '9:16', v: 9 / 16 },
    { id: '3:4', v: 3 / 4 },
    { id: '1:1', v: 1 },
    { id: 'free', v: 0 },
  ];
  const ANCHORS = [
    ['tl', 0, 0], ['t', 0.5, 0], ['tr', 1, 0],
    ['l', 0, 0.5], ['c', 0.5, 0.5], ['r', 1, 0.5],
    ['bl', 0, 1], ['b', 0.5, 1], ['br', 1, 1],
  ];
  const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

  return new Promise((resolve) => {
    let settled = false;
    let ratioId = '9:16';
    let aspect = 9 / 16;
    let sizePct = 100;
    let box = { x: 0, y: 0, w: imgW, h: imgH };
    let drag = null;
    let img = null;
    let raf = 0;

    const mask = document.createElement('div');
    mask.className = 'crop-mask';
    mask.innerHTML =
      '<div class="crop-card" tabindex="-1">' +
        '<div class="crop-stage"><canvas class="crop-main"></canvas></div>' +
        '<div class="crop-side">' +
          '<div class="crop-title"></div>' +
          '<div class="crop-prev-wrap"><canvas class="crop-prev"></canvas></div>' +
          '<div class="crop-sec"><div class="crop-lab">' + t('crop.ratio') + '</div>' +
            '<div class="crop-ratios"></div></div>' +
          '<div class="crop-sec"><div class="crop-lab">' + t('crop.anchor') + '</div>' +
            '<div class="crop-grid"></div></div>' +
          '<div class="crop-sec"><div class="crop-lab">' + t('crop.range') + '</div>' +
            '<input class="crop-range" type="range" min="30" max="100" value="100"></div>' +
          '<div class="crop-size"></div>' +
          '<div class="crop-hint"></div>' +
          '<div class="crop-ops">' +
            '<button type="button" class="btn ok crop-new">' + t('crop.save_new') + '</button>' +
            '<button type="button" class="btn crop-over">' + t('crop.overwrite') + '</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(mask);

    const card = mask.querySelector('.crop-card');
    const stage = mask.querySelector('.crop-stage');
    const canvas = mask.querySelector('.crop-main');
    const ctx = canvas.getContext('2d');
    const prev = mask.querySelector('.crop-prev');
    const pctx = prev.getContext('2d');
    mask.querySelector('.crop-title').textContent = t('crop.title', { p: String(opts.p || '') });
    const ratioBox = mask.querySelector('.crop-ratios');
    const gridBox = mask.querySelector('.crop-grid');
    const range = mask.querySelector('.crop-range');
    const sizeEl = mask.querySelector('.crop-size');
    const hintEl = mask.querySelector('.crop-hint');
    const btnNew = mask.querySelector('.crop-new');
    const btnOver = mask.querySelector('.crop-over');

    RATIOS.forEach((r) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'crop-chip' + (r.id === ratioId ? ' on' : '');
      b.dataset.id = r.id;
      b.textContent = t('crop.ratio.' + (r.id === 'free' ? 'free' : r.id.replace(':', '_')));
      b.addEventListener('click', () => setRatio(r.id));
      ratioBox.appendChild(b);
    });
    ANCHORS.forEach((a) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'crop-cell';
      b.dataset.k = a[0];
      b.textContent = t('crop.anchor.' + a[0]);
      b.addEventListener('click', () => snapAnchor(a[1], a[2]));
      gridBox.appendChild(b);
    });
    if (nRefs > 0) btnOver.title = t('crop.overwrite_hint', { n: String(nRefs) });

    function finish(val) {
      if (settled) return;
      settled = true;
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('resize', onResize);
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      if (mask.parentNode) mask.parentNode.removeChild(mask);
      resolve(val);
    }

    function ratioOf(id) {
      const r = RATIOS.find((x) => x.id === id);
      return r && r.v ? r.v : 0;
    }

    function maxBox(asp) {
      if (!asp) return { w: imgW, h: imgH };
      let w = imgW, h = w / asp;
      if (h > imgH) { h = imgH; w = h * asp; }
      return { w, h };
    }

    function clampBox(b, asp) {
      let x = b.x, y = b.y, w = b.w, h = b.h;
      if (w < 8) w = 8;
      if (h < 8) h = 8;
      if (asp) {
        if (w / h > asp) w = h * asp;
        else h = w / asp;
      }
      if (w > imgW) { w = imgW; if (asp) h = w / asp; }
      if (h > imgH) { h = imgH; if (asp) w = h * asp; }
      if (w > imgW) w = imgW;
      if (h > imgH) h = imgH;
      if (x < 0) x = 0;
      if (y < 0) y = 0;
      if (x + w > imgW) x = imgW - w;
      if (y + h > imgH) y = imgH - h;
      if (x < 0) x = 0;
      if (y < 0) y = 0;
      return { x, y, w, h };
    }

    function applyPct(pct, cx, cy) {
      sizePct = Math.max(30, Math.min(100, pct));
      const mx = maxBox(aspect || (box.w / box.h));
      const asp = aspect || (box.w / (box.h || 1));
      const w = mx.w * sizePct / 100;
      const h = asp ? w / asp : mx.h * sizePct / 100;
      const cX = cx == null ? box.x + box.w / 2 : cx;
      const cY = cy == null ? box.y + box.h / 2 : cy;
      box = clampBox({ x: cX - w / 2, y: cY - h / 2, w, h }, aspect || 0);
      range.value = String(Math.round(sizePct));
      paint();
    }

    function syncPctFromBox() {
      const mx = maxBox(aspect || (box.w / (box.h || 1)));
      sizePct = mx.w ? Math.max(30, Math.min(100, 100 * box.w / mx.w)) : 100;
      range.value = String(Math.round(sizePct));
    }

    function setRatio(id) {
      ratioId = id;
      aspect = ratioOf(id);
      ratioBox.querySelectorAll('.crop-chip').forEach((el) => {
        el.classList.toggle('on', el.dataset.id === id);
      });
      applyPct(100, imgW / 2, imgH / 2);
    }

    function snapAnchor(ax, ay) {
      const w = box.w, h = box.h;
      box = clampBox({ x: ax * (imgW - w), y: ay * (imgH - h), w, h }, aspect);
      paint();
    }

    function viewGeom() {
      const pad = 16;
      const vw = Math.max(40, stage.clientWidth);
      const vh = Math.max(40, stage.clientHeight);
      const innerW = Math.max(10, vw - pad * 2);
      const innerH = Math.max(10, vh - pad * 2);
      const scale = Math.min(innerW / imgW, innerH / imgH);
      const dw = imgW * scale, dh = imgH * scale;
      return {
        vw, vh, scale,
        ox: (vw - dw) / 2,
        oy: (vh - dh) / 2,
        dw, dh,
      };
    }

    function toView(g, x, y) {
      return { x: g.ox + x * g.scale, y: g.oy + y * g.scale };
    }

    function toOrig(g, vx, vy) {
      return { x: (vx - g.ox) / g.scale, y: (vy - g.oy) / g.scale };
    }

    function canvasPos(ev) {
      const r = canvas.getBoundingClientRect();
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    }

    function handleAt(g, vx, vy) {
      const hs = 8;
      const a = toView(g, box.x, box.y);
      const b = toView(g, box.x + box.w, box.y + box.h);
      const pts = {
        nw: [a.x, a.y], n: [(a.x + b.x) / 2, a.y], ne: [b.x, a.y],
        e: [b.x, (a.y + b.y) / 2], se: [b.x, b.y], s: [(a.x + b.x) / 2, b.y],
        sw: [a.x, b.y], w: [a.x, (a.y + b.y) / 2],
      };
      for (let i = 0; i < HANDLES.length; i++) {
        const k = HANDLES[i];
        const p = pts[k];
        if (Math.abs(vx - p[0]) <= hs && Math.abs(vy - p[1]) <= hs) return k;
      }
      if (vx >= a.x && vx <= b.x && vy >= a.y && vy <= b.y) return 'move';
      return '';
    }

    function cursorFor(h) {
      if (h === 'n' || h === 's') return 'ns-resize';
      if (h === 'e' || h === 'w') return 'ew-resize';
      if (h === 'nw' || h === 'se') return 'nwse-resize';
      if (h === 'ne' || h === 'sw') return 'nesw-resize';
      if (h === 'move') return 'move';
      return 'crosshair';
    }

    function resizeFrom(handle, mx, my, start) {
      const asp = aspect;
      if (handle === 'n' || handle === 's' || handle === 'e' || handle === 'w') {
        let x = start.x, y = start.y, w = start.w, h = start.h;
        if (handle === 'e') w = mx - start.x;
        if (handle === 'w') { w = start.x + start.w - mx; x = mx; }
        if (handle === 's') h = my - start.y;
        if (handle === 'n') { h = start.y + start.h - my; y = my; }
        if (w < 8) { if (handle === 'w') x = start.x + start.w - 8; w = 8; }
        if (h < 8) { if (handle === 'n') y = start.y + start.h - 8; h = 8; }
        if (asp) {
          if (handle === 'e' || handle === 'w') {
            h = w / asp;
            y = start.y + start.h / 2 - h / 2;
          } else {
            w = h * asp;
            x = start.x + start.w / 2 - w / 2;
          }
        }
        return clampBox({ x, y, w, h }, asp);
      }
      let fx, fy;
      if (handle === 'nw') { fx = start.x + start.w; fy = start.y + start.h; }
      else if (handle === 'ne') { fx = start.x; fy = start.y + start.h; }
      else if (handle === 'sw') { fx = start.x + start.w; fy = start.y; }
      else { fx = start.x; fy = start.y; }
      let w = mx - fx, h = my - fy;
      if (asp) {
        const sw = w >= 0 ? 1 : -1, sh = h >= 0 ? 1 : -1;
        const aw = Math.abs(w), ah = Math.abs(h);
        if (aw / asp >= ah) h = sh * aw / asp;
        else w = sw * ah * asp;
      }
      let x = w >= 0 ? fx : fx + w;
      let y = h >= 0 ? fy : fy + h;
      return clampBox({ x, y, w: Math.abs(w), h: Math.abs(h) }, asp);
    }

    function snap() {
      let x = Math.round(box.x), y = Math.round(box.y);
      let w = Math.round(box.w), h = Math.round(box.h);
      if (x < 0) x = 0;
      if (y < 0) y = 0;
      if (w < 1) w = 1;
      if (h < 1) h = 1;
      if (x + w > imgW) w = imgW - x;
      if (y + h > imgH) h = imgH - y;
      if (w < 1) w = 1;
      if (h < 1) h = 1;
      return { x, y, w, h };
    }

    function paint() {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(draw);
    }

    function draw() {
      raf = 0;
      const g = viewGeom();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(g.vw * dpr));
      canvas.height = Math.max(1, Math.round(g.vh * dpr));
      canvas.style.width = g.vw + 'px';
      canvas.style.height = g.vh + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, g.vw, g.vh);
      ctx.fillStyle = '#0d0c0b';
      ctx.fillRect(0, 0, g.vw, g.vh);
      if (img && img.complete) ctx.drawImage(img, g.ox, g.oy, g.dw, g.dh);
      const a = toView(g, box.x, box.y);
      const b = toView(g, box.x + box.w, box.y + box.h);
      const cx = a.x, cy = a.y, cw = b.x - a.x, ch = b.y - a.y;
      ctx.save();
      ctx.fillStyle = 'rgba(0,0,0,.55)';
      ctx.beginPath();
      ctx.rect(g.ox, g.oy, g.dw, g.dh);
      ctx.rect(cx, cy, cw, ch);
      ctx.fill('evenodd');
      ctx.restore();
      ctx.strokeStyle = '#E8C079';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(cx + 0.5, cy + 0.5, cw, ch);
      ctx.fillStyle = '#E8C079';
      const hs = 6;
      const pts = [
        [cx, cy], [(cx + cx + cw) / 2, cy], [cx + cw, cy],
        [cx + cw, cy + ch / 2], [cx + cw, cy + ch],
        [cx + cw / 2, cy + ch], [cx, cy + ch], [cx, cy + ch / 2],
      ];
      pts.forEach((p) => {
        ctx.fillRect(p[0] - hs / 2, p[1] - hs / 2, hs, hs);
      });
      drawPrev();
      const s = snap();
      sizeEl.textContent = t('crop.area', { w: String(s.w), h: String(s.h) });
      const short = Math.min(s.w, s.h);
      hintEl.className = 'crop-hint';
      if (short < MIN_SIDE) {
        hintEl.textContent = t('crop.err_tiny');
        hintEl.classList.add('bad');
        btnNew.disabled = true;
        btnOver.disabled = true;
      } else if (short < WARN_SIDE) {
        hintEl.textContent = t('crop.warn_small');
        hintEl.classList.add('warn');
        btnNew.disabled = false;
        btnOver.disabled = false;
      } else {
        hintEl.textContent = '';
        btnNew.disabled = false;
        btnOver.disabled = false;
      }
    }

    function drawPrev() {
      const pw = 90, ph = 160;
      const dpr = window.devicePixelRatio || 1;
      prev.width = Math.round(pw * dpr);
      prev.height = Math.round(ph * dpr);
      prev.style.width = pw + 'px';
      prev.style.height = ph + 'px';
      pctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      pctx.fillStyle = '#0d0c0b';
      pctx.fillRect(0, 0, pw, ph);
      if (!img || !img.complete) return;
      const s = snap();
      const scale = Math.min(pw / s.w, ph / s.h);
      const dw = s.w * scale, dh = s.h * scale;
      const ox = (pw - dw) / 2, oy = (ph - dh) / 2;
      pctx.drawImage(img, s.x, s.y, s.w, s.h, ox, oy, dw, dh);
    }

    function onDown(ev) {
      if (ev.button !== 0) return;
      const g = viewGeom();
      const p = canvasPos(ev);
      const h = handleAt(g, p.x, p.y);
      if (!h) return;
      ev.preventDefault();
      const o = toOrig(g, p.x, p.y);
      drag = { handle: h, mx: o.x, my: o.y, start: { x: box.x, y: box.y, w: box.w, h: box.h } };
    }

    function onMove(ev) {
      const g = viewGeom();
      const p = canvasPos(ev);
      if (!drag) {
        canvas.style.cursor = cursorFor(handleAt(g, p.x, p.y));
        return;
      }
      const o = toOrig(g, p.x, p.y);
      if (drag.handle === 'move') {
        const nx = drag.start.x + (o.x - drag.mx);
        const ny = drag.start.y + (o.y - drag.my);
        box = clampBox({ x: nx, y: ny, w: drag.start.w, h: drag.start.h }, 0);
      } else {
        box = resizeFrom(drag.handle, o.x, o.y, drag.start);
        syncPctFromBox();
      }
      paint();
    }

    function onUp() {
      drag = null;
    }

    function nudge(dx, dy) {
      box = clampBox({ x: box.x + dx, y: box.y + dy, w: box.w, h: box.h }, 0);
      paint();
    }

    function onKey(ev) {
      if (settled) return;
      if (ev.key === 'Escape') { ev.preventDefault(); finish(null); return; }
      if (ev.key === 'Enter') {
        ev.preventDefault();
        if (!btnNew.disabled) done(true);
        return;
      }
      const step = ev.shiftKey ? 10 : 1;
      if (ev.key === 'ArrowLeft') { ev.preventDefault(); nudge(-step, 0); }
      else if (ev.key === 'ArrowRight') { ev.preventDefault(); nudge(step, 0); }
      else if (ev.key === 'ArrowUp') { ev.preventDefault(); nudge(0, -step); }
      else if (ev.key === 'ArrowDown') { ev.preventDefault(); nudge(0, step); }
    }

    function onResize() { paint(); }

    function done(asNew) {
      const s = snap();
      if (Math.min(s.w, s.h) < MIN_SIDE) return;
      finish({ x: s.x, y: s.y, w: s.w, h: s.h, as_new: !!asNew });
    }

    mask.addEventListener('click', (ev) => { if (ev.target === mask) finish(null); });
    canvas.addEventListener('mousedown', onDown);
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', onResize);
    range.addEventListener('input', () => applyPct(Number(range.value)));
    btnNew.addEventListener('click', () => done(true));
    btnOver.addEventListener('click', () => done(false));

    img = new Image();
    img.onload = () => { applyPct(100, imgW / 2, imgH / 2); card.focus(); };
    img.onerror = () => {
      if (window.toast) toast(t('crop.load_fail'), '', 'bad');
      finish(null);
    };
    const rel = String(opts.file || '').replace(/\\/g, '/').split('/').filter(Boolean)
      .map(encodeURIComponent).join('/');
    img.src = window.srcWithV
      ? window.srcWithV(opts.file, opts.mtime || opts.sha1, opts.tid)
      : ('/media/' + encodeURIComponent(opts.tid) + '/' + rel
         + (opts.mtime || opts.sha1 ? ('?v=' + encodeURIComponent(String(opts.mtime || opts.sha1))) : ''));
  });
};
