// 规格 74：下拉滚轮 / 拖拽手势。不启浏览器。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');

function fail(msg) {
  console.error('FAIL ' + msg);
  process.exit(1);
}
function ok(name) { console.log('  ok  ' + name); }
function eq(name, got, want) {
  const a = JSON.stringify(got);
  const b = JSON.stringify(want);
  if (a !== b) fail(name + ' got=' + a + ' want=' + b);
  ok(name);
}

function makeDoc() {
  const map = {};
  return {
    addEventListener(type, fn, cap) {
      const k = type + (cap ? ':c' : '');
      (map[k] || (map[k] = [])).push(fn);
    },
    removeEventListener(type, fn, cap) {
      const k = type + (cap ? ':c' : '');
      map[k] = (map[k] || []).filter(x => x !== fn);
    },
    fire(type, ev, cap) {
      const k = type + (cap ? ':c' : '');
      (map[k] || []).slice().forEach(fn => fn(ev));
    },
  };
}

function makeClock() {
  let clock = 0;
  const timers = [];
  let seq = 1;
  return {
    now: () => clock,
    schedule(fn, ms) {
      const id = { id: seq++, t: clock + Number(ms || 0), fn };
      timers.push(id);
      return id;
    },
    unschedule(id) {
      const i = timers.indexOf(id);
      if (i >= 0) timers.splice(i, 1);
    },
    advance(ms) {
      clock += ms;
      let guard = 0;
      while (guard++ < 80) {
        const due = timers.filter(x => x.t <= clock).sort((a, b) => a.t - b.t);
        if (!due.length) break;
        due.forEach(x => {
          const i = timers.indexOf(x);
          if (i >= 0) timers.splice(i, 1);
          x.fn();
        });
      }
    },
  };
}

function makeTrig() {
  const classes = new Set();
  const el = {
    className: '',
    classList: {
      add(c) { classes.add(c); el.className = [...classes].join(' '); },
      remove(c) { classes.delete(c); el.className = [...classes].join(' '); },
      contains(c) { return classes.has(c); },
    },
    setPointerCapture() {},
    releasePointerCapture() {},
  };
  return el;
}

function loadPickgest(doc) {
  const src = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'parts', 'pickgest.js'), 'utf8');
  const sandbox = {
    window: {},
    document: doc,
    console,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  if (!sandbox.window.usePickerGestures) fail('no usePickerGestures');
  return sandbox.window;
}

function nItems(n) {
  const out = [];
  for (let i = 0; i < n; i++) out.push({ value: 'i' + i });
  return out;
}

function session(opts) {
  opts = opts || {};
  const doc = makeDoc();
  const clock = makeClock();
  const w = loadPickgest(doc);
  const el = makeTrig();
  const items = opts.items || nItems(20);
  let cur = opts.cur != null ? opts.cur : items[0].value;
  let open = !!opts.open;
  const previews = [];
  const commits = [];
  let opened = 0;
  const gest = w.usePickerGestures({
    items: () => items,
    current: () => cur,
    open: () => open,
    disabled: () => !!opts.disabled,
    now: clock.now,
    schedule: clock.schedule,
    unschedule: clock.unschedule,
    onPreview(item) { cur = item.value; previews.push(item.value); },
    onCommit(item) { cur = item.value; commits.push(item.value); },
    onCancel() {},
    onOpen() { opened += 1; open = !open; },
  });
  function wheel(dy) {
    let prevented = false;
    const ev = {
      deltaY: dy,
      currentTarget: el,
      target: el,
      preventDefault() { prevented = true; },
      stopPropagation() {},
    };
    gest.onWheel(ev);
    return prevented;
  }
  function ptr(type, x, extra) {
    const ev = Object.assign({
      pointerId: 1,
      button: 0,
      clientX: x,
      clientY: 0,
      currentTarget: el,
      target: el,
      preventDefault() {},
      stopPropagation() {},
      stopImmediatePropagation() {},
    }, extra || {});
    if (type === 'down') gest.onPointerDown(ev);
    else if (type === 'move') gest.onPointerMove(ev);
    else if (type === 'up') gest.onPointerUp(ev);
    return ev;
  }
  return { gest, el, items, clock, doc, previews, commits, get cur() { return cur; }, get opened() { return opened; }, get open() { return open; }, set open(v) { open = v; }, wheel, ptr };
}

// —— 源码接入 ——
const wired = [
  'ui/js/parts/taskpick.js',
  'ui/js/panels/track.js',
  'ui/js/parts/gen-mode.js',
  'ui/js/panels/review.js',
  'ui/js/panels/generate.js',
];
wired.forEach(rel => {
  const s = fs.readFileSync(path.join(ROOT, rel), 'utf8');
  if (s.indexOf('usePickerGestures') < 0) fail('not wired: ' + rel);
  ok('wired ' + path.basename(rel));
});

