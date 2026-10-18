window.AccountsDrawer = {
  name: 'AccountsDrawer',
  emits: ['close'],
  setup() {
    const { ref, reactive, computed, onMounted } = Vue;
    const adding = ref(null);           // 'flow' | 'grok' | null
    const form = reactive({ name: '', project_url: '' });
    const health = reactive({});        // id -> {state}
    const busy = ref(false);

    async function add() {
      if (!form.name.trim()) return toast(t('acc.name'), '', 'bad');
      busy.value = true;
      try { await API.post('/api/accounts', { name: form.name, engine: adding.value, project_url: form.project_url }); await refreshAccounts(); adding.value = null; form.name = ''; form.project_url = ''; }
      finally { busy.value = false; }
    }
    async function login(a) { const r = await API.post(`/api/accounts/${a.id}/login`); toast(r.hint || '已打开', '', 'ok'); setTimeout(() => check(a), 4000); }
    async function check(a) {
      const r = await API.get(`/api/accounts/${a.id}/health`);
      health[a.id] = r;
      if (r.state === 'ready' && a.login_state === 'need_relogin') {
        try {
          await API.post(`/api/accounts/${a.id}/login-state`, { state: '' });
          a.login_state = '';
        } catch (e) { /* 清标记失败不挡检查 */ }
      }
    }
    async function remove(a) { if (!confirm(t('common.confirm_delete') + ' ' + a.name)) return; await API.del(`/api/accounts/${a.id}`); await refreshAccounts(); }
    async function saveUrl(a) { await API.post(`/api/accounts/${a.id}`, { project_url: a.project_url, model: a.model, max_concurrent: +a.max_concurrent }); toast(t('toast.saved'), '', 'ok'); }
    async function importLegacy() { const r = await API.post('/api/accounts/import-legacy'); await refreshAccounts(); toast(`导入了 ${r.added.length} 个泳道`, '', 'ok'); }
    function pageInfo(a) {
      const h = health[a.id] || {};
      return (h.project_page || (a.extra && a.extra.project_page) || null);
    }
    function pageBad(a) {
      const pp = pageInfo(a);
      return !!(pp && pp.ok === false);
    }
    function pageTip(a) {
      const pp = pageInfo(a) || {};
      const why = pp.why || 'not_visible';
      if (why === 'collection') return t('acc.page_bad.collection');
      return t('acc.page_bad.not_visible', { name: a.name || '' });
    }
    function healthPageLine(a) {
      const h = health[a.id] || {};
      const pp = h.project_page;
      if (!pp) return '';
      if (pp.ok) return t('acc.health.page_ok');
      const why = pp.why === 'collection' ? t('acc.health.page_collection') : t('acc.health.page_hidden');
      return t('acc.health.page_bad', { why });
    }
    async function useProjectUrl(a) {
      try {
        await API.post(`/api/accounts/${a.id}/use-project-url`);
        await refreshAccounts();
        toast(t('acc.use_project'), '', 'ok');
      } catch (e) { /* API toast */ }
    }
    function throttleLeft(a) {
      const extra = (a && a.extra) || {};
      const ts = Number(extra.throttled_until_ts || 0);
      let end = ts ? ts * 1000 : 0;
      if (!end && extra.throttled_until) {
        const p = Date.parse(extra.throttled_until);
        if (Number.isFinite(p)) end = p;
      }
      if (!end) return 0;
      return Math.max(0, end - Date.now());
    }
    function throttleMin(a) {
      const ms = throttleLeft(a);
      return ms > 0 ? Math.max(1, Math.ceil(ms / 60000)) : 0;
    }
    const flowModels = computed(() => store.flow_models && store.flow_models.length
      ? store.flow_models
      : [
          { id: 'Veo 3.1 - Lite [Lower Priority]', paid: false, enabled: true },
          { id: 'Veo 3.1 - Lite', paid: true, enabled: false },
          { id: 'Veo 3.1 - Fast', paid: true, enabled: false },
          { id: 'Veo 3.1 - Quality', paid: true, enabled: false },
        ]);
    const allowPaid = computed(() => !!(store.settings && store.settings['flow.allow_paid']));
    function lockInfo(a) {
      const lk = a && a.lock;
      if (!lk) return null;
      const port = Number(lk.owner_port || 0);
      if (a.lock_mine) return { kind: 'mine', text: t('acc.lock.mine') };
      if (port) return { kind: 'other', text: t('acc.lock.other', { port: String(port) }) };
      return { kind: 'other', text: t('acc.lock.mine') };
    }
    // 浏览器账号（Flow / Grok）；本地模型是虚拟账号，单独一栏
    const realAccounts = computed(() => (store.accounts || []).filter(a => !a.virtual && a.engine !== 'studio'));
    onMounted(() => realAccounts.value.forEach(a => check(a).catch(() => {})));
    // 规格 98 / 99：本地模型 Studio Zero——不用登录、不能删；能启用 / 停用，能选出片模式（像 Flow 账号选模型）
    const STUDIO_MODES = ['fast', 'balanced', 'high', 'best'];
    const studio = computed(() => {
      const a = (store.accounts || []).find(x => x.engine === 'studio') || {};
      return Object.assign({}, a, store.studio || {});
    });
    const studioState = computed(() => {
      const s = studio.value;
      if (!s.enabled) return 'off';
      if (s.alive) return 'ready';
      return s.api_up ? 'comfy_down' : 'closed';
    });
    const studioMode = computed(() => {
      const q = studio.value.quality || studio.value.model || 'balanced';
      return STUDIO_MODES.includes(q) ? q : 'balanced';
    });
    const studioBusy = ref(false);
    async function saveStudio(patch) {
      studioBusy.value = true;
      try {
        await API.post('/api/accounts/STUDIO', patch);
        await window.refreshAccounts();
        if (patch.quality) toast(t('acc.studio.mode_saved', { q: t('acc.studio.q.' + patch.quality) }), '', 'ok');
        else if ('enabled' in patch) toast(t(patch.enabled ? 'acc.studio.on_ok' : 'acc.studio.off_ok'), '', 'ok');
      } catch (e) { /* API toast */ } finally { studioBusy.value = false; }
    }
    function setStudioMode(q) { if (q !== studioMode.value) saveStudio({ quality: q }); }
    function toggleStudio() { saveStudio({ enabled: !studio.value.enabled }); }
    async function checkStudio() {
      try {
        await API.get('/api/accounts/STUDIO/health');
        await window.refreshAccounts();
      } catch (e) { /* toast */ }
    }
    return { studio, studioState, checkStudio, STUDIO_MODES, studioMode, studioBusy, setStudioMode, toggleStudio, realAccounts,
      adding, form, health, busy, add, login, check, remove, saveUrl, importLegacy, store, t, flowModels, allowPaid, lockInfo,
      pageBad, pageTip, pageInfo, healthPageLine, useProjectUrl, throttleMin };
  },
  template: `
  <div><div class="drawer-mask" @click="$emit('close')"></div>
  <aside class="drawer">
    <div class="hd"><h2>{{ t('acc.title') }}</h2><button class="btn ghost sm" @click="$emit('close')">✕</button></div>
    <div class="bd">
      <div class="row" style="margin-bottom:12px">
        <button class="btn" style="border-color:var(--flow);color:var(--flow)" @click="adding='flow'">{{ t('acc.add_flow') }}</button>
        <button class="btn" style="border-color:var(--grok);color:var(--grok)" @click="adding='grok'">{{ t('acc.add_grok') }}</button>
        <span class="spacer" style="flex:1"></span>
        <button v-if="store.legacy_available" class="btn ghost sm muted" @click="importLegacy" title="开发机专用：把 lanes.json 的泳道导进来">{{ t('acc.import_legacy') }}</button>
      </div>
      <div v-if="adding" class="card" style="margin-bottom:12px">
        <div class="field"><label>{{ t('acc.name') }}</label><input v-model="form.name" :placeholder="adding==='flow' ? t('setup.acc_name_ph') : t('setup.acc_name_ph')" @keyup.enter="add"></div>
        <div class="field" v-if="adding==='flow'"><label>{{ t('acc.project_url') }}</label><input v-model="form.project_url" placeholder="https://flow.google.com/project/.../collection/..."></div>
        <div class="row" style="justify-content:flex-end"><button class="btn ghost" @click="adding=null">{{ t('common.cancel') }}</button><button class="btn primary" :disabled="busy" @click="add">{{ t('common.save') }}</button></div>
      </div>
      <div class="muted" style="margin:0 0 8px;font-size:12px;color:var(--ok)">⚡ 默认优先使用付费 Flow / Grok 账号（出片与扩图速度最快、效率最高）</div>
      <div v-if="!realAccounts.length" class="empty">{{ t('acc.empty') }}</div>
      <div v-for="a in realAccounts" :key="a.id" class="acc" style="flex-wrap:wrap">
        <span class="lamp" :class="(health[a.id]||{}).state || (a.alive?'open':'closed')"></span>
        <span class="tag" :class="a.engine">{{ a.engine }}</span>
        <span class="nm">{{ a.name }} <small class="muted mono">:{{ a.port }}</small></span>
        <span class="tag" :class="{ok:(health[a.id]||{}).state==='ready', warn:(health[a.id]||{}).state==='login'}">{{ t('acc.state.' + ((health[a.id]||{}).state || (a.alive?'open':'closed'))) }}</span>
        <span v-if="lockInfo(a)" class="acc-lock" :class="lockInfo(a).kind">{{ lockInfo(a).text }}</span>
        <span v-if="pageBad(a)" class="tag warn" :title="pageTip(a)">{{ t('acc.page_bad') }}</span>
        <span v-if="throttleMin(a)" class="tag acc-throttled">{{ t('acc.throttled', { n: String(throttleMin(a)) }) }}</span>
        <span v-if="a.login_state==='imported'" class="tag ok">{{ t('acc.login_state.imported') }}</span>
        <span v-else-if="a.login_state==='need_relogin'" class="tag warn">{{ t('acc.login_state.need') }}</span>
        <button class="btn sm" @click="login(a)">{{ a.login_state==='need_relogin' ? t('acc.relogin') : t('acc.login') }}</button>
        <button class="btn sm ghost" @click="check(a)">{{ t('acc.health') }}</button>
        <button class="btn sm ghost danger" @click="remove(a)">✕</button>
        <div v-if="a.engine==='flow'" class="row" style="width:100%;margin-top:6px">
          <input v-model="a.project_url" style="flex:1;font-size:12px" placeholder="Flow 项目/合集链接" @change="saveUrl(a)">
          <select v-model="a.model" style="max-width:280px;font-size:12px" :title="t('acc.model')" @change="saveUrl(a)">
            <option v-for="m in flowModels" :key="m.id" :value="m.id" :disabled="m.paid && !allowPaid">
              {{ m.id }}{{ m.paid ? '（' + t('acc.model.paid') + '）' : '' }}
            </option>
          </select>
          <input v-model="a.max_concurrent" type="number" min="1" max="10" style="width:64px" title="同时挂几条" @change="saveUrl(a)">
          <button v-if="pageBad(a) && (pageInfo(a)||{}).why==='collection'"
                  class="btn sm" @click="useProjectUrl(a)">{{ t('acc.use_project') }}</button>
        </div>
        <div v-if="healthPageLine(a)" class="muted" style="width:100%;margin-top:4px;font-size:12px">{{ healthPageLine(a) }}</div>
      </div>
      <div class="acc acc-studio" :class="{off: !studio.enabled}" style="flex-wrap:wrap;margin-top:10px">
        <span class="lamp" :class="studioState === 'ready' ? 'ready' : 'closed'"></span>
        <span class="tag studio">{{ t('acc.studio.tag') }}</span>
        <span class="nm" :title="(store.settings && store.settings['engine.studio.url']) || 'http://127.0.0.1:7870'">本地 AI · LTX-2.5 / FLUX.2 <small class="muted mono">可选项 · 自由切换</small></span>
        <span class="tag" :class="{ok: studioState==='ready', warn: studioState==='comfy_down'}">{{ t('acc.studio.state.' + studioState) }}</span>
        <button class="btn sm" :class="studio.enabled ? 'ghost' : 'primary'" :disabled="studioBusy" @click="toggleStudio">{{ studio.enabled ? t('acc.studio.disable') : t('acc.studio.enable') }}</button>
        <button class="btn sm ghost" :disabled="studioState==='off'" @click="checkStudio">{{ t('acc.health') }}</button>
        <div class="muted acc-studio-lab">{{ t('acc.studio.mode') }}</div>
        <div class="acc-studio-modes">
          <button v-for="q in STUDIO_MODES" :key="q" type="button" class="sz-mode" :class="{on: studioMode===q}"
                  :disabled="studioBusy" @click="setStudioMode(q)">
            <b>{{ t('acc.studio.q.' + q) }}</b><small>{{ t('acc.studio.q_hint.' + q) }}</small>
          </button>
        </div>
        <div class="muted" style="width:100%;margin-top:4px;font-size:12px">默认优先走上方付费 Flow / Grok 账号；可在图片悬浮条、审片台右键与微对话框中随时手动切换为本地 AI 模型。</div>
      </div>
    </div>
  </aside></div>`
};
