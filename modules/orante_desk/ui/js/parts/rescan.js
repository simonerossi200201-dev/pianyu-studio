// 重扫目录：预览 → 确认 → 落盘。顶栏右键、② 多模态、③ 编排共用。
window.Rescan = (() => {
  const st = Vue.reactive({
    on: false,
    tid: '',
    loading: false,
    applying: false,
    preview: null,
    pickOn: false,
    pickP: '',
    pickImages: [],
    pickBusy: false,
  });

  function close() {
    if (st.applying) return;
    st.on = false;
    st.preview = null;
  }

  async function open(tid) {
    tid = (tid || (window.ui && ui.panelTid) || '').trim();
    if (!tid) {
      if (window.toast) toast(t('rescan.need_task'), '', 'bad');
      return;
    }
    st.tid = tid;
    st.on = true;
    st.loading = true;
    st.preview = null;
    try {
      const r = await API.get('/api/tasks/' + encodeURIComponent(tid) + '/rescan');
      st.preview = r;
    } catch (e) {
      st.on = false;
    } finally {
      st.loading = false;
    }
  }

  async function apply() {
    if (!st.tid || st.applying) return;
    st.applying = true;
    try {
      const r = await API.post('/api/tasks/' + encodeURIComponent(st.tid) + '/rescan');
      const n = (r.recognized || []).length;
      const add = (r.added || []).length;
      const del = (r.deleted || []).length;
      if (window.toast) {
        toast(t('rescan.applied', {
          n: String(n), add: String(add), del: String(del),
          bak: r.backup || '',
        }), '', 'ok');
      }
      st.on = false;
      st.preview = null;
      store.refreshSeq = (store.refreshSeq || 0) + 1;
    } catch (e) {
      /* API 已经 toast */
    } finally {
      st.applying = false;
    }
  }

  function renamedLine(it) {
    const old = it.old_p ? t('rescan.was', { p: it.old_p }) : '';
    return (it.file || '') + (old ? ' ← ' + old : '');
  }

  function deletedLine(it) {
    const refs = (it.refs || []).join('、');
    const locked = (it.locked_refs || []).join('、');
    let s = t('rescan.del_item', { p: it.old_p || '?' });
    if (refs) s += t('rescan.del_refs', { segs: refs });
    if (locked) s += t('rescan.del_locked', { segs: locked });
    return s;
  }

  function dupeLine(it) {
    return t('rescan.dupe_item', {
      a: (it.files || [])[0] || '',
      b: (it.files || []).slice(1).join('、'),
    });
  }

  function closePick() {
    if (st.pickBusy) return;
    st.pickOn = false;
    st.pickP = '';
    st.pickImages = [];
  }

  function openPick(tid, p, images) {
    tid = (tid || (window.ui && ui.panelTid) || '').trim();
    if (!tid || !p) return;
    const usable = (images || []).filter(im =>
      im && im.p !== p && im.exists !== false && im.role !== '空镜');
    if (!usable.length) {
      if (window.toast) toast(t('rescan.pick_none'), '', 'bad');
      return;
    }
    st.tid = tid;
    st.pickP = p;
    st.pickImages = usable;
    st.pickOn = true;
  }

  async function pickOne(fromP) {
    if (!st.tid || !st.pickP || !fromP || st.pickBusy) return;
    st.pickBusy = true;
    try {
      await API.post(
        '/api/tasks/' + encodeURIComponent(st.tid)
        + '/image/' + encodeURIComponent(st.pickP) + '/adopt',
        { from: fromP });
      if (window.toast) toast(t('rescan.pick_ok', { p: st.pickP, from: fromP }), '', 'ok');
      closePick();
      store.refreshSeq = (store.refreshSeq || 0) + 1;
    } catch (e) {
      /* API 已经 toast */
    } finally {
      st.pickBusy = false;
    }
  }

  async function dropMissing(tid, p) {
    tid = (tid || '').trim();
    if (!tid || !p) return;
    if (!confirm(t('rescan.drop_confirm', { p }))) return;
    await API.post(
      '/api/tasks/' + encodeURIComponent(tid)
      + '/image/' + encodeURIComponent(p) + '/discard');
    if (window.toast) toast(t('rescan.drop_ok', { p }), '', 'ok');
    store.refreshSeq = (store.refreshSeq || 0) + 1;
  }

  return {
    st, open, close, apply, renamedLine, deletedLine, dupeLine,
    openPick, closePick, pickOne, dropMissing,
  };
})();
