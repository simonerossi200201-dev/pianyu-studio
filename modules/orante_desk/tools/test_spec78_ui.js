// 规格 78：描述语本地草稿 / 合并加载 / 防抖保存 / 手改角标。不启浏览器。
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
  if (start < 0 || end < 0) fail('no GeminiCardLogic / GeminiDescDrafts block');
  const sandbox = { window: {}, console, setTimeout, clearTimeout };
  vm.createContext(sandbox);
  vm.runInContext(geminiJs.slice(start, end), sandbox);
  if (!sandbox.window.GeminiDescDrafts) fail('GeminiDescDrafts not defined');
  if (!sandbox.window.GeminiCardLogic) fail('GeminiCardLogic not defined');
  return sandbox.window;
}

function memStorage() {
  const m = {};
  return {
    get length() { return Object.keys(m).length; },
    key(i) { return Object.keys(m)[i]; },
    getItem(k) { return Object.prototype.hasOwnProperty.call(m, k) ? m[k] : null; },
    setItem(k, v) { m[k] = String(v); },
    removeItem(k) { delete m[k]; },
  };
}

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
      const due = timers.filter(x => x.t <= clock).sort((a, b) => a.t - b.t);
      due.forEach(x => {
        const i = timers.indexOf(x);
        if (i >= 0) timers.splice(i, 1);
        x.fn();
      });
    },
  };
}

const w = load();
const D = w.GeminiDescDrafts;
const L = w.GeminiCardLogic;

// A. 合并式加载：三个框都不失焦，刷新后草稿全在
const drafts = {};
D.setDraft(drafts, 'p01', 'desc', 'AAA 没失焦');
D.setDraft(drafts, 'p02', 'desc', 'BBB 没失焦');
D.setDraft(drafts, 'p03', 'desc', 'CCC 没失焦');
const server = [
  { p: 'p01', desc: '仓库1' },
  { p: 'p02', desc: '仓库2' },
  { p: 'p03', desc: '仓库3' },
];
eq('A no-blur survives load', D.draftOf(drafts, server[0], 'desc'), 'AAA 没失焦');
eq('three drafts all survive', [
  D.draftOf(drafts, server[0], 'desc'),
  D.draftOf(drafts, server[1], 'desc'),
  D.draftOf(drafts, server[2], 'desc'),
], ['AAA 没失焦', 'BBB 没失焦', 'CCC 没失焦']);
eq('no draft follows repo', D.draftOf(drafts, { p: 'p04', desc: '仓库4' }, 'desc'), '仓库4');

// A 失焦已存 → 草稿删掉，刷新跟仓库
D.clearDraft(drafts, 'p01', 'desc');
server[0].desc = 'AAA 已保存';
eq('saved A follows repo after load', D.draftOf(drafts, server[0], 'desc'), 'AAA 已保存');
eq('B still draft', D.draftOf(drafts, server[1], 'desc'), 'BBB 没失焦');

// localStorage 崩溃恢复 + 过期清理
const st = memStorage();
D.persist('T78', { 'p01:desc': '关软件前的字' }, st, 1000);
const got = D.restore('T78', st, 1500);
eq('restore from ls', got, { 'p01:desc': '关软件前的字' });
const expired = D.restore('T78', st, 1000 + D.TTL_MS + 1);
eq('expired pruned', expired, {});

// 手改角标 + 右键恢复自动
const flagsManual = L.menuFlags({ p: 'p01', desc: '手改', desc_manual: true, exists: true }, [], 3);
if (flagsManual.restoreAuto) fail('restoreAuto should be on when desc_manual');
ok('restoreAuto clickable when manual');
const flagsAuto = L.menuFlags({ p: 'p01', desc: '自动', exists: true }, [], 3);
if (!flagsAuto.restoreAuto) fail('restoreAuto should be grey when not manual');
const titleOff = L.menuTitle('restoreAuto', flagsAuto, t);
if (titleOff.indexOf('不是手改') < 0) fail('grey title got=' + titleOff);
ok('restoreAuto grey + 不是手改');

if (geminiJs.indexOf('desc-manual') < 0) fail('no ✎ markup');
if (geminiJs.indexOf("t('gemini.manual_tip')") < 0) fail('no manual tip');
if (geminiJs.indexOf('gemini.ctx.restore_auto') < 0) fail('no restore auto menu item');
if (geminiJs.indexOf('forceManual') < 0) fail('no force checkbox');
ok('template has ✎ / restore / force');

