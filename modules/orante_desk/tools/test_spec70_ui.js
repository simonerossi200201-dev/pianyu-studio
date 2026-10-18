// 规格 70：覆盖库卡片显示。不启浏览器；布局量宽走 Playwright。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const HAN = /[\u4e00-\u9fff]/;

function fail(msg) {
  console.error('FAIL ' + msg);
  process.exit(1);
}
function ok(name) { console.log('  ok  ' + name); }

const js = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'panels', 'gemini.js'), 'utf8');
const css = fs.readFileSync(path.join(ROOT, 'ui', 'css', 'app.css'), 'utf8');
const zh = JSON.parse(fs.readFileSync(path.join(ROOT, 'orante', 'i18n', 'zh.json'), 'utf8'));

if (/<table\s+class="cov-tbl"/.test(js)) fail('gemini.js still has <table class="cov-tbl">');
if (js.indexOf('class="cov-list"') < 0) fail('no cov-list');
if (js.indexOf('class="cov-row"') < 0) fail('no cov-row');
if (js.indexOf('class="cov-name"') < 0) fail('no cov-name');
if (js.indexOf('class="cov-text"') < 0) fail('no cov-text');
if (js.indexOf('class="cov-sum"') < 0) fail('no cov-sum');
ok('no table.cov-tbl, has cov-list cards');

if (js.indexOf("onBlockInput('expand')") < 0) fail('onBlockInput(expand) gone');
if (js.indexOf("onBlockBlur('expand')") < 0) fail('onBlockBlur(expand) gone');
if (js.indexOf('onCovTextInput') < 0) fail('onCovTextInput gone');
if (js.indexOf('moveRow(coverage.cameras, i, -1)') < 0) fail('moveRow cameras up gone');
if (js.indexOf('moveRow(coverage.cameras, i, 1)') < 0) fail('moveRow cameras down gone');
if (js.indexOf('delRow(coverage.cameras, i)') < 0) fail('delRow cameras gone');
if (js.indexOf('moveRow(coverage.actions, i, -1)') < 0) fail('moveRow actions up gone');
if (js.indexOf('moveRow(coverage.actions, i, 1)') < 0) fail('moveRow actions down gone');
if (js.indexOf('delRow(coverage.actions, i)') < 0) fail('delRow actions gone');
if (js.indexOf('function addCam()') < 0) fail('addCam gone');
if (js.indexOf('function addAct()') < 0) fail('addAct gone');
if (js.indexOf("v-model=\"row.frontal\"") < 0) fail('row.frontal gone');
ok('save wiring + move/del/add still there');

if (css.indexOf('max-height:360px') >= 0 && /cov-tbl-wrap\{[^}]*max-height:360px/.test(css)) {
  fail('cov-tbl-wrap still has max-height:360px');
}
if (css.indexOf('.cov-list') < 0) fail('css missing .cov-list');
if (css.indexOf('min-width:120px') < 0) fail('css name min-width not 120px');
if (css.indexOf('font-size:13px') < 0) fail('css missing 13px');
if (css.indexOf('font-size:12.5px') < 0) fail('css missing summary 12.5px');
ok('css cards, no inner 360 scroll');

