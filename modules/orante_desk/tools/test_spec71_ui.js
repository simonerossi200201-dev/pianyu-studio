// 规格 71：⑤收片 可拖进度条 + 逐帧。jsdom（没有就用迷你 DOM）里给 HTMLMediaElement 打桩。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');

function fail(msg) {
  console.error('FAIL ' + msg);
  process.exit(1);
}
function ok(name) { console.log('  ok  ' + name); }

const src = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'panels', 'review.js'), 'utf8');
const css = fs.readFileSync(path.join(ROOT, 'ui', 'css', 'app.css'), 'utf8');
const zh = JSON.parse(fs.readFileSync(path.join(ROOT, 'orante', 'i18n', 'zh.json'), 'utf8'));

if (src.indexOf('fastSeek') >= 0) fail('must not use fastSeek');
if (!/async function onEnded\(seg\)/.test(src)) fail('onEnded missing');
const ended = src.slice(src.indexOf('async function onEnded(seg)'));
const endedBody = ended.slice(0, ended.indexOf('function guardUnsaved'));
if (endedBody.indexOf('resetBarS(') >= 0) fail('onEnded still clears the bar');
if (endedBody.indexOf('setFill(bar, 1)') < 0) fail('onEnded should freeze bar at 100%');
if (src.indexOf("ev.key === 'ArrowRight'") < 0 || src.indexOf('move(1)') < 0) {
  fail('ArrowRight still has to switch segment');
}
if (src.indexOf("ev.key === 'ArrowLeft'") < 0 || src.indexOf('move(-1)') < 0) {
  fail('ArrowLeft still has to switch segment');
}
if (src.indexOf('handleKey') < 0) fail('no handleKey');
if (src.indexOf('wasPlaying') < 0) fail('no wasPlaying');
if (src.indexOf('setPointerCapture') < 0) fail('no setPointerCapture');
if (src.indexOf('requestAnimationFrame') < 0) fail('no rAF coalesce');
ok('source: no fastSeek, onEnded keeps 100%, arrows still move');

if (zh['review.keys.frame'] !== '上一帧 / 下一帧') fail('zh frame key');
if (zh['review.keys.sec'] !== '后退 / 前进 1 秒') fail('zh sec key');
if (src.indexOf("t('review.keys.frame')") < 0) fail('keys.frame not in template');
ok('i18n frame keys');

if (css.indexOf('.panel-review .stage .bar-knob') < 0) fail('css missing knob');
if (css.indexOf('.panel-review .stage .bar-tip') < 0) fail('css missing tip');
if (css.indexOf('.panel-review .stage .bar-shade') < 0) fail('css missing shade');
if (css.indexOf('rgba(255,255,255,.28)') < 0) fail('css missing unplayed color');
if (css.indexOf('var(--accent)') < 0 || !/\.stage \.bar i\{[^}]*var\(--accent\)/.test(css.replace(/\s+/g, ''))) {
  // 宽松：已播用 accent
  if (css.indexOf('.panel-review .stage .bar i') < 0) fail('css missing fill');
}
if (css.indexOf('top:6px;right:7px') < 0) fail('rate should stay top-right');
ok('css scrubber');

