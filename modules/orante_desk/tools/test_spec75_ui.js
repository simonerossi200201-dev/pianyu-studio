// 规格 75：卡片多选 / 右键同步。不启浏览器。
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

const geminiJs = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'panels', 'gemini.js'), 'utf8');
const ctxJs = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'parts', 'ctxmenu.js'), 'utf8');
const zh = JSON.parse(fs.readFileSync(path.join(ROOT, 'orante', 'i18n', 'zh.json'), 'utf8'));

function t(k, vars) {
  let s = zh[k];
  if (s === undefined) return k;
  if (vars) Object.keys(vars).forEach(a => { s = s.split('{' + a + '}').join(vars[a]); });
  return s;
}

function load() {
  const start = geminiJs.indexOf('window.GeminiCardLogic');
  const end = geminiJs.indexOf('window.Panel_gemini');
  if (start < 0 || end < 0) fail('no GeminiCardLogic block');
  const sandbox = { window: {}, console };
  sandbox.window = sandbox.window;
  vm.createContext(sandbox);
  vm.runInContext(ctxJs, sandbox);
  vm.runInContext(geminiJs.slice(start, end), sandbox);
  if (!sandbox.window.GeminiCardLogic) fail('GeminiCardLogic not defined');
  if (typeof sandbox.window.placeMenu !== 'function') fail('placeMenu not shared');
  return sandbox.window;
}

const w = load();
const L = w.GeminiCardLogic;
const SUB = ['p01', 'p02', 'p03', 'p04', 'p05', 'p06', 'p07'];
const BROLL = ['p08', 'p09'];

let s = L.initial();
s = L.reduce(s, { type: 'click', p: 'p02', group: 'subjects', groupPs: SUB });
s = L.reduce(s, { type: 'click', p: 'p06', group: 'subjects', groupPs: SUB, shift: true });
eq('shift 2-6 five', s.sel.subjects, ['p02', 'p03', 'p04', 'p05', 'p06']);
eq('shift focus p06', s.focusP, 'p06');

const before = s.sel.subjects.slice();
s = L.reduce(s, { type: 'click', p: 'p07', group: 'subjects', groupPs: SUB });
eq('plain click sel unchanged', s.sel.subjects, before);
eq('plain click focus', s.focusP, 'p07');

s = L.reduce(s, { type: 'click', p: 'p01', group: 'subjects', groupPs: SUB, ctrl: true });
if (s.sel.subjects.indexOf('p01') < 0) fail('ctrl add p01');
ok('ctrl add');
s = L.reduce(s, { type: 'click', p: 'p01', group: 'subjects', groupPs: SUB, ctrl: true });
if (s.sel.subjects.indexOf('p01') >= 0) fail('ctrl remove p01');
ok('ctrl remove');

s.sel.brolls = ['p08'];
s = L.reduce(s, { type: 'ctrla', group: 'subjects', groupPs: SUB });
eq('select all subjects', s.sel.subjects, SUB);
eq('brolls untouched', s.sel.brolls, ['p08']);

s = L.reduce(s, { type: 'esc' });
eq('esc clears both', s.sel, { subjects: [], brolls: [] });

const noneFlags = L.menuFlags({ p: 'p04', desc: '好描述', voice: 'v', exists: true }, [], 12);
if (!noneFlags.sync || !noneFlags.syncDesc) fail('sync should be off when none selected');
const noneTitle = L.menuTitle('syncDesc', noneFlags, t);
if (noneTitle.indexOf('先勾选') < 0) fail('title 先勾选 got=' + noneTitle);
ok('menu grey + 先勾选');

const req = L.syncRequest('p04', ['p01', 'p02', 'p03'], ['desc']);
eq('sync from 4th to 3', req, { from_p: 'p04', to_ps: ['p01', 'p02', 'p03'], fields: ['desc'] });

const overlap = L.syncRequest('p02', ['p01', 'p02', 'p03'], ['desc']);
eq('exclude source from to_ps', overlap.to_ps, ['p01', 'p03']);
eq('from_p is source', overlap.from_p, 'p02');

const emptyDesc = L.menuFlags({ p: 'p04', desc: '', voice: 'v', exists: true }, ['p01', 'p02'], 12);
if (!emptyDesc.syncDesc) fail('syncDesc should be off when source desc empty');
if (emptyDesc.syncVoice) fail('syncVoice should be on when voice present and targets exist');
ok('empty desc greys syncDesc only');

const miss = L.menuFlags({ p: 'p04', desc: 'x', exists: false }, ['p01'], 4);
if (!miss.open || !miss.reveal) fail('missing file should grey open/reveal');
ok('missing file greys open');

function makeClock() {
  let clock = 0;
  const timers = [];
  let seq = 1;
  return {
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
      const due = timers.filter(x => x.t <= clock);
      due.forEach(x => {
        const i = timers.indexOf(x);
        if (i >= 0) timers.splice(i, 1);
        x.fn();
      });
    },
  };
}

const clock = makeClock();
let expired = false;
const u = L.makeUndo(clock.schedule, clock.unschedule, () => { expired = true; }, L.UNDO_MS);
if (!u.alive) fail('undo starts alive');
clock.advance(14999);
if (!u.alive || expired) fail('undo still alive before 15s');
clock.advance(1);
if (u.alive || !expired) fail('undo expires at 15s');
ok('undo button 15s');

const clock2 = makeClock();
const changed = [{ p: 'p05', desc_old: '旧', voice_old: 'v0' }];
const posts = [];
const u2 = L.makeUndo(clock2.schedule, clock2.unschedule, () => {}, L.UNDO_MS);
const ran = u2.run(() => { posts.push(L.restoreBody(changed)); });
if (!ran) fail('undo click should run');
eq('restore request', posts[0], { restore: changed });
if (u2.alive) fail('undo dead after click');
clock2.advance(20000);
ok('undo click restore body');

const p = w.placeMenu(2000, 2000, 260, 380, 1280, 720, 8);
if (p.left < 8 || p.top < 8) fail('placeMenu pad ' + JSON.stringify(p));
if (p.left + 260 > 1280 - 8) fail('placeMenu right overflow ' + JSON.stringify(p));
if (p.top + 380 > 720 - 8) fail('placeMenu bottom overflow ' + JSON.stringify(p));
ok('placeMenu flips inside viewport');

if (geminiJs.indexOf('function placeMenu(') >= 0) fail('gemini.js copied placeMenu');
if (geminiJs.indexOf('window.openCtxMenu') < 0) fail('gemini.js not using openCtxMenu');
ok('gemini reuses ctxmenu');

console.log('LOGIC_OK');
