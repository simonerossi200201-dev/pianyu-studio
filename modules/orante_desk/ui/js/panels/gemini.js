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

// ─── 需求 5：预设校验与最大换行处【人物说的话】红字插槽解析引擎 ───
window.PresetSlotLogic = (function () {
  function analyzeTemplate(rawTemplate) {
    const tpl = String(rawTemplate == null ? '' : rawTemplate).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    const trimmed = tpl.trim();
    if (!trimmed) {
      return {
        hasContent: false,
        hasNewline: false,
        maxNewlines: 0,
        prefix: '',
        suffix: '',
      };
    }
    const re = /\n+/g;
    let m;
    let bestLen = 0;
    let bestIdx = -1;
    while ((m = re.exec(tpl)) !== null) {
      const len = m[0].length;
      if (len > bestLen) {
        bestLen = len;
        bestIdx = m.index;
      }
    }
    if (bestIdx < 0) {
      return {
        hasContent: true,
        hasNewline: false,
        maxNewlines: 0,
        prefix: tpl,
        suffix: '',
      };
    }
    return {
      hasContent: true,
      hasNewline: true,
      maxNewlines: bestLen,
      prefix: tpl.slice(0, bestIdx),
      suffix: tpl.slice(bestIdx + bestLen),
    };
  }

  function validatePresetRow(row) {
    const name = String((row && row.name) || '').trim();
    const tpl = String((row && row.template) || '').trim();
    const role = String((row && row.role) || 'main');
    if (!name && !tpl) {
      return { empty: true, valid: true, missingName: false, missingTpl: false, noNewlineWarn: false, error: '' };
    }
    if (!name && tpl) {
      return {
        empty: false,
        valid: false,
        missingName: true,
        missingTpl: false,
        noNewlineWarn: false,
        error: '❌ 缺少预设名称',
      };
    }
    if (name && !tpl) {
      return {
        empty: false,
        valid: false,
        missingName: false,
        missingTpl: true,
        noNewlineWarn: false,
        error: '❌ 缺少描述词内容',
      };
    }
    const slot = analyzeTemplate(row.template);
    const needsSlot = role === 'main' || role === '' || !role;
    if (needsSlot && !slot.hasNewline) {
      return {
        empty: false,
        valid: false,
        missingName: false,
        missingTpl: false,
        noNewlineWarn: true,
        error: '⚠️ 请在要填写内容的地方添加换行',
        slot,
      };
    }
    return {
      empty: false,
      valid: true,
      missingName: false,
      missingTpl: false,
      noNewlineWarn: false,
      error: '',
      slot,
    };
  }

  function composeSpeech(template, speechText) {
    const slot = analyzeTemplate(template);
    const sp = String(speechText == null ? '' : speechText).trim();
    if (!slot.hasContent) return sp;
    if (!slot.hasNewline) return sp ? (slot.prefix + '\n' + sp) : slot.prefix;
    return slot.prefix + '\n' + (sp || '【人物说的话】') + '\n' + slot.suffix;
  }

  return { analyzeTemplate, validatePresetRow, composeSpeech };
})();

