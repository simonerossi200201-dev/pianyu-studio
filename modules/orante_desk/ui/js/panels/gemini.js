// 节点 ② 多模态：描述语 / 运镜 / 结语 / 问题句 + 看图写描述语 + 成品提示词预览。
// 提示词块：去抖自动保存；切块 / 切任务 / 切面板 / 关窗口先 flush，绝不拿服务端旧值盖掉手写。

// 规格 70：覆盖库机位/动作的显示层（卡片、摘要、收起）。不改字段、不改保存。
window.CoverageEditorLogic = (function () {
  const LS = { cameras: 'orante.cov.cameras.open', actions: 'orante.cov.actions.open' };
  function readOpen(kind, storage) {
    const st = storage || (typeof localStorage !== 'undefined' ? localStorage : null);
    if (!st) return false;
    try { return st.getItem(LS[kind]) === '1'; } catch (e) { return false; }
  }
  function writeOpen(kind, open, storage) {
    const st = storage || (typeof localStorage !== 'undefined' ? localStorage : null);
    if (!st) return;
    try { st.setItem(LS[kind], open ? '1' : '0'); } catch (e) { /* 隐私模式写不了就当没记住 */ }
  }
  function classOpt(cls, t) {
    const c = String(cls || 'A').toUpperCase();
    const lab = t ? t('gemini.cov.class.' + c) : c;
    return c + ' · ' + lab;
  }
  function phaseLabel(ph, t) {
    const p = String(ph || 'hold');
    return t ? t('gemini.cov.phase.' + p) : p;
  }
  function summaryCam(row, t) {
    const r = row || {};
    const rankLab = t ? t('gemini.cov.rank') : '权重';
    return [r.id, r.name].filter(Boolean).join(' ') + ' · ' + (r.class || 'A') + ' · ' + rankLab + ' ' + (r.rank != null ? r.rank : '');
  }
  function summaryAct(row, t) {
    const r = row || {};
    const rankLab = t ? t('gemini.cov.rank') : '权重';
    return [r.id, r.name].filter(Boolean).join(' ') + ' · ' + phaseLabel(r.phase, t) + ' · ' + rankLab + ' ' + (r.rank != null ? r.rank : '');
  }
  function autosize(el) {
    if (!el) return;
    const cs = (typeof getComputedStyle === 'function') ? getComputedStyle(el) : null;
    const lh = (cs && parseFloat(cs.lineHeight)) || 19.5;
    const pad = cs ? (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0) : 12;
    const minH = lh * 3 + pad;
    const maxH = lh * 12 + pad;
    el.style.height = 'auto';
    const h = Math.min(maxH, Math.max(minH, el.scrollHeight || minH));
    el.style.height = h + 'px';
  }
  return { LS, readOpen, writeOpen, classOpt, phaseLabel, summaryCam, summaryAct, autosize };
})();

// 规格 75：② 多模态卡片多选 / 右键同步。纯函数，测试和面板共用。
window.GeminiCardLogic = (function () {
  const UNDO_MS = 15000;

  function fmtP(v) {
    if (v == null || v === '') return '';
    const s = String(v).trim();
    const m = s.match(/^([pb])(\d+)$/i);
    if (m) return m[1].toLowerCase() + String(parseInt(m[2], 10)).padStart(2, '0');
    return s;
  }

  function isTypingTarget(el) {
    if (!el) return false;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
    if (el.isContentEditable) return true;
    if (el.closest && el.closest('input, textarea, select, [contenteditable="true"]')) return true;
    return false;
  }

  function isFormChrome(el) {
    if (!el || !el.closest) return false;
    return !!el.closest('textarea, input, select, button, a, .img-check');
  }

  function toggleIn(arr, p) {
    const out = (arr || []).slice();
    const i = out.indexOf(p);
    if (i >= 0) out.splice(i, 1);
    else out.push(p);
    return out;
  }

  function addAll(arr, ps) {
    const out = (arr || []).slice();
    (ps || []).forEach(p => { if (p && out.indexOf(p) < 0) out.push(p); });
    return out;
  }

  function rangeSelect(ps, fromP, toP) {
    const list = ps || [];
    const a = list.indexOf(fromP);
    const b = list.indexOf(toP);
    if (b < 0) return toP ? [toP] : [];
    if (a < 0) return [toP];
    const lo = Math.min(a, b), hi = Math.max(a, b);
    return list.slice(lo, hi + 1);
  }

  function initial() {
    return {
      sel: { subjects: [], brolls: [] },
      lastP: '',
      lastGroup: 'subjects',
      focusP: '',
    };
  }

  function reduce(state, action) {
    const s = state || initial();
    const next = {
      sel: {
        subjects: (s.sel.subjects || []).slice(),
        brolls: (s.sel.brolls || []).slice(),
      },
      lastP: s.lastP || '',
      lastGroup: s.lastGroup || 'subjects',
      focusP: s.focusP || '',
    };
    const group = action.group === 'brolls' ? 'brolls' : 'subjects';
    const p = action.p ? fmtP(action.p) : '';
    const groupPs = (action.groupPs || []).map(fmtP);
    const type = action.type || 'click';

    if (type === 'esc') {
      next.sel.subjects = [];
      next.sel.brolls = [];
      return next;
    }
    if (type === 'none') {
      next.sel[group] = [];
      next.lastGroup = group;
      return next;
    }
    if (type === 'all' || type === 'ctrla') {
      next.sel[group] = groupPs.slice();
      next.lastGroup = group;
      return next;
    }
    if (type === 'invert') {
      next.sel[group] = groupPs.filter(x => next.sel[group].indexOf(x) < 0);
      next.lastGroup = group;
      return next;
    }
    if (type === 'check' || ((type === 'click' || type === 'ctrl') && (action.ctrl || action.meta))) {
      next.sel[group] = toggleIn(next.sel[group], p);
      next.lastP = p;
      next.lastGroup = group;
      next.focusP = p;
      return next;
    }
    if ((type === 'click' || type === 'shift') && action.shift) {
      const from = (s.lastGroup === group && s.lastP) ? s.lastP : p;
      const range = rangeSelect(groupPs, from, p);
      next.sel[group] = addAll(next.sel[group], range);
      next.lastP = p;
      next.lastGroup = group;
      next.focusP = p;
      return next;
    }
    // 普通单击：只改焦点，不改勾选
    next.lastP = p;
    next.lastGroup = group;
    next.focusP = p;
    return next;
  }

  function copyTargets(selected, fromP) {
    const src = fmtP(fromP);
    return (selected || []).map(fmtP).filter(p => p && p !== src);
  }

  function syncRequest(fromP, selected, fields) {
    return {
      from_p: fmtP(fromP),
      to_ps: copyTargets(selected, fromP),
      fields: fields && fields.length ? fields.slice() : ['desc'],
    };
  }

  function restoreBody(changed) {
    return { restore: changed || [] };
  }

  function syncDisabled(fromP, selected) {
    return copyTargets(selected, fromP).length === 0;
  }

  function menuFlags(im, selected, groupN) {
    const desc = String((im && im.desc) || '').trim();
    const voice = String((im && im.voice) || '').trim();
    const exists = !im || im.exists !== false;
    const fromP = im && im.p;
    const noTgt = syncDisabled(fromP, selected);
    const nTgt = copyTargets(selected, fromP).length;
    const manual = !!(im && (im.desc_manual || im.voice_manual));
    return {
      nTgt,
      nGroup: groupN || 0,
      checked: !!(im && selected && selected.indexOf(im.p) >= 0),
      open: !exists,
      reveal: !exists,
      sync: noTgt,
      syncDesc: noTgt || !desc,
      syncVoice: noTgt || !voice,
      syncBoth: noTgt || (!desc && !voice),
      clear: !desc,
      restoreAuto: !manual,
    };
  }

  function menuTitle(key, flags, t) {
    const f = flags || {};
    const tr = t || ((k) => k);
    if (key === 'open' || key === 'reveal') return f.open ? tr('gemini.ctx.off.missing') : '';
    if (key === 'sync' || key === 'syncDesc' || key === 'syncVoice' || key === 'syncBoth') {
      if (f.sync) return tr('gemini.ctx.off.need_sel');
      if (key === 'syncDesc' && f.syncDesc) return tr('gemini.ctx.off.empty_desc');
      if (key === 'syncVoice' && f.syncVoice) return tr('gemini.ctx.off.empty_voice');
      if (key === 'syncBoth' && f.syncBoth) return tr('gemini.ctx.off.empty_desc');
      return '';
    }
    if (key === 'clear') return f.clear ? tr('gemini.ctx.off.already_empty') : '';
    if (key === 'restoreAuto') return f.restoreAuto ? tr('gemini.ctx.off.not_manual') : '';
    return '';
  }

  function listPs(ps, maxN) {
    const list = ps || [];
    const max = maxN == null ? 8 : maxN;
    if (list.length <= max) return list.join(' ');
    return list.slice(0, max).join(' ') + ' …';
  }

  function copyToastKey(fields) {
    const hasD = (fields || []).indexOf('desc') >= 0;
    const hasV = (fields || []).indexOf('voice') >= 0;
    if (hasD && hasV) return 'gemini.copy.ok_both';
    if (hasV && !hasD) return 'gemini.copy.ok_voice';
    return 'gemini.copy.ok';
  }

  function makeUndo(schedule, unschedule, onExpire, ms) {
    const wait = ms == null ? UNDO_MS : ms;
    let alive = true;
    const id = schedule ? schedule(() => {
      alive = false;
      if (onExpire) onExpire();
    }, wait) : null;
    return {
      get alive() { return alive; },
      expire() {
        if (!alive) return;
        alive = false;
        if (unschedule && id != null) unschedule(id);
        if (onExpire) onExpire();
      },
      run(fn) {
        if (!alive) return false;
        alive = false;
        if (unschedule && id != null) unschedule(id);
        if (fn) fn();
        return true;
      },
    };
  }

  return {
    UNDO_MS, fmtP, isTypingTarget, isFormChrome,
    toggleIn, addAll, rangeSelect, initial, reduce,
    copyTargets, syncRequest, restoreBody, syncDisabled,
    menuFlags, menuTitle, listPs, copyToastKey, makeUndo,
  };
})();

// 规格 78：每张卡 desc / voice 的本地草稿。后台刷新整表替换 manifest 时，
// 有草稿的框跟着草稿走，没有的才跟仓库。localStorage 按 tid 存，崩了也不丢。
window.GeminiDescDrafts = (function () {
  const SAVE_MS = 700;
  const FADE_MS = 2000;
  const TTL_MS = 7 * 24 * 60 * 60 * 1000;
  const PREFIX = 'orante.desc.draft.';

  function draftKey(p, field) {
    return String(p || '') + ':' + String(field || 'desc');
  }

  function lsKey(tid) {
    return PREFIX + String(tid || '');
  }

  function nowMs(now) {
    return now == null ? Date.now() : now;
  }

  function storageOf(storage) {
    if (storage) return storage;
    try {
      if (typeof localStorage !== 'undefined') return localStorage;
    } catch (e) { /* 隐私模式 */ }
    return null;
  }

  function draftOf(drafts, im, field) {
    const k = draftKey(im && im.p, field);
    if (drafts && Object.prototype.hasOwnProperty.call(drafts, k)) return drafts[k];
    return (im && im[field]) || '';
  }

  function setDraft(drafts, p, field, value) {
    if (!drafts) return;
    drafts[draftKey(p, field)] = value;
  }

  function clearDraft(drafts, p, field) {
    if (!drafts) return;
    delete drafts[draftKey(p, field)];
  }

  function clearObj(o) {
    if (!o) return;
    Object.keys(o).forEach(k => { delete o[k]; });
  }

  function hasDraft(drafts, p, field) {
    return !!(drafts && Object.prototype.hasOwnProperty.call(drafts, draftKey(p, field)));
  }

  function prune(storage, now) {
    const st = storageOf(storage);
    if (!st || typeof st.key !== 'function') return 0;
    const t = nowMs(now);
    const toDel = [];
    const n = st.length || 0;
    for (let i = 0; i < n; i++) {
      const k = st.key(i);
      if (!k || k.indexOf(PREFIX) !== 0) continue;
      let drop = false;
      try {
        const data = JSON.parse(st.getItem(k) || '');
        if (!data || !data.at || (t - Number(data.at)) > TTL_MS) drop = true;
        else if (!data.drafts || !Object.keys(data.drafts).length) drop = true;
      } catch (e) { drop = true; }
      if (drop) toDel.push(k);
    }
    toDel.forEach(k => {
      try { st.removeItem(k); } catch (e) { /* ignore */ }
    });
    return toDel.length;
  }

  function persist(tid, drafts, storage, now) {
    const st = storageOf(storage);
    if (!st || !tid) return;
    prune(st, now);
    const keys = Object.keys(drafts || {});
    const k = lsKey(tid);
    if (!keys.length) {
      try { st.removeItem(k); } catch (e) { /* ignore */ }
      return;
    }
    const blob = {};
    keys.forEach(key => { blob[key] = drafts[key]; });
    try {
      st.setItem(k, JSON.stringify({ at: nowMs(now), drafts: blob }));
    } catch (e) { /* 配额满就丢掉，内存草稿还在 */ }
  }

  function restore(tid, storage, now) {
    const st = storageOf(storage);
    if (!st || !tid) return {};
    prune(st, now);
    try {
      const raw = st.getItem(lsKey(tid));
      if (!raw) return {};
      const data = JSON.parse(raw);
      if (!data || !data.drafts || typeof data.drafts !== 'object') return {};
      if (data.at && (nowMs(now) - Number(data.at)) > TTL_MS) {
        st.removeItem(lsKey(tid));
        return {};
      }
      return data.drafts;
    } catch (e) {
      return {};
    }
  }

  function saveClass(st) {
    if (!st) return '';
    if (st.saving) return 'is-saving';
    if (st.error) return 'is-fail';
    if (st.dirty) return 'is-dirty';
    if (st.savedAt && !st.faded) return 'is-ok';
    return '';
  }

  function saveLabel(st, t) {
    const tr = t || ((k) => k);
    if (!st) return '';
    if (st.saving) return tr('gemini.save.saving');
    if (st.error) return tr('gemini.save.fail');
    if (st.dirty) return tr('gemini.save.dirty');
    if (st.savedAt && !st.faded) return tr('gemini.save.ok_short');
    return '';
  }

  function emptySt() {
    return { dirty: false, saving: false, savedAt: 0, error: '', faded: false };
  }

  function makeSaver(opts) {
    const o = opts || {};
    const saveMs = o.saveMs == null ? SAVE_MS : o.saveMs;
    const fadeMs = o.fadeMs == null ? FADE_MS : o.fadeMs;
    const schedule = o.schedule || ((fn, ms) => setTimeout(fn, ms));
    const unschedule = o.unschedule || ((id) => clearTimeout(id));
    const drafts = o.drafts || {};
    const fieldSave = o.fieldSave || {};
    const post = o.post;
    const persistFn = o.persist;
    const timers = {};
    const fadeTimers = {};
    const inflight = {};

    function mark(key, patch) {
      const cur = fieldSave[key] || emptySt();
      const next = {
        dirty: cur.dirty, saving: cur.saving, savedAt: cur.savedAt,
        error: cur.error, faded: cur.faded,
      };
      Object.assign(next, patch || {});
      fieldSave[key] = next;
      return next;
    }

    function dropFade(key) {
      if (fadeTimers[key]) { unschedule(fadeTimers[key]); delete fadeTimers[key]; }
    }

    function persistNow() {
      if (persistFn) persistFn();
    }

    async function saveNow(im, field) {
      const key = draftKey(im && im.p, field);
      if (timers[key]) { unschedule(timers[key]); delete timers[key]; }
      const value = hasDraft(drafts, im.p, field) ? drafts[key] : ((im && im[field]) || '');
      if (value === ((im && im[field]) || '')) {
        clearDraft(drafts, im.p, field);
        persistNow();
        dropFade(key);
        mark(key, { dirty: false, saving: false, error: '', faded: true });
        return { skipped: true };
      }
      if (!post) return { skipped: true };
      if (inflight[key]) {
        inflight[key].again = true;
        return inflight[key].promise;
      }
      dropFade(key);
      mark(key, { dirty: true, saving: true, error: '', faded: false });
      const rec = { again: false, promise: null };
      rec.promise = (async () => {
        try {
          await post(im, field, value);
          if (drafts[key] === value) {
            clearDraft(drafts, im.p, field);
            persistNow();
            mark(key, { dirty: false, saving: false, savedAt: Date.now(), error: '', faded: false });
            fadeTimers[key] = schedule(() => {
              delete fadeTimers[key];
              const st = fieldSave[key];
              if (st && !st.dirty && !st.saving) mark(key, { faded: true });
            }, fadeMs);
          } else {
            mark(key, { dirty: true, saving: false, faded: false });
          }
          return { ok: true };
        } catch (e) {
          const msg = (e && (e.message || e.hint)) || String(e || '');
          mark(key, { dirty: true, saving: false, error: msg, faded: false });
          return { ok: false, error: msg };
        } finally {
          delete inflight[key];
          if (rec.again && hasDraft(drafts, im.p, field)) {
            saveNow(im, field);
          }
        }
      })();
      inflight[key] = rec;
      return rec.promise;
    }

    function onInput(im, field, value) {
      const key = draftKey(im && im.p, field);
      drafts[key] = value;
      persistNow();
      dropFade(key);
      mark(key, { dirty: true, saving: false, error: '', faded: false });
      if (timers[key]) unschedule(timers[key]);
      timers[key] = schedule(() => {
        delete timers[key];
        saveNow(im, field);
      }, saveMs);
      return key;
    }

    function onBlur(im, field, value) {
      const key = draftKey(im && im.p, field);
      drafts[key] = value;
      persistNow();
      if (timers[key]) { unschedule(timers[key]); delete timers[key]; }
      return saveNow(im, field);
    }

    function cancel(im, field) {
      const key = draftKey(im && im.p, field);
      if (timers[key]) { unschedule(timers[key]); delete timers[key]; }
    }

    function pendingKeys() {
      return Object.keys(timers);
    }

    return {
      onInput, onBlur, saveNow, cancel, pendingKeys, mark, timers, fadeTimers,
    };
  }

  return {
    SAVE_MS, FADE_MS, TTL_MS, PREFIX,
    draftKey, lsKey, draftOf, setDraft, clearDraft, clearObj, hasDraft,
    persist, restore, prune, saveClass, saveLabel, emptySt, makeSaver,
  };
})();