function makeMiniWindow() {
  function matches(el, sel) {
    sel = String(sel || '').trim();
    if (!el || el.nodeType !== 1) return false;
    if (sel.startsWith('.')) return el.classList.contains(sel.slice(1));
    return (el.tagName || '').toLowerCase() === sel.toLowerCase();
  }
  function descendants(node) {
    const out = [];
    (function walk(n) {
      (n.children || []).forEach((c) => { out.push(c); walk(c); });
    })(node);
    return out;
  }
  function makeClassList(el) {
    const set = new Set();
    return {
      add() { [].forEach.call(arguments, (x) => { if (x) set.add(String(x)); }); },
      remove() { [].forEach.call(arguments, (x) => set.delete(String(x))); },
      contains(x) { return set.has(String(x)); },
      toggle(x) { if (set.has(x)) set.delete(x); else set.add(x); },
    };
  }
  class MiniEvent {
    constructor(type, opts) {
      opts = opts || {};
      this.type = type;
      this.bubbles = opts.bubbles !== false;
      this.cancelable = !!opts.cancelable;
      this.clientX = opts.clientX || 0;
      this.clientY = opts.clientY || 0;
      this.pointerId = opts.pointerId;
      this.shiftKey = !!opts.shiftKey;
      this.key = opts.key;
      this.code = opts.code;
      this.target = opts.target || null;
      this.currentTarget = null;
      this._stopped = false;
      this.defaultPrevented = false;
    }
    stopPropagation() { this._stopped = true; }
    preventDefault() { this.defaultPrevented = true; }
  }
  function decorate(el, tag) {
    el.nodeType = 1;
    el.tagName = String(tag || 'div').toUpperCase();
    el.children = [];
    el.parentElement = null;
    el.style = {};
    el.dataset = {};
    el.classList = makeClassList(el);
    el.className = '';
    el._listeners = {};
    el.offsetWidth = 72;
    el.setPointerCapture = function () {};
    el.releasePointerCapture = function () {};
    el.getBoundingClientRect = function () {
      return { left: 0, top: 0, width: 100, height: 4, right: 100, bottom: 4, x: 0, y: 0 };
    };
    el.appendChild = function (child) {
      child.parentElement = el;
      el.children.push(child);
      return child;
    };
    el.querySelector = function (sel) {
      const parts = String(sel).trim().split(/\s+/);
      let pool = descendants(el);
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        if (i === 0) {
          pool = pool.filter((n) => matches(n, part));
        } else {
          const next = [];
          pool.forEach((n) => {
            descendants(n).forEach((d) => { if (matches(d, part)) next.push(d); });
          });
          pool = next;
        }
      }
      return pool[0] || null;
    };
    el.querySelectorAll = function (sel) {
      const one = el.querySelector(sel);
      return one ? [one] : [];
    };
    el.closest = function (sel) {
      let n = el;
      while (n) {
        if (matches(n, sel)) return n;
        n = n.parentElement;
      }
      return null;
    };
    el.ownerDocument = null;
    el.addEventListener = function (type, fn) {
      (el._listeners[type] || (el._listeners[type] = [])).push(fn);
    };
    el.removeEventListener = function (type, fn) {
      const list = el._listeners[type] || [];
      el._listeners[type] = list.filter((x) => x !== fn);
    };
    el.dispatchEvent = function (ev) {
      ev.target = ev.target || el;
      let node = el;
      while (node) {
        ev.currentTarget = node;
        const list = (node._listeners && node._listeners[ev.type] || []).slice();
        list.forEach((fn) => fn.call(node, ev));
        if (ev._stopped || !ev.bubbles) break;
        node = node.parentElement;
      }
      return !ev.defaultPrevented;
    };
    return el;
  }
  function stubVideo(el) {
    let paused = true;
    let currentTime = 0;
    let duration = 8;
    el.playCalls = 0;
    el.pauseCalls = 0;
    Object.defineProperty(el, 'paused', { get() { return paused; }, configurable: true });
    Object.defineProperty(el, 'duration', {
      get() { return duration; },
      set(v) { duration = Number(v); },
      configurable: true,
    });
    Object.defineProperty(el, 'currentTime', {
      get() { return currentTime; },
      set(v) { currentTime = Number(v); },
      configurable: true,
    });
    el.play = function () { paused = false; el.playCalls += 1; return Promise.resolve(); };
    el.pause = function () { paused = true; el.pauseCalls += 1; };
    el._setPaused = function (v) { paused = !!v; };
  }
  const document = decorate({}, 'document');
  document.nodeType = 9;
  const body = decorate({}, 'body');
  body.ownerDocument = document;
  document.body = body;
  document.documentElement = decorate({}, 'html');
  document.createElement = function (tag) {
    const el = decorate({}, tag);
    el.ownerDocument = document;
    if (String(tag).toLowerCase() === 'video' || String(tag).toLowerCase() === 'audio') stubVideo(el);
    return el;
  };
  document.activeElement = body;
  const win = {
    document,
    window: null,
    Event: MiniEvent,
    PointerEvent: MiniEvent,
    HTMLMediaElement: { prototype: {} },
    requestAnimationFrame: function (fn) { fn(); return 1; },
    cancelAnimationFrame: function () {},
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    console,
  };
  win.window = win;
  document.defaultView = win;
  return win;
}

