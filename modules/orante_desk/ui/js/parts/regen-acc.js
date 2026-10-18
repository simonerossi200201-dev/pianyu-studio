// 重生时临时换账号：选哪个账号 + 时长怎么换（跟 orante.engines.caps.snap_duration 同一条规则）。
// 规格 98：多了「本地模型 · Studio Zero」（虚拟账号 STUDIO，状态在 store.studio）。
// 规格 99：本地模型也能当任务账号（store.accounts 最后一个，virtual=true）；任务本来就是本地模型时不再重复列。
window.RegenAcc = (() => {
  const STUDIO_ID = 'STUDIO';
  const DURS = { flow: [4, 6, 8], grok: [6, 10, 15], studio: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15] };
  const LABEL = { flow: 'Flow', grok: 'Grok', studio: '本地模型' };

  function engLabel(e) { return LABEL[String(e || '').toLowerCase()] || (e || '?'); }

  // 取不短于原时长的最短一档；比最长一档还长就取最长一档
  function snap(engine, sec) {
    const allowed = DURS[String(engine || '').toLowerCase()];
    if (!allowed) return Number(sec) || 0;
    const d = Math.round(Number(sec) || 0);
    if (allowed.includes(d)) return d;
    const longer = allowed.filter(x => x >= d);
    return longer.length ? longer[0] : allowed[allowed.length - 1];
  }

  function cooling(a) {
    const ts = Number(((a && a.extra) || {}).throttled_until_ts || 0);
    return ts * 1000 > Date.now();
  }

  // 能拿来重生的别的账号：启用着、flow / grok
  function others(task) {
    const own = task && task.account_id;
    return (store.accounts || []).filter(a =>
      a && a.id !== own && a.enabled !== 0 && a.enabled !== false
      && (a.engine === 'flow' || a.engine === 'grok'))
      .sort((x, y) => (x.engine === y.engine ? String(x.name || x.id).localeCompare(String(y.name || y.id))
        : (x.engine === 'grok' ? -1 : 1)));
  }

  // 本地模型：始终作为可选项列出，点击即可自由切换（若未开启自动开启）
  function studio() {
    const s = store.studio || null;
    return {
      id: STUDIO_ID, engine: 'studio', name: '本地 AI · LTX-2.5 / FLUX.2 (可选项)',
      enabled: true, rawEnabled: !!(s && s.enabled), alive: !!(s && s.alive), api_up: !!(s && s.api_up),
      quality: (s && s.quality) || 'balanced', virtual: true,
    };
  }
  function studioSub(st) {
    if (!st.rawEnabled) return '可选项 · 点击即用';
    if (!st.alive) return st.api_up ? t('regen.acc.studio_comfy_down') : t('regen.acc.studio_down');
    const q = ['fast', 'balanced', 'high', 'best'].includes(st.quality) ? st.quality : 'balanced';
    return t('regen.acc.studio_q.' + q);
  }

  function accOf(id) {
    if (id === STUDIO_ID) return studio();
    return (store.accounts || []).find(a => a.id === id) || null;
  }

  function label(a) {
    if (!a) return '';
    if (a.engine === 'studio') return '本地 AI · LTX-2.5 (可选项)';
    return engLabel(a.engine) + ' · ' + (a.name || a.id);
  }

  // segs: [{s, duration}]；返回要换时长的那几段的说明
  function durNote(accId, segs) {
    const a = accOf(accId);
    if (!a) return '';
    const eng = a.engine;
    const changed = [];
    for (const sg of (segs || [])) {
      const d0 = Math.round(Number(sg && sg.duration) || 0);
      if (!d0) continue;
      const d1 = snap(eng, d0);
      if (d1 !== d0) changed.push({ s: sg.s, d0, d1 });
    }
    if (!changed.length) return t('regen.acc.dur_same', { eng: engLabel(eng) });
    // 同样的换法合并成一句：s003、s007：8 → 10 秒
    const groups = {};
    for (const c of changed) {
      const k = c.d0 + ' → ' + c.d1;
      (groups[k] = groups[k] || []).push('s' + String(c.s).padStart(3, '0'));
    }
    const parts = Object.keys(groups).map(k => t('regen.acc.dur_part', {
      k, segs: groups[k].slice(0, 6).join('、') + (groups[k].length > 6 ? '…' : ''),
    }));
    return t('regen.acc.dur', { eng: engLabel(eng), list: parts.join('；') });
  }

  // 菜单项：原账号（优先付费账号）/ 别的 Flow、Grok / 本地模型（可选项）
  function items(task) {
    const own = task ? accOf(task.account_id) : null;
    const out = [{ id: '', label: own ? ('⚡ 优先 · ' + label(own)) : t('regen.acc.same_bare'), sub: '默认极速' }];
    for (const a of others(task)) {
      let sub = '';
      if (a.login_state === 'need_relogin') sub = t('regen.acc.relogin');
      else if (cooling(a)) sub = t('regen.acc.cooling');
      out.push({ id: a.id, label: label(a), sub, engine: a.engine });
    }
    if (task && task.account_id === STUDIO_ID) return out;   // 任务本来就用本地模型：「原账号」就是它
    const st = studio();
    out.push({ id: STUDIO_ID, label: label(st), sub: studioSub(st), engine: 'studio', disabled: false, sep: true });
    return out;
  }

  // ---- 全局弹出菜单（一个页面只有一个；position: fixed，抽屉 / 弹窗里也不会被裁掉）
  let pop = null;
  let done = null;
  function closeMenu(val) {
    if (pop) { pop.remove(); pop = null; }
    document.removeEventListener('mousedown', onDoc, true);
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('resize', onDoc, true);
    const cb = done; done = null;
    if (cb) cb(val === undefined ? null : val);
  }
  function onDoc(ev) { if (pop && ev && ev.target && pop.contains(ev.target)) return; closeMenu(null); }
  function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); closeMenu(null); } }

  // 在 anchor（按钮）下面弹出菜单，选中返回账号 id（'' = 原账号），取消返回 null
  function menu(anchor, opts) {
    closeMenu(null);
    const o = opts || {};
    return new Promise(resolve => {
      done = resolve;
      pop = document.createElement('div');
      pop.className = 'ra-pop';
      const head = document.createElement('div');
      head.className = 'ra-pop-h';
      head.textContent = o.title || t('regen.acc.title');
      pop.appendChild(head);
      for (const it of items(o.task || null)) {
        if (it.sep) { const hr = document.createElement('div'); hr.className = 'ra-pop-sep'; pop.appendChild(hr); }
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'ra-pop-i' + (it.engine ? ' ' + it.engine : '') + (it.disabled ? ' off' : '');
        b.textContent = it.label;
        if (it.sub) { const s = document.createElement('span'); s.className = 'ra-sub'; s.textContent = it.sub; b.appendChild(s); }
        if (it.disabled) {
          b.title = t('regen.acc.studio_off_tip');
        } else {
          b.addEventListener('click', async ev => {
            ev.stopPropagation();
            if (it.id === STUDIO_ID && !(store.studio && store.studio.enabled)) {
              try { await API.post('/api/accounts/STUDIO', { enabled: true }); if (window.refreshAccounts) window.refreshAccounts(); } catch (e) {}
            }
            closeMenu(it.id);
          });
        }
        pop.appendChild(b);
      }
      const note = o.segs && o.segs.length && !(o.task && o.task.account_id === STUDIO_ID) ? durNote(STUDIO_ID, o.segs) : '';
      if (note) { const n = document.createElement('div'); n.className = 'ra-pop-note'; n.textContent = note; pop.appendChild(n); }
      document.body.appendChild(pop);
      const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect()
        : { left: 40, right: 40, top: 40, bottom: 40 };
      const vw = window.innerWidth || 1200, vh = window.innerHeight || 800;
      const w = pop.offsetWidth || 240, h = pop.offsetHeight || 200;
      let x = Math.min(r.left, vw - w - 8), y = r.bottom + 4;
      if (y + h > vh - 8) y = Math.max(8, r.top - h - 4);
      pop.style.left = Math.max(8, x) + 'px';
      pop.style.top = y + 'px';
      setTimeout(() => {
        document.addEventListener('mousedown', onDoc, true);
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('resize', onDoc, true);
      }, 0);
    });
  }

  // 给 toast 用："用本地模型重生 s003"
  function regenToast(accId, segNums) {
    if (!accId) return;
    const a = accOf(accId);
    const ss = (segNums || []).map(n => 's' + String(n).padStart(3, '0')).join('、');
    if (window.toast) window.toast(t('regen.acc.started', { acc: label(a), segs: ss }), '', 'ok');
  }

  return { STUDIO_ID, DURS, snap, others, studio, accOf, label, durNote, engLabel, cooling, items, menu, closeMenu, regenToast };
})();

