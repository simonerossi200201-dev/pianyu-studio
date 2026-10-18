// 规格 62：全局当前任务链路 + 镜头序列纯函数。不启浏览器。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');

function eq(name, got, want) {
  const a = JSON.stringify(got);
  const b = JSON.stringify(want);
  if (a !== b) {
    console.error('FAIL ' + name + ' got=' + a + ' want=' + b);
    process.exit(1);
  }
  console.log('  ok  ' + name);
}

function loadTaskPick() {
  const src = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'parts', 'taskpick.js'), 'utf8');
  const sandbox = {
    window: {},
    Vue: {
      ref(v) { return { value: v }; },
      reactive(v) { return v; },
      computed(fn) { return { get value() { return fn(); } }; },
      watch() {},
      onMounted() {},
      onUnmounted() {},
      nextTick(fn) { return Promise.resolve().then(fn); },
    },
    document: { addEventListener() {}, removeEventListener() {} },
  };
  sandbox.window = Object.assign(sandbox, sandbox.window);
  sandbox.window.Vue = sandbox.Vue;
  sandbox.window.ui = { node: 'track', pendingTid: '', panelTid: '' };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return sandbox.window.TaskPickLogic;
}

function loadShotLogic() {
  const src = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'panels', 'track.js'), 'utf8');
  const i = src.indexOf('window.Panel_track');
  if (i < 0) {
    console.error('FAIL no Panel_track');
    process.exit(2);
  }
  const sandbox = { window: {} };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src.slice(0, i), sandbox);
  if (!sandbox.window.TrackShotLogic) {
    console.error('FAIL no TrackShotLogic');
    process.exit(2);
  }
  return sandbox.window.TrackShotLogic;
}

const L = loadTaskPick();
const S = loadShotLogic();

function runPick(uiState, fn) {
  const src = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'parts', 'taskpick.js'), 'utf8');
  const sandbox = {
    window: {},
    Vue: {
      ref(v) { return { value: v }; },
      reactive(v) { return v; },
      computed(fn2) { return { get value() { return fn2(); } }; },
      watch() {}, onMounted() {}, onUnmounted() {},
      nextTick(fn2) { return Promise.resolve().then(fn2); },
    },
    document: { addEventListener() {}, removeEventListener() {} },
  };
  sandbox.window = Object.assign(sandbox, sandbox.window);
  sandbox.window.Vue = sandbox.Vue;
  sandbox.window.ui = uiState;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  fn(sandbox.window.TaskPickLogic, sandbox.window);
}

runPick({ node: 'generate', pendingTid: 'T0022', panelTid: '' }, (Logic, w) => {
  const tid2 = { value: '' };
  const ok = Logic.adoptPanelTid(tid2, 'generate');
  eq('adopt pending generate', ok, true);
  eq('adopt tid T0022', tid2.value, 'T0022');
  eq('pending cleared', w.ui.pendingTid, '');
  eq('panelTid set', w.ui.panelTid, 'T0022');
});

runPick({ node: 'generate', pendingTid: '', panelTid: 'T0022' }, (Logic, w) => {
  const tid2 = { value: '' };
  const ok = Logic.adoptPanelTid(tid2, 'generate');
  eq('adopt panelTid', ok, true);
  eq('tid from panelTid', tid2.value, 'T0022');
  eq('panelTid stays', w.ui.panelTid, 'T0022');
});

runPick({ node: 'generate', pendingTid: '', panelTid: 'T0022' }, (Logic) => {
  const tid2 = { value: 'T0022' };
  // 准入不够也不许改成别的
  const gatePrep = Logic.gateInfo({ id: 'T0022', name: '认真0913', status: 'prepared' }, 'arranged');
  eq('gate prepared→gemini', gatePrep && gatePrep.node, 'gemini');
  eq('gate prepared key', gatePrep && gatePrep.key, 'prepared');
  const gateDesc = Logic.gateInfo({ id: 'T0022', status: 'described' }, 'arranged');
  eq('gate described→track', gateDesc && gateDesc.node, 'track');
  eq('gate described key', gateDesc && gateDesc.key, 'described');
  eq('tid not rewritten', tid2.value, 'T0022');
  const ready = Logic.gateInfo({ id: 'T0022', status: 'arranged' }, 'arranged');
  eq('gate none when arranged', ready, null);
});

