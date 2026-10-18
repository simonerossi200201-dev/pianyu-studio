// 规格 69：设置页渲染主标签是人话，不是键名。不启浏览器。
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

function loadLogic() {
  const src = fs.readFileSync(path.join(ROOT, 'ui', 'js', 'parts', 'settings.js'), 'utf8');
  const sandbox = { window: {}, Vue: { reactive() { return {}; }, ref() { return { value: null }; } } };
  sandbox.window = sandbox.window;
  sandbox.window.Vue = sandbox.Vue;
  sandbox.window.DescCardEditor = {};
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  if (!sandbox.window.SettingsLogic) fail('no SettingsLogic');
  return sandbox.window.SettingsLogic;
}

const zh = JSON.parse(fs.readFileSync(path.join(ROOT, 'orante', 'i18n', 'zh.json'), 'utf8'));
function tZh(k) {
  if (zh[k] !== undefined) return zh[k];
  if (String(k).indexOf('set.key.') === 0 || String(k).indexOf('set.hint.') === 0) return '';
  return k;
}

const L = loadLogic();
const defaults = {
  'workspace.root': 'D:\\ws',
  'workspace.by_date': true,
  'workspace.ask_each_batch': true,
  'archive.auto_on': false,
  'archive.auto_days': 7,
  'split.flow.max_chars': 145,
  'split.flow.dur_4s_max_words': 8,
  'split.flow.dur_6s_max_words': 13,
  'split.flow.allow_10s': false,
  'split.grok.max_chars_10s': 187,
  'split.grok.max_chars_15s': 250,
  'split.grok.dur_6s_max_chars': 112,
  'gemini.api_key': '',
  'gemini.model': 'gemini-3.5-flash-lite',
  'gemini.mode': 'direct',
  'gemini.concurrency': 4,
  'ai.max_workers': 16,
  'flow.image_model_key': '',
  'flow.allow_paid': false,
  'flow.credit_guard': true,
  'expand.min_images': 6,
  'expand.per_round': 4,
  'expand.flip_replacements': true,
  'expand.dup_dist': 40,
  'expand.no_frontal': true,
  'track.hold': 3,
  'track.jitter': 1,
  'track.no_adjacent_repeat': true,
  'images.rename_on_drive': false,
  'images.local_mode': 'copy',
  'images.local_move_confirmed': false,
  'ui.lang': 'zh',
  'ui.open_mode': 'window',
  'ui.port': 9400,
  'ui.current_task': '',
  'track.strip_view': 'pool',
  'ui.bench.density': 'comfy',
  'ui.bench.zoom': 100,
  'engine.flow.mode': 'api',
  'engine.flow.model': 'Veo 3.1 - Lite [Lower Priority]',
  'engine.flow.max_concurrent': 5,
  'engine.grok.max_concurrent': 4,
  'generate.mode': 'first_frame',
  'setup.done': true,
  'prompts.desc': 'x',
};

const flowModels = [
  { id: 'Veo 3.1 - Lite [Lower Priority]', paid: false, enabled: true },
  { id: 'Veo 3.1 - Lite', paid: true, enabled: false },
  { id: 'Veo 3.1 - Fast', paid: true, enabled: false },
  { id: 'Veo 3.1 - Quality', paid: true, enabled: false },
];

if (!L.HIDDEN_KEYS.includes('ui.current_task')) fail('HIDDEN_KEYS missing ui.current_task');
if (!L.HIDDEN_KEYS.includes('setup.done')) fail('HIDDEN_KEYS missing setup.done');
if (!L.isHidden('prompts.desc')) fail('prompts.* should be hidden');
if (!L.isHidden('ui.current_task')) fail('ui.current_task should be hidden');
if (!L.HIDDEN_KEYS.includes('images.local_move_confirmed')) fail('HIDDEN_KEYS missing images.local_move_confirmed');
ok('HIDDEN_KEYS');

