// 规格 73：礼花三层、粒子上限、降档、减少动态效果、点击收掉、出屏移除。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const src = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'panels', 'review.js'), 'utf8');
const css = fs.readFileSync(path.join(ROOT, 'ui', 'css', 'app.css'), 'utf8');

function fail(msg) {
  console.error('FAIL ' + msg);
  process.exit(1);
}
function ok(name) { console.log('  ok  ' + name); }

if (src.indexOf('CELEBRATE_MS = 4800') < 0) fail('CELEBRATE_MS not 4800');
if (src.indexOf('PARTICLE_CAP = 1100') < 0) fail('PARTICLE_CAP not 1100');
if (src.indexOf('quadraticCurveTo') < 0) fail('no ribbon arc');
if (src.indexOf('window.ReviewCelebrate') < 0) fail('no ReviewCelebrate');
if (css.indexOf('width:132px') < 0) fail('badge not 132px');
if (css.indexOf('rv-cele-ring') < 0) fail('no ring keyframes');
if (!/rv-celebrate\{[^}]*pointer-events:\s*auto/.test(css.replace(/\s+/g, ''))) {
  if (css.indexOf('pointer-events:auto') < 0) fail('celebrate should receive clicks');
}
ok('source + css');

function makeCanvas() {
  const ctx = {
    setTransform() {},
    clearRect() {},
    save() {},
    restore() {},
    translate() {},
    rotate() {},
    beginPath() {},
    arc() {},
    fill() {},
    stroke() {},
    moveTo() {},
    lineTo() {},
    quadraticCurveTo() {},
    fillRect() {},
    fillStyle: '',
    strokeStyle: '',
    globalAlpha: 1,
    lineWidth: 1,
    lineJoin: '',
    lineCap: '',
  };
  const el = {
    tagName: 'CANVAS',
    className: '',
    style: {},
    width: 0,
    height: 0,
    clientWidth: 800,
    clientHeight: 600,
    parentNode: null,
    children: [],
    getContext() { return ctx; },
  };
  return el;
}

function makeMiniDom() {
  const listeners = new Map();
  function makeEl(tag) {
    const node = {
      tagName: String(tag).toUpperCase(),
      className: '',
      classList: {
        add() {},
        remove() {},
        contains(x) { return String(node.className).split(/\s+/).indexOf(x) >= 0; },
      },
      style: {},
      parentNode: null,
      children: [],
      _html: '',
      _listeners: {},
      textContent: '',
      get innerHTML() { return node._html; },
      set innerHTML(v) {
        node._html = String(v || '');
        node.children = [];
        const re = /class="([^"]+)"/g;
        let m;
        while ((m = re.exec(node._html))) {
          const child = makeEl('div');
          child.className = m[1].split(/\s+/)[0];
          child.parentNode = node;
          node.children.push(child);
        }
      },
      appendChild(c) {
        c.parentNode = node;
        node.children.push(c);
        return c;
      },
      removeChild(c) {
        c.parentNode = null;
        node.children = node.children.filter((x) => x !== c);
        return c;
      },
      querySelector(sel) {
        const all = node.querySelectorAll(sel);
        return all[0] || null;
      },
      querySelectorAll(sel) {
        const out = [];
        const want = String(sel || '');
        (function walk(n) {
          (n.children || []).forEach((c) => {
            if (matchSel(c, want)) out.push(c);
            walk(c);
          });
        })(node);
        return out;
      },
      addEventListener(type, fn) {
        node._listeners[type] = node._listeners[type] || [];
        node._listeners[type].push(fn);
      },
      removeEventListener(type, fn) {
        const arr = node._listeners[type] || [];
        node._listeners[type] = arr.filter((x) => x !== fn);
      },
      dispatchEvent(ev) {
        (node._listeners[ev.type] || []).forEach((fn) => fn(ev));
      },
      getContext: String(tag).toLowerCase() === 'canvas' ? function () {
        return makeCanvas().getContext();
      } : undefined,
    };
    if (String(tag).toLowerCase() === 'canvas') {
      const c = makeCanvas();
      Object.assign(node, c, { tagName: 'CANVAS', children: node.children, parentNode: null, className: '' });
      node.getContext = c.getContext;
    }
    return node;
  }
  function matchSel(el, sel) {
    sel = String(sel || '').trim();
    if (sel.startsWith('.')) {
      const cls = sel.slice(1).split('.')[0];
      return (el.className || '').split(/\s+/).indexOf(cls) >= 0;
    }
    if (sel.indexOf('.') > 0) {
      const [tag, cls] = sel.split('.');
      return el.tagName === tag.toUpperCase() && (el.className || '').split(/\s+/).indexOf(cls) >= 0;
    }
    return el.tagName === sel.toUpperCase();
  }
  const body = makeEl('body');
  const document = {
    body,
    createElement: (tag) => makeEl(tag),
    querySelector(sel) { return body.querySelector(sel); },
    querySelectorAll(sel) {
      const a = body.querySelectorAll(sel);
      if (matchSel(body, sel)) a.unshift(body);
      return a;
    },
    addEventListener(type, fn) {
      listeners.set(type, (listeners.get(type) || []).concat([fn]));
    },
    removeEventListener(type, fn) {
      listeners.set(type, (listeners.get(type) || []).filter((x) => x !== fn));
    },
  };
  const win = {
    document,
    innerWidth: 800,
    innerHeight: 600,
    devicePixelRatio: 1,
    matchMedia: (q) => ({ matches: String(q).indexOf('reduce') >= 0 ? !!win._reduce : false }),
    _reduce: false,
    pcolor: (p) => '#8FB06A',
    addEventListener(type, fn) {
      listeners.set('w:' + type, (listeners.get('w:' + type) || []).concat([fn]));
    },
    removeEventListener(type, fn) {
      listeners.set('w:' + type, (listeners.get('w:' + type) || []).filter((x) => x !== fn));
    },
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    console,
  };
  win.window = win;
  document.defaultView = win;
  return win;
}

