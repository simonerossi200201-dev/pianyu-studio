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

      // ─── Part A: 📚 预设管理 (Google Sheet 预设库) ───
      const presetRows = ref([]);
      const presetFilterCat = ref('all');
      const presetSearch = ref('');
      const presetSelected = reactive(new Set());
      const presetDrag = reactive({ active: false, val: true });
      const presetSaving = ref(false);
      const findReplaceModal = reactive({ open: false, find: '', replace: '', target: 'presets' });

      // ─── Part B: 📝 任务提示词总表 (Synchronized with 2 排镜头) + 🎴 预设卡片库 ───
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
      const selectedCells = reactive(new Set()); // `${taskId}:${s}:${colId}`
      const dockSearch = ref('');
      const dockCat = ref('all');
      const dockOpen = ref(true);
      const tableSearch = ref('');
      const onlyCurrentTask = ref(false);
      const draggingPreset = ref(null);

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
        return {
          _uid: nextUid(),
          id: '',
          category: cat || (presetFilterCat.value !== 'all' ? presetFilterCat.value : '口播主词'),
          color: '#d4a24c',
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
          presetRows.value = list.map((p, i) => ({
            _uid: p.id || nextUid(),
            id: p.id || ('preset_' + (i + 1)),
            category: p.category || '默认分组',
            color: p.color || TASK_COLORS[i % TASK_COLORS.length],
            name: p.name || '',
            role: p.role || 'main',
            flow_mode: p.flow_mode || 'auto',
            template: p.template || '',
          }));
        } else if (!presetRows.value.length) {
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
              color: '#3b82f6',
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
        if (!presetSelected.size) {
          toast('请先勾选要删除的预设行', '', 'warn');
          return;
        }
        presetRows.value = presetRows.value.filter(r => !presetSelected.has(r._uid));
        presetSelected.clear();
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
          toast(`🚀 已发布保存 ${nonEmpty.length} 条预设，已同步至 2 排镜头 与右侧卡片库`, '', 'ok');
          await loadPresets(true);
        } catch (e) { /* API toast */ } finally {
          presetSaving.value = false;
        }
      }

      // Multi-row paste into Preset table (Ctrl+V from Excel / Google Sheet)
      function onPresetCellPaste(rowIdx, field, ev) {
        const text = ev.clipboardData && ev.clipboardData.getData('text/plain');
        if (!text) return;
        // If pasting inside template and there are no tabs, allow normal multiline paste with newlines!
        if (field === 'template' && !text.includes('\t')) return;
        if (!text.includes('\t') && !text.includes('\n')) return;
        ev.preventDefault();
        const rows = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
        while (rows.length && rows[rows.length - 1] === '') rows.pop();
        const colOrder = ['category', 'name', 'role', 'template'];
        const startCol = Math.max(0, colOrder.indexOf(field));
        for (let r = 0; r < rows.length; r++) {
          while (presetRows.value.length <= rowIdx + r) {
            presetRows.value.push(makeEmptyPresetRow());
          }
          const targetRow = presetRows.value[rowIdx + r];
          const cols = rows[r].split('\t');
          if (cols.length === 2 && field === 'name') {
            targetRow.name = cols[0].trim();
            targetRow.template = cols[1];
          } else if (cols.length >= 3 && field === 'category') {
            targetRow.category = cols[0].trim() || '默认分组';
            targetRow.name = cols[1].trim();
            targetRow.template = cols.slice(2).join('\n');
          } else {
            for (let c = 0; c < cols.length; c++) {
              const f = colOrder[startCol + c];
              if (f) targetRow[f] = cols[c];
            }
          }
        }
        toast(`已从剪贴板粘贴 ${rows.length} 行预设`, '', 'ok');
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

      // ─── Part B: Prompt Table Selection, Columns & Batch Actions ───
      function taskColor(idx, tk) {
        return (tk && tk.color) || TASK_COLORS[idx % TASK_COLORS.length];
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

      function stopAllDrags() {
        presetDrag.active = false;
        segDrag.active = false;
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

      function onGlobalKeyDown(ev) {
        if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'h' || ev.key === 'H')) {
          ev.preventDefault();
          openFindReplace();
        }
        if (ev.key === 'Escape') {
          colMenu.open = false;
        }
      }

      function onGlobalClick() {
        if (colMenu.open) colMenu.open = false;
      }

      onMounted(() => {
        loadPresets(false);
        loadTasksSegmentsTable(false);
        window.addEventListener('mouseup', stopAllDrags);
        window.addEventListener('keydown', onGlobalKeyDown);
        document.addEventListener('click', onGlobalClick);
      });

      onBeforeUnmount(() => {
        window.removeEventListener('mouseup', stopAllDrags);
        window.removeEventListener('keydown', onGlobalKeyDown);
        document.removeEventListener('click', onGlobalClick);
      });

      watch(() => store.refreshSeq, () => {
        loadPresets(true);
        loadTasksSegmentsTable(true);
      });

      return {
        ui, store, t, pad3,
        viewMode,
        // Part A: Presets
        presetRows, presetFilterCat, presetSearch, presetSelected, presetSaving,
        presetCategories, validPublishedPresets, filteredPresetRows, invalidPresetCount,
        validateRow, addPresetRows, deleteSelectedPresets, publishPresets,
        onPresetCellPaste, startPresetDrag, enterPresetDrag,
        // Part B: Tasks Prompt Table & Dock
        tasksTable, extraColumns, mainColControl, collapsedTasks, segChecked, selectedCells,
        dockSearch, dockCat, dockOpen, tableSearch, onlyCurrentTask,
        visibleTasksInTable, checkedTargets, dockPresets, orderedPromptColumns,
        colMenu, findReplaceModal, geminiCfgModal,
        taskColor, isSegChecked, startSegDrag, enterSegDrag,
        isTaskAllChecked, toggleTaskCheck, selectAllTableSegs, invertTableSegs, expandAllTasks,
        openColContextMenu, insertPromptColumn, renamePromptColumn, deletePromptColumn,
        setColHeaderMode, setColHeaderSecondVal, runColumnBatchAction,
        getPromptCellValue, updateSegmentField, onTableCellPaste,
        onDragStartPreset, onDropPresetOnCell, onDropPresetOnTask, applyPresetToTask, applyCardToChecked,
        openFindReplace, executeFindReplace, openGeminiCfg, saveGeminiCfg,
      };
    },
    template: `
    <section class="panel gm3-panel">
      <!-- 顶部模式切换栏 (Less is More: 预设管理 ⇄ 任务提示词总表 ⇄ 双栏同屏) -->
      <div class="gm3-topbar">
        <div class="gm3-mode-tabs">
          <button type="button" class="gm3-tab" :class="{active: viewMode==='table'}" @click="viewMode='table'">
            📝 任务提示词总表 <small>(同步 2 排镜头 · 批量配词)</small>
          </button>
          <button type="button" class="gm3-tab" :class="{active: viewMode==='presets'}" @click="viewMode='presets'">
            📚 预设管理 <small>(Google Sheet 预设库)</small>
            <span v-if="invalidPresetCount" class="tag bad" style="margin-left:4px">{{ invalidPresetCount }} 待修正</span>
          </button>
          <button type="button" class="gm3-tab" :class="{active: viewMode==='split'}" @click="viewMode='split'">
            🔲 双栏同屏
          </button>
        </div>
        <span class="spacer" style="flex:1"></span>
        <button type="button" class="btn sm ghost" @click="openFindReplace()">🔍 查找替换 (Ctrl+H)</button>
        <button type="button" class="btn sm ghost" @click="openGeminiCfg()">🔑 Gemini API 设置</button>
        <button type="button" class="btn sm" @click="ui.node='track'">← 返回 2 排镜头</button>
        <button type="button" class="btn sm primary" @click="ui.node='generate'">前往 4 生成 →</button>
      </div>

      <div class="gm3-workspace" :class="'mode-' + viewMode">
        <!-- ══════════════════════════════════════════════════════════════════
             Part A: 📚 预设管理 (Google Sheet 风格预设库 + 实时红框校验 + 最大换行处【人物说的话】红字预览)
             ══════════════════════════════════════════════════════════════════ -->
        <div v-if="viewMode==='presets' || viewMode==='split'" class="gm3-pane gm3-presets-pane">
          <div class="gm3-subbar">
            <button type="button" class="btn sm primary" :disabled="presetSaving" @click="publishPresets">
              🚀 发布/保存预设 ({{ presetRows.length }})
            </button>
            <button type="button" class="btn sm" @click="addPresetRows(1)">➕ 新增一行</button>
            <button type="button" class="btn sm" @click="addPresetRows(5)">➕ 新增 5 行</button>
            <button v-if="presetSelected.size" type="button" class="btn sm danger" @click="deleteSelectedPresets">
              🗑️ 删除选中 ({{ presetSelected.size }})
            </button>
            <div class="ap-pills" style="margin-left:8px">
              <button type="button" class="ap-pill" :class="{active: presetFilterCat==='all'}" @click="presetFilterCat='all'">全部</button>
              <button v-for="cat in presetCategories" :key="cat" type="button" class="ap-pill"
                      :class="{active: presetFilterCat===cat}" @click="presetFilterCat=cat">
                {{ cat }}
              </button>
            </div>
            <span class="spacer" style="flex:1"></span>
            <input v-model="presetSearch" type="search" class="trk-tp-search" style="width:180px" placeholder="🔍 搜索预设名称/内容…">
          </div>

          <div class="gm3-preset-hint">
            💡 <b>Google Sheet 预设规则</b>：支持直接从 Excel / Google Sheet 复制多行粘贴（<code>Ctrl+V</code>）。主提示词请在要插入台词的位置<b>添加换行</b>（若有多处换行，系统自动取<b>换行最多</b>的位置插入红字 <span class="speech-slot-badge">人物说的话</span>）。缺少名称或描述词会以红框标出。
          </div>

          <div class="gm3-table-scroll">
            <table class="ap-sheet-table gm3-preset-table">
              <thead>
                <tr>
                  <th style="width:36px;text-align:center">✓</th>
                  <th style="width:130px">分组 / 父级</th>
                  <th style="width:52px;text-align:center">颜色</th>
                  <th style="width:180px">预设名称 (必填)</th>
                  <th style="width:130px">预设类型</th>
                  <th style="width:38%">描述词模板 (必填 · Ctrl+V 多行粘贴 · 换行处插入说话内容)</th>
                  <th style="width:32%">实时预览 & 校验状态 (自动识别【人物说的话】插槽)</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, rIdx) in filteredPresetRows" :key="row._uid"
                    :class="{'row-selected': presetSelected.has(row._uid)}">
                  <td class="ap-td-sel"
                      @mousedown.prevent="startPresetDrag(row, $event)"
                      @mouseenter="enterPresetDrag(row)">
                    <input type="checkbox" :checked="presetSelected.has(row._uid)" tabindex="-1" style="pointer-events:none">
                  </td>
                  <td>
                    <input v-model="row.category" class="ap-cell-input" placeholder="如：口播主词"
                           @paste="onPresetCellPaste(rIdx, 'category', $event)">
                  </td>
                  <td style="text-align:center">
                    <input type="color" v-model="row.color" style="width:28px;height:24px;border:none;background:transparent;cursor:pointer">
                  </td>
                  <td :class="{'cell-invalid-red': validateRow(row).missingName}">
                    <input v-model="row.name" class="ap-cell-input"
                           :placeholder="validateRow(row).missingName ? '❌ 请填写预设名称' : '输入预设名称…'"
                           @paste="onPresetCellPaste(rIdx, 'name', $event)">
                  </td>
                  <td>
                    <select v-model="row.role" class="ap-cell-select">
                      <option value="main">🗣️ 主提示词 (含说话插槽)</option>
                      <option value="gemini_tpl">✨ Gemini 看图模板</option>
                      <option value="expand">🎨 扩图预设</option>
                      <option value="tail">📌 固定结语</option>
                    </select>
                  </td>
                  <td :class="{'cell-invalid-red': validateRow(row).missingTpl || validateRow(row).noNewlineWarn}">
                    <textarea v-model="row.template" rows="3" class="gm3-tpl-textarea"
                              :placeholder="validateRow(row).missingTpl ? '❌ 请填写描述词模板（在说话位置加换行）' : '在此输入描述词，在要插入【人物说的话】处按回车换行…'"
                              @paste="onPresetCellPaste(rIdx, 'template', $event)"></textarea>
                  </td>
                  <td class="gm3-preview-td">
                    <template v-if="validateRow(row).empty">
                      <span class="muted">（空行）</span>
                    </template>
                    <template v-else-if="validateRow(row).missingName || validateRow(row).missingTpl">
                      <div class="gm3-err-text">{{ validateRow(row).error }}</div>
                    </template>
                    <template v-else-if="validateRow(row).noNewlineWarn">
                      <div class="gm3-err-text">⚠️ 请在要填写内容的地方添加换行</div>
                      <div class="gm3-slot-preview muted" style="margin-top:4px">{{ row.template }}</div>
                    </template>
                    <template v-else-if="validateRow(row).slot && validateRow(row).slot.hasNewline">
                      <div class="gm3-slot-preview">
                        <span>{{ validateRow(row).slot.prefix }}</span>
                        <span class="speech-slot-badge">人物说的话</span>
                        <span>{{ validateRow(row).slot.suffix }}</span>
                      </div>
                      <div class="gm3-ok-badge">✓ 插槽有效（最大连续 {{ validateRow(row).slot.maxNewlines }} 换行处）</div>
                    </template>
                    <template v-else>
                      <div class="gm3-slot-preview">{{ row.template }}</div>
                      <div class="gm3-ok-badge">✓ 预设有效</div>
                    </template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- ══════════════════════════════════════════════════════════════════
             Part B: 📝 任务提示词总表 (同步 2 排镜头) + 右侧 🎴 预设卡片库
             ══════════════════════════════════════════════════════════════════ -->
        <div v-if="viewMode==='table' || viewMode==='split'" class="gm3-pane gm3-table-pane">
          <div class="gm3-subbar">
            <button type="button" class="btn sm" @click="selectAllTableSegs(true)">☑ 全选分段</button>
            <button type="button" class="btn sm" @click="invertTableSegs()">🔄 反选</button>
            <button type="button" class="btn sm" @click="selectAllTableSegs(false)">☐ 取消全选</button>
            <span class="tag ok mono">已勾选 {{ checkedTargets.length }} 段 (仅勾选行生效)</span>
            <button type="button" class="btn sm ghost" @click="expandAllTasks(false)">▾ 展开全部任务</button>
            <button type="button" class="btn sm ghost" @click="expandAllTasks(true)">▸ 收起全部任务</button>
            <button type="button" class="btn sm ghost" @click="insertPromptColumn('right')">➕ 插入提示词列</button>
            <label style="display:flex;align-items:center;gap:4px;font-size:12px;margin-left:6px">
              <input type="checkbox" v-model="onlyCurrentTask"> 仅看当前任务 ({{ ui.panelTid || '全部' }})
            </label>
            <span class="spacer" style="flex:1"></span>
            <input v-model="tableSearch" type="search" class="trk-tp-search" style="width:170px" placeholder="🔍 搜任务/文案/提示词…">
            <button type="button" class="btn sm tog" :class="{on: dockOpen}" @click="dockOpen=!dockOpen">
              🎴 预设卡片库 {{ dockOpen ? '▸' : '◂' }}
            </button>
          </div>

          <div class="gm3-main-split">
            <!-- 左侧：任务提示词总表 -->
            <div class="gm3-table-scroll" style="flex:1">
              <table class="ap-sheet-table gm3-prompt-table">
                <thead>
                  <!-- 第一行：表头名称（右键可左/右插入新提示词列） -->
                  <tr>
                    <th style="width:36px;text-align:center">勾选</th>
                    <th style="width:64px">段号</th>
                    <th style="width:82px">配图</th>
                    <th style="width:62px">时长</th>
                    <th style="width:24%">文案字幕 (人物说的话 · 可直接编辑/粘贴)</th>
                    <th v-for="(col, cIdx) in orderedPromptColumns" :key="col.col_id"
                        style="min-width:290px"
                        @contextmenu.prevent="openColContextMenu(col, cIdx, $event)"
                        title="右键可在此列左侧/右侧插入新的提示词列">
                      <div style="display:flex;align-items:center;justify-content:space-between;gap:6px">
                        <span>🎯 {{ col.name }}</span>
                        <button type="button" class="btn sm ghost" style="padding:0 5px;font-size:11px"
                                @click.stop="openColContextMenu(col, cIdx, $event)" title="列菜单：左/右插入列">⋮</button>
                      </div>
                    </th>
                  </tr>
                  <!-- 第二行：首行功能行（每列独立的二合一批量控制器：直接选预设 / Gemini按模板生成 / 随机提示词组） -->
                  <tr class="gm3-func-row">
                    <th colspan="5" style="text-align:right;font-weight:600;color:var(--accent)">
                      ⚡ 首行批量功能行（仅对左侧已勾选的 {{ checkedTargets.length }} 个分段生效）👉
                    </th>
                    <th v-for="col in orderedPromptColumns" :key="'fn-' + col.col_id">
                      <div class="gm3-col-controller">
                        <select class="ap-cell-select" :value="col.header_mode || 'single'"
                                @change="setColHeaderMode(col, $event.target.value)">
                          <option value="single">📋 1. 直接选预设 (整列应用)</option>
                          <option value="gemini_template">✨ 2. Gemini 生成描述语 (同图复用)</option>
                          <option value="random_group">🎲 3. 随机应用提示词组</option>
                        </select>
                        <select v-if="(col.header_mode || 'single') === 'random_group'"
                                class="ap-cell-select" :value="col.header_group || ''"
                                @change="setColHeaderSecondVal(col, $event.target.value)">
                          <option value="">— 选择提示词分组 —</option>
                          <option v-for="cat in presetCategories" :key="cat" :value="cat">🎲 分组: {{ cat }}</option>
                        </select>
                        <select v-else
                                class="ap-cell-select" :value="col.header_preset_id || ''"
                                @change="setColHeaderSecondVal(col, $event.target.value)">
                          <option value="">— 选择预设模板 —</option>
                          <option v-for="p in validPublishedPresets" :key="p.id" :value="p.id">
                            {{ p.category ? '[' + p.category + '] ' : '' }}{{ p.name }}
                          </option>
                        </select>
                        <button type="button" class="btn sm primary" @click="runColumnBatchAction(col)">
                          执行
                        </button>
                      </div>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="!visibleTasksInTable.length">
                    <td :colspan="5 + orderedPromptColumns.length" class="muted" style="text-align:center;padding:32px">
                      暂无已拆段的任务，请先在「1 任务表」准备任务或在「2 排镜头」查看。
                    </td>
                  </tr>
                  <template v-for="(tk, tIdx) in visibleTasksInTable" :key="tk.id">
                    <!-- 父级任务折叠行（支持拖入预设卡片应用到全任务） -->
                    <tr class="trk-task-group-row" :style="{'--task-accent': taskColor(tIdx, tk)}"
                        @dragover.prevent @drop.prevent="onDropPresetOnTask(tk)">
                      <td style="text-align:center">
                        <input type="checkbox" :checked="isTaskAllChecked(tk)" @change="toggleTaskCheck(tk)">
                      </td>
                      <td colspan="4">
                        <div style="display:flex;align-items:center;gap:8px">
                          <button type="button" class="btn sm ghost" style="padding:1px 6px"
                                  @click="collapsedTasks[tk.id]=!collapsedTasks[tk.id]">
                            {{ collapsedTasks[tk.id] ? '▸' : '▾' }}
                          </button>
                          <span class="trk-task-badge" :style="{background: taskColor(tIdx, tk) + '22', borderColor: taskColor(tIdx, tk), color: taskColor(tIdx, tk)}">
                            {{ tk.id }}
                          </span>
                          <b>{{ tk.name || tk.id }}</b>
                          <span class="muted mono" style="font-size:12px">({{ (tk.segments || []).length }} 段 · {{ tk.image_count || 0 }} 图)</span>
                          <span class="tag" v-if="tk.account">{{ tk.account }}</span>
                        </div>
                      </td>
                      <td :colspan="orderedPromptColumns.length">
                        <div style="display:flex;align-items:center;gap:8px">
                          <span class="muted" style="font-size:12px">📌 任务总预设:</span>
                          <select class="ap-cell-select" style="max-width:220px" :value="tk.preset_id || ''"
                                  @change="applyPresetToTask(tk, $event.target.value)">
                            <option value="">— 选择预设应用到 {{ tk.id }} 全段 —</option>
                            <option v-for="p in validPublishedPresets" :key="p.id" :value="p.id">
                              {{ p.category ? '[' + p.category + '] ' : '' }}{{ p.name }}
                            </option>
                          </select>
                          <span class="muted" style="font-size:11px">（也可将右侧预设卡片直接拖拽到此任务行）</span>
                        </div>
                      </td>
                    </tr>

                    <!-- 子级分段行 -->
                    <template v-if="!collapsedTasks[tk.id]">
                      <tr v-for="(sg, sIdx) in (tk.segments || [])" :key="tk.id + ':' + sg.s"
                          :class="{'row-selected': isSegChecked(tk.id, sg.s)}"
                          :style="{'--task-accent': taskColor(tIdx, tk)}">
                        <td class="ap-td-sel"
                            @mousedown.prevent="startSegDrag(tk.id, sg.s, $event)"
                            @mouseenter="enterSegDrag(tk.id, sg.s)">
                          <input type="checkbox" :checked="isSegChecked(tk.id, sg.s)" tabindex="-1" style="pointer-events:none">
                        </td>
                        <td class="mono" style="border-left:3px solid var(--task-accent)">s{{ pad3(sg.s) }}</td>
                        <td class="mono">{{ sg.image || 'p01' }}</td>
                        <td class="mono">{{ sg.duration || 6 }}s</td>
                        <td>
                          <input class="ap-cell-input" :value="sg.text || ''"
                                 placeholder="本段人物台词/字幕…"
                                 @change="updateSegmentField(tk.id, sg, 'text', $event.target.value, 'main')"
                                 @paste="onTableCellPaste(tk, sIdx, 'text', $event)">
                        </td>
                        <td v-for="(col, cIdx) in orderedPromptColumns" :key="tk.id + ':' + sg.s + ':' + col.col_id"
                            @contextmenu.prevent="openColContextMenu(col, cIdx, $event)"
                            @dragover.prevent
                            @drop.prevent="onDropPresetOnCell(tk, sg, col.col_id)">
                          <div class="gm3-seg-prompt-box">
                            <select v-if="col.col_id==='main'" class="ap-cell-select gm3-mini-preset"
                                    :value="sg.preset_id || ''"
                                    @change="applyPresetToSegment(tk, sg, 'main', validPublishedPresets.find(x => x.id === $event.target.value))">
                              <option value="">预设…</option>
                              <option v-for="p in validPublishedPresets" :key="p.id" :value="p.id">{{ p.name }}</option>
                            </select>
                            <textarea rows="2" class="gm3-cell-textarea"
                                      :value="getPromptCellValue(sg, col)"
                                      placeholder="可直接输入、Ctrl+V 多行粘贴，或从右侧拖入预设卡片…"
                                      @change="updateSegmentField(tk.id, sg, 'custom_prompt', $event.target.value, col.col_id)"
                                      @paste="onTableCellPaste(tk, sIdx, col.col_id, $event)"></textarea>
                          </div>
                        </td>
                      </tr>
                    </template>
                  </template>
                </tbody>
              </table>
            </div>

            <!-- 右侧：🎴 预设卡片库 (支持拖拽到单元格/任务行 + 选中多格一键点按/右键填充) -->
            <aside v-if="dockOpen" class="gm3-card-dock">
              <div class="gm3-dock-hd">
                <b>🎴 预设卡片库</b>
                <small class="muted">拖拽到左侧单元格，或勾选行后点击卡片一键填充</small>
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
                     @contextmenu.prevent="applyCardToChecked(p)"
                     title="可拖拽到任意分段单元格/任务行；或点击/右键直接应用到所有勾选行">
                  <div class="gm3-card-top">
                    <span class="gm3-card-cat" :style="{color: p.color || '#d4a24c'}">● {{ p.category || '默认' }}</span>
                    <b>{{ p.name }}</b>
                    <button type="button" class="btn sm ghost" style="margin-left:auto;padding:1px 6px;font-size:11px"
                            @click.stop="applyCardToChecked(p)">填入勾选行</button>
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