const html = fs.readFileSync(path.join(ROOT, 'ui', 'index.html'), 'utf8');
const iPg = html.indexOf('/ui/js/parts/pickgest.js');
const iTp = html.indexOf('/ui/js/parts/taskpick.js');
const iGm = html.indexOf('/ui/js/parts/gen-mode.js');
if (iPg < 0) fail('index.html missing pickgest.js');
if (!(iPg < iTp && iPg < iGm)) fail('pickgest.js should load before taskpick / gen-mode');
ok('index script order');

const css = fs.readFileSync(path.join(ROOT, 'ui', 'css', 'app.css'), 'utf8');
['pg-in-down', 'pg-in-up', 'pg-bump', 'pg-pos', 'pg-ew', 'prefers-reduced-motion'].forEach(k => {
  if (css.indexOf(k) < 0) fail('css missing ' + k);
});
ok('css feel');

// —— 滚轮：累计 +40 → 预览一次，250ms 后才提交 ——
{
  const s = session();
  const prevented = s.wheel(40);
  if (!prevented) fail('closed trigger wheel should preventDefault');
  eq('wheel preview once', s.previews, ['i1']);
  eq('wheel no commit yet', s.commits, []);
  s.clock.advance(249);
  eq('wheel commit not yet at 249', s.commits, []);
  s.clock.advance(1);
  eq('wheel commit after 250', s.commits, ['i1']);
}

// —— 连滚 10 格，间隔 30ms：预览被 70ms 节流，提交只有一次 ——
{
  const s = session();
  for (let i = 0; i < 10; i++) {
    if (i) s.clock.advance(30);
    s.wheel(40);
  }
  if (s.previews.length >= 10) fail('throttle failed, previews=' + s.previews.length);
  if (s.previews.length < 2) fail('expected several previews, got ' + s.previews.length);
  eq('burst no commit during roll', s.commits, []);
  const last = s.previews[s.previews.length - 1];
  s.clock.advance(250);
  eq('burst commit once', s.commits.length, 1);
  eq('burst commit last', s.commits[0], last);
  ok('burst preview throttled n=' + s.previews.length);
}

// —— 菜单打开时滚轮不切项 ——
{
  const s = session({ open: true });
  const prevented = s.wheel(40);
  if (prevented) fail('open menu wheel must not preventDefault');
  eq('open wheel no preview', s.previews, []);
  s.clock.advance(300);
  eq('open wheel no commit', s.commits, []);
}

// —— 跳过 disabled ——
{
  const items = [
    { value: 'a' },
    { value: 'b', disabled: true },
    { value: 'c', disabled: true },
    { value: 'd' },
  ];
  const s = session({ items, cur: 'a' });
  s.wheel(40);
  eq('skip disabled → d', s.previews, ['d']);
}

// —— 到头不循环，bump ——
{
  const items = [{ value: 'a' }, { value: 'b' }, { value: 'c' }];
  const s = session({ items, cur: 'c' });
  s.wheel(40);
  eq('end no wrap', s.cur, 'c');
  eq('end no preview', s.previews, []);
  if (!s.el.classList.contains('bump')) fail('end wheel should bump');
  ok('end bump class');
  s.clock.advance(300);
  eq('end no commit', s.commits, []);
}

// —— 4px 松手 = 点击打开菜单 ——
{
  const s = session();
  s.ptr('down', 0);
  s.ptr('move', 4);
  s.ptr('up', 4);
  eq('click open', s.opened, 1);
  eq('click no commit', s.commits, []);
  eq('click no preview', s.previews, []);
}

// —— 60px 拖 = 2 格，提交一次，不打开菜单 ——
{
  const s = session();
  s.ptr('down', 0);
  s.ptr('move', 60);
  s.ptr('up', 60);
  eq('drag 2 steps', s.previews[s.previews.length - 1], 'i2');
  eq('drag commit once', s.commits, ['i2']);
  eq('drag no open', s.opened, 0);
}

// —— 拖动中 Esc 回起点，不提交 ——
{
  const s = session();
  s.ptr('down', 0);
  s.ptr('move', 60);
  eq('esc previewed', s.cur, 'i2');
  s.doc.fire('keydown', {
    key: 'Escape',
    preventDefault() {},
    stopPropagation() {},
    stopImmediatePropagation() {},
  }, true);
  eq('esc restored', s.cur, 'i0');
  s.ptr('up', 60);
  eq('esc no commit', s.commits, []);
  eq('esc no open', s.opened, 0);
}

// pointerdown 之后滚轮不响应
{
  const s = session();
  s.ptr('down', 0);
  s.wheel(40);
  eq('pressing ignores wheel', s.previews, []);
  s.ptr('up', 0);
}

console.log('LOGIC_OK');