function loadCelebrate(win, extras) {
  extras = extras || {};
  const start = src.indexOf('window.ReviewCelebrate');
  const end = src.indexOf('window.Panel_review');
  if (start < 0 || end < 0) fail('ReviewCelebrate block missing');
  const sandbox = {
    window: win,
    document: win.document,
    requestAnimationFrame: extras.raf || win.requestAnimationFrame,
    cancelAnimationFrame: extras.caf || win.cancelAnimationFrame,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    performance: extras.performance || { now: () => Date.now() },
    console,
    Math,
    Date,
  };
  sandbox.window = win;
  win.window = win;
  vm.createContext(sandbox);
  vm.runInContext(src.slice(start, end), sandbox);
  if (!win.ReviewCelebrate) fail('ReviewCelebrate not defined');
  return win.ReviewCelebrate;
}

function makeClock() {
  let now = 0;
  let id = 0;
  const queue = [];
  let cafN = 0;
  let rafN = 0;
  return {
    now: () => now,
    raf(fn) {
      rafN += 1;
      const i = ++id;
      queue.push({ i, fn });
      return i;
    },
    caf(i) {
      cafN += 1;
      for (let k = queue.length - 1; k >= 0; k--) {
        if (queue[k].i === i) queue.splice(k, 1);
      }
    },
    get rafN() { return rafN; },
    get cafN() { return cafN; },
    pump(ms) {
      now += ms;
      const q = queue.splice(0);
      q.forEach((x) => x.fn(now));
    },
    get nowVal() { return now; },
    pending() { return queue.length; },
  };
}

// --- burstConfetti 跑完摘 canvas、取消 rAF ---
{
  const win = makeMiniDom();
  const clock = makeClock();
  win.requestAnimationFrame = clock.raf;
  win.cancelAnimationFrame = clock.caf;
  const C = loadCelebrate(win, { raf: clock.raf, caf: clock.caf, performance: { now: clock.now } });
  const canvas = makeCanvas();
  win.document.body.appendChild(canvas);
  const ctrl = C.burstConfetti(canvas, {
    now: clock.now, raf: clock.raf, caf: clock.caf,
    fwCount: 6, fwBurst: 70, confettiPer: 40, ribbonCount: 22,
  });
  if (!ctrl) fail('burstConfetti returned null');
  let guard = 0;
  while (!ctrl.done && guard < 400) {
    clock.pump(16);
    guard += 1;
  }
  if (!ctrl.done) fail('did not finish in 400 frames');
  if (canvas.parentNode) fail('canvas still attached');
  if (clock.cafN < 1) fail('rAF not cancelled, cafN=' + clock.cafN);
  ok('burstConfetti cleanup');
}

// --- 粒子总数 ≤ 1100；三层数量在区间 ---
{
  const win = makeMiniDom();
  const clock = makeClock();
  const C = loadCelebrate(win, { raf: clock.raf, caf: clock.caf, performance: { now: clock.now } });
  const canvas = makeCanvas();
  win.document.body.appendChild(canvas);
  const ctrl = C.burstConfetti(canvas, {
    now: clock.now, raf: clock.raf, caf: clock.caf,
    fwCount: 7, fwBurst: 90, confettiPer: 110, ribbonCount: 26,
  });
  let guard = 0;
  while (clock.nowVal < 700 && guard < 80) {
    clock.pump(16);
    guard += 1;
  }
  const live = ctrl.liveCount();
  if (live > 1100) fail('live particles ' + live + ' > 1100');
  if (ctrl.planned.total > 1100) fail('planned ' + ctrl.planned.total + ' > 1100');
  if (ctrl.spawned.rockets < 1 || ctrl.spawned.rockets > 8) {
    fail('rockets ' + ctrl.spawned.rockets);
  }
  if (ctrl.spawned.confetti < 80 || ctrl.spawned.confetti > 440) {
    fail('confetti ' + ctrl.spawned.confetti);
  }
  if (ctrl.spawned.ribbons < 8 || ctrl.spawned.ribbons > 30) {
    fail('ribbons ' + ctrl.spawned.ribbons);
  }
  ctrl.cancel();
  ok('particle counts cap + layers');
}

