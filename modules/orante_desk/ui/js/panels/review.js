// 节点 ⑤ 收片：视频墙 + 体检 + 重生。快捷键与 Orante1 主界面一致。
// 进度条：只叠在原生 <video> 上，不引播放器库。监听器只挂当前悬停的那一张。
window.ReviewScrubber = (() => {
  const DEFAULT_FPS = 24;
  const FRAME_SHOW_MS = 1200;
  const FADE_IN_MS = 120;
  const FADE_OUT_MS = 200;

  const state = {
    stage: null,
    video: null,
    bar: null,
    fps: DEFAULT_FPS,
    hovering: false,
    dragging: false,
    wasPlaying: false,
    pointerId: null,
    pendingT: null,
    raf: 0,
    frameTimer: 0,
    lastClientX: 0,
    hoverBound: null,
    moveOn: false,
  };

  function pad2(n) {
    return String(Math.max(0, Math.floor(Number(n) || 0))).padStart(2, '0');
  }

  function fpsOf(seg) {
    const f = Number(seg && seg.fps);
    return f > 0 ? f : DEFAULT_FPS;
  }

  function fmtClock(t) {
    t = Math.max(0, Number(t) || 0);
    const s = Math.floor(t + 1e-6);
    return Math.floor(s / 60) + ':' + pad2(s % 60);
  }

  function fmtTip(t, fps) {
    fps = fps > 0 ? fps : DEFAULT_FPS;
    const fpsi = Math.max(1, Math.round(fps));
    t = Math.max(0, Number(t) || 0);
    const total = Math.floor(t * fpsi + 1e-6);
    const mm = Math.floor(total / (fpsi * 60));
    const ss = Math.floor(total / fpsi) % 60;
    const ff = total % fpsi;
    return pad2(mm) + ':' + pad2(ss) + '.' + pad2(ff);
  }

  function isTyping(el) {
    if (!el) return false;
    const tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || !!el.isContentEditable;
  }

  function fillEl(bar) {
    return bar && (bar.querySelector('.bar-fill') || bar.querySelector('i'));
  }

  function setFill(bar, ratio) {
    if (!bar) return;
    const p = Math.max(0, Math.min(1, Number(ratio) || 0)) * 100;
    const el = fillEl(bar);
    if (el) el.style.width = p + '%';
    const knob = bar.querySelector('.bar-knob');
    if (knob) knob.style.left = p + '%';
  }

  function syncFromVideo(video, bar, fps) {
    if (!video || !bar) return;
    const d = Number(video.duration);
    const t = Number(video.currentTime) || 0;
    const ratio = (Number.isFinite(d) && d > 0) ? (t / d) : 0;
    setFill(bar, ratio);
    const clock = bar.querySelector('.bar-clock') ||
      (video.parentElement && video.parentElement.querySelector('.bar-clock'));
    if (clock) {
      clock.textContent = fmtClock(t) + ' / ' + fmtClock(Number.isFinite(d) ? d : 0);
    }
    const tip = bar.querySelector('.bar-tip');
    if (tip) tip.textContent = fmtTip(t, fps);
  }

  function ratioAt(bar, clientX) {
    const r = bar.getBoundingClientRect();
    const w = r.width || 1;
    let x = (clientX - r.left) / w;
    if (x < 0) x = 0;
    if (x > 1) x = 1;
    return x;
  }

  function timeFromEvent(ev, bar, video) {
    const d = Number(video && video.duration);
    if (!Number.isFinite(d) || d <= 0) return 0;
    return ratioAt(bar, ev.clientX) * d;
  }

  function placeTip(bar, clientX, t, fps) {
    const tip = bar && bar.querySelector('.bar-tip');
    if (!tip) return;
    tip.textContent = fmtTip(t, fps);
    const r = bar.getBoundingClientRect();
    const tw = tip.offsetWidth || 72;
    const w = r.width || tw;
    let x = clientX - r.left;
    x = Math.max(tw / 2, Math.min(w - tw / 2, x));
    tip.style.left = x + 'px';
  }

  function setVisible(stage, on) {
    if (!stage) return;
    const bar = stage.querySelector('.bar');
    const shade = stage.querySelector('.bar-shade');
    const clock = stage.querySelector('.bar-clock');
    if (on) {
      stage.classList.add('bar-on');
      if (bar) {
        bar.style.opacity = '1';
        bar.style.transition = 'opacity ' + FADE_IN_MS + 'ms';
      }
      if (shade) {
        shade.style.opacity = '1';
        shade.style.transition = 'opacity ' + FADE_IN_MS + 'ms';
      }
      if (clock) clock.style.opacity = '1';
    } else {
      stage.classList.remove('bar-on');
      if (bar) {
        bar.style.opacity = '0';
        bar.style.transition = 'opacity ' + FADE_OUT_MS + 'ms';
      }
      if (shade) {
        shade.style.opacity = '0';
        shade.style.transition = 'opacity ' + FADE_OUT_MS + 'ms';
      }
      if (clock) clock.style.opacity = '0';
    }
  }

  function stopAll(ev) {
    if (ev && ev.stopPropagation) ev.stopPropagation();
  }

  function applyTime(t) {
    const v = state.video;
    const bar = state.bar;
    if (!v) return;
    const d = Number(v.duration);
    if (Number.isFinite(d) && d > 0) {
      if (t < 0) t = 0;
      if (t > d) t = d;
    }
    try { v.currentTime = t; } catch (e) { /* 元数据没到就略过 */ }
    if (bar) {
      syncFromVideo(v, bar, state.fps);
      if (state.lastClientX != null) placeTip(bar, state.lastClientX, t, state.fps);
    }
  }

  function flushSeek() {
    if (state.raf) {
      const caf = (typeof cancelAnimationFrame === 'function') ? cancelAnimationFrame : function () {};
      try { caf(state.raf); } catch (e) { /* 测试环境可能没有 */ }
      state.raf = 0;
    }
    if (state.pendingT == null) return;
    const t = state.pendingT;
    state.pendingT = null;
    applyTime(t);
  }

  function queueSeek(t) {
    state.pendingT = t;
    if (state.raf) return;
    const raf = (typeof requestAnimationFrame === 'function')
      ? requestAnimationFrame
      : function (fn) { return setTimeout(fn, 16); };
    state.raf = raf(function () {
      state.raf = 0;
      if (state.pendingT == null) return;
      const t2 = state.pendingT;
      state.pendingT = null;
      applyTime(t2);
    });
  }

  function onBarMove(ev) {
    stopAll(ev);
    const bar = state.bar;
    const video = state.video;
    if (!bar || !video) return;
    state.lastClientX = ev.clientX;
    const t = timeFromEvent(ev, bar, video);
    placeTip(bar, ev.clientX, t, state.fps);
    if (!state.dragging) return;
    queueSeek(t);
  }

  function onBarUp(ev) {
    if (ev && state.pointerId != null && ev.pointerId != null && ev.pointerId !== state.pointerId) return;
    stopAll(ev);
    const bar = state.bar;
    const video = state.video;
    const stage = state.stage;
    const resume = state.wasPlaying;
    if (bar && state.pointerId != null && bar.releasePointerCapture) {
      try { bar.releasePointerCapture(state.pointerId); } catch (e) { /* 已经松了 */ }
    }
    if (bar) {
      bar.removeEventListener('pointerup', onBarUp);
      bar.removeEventListener('pointercancel', onBarUp);
      bar.classList.remove('dragging');
    }
    flushSeek();
    if (video && bar) syncFromVideo(video, bar, state.fps);
    state.dragging = false;
    state.wasPlaying = false;
    state.pointerId = null;
    if (stage) stage.classList.remove('is-scrub');
    if (resume && video && video.play) video.play();
    if (!state.hovering && stage) setVisible(stage, false);
  }

  function onBarDown(ev) {
    stopAll(ev);
    if (ev && ev.preventDefault) ev.preventDefault();
    const bar = ev.currentTarget;
    const stage = bar && bar.closest ? bar.closest('.stage') : (state.stage);
    const video = stage && stage.querySelector('video');
    if (!bar || !video) return;
    const d = Number(video.duration);
    if (!Number.isFinite(d) || d <= 0) return;
    state.stage = stage;
    state.video = video;
    state.bar = bar;
    state.fps = Number((stage && stage.dataset && stage.dataset.fps) || state.fps) || DEFAULT_FPS;
    state.dragging = true;
    state.wasPlaying = !video.paused;
    state.pointerId = ev.pointerId;
    state.lastClientX = ev.clientX;
    if (state.wasPlaying && video.pause) video.pause();
    bar.classList.add('dragging');
    if (stage) {
      stage.classList.add('is-scrub');
      setVisible(stage, true);
    }
    if (bar.setPointerCapture && ev.pointerId != null) {
      try { bar.setPointerCapture(ev.pointerId); } catch (e) { /* jsdom 可能没有 */ }
    }
    bar.addEventListener('pointerup', onBarUp);
    bar.addEventListener('pointercancel', onBarUp);
    applyTime(timeFromEvent(ev, bar, video));
  }

  function onBarClick(ev) {
    stopAll(ev);
    if (ev && ev.preventDefault) ev.preventDefault();
  }

  function bindBar(stage) {
    const bar = stage && stage.querySelector('.bar');
    if (!bar || state.moveOn && state.hoverBound === stage) return;
    if (state.hoverBound && state.hoverBound !== stage && !state.dragging) unbindBar(state.hoverBound);
    state.hoverBound = stage;
    state.stage = stage;
    state.bar = bar;
    state.video = stage.querySelector('video');
    state.fps = Number((stage.dataset && stage.dataset.fps) || DEFAULT_FPS) || DEFAULT_FPS;
    if (!state.moveOn) {
      bar.addEventListener('pointerdown', onBarDown);
      bar.addEventListener('pointermove', onBarMove);
      bar.addEventListener('click', onBarClick);
      bar.addEventListener('pointerup', stopAll);
      bar.addEventListener('pointercancel', stopAll);
      state.moveOn = true;
    }
  }

  function unbindBar(stage) {
    if (state.dragging) return;
    const bar = (stage && stage.querySelector('.bar')) || state.bar;
    if (bar && state.moveOn) {
      bar.removeEventListener('pointerdown', onBarDown);
      bar.removeEventListener('pointermove', onBarMove);
      bar.removeEventListener('click', onBarClick);
      bar.removeEventListener('pointerup', stopAll);
      bar.removeEventListener('pointercancel', stopAll);
    }
    state.moveOn = false;
    if (state.hoverBound === stage) state.hoverBound = null;
  }

  function onEnter(ev) {
    const stage = ev.currentTarget;
    if (!stage) return;
    // 拖着当前卡划到别的卡上：别解绑、别误挂另一张
    if (state.dragging && state.stage && state.stage !== stage) return;
    state.hovering = true;
    bindBar(stage);
    setVisible(stage, true);
  }

  function onLeave(ev) {
    const stage = ev.currentTarget;
    if (!stage) return;
    if (state.dragging && state.stage && state.stage !== stage) return;
    state.hovering = false;
    if (state.dragging && state.stage === stage) {
      setVisible(stage, true);
      return;
    }
    unbindBar(stage);
    setVisible(stage, false);
  }

  function hideIfIdle(stage) {
    if (!stage) return;
    if (state.dragging || state.hovering) return;
    setVisible(stage, false);
    stage.classList.remove('is-frame');
  }

  function pulseFrame(stage) {
    if (!stage) return;
    stage.classList.add('is-frame');
    setVisible(stage, true);
    if (state.frameTimer) clearTimeout(state.frameTimer);
    state.frameTimer = setTimeout(function () {
      stage.classList.remove('is-frame');
      hideIfIdle(stage);
    }, FRAME_SHOW_MS);
  }

  function step(video, dir, opts) {
    if (!video) return;
    const fps = (opts && opts.fps > 0) ? opts.fps : DEFAULT_FPS;
    const shift = !!(opts && opts.shift);
    const delta = shift ? 1 : (1 / fps);
    if (!video.paused && video.pause) video.pause();
    const dur = Number(video.duration);
    let t = (Number(video.currentTime) || 0) + dir * delta;
    if (!Number.isFinite(t)) return;
    if (t < 0) t = 0;
    if (Number.isFinite(dur) && dur > 0 && t > dur) t = dur;
    try { video.currentTime = t; } catch (e) { return; }
    const stage = video.closest ? video.closest('.stage') : (video.parentElement);
    const bar = stage && stage.querySelector('.bar');
    if (bar) {
      syncFromVideo(video, bar, fps);
      const r = bar.getBoundingClientRect();
      const x = r.left + (r.width || 0) * ((Number.isFinite(dur) && dur > 0) ? (t / dur) : 0);
      placeTip(bar, x, t, fps);
    }
    if (stage) pulseFrame(stage);
  }

  function handleKey(ev, ctx) {
    if (!ev) return false;
    if (isTyping(ev.target) || (ctx && isTyping(ctx.active))) return false;
    const code = ev.code || '';
    const key = ev.key || '';
    let dir = 0;
    if (code === 'Comma' || key === ',' || key === '<') dir = -1;
    else if (code === 'Period' || key === '.' || key === '>') dir = 1;
    else return false;
    const video = ctx && ctx.video;
    if (!video) return false;
    step(video, dir, { shift: !!ev.shiftKey, fps: (ctx && ctx.fps) || DEFAULT_FPS });
    if (ev.preventDefault) ev.preventDefault();
    return true;
  }

  function mount(stage, opts) {
    if (!stage) return;
    if (opts && opts.fps) stage.dataset.fps = String(opts.fps);
    const bar = stage.querySelector('.bar');
    const shade = stage.querySelector('.bar-shade');
    const clock = stage.querySelector('.bar-clock');
    if (bar) bar.style.opacity = '0';
    if (shade) shade.style.opacity = '0';
    if (clock) clock.style.opacity = '0';
    stage.addEventListener('pointerenter', onEnter);
    stage.addEventListener('pointerleave', onLeave);
  }

  function isDragging(video) {
    return !!(state.dragging && (!video || state.video === video));
  }

  function dispose() {
    if (state.dragging) onBarUp({ pointerId: state.pointerId });
    if (state.hoverBound) unbindBar(state.hoverBound);
    if (state.frameTimer) clearTimeout(state.frameTimer);
    state.stage = null;
    state.video = null;
    state.bar = null;
    state.hovering = false;
    state.dragging = false;
    state.wasPlaying = false;
    state.pendingT = null;
    state.raf = 0;
    state.hoverBound = null;
    state.moveOn = false;
  }

  return {
    DEFAULT_FPS, FRAME_SHOW_MS, FADE_IN_MS, FADE_OUT_MS,
    fpsOf, fmtClock, fmtTip, isTyping, setFill, syncFromVideo,
    mount, onEnter, onLeave, handleKey, step, isDragging, dispose,
    bindBar, unbindBar,
  };
})();