// 一排小按钮：原账号 / 别的 Flow、Grok 账号 / 本地模型。v-model 是账号 id，'' = 用任务自己的账号。
window.RegenAccPick = {
  name: 'RegenAccPick',
  props: {
    task: { type: Object, default: null },
    modelValue: { type: String, default: '' },
    segs: { type: Array, default: () => [] },
    disabled: { type: Boolean, default: false },
    title: { type: String, default: '' },
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    const { computed } = Vue;
    const L = window.RegenAcc;
    const list = computed(() => L.items(props.task));
    const note = computed(() => (props.modelValue ? L.durNote(props.modelValue, props.segs) : ''));
    async function pick(it) {
      if (props.disabled || it.disabled) return;
      if (it.id === L.STUDIO_ID && !(store.studio && store.studio.enabled)) {
        try { await API.post('/api/accounts/STUDIO', { enabled: true }); if (window.refreshAccounts) window.refreshAccounts(); } catch (e) {}
      }
      emit('update:modelValue', it.id || '');
    }
    return { list, note, pick, t: window.t };
  },
  template: `
  <div class="regen-acc">
    <div class="ra-title">{{ title || t('regen.acc.title') }}</div>
    <div class="ra-chips">
      <button type="button" v-for="it in list" :key="it.id || 'own'" class="ra-chip"
              :class="[it.engine || '', {on: (modelValue || '') === it.id, off: it.disabled}]"
              :disabled="disabled" :title="it.disabled ? t('regen.acc.studio_off_tip') : ''"
              @click="pick(it)">
        {{ it.label }}<span class="ra-sub" v-if="it.sub">{{ it.sub }}</span>
      </button>
    </div>
    <p class="ra-note" v-if="modelValue">{{ t('regen.acc.hint') }}</p>
    <p class="ra-note ra-dur" v-if="note">{{ note }}</p>
  </div>`,
};

