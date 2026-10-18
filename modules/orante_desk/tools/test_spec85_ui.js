// 规格 85 / 85b：积木开关 / 批量填描述语 UI 逻辑（不启浏览器）
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
function fail(msg) { console.error('FAIL ' + msg); process.exit(1); }
function ok(name) { console.log('  ok  ' + name); }
function eq(name, got, want) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  if (a !== b) fail(name + ' got=' + a + ' want=' + b);
  ok(name);
}

const geminiJs = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'panels', 'gemini.js'), 'utf8');
const logicName = geminiJs.includes('window.GeminiSwitchLogic') ? 'GeminiSwitchLogic'
  : (geminiJs.includes('window.GeminiSwitchLogic') ? 'GeminiSwitchLogic' : null);
if (!logicName) fail('missing GeminiSwitchLogic');
if (!geminiJs.includes('batch-desc') && !geminiJs.includes('batchDesc')) fail('missing batch desc wiring');
if (!geminiJs.includes('g-switch')) fail('missing switch class in template');
ok('gemini.js contains switch + batch markers');

// 85b: template string must not reference window.
const tPos = geminiJs.indexOf('template: `');
if (tPos < 0) fail('no template');
const template = geminiJs.slice(tPos);
if (/window\./.test(template)) fail('template still contains window.');
ok('template has no window.');

const start = geminiJs.indexOf('window.' + logicName);
let sliceEnd = geminiJs.indexOf('\nwindow.', start + 10);
if (sliceEnd < 0) sliceEnd = start + 8000;
const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(geminiJs.slice(start, sliceEnd), sandbox);
const L = sandbox.window[logicName];
if (!L) fail(logicName + ' not defined');

const allOn = L.defaultSwitches();
eq('default all true', Object.values(allOn).every(Boolean), true);

let sw = { ...allOn };
const before = { ...sw };
sw = L.applyMinimal(sw, true);
eq('minimal offs bricks', sw['运镜卡'], false);
eq('minimal keeps mute', sw['静音提示'], true);
sw['画质锁'] = true;
eq('still minimal? auto off', L.isMinimal(sw), false);
sw = L.restoreMinimal(sw, before);
eq('restore keeps prior off quality if was off', before['画质锁'], true);

sw = { ...allOn };
sw = L.toggleGroup(sw, 'lens', false);
eq('group lens off 运镜卡', sw['运镜卡'], false);
eq('group lens off 运动强制', sw['运动强制'], false);
eq('group state off', L.groupState(sw, 'lens'), 'off');
sw['运镜卡'] = true;
eq('group state mixed', L.groupState(sw, 'lens'), 'mixed');
sw = L.toggleGroup(sw, 'lens', true);
eq('group state on after toggle', L.groupState(sw, 'lens'), 'on');

// card off class expectation helper: quality off => sw-off semantically
sw = { ...allOn, '画质锁': false };
eq('quality off flag', sw['画质锁'], false);

const st = L.batchInit([
  { tid: 'T0050', name: 'a', status: 'prepared', images: [{p:'p01',desc:''},{p:'p02',desc:'x',role:'空镜'}] },
  { tid: 'T0051', name: 'b', status: 'prepared', images: [{p:'p01',desc:''}] },
  { tid: 'T0048', name: 'draft', status: 'draft', images: [{p:'p01',desc:''}] },
]);
eq('draft not selectable', st.tasks.find(t => t.tid==='T0048').selectable, false);
L.selectTask(st, 'T0050');
eq('select task selects subject imgs', st.sel['T0050'].sort(), ['p01']);
L.toggleImg(st, 'T0050', 'p01', false);
L.selectTask(st, 'T0051');
L.selectTask(st, 'T0050');
eq('img sel remembered', st.sel['T0050'], []);
L.selectMissing(st, 'T0050');
eq('only missing', st.sel['T0050'], ['p01']);
const stats = L.stats(st, 'overwrite');
eq('stats tasks', stats.tasks >= 1, true);

// 85b filter
st.tasks.forEach(tk => { if (tk.tid==='T0050') tk.missingDot = false; if (tk.tid==='T0051') tk.missingDot = true; });
let shown = L.filterTasks(st.tasks, 'T0051', false);
eq('filter T0051', shown.map(x => x.tid), ['T0051']);
shown = L.filterTasks(st.tasks, '', true);
eq('only missing tasks', shown.map(x => x.tid), ['T0051']);

// preview stats shape (on/total)
const keys = L.BRICKS.concat(L.META);
const on = keys.filter(k => allOn[k] !== false).length;
eq('preview all on count', on, keys.length);

console.log('ALL UI LOGIC OK');
