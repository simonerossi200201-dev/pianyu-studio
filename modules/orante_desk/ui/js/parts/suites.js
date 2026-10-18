// 规格 76：顶栏模板套装。面板 + 另存为 + 切换转场。登录态不跟着走。
window.SuiteBar = {
  name: 'SuiteBar',
  setup() {
    const { ref, reactive, computed, onMounted, onUnmounted, nextTick } = Vue;
    const open = ref(false);
    const items = ref([]);
    const currentId = ref('');
    const colors = ref(['#D9A34F', '#6BB2AF', '#D9705F', '#8FB06A', '#7FA7D9', '#C08497', '#B8956A', '#9B8EC4']);
    const autosave = ref(true);
    const busy = ref(false);
    const menu = reactive({ id: '', x: 0, y: 0 });
    const dlg = ref('');          // create | save | edit | color | confirm | dirty | jobs | export | import | del
    const form = reactive({
      name: '', note: '', color: '#D9A34F',
      fromKind: 'current', fromId: '', restore: false,
      groups: { prompts: true, coverage: true, desc_cards: true, gen: true, accounts: true },
      password: '', password2: '', path: '', file: '',
    });
    const target = ref(null);
    const jobsN = ref(0)
    const jobsList = ref([]);
    const dirtyName = ref('');
    const cardEls = {};
    const fx = reactive({
      on: false, phase: '', cx: '50%', cy: '50%', color: '#D9A34F', name: '',
    });
    let fxTimers = [];

    const current = computed(() => {
      const id = currentId.value;
      return items.value.find(x => x.id === id) || items.value[0] || {
        id: '', name: t('suite.title'), color: colors.value[0] || '#D9A34F',
      };
    });
    const otherSuites = computed(() => items.value.filter(x => x.id && x.id !== currentId.value));

    function reduced() {
      try {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
      } catch (e) { return false; }
    }
    function pad2(n) { return (n < 10 ? '0' : '') + n; }
    function usedWhen(s) {
      if (!s) return t('suite.never');
      const d = new Date(String(s).replace(' ', 'T'));
      if (Number.isNaN(d.getTime())) return String(s);
      const now = new Date();
      const same = d.getFullYear() === now.getFullYear()
        && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
      if (same) return t('suite.used_today', { t: pad2(d.getHours()) + ':' + pad2(d.getMinutes()) });
      return t('suite.used_day', { m: String(d.getMonth() + 1), d: String(d.getDate()) });
    }
    function countsLine(it) {
      const c = (it && it.counts) || {};
      return t('suite.counts', { p: String(c.prompts || 0), c: String(c.cards || 0) });
    }
    function accLine(it) {
      const n = (it && it.counts && it.counts.accounts) || 0;
      return t('suite.accounts', { n: String(n) });
    }
    function setCardEl(id, el) {
      if (el) cardEls[id] = el;
      else delete cardEls[id];
    }

    async function load() {
      const r = await API.get('/api/suites', { silent: true });
      items.value = r.items || [];
      currentId.value = r.current || '';
      if (r.colors && r.colors.length) colors.value = r.colors;
      autosave.value = r.autosave_on_switch !== false;
      store.suiteCurrent = currentId.value;
    }

    function toggle() {
      open.value = !open.value;
      menu.id = '';
      if (open.value) load().catch(() => {});
    }
    function closeAll() {
      open.value = false;
      menu.id = '';
      if (!fx.on) dlg.value = '';
    }
    function onDoc(ev) {
      if (!open.value && !dlg.value) return;
      const el = ev.target;
      if (el && el.closest && el.closest('.suite-root')) return;
      if (dlg.value) return;
      open.value = false;
      menu.id = '';
    }
    function onKey(ev) {
      if (ev.key !== 'Escape') return;
      if (dlg.value) { dlg.value = ''; return; }
      if (open.value) open.value = false;
    }

    function nextColor() {
      const cur = (current.value && current.value.color) || '';
      const i = colors.value.indexOf(cur);
      return colors.value[(i + 1) % colors.value.length] || colors.value[0] || '#D9A34F';
    }
    function openCreate() {
      form.name = ''; form.note = '';
      form.color = nextColor();
      form.fromKind = 'current';
      form.fromId = (otherSuites.value[0] && otherSuites.value[0].id) || '';
      dlg.value = 'create';
    }
    function openSaveAs() {
      form.name = ''; form.note = '';
      form.color = nextColor();
      form.restore = false;
      menu.id = '';
      dlg.value = 'save';
    }
    async function postNew(fromSrc) {
      const name = (form.name || '').trim();
      if (!name) {
        toast(t('suite.need_name'), '', 'bad');
        return null;
      }
      if (window.flushAll) {
        try { await window.flushAll(); } catch (e) { /* 没刷上也不挡建 */ }
      }
      const r = await API.post('/api/suites', {
        name, note: form.note, color: form.color, from: fromSrc || 'current',
      });
      dlg.value = '';
      await load();
      return r.item || null;
    }
    async function doCreate() {
      if (busy.value) return;
      let src = form.fromKind || 'current';
      if (src === 'other') {
        src = form.fromId;
        if (!src) return toast(t('suite.need_from'), '', 'bad');
      }
      busy.value = true;
      try {
        const item = await postNew(src);
        if (!item) return;
        target.value = item;
        busy.value = false;
        await runSwitch(true);
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }
    async function doSaveAs() {
      if (busy.value) return;
      busy.value = true;
      try {
        const item = await postNew('current');
        if (!item) return;
        target.value = item;
        busy.value = false;
        // 勾上「原套装回滚」→ 切换时不把改动写回旧套装
        await runSwitch(form.restore ? false : true);
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }

    function openMenu(it, ev) {
      if (ev) { ev.preventDefault(); ev.stopPropagation(); }
      menu.id = it.id;
      const guess = window.placeMenu
        ? window.placeMenu(ev.clientX, ev.clientY, 180, 240, window.innerWidth, window.innerHeight, 8)
        : { left: ev.clientX, top: ev.clientY };
      menu.x = guess.left;
      menu.y = guess.top;
    }
    function picked() {
      return items.value.find(x => x.id === menu.id) || target.value;
    }
    function openEdit(it) {
      const x = it || picked();
      if (!x) return;
      target.value = x;
      form.name = x.name;
      form.note = x.note || '';
      menu.id = '';
      dlg.value = 'edit';
    }
    async function doEdit() {
      const x = target.value;
      if (!x) return;
      const name = (form.name || '').trim();
      if (!name) return toast(t('suite.need_name'), '', 'bad');
      busy.value = true;
      try {
        await API.patch('/api/suites/' + encodeURIComponent(x.id), { name, note: form.note });
        dlg.value = '';
        await load();
        toast(t('suite.renamed'), '', 'ok');
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }
    function openColor(it) {
      const x = it || picked();
      if (!x) return;
      target.value = x;
      form.color = x.color;
      menu.id = '';
      dlg.value = 'color';
    }
    async function doColor(c) {
      const x = target.value;
      if (!x) return;
      busy.value = true;
      try {
        await API.patch('/api/suites/' + encodeURIComponent(x.id), { color: c });
        dlg.value = '';
        await load();
        toast(t('suite.recolored'), '', 'ok');
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }
    async function doDup(it) {
      const x = it || picked();
      if (!x) return;
      menu.id = '';
      busy.value = true;
      try {
        const r = await API.post('/api/suites/' + encodeURIComponent(x.id) + '/duplicate');
        await load();
        toast(t('suite.copied', { name: (r.item && r.item.name) || '' }), '', 'ok');
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }
    function openDel(it) {
      const x = it || picked();
      if (!x) return;
      if (x.builtin) return toast(t('suite.builtin_no_del'), '', 'bad');
      target.value = x;
      menu.id = '';
      dlg.value = 'del';
    }
    async function doDel() {
      const x = target.value;
      if (!x) return;
      busy.value = true;
      try {
        await API.del('/api/suites/' + encodeURIComponent(x.id));
        dlg.value = '';
        await load();
        toast(t('suite.deleted', { name: x.name }), '', 'ok');
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }
    async function doRestore(it) {
      const x = it || picked();
      if (!x || !x.builtin) return;
      menu.id = '';
      busy.value = true;
      try {
        await API.post('/api/suites/' + encodeURIComponent(x.id) + '/restore-builtin');
        await load();
        if (x.id === currentId.value) await afterSwitch();
        toast(t('suite.restored'), '', 'ok');
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }
    function openExport(it) {
      const x = it || picked();
      if (!x) return;
      target.value = x;
      form.password = ''; form.password2 = ''; form.path = '';
      menu.id = '';
      dlg.value = 'export';
    }
    async function pickExport() {
      const r = await API.post('/api/preset/pick-save', {
        filename: 'Orante-' + (target.value && target.value.name || '套装') + '.orpreset',
      });
      if (r.path) form.path = r.path;
    }
    async function doExport() {
      if (!form.password) return toast(t('suite.need_pw'), '', 'bad');
      if (form.password !== form.password2) return toast(t('suite.pw_mismatch'), '', 'bad');
      const x = target.value;
      if (!x) return;
      busy.value = true;
      try {
        const r = await API.post('/api/suites/' + encodeURIComponent(x.id) + '/export', {
          password: form.password, path: form.path,
        });
        dlg.value = '';
        toast(t('suite.exported'), r.path || '', 'ok');
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }
    function openImport() {
      form.file = ''; form.password = ''; form.name = '';
      dlg.value = 'import';
    }
    async function pickImport() {
      const r = await API.post('/api/preset/pick-open');
      if (r.path) form.file = r.path;
    }
    async function doImport() {
      if (!form.file) return toast(t('suite.need_file'), '', 'bad');
      if (!form.password) return toast(t('suite.need_pw'), '', 'bad');
      busy.value = true;
      try {
        const r = await API.post('/api/suites/import', {
          path: form.file, password: form.password, name: form.name,
        });
        dlg.value = '';
        await load();
        toast(t('suite.imported', { name: (r.item && r.item.name) || '' }), '', 'ok');
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }

    function askSwitch(it) {
      if (!it || it.id === currentId.value) return;
      target.value = it;
      menu.id = '';
      dlg.value = 'confirm';
    }
    function goGenerate() {
      dlg.value = '';
      open.value = false;
      ui.node = 'generate';
    }

    function clearFx() {
      fxTimers.forEach(id => clearTimeout(id));
      fxTimers = [];
      fx.on = false;
      fx.phase = '';
    }
    function later(ms, fn) {
      fxTimers.push(setTimeout(fn, ms));
    }

    async function afterSwitch() {
      if (window.flushAll) {
        try { await window.flushAll(); } catch (e) { /* 切之前已经 flush 过 */ }
      }
      await refreshState({ probe: false });
      store.suiteTick = (store.suiteTick || 0) + 1;
      store.refreshSeq = (store.refreshSeq || 0) + 1;
    }

    async function callSwitch(save) {
      const it = target.value;
      if (!it) return false;
      const body = {};
      if (save === true || save === false) body.save = save;
      try {
        const r = await API.post(
          '/api/suites/' + encodeURIComponent(it.id) + '/switch',
          body, { silent: true });
        return r;
      } catch (e) {
        const b = e && e.body || {};
        if (b.code === 'jobs_active') {
          jobsN.value = b.n || 0;
          jobsList.value = Array.isArray(b.jobs) ? b.jobs : [];
          dlg.value = 'jobs';
          return null;
        }
        if (b.code === 'dirty') {
          dirtyName.value = (b.current && b.current.name) || '';
          dlg.value = 'dirty';
          return null;
        }
        toast((e && e.message) || t('suite.title'), e && e.hint || '', 'bad');
        return null;
      }
    }

    async function runSwitch(save) {
      if (busy.value) return;
      const it = target.value;
      if (!it) return;
      if (window.flushAll) {
        try { await window.flushAll(); } catch (e) { /* 没存上也不挡切换拦截 */ }
      }
      busy.value = true;
      dlg.value = '';
      const skipAnim = reduced();
      let origin = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
      const el = cardEls[it.id];
      if (el && el.getBoundingClientRect) {
        const r = el.getBoundingClientRect();
        origin = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      }
      try {
        if (skipAnim) {
          const r = await callSwitch(save);
          if (!r) return;
          currentId.value = r.current || it.id;
          await finishSwitch(r);
          return;
        }
        fx.on = true;
        fx.phase = 'gather';
        fx.color = it.color || '#D9A34F';
        fx.name = it.name;
        fx.cx = origin.x + 'px';
        fx.cy = origin.y + 'px';
        later(180, () => { fx.phase = 'veil'; });
        later(400, async () => {
          fx.phase = 'name';
          const r = await callSwitch(save);
          if (!r) {
            clearFx();
            return;
          }
          currentId.value = r.current || it.id;
          await afterSwitch();
          later(120, () => { fx.phase = 'fade'; });
          later(500, () => {
            clearFx();
            open.value = false;
            toastSwitch(r);
          });
        });
      } finally {
        busy.value = false;
      }
    }

    async function finishSwitch(r) {
      await afterSwitch();
      open.value = false;
      toastSwitch(r);
    }
    function toastSwitch(r) {
      const name = (r && r.name) || (target.value && target.value.name) || '';
      const skip = (r && r.skipped_accounts) || [];
      toast(t('suite.switched', { name }),
            skip.length ? t('suite.skipped', { s: skip.join('、') }) : '',
            skip.length ? 'bad' : 'ok');
      load().catch(() => {});
    }

    onMounted(() => {
      load().catch(() => {});
      document.addEventListener('click', onDoc);
      window.addEventListener('keydown', onKey);
    });
    onUnmounted(() => {
      document.removeEventListener('click', onDoc);
      window.removeEventListener('keydown', onKey);
      clearFx();
    });

    
    function formatAge(sec) {
      sec = Number(sec) || 0;
      if (sec < 60) return sec + ' 秒前';
      if (sec < 3600) return Math.floor(sec / 60) + ' 分钟前';
      if (sec < 86400) return Math.floor(sec / 3600) + ' 小时前';
      return Math.floor(sec / 86400) + ' 天前';
    }
    async function cleanupJob(j) {
      if (!j || !j.id) return;
      busy.value = true;
      try {
        await API.post('/api/jobs/' + encodeURIComponent(j.id) + '/cancel', {}, { silent: true });
        jobsList.value = jobsList.value.filter(x => x.id !== j.id);
        jobsN.value = jobsList.value.length;
        if (!jobsList.value.length) {
          dlg.value = '';
          if (target && target.value) await doSwitch(target.value);
        }
      } catch (e) {
        toast((e && e.message) || String(e), (e && e.hint) || '', 'bad');
      } finally {
        busy.value = false;
      }
    }

    return {
      formatAge, cleanupJob, jobsList,
      open, items, current, currentId, colors, autosave, busy, menu, dlg, form,
      target, jobsN, dirtyName, fx, t, otherSuites,
      toggle, closeAll, setCardEl, usedWhen, countsLine, accLine,
      openCreate, doCreate, openSaveAs, doSaveAs, openMenu, openEdit, doEdit, openColor, doColor,
      doDup, openDel, doDel, doRestore, openExport, pickExport, doExport,
      openImport, pickImport, doImport, askSwitch, goGenerate, runSwitch,
      picked,
    };
  },
  template: `
  <div class="suite-root" @click.stop>
    <button class="tb suite-trig" type="button" @click="toggle" :title="t('suite.title')">
      <span class="suite-dot" :style="{background: current.color}"></span>
      <span class="suite-lab">{{ current.name }}</span>
      <span class="suite-caret">▾</span>
    </button>
    <div class="suite-panel" v-if="open" :class="{fx: fx.on}">
      <div class="suite-hd">
        <h3>{{ t('suite.title') }}</h3>
        <button class="btn sm primary" type="button" @click="openCreate">{{ t('suite.new') }}</button>
      </div>
      <div class="suite-grid">
        <div v-for="it in items" :key="it.id"
             class="suite-card"
             :class="{on: it.id===currentId, away: fx.on && target && it.id!==target.id, hero: fx.on && target && it.id===target.id}"
             :style="{'--suite': it.color}"
             :ref="el => setCardEl(it.id, el)">
          <span class="suite-badge" v-if="it.id===currentId">{{ t('suite.using') }}</span>
          <div class="suite-card-hd">
            <span class="suite-dot" :style="{background: it.color}"></span>
            <b>{{ it.name }}</b>
          </div>
          <p class="suite-note">{{ it.note || ' ' }}</p>
          <div class="suite-meta">{{ countsLine(it) }}</div>
          <div class="suite-meta">{{ accLine(it) }}</div>
          <div class="suite-meta muted">{{ t('suite.used', { when: usedWhen(it.used_at) }) }}</div>
          <div class="suite-card-ft">
            <button v-if="it.id!==currentId" class="btn sm" type="button" @click="askSwitch(it)">{{ t('suite.switch') }}</button>
            <span style="flex:1"></span>
            <button class="btn sm ghost suite-more" type="button" :title="t('suite.more')" @click="openMenu(it, $event)">⋯</button>
          </div>
        </div>
      </div>
      <div class="suite-ft">
        <button class="btn sm ghost" type="button" @click="openImport">{{ t('suite.import') }}</button>
      </div>
    </div>

    <ul class="ctx suite-ctx" v-if="menu.id"
        :style="{left: menu.x+'px', top: menu.y+'px'}">
      <li v-if="picked() && picked().id===currentId" @click="openSaveAs()">{{ t('suite.save_as') }}</li>
      <li v-if="picked() && picked().id===currentId" class="sep"></li>
      <li @click="openEdit()">{{ t('suite.rename') }}</li>
      <li @click="openColor()">{{ t('suite.recolor') }}</li>
      <li @click="doDup()">{{ t('suite.dup') }}</li>
      <li @click="openExport()">{{ t('suite.export') }}</li>
      <li v-if="picked() && picked().builtin" @click="doRestore()">{{ t('suite.restore') }}</li>
      <li v-if="picked() && !picked().builtin" class="bad" @click="openDel()">{{ t('suite.delete') }}</li>
    </ul>

    <div class="dlg-mask suite-dlg-mask" v-if="dlg" @click.self="dlg=''">
      <div class="dlg suite-dlg" v-if="dlg==='create'">
        <h3>{{ t('suite.new_title') }}</h3>
        <div class="field"><label>{{ t('suite.name') }}</label><input v-model="form.name" autofocus></div>
        <div class="field"><label>{{ t('suite.note') }} <span class="hint">{{ t('suite.note_hint') }}</span></label><input v-model="form.note"></div>
        <div class="field"><label>{{ t('suite.color') }}</label>
          <div class="suite-swatches">
            <button v-for="c in colors" :key="c" type="button" class="suite-sw"
                    :class="{on: form.color===c}" :style="{background:c}" @click="form.color=c"></button>
          </div>
        </div>
        <div class="field"><label>{{ t('suite.from_label') }}</label>
          <div class="suite-from">
            <label><input type="radio" value="current" v-model="form.fromKind">
              {{ t('suite.from.current', { name: current.name }) }}</label>
            <label>
              <input type="radio" value="other" v-model="form.fromKind" :disabled="!otherSuites.length">
              {{ t('suite.from.other') }}
              <select v-model="form.fromId" :disabled="form.fromKind!=='other' || !otherSuites.length">
                <option v-for="s in otherSuites" :key="s.id" :value="s.id">{{ s.name }}</option>
              </select>
            </label>
            <label><input type="radio" value="factory" v-model="form.fromKind">
              {{ t('suite.from.factory') }}</label>
          </div>
        </div>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" type="button" @click="dlg=''">{{ t('common.cancel') }}</button>
          <button class="btn primary" type="button" :disabled="busy" @click="doCreate">{{ t('suite.create_go') }}</button>
        </div>
      </div>
      <div class="dlg suite-dlg" v-else-if="dlg==='save'">
        <h3>{{ t('suite.save_as') }}</h3>
        <div class="field"><label>{{ t('suite.name') }}</label><input v-model="form.name" autofocus></div>
        <div class="field"><label>{{ t('suite.note') }} <span class="hint">{{ t('suite.note_hint') }}</span></label><input v-model="form.note"></div>
        <div class="field"><label>{{ t('suite.color') }}</label>
          <div class="suite-swatches">
            <button v-for="c in colors" :key="c" type="button" class="suite-sw"
                    :class="{on: form.color===c}" :style="{background:c}" @click="form.color=c"></button>
          </div>
        </div>
        <label class="suite-chk">
          <input type="checkbox" v-model="form.restore">
          {{ t('suite.save_restore', { name: current.name }) }}
        </label>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" type="button" @click="dlg=''">{{ t('common.cancel') }}</button>
          <button class="btn primary" type="button" :disabled="busy" @click="doSaveAs">{{ t('suite.create_go') }}</button>
        </div>
      </div>
      <div class="dlg suite-dlg" v-else-if="dlg==='edit'">
        <h3>{{ t('suite.rename') }}</h3>
        <div class="field"><label>{{ t('suite.name') }}</label><input v-model="form.name"></div>
        <div class="field"><label>{{ t('suite.note') }}</label><input v-model="form.note"></div>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" type="button" @click="dlg=''">{{ t('common.cancel') }}</button>
          <button class="btn primary" type="button" :disabled="busy" @click="doEdit">{{ t('suite.save_go') }}</button>
        </div>
      </div>
      <div class="dlg suite-dlg" v-else-if="dlg==='color'">
        <h3>{{ t('suite.recolor') }}</h3>
        <div class="suite-swatches">
          <button v-for="c in colors" :key="c" type="button" class="suite-sw"
                  :class="{on: form.color===c}" :style="{background:c}" @click="doColor(c)"></button>
        </div>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" type="button" @click="dlg=''">{{ t('common.cancel') }}</button>
        </div>
      </div>
      <div class="dlg suite-dlg" v-else-if="dlg==='confirm'">
        <h3>{{ t('suite.confirm_switch', { name: (target && target.name) || '' }) }}</h3>
        <p v-if="autosave">{{ t('suite.confirm_autosave', { name: current.name }) }}</p>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" type="button" @click="dlg=''">{{ t('common.cancel') }}</button>
          <button class="btn primary" type="button" :disabled="busy" @click="runSwitch()">{{ t('suite.switch') }}</button>
        </div>
      </div>
      <div class="dlg suite-dlg" v-else-if="dlg==='dirty'">
        <h3>{{ t('suite.dirty_ask', { name: dirtyName }) }}</h3>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" type="button" @click="dlg=''">{{ t('common.cancel') }}</button>
          <button class="btn" type="button" :disabled="busy" @click="runSwitch(false)">{{ t('suite.dirty_discard') }}</button>
          <button class="btn primary" type="button" :disabled="busy" @click="runSwitch(true)">{{ t('suite.dirty_save') }}</button>
        </div>
      </div>
      <div class="dlg suite-dlg" v-else-if="dlg==='jobs'">
        <h3>{{ t('suite.jobs', { n: String(jobsN) }) }}</h3>
        <div class="jobs-block-list" style="max-height:240px;overflow:auto;margin-top:8px">
          <div v-for="j in jobsList" :key="j.id" class="row" style="justify-content:space-between;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid rgba(127,127,127,.25)">
            <div style="flex:1;min-width:0;font-size:12px;line-height:1.4">
              <div>{{ j.task_id || '—' }} · {{ j.task_name || '' }}<span v-if="j.task_status">（{{ j.task_status }}）</span></div>
              <div style="opacity:.75">{{ j.kind }} · {{ j.status }} · {{ formatAge(j.age_sec) }}</div>
            </div>
            <button v-if="j.stale" class="btn sm danger" type="button" :disabled="busy" @click="cleanupJob(j)">清理掉</button>
          </div>
        </div>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" type="button" @click="dlg=''">{{ t('common.cancel') }}</button>
          <button class="btn primary" type="button" @click="goGenerate">{{ t('suite.jobs_go') }}</button>
        </div>
      </div>
      <div class="dlg suite-dlg" v-else-if="dlg==='del'">
        <h3>{{ t('suite.delete') }}「{{ target && target.name }}」？</h3>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" type="button" @click="dlg=''">{{ t('common.cancel') }}</button>
          <button class="btn danger" type="button" :disabled="busy" @click="doDel">{{ t('suite.delete') }}</button>
        </div>
      </div>
      <div class="dlg suite-dlg" v-else-if="dlg==='export'">
        <h3>{{ t('suite.export') }}</h3>
        <div class="field"><label>{{ t('suite.pw') }}</label><input type="password" v-model="form.password" autocomplete="new-password"></div>
        <div class="field"><label>{{ t('suite.pw2') }}</label><input type="password" v-model="form.password2" autocomplete="new-password"></div>
        <div class="field"><label>{{ t('suite.path') }}</label>
          <div class="row"><input v-model="form.path" style="flex:1"><button class="btn sm" type="button" @click="pickExport">{{ t('suite.pick') }}</button></div>
        </div>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" type="button" @click="dlg=''">{{ t('common.cancel') }}</button>
          <button class="btn primary" type="button" :disabled="busy" @click="doExport">{{ t('suite.export_go') }}</button>
        </div>
      </div>
      <div class="dlg suite-dlg" v-else-if="dlg==='import'">
        <h3>{{ t('suite.import') }}</h3>
        <div class="field"><label>{{ t('suite.file') }}</label>
          <div class="row"><input v-model="form.file" style="flex:1"><button class="btn sm" type="button" @click="pickImport">{{ t('suite.pick') }}</button></div>
        </div>
        <div class="field"><label>{{ t('suite.pw') }}</label><input type="password" v-model="form.password" autocomplete="current-password"></div>
        <div class="field"><label>{{ t('suite.import_name') }}</label><input v-model="form.name"></div>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" type="button" @click="dlg=''">{{ t('common.cancel') }}</button>
          <button class="btn primary" type="button" :disabled="busy" @click="doImport">{{ t('suite.import_go') }}</button>
        </div>
      </div>
    </div>

    <teleport to="body">
      <div class="suite-fx" v-if="fx.on" :class="fx.phase"
           :style="{'--cx': fx.cx, '--cy': fx.cy, '--suite': fx.color}">
        <div class="suite-fx-veil"></div>
        <div class="suite-fx-name" v-if="fx.phase==='name' || fx.phase==='fade'">
          <span class="suite-dot lg" :style="{background: fx.color}"></span>
          <span>{{ fx.name }}</span>
        </div>
      </div>
    </teleport>
  </div>`
};
