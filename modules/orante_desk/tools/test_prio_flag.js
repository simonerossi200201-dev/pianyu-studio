// 规格 19：旗子纯逻辑（颜色 / SVG / 防溢出），不启浏览器。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
let fail = 0;
function ok(m) { console.log('  OK  ', m); }
function bad(m) { fail += 1; console.log('  FAIL', m); }

const ctx = {
  window: {},
  store: { tasks: [{ id: 'T0001', priority: 0 }] },
  API: { post: async (_u, body) => ({ ok: true, priority: body.top ? 1 : body.priority }) },
  t: (k, vars) => {
    let s = k;
    if (vars) for (const [a, b] of Object.entries(vars)) s = s.split('{' + a + '}').join(b);
    return s;
  },
  document: {
    createElement: () => ({ appendChild() {}, parentNode: null, style: {} }),
    body: { appendChild() {} },
    addEventListener() {},
    removeEventListener() {},
  },
  Vue: {
    createApp() { return { mount() {}, unmount() {} }; },
  },
};
ctx.window = ctx;
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(ROOT, 'ui/js/parts/flag.js'), 'utf8'), ctx);

const L = ctx.window.PrioFlagLogic;
if (!L) {
  console.log('  FAIL  PrioFlagLogic 没挂上');
  process.exit(1);
}

const COLORS = [
  '#ff5f56', '#ff9f43', '#ffd166', '#4ade80', '#2dd4bf',
  '#60a5fa', '#818cf8', '#c084fc', '#f472b6', '#a3e635',
];
COLORS.forEach((c, i) => {
  const n = i + 1;
  const got = L.colorOf(n);
  if (got === c) ok('旗' + n + ' ' + c);
  else bad('旗' + n + ' 期望 ' + c + ' 实际 ' + got);
});
if (L.colorOf(11) === COLORS[0]) ok('旗11 循环回旗1'); else bad('旗11 循环');
if (L.colorOf(0) === '#6b7280') ok('自动灰 #6b7280'); else bad('自动色 ' + L.colorOf(0));
if (L.clampPrio(9999) === 0) ok('9999 当自动'); else bad('9999 -> ' + L.clampPrio(9999));
if (L.clampPrio(1000) === 999) ok('>999 卡住'); else bad('1000 -> ' + L.clampPrio(1000));

const s1 = L.svgFlag(1);
if (s1.includes('<svg') && s1.includes('viewBox="0 0 16 18"') && s1.includes('>1<')) ok('旗1 SVG 含数字');
else bad('旗1 SVG');
const s0 = L.svgFlag(0);
if (s0.includes('fill="none"') && !s0.includes('>0<')) ok('空心灰旗不写数字');
else bad('自动 SVG');
const s100 = L.svgFlag(100);
if (!s100.includes('>100<')) ok('≥100 旗面不写数字');
else bad('100 SVG 不该写 100');
const mini = L.miniFlag(3);
if (mini.includes('width="12"')) ok('顶栏小旗 12px'); else bad('小旗尺寸');

const html = L.buttonHtml(2, 'running', 'T0003');
if (html.includes('pf-btn') && html.includes('is-run') && html.includes('T0003')) ok('buttonHtml running');
else bad('buttonHtml ' + html.slice(0, 80));
const autoBtn = L.buttonHtml(0, 'queued', 'T0004');
if (autoBtn.includes('pf-auto') && autoBtn.includes('is-wait')) ok('buttonHtml auto queued');
else bad('auto queued 按钮');

const p1 = L.placeMenu(2000, 2000, 248, 180, 1280, 720, 8);
if (p1.left + 248 <= 1280 - 8 && p1.top + 180 <= 720 - 8) ok('右下角不溢出 ' + JSON.stringify(p1));
else bad('右下溢出 ' + JSON.stringify(p1));
const p2 = L.placeMenu(-40, -20, 248, 180, 1280, 720, 8);
if (p2.left >= 8 && p2.top >= 8) ok('左上夹到垫 8px'); else bad('左上 ' + JSON.stringify(p2));

const css = fs.readFileSync(path.join(ROOT, 'ui/css/app.css'), 'utf8');
if (/@media\s*\(\s*prefers-reduced-motion:\s*reduce\s*\)[\s\S]*flag-wave/.test(css)
    || /prefers-reduced-motion[\s\S]*animation:\s*none/.test(css)) {
  ok('reduced-motion 停动画');
} else {
  bad('reduced-motion 规则');
}

process.exit(fail ? 1 : 0);