window.GeminiSwitchLogic = (function () {
  const BRICKS = ["运镜卡","运动强制","镜头禁令","人物原地","人物站姿","人物坐姿","比例锁","车辆静止锁","人群保留","群像锁","画质锁","情绪","音色锁","说话指令"];
  const META = ["描述语整理","静音提示"];
  const GROUPS = {
    lens: ["运镜卡","运动强制","镜头禁令"],
    pose: ["人物原地","人物站姿","人物坐姿","比例锁","车辆静止锁"],
    scene: ["人群保留","群像锁"],
    quality: ["画质锁"],
    perf: ["情绪","音色锁"],
    ending: ["说话指令"],
    meta: ["描述语整理","静音提示"],
  };
  function defaultSwitches() {
    const o = {};
    BRICKS.concat(META).forEach(k => { o[k] = true; });
    return o;
  }
  function isMinimal(sw) {
    return BRICKS.concat(["描述语整理"]).every(k => sw[k] === false);
  }
  function applyMinimal(sw, on) {
    const out = { ...sw };
    if (on) {
      BRICKS.concat(["描述语整理"]).forEach(k => { out[k] = false; });
    }
    return out;
  }
  function restoreMinimal(sw, before) {
    return { ...(before || defaultSwitches()) };
  }
  function groupState(sw, g) {
    const keys = GROUPS[g] || [];
    const ons = keys.filter(k => sw[k] !== false);
    if (ons.length === keys.length) return 'on';
    if (ons.length === 0) return 'off';
    return 'mixed';
  }
  function toggleGroup(sw, g, on) {
    const out = { ...sw };
    (GROUPS[g] || []).forEach(k => { out[k] = !!on; });
    return out;
  }
  function batchInit(tasks) {
    const st = { tasks: [], sel: {}, active: null, mode: 'fill_empty', desc: '', voice: '', pickSource: false };
    tasks.forEach(t => {
      const selectable = t.status && t.status !== 'draft' && t.status !== 'trash';
      const images = (t.images || []).map(im => ({
        p: im.p, desc: im.desc || '', role: im.role || '',
        url: im.url || '',
        missing: !(im.desc || '').trim(),
      }));
      // 只有明确没文案时，空镜才进默认勾选。没传 copy 的旧调用仍只勾主体，避免口播任务被带上空镜。
      const noCopy = typeof t.copy === 'string' && !t.copy.trim();
      const countsForFill = im => noCopy || im.role !== '空镜';
      st.tasks.push({
        tid: t.tid, name: t.name || t.tid, status: t.status || 'draft',
        selectable, images, checked: selectable,
        missingDot: selectable && images.some(im => im.missing && countsForFill(im)),
      });
      if (selectable) {
        st.sel[t.tid] = images.filter(countsForFill).map(im => im.p);
      }
    });
    const first = st.tasks.find(t => t.selectable);
    st.active = first ? first.tid : null;
    return st;
  }
  function selectTask(st, tid) { st.active = tid; return st; }
  function toggleImg(st, tid, p, on) {
    const cur = new Set(st.sel[tid] || []);
    if (on) cur.add(p); else cur.delete(p);
    st.sel[tid] = [...cur];
    return st;
  }
  function selectMissing(st, tid) {
    const t = st.tasks.find(x => x.tid === tid);
    if (!t) return st;
    st.sel[tid] = t.images.filter(im => im.missing).map(im => im.p);
    return st;
  }
  function stats(st, mode) {
    // images = 这一轮真正会写的张数。只填空着的时，已有描述语的不计入。
    const overwrite = (mode || st.mode) === 'overwrite';
    let tasks = 0, images = 0, had = 0;
    st.tasks.forEach(t => {
      if (!t.checked || !t.selectable) return;
      const sel = st.sel[t.tid] || [];
      if (!sel.length) return;
      let writeN = 0;
      sel.forEach(p => {
        const im = t.images.find(x => x.p === p);
        const filled = !!(im && !im.missing);
        if (filled) had += 1;
        if (overwrite || !filled) writeN += 1;
      });
      if (writeN) {
        tasks += 1;
        images += writeN;
      }
    });
    return { tasks, images, had, mode: mode || st.mode };
  }
  
  function filterTasks(tasks, q, onlyMissing) {
    const qq = String(q || "").trim().toLowerCase();
    return (tasks || []).filter(tk => {
      if (onlyMissing && !tk.missingDot) return false;
      if (!qq) return true;
      const hay = ((tk.tid || "") + " " + (tk.name || "")).toLowerCase();
      return hay.indexOf(qq) >= 0;
    });
  }
  function invertChecks(tasks) {
    (tasks || []).forEach(tk => { if (tk.selectable) tk.checked = !tk.checked; });
    return tasks;
  }
  function selectAllChecks(tasks, on) {
    (tasks || []).forEach(tk => { if (tk.selectable) tk.checked = !!on; });
    return tasks;
  }

  return { BRICKS, META, GROUPS, defaultSwitches, isMinimal, applyMinimal, restoreMinimal, groupState, toggleGroup, batchInit, selectTask, toggleImg, selectMissing, stats, filterTasks, invertChecks, selectAllChecks };
})();