function stubJsdomMedia(win) {
  const proto = win.HTMLMediaElement && win.HTMLMediaElement.prototype;
  const orig = win.document.createElement.bind(win.document);
  win.document.createElement = function (tag) {
    const el = orig(tag);
    if (!el.setPointerCapture) el.setPointerCapture = function () {};
    if (!el.releasePointerCapture) el.releasePointerCapture = function () {};
    const t = String(tag).toLowerCase();
    if (t === 'video' || t === 'audio') {
      let paused = true;
      let currentTime = 0;
      let duration = 8;
      el.playCalls = 0;
      el.pauseCalls = 0;
      Object.defineProperty(el, 'paused', { get() { return paused; }, configurable: true });
      Object.defineProperty(el, 'duration', {
        get() { return duration; },
        set(v) { duration = Number(v); },
        configurable: true,
      });
      Object.defineProperty(el, 'currentTime', {
        get() { return currentTime; },
        set(v) { currentTime = Number(v); },
        configurable: true,
      });
      el.play = function () { paused = false; el.playCalls += 1; return Promise.resolve(); };
      el.pause = function () { paused = true; el.pauseCalls += 1; };
      el._setPaused = function (v) { paused = !!v; };
    }
    return el;
  };
  if (proto) {
    proto.play = proto.play || function () { return Promise.resolve(); };
    proto.pause = proto.pause || function () {};
  }
}

function makeWindow() {
  try {
    const { JSDOM } = require('jsdom');
    const dom = new JSDOM('<!doctype html><html><body></body></html>', {
      pretendToBeVisual: true,
      url: 'http://127.0.0.1/',
    });
    stubJsdomMedia(dom.window);
    if (!dom.window.requestAnimationFrame) {
      dom.window.requestAnimationFrame = function (fn) { fn(); return 1; };
      dom.window.cancelAnimationFrame = function () {};
    }
    console.log('  (jsdom)');
    return dom.window;
  } catch (e) {
    console.log('  (mini-dom, jsdom not installed)');
    return makeMiniWindow();
  }
}

function loadScrubber(win) {
  const start = src.indexOf('window.ReviewScrubber');
  const end = src.indexOf('window.Panel_review');
  if (start < 0 || end < 0) fail('ReviewScrubber block missing');
  const sandbox = {
    window: win,
    document: win.document,
    requestAnimationFrame: win.requestAnimationFrame,
    cancelAnimationFrame: win.cancelAnimationFrame,
    setTimeout: win.setTimeout || setTimeout,
    clearTimeout: win.clearTimeout || clearTimeout,
    console,
  };
  sandbox.window = win;
  win.window = win;
  vm.createContext(sandbox);
  vm.runInContext(src.slice(start, end), sandbox);
  if (!win.ReviewScrubber) fail('ReviewScrubber not defined');
  return win.ReviewScrubber;
}

function fire(el, type, extra) {
  extra = extra || {};
  const Ev = el.ownerDocument && el.ownerDocument.defaultView &&
    (el.ownerDocument.defaultView.PointerEvent || el.ownerDocument.defaultView.Event);
  let ev;
  if (typeof Ev === 'function') {
    try {
      ev = new Ev(type, Object.assign({ bubbles: true, cancelable: true }, extra));
    } catch (e) {
      ev = null;
    }
  }
  if (!ev) {
    ev = {
      type, bubbles: true, cancelable: true,
      clientX: extra.clientX || 0,
      pointerId: extra.pointerId,
      shiftKey: !!extra.shiftKey,
      key: extra.key, code: extra.code,
      target: el, currentTarget: el,
      _stopped: false, defaultPrevented: false,
      stopPropagation() { this._stopped = true; },
      preventDefault() { this.defaultPrevented = true; },
    };
  }
  try {
    if (extra.clientX != null) ev.clientX = extra.clientX;
    if (extra.pointerId != null) ev.pointerId = extra.pointerId;
    if (extra.key != null) ev.key = extra.key;
    if (extra.code != null) ev.code = extra.code;
    if (extra.shiftKey != null) ev.shiftKey = extra.shiftKey;
  } catch (e) { /* PointerEvent 的 clientX 可能只读 */ }
  el.dispatchEvent(ev);
  return ev;
}

