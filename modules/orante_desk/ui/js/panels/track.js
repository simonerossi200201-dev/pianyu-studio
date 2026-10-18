// 镜头序列：纯函数，③ 面板和测试共用。rows 非空 = 未应用的预演。
window.TrackShotLogic = (() => {
  function fmtP(v) {
    if (v == null || v === '') return '';
    const s = String(v).trim();
    const m = s.match(/^([pb])(\d+)$/i);
    if (m) return m[1].toLowerCase() + String(parseInt(m[2], 10)).padStart(2, '0');
    const n = parseInt(s.replace(/^p/i, ''), 10);
    if (!Number.isFinite(n) || n < 1) return s;
    return 'p' + String(n).padStart(2, '0');
  }

  function pad3(n) {
    return String(n || 0).padStart(3, '0');
  }

  function sLab(s) {
    return 's' + pad3(s);
  }

  function fmtDur(sec) {
    const s = Math.max(0, Math.round(Number(sec) || 0));
    const m = Math.floor(s / 60);
    const r = s % 60;
    if (m <= 0) return r + '秒';
    return m + '分' + String(r).padStart(2, '0') + '秒';
  }

  function lastTake(sg) {
    const takes = (sg && sg.takes) || [];
    return takes.length ? takes[takes.length - 1] : null;
  }

  function shotEdge(sg) {
    const st = (sg && sg.status) || 'planned';
    const hasClip = !!(sg && (sg.clip_file || lastTake(sg)));
    if (st === 'collected') return hasClip ? 'ok' : 'miss';
    if (st === 'submitted') return 'busy';
    if (st === 'failed' || st === 'blocked') return 'bad';
    return 'idle';
  }

  function clipRel(sg) {
    if (!sg) return '';
    if (sg.clip_file) return sg.clip_file;
    const tk = lastTake(sg);
    return (tk && tk.file) || '';
  }

  function isVideoRel(rel) {
    const s = String(rel || '').toLowerCase();
    return s.endsWith('.mp4') || s.endsWith('.webm') || s.endsWith('.mov');
  }

  // rows 非空用预演 p_new；空则读 manifest.segments[].image（生成时那份）。
  function shotCells(segments, rows, images) {
    const segs = segments || [];
    const byS = {};
    (rows || []).forEach(r => { if (r && r.s != null) byS[r.s] = r; });
    const preview = !!(rows && rows.length);
    const imgs = images || [];
    const byP = {};
    imgs.forEach(im => { if (im && im.p) byP[fmtP(im.p)] = im; });
    return segs.map((sg, i) => {
      const r = byS[sg.s];
      const p = fmtP(r ? r.p_new : sg.image);
      const im = byP[p] || null;
      const prev = i > 0 ? segs[i - 1] : null;
      const role = sg.role || '';
      const relClip = clipRel(sg);
      const collected = (sg.status || '') === 'collected';
      const useClip = collected && !!relClip;
      return {
        s: sg.s,
        sLab: sLab(sg.s),
        p,
        duration: Number(sg.duration || (r && r.dur) || 0) || 0,
        aspect_label: sg.aspect_label || '',
        role,
        status: sg.status || 'planned',
        edge: shotEdge(sg),
        changed: !!(preview && r && r.changed),
        preview,
        file: useClip ? relClip : ((im && im.file) || ''),
        mtime: useClip ? (sg.clip_mtime || (lastTake(sg) && lastTake(sg).mtime) || 0)
          : ((im && im.mtime) || 0),
        clipVideo: useClip && (sg.clip_video || isVideoRel(relClip)),
        fromTail: role === 'bridge' && prev ? sLab(prev.s) : '',
      };
    });
  }

  function longestRun(ps) {
    let bestP = '', bestN = 0, runP = '', runN = 0;
    (ps || []).forEach(p => {
      if (!p) { runP = ''; runN = 0; return; }
      if (p === runP) runN += 1;
      else { runP = p; runN = 1; }
      if (runN > bestN) { bestN = runN; bestP = p; }
    });
    return { p: bestP, n: bestN };
  }

  function shotStats(cells) {
    const list = cells || [];
    const ps = list.map(c => c.p).filter(Boolean);
    const uniq = new Set(ps);
    const run = longestRun(ps);
    const nAnchor = list.filter(c => c.role === 'anchor').length;
    const nBridge = list.filter(c => c.role === 'bridge').length;
    const dur = list.reduce((a, c) => a + (Number(c.duration) || 0), 0);
    return {
      n: list.length,
      nImages: uniq.size,
      longestP: run.p,
      longestN: run.n,
      longestWarn: run.n > 2,
      nAnchor,
      nBridge,
      dur,
      durText: fmtDur(dur),
    };
  }

  return {
    fmtP, pad3, sLab, fmtDur, lastTake, shotEdge, clipRel, isVideoRel,
    shotCells, longestRun, shotStats,
  };
})();