runPick({ node: 'track', pendingTid: 'T0003', panelTid: '' }, (Logic, w) => {
  const tid2 = { value: '' };
  const stole = Logic.takePendingTid(tid2, 'gemini');
  eq('source does not steal', stole, false);
  eq('pending kept', w.ui.pendingTid, 'T0003');
});

const gDraft = L.gateInfo({ id: 'T0022', status: 'draft' }, 'prepared');
eq('gate draft node tasks', gDraft.node, 'tasks');
eq('gate draft key', gDraft.key, 'draft');

eq('emptyFrom arranged', L.emptyFrom('arranged'), 'described');
eq('jump described track', L.jumpOf('described').node, 'track');

// —— 乙：镜头序列 ——
const segs = [
  { s: 1, image: 'p01', duration: 8, status: 'planned', role: 'anchor' },
  { s: 2, image: 'p03', duration: 8, status: 'planned', role: 'bridge' },
  { s: 3, image: 'p03', duration: 8, status: 'planned', role: 'anchor' },
  { s: 4, image: 'p03', duration: 8, status: 'collected', role: 'bridge',
    clip_file: 'clips/T0022-s004-p03.mp4', clip_mtime: 1, clip_video: true },
];
const images = [
  { p: 'p01', file: 'images/p01.jpg', mtime: 10 },
  { p: 'p03', file: 'images/p03.jpg', mtime: 11 },
];

const applied = S.shotCells(segs, null, images);
eq('applied n', applied.length, 4);
eq('applied p seq', applied.map(c => c.p), ['p01', 'p03', 'p03', 'p03']);
eq('applied uses manifest image', applied[1].p, 'p03');
eq('collected uses clip', applied[3].file, 'clips/T0022-s004-p03.mp4');
eq('collected video', applied[3].clipVideo, true);
eq('planned uses 配图', applied[0].file, 'images/p01.jpg');
eq('bridge fromTail', applied[1].fromTail, 's001');

const rows = [
  { s: 1, p_new: 'p02', changed: true },
  { s: 2, p_new: 'p03', changed: false },
  { s: 3, p_new: 'p03', changed: false },
  { s: 4, p_new: 'p01', changed: true },
];
const preview = S.shotCells(segs, rows, images);
eq('preview n equals segs', preview.length, 4);
eq('preview p_new', preview.map(c => c.p), ['p02', 'p03', 'p03', 'p01']);
eq('preview changed marks', preview.map(c => c.changed), [true, false, false, true]);
eq('preview flag', preview[0].preview, true);

const st = S.shotStats(applied);
eq('stats n', st.n, 4);
eq('stats images', st.nImages, 2);
eq('longest p', st.longestP, 'p03');
eq('longest n', st.longestN, 3);
eq('longest warn', st.longestWarn, true);
eq('anchor/bridge', [st.nAnchor, st.nBridge], [2, 2]);
eq('dur 32s', st.dur, 32);
eq('durText', st.durText, '32秒');

const run3 = S.shotCells([
  { s: 1, image: 'p03', duration: 8, status: 'planned' },
  { s: 2, image: 'p03', duration: 8, status: 'planned' },
  { s: 3, image: 'p03', duration: 8, status: 'planned' },
], null, images);
eq('repeat 3 cells', run3.map(c => c.p), ['p03', 'p03', 'p03']);
eq('repeat longest', S.shotStats(run3).longestN, 3);

const afterApply = S.shotCells(
  segs.map((sg, i) => Object.assign({}, sg, { image: ['p02', 'p03', 'p03', 'p01'][i] })),
  null,
  images,
);
eq('after apply no preview', afterApply.every(c => !c.changed && !c.preview), true);
eq('after apply images', afterApply.map(c => c.p), ['p02', 'p03', 'p03', 'p01']);

eq('fmtDur 4:52', S.fmtDur(292), '4分52秒');
eq('edge collected+clip', S.shotEdge(segs[3]), 'ok');
eq('edge planned', S.shotEdge(segs[0]), 'idle');
eq('edge miss clip', S.shotEdge({ status: 'collected' }), 'miss');
eq('edge submitted', S.shotEdge({ status: 'submitted' }), 'busy');
eq('edge failed', S.shotEdge({ status: 'failed' }), 'bad');

console.log('LOGIC_OK');