const vis = L.visibleKeys(defaults);
if (vis.includes('ui.current_task')) fail('visible has ui.current_task');
if (vis.includes('setup.done')) fail('visible has setup.done');
if (vis.includes('prompts.desc')) fail('visible has prompts.desc');
if (vis.includes('images.rename_on_drive')) fail('visible has images.rename_on_drive (not in C table)');
if (vis.includes('images.local_move_confirmed')) fail('visible has images.local_move_confirmed');
if (!vis.includes('images.local_mode')) fail('images.local_mode should show under workspace');
if (vis.includes('chain.upscale_every')) fail('visible has chain.upscale_every');
if (!vis.includes('ai.max_workers')) fail('ai.max_workers should show under 多模态接口');
if (!vis.includes('flow.allow_paid')) fail('flow.allow_paid should show under expand');
if (!vis.includes('generate.mode')) fail('generate.mode should show');
ok('visible keys');

const html = L.renderDom(defaults, tZh, flowModels);
if (html.indexOf('ui.current_task') >= 0) fail('DOM has ui.current_task');
if (html.indexOf('setup.done') >= 0) fail('DOM has setup.done');
if (html.indexOf('prompts.desc') >= 0) fail('DOM has prompts.desc');

const names = [];
const re = /<label class="kv-name">([\s\S]*?)<\/label>/g;
let m;
while ((m = re.exec(html))) names.push(m[1].replace(/<[^>]+>/g, ''));
if (!names.length) fail('no kv-name labels');
names.forEach(n => {
  if (n.indexOf('.') >= 0) fail('label has dot (looks like a key): ' + n);
  if (!HAN.test(n)) fail('label has no Chinese: ' + n);
});
ok('labels are Chinese, no dots n=' + names.length);

const SELECT_COUNTS = {
  'gemini.mode': 2,
  'flow.image_model_key': 2,
  'track.strip_view': 2,
  'engine.flow.mode': 2,
  'generate.mode': 3,
  'ui.lang': 2,
  'ui.open_mode': 2,
  'ui.bench.density': 3,
  'images.local_mode': 3,
};
Object.keys(SELECT_COUNTS).forEach(k => {
  const opts = L.optionsOf(k, tZh, flowModels);
  if (opts.length !== SELECT_COUNTS[k]) {
    fail(k + ' options want ' + SELECT_COUNTS[k] + ' got ' + opts.length);
  }
  opts.forEach(o => {
    if (k === 'ui.lang' && o.v === 'en') return;
    if (!HAN.test(o.label)) fail(k + ' option not Chinese: ' + o.label);
  });
});
const models = L.optionsOf('engine.flow.model', tZh, flowModels);
if (models.length !== 4) fail('engine.flow.model want 4 got ' + models.length);
ok('select option counts + Chinese');

if (L.controlOf('gemini.mode', defaults) !== 'select') fail('gemini.mode not select');
if (L.controlOf('engine.flow.mode', defaults) !== 'select') fail('engine.flow.mode not select');
if (L.controlOf('track.strip_view', defaults) !== 'select') fail('track.strip_view not select');
if (L.controlOf('ui.open_mode', defaults) !== 'select') fail('ui.open_mode not select');
if (L.controlOf('generate.mode', defaults) !== 'select') fail('generate.mode not select');
if (L.controlOf('workspace.root', defaults) !== 'path') fail('workspace.root not path');
if (L.controlOf('gemini.api_key', defaults) !== 'secret') fail('gemini.api_key not secret');
if (L.controlOf('expand.no_frontal', defaults) !== 'bool') fail('no_frontal not bool');
if (L.controlOf('expand.dup_dist', defaults) !== 'num') fail('dup_dist not num');
ok('control types');

const en = JSON.parse(fs.readFileSync(path.join(ROOT, 'orante', 'i18n', 'en.json'), 'utf8'));
function tEn(k) {
  if (en[k] !== undefined) return en[k];
  if (zh[k] !== undefined) return zh[k];
  if (String(k).indexOf('set.key.') === 0 || String(k).indexOf('set.hint.') === 0) return '';
  return k;
}
const labEnMissing = tEn('set.key.__no_such__');
if (labEnMissing === 'set.key.__no_such__') fail('en fallback returned the key name');
const labEnDup = tEn('set.key.expand.dup_dist');
if (labEnDup.indexOf('.') >= 0 && labEnDup.indexOf('expand.') >= 0) fail('en label fell back to key');
if (!labEnDup) fail('en dup_dist label empty');
ok('en fallback not key name');

console.log('spec69 ui ok');
