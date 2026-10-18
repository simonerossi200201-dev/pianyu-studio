// 规格 54：applyMode 没选不发请求；running 跳过并点名。不启浏览器。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const logicPath = path.join(ROOT, 'ui', 'js', 'parts', 'gen-mode.js');
const ctx = { window: {}, console };
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(logicPath, 'utf8'), ctx);
const L = ctx.window.GenModeLogic;
if (!L) {
  console.error('NO_LOGIC');
  process.exit(2);
}

function eq(name, got, want) {
  const a = JSON.stringify(got);
  const b = JSON.stringify(want);
  if (a !== b) {
    console.error('FAIL ' + name + ' got=' + a + ' want=' + b);
    process.exit(1);
  }
  console.log('  ok  ' + name);
}

eq('default grok', L.defaultModeForEngine('grok'), 'first_frame');
eq('default flow', L.defaultModeForEngine('flow'), 'anchor_bridge');

const empty = L.applyModeTargets([], () => false);
eq('empty blocked', empty.blocked, true);
eq('empty targets', empty.targets.length, 0);

const live = { id: 'T0017' };
const r2 = L.applyModeTargets([live], t => t.id === 'T0017');
eq('running skipped', r2.skipped.map(t => t.id), ['T0017']);
eq('running targets', r2.targets.length, 0);
const toast = L.skipToast(r2.skipped);
if (toast.indexOf('T0017') < 0 || toast.indexOf('正在跑') < 0) {
  console.error('FAIL skipToast ' + toast);
  process.exit(1);
}
console.log('  ok  skipToast names T0017');

const st0 = L.selectedModeState([], t => 'first_frame');
eq('none disabled', st0.disabled, true);
eq('none hint', st0.hintKey, 'gen.mode.pick');

const st1 = L.selectedModeState([{ id: 'T0015' }], () => 'first_frame');
eq('one mode', st1.mode, 'first_frame');
eq('one enabled', st1.disabled, false);

const stM = L.selectedModeState(
  [{ id: 'a' }, { id: 'b' }],
  t => (t.id === 'a' ? 'first_frame' : 'anchor_bridge'),
);
eq('mixed', stM.mixed, true);
eq('mixed hint', stM.hintKey, 'gen.mode.mixed');

const warn = L.mismatchWarn({ gen_mode: 'anchor_bridge', engine: 'grok' });
if (warn.indexOf('锚点+接片') < 0 || warn.indexOf('Grok') < 0) {
  console.error('FAIL warn ' + warn);
  process.exit(1);
}
console.log('  ok  mismatchWarn grok+anchor');

async function main() {
  const posted = [];
  const rEmpty = await L.runApplyMode({
    mode: 'first_frame',
    selected: [],
    isLive: () => false,
    post: async (url, body) => { posted.push({ url, body }); return { ok: true }; },
    confirmFn: () => true,
    toastFn: () => {},
  });
  eq('run empty posted', posted.length, 0);
  eq('run empty applied', rEmpty.applied, []);

  const toasts = [];
  const rLive = await L.runApplyMode({
    mode: 'first_frame',
    selected: [{ id: 'T0017' }],
    isLive: () => true,
    post: async (url, body) => { posted.push({ url, body }); return { ok: true, hint: 'x' }; },
    confirmFn: () => true,
    toastFn: (m) => toasts.push(m),
  });
  eq('run live posted', posted.length, 0);
  eq('run live skipped', rLive.skipped, ['T0017']);
  if (!toasts.length || toasts[0].indexOf('T0017') < 0) {
    console.error('FAIL toast ' + JSON.stringify(toasts));
    process.exit(1);
  }
  console.log('  ok  run live toast');
}

main().catch((e) => { console.error(e); process.exit(1); });