function makeCard(win, S, opts) {
  opts = opts || {};
  const doc = win.document;
  const stage = doc.createElement('div');
  stage.classList.add('stage');
  const video = doc.createElement('video');
  video.duration = opts.duration != null ? opts.duration : 8;
  video.currentTime = opts.currentTime || 0;
  if (opts.paused === false && video._setPaused) video._setPaused(false);
  else if (opts.paused === false) { video.play(); }
  const shade = doc.createElement('div');
  shade.classList.add('bar-shade');
  const bar = doc.createElement('div');
  bar.classList.add('bar');
  const fill = doc.createElement('i');
  const knob = doc.createElement('em');
  knob.classList.add('bar-knob');
  const tip = doc.createElement('span');
  tip.classList.add('bar-tip');
  const clock = doc.createElement('span');
  clock.classList.add('bar-clock');
  bar.appendChild(fill);
  bar.appendChild(knob);
  bar.appendChild(tip);
  stage.appendChild(video);
  stage.appendChild(shade);
  stage.appendChild(bar);
  stage.appendChild(clock);
  bar.getBoundingClientRect = function () {
    return { left: 0, top: 90, width: 100, height: 4, right: 100, bottom: 94, x: 0, y: 90 };
  };
  doc.body.appendChild(stage);
  S.mount(stage, { fps: opts.fps || 24 });
  return { stage, video, bar, fill, clock };
}

const win = makeWindow();
const S = loadScrubber(win);

// A. 悬停显隐
{
  const { stage, bar } = makeCard(win, S);
  if (bar.style.opacity !== '0') fail('idle opacity want 0 got ' + bar.style.opacity);
  fire(stage, 'pointerenter', { bubbles: false });
  if (bar.style.opacity !== '1') fail('enter opacity want 1 got ' + bar.style.opacity);
  fire(stage, 'pointerleave', { bubbles: false });
  if (bar.style.opacity !== '0') fail('leave opacity want 0 got ' + bar.style.opacity);
  S.dispose();
  ok('hover show/hide');
}

{
  const { stage, bar, video } = makeCard(win, S, { paused: false });
  fire(stage, 'pointerenter');
  fire(bar, 'pointerdown', { clientX: 40, pointerId: 1 });
  fire(stage, 'pointerleave');
  if (bar.style.opacity !== '1') fail('drag leave still want 1 got ' + bar.style.opacity);
  if (video.paused !== true) fail('playing drag should pause');
  fire(bar, 'pointerup', { clientX: 40, pointerId: 1 });
  S.dispose();
  ok('drag keeps bar visible after leave');
}

// C. 点击 60%
{
  const { stage, bar, video } = makeCard(win, S, { duration: 10, paused: true });
  fire(stage, 'pointerenter');
  fire(bar, 'pointerdown', { clientX: 60, pointerId: 7 });
  const want = 10 * 0.6;
  if (Math.abs(video.currentTime - want) > 0.05) {
    fail('seek 60% got ' + video.currentTime + ' want ' + want);
  }
  S.dispose();
  ok('click 60% seeks');
}