window.Panel_gemini = (() => {
  const ST_ORDER = ['draft', 'prepared', 'described', 'arranged', 'generating', 'collected', 'finished'];
  const DEFAULT_MODEL = 'gemini-3.5-flash-lite';
  const SAVE_MS = 800;
  const BLOCKS = ['desc', 'camera', 'expand', 'refill_clause', 'upscale', 'bridge', 'bridge_single', 'chain', 'tail'];

  function atLeastPrepared(st) {
    return ST_ORDER.indexOf(st) >= ST_ORDER.indexOf('prepared');
  }

  function mediaUrl(tid, file, ver) {
    return window.srcWithV(file, ver, tid);
  }

  function segLabel(s) {
    const n = Number(s);
    return 's' + String(Number.isFinite(n) ? n : s).padStart(3, '0');
  }

  function measureLine(im) {
    const m = (im && im.measure) || {};
    const bits = [];
    if (m.shot_size) bits.push(m.shot_size);
    if (m.facing) bits.push(m.facing);
    if (m.people != null && m.people !== '') bits.push(m.people + '人');
    return bits.join(' · ');
  }

  function covLine(im) {
    const c = im && im.cov;
    if (!c) return '';
    const cam = [c.camera, c.camera_name].filter(Boolean).join(' ');
    const act = c.action_name || '';
    return [cam, act].filter(Boolean).join(' · ');
  }

  function notesText(notes) {
    if (!notes) return '';
    if (typeof notes === 'string') return notes;
    if (Array.isArray(notes)) return notes.join('\n');
    const parts = [];
    for (const [k, v] of Object.entries(notes)) {
      const body = Array.isArray(v) ? v.join('\n') : (typeof v === 'string' ? v : JSON.stringify(v, null, 2));
      parts.push((k.startsWith('_') ? k.slice(1) : k) + '\n' + body);
    }
    return parts.join('\n\n');
  }

  function sourceLabel(src) {
    if (src === 'gemini') return t('gemini.source.gemini');
    if (src === 'user') return t('gemini.source.user');
    return t('gemini.source.default');
  }

  function emptySave() {
    return { dirty: false, saving: false, savedAt: 0, error: '' };
  }

  function hhmm(ts) {
    const d = new Date(ts);
    const h = String(d.getHours()).padStart(2, '0');
    const m = String(d.getMinutes()).padStart(2, '0');
    return h + ':' + m;
  }

  return {
    name: 'Panel_gemini',
    components: { TaskPick: window.TaskPick, ExpandUi: window.ExpandUi },
    setup() {
      const { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } = Vue;

      const open = reactive({ gemini: true, desc: true, camera: false, expand: false, refill: true, upscale: false, bridge: false, bridge_single: false, chain: false, tail: false, problem: false });
      // 规格 92：按语种改提示词（和第一次运行向导第 5 步是同一套接口）
      const lp = reactive({ open: false, code: '', langs: [], scope: 'core',
                            n: 0, n_all: 0, text: '', paste: '', busy: false,
                            applied: null, problems: [], has_backup: false, cur: '' });
      async function lpLoad() {
        try {
          const st = await LangPackLogic.state();
          lp.langs = st.langs || [];
          lp.n = st.n || 0; lp.n_all = st.n_all || 0;
          lp.has_backup = !!st.has_backup;
          lp.cur = st.lang_name || '';
          if (!lp.code) lp.code = st.lang || '';
        } catch (e) { /* 面板照常用 */ }
      }
      async function lpOpen() {
        lp.open = !lp.open;
        if (lp.open) await lpLoad();
      }
      async function lpCopy() {
        if (!lp.code) return;
        lp.busy = true;
        try {
          const r = await LangPackLogic.exportPack(lp.code, lp.scope);
          lp.text = r.text || ''; lp.n = r.n || 0;
          const ok = await LangPackLogic.copy(lp.text);
          toast(ok ? t('setup.lang.copied', { n: String(lp.n) }) : t('setup.lang.copy_fail'),
                '', ok ? 'ok' : 'bad');
        } finally { lp.busy = false; }
      }
      async function lpApply() {
        if (!lp.paste.trim()) return toast(t('setup.lang.need_paste'), '', 'bad');
        lp.busy = true;
        try {
          const r = await LangPackLogic.apply(lp.paste, lp.code);
          lp.applied = r; lp.problems = r.problems || [];
          toast(t('setup.lang.applied', { n: String(r.applied || 0) }), '',
                r.applied ? 'ok' : 'bad');
          await loadPrompts(true);
          await lpLoad();
        } finally { lp.busy = false; }
      }
      async function lpRestore() {
        lp.busy = true;
        try {
          const r = await LangPackLogic.restore();
          toast(t('prompts.lang.restored', { n: String(r.restored || 0) }), '', 'ok');
          lp.applied = null; lp.problems = [];
          await loadPrompts(true);
          await lpLoad();
        } finally { lp.busy = false; }
      }

      const apiKey = ref(store.settings['gemini.api_key'] || '');
      const model = ref(store.settings['gemini.model'] || DEFAULT_MODEL);
      const models = ref([]);
      const testOk = ref('');
      const testing = ref(false);

      const desc = ref('');
      const cameraText = ref('{}');
      const expand = ref('');
      const expandLegacy = ref(false);
      const coverage = reactive({
        version: 1, note: '', head: '', tail: '', cameras: [], actions: [],
      });
      const refillClause = ref('');
      const upscaleTpl = ref('');
      const bridgeTpl = ref('');
      const bridgeSingleTpl = ref('');
      const chainTpl = ref('');
      const tail = ref({});
      const tailNotes = ref('');
      const problem = ref({ patterns: [], template: '' });
      const newPhrase = ref('');
      const saveState = reactive({
        desc: emptySave(), camera: emptySave(), expand: emptySave(),
        refill_clause: emptySave(), upscale: emptySave(),
        bridge: emptySave(), bridge_single: emptySave(), chain: emptySave(), tail: emptySave(),
      });
      const saveTimers = {};
      const saveChain = {};
      const notices = ref([]);
      const bakView = ref(null);
      const qDismissed = ref('');

      const tid = ref('');
      const drafts = reactive({});
      const fieldSave = reactive({});
      const forceManual = ref(false);
      let descSaver = null;
      const focusP = ref('');
      const sel = reactive({ subjects: [], brolls: [] });
      const lastP = ref('');
      const lastGroup = ref('subjects');
      const menu = reactive({ show: false, x: 0, y: 0, im: null, group: 'subjects' });
      const menuRef = ref(null);
      let copyUndo = null;
      const manifest = ref(null);
      const loadingMan = ref(false);
      const runningKind = ref('');
      const previewOn = ref(false);
      const switches = ref(window.GeminiSwitchLogic.defaultSwitches());
      const minimalOn = ref(false);
      const batchDlg = ref(null);
      const previewSeg = ref('');
      const previewText = ref('');
      const previewLoading = ref(false);

      const allTasks = computed(() => store.tasks || []);
      const readyTasks = computed(() =>
        allTasks.value.filter(x => atLeastPrepared(x.status) && x.status !== 'trash'));
      const images = computed(() => (manifest.value && manifest.value.images) || []);
      const subjects = computed(() => images.value.filter(im => im.role !== '空镜'));
      const brolls = computed(() => images.value.filter(im => im.role === '空镜'));
      const segments = computed(() => (manifest.value && manifest.value.segments) || []);
      const selectedTask = computed(() => allTasks.value.find(x => x.id === tid.value) || null);
      const tidReady = computed(() => !!(selectedTask.value && atLeastPrepared(selectedTask.value.status)));
      const missingDesc = computed(() =>
        subjects.value.filter(im => !(String(im.desc || '').trim()) && im.status !== 'missing'));
      const manualN = computed(() =>
        images.value.filter(im => im && im.desc_manual).length);
      const expandAcc = computed(() => {
        const tsk = selectedTask.value;
        if (!tsk) return null;
        const aid = tsk.expand_account_id || tsk.account_id;
        const a = (store.accounts || []).find(x => x.id === aid);
        if (!a) return null;
        return { id: a.id, name: a.name, engine: a.engine };
      });
      const busy = computed(() => {
        const kinds = new Set((store.jobs || [])
          .filter(j => j.task_id === tid.value && (j.status === 'running' || j.status === 'queued'))
          .map(j => j.kind));
        return kinds;
      });
      const modelOptions = computed(() => {
        const set = new Set(models.value);
        const cur = model.value || DEFAULT_MODEL;
        if (cur && !set.has(cur)) set.add(cur);
        if (!set.has(DEFAULT_MODEL)) set.add(DEFAULT_MODEL);
        return [...set];
      });
      const q = computed(() => store.descQueue || { on: false, paused: false, done: 0, total: 0, results: [] });
      const qSummary = computed(() => {
        const s = q.value.summary || '';
        if (!s || q.value.on || qDismissed.value === s) return '';
        return s;
      });
      const qFails = computed(() =>
        (q.value.results || []).filter(r => !r.ok && r.error && r.error !== '跳过' && r.error !== '已停止'));

      const noFrontal = computed(() => store.settings['expand.no_frontal'] !== false);
      const legalN = computed(() => {
        const cams = (coverage.cameras || []).filter(c => !noFrontal.value || !c.frontal);
        const acts = coverage.actions || [];
        let n = 0;
        for (const c of cams) {
          for (const a of acts) {
            const d = Number(c.rank) - Number(a.rank);
            if (d >= 0 && d <= 1) n += 1;
          }
        }
        return n;
      });
      async function onNoFrontal(ev) {
        const v = !!(ev && ev.target && ev.target.checked);
        store.settings['expand.no_frontal'] = v;
        await saveSetting('expand.no_frontal', v);
      }

      function nextId(rows) {
        const used = new Set((rows || []).map(r => String(r.id || '')));
        let i = (rows || []).length + 1;
        let id = String(i).padStart(2, '0');
        while (used.has(id)) {
          i += 1;
          id = String(i).padStart(2, '0');
        }
        return id;
      }
      function addCam() {
        coverage.cameras.push({
          id: nextId(coverage.cameras), name: '', class: 'A',
          frontal: false, rank: 3, text: '',
        });
        onBlockInput('expand');
      }
      function addAct() {
        coverage.actions.push({
          id: nextId(coverage.actions), name: '', phase: 'hold',
          rank: 3, text: '',
        });
        onBlockInput('expand');
      }
      function delRow(arr, i) {
        arr.splice(i, 1);
        onBlockInput('expand');
      }
      function moveRow(arr, i, dir) {
        const j = i + dir;
        if (j < 0 || j >= arr.length) return;
        const t = arr[i];
        arr.splice(i, 1);
        arr.splice(j, 0, t);
        onBlockInput('expand');
      }

      // 机位/动作列表默认收起；展开状态记 localStorage，下次沿用
      const covOpen = reactive({
        cameras: window.CoverageEditorLogic.readOpen('cameras'),
        actions: window.CoverageEditorLogic.readOpen('actions'),
      });
      function persistCovOpen(kind) {
        window.CoverageEditorLogic.writeOpen(kind, !!covOpen[kind]);
      }
      function autosizeCovKind(kind) {
        const root = document.querySelector('.cov-list[data-kind="' + kind + '"]');
        if (!root) return;
        root.querySelectorAll('textarea.cov-text').forEach(el => window.CoverageEditorLogic.autosize(el));
      }
      function toggleCovOpen(kind) {
        covOpen[kind] = !covOpen[kind];
        persistCovOpen(kind);
        if (covOpen[kind]) nextTick(() => autosizeCovKind(kind));
      }
      function openCovList(kind) {
        covOpen[kind] = true;
        persistCovOpen(kind);
        nextTick(() => autosizeCovKind(kind));
      }
      function covCamSummary(row) { return window.CoverageEditorLogic.summaryCam(row, t); }
      function covActSummary(row) { return window.CoverageEditorLogic.summaryAct(row, t); }
      function covClassOpt(cls) { return window.CoverageEditorLogic.classOpt(cls, t); }
      function onCovTextInput(ev) {
        onBlockInput('expand');
        window.CoverageEditorLogic.autosize(ev && ev.target);
      }
      watch(() => open.expand, (v) => {
        if (!v) return;
        nextTick(() => {
          if (covOpen.cameras) autosizeCovKind('cameras');
          if (covOpen.actions) autosizeCovKind('actions');
        });
      });

      function applyCoverage(obj) {
        coverage.version = obj.version || 1;
        coverage.note = obj.note || '';
        coverage.head = obj.head || '';
        coverage.tail = obj.tail || '';
        coverage.cameras = Array.isArray(obj.cameras) ? obj.cameras.map(c => ({
          id: String(c.id || ''), name: String(c.name || ''),
          class: String(c.class || 'A'),
          frontal: !!(c.frontal || c.front),
          rank: Number(c.rank) || 2, text: String(c.text || ''),
        })) : [];
        coverage.actions = Array.isArray(obj.actions) ? obj.actions.map(a => ({
          id: String(a.id || ''), name: String(a.name || ''),
          phase: String(a.phase || 'hold'),
          rank: Number(a.rank) || 2, text: String(a.text || ''),
        })) : [];
      }

      function snapOf(name) {
        if (name === 'desc') return desc.value;
        if (name === 'expand') {
          if (expandLegacy.value) return expand.value;
          return JSON.stringify({
            version: coverage.version || 1,
            note: coverage.note || '',
            head: coverage.head || '',
            tail: coverage.tail || '',
            cameras: coverage.cameras || [],
            actions: coverage.actions || [],
          });
        }
        if (name === 'refill_clause') return refillClause.value;
        if (name === 'upscale') return upscaleTpl.value;
        if (name === 'bridge') return bridgeTpl.value;
        if (name === 'bridge_single') return bridgeSingleTpl.value;
        if (name === 'chain') return chainTpl.value;
        if (name === 'camera') return cameraText.value;
        if (name === 'tail') return JSON.stringify(tail.value);
        return '';
      }

      function parseBlock(name, snap) {
        if (name === 'camera') {
          const obj = JSON.parse(snap);
          if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
            throw new Error(t('gemini.camera_bad'));
          }
          return obj;
        }
        if (name === 'expand' && !expandLegacy.value) {
          const obj = JSON.parse(snap);
          if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
            throw new Error(t('gemini.camera_bad'));
          }
          return obj;
        }
        if (name === 'tail') return JSON.parse(snap);
        return snap;
      }

      function applyFromServer(r, force) {
        if (r && r.switches) {
          switches.value = Object.assign(window.GeminiSwitchLogic.defaultSwitches(), r.switches);
          minimalOn.value = window.GeminiSwitchLogic.isMinimal(switches.value);
        }

        if (!r) return;
        const take = (name) => force || !(saveState[name] && saveState[name].dirty);
        if (r.desc != null && take('desc')) desc.value = r.desc;
        if (r.camera && take('camera')) cameraText.value = JSON.stringify(r.camera, null, 2);
        if (r.expand != null && take('expand')) {
          if (typeof r.expand === 'string') {
            expandLegacy.value = true;
            expand.value = r.expand;
          } else if (r.expand && typeof r.expand === 'object') {
            expandLegacy.value = false;
            expand.value = '';
            applyCoverage(r.expand);
          }
        }
        if (r.refill_clause != null && take('refill_clause')) refillClause.value = r.refill_clause;
        if (r.upscale != null && take('upscale')) upscaleTpl.value = r.upscale;
        if (r.bridge != null && take('bridge')) bridgeTpl.value = r.bridge;
        if (r.bridge_single != null && take('bridge_single')) bridgeSingleTpl.value = r.bridge_single;
        if (r.chain != null && take('chain')) chainTpl.value = r.chain;
        if (r.tail && take('tail')) tail.value = r.tail;
        if (r.tail_notes) tailNotes.value = notesText(r.tail_notes);
        if (r.problem) {
          problem.value = r.problem;
          if (!Array.isArray(problem.value.patterns)) problem.value.patterns = [];
        }
        if (Array.isArray(r.migrate_notice)) notices.value = r.migrate_notice;
      }

      async function loadPrompts(force) {
        const r = await API.get('/api/prompts');
        applyFromServer(r, force === true);
      }

      async function _doSave(name, opts) {
        const st = saveState[name];
        if (!st) return false;
        clearTimeout(saveTimers[name]);
        const snap = snapOf(name);
        let value;
        try {
          value = parseBlock(name, snap);
        } catch (e) {
          st.error = t('gemini.camera_bad');
          st.dirty = true;
          return false;
        }
        st.saving = true;
        st.error = '';
        try {
          await API.post('/api/prompts', { name, value });
          if (snapOf(name) === snap) {
            st.dirty = false;
            st.savedAt = Date.now();
            st.error = '';
          } else {
            scheduleSave(name);
          }
          if (opts && opts.toastOk) toast(t('gemini.saved'), '', 'ok');
          return true;
        } catch (e) {
          // 失败保住 textarea，绝不回滚成服务端旧值
          st.error = (e && e.message) || String(e);
          st.dirty = true;
          return false;
        } finally {
          st.saving = false;
        }
      }

      function doSave(name, opts) {
        const prev = saveChain[name] || Promise.resolve();
        const p = prev.catch(() => {}).then(() => _doSave(name, opts));
        saveChain[name] = p;
        return p;
      }

      function scheduleSave(name) {
        clearTimeout(saveTimers[name]);
        saveTimers[name] = setTimeout(() => { doSave(name); }, SAVE_MS);
      }

      function onBlockInput(name) {
        const st = saveState[name];
        if (!st) return;
        st.dirty = true;
        st.error = '';
        scheduleSave(name);
      }

      function onBlockBlur(name) {
        const st = saveState[name];
        if (!st) return;
        clearTimeout(saveTimers[name]);
        if (st.dirty || st.error) doSave(name);
      }

      async function flush(keepalive) {
        const names = BLOCKS.filter(n => saveState[n] && saveState[n].dirty);
        if (!names.length) return;
        if (keepalive) {
          for (const name of names) {
            try {
              const value = parseBlock(name, snapOf(name));
              const body = JSON.stringify({ name, value });
              const blob = new Blob([body], { type: 'application/json' });
              if (navigator.sendBeacon) navigator.sendBeacon('/api/prompts', blob);
              else fetch('/api/prompts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true });
              saveState[name].dirty = false;
            } catch (e) { /* 运镜 JSON 不合法就先不存，保住原文 */ }
          }
          return;
        }
        await Promise.all(names.map(n => doSave(n)));
      }

      function saveLabel(name) {
        const st = saveState[name];
        if (!st) return '';
        if (st.saving) return t('gemini.save.saving');
        if (st.error) return t('gemini.save.fail');
        if (st.dirty) return t('gemini.save.dirty');
        if (st.savedAt) return t('gemini.save.ok', { time: hhmm(st.savedAt) });
        return '';
      }
      function saveClass(name) {
        const st = saveState[name];
        if (!st) return '';
        if (st.saving) return 'is-saving';
        if (st.error) return 'is-fail';
        if (st.dirty) return 'is-dirty';
        if (st.savedAt) return 'is-ok';
        return '';
      }

      async function toggleOpen(name) {
        await flush();
        open[name] = !open[name];
      }

      async function loadManifest() {
        if (!tid.value || !tidReady.value) { manifest.value = null; return; }
        loadingMan.value = true;
        try {
          const r = await API.get('/api/tasks/' + encodeURIComponent(tid.value) + '/manifest');
          // 合并式：仓库值整体换掉，但 drafts 里没存上的字段 draftOf 仍走草稿，
          // 所以后台刷新冲不掉任何一个框，不只是当前有焦点那个。
          manifest.value = r.manifest;
          const log = r.sync_log || [];
          if (log.length) toast(t('sync.disk', { detail: log.join('；') }), '', 'ok');
        } finally {
          loadingMan.value = false;
        }
      }

      // ---- 描述语手改保护（0920 + 78）----
      // 0920：正在输入时不重载 manifest，挂起来等失焦再补。
      // 78：每张卡 desc/voice 本地草稿 + localStorage，loadManifest 合并式保留草稿。
      let _reloadPending = false;
      const D = window.GeminiDescDrafts;

      function isEditingDesc() {
        const el = document.activeElement;
        return !!(el && el.classList
          && (el.classList.contains('desc-edit') || el.classList.contains('voice-edit')));
      }

      async function loadManifestSafe() {
        if (isEditingDesc()) { _reloadPending = true; return; }
        await loadManifest();
      }

      async function flushReload() {
        if (!_reloadPending) return;
        _reloadPending = false;
        await loadManifest();
      }

      function persistDescDrafts(id) {
        D.persist(id || tid.value, drafts);
      }

      function loadDescDrafts(id) {
        D.clearObj(drafts);
        D.clearObj(fieldSave);
        const got = D.restore(id || tid.value);
        Object.keys(got || {}).forEach(k => { drafts[k] = got[k]; });
      }

      descSaver = D.makeSaver({
        drafts, fieldSave,
        persist: () => persistDescDrafts(),
        post: async (im, field, value) => {
          await patchImage(im, { [field]: value, by: 'user' });
        },
      });

      function draftOf(im, field) {
        return D.draftOf(drafts, im, field);
      }
      function fieldSaveClass(im, field) {
        return D.saveClass(fieldSave[D.draftKey(im && im.p, field)]);
      }
      function fieldSaveLabel(im, field) {
        return D.saveLabel(fieldSave[D.draftKey(im && im.p, field)], t);
      }
      function fieldSaveTitle(im, field) {
        const st = fieldSave[D.draftKey(im && im.p, field)];
        return (st && st.error) || '';
      }

      function onDescInput(im, ev) { descSaver.onInput(im, 'desc', ev.target.value); }
      function onVoiceInput(im, ev) { descSaver.onInput(im, 'voice', ev.target.value); }

      async function saveSetting(key, value) {
        const r = await API.post('/api/settings', { set: { [key]: value } });
        Object.assign(store.settings, r.settings || { [key]: value });
      }

      async function onKeyBlur() {
        if ((store.settings['gemini.api_key'] || '') === apiKey.value) return;
        await saveSetting('gemini.api_key', apiKey.value);
      }

      async function onModelChange() {
        await saveSetting('gemini.model', model.value);
      }

      async function testKey() {
        testing.value = true;
        testOk.value = '';
        try {
          if ((store.settings['gemini.api_key'] || '') !== apiKey.value) {
            await saveSetting('gemini.api_key', apiKey.value);
          }
          const r = await API.post('/api/gemini/test', { api_key: apiKey.value, model: model.value });
          const list = (r.models || []).filter(n => /gemini/i.test(n));
          models.value = list.length ? list : (r.models || []);
          if (r.model) model.value = r.model;
          if (model.value && !models.value.includes(model.value)) models.value.unshift(model.value);
          testOk.value = t('gemini.test_ok', { n: String(models.value.length) });
        } catch (e) {
          testOk.value = '';
        } finally {
          testing.value = false;
        }
      }

      async function resetBlock(name) {
        const r = await API.post('/api/prompts/reset', { name });
        applyFromServer(r, false);
        if (name === 'desc') desc.value = r.desc || '';
        if (name === 'camera') cameraText.value = JSON.stringify(r.camera || {}, null, 2);
        if (name === 'expand') {
          if (typeof r.expand === 'string') {
            expandLegacy.value = true;
            expand.value = r.expand || '';
          } else {
            expandLegacy.value = false;
            expand.value = '';
            applyCoverage(r.expand || {});
          }
        }
        if (name === 'refill_clause') refillClause.value = r.refill_clause || '';
        if (name === 'upscale') upscaleTpl.value = r.upscale || '';
        if (name === 'bridge') bridgeTpl.value = r.bridge || '';
        if (name === 'bridge_single') bridgeSingleTpl.value = r.bridge_single || '';
        if (name === 'chain') chainTpl.value = r.chain || '';
        if (name === 'tail') tail.value = r.tail || {};
        saveState[name].dirty = false;
        saveState[name].error = '';
        saveState[name].savedAt = Date.now();
        toast(t('gemini.reset_ok'), '', 'ok');
      }

      async function saveDesc() { await doSave('desc', { toastOk: true }); }
      async function saveExpand() { await doSave('expand', { toastOk: true }); }
      async function saveRefillClause() { await doSave('refill_clause', { toastOk: true }); }
      async function saveUpscale() { await doSave('upscale', { toastOk: true }); }
      async function saveBridge() { await doSave('bridge', { toastOk: true }); }
      async function saveBridgeSingle() { await doSave('bridge_single', { toastOk: true }); }
      async function saveChainBlock() { await doSave('chain', { toastOk: true }); }
      async function saveCamera() { await doSave('camera', { toastOk: true }); }
      async function saveTail() { await doSave('tail', { toastOk: true }); }

      function openPromptBlock(name) {
        if (name !== 'expand' && name !== 'refill') return;
        open[name] = true;
        Vue.nextTick(() => {
          const el = document.getElementById('g-block-' + name);
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
      }

      function tailStringKeys() {
        const out = [];
        for (const [k, v] of Object.entries(tail.value || {})) {
          if (typeof v === 'string') out.push(k);
        }
        return out;
      }
      function tailDictKeys() {
        const out = [];
        for (const [k, v] of Object.entries(tail.value || {})) {
          if (k === '情绪预设') continue;  // 嵌套预设，上面单独做切换，不当普通字典渲
          if (v && typeof v === 'object' && !Array.isArray(v)) out.push(k);
        }
        return out;
      }
      function emotionPresetName() {
        const p = (tail.value || {}).情绪预设 || {};
        return p.当前 || '温暖';
      }
      async function setEmotionPreset(name) {
        if (emotionPresetName() === name) return;
        await doSave('tail');
        try {
          const r = await API.post('/api/prompts/emotion-preset', { name });
          if (r.tail) tail.value = r.tail;
          if (saveState.tail) {
            saveState.tail.dirty = false;
            saveState.tail.savedAt = Date.now();
            saveState.tail.error = '';
          }
          toast(t('gemini.emotion_switched', { name }), '', 'ok');
        } catch (e) {
          toast((e && e.message) || String(e), '', 'bad');
        }
      }
      function dictInnerKeys(k) {
        const v = (tail.value || {})[k] || {};
        return Object.keys(v);
      }

      async function addPhrase() {
        const v = newPhrase.value.trim();
        if (!v) return;
        const block = {
          patterns: [...(problem.value.patterns || [])],
          template: problem.value.template || '包括{原话}这句话也要读',
        };
        block.patterns.push({ kind: 'phrase', value: v, lang: '*', note: '', source: 'user' });
        try {
          const r = await API.post('/api/prompts', { name: 'problem', value: block });
          if (r.problem) {
            problem.value = r.problem;
            if (!Array.isArray(problem.value.patterns)) problem.value.patterns = [];
          }
          newPhrase.value = '';
        } catch (e) { /* API 已 toast */ }
      }

      async function delPhrase(i) {
        const block = {
          patterns: (problem.value.patterns || []).filter((_, idx) => idx !== i),
          template: problem.value.template || '包括{原话}这句话也要读',
        };
        try {
          const r = await API.post('/api/prompts', { name: 'problem', value: block });
          if (r.problem) {
            problem.value = r.problem;
            if (!Array.isArray(problem.value.patterns)) problem.value.patterns = [];
          }
        } catch (e) { /* API 已 toast */ }
      }

      function bannerText(n) {
        if (!n) return '';
        if (n.kind === 'refill_merged') return t('gemini.migrate.merged');
        return t('gemini.migrate.reset', { label: n.label || n.name || '' });
      }
      function viewBak(n) {
        bakView.value = { title: t('gemini.migrate.bak_title'), text: (n && n.bak_text) || '' };
      }
      async function restoreBak(n) {
        if (!n) return;
        const r = await API.post('/api/prompts/migrate/restore', { name: n.name || 'expand' });
        applyFromServer(r, true);
        saveState.expand.dirty = false;
        saveState.expand.savedAt = Date.now();
        toast(t('gemini.migrate.restored'), '', 'ok');
      }
      async function ackNotice(n) {
        const r = await API.post('/api/prompts/migrate/ack', {
          kind: (n && n.kind) || '', name: (n && n.name) || '',
        });
        applyFromServer(r, false);
        if (!n) notices.value = [];
        else notices.value = (notices.value || []).filter(x =>
          !(x.kind === n.kind && x.name === n.name && x.bak_key === n.bak_key));
        bakView.value = null;
      }

      async function runJob(kind) {
        if (!tid.value) {
          toast(t('gemini.need_task'), '', 'bad');
          return;
        }
        if (kind === 'describe') {
          if (forceManual.value && manualN.value) {
            if (!confirm(t('gemini.force_confirm', { n: String(manualN.value) }))) return;
          }
          if ((apiKey.value || '') !== (store.settings['gemini.api_key'] || '')) {
            try { await saveSetting('gemini.api_key', apiKey.value); }
            catch (e) { return; }
          }
          if (!(apiKey.value || '').trim()) {
            try { await API.post('/api/gemini/test', { api_key: '' }); }
            catch (e) { return; }
          }
        }
        runningKind.value = kind;
        try {
          const extra = {};
          if (kind === 'describe' && forceManual.value) extra.force = true;
          await API.job(kind, tid.value, extra);
        } catch (e) {
          runningKind.value = '';
        }
      }

      async function scanProblems() {
        if (!tid.value) {
          toast(t('gemini.scan_need'), '', 'bad');
          return;
        }
        await runJob('scan_problems');
      }

      async function patchImage(im, fields) {
        if (!tid.value || !im || !im.p) return;
        const r = await API.post(
          '/api/tasks/' + encodeURIComponent(tid.value) + '/image/' + encodeURIComponent(im.p),
          fields);
        if (r.image && manifest.value && manifest.value.images) {
          const i = manifest.value.images.findIndex(x => x.p === im.p);
          if (i >= 0) manifest.value.images.splice(i, 1, r.image);
          else Object.assign(im, r.image);
        } else if (r.image) {
          Object.assign(im, r.image);
        }
        if (fields && (fields.desc != null || fields.voice != null) && window.applyTaskCounts) {
          fetch('/api/tasks/' + encodeURIComponent(tid.value) + '/counts')
            .then(res => res.json())
            .then(d => { if (d && d.ok && d.counts) applyTaskCounts(tid.value, d.counts); })
            .catch(() => {});
        }
      }

      const Lcard = window.GeminiCardLogic;

      function groupPs(group) {
        const list = group === 'brolls' ? brolls.value : subjects.value;
        return (list || []).map(im => im.p);
      }
      function allSelected() {
        return (sel.subjects || []).concat(sel.brolls || []);
      }
      function selN(group) {
        return (sel[group] || []).length;
      }
      function isChecked(im, group) {
        return (sel[group] || []).indexOf(im && im.p) >= 0;
      }
      function applySelState(next) {
        sel.subjects = next.sel.subjects;
        sel.brolls = next.sel.brolls;
        lastP.value = next.lastP;
        lastGroup.value = next.lastGroup;
        focusP.value = next.focusP;
      }
      function snapshotSel() {
        return {
          sel: { subjects: sel.subjects.slice(), brolls: sel.brolls.slice() },
          lastP: lastP.value,
          lastGroup: lastGroup.value,
          focusP: focusP.value,
        };
      }
      function onCheckClick(ev, im, group) {
        ev.preventDefault();
        ev.stopPropagation();
        applySelState(Lcard.reduce(snapshotSel(), {
          type: 'check', p: im.p, group, groupPs: groupPs(group),
        }));
      }
      function onCardClick(ev, im, group) {
        if (Lcard.isFormChrome(ev.target)) {
          focusP.value = im.p;
          lastP.value = im.p;
          lastGroup.value = group;
          return;
        }
        applySelState(Lcard.reduce(snapshotSel(), {
          type: 'click', p: im.p, group, groupPs: groupPs(group),
          ctrl: !!(ev.ctrlKey || ev.metaKey), shift: !!ev.shiftKey,
        }));
        if (ev.shiftKey || ev.ctrlKey || ev.metaKey) ev.preventDefault();
      }
      function selAll(group) {
        applySelState(Lcard.reduce(snapshotSel(), {
          type: 'all', group, groupPs: groupPs(group),
        }));
      }
      function selInvert(group) {
        applySelState(Lcard.reduce(snapshotSel(), {
          type: 'invert', group, groupPs: groupPs(group),
        }));
      }
      function selNone(group) {
        applySelState(Lcard.reduce(snapshotSel(), { type: 'none', group }));
      }
      function clearAllSel() {
        applySelState(Lcard.reduce(snapshotSel(), { type: 'esc' }));
      }
      function findIm(p) {
        return (images.value || []).find(x => x.p === p) || null;
      }
      function applyFieldsLocal(toPs, fields, src, olds) {
        const byP = {};
        (images.value || []).forEach(im => { byP[im.p] = im; });
        (toPs || []).forEach(p => {
          const im = byP[p];
          if (!im) return;
          if (olds) {
            const rec = olds.find(x => x.p === p);
            if (!rec) return;
            if (fields.indexOf('desc') >= 0) {
              im.desc = rec.desc_old || '';
              im.desc_manual = !!rec.desc_manual_old;
              D.clearDraft(drafts, p, 'desc');
            }
            if (fields.indexOf('voice') >= 0) {
              im.voice = rec.voice_old || '';
              im.voice_manual = !!rec.voice_manual_old;
              D.clearDraft(drafts, p, 'voice');
            }
            return;
          }
          if (fields.indexOf('desc') >= 0) {
            im.desc = (src && src.desc) || '';
            im.desc_manual = true;
            im.desc_stale = false;
            D.clearDraft(drafts, p, 'desc');
          }
          if (fields.indexOf('voice') >= 0) {
            im.voice = (src && src.voice) || '';
            im.voice_manual = true;
            D.clearDraft(drafts, p, 'voice');
          }
        });
        persistDescDrafts();
      }
      function closeImgMenu() {
        menu.show = false;
        menu.im = null;
      }
      function openImgMenu(ev, im, group) {
        menu.im = im;
        menu.group = group;
        lastGroup.value = group;
        lastP.value = im.p;
        focusP.value = im.p;
        window.openCtxMenu(ev, menu, menuRef, nextTick, 240, 460);
      }
      function onDocClick(ev) {
        if (menu.show && menuRef.value && !menuRef.value.contains(ev.target)) closeImgMenu();
      }
      function menuFlagsNow() {
        return Lcard.menuFlags(menu.im, allSelected(), groupPs(menu.group || 'subjects').length);
      }
      function menuOff(key) {
        const f = menuFlagsNow();
        if (key === 'open' || key === 'reveal') return f.open;
        if (key === 'syncDesc') return f.syncDesc;
        if (key === 'syncVoice') return f.syncVoice;
        if (key === 'syncBoth') return f.syncBoth;
        if (key === 'clear') return f.clear;
        if (key === 'restoreAuto') return f.restoreAuto;
        return false;
      }
      function menuTitle(key) {
        return Lcard.menuTitle(key, menuFlagsNow(), t);
      }
      function menuSyncN() {
        return Lcard.copyTargets(allSelected(), menu.im && menu.im.p).length;
      }
      async function copyDescTo(fromIm, fields) {
        if (!tid.value || !fromIm) return;
        const body = Lcard.syncRequest(fromIm.p, allSelected(), fields);
        if (!body.to_ps.length) {
          toast(t('gemini.copy.none'), '', 'bad');
          return;
        }
        try {
          const r = await API.post(
            '/api/tasks/' + encodeURIComponent(tid.value) + '/images/copy-desc',
            body);
          applyFieldsLocal(body.to_ps, body.fields, fromIm, null);
          const n = r.n || (r.changed || []).length;
          const msg = t(Lcard.copyToastKey(body.fields), {
            n: String(n),
            ps: Lcard.listPs((r.changed || []).map(x => x.p)),
          });
          if (copyUndo && copyUndo.expire) copyUndo.expire();
          const changed = r.changed || [];
          copyUndo = Lcard.makeUndo(
            (fn, ms) => setTimeout(fn, ms),
            (id) => clearTimeout(id),
            () => { copyUndo = null; },
            Lcard.UNDO_MS,
          );
          toast(msg, '', 'ok', [{
            label: t('gemini.copy.undo'),
            run: () => undoCopy(changed, body.fields),
          }], Lcard.UNDO_MS);
        } catch (e) { /* API 已经 toast */ }
      }
      async function undoCopy(changed, fields) {
        if (!copyUndo || !copyUndo.run(() => {})) return;
        copyUndo = null;
        if (!tid.value || !changed || !changed.length) return;
        try {
          await API.post(
            '/api/tasks/' + encodeURIComponent(tid.value) + '/images/copy-desc',
            Lcard.restoreBody(changed));
          applyFieldsLocal(changed.map(x => x.p), fields || ['desc', 'voice'], null, changed);
          toast(t('gemini.copy.undone', { n: String(changed.length) }), '', 'ok');
        } catch (e) { /* API 已经 toast */ }
      }
      async function syncFromFocus(fields) {
        const im = findIm(focusP.value);
        if (!im) {
          toast(t('gemini.copy.none'), '', 'bad');
          return;
        }
        await copyDescTo(im, fields || ['desc']);
      }
      async function onImgMenu(key) {
        const im = menu.im;
        if (!im || menuOff(key)) return;
        closeImgMenu();
        if (!tid.value) return;
        try {
          if (key === 'open') {
            await API.post('/api/tasks/' + encodeURIComponent(tid.value)
              + '/image/' + encodeURIComponent(im.p) + '/open');
            return;
          }
          if (key === 'reveal') {
            await API.post('/api/tasks/' + encodeURIComponent(tid.value)
              + '/image/' + encodeURIComponent(im.p) + '/reveal');
            return;
          }
          if (key === 'toggle') {
            onCheckClick({ preventDefault() {}, stopPropagation() {} }, im, menu.group);
            return;
          }
          if (key === 'all') { selAll(menu.group); return; }
          if (key === 'none') { selNone(menu.group); return; }
          if (key === 'syncDesc') { await copyDescTo(im, ['desc']); return; }
          if (key === 'syncVoice') { await copyDescTo(im, ['voice']); return; }
          if (key === 'syncBoth') { await copyDescTo(im, ['desc', 'voice']); return; }
          if (key === 'copyDesc') {
            const text = draftOf(im, 'desc') || im.desc || '';
            if (navigator.clipboard && navigator.clipboard.writeText)
              await navigator.clipboard.writeText(text);
            else window.copyText && copyText(text);
            toast(t('gemini.copied_desc'), '', 'ok');
            return;
          }
          if (key === 'restoreAuto') {
            await patchImage(im, { desc_manual: false, voice_manual: false });
            return;
          }
          if (key === 'clear') {
            await patchImage(im, { desc: '', by: 'user' });
            toast(t('gemini.cleared', { p: im.p }), '', 'ok');
          }
        } catch (e) { /* API 已经 toast */ }
      }

      async function onDescBlur(im, ev) {
        await descSaver.onBlur(im, 'desc', ev.target.value);
        await flushReload();
      }
      async function onVoiceBlur(im, ev) {
        await descSaver.onBlur(im, 'voice', ev.target.value);
        await flushReload();
      }
      async function rewriteDesc(im) {
        if (!tid.value || !im || !im.p) return;
        D.clearDraft(drafts, im.p, 'desc');
        persistDescDrafts();
        runningKind.value = 'describe';
        try {
          await API.job('describe', tid.value, { force: true, ps: [im.p] });
        } catch (e) {
          runningKind.value = '';
        }
      }
      async function onRoleChange(im, ev) {
        const v = ev.target.value;
        const prev = im.role;
        if (v === prev) return;
        try { await patchImage(im, { role: v }); }
        catch (e) { ev.target.value = prev; }
      }

      async function openPreview() {
        if (!tid.value) {
          toast(t('gemini.need_task'), '', 'bad');
          return;
        }
        if (!manifest.value) await loadManifest();
        const segs = segments.value;
        if (!segs.length) {
          toast(t('gemini.preview.empty'), '', 'bad');
          return;
        }
        previewOn.value = true;
        if (!previewSeg.value) previewSeg.value = String(segs[0].s);
        await loadPreview();
      }

      async function loadPreview() {
        if (!tid.value || !previewSeg.value) return;
        previewLoading.value = true;
        try {
          const r = await API.get(
            '/api/tasks/' + encodeURIComponent(tid.value)
            + '/prompt/' + encodeURIComponent(previewSeg.value));
          previewText.value = r.prompt || '';
        } catch (e) {
          previewText.value = '';
        } finally {
          previewLoading.value = false;
        }
      }

      async function copyPreview() {
        try {
          await navigator.clipboard.writeText(previewText.value || '');
          toast(t('gemini.copied'), '', 'ok');
        } catch (e) {
          toast(String(e.message || e), '', 'bad');
        }
      }

      async function toggleQueue() {
        if (q.value.on) {
          await API.post('/api/queue/describe/stop');
          return;
        }
        qDismissed.value = '';
        await API.post('/api/queue/describe/start', {});
      }
      async function qPause(on) {
        await API.post('/api/queue/describe/pause', { on: !!on });
      }
      async function qSkip() { await API.post('/api/queue/describe/skip'); }
      async function qStop() { await API.post('/api/queue/describe/stop'); }
      function goTask(id) {
        if (!id) return;
        tid.value = id;
        ui.pendingTid = id;
      }
      function dismissSummary() {
        qDismissed.value = q.value.summary || 'x';
      }

      function focusedBlock() {
        const el = document.activeElement;
        const card = el && el.closest && el.closest('[data-block]');
        return card ? card.getAttribute('data-block') : '';
      }
      function onKey(ev) {
        if (menu.show && ev.key === 'Escape') {
          closeImgMenu();
          ev.preventDefault();
          return;
        }
        const typing = Lcard.isTypingTarget(ev.target);
        const panelOn = window.ui && ui.node === 'gemini';
        if (ev.key === 'Escape' && !typing && panelOn) {
          clearAllSel();
          return;
        }
        if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'a' || ev.key === 'A') && !typing && panelOn) {
          ev.preventDefault();
          selAll(lastGroup.value || 'subjects');
          return;
        }
        if (!(ev.ctrlKey || ev.metaKey) || (ev.key !== 's' && ev.key !== 'S')) return;
        ev.preventDefault();
        const name = focusedBlock() || (open.desc ? 'desc' : open.expand ? 'expand'
          : open.camera ? 'camera' : open.tail ? 'tail' : open.refill ? 'refill_clause'
          : open.upscale ? 'upscale' : '');
        if (name) doSave(name, { toastOk: true });
      }
      function onBeforeUnload() {
        persistDescDrafts();
        flush(true);
      }

      watch(() => ui.promptBlock, async (v) => {
        if (v === 'expand' || v === 'refill') {
          await flush();
          openPromptBlock(v);
          ui.promptBlock = '';
        }
      });
      watch(tid, async (id, prev) => {
        if (prev) persistDescDrafts(prev);
        loadDescDrafts(id);
        ui.panelTid = tid.value || '';
        await flush();
        previewSeg.value = '';
        previewText.value = '';
        clearAllSel();
        closeImgMenu();
        loadManifest();
      });
      watch(() => store.refreshSeq, async () => {
        await loadPrompts();
        await loadManifestSafe();
      });
      watch(() => ui.pendingTid, (v) => { if (v) takePendingTid(tid, 'gemini'); });
      watch(() => ui.panelTid, (v) => { if (v && v !== tid.value) tid.value = v; });
      watch(() => ui.pendingP, (v) => {
        if (!v) return;
        focusP.value = v;
        ui.pendingP = '';
        Vue.nextTick(() => {
          const el = document.querySelector('.panel-gemini .img-card[data-p="' + v + '"]');
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
      });
      watch(previewSeg, () => { if (previewOn.value) loadPreview(); });
      watch(() => store.settings['gemini.api_key'], v => {
        if (v != null && v !== apiKey.value && document.activeElement
            && document.activeElement.getAttribute
            && document.activeElement.getAttribute('data-gkey') !== '1') {
          apiKey.value = v || '';
        }
      });
      watch(() => (store.jobs || []).map(j => j.id + ':' + j.status).join('|'), async () => {
        const mine = (store.jobs || []).filter(j =>
          j.task_id === tid.value
          && (j.kind === 'describe' || j.kind === 'scan_problems' || j.kind === 'expand'));
        const live = mine.some(j => j.status === 'running' || j.status === 'queued');
        if (!live && runningKind.value) {
          const kind = runningKind.value;
          runningKind.value = '';
          if (kind === 'describe' || kind === 'expand') await loadManifestSafe();
          if (kind === 'scan_problems') await loadPrompts();
        }
      });
      watch(() => (q.value.current && q.value.current.id) || '', (id) => {
        if (id && q.value.on && id === tid.value) loadManifestSafe();
      });

      onMounted(async () => {
        apiKey.value = store.settings['gemini.api_key'] || '';
        model.value = store.settings['gemini.model'] || DEFAULT_MODEL;
        if (covOpen.cameras) nextTick(() => autosizeCovKind('cameras'));
        if (covOpen.actions) nextTick(() => autosizeCovKind('actions'));
        await loadPrompts(true);
        if (!adoptPanelTid(tid, 'gemini') && !tid.value && readyTasks.value.length) {
          tid.value = readyTasks.value[0].id;
        }
        if (ui.promptBlock === 'expand' || ui.promptBlock === 'refill') {
          openPromptBlock(ui.promptBlock);
          ui.promptBlock = '';
        }
        window.addEventListener('keydown', onKey);
        document.addEventListener('click', onDocClick);
        window.addEventListener('beforeunload', onBeforeUnload);
        window.addEventListener('pagehide', onBeforeUnload);
        ui.panelTid = tid.value || '';
        if (tid.value) loadDescDrafts(tid.value);
        if (window.registerFlush) window.registerFlush('gemini', () => {
          persistDescDrafts();
          return flush();
        });
      });
      function openRescan() {
        if (window.Rescan) window.Rescan.open(tid.value);
      }
      function openAdopt(im) {
        if (window.Rescan) window.Rescan.openPick(tid.value, im && im.p, images.value);
      }
      function dropMissing(im) {
        if (window.Rescan) window.Rescan.dropMissing(tid.value, im && im.p);
      }

      onBeforeUnmount(() => {
        window.removeEventListener('keydown', onKey);
        document.removeEventListener('click', onDocClick);
        window.removeEventListener('beforeunload', onBeforeUnload);
        window.removeEventListener('pagehide', onBeforeUnload);
        if (window.unregisterFlush) window.unregisterFlush('gemini');
        persistDescDrafts();
        flush();
      });


      // --- spec 85b: switch + batch helpers (wired for template; no window. in template) ---
      const Lsw = window.GeminiSwitchLogic;
      if (!switches || !switches.value) {
        /* switches/minimalOn/batchDlg may already be declared above; only fill if missing */
      }
      
      const brickKeys = Vue.computed(() => (Lsw.BRICKS || []).slice());
      const switchGroups = Vue.computed(() => {
        const G = Lsw.GROUPS || {};
        const labs = {
          lens: t('gemini.switches.group.lens'),
          pose: t('gemini.switches.group.pose'),
          scene: t('gemini.switches.group.scene'),
          quality: t('gemini.switches.group.quality'),
          perf: t('gemini.switches.group.perf'),
          ending: t('gemini.switches.group.ending'),
          meta: t('gemini.switches.group.meta'),
        };
        return Object.keys(G).filter(id => id !== 'meta').map(id => ({ id, label: labs[id] || id, keys: G[id] }));
      });

      function brickLabel(k) {
        const map = {
          '运镜卡': 'gemini.switches.brick.camera',
          '运动强制': 'gemini.switches.brick.motion',
          '镜头禁令': 'gemini.switches.brick.lens_ban',
          '人物原地': 'gemini.switches.brick.still',
          '人物站姿': 'gemini.switches.brick.stand',
          '人物坐姿': 'gemini.switches.brick.sit',
          '比例锁': 'gemini.switches.brick.ratio',
          '车辆静止锁': 'gemini.switches.brick.vehicle',
          '人群保留': 'gemini.switches.brick.crowd',
          '群像锁': 'gemini.switches.brick.group',
          '画质锁': 'gemini.switches.brick.quality',
          '情绪': 'gemini.switches.brick.emotion',
          '音色锁': 'gemini.switches.brick.voice',
          '说话指令': 'gemini.switches.brick.speech',
          '描述语整理': 'gemini.switches.desc_tidy',
          '静音提示': 'gemini.switches.mute',
        };
        const key = map[k];
        return key ? t(key) : k;
      }
      function groupOn(gid) {
        return Lsw.groupState(switches.value, gid) === 'on';
      }
      function groupStateOf(gid) {
        return Lsw.groupState(switches.value, gid);
      }
      function toggleBrick(k) {
        const next = { ...switches.value, [k]: !switches.value[k] };
        switches.value = next;
        minimalOn.value = Lsw.isMinimal(next);
        persistSwitches();
        bumpPreviewHi(k);
      }
      function toggleSwitchGroup(gid) {
        const st = Lsw.groupState(switches.value, gid);
        const turnOn = st !== 'on';
        switches.value = Lsw.toggleGroup(switches.value, gid, turnOn);
        minimalOn.value = Lsw.isMinimal(switches.value);
        persistSwitches();
        bumpPreviewHi(gid);
      }
      function toggleMinimal() {
        if (!minimalOn.value) {
          window.__swBeforeMinimal = { ...switches.value };
          switches.value = Lsw.applyMinimal(switches.value, true);
          minimalOn.value = true;
        } else {
          switches.value = Lsw.restoreMinimal(switches.value, window.__swBeforeMinimal);
          minimalOn.value = Lsw.isMinimal(switches.value);
        }
        persistSwitches();
        bumpPreviewHi('minimal');
      }
      async function persistSwitches() {
        try {
          await API.post('/api/prompts', { name: 'switches', value: switches.value });
        } catch (e) { /* keep UI state */ }
      }
      const previewHi = Vue.ref('');
      let _hiTimer = null;
      function bumpPreviewHi(tag) {
        previewHi.value = String(tag || '');
        clearTimeout(_hiTimer);
        _hiTimer = setTimeout(() => { previewHi.value = ''; }, 1000);
        if (previewOn.value) loadPreview();
      }
      const previewBrickStats = Vue.computed(() => {
        const sw = switches.value || {};
        const keys = (Lsw.BRICKS || []).concat(Lsw.META || []);
        const total = keys.length;
        const on = keys.filter(k => sw[k] !== false).length;
        const text = previewText.value || '';
        return { on, total, chars: text.length };
      });
      const DEFAULT_PROMPT_NODES = [
        {
          id: 'D01', name: '竖起大拇指赞同', tag: '赞同手势', color: '#4a90e2',
          desc: '保持原图单人出镜与原始场景构图不变，严禁出现其他人或新增肢体。画面中的人物作为正在认真观看视频的倾听者，嘴唇全程保持自然闭合，绝不说话、不做说话口型。人物神情专注投入，在倾听过程中自然流露出深表赞同的神态，缓缓竖起一只手的大拇指表示认同与称赞，随后手部自然微动，伴随轻轻点头与柔和眼神变化，呼吸起伏真实自然，整体宛如鲜活生动的真实人物视频。'
        },
        {
          id: 'D02', name: '手捂胸口深表感动', tag: '捂心共鸣', color: '#e26a6a',
          desc: '保持原图单人出镜与原始场景构图不变，严禁出现其他人或新增肢体。画面中的人物作为正在认真观看视频的倾听者，嘴唇全程保持自然闭合，绝不说话、不做说话口型。人物神情温润专注，内心深受触动，一只手自然轻抚在胸口（捂心口）表达感动与虔诚共鸣，伴随轻微缓慢的点头、柔和眨眼与自然的胸廓呼吸起伏，手部与肩颈姿态生动自然，绝不僵硬定格。'
        },
        {
          id: 'D03', name: '频频点头深表认同', tag: '专注点头', color: '#50b878',
          desc: '保持原图单人出镜与原始场景构图不变，严禁出现其他人或新增肢体。画面中的人物作为正在认真观看视频的倾听者，嘴唇全程保持自然闭合，绝不说话、不做说话口型。人物目光温和专注地注视前方，随着所听内容自然地频频点头表示深深认同，嘴角带着温润笑意，双肩与手臂有轻微自然的放松微动与呼吸感，整体动态真实流畅，与静态照片明显区分。'
        },
        {
          id: 'D04', name: '微笑倾听双手微动', tag: '松弛倾听', color: '#f5a623',
          desc: '保持原图单人出镜与原始场景构图不变，严禁出现其他人或新增肢体。画面中的人物作为正在认真观看视频的倾听者，嘴唇全程保持自然闭合，绝不说话、不做说话口型。人物面带温暖平和的微笑，眼神专注，偶尔轻轻点头赞同，身前或腿上的手臂与手掌有轻微自然的姿势调整与放松微动，配合真实呼吸起伏与自然眨眼，神态鲜活自然。'
        },
        {
          id: 'D05', name: '托腮/交握静思共鸣', tag: '静思赞同', color: '#9b6be2',
          desc: '保持原图单人出镜与原始场景构图不变，严禁出现其他人或新增肢体。画面中的人物作为正在认真观看视频的倾听者，嘴唇全程保持自然闭合，绝不说话、不做说话口型。人物保持自然的倾听姿态，手指与手腕有细腻真实的微动，目光专注深邃，随着倾听内容轻轻点头表示领悟与赞同，伴随柔和眨眼与均匀呼吸起伏，极具真实视频质感。'
        },
        {
          id: 'D06', name: '侧首微笑欣然赞许', tag: '欣然赞许', color: '#38b2ac',
          desc: '保持原图单人出镜与原始场景构图不变，严禁出现其他人或新增肢体。画面中的人物作为正在认真观看视频的倾听者，嘴唇全程保持自然闭合，绝不说话、不做说话口型。人物头部有轻微自然的侧倾与缓和点头动作，眉眼舒展流露出欣然赞许的神情，手臂与手部自然放松微动，配合真实呼吸与眨眼，整体生动自然。'
        },
        {
          id: 'D07', name: '车内静止专注点头', tag: '车内倾听', color: '#d69e2e',
          desc: '保持原图单人出镜与车内构图不变，车辆处于完全停稳状态，车窗外景物保持完全静止。画面中的人物作为正在车内认真观看视频的倾听者，嘴唇全程保持自然闭合，绝不说话、不做说话口型。人物神情专注平和，在倾听时自然轻轻点头表示赞同，手部与肩部有轻微自然的放松微动与呼吸起伏，整体真实自然且绝不穿帮。'
        },
        {
          id: 'D08', name: '抬手示意后自然回落', tag: '自然回落', color: '#ed64a6',
          desc: '保持原图单人出镜与原始场景构图不变，严禁出现其他人或新增肢体。画面中的人物作为正在认真观看视频的倾听者，嘴唇全程保持自然闭合，绝不说话、不做说话口型。人物原本抬起的手部做出简短温和的赞同示意后，顺畅自然地缓缓放下回归放松姿态（绝不僵硬举在半空），同时配合轻轻点头、温和眼神与自然呼吸起伏，动作连贯真实。'
        }
      ];
      const promptNodes = Vue.ref(DEFAULT_PROMPT_NODES.map(n => Object.assign({}, n)));
      const batchBodyRef = Vue.ref(null);
      const batchWires = Vue.ref([]);
      const batchCtx = Vue.reactive({ show: false, x: 0, y: 0, p: '' });

      async function loadPromptNodes() {
        try {
          const r = await API.get('/api/prompt-nodes');
          if (r && Array.isArray(r.nodes) && r.nodes.length) {
            promptNodes.value = r.nodes.map((n, idx) => {
              const id = n.id || ('D' + String(idx + 1).padStart(2, '0'));
              const rawName = String(n.name || ('描述语节点 ' + (idx + 1))).replace(/^D\d+\s*[·:：\-]\s*/i, '').trim();
              return Object.assign({
                id,
                name: rawName || id,
                tag: n.tag || '动作节点',
                color: n.color || DEFAULT_PROMPT_NODES[idx % DEFAULT_PROMPT_NODES.length].color,
                desc: n.desc || '',
              }, n, { id, name: rawName || id });
            });
          }
        } catch (e) { /* keep defaults */ }
      }
      loadPromptNodes();

      async function savePromptNodesQuiet() {
        try {
          await API.post('/api/prompt-nodes', { nodes: promptNodes.value });
        } catch (e) { /* ignore */ }
      }

      function nodeColorOf(nodeId) {
        const nd = (promptNodes.value || []).find(n => n.id === nodeId);
        return (nd && nd.color) || '#5b8cff';
      }

      function nodeLabelOf(im) {
        if (!im) return '';
        const nid = im.prompt_node_id || '';
        const nd = (promptNodes.value || []).find(n => n.id === nid);
        if (nd) return nd.id + ' · ' + String(nd.name || '').replace(/^D\d+\s*[·:：\-]\s*/i, '');
        if (nid) {
          const cleanNm = String(im.prompt_node_name || '').replace(/^D\d+\s*[·:：\-]\s*/i, '');
          return nid + (cleanNm ? (' · ' + cleanNm) : '');
        }
        return '';
      }

      function batchRowsFromIndex(rows) {
        return (rows || []).map(x => {
          const id = x.tid || x.id;
          return {
            tid: id, name: x.name || id, status: x.status || 'prepared',
            copy: x.copy || '',
            images: (x.images || []).map(im => ({
              p: im.p, desc: im.desc || '', role: im.role || '',
              prompt_node_id: im.prompt_node_id || '',
              prompt_node_name: im.prompt_node_name || '',
              url: im.file ? mediaUrl(id, im.file, im.mtime) : (im.url || ''),
            })),
          };
        });
      }

      function updateBatchWires() {
        if (!batchDlg.value || !batchBodyRef.value) {
          batchWires.value = [];
          return;
        }
        const wrap = batchBodyRef.value;
        const wrapRect = wrap.getBoundingClientRect();
        const tk = activeBatchTask();
        if (!tk || !tk.images) {
          batchWires.value = [];
          return;
        }
        const map = (batchDlg.value.nodeMap && batchDlg.value.nodeMap[tk.tid]) || {};
        const wires = [];
        tk.images.forEach(im => {
          const nid = map[im.p] || im.prompt_node_id || '';
          if (!nid) return;
          const imEl = wrap.querySelector('[data-batch-im="' + im.p + '"] .port-out');
          const ndEl = wrap.querySelector('[data-batch-node="' + nid + '"] .port-in');
          if (!imEl || !ndEl) return;
          const r1 = imEl.getBoundingClientRect();
          const r2 = ndEl.getBoundingClientRect();
          const x1 = r1.left + r1.width / 2 - wrapRect.left;
          const y1 = r1.top + r1.height / 2 - wrapRect.top;
          const x2 = r2.left + r2.width / 2 - wrapRect.left;
          const y2 = r2.top + r2.height / 2 - wrapRect.top;
          const dx = Math.max(40, Math.abs(x2 - x1) * 0.45);
          wires.push({
            key: im.p + '->' + nid,
            p: im.p,
            nodeId: nid,
            color: nodeColorOf(nid),
            d: 'M ' + x1.toFixed(1) + ' ' + y1.toFixed(1)
              + ' C ' + (x1 + dx).toFixed(1) + ' ' + y1.toFixed(1)
              + ', ' + (x2 - dx).toFixed(1) + ' ' + y2.toFixed(1)
              + ', ' + x2.toFixed(1) + ' ' + y2.toFixed(1),
          });
        });
        batchWires.value = wires;
      }

      async function openBatchDesc(seed) {
        await loadPromptNodes();
        let rows = [];
        try {
          const r = await API.get('/api/images/batch-desc-index');
          rows = batchRowsFromIndex(r.tasks || []);
        } catch (e) { rows = []; }
        if (!rows.length) {
          rows = batchRowsFromIndex((readyTasks.value || []).map(tk => {
            const mine = tk.id === tid.value && manifest.value && manifest.value.images;
            return {
              tid: tk.id, name: tk.name || tk.id, status: tk.status || 'draft',
              copy: tk.copy || '',
              images: mine ? manifest.value.images : (tk.images || []),
            };
          }));
        }
        const st = Lsw.batchInit(rows);
        st.filter = '';
        st.onlyMissing = false;
        st.mode = 'overwrite';
        st.activeNodeId = (promptNodes.value[0] && promptNodes.value[0].id) || 'D01';
        st.nodeMap = {};
        (st.tasks || []).forEach(tk => {
          st.nodeMap[tk.tid] = {};
          (tk.images || []).forEach(im => {
            if (im.prompt_node_id) {
              st.nodeMap[tk.tid][im.p] = im.prompt_node_id;
            }
          });
        });
        if (tid.value && st.tasks.some(tk => tk.tid === tid.value && tk.selectable)) {
          st.active = tid.value;
        }
        if (seed && typeof seed === 'string') {
          const matchNode = (promptNodes.value || []).find(n => n.id === seed);
          if (matchNode) {
            st.activeNodeId = matchNode.id;
            st.desc = matchNode.desc;
          } else {
            st.desc = String(seed);
          }
        } else {
          const firstNd = (promptNodes.value || [])[0];
          if (firstNd) st.desc = firstNd.desc;
        }
        batchDlg.value = st;
        Vue.nextTick(() => setTimeout(updateBatchWires, 40));
      }
      function closeBatchDesc() {
        batchCtx.show = false;
        batchDlg.value = null;
      }
      function batchStats() {
        if (!batchDlg.value) return { tasks: 0, images: 0, had: 0 };
        return Lsw.stats(batchDlg.value, batchDlg.value.mode);
      }

      function batchImgNodeId(im) {
        if (!batchDlg.value || !batchDlg.value.active || !im) return '';
        const map = (batchDlg.value.nodeMap && batchDlg.value.nodeMap[batchDlg.value.active]) || {};
        return map[im.p] || im.prompt_node_id || '';
      }

      function batchNodeConnectedPs(nodeId) {
        if (!batchDlg.value || !batchDlg.value.active) return [];
        const tk = activeBatchTask();
        if (!tk || !tk.images) return [];
        const map = (batchDlg.value.nodeMap && batchDlg.value.nodeMap[tk.tid]) || {};
        return tk.images.filter(im => (map[im.p] || im.prompt_node_id) === nodeId).map(im => im.p);
      }

      function connectBatchSelToNode(nodeId, explicitPs) {
        if (!batchDlg.value || !batchDlg.value.active) return;
        const tk = activeBatchTask();
        if (!tk) return;
        const nd = (promptNodes.value || []).find(n => n.id === nodeId);
        if (!nd) return;
        const ps = (explicitPs && explicitPs.length)
          ? explicitPs
          : ((batchDlg.value.sel && batchDlg.value.sel[tk.tid]) || []);
        if (!ps.length) {
          toast('请先在左侧勾选需要连线的图片（支持多选或右键连线）', '', 'bad');
          return;
        }
        if (!batchDlg.value.nodeMap[tk.tid]) batchDlg.value.nodeMap[tk.tid] = {};
        ps.forEach(p => {
          batchDlg.value.nodeMap[tk.tid][p] = nd.id;
          const im = (tk.images || []).find(x => x.p === p);
          if (im) {
            im.prompt_node_id = nd.id;
            im.prompt_node_name = nd.name;
            im.desc = nd.desc;
          }
        });
        batchDlg.value.activeNodeId = nd.id;
        batchDlg.value.desc = nd.desc;
        batchCtx.show = false;
        Vue.nextTick(updateBatchWires);
        toast('已将 ' + ps.length + ' 张图片连线到节点 [' + nd.id + ' · ' + nd.name + ']', '', 'ok');
      }

      function unlinkBatchImg(p) {
        if (!batchDlg.value || !batchDlg.value.active) return;
        const tk = activeBatchTask();
        if (!tk) return;
        if (batchDlg.value.nodeMap && batchDlg.value.nodeMap[tk.tid]) {
          delete batchDlg.value.nodeMap[tk.tid][p];
        }
        const im = (tk.images || []).find(x => x.p === p);
        if (im) {
          im.prompt_node_id = '';
          im.prompt_node_name = '';
        }
        Vue.nextTick(updateBatchWires);
      }

      function autoDistributeBatchNodes() {
        if (!batchDlg.value || !batchDlg.value.active) return;
        const tk = activeBatchTask();
        if (!tk || !tk.images || !tk.images.length) return;
        const nodes = (promptNodes.value || []).filter(n => (n.desc || '').trim());
        if (!nodes.length) return;
        const selPs = (batchDlg.value.sel && batchDlg.value.sel[tk.tid] && batchDlg.value.sel[tk.tid].length)
          ? batchDlg.value.sel[tk.tid]
          : tk.images.map(im => im.p);
        if (!batchDlg.value.nodeMap[tk.tid]) batchDlg.value.nodeMap[tk.tid] = {};
        // Rotate across general reaction nodes so adjacent images never repeat the same action
        const generalNodes = nodes.filter(n => n.id !== 'D07' && n.id !== 'D08');
        const pool = generalNodes.length ? generalNodes : nodes;
        selPs.forEach((p, idx) => {
          const im = (tk.images || []).find(x => x.p === p);
          // Preserve specialized nodes (D07 in-car, D08 raised hand) if already matched
          const curId = (im && im.prompt_node_id) || batchDlg.value.nodeMap[tk.tid][p] || '';
          const nd = (curId === 'D07' || curId === 'D08')
            ? (nodes.find(n => n.id === curId) || pool[idx % pool.length])
            : pool[idx % pool.length];
          batchDlg.value.nodeMap[tk.tid][p] = nd.id;
          if (im) {
            im.prompt_node_id = nd.id;
            im.prompt_node_name = nd.name;
            im.desc = nd.desc;
          }
        });
        Vue.nextTick(updateBatchWires);
        toast('已按组不重复智能连线 ' + selPs.length + ' 张图片到右侧描述语节点', '', 'ok');
      }

      function addPromptNode() {
        const nextIdx = (promptNodes.value || []).length + 1;
        const newId = 'D' + String(nextIdx).padStart(2, '0');
        const palette = ['#4a90e2', '#e26a6a', '#50b878', '#f5a623', '#9b6be2', '#38b2ac', '#d69e2e', '#ed64a6'];
        promptNodes.value.push({
          id: newId,
          name: '自定义倾听动作 ' + nextIdx,
          tag: '自定义',
          color: palette[(nextIdx - 1) % palette.length],
          desc: '保持原图单人出镜与原始场景构图不变，严禁出现其他人或新增肢体。画面中的人物作为正在认真观看视频的倾听者，嘴唇全程保持自然闭合，绝不说话、不做说话口型。人物神情专注自然，伴随轻轻点头、手部轻微自然动作与真实呼吸起伏。',
        });
        savePromptNodesQuiet();
        Vue.nextTick(updateBatchWires);
      }

      function openBatchImgCtx(ev, im) {
        if (!batchDlg.value || !batchDlg.value.active || !im) return;
        const curSel = (batchDlg.value.sel && batchDlg.value.sel[batchDlg.value.active]) || [];
        if (!curSel.includes(im.p)) {
          toggleBatchImg(im.p, true);
        }
        batchCtx.p = im.p;
        batchCtx.x = Math.min(ev.clientX, window.innerWidth - 280);
        batchCtx.y = Math.min(ev.clientY, window.innerHeight - 360);
        batchCtx.show = true;
      }

      async function connectWallImgsToNode(nodeId) {
        const nd = (promptNodes.value || []).find(n => n.id === nodeId);
        if (!nd || !tid.value) return;
        const group = menu.group || lastGroup.value || 'subjects';
        const curSel = (sel[group] || []).slice();
        const targetPs = curSel.length
          ? curSel
          : (menu.im && menu.im.p ? [menu.im.p] : (focusP.value ? [focusP.value] : []));
        closeImgMenu();
        if (!targetPs.length) {
          toast('请先勾选或右键点击至少一张图片', '', 'bad');
          return;
        }
        const r = await API.post('/api/images/batch-desc', {
          items: [{ tid: tid.value, ps: targetPs }],
          desc: nd.desc,
          node_id: nd.id,
          node_name: nd.name,
          mode: 'overwrite',
        });
        await loadManifestSafe();
        toast('已将 ' + (r.n_images || targetPs.length) + ' 张图片连线到 [' + nd.id + ' · ' + nd.name + ']', '', 'ok');
      }

      async function autoDistributeWallNodes() {
        if (!tid.value || !manifest.value || !manifest.value.images) return;
        const group = menu.group || lastGroup.value || 'subjects';
        const curSel = (sel[group] || []).slice();
        const allSubjs = (subjects.value || []).map(im => im.p);
        const targetPs = curSel.length ? curSel : allSubjs;
        closeImgMenu();
        if (!targetPs.length) return;
        const nodes = (promptNodes.value || []).filter(n => (n.desc || '').trim());
        const general = nodes.filter(n => n.id !== 'D07' && n.id !== 'D08');
        const pool = general.length ? general : nodes;
        const byNode = {};
        targetPs.forEach((p, idx) => {
          const im = findIm(p);
          const curId = (im && im.prompt_node_id) || '';
          const nd = (curId === 'D07' || curId === 'D08')
            ? (nodes.find(n => n.id === curId) || pool[idx % pool.length])
            : pool[idx % pool.length];
          if (!byNode[nd.id]) byNode[nd.id] = { nd, ps: [] };
          byNode[nd.id].ps.push(p);
        });
        const connections = Object.values(byNode).map(g => ({
          node_id: g.nd.id,
          node_name: g.nd.name,
          desc: g.nd.desc,
          items: [{ tid: tid.value, ps: g.ps }],
        }));
        const r = await API.post('/api/images/batch-desc', { connections, mode: 'overwrite' });
        await loadManifestSafe();
        toast('已按组不重复智能分配 ' + (r.n_images || targetPs.length) + ' 张图片的描述语节点', '', 'ok');
      }

      async function runBatchDesc() {
        if (!batchDlg.value) return;
        const st = batchDlg.value;
        await savePromptNodesQuiet();

        // Build multi-node connections from st.nodeMap across checked tasks
        const byNode = {};
        (st.tasks || []).forEach(tk => {
          if (!tk.checked || !tk.selectable) return;
          const tmap = (st.nodeMap && st.nodeMap[tk.tid]) || {};
          Object.keys(tmap).forEach(p => {
            const nid = tmap[p];
            const nd = (promptNodes.value || []).find(n => n.id === nid);
            if (!nd || !(nd.desc || '').trim()) return;
            if (!byNode[nid]) byNode[nid] = { nd, taskMap: {} };
            if (!byNode[nid].taskMap[tk.tid]) byNode[nid].taskMap[tk.tid] = [];
            byNode[nid].taskMap[tk.tid].push(p);
          });
        });

        const connections = Object.values(byNode).map(entry => ({
          node_id: entry.nd.id,
          node_name: entry.nd.name,
          desc: entry.nd.desc,
          voice: st.voice || '',
          items: Object.keys(entry.taskMap).map(tKey => ({ tid: tKey, ps: entry.taskMap[tKey] })),
        }));

        const items = [];
        st.tasks.forEach(tk => {
          if (!tk.checked || !tk.selectable) return;
          const ps = (st.sel[tk.tid] || []).filter(p => !((st.nodeMap && st.nodeMap[tk.tid] && st.nodeMap[tk.tid][p])));
          if (ps.length) items.push({ tid: tk.tid, ps });
        });

        if (!connections.length && (!items.length || !(st.desc || '').trim())) {
          toast('请先将左侧图片连线到右侧描述语节点，或勾选图片后填写描述语', '', 'bad');
          return;
        }
        const activeNd = (promptNodes.value || []).find(n => n.id === st.activeNodeId);
        const body = {
          connections,
          items,
          desc: st.desc || (activeNd ? activeNd.desc : ''),
          node_id: activeNd ? activeNd.id : '',
          node_name: activeNd ? activeNd.name : '',
          voice: st.voice,
          mode: st.mode === 'overwrite' ? 'overwrite' : 'fill_empty',
        };
        const r = await API.post('/api/images/batch-desc', body);
        const wrote = Number(r.n_images || 0);
        if (!wrote) {
          const skipped = r.skipped || [];
          const had = skipped.filter(s => s && s.why === '已有描述语').length;
          toast(had ? t('gemini.batch_desc.none_empty') : t('gemini.batch_desc.none'), '', '');
          return;
        }
        toast(t('gemini.batch_desc.done', { tasks: String(r.n_tasks || 0), images: String(wrote) }), '', 'ok');
        closeBatchDesc();
        if (tid.value) loadManifestSafe();
      }
      const batchTasksShown = Vue.computed(() => {
        if (!batchDlg.value) return [];
        return Lsw.filterTasks(batchDlg.value.tasks, batchDlg.value.filter, batchDlg.value.onlyMissing);
      });
      function batchSelectAll() {
        if (!batchDlg.value) return;
        Lsw.selectAllChecks(batchTasksShown.value, true);
      }
      function batchInvert() {
        if (!batchDlg.value) return;
        Lsw.invertChecks(batchTasksShown.value);
      }
      function selectMissingImgs() {
        if (!batchDlg.value || !batchDlg.value.active) return;
        Lsw.selectMissing(batchDlg.value, batchDlg.value.active);
      }
      function selectAllImgs() {
        if (!batchDlg.value || !batchDlg.value.active) return;
        const tk = (batchDlg.value.tasks || []).find(t => t.tid === batchDlg.value.active);
        if (!tk) return;
        const ids = (tk.images || []).filter(im => !(im.empty || im.isEmpty)).map(im => im.p);
        if (!batchDlg.value.sel) batchDlg.value.sel = {};
        batchDlg.value.sel[batchDlg.value.active] = ids;
      }
      function selectNoneImgs() {
        if (!batchDlg.value || !batchDlg.value.active) return;
        if (!batchDlg.value.sel) batchDlg.value.sel = {};
        batchDlg.value.sel[batchDlg.value.active] = [];
      }
      function toggleBatchImg(p, on) {
        if (!batchDlg.value || !batchDlg.value.active) return;
        if (typeof on !== 'boolean') {
          const cur = batchDlg.value.sel[batchDlg.value.active] || [];
          on = !cur.includes(p);
        }
        Lsw.toggleImg(batchDlg.value, batchDlg.value.active, p, on);
      }
      function activeBatchTask() {
        if (!batchDlg.value) return null;
        return batchDlg.value.tasks.find(x => x.tid === batchDlg.value.active) || null;
      }
      function activeBatchImgCount() {
        const tk = activeBatchTask();
        if (!tk) return { sel: 0, total: 0 };
        return { sel: (batchDlg.value.sel[tk.tid] || []).length, total: (tk.images || []).length };
      }

      async function quickImgAct(im, act, ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        if (!tid.value || !im) return;
        const p = im.p;
        const imgPath = '/api/tasks/' + encodeURIComponent(tid.value) + '/image/' + encodeURIComponent(p);
        try {
          if (act === 'engine') {
            const curIsStudio = !!(expandAcc.value && expandAcc.value.id === 'STUDIO');
            const nextId = curIsStudio ? '' : 'STUDIO';
            const r = await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/expand-account', { account_id: nextId });
            if (r && r.current && r.current.id) expandAcc.value = r.current;
            toast(nextId === 'STUDIO' ? '已切换为：🖥️ 本地 AI · FLUX.2 (可选项)' : '已切换为：⚡ 优先使用付费账号 (Flow / Grok)', '', 'ok');
            return;
          }
          if (act === 'expand') {
            await API.post(imgPath, { expand_ref: true });
            im.expand_ref = true;
            await API.job('expand', tid.value);
            toast('已将 ' + p + ' 设为参考并提交扩图', '', 'ok');
            return;
          }
          if (act === 'upscale') {
            await API.job('upscale', tid.value, { ps: [p] });
            toast('已提交 ' + p + ' 超分清画', '', 'ok');
            return;
          }
          if (act === 'flip') {
            await API.post(imgPath + '/flip');
            await loadManifestSafe();
            toast('已镜像翻转 ' + p, '', 'ok');
            return;
          }
          if (act === 'crop') {
            if (!window.openCropper) return;
            const info = await API.get(imgPath + '/info');
            const box = await window.openCropper({
              tid: tid.value, p, file: info.file || im.file,
              w: info.w, h: info.h, sha1: im.sha1, mtime: im.mtime, n_refs: 0,
            });
            if (!box) return;
            await API.post(imgPath + '/crop', box);
            await loadManifestSafe();
            toast('已完成裁切 ' + p, '', 'ok');
            return;
          }
        } catch (e) { /* API 已 toast */ }
      }

      return {
        t, open, apiKey, model, modelOptions, testOk, testing,
        lp, lpOpen, lpCopy, lpApply, lpRestore,
        desc, cameraText, expand, expandLegacy, coverage, legalN, noFrontal, onNoFrontal, refillClause, upscaleTpl, bridgeTpl, bridgeSingleTpl, chainTpl, tail, tailNotes, problem, newPhrase,
        saveState, notices, bakView, q, qSummary, qFails,
        tid, manifest, loadingMan, runningKind, previewOn, previewSeg, previewText, previewLoading,
        readyTasks, allTasks, images, subjects, brolls, segments, selectedTask, tidReady, missingDesc, manualN, expandAcc, busy,
        mediaUrl, measureLine, covLine, segLabel, sourceLabel,
        draftOf, fieldSaveClass, fieldSaveLabel, fieldSaveTitle, forceManual, rewriteDesc,
        addCam, addAct, delRow, moveRow,
        covOpen, toggleCovOpen, openCovList, covCamSummary, covActSummary, covClassOpt, onCovTextInput,
        tailStringKeys, tailDictKeys, dictInnerKeys, emotionPresetName, setEmotionPreset,
        saveLabel, saveClass, onBlockInput, onBlockBlur, toggleOpen,
        testKey, onKeyBlur, onModelChange,
        saveDesc, saveExpand, saveRefillClause, saveUpscale, saveBridge, saveBridgeSingle, saveChainBlock, resetBlock, saveCamera, saveTail,
        addPhrase, delPhrase, runJob, scanProblems,
        bannerText, viewBak, restoreBak, ackNotice,
        toggleQueue, qPause, qSkip, qStop, goTask, dismissSummary,
        onDescBlur, onVoiceBlur, onDescInput, onVoiceInput, onRoleChange, openPreview, loadPreview, copyPreview,
        switches, minimalOn, brickKeys, switchGroups, batchDlg, brickLabel,
        groupOn, groupStateOf, toggleBrick, toggleSwitchGroup, toggleMinimal,
        openBatchDesc, closeBatchDesc, batchStats, runBatchDesc,
        batchTasksShown, batchSelectAll, batchInvert, selectMissingImgs, selectAllImgs, selectNoneImgs, toggleBatchImg,
        activeBatchTask, activeBatchImgCount, previewHi, previewBrickStats,
        promptNodes, batchBodyRef, batchWires, batchCtx,
        nodeColorOf, nodeLabelOf, updateBatchWires, batchImgNodeId, batchNodeConnectedPs,
        connectBatchSelToNode, unlinkBatchImg, autoDistributeBatchNodes, addPromptNode,
        openBatchImgCtx, connectWallImgsToNode, autoDistributeWallNodes, savePromptNodesQuiet,
        focusP, sel, lastGroup, menu, menuRef,
        selN, isChecked, onCheckClick, onCardClick, selAll, selInvert, selNone, syncFromFocus,
        openImgMenu, onImgMenu, closeImgMenu, menuOff, menuTitle, menuSyncN,
        openRescan, openAdopt, dropMissing, onExpandDone: loadManifestSafe, openPromptBlock, quickImgAct,
      };
    },
    template: `
<section class="panel-gemini">
  <div class="g-banner" v-for="n in notices" :key="(n.kind||'')+':'+(n.name||'')+':'+(n.bak_key||'')">
    <span class="g-banner-txt">{{ bannerText(n) }}</span>
    <button class="btn sm" @click="viewBak(n)">{{ t('gemini.migrate.view') }}</button>
    <button class="btn sm" v-if="n.kind==='format_reset' && n.name==='expand'" @click="restoreBak(n)">{{ t('gemini.migrate.restore') }}</button>
    <button class="btn ghost sm" @click="ackNotice(n)">{{ t('gemini.migrate.ack') }}</button>
  </div>
  <div class="g-bak-view" v-if="bakView">
    <div class="row" style="display:flex;align-items:center;gap:8px">
      <b>{{ bakView.title }}</b>
      <span class="spacer"></span>
      <button class="btn ghost sm" @click="bakView=null">✕</button>
    </div>
    <pre>{{ bakView.text }}</pre>
  </div>

  <div class="col-left">
    <article class="g-card g-lang">
      <div class="g-hd" @click="lpOpen">
        <span class="chev">{{ lp.open ? '▾' : '▸' }}</span>{{ t('prompts.lang.title') }}
        <span class="spacer"></span>
        <span class="muted">{{ lp.cur ? t('prompts.lang.cur', { name: lp.cur }) : t('prompts.lang.none') }}</span>
      </div>
      <div class="g-bd" v-show="lp.open">
        <p class="muted">{{ t('prompts.lang.intro') }}</p>
        <div class="g-field">
          <label>{{ t('setup.lang.pick') }}</label>
          <select v-model="lp.code" @change="lp.text=''">
            <option value="">{{ t('setup.lang.keep') }}</option>
            <option v-for="l in lp.langs" :key="l.code" :value="l.code">{{ l.name }}</option>
          </select>
        </div>
        <template v-if="lp.code">
          <p class="muted">{{ t('setup.lang.how') }}</p>
          <div class="row">
            <label><input type="radio" value="core" v-model="lp.scope" @change="lp.text=''"> {{ t('setup.lang.scope_core', { n: String(lp.n) }) }}</label>
            <label><input type="radio" value="all" v-model="lp.scope" @change="lp.text=''"> {{ t('setup.lang.scope_all', { n: String(lp.n_all) }) }}</label>
          </div>
          <div class="row">
            <button class="btn sm primary" :disabled="lp.busy" @click="lpCopy">{{ t('setup.lang.copy') }}</button>
            <button class="btn sm ghost" v-if="lp.has_backup" :disabled="lp.busy" @click="lpRestore">{{ t('prompts.lang.restore') }}</button>
          </div>
          <textarea v-if="lp.text" class="g-lang-out" readonly :value="lp.text"></textarea>
          <div class="g-field">
            <label>{{ t('setup.lang.paste_label') }}</label>
            <textarea v-model="lp.paste" class="g-lang-in" :placeholder="t('setup.lang.paste_ph')"></textarea>
          </div>
          <div class="row">
            <button class="btn sm" :disabled="lp.busy" @click="lpApply">{{ t('setup.lang.apply') }}</button>
            <span v-if="lp.applied" class="g-ok">{{ t('setup.lang.applied', { n: String(lp.applied.applied || 0) }) }}</span>
          </div>
          <ul v-if="lp.problems.length" class="g-lang-prob">
            <li v-for="(p, i) in lp.problems" :key="i">{{ p.key }} — {{ p.text }}</li>
          </ul>
        </template>
      </div>
    </article>

    <article class="g-card">
      <div class="g-hd" @click="toggleOpen('gemini')">
        <span class="chev">{{ open.gemini ? '▾' : '▸' }}</span>{{ t('gemini.card.gemini') }}
      </div>
      <div class="g-bd" v-show="open.gemini">
        <div class="g-field">
          <label>{{ t('gemini.api_key') }}</label>
          <div class="row">
            <input type="password" v-model="apiKey" data-gkey="1" @blur="onKeyBlur" autocomplete="off" style="flex:1">
            <button class="btn sm" :disabled="testing" @click="testKey">{{ t('gemini.test') }}</button>
          </div>
          <div class="g-ok" v-if="testOk">{{ testOk }}</div>
        </div>
        <div class="g-field">
          <label>{{ t('gemini.model') }}</label>
          <select v-model="model" @change="onModelChange">
            <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
      </div>
    </article>

    
    <article class="g-card g-sw-top" data-block="switches">
      <div class="g-switch-bar">
        <button type="button" class="g-switch" :class="{on: minimalOn}" @click="toggleMinimal" :title="t('gemini.switches.minimal')"></button>
        <div class="g-sw-top-text">
          <b>{{ t('gemini.switches.minimal') }}</b>
          <div class="hint">{{ t('gemini.switches.minimal_hint') }}</div>
        </div>
        <button class="btn ghost sm" @click="openPreview && openPreview()">{{ t('gemini.switches.preview') }}</button>
      </div>
      <div class="g-sw-section-label">{{ t('gemini.switches.film_group') }}</div>
      <div class="g-sw-group" v-for="g in switchGroups" :key="g.id">
        <button type="button" class="g-switch g-switch-tri" :class="groupStateOf(g.id)" @click="toggleSwitchGroup(g.id)" :title="g.label"></button>
        <span class="g-sw-group-lab">{{ g.label }}</span>
        <div class="g-sw-bricks">
          <article class="g-card g-brick-card" v-for="k in g.keys" :key="'bk-'+k" :class="{'sw-off': !switches[k]}">
            <div class="g-hd" @click="toggleOpen('sw_'+k)">
              <span class="chev">{{ open['sw_'+k] ? '▾' : '▸' }}</span>
              <span>{{ brickLabel(k) }}</span>
              <span class="sw-off-tag" v-if="!switches[k]">{{ t('gemini.switches.off') }}</span>
              <span class="spacer"></span>
              <button type="button" class="g-switch" :class="{on: !!switches[k]}" @click.stop="toggleBrick(k)"></button>
            </div>
            <div class="g-bd" v-show="open['sw_'+k]">
              <p class="g-hint">{{ t('gemini.switches.brick_hint') }}</p>
            </div>
</article>
        </div>
      </div>
      <div class="g-sw-divider"></div>
      <div class="g-sw-section-label">{{ t('gemini.switches.tool_group') }}</div>
    </article>
<article class="g-card" data-block="desc">
      <div class="g-hd" @click="toggleOpen('desc')">
        <span class="chev">{{ open.desc ? '▾' : '▸' }}</span>{{ t('gemini.card.desc') }}
        <span class="g-tool-hint">{{ t('gemini.switches.used_desc') }}</span>
        <span class="g-save" :class="saveClass('desc')">{{ saveLabel('desc') }}</span>
      </div>
      <div class="g-bd" v-show="open.desc">
        <textarea class="mono desc-box" v-model="desc" @input="onBlockInput('desc')" @blur="onBlockBlur('desc')"></textarea>
        <div class="g-err" v-if="saveState.desc.error">{{ saveState.desc.error }}</div>
        <div class="g-ft">
          <button class="btn ghost sm" @click="resetBlock('desc')">{{ t('gemini.reset') }}</button>
          <button class="btn sm primary" :disabled="saveState.desc.saving" @click="saveDesc">{{ t('gemini.save') }}</button>
        </div>
      </div>
    </article>

    <article class="g-card" data-block="camera">
      <div class="g-hd" @click="toggleOpen('camera')">
        <span class="chev">{{ open.camera ? '▾' : '▸' }}</span>{{ t('gemini.card.camera') }}
        <span class="g-tool-hint">{{ t('gemini.switches.used_camera') }}</span>
        <span class="g-save" :class="saveClass('camera')">{{ saveLabel('camera') }}</span>
      </div>
      <div class="g-bd" v-show="open.camera">
        <p class="g-hint">{{ t('gemini.camera_hint') }}</p>
        <textarea class="mono cam-box" v-model="cameraText" @input="onBlockInput('camera')" @blur="onBlockBlur('camera')"></textarea>
        <div class="g-err" v-if="saveState.camera.error">{{ saveState.camera.error }}</div>
        <div class="g-ft">
          <button class="btn ghost sm" @click="resetBlock('camera')">{{ t('gemini.reset') }}</button>
          <button class="btn sm primary" :disabled="saveState.camera.saving" @click="saveCamera">{{ t('gemini.save') }}</button>
        </div>
      </div>
    </article>

    <article class="g-card" id="g-block-expand" data-block="expand">
      <div class="g-hd" @click="toggleOpen('expand')">
        <span class="chev">{{ open.expand ? '▾' : '▸' }}</span>{{ t('gemini.card.expand') }}
        <span class="g-tool-hint">{{ t('gemini.switches.used_expand') }}</span>
        <span class="g-save" :class="saveClass('expand')">{{ saveLabel('expand') }}</span>
      </div>
      <div class="g-bd" v-show="open.expand">
        <p class="g-hint">{{ t('gemini.expand_hint') }}</p>
        <template v-if="expandLegacy">
          <p class="g-hint">{{ t('gemini.cov.legacy') }}</p>
          <textarea class="mono expand-box" v-model="expand" @input="onBlockInput('expand')" @blur="onBlockBlur('expand')"></textarea>
        </template>
        <template v-else>
          <div class="cov-legal">{{ t('gemini.cov.legal', { n: String(legalN) }) }}</div>
          <label class="cov-switch">
            <input type="checkbox" :checked="noFrontal" @change="onNoFrontal">
            <span>{{ t('gemini.cov.no_frontal') }}</span>
          </label>
          <p class="g-hint cov-switch-hint">{{ t('gemini.cov.no_frontal_hint') }}</p>
          <label class="cov-lab">{{ t('gemini.cov.head') }}</label>
          <textarea class="mono cov-head" v-model="coverage.head" @input="onBlockInput('expand')" @blur="onBlockBlur('expand')"></textarea>
          <label class="cov-lab">{{ t('gemini.cov.tail') }}</label>
          <textarea class="mono cov-tail" v-model="coverage.tail" @input="onBlockInput('expand')" @blur="onBlockBlur('expand')"></textarea>
          <div class="cov-tbl-hd">
            <b>{{ t('gemini.cov.cameras') }}</b>
            <span class="cov-hd-sp"></span>
            <button type="button" class="btn ghost sm" @click="toggleCovOpen('cameras')">{{ covOpen.cameras ? t('gemini.cov.collapse') : t('gemini.cov.expand_btn') }}</button>
            <button type="button" class="btn ghost sm" @click="addCam(); openCovList('cameras')">{{ t('gemini.cov.add') }}</button>
          </div>
          <div class="cov-list" data-kind="cameras">
            <template v-if="!covOpen.cameras">
              <div class="cov-sum" v-for="(row, i) in coverage.cameras" :key="'cs'+i">{{ covCamSummary(row) }}</div>
            </template>
            <template v-else>
            <div class="cov-row" v-for="(row, i) in coverage.cameras" :key="'c'+i">
              <div class="cov-row-top">
                <input class="cov-id" v-model="row.id" :title="t('gemini.cov.id.title')" @input="onBlockInput('expand')" @blur="onBlockBlur('expand')">
                <input class="cov-name" v-model="row.name" :title="t('gemini.cov.name.title')" @input="onBlockInput('expand')" @blur="onBlockBlur('expand')">
                <div class="cov-meta-group">
                <label class="cov-meta" :title="t('gemini.cov.class.title')">
                  <span>{{ t('gemini.cov.class') }}</span>
                  <select v-model="row.class" @change="onBlockInput('expand')">
                    <option value="A">{{ covClassOpt('A') }}</option>
                    <option value="B">{{ covClassOpt('B') }}</option>
                    <option value="C">{{ covClassOpt('C') }}</option>
                    <option value="D">{{ covClassOpt('D') }}</option>
                  </select>
                </label>
                <label class="cov-meta cov-chk" :title="t('gemini.cov.frontal.title')">
                  <input type="checkbox" v-model="row.frontal" @change="onBlockInput('expand')">
                  <span>{{ t('gemini.cov.frontal') }}</span>
                </label>
                <label class="cov-meta" :title="t('gemini.cov.rank.title')">
                  <span>{{ t('gemini.cov.rank') }}</span>
                  <input class="cov-rank" type="number" min="1" max="4" v-model.number="row.rank" @input="onBlockInput('expand')" @blur="onBlockBlur('expand')">
                </label>
                </div>
                <span class="cov-ops">
                  <button type="button" class="x" :title="t('gemini.cov.up')" @click="moveRow(coverage.cameras, i, -1)">↑</button>
                  <button type="button" class="x" :title="t('gemini.cov.down')" @click="moveRow(coverage.cameras, i, 1)">↓</button>
                  <button type="button" class="x" :title="t('gemini.cov.del')" @click="delRow(coverage.cameras, i)">✕</button>
                </span>
              </div>
              <textarea class="cov-text" v-model="row.text" :title="t('gemini.cov.text.title')" @input="onCovTextInput($event)" @blur="onBlockBlur('expand')"></textarea>
            </div>
            </template>
          </div>
          <div class="cov-tbl-hd">
            <b>{{ t('gemini.cov.actions') }}</b>
            <span class="cov-hd-sp"></span>
            <button type="button" class="btn ghost sm" @click="toggleCovOpen('actions')">{{ covOpen.actions ? t('gemini.cov.collapse') : t('gemini.cov.expand_btn') }}</button>
            <button type="button" class="btn ghost sm" @click="addAct(); openCovList('actions')">{{ t('gemini.cov.add') }}</button>
          </div>
          <div class="cov-list" data-kind="actions">
            <template v-if="!covOpen.actions">
              <div class="cov-sum" v-for="(row, i) in coverage.actions" :key="'as'+i">{{ covActSummary(row) }}</div>
            </template>
            <template v-else>
            <div class="cov-row" v-for="(row, i) in coverage.actions" :key="'a'+i">
              <div class="cov-row-top">
                <input class="cov-id" v-model="row.id" :title="t('gemini.cov.id.title')" @input="onBlockInput('expand')" @blur="onBlockBlur('expand')">
                <input class="cov-name" v-model="row.name" :title="t('gemini.cov.name.title')" @input="onBlockInput('expand')" @blur="onBlockBlur('expand')">
                <div class="cov-meta-group">
                <label class="cov-meta" :title="t('gemini.cov.phase.title')">
                  <span>{{ t('gemini.cov.phase') }}</span>
                  <select v-model="row.phase" @change="onBlockInput('expand')">
                    <option value="start">{{ t('gemini.cov.phase.start') }}</option>
                    <option value="develop">{{ t('gemini.cov.phase.develop') }}</option>
                    <option value="hold">{{ t('gemini.cov.phase.hold') }}</option>
                    <option value="resolve">{{ t('gemini.cov.phase.resolve') }}</option>
                  </select>
                </label>
                <label class="cov-meta" :title="t('gemini.cov.rank.title')">
                  <span>{{ t('gemini.cov.rank') }}</span>
                  <input class="cov-rank" type="number" min="1" max="4" v-model.number="row.rank" @input="onBlockInput('expand')" @blur="onBlockBlur('expand')">
                </label>
                </div>
                <span class="cov-ops">
                  <button type="button" class="x" :title="t('gemini.cov.up')" @click="moveRow(coverage.actions, i, -1)">↑</button>
                  <button type="button" class="x" :title="t('gemini.cov.down')" @click="moveRow(coverage.actions, i, 1)">↓</button>
                  <button type="button" class="x" :title="t('gemini.cov.del')" @click="delRow(coverage.actions, i)">✕</button>
                </span>
              </div>
              <textarea class="cov-text" v-model="row.text" :title="t('gemini.cov.text.title')" @input="onCovTextInput($event)" @blur="onBlockBlur('expand')"></textarea>
            </div>
            </template>
          </div>
        </template>
        <div class="g-err" v-if="saveState.expand.error">{{ saveState.expand.error }}</div>
        <div class="g-ft">
          <button class="btn ghost sm" @click="resetBlock('expand')">{{ t('gemini.reset') }}</button>
          <button class="btn sm primary" :disabled="saveState.expand.saving" @click="saveExpand">{{ t('gemini.save') }}</button>
        </div>
      </div>
    </article>

    <article class="g-card" id="g-block-refill" data-block="refill_clause">
      <div class="g-hd" @click="toggleOpen('refill')">
        <span class="chev">{{ open.refill ? '▾' : '▸' }}</span>{{ t('gemini.card.refill') }}
        <span class="g-save" :class="saveClass('refill_clause')">{{ saveLabel('refill_clause') }}</span>
      <span class="g-tool-hint">{{ t('gemini.switches.used_refill') }}</span></div>
      <div class="g-bd" v-show="open.refill">
        <input class="refill-line" v-model="refillClause" @input="onBlockInput('refill_clause')" @blur="onBlockBlur('refill_clause')">
        <p class="g-hint">{{ t('gemini.refill_hint') }}</p>
        <div class="g-err" v-if="saveState.refill_clause.error">{{ saveState.refill_clause.error }}</div>
        <div class="g-ft">
          <button class="btn ghost sm" @click="resetBlock('refill_clause')">{{ t('gemini.reset') }}</button>
          <button class="btn sm primary" :disabled="saveState.refill_clause.saving" @click="saveRefillClause">{{ t('gemini.save') }}</button>
        </div>
      </div>
    </article>

    <article class="g-card" id="g-block-upscale" data-block="upscale">
      <div class="g-hd" @click="toggleOpen('upscale')">
        <span class="chev">{{ open.upscale ? '▾' : '▸' }}</span>{{ t('gemini.card.upscale') }}
        <span class="g-save" :class="saveClass('upscale')">{{ saveLabel('upscale') }}</span>
      <span class="g-tool-hint">{{ t('gemini.switches.used_upscale') }}</span></div>
      <div class="g-bd" v-show="open.upscale">
        <textarea class="mono tail-box bridge-box" v-model="upscaleTpl" @input="onBlockInput('upscale')" @blur="onBlockBlur('upscale')"></textarea>
        <p class="g-hint">{{ t('gemini.upscale_hint') }}</p>
        <div class="g-err" v-if="saveState.upscale.error">{{ saveState.upscale.error }}</div>
        <div class="g-ft">
          <button class="btn ghost sm" @click="resetBlock('upscale')">{{ t('gemini.reset') }}</button>
          <button class="btn sm primary" :disabled="saveState.upscale.saving" @click="saveUpscale">{{ t('gemini.save') }}</button>
        </div>
      </div>
    </article>

    <article class="g-card" id="g-block-bridge" data-block="bridge">
      <div class="g-hd" @click="toggleOpen('bridge')">
        <span class="chev">{{ open.bridge ? '▾' : '▸' }}</span>{{ t('gemini.card.bridge') }}
        <span class="g-save" :class="saveClass('bridge')">{{ saveLabel('bridge') }}</span>
      <span class="g-tool-hint">{{ t('gemini.switches.used_bridge') }}</span></div>
      <div class="g-bd" v-show="open.bridge">
        <textarea class="mono tail-box bridge-box" v-model="bridgeTpl" @input="onBlockInput('bridge')" @blur="onBlockBlur('bridge')"></textarea>
        <p class="g-hint">{{ t('gemini.bridge_hint') }}</p>
        <div class="g-err" v-if="saveState.bridge.error">{{ saveState.bridge.error }}</div>
        <div class="g-ft">
          <button class="btn ghost sm" @click="resetBlock('bridge')">{{ t('gemini.reset') }}</button>
          <button class="btn sm primary" :disabled="saveState.bridge.saving" @click="saveBridge">{{ t('gemini.save') }}</button>
        </div>
      </div>
    </article>

    <article class="g-card" id="g-block-bridge-single" data-block="bridge_single">
      <div class="g-hd" @click="toggleOpen('bridge_single')">
        <span class="chev">{{ open.bridge_single ? '▾' : '▸' }}</span>{{ t('gemini.card.bridge_single') }}
        <span class="g-save" :class="saveClass('bridge_single')">{{ saveLabel('bridge_single') }}</span>
      <span class="g-tool-hint">{{ t('gemini.switches.used_bridge_single') }}</span></div>
      <div class="g-bd" v-show="open.bridge_single">
        <textarea class="mono tail-box bridge-box" v-model="bridgeSingleTpl" @input="onBlockInput('bridge_single')" @blur="onBlockBlur('bridge_single')"></textarea>
        <p class="g-hint">{{ t('gemini.bridge_single_hint') }}</p>
        <div class="g-err" v-if="saveState.bridge_single.error">{{ saveState.bridge_single.error }}</div>
        <div class="g-ft">
          <button class="btn ghost sm" @click="resetBlock('bridge_single')">{{ t('gemini.reset') }}</button>
          <button class="btn sm primary" :disabled="saveState.bridge_single.saving" @click="saveBridgeSingle">{{ t('gemini.save') }}</button>
        </div>
      </div>
    </article>
    <article class="g-card" id="g-block-chain" data-block="chain">
      <div class="g-hd" @click="toggleOpen('chain')">
        <span class="chev">{{ open.chain ? '▾' : '▸' }}</span>{{ t('gemini.card.chain') }}
        <span class="g-save" :class="saveClass('chain')">{{ saveLabel('chain') }}</span>
      <span class="g-tool-hint">{{ t('gemini.switches.used_chain') }}</span></div>
      <div class="g-bd" v-show="open.chain">
        <textarea class="mono tail-box bridge-box" v-model="chainTpl" @input="onBlockInput('chain')" @blur="onBlockBlur('chain')"></textarea>
        <p class="g-hint">{{ t('gemini.chain_hint') }}</p>
        <div class="g-err" v-if="saveState.chain.error">{{ saveState.chain.error }}</div>
        <div class="g-actions">
          <button class="btn ghost sm" @click="resetBlock('chain')">{{ t('gemini.reset') }}</button>
          <button class="btn sm primary" :disabled="saveState.chain.saving" @click="saveChainBlock">{{ t('gemini.save') }}</button>
        </div>
      </div>
    </article>

    <article class="g-card" data-block="tail">
      <div class="g-hd" @click="toggleOpen('tail')">
        <span class="chev">{{ open.tail ? '▾' : '▸' }}</span>{{ t('gemini.card.tail') }}
        <span class="g-tool-hint">{{ t('gemini.switches.used_tail') }}</span>
        <span class="g-save" :class="saveClass('tail')">{{ saveLabel('tail') }}</span>
      </div>
      <div class="g-bd" v-show="open.tail">
        <div class="emotion-preset">
          <div class="tail-key">{{ t('gemini.emotion_preset') }}</div>
          <p class="g-hint">{{ t('gemini.emotion_preset_hint') }}</p>
          <div class="row emotion-preset-btns">
            <button class="btn sm" :class="{ primary: emotionPresetName()==='温暖' }"
                    type="button" @click="setEmotionPreset('温暖')">{{ t('gemini.emotion_warm') }}</button>
            <button class="btn sm" :class="{ primary: emotionPresetName()==='激昂' }"
                    type="button" @click="setEmotionPreset('激昂')">{{ t('gemini.emotion_fiery') }}</button>
          </div>
        </div>
        <div v-for="k in tailStringKeys()" :key="k">
          <div class="tail-key">{{ k }}</div>
          <textarea class="mono tail-box" v-model="tail[k]" @input="onBlockInput('tail')" @blur="onBlockBlur('tail')"></textarea>
        </div>
        <div v-for="k in tailDictKeys()" :key="'d-'+k" class="tail-sub">
          <div class="tail-key">{{ k }}</div>
          <div v-for="sk in dictInnerKeys(k)" :key="k+'.'+sk">
            <div class="tail-key">{{ sk }}</div>
            <textarea class="mono tail-box" v-model="tail[k][sk]" @input="onBlockInput('tail')" @blur="onBlockBlur('tail')"></textarea>
          </div>
        </div>
        <details class="g-notes" v-if="tailNotes">
          <summary>{{ t('gemini.tail_notes') }}</summary>
          <div>{{ tailNotes }}</div>
        </details>
        <div class="g-err" v-if="saveState.tail.error">{{ saveState.tail.error }}</div>
        <div class="g-ft">
          <button class="btn ghost sm" @click="resetBlock('tail')">{{ t('gemini.reset') }}</button>
          <button class="btn sm primary" :disabled="saveState.tail.saving" @click="saveTail">{{ t('gemini.save') }}</button>
        </div>
      </div>
    </article>

    <article class="g-card">
      <div class="g-hd" @click="toggleOpen('problem')">
        <span class="chev">{{ open.problem ? '▾' : '▸' }}</span>{{ t('gemini.card.problem') }}
      </div>
      <div class="g-bd" v-show="open.problem">
        <div class="prob-list">
          <div class="prob-item" v-for="(p,i) in (problem.patterns||[])" :key="i">
            <span class="tag">{{ p.kind==='regex' ? t('gemini.kind.regex') : t('gemini.kind.phrase') }}</span>
            <span class="val">{{ p.value }}</span>
            <span class="tag" v-if="p.lang">{{ p.lang }}</span>
            <span class="tag">{{ sourceLabel(p.source) }}</span>
            <button class="x" :title="t('common.delete')" @click="delPhrase(i)">✕</button>
          </div>
        </div>
        <div class="prob-add">
          <input v-model="newPhrase" :placeholder="t('gemini.phrase_ph')" @keydown.enter.prevent="addPhrase">
          <button class="btn sm" @click="addPhrase">{{ t('gemini.add_phrase') }}</button>
        </div>
        <button class="btn sm" :disabled="!tid || busy.has('scan_problems')" @click="scanProblems">{{ t('gemini.scan') }}</button>
      </div>
    </article>
  </div>

  <div class="col-right">
    <div class="right-hd">
      <h2>{{ t('node.gemini') }}</h2>
      <task-pick v-model="tid" min-status="prepared" :tasks="allTasks"></task-pick>
      <button class="btn" :class="{primary: q.on && !q.foreign}" :disabled="!!q.foreign" :title="q.foreign ? t('gemini.queue.foreign', { port: String(q.owner_port || '') }) : ''" @click="toggleQueue">{{ t('gemini.queue') }}</button>
      <button class="btn primary" :disabled="!tid || busy.has('describe') || (q.on && !q.foreign)" @click="runJob('describe')">{{ t('gemini.describe') }}</button>
      <label class="g-force" :title="t('gemini.force_tip')">
        <input type="checkbox" v-model="forceManual">
        {{ t('gemini.force_manual', { n: String(manualN) }) }}
      </label>
      <button class="btn" :disabled="!tid" @click="openPreview">{{ t('gemini.preview') }}</button>
      <button class="btn sm" :disabled="!tid" @click="openBatchDesc()">{{ t('gemini.batch_desc') }}</button>
    </div>
    <div class="g-qbar" v-if="q.foreign">
      <span>{{ t('gemini.queue.foreign', { port: String(q.owner_port || '') }) }}</span>
    </div>
    <div class="g-qbar" v-else-if="q.on">
      <span>{{ t('gemini.queue') }}</span>
      <span class="dot" :class="{pause: q.paused}"></span>
      <span>{{ q.paused ? t('gemini.queue.paused') : t('gemini.queue.on') }}</span>
      <span>{{ t('gemini.queue.progress', { done: String(q.done||0), total: String(q.total||0) }) }}</span>
      <span v-if="q.current">{{ t('gemini.queue.running', { id: q.current.id || '', name: q.current.name || '' }) }}</span>
      <span v-if="q.current">{{ t('gemini.queue.imgs', { done: String(q.current.written||0), total: String(q.current.total||0) }) }}</span>
      <span class="spacer"></span>
      <button class="btn sm" v-if="!q.paused" @click="qPause(true)">{{ t('gemini.queue.pause') }}</button>
      <button class="btn sm" v-else @click="qPause(false)">{{ t('gemini.queue.resume') }}</button>
      <button class="btn sm" :disabled="!q.current" @click="qSkip">{{ t('gemini.queue.skip') }}</button>
      <button class="btn sm danger" @click="qStop">{{ t('gemini.queue.stop') }}</button>
    </div>
    <div class="g-qsum" v-if="qSummary">
      <span>{{ qSummary }}</span>
      <span class="fail" v-for="f in qFails" :key="f.id" @click="goTask(f.id)">{{ f.id }}：{{ f.error }}</span>
      <button class="btn ghost sm" @click="dismissSummary">✕</button>
    </div>
    <div class="muted" v-if="!tid">{{ t('gemini.no_task') }}</div>
    <div class="muted" v-if="tid && busy.has('describe')">{{ t('gemini.running') }}</div>
    <div class="banner warn g-need" v-if="tid && missingDesc.length">
      <span>{{ t('gemini.need_desc', { n: String(missingDesc.length) }) }}</span>
      <button class="btn sm primary" type="button"
              :disabled="busy.has('describe') || (q.on && !q.foreign)"
              @click="runJob('describe')">{{ t('gemini.need_desc.fill', { n: String(missingDesc.length) }) }}</button>
    </div>
    <template v-if="tid && manifest">
      <div class="wall-hd">
        <h3>{{ t('gemini.wall') }} · {{ subjects.length }}</h3>
        <div class="sel-bar" v-if="selN('subjects')">
          <span>{{ t('gemini.sel.count', { n: String(selN('subjects')) }) }}</span>
          <button class="btn ghost sm" type="button" @click="selAll('subjects')">{{ t('gemini.sel.all') }}</button>
          <button class="btn ghost sm" type="button" @click="selInvert('subjects')">{{ t('gemini.sel.invert') }}</button>
          <button class="btn ghost sm" type="button" @click="selNone('subjects')">{{ t('gemini.sel.none') }}</button>
          <button class="btn sm primary" type="button" @click="syncFromFocus(['desc'])">{{ t('gemini.sel.sync') }}</button>
          <button class="btn sm" type="button" style="border-color:#4a90e2;color:#7ab8ff" @click="openBatchDesc()">🔗 节点连线分配</button>
          <button class="btn sm" type="button" style="border-color:#50b878;color:#7be0a0" @click="autoDistributeWallNodes()">🎲 按组不重复分配动作</button>
        </div>
        <span class="spacer"></span>
        <button class="btn sm" type="button" style="border-color:#4a90e2;color:#7ab8ff" @click="openBatchDesc()">🔗 批量连线描述语节点</button>
        <button class="btn sm" type="button" @click="openRescan">{{ t('rescan.btn') }}</button>
        <expand-ui :tid="tid" :images="images" :expand-acc="expandAcc" :trigger="true"
                   @done="onExpandDone" @prompt="openPromptBlock('expand')"></expand-ui>
      </div>
      <div class="img-wall">
        <article class="img-card" :class="{'is-dup': im.dup_warn || im.stage_warn, hilite: focusP===im.p, checked: isChecked(im, 'subjects')}"
                 :data-p="im.p"
                 v-for="im in subjects" :key="im.p + '-' + (im.mtime || 0)"
                 @click="onCardClick($event, im, 'subjects')"
                 @contextmenu.prevent="openImgMenu($event, im, 'subjects')">
          <div class="thumb">
            <button type="button" class="img-check" :class="{on: isChecked(im, 'subjects')}"
                    @click="onCheckClick($event, im, 'subjects')"></button>
            <img v-if="im.exists !== false" :src="mediaUrl(tid, im.file, im.mtime)" :alt="im.p">
            <div class="ps-hover-bar" v-if="im.exists !== false" @click.stop>
              <button type="button" class="ps-hb-btn" title="✨ 以此图作参考一键扩图" @click.stop="quickImgAct(im, 'expand', $event)">✨扩图</button>
              <button type="button" class="ps-hb-btn" title="🔍 提升画质 / 超分重绘" @click.stop="quickImgAct(im, 'upscale', $event)">🔍超分</button>
              <button type="button" class="ps-hb-btn" title="↔ 水平镜像翻转" @click.stop="quickImgAct(im, 'flip', $event)">↔翻转</button>
              <button type="button" class="ps-hb-btn" title="✂ 裁切构图" @click.stop="quickImgAct(im, 'crop', $event)">✂裁切</button>
              <button type="button" class="ps-hb-btn ps-hb-eng" :class="{studio: expandAcc && expandAcc.id==='STUDIO'}"
                      :title="'点击切换生图引擎（当前：' + ((expandAcc && expandAcc.id==='STUDIO') ? '本地 AI · FLUX.2 (可选项)' : '优先付费 Flow/Grok (默认)') + '）'"
                      @click.stop="quickImgAct(im, 'engine', $event)">
                {{ (expandAcc && expandAcc.id==='STUDIO') ? '🖥️本地' : '⚡付费' }}
              </button>
            </div>
            <div v-else class="missing">
              <b>{{ t('gemini.missing') }}</b>
              <div class="miss-acts">
                <button class="btn sm" type="button" @click="openRescan">{{ t('rescan.missing_rescan') }}</button>
                <button class="btn sm" type="button" @click="openAdopt(im)">{{ t('rescan.missing_pick') }}</button>
                <button class="btn sm danger" type="button" @click="dropMissing(im)">{{ t('rescan.missing_drop') }}</button>
              </div>
            </div>
          </div>
          <div class="meta">
            <div class="meta-hd">
              <span class="mono">{{ im.p }}</span>
              <span v-if="im.desc_manual" class="desc-manual" :title="t('gemini.manual_tip')">✎</span>
              <span v-if="nodeLabelOf(im)" class="tag node-pill"
                    :style="{borderColor: nodeColorOf(im.prompt_node_id), color: nodeColorOf(im.prompt_node_id)}"
                    title="点击打开节点连线面板切换描述语节点"
                    @click.stop="openBatchDesc(im.prompt_node_id)">🔗 {{ nodeLabelOf(im) }}</span>
              <span class="tag tail" v-if="im.origin==='tail'" :title="t('gemini.tail_tip')">{{ t('gemini.tail_of', { s: 's'+String(im.tail_of||0).padStart(3,'0') }) }}</span>
              <span class="tag dup" v-if="im.dup_warn">{{ t('gemini.cov.dup') }}</span>
              <span class="tag dup" v-if="im.stage_warn">{{ t('gemini.cov.stage_fail') }}</span>
              <select class="tag" :class="'role-'+im.role" :value="im.role" @change="onRoleChange(im, $event)">
                <option value="主体">{{ t('gemini.role.主体') }}</option>
                <option value="空镜">{{ t('gemini.role.空镜') }}</option>
              </select>
            </div>
            <div class="desc-box">
              <textarea class="desc-edit" :value="draftOf(im,'desc')" :placeholder="t('gemini.desc_ph')" @input="onDescInput(im, $event)" @blur="onDescBlur(im, $event)"></textarea>
              <span class="g-save" :class="fieldSaveClass(im,'desc')" :title="fieldSaveTitle(im,'desc')">{{ fieldSaveLabel(im,'desc') }}</span>
            </div>
            <div class="voice-box">
              <input class="voice-edit" :value="draftOf(im,'voice')" :placeholder="t('gemini.voice_empty')" @input="onVoiceInput(im, $event)" @blur="onVoiceBlur(im, $event)">
              <span class="g-save" :class="fieldSaveClass(im,'voice')" :title="fieldSaveTitle(im,'voice')">{{ fieldSaveLabel(im,'voice') }}</span>
            </div>
            <div class="desc-stale" v-if="im.desc_stale && im.desc_manual">
              <span>{{ t('gemini.stale.tip') }}</span>
              <button type="button" class="btn sm" @click.stop="rewriteDesc(im)">{{ t('gemini.stale.rewrite') }}</button>
            </div>
            <div class="meas cov" v-if="covLine(im)">{{ covLine(im) }}</div>
            <div class="meas">{{ measureLine(im) || t('gemini.measure_empty') }}</div>
          </div>
        </article>
      </div>
      <div class="broll-sec" v-if="brolls.length">
        <div class="wall-hd">
          <h3>{{ t('gemini.broll') }} · {{ brolls.length }}</h3>
          <div class="sel-bar" v-if="selN('brolls')">
            <span>{{ t('gemini.sel.count', { n: String(selN('brolls')) }) }}</span>
            <button class="btn ghost sm" type="button" @click="selAll('brolls')">{{ t('gemini.sel.all') }}</button>
            <button class="btn ghost sm" type="button" @click="selInvert('brolls')">{{ t('gemini.sel.invert') }}</button>
            <button class="btn ghost sm" type="button" @click="selNone('brolls')">{{ t('gemini.sel.none') }}</button>
            <button class="btn sm primary" type="button" @click="syncFromFocus(['desc'])">{{ t('gemini.sel.sync') }}</button>
          </div>
        </div>
        <div class="img-wall">
          <article class="img-card" :class="{hilite: focusP===im.p, checked: isChecked(im, 'brolls')}"
                   :data-p="im.p"
                   v-for="im in brolls" :key="im.p + '-' + (im.mtime || 0)"
                   @click="onCardClick($event, im, 'brolls')"
                   @contextmenu.prevent="openImgMenu($event, im, 'brolls')">
            <div class="thumb">
              <button type="button" class="img-check" :class="{on: isChecked(im, 'brolls')}"
                      @click="onCheckClick($event, im, 'brolls')"></button>
              <img v-if="im.exists !== false" :src="mediaUrl(tid, im.file, im.mtime)" :alt="im.p">
              <div v-else class="missing">
                <b>{{ t('gemini.missing') }}</b>
                <div class="miss-acts">
                  <button class="btn sm" type="button" @click="openRescan">{{ t('rescan.missing_rescan') }}</button>
                  <button class="btn sm" type="button" @click="openAdopt(im)">{{ t('rescan.missing_pick') }}</button>
                  <button class="btn sm danger" type="button" @click="dropMissing(im)">{{ t('rescan.missing_drop') }}</button>
                </div>
              </div>
            </div>
            <div class="meta">
              <div class="meta-hd">
                <span class="mono">{{ im.p }}</span>
                <span v-if="im.desc_manual" class="desc-manual" :title="t('gemini.manual_tip')">✎</span>
                <span v-if="nodeLabelOf(im)" class="tag node-pill"
                      :style="{borderColor: nodeColorOf(im.prompt_node_id), color: nodeColorOf(im.prompt_node_id)}"
                      @click.stop="openBatchDesc(im.prompt_node_id)">🔗 {{ nodeLabelOf(im) }}</span>
                <span class="tag tail" v-if="im.origin==='tail'" :title="t('gemini.tail_tip')">{{ t('gemini.tail_of', { s: 's'+String(im.tail_of||0).padStart(3,'0') }) }}</span>
                <select class="tag" :class="'role-'+im.role" :value="im.role" @change="onRoleChange(im, $event)">
                  <option value="主体">{{ t('gemini.role.主体') }}</option>
                  <option value="空镜">{{ t('gemini.role.空镜') }}</option>
                </select>
              </div>
              <div class="desc-box">
                <textarea class="desc-edit" :value="draftOf(im,'desc')" :placeholder="t('gemini.desc_ph')" @input="onDescInput(im, $event)" @blur="onDescBlur(im, $event)"></textarea>
                <span class="g-save" :class="fieldSaveClass(im,'desc')" :title="fieldSaveTitle(im,'desc')">{{ fieldSaveLabel(im,'desc') }}</span>
              </div>
              <div class="voice-box">
                <input class="voice-edit" :value="draftOf(im,'voice')" :placeholder="t('gemini.voice_empty')" @input="onVoiceInput(im, $event)" @blur="onVoiceBlur(im, $event)">
                <span class="g-save" :class="fieldSaveClass(im,'voice')" :title="fieldSaveTitle(im,'voice')">{{ fieldSaveLabel(im,'voice') }}</span>
              </div>
              <div class="desc-stale" v-if="im.desc_stale && im.desc_manual">
                <span>{{ t('gemini.stale.tip') }}</span>
                <button type="button" class="btn sm" @click.stop="rewriteDesc(im)">{{ t('gemini.stale.rewrite') }}</button>
              </div>
              <div class="meas">{{ measureLine(im) || t('gemini.measure_empty') }}</div>
            </div>
          </article>
        </div>
      </div>
      <div class="empty" v-if="!images.length">{{ t('gemini.no_images') }}</div>
    </template>
  </div>

  <ul v-if="menu.show && menu.im" class="ctx" ref="menuRef"
      :style="{left: menu.x + 'px', top: menu.y + 'px'}"
      @click.stop>
    <li :class="{off: menuOff('open')}" :title="menuTitle('open')" @click="onImgMenu('open')">{{ t('gemini.ctx.open') }}</li>
    <li :class="{off: menuOff('reveal')}" :title="menuTitle('reveal')" @click="onImgMenu('reveal')">{{ t('gemini.ctx.reveal') }}</li>
    <li class="sep"></li>
    <li @click="onImgMenu('toggle')">{{ (sel[menu.group]||[]).indexOf(menu.im.p)>=0 ? t('gemini.ctx.unselect') : t('gemini.ctx.select') }}</li>
    <li @click="onImgMenu('all')">{{ t('gemini.ctx.all', { n: String((menu.group==='brolls'?brolls:subjects).length) }) }}</li>
    <li @click="onImgMenu('none')">{{ t('gemini.ctx.none') }}</li>
    <li class="sep"></li>
    <li class="ctx-sec-hd">🔗 右键批量连线到描述语节点 ({{ (sel[menu.group]||[]).length || 1 }} 张)</li>
    <li v-for="nd in promptNodes" :key="'ctx-'+nd.id" class="ctx-node-item" @click="connectWallImgsToNode(nd.id)">
      <span class="ctx-node-dot" :style="{background: nd.color}"></span>
      <b>[{{ nd.id }}]</b> {{ nd.name }}
    </li>
    <li style="color:#7be0a0" @click="autoDistributeWallNodes()">🎲 按组不重复智能分配节点</li>
    <li class="sep"></li>
    <li :class="{off: menuOff('syncDesc')}" :title="menuTitle('syncDesc')" @click="onImgMenu('syncDesc')">{{ t('gemini.ctx.sync_desc', { n: String(menuSyncN()) }) }}</li>
    <li :class="{off: menuOff('syncVoice')}" :title="menuTitle('syncVoice')" @click="onImgMenu('syncVoice')">{{ t('gemini.ctx.sync_voice', { n: String(menuSyncN()) }) }}</li>
    <li :class="{off: menuOff('syncBoth')}" :title="menuTitle('syncBoth')" @click="onImgMenu('syncBoth')">{{ t('gemini.ctx.sync_both') }}</li>
    <li class="sep"></li>
    <li @click="onImgMenu('copyDesc')">{{ t('gemini.ctx.copy_desc') }}</li>
    <li @click="openBatchDesc((menu.im && menu.im.prompt_node_id) || '')">🔗 打开节点连线批量描述语面板…</li>
    <li :class="{off: menuOff('restoreAuto')}" :title="menuTitle('restoreAuto')" @click="onImgMenu('restoreAuto')">{{ t('gemini.ctx.restore_auto') }}</li>
    <li class="sep"></li>
    <li class="bad" :class="{off: menuOff('clear')}" :title="menuTitle('clear')" @click="onImgMenu('clear')">{{ t('gemini.ctx.clear') }}</li>
  </ul>

  <div v-if="previewOn">
    <div class="drawer-mask" @click="previewOn=false"></div>
    <aside class="drawer preview-drawer">
      <div class="hd">
        <h2>{{ t('gemini.preview.title') }} <span class="g-preview-stats">{{ t('gemini.preview.bricks', previewBrickStats) }}</span></h2>
        <select v-model="previewSeg" style="width:120px">
          <option v-for="sg in segments" :key="sg.s" :value="String(sg.s)">{{ segLabel(sg.s) }}</option>
        </select>
        <button class="btn sm" @click="copyPreview">{{ t('gemini.preview.copy') }}</button>
        <button class="btn ghost sm" @click="previewOn=false">✕</button>
      </div>
      <div class="bd">
        <textarea class="mono" :class="{'g-preview-hi': !!previewHi}" readonly :value="previewText"></textarea>
      </div>
    </aside>
  </div>

  <div class="g-batch-mask batch-desc" v-if="batchDlg" @click.self="closeBatchDesc()">
    <div class="g-batch-dlg cutora-node-dlg" @click="batchCtx.show=false">
      <div class="hd">
        <b>🔗 {{ t('gemini.batch_desc.title') }} · Cutora 式图片-文字节点连线工作台</b>
        <span class="hint" style="margin-left:12px">左选多张图片（或右键图片）→ 连线到右侧带编号 [D01~D08] 描述语节点，一对多批量复用且互不雷同</span>
        <span class="spacer"></span>
        <button class="btn sm" style="border-color:#50b878;color:#7be0a0;margin-right:8px" @click="autoDistributeBatchNodes()">🎲 智能按组不重复连线</button>
        <button class="btn sm" style="border-color:#4a90e2;color:#7ab8ff;margin-right:8px" @click="addPromptNode()">＋ 新增描述语节点</button>
        <button class="btn ghost sm" @click="closeBatchDesc()">✕</button>
      </div>
      <div class="body batch-graph-body" ref="batchBodyRef">
        <svg class="batch-wire-svg" aria-hidden="true">
          <path v-for="w in batchWires" :key="w.key" :d="w.d"
                :stroke="w.color" stroke-width="2" fill="none" stroke-opacity="0.72"
                stroke-dasharray="none"></path>
        </svg>
        <div class="col col-tasks" @scroll="updateBatchWires">
          <input class="batch-search" :placeholder="t('gemini.batch_desc.search')" v-model="batchDlg.filter">
          <label class="batch-only-miss"><input type="checkbox" v-model="batchDlg.onlyMissing"> {{ t('gemini.batch_desc.only_missing') }}</label>
          <div class="batch-sel-row">
            <button type="button" class="btn ghost sm" @click="batchSelectAll">{{ t('gemini.batch_desc.select_all') }}</button>
            <button type="button" class="btn ghost sm" @click="batchInvert">{{ t('gemini.batch_desc.invert') }}</button>
          </div>
          <div class="task-row" v-for="tk in batchTasksShown" :key="tk.tid"
               :class="{active: batchDlg.active===tk.tid, draft: !tk.selectable}"
               @click="tk.selectable && (batchDlg.active=tk.tid, $nextTick(updateBatchWires))">
            <input type="checkbox" v-if="tk.selectable" v-model="tk.checked" @click.stop>
            <span>{{ tk.tid }} · {{ tk.name }}</span>
            <span class="dot" v-if="tk.missingDot"></span>
            <span v-if="!tk.selectable" class="hint">{{ t('gemini.batch_desc.draft_hint') }}</span>
          </div>
        </div>
        <div class="col col-mid" @scroll="updateBatchWires">
          <div class="batch-mid-hd" v-if="activeBatchTask()">
            <b>{{ activeBatchTask().tid }} · {{ activeBatchTask().name }}</b>
            <span class="hint">{{ t('gemini.batch_desc.img_count', activeBatchImgCount()) }} · 支持多选后右键一键连线到指定编号节点</span>
          </div>
          <div class="batch-mid-actions">
            <button class="btn ghost sm" @click="selectAllImgs">{{ t('gemini.batch_desc.select_all') }}</button>
            <button class="btn ghost sm" @click="selectNoneImgs">{{ t('gemini.batch_desc.select_none') }}</button>
            <button class="btn ghost sm" @click="selectMissingImgs">{{ t('gemini.batch_desc.only_sel_missing') }}</button>
            <button class="btn ghost sm" @click="autoDistributeBatchNodes">🎲 智能分配</button>
          </div>
          <div class="thumbs cutora-img-nodes" v-if="batchDlg.active">
            <div class="thumb cutora-im-card" v-for="im in (activeBatchTask() && activeBatchTask().images) || []" :key="im.p"
                 :data-batch-im="im.p"
                 :class="{on: (batchDlg.sel[batchDlg.active]||[]).includes(im.p), linked: !!batchImgNodeId(im)}"
                 :style="batchImgNodeId(im) ? {borderColor: nodeColorOf(batchImgNodeId(im))} : {}"
                 @click="toggleBatchImg(im.p)"
                 @contextmenu.prevent.stop="openBatchImgCtx($event, im)">
              <input class="ck" type="checkbox" :checked="(batchDlg.sel[batchDlg.active]||[]).includes(im.p)" @click.stop="toggleBatchImg(im.p, $event.target.checked)">
              <img v-if="im.url" :src="im.url" :alt="im.p">
              <div v-else class="thumb-ph">{{ im.p }}</div>
              <div class="im-node-foot">
                <b class="mono">{{ im.p }}</b>
                <span v-if="batchImgNodeId(im)" class="im-node-badge"
                      :style="{background: nodeColorOf(batchImgNodeId(im))}"
                      @click.stop="unlinkBatchImg(im.p)"
                      title="点击断开该节点连线">
                  {{ batchImgNodeId(im) }} ✕
                </span>
                <span v-else class="im-node-unlinked">未连线</span>
              </div>
              <div class="desc-prev" :title="im.desc">{{ (im.desc||'').slice(0,22) }}</div>
              <span class="port-dot port-out"
                    :style="{background: batchImgNodeId(im) ? nodeColorOf(batchImgNodeId(im)) : '#666'}"
                    title="右键或点击右侧节点『连入已选图』建立连线"></span>
            </div>
          </div>
        </div>
        <div class="col col-right cutora-prompt-col" @scroll="updateBatchWires">
          <div class="prompt-nodes-hd">
            <b>右侧文字描述语节点 ({{ promptNodes.length }})</b>
            <span class="hint">点击『🔗 连入已选图』将左侧勾选图片批量连入该编号节点</span>
          </div>
          <div class="prompt-node-list">
            <div class="prompt-node-card" v-for="nd in promptNodes" :key="nd.id"
                 :data-batch-node="nd.id"
                 :class="{active: batchDlg.activeNodeId===nd.id}"
                 :style="{borderColor: batchDlg.activeNodeId===nd.id ? nd.color : ''}"
                 @click="batchDlg.activeNodeId=nd.id; batchDlg.desc=nd.desc">
              <span class="port-dot port-in" :style="{background: nd.color}" @click.stop="connectBatchSelToNode(nd.id)" title="点击将左侧已选图片连接到此节点"></span>
              <div class="pnode-hd">
                <span class="pnode-id" :style="{background: nd.color}">{{ nd.id }}</span>
                <input class="pnode-name" v-model="nd.name" @blur="savePromptNodesQuiet" @click.stop>
                <span class="pnode-cnt" title="当前已连入此节点的图片数量">已连 {{ batchNodeConnectedPs(nd.id).length }} 张</span>
                <button type="button" class="btn sm pnode-link-btn"
                        :style="{borderColor: nd.color, color: nd.color}"
                        @click.stop="connectBatchSelToNode(nd.id)">
                  🔗 连入已选图 ({{ activeBatchImgCount().sel }})
                </button>
              </div>
              <div class="pnode-ps" v-if="batchNodeConnectedPs(nd.id).length">
                已连图片：{{ batchNodeConnectedPs(nd.id).join(', ') }}
              </div>
              <textarea class="pnode-desc" v-model="nd.desc"
                        @input="batchDlg.activeNodeId===nd.id && (batchDlg.desc=nd.desc)"
                        @blur="savePromptNodesQuiet"
                        @click.stop></textarea>
            </div>
          </div>
          <div class="batch-Global-opts">
            <label class="batch-mode"><input type="radio" value="fill_empty" v-model="batchDlg.mode"> {{ t('gemini.batch_desc.fill_empty') }}</label>
            <label class="batch-mode"><input type="radio" value="overwrite" v-model="batchDlg.mode"> {{ t('gemini.batch_desc.overwrite') }}</label>
          </div>
        </div>
      </div>
      <ul v-if="batchCtx.show" class="ctx batch-inner-ctx"
          :style="{left: batchCtx.x + 'px', top: batchCtx.y + 'px'}"
          @click.stop>
        <li class="ctx-sec-hd">🔗 将已选 {{ activeBatchImgCount().sel }} 张图片连线到节点：</li>
        <li v-for="nd in promptNodes" :key="'bctx-'+nd.id" class="ctx-node-item" @click="connectBatchSelToNode(nd.id)">
          <span class="ctx-node-dot" :style="{background: nd.color}"></span>
          <b>[{{ nd.id }}]</b> {{ nd.name }}
        </li>
        <li class="sep"></li>
        <li style="color:#7be0a0" @click="autoDistributeBatchNodes(); batchCtx.show=false">🎲 智能按组不重复分配所有图片</li>
      </ul>
      <div class="ft">
        <button class="btn ghost" @click="closeBatchDesc()">{{ t('gemini.batch_desc.cancel') }}</button>
        <button class="btn primary" @click="runBatchDesc()">✅ 按节点连线批量写入描述语</button>
      </div>
    </div>
  </div>
</section>`
  };
})();