// ─── 节点 3 提示词面板：Less is More 双核架构（📚 预设管理 + 📝 任务提示词总表 + 🎴 预设卡片库） ───
window.Panel_gemini = (() => {
  const TASK_COLORS = ['#d4a24c', '#3b82f6', '#10b981', '#a855f7', '#ec4899', '#f97316', '#06b6d4', '#84cc16'];
  let _rowSeq = 0;
  function nextUid() { return 'pr_' + Date.now().toString(36) + '_' + (++_rowSeq); }
  function pad3(n) { return String(n == null ? '' : n).padStart(3, '0'); }

  return {
    name: 'Panel_gemini',
    setup() {
      const { ref, reactive, computed, onMounted, onBeforeUnmount, watch } = Vue;

      // Top view mode: 'table' (任务提示词总表) | 'presets' (预设管理) | 'split' (双栏同屏)
      const viewMode = ref(ui.promptBlock === 'expand' ? 'presets' : 'table');
      if (ui.promptBlock === 'expand') ui.promptBlock = '';

      // ─── Part A: 📚 预设管理 (Google Sheet 3列极简标准表格 + 右键菜单同色外框分组) ───
      const presetRows = ref([]);
      const presetFilterCat = ref('all');
      const presetSearch = ref('');
      const presetSelected = reactive(new Set());
      const presetDrag = reactive({ active: false, val: true });
      const presetSaving = ref(false);
      const findReplaceModal = reactive({ open: false, find: '', replace: '', target: 'presets' });

      // Preset table column widths & row heights
      const presetColWidths = reactive({
        color: 88,
        name: 230,
        template: 760,
      });
      const presetRowHeights = reactive({}); // row._uid -> px
      const presetEditingCell = reactive({ rIdx: -1, cIdx: -1 });

      // Preset table 2D cell selection & fill handle
      const presetGridSel = reactive({
        selecting: false,
        filling: false,
        startRow: -1,
        startCol: -1,
        endRow: -1,
        endCol: -1,
        fillEndRow: -1,
      });

      // Preset Right-Click Context Menu (Grouping & Row operations)
      const presetCtxMenu = reactive({
        open: false,
        x: 0,
        y: 0,
      });

      // Group color map (category name -> border color)
      const groupColorMap = reactive({});

      // ─── Part B: 📝 任务提示词总表 (Google Sheet 标准单元格 + 默认隐藏任务行 + 双击富文本编辑 + 换行/截断 + 悬停预览) ───
      const tasksTable = ref([]);
      const extraColumns = ref([]); // [{ col_id, name, position, header_mode, header_preset_id, header_group }]
      const mainColControl = reactive({
        header_mode: 'single', // 'single' | 'gemini_template' | 'random_group'
        header_preset_id: '',
        header_group: '',
      });
      const collapsedTasks = reactive({});
      const segChecked = reactive({}); // `${taskId}:${s}` -> bool
      const segDrag = reactive({ active: false, val: true });
      const selectedCells = reactive(new Set());
      const dockSearch = ref('');
      const dockCat = ref('all');
      const dockOpen = ref(false);
      const tableSearch = ref('');
      const onlyCurrentTask = ref(false);
      const draggingPreset = ref(null);

      // Default hide parent task rows, keeping colored outer border per task group
      const showTaskHeaderRows = ref(false);
      // Default wrap text inside cells; toggleable to 'clip' (overflow hidden/ellipsis)
      const textWrapMode = ref('wrap'); // 'wrap' | 'clip'

      // Task Prompt Table column widths & row heights
      const tableColWidths = reactive({
        sel: 42,
        s: 88,
        image: 76,
        duration: 64,
        text: 280,
        preset: 150,
        main: 440,
      });
      const tableRowHeights = reactive({}); // `${taskId}:${s}` -> px
      const tableEditingCell = reactive({ rIdx: -1, cIdx: -1, key: '' });

      // Task Prompt Table 2D cell selection & fill handle
      const tableGridSel = reactive({
        selecting: false,
        filling: false,
        startRow: -1,
        startCol: -1,
        endRow: -1,
        endCol: -1,
        fillEndRow: -1,
      });

      // Floating cell hover preview popover (shared across both tables)
      const hoverPopover = reactive({
        visible: false,
        x: 0,
        y: 0,
        title: '',
        content: '',
      });

      // Column context menu (right-click to insert left/right, rename, delete)
      const colMenu = reactive({
        open: false,
        x: 0,
        y: 0,
        colId: 'main',
        colIndex: -1,
      });

      // Gemini API Key & 看图写描述语 modal
      const geminiCfgModal = reactive({
        open: false,
        apiKey: '',
        model: 'gemini-3.5-flash-lite',
        saving: false,
      });

      const presetCategories = computed(() => {
        const set = new Set(['默认分组', '口播主词', '运镜动作', '扩图预设', '固定结语']);
        for (const p of presetRows.value) {
          if (p && p.category && String(p.category).trim()) set.add(String(p.category).trim());
        }
        for (const c of (store.promptCategories || [])) {
          if (c) set.add(typeof c === 'string' ? c : (c.name || ''));
        }
        return Array.from(set).filter(Boolean);
      });

      const validPublishedPresets = computed(() => {
        const list = [];
        for (const r of presetRows.value) {
          const v = PresetSlotLogic.validatePresetRow(r);
          if (!v.empty && r.name && r.template) {
            list.push(Object.assign({}, r, {
              id: r.id || r._uid,
              valid: v.valid,
              error: v.error,
              slot: v.slot,
            }));
          }
        }
        return list.length ? list : (store.promptPresets || []);
      });

      const filteredPresetRows = computed(() => {
        const q = presetSearch.value.trim().toLowerCase();
        return presetRows.value.filter(r => {
          if (!r) return false;
          if (presetFilterCat.value !== 'all' && (r.category || '默认分组') !== presetFilterCat.value) return false;
          if (q) {
            const hay = `${r.name || ''} ${r.category || ''} ${r.template || ''}`.toLowerCase();
            if (!hay.includes(q)) return false;
          }
          return true;
        });
      });

      const dockPresets = computed(() => {
        const q = dockSearch.value.trim().toLowerCase();
        return validPublishedPresets.value.filter(p => {
          if (!p) return false;
          if (dockCat.value !== 'all' && (p.category || '默认分组') !== dockCat.value) return false;
          if (q) {
            const hay = `${p.name || ''} ${p.category || ''} ${p.template || ''}`.toLowerCase();
            if (!hay.includes(q)) return false;
          }
          return true;
        });
      });

      const invalidPresetCount = computed(() => {
        let n = 0;
        for (const r of presetRows.value) {
          const v = PresetSlotLogic.validatePresetRow(r);
          if (!v.empty && !v.valid) n++;
        }
        return n;
      });

      const visibleTasksInTable = computed(() => {
        const q = tableSearch.value.trim().toLowerCase();
        return tasksTable.value.filter(tk => {
          if (!tk) return false;
          if (onlyCurrentTask.value && ui.panelTid && tk.id !== ui.panelTid) return false;
          if (q) {
            const hayTask = `${tk.id || ''} ${tk.name || ''} ${tk.account || ''}`.toLowerCase();
            const matchSeg = (tk.segments || []).some(sg =>
              `${sg.text || ''} ${sg.custom_prompt || ''} ${sg.image || ''}`.toLowerCase().includes(q)
            );
            if (!hayTask.includes(q) && !matchSeg) return false;
          }
          return true;
        });
      });

      const checkedTargets = computed(() => {
        const out = [];
        for (const tk of visibleTasksInTable.value) {
          for (const sg of (tk.segments || [])) {
            if (segChecked[tk.id + ':' + sg.s]) {
              out.push({ task_id: tk.id, s: sg.s, seg: sg, task: tk });
            }
          }
        }
        return out;
      });

      function makeEmptyPresetRow(cat) {
        const groupName = cat !== undefined ? cat : (presetFilterCat.value !== 'all' ? presetFilterCat.value : '');
        const groupColor = (groupName && groupColorMap[groupName]) || '#d4a24c';
        return {
          _uid: nextUid(),
          id: '',
          category: groupName,
          color: groupColor,
          name: '',
          role: 'main',
          flow_mode: 'auto',
          template: '',
        };
      }

      async function loadPresets(silent = false) {
        const r = await window.refreshPromptPresets({ silent });
        const list = (r && Array.isArray(r.presets)) ? r.presets : (store.promptPresets || []);
        if (list.length) {
          presetRows.value = list.map((p, i) => {
            const cat = (p.category && p.category !== '默认分组') ? p.category : (p.category || '');
            const col = p.color || TASK_COLORS[i % TASK_COLORS.length];
            if (cat && !groupColorMap[cat]) groupColorMap[cat] = col;
            return {
              _uid: p.id || nextUid(),
              id: p.id || ('preset_' + (i + 1)),
              category: cat,
              color: col,
              name: p.name || '',
              role: p.role || 'main',
              flow_mode: p.flow_mode || 'auto',
              template: p.template || '',
            };
          });
        } else if (!presetRows.value.length) {
          groupColorMap['口播主词'] = '#d4a24c';
          groupColorMap['扩图预设'] = '#10b981';
          presetRows.value = [
            {
              _uid: nextUid(),
              id: 'preset_default_1',
              category: '口播主词',
              color: '#d4a24c',
              name: '自然口播·眼神交流',
              role: 'main',
              flow_mode: 'frames',
              template: '画面中的人物自然看向镜头，表情生动自然，口型清晰地说道：\n\n\n镜头保持平稳推进，光影自然柔和，高画质细节。',
            },
            {
              _uid: nextUid(),
              id: 'preset_default_2',
              category: '口播主词',
              color: '#d4a24c',
              name: '情绪感染·手势配合',
              role: 'main',
              flow_mode: 'frames',
              template: '人物带着自信微笑配合自然手势，清晰流利地开口说：\n\n\n背景保持稳定不闪烁，浅景深电影质感。',
            },
            {
              _uid: nextUid(),
              id: 'preset_expand_1',
              category: '扩图预设',
              color: '#10b981',
              name: '同人物多角度扩图',
              role: 'expand',
              flow_mode: 'ingredients',
              template: '保持参考图中人物的面部特征、发型与服装完全一致，切换为中景微侧视角，自然环境光。\n\n保持高解析度真实摄影质感。',
            },
          ];
        }
      }

      async function loadTasksSegmentsTable(silent = false) {
        try {
          const r = await API.get('/api/tasks/all-segments-table', { silent });
          if (r && Array.isArray(r.tasks)) {
            tasksTable.value = r.tasks;
            if (Array.isArray(r.extra_columns) && r.extra_columns.length) {
              extraColumns.value = r.extra_columns.map(c => ({
                col_id: c.col_id || ('col_' + nextUid()),
                name: c.name || '附加提示词列',
                position: c.position || 'right',
                header_mode: c.header_mode || 'single',
                header_preset_id: c.header_preset_id || '',
                header_group: c.header_group || '',
              }));
            }
            r.tasks.forEach(tk => {
              if (collapsedTasks[tk.id] === undefined) collapsedTasks[tk.id] = !!tk.collapsed;
              (tk.segments || []).forEach(sg => {
                if (!sg.extra_prompts) sg.extra_prompts = {};
                const k = tk.id + ':' + sg.s;
                if (segChecked[k] === undefined) segChecked[k] = sg.checked !== false;
              });
            });
          }
        } catch (e) { /* ignore */ }
      }

      function validateRow(row) {
        return PresetSlotLogic.validatePresetRow(row);
      }

      function addPresetRows(n = 1) {
        for (let i = 0; i < n; i++) {
          presetRows.value.push(makeEmptyPresetRow());
        }
      }

      function deleteSelectedPresets() {
        const selSet = getSelectedPresetUids();
        if (!selSet.size) {
          toast('请先选中要删除的预设行', '', 'warn');
          return;
        }
        presetRows.value = presetRows.value.filter(r => !selSet.has(r._uid));
        presetSelected.clear();
        presetGridSel.startRow = -1;
        presetGridSel.endRow = -1;
        if (!presetRows.value.length) addPresetRows(1);
      }

      async function publishPresets() {
        const nonEmpty = [];
        for (let i = 0; i < presetRows.value.length; i++) {
          const r = presetRows.value[i];
          const v = validateRow(r);
          if (v.empty) continue;
          if (v.missingName || v.missingTpl) {
            toast(`第 ${i + 1} 行校验未通过：${v.error}`, '请补全红框高亮的必填单元格后再发布', 'bad');
            return;
          }
          if (v.noNewlineWarn) {
            toast(`第 ${i + 1} 行【${r.name}】：请在要填写内容的地方添加换行`, '主提示词需要通过换行位置标识【人物说的话】插入点', 'bad');
            return;
          }
          nonEmpty.push({
            id: r.id || ('preset_' + (i + 1) + '_' + Date.now().toString(36)),
            name: String(r.name).trim(),
            category: String(r.category || '默认分组').trim(),
            role: r.role || 'main',
            color: r.color || '#d4a24c',
            flow_mode: r.flow_mode || 'auto',
            template: r.template || '',
            sort_order: i,
          });
        }
        presetSaving.value = true;
        try {
          const res = await API.post('/api/prompt-presets/batch-save', { presets: nonEmpty });
          if (res && Array.isArray(res.presets)) {
            store.promptPresets = res.presets;
          } else {
            store.promptPresets = nonEmpty;
          }
          toast(`🚀 已保存 ${nonEmpty.length} 条预设，已同步至 2 排镜头 与预设库`, '', 'ok');
          await loadPresets(true);
        } catch (e) { /* API toast */ } finally {
          presetSaving.value = false;
        }
      }

      // ─── Hover Cell Content Preview Popover (Shared across Preset Table & Task Prompt Table) ───
      function onCellHoverEnter(ev, title, content) {
        const txt = String(content == null ? '' : content).trim();
        if (!txt) {
          hoverPopover.visible = false;
          return;
        }
        const rect = ev.currentTarget && ev.currentTarget.getBoundingClientRect
          ? ev.currentTarget.getBoundingClientRect()
          : { left: ev.clientX, bottom: ev.clientY + 14 };
        hoverPopover.title = title || '';
        hoverPopover.content = txt;
        hoverPopover.x = Math.max(12, Math.min(window.innerWidth - 440, rect.left));
        hoverPopover.y = Math.max(12, Math.min(window.innerHeight - 220, rect.bottom + 6));
        hoverPopover.visible = true;
      }

      function onCellHoverLeave() {
        hoverPopover.visible = false;
      }

      // ─── Column & Row Resizers (Google Sheet Style) ───
      function startColResize(targetObj, colKey, ev) {
        const startX = ev.clientX;
        const startW = Number(targetObj[colKey] || 180);
        const onMove = (e) => {
          targetObj[colKey] = Math.max(48, startW + (e.clientX - startX));
        };
        const onUp = () => {
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      }

      function startRowResize(heightMap, rowKey, ev) {
        const tr = ev.target.closest('tr');
        const startY = ev.clientY;
        const startH = heightMap[rowKey] || (tr ? tr.getBoundingClientRect().height : 38);
        const onMove = (e) => {
          heightMap[rowKey] = Math.max(28, Math.round(startH + (e.clientY - startY)));
        };
        const onUp = () => {
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      }

      // ─── Part A: Preset Management 3-Column Google Sheet Grid + Right-Click Grouping ───
      function presetGroupBorderColor(row) {
        if (!row) return '';
        const cat = String(row.category || '').trim();
        if (cat && cat !== '默认分组') {
          return groupColorMap[cat] || row.color || '#d4a24c';
        }
        return '';
      }

      function isPresetGroupFirst(rIdx) {
        const rows = filteredPresetRows.value;
        const cur = rows[rIdx];
        const col = presetGroupBorderColor(cur);
        if (!col) return false;
        if (rIdx === 0) return true;
        const prev = rows[rIdx - 1];
        return (prev.category || '') !== (cur.category || '');
      }

      function isPresetGroupLast(rIdx) {
        const rows = filteredPresetRows.value;
        const cur = rows[rIdx];
        const col = presetGroupBorderColor(cur);
        if (!col) return false;
        if (rIdx === rows.length - 1) return true;
        const next = rows[rIdx + 1];
        return (next.category || '') !== (cur.category || '');
      }

      const presetGroupsSummary = computed(() => {
        const map = new Map();
        for (const r of presetRows.value) {
          const cat = String(r.category || '').trim();
          if (!cat || cat === '默认分组') continue;
          if (!map.has(cat)) {
            map.set(cat, { name: cat, color: groupColorMap[cat] || r.color || '#d4a24c', count: 0 });
          }
          map.get(cat).count++;
        }
        return Array.from(map.values());
      });

      function presetSelBounds() {
        if (presetGridSel.startRow < 0 || presetGridSel.startCol < 0) return null;
        return {
          r1: Math.min(presetGridSel.startRow, presetGridSel.endRow),
          r2: Math.max(presetGridSel.startRow, presetGridSel.endRow),
          c1: Math.min(presetGridSel.startCol, presetGridSel.endCol),
          c2: Math.max(presetGridSel.startCol, presetGridSel.endCol),
        };
      }

      function isPresetCellSelected(rIdx, cIdx) {
        const b = presetSelBounds();
        if (!b) return false;
        return rIdx >= b.r1 && rIdx <= b.r2 && cIdx >= b.c1 && cIdx <= b.c2;
      }

      function isPresetFillCorner(rIdx, cIdx) {
        const b = presetSelBounds();
        if (!b) return false;
        return rIdx === b.r2 && cIdx === b.c2;
      }

      function onPresetCellMouseDown(rIdx, cIdx, row, ev) {
        if (ev.button !== 0) return;
        const tag = (ev.target && ev.target.tagName) ? ev.target.tagName.toLowerCase() : '';
        if (['input', 'textarea', 'select', 'button'].includes(tag)) return;
        if (ev.shiftKey && presetGridSel.startRow >= 0) {
          presetGridSel.endRow = rIdx;
          presetGridSel.endCol = cIdx;
        } else {
          presetGridSel.selecting = true;
          presetGridSel.startRow = rIdx;
          presetGridSel.startCol = cIdx;
          presetGridSel.endRow = rIdx;
          presetGridSel.endCol = cIdx;
        }
        if (ev.ctrlKey || ev.metaKey) {
          if (presetSelected.has(row._uid)) presetSelected.delete(row._uid);
          else presetSelected.add(row._uid);
        } else if (!ev.shiftKey) {
          presetSelected.clear();
          presetSelected.add(row._uid);
        }
      }

      function onPresetCellMouseEnter(rIdx, cIdx) {
        if (presetGridSel.selecting) {
          presetGridSel.endRow = rIdx;
          presetGridSel.endCol = cIdx;
          const b = presetSelBounds();
          if (b) {
            presetSelected.clear();
            const rows = filteredPresetRows.value;
            for (let r = b.r1; r <= b.r2; r++) {
              if (rows[r]) presetSelected.add(rows[r]._uid);
            }
          }
        } else if (presetGridSel.filling) {
          presetGridSel.fillEndRow = rIdx;
        }
      }

      function startPresetFillHandle(rIdx, cIdx, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        presetGridSel.filling = true;
        presetGridSel.fillEndRow = rIdx;
      }

      function startEditPresetCell(rIdx, cIdx) {
        presetEditingCell.rIdx = rIdx;
        presetEditingCell.cIdx = cIdx;
      }

      function stopEditPresetCell() {
        presetEditingCell.rIdx = -1;
        presetEditingCell.cIdx = -1;
      }

      function getSelectedPresetUids() {
        const set = new Set(presetSelected);
        const b = presetSelBounds();
        if (b) {
          const rows = filteredPresetRows.value;
          for (let r = b.r1; r <= b.r2; r++) {
            if (rows[r]) set.add(rows[r]._uid);
          }
        }
        return set;
      }

      function openPresetContextMenu(rIdx, row, ev) {
        ev.preventDefault();
        if (row && !presetSelected.has(row._uid)) {
          const b = presetSelBounds();
          const inBounds = b && rIdx >= b.r1 && rIdx <= b.r2;
          if (!inBounds) {
            presetSelected.clear();
            presetSelected.add(row._uid);
            presetGridSel.startRow = rIdx;
            presetGridSel.endRow = rIdx;
            presetGridSel.startCol = 0;
            presetGridSel.endCol = 2;
          }
        }
        presetCtxMenu.x = Math.min(window.innerWidth - 260, ev.clientX);
        presetCtxMenu.y = Math.min(window.innerHeight - 320, ev.clientY);
        presetCtxMenu.open = true;
      }

      // Group actions inside right-click menu
      function createPresetGroupFromSelection() {
        presetCtxMenu.open = false;
        const selUids = getSelectedPresetUids();
        if (!selUids.size) {
          toast('请先框选或多选要分组的预设行', '', 'warn');
          return;
        }
        const defaultName = `分组 ${presetGroupsSummary.value.length + 1}`;
        const name = prompt('请输入分组名称（同组行将共享相同外框颜色）：', defaultName);
        if (!name || !name.trim()) return;
        const gName = name.trim();
        const usedColors = presetGroupsSummary.value.map(g => g.color);
        const pickedColor = groupColorMap[gName]
          || TASK_COLORS.find(c => !usedColors.includes(c))
          || TASK_COLORS[presetGroupsSummary.value.length % TASK_COLORS.length];
        groupColorMap[gName] = pickedColor;

        let count = 0;
        for (const r of presetRows.value) {
          if (selUids.has(r._uid)) {
            r.category = gName;
            r.color = pickedColor;
            count++;
          }
        }
        // Sort grouped rows together so their shared outer border forms a clean contiguous block
        sortPresetsByGroup();
        toast(`🎨 已创建分组【${gName}】（共 ${count} 行，已应用同色外框）`, '', 'ok');
      }

      function addSelectionToPresetGroup(groupName) {
        presetCtxMenu.open = false;
        const selUids = getSelectedPresetUids();
        if (!selUids.size || !groupName) return;
        const gColor = groupColorMap[groupName] || TASK_COLORS[0];
        let count = 0;
        for (const r of presetRows.value) {
          if (selUids.has(r._uid)) {
            r.category = groupName;
            r.color = gColor;
            count++;
          }
        }
        sortPresetsByGroup();
        toast(`📂 已将 ${count} 行加入分组【${groupName}】`, '', 'ok');
      }

      function removeSelectionFromPresetGroup() {
        presetCtxMenu.open = false;
        const selUids = getSelectedPresetUids();
        if (!selUids.size) return;
        let count = 0;
        for (const r of presetRows.value) {
          if (selUids.has(r._uid)) {
            r.category = '';
            count++;
          }
        }
        toast(`📤 已将 ${count} 行从分组中移出`, '', 'ok');
      }

      function filterPresetByGroup(groupName) {
        presetCtxMenu.open = false;
        presetFilterCat.value = groupName || 'all';
      }

      function sortPresetsByGroup() {
        const orderMap = new Map();
        let seq = 0;
        for (const r of presetRows.value) {
          const cat = String(r.category || '').trim();
          if (cat && !orderMap.has(cat)) orderMap.set(cat, ++seq);
        }
        presetRows.value.sort((a, b) => {
          const ca = String(a.category || '').trim();
          const cb = String(b.category || '').trim();
          if (ca === cb) return 0;
          if (!ca) return 1;
          if (!cb) return -1;
          return (orderMap.get(ca) || 999) - (orderMap.get(cb) || 999);
        });
      }

      function onPresetColorChange(row, newColor) {
        row.color = newColor;
        const cat = String(row.category || '').trim();
        if (cat && cat !== '默认分组') {
          groupColorMap[cat] = newColor;
          for (const r of presetRows.value) {
            if (r.category === cat) r.color = newColor;
          }
        }
      }

      // Multi-row paste into Preset 3-column table (Ctrl+V from Excel / Google Sheet)
      function onPresetCellPaste(rowIdx, field, ev) {
        const text = ev.clipboardData && ev.clipboardData.getData('text/plain');
        if (!text) return;
        if (field === 'template' && !text.includes('\t')) return;
        if (!text.includes('\t') && !text.includes('\n')) return;
        ev.preventDefault();
        const rows = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
        while (rows.length && rows[rows.length - 1] === '') rows.pop();
        const colOrder = ['color', 'name', 'template'];
        const startCol = Math.max(0, colOrder.indexOf(field));
        for (let r = 0; r < rows.length; r++) {
          while (presetRows.value.length <= rowIdx + r) {
            presetRows.value.push(makeEmptyPresetRow());
          }
          const targetRow = presetRows.value[rowIdx + r];
          const cols = rows[r].split('\t');
          if (cols.length === 2 && field !== 'template') {
            targetRow.name = cols[0].trim();
            targetRow.template = cols[1];
          } else {
            for (let c = 0; c < cols.length; c++) {
              const f = colOrder[startCol + c];
              if (f) targetRow[f] = cols[c];
            }
          }
        }
        toast(`已批量粘贴 ${rows.length} 行预设数据`, '', 'ok');
      }

      // Preset drag-select
      function startPresetDrag(row, ev) {
        if (ev && ev.button !== 0) return;
        const next = !presetSelected.has(row._uid);
        presetDrag.active = true;
        presetDrag.val = next;
        if (next) presetSelected.add(row._uid);
        else presetSelected.delete(row._uid);
      }
      function enterPresetDrag(row) {
        if (!presetDrag.active || !row) return;
        if (presetDrag.val) presetSelected.add(row._uid);
        else presetSelected.delete(row._uid);
      }

      // ─── Part B: Task Prompt Table (Google Sheet Grid + Hidden Task Rows + Preset Name Column + Rich Text) ───
      function taskColor(idx, tk) {
        return (tk && tk.color) || TASK_COLORS[idx % TASK_COLORS.length];
      }

      function presetNameById(presetId) {
        if (!presetId) return '';
        const found = validPublishedPresets.value.find(p => p.id === presetId || p.name === presetId);
        return found ? found.name : presetId;
      }

      // Flat rows for 2D Google Sheet selection/copy/paste/fill in Task Prompt Table
      const flatTableRows = computed(() => {
        const list = [];
        const tasks = visibleTasksInTable.value;
        for (let tIdx = 0; tIdx < tasks.length; tIdx++) {
          const tk = tasks[tIdx];
          if (collapsedTasks[tk.id]) continue;
          const segs = tk.segments || [];
          const color = taskColor(tIdx, tk);
          for (let sIdx = 0; sIdx < segs.length; sIdx++) {
            list.push({
              flatIdx: list.length,
              tk,
              tIdx,
              sg: segs[sIdx],
              sIdx,
              rowKey: `${tk.id}:${segs[sIdx].s}`,
              groupColor: color,
              isFirstInTask: sIdx === 0,
              isLastInTask: sIdx === segs.length - 1,
            });
          }
        }
        return list;
      });

      function tableSelBounds() {
        if (tableGridSel.startRow < 0 || tableGridSel.startCol < 0) return null;
        return {
          r1: Math.min(tableGridSel.startRow, tableGridSel.endRow),
          r2: Math.max(tableGridSel.startRow, tableGridSel.endRow),
          c1: Math.min(tableGridSel.startCol, tableGridSel.endCol),
          c2: Math.max(tableGridSel.startCol, tableGridSel.endCol),
        };
      }

      function isTableCellSelected(flatIdx, cIdx) {
        const b = tableSelBounds();
        if (!b) return false;
        return flatIdx >= b.r1 && flatIdx <= b.r2 && cIdx >= b.c1 && cIdx <= b.c2;
      }

      function isTableFillCorner(flatIdx, cIdx) {
        const b = tableSelBounds();
        if (!b) return false;
        return flatIdx === b.r2 && cIdx === b.c2;
      }

      function onTableCellMouseDown(flatIdx, cIdx, ev) {
        if (ev.button !== 0) return;
        const tag = (ev.target && ev.target.tagName) ? ev.target.tagName.toLowerCase() : '';
        if (['input', 'textarea', 'select', 'button'].includes(tag)) return;
        if (ev.shiftKey && tableGridSel.startRow >= 0) {
          tableGridSel.endRow = flatIdx;
          tableGridSel.endCol = cIdx;
        } else {
          tableGridSel.selecting = true;
          tableGridSel.startRow = flatIdx;
          tableGridSel.startCol = cIdx;
          tableGridSel.endRow = flatIdx;
          tableGridSel.endCol = cIdx;
        }
      }

      function onTableCellMouseEnter(flatIdx, cIdx) {
        if (tableGridSel.selecting) {
          tableGridSel.endRow = flatIdx;
          tableGridSel.endCol = cIdx;
        } else if (tableGridSel.filling) {
          tableGridSel.fillEndRow = flatIdx;
        }
      }

      function startTableFillHandle(flatIdx, cIdx, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        tableGridSel.filling = true;
        tableGridSel.fillEndRow = flatIdx;
      }

      function startEditTableCell(flatIdx, cIdx, key) {
        tableEditingCell.rIdx = flatIdx;
        tableEditingCell.cIdx = cIdx;
        tableEditingCell.key = key || `${flatIdx}:${cIdx}`;
      }

      function stopEditTableCell() {
        tableEditingCell.rIdx = -1;
        tableEditingCell.cIdx = -1;
        tableEditingCell.key = '';
      }

      // Column index mapping for Task Prompt Table:
      // 0: s, 1: image, 2: duration, 3: text, 4: preset (预设名称), 5+: orderedPromptColumns
      function getTableCellTextByColIdx(rowItem, cIdx) {
        if (!rowItem) return '';
        const { sg } = rowItem;
        if (cIdx === 0) return 's' + pad3(sg.s);
        if (cIdx === 1) return sg.image || 'p01';
        if (cIdx === 2) return (sg.duration || 6) + 's';
        if (cIdx === 3) return sg.text || '';
        if (cIdx === 4) return presetNameById(sg.preset_id);
        const col = orderedPromptColumns.value[cIdx - 5];
        if (col) return getPromptCellValue(sg, col);
        return '';
      }

      async function setTableCellTextByColIdx(rowItem, cIdx, rawVal, batchUpdates) {
        if (!rowItem) return;
        const { tk, sg } = rowItem;
        const val = String(rawVal == null ? '' : rawVal);
        if (cIdx === 1) {
          sg.image = val.trim() || 'p01';
          batchUpdates.push({ task_id: tk.id, s: sg.s, image: sg.image });
        } else if (cIdx === 2) {
          const d = parseInt(val.replace(/[^\d]/g, ''), 10);
          if ([4, 6, 8].includes(d)) {
            sg.duration = d;
            batchUpdates.push({ task_id: tk.id, s: sg.s, duration: d });
          }
        } else if (cIdx === 3) {
          sg.text = val;
          batchUpdates.push({ task_id: tk.id, s: sg.s, text: val });
        } else if (cIdx === 4) {
          const p = validPublishedPresets.value.find(x => x.name === val.trim() || x.id === val.trim());
          if (p) {
            sg.preset_id = p.id;
            sg.custom_prompt = PresetSlotLogic.composeSpeech(p.template, sg.text);
            batchUpdates.push({ task_id: tk.id, s: sg.s, preset_id: p.id, custom_prompt: sg.custom_prompt });
          }
        } else if (cIdx >= 5) {
          const col = orderedPromptColumns.value[cIdx - 5];
          if (!col) return;
          if (col.col_id === 'main') {
            sg.custom_prompt = val;
            batchUpdates.push({ task_id: tk.id, s: sg.s, custom_prompt: val });
          } else {
            if (!sg.extra_prompts) sg.extra_prompts = {};
            sg.extra_prompts[col.col_id] = val;
            batchUpdates.push({ task_id: tk.id, s: sg.s, extra_prompts: Object.assign({}, sg.extra_prompts) });
          }
        }
      }

      function isSegChecked(taskId, s) {
        return !!segChecked[taskId + ':' + s];
      }

      function startSegDrag(taskId, s, ev) {
        if (ev && ev.button !== 0) return;
        const k = taskId + ':' + s;
        const next = !segChecked[k];
        segDrag.active = true;
        segDrag.val = next;
        segChecked[k] = next;
      }

      function enterSegDrag(taskId, s) {
        if (!segDrag.active) return;
        segChecked[taskId + ':' + s] = segDrag.val;
      }

      async function stopAllDrags() {
        presetDrag.active = false;
        segDrag.active = false;
        presetGridSel.selecting = false;
        tableGridSel.selecting = false;

        // Finish Preset Fill Handle drag
        if (presetGridSel.filling) {
          presetGridSel.filling = false;
          const b = presetSelBounds();
          const targetEnd = presetGridSel.fillEndRow;
          if (b && targetEnd > b.r2) {
            const rows = filteredPresetRows.value;
            const colKeys = ['color', 'name', 'template'];
            for (let r = b.r2 + 1; r <= targetEnd; r++) {
              const dst = rows[r];
              const src = rows[b.r1 + ((r - b.r1) % (b.r2 - b.r1 + 1))];
              if (!dst || !src) continue;
              for (let c = b.c1; c <= b.c2; c++) {
                const k = colKeys[c];
                if (k) dst[k] = src[k];
              }
            }
            presetGridSel.endRow = targetEnd;
            toast(`已向下拖拽填充 ${targetEnd - b.r2} 行预设单元格`, '', 'ok');
          }
        }

        // Finish Task Prompt Table Fill Handle drag
        if (tableGridSel.filling) {
          tableGridSel.filling = false;
          const b = tableSelBounds();
          const targetEnd = tableGridSel.fillEndRow;
          if (b && targetEnd > b.r2) {
            const rows = flatTableRows.value;
            const updates = [];
            for (let r = b.r2 + 1; r <= targetEnd; r++) {
              const dst = rows[r];
              const src = rows[b.r1 + ((r - b.r1) % (b.r2 - b.r1 + 1))];
              if (!dst || !src) continue;
              for (let c = b.c1; c <= b.c2; c++) {
                const srcVal = getTableCellTextByColIdx(src, c);
                await setTableCellTextByColIdx(dst, c, srcVal, updates);
              }
            }
            tableGridSel.endRow = targetEnd;
            if (updates.length) {
              await API.post('/api/tasks/batch-update-segments', { updates }).catch(() => {});
              toast(`已向下拖拽拷贝填充 ${targetEnd - b.r2} 行单元格`, '', 'ok');
            }
          }
        }
      }

      function isTaskAllChecked(tk) {
        const segs = (tk && tk.segments) || [];
        return segs.length > 0 && segs.every(sg => !!segChecked[tk.id + ':' + sg.s]);
      }

      function toggleTaskCheck(tk) {
        const allOn = isTaskAllChecked(tk);
        (tk.segments || []).forEach(sg => {
          segChecked[tk.id + ':' + sg.s] = !allOn;
        });
      }

      function selectAllTableSegs(on) {
        for (const tk of visibleTasksInTable.value) {
          for (const sg of (tk.segments || [])) {
            segChecked[tk.id + ':' + sg.s] = !!on;
          }
        }
      }

      function invertTableSegs() {
        for (const tk of visibleTasksInTable.value) {
          for (const sg of (tk.segments || [])) {
            const k = tk.id + ':' + sg.s;
            segChecked[k] = !segChecked[k];
          }
        }
      }

      function expandAllTasks(collapse) {
        for (const tk of tasksTable.value) {
          collapsedTasks[tk.id] = !!collapse;
        }
      }

      // Dynamic Right-Click Insertable Columns
      const orderedPromptColumns = computed(() => {
        const leftCols = extraColumns.value.filter(c => c.position === 'left');
        const rightCols = extraColumns.value.filter(c => c.position !== 'left');
        const mainCol = {
          col_id: 'main',
          name: '主提示词列 (同步排镜头)',
          isMain: true,
          header_mode: mainColControl.header_mode,
          header_preset_id: mainColControl.header_preset_id,
          header_group: mainColControl.header_group,
        };
        return [...leftCols, mainCol, ...rightCols];
      });

      function openColContextMenu(col, idx, ev) {
        colMenu.colId = col.col_id;
        colMenu.colIndex = idx;
        colMenu.x = Math.min(window.innerWidth - 220, ev.clientX);
        colMenu.y = Math.min(window.innerHeight - 180, ev.clientY);
        colMenu.open = true;
      }

      async function persistExtraColumns() {
        try {
          await API.post('/api/tasks/batch-update-segments', {
            updates: [],
            extra_columns: extraColumns.value,
          });
        } catch (e) { /* ignore */ }
      }

      async function insertPromptColumn(side) {
        colMenu.open = false;
        const name = prompt('请输入新提示词列名称：', `附加提示词 ${extraColumns.value.length + 1}`);
        if (!name) return;
        const newCol = {
          col_id: 'col_' + Date.now().toString(36),
          name: name.trim(),
          position: side === 'left' ? 'left' : 'right',
          header_mode: 'single',
          header_preset_id: '',
          header_group: '',
        };
        extraColumns.value.push(newCol);
        await persistExtraColumns();
        toast(`已${side === 'left' ? '在左侧' : '在右侧'}插入列【${newCol.name}】`, '', 'ok');
      }

      async function renamePromptColumn() {
        const cid = colMenu.colId;
        colMenu.open = false;
        const col = extraColumns.value.find(c => c.col_id === cid);
        if (!col) {
          toast('主提示词列为系统默认同步列', '', 'warn');
          return;
        }
        const nextName = prompt('重命名提示词列：', col.name);
        if (!nextName || !nextName.trim()) return;
        col.name = nextName.trim();
        await persistExtraColumns();
      }

      async function deletePromptColumn() {
        const cid = colMenu.colId;
        colMenu.open = false;
        if (cid === 'main') {
          toast('主提示词列不能删除', '', 'warn');
          return;
        }
        const idx = extraColumns.value.findIndex(c => c.col_id === cid);
        if (idx < 0) return;
        if (!confirm(`确定删除提示词列【${extraColumns.value[idx].name}】吗？`)) return;
        extraColumns.value.splice(idx, 1);
        await persistExtraColumns();
        toast('已删除提示词列', '', 'ok');
      }

      // Top Batch Function Bar (2-part controller per column)
      function setColHeaderMode(col, mode) {
        if (col.col_id === 'main') mainColControl.header_mode = mode;
        else {
          const target = extraColumns.value.find(c => c.col_id === col.col_id);
          if (target) target.header_mode = mode;
        }
      }

      function setColHeaderSecondVal(col, val) {
        if (col.col_id === 'main') {
          if (col.header_mode === 'random_group') mainColControl.header_group = val;
          else mainColControl.header_preset_id = val;
        } else {
          const target = extraColumns.value.find(c => c.col_id === col.col_id);
          if (target) {
            if (target.header_mode === 'random_group') target.header_group = val;
            else target.header_preset_id = val;
          }
        }
      }

      async function runColumnBatchAction(col) {
        const targets = checkedTargets.value.map(x => ({ task_id: x.task_id, s: x.s }));
        if (!targets.length) {
          toast('请先在左侧勾选要生效的任务分段（仅勾选行生效）', '', 'warn');
          return;
        }
        const mode = col.header_mode || 'single';
        const presetId = col.header_preset_id || '';
        const category = col.header_group || '';

        if ((mode === 'single' || mode === 'gemini_template') && !presetId) {
          toast('请先在第二个下拉框中选择要应用的预设或模板', '', 'warn');
          return;
        }
        if (mode === 'random_group' && !category) {
          toast('请先在第二个下拉框中选择要随机应用的提示词分组', '', 'warn');
          return;
        }

        // Local immediate preview application + backend persistence
        const chosenPreset = validPublishedPresets.value.find(p => p.id === presetId);
        const groupPresets = validPublishedPresets.value.filter(p => (p.category || '默认分组') === category);

        const updates = [];
        // When mode === 'gemini_template', segments sharing the same image (e.g. p01) within a task reuse the same generated prompt
        const imagePromptCache = {};

        for (const item of checkedTargets.value) {
          const { task_id, s, seg } = item;
          let picked = chosenPreset;
          if (mode === 'random_group' && groupPresets.length) {
            picked = groupPresets[Math.floor(Math.random() * groupPresets.length)];
          }
          let composed = '';
          const cacheKey = `${task_id}:${seg.image || 'p01'}`;
          if (mode === 'gemini_template' && imagePromptCache[cacheKey]) {
            composed = PresetSlotLogic.composeSpeech(imagePromptCache[cacheKey], seg.text);
          } else if (picked) {
            if (mode === 'gemini_template') {
              imagePromptCache[cacheKey] = picked.template;
            }
            composed = PresetSlotLogic.composeSpeech(picked.template, seg.text);
          }

          if (col.col_id === 'main') {
            if (picked) seg.preset_id = picked.id;
            if (composed) seg.custom_prompt = composed;
            updates.push({
              task_id,
              s,
              preset_id: picked ? picked.id : seg.preset_id,
              custom_prompt: composed || seg.custom_prompt,
            });
          } else {
            if (!seg.extra_prompts) seg.extra_prompts = {};
            seg.extra_prompts[col.col_id] = composed;
            updates.push({
              task_id,
              s,
              extra_prompts: Object.assign({}, seg.extra_prompts),
            });
          }
        }

        try {
          await API.post('/api/tasks/batch-apply-preset', {
            mode,
            preset_id: presetId || undefined,
            category: category || undefined,
            col_id: col.col_id,
            targets,
          });
        } catch (e) {
          await API.post('/api/tasks/batch-update-segments', { updates }).catch(() => {});
        }
        await loadTasksSegmentsTable(true);
        toast(`✓ 已对 ${targets.length} 个勾选分段批量执行【${mode === 'random_group' ? '随机应用提示词组' : (mode === 'gemini_template' ? '按模板生成描述语（同图复用）' : '直接选预设')}】`, '', 'ok');
      }

      // Cell value getter/setter & multi-row paste
      function getPromptCellValue(sg, col) {
        if (col.col_id === 'main') {
          return sg.custom_prompt || sg.prompt_preview || '';
        }
        return (sg.extra_prompts && sg.extra_prompts[col.col_id]) || '';
      }

      async function updateSegmentField(taskId, sg, field, val, extraColId) {
        const u = { task_id: taskId, s: sg.s };
        if (extraColId && extraColId !== 'main') {
          if (!sg.extra_prompts) sg.extra_prompts = {};
          sg.extra_prompts[extraColId] = val;
          u.extra_prompts = Object.assign({}, sg.extra_prompts);
        } else {
          sg[field] = val;
          u[field] = val;
        }
        try {
          await API.post('/api/tasks/batch-update-segments', { updates: [u] });
        } catch (e) { /* API toast */ }
      }

      async function onTableCellPaste(tk, startIdx, colId, ev) {
        const text = ev.clipboardData && ev.clipboardData.getData('text/plain');
        if (!text || (!text.includes('\n') && !text.includes('\t'))) return;
        ev.preventDefault();
        const rows = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
        while (rows.length && rows[rows.length - 1] === '') rows.pop();
        const segs = tk.segments || [];
        const updates = [];
        for (let i = 0; i < rows.length; i++) {
          const sg = segs[startIdx + i];
          if (!sg) break;
          const val = rows[i].split('\t')[0];
          if (colId === 'text') {
            sg.text = val;
            updates.push({ task_id: tk.id, s: sg.s, text: val });
          } else if (colId === 'main') {
            sg.custom_prompt = val;
            updates.push({ task_id: tk.id, s: sg.s, custom_prompt: val });
          } else {
            if (!sg.extra_prompts) sg.extra_prompts = {};
            sg.extra_prompts[colId] = val;
            updates.push({ task_id: tk.id, s: sg.s, extra_prompts: Object.assign({}, sg.extra_prompts) });
          }
        }
        if (updates.length) {
          await API.post('/api/tasks/batch-update-segments', { updates });
          toast(`已批量粘贴 ${updates.length} 段内容`, '', 'ok');
        }
      }

      // Drag & Drop or Click Apply from Right-Side Preset Card Dock
      function onDragStartPreset(preset, ev) {
        draggingPreset.value = preset;
        if (ev.dataTransfer) {
          ev.dataTransfer.effectAllowed = 'copy';
          ev.dataTransfer.setData('text/plain', preset.id || '');
        }
      }

      async function applyPresetToSegment(tk, sg, colId, preset) {
        if (!preset || !sg) return;
        const composed = PresetSlotLogic.composeSpeech(preset.template, sg.text);
        if (!colId || colId === 'main') {
          sg.preset_id = preset.id;
          sg.custom_prompt = composed;
          await updateSegmentField(tk.id, sg, 'custom_prompt', composed, 'main');
          await updateSegmentField(tk.id, sg, 'preset_id', preset.id, 'main');
        } else {
          await updateSegmentField(tk.id, sg, 'extra_prompts', composed, colId);
        }
        toast(`已应用预设【${preset.name}】→ ${tk.id} s${pad3(sg.s)}`, '', 'ok');
      }

      async function onDropPresetOnCell(tk, sg, colId) {
        const p = draggingPreset.value;
        draggingPreset.value = null;
        if (!p) return;
        await applyPresetToSegment(tk, sg, colId, p);
      }

      async function onDropPresetOnTask(tk) {
        const p = draggingPreset.value;
        draggingPreset.value = null;
        if (!p || !tk) return;
        await applyPresetToTask(tk, p.id);
      }

      async function applyPresetToTask(tk, presetId) {
        const p = validPublishedPresets.value.find(x => x.id === presetId);
        tk.preset_id = presetId;
        const updates = [{ task_id: tk.id, task_preset_id: presetId }];
        if (p) {
          for (const sg of (tk.segments || [])) {
            sg.preset_id = p.id;
            sg.custom_prompt = PresetSlotLogic.composeSpeech(p.template, sg.text);
            updates.push({
              task_id: tk.id,
              s: sg.s,
              preset_id: p.id,
              custom_prompt: sg.custom_prompt,
            });
          }
        }
        await API.post('/api/tasks/batch-update-segments', { updates });
        toast(`已将预设应用到任务 ${tk.id} 全段`, '', 'ok');
      }

      async function applyCardToChecked(preset) {
        if (!preset) return;
        const list = checkedTargets.value;
        if (!list.length) {
          toast('请先勾选要应用此预设卡片的分段行', '', 'warn');
          return;
        }
        const updates = [];
        for (const item of list) {
          const { task_id, s, seg } = item;
          const composed = PresetSlotLogic.composeSpeech(preset.template, seg.text);
          seg.preset_id = preset.id;
          seg.custom_prompt = composed;
          updates.push({
            task_id,
            s,
            preset_id: preset.id,
            custom_prompt: composed,
          });
        }
        await API.post('/api/tasks/batch-update-segments', { updates });
        toast(`🎴 已将预设【${preset.name}】应用到 ${updates.length} 个勾选分段`, '', 'ok');
      }

      // Find & Replace (Ctrl+H)
      function openFindReplace(target) {
        findReplaceModal.target = target || (viewMode.value === 'presets' ? 'presets' : 'table');
        findReplaceModal.find = '';
        findReplaceModal.replace = '';
        findReplaceModal.open = true;
      }

      async function executeFindReplace() {
        const f = findReplaceModal.find;
        const rep = findReplaceModal.replace;
        if (!f) {
          toast('请输入要查找的文本', '', 'warn');
          return;
        }
        let count = 0;
        if (findReplaceModal.target === 'presets') {
          for (const r of presetRows.value) {
            if (r.template && r.template.includes(f)) {
              r.template = r.template.split(f).join(rep);
              count++;
            }
            if (r.name && r.name.includes(f)) {
              r.name = r.name.split(f).join(rep);
            }
          }
          toast(`已在 ${count} 条预设中完成替换`, '', 'ok');
        } else {
          const updates = [];
          for (const tk of visibleTasksInTable.value) {
            for (const sg of (tk.segments || [])) {
              let changed = false;
              const u = { task_id: tk.id, s: sg.s };
              if (sg.custom_prompt && sg.custom_prompt.includes(f)) {
                sg.custom_prompt = sg.custom_prompt.split(f).join(rep);
                u.custom_prompt = sg.custom_prompt;
                changed = true;
              }
              if (sg.text && sg.text.includes(f)) {
                sg.text = sg.text.split(f).join(rep);
                u.text = sg.text;
                changed = true;
              }
              if (changed) {
                updates.push(u);
                count++;
              }
            }
          }
          if (updates.length) {
            await API.post('/api/tasks/batch-update-segments', { updates });
          }
          toast(`已在 ${count} 个任务分段中完成替换`, '', 'ok');
        }
        findReplaceModal.open = false;
      }

      // Gemini API Key modal & Describe trigger
      function openGeminiCfg() {
        geminiCfgModal.apiKey = (store.settings && store.settings['gemini.api_key']) || '';
        geminiCfgModal.model = (store.settings && store.settings['gemini.model']) || 'gemini-3.5-flash-lite';
        geminiCfgModal.open = true;
      }

      async function saveGeminiCfg() {
        geminiCfgModal.saving = true;
        try {
          const r = await API.post('/api/settings', {
            set: {
              'gemini.api_key': geminiCfgModal.apiKey.trim(),
              'gemini.model': geminiCfgModal.model,
            },
          });
          if (r && r.settings) Object.assign(store.settings, r.settings);
          geminiCfgModal.open = false;
          toast('Gemini API 配置已保存', '', 'ok');
        } catch (e) { /* API toast */ } finally {
          geminiCfgModal.saving = false;
        }
      }

      function copySelectedPresetCellsToClipboard() {
        const b = presetSelBounds();
        if (!b) return false;
        const rows = filteredPresetRows.value;
        const colKeys = ['color', 'name', 'template'];
        const outLines = [];
        for (let r = b.r1; r <= b.r2; r++) {
          const row = rows[r];
          if (!row) continue;
          const cols = [];
          for (let c = b.c1; c <= b.c2; c++) {
            const k = colKeys[c];
            cols.push(String(row[k] || '').replace(/\r?\n/g, ' '));
          }
          outLines.push(cols.join('\t'));
        }
        if (outLines.length && navigator.clipboard) {
          navigator.clipboard.writeText(outLines.join('\n')).catch(() => {});
          toast(`📋 已复制 ${outLines.length} 行预设单元格`, '', 'ok');
          return true;
        }
        return false;
      }

      function copySelectedTableCellsToClipboard() {
        const b = tableSelBounds();
        if (!b) return false;
        const rows = flatTableRows.value;
        const outLines = [];
        for (let r = b.r1; r <= b.r2; r++) {
          const item = rows[r];
          if (!item) continue;
          const cols = [];
          for (let c = b.c1; c <= b.c2; c++) {
            cols.push(String(getTableCellTextByColIdx(item, c) || '').replace(/\r?\n/g, ' '));
          }
          outLines.push(cols.join('\t'));
        }
        if (outLines.length && navigator.clipboard) {
          navigator.clipboard.writeText(outLines.join('\n')).catch(() => {});
          toast(`📋 已复制 ${outLines.length} 行 × ${b.c2 - b.c1 + 1} 列单元格`, '', 'ok');
          return true;
        }
        return false;
      }

      async function onGlobalPaste(ev) {
        const tag = (ev.target && ev.target.tagName) ? ev.target.tagName.toLowerCase() : '';
        if (['input', 'textarea', 'select'].includes(tag)) return;
        const text = ev.clipboardData && ev.clipboardData.getData('text/plain');
        if (!text) return;

        // Paste into Preset 2D Grid
        if (viewMode.value === 'presets' && presetSelBounds()) {
          ev.preventDefault();
          const b = presetSelBounds();
          const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
          while (lines.length && lines[lines.length - 1] === '') lines.pop();
          const colKeys = ['color', 'name', 'template'];
          for (let r = 0; r < lines.length; r++) {
            while (presetRows.value.length <= b.r1 + r) {
              presetRows.value.push(makeEmptyPresetRow());
            }
            const dst = filteredPresetRows.value[b.r1 + r] || presetRows.value[b.r1 + r];
            if (!dst) continue;
            const cols = lines[r].split('\t');
            for (let c = 0; c < cols.length; c++) {
              const k = colKeys[b.c1 + c];
              if (k) dst[k] = cols[c];
            }
          }
          toast(`📋 已粘贴 ${lines.length} 行预设数据`, '', 'ok');
          return;
        }

        // Paste into Task Prompt Table 2D Grid
        if ((viewMode.value === 'table' || viewMode.value === 'split') && tableSelBounds()) {
          ev.preventDefault();
          const b = tableSelBounds();
          const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
          while (lines.length && lines[lines.length - 1] === '') lines.pop();
          const rows = flatTableRows.value;
          const updates = [];
          for (let r = 0; r < lines.length; r++) {
            const dst = rows[b.r1 + r];
            if (!dst) break;
            const cols = lines[r].split('\t');
            for (let c = 0; c < cols.length; c++) {
              await setTableCellTextByColIdx(dst, b.c1 + c, cols[c], updates);
            }
          }
          if (updates.length) {
            await API.post('/api/tasks/batch-update-segments', { updates }).catch(() => {});
            toast(`📋 已批量粘贴 ${lines.length} 行单元格数据`, '', 'ok');
          }
        }
      }

      function onGlobalKeyDown(ev) {
        const tag = (ev.target && ev.target.tagName) ? ev.target.tagName.toLowerCase() : '';
        const isEditingInput = ['input', 'textarea', 'select'].includes(tag);
        if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'h' || ev.key === 'H')) {
          ev.preventDefault();
          openFindReplace();
          return;
        }
        if (!isEditingInput && (ev.ctrlKey || ev.metaKey) && (ev.key === 'c' || ev.key === 'C')) {
          if (viewMode.value === 'presets' && copySelectedPresetCellsToClipboard()) {
            ev.preventDefault();
            return;
          }
          if (copySelectedTableCellsToClipboard()) {
            ev.preventDefault();
            return;
          }
        }
        if (ev.key === 'Escape') {
          colMenu.open = false;
          presetCtxMenu.open = false;
          stopEditPresetCell();
          stopEditTableCell();
        }
      }

      function onGlobalClick() {
        if (colMenu.open) colMenu.open = false;
        if (presetCtxMenu.open) presetCtxMenu.open = false;
      }

      onMounted(() => {
        loadPresets(false);
        loadTasksSegmentsTable(false);
        window.addEventListener('mouseup', stopAllDrags);
        window.addEventListener('keydown', onGlobalKeyDown);
        window.addEventListener('paste', onGlobalPaste);
        document.addEventListener('click', onGlobalClick);
      });

      onBeforeUnmount(() => {
        window.removeEventListener('mouseup', stopAllDrags);
        window.removeEventListener('keydown', onGlobalKeyDown);
        window.removeEventListener('paste', onGlobalPaste);
        document.removeEventListener('click', onGlobalClick);
      });

      watch(() => store.refreshSeq, () => {
        loadPresets(true);
        loadTasksSegmentsTable(true);
      });

      return {
        ui, store, t, pad3,
        viewMode,
        hoverPopover, onCellHoverEnter, onCellHoverLeave,
        startColResize, startRowResize,
        // Part A: Presets (3-column Google Sheet + Right-Click Grouping)
        presetRows, presetFilterCat, presetSearch, presetSelected, presetSaving,
        presetColWidths, presetRowHeights, presetEditingCell, presetGridSel, presetCtxMenu,
        presetCategories, presetGroupsSummary, validPublishedPresets, filteredPresetRows, invalidPresetCount,
        validateRow, addPresetRows, deleteSelectedPresets, publishPresets,
        presetGroupBorderColor, isPresetGroupFirst, isPresetGroupLast,
        isPresetCellSelected, isPresetFillCorner, onPresetCellMouseDown, onPresetCellMouseEnter,
        startPresetFillHandle, startEditPresetCell, stopEditPresetCell,
        openPresetContextMenu, createPresetGroupFromSelection, addSelectionToPresetGroup,
        removeSelectionFromPresetGroup, filterPresetByGroup, onPresetColorChange,
        onPresetCellPaste, startPresetDrag, enterPresetDrag, copySelectedPresetCellsToClipboard,
        // Part B: Tasks Prompt Table & Dock
        tasksTable, extraColumns, mainColControl, collapsedTasks, segChecked, selectedCells,
        dockSearch, dockCat, dockOpen, tableSearch, onlyCurrentTask,
        showTaskHeaderRows, textWrapMode,
        tableColWidths, tableRowHeights, tableEditingCell, tableGridSel,
        visibleTasksInTable, flatTableRows, checkedTargets, dockPresets, orderedPromptColumns,
        colMenu, findReplaceModal, geminiCfgModal,
        taskColor, presetNameById,
        isTableCellSelected, isTableFillCorner, onTableCellMouseDown, onTableCellMouseEnter,
        startTableFillHandle, startEditTableCell, stopEditTableCell,
        isSegChecked, startSegDrag, enterSegDrag,
        isTaskAllChecked, toggleTaskCheck, selectAllTableSegs, invertTableSegs, expandAllTasks,
        openColContextMenu, insertPromptColumn, renamePromptColumn, deletePromptColumn,
        setColHeaderMode, setColHeaderSecondVal, runColumnBatchAction,
        getPromptCellValue, updateSegmentField, onTableCellPaste,
        onDragStartPreset, onDropPresetOnCell, onDropPresetOnTask, applyPresetToSegment, applyPresetToTask, applyCardToChecked,
        openFindReplace, executeFindReplace, openGeminiCfg, saveGeminiCfg,
      };
    },
    template: `
    <section class="panel gm3-panel">
      <!-- 顶部精简模式切换栏 -->
      <div class="gm3-topbar">
        <div class="gm3-mode-tabs">
          <button type="button" class="gm3-tab" :class="{active: viewMode==='table'}" @click="viewMode='table'">
            📝 任务提示词总表
          </button>
          <button type="button" class="gm3-tab" :class="{active: viewMode==='presets'}" @click="viewMode='presets'">
            📚 预设管理
            <span v-if="invalidPresetCount" class="tag bad" style="margin-left:4px">{{ invalidPresetCount }} 待修正</span>
          </button>
          <button type="button" class="gm3-tab" :class="{active: viewMode==='split'}" @click="viewMode='split'">
            🔲 双栏同屏
          </button>
        </div>
        <span class="spacer" style="flex:1"></span>
        <button type="button" class="btn sm tog" :class="{on: textWrapMode==='wrap'}"
                @click="textWrapMode = textWrapMode==='wrap' ? 'clip' : 'wrap'"
                title="切换单元格文字自动换行 / 溢出截断">
          {{ textWrapMode==='wrap' ? '↩ 自动换行' : '✂️ 溢出截断' }}
        </button>
        <button type="button" class="btn sm ghost" @click="openFindReplace()">🔍 查找替换</button>
        <button type="button" class="btn sm ghost" @click="openGeminiCfg()">🔑 Gemini 设置</button>
        <button type="button" class="btn sm" @click="ui.node='track'">← 2 排镜头</button>
        <button type="button" class="btn sm primary" @click="ui.node='generate'">4 生成 →</button>
      </div>

      <div class="gm3-workspace" :class="'mode-' + viewMode">
        <!-- ══════════════════════════════════════════════════════════════════
             Part A: 📚 预设管理 (极简 3 列标准 Google Sheet：预览颜色 | 预设名称 | 提示词内容 + 右键菜单同框色分组)
             ══════════════════════════════════════════════════════════════════ -->
        <div v-if="viewMode==='presets' || viewMode==='split'" class="gm3-pane gm3-presets-pane">
          <div class="gm3-subbar">
            <button type="button" class="btn sm primary" :disabled="presetSaving" @click="publishPresets">
              💾 保存预设 ({{ presetRows.length }})
            </button>
            <button type="button" class="btn sm" @click="addPresetRows(1)">➕ 新增行</button>
            <button type="button" class="btn sm" @click="addPresetRows(5)">➕ 5 行</button>
            <button v-if="presetSelected.size" type="button" class="btn sm danger" @click="deleteSelectedPresets">
              🗑️ 删除 ({{ presetSelected.size }})
            </button>
            <span v-if="presetFilterCat!=='all'" class="tag ok" style="cursor:pointer" @click="presetFilterCat='all'" title="点击恢复显示全部分组">
              📂 当前筛选组: {{ presetFilterCat }} ✕
            </span>
            <span class="muted" style="font-size:11px;margin-left:6px">
              💡 框选多行后<b>右键</b>可创建分组（同组共享同色外框）、加入/移出分组或按组查看；双击单元格编辑，右下角拖拽批量填充
            </span>
            <span class="spacer" style="flex:1"></span>
            <input v-model="presetSearch" type="search" class="trk-tp-search" style="width:180px" placeholder="🔍 搜索预设名称/内容…">
          </div>

          <div class="gm3-table-scroll">
            <table class="gs-sheet-table gm3-preset-table">
              <thead>
                <tr>
                  <th :style="{width: presetColWidths.color + 'px', textAlign: 'center'}">
                    预览颜色
                    <span class="gs-col-resizer" @mousedown.stop.prevent="startColResize(presetColWidths, 'color', $event)"></span>
                  </th>
                  <th :style="{width: presetColWidths.name + 'px'}">
                    预设名称
                    <span class="gs-col-resizer" @mousedown.stop.prevent="startColResize(presetColWidths, 'name', $event)"></span>
                  </th>
                  <th :style="{width: presetColWidths.template + 'px'}">
                    提示词内容 <small class="muted" style="font-weight:400">(双击编辑 · 换行处自动作为【人物说的话】插槽)</small>
                    <span class="gs-col-resizer" @mousedown.stop.prevent="startColResize(presetColWidths, 'template', $event)"></span>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, rIdx) in filteredPresetRows" :key="row._uid"
                    :class="{
                      'row-selected': presetSelected.has(row._uid),
                      'gs-group-row': !!presetGroupBorderColor(row),
                      'gs-group-first': isPresetGroupFirst(rIdx),
                      'gs-group-last': isPresetGroupLast(rIdx)
                    }"
                    :style="{
                      '--group-border-color': presetGroupBorderColor(row) || 'transparent',
                      height: (presetRowHeights[row._uid] || 48) + 'px'
                    }"
                    @contextmenu.prevent="openPresetContextMenu(rIdx, row, $event)">
                  <!-- 第 1 列：预览颜色（大号清晰色块 + 行高度调节器） -->
                  <td style="text-align:center;vertical-align:middle"
                      :class="{'gs-cell-selected': isPresetCellSelected(rIdx, 0)}"
                      @mousedown="onPresetCellMouseDown(rIdx, 0, row, $event)"
                      @mouseenter="onPresetCellMouseEnter(rIdx, 0); onCellHoverEnter($event, '预设分组与颜色', (row.category ? '分组：' + row.category + ' · ' : '未分组 · ') + '颜色：' + (row.color || '#d4a24c'))"
                      @mouseleave="onCellHoverLeave">
                    <div style="display:inline-flex;align-items:center;justify-content:center;gap:6px">
                      <label class="gs-big-color-swatch" :style="{background: row.color || '#d4a24c'}" :title="(row.category ? '分组: ' + row.category + ' · ' : '') + '点击修改颜色'">
                        <input type="color" :value="row.color || '#d4a24c'"
                               @input="onPresetColorChange(row, $event.target.value)"
                               style="opacity:0;width:100%;height:100%;cursor:pointer">
                      </label>
                    </div>
                    <span v-if="isPresetFillCorner(rIdx, 0)" class="gs-fill-handle" @mousedown="startPresetFillHandle(rIdx, 0, $event)"></span>
                    <span class="gs-row-resizer" @mousedown.stop.prevent="startRowResize(presetRowHeights, row._uid, $event)"></span>
                  </td>

                  <!-- 第 2 列：预设名称 -->
                  <td :class="{
                        'cell-invalid-red': validateRow(row).missingName,
                        'gs-cell-selected': isPresetCellSelected(rIdx, 1),
                        'gs-cell-active': presetEditingCell.rIdx===rIdx && presetEditingCell.cIdx===1
                      }"
                      @mousedown="onPresetCellMouseDown(rIdx, 1, row, $event)"
                      @mouseenter="onPresetCellMouseEnter(rIdx, 1); onCellHoverEnter($event, '预设名称', row.name || '（未填写预设名称）')"
                      @mouseleave="onCellHoverLeave"
                      @dblclick="startEditPresetCell(rIdx, 1)">
                    <input v-if="presetEditingCell.rIdx===rIdx && presetEditingCell.cIdx===1"
                           v-model="row.name"
                           class="ap-cell-input"
                           autofocus
                           placeholder="输入预设名称…"
                           @blur="stopEditPresetCell()"
                           @keydown.enter.prevent="stopEditPresetCell()"
                           @paste="onPresetCellPaste(rIdx, 'name', $event)">
                    <div v-else class="gs-cell-text" :class="textWrapMode" style="font-weight:600">
                      <span v-if="row.name">{{ row.name }}</span>
                      <span v-else class="muted">双击输入预设名称…</span>
                    </div>
                    <span v-if="isPresetFillCorner(rIdx, 1)" class="gs-fill-handle" @mousedown="startPresetFillHandle(rIdx, 1, $event)"></span>
                  </td>

                  <!-- 第 3 列：提示词内容（完全填充单元格 + 富文本显示【人物说的话】插槽 + 双击编辑） -->
                  <td :class="{
                        'cell-invalid-red': validateRow(row).missingTpl || validateRow(row).noNewlineWarn,
                        'gs-cell-selected': isPresetCellSelected(rIdx, 2),
                        'gs-cell-active': presetEditingCell.rIdx===rIdx && presetEditingCell.cIdx===2
                      }"
                      @mousedown="onPresetCellMouseDown(rIdx, 2, row, $event)"
                      @mouseenter="onPresetCellMouseEnter(rIdx, 2); onCellHoverEnter($event, row.name ? ('提示词内容 · ' + row.name) : '提示词内容', row.template)"
                      @mouseleave="onCellHoverLeave"
                      @dblclick="startEditPresetCell(rIdx, 2)">
                    <textarea v-if="presetEditingCell.rIdx===rIdx && presetEditingCell.cIdx===2"
                              v-model="row.template"
                              class="gm3-cell-textarea"
                              style="height:100%;min-height:64px"
                              autofocus
                              placeholder="在此输入提示词内容，在要插入【人物说的话】的位置按回车换行…"
                              @blur="stopEditPresetCell()"
                              @paste="onPresetCellPaste(rIdx, 'template', $event)"></textarea>
                    <div v-else class="gs-cell-text" :class="textWrapMode">
                      <template v-if="!row.template">
                        <span class="muted">双击编辑提示词内容（在台词位置加换行）…</span>
                      </template>
                      <template v-else-if="validateRow(row).slot && validateRow(row).slot.hasNewline">
                        <span>{{ validateRow(row).slot.prefix }}</span>
                        <span class="speech-slot-badge">人物说的话</span>
                        <span>{{ validateRow(row).slot.suffix }}</span>
                      </template>
                      <template v-else>
                        <span>{{ row.template }}</span>
                        <span v-if="validateRow(row).noNewlineWarn" class="tag bad" style="margin-left:6px">⚠️ 需加换行插槽</span>
                      </template>
                    </div>
                    <span v-if="isPresetFillCorner(rIdx, 2)" class="gs-fill-handle" @mousedown="startPresetFillHandle(rIdx, 2, $event)"></span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- ══════════════════════════════════════════════════════════════════
             Part B: 📝 任务提示词总表 (真 Google Sheet 单元格 + 默认隐藏任务行保留色框 + 预设列仅显示名称 + 富文本双击编辑 + 悬停预览)
             ══════════════════════════════════════════════════════════════════ -->
        <div v-if="viewMode==='table' || viewMode==='split'" class="gm3-pane gm3-table-pane">
          <div class="gm3-subbar">
            <button type="button" class="btn sm" @click="selectAllTableSegs(true)">☑ 全选</button>
            <button type="button" class="btn sm" @click="invertTableSegs()">反选</button>
            <button type="button" class="btn sm" @click="selectAllTableSegs(false)">取消</button>
            <span class="tag ok mono">已选 {{ checkedTargets.length }} 段</span>
            <button type="button" class="btn sm tog" :class="{on: showTaskHeaderRows}" @click="showTaskHeaderRows=!showTaskHeaderRows"
                    title="默认隐藏任务行并保留同任务外框颜色；开启后显示任务父级操作行">
              📋 任务行
            </button>
            <button type="button" class="btn sm ghost" @click="insertPromptColumn('right')">➕ 插入列</button>
            <label style="display:flex;align-items:center;gap:4px;font-size:12px;margin-left:4px">
              <input type="checkbox" v-model="onlyCurrentTask"> 仅当前任务
            </label>
            <span class="spacer" style="flex:1"></span>
            <input v-model="tableSearch" type="search" class="trk-tp-search" style="width:170px" placeholder="🔍 搜任务/文案/提示词…">
            <button type="button" class="btn sm tog" :class="{on: dockOpen}" @click="dockOpen=!dockOpen">
              🎴 预设卡片 {{ dockOpen ? '▸' : '◂' }}
            </button>
          </div>

          <div class="gm3-main-split">
            <!-- 左侧：任务提示词总表 (Google Sheet 标准网格) -->
            <div class="gm3-table-scroll" style="flex:1">
              <table class="gs-sheet-table gm3-prompt-table">
                <thead>
                  <!-- 第一行：列头（支持拖拽调节列宽 + 右键插入新提示词列） -->
                  <tr>
                    <th :style="{width: tableColWidths.sel + 'px', textAlign: 'center'}">✓</th>
                    <th :style="{width: tableColWidths.s + 'px'}">
                      段号
                      <span class="gs-col-resizer" @mousedown.stop.prevent="startColResize(tableColWidths, 's', $event)"></span>
                    </th>
                    <th :style="{width: tableColWidths.image + 'px'}">
                      配图
                      <span class="gs-col-resizer" @mousedown.stop.prevent="startColResize(tableColWidths, 'image', $event)"></span>
                    </th>
                    <th :style="{width: tableColWidths.duration + 'px'}">
                      时长
                      <span class="gs-col-resizer" @mousedown.stop.prevent="startColResize(tableColWidths, 'duration', $event)"></span>
                    </th>
                    <th :style="{width: tableColWidths.text + 'px'}">
                      文案字幕
                      <span class="gs-col-resizer" @mousedown.stop.prevent="startColResize(tableColWidths, 'text', $event)"></span>
                    </th>
                    <th :style="{width: tableColWidths.preset + 'px'}">
                      预设
                      <span class="gs-col-resizer" @mousedown.stop.prevent="startColResize(tableColWidths, 'preset', $event)"></span>
                    </th>
                    <th v-for="(col, cIdx) in orderedPromptColumns" :key="col.col_id"
                        :style="{width: (tableColWidths[col.col_id] || 420) + 'px'}"
                        @contextmenu.prevent="openColContextMenu(col, cIdx, $event)"
                        title="右键可在此列左侧/右侧插入新的提示词列">
                      <div style="display:flex;align-items:center;justify-content:space-between;gap:6px">
                        <span>🎯 {{ col.name }}</span>
                        <button type="button" class="btn sm ghost" style="padding:0 5px;font-size:11px"
                                @click.stop="openColContextMenu(col, cIdx, $event)">⋮</button>
                      </div>
                      <span class="gs-col-resizer" @mousedown.stop.prevent="startColResize(tableColWidths, col.col_id, $event)"></span>
                    </th>
                  </tr>
                  <!-- 第二行：批量功能控制行 -->
                  <tr class="gm3-func-row">
                    <th colspan="6" style="text-align:right;font-weight:600;color:var(--accent)">
                      ⚡ 批量配词（对勾选的 {{ checkedTargets.length }} 段生效）👉
                    </th>
                    <th v-for="col in orderedPromptColumns" :key="'fn-' + col.col_id">
                      <div class="gm3-col-controller">
                        <select class="ap-cell-select" :value="col.header_mode || 'single'"
                                @change="setColHeaderMode(col, $event.target.value)">
                          <option value="single">📋 直接选预设</option>
                          <option value="gemini_template">✨ Gemini 模板 (同图复用)</option>
                          <option value="random_group">🎲 随机提示词组</option>
                        </select>
                        <select v-if="(col.header_mode || 'single') === 'random_group'"
                                class="ap-cell-select" :value="col.header_group || ''"
                                @change="setColHeaderSecondVal(col, $event.target.value)">
                          <option value="">— 选择分组 —</option>
                          <option v-for="cat in presetCategories" :key="cat" :value="cat">🎲 {{ cat }}</option>
                        </select>
                        <select v-else
                                class="ap-cell-select" :value="col.header_preset_id || ''"
                                @change="setColHeaderSecondVal(col, $event.target.value)">
                          <option value="">— 选择预设 —</option>
                          <option v-for="p in validPublishedPresets" :key="p.id" :value="p.id">
                            {{ p.category ? '[' + p.category + '] ' : '' }}{{ p.name }}
                          </option>
                        </select>
                        <button type="button" class="btn sm primary" @click="runColumnBatchAction(col)">执行</button>
                      </div>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="!flatTableRows.length && !visibleTasksInTable.length">
                    <td :colspan="6 + orderedPromptColumns.length" class="muted" style="text-align:center;padding:32px">
                      暂无已拆段的任务，请先在「1 任务表」准备任务或在「2 排镜头」查看。
                    </td>
                  </tr>

                  <!-- 当开启「任务行」时，渲染任务分组头；默认隐藏任务行，仅保留同组颜色外框 -->
                  <template v-for="(item) in flatTableRows" :key="item.rowKey">
                    <tr v-if="showTaskHeaderRows && item.isFirstInTask"
                        class="trk-task-group-row"
                        :style="{'--task-accent': item.groupColor}"
                        @dragover.prevent @drop.prevent="onDropPresetOnTask(item.tk)">
                      <td style="text-align:center">
                        <input type="checkbox" :checked="isTaskAllChecked(item.tk)" @change="toggleTaskCheck(item.tk)">
                      </td>
                      <td colspan="5">
                        <div style="display:flex;align-items:center;gap:8px">
                          <span class="trk-task-badge" :style="{background: item.groupColor + '22', borderColor: item.groupColor, color: item.groupColor}">
                            {{ item.tk.id }}
                          </span>
                          <b>{{ item.tk.name || item.tk.id }}</b>
                          <span class="muted mono" style="font-size:12px">({{ (item.tk.segments || []).length }} 段 · {{ item.tk.image_count || 0 }} 图)</span>
                        </div>
                      </td>
                      <td :colspan="orderedPromptColumns.length">
                        <div style="display:flex;align-items:center;gap:8px">
                          <span class="muted" style="font-size:12px">📌 任务总预设:</span>
                          <select class="ap-cell-select" style="max-width:220px" :value="item.tk.preset_id || ''"
                                  @change="applyPresetToTask(item.tk, $event.target.value)">
                            <option value="">— 选择预设应用到 {{ item.tk.id }} 全段 —</option>
                            <option v-for="p in validPublishedPresets" :key="p.id" :value="p.id">{{ p.name }}</option>
                          </select>
                        </div>
                      </td>
                    </tr>

                    <!-- 标准 Google Sheet 分段数据行（带同任务外框颜色） -->
                    <tr :class="{
                          'row-selected': isSegChecked(item.tk.id, item.sg.s),
                          'gs-group-row': true,
                          'gs-group-first': item.isFirstInTask && !showTaskHeaderRows,
                          'gs-group-last': item.isLastInTask
                        }"
                        :style="{
                          '--group-border-color': item.groupColor,
                          '--task-accent': item.groupColor,
                          height: (tableRowHeights[item.rowKey] || 42) + 'px'
                        }">
                      <!-- 勾选列 -->
                      <td class="ap-td-sel"
                          @mousedown.prevent="startSegDrag(item.tk.id, item.sg.s, $event)"
                          @mouseenter="enterSegDrag(item.tk.id, item.sg.s)">
                        <input type="checkbox" :checked="isSegChecked(item.tk.id, item.sg.s)" tabindex="-1" style="pointer-events:none">
                        <span class="gs-row-resizer" @mousedown.stop.prevent="startRowResize(tableRowHeights, item.rowKey, $event)"></span>
                      </td>

                      <!-- 列 0: 段号（首行附带可点击的任务胶囊） -->
                      <td class="mono"
                          :class="{'gs-cell-selected': isTableCellSelected(item.flatIdx, 0)}"
                          @mousedown="onTableCellMouseDown(item.flatIdx, 0, $event)"
                          @mouseenter="onTableCellMouseEnter(item.flatIdx, 0)">
                        <div style="display:flex;align-items:center;gap:4px">
                          <span v-if="item.isFirstInTask && !showTaskHeaderRows"
                                class="gs-task-inline-pill"
                                :style="{background: item.groupColor + '22', borderColor: item.groupColor, color: item.groupColor}"
                                title="点击切换展开任务行"
                                @click.stop="showTaskHeaderRows = !showTaskHeaderRows">
                            {{ item.tk.id }}
                          </span>
                          <span>s{{ pad3(item.sg.s) }}</span>
                        </div>
                        <span v-if="isTableFillCorner(item.flatIdx, 0)" class="gs-fill-handle" @mousedown="startTableFillHandle(item.flatIdx, 0, $event)"></span>
                      </td>

                      <!-- 列 1: 配图 -->
                      <td class="mono"
                          :class="{'gs-cell-selected': isTableCellSelected(item.flatIdx, 1)}"
                          @mousedown="onTableCellMouseDown(item.flatIdx, 1, $event)"
                          @mouseenter="onTableCellMouseEnter(item.flatIdx, 1); onCellHoverEnter($event, item.tk.id + ' s' + pad3(item.sg.s) + ' 配图', item.sg.image || 'p01')"
                          @mouseleave="onCellHoverLeave">
                        {{ item.sg.image || 'p01' }}
                        <span v-if="isTableFillCorner(item.flatIdx, 1)" class="gs-fill-handle" @mousedown="startTableFillHandle(item.flatIdx, 1, $event)"></span>
                      </td>

                      <!-- 列 2: 时长 -->
                      <td class="mono"
                          :class="{'gs-cell-selected': isTableCellSelected(item.flatIdx, 2)}"
                          @mousedown="onTableCellMouseDown(item.flatIdx, 2, $event)"
                          @mouseenter="onTableCellMouseEnter(item.flatIdx, 2)">
                        {{ item.sg.duration || 6 }}s
                        <span v-if="isTableFillCorner(item.flatIdx, 2)" class="gs-fill-handle" @mousedown="startTableFillHandle(item.flatIdx, 2, $event)"></span>
                      </td>

                      <!-- 列 3: 文案字幕（富文本显示 + 双击编辑 + 悬停预览） -->
                      <td :class="{
                            'gs-cell-selected': isTableCellSelected(item.flatIdx, 3),
                            'gs-cell-active': tableEditingCell.rIdx===item.flatIdx && tableEditingCell.cIdx===3
                          }"
                          @mousedown="onTableCellMouseDown(item.flatIdx, 3, $event)"
                          @mouseenter="onTableCellMouseEnter(item.flatIdx, 3); onCellHoverEnter($event, item.tk.id + ' s' + pad3(item.sg.s) + ' 文案字幕', item.sg.text)"
                          @mouseleave="onCellHoverLeave"
                          @dblclick="startEditTableCell(item.flatIdx, 3)">
                        <textarea v-if="tableEditingCell.rIdx===item.flatIdx && tableEditingCell.cIdx===3"
                                  class="gm3-cell-textarea"
                                  :value="item.sg.text || ''"
                                  autofocus
                                  @change="updateSegmentField(item.tk.id, item.sg, 'text', $event.target.value, 'main')"
                                  @blur="stopEditTableCell()"
                                  @paste="onTableCellPaste(item.tk, item.sIdx, 'text', $event)"></textarea>
                        <div v-else class="gs-cell-text" :class="textWrapMode">
                          {{ item.sg.text || '' }}
                        </div>
                        <span v-if="isTableFillCorner(item.flatIdx, 3)" class="gs-fill-handle" @mousedown="startTableFillHandle(item.flatIdx, 3, $event)"></span>
                      </td>

                      <!-- 列 4: 预设（严格只显示预设名称，不显示预设内容！） -->
                      <td :class="{
                            'gs-cell-selected': isTableCellSelected(item.flatIdx, 4),
                            'gs-cell-active': tableEditingCell.rIdx===item.flatIdx && tableEditingCell.cIdx===4
                          }"
                          @mousedown="onTableCellMouseDown(item.flatIdx, 4, $event)"
                          @mouseenter="onTableCellMouseEnter(item.flatIdx, 4); onCellHoverEnter($event, '当前预设名称', presetNameById(item.sg.preset_id) || '未指定预设')"
                          @mouseleave="onCellHoverLeave"
                          @dblclick="startEditTableCell(item.flatIdx, 4)">
                        <select class="ap-cell-select"
                                :value="item.sg.preset_id || ''"
                                @change="applyPresetToSegment(item.tk, item.sg, 'main', validPublishedPresets.find(x => x.id === $event.target.value))">
                          <option value="">— 选择预设 —</option>
                          <option v-for="p in validPublishedPresets" :key="p.id" :value="p.id">{{ p.name }}</option>
                        </select>
                        <span v-if="isTableFillCorner(item.flatIdx, 4)" class="gs-fill-handle" @mousedown="startTableFillHandle(item.flatIdx, 4, $event)"></span>
                      </td>

                      <!-- 列 5+: 提示词列（单元格富文本，默认自动换行/可切换截断，双击编辑，悬停预览） -->
                      <td v-for="(col, cIdx) in orderedPromptColumns" :key="item.rowKey + ':' + col.col_id"
                          :class="{
                            'gs-cell-selected': isTableCellSelected(item.flatIdx, 5 + cIdx),
                            'gs-cell-active': tableEditingCell.rIdx===item.flatIdx && tableEditingCell.cIdx===(5 + cIdx)
                          }"
                          @mousedown="onTableCellMouseDown(item.flatIdx, 5 + cIdx, $event)"
                          @mouseenter="onTableCellMouseEnter(item.flatIdx, 5 + cIdx); onCellHoverEnter($event, item.tk.id + ' s' + pad3(item.sg.s) + ' · ' + col.name, getPromptCellValue(item.sg, col))"
                          @mouseleave="onCellHoverLeave"
                          @dblclick="startEditTableCell(item.flatIdx, 5 + cIdx)"
                          @contextmenu.prevent="openColContextMenu(col, cIdx, $event)"
                          @dragover.prevent
                          @drop.prevent="onDropPresetOnCell(item.tk, item.sg, col.col_id)">
                        <textarea v-if="tableEditingCell.rIdx===item.flatIdx && tableEditingCell.cIdx===(5 + cIdx)"
                                  class="gm3-cell-textarea"
                                  style="height:100%;min-height:56px"
                                  :value="getPromptCellValue(item.sg, col)"
                                  autofocus
                                  placeholder="输入提示词内容…"
                                  @change="updateSegmentField(item.tk.id, item.sg, 'custom_prompt', $event.target.value, col.col_id)"
                                  @blur="stopEditTableCell()"
                                  @paste="onTableCellPaste(item.tk, item.sIdx, col.col_id, $event)"></textarea>
                        <div v-else class="gs-cell-text" :class="textWrapMode">
                          {{ getPromptCellValue(item.sg, col) }}
                        </div>
                        <span v-if="isTableFillCorner(item.flatIdx, 5 + cIdx)" class="gs-fill-handle" @mousedown="startTableFillHandle(item.flatIdx, 5 + cIdx, $event)"></span>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>

            <!-- 右侧：🎴 预设卡片库 -->
            <aside v-if="dockOpen" class="gm3-card-dock">
              <div class="gm3-dock-hd">
                <b>🎴 预设卡片库</b>
                <small class="muted">拖拽到左侧单元格，或点击卡片填入勾选行</small>
                <input v-model="dockSearch" type="search" class="ap-cell-input" style="margin-top:6px" placeholder="🔍 筛选卡片…">
                <div class="ap-pills" style="margin-top:6px">
                  <button type="button" class="ap-pill" :class="{active: dockCat==='all'}" @click="dockCat='all'">全部</button>
                  <button v-for="cat in presetCategories" :key="'d-'+cat" type="button" class="ap-pill"
                          :class="{active: dockCat===cat}" @click="dockCat=cat">{{ cat }}</button>
                </div>
              </div>
              <div class="gm3-dock-list">
                <div v-for="p in dockPresets" :key="p.id" class="gm3-preset-card"
                     draggable="true"
                     :style="{'--card-color': p.color || '#d4a24c'}"
                     @dragstart="onDragStartPreset(p, $event)"
                     @click="applyCardToChecked(p)"
                     @contextmenu.prevent="applyCardToChecked(p)">
                  <div class="gm3-card-top">
                    <span class="gm3-card-cat" :style="{color: p.color || '#d4a24c'}">● {{ p.category || '默认' }}</span>
                    <b>{{ p.name }}</b>
                    <button type="button" class="btn sm ghost" style="margin-left:auto;padding:1px 6px;font-size:11px"
                            @click.stop="applyCardToChecked(p)">填入</button>
                  </div>
                  <div class="gm3-card-body">
                    <template v-if="p.slot && p.slot.hasNewline">
                      <span>{{ p.slot.prefix }}</span>
                      <span class="speech-slot-badge">人物说的话</span>
                      <span>{{ p.slot.suffix }}</span>
                    </template>
                    <template v-else>{{ p.template }}</template>
                  </div>
                </div>
              </div>
            </aside>
          </div>
        </div>
      </div>

      <!-- 鼠标悬停单元格内容预览浮层 (Google Sheet Cell Hover Preview) -->
      <div v-if="hoverPopover.visible && hoverPopover.content"
           class="gs-cell-hover-popover"
           :style="{left: hoverPopover.x + 'px', top: hoverPopover.y + 'px'}">
        <div v-if="hoverPopover.title" class="gs-pop-title">{{ hoverPopover.title }}</div>
        <div>{{ hoverPopover.content }}</div>
      </div>

      <!-- 📚 预设管理右键菜单（创建分组 / 加入分组 / 移出分组 / 查看全部分组） -->
      <ul v-if="presetCtxMenu.open" class="ctx" :style="{left: presetCtxMenu.x + 'px', top: presetCtxMenu.y + 'px', minWidth: '220px'}" @click.stop>
        <li @click="createPresetGroupFromSelection()">🎨 创建新分组 (选中行同色外框)</li>
        <li v-if="presetGroupsSummary.length" class="sep"></li>
        <li v-for="g in presetGroupsSummary" :key="'join-' + g.name" @click="addSelectionToPresetGroup(g.name)">
          <span :style="{color: g.color, fontWeight: 700}">●</span> 加入分组【{{ g.name }}】
        </li>
        <li @click="removeSelectionFromPresetGroup()">📤 从分组中移出选中行</li>
        <li class="sep"></li>
        <li @click="filterPresetByGroup('all')">
          👁️ 查看全部分组 ({{ presetRows.length }} 行)
        </li>
        <li v-for="g in presetGroupsSummary" :key="'view-' + g.name" @click="filterPresetByGroup(g.name)">
          🔍 仅看分组【{{ g.name }}】({{ g.count }} 行)
        </li>
        <li class="sep"></li>
        <li @click="presetCtxMenu.open=false; addPresetRows(1)">➕ 新增一行预设</li>
        <li @click="presetCtxMenu.open=false; copySelectedPresetCellsToClipboard()">📋 复制选中单元格 (Ctrl+C)</li>
        <li class="bad" @click="presetCtxMenu.open=false; deleteSelectedPresets()">🗑️ 删除选中预设行</li>
      </ul>

      <!-- 提示词列右键菜单（向左/向右插入列、重命名、删除） -->
      <ul v-if="colMenu.open" class="ctx" :style="{left: colMenu.x + 'px', top: colMenu.y + 'px'}" @click.stop>
        <li @click="insertPromptColumn('left')">⬅️ 在左侧插入提示词列</li>
        <li @click="insertPromptColumn('right')">➡️ 在右侧插入提示词列</li>
        <li class="sep"></li>
        <li :class="{off: colMenu.colId==='main'}" @click="renamePromptColumn()">✏️ 重命名此提示词列</li>
        <li class="bad" :class="{off: colMenu.colId==='main'}" @click="deletePromptColumn()">🗑️ 删除此提示词列</li>
      </ul>

      <!-- 查找与替换弹窗 (Ctrl+H) -->
      <div class="rs-mask" v-if="findReplaceModal.open" @click.self="findReplaceModal.open=false">
        <div class="rs-box" style="width:420px;max-width:94vw">
          <h3 style="margin-top:0">🔍 批量查找与替换 (Ctrl+H)</h3>
          <div class="field">
            <label>作用范围</label>
            <select v-model="findReplaceModal.target">
              <option value="table">📝 任务提示词总表</option>
              <option value="presets">📚 预设管理库</option>
            </select>
          </div>
          <div class="field"><label>查找内容</label><input v-model="findReplaceModal.find" placeholder="输入要查找的词…"></div>
          <div class="field"><label>替换为</label><input v-model="findReplaceModal.replace" placeholder="输入替换后的内容…" @keyup.enter="executeFindReplace"></div>
          <div class="rs-acts" style="margin-top:14px">
            <button class="btn" type="button" @click="findReplaceModal.open=false">取消</button>
            <button class="btn primary" type="button" @click="executeFindReplace">全部替换</button>
          </div>
        </div>
      </div>

      <!-- Gemini API Key 配置弹窗 -->
      <div class="rs-mask" v-if="geminiCfgModal.open" @click.self="geminiCfgModal.open=false">
        <div class="rs-box" style="width:440px;max-width:94vw">
          <h3 style="margin-top:0">🔑 Gemini API 设置</h3>
          <div class="field">
            <label>Gemini API Key</label>
            <input v-model="geminiCfgModal.apiKey" type="password" placeholder="AIzaSy...">
          </div>
          <div class="field">
            <label>默认视觉描述模型</label>
            <select v-model="geminiCfgModal.model">
              <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite (推荐·极速)</option>
              <option value="gemini-2.5-flash">gemini-2.5-flash</option>
              <option value="gemini-2.5-pro">gemini-2.5-pro</option>
            </select>
          </div>
          <div class="rs-acts" style="margin-top:14px">
            <button class="btn" type="button" @click="geminiCfgModal.open=false">取消</button>
            <button class="btn primary" type="button" :disabled="geminiCfgModal.saving" @click="saveGeminiCfg">保存</button>
          </div>
        </div>
      </div>
    </section>
    `,
  };
})();