window.ReviewCelebrate = (() => {
  const CELEBRATE_MS = 4800;
  const PARTICLE_CAP = 1100;
  const REDUCE_HOLD_MS = 1200;
  const FRAME_AVG_N = 12;
  const FRAME_SLOW_MS = 26;
  const CONFETTI_AT = 200;
  const RIBBON_AT = 600;

  let running = null;

  function prefersReduce() {
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) {
      return false;
    }
  }

  function confettiColors() {
    const cols = [];
    const pc = (typeof window !== 'undefined' && typeof window.pcolor === 'function')
      ? window.pcolor
      : function () { return '#8FB06A'; };
    for (let i = 1; i <= 16; i++) {
      cols.push(pc('p' + String(i).padStart(2, '0')));
    }
    cols.push('#D9A34F', '#E8C079', '#8FB06A', '#C9D98A', '#F2E6C8');
    return cols;
  }

  function hslColor(h, s, l) {
    return 'hsl(' + h + ' ' + s + '% ' + l + '%)';
  }

  function darken(color, amt) {
    const m = String(color).match(/hsl\(\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%\s*\)/i);
    if (m) return hslColor(m[1], m[2], Math.max(0, Number(m[3]) - amt));
    const hx = String(color).match(/^#([0-9a-f]{6})$/i);
    if (hx) {
      const n = parseInt(hx[1], 16);
      const cut = amt * 2.2;
      const r = Math.max(0, ((n >> 16) & 255) - cut);
      const g = Math.max(0, ((n >> 8) & 255) - cut);
      const b = Math.max(0, (n & 255) - cut);
      return 'rgb(' + (r | 0) + ',' + (g | 0) + ',' + (b | 0) + ')';
    }
    return color;
  }

  function fireworkColors(hue) {
    const j = function () { return hue + (Math.random() * 40 - 20); };
    return [
      hslColor(j(), 92, 72),
      hslColor(j(), 88, 58),
      hslColor(j(), 80, 46),
      hslColor(j(), 70, 78),
      '#ffffff',
    ];
  }

  function irand(a, b) {
    return a + Math.floor(Math.random() * (b - a + 1));
  }

  function formatBatchSub(ids) {
    ids = ids || [];
    if (ids.length <= 5) return ids.join(' ');
    return ids.slice(0, 3).join(' ') + ' … 共 ' + ids.length + ' 个';
  }

  function fillHost(host, title, sub) {
    if (!host) return;
    const ttl = host.querySelector('.rv-celebrate-ttl');
    const subEl = host.querySelector('.rv-celebrate-sub');
    if (ttl) ttl.textContent = title || '';
    if (subEl) {
      subEl.textContent = sub || '';
      subEl.style.display = sub ? '' : 'none';
    }
  }

  function makeHost() {
    const el = document.createElement('div');
    el.className = 'rv-celebrate';
    el.innerHTML =
      '<div class="rv-celebrate-inner">' +
        '<div class="rv-celebrate-rings"><i class="rv-celebrate-ring"></i><i class="rv-celebrate-ring delay"></i></div>' +
        '<div class="rv-celebrate-mark">✓</div>' +
        '<div class="rv-celebrate-ttl"></div>' +
        '<div class="rv-celebrate-sub"></div>' +
      '</div>';
    return el;
  }

  function stopCelebrate() {
    const r = running;
    running = null;
    if (!r) return;
    if (r.rafId && r.caf) {
      try { r.caf(r.rafId); } catch (e) { /* 测试环境可能没有 */ }
      r.rafId = 0;
    }
    if (r.ctrl) r.ctrl.cancelled = true;
    if (r.onResize && typeof window !== 'undefined' && window.removeEventListener) {
      window.removeEventListener('resize', r.onResize);
    }
    if (r.host && r.onClick) {
      try { r.host.removeEventListener('click', r.onClick); } catch (e) { /* 已摘 */ }
    }
    if (r.canvas && r.canvas.parentNode) r.canvas.parentNode.removeChild(r.canvas);
    document.querySelectorAll('canvas.rv-confetti').forEach(function (c) {
      if (c.parentNode) c.parentNode.removeChild(c);
    });
    if (r.createdHost && r.host && r.host.parentNode) r.host.parentNode.removeChild(r.host);
    if (r.timer) {
      try { clearTimeout(r.timer); } catch (e) { /* ignore */ }
    }
    if (typeof r.onDone === 'function') {
      try { r.onDone(); } catch (e) { /* ignore */ }
    }
  }

  function burstConfetti(canvasEl, opts) {
    opts = opts || {};
    if (!canvasEl || typeof canvasEl.getContext !== 'function') return null;
    const ctx = canvasEl.getContext('2d');
    if (!ctx) return null;
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    let W = (typeof window !== 'undefined' && window.innerWidth) || canvasEl.clientWidth || 800;
    let H = (typeof window !== 'undefined' && window.innerHeight) || canvasEl.clientHeight || 600;
    function sizeCanvas() {
      canvasEl.width = Math.max(1, Math.floor(W * dpr));
      canvasEl.height = Math.max(1, Math.floor(H * dpr));
      canvasEl.style.width = W + 'px';
      canvasEl.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    sizeCanvas();

    const colors = confettiColors();
    const particles = [];
    const fwCount = opts.fwCount != null ? opts.fwCount : irand(6, 8);
    const burstN = opts.fwBurst != null ? opts.fwBurst : irand(70, 110);
    const confettiPer = opts.confettiPer != null ? opts.confettiPer : 110;
    const ribbonCount = opts.ribbonCount != null ? opts.ribbonCount : irand(22, 30);
    const doubleN = Math.min(2, fwCount);

    // 粒子预算：先按计划算，超 1100 按 纸屑 → 长彩带 → 烟花 砍。
    let planFw = fwCount * burstN;
    let planConf = 4 * confettiPer;
    let planRib = ribbonCount;
    const cap = opts.cap != null ? opts.cap : PARTICLE_CAP;
    let over = (planFw + planConf + planRib + fwCount) - cap;
    if (over > 0) {
      const cutC = Math.min(planConf, over);
      planConf -= cutC; over -= cutC;
    }
    if (over > 0) {
      const cutR = Math.min(planRib, over);
      planRib -= cutR; over -= cutR;
    }
    if (over > 0) planFw = Math.max(0, planFw - over);
    const burstUse = fwCount > 0 ? Math.max(8, Math.floor(planFw / fwCount)) : 0;
    const confettiUse = Math.max(0, Math.floor(planConf / 4));
    const ribbonUse = planRib;

    const fwPlan = [];
    let launchT = 0;
    for (let i = 0; i < fwCount; i++) {
      fwPlan.push({
        t: launchT,
        x: 40 + Math.random() * Math.max(40, W - 80),
        peak: H * (0.30 + Math.random() * 0.35),
        hue: Math.floor(Math.random() * 360),
        double: i < doubleN,
        n: burstUse,
        launched: false,
      });
      launchT += 120 + Math.random() * 140;
    }

    let confettiSpawned = false;
    let ribbonsSpawned = false;
    let stopFireworks = false;
    let downgraded = false;
    const dts = [];
    let lastNow = null;
    let rafId = 0;
    let cancelled = false;
    let done = false;
    let spawned = { rockets: 0, sparks: 0, confetti: 0, ribbons: 0, secondary: 0 };

    const nowFn = opts.now || ((typeof performance !== 'undefined' && performance.now)
      ? function () { return performance.now(); }
      : function () { return Date.now(); });
    const raf = opts.raf || ((typeof requestAnimationFrame === 'function')
      ? requestAnimationFrame
      : function (fn) { return setTimeout(function () { fn(nowFn()); }, 16); });
    const caf = opts.caf || ((typeof cancelAnimationFrame === 'function')
      ? cancelAnimationFrame
      : function (id) { clearTimeout(id); });

    function liveCount() { return particles.length; }

    function canSpawn(n) {
      n = n || 1;
      return particles.length + n <= cap;
    }

    function spawnRocket(plan) {
      if (stopFireworks || !canSpawn(1)) return;
      const pal = fireworkColors(plan.hue);
      particles.push({
        kind: 'rocket',
        x: plan.x, y: H + 8,
        vx: (Math.random() - 0.5) * 0.6,
        vy: -(8.5 + Math.random() * 3.5),
        peak: plan.peak,
        pal: pal,
        color: pal[0],
        trail: [],
        trailN: 4 + Math.floor(Math.random() * 3),
        double: plan.double,
        n: plan.n,
        g: 0.04,
      });
      spawned.rockets += 1;
    }

    function explode(rocket) {
      const n = rocket.n || burstUse;
      const pal = rocket.pal || fireworkColors(0);
      for (let i = 0; i < n; i++) {
        if (!canSpawn(1)) break;
        const ang = (Math.PI * 2 * i) / n + (Math.random() - 0.5) * 0.18;
        const sp = 4 + Math.random() * 7;
        particles.push({
          kind: 'spark',
          x: rocket.x, y: rocket.y,
          vx: Math.cos(ang) * sp,
          vy: Math.sin(ang) * sp,
          g: 0.12 + Math.random() * 0.08,
          damp: 0.985,
          color: pal[i % pal.length],
          r: 1.4 + Math.random() * 1.8,
          life: 0,
          fade: 900 + Math.random() * 700,
          double: rocket.double,
          popped: false,
        });
        spawned.sparks += 1;
      }
    }

    function spawnSecondary(p) {
      const n = irand(5, 8);
      for (let i = 0; i < n; i++) {
        if (!canSpawn(1)) break;
        const ang = (Math.PI * 2 * i) / n + (Math.random() - 0.5) * 0.4;
        const sp = 2 + Math.random() * 3.5;
        particles.push({
          kind: 'spark',
          x: p.x, y: p.y,
          vx: Math.cos(ang) * sp,
          vy: Math.sin(ang) * sp,
          g: 0.14,
          damp: 0.985,
          color: p.color,
          r: 0.8 + Math.random() * 1.1,
          life: 0,
          fade: 500 + Math.random() * 400,
          double: false,
          popped: true,
        });
        spawned.secondary += 1;
      }
    }

    function spawnCannon(ox, oy, dir) {
      const n = confettiUse;
      for (let i = 0; i < n; i++) {
        if (!canSpawn(1)) break;
        const speed = 8 + Math.random() * 14;
        const spread = Math.random() * 0.85;
        let ang;
        if (dir === 'bl') ang = -Math.PI * 0.78 + spread;
        else if (dir === 'br') ang = -Math.PI * 0.22 - spread;
        else if (dir === 'tl') ang = Math.PI * 0.28 + spread * 0.5;
        else ang = Math.PI * 0.72 - spread * 0.5;
        const shape = Math.random();
        particles.push({
          kind: 'confetti',
          x: ox + (Math.random() - 0.5) * 18,
          y: oy + (Math.random() - 0.5) * 10,
          vx: Math.cos(ang) * speed,
          vy: Math.sin(ang) * speed,
          g: 0.26 + Math.random() * 0.14,
          rot: Math.random() * Math.PI * 2,
          vr: (Math.random() - 0.5) * 0.32,
          w: 5 + Math.random() * 9,
          h: 3 + Math.random() * 6,
          shape: shape < 0.34 ? 'round' : (shape < 0.68 ? 'rect' : 'arc'),
          color: colors[(i + (dir === 'bl' || dir === 'tl' ? 0 : 8)) % colors.length],
          damp: 0.984 + Math.random() * 0.01,
        });
        spawned.confetti += 1;
      }
    }

    function spawnRibbons() {
      for (let i = 0; i < ribbonUse; i++) {
        if (!canSpawn(1)) break;
        const col = colors[i % colors.length];
        particles.push({
          kind: 'ribbon',
          x: 20 + Math.random() * Math.max(20, W - 40),
          y: -20 - Math.random() * 80,
          vx: (Math.random() - 0.5) * 0.025,
          vy: (H + 80) / 5000,
          g: 0,
          rot: Math.random() * Math.PI * 2,
          vr: (Math.random() - 0.5) * 0.02,
          w: 6 + Math.random() * 6,
          len: 40 + Math.random() * 50,
          segs: 8 + Math.floor(Math.random() * 5),
          f: 0.004 + Math.random() * 0.004,
          a: 8 + Math.random() * 10,
          color: col,
          stroke: darken(col, 18),
          phase: Math.random() * Math.PI * 2,
        });
        spawned.ribbons += 1;
      }
    }

    function offscreen(p) {
      return p.y > H + 60 || p.x < -80 || p.x > W + 80;
    }

    function downgrade() {
      if (downgraded) return;
      downgraded = true;
      stopFireworks = true;
      const keep = [];
      for (let i = 0; i < particles.length; i++) {
        if (Math.random() >= 0.4) keep.push(particles[i]);
      }
      particles.length = 0;
      for (let i = 0; i < keep.length; i++) particles.push(keep[i]);
      console.info('[celebrate] 降档：前 ' + FRAME_AVG_N + ' 帧偏慢，粒子砍到 60%，停发烟花');
    }

    function drawRocket(p) {
      const trail = p.trail || [];
      for (let i = 0; i < trail.length; i++) {
        const q = trail[i];
        ctx.beginPath();
        ctx.fillStyle = p.color;
        ctx.globalAlpha = (i + 1) / (trail.length + 1) * 0.55;
        ctx.arc(q.x, q.y, 2.2, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.beginPath();
      ctx.globalAlpha = 1;
      ctx.fillStyle = '#fff';
      ctx.arc(p.x, p.y, 3.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.fillStyle = p.color;
      ctx.arc(p.x, p.y, 2.1, 0, Math.PI * 2);
      ctx.fill();
    }

    function drawSpark(p, fade) {
      const k = Math.max(0, 1 - p.life / p.fade);
      ctx.beginPath();
      ctx.globalAlpha = k * fade;
      ctx.fillStyle = p.color;
      ctx.arc(p.x, p.y, p.r * (0.7 + k * 0.3), 0, Math.PI * 2);
      ctx.fill();
    }

    function drawConfetti(p, fade) {
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.globalAlpha = fade;
      ctx.fillStyle = p.color;
      const flip = Math.abs(Math.cos(p.rot));
      if (p.shape === 'round') {
        ctx.beginPath();
        ctx.arc(0, 0, p.w / 2, 0, Math.PI * 2);
        ctx.fill();
      } else if (p.shape === 'arc') {
        ctx.strokeStyle = p.color;
        ctx.lineWidth = Math.max(1.2, p.h * 0.45);
        ctx.beginPath();
        const hw = (p.w * 0.7) * flip;
        ctx.moveTo(-hw, 0);
        ctx.quadraticCurveTo(0, p.h * 1.4, hw, 0);
        ctx.stroke();
      } else {
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      }
      ctx.restore();
    }

    function drawRibbon(p, t, fade) {
      const n = p.segs;
      const step = p.len / n;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot * 0.15);
      ctx.globalAlpha = 0.85 * fade;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      ctx.beginPath();
      for (let i = 0; i <= n; i++) {
        const x = Math.sin(t * p.f + i * 0.5 + p.phase) * p.a;
        const y = i * step;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = p.stroke;
      ctx.lineWidth = p.w + 1.6;
      ctx.stroke();
      ctx.strokeStyle = p.color;
      ctx.lineWidth = p.w;
      ctx.stroke();
      ctx.restore();
    }

    const t0 = nowFn();

    function frame(now) {
      if (cancelled || done) return;
      const t = now - t0;
      let dt = 16;
      if (lastNow != null) {
        dt = now - lastNow;
        if (dts.length < FRAME_AVG_N) dts.push(dt);
        if (!downgraded && dts.length >= FRAME_AVG_N) {
          let s = 0;
          for (let i = 0; i < FRAME_AVG_N; i++) s += dts[i];
          if (s / FRAME_AVG_N > FRAME_SLOW_MS) downgrade();
        }
      }
      lastNow = now;

      if (!confettiSpawned && t >= CONFETTI_AT) {
        confettiSpawned = true;
        spawnCannon(24, H - 8, 'bl');
        spawnCannon(W - 24, H - 8, 'br');
        spawnCannon(24, 12, 'tl');
        spawnCannon(W - 24, 12, 'tr');
      }
      if (!ribbonsSpawned && t >= RIBBON_AT) {
        ribbonsSpawned = true;
        spawnRibbons();
      }
      if (!stopFireworks) {
        for (let i = 0; i < fwPlan.length; i++) {
          const pl = fwPlan[i];
          if (!pl.launched && t >= pl.t) {
            pl.launched = true;
            spawnRocket(pl);
          }
        }
      }

      ctx.clearRect(0, 0, W, H);
      const fade = t > CELEBRATE_MS - 400 ? Math.max(0, (CELEBRATE_MS - t) / 400) : 1;

      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        if (p.kind === 'rocket') {
          p.vy += p.g;
          p.x += p.vx;
          p.y += p.vy;
          p.trail.push({ x: p.x, y: p.y });
          if (p.trail.length > p.trailN) p.trail.shift();
          if (p.y <= p.peak || p.vy >= 0) {
            explode(p);
            particles.splice(i, 1);
            continue;
          }
          if (offscreen(p)) { particles.splice(i, 1); continue; }
          drawRocket(p);
        } else if (p.kind === 'spark') {
          p.vx *= p.damp;
          p.vy = p.vy * p.damp + p.g;
          p.x += p.vx;
          p.y += p.vy;
          p.life += dt;
          if (p.double && !p.popped && p.life >= 400 && Math.random() < 0.15) {
            p.popped = true;
            spawnSecondary(p);
          } else if (p.double && !p.popped && p.life >= 400) {
            p.popped = true;
          }
          if (p.life >= p.fade || offscreen(p)) { particles.splice(i, 1); continue; }
          drawSpark(p, fade);
        } else if (p.kind === 'confetti') {
          p.vx *= p.damp;
          p.vy = p.vy * p.damp + p.g;
          p.x += p.vx;
          p.y += p.vy;
          p.rot += p.vr;
          if (offscreen(p)) { particles.splice(i, 1); continue; }
          drawConfetti(p, fade);
        } else if (p.kind === 'ribbon') {
          p.x += p.vx * dt + Math.sin(t * p.f + p.phase) * 0.15;
          p.y += p.vy * dt;
          p.rot += p.vr;
          if (offscreen(p)) { particles.splice(i, 1); continue; }
          drawRibbon(p, t, fade);
        }
      }

      if (t >= CELEBRATE_MS) {
        done = true;
        if (rafId) { try { caf(rafId); } catch (e) { /* ignore */ } rafId = 0; }
        if (canvasEl.parentNode) canvasEl.parentNode.removeChild(canvasEl);
        if (running && running.ctrl === ctrl) {
          running.rafId = 0;
          running.canvas = null;
        }
        return;
      }
      rafId = raf(frame);
      if (running && running.ctrl === ctrl) running.rafId = rafId;
    }

    function onResize() {
      if (typeof window === 'undefined') return;
      const nW = window.innerWidth || W;
      const nH = window.innerHeight || H;
      if (!nW || !nH || (nW === W && nH === H)) return;
      const sx = nW / W, sy = nH / H;
      for (let i = 0; i < particles.length; i++) {
        particles[i].x *= sx;
        particles[i].y *= sy;
        if (particles[i].kind === 'rocket') particles[i].peak *= sy;
      }
      W = nW; H = nH;
      sizeCanvas();
    }

    const ctrl = {
      particles: particles,
      spawned: spawned,
      planned: {
        rockets: fwCount, burst: burstUse, confetti: confettiUse * 4, ribbons: ribbonUse,
        total: fwCount + burstUse * fwCount + confettiUse * 4 + ribbonUse,
      },
      liveCount: liveCount,
      cancel: function () {
        cancelled = true;
        if (rafId) { try { caf(rafId); } catch (e) { /* ignore */ } rafId = 0; }
        if (canvasEl.parentNode) canvasEl.parentNode.removeChild(canvasEl);
      },
      tick: function (now) { frame(now); },
      get downgraded() { return downgraded; },
      get stopFireworks() { return stopFireworks; },
      get rafId() { return rafId; },
      get done() { return done; },
      get cancelled() { return cancelled; },
      set cancelled(v) { cancelled = !!v; },
      onResize: onResize,
      caf: caf,
    };

    if (!opts.manual) rafId = raf(frame);
    return ctrl;
  }

  function launchCelebrate(opts) {
    opts = opts || {};
    stopCelebrate();
    const reduce = opts.reduce != null ? !!opts.reduce : prefersReduce();
    const title = opts.title || '';
    const sub = opts.sub || '';
    let host = opts.host || document.querySelector('.rv-celebrate');
    let createdHost = false;
    if (!host) {
      host = makeHost();
      document.body.appendChild(host);
      createdHost = true;
    }
    fillHost(host, title, sub);

    const rec = {
      host: host,
      createdHost: createdHost,
      canvas: null,
      ctrl: null,
      rafId: 0,
      caf: null,
      onResize: null,
      onClick: null,
      onDone: opts.onDone,
      timer: null,
    };
    rec.onClick = function () { stopCelebrate(); };
    host.addEventListener('click', rec.onClick);
    running = rec;

    const hold = reduce ? REDUCE_HOLD_MS : CELEBRATE_MS;
    rec.timer = setTimeout(function () { stopCelebrate(); }, hold);

    if (reduce) return rec;

    const c = document.createElement('canvas');
    c.className = 'rv-confetti';
    document.body.appendChild(c);
    rec.canvas = c;
    const ctrl = burstConfetti(c, opts);
    rec.ctrl = ctrl;
    if (ctrl) {
      rec.caf = ctrl.caf;
      rec.rafId = ctrl.rafId;
      rec.onResize = ctrl.onResize;
      if (typeof window !== 'undefined' && window.addEventListener) {
        window.addEventListener('resize', rec.onResize);
      }
    }
    return rec;
  }

  return {
    CELEBRATE_MS: CELEBRATE_MS,
    PARTICLE_CAP: PARTICLE_CAP,
    REDUCE_HOLD_MS: REDUCE_HOLD_MS,
    prefersReduce: prefersReduce,
    confettiColors: confettiColors,
    burstConfetti: burstConfetti,
    launchCelebrate: launchCelebrate,
    stopCelebrate: stopCelebrate,
    formatBatchSub: formatBatchSub,
    makeHost: makeHost,
  };
})();

window.Panel_review = (() => {
  const ST_ORDER = ['draft', 'prepared', 'described', 'arranged', 'generating', 'collected', 'finished'];
  const RATE_BTNS = [1, 2, 3, 4, 6, 8, 12];
  const RATES = { '1': 1, '2': 2, '3': 3, '4': 4, '6': 6, '8': 8, '0': 12 };
  const CELEBRATE_HOLD_MS = window.ReviewCelebrate.CELEBRATE_MS;

  function atLeastGen(st) {
    return ST_ORDER.indexOf(st) >= ST_ORDER.indexOf('generating');
  }

  function pad3(n) {
    return String(n || 0).padStart(3, '0');
  }

  function fmtP(v) {
    if (v == null || v === '') return '';
    const s = String(v).trim();
    const m = s.match(/^([pb])(\d+)$/i);
    if (m) return m[1].toLowerCase() + String(parseInt(m[2], 10)).padStart(2, '0');
    const n = parseInt(s.replace(/^p/i, ''), 10);
    if (!Number.isFinite(n) || n < 1) return s;
    return 'p' + String(n).padStart(2, '0');
  }

  function pcolor(p) {
    return window.pcolor(p);
  }

  function lastTake(seg) {
    const takes = (seg && seg.takes) || [];
    return takes.length ? takes[takes.length - 1] : null;
  }

  function isBorrowed(seg) {
    if (!seg) return false;
    if (seg.borrowed) return true;
    const tk = lastTake(seg);
    return !!(tk && tk.borrowed);
  }

  function isReplaced(seg) {
    return !!(seg && seg.image_replaced);
  }

  function issuesOf(seg) {
    return (seg && seg.check && seg.check.issues) || [];
  }

  function isMissing(seg) {
    return !seg || !seg.exists;
  }

  function isBad(seg) {
    return isMissing(seg) || issuesOf(seg).length > 0;
  }

  function isCandidate(seg) {
    if (!seg) return false;
    if (seg.mark) return true;
    if (isMissing(seg)) return true;
    if (seg.status === 'failed') return true;
    return false;
  }

  function candidateWhy(seg) {
    if (isMissing(seg)) return 'miss';
    if (seg.status === 'failed' && !seg.mark) return 'fail';
    if (seg.mark) return 'mark';
    return 'miss';
  }

  function filterSegs(segs, f) {
    segs = segs || [];
    if (f === 'fresh') return segs.filter(s => s.fresh);
    if (f === 'mark') return segs.filter(s => s.mark);
    if (f === 'miss') return segs.filter(s => isMissing(s));
    if (f === 'bad') return segs.filter(s => isBad(s));
    return segs.slice();
  }

  function mediaUrl(tid, file, ver) {
    return window.srcWithV(file, ver, tid);
  }

  function typing(el) {
    if (!el) return false;
    const tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
  }

  function hasClip(seg) {
    return !isMissing(seg) && !!lastTake(seg);
  }

  // 从 fromS 之后找下一段有片的；没有则 null（连播跳缺片用）。
  function nextPlayable(list, fromS) {
    list = list || [];
    let i = list.findIndex(x => x.s === fromS);
    if (i < 0) i = -1;
    for (let j = i + 1; j < list.length; j++) {
      if (hasClip(list[j])) return list[j];
    }
    return null;
  }

  function sortReady(list) {
    return (list || []).slice().sort((a, b) => {
      const af = a.status === 'finished' ? 1 : 0;
      const bf = b.status === 'finished' ? 1 : 0;
      if (af !== bf) return af - bf;
      return 0;
    });
  }

  function taskLabel(x) {
    if (!x) return '';
    const s = x.id + ' · ' + (x.name || '') + ' · ' + (x.engine || '');
    return x.status === 'finished' ? '✓ ' + s : s;
  }

  function finishBlockers(segs, status, extra) {
    segs = segs || [];
    extra = extra || {};
    const missList = segs.filter(isMissing);
    const miss = missList.length;
    const missIds = missList.map(s => 's' + pad3(s.s));
    const mark = segs.filter(s => s.mark).length;
    const fail = segs.filter(s => s.status === 'failed').length;
    const fresh = segs.filter(s => s.fresh).length;
    const job = !!extra.job;
    const stOk = status === 'collected' || status === 'generating';
    const empty = !segs.length;
    return {
      miss, missIds, mark, fail, fresh, job, stOk, empty,
      n: segs.length,
      nOk: segs.length - miss,
      ok: stOk && !miss && !mark && !fail && !empty && !fresh && !job,
    };
  }

  function finishReason(b, tFn) {
    tFn = tFn || (typeof window !== 'undefined' && window.t) || function (k) { return k; };
    if (!b) return '';
    if (b.ok) return tFn('review.finish_tip_ok', { n: String(b.n) });
    const parts = [];
    if (b.job) parts.push(tFn('review.finish_tip_job'));
    if (b.empty) parts.push(tFn('review.finish_tip_empty'));
    else if (!b.stOk) parts.push(tFn('review.finish_tip_status'));
    if (b.miss) {
      if (b.missIds && b.missIds.length) {
        parts.push(tFn('review.finish_tip_missing_ids', { n: String(b.miss), ids: b.missIds.join(' ') }));
      } else {
        parts.push(tFn('review.finish_tip_missing', { n: String(b.miss) }));
      }
    }
    if (b.mark) parts.push(tFn('review.finish_tip_marked', { n: String(b.mark) }));
    if (b.fail) parts.push(tFn('review.finish_tip_failed', { n: String(b.fail) }));
    if (b.fresh) parts.push(tFn('review.finish_tip_fresh', { n: String(b.fresh) }));
    return parts.join(' / ');
  }

  function prefersReduce() { return window.ReviewCelebrate.prefersReduce(); }
  function confettiColors() { return window.ReviewCelebrate.confettiColors(); }
  function burstConfetti(canvasEl, opts) { return window.ReviewCelebrate.burstConfetti(canvasEl, opts); }

  return {
    name: 'Panel_review',
    components: { TaskPick: window.TaskPick, DescCardEditor: window.DescCardEditor, RegenAccPick: window.RegenAccPick,
      RegenAccMenu: window.RegenAccMenu },
    setup() {
      const { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } = Vue;

      const tid = ref('');
      const extra = ref([]);
      const segments = ref([]);
      const images = ref([]);
      const picker = reactive({ on: false, s: 0, busy: false, autoRedo: true, accOpen: false });
      const descCards = ref([]);
      const cardAcc = ref(null);
      const cardAccState = ref(null);
      const cardAccPreview = ref(null);
      const cardEdit = ref(null);
      const cardJobId = ref('');
      const marks = ref({});
      const filter = ref('all');
      const cols = ref(5);
      const rate = ref(4);
      const muted = ref(false);
      const chain = ref(true);
      // 0922：「全部任务」——按任务顺序把所有任务的片子摊在一页，可以一路连播
      const allMode = ref(false);
      const allIncFinished = ref(false);
      const allLoading = ref(false);
      const allGroups = ref([]);
      const allCur = ref('');
      const allPlaying = ref('');
      let allSeq = 0;
      const curS = ref(0);
      const playingS = ref(0);
      const flashS = ref(0);
      const taskMode = ref('first_frame');
      let flashTimer = null;
      const open = reactive({});
      const editing = reactive({ s: 0, text: '', orig: '' });
      const promptBusy = ref(false);
      const drawer = ref(false);
      const gapOn = ref(false);
      const gap = ref({ report_path: '', group: '', clips_seen: 0, gaps: [], rows: [], counts: { real: 0, maybe: 0, note: 0, mismatch: 0 }, found: false });
      const gapPicked = ref([]);
      const gapLoading = ref(false);
      const gapNotesOpen = ref(false);
      const gapExpand = ref({});
      const picked = ref([]);
      // 重生时临时换账号：'' = 用任务自己的账号（只在重生抽屉里选，别处的重生一律原账号）
      const regenAcc = ref('');
      // 规格 98：换图面板里「换图并重生」/ 描述卡自动重生用哪个账号（'' = 原账号）
      const pickerAcc = ref('');
      const pickedSegs = computed(() => (segments.value || []).filter(sg => picked.value.includes(sg.s)));
      const waiting = ref('');
      const loading = ref(false);
      const celebrateOn = ref(false);
      const celeTitle = ref('');
      const celeSub = ref('');
      const finishedAt = ref('');
      const finishing = ref(false);
      const verifying = ref(false);
      const verifyFade = ref(false);
      const verifyDone = ref(0);
      const verifyTotal = ref(0);
      let reloadTimer = null;
      let loadingLogs = false;
      let celeTimer = null;
      let celeGen = 0;
      let verifyFadeTimer = null;
      const tailsOn = ref(false);
      const tails = reactive({ items: [], i: 0, showHead: false, loading: false, jobId: '' });

      const VERIFY_CODES = { E_DUP_ASSET: 1, E_WRONG_CLIP: 1, W_FRAME_SOFT: 1, W_FRAME_TWIN: 1 };

      function applyVerified(results) {
        // 就地改旗子，不换 segments 数组、不动 video.src，正在播的不要断
        if (!results || !results.length) return;
        for (const r of results) {
          const seg = segments.value.find(s => s.s === r.s);
          if (!seg) continue;
          if (r.take && Array.isArray(seg.takes) && seg.takes.length) {
            const tk = seg.takes[seg.takes.length - 1];
            const a = String(tk.file || '').replace(/\\/g, '/');
            const b = String(r.take.file || '').replace(/\\/g, '/');
            if (!b || a === b) {
              const file = tk.file;
              Object.assign(tk, r.take);
              tk.file = file;
            }
          }
          const chk = Object.assign({}, seg.check || {});
          const old = (chk.issues || []).filter(i => !VERIFY_CODES[i.code]);
          chk.issues = old.concat(r.issues || []);
          seg.check = chk;
        }
      }

      function finishVerifyBar() {
        verifyFade.value = true;
        if (verifyFadeTimer) clearTimeout(verifyFadeTimer);
        verifyFadeTimer = setTimeout(() => {
          verifying.value = false;
          verifyFade.value = false;
        }, 700);
      }

      const pickerTasks = computed(() => {
        const map = new Map();
        for (const x of [...(store.tasks || []), ...extra.value]) {
          if (x && x.id) map.set(x.id, x);
        }
        return [...map.values()];
      });
      const readyTasks = computed(() => {
        const map = new Map();
        for (const x of pickerTasks.value) {
          if (atLeastGen(x.status) && x.status !== 'trash') map.set(x.id, x);
        }
        return sortReady([...map.values()]);
      });
      const selectedTask = computed(() =>
        pickerTasks.value.find(x => x.id === tid.value) || null);
      const tidReady = computed(() => !!(selectedTask.value && atLeastGen(selectedTask.value.status)));

      const ts = reactive(window.TextSearch.create({
        getSegs: () => filterSegs(segments.value, filter.value),
        cardSel: '.panel-review .rv-card[data-s="{s}"]',
        inputSel: '.panel-review .ts-bar input',
        onJump: (s) => { curS.value = s; },
      }));
      const view = computed(() => {
        const list = filterSegs(segments.value, filter.value);
        if (!String(ts.q || '').trim()) return list;
        return list.filter(sg => ts.isHit(sg.s));
      });
      const candidates = computed(() => segments.value.filter(isCandidate));
      const noImgPicked = computed(() => segments.value
        .filter(s => picked.value.includes(s.s) && !(s.image && s.image_ok !== false))
        .map(s => s.s));
      const checkJob = computed(() => (store.jobs || []).find(j =>
        j.task_id === tid.value && j.kind === 'check' &&
        (j.status === 'running' || j.status === 'queued')));
      const regenJob = computed(() => (store.jobs || []).find(j =>
        j.task_id === tid.value && j.kind === 'regenerate' &&
        (j.status === 'running' || j.status === 'queued')));
      const lastRegen = computed(() => {
        const all = (store.jobs || []).filter(j => j.task_id === tid.value && j.kind === 'regenerate');
        return regenJob.value || all[all.length - 1] || null;
      });
      const redoLog = computed(() => {
        const j = lastRegen.value;
        if (!j) return [];
        return (store.logs[j.id] || []).slice(-120);
      });
      const logText = computed(() => redoLog.value.join('\n'));
      const checking = computed(() => !!checkJob.value);
      const regenerating = computed(() => !!regenJob.value);

      const nMiss = computed(() => segments.value.filter(isMissing).length);
      const nMark = computed(() => segments.value.filter(s => s.mark).length);
      const nFail = computed(() => segments.value.filter(s => s.status === 'failed').length);
      const nFlag = computed(() => segments.value.filter(s =>
        issuesOf(s).some(i => i.level === 'bad' || i.level === 'miss')
      ).length);
      const isFinished = computed(() =>
        !!(selectedTask.value && (selectedTask.value.status === 'finished' || selectedTask.value.archived)));
      const hasLiveJob = computed(() => {
        const id = tid.value;
        if (!id) return false;
        // 规格 83：lost 已结束，不当活 Job；halted 仍算占着
        return (store.jobs || []).some(j =>
          j.task_id === id && (j.status === 'running' || j.status === 'queued' || j.status === 'halted'));
      });
      const blockers = computed(() =>
        finishBlockers(segments.value, selectedTask.value && selectedTask.value.status, {
          job: hasLiveJob.value,
        }));
      const canFinish = computed(() => !isFinished.value && blockers.value.ok);

      function finishTip() {
        if (isFinished.value) return '';
        return finishReason(blockers.value, t);
      }

      function imgOf(p) {
        return images.value.find(im => im.p === p) || {};
      }

      function durText(seg) {
        const c = seg.check || {};
        if (c.duration) return Number(c.duration).toFixed(1) + 's';
        if (seg.duration) return seg.duration + 's';
        return '—';
      }

      function flagClass(iss) {
        if (!iss) return 'bad';
        if (iss.level === 'miss') return 'miss';
        if (iss.level === 'warn') return 'warn';
        return 'bad';
      }

      function checkedOk(seg) {
        const c = seg.check;
        if (!c || isMissing(seg)) return false;
        if (!Array.isArray(c.issues)) return false;
        if (c.issues.length) return false;
        // 跑过体检才会有 duration / has_audio / size 之一
        return c.duration != null || c.has_audio != null || c.size != null;
      }

      function okText(seg) {
        const db = seg.check && seg.check.mean_db;
        if (db != null && Number.isFinite(Number(db))) {
          return t('review.ok_db', { n: String(Math.round(Number(db))) });
        }
        return t('review.ok');
      }

      function curSeg() {
        return view.value.find(s => s.s === curS.value) || view.value[0] || null;
      }

      // 按 data-s 找，不靠 .cur（Vue 还没重渲染时 .cur 仍挂在旧卡上）。
      function curCard() {
        return document.querySelector('.panel-review .rv-card[data-s="' + curS.value + '"]');
      }

      function curVideo() {
        return document.querySelector('.panel-review video[data-s="' + curS.value + '"]');
      }

      function resetBarS(s) {
        const card = document.querySelector('.panel-review .rv-card[data-s="' + s + '"]');
        const bar = card && card.querySelector('.bar');
        if (bar) window.ReviewScrubber.setFill(bar, 0);
      }

      function resetBarOfVideo(v) {
        const stage = v && v.parentElement;
        const bar = stage && stage.querySelector('.bar');
        if (bar) window.ReviewScrubber.setFill(bar, 0);
      }

      function pauseAll() {
        document.querySelectorAll('.panel-review video').forEach(v => v.pause());
        playingS.value = 0;
      }

      function focusS(s, scroll) {
        curS.value = s;
        if (scroll !== false) {
          nextTick(() => {
            const el = curCard();
            if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          });
        }
      }

      async function play(fromStart) {
        const v = curVideo();
        const seg = curSeg();
        if (!v) {
          if (chain.value) {
            const nxt = nextPlayable(view.value, curS.value);
            if (nxt) {
              focusS(nxt.s);
              await nextTick();
              return play(true);
            }
          }
          playingS.value = 0;
          return;
        }
        document.querySelectorAll('.panel-review video').forEach(o => { if (o !== v) o.pause(); });
        if (fromStart) {
          try { v.currentTime = 0; } catch (e) { /* 元数据没到就从默认起点放 */ }
          resetBarOfVideo(v);
        }
        v.muted = muted.value;
        v.volume = 1;
        v.playbackRate = rate.value;
        v.play().then(() => {
          v.playbackRate = rate.value;
          playingS.value = seg ? seg.s : 0;
          if (seg && taskMode.value === 'anchor_bridge' && seg.role === 'bridge') {
            flashS.value = seg.s;
            if (flashTimer) clearTimeout(flashTimer);
            flashTimer = setTimeout(() => { if (flashS.value === seg.s) flashS.value = 0; }, 1200);
          }
        }).catch(() => {});
      }

      async function move(d) {
        const prev = curS.value;
        const v = curVideo();
        if (v) v.pause();
        playingS.value = 0;
        resetBarS(prev);
        const list = view.value;
        if (!list.length) return;
        let i = list.findIndex(x => x.s === curS.value);
        if (i < 0) i = 0;
        i = Math.max(0, Math.min(list.length - 1, i + d));
        focusS(list[i].s);
        await nextTick();
        if (curVideo()) play(true);
      }

      function toggle() {
        const v = curVideo();
        if (!v) {
          play(false);
          return;
        }
        if (v.paused) play(false);
        else { v.pause(); playingS.value = 0; }
      }

      // ---- 全部任务（全功能版 + 活跃视频解码器池防黑屏/防卡死） ----
      const MAX_ALL_VIDEOS = 8;
      const activeVideoKeys = ref([]);
      let allObserver = null;

      function allKey(id, s) { return id + '|' + s; }

      function groupOf(tId) {
        if (!tId) return null;
        return (allGroups.value || []).find(g => g && g.id === tId) || null;
      }

      function imgOfFor(tId, pName) {
        if (!pName) return {};
        if (!tId || tId === tid.value) {
          const hit = (images.value || []).find(im => im.p === pName);
          if (hit) return hit;
        }
        const g = groupOf(tId);
        if (g && Array.isArray(g.images)) {
          return g.images.find(im => im.p === pName) || {};
        }
        return (images.value || []).find(im => im.p === pName) || {};
      }

      function posterUrl(tId, seg) {
        if (!tId || !seg) return '';
        if (seg.bridge && seg.bridge.tail_frame) {
          return mediaUrl(tId, seg.bridge.tail_frame, seg.mtime || 0);
        }
        const im = imgOfFor(tId, seg.image);
        if (im && im.file) {
          return mediaUrl(tId, im.file, im.mtime || seg.image_mtime || 0);
        }
        return '/media/' + encodeURIComponent(tId) + '/thumb/' + encodeURIComponent(String(seg.s || 1)) + '?v=' + (seg.mtime || 1);
      }

      function onPosterLoad(ev) {
        const img = ev && ev.target;
        const st = img && img.parentElement;
        if (st && img.naturalWidth && img.naturalHeight) {
          st.style.aspectRatio = img.naturalWidth + ' / ' + img.naturalHeight;
        }
      }

      function isAllVideoMounted(key) {
        if (!key) return false;
        if (allPlaying.value === key || allCur.value === key) return true;
        return activeVideoKeys.value.includes(key);
      }

      function activateAllVideo(key) {
        if (!key) return;
        const list = activeVideoKeys.value.filter(k => k !== key);
        list.unshift(key);
        while (list.length > MAX_ALL_VIDEOS) {
          const cand = list[list.length - 1];
          if (cand === allPlaying.value || cand === allCur.value) {
            if (list.length <= 2) break;
            list.splice(list.length - 2, 1);
          } else {
            list.pop();
          }
        }
        activeVideoKeys.value = list;
      }

      function setupAllObserver() {
        if (allObserver) {
          try { allObserver.disconnect(); } catch (e) {}
          allObserver = null;
        }
        if (!allMode.value || typeof IntersectionObserver !== 'function') return;
        const rootEl = document.querySelector('.panel-review .rv-all');
        allObserver = new IntersectionObserver((entries) => {
          for (const ent of entries) {
            if (ent.isIntersecting) {
              const k = ent.target && ent.target.dataset && ent.target.dataset.key;
              if (k) activateAllVideo(k);
            }
          }
        }, { root: rootEl || null, rootMargin: '180px 0px', threshold: 0.15 });
        document.querySelectorAll('.panel-review .rv-all-card').forEach(el => {
          allObserver.observe(el);
        });
      }

      function syncContextFromGroup(targetTid, sNum) {
        if (!targetTid) return;
        const g = groupOf(targetTid);
        if (tid.value !== targetTid) {
          skipTid = true;
          tid.value = targetTid;
          ui.panelTid = targetTid;
        }
        if (g) {
          segments.value = g.segs || [];
          images.value = g.images || [];
          marks.value = g.marks || {};
          taskMode.value = g.mode || 'first_frame';
        }
        if (sNum) {
          curS.value = sNum;
          allCur.value = allKey(targetTid, sNum);
        }
      }

      async function refreshTaskInAll(targetTid) {
        if (!targetTid) return;
        const g = groupOf(targetTid);
        if (!g) return;
        try {
          const r = await API.get('/api/tasks/' + encodeURIComponent(targetTid) + '/review?lite=1', { silent: true });
          g.segs = r.segments || [];
          g.images = r.images || [];
          g.marks = r.marks || {};
          g.status = r.status || g.status;
          g.mode = r.mode || g.mode || 'first_frame';
          if (tid.value === targetTid) {
            segments.value = g.segs;
            images.value = g.images;
            marks.value = g.marks;
            taskMode.value = g.mode;
          }
        } catch (e) {}
      }

      const allTasks = computed(() => readyTasks.value.filter(x =>
        allIncFinished.value || x.status !== 'finished'));

      const allRawFlat = computed(() => {
        const out = [];
        allGroups.value.forEach((g, gi) => {
          (g.segs || []).forEach((seg, si) => out.push({
            tid: g.id, name: g.name, engine: g.engine, status: g.status,
            archived: !!g.archived, isFinished: g.status === 'finished' || !!g.archived,
            mode: g.mode || 'first_frame',
            gi, first: si === 0,
            n: (g.segs || []).length, seg, key: allKey(g.id, seg.s),
          }));
        });
        return out;
      });

      const allFlat = computed(() => {
        let list = allRawFlat.value;
        const f = filter.value;
        if (f === 'fresh') list = list.filter(x => x.seg.fresh);
        else if (f === 'mark') list = list.filter(x => x.seg.mark);
        else if (f === 'miss') list = list.filter(x => isMissing(x.seg));
        else if (f === 'bad') list = list.filter(x => isBad(x.seg));
        const q = String(ts.qLive || ts.q || '').trim().toLowerCase();
        if (q) {
          list = list.filter(x => {
            const sg = x.seg || {};
            const im = imgOfFor(x.tid, sg.image);
            const hay = [
              x.tid, x.name, 's' + pad3(sg.s), sg.image,
              sg.text, sg.prompt_final, sg.shot, im.desc, im.voice,
            ].filter(Boolean).join(' ').toLowerCase();
            return hay.includes(q);
          });
        }
        return list;
      });

      const allTotalCount = computed(() => allRawFlat.value.length);
      const allHave = computed(() => allRawFlat.value.filter(x => hasClip(x.seg)).length);
      const allNMiss = computed(() => allRawFlat.value.filter(x => isMissing(x.seg)).length);
      const allNMark = computed(() => allRawFlat.value.filter(x => x.seg.mark).length);
      const allNFail = computed(() => allRawFlat.value.filter(x => x.seg.status === 'failed').length);
      const allNFlag = computed(() => allRawFlat.value.filter(x =>
        issuesOf(x.seg).some(i => i.level === 'bad' || i.level === 'miss')).length);
      const allNFresh = computed(() => allRawFlat.value.filter(x => x.seg.fresh).length);
      const allPicked = ref([]);
      const allCandidates = computed(() =>
        allRawFlat.value.filter(x => !x.isFinished && isCandidate(x.seg))
      );
      const allPickedSegs = computed(() =>
        allCandidates.value
          .filter(x => allPicked.value.includes(x.key))
          .map(x => Object.assign({}, x.seg, { _tid: x.tid }))
      );

      async function loadAll() {
        const my = ++allSeq;
        allLoading.value = true;
        const list = allTasks.value.slice();
        const res = new Array(list.length);
        let i = 0;
        async function worker() {
          while (i < list.length) {
            const k = i++;
            const x = list[k];
            try {
              const r = await API.get('/api/tasks/' + encodeURIComponent(x.id) + '/review?lite=1', { silent: true });
              res[k] = {
                id: x.id,
                name: x.name || r.name || '',
                engine: x.engine || r.engine || '',
                status: r.status || x.status || '',
                archived: !!(r.archived || x.archived),
                mode: r.mode || 'first_frame',
                segs: r.segments || [],
                images: r.images || [],
                marks: r.marks || {},
              };
            } catch (e) {
              res[k] = {
                id: x.id, name: x.name || '', engine: x.engine || '', status: x.status,
                archived: !!x.archived, mode: 'first_frame', segs: [], images: [], marks: {}, err: true,
              };
            }
          }
        }
        try {
          await Promise.all([worker(), worker(), worker(), worker()]);
        } finally {
          if (my === allSeq) {
            allGroups.value = res.filter(g => g && (g.segs.length || g.err));
            allLoading.value = false;
            const initialKeys = [];
            for (const item of allRawFlat.value) {
              if (hasClip(item.seg)) {
                initialKeys.push(item.key);
                if (initialKeys.length >= 6) break;
              }
            }
            activeVideoKeys.value = initialKeys;
            if (!allCur.value && initialKeys.length) {
              allCur.value = initialKeys[0];
              const firstIt = allRawFlat.value.find(x => x.key === initialKeys[0]);
              if (firstIt) syncContextFromGroup(firstIt.tid, firstIt.seg.s);
            }
            nextTick(() => setupAllObserver());
          }
        }
      }

      function setAllMode(on) {
        pauseAll();
        allCur.value = '';
        allPlaying.value = '';
        if (!on && allObserver) {
          try { allObserver.disconnect(); } catch (e) {}
          allObserver = null;
        }
        allMode.value = !!on;
        if (on) loadAll();
        else if (tid.value) loadReview();
      }

      function allVideo(key) {
        const sel = '.panel-review .rv-all-card[data-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"] video';
        return document.querySelector(sel);
      }

      async function allPlay(key, fromStart) {
        allCur.value = key;
        const item = allRawFlat.value.find(x => x.key === key);
        if (item) syncContextFromGroup(item.tid, item.seg.s);
        activateAllVideo(key);
        await nextTick();
        const v = allVideo(key);
        if (!v) { allPlaying.value = ''; return; }
        document.querySelectorAll('.panel-review video').forEach(o => { if (o !== v) o.pause(); });
        if (fromStart) {
          try { v.currentTime = 0; } catch (e) { /* 元数据没到就从头 */ }
          resetBarOfVideo(v);
        }
        v.muted = muted.value;
        v.volume = 1;
        v.playbackRate = rate.value;
        const card = v.closest('.rv-all-card');
        if (card) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        try {
          await v.play();
          v.playbackRate = rate.value;
          allPlaying.value = key;
        } catch (e) { allPlaying.value = ''; }
      }

      async function allToggle(item) {
        if (!item) return;
        syncContextFromGroup(item.tid, item.seg.s);
        activateAllVideo(item.key);
        await nextTick();
        const v = allVideo(item.key);
        if (!v) { allCur.value = item.key; return; }
        if (allCur.value === item.key && !v.paused) {
          v.pause();
          allPlaying.value = '';
        } else {
          allPlay(item.key, false);
        }
      }

      function onAllStageEnter(ev, item) {
        if (item && item.key) activateAllVideo(item.key);
        onStageEnter(ev, item && item.seg);
      }

      function allStep(d) {
        const L = allFlat.value.filter(x => hasClip(x.seg));
        if (!L.length) return;
        let i = L.findIndex(x => x.key === allCur.value);
        if (i < 0) i = d > 0 ? -1 : L.length;
        const j = Math.max(0, Math.min(L.length - 1, i + d));
        allPlay(L[j].key, true);
      }

      async function onAllEnded(item) {
        allPlaying.value = '';
        const v = allVideo(item.key);
        const stage = v && v.parentElement;
        const bar = stage && stage.querySelector('.bar');
        if (bar) {
          window.ReviewScrubber.setFill(bar, 1);
          if (v) window.ReviewScrubber.syncFromVideo(v, bar, window.ReviewScrubber.fpsOf(item.seg));
        }
        if (!chain.value || allCur.value !== item.key) return;
        const L = allFlat.value;
        const i = L.findIndex(x => x.key === item.key);
        for (let j = i + 1; j < L.length; j++) {
          if (hasClip(L[j].seg)) {
            await nextTick();
            return allPlay(L[j].key, true);
          }
        }
      }

      function onAllMeta(ev) {
        // 片子是横是竖就按它自己的比例摆，横屏片不再被塞进竖框
        const v = ev.target;
        const st = v && v.parentElement;
        if (st && v.videoWidth && v.videoHeight) st.style.aspectRatio = v.videoWidth + ' / ' + v.videoHeight;
      }

      let jumpS = 0;
      function allOpen(id, s) {
        setAllMode(false);
        if (s) jumpS = s;
        if (tid.value === id) {
          loadReview().then(() => {
            if (s && segments.value.some(x => x.s === s)) { jumpS = 0; focusS(s); }
          });
          return;
        }
        tid.value = id;
      }

      // 全部任务模式下：打开跨任务批量重生抽屉，支持自由选择 Flow1 (Omni) 等账号并自动跨模型重分段
      function redoAllMarkedAcrossTasks() {
        openRedo();
      }

      function setRate(r) {
        rate.value = r;
        const v = allMode.value ? allVideo(allCur.value) : curVideo();
        if (v) v.playbackRate = r;
      }

      function setMuted(on) {
        muted.value = on;
        document.querySelectorAll('.panel-review video').forEach(v => { v.muted = on; });
      }

      function setChain(on) {
        chain.value = on;
        if (allMode.value) {
          if (on && allCur.value) allPlay(allCur.value, false);
          return;
        }
        if (on) play(false);
      }

      function onStageClick(seg) {
        focusS(seg.s, false);
        toggle();
      }

      function onStageEnter(ev, seg) {
        const stage = ev.currentTarget;
        if (stage && stage.dataset) {
          stage.dataset.fps = String(window.ReviewScrubber.fpsOf(seg));
        }
        window.ReviewScrubber.onEnter(ev);
      }

      function onStageLeave(ev) {
        window.ReviewScrubber.onLeave(ev);
      }

      function onTime(ev) {
        const v = ev.target;
        if (window.ReviewScrubber.isDragging(v)) return;
        const stage = v.parentElement;
        const bar = stage && stage.querySelector('.bar');
        if (bar && v.duration) {
          const fps = Number((stage.dataset && stage.dataset.fps) || 24);
          window.ReviewScrubber.syncFromVideo(v, bar, fps);
        }
      }

      async function onEnded(seg) {
        playingS.value = 0;
        const card = document.querySelector('.panel-review .rv-card[data-s="' + seg.s + '"]');
        const bar = card && card.querySelector('.bar');
        const v = card && card.querySelector('video');
        if (bar) {
          window.ReviewScrubber.setFill(bar, 1);
          if (v) window.ReviewScrubber.syncFromVideo(v, bar, window.ReviewScrubber.fpsOf(seg));
        }
        if (!(chain.value && curS.value === seg.s)) return;
        const nxt = nextPlayable(view.value, seg.s);
        if (!nxt) return;
        focusS(nxt.s);
        await nextTick();
        play(true);
      }

      function guardUnsaved() {
        if (!editing.s || editing.text === editing.orig) return true;
        return confirm(t('review.prompt_unsaved'));
      }

      function autosizePrompt(el) {
        if (!el) return;
        el.style.height = 'auto';
        el.style.height = Math.max(el.scrollHeight, 8 * 20) + 'px';
      }

      const editingDesc = reactive({
        key: '', tid: '', s: 0, p: '', desc: '', voice: '', origDesc: '', origVoice: '', busy: false,
      });

      function startEdit(seg, cardKey, targetTid) {
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        if (isFinished.value) return;
        if (editing.s && editing.s !== seg.s && !guardUnsaved()) return;
        const ck = cardKey || seg.s;
        open[ck] = true;
        open[seg.s] = true;
        editing.s = seg.s;
        editing.key = ck;
        editing.tid = targetTid || tid.value;
        editing.text = seg.prompt_final || '';
        editing.orig = seg.prompt_final || '';
        nextTick(() => {
          const el = document.querySelector('.panel-review textarea.pedit');
          if (el) { autosizePrompt(el); el.focus(); }
        });
      }

      function cancelEdit() {
        editing.s = 0;
        editing.key = '';
        editing.tid = '';
        editing.text = '';
        editing.orig = '';
      }

      async function savePrompt(seg, targetTid) {
        const useTid = targetTid || editing.tid || tid.value;
        if (!useTid || !seg || promptBusy.value) return;
        promptBusy.value = true;
        try {
          const r = await API.post(
            '/api/tasks/' + encodeURIComponent(useTid) + '/review/prompt',
            { s: seg.s, text: editing.text });
          seg.prompt_final = editing.text;
          seg.prompt_locked = true;
          cancelEdit();
          toast(t('review.prompt_saved', { n: String(r.chars) }), '', 'ok');
        } catch (e) { /* API 已经 toast */ } finally {
          promptBusy.value = false;
        }
      }

      async function resetPrompt(seg, targetTid) {
        const useTid = targetTid || editing.tid || tid.value;
        if (!useTid || !seg || promptBusy.value) return;
        if (!confirm(t('review.prompt_reset_confirm'))) return;
        promptBusy.value = true;
        try {
          await API.post(
            '/api/tasks/' + encodeURIComponent(useTid) + '/review/prompt',
            { s: seg.s, reset: true });
          seg.prompt_final = '';
          seg.prompt_locked = false;
          cancelEdit();
          toast(t('review.prompt_saved', { n: '0' }), '', 'ok');
        } catch (e) { /* API 已经 toast */ } finally {
          promptBusy.value = false;
        }
      }

      function onPromptKey(ev, seg, targetTid) {
        if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
          ev.preventDefault();
          savePrompt(seg, targetTid);
        } else if (ev.key === 'Escape') {
          ev.preventDefault();
          cancelEdit();
        }
      }

      // ---- 直接编辑本段配图的描述语与音色 ----
      function startEditDesc(seg, cardKey, targetTid) {
        const useTid = targetTid || tid.value;
        if (!useTid || !seg || !seg.image) return;
        if (targetTid) syncContextFromGroup(targetTid, seg.s);
        if (isFinished.value) return;
        const ck = cardKey || seg.s;
        open[ck] = true;
        open[seg.s] = true;
        const im = imgOfFor(useTid, seg.image);
        editingDesc.key = String(ck);
        editingDesc.tid = useTid;
        editingDesc.s = seg.s;
        editingDesc.p = seg.image;
        editingDesc.desc = im.desc || '';
        editingDesc.voice = im.voice || '';
        editingDesc.origDesc = im.desc || '';
        editingDesc.origVoice = im.voice || '';
        editingDesc.busy = false;
        nextTick(() => {
          const el = document.querySelector('.panel-review textarea.pedit-desc');
          if (el) { autosizePrompt(el); el.focus(); }
        });
      }

      function cancelEditDesc() {
        editingDesc.key = '';
        editingDesc.tid = '';
        editingDesc.s = 0;
        editingDesc.p = '';
        editingDesc.desc = '';
        editingDesc.voice = '';
        editingDesc.busy = false;
      }

      async function saveDesc(seg, targetTid) {
        const useTid = targetTid || editingDesc.tid || tid.value;
        const pName = (seg && seg.image) || editingDesc.p;
        if (!useTid || !pName || editingDesc.busy) return;
        editingDesc.busy = true;
        try {
          const r = await API.post(
            '/api/tasks/' + encodeURIComponent(useTid) + '/image/' + encodeURIComponent(pName),
            { desc: editingDesc.desc, voice: editingDesc.voice, by: 'user' }
          );
          const updatedIm = (r && r.image) || {};
          const imLocal = imgOfFor(useTid, pName);
          if (imLocal) {
            imLocal.desc = updatedIm.desc != null ? updatedIm.desc : editingDesc.desc;
            imLocal.voice = updatedIm.voice != null ? updatedIm.voice : editingDesc.voice;
            imLocal.desc_manual = true;
          }
          const g = groupOf(useTid);
          if (g && Array.isArray(g.images)) {
            const gim = g.images.find(x => x.p === pName);
            if (gim) {
              gim.desc = imLocal.desc;
              gim.voice = imLocal.voice;
              gim.desc_manual = true;
            }
          }
          if (seg && !seg.prompt_locked) {
            try {
              const pr = await API.get('/api/tasks/' + encodeURIComponent(useTid) + '/describe/preview?s=' + encodeURIComponent(String(seg.s)), { silent: true });
              if (pr && pr.prompt) seg.prompt_final = pr.prompt;
            } catch (e) {}
          }
          cancelEditDesc();
          toast('已保存描述语并同步更新提示词', '', 'ok');
        } catch (e) {
          editingDesc.busy = false;
        }
      }

      function segLocked(s) {
        const sg = segments.value.find(x => x.s === s);
        return !!(sg && sg.prompt_locked);
      }

      function togglePrompt(seg, cardKey, targetTid) {
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        const ck = cardKey || seg.s;
        if (open[ck] && editing.s === seg.s && !guardUnsaved()) return;
        if (open[ck] && editing.s === seg.s) cancelEdit();
        const nextVal = !open[ck];
        open[ck] = nextVal;
        open[seg.s] = nextVal;
      }

      // ---- 「新」标：重生出来还没复查的片子 ----
      const nFresh = computed(() => segments.value.filter(s => s.fresh).length);
      const popping = reactive({});   // 段号 -> true：标签正在播放"收下"动画

      // 点掉「新」标：支持单任务与全部任务模式
      async function clearFresh(sn, targetTid) {
        if (allMode.value && !sn && !targetTid) {
          for (const g of (allGroups.value || [])) {
            const hasF = (g.segs || []).some(sg => sg.fresh);
            if (!hasF) continue;
            API.post('/api/tasks/' + encodeURIComponent(g.id) + '/review/clear_fresh', {}).catch(() => null);
            for (const sg of (g.segs || [])) sg.fresh = '';
          }
          if (filter.value === 'fresh') filter.value = 'all';
          return;
        }
        const useTid = targetTid || tid.value;
        if (!useTid) return;
        const g = groupOf(useTid);
        const segList = (allMode.value && g) ? (g.segs || []) : (segments.value || []);
        const targets = segList
          .filter(sg => sg.fresh && (!sn || sg.s === sn))
          .map(sg => sg.s);
        if (!targets.length) return;
        for (const x of targets) {
          popping[x] = true;
          popping[allKey(useTid, x)] = true;
        }
        const body = {};
        if (sn) body.s = sn;
        const req = API.post(
          '/api/tasks/' + encodeURIComponent(useTid) + '/review/clear_fresh', body
        ).catch(() => null);
        await new Promise(r => setTimeout(r, 420));   // 等动画放完
        await req;
        for (const sg of segList) {
          if (targets.includes(sg.s)) sg.fresh = '';
        }
        for (const x of targets) {
          delete popping[x];
          delete popping[allKey(useTid, x)];
        }
        if (!sn && filter.value === 'fresh') filter.value = 'all';
      }

      // ---- 换图 / 换图重生 ----
      const usableImgs = computed(() => (images.value || []).filter(im =>
        im.role !== '空镜' && im.status !== 'blocked' && im.exists !== false
        && im.status !== 'pending_review'));

      function openPicker(seg, targetTid) {
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        if (isFinished.value) return;
        picker.s = seg.s;
        picker.on = true;
        picker.accOpen = false;
        pickerAcc.value = '';
        loadDescCards();
        loadCardAcc();
      }

      function closePicker() {
        picker.on = false;
        picker.s = 0;
        picker.accOpen = false;
        cardAccPreview.value = null;
        cardEdit.value = null;
      }

      async function loadDescCards() {
        try {
          const r = await API.get('/api/desc-cards');
          descCards.value = r.cards || [];
        } catch (e) { descCards.value = []; }
      }

      async function loadCardAcc() {
        if (!tid.value) return;
        try {
          const r = await API.get('/api/tasks/' + encodeURIComponent(tid.value) + '/expand-account');
          cardAccState.value = r;
          cardAcc.value = (r && r.current && r.current.id) ? r.current : null;
        } catch (e) { /* API 已 toast */ }
      }

      async function pickCardAcc(id) {
        if (!tid.value) return;
        try {
          const r = await API.post(
            '/api/tasks/' + encodeURIComponent(tid.value) + '/expand-account',
            { account_id: id == null ? '' : id });
          cardAccState.value = r;
          cardAcc.value = (r && r.current && r.current.id) ? r.current : null;
          picker.accOpen = false;
          cardAccPreview.value = null;
        } catch (e) { cardAccPreview.value = null; }
      }

      function accText(a) {
        if (!a) return '';
        return (a.engine || '') + ' · ' + (a.name || a.id || '');
      }
      const cardAccPinned = computed(() => !!(cardAccState.value && cardAccState.value.current
        && cardAccState.value.current.source === 'pinned'));
      const cardAccBorrowed = computed(() => !!(cardAccState.value && cardAccState.value.current
        && cardAccState.value.current.source === 'borrowed'));
      const cardAccOptions = computed(() => (cardAccState.value && cardAccState.value.options) || []);
      const cardFollowLabel = computed(() => {
        const ta = cardAccState.value && cardAccState.value.task_account;
        if (ta && ta.name) return t('track.acc.follow_one', { acc: (ta.engine || '') + ' · ' + ta.name });
        return t('track.acc.follow');
      });
      const shownCardAcc = computed(() => {
        if (cardAccPreview.value == null) return cardAcc.value;
        if (cardAccPreview.value === '') {
          const ta = cardAccState.value && cardAccState.value.task_account;
          return ta || null;
        }
        return (cardAccOptions.value || []).find(a => a.id === cardAccPreview.value) || cardAcc.value;
      });

      function toggleCardAccMenu() {
        if (picker.busy) return;
        picker.accOpen = !picker.accOpen;
      }

      const cardAccGest = window.usePickerGestures({
        items: () => {
          const out = [{ value: '', disabled: false }];
          (cardAccOptions.value || []).forEach(a => {
            out.push({
              value: a.id,
              disabled: !!a.disabled || a.enabled === false,
            });
          });
          return out;
        },
        current: () => {
          if (cardAccPreview.value != null) return cardAccPreview.value;
          if (cardAccPinned.value && cardAcc.value) return cardAcc.value.id;
          return '';
        },
        open: () => picker.accOpen,
        disabled: () => !!picker.busy,
        onPreview(item) { cardAccPreview.value = item ? item.value : ''; },
        onCommit(item) {
          pickCardAcc(item ? item.value : '');
        },
        onCancel() { cardAccPreview.value = null; },
        onOpen: toggleCardAccMenu,
      });

      function identityKindLabel(kind) {
        if (kind === 'chain_tail') return t('review.cards.ref_tail');
        if (kind === 'expand_ref') return t('review.cards.ref_start');
        return t('review.cards.ref');
      }

      async function clickCard(card) {
        const sn = picker.s;
        if (!tid.value || !sn || picker.busy || !card) return;
        picker.busy = true;
        try {
          const args = { s: sn, card_id: card.id, auto_redo: !!picker.autoRedo };
          if (picker.autoRedo && pickerAcc.value) args.redo_account_id = pickerAcc.value;
          const r = await API.job('card_swap', tid.value, args);
          const jid = (r.job && r.job.id) || '';
          cardJobId.value = jid;
          waiting.value = jid;
          toast(t('review.cards.busy', { name: card.name }), '', 'ok');
        } catch (e) {
          picker.busy = false;
        }
      }

      function openCardEdit(card) {
        cardEdit.value = card
          ? { id: card.id, name: card.name, prompt: card.prompt }
          : { id: '', name: '', prompt: '' };
      }
      async function onCardSaved() {
        await loadDescCards();
      }

      const pickerSeg = computed(() => segments.value.find(x => x.s === picker.s) || null);
      const identityRef = computed(() => (pickerSeg.value && pickerSeg.value.identity_ref) || null);

      async function setImage(p, alsoRedo) {
        const sn = picker.s;
        if (!tid.value || !sn || picker.busy) return;
        picker.busy = true;
        try {
          const r = await API.post(
            '/api/tasks/' + encodeURIComponent(tid.value) + '/review/image', { s: sn, p });
          const seg = segments.value.find(x => x.s === sn);
          if (seg) {
            seg.image = r.image;
            if (!r.kept_locked) seg.prompt_final = '';
            seg.image_ok = true;
            if (r.marked) { seg.mark = true; marks.value[String(sn)] = true; }
          }
          toast(
            t('review.swap_ok', { s: 's' + pad3(sn), p: r.image }),
            r.kept_locked ? t('review.swap_kept_locked') : '',
            r.kept_locked ? 'warn' : 'ok');
          closePicker();
          if (alsoRedo) {
            picked.value = [sn];
            drawer.value = true;
            await doRegen(false, false, pickerAcc.value || '');
          }
        } catch (e) { /* API 已经 toast */ } finally {
          picker.busy = false;
        }
      }

      function swapOnly(p) { return setImage(p, false); }
      function swapAndRedo(p) { return setImage(p, true); }

      async function redoReplaced(seg, acc, targetTid) {
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        if (!seg || isFinished.value) return;
        picked.value = [seg.s];
        await doRegen(false, false, acc || '');
      }

      // 「旧图」旁边的 ▾：选账号 / 本地模型再重生
      async function redoReplacedMenu(ev, seg, targetTid) {
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        if (!seg || isFinished.value) return;
        const acc = await window.RegenAcc.menu(ev.currentTarget, { task: selectedTask.value, segs: [seg] });
        if (acc !== null && acc !== undefined) await redoReplaced(seg, acc, targetTid);
      }

      function copyPrompt(seg) {
        const body = (seg && seg.prompt_final) || '';
        if (!body) { toast(t('review.copied_empty')); return; }
        const go = () => toast(t('review.copied', { n: String(body.length) }), '', 'ok');
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(body).then(go).catch(go);
        } else go();
      }

      async function mark(seg, on, targetTid) {
        const useTid = targetTid || tid.value;
        if (!useTid) return;
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        if (isFinished.value) return;
        await API.post('/api/tasks/' + encodeURIComponent(useTid) + '/marks', { s: seg.s, mark: on });
        seg.mark = on;
        if (on) marks.value[String(seg.s)] = true;
        else delete marks.value[String(seg.s)];
      }

      async function reveal(seg, what, targetTid) {
        const useTid = targetTid || tid.value;
        if (!useTid) return;
        await API.post('/api/tasks/' + encodeURIComponent(useTid) + '/reveal', { s: seg.s, what });
      }

      async function loadExtra() {
        try {
          extra.value = (await API.get('/api/tasks?all=1')).tasks || [];
        } catch (e) { /* API 已经 toast */ }
      }

      async function loadReview() {
        if (!tid.value || !tidReady.value) {
          segments.value = [];
          images.value = [];
          marks.value = {};
          return;
        }
        loading.value = true;
        try {
          const r = await API.get('/api/tasks/' + encodeURIComponent(tid.value) + '/review');
          const slog = r.sync_log || [];
          if (slog.length) toast(t('sync.disk', { detail: slog.join('；') }), '', 'ok');
          const keep = (editing.s && editing.text !== editing.orig)
            ? { s: editing.s, text: editing.text, orig: editing.orig } : null;
          segments.value = r.segments || [];
          images.value = r.images || [];
          marks.value = r.marks || {};
          taskMode.value = r.mode || 'first_frame';
          if (r.verifying) {
            verifying.value = true;
            verifyFade.value = false;
            verifyDone.value = r.verify_done || 0;
            verifyTotal.value = r.verify_total || 0;
          } else {
            verifying.value = false;
            verifyFade.value = false;
          }
          const cached = (store.verify || {})[tid.value];
          if (!r.verifying && cached && cached.results) applyVerified(cached.results);
          if (keep) {
            editing.s = keep.s;
            editing.text = keep.text;
            editing.orig = keep.orig;
          }
          if (!segments.value.some(s => s.s === curS.value) && segments.value.length) {
            curS.value = segments.value[0].s;
          }
          if (jumpS && segments.value.some(s => s.s === jumpS)) {
            const js = jumpS;
            jumpS = 0;
            nextTick(() => focusS(js));
          }
        } catch (e) {
          segments.value = [];
        } finally {
          loading.value = false;
        }
      }

      async function onTid() {
        pauseAll();
        curS.value = 0;
        finishedAt.value = '';
        celebrateOn.value = false;
        Object.keys(open).forEach(k => delete open[k]);
        if (!tid.value || !tidReady.value) {
          segments.value = [];
          return;
        }
        try {
          const r = await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/finish-check');
          if (r.finished) {
            toast(t('review.finished'), '', 'ok');
            await loadExtra();
          }
        } catch (e) { /* 没有文件夹就算了 */ }
        await loadReview();
      }

      function pickDefaultTid() {
        if (tid.value) return;
        const cur = (window.ui && ui.panelTid) || '';
        if (cur) { tid.value = cur; return; }
        if (!readyTasks.value.length) return;
        const u = readyTasks.value.find(x => x.status !== 'finished');
        tid.value = (u || readyTasks.value[0]).id;
      }

      function patchStatus(st) {
        const id = tid.value;
        if (!id) return;
        const bump = (arr) => {
          const hit = arr && arr.find(x => x.id === id);
          if (hit) hit.status = st;
          return hit;
        };
        const inExtra = bump(extra.value);
        const inStore = bump(store.tasks || []);
        if (!inExtra && inStore) {
          extra.value = extra.value.concat([Object.assign({}, inStore, { status: st })]);
        }
      }

      function launchCelebrate(opts) {
        opts = opts || {};
        const id = opts.id || tid.value || '';
        const name = opts.name || (selectedTask.value && selectedTask.value.name) || '';
        celeTitle.value = opts.title || t('review.confetti_title', { id: id });
        celeSub.value = opts.sub != null ? opts.sub : (id ? (id + ' · ' + name) : '');
        const gen = ++celeGen;
        celebrateOn.value = true;
        if (celeTimer) clearTimeout(celeTimer);
        const reduce = prefersReduce();
        const hold = reduce ? window.ReviewCelebrate.REDUCE_HOLD_MS : window.ReviewCelebrate.CELEBRATE_MS;
        celeTimer = setTimeout(() => {
          if (celeGen !== gen) return;
          window.ReviewCelebrate.stopCelebrate();
          celebrateOn.value = false;
        }, hold);
        nextTick(() => {
          if (celeGen !== gen) return;
          const host = document.querySelector('.panel-review .rv-celebrate');
          window.ReviewCelebrate.launchCelebrate({
            host: host,
            title: celeTitle.value,
            sub: celeSub.value,
            reduce: reduce,
            onDone: () => {
              if (celeGen !== gen) return;
              celebrateOn.value = false;
              if (celeTimer) { clearTimeout(celeTimer); celeTimer = null; }
            },
          });
        });
      }

      async function finishTask() {
        if (!tid.value || isFinished.value || finishing.value) return;
        if (!canFinish.value) {
          const tip = finishTip();
          if (tip) toast(tip);
          return;
        }
        finishing.value = true;
        pauseAll();
        try {
          await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/status', { status: 'finished' });
          finishedAt.value = new Date().toLocaleString();
          patchStatus('finished');
          launchCelebrate();
          await loadExtra();
        } finally {
          finishing.value = false;
        }
      }

      async function unfinishTask() {
        if (!tid.value || !isFinished.value || finishing.value) return;
        finishing.value = true;
        try {
          await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/status', { status: 'collected' });
          finishedAt.value = '';
          celebrateOn.value = false;
          window.ReviewCelebrate.stopCelebrate();
          patchStatus('collected');
          await loadExtra();
        } finally {
          finishing.value = false;
        }
      }

      async function runCheck() {
        if (!tid.value || checking.value) return;
        const r = await API.job('check', tid.value);
        waiting.value = (r.job && r.job.id) || '';
      }

      function openRedo() {
        if (allMode.value) {
          const allCands = allCandidates.value;
          if (!allCands.length) {
            toast(t('review.redo_none'));
            return;
          }
          allPicked.value = allCands.map(x => x.key);
          syncContextFromGroup(allCands[0].tid, allCands[0].seg.s);
          picked.value = candidates.value.map(s => s.s);
          const flow1 = (store.accounts || []).find(a => a && a.enabled && a.engine === 'flow' && /flow1|omni/i.test((a.name || '') + ' ' + (a.model || '')));
          regenAcc.value = flow1 ? flow1.id : '';
          drawer.value = true;
          const j = regenJob.value || lastRegen.value;
          if (j) {
            waiting.value = j.id;
            loadJobLog(j.id);
          }
          return;
        }
        if (isFinished.value) return;
        const cand = candidates.value;
        if (!cand.length) { toast(t('review.redo_none')); return; }
        picked.value = cand.map(s => s.s);
        regenAcc.value = '';
        drawer.value = true;
        const j = regenJob.value || lastRegen.value;
        if (j) {
          waiting.value = j.id;
          loadJobLog(j.id);
        }
      }

      function closeRedo() {
        drawer.value = false;
      }

      function togglePick(s) {
        const i = picked.value.indexOf(s);
        if (i >= 0) picked.value = picked.value.filter(x => x !== s);
        else picked.value = picked.value.concat([s]);
      }

      function toggleAllPick(key) {
        const i = allPicked.value.indexOf(key);
        if (i >= 0) allPicked.value = allPicked.value.filter(x => x !== key);
        else allPicked.value = allPicked.value.concat([key]);
      }

      function cascadeCostOf(seg) {
        // 点重生之前就告诉用户：这段主片后面哪些已收接片会被一起打回
        if (!seg || seg.role !== 'anchor') return [];
        const all = segments.value || [];
        const i = all.findIndex(x => x.s === seg.s);
        if (i < 0) return [];
        const out = [];
        for (let j = i + 1; j < all.length; j++) {
          const n = all[j];
          if (n.role === 'anchor') break;
          if (n.role === 'bridge' && (n.status === 'collected' || n.status === 'submitted')) {
            out.push(n.s);
          }
        }
        return out;
      }

      function cascadeCostText(seg) {
        if (taskMode.value !== 'anchor_bridge') return '';
        const ss = cascadeCostOf(seg);
        if (!ss.length) return '';
        return t('review.cascade_cost', { ss: ss.map(n => 's' + pad3(n)).join('、') });
      }

      function isLastSeg(seg) {
        const all = segments.value || [];
        if (!all.length || !seg) return false;
        return all[all.length - 1] && all[all.length - 1].s === seg.s;
      }

      function prevLab(seg) {
        const all = segments.value || [];
        const i = all.findIndex(x => x.s === (seg && seg.s));
        if (i > 0) return 's' + pad3(all[i - 1].s);
        return 's' + pad3((seg && seg.s ? seg.s : 1) - 1);
      }

      function segModeOff() {
        return taskMode.value !== 'anchor_bridge';
      }

      async function setSegMode(seg, mode, targetTid) {
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        if (!tid.value || !seg || isFinished.value) return;
        if (segModeOff()) { toast(t('review.seg.off')); return; }
        try {
          const r = await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/seg-mode', {
            s: seg.s, mode,
          });
          toast(r.hint || '', '', 'ok');
          if (allMode.value) await refreshTaskInAll(tid.value);
          else await loadReview();
        } catch (e) { /* toast */ }
      }

      async function ignoreWait(seg, targetTid) {
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        if (!tid.value || !seg || isFinished.value) return;
        try {
          await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/bench/pass', {
            s: seg.s, force: true,
          });
          toast(t('review.wait.passed', { s: 's' + pad3(seg.s) }), '', 'ok');
          if (allMode.value) await refreshTaskInAll(tid.value);
          else await loadReview();
        } catch (e) { /* toast */ }
      }

      async function openBenchTail(seg, targetTid) {
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        if (!tid.value || !seg) return;
        if (window.flushAll) await window.flushAll();
        ui.pendingTid = tid.value;
        ui.panelTid = tid.value;
        ui.pendingBench = { tid: tid.value, s: seg.s };
        ui.node = 'generate';
      }

      // acc：调用方直接指定的账号（分体按钮 / 菜单选的；'' = 原账号）。null = 没指定，抽屉里按抽屉的选择
      async function doRegen(fill = false, fromDrawer = false, acc = null) {
        const chosenAcc = (acc !== null && acc !== undefined)
          ? acc
          : (fromDrawer ? (regenAcc.value || '') : '');
        if (fromDrawer && allMode.value) {
          if (!allPicked.value.length) { toast(t('review.redo_need')); return; }
          const byTid = new Map();
          for (const key of allPicked.value) {
            const parts = String(key).split('|');
            if (parts.length < 2) continue;
            const kTid = parts[0];
            const kS = Number(parts[1]);
            if (!kTid || !kS) continue;
            if (!byTid.has(kTid)) byTid.set(kTid, []);
            byTid.get(kTid).push(kS);
          }
          if (!byTid.size) { toast(t('review.redo_need')); return; }
          let startedTasks = 0;
          let totalSegs = 0;
          let lastJid = '';
          let firstTid = '';
          for (const [kTid, segList] of byTid.entries()) {
            const liveJobs = (store.jobs || []).filter(j =>
              j.task_id === kTid && (j.kind === 'generate' || j.kind === 'regenerate') &&
              (j.status === 'running' || j.status === 'queued'));
            for (const lj of liveJobs) {
              try { await API.post('/api/jobs/' + encodeURIComponent(lj.id) + '/cancel', {}, { silent: true }); } catch (e) {}
            }
            const args = { segs: segList.slice().sort((a, b) => a - b) };
            if (fill) args.fill_missing = true;
            if (chosenAcc) args.account_id = chosenAcc;
            try {
              const r = await API.job('regenerate', kTid, args);
              if (r && r.job && r.job.id) {
                lastJid = r.job.id;
                if (!firstTid) firstTid = kTid;
              }
              startedTasks += 1;
              totalSegs += segList.length;
            } catch (e) {}
          }
          if (firstTid) syncContextFromGroup(firstTid);
          if (lastJid) {
            waiting.value = lastJid;
            loadJobLog(lastJid);
          }
          if (startedTasks > 0) {
            const accInfo = chosenAcc ? window.RegenAcc.label(window.RegenAcc.accOf(chosenAcc)) : '各任务原账号';
            toast('已提交 ' + startedTasks + ' 个任务共 ' + totalSegs + ' 段至「' + accInfo + '」（长段落自动拆分，拒图自动转本地模型）', '', 'ok');
          }
          return;
        }
        if (!tid.value || isFinished.value) return;
        if (!picked.value.length) { toast(t('review.redo_need')); return; }
        if (fromDrawer && regenerating.value && regenJob.value) {
          try { await API.post('/api/jobs/' + encodeURIComponent(regenJob.value.id) + '/cancel', {}, { silent: true }); } catch (e) {}
        }
        const args = { segs: picked.value.slice() };
        if (fill) args.fill_missing = true;
        if (chosenAcc) args.account_id = chosenAcc;
        if (args.account_id) window.RegenAcc.regenToast(args.account_id, args.segs);
        const r = await API.job('regenerate', tid.value, args);
        waiting.value = (r.job && r.job.id) || '';
        loadJobLog(waiting.value);
      }

      function doRegenFill() { return doRegen(true, true); }

      async function loadGapfill(path) {
        if (!tid.value) return;
        gapLoading.value = true;
        try {
          let url = '/api/tasks/' + encodeURIComponent(tid.value) + '/gapfill';
          if (path) url += '?path=' + encodeURIComponent(path);
          const r = await API.get(url);
          gap.value = {
            report_path: r.report_path || '',
            group: r.group || '',
            clips_seen: r.clips_seen || 0,
            gaps: r.gaps || [],
            rows: r.rows || [],
            counts: r.counts || { real: 0, maybe: 0, note: 0, mismatch: 0 },
            found: !!r.found,
          };
          gapNotesOpen.value = false;
          gapExpand.value = {};
          // 只勾 real / 文件对不上；note 和 maybe 默认空
          gapPicked.value = (gap.value.rows || []).filter(x => x.checked).map(x => x.s);
        } catch (e) {
          gap.value = { report_path: '', group: '', clips_seen: 0, gaps: [], rows: [], counts: { real: 0, maybe: 0, note: 0, mismatch: 0 }, found: false };
          gapPicked.value = [];
          gapNotesOpen.value = false;
        } finally {
          gapLoading.value = false;
        }
      }

      function openGapfill() {
        if (!tid.value || isFinished.value) return;
        drawer.value = false;
        gapOn.value = true;
        loadGapfill();
      }

      function closeGapfill() {
        gapOn.value = false;
      }

      async function pickGapfill() {
        if (!tid.value) return;
        try {
          const r = await API.post('/api/workspace/pick-file');
          const p = r && r.path;
          if (!p) return;
          await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/gapfill/path', { path: p });
          await loadGapfill(p);
        } catch (e) { /* API 已经 toast */ }
      }

      function toggleGapPick(s) {
        const i = gapPicked.value.indexOf(s);
        if (i >= 0) gapPicked.value = gapPicked.value.filter(x => x !== s);
        else gapPicked.value = gapPicked.value.concat([s]);
      }

      function gapRowsOf(tier) {
        return (gap.value.rows || []).filter(r => r.tier === tier);
      }
      const gapReal = computed(() => gapRowsOf('real'));
      const gapMaybe = computed(() => gapRowsOf('maybe'));
      const gapNote = computed(() => gapRowsOf('note'));
      const gapMismatch = computed(() => gapRowsOf('mismatch'));
      const gapCounts = computed(() => gap.value.counts || {
        real: gapReal.value.length,
        maybe: gapMaybe.value.length,
        note: gapNote.value.length,
        mismatch: gapMismatch.value.length,
      });
      const gapHasRows = computed(() =>
        gapReal.value.length + gapMaybe.value.length +
        gapNote.value.length + gapMismatch.value.length > 0);

      function gapRowKey(row) {
        return [row.tier, row.s, row.from, row.to].join('-');
      }
      function toggleGapText(s) {
        const next = Object.assign({}, gapExpand.value);
        next[s] = !next[s];
        gapExpand.value = next;
      }
      function toggleNotes() {
        gapNotesOpen.value = !gapNotesOpen.value;
      }
      function showGapRange(row) {
        return row && row.tier !== 'mismatch' && row.from && row.to;
      }

      function jumpGapSeg(s) {
        curS.value = s;
        filter.value = 'all';
        nextTick(() => {
          const el = document.querySelector('.panel-review .rv-card[data-s="' + s + '"]');
          if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        });
      }

      async function doGapfillRegen(acc) {
        if (!tid.value || isFinished.value) return;
        if (!gapPicked.value.length) { toast(t('review.redo_need')); return; }
        picked.value = gapPicked.value.slice();
        gapOn.value = false;
        await doRegen(false, false, acc || '');
      }
      const gapPickedSegs = computed(() => (segments.value || []).filter(sg => gapPicked.value.includes(sg.s)));

      async function goTrack() {
        // 带着当前任务跳到编排面板重新配图
        if (window.flushAll) await window.flushAll();
        ui.pendingTid = tid.value;
        ui.panelTid = tid.value;
        drawer.value = false;
        ui.node = 'track';
      }

      async function loadJobLog(jid) {
        if (!jid || loadingLogs) return;
        if (store.logs[jid] && store.logs[jid].length) return;
        loadingLogs = true;
        try {
          const txt = await API.get('/api/jobs/' + encodeURIComponent(jid) + '/log');
          if (typeof txt === 'string' && txt) {
            store.logs[jid] = txt.replace(/\r\n/g, '\n').split('\n').filter(x => x !== '');
          }
        } catch (e) { /* 没有日志就算了 */ }
        loadingLogs = false;
      }

      async function loadTails() {
        if (!tid.value) return;
        tails.loading = true;
        try {
          const r = await API.get('/api/tasks/' + encodeURIComponent(tid.value) + '/tails');
          tails.items = r.items || [];
          if (tails.i >= tails.items.length) tails.i = Math.max(0, tails.items.length - 1);
        } catch (e) { /* */ }
        tails.loading = false;
      }

      function openTails(i) {
        tailsOn.value = true;
        tails.showHead = false;
        if (i != null) tails.i = i;
        loadTails();
      }

      function closeTails() { tailsOn.value = false; }

      function tailsCur() { return tails.items[tails.i] || null; }

      function tailsGo(d) {
        const n = tails.items.length;
        if (!n) return;
        tails.i = (tails.i + d + n) % n;
        tails.showHead = false;
      }

      function tailsMedia(it, which) {
        if (!it || !tid.value) return '';
        const file = which === 'head' ? it.head_file : it.file;
        if (!file) return '';
        const rel = String(file).replace(/\\/g, '/').replace(/^\/+/, '');
        return '/media/' + encodeURIComponent(tid.value) + '/' + rel + (it.mtime ? ('?v=' + it.mtime) : '');
      }

      function tailsFlag(it) {
        if (!it) return '';
        if (it.severe) return t('review.tails.severe', { why: it.severe });
        if (it.upstream_changed) return t('review.tails.stale');
        if (it.dist != null && it.dist > 16) return t('review.tails.dist', { n: String(it.dist) });
        return '';
      }

      async function tailsRegen(acc) {
        const it = tailsCur();
        if (!it || !tid.value) return;
        const args = { segs: [it.s] };
        if (acc) { args.account_id = acc; window.RegenAcc.regenToast(acc, args.segs); }
        const r = await API.job('regenerate', tid.value, args);
        tails.jobId = (r.job && r.job.id) || '';
      }
      function tailsSegs() {
        const it = tailsCur();
        return it ? (segments.value || []).filter(sg => sg.s === it.s) : [];
      }

      async function tailsRestart() {
        const it = tailsCur();
        if (!it || !tid.value) return;
        await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/seg-mode', { s: it.s, mode: 'single' });
        toast(t('gen.bench.chain.restart'), '', 'ok');
      }

      async function tailsUpscale() {
        const it = tailsCur();
        if (!it || !tid.value) return;
        const r = await API.job('upscale_tail', tid.value, { s: it.s });
        tails.jobId = (r.job && r.job.id) || '';
      }

      async function tailsMark() {
        const it = tailsCur();
        if (!it || !tid.value) return;
        const seg = segments.value.find(s => s.s === it.s);
        await mark(seg || { s: it.s, mark: false }, !(seg && seg.mark));
      }

      function onKey(ev) {
        if (ui.drawer) return;
        if (tailsOn.value) {
          if (ev.key === 'Escape') { closeTails(); ev.preventDefault(); return; }
          if (ev.key === 'ArrowLeft') { tailsGo(-1); ev.preventDefault(); return; }
          if (ev.key === 'ArrowRight') { tailsGo(1); ev.preventDefault(); return; }
          if (ev.key === 'Home') { tails.i = 0; ev.preventDefault(); return; }
          if (ev.key === 'End') { tails.i = Math.max(0, tails.items.length - 1); ev.preventDefault(); return; }
          if (ev.key === ' ') { tails.showHead = !tails.showHead; ev.preventDefault(); return; }
          return;
        }
        if (typing(ev.target) || typing(document.activeElement)) return;
        if (allMode.value) {
          const curIt = allFlat.value.find(x => x.key === allCur.value)
            || allFlat.value.find(x => hasClip(x.seg))
            || allFlat.value[0] || null;
          if (curIt && window.ReviewScrubber.handleKey(ev, {
            video: allVideo(curIt.key),
            fps: window.ReviewScrubber.fpsOf(curIt.seg),
            active: document.activeElement,
          })) return;
          const k = ev.key;
          const kl = (k || '').toLowerCase();
          if (k === ' ') {
            ev.preventDefault();
            if (curIt) allToggle(curIt);
            return;
          }
          if (kl === 'j' || k === 'ArrowRight' || k === 'ArrowDown') { ev.preventDefault(); allStep(1); return; }
          if (kl === 'k' || k === 'ArrowLeft' || k === 'ArrowUp') { ev.preventDefault(); allStep(-1); return; }
          if (kl === 'x' && curIt) {
            ev.preventDefault();
            if (!curIt.isFinished) mark(curIt.seg, !curIt.seg.mark, curIt.tid);
            return;
          }
          if (kl === 'e' && curIt) {
            ev.preventDefault();
            togglePrompt(curIt.seg, curIt.key, curIt.tid);
            return;
          }
          if (kl === 'c' && curIt) {
            ev.preventDefault();
            copyPrompt(curIt.seg);
            return;
          }
          if (kl === 'm') {
            ev.preventDefault();
            setMuted(!muted.value);
            toast(muted.value ? t('review.muted') : t('review.unmuted'));
            return;
          }
          if (kl === 'v' && curIt && curIt.seg.exists) {
            ev.preventDefault();
            reveal(curIt.seg, 'video', curIt.tid);
            return;
          }
          if (kl === 'p' && curIt && curIt.seg.image) {
            ev.preventDefault();
            reveal(curIt.seg, 'image', curIt.tid);
            return;
          }
          if (kl === 'a') {
            ev.preventDefault();
            setChain(!chain.value);
            toast(chain.value ? t('review.chain_toast_on') : t('review.chain_toast_off'));
            return;
          }
          if (RATES[kl] !== undefined) {
            setRate(RATES[kl]);
            return;
          }
          if (k === 'Escape') {
            if (gapOn.value) { closeGapfill(); return; }
            if (drawer.value) { closeRedo(); return; }
            if (picker.on) { closePicker(); return; }
            if (rvMicro.on) { closeRvMicro(); return; }
            ev.preventDefault();
            setAllMode(false);
            return;
          }
          return;
        }
        if (window.ReviewScrubber.handleKey(ev, {
          video: curVideo(),
          fps: window.ReviewScrubber.fpsOf(curSeg()),
          active: document.activeElement,
        })) return;
        if (ev.key === 'Escape') {
          if (gapOn.value) { closeGapfill(); return; }
          if (drawer.value) { closeRedo(); return; }
          if (picker.on) { closePicker(); return; }
          if (String(ts.qLive || ts.q || '').trim()) { ts.clear(); ev.preventDefault(); return; }
          setChain(false);
          pauseAll();
          return;
        }
        if (ts.onPanelKey(ev)) return;
        const k = (ev.key || '').toLowerCase();
        if (k === 'j' || ev.key === 'ArrowRight') { move(1); ev.preventDefault(); }
        else if (k === 'k' || ev.key === 'ArrowLeft') { move(-1); ev.preventDefault(); }
        else if (ev.key === ' ') { toggle(); ev.preventDefault(); }
        else if (k === 'x') {
          if (isFinished.value) return;
          const s = curSeg();
          if (s) mark(s, !s.mark);
        } else if (k === 'e') {
          const s = curSeg();
          if (s) togglePrompt(s);
        } else if (k === 'm') {
          setMuted(!muted.value);
          toast(muted.value ? t('review.muted') : t('review.unmuted'));
        } else if (k === 'v') {
          const s = curSeg();
          if (s && s.exists) reveal(s, 'video');
        } else if (k === 'p') {
          const s = curSeg();
          if (s && s.image) reveal(s, 'image');
        } else if (k === 'a') {
          setChain(!chain.value);
          toast(chain.value ? t('review.chain_toast_on') : t('review.chain_toast_off'));
        } else if (k === 'c') {
          const s = curSeg();
          if (s) copyPrompt(s);
        } else if (k === 'f') {
          finishTask();
          ev.preventDefault();
        } else if (RATES[k] !== undefined) {
          setRate(RATES[k]);
        }
      }

      let skipTid = false;
      watch(tid, (n, o) => {
        ui.panelTid = n || '';
        if (skipTid) { skipTid = false; return; }
        if (o && editing.s && editing.text !== editing.orig) {
          if (!confirm(t('review.prompt_unsaved'))) {
            skipTid = true;
            tid.value = o;
            return;
          }
        }
        cancelEdit();
        onTid();
      });
      watch(() => store.refreshSeq, () => { if (tid.value) loadReview(); });
      watch(() => ui.pendingTid, (v) => { if (v) takePendingTid(tid, 'review'); });
      watch(() => ui.panelTid, (v) => { if (v && v !== tid.value) tid.value = v; });
      watch(view, (v) => {
        if (!v.length) return;
        if (!v.some(s => s.s === curS.value)) curS.value = v[0].s;
      });
      watch(() => store.jobs.map(j => j.id + ':' + j.status + ':' + j.kind).join('|'), async () => {
        if (tails.jobId) {
          const jt = (store.jobs || []).find(x => x.id === tails.jobId);
          if (jt && (jt.status === 'done' || jt.status === 'failed' || jt.status === 'cancelled' || jt.status === 'lost')) {
            tails.jobId = '';
            if (tailsOn.value) loadTails();
          }
        }
        const id = waiting.value;
        if (!id) return;
        const j = (store.jobs || []).find(x => x.id === id);
        if (!j) return;
        if (j.status === 'done' || j.status === 'failed' || j.status === 'cancelled' || j.status === 'lost') {
          waiting.value = '';
          if (j.id === cardJobId.value) {
            cardJobId.value = '';
            picker.busy = false;
            if (j.status === 'done') {
              const msg = (j.result && j.result.toast) || t('review.swap_ok', {
                s: 's' + pad3(picker.s), p: (j.result && j.result.p) || '',
              });
              toast(msg, '', 'ok');
              if (!picker.autoRedo) closePicker();
            }
          }
          if (allMode.value && j.task_id) await refreshTaskInAll(j.task_id);
          await loadReview();
          if (j.kind === 'check' && j.status === 'done') {
            const n = (j.result && j.result.n_flagged) || nFlag.value;
            const hint = (j.result && j.result.sound) ? t('review.check_sound') : t('review.check_nosound');
            toast(t('review.check_done', { n: String(n) }), hint, '');
          }
        }
      });
      watch(() => {
        const tsk = (store.tasks || []).find(x => x.id === tid.value);
        return tsk ? JSON.stringify(tsk.counts) : '';
      }, () => {
        if (!tid.value) return;
        if (!(regenJob.value || checkJob.value || waiting.value)) return;
        clearTimeout(reloadTimer);
        reloadTimer = setTimeout(() => loadReview(), 400);
      });
      watch(() => {
        const v = (store.verify || {})[tid.value];
        if (!v) return '';
        return [v.running, v.done, v.total, v.at || 0].join(':');
      }, () => {
        const v = (store.verify || {})[tid.value];
        if (!v) return;
        verifyDone.value = v.done || 0;
        verifyTotal.value = v.total || 0;
        if (v.running) {
          verifying.value = true;
          verifyFade.value = false;
        } else if (v.results) {
          applyVerified(v.results);
          if (verifying.value) finishVerifyBar();
        }
      });

      async function flushPrompt() {
        if (!editing.s || editing.text === editing.orig) return;
        const seg = segments.value.find(x => x.s === editing.s);
        if (seg) await savePrompt(seg);
      }

      watch(allIncFinished, () => { if (allMode.value) loadAll(); });

      // ---- Grok / 达芬奇式审片台右键菜单与微指令对话框 ----
      const rvCtx = reactive({ show: false, x: 0, y: 0, seg: null });
      const rvCtxRef = ref(null);
      const rvMicro = reactive({
        on: false, s: 0, instruction: '', accId: '', busy: false,
      });
      const MICRO_CHIPS = [
        '镜头缓慢推进，保持画面稳重',
        '人物保持自然稳定，减少多余手势',
        '眼神坚定柔和，表情更自然生动',
        '电影级柔和布光，强化面部光影层次',
        '保持原构图与服装一致，动作流畅自然',
      ];
      const rvMicroSeg = computed(() => segments.value.find(x => x.s === rvMicro.s) || null);

      function openRvCtx(ev, seg, targetTid) {
        if (!seg) return;
        if (targetTid) syncContextFromGroup(targetTid, seg.s);
        curS.value = seg.s;
        rvCtx.seg = seg;
        rvCtx.tid = targetTid || tid.value;
        rvCtx.key = (targetTid || tid.value) ? allKey(targetTid || tid.value, seg.s) : String(seg.s);
        window.openCtxMenu(ev, rvCtx, rvCtxRef, nextTick, 240, 310);
      }
      function closeRvCtx() {
        rvCtx.show = false;
        rvCtx.seg = null;
      }
      function onRvDocClick(ev) {
        if (rvCtx.show && rvCtxRef.value && !rvCtxRef.value.contains(ev.target)) closeRvCtx();
      }
      function openRvMicro(seg, ev, targetTid) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        closeRvCtx();
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        if (!seg || isFinished.value) return;
        curS.value = seg.s;
        rvMicro.s = seg.s;
        rvMicro.instruction = '';
        rvMicro.accId = ''; // 默认优先走付费账号 (Flow / Grok)
        rvMicro.busy = false;
        rvMicro.on = true;
        nextTick(() => {
          const inp = document.querySelector('.rv-micro-input');
          if (inp && inp.focus) inp.focus();
        });
      }
      function closeRvMicro() {
        rvMicro.on = false;
        rvMicro.busy = false;
      }
      function addMicroChip(chip) {
        const cur = (rvMicro.instruction || '').trim();
        rvMicro.instruction = cur ? (cur + '；' + chip) : chip;
      }
      async function quickRedoSeg(seg, accId, targetTid) {
        closeRvCtx();
        if (targetTid) syncContextFromGroup(targetTid, seg && seg.s);
        if (!seg || isFinished.value) return;
        if (accId === 'STUDIO' && !(store.studio && store.studio.enabled)) {
          try { await API.post('/api/accounts/STUDIO', { enabled: true }); if (window.refreshAccounts) window.refreshAccounts(); } catch (e) {}
        }
        picked.value = [seg.s];
        await doRegen(false, false, accId || '');
      }
      async function submitRvMicro() {
        const seg = rvMicroSeg.value;
        if (!tid.value || !seg || rvMicro.busy || isFinished.value) return;
        rvMicro.busy = true;
        try {
          const note = (rvMicro.instruction || '').trim();
          if (note) {
            const basePrompt = (seg.prompt_final || '').trim();
            const merged = basePrompt
              ? (basePrompt.replace(/\n*【微调要求】[^\n]*/g, '') + '\n【微调要求】' + note)
              : ('【镜头】' + note + '\n' + (seg.text || ''));
            await API.post(
              '/api/tasks/' + encodeURIComponent(tid.value) + '/review/prompt',
              { s: seg.s, text: merged }
            );
            seg.prompt_final = merged;
            seg.prompt_locked = true;
          }
          if (rvMicro.accId === 'STUDIO' && !(store.studio && store.studio.enabled)) {
            try { await API.post('/api/accounts/STUDIO', { enabled: true }); if (window.refreshAccounts) window.refreshAccounts(); } catch (e) {}
          }
          const acc = rvMicro.accId || '';
          closeRvMicro();
          picked.value = [seg.s];
          await doRegen(false, false, acc);
        } catch (e) {
          rvMicro.busy = false;
        }
      }

      onMounted(async () => {
        await loadExtra();
        if (!adoptPanelTid(tid, 'review')) pickDefaultTid();
        document.addEventListener('keydown', onKey);
        document.addEventListener('click', onRvDocClick);
        ui.panelTid = tid.value || '';
        if (window.registerFlush) window.registerFlush('review', flushPrompt);
      });
      onUnmounted(() => {
        document.removeEventListener('keydown', onKey);
        document.removeEventListener('click', onRvDocClick);
        if (window.unregisterFlush) window.unregisterFlush('review');
        if (allObserver) { try { allObserver.disconnect(); } catch (e) {} allObserver = null; }
        window.ReviewScrubber.dispose();
        pauseAll();
        clearTimeout(reloadTimer);
        if (celeTimer) clearTimeout(celeTimer);
        if (verifyFadeTimer) clearTimeout(verifyFadeTimer);
        if (flashTimer) clearTimeout(flashTimer);
        if (cardAccGest && cardAccGest.dispose) cardAccGest.dispose();
        window.ReviewCelebrate.stopCelebrate();
        document.querySelectorAll('canvas.rv-confetti').forEach(c => {
          if (c.parentNode) c.parentNode.removeChild(c);
        });
      });

      const cutoraStitch = reactive({
        on: false,
        mode: 'all', // 'all' | 'range' | 'current'
        rangeText: '',
        autoStart: true,
        autoAi: true,
        busy: false,
      });
      function openCutoraStitchModal() {
        cutoraStitch.mode = allMode.value ? 'all' : (tid.value ? 'all' : 'all');
        cutoraStitch.rangeText = '';
        cutoraStitch.autoStart = true;
        cutoraStitch.autoAi = true;
        cutoraStitch.busy = false;
        cutoraStitch.on = true;
      }
      function closeCutoraStitchModal() {
        cutoraStitch.on = false;
        cutoraStitch.busy = false;
      }
      async function sendToCutoraStitch(quickMode) {
        const mode = quickMode || cutoraStitch.mode || 'all';
        const rangeText = mode === 'range' ? (cutoraStitch.rangeText || '').trim() : '';
        if (mode === 'range' && !rangeText) {
          toast('请输入任务或视频编号范围（例如 T0137-T0162 或 v01-v26）', '', 'err');
          return;
        }
        cutoraStitch.busy = true;
        try {
          const r = await api('POST', '/api/tasks/send-to-cutora-stitch', {
            mode,
            range_text: rangeText,
            tid: tid.value || '',
            auto_start: cutoraStitch.autoStart,
            auto_ai: cutoraStitch.autoAi,
          });
          cutoraStitch.on = false;
          toast(
            `已将 ${r.task_count || 0} 个任务（${r.range_text || '全部任务'}）连同文案送入 Cutora 智能拼接并自动启动！`,
            '若检测到缺词遗漏将自动回传标记，直接切到「全部任务 -> 已标记」即可重生补全',
            'ok'
          );
        } catch (e) {
          toast((e && e.message) || '送入 Cutora 智能拼接失败', (e && e.hint) || '', 'err');
        } finally {
          cutoraStitch.busy = false;
        }
      }

      return {
        cutoraStitch, openCutoraStitchModal, closeCutoraStitchModal, sendToCutoraStitch,
        t, tid, extra, segments, images, filter, cols, rate, muted, chain,
        curS, playingS, flashS, taskMode, open, ts, editing, promptBusy, drawer, gapOn, gap, gapPicked, gapLoading,
        gapNotesOpen, gapExpand, gapReal, gapMaybe, gapNote, gapMismatch, gapCounts, gapHasRows,
        gapRowKey, toggleGapText, toggleNotes, showGapRange,
        picked, loading,
        verifying, verifyFade, verifyDone, verifyTotal,
        celebrateOn, celeTitle, celeSub, finishedAt, isFinished, canFinish,
        readyTasks, pickerTasks, selectedTask, view, candidates, noImgPicked, doRegenFill, goTrack,
        picker, usableImgs, pickerSeg, openPicker, closePicker, swapOnly, swapAndRedo,
        descCards, cardAcc, cardAccState, cardEdit, cardJobId, identityRef,
        clickCard, openCardEdit, onCardSaved, pickCardAcc, accText, identityKindLabel,
        cardAccPinned, cardAccBorrowed, cardAccOptions, cardFollowLabel, shownCardAcc, cardAccGest,
        descCardPreview: window.descCardPreview,
        nFresh, clearFresh, popping, checkJob, regenJob, lastRegen,
        redoLog, logText, checking, regenerating, nMiss, nMark, nFail, nFlag,
        RATE_BTNS, pad3, fmtP, pcolor, lastTake, isBorrowed, isReplaced, redoReplaced, issuesOf, isMissing, isBad,
        candidateWhy, cascadeCostText, isLastSeg, prevLab, segModeOff, setSegMode,
        ignoreWait, openBenchTail,
        mediaUrl, imgOf, durText, flagClass, checkedOk, okText,
        taskLabel, finishTip,
        setRate, setMuted, setChain, onStageClick, onStageEnter, onStageLeave, onTime, onEnded,
        togglePrompt, startEdit, cancelEdit, savePrompt, resetPrompt, onPromptKey, autosizePrompt, segLocked,
        copyPrompt, copyText: window.copyText, mark, reveal, runCheck, openRedo, closeRedo,
        togglePick, doRegen, finishTask, unfinishTask, regenAcc, pickedSegs, pickerAcc, redoReplacedMenu,
        gapPickedSegs, tailsSegs,
        openGapfill, closeGapfill, loadGapfill, pickGapfill, jumpGapSeg, toggleGapPick, doGapfillRegen,
        tailsOn, tails, openTails, closeTails, tailsCur, tailsGo, tailsMedia, tailsFlag,
        tailsRegen, tailsRestart, tailsUpscale, tailsMark,
        allMode, allIncFinished, allLoading, allGroups, allCur, allPlaying, allFlat, allRawFlat,
        allTotalCount, allHave, allNMiss, allNMark, allNFail, allNFlag, allNFresh,
        allPicked, allCandidates, allPickedSegs, toggleAllPick,
        setAllMode, loadAll, allToggle, allOpen, onAllEnded, onAllMeta, onAllStageEnter, allKey,
        imgOfFor, posterUrl, onPosterLoad, isAllVideoMounted, activateAllVideo, syncContextFromGroup, redoAllMarkedAcrossTasks,
        editingDesc, startEditDesc, cancelEditDesc, saveDesc,
        rvCtx, rvCtxRef, rvMicro, MICRO_CHIPS, rvMicroSeg,
        openRvCtx, closeRvCtx, openRvMicro, closeRvMicro, addMicroChip, quickRedoSeg, submitRvMicro,
      };
    },
    template: `
<section class="panel-review">
  <div class="rv-top">
    <h2>{{ t('node.review') }}</h2>
    <span class="tag" v-if="selectedTask && !allMode" :class="selectedTask.engine">{{ selectedTask.engine }}</span>
    <div class="rv-stats" v-if="allMode">
      <span>{{ t('review.all.stats', { t: String(allGroups.length), n: String(allTotalCount), have: String(allHave) }) }}</span>
      <span class="r" v-if="allNMiss">{{ t('review.stats.miss', { n: String(allNMiss) }) }}</span>
      <span class="y" v-if="allNFlag">{{ t('review.stats.flagged', { n: String(allNFlag) }) }}</span>
      <span class="r" v-if="allNMark">{{ t('review.stats.marked', { n: String(allNMark) }) }}</span>
      <span class=" tag" v-if="tid" style="margin-left:6px">当前焦点：{{ tid }}</span>
    </div>
    <div class="rv-stats" v-if="tid && !allMode">
      <span>{{ t('review.stats.total', { n: String(segments.length) }) }}</span>
      <span>{{ t('review.stats.have', { n: String(segments.length - nMiss) }) }}</span>
      <span class="r" v-if="nMiss">{{ t('review.stats.miss', { n: String(nMiss) }) }}</span>
      <span class="y" v-if="nFlag">{{ t('review.stats.flagged', { n: String(nFlag) }) }}</span>
      <span class="r" v-if="nMark">{{ t('review.stats.marked', { n: String(nMark) }) }}</span>
      <span class="fin" v-if="isFinished"><i></i>{{ t('review.stats.finished') }}</span>
      <span class="rv-verify" v-if="verifying || verifyFade" :class="{ fade: verifyFade }">{{ t('review.verifying', { n: String(verifyDone), m: String(verifyTotal) }) }}</span>
    </div>
    <span class="spacer"></span>
    <button class="tog rv-all-tog" :class="allMode ? 'on' : ''" :title="t('review.all.tip')" @click="setAllMode(!allMode)">
      {{ allMode ? t('review.all.on') : t('review.all.off') }}
    </button>
    <button class="btn sm" :class="{on: taskMode==='tail_chain'}" :disabled="!tid"
            @click="openTails()">{{ t('review.tails') }}</button>
    <button class="btn sm" :disabled="!tid || isFinished" :title="t('review.gapfill_tip')" @click="openGapfill">{{ t('review.gapfill') }}</button>
    <button class="btn sm go" title="一键把全部任务或指定范围任务的成品片段与原文案送入 Cutora 智能拼接并自动开始" @click="openCutoraStitchModal">🎬 送入 Cutora 智能拼接</button>
    <div class="seg">
      <button v-for="r in RATE_BTNS" :key="r" :class="{on: rate===r}" @click="setRate(r)">{{ r }}×</button>
    </div>
    <button class="tog" :class="muted ? 'off' : 'on'" @click="setMuted(!muted)">
      {{ muted ? t('review.sound_off') : t('review.sound_on') }}
    </button>
    <button class="tog" :class="chain ? 'on' : ''" @click="setChain(!chain)">
      {{ chain ? t('review.chain_on') : t('review.chain_off') }}
    </button>
    <span class="hint" v-if="rate > 4">{{ t('review.rate_hint') }}</span>
  </div>

  <div class="rv-body">
    <aside class="rv-left">
      <div class="rv-all-side" v-if="allMode">
        <p class="hint">{{ t('review.all.side') }}</p>
        <label class="rv-all-inc"><input type="checkbox" v-model="allIncFinished"> {{ t('review.all.inc_finished') }}</label>
        <button class="btn sm" :disabled="allLoading" @click="loadAll">{{ allLoading ? t('review.all.loading') : t('review.all.refresh') }}</button>
      </div>
      <task-pick v-else v-model="tid" min-status="generating" :tasks="pickerTasks"></task-pick>
      <div class="ts-bar">
        <input :value="ts.qLive" :placeholder="t('search.placeholder')"
               @input="ts.setQ($event.target.value)" @keydown="ts.onBoxKey">
        <span class="ts-count">{{ allMode ? (allFlat.length + '/' + allTotalCount) : ts.countText }}</span>
        <button type="button" class="ts-nav" v-if="!allMode" :disabled="!ts.hits.length" @click="ts.prev">‹</button>
        <button type="button" class="ts-nav" v-if="!allMode" :disabled="!ts.hits.length" @click="ts.next">›</button>
        <button type="button" class="ts-x" v-if="ts.qLive" @click="ts.clear">×</button>
      </div>
      <div class="seg">
        <button data-f="all" :class="{on: filter==='all'}" @click="filter='all'">{{ t('review.filter.all') }}</button>
        <button data-f="bad" :class="{on: filter==='bad'}" @click="filter='bad'">{{ t('review.filter.bad') }}</button>
        <button data-f="mark" :class="{on: filter==='mark'}" @click="filter='mark'">{{ t('review.filter.mark') }}</button>
        <button data-f="miss" :class="{on: filter==='miss'}" @click="filter='miss'">{{ t('review.filter.miss') }}</button>
        <button data-f="fresh" v-if="allMode ? allNFresh : nFresh" class="f-fresh" :class="{on: filter==='fresh'}"
                @click="filter='fresh'">{{ t('review.filter.fresh', { n: String(allMode ? allNFresh : nFresh) }) }}</button>
      </div>
      <div class="seg">
        <button :class="{on: cols===7}" @click="cols=7">{{ t('review.size.s') }}</button>
        <button :class="{on: cols===5}" @click="cols=5">{{ t('review.size.m') }}</button>
        <button :class="{on: cols===3}" @click="cols=3">{{ t('review.size.l') }}</button>
      </div>
      <button class="btn go" :disabled="!tid || checking" @click="runCheck">
        {{ checking ? t('review.checking') : t('review.check') }}
      </button>
      <button class="btn" :disabled="(!tid && !allMode) || isFinished" @click="openRedo">{{ t('review.redo') }}</button>
      <button class="btn" v-if="allMode && (allNMark + allNMiss + allNFail > 0)" @click="redoAllMarkedAcrossTasks">
        ⚡ 批量重生全部任务问题片 ({{ allNMark + allNMiss + allNFail }})
      </button>
      <div class="rv-finish" v-if="!allMode">
        <button class="btn ok" v-if="!isFinished"
                :disabled="!canFinish" :title="finishTip()" @click="finishTask">
          {{ t('review.finish') }}
        </button>
        <button class="btn sm" v-if="isFinished" @click="unfinishTask">
          {{ t('review.unfinish') }}
        </button>
      </div>
      <div class="rv-keys">
        <div><kbd>J</kbd><kbd>K</kbd> {{ allMode ? t('review.all.keys_jk') : t('review.keys.jk') }}</div>
        <div><kbd>空格</kbd> {{ t('review.keys.space') }}</div>
        <div><kbd>X</kbd> {{ t('review.keys.x') }}</div>
        <div><kbd>E</kbd> {{ t('review.keys.e') }}</div>
        <div><kbd>C</kbd> {{ t('review.keys.c') }}</div>
        <div><kbd>M</kbd> {{ t('review.keys.m') }}</div>
        <div><kbd>V</kbd>/<kbd>P</kbd> {{ t('review.keys.vp') }}</div>
        <div><kbd>A</kbd> {{ t('review.keys.a') }}</div>
        <div><kbd>,</kbd><kbd>.</kbd> {{ t('review.keys.frame') }}</div>
        <div><kbd>1</kbd>…<kbd>0</kbd> {{ t('review.keys.rate') }}</div>
        <div><kbd>Esc</kbd> {{ allMode ? t('review.all.keys_esc') : t('review.keys.esc') }}</div>
      </div>
    </aside>

    <div class="rv-scroll rv-all" v-if="allMode">
      <div class="fresh-bar" v-if="allNFresh">
        <span class="dot"></span>
        <span>{{ t('review.fresh_bar', { n: String(allNFresh) }) }}</span>
        <button class="btn ghost sm" @click="clearFresh(0)">{{ t('review.fresh_clear') }}</button>
      </div>
      <div class="empty" v-if="allLoading && !allGroups.length">{{ t('review.all.loading') }}</div>
      <div class="empty" v-else-if="!allGroups.length">{{ t('review.all.empty') }}</div>
      <div class="rv-grid rv-all-grid" v-if="allGroups.length" :style="{gridTemplateColumns: 'repeat(' + cols + ',minmax(0,1fr))'}">
        <div v-for="it in allFlat" :key="it.key"
             class="rv-card rv-all-card" :data-key="it.key" :data-s="it.seg.s"
             :class="{ cur: allCur===it.key, marked: it.seg.mark, playing: allPlaying===it.key, open: open[it.key], alt: it.gi % 2 === 1, first: it.first }"
             @contextmenu.prevent="openRvCtx($event, it.seg, it.tid)">
          <div class="stage" :data-fps="it.seg.fps || 24" :title="t('review.all.card_tip')"
               @click="allToggle(it)"
               @dblclick="allOpen(it.tid, it.seg.s)"
               @pointerenter="onAllStageEnter($event, it)"
               @pointerleave="onStageLeave($event)">
            <img v-if="it.seg.exists && lastTake(it.seg) && posterUrl(it.tid, it.seg)"
                 class="rv-stage-poster"
                 :src="posterUrl(it.tid, it.seg)"
                 loading="lazy"
                 @load="onPosterLoad"
                 alt="">
            <video v-if="it.seg.exists && lastTake(it.seg) && isAllVideoMounted(it.key)"
                   :key="'all-' + it.key + '-' + (it.seg.mtime || 0)"
                   :src="mediaUrl(it.tid, lastTake(it.seg).file, it.seg.mtime || (lastTake(it.seg) && lastTake(it.seg).mtime))"
                   :poster="posterUrl(it.tid, it.seg)"
                   preload="metadata" playsinline
                   :muted="muted"
                   @loadedmetadata="onAllMeta"
                   @timeupdate="onTime"
                   @ended="onAllEnded(it)"></video>
            <div class="gone" v-else-if="!it.seg.exists || !lastTake(it.seg)"><b>{{ t('review.missing') }}</b>s{{ pad3(it.seg.s) }}</div>
            <span class="idx">{{ it.tid }} · {{ pad3(it.seg.s) }}</span>
            <span class="role-mark" v-if="it.mode==='anchor_bridge' && it.seg.role==='anchor'"
                  :title="t('review.role.anchor')">●</span>
            <span class="role-mark bridge" v-if="it.mode==='anchor_bridge' && it.seg.role==='bridge'"
                  :title="t('review.role.bridge')">◇</span>
            <span class="fresh-tag" v-if="it.seg.fresh" :class="{pop: popping[it.key] || popping[it.seg.s]}"
                  :title="t('review.fresh_tip', { t: it.seg.fresh })"
                  @click.stop="clearFresh(it.seg.s, it.tid)">{{ t('review.fresh') }}</span>
            <span class="borrow-tag" v-if="isBorrowed(it.seg)"
                  :title="t('review.borrowed_tip')">{{ t('review.borrowed') }}</span>
            <span class="rate">{{ rate }}×</span>
            <div class="bar-shade"></div>
            <div class="bar">
              <i></i>
              <em class="bar-knob"></em>
              <span class="bar-tip"></span>
            </div>
            <span class="bar-clock">0:00 / 0:00</span>
          </div>
          <div class="meta">
            <div class="line1">
              <span class="lb">
                <i class="swatch" :style="{background: pcolor(it.seg.image)}"></i>{{ fmtP(it.seg.image) || '—' }}
                <span class="old-img" v-if="isReplaced(it.seg)"
                      :title="t('review.old_img_tip') + ' · ' + t('review.old_img_click')"
                      @click.stop="redoReplaced(it.seg, '', it.tid)">{{ t('review.old_img') }}</span><span
                      class="old-img ra-mini" v-if="isReplaced(it.seg)" :title="t('regen.acc.more')"
                      @click.stop="redoReplacedMenu($event, it.seg, it.tid)">▾</span>
              </span>
              <button type="button" class="rv-all-task-tag" :title="t('review.all.open')" @click.stop="allOpen(it.tid, it.seg.s)">
                {{ it.name || it.tid }}<span v-if="it.status==='finished'"> ✓</span> · {{ t('review.all.open') }}
              </button>
              <span class="rt">{{ durText(it.seg) }}</span>
            </div>
            <div class="bridge-line" v-if="it.mode==='anchor_bridge' && it.seg.role==='bridge' && it.seg.bridge">
              {{ t('review.bridge_link', {
                a: 's' + pad3(it.seg.bridge.from_seg || (it.seg.s-1)),
                p: it.seg.bridge.to_image || '—'
              }) }}
            </div>
            <div class="wait-why" v-if="isMissing(it.seg) && it.seg.wait_why">
              <p>{{ it.seg.wait_why }}</p>
              <div class="wait-acts">
                <button class="btn sm" :disabled="it.isFinished" @click.stop="ignoreWait(it.seg, it.tid)">{{ t('review.wait.ignore') }}</button>
                <button class="btn sm" :disabled="it.isFinished" @click.stop="openBenchTail(it.seg, it.tid)">{{ t('review.wait.retake') }}</button>
              </div>
            </div>
            <div class="flags">
              <span class="flag mk" v-if="it.seg.mark">{{ t('review.flagged') }}</span>
              <span class="flag st" :class="'st-' + (it.seg.status||'')">{{ t('gen.seg.' + (it.seg.status||'planned')) }}</span>
              <span class="flag lock" v-if="it.seg.prompt_locked">🔒 {{ t('review.prompt_locked') }}</span>
              <span class="flag" v-for="(iss, ii) in issuesOf(it.seg)" :key="ii" :class="flagClass(iss)">{{ iss.text }}</span>
              <span class="flag miss" v-if="isMissing(it.seg) && !issuesOf(it.seg).length">{{ t('review.missing') }}</span>
              <span class="flag ok" v-if="checkedOk(it.seg)">{{ okText(it.seg) }}</span>
              <button class="swap-chip" :disabled="it.isFinished" @click.stop="openPicker(it.seg, it.tid)">{{ t('review.swap') }}</button>
            </div>
            <div class="tools">
              <button class="mk" :class="{on: it.seg.mark}" :disabled="it.isFinished" @click.stop="mark(it.seg, !it.seg.mark, it.tid)">
                {{ it.seg.mark ? t('review.unmark') : t('review.mark') }}
              </button>
              <button class="rv-micro-btn" :disabled="it.isFinished" title="打开 Grok 式微指令改片与重生对话框" @click.stop="openRvMicro(it.seg, $event, it.tid)">💬 微调</button>
              <button @click.stop="togglePrompt(it.seg, it.key, it.tid)">{{ t('review.prompt') }}</button>
              <button :disabled="!it.seg.image || it.isFinished" @click.stop="startEditDesc(it.seg, it.key, it.tid)">✏️ 描述语</button>
              <button :disabled="!it.seg.exists" @click.stop="reveal(it.seg, 'video', it.tid)">{{ t('review.video') }}</button>
              <button :disabled="!it.seg.image" @click.stop="reveal(it.seg, 'image', it.tid)">{{ t('review.image') }}</button>
            </div>
          </div>
          <div class="prompt">
            <div class="pblock final">
              <h5>
                <span>{{ t('review.prompt_final') }}</span>
                <span class="src" v-if="editing.key===it.key || it.seg.prompt_final">{{ t('review.n_chars', { n: String((editing.key===it.key ? editing.text : (it.seg.prompt_final || '')).length) }) }}</span>
                <span class="lock-badge" v-if="it.seg.prompt_locked">🔒 {{ t('review.prompt_lock_badge') }}</span>
                <span class="ops" v-if="editing.key===it.key">
                  <button class="cp" :disabled="promptBusy" @click.stop="savePrompt(it.seg, it.tid)">{{ t('review.prompt_save') }}</button>
                  <button class="cp" @click.stop="cancelEdit">{{ t('review.prompt_cancel') }}</button>
                  <button class="cp" :disabled="promptBusy" @click.stop="resetPrompt(it.seg, it.tid)">{{ t('review.prompt_reset') }}</button>
                  <span class="dirty" v-if="editing.text !== editing.orig">● {{ t('review.prompt_dirty') }}</span>
                </span>
                <span class="ops" v-else>
                  <button class="cp" @click.stop="copyPrompt(it.seg)">{{ t('review.copy') }}</button>
                  <button class="cp" :disabled="it.isFinished" @click.stop="startEdit(it.seg, it.key, it.tid)">{{ t('review.prompt_edit') }}</button>
                </span>
              </h5>
              <textarea v-if="editing.key===it.key" class="pedit" rows="8"
                        v-model="editing.text"
                        @input="autosizePrompt($event.target)"
                        @keydown="onPromptKey($event, it.seg, it.tid)"></textarea>
              <p v-else>{{ it.seg.prompt_final || t('review.prompt_empty') }}</p>
            </div>
            <div class="pblock parts" v-if="it.seg.image">
              <h5>
                <span>{{ t('review.desc') }} ({{ fmtP(it.seg.image) }})</span>
                <span class="ops" v-if="editingDesc.key===String(it.key)">
                  <button class="cp" :disabled="editingDesc.busy" @click.stop="saveDesc(it.seg, it.tid)">{{ t('review.prompt_save') }}</button>
                  <button class="cp" @click.stop="cancelEditDesc">{{ t('review.prompt_cancel') }}</button>
                </span>
                <span class="ops" v-else>
                  <button class="cp" :disabled="it.isFinished" @click.stop="startEditDesc(it.seg, it.key, it.tid)">✏️ 编辑描述语</button>
                </span>
              </h5>
              <template v-if="editingDesc.key===String(it.key)">
                <textarea class="pedit pedit-desc" rows="4" v-model="editingDesc.desc"
                          placeholder="输入画面人物与场景描述语…"
                          @input="autosizePrompt($event.target)"></textarea>
                <input class="pedit-line" v-model="editingDesc.voice" placeholder="【音色】例如：非洲裔|女|中年|中低音|非洲口音">
              </template>
              <template v-else>
                <p>{{ imgOfFor(it.tid, it.seg.image).desc || '—' }}</p>
                <p v-if="imgOfFor(it.tid, it.seg.image).voice" class="muted" style="margin-top:4px">{{ imgOfFor(it.tid, it.seg.image).voice }}</p>
              </template>
            </div>
            <div class="pblock en parts" v-if="it.seg.text">
              <h5>{{ t('review.text') }}</h5>
              <p>{{ it.seg.text }}</p>
            </div>
            <div class="pblock parts" v-if="it.seg.shot">
              <h5>{{ t('review.shot') }}</h5>
              <p>{{ it.seg.shot }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="rv-scroll" v-else :class="{ done: isFinished }">
      <div class="rv-done-layer" v-if="isFinished">
        <div class="rv-done-chip">
          <b>{{ t('review.finished_badge') }}</b>
          <span class="id">{{ tid }}</span>
          <span class="at" v-if="finishedAt">{{ t('review.finished_at', { t: finishedAt }) }}</span>
        </div>
      </div>
      <div class="fresh-bar" v-if="nFresh">
        <span class="dot"></span>
        <span>{{ t('review.fresh_bar', { n: String(nFresh) }) }}</span>
        <button class="btn ghost sm" @click="clearFresh(0)">{{ t('review.fresh_clear') }}</button>
      </div>
      <div class="empty" v-if="!readyTasks.length">{{ t('review.no_task') }}</div>
      <div class="empty" v-else-if="!tid">{{ t('review.need_task') }}</div>
      <div class="rv-grid" v-else :style="{gridTemplateColumns: 'repeat(' + cols + ',minmax(0,1fr))'}">
        <div v-for="seg in view" :key="seg.s"
             class="rv-card"
             :data-s="seg.s"
             :class="{ cur: seg.s===curS, marked: seg.mark, playing: seg.s===playingS, open: open[seg.s], 'ts-hit': ts.isHit(seg.s), 'ts-flash': ts.isFlash(seg.s) }"
             @contextmenu.prevent="openRvCtx($event, seg)">
          <div class="stage" :data-fps="seg.fps || 24"
               @click="onStageClick(seg)"
               @pointerenter="onStageEnter($event, seg)"
               @pointerleave="onStageLeave($event, seg)">
            <video v-if="seg.exists && lastTake(seg)"
                   :key="tid + '-v-' + seg.s + '-' + (seg.mtime || 0)"
                   :data-s="seg.s"
                   :src="mediaUrl(tid, lastTake(seg).file, seg.mtime || (lastTake(seg) && lastTake(seg).mtime))"
                   :poster="posterUrl(tid, seg)"
                   preload="metadata" playsinline
                   :muted="muted"
                   @timeupdate="onTime"
                   @ended="onEnded(seg)"></video>
            <div class="gone" v-else><b>{{ t('review.missing') }}</b>s{{ pad3(seg.s) }}</div>
            <span class="idx">{{ pad3(seg.s) }}</span>
            <span class="role-mark" v-if="taskMode==='anchor_bridge' && seg.role==='anchor'"
                  :title="t('review.role.anchor')">●</span>
            <span class="role-mark bridge" v-if="taskMode==='anchor_bridge' && seg.role==='bridge'"
                  :title="t('review.role.bridge')">◇</span>
            <span class="bridge-flash" v-if="flashS===seg.s">{{ t('review.bridge_flash') }}</span>
            <span class="fresh-tag" v-if="seg.fresh" :class="{pop: popping[seg.s]}"
                  :title="t('review.fresh_tip', { t: seg.fresh })"
                  @click.stop="clearFresh(seg.s)">{{ t('review.fresh') }}</span>
            <span class="borrow-tag" v-if="isBorrowed(seg)"
                  :title="t('review.borrowed_tip')">{{ t('review.borrowed') }}</span>
            <span class="rate">{{ rate }}×</span>
            <div class="bar-shade"></div>
            <div class="bar">
              <i></i>
              <em class="bar-knob"></em>
              <span class="bar-tip"></span>
            </div>
            <span class="bar-clock">0:00 / 0:00</span>
          </div>
          <div class="meta">
            <div class="line1">
              <span class="lb">
                <i class="swatch" :style="{background: pcolor(seg.image)}"></i>{{ fmtP(seg.image) || '—' }}
                <span class="old-img" v-if="isReplaced(seg)"
                      :title="t('review.old_img_tip') + ' · ' + t('review.old_img_click')"
                      @click.stop="redoReplaced(seg)">{{ t('review.old_img') }}</span><span
                      class="old-img ra-mini" v-if="isReplaced(seg)" :title="t('regen.acc.more')"
                      @click.stop="redoReplacedMenu($event, seg)">▾</span>
              </span>
              <span class="ts-badge" v-if="ts.isHit(seg.s)">{{ ts.hits.indexOf(seg.s) + 1 }}</span>
              <span class="rt">{{ durText(seg) }}</span>
            </div>
            <div class="bridge-line" v-if="taskMode==='anchor_bridge' && seg.role==='bridge' && seg.bridge">
              {{ t('review.bridge_link', {
                a: 's' + pad3(seg.bridge.from_seg || (seg.s-1)),
                p: seg.bridge.to_image || '—'
              }) }}
            </div>
            <div class="wait-why" v-if="isMissing(seg) && seg.wait_why">
              <p>{{ seg.wait_why }}</p>
              <div class="wait-acts">
                <button class="btn sm" :disabled="isFinished" @click.stop="ignoreWait(seg)">{{ t('review.wait.ignore') }}</button>
                <button class="btn sm" :disabled="isFinished" @click.stop="openBenchTail(seg)">{{ t('review.wait.retake') }}</button>
              </div>
            </div>
            <div class="cascade-cost" v-if="cascadeCostText(seg)">{{ cascadeCostText(seg) }}</div>
            <div class="seg-mode" v-if="taskMode==='anchor_bridge'">
              <template v-if="seg.role==='bridge'">
                <button class="btn sm" :disabled="isFinished" @click.stop="setSegMode(seg, 'single')">{{ t('review.seg.to_single') }}</button>
                <small>{{ t('review.seg.to_single_hint', { p: fmtP(seg.image) || 'p—' }) }}</small>
              </template>
              <template v-else-if="!isLastSeg(seg)">
                <button class="btn sm" :disabled="isFinished" @click.stop="setSegMode(seg, 'bridge')">{{ t('review.seg.to_bridge') }}</button>
                <small>{{ t('review.seg.to_bridge_hint', { s: prevLab(seg) }) }}</small>
              </template>
            </div>
            <div class="seg-mode off" v-else-if="selectedTask" :title="t('review.seg.off')">
              <button class="btn sm" disabled :title="t('review.seg.off')">{{ t('review.role.anchor') }}</button>
            </div>
            <div class="flags">
              <span class="flag mk" v-if="seg.mark">{{ t('review.flagged') }}</span>
              <span class="flag st" :class="'st-' + (seg.status||'')">{{ t('gen.seg.' + (seg.status||'planned')) }}</span>
              <span class="flag lock" v-if="seg.prompt_locked">🔒 {{ t('review.prompt_locked') }}</span>
              <span class="flag" v-for="(iss, ii) in issuesOf(seg)" :key="ii" :class="flagClass(iss)">{{ iss.text }}</span>
              <span class="flag miss" v-if="isMissing(seg) && !issuesOf(seg).length">{{ t('review.missing') }}</span>
              <span class="flag ok" v-if="checkedOk(seg)">{{ okText(seg) }}</span>
              <span class="flag info" v-if="taskMode==='anchor_bridge' && seg.role==='bridge'"
                    :title="t('review.bridge_quality')">ⓘ</span>
              <button class="swap-chip" :disabled="isFinished" @click.stop="openPicker(seg)">{{ t('review.swap') }}</button>
            </div>
            <div class="tools">
              <button class="mk" :class="{on: seg.mark}" :disabled="isFinished" @click.stop="mark(seg, !seg.mark)">
                {{ seg.mark ? t('review.unmark') : t('review.mark') }}
              </button>
              <button class="rv-micro-btn" :disabled="isFinished" title="打开 Grok 式微指令改片与引擎切换对话框" @click.stop="openRvMicro(seg, $event)">💬 微调</button>
              <button @click.stop="togglePrompt(seg)">{{ t('review.prompt') }}</button>
              <button :disabled="!seg.image || isFinished" @click.stop="startEditDesc(seg, seg.s, tid)">✏️ 描述语</button>
              <button :disabled="!seg.exists" @click.stop="reveal(seg, 'video')">{{ t('review.video') }}</button>
              <button :disabled="!seg.image" @click.stop="reveal(seg, 'image')">{{ t('review.image') }}</button>
            </div>
          </div>
          <div class="prompt">
            <div class="pblock final">
              <h5>
                <span>{{ t('review.prompt_final') }}</span>
                <span class="src" v-if="editing.s===seg.s || seg.prompt_final">{{ t('review.n_chars', { n: String((editing.s===seg.s ? editing.text : (seg.prompt_final || '')).length) }) }}</span>
                <span class="lock-badge" v-if="seg.prompt_locked">🔒 {{ t('review.prompt_lock_badge') }}</span>
                <span class="ops" v-if="editing.s===seg.s">
                  <button class="cp" :disabled="promptBusy" @click.stop="savePrompt(seg)">{{ t('review.prompt_save') }}</button>
                  <button class="cp" @click.stop="cancelEdit">{{ t('review.prompt_cancel') }}</button>
                  <button class="cp" :disabled="promptBusy" @click.stop="resetPrompt(seg)">{{ t('review.prompt_reset') }}</button>
                  <span class="dirty" v-if="editing.text !== editing.orig">● {{ t('review.prompt_dirty') }}</span>
                </span>
                <span class="ops" v-else>
                  <button class="cp" @click.stop="copyPrompt(seg)">{{ t('review.copy') }}</button>
                  <button class="cp" :disabled="isFinished" @click.stop="startEdit(seg)">{{ t('review.prompt_edit') }}</button>
                </span>
              </h5>
              <textarea v-if="editing.s===seg.s" class="pedit" rows="8"
                        v-model="editing.text"
                        @input="autosizePrompt($event.target)"
                        @keydown="onPromptKey($event, seg)"></textarea>
              <p v-else-if="ts.q && seg.prompt_final" v-html="ts.highlight(seg.prompt_final)"></p>
              <p v-else>{{ seg.prompt_final || t('review.prompt_empty') }}</p>
            </div>
            <div class="pblock en parts" v-if="seg.text">
              <h5>{{ t('review.text') }}</h5>
              <p v-if="ts.q" v-html="ts.highlight(seg.text)"></p>
              <p v-else>{{ seg.text }}</p>
            </div>
            <div class="pblock parts" v-if="seg.shot">
              <h5>{{ t('review.shot') }}</h5>
              <p>{{ seg.shot }}</p>
            </div>
            <div class="pblock parts" v-if="seg.image">
              <h5>
                <span>{{ t('review.desc') }} ({{ fmtP(seg.image) }})</span>
                <span class="ops" v-if="editingDesc.key===String(seg.s)">
                  <button class="cp" :disabled="editingDesc.busy" @click.stop="saveDesc(seg, tid)">{{ t('review.prompt_save') }}</button>
                  <button class="cp" @click.stop="cancelEditDesc">{{ t('review.prompt_cancel') }}</button>
                </span>
                <span class="ops" v-else>
                  <button class="cp" :disabled="isFinished" @click.stop="startEditDesc(seg, seg.s, tid)">✏️ 编辑描述语</button>
                </span>
              </h5>
              <template v-if="editingDesc.key===String(seg.s)">
                <textarea class="pedit pedit-desc" rows="4" v-model="editingDesc.desc"
                          placeholder="输入画面人物与场景描述语…"
                          @input="autosizePrompt($event.target)"></textarea>
                <input class="pedit-line" v-model="editingDesc.voice" placeholder="【音色】例如：非洲裔|女|中年|中低音|非洲口音">
              </template>
              <template v-else>
                <p>{{ imgOf(seg.image).desc || '—' }}</p>
                <p v-if="imgOf(seg.image).voice" class="muted" style="margin-top:4px">{{ imgOf(seg.image).voice }}</p>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="rv-celebrate" v-if="celebrateOn">
    <div class="rv-celebrate-inner">
      <div class="rv-celebrate-rings">
        <i class="rv-celebrate-ring"></i>
        <i class="rv-celebrate-ring delay"></i>
      </div>
      <div class="rv-celebrate-mark">✓</div>
      <div class="rv-celebrate-ttl">{{ celeTitle }}</div>
      <div class="rv-celebrate-sub" v-if="celeSub">{{ celeSub }}</div>
    </div>
  </div>

  <div class="rv-veil" v-if="picker.on" @click="closePicker"></div>
  <aside class="rv-picker" v-if="picker.on">
    <div class="dhead">
      <h3>{{ t('review.swap_title', { s: 's' + pad3(picker.s), p: (pickerSeg && pickerSeg.image) || '—' }) }}</h3>
      <button class="x" @click="closePicker">×</button>
    </div>
    <div class="pk-cards-wrap">
      <div class="pk-cards-hd">
        <span>{{ t('review.cards.head') }}</span>
        <span class="muted">（{{ identityKindLabel(identityRef && identityRef.kind) }}
          <span v-if="shownCardAcc"> · {{ t('review.cards.acc') }} {{ accText(shownCardAcc) }}</span>）</span>
        <span class="pk-idref" v-if="identityRef">
          <img :src="mediaUrl(tid, identityRef.file, identityRef.mtime)" :alt="identityRef.ref || ''">
          <em>{{ identityRef.ref || '' }}</em>
        </span>
        <span class="split-btn accpick-host pk-acc">
          <button class="btn sm split-caret pg-trig" type="button"
                  :class="{ dragging: cardAccGest.dragging, bump: cardAccGest.bump,
                            'pg-in-down': cardAccGest.slide==='down', 'pg-in-up': cardAccGest.slide==='up' }"
                  :disabled="picker.busy"
                  @wheel="cardAccGest.onWheel"
                  @pointerdown="cardAccGest.onPointerDown"
                  @pointermove="cardAccGest.onPointerMove"
                  @pointerup="cardAccGest.onPointerUp"
                  @pointercancel="cardAccGest.onPointerCancel"
                  @lostpointercapture="cardAccGest.onLostPointerCapture"
                  @click.stop="cardAccGest.onClick"
                  @contextmenu="cardAccGest.onContextMenu">
            ▾
            <span class="pg-ew" v-if="cardAccGest.dragging">◂ ▸</span>
            <span class="pg-pos" v-if="cardAccGest.posHint">{{ cardAccGest.posHint }}</span>
          </button>
          <div class="accpick-menu" v-if="picker.accOpen" @click.stop>
            <div class="accpick-item" :class="{on: !cardAccPinned}" @click="pickCardAcc('')">
              {{ cardAccPinned ? '○ ' : '● ' }}{{ cardFollowLabel }}
            </div>
            <div class="accpick-borrowed" v-if="cardAccBorrowed">{{ t('track.acc.borrowed') }}</div>
            <div class="accpick-sep"></div>
            <div v-for="a in cardAccOptions" :key="a.id" class="accpick-item"
                 :class="{on: cardAccPinned && cardAcc && cardAcc.id===a.id}"
                 :title="a.alive ? '' : t('track.acc.offline_tip')"
                 @click="pickCardAcc(a.id)">
              <span>{{ a.engine }} · {{ a.name }}</span>
              <span class="acc-alive">{{ a.alive ? t('track.acc.online') : t('track.acc.offline') }}</span>
            </div>
          </div>
        </span>
      </div>
      <div class="pk-cards">
        <button v-for="c in descCards" :key="c.id" type="button" class="pk-card"
                :disabled="picker.busy" :title="c.prompt"
                @click="clickCard(c)">
          <b>{{ c.name }}</b>
          <span class="muted">{{ descCardPreview(c.prompt) }}</span>
          <em class="pk-card-edit" @click.stop="openCardEdit(c)">{{ t('review.cards.edit') }}</em>
        </button>
        <button type="button" class="pk-card pk-card-new" :disabled="picker.busy"
                @click="openCardEdit(null)">
          <b>+</b>
          <span>{{ t('review.cards.new') }}</span>
        </button>
      </div>
      <label class="pk-auto">
        <input type="checkbox" v-model="picker.autoRedo"> {{ t('review.cards.auto') }}
      </label>
    </div>
    <regen-acc-pick class="pk-acc" :task="selectedTask" v-model="pickerAcc"
                    :segs="pickerSeg ? [pickerSeg] : []" :title="t('regen.acc.title_picker')"
                    :disabled="picker.busy"></regen-acc-pick>
    <p class="dhint">{{ t('review.swap_hint') }}</p>
    <div class="pk-grid">
      <button v-for="im in usableImgs" :key="im.p" type="button" class="pk"
              :class="{cur: pickerSeg && fmtP(pickerSeg.image) === fmtP(im.p)}"
              :disabled="picker.busy"
              :title="t('review.swap_only') + ' → ' + fmtP(im.p)"
              @click="swapOnly(im.p)">
        <img :src="mediaUrl(tid, im.file, im.mtime)" :alt="im.p">
        <span class="pk-p"><i class="swatch" :style="{background: pcolor(im.p)}"></i>{{ fmtP(im.p) }}</span>
        <span class="pk-redo" :title="t('review.swap_redo')"
              @click.stop="swapAndRedo(im.p)">{{ t('review.swap_redo') }}</span>
      </button>
      <div class="empty" v-if="!usableImgs.length">{{ t('review.swap_none') }}</div>
    </div>
    <desc-card-editor v-if="cardEdit" :card="cardEdit" @close="cardEdit=null" @saved="onCardSaved"></desc-card-editor>
  </aside>

  <div class="rv-veil" v-if="drawer" @click="closeRedo"></div>
  <aside class="rv-drawer" v-if="drawer">
    <div class="dhead">
      <h3 v-if="allMode">跨任务批量重生（已选 {{ allPicked.length }} / 候选 {{ allCandidates.length }} 段）</h3>
      <h3 v-else>{{ t('review.redo_title', { a: String(segments.length), b: String(candidates.length) }) }}</h3>
      <button class="x" @click="closeRedo">×</button>
    </div>
    <div class="dbody">
      <p class="dhint">{{ allMode ? '已汇总全部任务中的标记/缺片/失败段落。选择 Flow1 (Omni) 等账号后点击底部按钮即可一键批量重跑（超过 8 秒的长段落将自动拆分为两段 ≤8s，若图片被 Flow 拒绝将自动转入本地模型 LTX 生成）。' : t('review.redo_hint') }}</p>
      <div class="dsec" v-if="allMode">
        <h4>已选要重生的段（{{ allPicked.length }}）</h4>
        <div class="picks">
          <button v-for="it in allCandidates" :key="it.key"
                  class="pick" :class="{on: allPicked.includes(it.key)}"
                  @click="toggleAllPick(it.key)">
            {{ it.tid }}·s{{ pad3(it.seg.s) }}<span class="why">{{ t('review.why.' + candidateWhy(it.seg)) }} · {{ it.seg.duration || 8 }}s</span>
            <span v-if="it.seg.prompt_locked" :title="t('review.prompt_locked')">🔒</span>
          </button>
        </div>
      </div>
      <div class="dsec" v-else>
        <h4>{{ t('review.picked', { n: String(picked.length) }) }}</h4>
        <div class="picks">
          <button v-for="seg in candidates" :key="seg.s"
                  class="pick" :class="{on: picked.includes(seg.s)}"
                  @click="togglePick(seg.s)">
            s{{ pad3(seg.s) }}<span class="why">{{ t('review.why.' + candidateWhy(seg)) }}</span>
            <span v-if="seg.prompt_locked" :title="t('review.prompt_locked')">🔒</span>
          </button>
        </div>
      </div>
      <div class="dsec">
        <regen-acc-pick :task="allMode ? null : selectedTask" v-model="regenAcc" :segs="allMode ? allPickedSegs : pickedSegs"
                        :disabled="!allMode && isFinished"></regen-acc-pick>
      </div>
      <div class="dsec">
        <h4 class="hrow">{{ t('review.log') }}
          <button class="btn ghost xs" :disabled="!logText" @click="copyText(logText, lastRegen ? lastRegen.id : '')">{{ t('log.copy') }}</button>
        </h4>
        <div class="jbar" v-if="lastRegen" :class="lastRegen.status==='running' ? 'run' : lastRegen.status">
          <span class="dot"></span>
          <span>{{ lastRegen.kind }} · {{ lastRegen.status }} · {{ lastRegen.id }}</span>
        </div>
        <div class="rv-log">{{ logText || t('review.log_empty') }}</div>
      </div>
    </div>
    <div class="dnoimg" v-if="!allMode && noImgPicked.length">
      <p>{{ t('review.redo_noimg', { segs: noImgPicked.map(x => 's' + pad3(x)).join('、') }) }}</p>
      <p class="sub">{{ t('review.redo_noimg_hint') }}</p>
    </div>
    <div class="dfoot">
      <template v-if="!allMode && noImgPicked.length">
        <button class="btn ghost" :disabled="isFinished" @click="goTrack">
          {{ t('review.redo_go_track') }}
        </button>
        <button class="btn go" :disabled="!picked.length || isFinished" @click="doRegenFill">
          {{ t('review.redo_go_fill') }}
        </button>
      </template>
      <button v-else class="btn go" :disabled="(allMode ? !allPicked.length : !picked.length) || (!allMode && isFinished)" @click="doRegen(false, true)">
        {{ allMode ? ('🚀 批量提交重生 (' + allPicked.length + ' 段)') : (regenerating ? '⚡ 切换账号并重新提交' : t('review.redo_go')) }}
      </button>
    </div>
  </aside>

  <div class="rv-veil" v-if="gapOn" @click="closeGapfill"></div>
  <aside class="rv-drawer gf-drawer" v-if="gapOn">
    <div class="dhead">
      <h3>{{ t('review.gapfill') }}</h3>
      <button class="x" @click="closeGapfill">×</button>
    </div>
    <div class="dbody">
      <div class="gf-path">
        <span class="lab">{{ t('review.gapfill_list') }}：</span>
        <span class="p" :title="gap.report_path">{{ gap.report_path || '—' }}</span>
        <button class="btn sm" :disabled="gapLoading || !tid" @click="loadGapfill()">{{ t('review.gapfill_reload') }}</button>
        <button class="btn sm" :disabled="gapLoading || !tid" @click="pickGapfill">{{ t('review.gapfill_pick') }}</button>
      </div>
      <p class="dhint" v-if="gapLoading">…</p>
      <p class="dhint" v-else-if="!gap.report_path">{{ t('review.gapfill_none') }}</p>
      <p class="dhint" v-else-if="!gap.group">{{ t('review.gapfill_nogroup') }}</p>
      <template v-else>
        <p class="gf-counts">{{ t('review.gapfill_counts', { real: String(gapCounts.real), maybe: String(gapCounts.maybe), note: String(gapCounts.note) }) }}</p>
        <p class="dhint">{{ t('review.gapfill_group', { g: gap.group, n: String(gap.clips_seen), k: String((gap.gaps||[]).length) }) }}</p>
        <div class="gf-rows" v-if="gapReal.length">
          <label v-for="row in gapReal" :key="gapRowKey(row)" class="gf-row real" :class="{on: gapPicked.includes(row.s)}">
            <input type="checkbox" :checked="gapPicked.includes(row.s)" @change="toggleGapPick(row.s)">
            <i class="swatch" :style="{background: pcolor(row.image)}"></i>
            <button type="button" class="sn" @click.prevent="jumpGapSeg(row.s)">s{{ pad3(row.s) }}</button>
            <span v-if="segLocked(row.s)" :title="t('review.prompt_locked')">🔒</span>
            <span class="rng" v-if="showGapRange(row)">{{ t('review.gapfill_words', { a: String(row.from), b: String(row.to) }) }}</span>
            <span class="tx" :class="{open: gapExpand[row.s]}" @click.prevent="toggleGapText(row.s)">{{ row.text }}</span>
            <span class="gf-vnote" v-if="row.verify_wrong">{{ t('review.gapfill_verify_wrong') }}</span>
          </label>
        </div>
        <div class="gf-sec" v-if="gapMismatch.length">
          <h4>{{ t('review.gapfill_mismatch') }}</h4>
          <div class="gf-rows">
            <label v-for="row in gapMismatch" :key="gapRowKey(row)" class="gf-row mismatch" :class="{on: gapPicked.includes(row.s)}" :title="t('review.gapfill_mismatch_hint')">
              <input type="checkbox" :checked="gapPicked.includes(row.s)" @change="toggleGapPick(row.s)">
              <i class="swatch" :style="{background: pcolor(row.image)}"></i>
              <button type="button" class="sn" @click.prevent="jumpGapSeg(row.s)">s{{ pad3(row.s) }}</button>
              <span v-if="segLocked(row.s)" :title="t('review.prompt_locked')">🔒</span>
              <span class="tx" :class="{open: gapExpand[row.s]}" @click.prevent="toggleGapText(row.s)">{{ row.text }}</span>
              <span class="gf-vnote">{{ t('review.gapfill_verify_wrong') }}</span>
            </label>
          </div>
        </div>
        <div class="gf-rows" v-if="gapMaybe.length">
          <label v-for="row in gapMaybe" :key="gapRowKey(row)" class="gf-row maybe" :class="{on: gapPicked.includes(row.s)}">
            <input type="checkbox" :checked="gapPicked.includes(row.s)" @change="toggleGapPick(row.s)">
            <i class="swatch" :style="{background: pcolor(row.image)}"></i>
            <button type="button" class="sn" @click.prevent="jumpGapSeg(row.s)">s{{ pad3(row.s) }}</button>
            <span v-if="segLocked(row.s)" :title="t('review.prompt_locked')">🔒</span>
            <span class="rng" v-if="showGapRange(row)">{{ t('review.gapfill_words', { a: String(row.from), b: String(row.to) }) }}</span>
            <span class="tx" :class="{open: gapExpand[row.s]}" @click.prevent="toggleGapText(row.s)">{{ row.text }}</span>
          </label>
        </div>
        <div class="gf-notes" v-if="gapNote.length">
          <button type="button" class="gf-fold" @click="toggleNotes">
            <span>{{ t('review.gapfill_notes_fold', { n: String(gapNote.length) }) }}</span>
            <span class="caret">{{ gapNotesOpen ? '▴' : '▾' }}</span>
          </button>
          <div class="gf-rows" v-if="gapNotesOpen">
            <label v-for="row in gapNote" :key="gapRowKey(row)" class="gf-row note" :class="{on: gapPicked.includes(row.s)}">
              <input type="checkbox" :checked="gapPicked.includes(row.s)" @change="toggleGapPick(row.s)">
              <i class="swatch" :style="{background: pcolor(row.image)}"></i>
              <button type="button" class="sn" @click.prevent="jumpGapSeg(row.s)">s{{ pad3(row.s) }}</button>
              <span v-if="segLocked(row.s)" :title="t('review.prompt_locked')">🔒</span>
              <span class="rng" v-if="showGapRange(row)">{{ t('review.gapfill_words', { a: String(row.from), b: String(row.to) }) }}</span>
              <span class="tx" :class="{open: gapExpand[row.s]}" @click.prevent="toggleGapText(row.s)">{{ row.text }}</span>
              <span class="gf-paren" v-if="row.paren_mismatch">{{ t('review.gapfill_paren') }}</span>
            </label>
          </div>
        </div>
        <p class="dhint" v-if="!gapHasRows">{{ t('review.gapfill_empty') }}</p>
      </template>
    </div>
    <div class="dfoot">
      <regen-acc-menu btn-class="btn go" :task="selectedTask" :segs="gapPickedSegs"
                      :disabled="!gapPicked.length || regenerating || isFinished"
                      @go="acc => doGapfillRegen(acc)">
        {{ t('review.gapfill_redo', { n: String(gapPicked.length) }) }}
      </regen-acc-menu>
    </div>
  </aside>

  <div class="tails-ov" v-if="tailsOn" @click.self="closeTails">
    <div class="tails-box">
      <div class="tails-hd">
        <b>{{ t('review.tails.title', { tid: tid, n: String(tails.items.length) }) }}</b>
        <button class="btn sm" @click="tailsGo(-1)">◀ {{ t('review.tails.prev') }}</button>
        <span v-if="tailsCur()">s{{ pad3(tailsCur().s) }} / {{ tails.items.length }}</span>
        <button class="btn sm" @click="tailsGo(1)">{{ t('review.tails.next') }} ▶</button>
        <span class="spacer"></span>
        <button class="btn sm" @click="closeTails">✕</button>
      </div>
      <div class="tails-main">
        <img v-if="tailsCur() && tailsMedia(tailsCur(), tails.showHead ? 'head' : 'tail')"
             :src="tailsMedia(tailsCur(), tails.showHead ? 'head' : 'tail')" alt="">
        <div class="ph" v-else>—</div>
      </div>
      <div class="tails-info" v-if="tailsCur()">
        <span>s{{ pad3(tailsCur().s) }} {{ tails.showHead ? t('review.tails.head') : t('review.tails.tail') }}</span>
        <span v-if="tailsCur().w">{{ tailsCur().w }}×{{ tailsCur().h }}</span>
        <span v-if="tailsCur().sharp!=null">{{ t('review.tails.sharp', { n: String(Math.round(tailsCur().sharp)) }) }}</span>
        <span v-if="tailsCur().dist!=null">{{ t('review.tails.dist', { n: String(tailsCur().dist) }) }}</span>
        <span v-if="tailsCur().upscaled">{{ t('review.tails.upscaled', {
          a: String((tailsCur().upscaled.from||[])[0] || '?'),
          b: String((tailsCur().upscaled.to||[])[0] || '?')
        }) }}</span>
        <span class="y" v-if="tailsFlag(tailsCur())">{{ tailsFlag(tailsCur()) }}</span>
      </div>
      <div class="tails-acts" v-if="tailsCur()">
        <regen-acc-menu btn-class="btn sm" :task="selectedTask" :segs="tailsSegs()"
                        @go="acc => tailsRegen(acc)">{{ t('review.tails.regen', { s: 's'+pad3(tailsCur().s) }) }}</regen-acc-menu>
        <button class="btn sm" @click="tailsRestart">{{ t('review.tails.restart') }}</button>
        <button class="btn sm" @click="tailsUpscale">{{ t('review.tails.upscale') }}</button>
        <button class="btn sm" @click="tailsMark">{{ t('review.tails.mark') }}</button>
      </div>
      <div class="tails-strip">
        <button v-for="(it, i) in tails.items" :key="it.s" type="button"
                :class="{on: i===tails.i, y: !!(it.severe || it.upstream_changed || (it.dist!=null && it.dist>16))}"
                @click="tails.i=i; tails.showHead=false">
          <img v-if="tailsMedia(it,'tail')" :src="tailsMedia(it,'tail')" alt="">
          <span v-if="it.severe || it.upstream_changed || (it.dist!=null && it.dist>16)" class="badge">!</span>
        </button>
      </div>
    </div>
  </div>

  <ul v-if="rvCtx.show && rvCtx.seg" class="ctx rv-ctx" ref="rvCtxRef"
      :style="{left: rvCtx.x + 'px', top: rvCtx.y + 'px'}"
      @click.stop>
    <li @click="openRvMicro(rvCtx.seg)">💬 Grok 式微指令改片 / 局部精修 (s{{ pad3(rvCtx.seg.s) }})…</li>
    <li @click="quickRedoSeg(rvCtx.seg, '')">⚡ 立即重生本段（优先原付费账号 Flow / Grok）</li>
    <li @click="quickRedoSeg(rvCtx.seg, 'STUDIO')">🖥️ 切换本地 AI (LTX-2.5) 重生本段（可选项）</li>
    <li @click="openPicker(rvCtx.seg); closeRvCtx()">🔄 换配图 / 姿势卡重生…</li>
    <li class="sep"></li>
    <li @click="mark(rvCtx.seg, !rvCtx.seg.mark); closeRvCtx()">🚩 {{ rvCtx.seg.mark ? '取消标记问题片' : '标记为问题片 (X)' }}</li>
    <li @click="startEdit(rvCtx.seg, allMode ? rvCtx.key : rvCtx.seg.s, rvCtx.tid); closeRvCtx()">📝 展开并编辑完整提示词 (E)</li>
    <li v-if="rvCtx.seg.image" @click="startEditDesc(rvCtx.seg, allMode ? rvCtx.key : rvCtx.seg.s, rvCtx.tid); closeRvCtx()">✏️ 编辑配图描述语与音色…</li>
    <li @click="copyPrompt(rvCtx.seg); closeRvCtx()">📋 复制本段提示词 (C)</li>
    <li :class="{off: !rvCtx.seg.exists}" @click="rvCtx.seg.exists && reveal(rvCtx.seg, 'video'); closeRvCtx()">📂 打开视频所在文件夹 (V)</li>
  </ul>

  <div class="rv-veil" v-if="rvMicro.on" @click="closeRvMicro"></div>
  <div class="rv-micro-dlg" v-if="rvMicro.on && rvMicroSeg" @click.stop>
    <div class="rv-micro-hd">
      <b>✨ Grok / 达芬奇式微指令改片 · s{{ pad3(rvMicroSeg.s) }}</b>
      <span class="muted">配图 {{ fmtP(rvMicroSeg.image) || '—' }} · {{ durText(rvMicroSeg) }}</span>
      <span class="spacer"></span>
      <button type="button" class="x" @click="closeRvMicro">✕</button>
    </div>
    <div class="rv-micro-chips">
      <button type="button" v-for="(c, idx) in MICRO_CHIPS" :key="idx" class="rv-mchip" @click="addMicroChip(c)">+ {{ c }}</button>
    </div>
    <textarea class="rv-micro-input" rows="3" v-model="rvMicro.instruction"
              placeholder="输入一句修改要求（如：镜头推进慢一点、表情更自然、去掉多余动作…）留空则直接按原提示词重绘"
              @keydown.ctrl.enter.prevent="submitRvMicro"></textarea>
    <regen-acc-pick :task="selectedTask" v-model="rvMicro.accId" :segs="[rvMicroSeg]"
                    title="生成引擎选择（默认优先使用付费 Flow / Grok 账号，可手动切换本地 AI）："
                    :disabled="rvMicro.busy"></regen-acc-pick>
    <div class="rv-micro-ft">
      <span class="muted" style="font-size:12px">Ctrl + Enter 快速提交</span>
      <span class="spacer"></span>
      <button type="button" class="btn sm ghost" @click="closeRvMicro">取消</button>
      <button type="button" class="btn sm primary" :disabled="rvMicro.busy" @click="submitRvMicro">
        🚀 {{ rvMicro.accId === 'STUDIO' ? '用本地 AI (LTX-2.5) 提交重绘' : '用付费账号优先提交重绘' }}
      </button>
    </div>
  </div>

  <div class="rv-veil" v-if="cutoraStitch.on" @click="closeCutoraStitchModal"></div>
  <aside class="rv-drawer" v-if="cutoraStitch.on" style="max-width:480px">
    <div class="dhead">
      <h3>🎬 送入 Cutora 智能拼接（自动填文案 + 缺词回传标记）</h3>
      <button class="x" @click="closeCutoraStitchModal">×</button>
    </div>
    <div class="dbody" style="display:flex;flex-direction:column;gap:12px;padding:16px">
      <p class="hint" style="margin:0;line-height:1.5">
        直接从 Orante 提取任务成品片段与原文案送入 Cutora 自动开始智能拼接：<br>
        · <b>若发现缺词/遗漏</b>：Cutora 会自动发信号回 Orante 定位并标记具体片段（切换到<b>「全部任务 → 已标记」</b>即可一键重生补全）<br>
        · <b>若片段完整无缺</b>：自动为每个任务创建时间线并启动全自动加字幕！
      </p>
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="radio" value="all" v-model="cutoraStitch.mode">
        <b>提交当前批次全部任务成品片段（推荐）</b>
      </label>
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="radio" value="range" v-model="cutoraStitch.mode">
        <b>按任务编号 / 视频序号范围提交</b>
      </label>
      <div v-if="cutoraStitch.mode==='range'" style="padding-left:24px">
        <input type="text" v-model="cutoraStitch.rangeText"
               placeholder="例: T0137-T0162 或 v01-v26 或 137-162 或 A"
               style="width:100%;padding:8px 10px;border-radius:6px;border:1px solid var(--line,#333);background:var(--bg1,#181a20);color:var(--fg0,#fff)">
        <div class="muted" style="font-size:12px;margin-top:4px">支持任务区间 (T0137-T0162)、视频名区间 (v01-v26)、逗号分隔或子批次名 (A/B/C)</div>
      </div>
      <label v-if="tid" style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="radio" value="current" v-model="cutoraStitch.mode">
        <span>仅提交当前选中任务 ({{ tid }})</span>
      </label>
      <hr style="border:none;border-top:1px solid var(--line,#2a2d35);margin:4px 0">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="checkbox" v-model="cutoraStitch.autoStart">
        <span>进入 Cutora 后立即自动开始智能拼接分析</span>
      </label>
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="checkbox" v-model="cutoraStitch.autoAi">
        <span>片段完整时自动创建各任务时间线并启动全自动加字幕</span>
      </label>
    </div>
    <div class="dfoot" style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px">
      <button class="btn" @click="closeCutoraStitchModal">取消</button>
      <button class="btn go" :disabled="cutoraStitch.busy" @click="sendToCutoraStitch()">
        {{ cutoraStitch.busy ? '正在启动 Cutora…' : '🚀 立即送入 Cutora 并开始拼接' }}
      </button>
    </div>
  </aside>
</section>
    `,
    util: {
      pad3, lastTake, isBorrowed, issuesOf, isMissing, isBad, isCandidate, candidateWhy,
      filterSegs, mediaUrl, RATES, nextPlayable, sortReady, taskLabel,
      finishBlockers, finishReason, hasClip, burstConfetti, confettiColors, prefersReduce,
      typing,
    },
  };
})();