// 节点 ③ 编排：配图轨。Flow / Grok 任务都能选（status ≥ prepared）。
// 配图轨不用描述语；没写描述语也能进、能预演、能应用。出片前再补。
window.Panel_track = (() => {
  const ST_ORDER = ['draft', 'prepared', 'described', 'arranged', 'generating', 'collected', 'finished'];

  function atLeastPrepared(st) {
    return ST_ORDER.indexOf(st) >= ST_ORDER.indexOf('prepared');
  }

  function fmtP(v) {
    if (v == null || v === '') return '';
    const s = String(v).trim();
    const m = s.match(/^([pb])(\d+)$/i);
    if (m) return m[1].toLowerCase() + String(parseInt(m[2], 10)).padStart(2, '0');
    const n = parseInt(s.replace(/^p/i, ''), 10);
    if (!Number.isFinite(n) || n < 1) return s;
    return 'p' + String(n).padStart(2, '0');
  }

  function pNum(p) {
    const s = String(p || '');
    const m = s.match(/^([pb])(\d+)$/i);
    if (m) return (m[1].toLowerCase() === 'b' ? 1000 : 0) + parseInt(m[2], 10);
    const n = parseInt(s.replace(/^p/i, ''), 10);
    return Number.isFinite(n) ? n : 0;
  }

  // 配色与收片面板共用 ui/js/colors.js
  function pcolor(p) {
    return window.pcolor(p);
  }

  function mediaUrl(tid, file, ver) {
    return window.srcWithV(file, ver, tid);
  }

  // 定位抽到 parts/ctxmenu.js，和 ② 多模态共用。
  const placeMenu = function (x, y, w, h, vw, vh, pad) {
    return window.placeMenu(x, y, w, h, vw, vh, pad);
  };

  function pad3(n) {
    return String(n || 0).padStart(3, '0');
  }

  function parsePList(raw, allowed) {
    const allow = new Set((allowed || []).map(fmtP));
    const out = [];
    String(raw || '').split(/[^0-9pPbB]+/).forEach(tok => {
      const p = fmtP(tok);
      if (p && allow.has(p) && !out.includes(p)) out.push(p);
    });
    return out;
  }

  return {
    name: 'Panel_track',
    components: { TaskPick: window.TaskPick },
    setup() {
      const { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } = Vue;

      const tid = ref('');
      const images = ref([]);
      const segments = ref([]);
      const pool = ref([]);
      const pins = reactive({});
      const zones = ref([]);
      const hold = ref(3);
      const jitter = ref(1);
      const noAdj = ref(true);          // 间隔排列，默认开
      const spacingPending = ref(false); // 开关改了还没预演
      const unit = ref('seg');
      const taskMode = ref('first_frame');
      const seed = ref(1);
      const rows = ref(null);
      const summary = ref(null);
      const sel = ref(null);
      const anchor = ref(null);
      const hint = ref('');
      const expandAcc = ref(null);   // {id,name,engine}：这次扩图会用的账号
      const accOpen = ref(false);    // 扩图账号下拉
      const accState = ref(null);    // GET expand-account 的整包
      const accPreview = ref(null);  // 滚轮/拖拽预览：null=未预览，''=跟随任务账号
      const upscaleDlg = reactive({
        on: false, p: '', w: 0, h: 0, starting: false,
      });
      const statsText = ref('');
      const loading = ref(false);
      const planning = ref(false);
      const applying = ref(false);
      const runningKind = ref('');
      const pinPick = ref('');
      const keepMode = ref(true);   // true=只动选中的那几段；false=整轨重算（换一批 / 改块长）
      let saveTimer = null;
      const refsOpen = ref(false);
      // 文案列哪些段展开成全文。切任务清空，默认两行折叠。
      const txtOpen = ref({});
      const menu = reactive({ show: false, x: 0, y: 0, im: null });
      const menuRef = ref(null);
      const card = reactive({
        on: false, kind: '', jid: '', failed: false, error: '',
        logOpen: false, logText: '', holdMsg: '', hold: false,
      });
      let planTimer = null;
      let skipWatch = false;
      let jobPoll = null;
      let logPoll = null;
      let holdTimer = null;
      let finishing = false;
      const RING = 2 * Math.PI * 30;

      const allTasks = computed(() => store.tasks || []);
      const readyTasks = computed(() =>
        allTasks.value.filter(x => atLeastPrepared(x.status) && x.status !== 'trash'));
      const needDesc = computed(() => {
        const st = selectedTask.value && selectedTask.value.status;
        if (!st) return false;
        return ST_ORDER.indexOf(st) >= 0 && ST_ORDER.indexOf(st) < ST_ORDER.indexOf('described');
      });
      const selectedTask = computed(() => allTasks.value.find(x => x.id === tid.value) || null);
      const tidReady = computed(() => !!(selectedTask.value && atLeastPrepared(selectedTask.value.status)));
      const stripView = ref((store.settings && store.settings['track.strip_view'] === 'shots') ? 'shots' : 'pool');
      const seqHi = ref(0);
      const minImages = computed(() => Number(store.settings['expand.min_images'] || 6) || 6);
      const okSubjects = computed(() =>
        images.value.filter(im => im.role === '主体' && im.status === 'ok' && im.exists !== false));
      const blockedImgs = computed(() =>
        images.value.filter(im => im.status === 'blocked' || im.blocked));
      const needExpand = computed(() =>
        tid.value && images.value.length > 0 && okSubjects.value.length < minImages.value
        && !isSimpleTask.value);
      const isSimpleTask = computed(() => {
        const tsk = selectedTask.value;
        if (!tsk) return false;
        // 规格 86 收窄：只认没有文案；有文案单图仍走原黄条/扩图提示
        return !String(tsk.copy || '').trim();
      });
      const pendingImgs = computed(() =>
        images.value.filter(im => im.status === 'pending_review'));
      const pickedRefs = computed(() =>
        okSubjects.value.filter(im => im.expand_ref === true));
      const canExpand = computed(() => pickedRefs.value.length > 0);
      // 按钮上写清楚这次会用谁的号：引擎名 + 账号名，账号还没定就退回旧文案
      const expandBtnLabel = computed(() => {
        const a = shownAcc.value;
        if (!a) return t('track.expand_btn');
        return t('track.expand_btn_acc', { acc: accText(a) });
      });
      const accPinned = computed(() =>
        !!(accState.value && accState.value.current && accState.value.current.source === 'pinned'));
      const accFollowLabel = computed(() => {
        const ta = accState.value && accState.value.task_account;
        if (!ta) return t('track.acc.follow');
        return t('track.acc.follow_one', { acc: accText(ta) });
      });
      const accOptions = computed(() => (accState.value && accState.value.options) || []);
      const accBorrowed = computed(() =>
        !!(accState.value && accState.value.current && accState.value.current.source === 'borrowed'));
      const shownAcc = computed(() => {
        if (accPreview.value == null) return expandAcc.value;
        if (accPreview.value === '') {
          const ta = accState.value && accState.value.task_account;
          return ta || null;
        }
        return (accOptions.value || []).find(a => a.id === accPreview.value) || expandAcc.value;
      });
      const refLabel = computed(() =>
        pickedRefs.value.map(im => fmtP(im.p)).join('、') || '—');
      const liveJob = computed(() => {
        if (!card.jid) return null;
        return (store.jobs || []).find(j => j.id === card.jid) || null;
      });
      const prog = computed(() => {
        const p = (liveJob.value && liveJob.value.progress) || {};
        return {
          phase: p.phase || '',
          done: Number(p.done || 0),
          total: Number(p.total || 0),
          current: p.current || '',
          items: Array.isArray(p.items) ? p.items : [],
        };
      });
      const cardLive = computed(() => {
        const st = liveJob.value && liveJob.value.status;
        return card.on && !card.failed && !card.hold
          && (st === 'running' || st === 'queued' || !st);
      });
      const phaseText = computed(() => {
        if (card.failed) return t('track.phase.failed');
        const st = liveJob.value && liveJob.value.status;
        if (st === 'cancelled' || prog.value.phase === 'cancelled') return t('track.phase.cancelled');
        const ph = prog.value.phase || 'connect';
        const key = 'track.phase.' + ph;
        const s = t(key);
        return s === key ? ph : s;
      });
      const ringOff = computed(() => {
        const tot = prog.value.total || 0;
        const d = prog.value.done || 0;
        if (!tot) return RING;
        return RING * (1 - Math.min(1, d / tot));
      });
      const barPct = computed(() => {
        const tot = prog.value.total || 0;
        if (!tot) return 0;
        return Math.min(100, Math.round(100 * (prog.value.done || 0) / tot));
      });
      const shownItems = computed(() => {
        const arr = prog.value.items || [];
        return arr.length > 8 ? arr.slice(-8) : arr;
      });
      const hiddenN = computed(() => Math.max(0, (prog.value.items || []).length - 8));
      const brollHits = computed(() =>
        segments.value.filter(sg => {
          const im = images.value.find(x => fmtP(x.p) === fmtP(sg.image));
          return im && im.role === '空镜';
        }));
      const nEditable = computed(() =>
        segments.value.filter(sg => sg.status !== 'submitted' && sg.status !== 'collected').length);
      const busy = computed(() => {
        const kinds = new Set((store.jobs || [])
          .filter(j => j.task_id === tid.value && (j.status === 'running' || j.status === 'queued'))
          .map(j => j.kind));
        return kinds;
      });
      const axisN = computed(() => segments.value.length);

      const ts = reactive(window.TextSearch.create({
        getSegs: () => segments.value,
        cardSel: '.panel-track .btable tr[data-s="{s}"]',
        inputSel: '.panel-track .ts-bar input',
      }));

      function usedCount(p) {
        const key = fmtP(p);
        if (rows.value) return rows.value.filter(r => fmtP(r.p_new) === key).length;
        return segments.value.filter(sg => fmtP(sg.image) === key).length;
      }

      function zoneOf(sn) {
        return zones.value.find(z => sn >= z.from && sn <= z.to);
      }

      function cellP(sg) {
        const r = rows.value && rows.value.find(x => x.s === sg.s);
        return fmtP(r ? r.p_new : sg.image);
      }

      function cellLocked(sg) {
        if (chainLocked(sg)) return true;
        const r = rows.value && rows.value.find(x => x.s === sg.s);
        if (r) return r.locked;
        return sg.status === 'submitted' || sg.status === 'collected';
      }

      function isSelected(sn) {
        return sel.value && sn >= sel.value[0] && sn <= sel.value[1];
      }

      function thClass(im) {
        const p = fmtP(im.p);
        const review = im.status === 'pending_review';
        const dead = im.role === '空镜' || im.status === 'blocked' || im.blocked || im.exists === false;
        return {
          th: true,
          inpool: pool.value.includes(p),
          dead: dead && !review,
          review,
        };
      }

      function isDead(im) {
        return im.role === '空镜' || im.status === 'blocked' || im.blocked
          || im.exists === false || im.status === 'pending_review';
      }

      function trackPayload() {
        const pinObj = {};
        for (const [k, v] of Object.entries(pins)) {
          if (v) pinObj[String(k)] = fmtP(v);
        }
        return {
          pins: pinObj,
          pools: zones.value.map(z => ({ from: z.from, to: z.to, images: z.images.slice() })),
          hold: Number(hold.value),
          jitter: Number(jitter.value),
          no_adjacent_repeat: !!noAdj.value,
          unit: unit.value,
          seed: Number(seed.value),
          pool: pool.value.slice(),
          keep: !!keepMode.value,
        };
      }

      // 钉住 / 限定图池 / 参数一改就存盘，切走再回来、重启都还在
      function saveSettings() {
        if (skipWatch || !tid.value) return;
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
          const p = trackPayload();
          delete p.keep;
          API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/track/settings', { track: p })
            .catch(() => { /* 存不上不打断操作 */ });
        }, 400);
      }

      function clearPinsObj() {
        for (const k of Object.keys(pins)) delete pins[k];
      }

      function applyPins(src) {
        clearPinsObj();
        for (const [k, v] of Object.entries(src || {})) {
          const n = parseInt(k, 10);
          const p = fmtP(v);
          if (Number.isFinite(n) && p) pins[n] = p;
        }
      }

      async function loadTrack() {
        if (!tid.value || !tidReady.value) {
          images.value = [];
          segments.value = [];
          rows.value = null;
          return;
        }
        loading.value = true;
        try {
          const r = await API.get('/api/tasks/' + encodeURIComponent(tid.value) + '/track');
          skipWatch = true;
          const slog = r.sync_log || [];
          if (slog.length) toast(t('sync.disk', { detail: slog.join('；') }), '', 'ok');
          expandAcc.value = (r.expand_account && r.expand_account.id) ? r.expand_account : null;
          images.value = r.images || [];
          segments.value = r.segments || [];
          taskMode.value = r.mode || 'first_frame';
          const tr = r.track || {};
          applyPins(tr.pins);
          zones.value = (tr.pools || []).map(z => ({
            from: Number(z.from), to: Number(z.to),
            images: (z.images || z.pool || []).map(fmtP),
          }));
          hold.value = tr.hold != null ? Number(tr.hold) : Number(store.settings['track.hold'] || 3);
          jitter.value = tr.jitter != null ? Number(tr.jitter) : Number(store.settings['track.jitter'] || 1);
          if (tr.no_adjacent_repeat != null) noAdj.value = !!tr.no_adjacent_repeat;
          else noAdj.value = store.settings['track.no_adjacent_repeat'] !== false;
          spacingPending.value = false;
          unit.value = tr.unit || 'seg';
          seed.value = tr.seed != null ? Number(tr.seed) : Math.floor(Math.random() * 100000);
          const usable = (r.pool || []).map(fmtP);
          pool.value = usable.length ? usable : okSubjects.value.map(im => fmtP(im.p));
          rows.value = null;
          summary.value = null;
          sel.value = null;
          anchor.value = null;
          hint.value = t('track.hint.editable', {
            n: String(r.n_editable != null ? r.n_editable : nEditable.value),
            m: String((r.segments || []).length),
          });
          statsText.value = t('track.hint.plan');
          Vue.nextTick(() => { skipWatch = false; });
          loadAccState().catch(() => {});
        } finally {
          loading.value = false;
        }
      }

      async function planNow() {
        if (!tid.value || !segments.value.length) return;
        planning.value = true;
        hint.value = t('track.hint.recalc');
        try {
          const r = await API.post(
            '/api/tasks/' + encodeURIComponent(tid.value) + '/track/preview',
            { track: trackPayload() });
          rows.value = r.rows || [];
          summary.value = r.summary || null;
          const st = r.summary || {};
          statsText.value = t('track.stats', {
            a: String(st.switch_before),
            b: String(st.switch_after),
            c: String(st.sec_per_image_before),
            d: String(st.sec_per_image_after),
            n: String(st.n_changed),
          }) + (st.n_locked ? t('track.stats.locked', { n: String(st.n_locked) }) : '')
            + (noAdj.value ? t('track.stats.spacing', {
              n: String(st.spacing_changed || 0),
              m: String(st.spacing_blocked || 0),
            }) : '');
          spacingPending.value = false;
          if ((r.warnings || []).length) toast(r.warnings[0], '', 'warn');
          hint.value = t('track.hint.editable', {
            n: String(nEditable.value), m: String(segments.value.length),
          });
        } catch (e) {
          rows.value = null;
        } finally {
          planning.value = false;
        }
      }

      function schedulePlan(keep = true) {
        if (skipWatch || !tid.value) return;
        keepMode.value = !!keep;
        rows.value = null;
        saveSettings();
        clearTimeout(planTimer);
        planTimer = setTimeout(() => { planNow(); }, 250);
      }

      function onHoldInput() {
        schedulePlan(false);      // 一张图撑几段变了，整条轨得重新排
      }

      function toggleSpacing() {
        // 只改设置，不重排。下次预演 / 应用才生效。
        noAdj.value = !noAdj.value;
        spacingPending.value = true;
        saveSettings();
        API.post('/api/settings', { set: { 'track.no_adjacent_repeat': noAdj.value } })
          .then(r => { Object.assign(store.settings, r.settings || {}); })
          .catch(() => {});
      }

      function onUnitChange() {
        const sec = unit.value === 'sec';
        if (sec) {
          if (hold.value < 6 || hold.value > 60) hold.value = 25;
        } else if (hold.value > 8) {
          hold.value = 3;
        }
        schedulePlan(false);
      }

      function reseed() {
        seed.value = Math.floor(Math.random() * 100000);
        keepMode.value = false;   // 只有「换一批」整批换
        saveSettings();
        planNow();
      }

      function clickThumb(im) {
        if (im.status === 'pending_review') return;
        if (im.status === 'blocked' || im.blocked) {
          toast(fmtP(im.p) + ' ' + t('track.dead.blocked'), '', 'bad');
          return;
        }
        if (im.exists === false) {
          toast(fmtP(im.p) + ' ' + t('track.dead.missing'), '', 'bad');
          return;
        }
        if (im.role === '空镜') {
          toast(t('track.dead.broll'), '', 'bad');
          return;
        }
        const p = fmtP(im.p);
        const i = pool.value.indexOf(p);
        if (i >= 0) {
          if (pool.value.length === 1) { toast(t('track.pool_min'), '', 'bad'); return; }
          pool.value.splice(i, 1);
        } else {
          pool.value.push(p);
          pool.value.sort((a, b) => pNum(a) - pNum(b));
        }
        schedulePlan();
      }

      async function keepImg(im, ev) {
        ev.stopPropagation();
        if (!tid.value) return;
        await API.post(
          '/api/tasks/' + encodeURIComponent(tid.value) + '/image/' + encodeURIComponent(im.p) + '/keep');
        await loadTrack();
      }

      async function discardImg(im, ev) {
        ev.stopPropagation();
        if (!tid.value) return;
        await API.post(
          '/api/tasks/' + encodeURIComponent(tid.value) + '/image/' + encodeURIComponent(im.p) + '/discard');
        await loadTrack();
      }

      function closeImgMenu() {
        menu.show = false;
        menu.im = null;
      }

      function openImgMenu(ev, im) {
        menu.im = im;
        window.openCtxMenu(ev, menu, menuRef, nextTick, 180, 180);
      }

      function onDocClick(ev) {
        if (menu.show && menuRef.value && !menuRef.value.contains(ev.target)) closeImgMenu();
        if (accOpen.value && !(ev.target && ev.target.closest && ev.target.closest('.accpick-host'))) {
          accOpen.value = false;
        }
      }

      function onMenuKey(ev) {
        if (upscaleDlg.on && ev.key === 'Escape') { closeUpscaleDlg(); return; }
        if (accOpen.value && ev.key === 'Escape') { accOpen.value = false; return; }
        if (menu.show) {
          if (ev.key === 'Escape') closeImgMenu();
          return;
        }
        if (ui.drawer) return;
        ts.onPanelKey(ev);
      }

      function imgLimited(im) {
        return !im || im.role === '空镜' || im.status === 'blocked' || im.blocked || im.exists === false;
      }

      function imgMenuOff(im, key) {
        if (!im) return true;
        if (key === 'copy' || key === 'discard') return false;
        if (key === 'flip' || key === 'crop' || key === 'upscale') return imgLimited(im);
        return false;
      }

      function accText(a) {
        if (!a) return '';
        return (a.engine || '') + ' · ' + (a.name || a.id || '');
      }

      async function loadAccState() {
        if (!tid.value) return null;
        const r = await API.get('/api/tasks/' + encodeURIComponent(tid.value) + '/expand-account');
        accState.value = r;
        if (r && r.current && r.current.id) expandAcc.value = r.current;
        return r;
      }

      async function toggleAccMenu() {
        if (accOpen.value) { accOpen.value = false; return; }
        try { await loadAccState(); } catch (e) { /* API 已经 toast */ return; }
        accOpen.value = true;
      }

      async function pickExpandAcc(id) {
        if (!tid.value) return;
        try {
          const r = await API.post(
            '/api/tasks/' + encodeURIComponent(tid.value) + '/expand-account',
            { account_id: id == null ? '' : id });
          accState.value = r;
          if (r && r.current && r.current.id) expandAcc.value = r.current;
          else expandAcc.value = null;
          accOpen.value = false;
          accPreview.value = null;
        } catch (e) { accPreview.value = null; }
      }

      const accGest = window.usePickerGestures({
        items: () => {
          const out = [{ value: '', disabled: false }];
          (accOptions.value || []).forEach(a => {
            out.push({
              value: a.id,
              disabled: !!a.disabled || a.enabled === false,
            });
          });
          return out;
        },
        current: () => {
          if (accPreview.value != null) return accPreview.value;
          if (accPinned.value && expandAcc.value) return expandAcc.value.id;
          return '';
        },
        open: () => accOpen.value,
        disabled: () => !!(busy.value.has('expand') || runningKind.value),
        onPreview(item) { accPreview.value = item ? item.value : ''; },
        onCommit(item) {
          pickExpandAcc(item ? item.value : '');
        },
        onCancel() { accPreview.value = null; },
        onOpen: toggleAccMenu,
      });

      function imgPath(p) {
        return '/api/tasks/' + encodeURIComponent(tid.value)
          + '/image/' + encodeURIComponent(p);
      }

      function refCount(p) {
        const key = fmtP(p);
        return segments.value.filter(sg => fmtP(sg.image) === key).length;
      }

      function toastReset(key, p, reset) {
        const n = Array.isArray(reset) ? reset.length : 0;
        if (n) toast(t(key + '_reset', { p, n: String(n) }), '', 'ok');
        else toast(t(key + '_ok', { p }), '', 'ok');
      }

      async function openUpscaleDlg(im) {
        if (!im || !tid.value || imgMenuOff(im, 'upscale')) return;
        const p = fmtP(im.p);
        try {
          const info = await API.get(imgPath(im.p) + '/info');
          await loadAccState();
          upscaleDlg.p = p;
          upscaleDlg.w = Number(info.w || 0);
          upscaleDlg.h = Number(info.h || 0);
          upscaleDlg.starting = false;
          upscaleDlg.on = true;
        } catch (e) { /* API 已经 toast */ }
      }

      function closeUpscaleDlg() {
        upscaleDlg.on = false;
        upscaleDlg.starting = false;
        accOpen.value = false;
      }

      async function startUpscale() {
        if (!tid.value || !upscaleDlg.p || upscaleDlg.starting) return;
        upscaleDlg.starting = true;
        const p = upscaleDlg.p;
        closeUpscaleDlg();
        await runJob('upscale', { ps: [p] });
      }

      function toastUpscale(item) {
        const p = fmtP(item.p);
        const from = (item.from || []).join('×') || '—';
        const to = (item.to || []).join('×') || '—';
        const refs = Array.isArray(item.refs) ? item.refs : [];
        const n = refs.length;
        const text = t('track.upscale_done', { p, from, to });
        if (!n) { toast(text, '', 'ok'); return; }
        toast(text, t('track.upscale_toast_hint', { n: String(n) }), 'ok', [
          {
            label: t('track.upscale_reset', { n: String(n) }),
            kind: 'primary',
            run: async () => {
              try {
                await API.post(imgPath(p) + '/reset-refs');
                await loadTrack();
                toast(t('track.upscale_reset_ok', { n: String(n) }), '', 'ok');
              } catch (e) { /* API 已经 toast */ }
            },
          },
          { label: t('track.upscale_ignore'), run: () => {} },
        ]);
      }

      async function onImgMenu(key) {
        const im = menu.im;
        if (!im || imgMenuOff(im, key)) return;
        closeImgMenu();
        if (!tid.value) return;
        const p = fmtP(im.p);
        try {
          if (key === 'copy') {
            const r = await API.post(imgPath(im.p) + '/duplicate');
            await loadTrack();
            const np = r.image && r.image.p ? fmtP(r.image.p) : '';
            toast(t('track.ctx.copy_ok', { p: np || p }), '', 'ok');
            return;
          }
          if (key === 'flip') {
            const r = await API.post(imgPath(im.p) + '/flip');
            await loadTrack();
            toastReset('track.ctx.flip', p, r.reset_segments);
            return;
          }
          if (key === 'upscale') {
            await openUpscaleDlg(im);
            return;
          }
          if (key === 'crop') {
            if (!window.openCropper) {
              toast(t('crop.load_fail'), '', 'bad');
              return;
            }
            const info = await API.get(imgPath(im.p) + '/info');
            const box = await window.openCropper({
              tid: tid.value, p, file: info.file || im.file,
              w: info.w, h: info.h, sha1: im.sha1, mtime: im.mtime,
              n_refs: refCount(im.p),
            });
            if (!box) return;
            const r = await API.post(imgPath(im.p) + '/crop', box);
            await loadTrack();
            const outP = r.image && r.image.p ? fmtP(r.image.p) : p;
            toastReset('track.ctx.crop', outP, r.reset_segments);
            return;
          }
          if (key === 'discard') {
            const n = refCount(im.p);
            const msg = n
              ? t('track.ctx.discard_used', { p, n: String(n) })
              : t('track.ctx.discard_confirm', { p });
            if (!confirm(msg)) return;
            await API.post(imgPath(im.p) + '/discard');
            await loadTrack();
            toast(t('track.ctx.discard_ok', { p }), '', 'ok');
          }
        } catch (e) { /* API 已经 toast */ }
      }

      async function quickImgAct(im, act, ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        if (!im || !tid.value) return;
        if (act === 'engine') {
          const curIsStudio = !!(expandAcc.value && expandAcc.value.id === 'STUDIO');
          const nextId = curIsStudio ? '' : 'STUDIO';
          await pickExpandAcc(nextId);
          toast(nextId === 'STUDIO' ? '已切换为：🖥️ 本地 AI · FLUX.2 / LTX-2.5 (可选项)' : '已切换为：⚡ 优先使用付费账号 (Flow / Grok)', '', 'ok');
          return;
        }
        if (act === 'expand') {
          if (!im.expand_ref) {
            await toggleRef(im, { target: { type: 'checkbox', checked: true }, stopPropagation() {} });
          }
          await runJob('expand');
          return;
        }
        menu.im = im;
        await onImgMenu(act);
      }

      function clickCell(sg, ev) {
        const sn = sg.s;
        if (ev.shiftKey && anchor.value != null)
          sel.value = [Math.min(anchor.value, sn), Math.max(anchor.value, sn)];
        else { anchor.value = sn; sel.value = [sn, sn]; }
      }

      function onPinPick() {
        const p = fmtP(pinPick.value);
        pinPick.value = '';
        if (!p || !sel.value) return;
        const [a, b] = sel.value;
        for (let i = a; i <= b; i++) {
          const sg = (segments.value || []).find(x => x.s === i);
          if (chainLocked(sg)) continue;
          pins[i] = p;
        }
        rows.value = null;
        toast(t('track.pin_ok', { a: String(a), b: String(b), p }), '', 'ok');
        schedulePlan();
      }

      function setZone() {
        if (!sel.value) return;
        const [a, b] = sel.value;
        const raw = prompt(
          t('track.zone_prompt') + '\n' + t('track.zone_available') + pool.value.join(' '),
          pool.value.join(','));
        if (raw == null) return;
        const ps = parsePList(raw, pool.value);
        if (!ps.length) { toast(t('track.zone_bad'), '', 'bad'); return; }
        zones.value = zones.value.filter(z => z.to < a || z.from > b);
        zones.value.push({ from: a, to: b, images: ps });
        rows.value = null;
        toast(t('track.zone_ok', { a: String(a), b: String(b), ps: ps.join(' ') }), '', 'ok');
        schedulePlan();
      }

      function unpin() {
        if (!sel.value) return;
        const [a, b] = sel.value;
        for (let i = a; i <= b; i++) delete pins[i];
        rows.value = null;
        schedulePlan();
      }

      function clearZone() {
        if (!sel.value) return;
        const [a, b] = sel.value;
        zones.value = zones.value.filter(z => z.to < a || z.from > b);
        rows.value = null;
        schedulePlan();
      }

      function unselect() {
        sel.value = null;
        anchor.value = null;
      }

      async function applyTrack() {
        if (!tid.value) return;
        if (!rows.value) await planNow();
        if (!rows.value) return;
        if (!confirm(t('track.confirm_apply'))) return;
        applying.value = true;
        hint.value = t('track.applying');
        try {
          await API.post(
            '/api/tasks/' + encodeURIComponent(tid.value) + '/track/apply',
            { track: trackPayload() });
          runningKind.value = 'arrange';
        } catch (e) {
          applying.value = false;
        }
      }

      function upsertJob(j) {
        if (!j || !j.id) return;
        const i = store.jobs.findIndex(x => x.id === j.id);
        if (i >= 0) store.jobs[i] = j;
        else store.jobs.push(j);
      }

      function stopPolls() {
        if (jobPoll) { clearInterval(jobPoll); jobPoll = null; }
        if (logPoll) { clearInterval(logPoll); logPoll = null; }
      }

      function openCard(kind) {
        finishing = false;
        card.on = true;
        card.kind = kind;
        card.jid = '';
        card.failed = false;
        card.error = '';
        card.logOpen = false;
        card.logText = '';
        card.holdMsg = '';
        card.hold = false;
        refsOpen.value = false;
      }

      function closeCard() {
        stopPolls();
        if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
        finishing = false;
        card.on = false;
        card.hold = false;
        card.holdMsg = '';
        card.jid = '';
        card.failed = false;
        card.error = '';
        card.logOpen = false;
        runningKind.value = '';
      }

      function startJobPoll(jid) {
        if (jobPoll) { clearInterval(jobPoll); jobPoll = null; }
        jobPoll = setInterval(async () => {
          try {
            const r = await fetch('/api/jobs/' + encodeURIComponent(jid));
            const body = await r.json();
            if (body && body.ok && body.job) {
              upsertJob(body.job);
              maybeFinish(body.job);
            }
          } catch (e) { /* 轮询失败下一秒再试 */ }
        }, 1000);
      }

      async function refreshLog() {
        if (!card.jid || !card.logOpen) return;
        try {
          const r = await fetch('/api/jobs/' + encodeURIComponent(card.jid) + '/log');
          card.logText = await r.text();
        } catch (e) { /* 日志稍后再刷 */ }
      }

      function toggleLog() {
        card.logOpen = !card.logOpen;
        if (card.logOpen) {
          refreshLog();
          if (logPoll) clearInterval(logPoll);
          logPoll = setInterval(refreshLog, 2000);
        } else if (logPoll) {
          clearInterval(logPoll);
          logPoll = null;
        }
      }

      function maybeFinish(j) {
        if (!j || finishing || card.failed) return;
        if (j.status === 'done') {
          finishing = true;
          card.hold = true;
          const r = j.result || {};
          const upOk = (j.kind === 'upscale') ? (r.ok || []) : [];
          const upFail = (j.kind === 'upscale') ? (r.failed || []) : [];
          if (j.kind === 'refill') {
            const n = Array.isArray(r.replaced) ? r.replaced.length : Number(r.replaced || 0);
            card.holdMsg = t('track.refill_done', { n: String(n) });
          } else if (j.kind === 'upscale') {
            if (upOk.length) {
              const it = upOk[0];
              card.holdMsg = t('track.upscale_done', {
                p: fmtP(it.p),
                from: (it.from || []).join('×') || '—',
                to: (it.to || []).join('×') || '—',
              });
            } else if (upFail.length) {
              card.holdMsg = t('track.upscale_fail', {
                p: fmtP(upFail[0].p), reason: upFail[0].reason || '',
              });
            } else {
              card.holdMsg = t('track.phase.done');
            }
          } else {
            const n = r.added != null ? Number(r.added) : 0;
            card.holdMsg = t('track.expand_done', { n: String(n), m: String(n) });
          }
          stopPolls();
          holdTimer = setTimeout(async () => {
            closeCard();
            await loadTrack();
            if (j.kind === 'upscale') {
              upOk.forEach(toastUpscale);
              upFail.forEach(it => toast(
                t('track.upscale_fail', { p: fmtP(it.p), reason: it.reason || '' }),
                '', 'bad'));
            }
          }, 3000);
        } else if (j.status === 'cancelled' || j.status === 'lost') {
          finishing = true;
          card.hold = true;
          card.holdMsg = t('track.phase.cancelled');
          stopPolls();
          holdTimer = setTimeout(async () => {
            closeCard();
            await loadTrack();
          }, 3000);
        } else if (j.status === 'failed') {
          finishing = true;
          card.failed = true;
          card.error = j.error || t('track.phase.failed');
          stopPolls();
          runningKind.value = '';
        }
      }

      async function runJob(kind, extra) {
        if (!tid.value) { toast(t('track.need_task'), '', 'bad'); return; }
        if (kind === 'expand' && !canExpand.value) {
          toast(t('track.expand_need_ref'), '', 'bad');
          return;
        }
        runningKind.value = kind;
        openCard(kind);
        try {
          const res = await fetch('/api/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Object.assign({ kind, task_id: tid.value }, extra || {})),
          });
          const body = await res.json();
          if (!body || body.ok === false) {
            card.failed = true;
            card.error = [body && body.error, body && body.hint].filter(Boolean).join(' · ')
              || t('track.phase.failed');
            runningKind.value = '';
            return;
          }
          const j = body.job;
          card.jid = j.id;
          upsertJob(j);
          startJobPoll(j.id);
        } catch (e) {
          card.failed = true;
          card.error = String((e && e.message) || e);
          runningKind.value = '';
        }
      }

      async function cancelExpand() {
        if (!card.jid) return;
        try {
          await API.post('/api/jobs/' + encodeURIComponent(card.jid) + '/cancel');
        } catch (e) { /* 卡片会跟到 cancelled */ }
      }

      async function goExpandPrompt() {
        ui.promptBlock = 'expand';
        if (window.flushAll) await window.flushAll();
        ui.node = 'gemini';
      }

      function canBeRef(im) {
        return im.role === '主体' && im.status === 'ok' && im.exists !== false;
      }

      async function toggleRef(im, ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        if (!tid.value || !canBeRef(im)) return;
        const on = ev && ev.target && ev.target.type === 'checkbox'
          ? ev.target.checked
          : !im.expand_ref;
        const prev = !!im.expand_ref;
        im.expand_ref = on;
        try {
          const r = await API.post(
            '/api/tasks/' + encodeURIComponent(tid.value)
            + '/image/' + encodeURIComponent(im.p),
            { expand_ref: on });
          if (r.image) Object.assign(im, r.image);
        } catch (e) {
          im.expand_ref = prev;
          if (ev && ev.target && ev.target.type === 'checkbox') ev.target.checked = prev;
        }
      }

      function itemClass(it) {
        const r = it && it.role;
        return { xit: true, ok: r === '主体', broll: r === '空镜', blocked: r === '被拒' };
      }

      function itemTitle(it) {
        const bits = [];
        if (it && it.ref) bits.push(t('track.item_ref', { p: fmtP(it.ref) }));
        if (it && it.variant) bits.push(t('track.item_var', { n: String(it.variant) }));
        if (it && it.prompt) bits.push(it.prompt);
        return bits.join(' · ');
      }

      function rowOf(sg) {
        return rows.value && rows.value.find(x => x.s === sg.s);
      }

      function whyMark(sg) {
        if (isNowBroll(sg)) return t('track.why.need_replan');
        if (pins[sg.s]) return t('track.why.pin');
        if (zoneOf(sg.s)) return t('track.why.zone');
        if (sg.role === '空镜' || isNowBroll(sg)) return t('track.why.broll');
        return '';
      }

      function isNowBroll(sg) {
        const p = cellP(sg);
        const im = images.value.find(x => fmtP(x.p) === p);
        return !!(im && im.role === '空镜');
      }

      function isLastSeg(sg) {
        const all = segments.value || [];
        if (!all.length || !sg) return false;
        return all[all.length - 1] && all[all.length - 1].s === sg.s;
      }

      function chainLocked(sg) {
        return taskMode.value === 'tail_chain' && sg && sg.s !== 1 && sg.gen_mode !== 'first_frame';
      }

      function chainFromLabel(sg) {
        const prev = sg && sg.s > 1 ? ('s' + pad3(sg.s - 1)) : '';
        return t('track.chain.from', { s: prev || 's001' });
      }

      async function setSegMode(sg, mode) {
        if (!tid.value || !sg) return;
        if (taskMode.value !== 'anchor_bridge' && taskMode.value !== 'tail_chain') {
          toast(t('track.seg.off'));
          return;
        }
        try {
          const r = await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/seg-mode', {
            s: sg.s, mode,
          });
          toast(r.hint || '', '', 'ok');
          await loadTrack();
        } catch (e) { /* toast */ }
      }

      
      const ENGINE_DURS = { flow: [4, 6, 8], grok: [6, 10, 15] };
      const durOptions = computed(() => {
        const eng = (selectedTask.value && selectedTask.value.engine) || 'flow';
        return ENGINE_DURS[eng] || ENGINE_DURS.flow;
      });
      const durMenu = reactive({ show: false, x: 0, y: 0, sg: null });
      const durDlg = reactive({ on: false, mode: 'single', sList: [], flowDur: 8, grokDur: 10, studioDur: 8, note: '', engines: [] });

      function openDurMenu(ev, sg) {
        durMenu.show = true;
        durMenu.x = ev.clientX;
        durMenu.y = ev.clientY;
        durMenu.sg = sg;
      }
      function closeDurMenu() { durMenu.show = false; durMenu.sg = null; }

      async function onDurChange(sg, ev) {
        const v = Number(ev.target.value);
        if (!tid.value || !sg || !v) return;
        try {
          const r = await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/seg-duration', {
            s: sg.s, duration: v,
          });
          if (r.warns && r.warns.length) toast(r.warns[0].warn, '', 'warn');
          else toast(t('track.dur_set_ok'), '', 'ok');
          await loadTrack();
        } catch (e) { /* toast */ }
      }

      async function restoreDur(sg) {
        closeDurMenu();
        if (!tid.value || !sg) return;
        try {
          await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/seg-duration/restore', { s: sg.s });
          toast(t('track.dur_restore_ok'), '', 'ok');
          await loadTrack();
        } catch (e) { /* toast */ }
      }

      function openBatchDurDlg() {
        closeDurMenu();
        if (!sel.value) { toast(t('track.dur_need_sel'), '', 'bad'); return; }
        const [a, b] = sel.value;
        const list = (segments.value || []).filter(sg => sg.s >= a && sg.s <= b);
        const live = list.filter(sg => sg.status !== 'collected');
        const nCol = list.length - live.length;
        durDlg.on = true;
        durDlg.mode = 'batch';
        durDlg.sList = live.map(sg => sg.s);
        durDlg.note = nCol
          ? t('track.dur_batch_note_collected', { n: String(nCol) })
          : t('track.dur_batch_note');
        const eng = (selectedTask.value && selectedTask.value.engine) || 'flow';
        durDlg.engines = [eng];
        if (eng === 'flow') durDlg.flowDur = 8;
        else if (eng === 'studio') durDlg.studioDur = 8;
        else durDlg.grokDur = 10;
      }

      async function applyBatchDur() {
        if (!tid.value || !durDlg.sList.length) { durDlg.on = false; return; }
        const eng = durDlg.engines[0] || 'flow';
        const dur = eng === 'flow' ? Number(durDlg.flowDur)
          : (eng === 'studio' ? Number(durDlg.studioDur) : Number(durDlg.grokDur));
        const items = durDlg.sList.map(s => ({ s, duration: dur }));
        try {
          const r = await API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/seg-duration', {
            items, skip_collected: true,
          });
          const msg = t('track.dur_batch_ok', { n: String((r.changed || []).length) });
          toast(msg, r.warns && r.warns[0] ? r.warns[0].warn : '', 'ok');
          durDlg.on = false;
          await loadTrack();
        } catch (e) { /* toast */ }
      }


      function toggleTxt(s) {
        const cur = { ...txtOpen.value };
        if (cur[s]) delete cur[s];
        else cur[s] = true;
        txtOpen.value = cur;
      }

      watch(tid, () => {
        ui.panelTid = tid.value || '';
        closeCard();
        closeImgMenu();
        refsOpen.value = false;
        txtOpen.value = {};
        loadTrack();
      });
      watch(() => store.refreshSeq, () => { if (tid.value) loadTrack(); });
      watch(() => ui.pendingTid, (v) => { if (v) takePendingTid(tid, 'track'); });
      watch(() => ui.panelTid, (v) => { if (v && v !== tid.value) tid.value = v; });
      watch([hold, jitter], () => { if (!skipWatch) schedulePlan(false); });
      watch(() => (store.jobs || []).map(j => j.id + ':' + j.status).join('|'), async () => {
        const mine = (store.jobs || []).filter(j =>
          j.task_id === tid.value
          && (j.kind === 'arrange' || j.kind === 'expand' || j.kind === 'refill'));
        const live = mine.some(j => j.status === 'running' || j.status === 'queued');
        if (!live && runningKind.value === 'arrange') {
          runningKind.value = '';
          applying.value = false;
          await loadTrack();
          const done = mine.filter(j => j.kind === 'arrange' && j.status === 'done');
          const last = done[done.length - 1];
          const nKept = last && last.result && last.result.n_prompt_locked;
          toast(t('track.applied'),
                nKept ? t('track.applied_locked', { n: String(nKept) }) : '',
                'ok');
        }
        if (card.on && card.jid) {
          const j = (store.jobs || []).find(x => x.id === card.jid);
          if (j) maybeFinish(j);
        }
        if (!card.on) {
          const running = mine.find(j =>
            (j.kind === 'expand' || j.kind === 'refill')
            && (j.status === 'running' || j.status === 'queued'));
          if (running) {
            openCard(running.kind);
            card.jid = running.id;
            runningKind.value = running.kind;
            startJobPoll(running.id);
          }
        }
      });

      function flushTrack() {
        if (skipWatch || !tid.value) return;
        clearTimeout(saveTimer);
        const p = trackPayload();
        delete p.keep;
        return API.post('/api/tasks/' + encodeURIComponent(tid.value) + '/track/settings', { track: p })
          .catch(() => {});
      }

      function openRescan() {
        if (window.Rescan) window.Rescan.open(tid.value);
      }

      async function goDescribe() {
        if (window.flushAll) await window.flushAll();
        else if (window.flushEdits) await window.flushEdits();
        if (tid.value) {
          ui.pendingTid = tid.value;
          ui.panelTid = tid.value;
        }
        ui.node = 'gemini';
      }

      const Lshot = window.TrackShotLogic;
      const shotCells = computed(() =>
        Lshot.shotCells(segments.value, rows.value, images.value));
      const shotStats = computed(() => Lshot.shotStats(shotCells.value));
      const shotStatsText = computed(() => {
        const st = shotStats.value;
        const vars = {
          n: String(st.n), m: String(st.nImages),
          p: st.longestP || '—', k: String(st.longestN || 0),
          a: String(st.nAnchor), b: String(st.nBridge), dur: st.durText,
        };
        if (st.nAnchor + st.nBridge > 0) return t('track.seq.stats', vars);
        return t('track.seq.stats_plain', vars);
      });
      const previewOn = computed(() => !!(rows.value && rows.value.length));

      function setStripView(v) {
        stripView.value = v === 'shots' ? 'shots' : 'pool';
        if (store.settings) store.settings['track.strip_view'] = stripView.value;
        API.post('/api/settings', { set: { 'track.strip_view': stripView.value } })
          .then(r => { Object.assign(store.settings, r.settings || {}); })
          .catch(() => {});
      }

      function revertPreview() {
        rows.value = null;
        summary.value = null;
        statsText.value = t('track.hint.plan');
        hint.value = t('track.hint.editable', {
          n: String(nEditable.value), m: String(segments.value.length),
        });
      }

      function focusSeg(s) {
        seqHi.value = s;
        const el = document.querySelector('.panel-track .btable tr[data-s="' + s + '"]');
        if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }

      function shotSrc(cell) {
        if (!cell || !cell.file) return '';
        return mediaUrl(tid.value, cell.file, cell.mtime);
      }

      onMounted(() => {
        document.addEventListener('click', onDocClick);
        document.addEventListener('keydown', onMenuKey);
        if (!adoptPanelTid(tid, 'track') && !tid.value && readyTasks.value.length) {
          tid.value = readyTasks.value[0].id;
        }
        ui.panelTid = tid.value || '';
        if (window.registerFlush) window.registerFlush('track', flushTrack);
      });
      onUnmounted(() => {
        if (accGest && accGest.dispose) accGest.dispose();
        document.removeEventListener('click', onDocClick);
        document.removeEventListener('keydown', onMenuKey);
        if (window.unregisterFlush) window.unregisterFlush('track');
        stopPolls();
        if (holdTimer) clearTimeout(holdTimer);
      });

      return {
        t, tid, images, segments, pool, pins, zones, hold, jitter, noAdj, spacingPending, unit, seed, ts, taskMode,
        chainLocked, chainFromLabel,
        rows, summary, sel, hint, statsText, loading, planning, applying, runningKind,
        stripView, seqHi, tidReady, shotCells, shotStats, shotStatsText, previewOn,
        setStripView, revertPreview, focusSeg, shotSrc,
        pinPick, keepMode, refsOpen, card, RING, readyTasks, allTasks, selectedTask, needDesc, isSimpleTask, minImages, okSubjects, blockedImgs,
        needExpand, pendingImgs, pickedRefs, canExpand, refLabel, expandBtnLabel,
        accOpen, accState, accPinned, accFollowLabel, accOptions, accBorrowed, accText,
        shownAcc, accGest, toggleAccMenu, pickExpandAcc,
        upscaleDlg, closeUpscaleDlg, startUpscale,
        liveJob, prog, cardLive, phaseText, ringOff, barPct, shownItems, hiddenN,
        brollHits, nEditable, busy, axisN, menu, menuRef,
        pcolor, fmtP, pad3, mediaUrl, usedCount, zoneOf, cellP, cellLocked, isSelected,
        openRescan, goDescribe,
        thClass, isDead, clickThumb, keepImg, discardImg, clickCell,
        isLastSeg, setSegMode, durOptions, durMenu, durDlg, openDurMenu, closeDurMenu, onDurChange, restoreDur, openBatchDurDlg, applyBatchDur,
        onPinPick, setZone, unpin, clearZone, unselect,
        planNow, reseed, onHoldInput, onUnitChange, toggleSpacing, applyTrack, runJob,
        cancelExpand, toggleLog, goExpandPrompt, canBeRef, toggleRef, itemClass, itemTitle,
        rowOf, whyMark, isNowBroll, txtOpen, toggleTxt, openImgMenu, onImgMenu, imgMenuOff, quickImgAct,
      };
    },
    template: `
<section class="panel-track">
  <div class="thd">
    <h2>{{ t('node.track') }}</h2>
    <task-pick v-model="tid" min-status="prepared" :tasks="allTasks"></task-pick>
    <span class="tag" v-if="selectedTask" :class="selectedTask.engine">{{ selectedTask.engine }}</span>
    <span class="bstat">{{ statsText }}</span>
    <button class="btn sm" type="button" :disabled="!tid" @click="openRescan">{{ t('rescan.btn') }}</button>
  </div>

  <div class="xcard" v-if="card.on" :class="{failed: card.failed}">
    <div class="xcard-ops">
      <button class="btn sm" v-if="cardLive" @click="cancelExpand">{{ t('track.stop') }}</button>
      <button class="btn sm" @click="toggleLog">{{ t('track.log') }}</button>
    </div>
    <div class="xcard-row">
      <div class="xring" :class="{spin: cardLive}">
        <svg viewBox="0 0 72 72" aria-hidden="true">
          <circle class="bg" cx="36" cy="36" r="30"></circle>
          <circle class="fg" cx="36" cy="36" r="30"
                  :stroke-dasharray="RING" :stroke-dashoffset="ringOff"></circle>
          <circle class="hi" cx="36" cy="36" r="30"></circle>
        </svg>
        <div class="xnum">{{ prog.done }} / {{ prog.total }}</div>
      </div>
      <div class="xmid">
        <div class="xphase">{{ card.hold ? card.holdMsg : phaseText }}</div>
        <div class="xcur">{{ card.failed ? card.error : (card.hold ? '' : prog.current) }}</div>
        <div class="xbar"><i :style="{width: barPct + '%'}"></i></div>
      </div>
      <div class="xitems">
        <div v-for="(it, idx) in shownItems" :key="idx" :class="itemClass(it)" :title="itemTitle(it)">
          <img v-if="it.thumb" :src="mediaUrl(tid, it.thumb)" alt="">
          <span class="xbadge" v-if="it.ref">{{ fmtP(it.ref) }}</span>
        </div>
        <div class="xmore" v-if="hiddenN">+{{ hiddenN }}</div>
      </div>
    </div>
    <pre class="xlog" v-if="card.logOpen">{{ card.logText || '…' }}</pre>
  </div>
  <div class="banner info" v-if="tid && isSimpleTask">
    <span>{{ t('track.simple86_banner') }}</span>
  </div>
  <div class="banner warn" v-if="tid && needDesc && !isSimpleTask">
    <span>{{ t('track.need_desc') }}</span>
    <button class="btn sm" type="button" @click="goDescribe">{{ t('track.need_desc.go') }}</button>
  </div>
  <div class="banner warn" v-if="!card.on && needExpand">
    <span>{{ t('track.expand_banner', { n: String(okSubjects.length) }) }}</span>
    <span class="refpick">
      <button class="btn sm ghost" type="button" @click="refsOpen=!refsOpen">
        {{ t('track.expand_refs', { ps: refLabel }) }} ▾
      </button>
      <div class="refpick-menu" v-if="refsOpen">
        <label v-for="im in okSubjects" :key="'r'+im.p" class="refpick-item">
          <input type="checkbox" :checked="!!im.expand_ref" @change="toggleRef(im, $event)">
          <img :src="mediaUrl(tid, im.file, im.mtime)" :alt="fmtP(im.p)">
          <span>{{ fmtP(im.p) }}</span>
        </label>
      </div>
    </span>
    <span class="split-btn accpick-host">
      <button class="btn sm" :disabled="busy.has('expand') || !!runningKind || !canExpand"
              :title="canExpand ? expandBtnLabel : t('track.expand_need_ref')"
              @click="runJob('expand')">
        {{ expandBtnLabel }}
      </button>
      <button class="btn sm split-caret pg-trig" type="button"
              :class="{ dragging: accGest.dragging, bump: accGest.bump,
                        'pg-in-down': accGest.slide==='down', 'pg-in-up': accGest.slide==='up' }"
              :disabled="busy.has('expand') || !!runningKind"
              :title="t('track.acc.follow')"
              @wheel="accGest.onWheel"
              @pointerdown="accGest.onPointerDown"
              @pointermove="accGest.onPointerMove"
              @pointerup="accGest.onPointerUp"
              @pointercancel="accGest.onPointerCancel"
              @lostpointercapture="accGest.onLostPointerCapture"
              @click.stop="accGest.onClick"
              @contextmenu="accGest.onContextMenu">
        ▾
        <span class="pg-ew" v-if="accGest.dragging">◂ ▸</span>
        <span class="pg-pos" v-if="accGest.posHint">{{ accGest.posHint }}</span>
      </button>
      <div class="accpick-menu" v-if="accOpen && !upscaleDlg.on" @click.stop>
        <div class="accpick-item" :class="{on: !accPinned}" @click="pickExpandAcc('')">
          {{ accPinned ? '○ ' : '● ' }}{{ accFollowLabel }}
        </div>
        <div class="accpick-borrowed" v-if="accBorrowed">{{ t('track.acc.borrowed') }}</div>
        <div class="accpick-sep"></div>
        <div v-for="a in accOptions" :key="a.id" class="accpick-item"
             :class="{on: accPinned && accState.current && accState.current.id===a.id}"
             :title="a.alive ? '' : t('track.acc.offline_tip')"
             @click="pickExpandAcc(a.id)">
          <span>{{ a.engine }} · {{ a.name }}{{ a.owner_port && !a.lock_mine ? t('track.acc.held', { port: String(a.owner_port) }) : '' }}</span>
          <span class="acc-alive">{{ a.alive ? t('track.acc.online') : t('track.acc.offline') }}</span>
        </div>
      </div>
    </span>
    <a class="xlink" href="#" @click.prevent="goExpandPrompt">{{ t('track.expand_prompts') }}</a>
  </div>
  <div class="banner warn" v-if="!card.on && pendingImgs.length">
    <span>{{ t('track.pending_banner', { n: String(pendingImgs.length) }) }}</span>
  </div>
  <div class="banner bad" v-if="!card.on && blockedImgs.length">
    <span>{{ t('track.refill_banner', { n: String(blockedImgs.length) }) }}</span>
    <button class="btn sm" :disabled="busy.has('refill') || !!runningKind" @click="runJob('refill')">
      {{ t('track.refill_btn') }}
    </button>
  </div>
  <div class="banner bad" v-if="brollHits.length">
    <span>{{ t('track.broll_banner', { n: String(brollHits.length) }) }}</span>
  </div>

  <div class="empty" v-if="!tid && !allTasks.length">{{ t('track.no_task') }}</div>

  <template v-else-if="tid && tidReady">
    <div class="bctl">
      <span>{{ t('track.hold') }}
        <input type="range" :min="unit==='sec'?6:1" :max="unit==='sec'?60:8"
               v-model.number="hold" @input="onHoldInput">
        <b>{{ hold }}</b> {{ unit==='sec' ? t('track.hold.sec') : t('track.hold.seg') }}
      </span>
      <span>{{ t('track.jitter') }}
        <select v-model.number="jitter">
          <option :value="0">0</option>
          <option :value="1">1</option>
          <option :value="2">2</option>
        </select>
      </span>
      <button class="btn sm tog" :class="{on: noAdj}" type="button"
              :title="t('track.spacing_tip')" @click="toggleSpacing">{{ t('track.spacing') }}</button>
      <span class="muted" v-if="spacingPending">{{ t('track.spacing_pending') }}</span>
      <span>{{ t('track.unit') }}
        <select v-model="unit" @change="onUnitChange">
          <option value="seg">{{ t('track.unit.seg') }}</option>
          <option value="sec">{{ t('track.unit.sec') }}</option>
        </select>
      </span>
      <button class="btn sm" :title="t('track.reseed_title')" @click="reseed">{{ t('track.reseed') }}</button>
      <span style="margin-left:auto"></span>
      <button class="btn sm" :title="t('track.plan_title')" :disabled="planning" @click="planNow">{{ t('track.plan') }}</button>
    </div>

    <div class="ts-bar">
      <input :value="ts.qLive" :placeholder="t('search.placeholder')"
             @input="ts.setQ($event.target.value)" @keydown="ts.onBoxKey">
      <span class="ts-count">{{ ts.countText }}</span>
      <button type="button" class="ts-nav" :disabled="!ts.hits.length" @click="ts.prev">‹</button>
      <button type="button" class="ts-nav" :disabled="!ts.hits.length" @click="ts.next">›</button>
      <button type="button" class="ts-x" v-if="ts.qLive" @click="ts.clear">×</button>
    </div>

    <div class="bwrap">
      <div class="strip-hd">
        <button type="button" class="btn sm tog" :class="{on: stripView==='pool'}"
                @click="setStripView('pool')">{{ t('track.view.pool') }}</button>
        <button type="button" class="btn sm tog" :class="{on: stripView==='shots'}"
                @click="setStripView('shots')">{{ t('track.view.shots') }}</button>
        <button type="button" class="btn sm" :disabled="!previewOn" @click="revertPreview">{{ t('track.revert') }}</button>
      </div>
      <div class="seq-bar warn" v-if="stripView==='shots' && previewOn">{{ t('track.preview_bar') }}</div>
      <div class="seq-stats" v-if="stripView==='shots'">
        <span>{{ t('track.seq.n', { n: String(shotStats.n) }) }}</span>
        <span>{{ t('track.seq.imgs', { m: String(shotStats.nImages) }) }}</span>
        <span class="seq-long" :class="{warn: shotStats.longestWarn}">{{ t('track.seq.longest', { p: shotStats.longestP || '—', k: String(shotStats.longestN || 0) }) }}</span>
        <span v-if="shotStats.nAnchor + shotStats.nBridge">{{ t('track.seq.roles', { a: String(shotStats.nAnchor), b: String(shotStats.nBridge) }) }}</span>
        <span>{{ t('track.seq.dur', { dur: shotStats.durText }) }}</span>
      </div>
      <div class="strip" v-if="stripView==='pool'">
        <button v-for="im in images" :key="im.p" :class="thClass(im)"
                :style="{borderColor: pool.includes(fmtP(im.p)) ? pcolor(im.p) : ''}"
                :title="im.status==='pending_review' ? t('track.review') : t('track.th_in')"
                @click="clickThumb(im)"
                @contextmenu.prevent="openImgMenu($event, im)">
          <img :alt="fmtP(im.p)" :src="mediaUrl(tid, im.file, im.mtime)">
          <span class="refmark" v-if="canBeRef(im)" :class="{on: !!im.expand_ref}"
                :title="t('track.ref_mark')" @click="toggleRef(im, $event)">{{ im.expand_ref ? '✓' : '' }}</span>
          <span class="tag">{{ fmtP(im.p) }}</span>
          <span class="used">{{ t('track.used', { n: String(usedCount(im.p)) }) }}</span>
          <span class="no" v-if="im.status==='blocked' || im.blocked">×</span>
          <span class="ps-hover-bar" v-if="im.status!=='pending_review' && im.exists!==false" @click.stop>
            <i class="ps-hb-btn" title="✨ 设为参考并扩图" @click.stop="quickImgAct(im, 'expand', $event)">✨扩图</i>
            <i class="ps-hb-btn" title="🔍 提升画质 / 超分重绘" @click.stop="quickImgAct(im, 'upscale', $event)">🔍超分</i>
            <i class="ps-hb-btn" title="↔ 水平镜像翻转" @click.stop="quickImgAct(im, 'flip', $event)">↔翻转</i>
            <i class="ps-hb-btn" title="✂ 裁切构图" @click.stop="quickImgAct(im, 'crop', $event)">✂裁切</i>
            <i class="ps-hb-btn ps-hb-eng" :class="{studio: expandAcc && expandAcc.id==='STUDIO'}"
               :title="'点击切换生图引擎（当前：' + ((expandAcc && expandAcc.id==='STUDIO') ? '本地 AI · FLUX.2 (可选项)' : '优先付费 Flow/Grok (默认)') + '）'"
               @click.stop="quickImgAct(im, 'engine', $event)">{{ (expandAcc && expandAcc.id==='STUDIO') ? '🖥️本地' : '⚡付费' }}</i>
          </span>
          <span class="rev" v-if="im.status==='pending_review'">
            <button class="keep" @click="keepImg(im, $event)">{{ t('track.keep') }}</button>
            <button class="drop" @click="discardImg(im, $event)">{{ t('track.discard') }}</button>
          </span>
        </button>
      </div>
      <div class="seq-scroller" v-else>
        <button v-for="cell in shotCells" :key="'q-'+cell.s" type="button"
                class="seq-cell" :class="['edge-'+cell.edge, {chg: cell.changed, hi: seqHi===cell.s}]"
                @click="focusSeg(cell.s)">
          <span class="seq-mark" v-if="cell.changed"></span>
          <video v-if="cell.clipVideo && cell.file" :src="shotSrc(cell)" muted playsinline preload="metadata"></video>
          <img v-else-if="cell.file" :alt="cell.sLab" :src="shotSrc(cell)" loading="lazy">
          <span class="seq-s">{{ cell.sLab }}</span>
          <span class="seq-p">
            <i class="swatch" :style="{background: pcolor(cell.p)}"></i>{{ cell.p }}
          </span>
          <span class="seq-dur">{{ cell.duration }}s</span><span class="aspect-muted" v-if="cell.aspect_label">{{ cell.aspect_label }}</span>
          <span class="seq-role" v-if="taskMode==='anchor_bridge'">
            {{ cell.role==='bridge' ? t('track.seq.bridge') : t('track.seq.anchor') }}
            <em v-if="cell.fromTail">{{ t('track.seq.from_tail', { s: cell.fromTail }) }}</em>
          </span>
        </button>
      </div>

      <div class="trackbar">
        <div v-for="sg in segments" :key="sg.s"
             class="tcell"
             :data-s="sg.s"
             :class="{pin: !!pins[sg.s], zone: !!zoneOf(sg.s), sel: isSelected(sg.s), lock: !!cellLocked(sg), 'need-replan': isNowBroll(sg), 'ts-hit': ts.isHit(sg.s), 'ts-flash': ts.isFlash(sg.s)}"
             :style="{background: pcolor(cellP(sg))}"
             :title="'s'+sg.s+' · '+(cellP(sg)||'')+(isNowBroll(sg) ? ' · 空镜' : '')"
             @click="clickCell(sg, $event)">
          <span class="pn">{{ cellP(sg) }}</span>
        </div>
      </div>
      <div class="taxis" v-if="axisN">
        <span>s001</span><span>{{ axisN }} 段</span><span>s{{ pad3(axisN) }}</span>
      </div>

      <div class="selbar" :class="{idle: !sel}">
        <template v-if="!sel">{{ t('track.hint.pick') }}</template>
        <template v-else>
          <span>{{ t('track.sel', { a: String(sel[0]), b: String(sel[1]), n: String(sel[1]-sel[0]+1) }) }}</span>
          <select v-model="pinPick" @change="onPinPick">
            <option value="">{{ t('track.pin') }}</option>
            <option v-for="im in images" :key="im.p" :value="fmtP(im.p)">
              {{ fmtP(im.p) }}{{ im.status==='blocked' || im.blocked ? '（'+t('track.blocked')+'）' : (im.exists===false ? '（'+t('track.missing')+'）' : '') }}
            </option>
          </select>
          <button class="btn sm" @click="setZone">{{ t('track.zone') }}</button>
          <button class="btn sm" @click="unpin">{{ t('track.unpin') }}</button>
          <button class="btn sm" @click="clearZone">{{ t('track.clear_zone') }}</button>
          <button class="btn sm" @click="unselect">{{ t('track.unselect') }}</button>
        </template>
      </div>

      <table class="btable">
        <thead><tr>
          <th style="width:52px">{{ t('track.col.seg') }}</th>
          <th style="width:140px">{{ t('track.col.image') }}</th>
          <th style="width:66px">{{ t('track.col.dur') }}</th>
          <th style="width:120px">{{ t('track.col.mode') }}</th>
          <th style="width:100%">{{ t('track.col.text') }}</th>
        </tr></thead>
        <tbody>
          <tr v-for="sg in segments" :key="sg.s"
              :data-s="sg.s"
              :title="sg.text || ''"
              :class="{chg: rowOf(sg) && rowOf(sg).changed, lock: rowOf(sg) && rowOf(sg).locked, 'need-replan': isNowBroll(sg), 'ts-hit': ts.isHit(sg.s), 'ts-flash': ts.isFlash(sg.s), 'seq-hi': seqHi===sg.s}">
            <td class="sn">s{{ pad3(sg.s) }}<b v-if="whyMark(sg)"> {{ whyMark(sg) }}</b>
              <span class="ts-badge" v-if="ts.isHit(sg.s)">{{ ts.hits.indexOf(sg.s) + 1 }}</span>
            </td>
            <td class="pp">
              <template v-if="chainLocked(sg)">
                <span class="muted">{{ chainFromLabel(sg) }}</span>
              </template>
              <template v-else-if="rowOf(sg) && rowOf(sg).changed">
                <span class="swatch" :style="{background: pcolor(sg.image)}"></span>{{ fmtP(sg.image) }}
                <span class="arrow">→</span>
                <span class="swatch" :style="{background: pcolor(rowOf(sg).p_new)}"></span>{{ fmtP(rowOf(sg).p_new) }}
              </template>
              <template v-else>
                <span class="swatch" :style="{background: pcolor(cellP(sg))}"></span>{{ cellP(sg) }}
              </template>
              <span class="old-img" v-if="sg.image_replaced"
                    :title="t('track.old_img_tip')">{{ t('track.old_img') }}</span>
            </td>
            <td class="sn dur-cell" :class="{warn: !!sg.duration_warn}"
                @contextmenu.prevent="openDurMenu($event, sg)">
              <select class="dur-sel" :value="sg.duration"
                      @click.stop @change="onDurChange(sg, $event)">
                <option v-for="d in (sg.duration_options || durOptions)" :key="d" :value="d">{{ d }}s</option>
              </select>
              <span class="dur-edit" v-if="sg.duration_manual" title="手动时长">✎</span>
              <span class="old-img" v-if="sg.duration_stale"
                    :title="t('track.dur_stale_tip')">{{ t('track.dur_stale') }}</span>
              <div class="dur-warn" v-if="sg.duration_warn">{{ sg.duration_warn }}</div>
              <div class="aspect-muted" v-if="sg.aspect_label">{{ sg.aspect_label }}</div>
            </td>
            <td class="seg-mode">
              <button type="button" class="btn sm" v-if="taskMode==='tail_chain'"
                      :title="chainLocked(sg) ? t('track.seg.chain') : t('track.seg.single')"
                      @click.stop="setSegMode(sg, chainLocked(sg) ? 'single' : 'chain')">
                {{ chainLocked(sg) ? t('track.seg.chain') : t('track.seg.single') }}
              </button>
              <button type="button" class="btn sm" v-else :disabled="taskMode!=='anchor_bridge' || isLastSeg(sg)"
                      :title="taskMode!=='anchor_bridge' ? t('track.seg.off') : (sg.role==='bridge' ? t('track.seg.single') : t('track.seg.bridge'))"
                      @click.stop="setSegMode(sg, sg.role==='bridge' ? 'single' : 'bridge')">
                {{ sg.role==='bridge' ? t('track.seg.bridge') : t('track.seg.single') }}
              </button>
            </td>
            <td class="txt" :class="{open: txtOpen[sg.s]}" @click="toggleTxt(sg.s)">
              <span v-if="ts.q" v-html="ts.highlight(sg.text || '')"></span>
              <span v-else>{{ sg.text || '' }}</span>
              <div class="w" v-if="isNowBroll(sg)">{{ t('track.broll_need_replan') }}</div>
              <div class="w" v-else-if="rowOf(sg) && (rowOf(sg).locked || rowOf(sg).warn)">
                {{ rowOf(sg).locked || rowOf(sg).warn }}
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="bfoot">
      <button class="btn primary" :disabled="applying || !rows || busy.has('arrange')"
              :title="t('track.apply_title')" @click="applyTrack">{{ t('track.apply') }}</button>
      <span class="bstat" style="margin-left:auto">{{ hint }}</span>
    </div>
  </template>

  <ul v-if="menu.show && menu.im" class="ctx" ref="menuRef"
      :style="{left: menu.x + 'px', top: menu.y + 'px'}"
      @click.stop>
    <li :class="{off: imgMenuOff(menu.im, 'copy')}" @click="onImgMenu('copy')">{{ t('track.ctx.copy') }}</li>
    <li :class="{off: imgMenuOff(menu.im, 'flip')}" @click="onImgMenu('flip')">{{ t('track.ctx.flip') }}</li>
    <li :class="{off: imgMenuOff(menu.im, 'crop')}" @click="onImgMenu('crop')">{{ t('track.ctx.crop') }}</li>
    <li :class="{off: imgMenuOff(menu.im, 'upscale')}" @click="onImgMenu('upscale')">{{ t('track.ctx.upscale') }}</li>
    <li class="sep"></li>
    <li class="bad" :class="{off: imgMenuOff(menu.im, 'discard')}" @click="onImgMenu('discard')">{{ t('track.ctx.discard') }}</li>
  </ul>

  <div class="upscale-mask" v-if="upscaleDlg.on" @click.self="closeUpscaleDlg">
    <div class="upscale-card accpick-host" @click.stop>
      <h3>{{ t('track.upscale.title', { p: upscaleDlg.p }) }}</h3>
      <p>
        <span class="upscale-acc pg-trig"
              :class="{ dragging: accGest.dragging, bump: accGest.bump }"
              @wheel="accGest.onWheel"
              @pointerdown="accGest.onPointerDown"
              @pointermove="accGest.onPointerMove"
              @pointerup="accGest.onPointerUp"
              @pointercancel="accGest.onPointerCancel"
              @click.stop="accGest.onClick"
              @contextmenu="accGest.onContextMenu">{{ t('track.upscale.acc', { acc: accText(shownAcc) || '—' }) }}</span>
      </p>
      <div class="accpick-menu" v-if="accOpen" style="position:relative;top:0;right:auto;margin:0 0 10px" @click.stop>
        <div class="accpick-item" :class="{on: !accPinned}" @click="pickExpandAcc('')">
          {{ accPinned ? '○ ' : '● ' }}{{ accFollowLabel }}
        </div>
        <div class="accpick-borrowed" v-if="accBorrowed">{{ t('track.acc.borrowed') }}</div>
        <div class="accpick-sep"></div>
        <div v-for="a in accOptions" :key="'u'+a.id" class="accpick-item"
             :class="{on: accPinned && accState.current && accState.current.id===a.id}"
             :title="a.alive ? '' : t('track.acc.offline_tip')"
             @click="pickExpandAcc(a.id)">
          <span>{{ a.engine }} · {{ a.name }}{{ a.owner_port && !a.lock_mine ? t('track.acc.held', { port: String(a.owner_port) }) : '' }}</span>
          <span class="acc-alive">{{ a.alive ? t('track.acc.online') : t('track.acc.offline') }}</span>
        </div>
      </div>
      <p>{{ t('track.upscale.size', { w: String(upscaleDlg.w), h: String(upscaleDlg.h) }) }}</p>
      <p>{{ t('track.upscale.bak') }}</p>
      <div class="upscale-ft">
        <button class="btn sm" type="button" @click="closeUpscaleDlg">{{ t('track.upscale.cancel') }}</button>
        <button class="btn sm primary" type="button" :disabled="upscaleDlg.starting" @click="startUpscale">{{ t('track.upscale.start') }}</button>
      </div>
    </div>
  </div>

  <ul v-if="durMenu.show && durMenu.sg" class="ctx" :style="{left: durMenu.x + 'px', top: durMenu.y + 'px'}"
      @click.stop>
    <li v-if="durMenu.sg.duration_manual" @click="restoreDur(durMenu.sg)">{{ t('track.dur_restore') }}</li>
    <li @click="openBatchDurDlg">{{ t('track.dur_batch') }}</li>
  </ul>
  <div class="dur-mask" v-if="durDlg.on" @click.self="durDlg.on=false">
    <div class="dur-card" @click.stop>
      <h3>{{ t('track.dur_batch_title') }}</h3>
      <p class="muted">{{ durDlg.note }}</p>
      <p v-if="durDlg.engines.indexOf('flow')>=0">
        Flow
        <select v-model.number="durDlg.flowDur">
          <option :value="4">4s</option><option :value="6">6s</option><option :value="8">8s</option>
        </select>
      </p>
      <p v-if="durDlg.engines.indexOf('grok')>=0">
        Grok
        <select v-model.number="durDlg.grokDur">
          <option :value="6">6s</option><option :value="10">10s</option><option :value="15">15s</option>
        </select>
      </p>
      <p v-if="durDlg.engines.indexOf('studio')>=0">
        本地模型
        <select v-model.number="durDlg.studioDur">
          <option v-for="n in [4,5,6,7,8,9,10,11,12,13,14,15]" :key="n" :value="n">{{ n }}s</option>
        </select>
      </p>
      <div class="dur-ft">
        <button class="btn sm" type="button" @click="durDlg.on=false">{{ t('track.dur_cancel') }}</button>
        <button class="btn sm primary" type="button" :disabled="!durDlg.sList.length" @click="applyBatchDur">{{ t('track.dur_apply') }}</button>
      </div>
    </div>
  </div>

</section>`
  };
})();