(async function () {
  const clock = makeClock();
  const posts = [];
  const queue = [];
  const d2 = {};
  const fieldSave = {};
  const im = { p: 'p01', desc: '旧' };
  const saver = D.makeSaver({
    drafts: d2, fieldSave, saveMs: 700, fadeMs: 2000,
    schedule: clock.schedule, unschedule: clock.unschedule,
    persist() {},
    post(imX, field, value) {
      posts.push({ field, value });
      return new Promise((resolve, reject) => queue.push({ resolve, reject }));
    },
  });

  saver.onInput(im, 'desc', '新字');
  eq('status dirty', D.saveClass(fieldSave['p01:desc']), 'is-dirty');
  eq('label dirty', D.saveLabel(fieldSave['p01:desc'], t), t('gemini.save.dirty'));
  clock.advance(699);
  eq('no post before 700', posts.length, 0);
  clock.advance(1);
  await Promise.resolve();
  await Promise.resolve();
  eq('status saving', D.saveClass(fieldSave['p01:desc']), 'is-saving');
  eq('label saving', D.saveLabel(fieldSave['p01:desc'], t), t('gemini.save.saving'));
  eq('one post after 700', posts.length, 1);
  queue[0].resolve();
  await Promise.resolve();
  await Promise.resolve();
  eq('status ok', D.saveClass(fieldSave['p01:desc']), 'is-ok');
  eq('label ok', D.saveLabel(fieldSave['p01:desc'], t), t('gemini.save.ok_short'));
  eq('draft cleared after save', D.hasDraft(d2, 'p01', 'desc'), false);
  clock.advance(2000);
  eq('ok faded', D.saveClass(fieldSave['p01:desc']), '');

  // 再输入 → 又一次；中途失焦 → 防抖取消、只发一次
  const posts2 = [];
  const queue2 = [];
  const d3 = {};
  const fieldSave3 = {};
  const im3 = { p: 'p02', desc: '旧2' };
  const clock2 = makeClock();
  const saver2 = D.makeSaver({
    drafts: d3, fieldSave: fieldSave3, saveMs: 700, fadeMs: 2000,
    schedule: clock2.schedule, unschedule: clock2.unschedule,
    persist() {},
    post(imX, field, value) {
      posts2.push({ field, value });
      return new Promise((resolve, reject) => queue2.push({ resolve, reject }));
    },
  });
  saver2.onInput(im3, 'desc', '一次');
  clock2.advance(700);
  await Promise.resolve();
  await Promise.resolve();
  eq('debounce first post', posts2.length, 1);
  queue2[0].resolve();
  await Promise.resolve();
  await Promise.resolve();
  im3.desc = '一次';
  saver2.onInput(im3, 'desc', '二次');
  clock2.advance(700);
  await Promise.resolve();
  await Promise.resolve();
  eq('debounce second post', posts2.length, 2);
  queue2[1].resolve();
  await Promise.resolve();
  await Promise.resolve();

  const posts3 = [];
  const queue3 = [];
  const d4 = {};
  const fieldSave4 = {};
  const im4 = { p: 'p03', desc: '旧3' };
  const clock3 = makeClock();
  const saver3 = D.makeSaver({
    drafts: d4, fieldSave: fieldSave4, saveMs: 700, fadeMs: 2000,
    schedule: clock3.schedule, unschedule: clock3.unschedule,
    persist() {},
    post(imX, field, value) {
      posts3.push({ field, value });
      return new Promise((resolve, reject) => queue3.push({ resolve, reject }));
    },
  });
  saver3.onInput(im4, 'desc', '打到一半');
  clock3.advance(300);
  eq('blur cancels debounce no post yet', posts3.length, 0);
  const blurP = saver3.onBlur(im4, 'desc', '打到一半');
  await Promise.resolve();
  await Promise.resolve();
  eq('blur posts once', posts3.length, 1);
  eq('no pending timer', saver3.pendingKeys(), []);
  queue3[0].resolve();
  await blurP;

  // 保存失败：红字、草稿留下
  const postsF = [];
  const dF = {};
  const fieldF = {};
  const imF = { p: 'p09', desc: '旧' };
  const clockF = makeClock();
  const saverF = D.makeSaver({
    drafts: dF, fieldSave: fieldF, saveMs: 700, fadeMs: 2000,
    schedule: clockF.schedule, unschedule: clockF.unschedule,
    persist() {},
    post() { postsF.push(1); return Promise.reject({ message: '磁盘满了' }); },
  });
  saverF.onInput(imF, 'desc', '没存上');
  clockF.advance(700);
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  eq('fail class', D.saveClass(fieldF['p09:desc']), 'is-fail');
  eq('fail label', D.saveLabel(fieldF['p09:desc'], t), t('gemini.save.fail'));
  eq('fail keeps draft', dF['p09:desc'], '没存上');

  console.log('LOGIC_OK');
})().catch(e => {
  console.error(e);
  process.exit(1);
});