const LABELS = {
  'gemini.cov.id': '编号',
  'gemini.cov.name': '名称',
  'gemini.cov.class': '类别',
  'gemini.cov.frontal': '正脸机位',
  'gemini.cov.rank': '权重',
  'gemini.cov.phase': '阶段',
  'gemini.cov.text': '提示词',
};
Object.keys(LABELS).forEach(k => {
  if (zh[k] !== LABELS[k]) fail(k + ' want ' + LABELS[k] + ' got ' + zh[k]);
  if (String(zh[k]).toLowerCase() === 'rank') fail(k + ' still rank');
});
if (String(zh['gemini.cov.rank']).toLowerCase().indexOf('rank') >= 0) fail('rank label still contains rank');
const TITLES = [
  'gemini.cov.id.title', 'gemini.cov.name.title', 'gemini.cov.class.title',
  'gemini.cov.frontal.title', 'gemini.cov.rank.title', 'gemini.cov.phase.title',
  'gemini.cov.text.title',
];
TITLES.forEach(k => {
  if (!zh[k] || !HAN.test(zh[k])) fail('missing title ' + k);
});
if (zh['gemini.cov.class.title'].indexOf('主力') < 0) fail('class title missing 主力');
if (zh['gemini.cov.class.title'].indexOf('侧面') < 0) fail('class title missing 侧面');
if (zh['gemini.cov.class.A'] !== '主力') fail('class.A');
if (zh['gemini.cov.class.B'] !== '侧面') fail('class.B');
if (zh['gemini.cov.class.C'] !== '空间') fail('class.C');
if (zh['gemini.cov.class.D'] !== '收尾') fail('class.D');
if (zh['gemini.cov.phase.start'] !== '起势') fail('phase.start');
if (zh['gemini.cov.phase.develop'] !== '展开') fail('phase.develop');
if (zh['gemini.cov.phase.hold'] !== '保持') fail('phase.hold');
if (zh['gemini.cov.phase.resolve'] !== '收束') fail('phase.resolve');
if (zh['gemini.cov.collapse'] !== '收起') fail('collapse');
if (zh['gemini.cov.expand_btn'] !== '展开') fail('expand_btn');
ok('zh labels + titles');

function loadLogic() {
  const start = js.indexOf('window.CoverageEditorLogic');
  const end = js.indexOf('window.Panel_gemini');
  if (start < 0 || end < 0) fail('no CoverageEditorLogic block');
  const sandbox = { window: {} };
  sandbox.window = sandbox.window;
  vm.createContext(sandbox);
  vm.runInContext(js.slice(start, end), sandbox);
  if (!sandbox.window.CoverageEditorLogic) fail('CoverageEditorLogic not defined');
  return sandbox.window.CoverageEditorLogic;
}

const L = loadLogic();
function tZh(k) { return zh[k] !== undefined ? zh[k] : k; }

const sumCam = L.summaryCam({ id: '04', name: '观众视角', class: 'B', rank: 3 }, tZh);
if (sumCam.indexOf('04') < 0 || sumCam.indexOf('观众视角') < 0 || sumCam.indexOf('B') < 0 || sumCam.indexOf('权重 3') < 0) {
  fail('summaryCam ' + sumCam);
}
if (/\brank\b/i.test(sumCam)) fail('summary has rank: ' + sumCam);
ok('summaryCam ' + sumCam);

const sumAct = L.summaryAct({ id: '03', name: '右手抬起', phase: 'develop', rank: 3 }, tZh);
if (sumAct.indexOf('右手抬起') < 0 || sumAct.indexOf('展开') < 0 || sumAct.indexOf('权重 3') < 0) {
  fail('summaryAct ' + sumAct);
}
ok('summaryAct ' + sumAct);

if (L.classOpt('A', tZh) !== 'A · 主力') fail('classOpt A ' + L.classOpt('A', tZh));
if (L.classOpt('B', tZh) !== 'B · 侧面') fail('classOpt B');
if (L.phaseLabel('start', tZh) !== '起势') fail('phase start');
if (L.phaseLabel('resolve', tZh) !== '收束') fail('phase resolve');
ok('class/phase options');

const mem = {
  _d: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._d, k) ? this._d[k] : null; },
  setItem(k, v) { this._d[k] = String(v); },
};
if (L.readOpen('cameras', mem) !== false) fail('default should be collapsed');
L.writeOpen('cameras', true, mem);
if (L.readOpen('cameras', mem) !== true) fail('writeOpen cameras not kept');
L.writeOpen('actions', true, mem);
if (L.readOpen('actions', mem) !== true) fail('writeOpen actions not kept');
L.writeOpen('cameras', false, mem);
if (L.readOpen('cameras', mem) !== false) fail('collapse not kept');
if (mem._d[L.LS.cameras] !== '0') fail('ls value not 0');
ok('localStorage expand/collapse');

const headerBits = [zh['gemini.cov.id'], zh['gemini.cov.name'], zh['gemini.cov.class'],
  zh['gemini.cov.frontal'], zh['gemini.cov.rank'], zh['gemini.cov.phase'], zh['gemini.cov.text']];
headerBits.forEach(s => {
  if (/\brank\b/i.test(s)) fail('header still has rank: ' + s);
});
ok('headers have no rank');

console.log('spec70 ui ok');
