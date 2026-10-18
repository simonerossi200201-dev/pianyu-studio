// 第一次运行向导：盖在 ①任务 上面的模态，不是新页面。
window.SetupWizard = {
  name: 'SetupWizard',
  emits: ['close'],
  setup() {
    const { reactive, ref, computed, onMounted } = Vue;
    const step = ref(0);            // 0 岔路  1 环境  2 工作区  3 账号  4 Key  5 语种
    const path = ref('wizard');     // wizard | preset
    const busy = ref(false);
    const checks = ref([]);
    const checkOk = ref(false);
    const workspace = ref((store.settings && store.settings['workspace.root']) || '');
    const port = ref(Number((store.settings && store.settings['ui.port']) || 9400));
    const accounts = ref([]);
    const health = reactive({});
    const form = reactive({ engine: 'flow', name: '', project_url: '', port: '' });
    const showWhat = ref(false);
    const showAdv = ref(false);
    const key = ref('');
    const keyResult = ref(null);
    const skipKeyNote = ref(false);
    const preset = reactive({ file: '', password: '', meta: null, busy: false });
    // 规格 92：成片语种。选完把提示词整理成一份清单，用户丢给 ChatGPT 翻好再贴回来。
    const lang = reactive({
      code: '', langs: [], scope: 'core', n: 0, n_all: 0,
      text: '', paste: '', busy: false, done: null, problems: [],
    });
    const helpWhat = computed(() => t('setup.project_help'));

    async function loadState() {
      const st = await API.get('/api/setup/state', { silent: true });
      workspace.value = st.workspace_root || workspace.value;
      port.value = Number(st.ui_port || port.value);
      store.legacy_available = !!st.legacy_available;
      return st;
    }
    async function runCheck() {
      busy.value = true;
      try {
        const r = await API.post('/api/setup/check', {
          port: Number(port.value), workspace: workspace.value,
        });
        checks.value = r.checks || [];
        checkOk.value = !!r.ok;
        if (r.workspace) workspace.value = r.workspace;
      } finally { busy.value = false; }
    }
    const ffmBusy = ref(false);
    const ffmMsg = ref('');
    async function installFfmpeg() {
      // 规格 95：pip 装 imageio-ffmpeg，装完重新自检
      ffmBusy.value = true; ffmMsg.value = '';
      try {
        const r = await API.post('/api/env/ffmpeg/install', {});
        if (r.found) { ffmMsg.value = t('setup.ffmpeg.done', { p: r.path || '' }); }
        else { ffmMsg.value = t('setup.ffmpeg.fail', { e: r.error || '' }); }
        await runCheck();
      } catch (e) {
        ffmMsg.value = t('setup.ffmpeg.fail', { e: (e && e.message) || '' });
      } finally { ffmBusy.value = false; }
    }
    async function refreshAcc() {
      const r = await API.get('/api/accounts', { silent: true });
      store.accounts = r.accounts || [];
      if (r.studio) store.studio = r.studio;
      accounts.value = store.accounts.filter(a => !a.virtual);   // 向导只管浏览器账号
      if (r.flow_models) store.flow_models = r.flow_models;
    }
    async function pickWs() {
      const r = await API.post('/api/workspace/pick');
      if (r.folder) workspace.value = r.folder;
    }
    async function addAcc() {
      if (!form.name.trim()) return toast(t('acc.name'), '', 'bad');
      busy.value = true;
      try {
        await API.post('/api/accounts', {
          name: form.name, engine: form.engine, project_url: form.project_url,
        });
        form.name = ''; form.project_url = '';
        await refreshAcc();
      } finally { busy.value = false; }
    }
    async function login(a) {
      const r = await API.post(`/api/accounts/${a.id}/login`);
      toast(r.hint || t('setup.login_opened'), '', 'ok');
      setTimeout(() => checkOne(a), 4000);
    }
    async function checkOne(a) {
      const r = await API.get(`/api/accounts/${a.id}/health`);
      health[a.id] = r;
    }
    const hasReady = computed(() =>
      accounts.value.some(a => (health[a.id] || {}).state === 'ready'));
    async function testKey() {
      if (!key.value.trim()) return toast(t('setup.key_need'), '', 'bad');
      busy.value = true; keyResult.value = null;
      try {
        const r = await API.post('/api/setup/gemini-test', { api_key: key.value.trim() });
        keyResult.value = { ok: true, reply: r.reply || '' };
      } catch (e) {
        keyResult.value = { ok: false, error: (e && e.message) || String(e) };
      } finally { busy.value = false; }
    }
    async function pickPreset() {
      const r = await API.post('/api/preset/pick-open');
      if (!r.path) return;
      preset.file = r.path; preset.meta = null;
      try {
        const ins = await API.post('/api/preset/inspect', { path: r.path });
        preset.meta = ins.meta;
      } catch (e) { /* API 已 toast */ }
    }
    async function doImport() {
      if (!preset.file) return toast(t('set.preset.need_file'), '', 'bad');
      if (!preset.password) return toast(t('set.preset.need_pw'), '', 'bad');
      preset.busy = true;
      try {
        await API.post('/api/preset/import', {
          path: preset.file, password: preset.password,
          mode: 'merge_preset',
          groups: ['settings', 'prompts', 'accounts', 'ai_providers', 'counters', 'desc_cards', 'cookies'],
        });
        await API.post('/api/setup/complete', {});
        await refreshState();
        toast(t('setup.imported'), '', 'ok');
        $close();
      } catch (e) { /* API 已 toast */ }
      finally { preset.busy = false; }
    }
    function $close() {
      ui.showSetup = false;
    }
    async function finish(skipKey) {
      if (skipKey) skipKeyNote.value = true;
      busy.value = true;
      try {
        await API.post('/api/setup/complete', {
          workspace: workspace.value,
          port: Number(port.value),
          gemini_key: skipKey ? '' : (key.value || ''),
        });
        await refreshState();
        toast(t('setup.done_toast'), '', 'ok');
        $close();
      } finally { busy.value = false; }
    }
    async function loadLangs() {
      try {
        const st = await LangPackLogic.state();
        lang.langs = st.langs || [];
        lang.n = st.n || 0;
        lang.n_all = st.n_all || 0;
        if (!lang.code) lang.code = st.lang || '';
      } catch (e) { /* 拿不到就只显示输入框 */ }
    }
    async function makeLangPack() {
      if (!lang.code) return;
      lang.busy = true;
      try {
        const r = await LangPackLogic.exportPack(lang.code, lang.scope);
        lang.text = r.text || '';
        lang.n = r.n || 0;
      } finally { lang.busy = false; }
    }
    async function copyLangPack() {
      if (!lang.text) await makeLangPack();
      const ok = await LangPackLogic.copy(lang.text);
      toast(ok ? t('setup.lang.copied', { n: String(lang.n) }) : t('setup.lang.copy_fail'),
            '', ok ? 'ok' : 'bad');
    }
    async function applyLangPack() {
      if (!lang.paste.trim()) return toast(t('setup.lang.need_paste'), '', 'bad');
      lang.busy = true;
      try {
        const r = await LangPackLogic.apply(lang.paste, lang.code);
        lang.done = r;
        lang.problems = r.problems || [];
        toast(t('setup.lang.applied', { n: String(r.applied || 0) }),
              (r.problems || []).length ? t('setup.lang.some_skipped', { n: String(r.problems.length) }) : '',
              (r.applied ? 'ok' : 'bad'));
      } finally { lang.busy = false; }
    }

    async function next() {
      if (step.value === 0) {
        if (path.value === 'preset') { step.value = 'preset'; return; }
        step.value = 1;
        await runCheck();
        return;
      }
      if (step.value === 1) {
        if (!checkOk.value) { await runCheck(); if (!checkOk.value) return; }
        step.value = 2;
        return;
      }
      if (step.value === 2) { step.value = 3; await refreshAcc(); return; }
      if (step.value === 3) {
        if (!hasReady.value) return toast(t('setup.need_ready'), '', 'bad');
        step.value = 4;
        return;
      }
      if (step.value === 4) {
        step.value = 5;
        await loadLangs();
        return;
      }
    }
    function back() {
      if (step.value === 'preset') { step.value = 0; return; }
      if (typeof step.value === 'number' && step.value > 0) step.value -= 1;
    }
    onMounted(async () => {
      try { await loadState(); } catch (e) { /* 向导开着也能再点 */ }
      accounts.value = (store.accounts || []).filter(a => !a.virtual);
      loadLangs();     // 语种清单先备着，走到第 5 步就不用等
    });
    return {
      step, path, busy, checks, checkOk, workspace, port, accounts, health, form,
      ffmBusy, ffmMsg, installFfmpeg,
      showWhat, showAdv, key, keyResult, skipKeyNote, preset, helpWhat, lang,
      hasReady, runCheck, pickWs, addAcc, login, checkOne, testKey,
      pickPreset, doImport, next, back, finish, t, store,
      loadLangs, makeLangPack, copyLangPack, applyLangPack,
    };
  },
  template: `
  <div class="setup-mask">
    <div class="setup-box" @click.stop>
      <div class="setup-hd">
        <h2>{{ t('setup.title') }}</h2>
        <span class="muted" v-if="step!==0 && step!=='preset'">{{ t('setup.stepn', { n: String(step) }) }}</span>
      </div>

      <div v-if="step===0" class="setup-bd">
        <p>{{ t('setup.welcome') }}</p>
        <label class="setup-choice"><input type="radio" value="wizard" v-model="path"> {{ t('setup.path_wizard') }}</label>
        <label class="setup-choice"><input type="radio" value="preset" v-model="path"> {{ t('setup.path_preset') }}</label>
        <div class="setup-ft">
          <span class="spacer"></span>
          <button class="btn primary" @click="next">{{ t('setup.next') }}</button>
        </div>
      </div>

      <div v-else-if="step==='preset'" class="setup-bd">
        <p>{{ t('setup.preset_intro') }}</p>
        <div class="row">
          <button class="btn sm" @click="pickPreset">{{ t('set.preset.pick') }}</button>
          <span class="muted mono" style="flex:1;overflow:hidden;text-overflow:ellipsis">{{ preset.file || t('set.preset.need_file') }}</span>
        </div>
        <p v-if="preset.meta" class="muted">{{ (preset.meta.machine || '') + ' · ' + (preset.meta.created || '').slice(0,10) }}</p>
        <div class="field"><label>{{ t('set.preset.password') }}</label>
          <input type="password" v-model="preset.password" autocomplete="new-password">
        </div>
        <div class="setup-ft">
          <button class="btn ghost" @click="back">{{ t('setup.back') }}</button>
          <span class="spacer"></span>
          <button class="btn primary" :disabled="preset.busy" @click="doImport">{{ t('set.preset.import_go') }}</button>
        </div>
      </div>

      <div v-else-if="step===1" class="setup-bd">
        <p>{{ t('setup.env') }}</p>
        <div class="setup-check" v-for="c in checks" :key="c.id">
          <span class="setup-mark" :class="{ok:c.ok, bad:!c.ok}">{{ c.ok ? '✓' : '✗' }}</span>
          <div>
            <div>{{ t('setup.check.'+c.id) }} <span class="muted mono">{{ c.detail }}</span></div>
            <div class="setup-hint" v-if="!c.ok">{{ c.hint }}</div>
            <div v-if="c.id==='ffmpeg' && !c.ok" class="field" style="margin-top:6px">
              <button class="btn sm" :disabled="busy || ffmBusy" @click="installFfmpeg">{{ ffmBusy ? t('setup.ffmpeg.installing') : t('setup.ffmpeg.install') }}</button>
              <span class="muted" v-if="ffmMsg">{{ ffmMsg }}</span>
            </div>
            <div v-if="c.id==='port' && !c.ok" class="field" style="margin-top:6px">
              <label>{{ t('setup.port') }}</label>
              <input type="number" v-model.number="port" min="1024" max="65535" style="width:120px">
              <button class="btn sm" :disabled="busy" @click="runCheck">{{ t('setup.recheck') }}</button>
            </div>
          </div>
        </div>
        <div class="setup-ft">
          <button class="btn ghost" @click="back">{{ t('setup.back') }}</button>
          <button class="btn" :disabled="busy" @click="runCheck">{{ t('setup.recheck') }}</button>
          <span class="spacer"></span>
          <button class="btn primary" :disabled="busy || !checkOk" @click="next">{{ t('setup.next') }}</button>
        </div>
      </div>

      <div v-else-if="step===2" class="setup-bd">
        <p>{{ t('setup.ws_intro') }}</p>
        <div class="field"><label>{{ t('setup.ws') }}</label>
          <div class="row">
            <input v-model="workspace" style="flex:1">
            <button class="btn sm" @click="pickWs">…</button>
          </div>
        </div>
        <p class="muted">{{ t('setup.ws_hint') }}</p>
        <div class="setup-ft">
          <button class="btn ghost" @click="back">{{ t('setup.back') }}</button>
          <span class="spacer"></span>
          <button class="btn primary" @click="next">{{ t('setup.next') }}</button>
        </div>
      </div>

      <div v-else-if="step===3" class="setup-bd">
        <p class="setup-warn">{{ t('setup.paid_warn') }}</p>
        <div class="setup-add">
          <div class="row">
            <label><input type="radio" value="flow" v-model="form.engine"> Flow</label>
            <label><input type="radio" value="grok" v-model="form.engine"> Grok</label>
          </div>
          <div class="field"><label>{{ t('setup.acc_name') }}</label>
            <input v-model="form.name" :placeholder="t('setup.acc_name_ph')" @keyup.enter="addAcc">
          </div>
          <div class="field" v-if="form.engine==='flow'">
            <label>{{ t('setup.project') }}
              <button class="btn ghost sm" type="button" @click="showWhat=!showWhat">{{ t('setup.what') }}</button>
            </label>
            <input v-model="form.project_url" placeholder="https://flow.google.com/project/...">
            <p class="muted" v-if="showWhat">{{ helpWhat }}</p>
          </div>
          <details v-if="showAdv || form.port" class="muted">
            <summary>{{ t('setup.advanced') }}</summary>
            <p>{{ t('setup.port_auto') }}</p>
          </details>
          <button class="btn sm" :disabled="busy" @click="addAcc">{{ t('setup.add_acc') }}</button>
        </div>
        <div v-if="!accounts.length" class="empty">{{ t('setup.acc_empty') }}</div>
        <div v-for="a in accounts" :key="a.id" class="setup-acc">
          <span class="tag" :class="a.engine">{{ a.engine }}</span>
          <span class="nm">{{ a.name }}</span>
          <span class="tag" :class="{ok:(health[a.id]||{}).state==='ready'}">
            {{ (health[a.id]||{}).state==='ready' ? t('setup.ready') : t('setup.not_login') }}
          </span>
          <button class="btn sm" @click="login(a)">{{ t('setup.open_login') }}</button>
          <button class="btn sm ghost" @click="checkOne(a)">{{ t('setup.detect') }}</button>
          <div v-if="health[a.id] && health[a.id].project_page" class="muted" style="width:100%;font-size:12px;margin-top:4px">
            {{ health[a.id].project_page.ok
               ? t('acc.health.page_ok')
               : t('acc.health.page_bad', { why: health[a.id].project_page.why==='collection'
                   ? t('acc.health.page_collection') : t('acc.health.page_hidden') }) }}
          </div>
        </div>
        <div class="setup-ft">
          <button class="btn ghost" @click="back">{{ t('setup.back') }}</button>
          <span class="spacer"></span>
          <button class="btn primary" :disabled="!hasReady" @click="next">{{ t('setup.next') }}</button>
        </div>
      </div>

      <div v-else-if="step===4" class="setup-bd">
        <p>{{ t('setup.key_intro') }} <a href="https://aistudio.google.com" target="_blank" rel="noopener">aistudio.google.com</a></p>
        <div class="field"><label>Gemini API Key</label>
          <div class="row">
            <input v-model="key" type="password" autocomplete="off" style="flex:1">
            <button class="btn sm" :disabled="busy" @click="testKey">{{ t('setup.key_test') }}</button>
          </div>
        </div>
        <p v-if="keyResult && keyResult.ok" class="setup-ok">{{ t('setup.key_ok') }} {{ keyResult.reply }}</p>
        <p v-if="keyResult && !keyResult.ok" class="setup-hint">{{ keyResult.error }}</p>
        <p class="muted">{{ t('setup.key_skip_note') }}</p>
        <div class="setup-ft">
          <button class="btn ghost" @click="back">{{ t('setup.back') }}</button>
          <span class="spacer"></span>
          <button class="btn" :disabled="busy" @click="next">{{ t('setup.skip') }}</button>
          <button class="btn primary" :disabled="busy" @click="next">{{ t('setup.next') }}</button>
        </div>
      </div>

      <div v-else-if="step===5" class="setup-bd setup-lang">
        <p>{{ t('setup.lang.intro') }}</p>
        <div class="field">
          <label>{{ t('setup.lang.pick') }}</label>
          <select v-model="lang.code" @change="lang.text=''">
            <option value="">{{ t('setup.lang.keep') }}</option>
            <option v-for="l in lang.langs" :key="l.code" :value="l.code">{{ l.name }}</option>
          </select>
        </div>

        <template v-if="lang.code">
          <p class="muted">{{ t('setup.lang.how') }}</p>
          <div class="row">
            <label class="setup-choice"><input type="radio" value="core" v-model="lang.scope" @change="lang.text=''">
              {{ t('setup.lang.scope_core', { n: String(lang.n_all ? lang.n : lang.n) }) }}</label>
            <label class="setup-choice"><input type="radio" value="all" v-model="lang.scope" @change="lang.text=''">
              {{ t('setup.lang.scope_all', { n: String(lang.n_all) }) }}</label>
          </div>
          <div class="row">
            <button class="btn primary sm" :disabled="lang.busy" @click="copyLangPack">{{ t('setup.lang.copy') }}</button>
            <span class="muted">{{ t('setup.lang.copy_note') }}</span>
          </div>
          <textarea v-if="lang.text" class="setup-lang-out" readonly :value="lang.text"></textarea>

          <div class="field">
            <label>{{ t('setup.lang.paste_label') }}</label>
            <textarea v-model="lang.paste" class="setup-lang-in"
                      :placeholder="t('setup.lang.paste_ph')"></textarea>
          </div>
          <div class="row">
            <button class="btn sm" :disabled="lang.busy" @click="applyLangPack">{{ t('setup.lang.apply') }}</button>
            <span v-if="lang.done" class="setup-ok">{{ t('setup.lang.applied', { n: String(lang.done.applied || 0) }) }}</span>
          </div>
          <ul v-if="lang.problems.length" class="setup-lang-prob">
            <li v-for="(p, i) in lang.problems" :key="i">{{ p.key }} — {{ p.text }}</li>
          </ul>
          <p class="muted">{{ t('setup.lang.later') }}</p>
        </template>

        <div class="setup-ft">
          <button class="btn ghost" @click="back">{{ t('setup.back') }}</button>
          <span class="spacer"></span>
          <button class="btn primary" :disabled="busy" @click="finish(false)">{{ t('setup.finish') }}</button>
        </div>
      </div>
    </div>
  </div>`
};