// C. 拖动 wasPlaying
{
  const { stage, bar, video } = makeCard(win, S, { paused: false });
  fire(stage, 'pointerenter');
  const play0 = video.playCalls;
  fire(bar, 'pointerdown', { clientX: 20, pointerId: 2 });
  if (video.paused !== true) fail('pointerdown should pause');
  if (video.pauseCalls < 1) fail('pause() not called');
  fire(bar, 'pointerup', { clientX: 20, pointerId: 2 });
  if (video.playCalls !== play0 + 1) fail('pointerup should play once, got ' + video.playCalls);
  if (video.paused !== false) fail('should resume playing');
  S.dispose();
  ok('drag resume play');
}

{
  const { stage, bar, video } = makeCard(win, S, { paused: true });
  fire(stage, 'pointerenter');
  fire(bar, 'pointerdown', { clientX: 30, pointerId: 3 });
  fire(bar, 'pointerup', { clientX: 30, pointerId: 3 });
  if (video.playCalls !== 0) fail('paused drag must not play, got ' + video.playCalls);
  if (video.paused !== true) fail('still paused');
  S.dispose();
  ok('paused drag stays paused');
}

// 轨道 pointerdown 不冒泡到 stage（不会切播放）
{
  const { stage, bar, video } = makeCard(win, S, { paused: true });
  let stageHits = 0;
  stage.addEventListener('pointerdown', () => { stageHits += 1; });
  stage.addEventListener('click', () => { stageHits += 1; video.play(); });
  fire(stage, 'pointerenter');
  fire(bar, 'pointerdown', { clientX: 50, pointerId: 4 });
  fire(bar, 'click', { clientX: 50, pointerId: 4 });
  if (stageHits !== 0) fail('bar events leaked to stage: ' + stageHits);
  if (video.playCalls !== 0) fail('onStageClick fired via leak');
  S.dispose();
  ok('bar pointerdown does not toggle');
}

// onEnded 停在 100%
{
  const { bar, fill } = makeCard(win, S);
  S.setFill(bar, 1);
  if (fill.style.width !== '100%') fail('ended fill want 100% got ' + fill.style.width);
  ok('ended bar stays 100%');
}

// D. 逐帧
{
  const { video } = makeCard(win, S, { duration: 8, currentTime: 1, fps: 24, paused: true });
  const ev = {
    code: 'Period', key: '.', shiftKey: false, target: win.document.body,
    preventDefault() { this.defaultPrevented = true; },
  };
  const handled = S.handleKey(ev, { video, fps: 24, active: win.document.body });
  if (!handled) fail('period not handled');
  const want = 1 + 1 / 24;
  if (Math.abs(video.currentTime - want) > 1e-6) fail('. frame got ' + video.currentTime);
  ok('. steps 1/fps');
}

{
  const { video } = makeCard(win, S, { duration: 8, currentTime: 1, fps: 24, paused: true });
  const ev = {
    code: 'Period', key: '>', shiftKey: true, target: win.document.body,
    preventDefault() { this.defaultPrevented = true; },
  };
  S.handleKey(ev, { video, fps: 24, active: win.document.body });
  if (Math.abs(video.currentTime - 2) > 1e-6) fail('Shift+. got ' + video.currentTime);
  ok('Shift+. steps 1s');
}

{
  const { video } = makeCard(win, S, { duration: 8, currentTime: 1, fps: 24, paused: true });
  const input = win.document.createElement('input');
  input.tagName = 'INPUT';
  const ev = {
    code: 'Period', key: '.', shiftKey: false, target: input,
    preventDefault() { this.defaultPrevented = true; },
  };
  const handled = S.handleKey(ev, { video, fps: 24, active: input });
  if (handled) fail('should ignore when typing');
  if (Math.abs(video.currentTime - 1) > 1e-9) fail('input focus still stepped');
  ok('ignore key in input');
}

{
  const { video } = makeCard(win, S, { duration: 8, currentTime: 1, paused: false, fps: 24 });
  S.step(video, 1, { fps: 24, shift: false });
  if (video.paused !== true) fail('step should pause');
  ok('step auto-pauses');
}

ok('fmt ' + S.fmtTip(3.12, 24));
if (!/^\d{2}:\d{2}\.\d{2}$/.test(S.fmtTip(3.12, 24))) fail('tip format');

console.log('spec71 ui ok');
