// 设置页字段表：中文名走 i18n，英文键名只在悬停时露。测试直接挂这份。
window.SettingsLogic = (function () {
  const HIDDEN_KEYS = ['ui.current_task', 'setup.done', 'suite.current', 'images.local_move_confirmed', 'ui.float_icon.pos'];
  const GROUPS = ['workspace', 'archive', 'split', 'ai', 'gemini', 'expand', 'track', 'engine', 'studio', 'generate', 'suite', 'ui'];
  const GROUP_KEYS = {
    workspace: ['workspace.root', 'workspace.by_date', 'workspace.ask_each_batch', 'images.local_mode'],
    archive: ['archive.auto_on', 'archive.auto_days'],
    split: [
      'split.flow.max_chars', 'split.flow.dur_4s_max_words', 'split.flow.dur_6s_max_words',
      'split.no_copy_duration.flow', 'split.no_copy_duration.grok', 'split.grok.max_chars_10s', 'split.grok.max_chars_15s',
      'split.grok.dur_6s_max_chars',
    ],
    ai: ['ai.max_workers'],
    gemini: ['gemini.api_key', 'gemini.model', 'gemini.mode', 'gemini.concurrency'],
    expand: [
      'flow.image_model_key', 'flow.allow_paid', 'flow.credit_guard',
      'expand.min_images', 'expand.per_round', 'expand.flip_replacements',
      'expand.dup_dist', 'expand.no_frontal',
    ],
    track: ['track.hold', 'track.jitter', 'track.no_adjacent_repeat', 'track.strip_view'],
    engine: ['engine.flow.mode', 'engine.flow.model', 'engine.flow.max_concurrent', 'engine.grok.max_concurrent',
             'engine.pack.multi_task', 'engine.pack.max_tasks', 'engine.flow.throttle_probe_min',
             'engine.flow.submit_gap_sec', 'flow.human_warmup'],
    // 规格 98：本地模型 Studio Zero（LTX）— 优先开关排第一
    studio: ['engine.studio.fallback', 'engine.studio.enabled', 'engine.studio.quality', 'engine.studio.lock_last',
             'engine.studio.max_inflight', 'engine.studio.match_flow_spec', 'engine.studio.url', 'engine.studio.launcher'],
    generate: ['generate.mode', 'chain.desc_policy', 'tools.ffmpeg'],
    suite: ['suite.autosave_on_switch'],
    ui: ['ui.lang', 'ui.open_mode', 'ui.port', 'ui.bench.density', 'ui.bench.zoom',
         'ui.float_icon', 'ui.float_icon.size', 'ui.float_icon.edge_hide'],
  };
  const SELECT = {
    'gemini.mode': [
      { v: 'direct', k: 'set.opt.gemini.mode.direct' },
      { v: 'page', k: 'set.opt.gemini.mode.page' },
    ],
    'flow.image_model_key': [
      { v: '', k: 'set.opt.flow.image_model_key.default' },
      { v: 'NARWHAL', k: 'set.opt.flow.image_model_key.narwhal' },
    ],
    'track.strip_view': [
      { v: 'pool', k: 'set.opt.track.strip_view.pool' },
      { v: 'shots', k: 'set.opt.track.strip_view.shots' },
    ],
    'engine.flow.mode': [
      { v: 'api', k: 'set.opt.engine.flow.mode.api' },
      { v: 'dom', k: 'set.opt.engine.flow.mode.dom' },
    ],
    'generate.mode': [
      { v: 'first_frame', k: 'set.opt.generate.mode.first_frame' },
      { v: 'anchor_bridge', k: 'set.opt.generate.mode.anchor_bridge' },
      { v: 'tail_chain', k: 'set.opt.generate.mode.tail_chain' },
    ],
    'engine.studio.quality': [
      { v: 'balanced', k: 'set.opt.engine.studio.quality.balanced' },
      { v: 'fast', k: 'set.opt.engine.studio.quality.fast' },
      { v: 'high', k: 'set.opt.engine.studio.quality.high' },
      { v: 'best', k: 'set.opt.engine.studio.quality.best' },
    ],
    'engine.studio.lock_last': [
      { v: 'auto', k: 'set.opt.engine.studio.lock_last.auto' },
      { v: 'always', k: 'set.opt.engine.studio.lock_last.always' },
      { v: 'never', k: 'set.opt.engine.studio.lock_last.never' },
    ],
    'chain.desc_policy': [
      { v: 'never', k: 'set.opt.chain.desc_policy.never' },
      { v: 'always', k: 'set.opt.chain.desc_policy.always' },
      { v: 'auto', k: 'set.opt.chain.desc_policy.auto' },
    ],
    'ui.lang': [
      { v: 'zh', k: 'set.opt.ui.lang.zh' },
      { v: 'en', k: 'set.opt.ui.lang.en' },
    ],
    'ui.open_mode': [
      { v: 'window', k: 'set.opt.ui.open_mode.window' },
      { v: 'browser', k: 'set.opt.ui.open_mode.browser' },
    ],
    'ui.bench.density': [
      { v: 'compact', k: 'set.opt.ui.bench.density.compact' },
      { v: 'comfy', k: 'set.opt.ui.bench.density.comfy' },
      { v: 'large', k: 'set.opt.ui.bench.density.large' },
    ],
    'images.local_mode': [
      { v: 'copy', k: 'set.opt.images.local_mode.copy' },
      { v: 'copy_mark', k: 'set.opt.images.local_mode.copy_mark' },
      { v: 'move', k: 'set.opt.images.local_mode.move' },
    ],
  };
  const UNITS = {
    'archive.auto_days': 'set.unit.archive.auto_days',
    'ui.float_icon.size': 'set.unit.ui.float_icon.size',
    'engine.pack.max_tasks': 'set.unit.engine.pack.max_tasks',
    'engine.flow.throttle_probe_min': 'set.unit.engine.flow.throttle_probe_min',
    'engine.flow.submit_gap_sec': 'set.unit.engine.flow.submit_gap_sec',
    'expand.dup_dist': 'set.unit.expand.dup_dist',
    'ui.bench.zoom': 'set.unit.ui.bench.zoom',
  };
  const NUM_RANGE = {
    'expand.dup_dist': [0, 255],
    'ui.bench.zoom': [40, 200],
    'engine.studio.max_inflight': [1, 4],
    'ui.port': [1, 65535],
    'ui.float_icon.size': [40, 256],
    'engine.pack.max_tasks': [2, 5],
    'engine.flow.throttle_probe_min': [0, 60],
    'engine.flow.submit_gap_sec': [0, 60],
  };
  const PATH_KEYS = ['workspace.root'];
  const SECRET_KEYS = ['gemini.api_key'];

  function isHidden(k) {
    if (!k) return true;
    if (HIDDEN_KEYS.indexOf(k) >= 0) return true;
    if (String(k).indexOf('prompts.') === 0) return true;
    return false;
  }
  function keysOf(g, defaults) {
    const listed = GROUP_KEYS[g] || [];
    const src = defaults || {};
    return listed.filter(k => (k in src) && !isHidden(k));
  }
  function typeOf(k, defaults) {
    const v = (defaults || {})[k];
    return typeof v === 'boolean' ? 'bool' : typeof v === 'number' ? 'num' : 'str';
  }
  function controlOf(k, defaults) {
    if (SECRET_KEYS.indexOf(k) >= 0) return 'secret';
    if (PATH_KEYS.indexOf(k) >= 0) return 'path';
    if (k === 'engine.flow.model' || SELECT[k]) return 'select';
    const tp = typeOf(k, defaults);
    if (tp === 'bool' || tp === 'num') return tp;
    return 'str';
  }
  function optionsOf(k, tFn, flowModels) {
    if (k === 'engine.flow.model') {
      const paid = (tFn && tFn('acc.model.paid_mark')) || '（扣额度）';
      return (flowModels || []).map(m => ({
        v: m.id,
        label: m.id + (m.paid ? paid : ''),
        disabled: m.enabled === false,
      }));
    }
    const list = SELECT[k];
    if (!list) return [];
    return list.map(o => ({ v: o.v, label: tFn ? tFn(o.k) : o.k, disabled: false }));
  }
  function visibleKeys(defaults) {
    const out = [];
    GROUPS.forEach(g => keysOf(g, defaults).forEach(k => out.push(k)));
    return out;
  }
  function labelOf(k, tFn) {
    const fn = tFn || (typeof window !== 'undefined' && window.t);
    const v = fn ? fn('set.key.' + k) : '';
    if (v && v !== 'set.key.' + k) return v;
    return '';
  }
  function hintOf(k, tFn) {
    const fn = tFn || (typeof window !== 'undefined' && window.t);
    const v = fn ? fn('set.hint.' + k) : '';
    if (v && v !== 'set.hint.' + k) return v;
    return '';
  }
  function unitOf(k, tFn) {
    const ik = UNITS[k];
    if (!ik) return '';
    const fn = tFn || (typeof window !== 'undefined' && window.t);
    const v = fn ? fn(ik) : '';
    if (v && v !== ik) return v;
    return '';
  }
  function renderDom(defaults, tFn, flowModels) {
    const parts = [];
    GROUPS.forEach(g => {
      keysOf(g, defaults).forEach(k => {
        const label = labelOf(k, tFn);
        const hint = hintOf(k, tFn);
        const unit = unitOf(k, tFn);
        const ctrl = controlOf(k, defaults);
        let extra = '';
        if (ctrl === 'select') {
          extra = optionsOf(k, tFn, flowModels).map(o =>
            '<option value="' + String(o.v).replace(/"/g, '&quot;') + '"' +
            (o.disabled ? ' disabled' : '') + '>' + o.label + '</option>'
          ).join('');
          extra = '<select data-key="' + k + '">' + extra + '</select>';
        } else if (ctrl === 'bool') extra = '<input type="checkbox" data-key="' + k + '">';
        else if (ctrl === 'num') extra = '<input type="number" data-key="' + k + '">';
        else if (ctrl === 'secret') extra = '<input type="password" data-key="' + k + '">';
        else extra = '<input type="text" data-key="' + k + '">';
        parts.push(
          '<div class="kv" data-key="' + k + '" title="' + k + '">' +
          '<label class="kv-name">' + label + (unit ? '<span class="kv-unit">' + unit + '</span>' : '') + '</label>' +
          '<p class="kv-hint">' + hint + '</p>' + extra + '</div>'
        );
      });
    });
    return parts.join('');
  }
  return {
    HIDDEN_KEYS, GROUPS, GROUP_KEYS, SELECT, UNITS, NUM_RANGE, PATH_KEYS, SECRET_KEYS,
    isHidden, keysOf, typeOf, controlOf, optionsOf, visibleKeys, labelOf, hintOf, unitOf, renderDom,
  };
})();

// 设置抽屉：按 SettingsLogic 分组渲染；「多模态接口」名册列表另走一块。
window.SettingsDrawer = {
  name: 'SettingsDrawer',
  components: { DescCardEditor: window.DescCardEditor },
  emits: ['close'],
  setup() {
    const { reactive, ref, watch, onMounted, onUnmounted } = Vue;
    const L = window.SettingsLogic;
    const s = reactive({}); const defaults = reactive({});
    const saved = reactive({});
    const groups = L.GROUPS;
    const showSecret = reactive({});
    L.SECRET_KEYS.forEach(k => { showSecret[k] = false; });
    const KIND_DEF = {
      gemini: { base_url: '', model: 'gemini-3.5-flash-lite' },
      openai: { base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
      ollama: { base_url: 'http://127.0.0.1:11434', model: 'qwen2.5vl:7b' },
    };

    const providers = ref([]);
    const statusMap = ref({});
    const cap = ref(0);
    const workersN = ref(0);
    const aiDlg = ref(false);
    const form = reactive(blankForm());
    const keyOrig = ref('');
    const probe = ref(null);
    const testing = ref(false);
    const rowBusy = ref('');
    const dragId = ref('');
    let pollTimer = 0;

    function blankForm() {
      return {
        id: '', name: '', kind: 'gemini', base_url: '', api_key: '',
        model: 'gemini-3.5-flash-lite', priority: 1, max_inflight: 2,
        daily_limit: 0, vision: true, enabled: 1,
      };
    }
    function resetForm() {
      Object.assign(form, blankForm());
      keyOrig.value = '';
      probe.value = null;
    }
    function stOf(p) { return statusMap.value[p.id] || {}; }
    function stateCls(st) {
      if (!st || !st.enabled) return 'off';
      const s = st.state || '';
      if (s.startsWith('冷却')) return 'cool';
      if (s.startsWith('在飞')) return 'fly';
      if (s === '停用') return 'off';
      return 'idle';
    }
    function stateLabel(st) {
      const s = (st && st.state) || '';
      const cls = stateCls(st);
      if (cls === 'idle' || cls === 'fly')
        return (cls === 'fly' ? t('ai.st.fly') : t('ai.st.idle')) + ' ' + (st.inflight || 0) + '/' + (st.max_inflight || 0);
      return s;
    }
    function todayText(st) {
      const x = st.calls || 0, y = st.daily_limit || 0;
      return y > 0 ? t('ai.today_lim', { x, y }) : t('ai.today', { x });
    }
    function keyDirty() {
      const k = (form.api_key || '').trim();
      return k !== '' && k !== '****' && k !== keyOrig.value;
    }
    function draftBody() {
      const d = {
        id: form.id || undefined,
        name: form.name, kind: form.kind,
        base_url: form.kind === 'gemini' ? '' : form.base_url,
        model: form.model,
        priority: +form.priority, max_inflight: +form.max_inflight,
        daily_limit: +form.daily_limit,
        vision: form.vision ? 1 : 0, enabled: form.enabled ? 1 : 0,
      };
      const k = (form.api_key || '').trim();
      if (form.id && !keyDirty()) d.api_key = '';
      else d.api_key = k;
      return d;
    }

    async function loadAi(quiet) {
      try {
        let r;
        if (quiet) {
          const res = await fetch('/api/ai/providers');
          r = await res.json();
          if (!r || r.ok === false) return;
        } else {
          r = await API.get('/api/ai/providers');
        }
        // 列表按 row_order 排，拖拽才跟眼睛看到的一致；池子调度仍按 priority 分档
        providers.value = (r.providers || []).slice().sort((a, b) => (a.row_order || 0) - (b.row_order || 0));
        const m = {};
        for (const st of (r.status || [])) m[st.id] = st;
        statusMap.value = m;
        cap.value = r.capacity || 0;
        workersN.value = r.workers || 0;
      } catch (e) {
        if (!quiet) throw e;
      }
    }

    function snapFrom(src) {
      Object.keys(saved).forEach(k => { delete saved[k]; });
      Object.assign(saved, src || {});
    }
    watch(() => store.suiteTick, async () => {
      const src = store.settings || {};
      Object.assign(s, src);
      snapFrom(src);
      try { await loadCards(); } catch (e) { /* 切套装后卡片重载失败不挡抽屉 */ }
    });
    onMounted(async () => {
      const r = await API.get('/api/settings');
      Object.assign(s, r.settings); Object.assign(defaults, r.defaults);
      snapFrom(r.settings);
      await loadAi(false);
      await loadCards();
      pollTimer = setInterval(() => loadAi(true), 2000);
    });
    onUnmounted(() => { if (pollTimer) clearInterval(pollTimer); });

    function keysOf(g) { return L.keysOf(g, defaults); }
    function typeOf(k) { return L.typeOf(k, defaults); }
    function ctrlOf(k) { return L.controlOf(k, defaults); }
    function optsOf(k) {
      const models = (store.flow_models || []).slice();
      // 名册还没回来时，至少保住当前正在用的那一项
      if (k === 'engine.flow.model' && s[k] && !models.some(m => m.id === s[k])) {
        models.unshift({ id: s[k], paid: false, enabled: true });
      }
      return L.optionsOf(k, t, models);
    }
    function labelOf(k) { return L.labelOf(k, t); }
    function hintOf(k) { return L.hintOf(k, t); }
    function unitOf(k) { return L.unitOf(k, t); }
    function numMin(k) { return (L.NUM_RANGE[k] || [])[0]; }
    function numMax(k) { return (L.NUM_RANGE[k] || [])[1]; }
    function eqVal(k, a, b) {
      if (typeOf(k) === 'num') return Number(a) === Number(b);
      if (typeOf(k) === 'bool') return !!a === !!b;
      return String(a == null ? '' : a) === String(b == null ? '' : b);
    }
    function isDirty(k) { return !eqVal(k, s[k], saved[k]); }
    function anyDirty() { return L.visibleKeys(defaults).some(k => isDirty(k)); }
    function toggleSecret(k) { showSecret[k] = !showSecret[k]; }
    async function save() {
      const prevLang = saved['ui.lang'];
      const set = {};
      for (const k of L.visibleKeys(defaults)) {
        let v = s[k];
        if (typeOf(k) === 'num') v = Number(v);
        set[k] = v;
      }
      if (set['images.local_mode'] === 'move' && !saved['images.local_move_confirmed']) {
        const msg = t('set.confirm.images.local_mode.move');
        if (!window.confirm(msg)) {
          s['images.local_mode'] = saved['images.local_mode'] || 'copy';
          return;
        }
        set['images.local_move_confirmed'] = true;
      }
      const r = await API.post('/api/settings', { set });
      Object.assign(s, r.settings);
      snapFrom(r.settings);
      Object.assign(store.settings, r.settings);
      if (s['ui.lang'] !== prevLang && window._loadI18nSilent) {
        await window._loadI18nSilent(s['ui.lang']);
      }
      // 规格 98：本地模型开关 / 地址变了，重生菜单里那一项马上跟着变
      if (window.refreshAccounts) window.refreshAccounts().catch(() => {});
      toast(t('toast.saved'), '', 'ok');
    }
    async function resetAll(g) {
      const ks = keysOf(g);
      if (!ks.length) return;
      const r = await API.post('/api/settings', { reset: ks });
      Object.assign(s, r.settings);
      snapFrom(r.settings);
      Object.assign(store.settings, r.settings);
    }
    async function resetOne(k) {
      const r = await API.post('/api/settings', { reset: [k] });
      s[k] = r.settings[k];
      saved[k] = r.settings[k];
      Object.assign(store.settings, r.settings);
    }
    async function pick() { const r = await API.post('/api/workspace/pick'); if (r.folder) s['workspace.root'] = r.folder; }
    const busy = ref(false);
    const presetDlg = ref('');          // '' | export | import | factory | replace
    const preset = reactive({
      groups: { settings: true, prompts: true, accounts: true, ai_providers: true, counters: true, desc_cards: true },
      cookies: true,
      password: '', password2: '',
      path: '',
      counts: {},
      file: '',
      meta: null,
      diff: null,
      mode: 'merge_local',
      confirm: '',
      factoryPassword: '',
      busy: false,
      result: null,
    });
    async function loadPresetPreview() {
      try {
        const r = await API.get('/api/preset/preview');
        preset.counts = r.counts || {};
        if (!preset.path) preset.path = r.default_path || '';
      } catch (e) { /* API 已 toast */ }
    }
    function openPreset(kind) {
      preset.password = ''; preset.password2 = '';
      preset.confirm = ''; preset.factoryPassword = '';
      preset.meta = null; preset.diff = null; preset.result = null;
      preset.file = '';
      preset.mode = 'merge_local';
      preset.groups = { settings: true, prompts: true, accounts: true, ai_providers: true, counters: true, desc_cards: true };
      preset.cookies = true;
      presetDlg.value = kind;
      if (kind === 'export') loadPresetPreview();
    }
    async function pickExportPath() {
      const r = await API.post('/api/preset/pick-save', {
        filename: (preset.path || '').split(/[/\\]/).pop(),
        directory: (preset.path || '').replace(/[/\\][^/\\]+$/, ''),
      });
      if (r.path) preset.path = r.path;
    }
    async function doExport() {
      if (preset.busy) return;
      if (!preset.password) return toast(t('set.preset.need_pw'), '', 'bad');
      if (preset.password !== preset.password2) return toast(t('set.preset.pw_mismatch'), '', 'bad');
      const groups = Object.keys(preset.groups).filter(k => preset.groups[k]);
      if (!groups.length && !preset.cookies) return toast(t('set.preset.need_group'), '', 'bad');
      preset.busy = true;
      try {
        const r = await API.post('/api/preset/export', {
          groups, cookies: !!preset.cookies, password: preset.password, path: preset.path,
        });
        preset.result = r;
        toast(t('set.preset.exported', { n: String(r.bytes || 0) }), r.path, 'ok');
        if (r.warnings && r.warnings.length) toast(r.warnings.join('；'), '', 'bad');
        presetDlg.value = '';
      } catch (e) { /* API 已 toast */ }
      finally { preset.busy = false; }
    }
    async function pickImportFile() {
      const r = await API.post('/api/preset/pick-open');
      if (!r.path) return;
      preset.file = r.path;
      preset.meta = null; preset.diff = null; preset.result = null;
      try {
        const ins = await API.post('/api/preset/inspect', { path: r.path });
        preset.meta = ins.meta;
      } catch (e) { /* API 已 toast */ }
    }
    async function loadDiff() {
      if (!preset.file || !preset.password) return toast(t('set.preset.need_pw'), '', 'bad');
      preset.busy = true;
      try {
        const r = await API.post('/api/preset/diff', { path: preset.file, password: preset.password });
        preset.diff = r;
        if (r.meta) preset.meta = r.meta;
      } catch (e) { preset.diff = null; }
      finally { preset.busy = false; }
    }
    async function doImport() {
      if (preset.busy) return;
      if (!preset.file) return toast(t('set.preset.need_file'), '', 'bad');
      if (!preset.password) return toast(t('set.preset.need_pw'), '', 'bad');
      if (preset.mode === 'replace' && presetDlg.value !== 'replace') {
        presetDlg.value = 'replace';
        return;
      }
      const groups = Object.keys(preset.groups).filter(k => preset.groups[k]);
      if (preset.cookies) groups.push('cookies');
      preset.busy = true;
      try {
        const r = await API.post('/api/preset/import', {
          path: preset.file, password: preset.password, mode: preset.mode, groups,
        });
        await refreshState();
        const n = (r.need_relogin || []).length;
        toast(t('set.preset.imported'),
              n ? t('set.preset.need_relogin', { s: (r.need_relogin || []).join('、') }) : '',
              n ? 'bad' : 'ok');
        if (r.warnings && r.warnings.length) toast(r.warnings.join('；'), '', 'bad');
        presetDlg.value = '';
      } catch (e) { /* API 已 toast */ }
      finally { preset.busy = false; }
    }
    async function doFactory() {
      if (preset.busy) return;
      if ((preset.confirm || '').trim() !== t('set.preset.factory_phrase')) {
        return toast(t('set.preset.factory_need'), '', 'bad');
      }
      preset.busy = true;
      try {
        const r = await API.post('/api/preset/factory_reset', {
          confirm: (preset.confirm || '').trim(),
          password: preset.factoryPassword || undefined,
        });
        await refreshState();
        toast(t('set.preset.factory_ok'), r.backup || '', 'ok');
        if (r.backup_password && !preset.factoryPassword) {
          toast(t('set.preset.factory_pw', { p: r.backup_password }), '', 'ok');
        }
        presetDlg.value = '';
      } catch (e) { /* API 已 toast */ }
      finally { preset.busy = false; }
    }
    function metaLine(m) {
      if (!m) return '';
      const day = (m.created || '').slice(0, 10);
      const ck = m.has_cookies ? t('set.preset.with_ck') : t('set.preset.no_ck');
      return (m.machine || '') + ' · ' + day + ' · ' + ck;
    }
    async function installShortcuts() {
      if (busy.value) return;
      busy.value = true;
      try {
        await API.post('/api/system/install-shortcuts');
        toast(t('set.shortcuts.ok'), '', 'ok');
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }
    function rerunWizard() {
      ui.showSetup = true;
      // 关掉设置抽屉，向导盖在任务上
    }

    function onKind() {
      const d = KIND_DEF[form.kind] || KIND_DEF.gemini;
      form.base_url = d.base_url;
      const models = Object.values(KIND_DEF).map(x => x.model);
      if (!form.model || models.includes(form.model)) form.model = d.model;
    }
    function openNew() {
      resetForm();
      aiDlg.value = true;
    }
    function openEdit(p) {
      Object.assign(form, {
        id: p.id, name: p.name, kind: p.kind,
        base_url: p.base_url || '', api_key: p.api_key || '',
        model: p.model, priority: p.priority, max_inflight: p.max_inflight,
        daily_limit: p.daily_limit, vision: !!p.vision, enabled: p.enabled,
      });
      keyOrig.value = p.api_key || '';
      probe.value = null;
      aiDlg.value = true;
    }
    async function saveProv() {
      const d = draftBody();
      if (form.id) {
        if (!keyDirty()) d.api_key = '****';
        await API.post('/api/ai/providers/' + form.id, d);
      } else {
        delete d.id;
        await API.post('/api/ai/providers', d);
      }
      aiDlg.value = false;
      toast(t('toast.saved'), '', 'ok');
      await loadAi(false);
    }
    async function removeProv(p) {
      if (!confirm(t('common.confirm_delete') + ' ' + p.name)) return;
      await API.del('/api/ai/providers/' + p.id);
      await loadAi(false);
    }
    async function toggleEn(p) {
      await API.post('/api/ai/providers/' + p.id, { enabled: p.enabled ? 0 : 1, api_key: '****' });
      await loadAi(false);
    }
    async function thaw(p) {
      await API.post('/api/ai/thaw', { id: p.id });
      await loadAi(false);
    }
    function applyProbe(r) {
      probe.value = r || null;
      if (r && r.ok_vision === false) {
        form.vision = false;
        toast(t('ai.novision'), '', 'bad');
      }
    }
    async function testDlg() {
      if (testing.value) return;
      testing.value = true;
      probe.value = null;
      try {
        let r;
        if (form.id && !keyDirty()) {
          r = await API.post('/api/ai/providers/' + form.id + '/test');
        } else {
          r = await API.post('/api/ai/test', draftBody());
        }
        applyProbe(r.result);
      } catch (e) { /* API 已 toast */ }
      finally { testing.value = false; }
    }
    async function testRow(p) {
      if (rowBusy.value) return;
      rowBusy.value = p.id;
      try {
        const r = await API.post('/api/ai/providers/' + p.id + '/test');
        const res = r.result || {};
        if (res.ok_text && res.ok_vision) {
          toast(t('ai.probe.both_ok'), t('ai.probe.vision_ok', { s: res.vision || '' }), 'ok');
        } else if (res.ok_text) {
          toast(t('ai.probe.text_ok', { s: res.text || '' }), t('ai.probe.vision_hint'), 'bad');
        } else {
          toast(t('ai.probe.text_bad'), '', 'bad');
        }
      } catch (e) { /* API 已 toast */ }
      finally { rowBusy.value = ''; }
    }
    function dragStart(id, e) {
      dragId.value = id;
      try { e.dataTransfer.setData('text/plain', id); e.dataTransfer.effectAllowed = 'move'; } catch (x) {}
    }
    async function dropOn(id) {
      const from = dragId.value;
      dragId.value = '';
      if (!from || from === id) return;
      const ids = providers.value.map(p => p.id);
      const a = ids.indexOf(from), b = ids.indexOf(id);
      if (a < 0 || b < 0) return;
      ids.splice(a, 1);
      ids.splice(b, 0, from);
      providers.value = ids.map(i => providers.value.find(p => p.id === i)).filter(Boolean);
      await API.post('/api/ai/providers/reorder', { ids });
      await loadAi(true);
    }
    const cards = ref([]);
    const cardOpen = ref(null);
    const cardDrag = ref('');
    async function loadCards() {
      try {
        const r = await API.get('/api/desc-cards');
        cards.value = r.cards || [];
      } catch (e) { /* API 已 toast */ }
    }
    function openCard(c) { cardOpen.value = c ? { id: c.id, name: c.name, prompt: c.prompt } : { id: '', name: '', prompt: '' }; }
    function onCardSaved() { loadCards(); }
    async function copyCard(c) {
      await API.post('/api/desc-cards/' + encodeURIComponent(c.id) + '/copy');
      await loadCards();
    }
    async function delCard(c) {
      if (!confirm(t('set.cards.del') + ' 「' + c.name + '」？')) return;
      await API.del('/api/desc-cards/' + encodeURIComponent(c.id));
      await loadCards();
    }
    async function moveCard(c, delta) {
      await API.post('/api/desc-cards/reorder', { id: c.id, delta });
      await loadCards();
    }
    async function restoreCards() {
      await API.post('/api/desc-cards/restore');
      toast(t('set.cards.restore_ok'), '', 'ok');
      await loadCards();
    }
    function cardDragStart(id, e) {
      cardDrag.value = id;
      try { e.dataTransfer.setData('text/plain', id); e.dataTransfer.effectAllowed = 'move'; } catch (x) {}
    }
    async function cardDropOn(id) {
      const from = cardDrag.value;
      cardDrag.value = '';
      if (!from || from === id) return;
      const ids = cards.value.map(c => c.id);
      const a = ids.indexOf(from), b = ids.indexOf(id);
      if (a < 0 || b < 0) return;
      ids.splice(a, 1);
      ids.splice(b, 0, from);
      await API.post('/api/desc-cards/reorder', { ids });
      await loadCards();
    }

    return {
      s, defaults, groups, keysOf, typeOf, ctrlOf, optsOf, labelOf, hintOf, unitOf, numMin, numMax,
      isDirty, anyDirty, showSecret, toggleSecret, save, resetAll, resetOne, pick, t, busy, installShortcuts, rerunWizard,
      presetDlg, preset, openPreset, pickExportPath, doExport, pickImportFile, loadDiff, doImport,
      doFactory, metaLine,
      providers, cap, workersN, aiDlg, form, probe, testing, rowBusy, dragId,
      stOf, stateCls, stateLabel, todayText, openNew, openEdit, saveProv, removeProv, toggleEn, thaw,
      testDlg, testRow, onKind, dragStart, dropOn,
      cards, cardOpen, cardDrag, openCard, onCardSaved, copyCard, delCard, moveCard, restoreCards,
      cardDragStart, cardDropOn, descCardPreview: window.descCardPreview,
    };
  },
  template: `
  <div><div class="drawer-mask" @click="aiDlg ? (aiDlg=false) : (presetDlg ? (presetDlg='') : $emit('close'))"></div>
  <aside class="drawer wide">
    <div class="hd"><h2>{{ t('set.title') }}</h2><button class="btn ghost sm" @click="$emit('close')">✕</button></div>
    <div class="bd">
      <h3 class="row">{{ t('set.preset.title') }}</h3>
      <p class="aip-intro">{{ t('set.preset.intro') }}</p>
      <div class="preset-acts">
        <button class="btn sm" @click="openPreset('export')">{{ t('set.preset.export') }}</button>
        <span class="muted">{{ t('set.preset.export_hint') }}</span>
      </div>
      <div class="preset-acts">
        <button class="btn sm" @click="openPreset('import')">{{ t('set.preset.import') }}</button>
        <span class="muted">{{ t('set.preset.import_hint') }}</span>
      </div>
      <div class="preset-acts">
        <button class="btn sm ghost danger" @click="openPreset('factory')">{{ t('set.preset.factory') }}</button>
        <span class="muted">{{ t('set.preset.factory_hint') }}</span>
      </div>
      <div class="preset-acts">
        <button class="btn sm" @click="rerunWizard(); $emit('close')">{{ t('set.rerun_wizard') }}</button>
        <span class="muted">{{ t('set.rerun_wizard_hint') }}</span>
      </div>

      <h3 class="row">{{ t('set.cards.title') }}</h3>
      <p class="aip-intro">{{ t('set.cards.intro') }}</p>
      <div class="aip-hd">
        <button class="btn sm" @click="openCard(null)">{{ t('set.cards.new') }}</button>
        <button class="btn sm ghost" @click="restoreCards">{{ t('set.cards.restore') }}</button>
      </div>
      <div v-if="!cards.length" class="empty aip-empty">{{ t('set.cards.empty') }}</div>
      <div v-for="c in cards" :key="c.id"
           class="dc-row" :class="{dragging: cardDrag===c.id, open: cardOpen && cardOpen.id===c.id}"
           @dragover.prevent @drop.prevent="cardDropOn(c.id)">
        <span class="aip-handle" draggable="true"
              @dragstart="cardDragStart(c.id, $event)" @dragend="cardDrag=''">⠿</span>
        <div class="dc-body" @click="openCard(c)">
          <div class="dc-name">{{ c.name }}
            <span v-if="c.builtin" class="tag">{{ t('set.cards.builtin') }}</span>
          </div>
          <div class="dc-prev muted">{{ descCardPreview(c.prompt) }}</div>
        </div>
        <span class="aip-acts">
          <button class="btn sm ghost" @click="copyCard(c)">{{ t('set.cards.copy') }}</button>
          <button class="btn sm ghost" @click="moveCard(c, -1)">{{ t('set.cards.up') }}</button>
          <button class="btn sm ghost" @click="moveCard(c, 1)">{{ t('set.cards.down') }}</button>
          <button class="btn sm ghost danger" @click="delCard(c)">{{ t('set.cards.del') }}</button>
        </span>
      </div>

      <template v-for="g in groups" :key="g">
        <h3 class="row">{{ t('set.group.'+g) }} <span style="flex:1"></span><button class="btn ghost sm muted" @click="resetAll(g)">{{ t('set.reset') }}</button></h3>

        <div v-for="k in keysOf(g)" :key="k" class="kv" :class="{dirty: isDirty(k)}" :title="k">
          <div class="kv-meta">
            <div class="kv-name">{{ labelOf(k) }}<span v-if="unitOf(k)" class="kv-unit">{{ unitOf(k) }}</span></div>
            <div class="kv-hint">{{ hintOf(k) }}</div>
            <div class="kv-key">{{ k }}</div>
          </div>
          <div class="row kv-ctrl">
            <input v-if="ctrlOf(k)==='bool'" type="checkbox" v-model="s[k]">
            <input v-else-if="ctrlOf(k)==='num'" type="number" v-model.number="s[k]"
                   :min="numMin(k)" :max="numMax(k)">
            <select v-else-if="ctrlOf(k)==='select'" v-model="s[k]">
              <option v-for="o in optsOf(k)" :key="String(o.v)" :value="o.v" :disabled="o.disabled">{{ o.label }}</option>
            </select>
            <template v-else-if="ctrlOf(k)==='secret'">
              <input :type="showSecret[k] ? 'text' : 'password'" v-model="s[k]" autocomplete="off">
              <button class="btn sm ghost" type="button" @click="toggleSecret(k)"
                      :title="showSecret[k] ? t('set.hide_secret') : t('set.show_secret')">{{ showSecret[k] ? '🙈' : '👁' }}</button>
            </template>
            <input v-else type="text" v-model="s[k]">
            <button v-if="ctrlOf(k)==='path'" class="btn sm" type="button" @click="pick">{{ t('set.pick') }}</button>
            <button class="btn sm ghost kv-reset" type="button" @click="resetOne(k)">{{ t('set.reset_one') }}</button>
          </div>
        </div>

        <template v-if="g==='ai'">
          <p class="aip-intro">{{ t('ai.intro') }}</p>
          <div class="aip-hd">
            <button class="btn sm" @click="openNew">{{ t('ai.add') }}</button>
            <span class="spacer" style="flex:1"></span>
            <span v-if="providers.length" class="aip-cap">{{ t('ai.cap', { n: cap, m: workersN }) }}</span>
          </div>
          <div v-if="!providers.length" class="empty aip-empty">{{ t('ai.empty') }}</div>
          <div v-for="p in providers" :key="p.id"
               class="aip-row" :class="['is-'+stateCls(stOf(p)), {dragging: dragId===p.id}]"
               @dragover.prevent @drop.prevent="dropOn(p.id)">
            <span class="aip-handle" draggable="true"
                  @dragstart="dragStart(p.id, $event)" @dragend="dragId=''">⠿</span>
            <button class="aip-dot" :class="[stateCls(stOf(p)), {on: !!p.enabled}]"
                    :title="p.enabled ? t('ai.enabled') : t('ai.disabled')"
                    @click="toggleEn(p)"></button>
            <span class="aip-nm">{{ p.name }}</span>
            <span class="tag">{{ p.kind }}</span>
            <span class="muted mono aip-model">{{ p.model }}</span>
            <span class="muted">{{ t('ai.priority', { n: stOf(p).priority || p.priority }) }}</span>
            <span class="aip-state">{{ stateLabel(stOf(p)) }}</span>
            <span class="muted">{{ todayText(stOf(p)) }}</span>
            <span v-if="(stOf(p).fails||0)>0" class="aip-fails"
                  :title="stOf(p).last_error || ''">{{ t('ai.fails', { n: stOf(p).fails }) }}</span>
            <span class="aip-acts">
              <button class="btn sm ghost" :disabled="rowBusy===p.id" @click="testRow(p)">{{ t('ai.test') }}</button>
              <button class="btn sm ghost" @click="openEdit(p)">{{ t('ai.edit') }}</button>
              <button class="btn sm ghost danger" @click="removeProv(p)">{{ t('ai.delete') }}</button>
              <button v-if="(stOf(p).cool_left||0)>0" class="btn sm" @click="thaw(p)">{{ t('ai.thaw') }}</button>
            </span>
            <div v-if="stOf(p).cool_why" class="aip-why">{{ stOf(p).cool_why }}</div>
          </div>
        </template>
        <div v-if="g==='ui'" class="kv">
          <div class="kv-meta">
            <div class="kv-name">{{ t('set.shortcuts') }}</div>
            <div class="kv-hint">{{ t('set.shortcuts.btn') }}</div>
          </div>
          <div class="row kv-ctrl">
            <button class="btn sm" :disabled="busy" @click="installShortcuts">{{ t('set.shortcuts.btn') }}</button>
          </div>
        </div>
      </template>
    </div>
    <div class="ft"><button class="btn ghost" @click="$emit('close')">{{ t('common.close') }}</button><button class="btn primary" :class="{dirty: anyDirty()}" @click="save">{{ t('set.save') }}</button></div>
  </aside>

  <div v-if="aiDlg" class="dlg-mask" @click.self="aiDlg=false">
    <div class="dlg aip-dlg" @click.stop>
      <h3>{{ form.id ? t('ai.dlg.edit') : t('ai.dlg.new') }}</h3>
      <div class="field">
        <label>{{ t('ai.f.kind') }}</label>
        <div class="aip-kinds">
          <label><input type="radio" value="gemini" v-model="form.kind" @change="onKind"> {{ t('ai.kind.gemini') }}</label>
          <label><input type="radio" value="openai" v-model="form.kind" @change="onKind"> {{ t('ai.kind.openai') }}</label>
          <label><input type="radio" value="ollama" v-model="form.kind" @change="onKind"> {{ t('ai.kind.ollama') }}</label>
        </div>
      </div>
      <div class="field"><label>{{ t('ai.f.name') }}</label><input v-model="form.name" :placeholder="t('ai.name_ph')"></div>
      <div class="field" v-if="form.kind!=='gemini'"><label>{{ t('ai.f.base') }}</label><input v-model="form.base_url"></div>
      <div class="field"><label>{{ t('ai.f.key') }}</label><input v-model="form.api_key" :placeholder="form.kind==='ollama' ? t('ai.key_optional') : ''" autocomplete="off"></div>
      <div class="field"><label>{{ t('ai.f.model') }}</label><input v-model="form.model"></div>
      <div class="aip-nums">
        <div class="field"><label>{{ t('ai.f.priority') }}</label><input type="number" min="1" max="99" v-model.number="form.priority"></div>
        <div class="field"><label>{{ t('ai.f.inflight') }}</label><input type="number" min="1" max="32" v-model.number="form.max_inflight"></div>
        <div class="field"><label>{{ t('ai.f.daily') }}</label><input type="number" min="0" v-model.number="form.daily_limit" :placeholder="t('ai.f.daily_hint')"></div>
      </div>
      <label class="aip-check"><input type="checkbox" v-model="form.vision"> {{ t('ai.f.vision') }}</label>
      <div v-if="probe" class="aip-probe">
        <div :class="probe.ok_text ? 'ok' : 'bad'">{{ probe.ok_text ? t('ai.probe.text_ok', { s: probe.text || '' }) : t('ai.probe.text_bad') }}</div>
        <div :class="probe.ok_vision ? 'ok' : 'bad'">{{ probe.ok_vision ? t('ai.probe.vision_ok', { s: probe.vision || '' }) : t('ai.probe.vision_bad') }}</div>
        <div v-if="!probe.ok_vision" class="bad">{{ t('ai.probe.vision_hint') }}</div>
      </div>
      <div class="row" style="justify-content:flex-end;margin-top:12px">
        <button class="btn ghost" :disabled="testing" @click="testDlg">{{ testing ? t('ai.testing') : t('ai.test') }}</button>
        <span style="flex:1"></span>
        <button class="btn ghost" @click="aiDlg=false">{{ t('common.cancel') }}</button>
        <button class="btn primary" @click="saveProv">{{ t('common.save') }}</button>
      </div>
    </div>
  </div>

  <div v-if="presetDlg==='export'" class="dlg-mask" @click.self="presetDlg=''">
    <div class="dlg preset-dlg" @click.stop>
      <h3>{{ t('set.preset.export') }}</h3>
      <label class="aip-check" v-for="g in ['settings','prompts','accounts','ai_providers','counters','desc_cards']" :key="g">
        <input type="checkbox" v-model="preset.groups[g]"> {{ t('set.preset.g.'+g) }}
        <span class="muted" v-if="preset.counts[g]!=null">（{{ preset.counts[g] }}）</span>
      </label>
      <label class="aip-check">
        <input type="checkbox" v-model="preset.cookies"> {{ t('set.preset.g.cookies') }}
      </label>
      <p class="g-hint">{{ t('set.preset.cookies_note') }}</p>
      <div class="field"><label>{{ t('set.preset.password') }}</label><input type="password" v-model="preset.password" autocomplete="new-password"></div>
      <div class="field"><label>{{ t('set.preset.password2') }}</label><input type="password" v-model="preset.password2" autocomplete="new-password"></div>
      <div class="field"><label>{{ t('set.preset.path') }}</label>
        <div class="row"><input v-model="preset.path" style="flex:1"><button class="btn sm" @click="pickExportPath">…</button></div>
      </div>
      <div class="row" style="justify-content:flex-end;margin-top:12px">
        <button class="btn ghost" @click="presetDlg=''">{{ t('common.cancel') }}</button>
        <button class="btn primary" :disabled="preset.busy" @click="doExport">{{ t('set.preset.export_go') }}</button>
      </div>
    </div>
  </div>

  <div v-if="presetDlg==='import' || presetDlg==='replace'" class="dlg-mask" @click.self="presetDlg=''">
    <div class="dlg preset-dlg" @click.stop>
      <h3>{{ t('set.preset.import') }}</h3>
      <div class="row" style="margin-bottom:10px">
        <button class="btn sm" @click="pickImportFile">{{ t('set.preset.pick') }}</button>
        <span class="muted mono" style="flex:1;overflow:hidden;text-overflow:ellipsis">{{ preset.file || t('set.preset.need_file') }}</span>
      </div>
      <p v-if="preset.meta" class="preset-meta">{{ t('set.preset.this') }}{{ metaLine(preset.meta) }}</p>
      <div class="field"><label>{{ t('set.preset.password') }}</label>
        <div class="row">
          <input type="password" v-model="preset.password" autocomplete="off" style="flex:1">
          <button class="btn sm" :disabled="preset.busy" @click="loadDiff">{{ t('set.preset.diff') }}</button>
        </div>
      </div>
      <div v-if="preset.diff" class="preset-diff">
        <div>{{ t('set.preset.diff.settings', { n: String((preset.meta && preset.meta.counts && preset.meta.counts.settings) || 0), o: String((preset.diff.settings && preset.diff.settings.overwrite) || 0), a: String((preset.diff.settings && preset.diff.settings.add) || 0) }) }}</div>
        <div>{{ t('set.preset.diff.prompts', { n: String((preset.meta && preset.meta.counts && preset.meta.counts.prompts) || 0), o: String((preset.diff.prompts && preset.diff.prompts.overwrite) || 0), names: ((preset.diff.prompts && preset.diff.prompts.overwritten_names) || []).join(' / ') || '—' }) }}</div>
        <div>{{ t('set.preset.diff.accounts', { n: String((preset.meta && preset.meta.counts && preset.meta.counts.accounts) || 0), a: String((preset.diff.accounts && preset.diff.accounts.add) || 0), c: String((preset.diff.accounts && preset.diff.accounts.conflict && preset.diff.accounts.conflict.length) || 0) }) }}</div>
        <div>{{ t('set.preset.diff.providers', { n: String((preset.meta && preset.meta.counts && preset.meta.counts.ai_providers) || 0), a: String((preset.diff.ai_providers && preset.diff.ai_providers.add) || 0) }) }}</div>
        <div>{{ t('set.preset.diff.desc_cards', { n: String((preset.meta && preset.meta.counts && preset.meta.counts.desc_cards) || 0), o: String((preset.diff.desc_cards && preset.diff.desc_cards.overwrite) || 0), a: String((preset.diff.desc_cards && preset.diff.desc_cards.add) || 0) }) }}</div>
      </div>
      <div class="preset-modes">
        <label><input type="radio" value="merge_preset" v-model="preset.mode"> {{ t('set.preset.mode.preset') }}</label>
        <label><input type="radio" value="merge_local" v-model="preset.mode"> {{ t('set.preset.mode.local') }}</label>
        <label><input type="radio" value="replace" v-model="preset.mode"> {{ t('set.preset.mode.replace') }}</label>
      </div>
      <p v-if="preset.mode==='replace'" class="g-err">{{ t('set.preset.replace_warn') }}</p>
      <div class="row" style="justify-content:flex-end;margin-top:12px">
        <button class="btn ghost" @click="presetDlg=''">{{ t('common.cancel') }}</button>
        <button class="btn primary" :disabled="preset.busy" @click="doImport">{{ t('set.preset.import_go') }}</button>
      </div>
    </div>
  </div>

  <div v-if="presetDlg==='replace'" class="dlg-mask" style="z-index:56" @click.self="presetDlg='import'">
    <div class="dlg" @click.stop>
      <h3>{{ t('set.preset.replace_confirm') }}</h3>
      <p>{{ t('set.preset.replace_warn') }}</p>
      <div class="row" style="justify-content:flex-end;margin-top:12px">
        <button class="btn ghost" @click="presetDlg='import'">{{ t('common.cancel') }}</button>
        <button class="btn primary" :disabled="preset.busy" @click="doImport">{{ t('set.preset.replace_go') }}</button>
      </div>
    </div>
  </div>

  <div v-if="presetDlg==='factory'" class="dlg-mask" @click.self="presetDlg=''">
    <div class="dlg preset-dlg" @click.stop>
      <h3>{{ t('set.preset.factory') }}</h3>
      <p>{{ t('set.preset.factory_body') }}</p>
      <div class="field"><label>{{ t('set.preset.factory_type') }}</label><input v-model="preset.confirm" :placeholder="t('set.preset.factory_phrase')"></div>
      <div class="field"><label>{{ t('set.preset.factory_pw_lab') }}</label><input type="password" v-model="preset.factoryPassword" autocomplete="new-password"></div>
      <p class="g-hint">{{ t('set.preset.factory_pw_hint') }}</p>
      <div class="row" style="justify-content:flex-end;margin-top:12px">
        <button class="btn ghost" @click="presetDlg=''">{{ t('common.cancel') }}</button>
        <button class="btn primary" :disabled="preset.busy" @click="doFactory">{{ t('set.preset.factory_go') }}</button>
      </div>
    </div>
  </div>
  <desc-card-editor v-if="cardOpen" :card="cardOpen" @close="cardOpen=null" @saved="onCardSaved"></desc-card-editor>
  </div>`
};