// 分体按钮：左边照旧（原账号，一点就重生），右边 ▾ 弹出菜单选别的账号 / 本地模型。
// <regen-acc-menu :task="task" :segs="[seg]" :disabled="busy" btn-class="btn sm" @go="acc => doIt(acc)">重生</regen-acc-menu>
window.RegenAccMenu = {
  name: 'RegenAccMenu',
  props: {
    task: { type: Object, default: null },
    segs: { type: Array, default: () => [] },
    disabled: { type: Boolean, default: false },
    btnClass: { type: String, default: 'btn sm' },
  },
  emits: ['go'],
  setup(props, { emit }) {
    const { ref } = Vue;
    const caret = ref(null);
    function goDefault() { if (!props.disabled) emit('go', ''); }
    async function open() {
      if (props.disabled) return;
      const acc = await window.RegenAcc.menu(caret.value, { task: props.task, segs: props.segs });
      if (acc !== null && acc !== undefined) emit('go', acc);
    }
    return { caret, goDefault, open, t: window.t };
  },
  template: `
  <span class="ra-split">
    <button type="button" :class="btnClass" :disabled="disabled" @click.stop="goDefault"><slot></slot></button><button
      type="button" ref="caret" :class="btnClass + ' ra-caret'" :disabled="disabled"
      :title="t('regen.acc.more')" @click.stop="open">▾</button>
  </span>`,
};