// --- 前 12 帧 30ms → 降档，粒子约 60%，停发烟花 ---
{
  const win = makeMiniDom();
  const clock = makeClock();
  const C = loadCelebrate(win, { raf: clock.raf, caf: clock.caf, performance: { now: clock.now } });
  const canvas = makeCanvas();
  win.document.body.appendChild(canvas);
  const ctrl = C.burstConfetti(canvas, {
    now: clock.now, raf: clock.raf, caf: clock.caf,
    fwCount: 8, fwBurst: 80, confettiPer: 80, ribbonCount: 24,
  });
  // 第 1 帧没有 dt；再 12 帧 30ms 凑满 12 个样本
  clock.pump(30);
  for (let i = 0; i < 11; i++) clock.pump(30);
  if (ctrl.downgraded) fail('downgraded too early');
  const before = ctrl.liveCount();
  const rocketsBefore = ctrl.spawned.rockets;
  clock.pump(30);
  if (!ctrl.downgraded) fail('did not downgrade after 12 slow frames');
  if (!ctrl.stopFireworks) fail('still spawning fireworks');
  const after = ctrl.liveCount();
  if (before > 8) {
    const ratio = after / before;
    if (ratio > 0.78 || ratio < 0.35) fail('downgrade ratio ' + ratio + ' before=' + before + ' after=' + after);
  }
  clock.pump(200);
  if (ctrl.spawned.rockets !== rocketsBefore && ctrl.spawned.rockets > rocketsBefore) {
    fail('new fireworks after downgrade ' + rocketsBefore + ' → ' + ctrl.spawned.rockets);
  }
  ctrl.cancel();
  ok('downgrade');
}

// --- prefersReduce → 不创建 canvas、不调 rAF ---
{
  const win = makeMiniDom();
  win._reduce = true;
  const clock = makeClock();
  win.requestAnimationFrame = clock.raf;
  const C = loadCelebrate(win, { raf: clock.raf, caf: clock.caf });
  if (!C.prefersReduce()) fail('prefersReduce should be true');
  const rec = C.launchCelebrate({ title: '任务完成', reduce: true });
  if (clock.rafN !== 0) fail('rAF called under reduce, n=' + clock.rafN);
  if (win.document.body.querySelectorAll('canvas.rv-confetti').length) {
    fail('canvas created under reduce');
  }
  if (rec && rec.canvas) fail('rec.canvas set under reduce');
  C.stopCelebrate();
  ok('prefersReduce no canvas');
}

// --- 点击 .rv-celebrate 提前结束 ---
{
  const win = makeMiniDom();
  const clock = makeClock();
  win.requestAnimationFrame = clock.raf;
  win.cancelAnimationFrame = clock.caf;
  const C = loadCelebrate(win, { raf: clock.raf, caf: clock.caf, performance: { now: clock.now } });
  const rec = C.launchCelebrate({
    title: '完成',
    sub: 'T0001',
    now: clock.now, raf: clock.raf, caf: clock.caf,
    fwCount: 6, fwBurst: 40, confettiPer: 20, ribbonCount: 8,
  });
  clock.pump(16);
  const canvases = win.document.body.querySelectorAll('canvas.rv-confetti');
  if (!canvases.length && !(rec && rec.canvas)) fail('no canvas to dismiss');
  const host = rec.host || win.document.body.querySelector('.rv-celebrate');
  if (!host) fail('no celebrate host');
  host.dispatchEvent({ type: 'click' });
  if (rec.canvas && rec.canvas.parentNode) fail('canvas still there after click');
  if (win.document.body.querySelectorAll('canvas.rv-confetti').length) {
    fail('canvas leftover after click');
  }
  ok('click dismisses');
}

// --- 出屏粒子被移除 ---
{
  const win = makeMiniDom();
  const clock = makeClock();
  const C = loadCelebrate(win, { raf: clock.raf, caf: clock.caf, performance: { now: clock.now } });
  const canvas = makeCanvas();
  win.document.body.appendChild(canvas);
  const ctrl = C.burstConfetti(canvas, {
    now: clock.now, raf: clock.raf, caf: clock.caf,
    fwCount: 6, fwBurst: 70, confettiPer: 60, ribbonCount: 22,
  });
  let peak = 0;
  for (let i = 0; i < 50; i++) {
    clock.pump(16);
    peak = Math.max(peak, ctrl.liveCount());
  }
  for (let i = 0; i < 200; i++) clock.pump(16);
  const later = ctrl.liveCount();
  if (peak < 10) fail('never spawned enough, peak=' + peak);
  if (later >= peak * 0.85) fail('offscreen not culled peak=' + peak + ' later=' + later);
  ctrl.cancel();
  ok('offscreen culled');
}

console.log('spec73 confetti ok');
