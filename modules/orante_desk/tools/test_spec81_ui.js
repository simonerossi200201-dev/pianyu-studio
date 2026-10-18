// 规格 81：图片来源徽章 / 缓存防抖 / 粘贴路径 / 拖放 / 准备前体检。不启浏览器。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');

function fail(msg) {
  console.error('FAIL ' + msg);
  process.exit(1);
}
function ok(name) { console.log('  ok  ' + name); }

const logicSrc = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'parts', 'image-source.js'), 'utf8');
const tasksSrc = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'panels', 'tasks.js'), 'utf8');
const css = fs.readFileSync(path.join(ROOT, 'ui', 'css', 'app.css'), 'utf8');
const html = fs.readFileSync(path.join(ROOT, 'ui', 'index.html'), 'utf8');

if (html.indexOf('image-source.js') < 0) fail('index.html 没引入 image-source.js');
if (tasksSrc.indexOf('tg-img-browse') < 0) fail('tasks.js 没有浏览按钮');
if (tasksSrc.indexOf('jumpPrepBlocked') < 0) fail('tasks.js 没有点还不行落格');
if (tasksSrc.indexOf('looksLikeWinPath') < 0) fail('tasks.js 粘贴没走 Windows 路径判断');
if (tasksSrc.indexOf('folderFromDrop') < 0) fail('tasks.js 没有拖放填路径');
if (tasksSrc.indexOf('dlg===\'prep\'') < 0 && tasksSrc.indexOf('dlg===\'prep\'') < 0) {
  if (tasksSrc.indexOf("dlg==='prep'") < 0) fail('tasks.js 没有准备前体检对话框');
}
if (css.indexOf('tg-img-badge') < 0) fail('css 没有徽章样式');
ok('source hooks');

const sandbox = { window: {}, console, setTimeout, clearTimeout };
sandbox.window = sandbox.window;
vm.createContext(sandbox);
vm.runInContext(logicSrc, sandbox);
const L = sandbox.window.ImageSourceLogic;
if (!L) fail('no ImageSourceLogic');

const local = L.badgeView({ kind: 'local_dir', ok: true, n: 24, n_deep: 24, sample: ['a.jpg'] });
if (L.badgeText(local).indexOf('📁') < 0 || L.badgeText(local).indexOf('本地') < 0 || L.badgeText(local).indexOf('24 张') < 0) {
  fail('local badge ' + L.badgeText(local));
}
ok('输入本地路径 → 📁 本地 · 24 张');

const drive = L.badgeView({ kind: 'drive_folder', ok: true, n: 0, n_deep: 0, sample: [] });
if (L.badgeText(drive).indexOf('☁') < 0 || L.badgeText(drive).indexOf('网盘') < 0) {
  fail('drive badge ' + L.badgeText(drive));
}
ok('Drive 链接 → ☁ 网盘');

const badv = L.badgeView({ kind: 'local_missing', ok: false, note: '找不到这个文件夹', n: 0, n_deep: 0 });
if (badv.tone !== 'bad' || L.badgeText(badv).indexOf('⚠') < 0) {
  fail('bad badge ' + JSON.stringify(badv));
}
ok('乱填 → 红色 ⚠');

if (!L.looksLikeWinPath('D:\\素材\\0920')) fail('D:\\ 应认成 Windows 路径');
if (!L.looksLikeWinPath('\\\\srv\\share\\x')) fail('UNC 应认成 Windows 路径');
if (L.looksLikeWinPath('https://drive.google.com/x')) fail('http 不该当路径');

const prefer = { has: (f) => f === 'image_source' };
const pasted = L.pickPasteValue(
  'image_source',
  'D:\\素材\\0920',
  { href: 'https://drive.google.com/drive/folders/abc', text: '李良素材' },
  prefer,
);
if (pasted.val !== 'D:\\素材\\0920' || pasted.linked) {
  fail('粘贴路径被当成链接 ' + JSON.stringify(pasted));
}
ok('粘贴 D:\\素材\\0920 不会被当成链接');

const quoted = L.pickPasteValue('image_source', '"D:\\素材\\0920"', null, prefer);
if (quoted.val !== 'D:\\素材\\0920') fail('粘贴带引号路径 ' + quoted.val);

const dropFolder = L.folderFromDrop({
  files: [{ path: 'D:\\素材\\0920', name: '0920', isDir: true }],
  text: '',
});
if (!dropFolder || dropFolder.folder !== 'D:\\素材\\0920') fail('拖文件夹 ' + JSON.stringify(dropFolder));
ok('拖一个文件夹进单元格 → 路径被填入');

const dropFiles = L.folderFromDrop({
  files: [
    { path: 'D:\\素材\\0920\\a.jpg', name: 'a.jpg', isDir: false },
    { path: 'D:\\素材\\0920\\b.png', name: 'b.png', isDir: false },
    { path: 'D:\\素材\\0920\\c.webp', name: 'c.webp', isDir: false },
  ],
  text: '',
});
if (!dropFiles || dropFiles.folder !== 'D:\\素材\\0920' || !dropFiles.fromFiles) {
  fail('拖 3 个图片 ' + JSON.stringify(dropFiles));
}
ok('拖 3 个图片文件 → 填入共同父目录');

const items = [
  { tid: 'T0031', ok: true, reason: '素材 24 张（本地 · 复制）' },
  { tid: 'T0032', ok: true, reason: 'Drive 文件夹' },
  { tid: 'T0033', ok: true, reason: '素材 18 张（本地 · 包含子文件夹）' },
  { tid: 'T0034', ok: false, reason: '找不到文件夹 D:\\素材\\0921' },
  { tid: 'T0035', ok: false, reason: '文件夹里 0 张图片（顶层和子文件夹都没有）' },
];
const split = L.splitPrepare(items);
if (split.ready.length !== 3 || split.blocked.length !== 2) {
  fail('体检分组 ' + JSON.stringify(split));
}
if (split.blocked.map(x => x.tid).join(',') !== 'T0034,T0035') fail('还不行的 id');
ok('准备前体检 2 个不行的被列出');

if (tasksSrc.indexOf('focusImageCell') < 0) fail('点还不行没有 focusImageCell');
if (tasksSrc.indexOf('jumpPrepBlocked') < 0) fail('点还不行没有 jumpPrepBlocked');
ok('点还不行 → 焦点落到对应单元格');

async function probeCache() {
  let now = 1000;
  const posted = [];
  const cache = L.createProbe({
    now: () => now,
    cacheMs: 60000,
    debounceMs: 400,
    post: async (url, body) => {
      posted.push(body);
      return { kind: 'local_dir', ok: true, src_ok: true, n: 24, n_deep: 24, sample: ['a.jpg'] };
    },
  });
  let hits = 0;
  cache.schedule('D:\\素材\\0920', false, () => { hits += 1; });
  cache.schedule('D:\\素材\\0920', false, () => { hits += 1; });
  if (posted.length !== 0) fail('防抖前不该发请求 ' + posted.length);
  await cache.flush();
  if (posted.length !== 1) fail('同一路径防抖后应发 1 次，得到 ' + posted.length);
  await cache.probeNow('D:\\素材\\0920', false);
  if (posted.length !== 1) fail('60 秒缓存应命中，又发了 ' + posted.length);
  now += 61000;
  await cache.probeNow('D:\\素材\\0920', false);
  if (posted.length !== 2) fail('过期后应再发，得到 ' + posted.length);
  ok('同一路径 60 秒内重复输入只发一次 probe（缓存 + 防抖）');
}

probeCache().then(() => {
  console.log('spec81 ui ok');
}).catch(e => {
  fail(String(e && e.stack || e));
});
