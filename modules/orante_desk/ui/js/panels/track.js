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

  // rows 非空用预演 p_new；空则读 pins 或 manifest.segments[].image（生成时那份）。
  function shotCells(segments, rows, images, pins) {
    const segs = segments || [];
    const byS = {};
    (rows || []).forEach(r => { if (r && r.s != null) byS[r.s] = r; });
    const preview = !!(rows && rows.length);
    const imgs = images || [];
    const byP = {};
    imgs.forEach(im => { if (im && im.p) byP[fmtP(im.p)] = im; });
    const fallbackP = (imgs[0] && imgs[0].p) ? fmtP(imgs[0].p) : '';
    return segs.map((sg, i) => {
      const r = byS[sg.s];
      const pinned = pins && pins[sg.s] ? fmtP(pins[sg.s]) : '';
      const p = fmtP(pinned || (r && r.p_new) || sg.image || fallbackP || '');
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
        file: useClip ? relClip : ((im && im.file) || (p ? ('images/' + p + '.jpg') : '')),
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
        const tsk = selectedTask.value;
        const st = tsk && tsk.status;
        if (!st) return false;
        if (tsk && tsk.counts && Number(tsk.counts.desc_missing) === 0) return false;
        if (segments.value && segments.value.length && segments.value.every(s => s.preset_id || s.custom_prompt || s.prompt_preview || s.prompt_final)) {
          return false;
        }
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
        const inpool = pool.value.includes(p);
        return {
          th: true,
          inpool,
          excluded: !inpool && !dead && !review,
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
          Vue.nextTick(() => {
            skipWatch = false;
            // 若当前任务已有图片但存在未分配配图的分段，自动预演排轨，确保镜头序列与轴条实时显示图片
            if (images.value.length > 0 && segments.value.length > 0 && segments.value.some(sg => !sg.image && !pins[sg.s])) {
              planNow();
            }
          });
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

      async function clickThumb(im) {
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
          // 排除该图：清除钉住与限定区间中的该图，并将所有正在使用该图的分段立即重分配给可用图池
          for (const [k, v] of Object.entries(pins)) {
            if (fmtP(v) === p) delete pins[k];
          }
          zones.value.forEach(z => {
            if (Array.isArray(z.images)) {
              z.images = z.images.filter(x => fmtP(x) !== p);
            }
          });
          const avail = pool.value.slice();
          if (avail.length && tid.value) {
            const updates = [];
            (segments.value || []).forEach((sg, idx) => {
              if (fmtP(sg.image) === p || fmtP(cellP(sg)) === p) {
                const replacement = avail[idx % avail.length];
                sg.image = replacement;
                syncLocalSegImage(tid.value, sg.s, replacement);
                delete pins[sg.s];
                updates.push({ task_id: tid.value, s: sg.s, image: replacement });
              }
            });
            if (updates.length) {
              saveBatchSegUpdates(updates);
            }
          }
          toast(`🚫 已将 ${p} 标记为「不使用」，已从所有分段排除`, '图片保留在图池中，可随时右键或点击恢复使用', 'ok');
        } else {
          pool.value.push(p);
          pool.value.sort((a, b) => pNum(a) - pNum(b));
          toast(`✅ 已恢复使用图片 ${p}`, '', 'ok');
        }
        saveSettings();
        await planNow();
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
        if (gsCellMenu.show && !(ev.target && ev.target.closest && ev.target.closest('.gs-context-menu'))) {
          gsCellMenu.show = false;
        }
        if (accOpen.value && !(ev.target && ev.target.closest && ev.target.closest('.accpick-host'))) {
          accOpen.value = false;
        }
      }

      function onMenuKey(ev) {
        if (upscaleDlg.on && ev.key === 'Escape') { closeUpscaleDlg(); return; }
        if (accOpen.value && ev.key === 'Escape') { accOpen.value = false; return; }
        if (gsCellMenu.show && ev.key === 'Escape') { gsCellMenu.show = false; return; }
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
        const jobExtra = Object.assign({}, extra || {});
        if (kind === 'expand' && expandPresetId.value) {
          jobExtra.expand_preset_id = expandPresetId.value;
          const ep = (store.promptPresets || []).find(x => x && x.id === expandPresetId.value);
          if (ep && ep.template) jobExtra.prompt_template = ep.template;
        }
        runningKind.value = kind;
        openCard(kind);
        try {
          const res = await fetch('/api/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Object.assign({ kind, task_id: tid.value }, jobExtra)),
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

      const batchApplying = ref(false);
      const unarrangedCount = computed(() =>
        (readyTasks.value || []).filter(x => x.status === 'prepared' || x.status === 'described').length
      );

      async function batchApplyAll(startGen = false) {
        if (batchApplying.value) return;
        batchApplying.value = true;
        try {
          if (window.flushAll) await window.flushAll();
          const r = await API.post('/api/track/batch-apply', {
            only_unarranged: false,
            start_generate: !!startGen,
          });
          try {
            const tr = await API.get('/api/tasks');
            if (tr && Array.isArray(tr.tasks)) store.tasks = tr.tasks;
          } catch (e) { /* ignore */ }
          if (tid.value) await loadTrack();
          const cnt = String((r && r.count) || 0);
          const scnt = String((r && r.started_count) || 0);
          if (startGen) {
            toast(
              '🚀 已批量跳过/应用 ' + cnt + ' 个任务编排，并排队启动 ' + scnt + ' 个生成任务',
              '已为您切到 ④ 生成面板查看进度',
              'ok'
            );
            ui.node = 'generate';
          } else {
            toast(
              '✓ 已一键对 ' + cnt + ' 个任务批量应用/跳过编排',
              '所有短视频/单图及多图任务均已就绪，可直接进入 ④ 生成排队',
              'ok'
            );
          }
        } catch (e) {
          /* API.post 已弹 toast */
        } finally {
          batchApplying.value = false;
        }
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
        Lshot.shotCells(segments.value, rows.value, images.value, pins));
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

      // ─── 需求 4 升级：Google Sheet 标准单元格网格、可调行高列宽、右下角拖拽拷贝、隐藏任务行保留颜色外边框、行内缩略图 + 外部图片预览悬浮窗 ───
      const TASK_COLORS = ['#d4a24c', '#3b82f6', '#10b981', '#a855f7', '#ec4899', '#f97316', '#06b6d4', '#84cc16'];
      const expandPresetId = ref('');
      const presetSearch = ref('');
      const hoveredImgP = ref('');
      const activePoolImg = computed(() => {
        const list = (images.value || []).filter(im => im && im.status !== 'pending_review' && im.exists !== false);
        if (!list.length) return null;
        if (hoveredImgP.value) {
          const hit = list.find(im => fmtP(im.p) === fmtP(hoveredImgP.value));
          if (hit) return hit;
        }
        return list[0];
      });
      const showAllTasks = ref(true);           // 默认展示全部任务形成完整表格
      const showTaskHeaderRows = ref(false);    // 默认隐藏“任务”行，只保留同组外框颜色
      const expandedTaskRows = reactive({});    // 点击某组首行任务徽标可单独展开该任务的任务行
      const batchMenuOpen = ref(false);         // 顶部工具栏收纳下拉菜单
      const allTasksTable = ref([]);
      const collapsedTasks = reactive({});
      const segChecked = reactive({}); // `${taskId}:${s}` -> bool
      const segDrag = reactive({ active: false, val: true });

      // Google Sheet 列宽与行高状态
      const colWidths = reactive({
        check: 36,
        seg: 92,
        image: 146,
        dur: 74,
        flow: 126,
        conn: 98,
        text: 360,
        preset: 180,
        prompt: 340,
      });
      const rowHeights = reactive({}); // `${taskId}:${s}` -> px
      let resizeState = null;

      function startColResize(colKey, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const startX = ev.clientX;
        const startW = Number(colWidths[colKey] || 120);
        resizeState = { type: 'col', key: colKey, startX, startW };
        const onMove = (e) => {
          if (!resizeState || resizeState.type !== 'col') return;
          colWidths[colKey] = Math.max(42, startW + (e.clientX - startX));
        };
        const onUp = () => {
          resizeState = null;
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      }

      function startRowResize(rowKey, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const tr = ev.target.closest('tr');
        const startY = ev.clientY;
        const startH = Number(rowHeights[rowKey] || (tr ? tr.offsetHeight : 38));
        resizeState = { type: 'row', key: rowKey, startY, startH };
        const onMove = (e) => {
          if (!resizeState || resizeState.type !== 'row') return;
          rowHeights[rowKey] = Math.max(28, startH + (e.clientY - startY));
        };
        const onUp = () => {
          resizeState = null;
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      }

      // 按任务分组构建表格数据：支持「收起全部任务组」时保留任务组汇总行并隐藏所有段行
      const sheetTaskGroups = computed(() => {
        const groups = [];
        let flatCounter = 0;
        if (!showAllTasks.value) {
          const list = segments.value || [];
          const tColor = taskColor(0, selectedTask.value);
          const isCollapsed = !!collapsedTasks[tid.value];
          const grpRows = [];
          if (!isCollapsed) {
            list.forEach((sg, idx) => {
              const p = fmtP(cellP(sg) || sg.image || 'p01');
              const im = (images.value || []).find(x => fmtP(x.p) === p);
              const thumbUrl = im && im.file ? mediaUrl(tid.value, im.file, im.mtime) : (tid.value ? `/api/tasks/${encodeURIComponent(tid.value)}/file/images/${p}.jpg` : '');
              grpRows.push({
                flatIdx: flatCounter++,
                rowKey: `${tid.value}:${sg.s}`,
                taskId: tid.value,
                taskName: (selectedTask.value && selectedTask.value.name) || tid.value,
                taskColor: tColor,
                taskObj: selectedTask.value,
                taskImages: images.value || [],
                seg: sg,
                sIdx: idx,
                isFirstInGroup: idx === 0,
                isLastInGroup: idx === list.length - 1,
                thumbUrl,
                pCode: p,
              });
            });
          }
          if (tid.value) {
            groups.push({
              taskId: tid.value,
              taskName: (selectedTask.value && selectedTask.value.name) || tid.value,
              taskColor: tColor,
              taskObj: selectedTask.value,
              segList: list,
              segCount: list.length,
              rows: grpRows,
            });
          }
          return groups;
        }
        (allTasksTable.value || []).forEach((tk, tIdx) => {
          const list = tk.segments || [];
          const tColor = taskColor(tIdx, tk);
          const tkImgs = (tk.images && tk.images.length) ? tk.images : (tid.value === tk.id ? images.value : []);
          const isCollapsed = !!collapsedTasks[tk.id];
          const grpRows = [];
          if (!isCollapsed) {
            list.forEach((sg, idx) => {
              const p = fmtP((tid.value === tk.id ? cellP(sg) : sg.image) || sg.image || 'p01');
              const im = (tkImgs || []).find(x => fmtP(x.p) === p);
              const relFile = (im && im.file) || sg.image_file || `images/${p}.jpg`;
              const thumbUrl = mediaUrl(tk.id, relFile, (im && im.mtime) || 0);
              grpRows.push({
                flatIdx: flatCounter++,
                rowKey: `${tk.id}:${sg.s}`,
                taskId: tk.id,
                taskName: tk.name || tk.id,
                taskColor: tColor,
                taskObj: tk,
                taskImages: tkImgs,
                seg: sg,
                sIdx: idx,
                isFirstInGroup: idx === 0,
                isLastInGroup: idx === list.length - 1,
                thumbUrl,
                pCode: p,
              });
            });
          }
          groups.push({
            taskId: tk.id,
            taskName: tk.name || tk.id,
            taskColor: tColor,
            taskObj: tk,
            segList: list,
            segCount: list.length,
            rows: grpRows,
          });
        });
        return groups;
      });

      // 扁平化可见数据行列表（用于 Google Sheet 2D 单元格选区、右下角拖拽填充与悬浮图片阅读器）
      const sheetFlatRows = computed(() => {
        const out = [];
        for (const grp of sheetTaskGroups.value) {
          for (const r of grp.rows) out.push(r);
        }
        return out;
      });

      // Google Sheet 列字段定义 (0..6)
      const GS_COLS = ['image', 'duration', 'flow_mode', 'mode', 'text', 'preset_id', 'custom_prompt'];
      const gsSel = reactive({
        r0: -1, c0: -1, r1: -1, c1: -1,
        activeR: -1, activeC: -1,
        selecting: false,
        fillDragging: false,
        fillTargetR: -1,
      });

      function gsNormRange() {
        if (gsSel.r0 < 0 || gsSel.c0 < 0) return null;
        return {
          minR: Math.min(gsSel.r0, gsSel.r1),
          maxR: Math.max(gsSel.r0, gsSel.r1),
          minC: Math.min(gsSel.c0, gsSel.c1),
          maxC: Math.max(gsSel.c0, gsSel.c1),
        };
      }

      function isGsCellInRange(r, c) {
        const rg = gsNormRange();
        if (!rg) return false;
        return r >= rg.minR && r <= rg.maxR && c >= rg.minC && c <= rg.maxC;
      }

      function isGsCellActive(r, c) {
        return gsSel.activeR === r && gsSel.activeC === c;
      }

      function isGsFillHandleCell(r, c) {
        const rg = gsNormRange();
        if (!rg) return false;
        return r === rg.maxR && c === rg.maxC;
      }

      function isGsCellFillPreview(r, c) {
        if (!gsSel.fillDragging || gsSel.fillTargetR < 0) return false;
        const rg = gsNormRange();
        if (!rg || c < rg.minC || c > rg.maxC) return false;
        if (gsSel.fillTargetR > rg.maxR) return r > rg.maxR && r <= gsSel.fillTargetR;
        if (gsSel.fillTargetR < rg.minR) return r >= gsSel.fillTargetR && r < rg.minR;
        return false;
      }

      function onGsCellMouseDown(r, c, ev) {
        if (ev.button !== 0) return;
        const rowItem = sheetFlatRows.value[r];
        if (rowItem) {
          hotSwitchTask(rowItem.taskId);
          floatingViewer.rowIdx = r;
        }
        if (ev.shiftKey && gsSel.activeR >= 0 && gsSel.activeC >= 0) {
          gsSel.r1 = r;
          gsSel.c1 = c;
          return;
        }
        gsSel.r0 = r;
        gsSel.c0 = c;
        gsSel.r1 = r;
        gsSel.c1 = c;
        gsSel.activeR = r;
        gsSel.activeC = c;
        gsSel.selecting = true;
      }

      function onGsCellMouseEnter(r, c) {
        if (gsSel.fillDragging) {
          gsSel.fillTargetR = r;
          return;
        }
        if (gsSel.selecting) {
          gsSel.r1 = r;
          gsSel.c1 = c;
        }
      }

      function startGsFillDrag(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const rg = gsNormRange();
        if (!rg) return;
        gsSel.fillDragging = true;
        gsSel.fillTargetR = rg.maxR;
      }

      // 核心原则：在 2 排镜头 中，提示词覆盖列始终保留【人物说的话】动态变量插槽，绝不把左侧具体文案写死进提示词！
      const editingPromptKey = ref('');

      function composePresetPreview(presetId) {
        if (!presetId) return '';
        const list = store.promptPresets || [];
        const pr = list.find(p => p && (p.id === presetId || p.name === presetId));
        if (!pr || !pr.template) return '';
        return String(pr.template);
      }

      function presetNameOf(presetId) {
        if (!presetId) return '';
        const list = store.promptPresets || [];
        const pr = list.find(p => p && (p.id === presetId || p.name === presetId));
        return pr ? pr.name : presetId;
      }

      function stripSpeechIfPresent(rawPrompt, segObj) {
        if (!rawPrompt) return '';
        const s = String(rawPrompt);
        if (window.PresetSlotLogic && typeof window.PresetSlotLogic.stripBakedSpeech === 'function') {
          return window.PresetSlotLogic.stripBakedSpeech(s, segObj && segObj.text ? [segObj.text] : []);
        }
        return s;
      }

      function getSegPromptDisplay(sg) {
        if (!sg) return '';
        if (sg.custom_prompt && String(sg.custom_prompt).trim()) {
          return stripSpeechIfPresent(sg.custom_prompt, sg);
        }
        if (sg.preset_id) {
          return composePresetPreview(sg.preset_id) || stripSpeechIfPresent(sg.prompt_preview || '', sg);
        }
        return stripSpeechIfPresent(sg.prompt_preview || '', sg);
      }

      function analyzeSegPromptSlot(sg) {
        const tpl = getSegPromptDisplay(sg);
        if (window.PresetSlotLogic && typeof window.PresetSlotLogic.analyzeTemplate === 'function') {
          return window.PresetSlotLogic.analyzeTemplate(tpl);
        }
        return { hasContent: !!tpl, hasNewline: false, maxNewlines: 0, prefix: tpl, suffix: '' };
      }

      function startEditSegPrompt(item, ev) {
        if (!item) return;
        editingPromptKey.value = item.rowKey;
        setTimeout(() => {
          const el = document.querySelector(`textarea[data-prompt-edit="${item.rowKey}"]`);
          if (el && el.focus) el.focus();
        }, 20);
      }

      function stopEditSegPrompt() {
        editingPromptKey.value = '';
      }

      function getCellVal(rowItem, colIdx) {
        if (!rowItem || !rowItem.seg) return '';
        const sg = rowItem.seg;
        const field = GS_COLS[colIdx];
        if (field === 'image') return fmtP(sg.image || 'p01');
        if (field === 'duration') return String(sg.duration || 6);
        if (field === 'flow_mode') return sg.flow_mode || 'auto';
        if (field === 'mode') return sg.mode || (sg.role === 'bridge' ? 'bridge' : 'single');
        if (field === 'text') return sg.text || '';
        if (field === 'preset_id') return sg.preset_id || '';
        if (field === 'custom_prompt') return getSegPromptDisplay(sg);
        return '';
      }

      function syncLocalSegImage(taskId, sNum, pCode) {
        if (taskId === tid.value) {
          pins[sNum] = pCode;
          const s0 = (segments.value || []).find(x => x.s === sNum);
          if (s0) s0.image = pCode;
          if (rows.value && Array.isArray(rows.value)) {
            const r0 = rows.value.find(x => x && x.s === sNum);
            if (r0) r0.p_new = pCode;
          }
        }
        const atk = (allTasksTable.value || []).find(x => x && x.id === taskId);
        if (atk && Array.isArray(atk.segments)) {
          const s1 = atk.segments.find(x => x && x.s === sNum);
          if (s1) s1.image = pCode;
        }
      }

      function applyCellValToRow(rowItem, colIdx, rawVal, updatesOut) {
        if (!rowItem || !rowItem.seg) return;
        const sg = rowItem.seg;
        const field = GS_COLS[colIdx];
        const u = { task_id: rowItem.taskId, s: sg.s };
        const val = String(rawVal == null ? '' : rawVal).trim();
        if (field === 'image') {
          const p = fmtP(val || 'p01');
          sg.image = p;
          u.image = p;
          syncLocalSegImage(rowItem.taskId, sg.s, p);
        } else if (field === 'duration') {
          const d = parseInt(val.replace(/s$/i, ''), 10) || 6;
          sg.duration = d;
          u.duration = d;
        } else if (field === 'flow_mode') {
          const fm = ['auto', 'frames', 'ingredients'].includes(val) ? val : 'auto';
          sg.flow_mode = fm;
          u.flow_mode = fm;
        } else if (field === 'mode') {
          const cm = ['single', 'bridge', 'chain'].includes(val) ? val : 'single';
          sg.mode = cm;
          u.mode = cm;
        } else if (field === 'text') {
          sg.text = String(rawVal == null ? '' : rawVal);
          u.text = sg.text;
        } else if (field === 'preset_id') {
          // 支持填入预设 ID 或预设名称；二选一：指定或清空预设时清空 custom_prompt
          let pid = val;
          const byName = allPresets.value.find(p => p && (p.id === val || p.name === val));
          if (byName) pid = byName.id;
          sg.preset_id = pid;
          sg.custom_prompt = '';
          u.preset_id = pid;
          u.custom_prompt = '';
        } else if (field === 'custom_prompt') {
          const cp = stripSpeechIfPresent(String(rawVal == null ? '' : rawVal), sg);
          sg.custom_prompt = cp;
          u.custom_prompt = cp;
          // 二选一：只要手动添加/粘贴了提示词覆盖，前面的预设自动清空
          sg.preset_id = '';
          u.preset_id = '';
        }
        updatesOut.push(u);
      }

      async function commitGsFillDrag() {
        if (!gsSel.fillDragging) return;
        const rg = gsNormRange();
        const targetR = gsSel.fillTargetR;
        gsSel.fillDragging = false;
        gsSel.fillTargetR = -1;
        if (!rg || targetR < 0 || (targetR >= rg.minR && targetR <= rg.maxR)) return;
        const flat = sheetFlatRows.value;
        const srcRows = [];
        for (let r = rg.minR; r <= rg.maxR; r++) {
          if (flat[r]) srcRows.push(flat[r]);
        }
        if (!srcRows.length) return;
        const updates = [];
        const startR = targetR > rg.maxR ? rg.maxR + 1 : targetR;
        const endR = targetR > rg.maxR ? targetR : rg.minR - 1;
        for (let r = startR; r <= endR; r++) {
          const dst = flat[r];
          if (!dst) continue;
          const src = srcRows[(r - startR) % srcRows.length];
          for (let c = rg.minC; c <= rg.maxC; c++) {
            const v = getCellVal(src, c);
            applyCellValToRow(dst, c, v, updates);
          }
        }
        if (targetR > rg.maxR) gsSel.r1 = targetR;
        else gsSel.r0 = targetR;
        if (updates.length) {
          saveSettings();
          await saveBatchSegUpdates(updates, `已拖拽填充 ${updates.length} 个单元格`);
        }
      }

      async function handleGsCopyPasteKey(ev) {
        const tag = (ev.target && ev.target.tagName || '').toLowerCase();
        const inInput = tag === 'input' || tag === 'textarea' || tag === 'select';
        const rg = gsNormRange();

        // 外部图片预览悬浮窗快捷键支持 (↓/↑/J/K/Space 切换上/下一行图片)
        if (floatingViewer.on && !inInput && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
          const flatLen = sheetFlatRows.value.length;
          if (flatLen > 0) {
            if (ev.key === 'ArrowDown' || ev.key === 'j' || ev.key === 'J' || ev.key === ' ') {
              ev.preventDefault();
              stepFloatingViewer(1);
              return;
            }
            if (ev.key === 'ArrowUp' || ev.key === 'k' || ev.key === 'K') {
              ev.preventDefault();
              stepFloatingViewer(-1);
              return;
            }
          }
        }

        if (!rg) return;
        const isMultiCell = rg.minR !== rg.maxR || rg.minC !== rg.maxC;
        const isTextEditing = tag === 'input' || tag === 'textarea';
        // 支持按 Delete / Backspace 直接清空选中的预设名称、提示词覆盖或文案单元格
        if ((ev.key === 'Delete' || ev.key === 'Backspace') && (!isTextEditing || isMultiCell)) {
          ev.preventDefault();
          await clearSelectedGsCells();
          return;
        }
        if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'c' && (!isTextEditing || isMultiCell)) {
          ev.preventDefault();
          const flat = sheetFlatRows.value;
          const lines = [];
          for (let r = rg.minR; r <= rg.maxR; r++) {
            const rowItem = flat[r];
            if (!rowItem) continue;
            const cols = [];
            for (let c = rg.minC; c <= rg.maxC; c++) {
              cols.push(String(getCellVal(rowItem, c)).replace(/\r?\n/g, ' '));
            }
            lines.push(cols.join('\t'));
          }
          if (navigator.clipboard && lines.length) {
            navigator.clipboard.writeText(lines.join('\n')).catch(() => {});
            toast(`已复制 ${rg.maxR - rg.minR + 1} 行 × ${rg.maxC - rg.minC + 1} 列单元格`, '', 'ok');
          }
          return;
        }
        if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'v' && (!inInput || isMultiCell)) {
          if (!navigator.clipboard || !navigator.clipboard.readText) return;
          try {
            const text = await navigator.clipboard.readText();
            if (!text) return;
            ev.preventDefault();
            const flat = sheetFlatRows.value;
            const rowsText = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
            while (rowsText.length && rowsText[rowsText.length - 1] === '') rowsText.pop();
            const updates = [];
            for (let i = 0; i < rowsText.length; i++) {
              const dst = flat[rg.minR + i];
              if (!dst) break;
              const cols = rowsText[i].split('\t');
              for (let j = 0; j < cols.length; j++) {
                const cIdx = rg.minC + j;
                if (cIdx >= GS_COLS.length) break;
                applyCellValToRow(dst, cIdx, cols[j], updates);
              }
            }
            if (updates.length) {
              await saveBatchSegUpdates(updates, `已批量粘贴 ${updates.length} 个单元格`);
            }
          } catch (e) { /* clipboard permission fallback */ }
        }
      }

      // 外部图片预览悬浮窗口状态与拖拽
      const floatingViewer = reactive({
        on: false,
        rowIdx: 0,
        x: null,
        y: null,
      });

      const currentFloatingRow = computed(() => {
        const flat = sheetFlatRows.value;
        if (!flat.length) return null;
        const idx = Math.max(0, Math.min(flat.length - 1, floatingViewer.rowIdx || 0));
        return flat[idx] || flat[0];
      });

      function toggleFloatingViewer() {
        floatingViewer.on = !floatingViewer.on;
        if (floatingViewer.on && gsSel.activeR >= 0) {
          floatingViewer.rowIdx = gsSel.activeR;
        }
      }

      function stepFloatingViewer(delta) {
        const flat = sheetFlatRows.value;
        if (!flat.length) return;
        const next = Math.max(0, Math.min(flat.length - 1, (floatingViewer.rowIdx || 0) + delta));
        floatingViewer.rowIdx = next;
        gsSel.activeR = next;
        gsSel.r0 = next;
        gsSel.r1 = next;
        if (gsSel.activeC < 0) { gsSel.activeC = 0; gsSel.c0 = 0; gsSel.c1 = 0; }
        const item = flat[next];
        if (item) {
          hotSwitchTask(item.taskId);
          const tr = document.querySelector(`.panel-track tr[data-rowkey="${item.rowKey}"]`);
          if (tr && tr.scrollIntoView) tr.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      }

      function startFloatingViewerDrag(ev) {
        if (ev.button !== 0) return;
        const box = ev.target.closest('.gs-floating-img-viewer');
        if (!box) return;
        const rect = box.getBoundingClientRect();
        const offsetX = ev.clientX - rect.left;
        const offsetY = ev.clientY - rect.top;
        const onMove = (e) => {
          floatingViewer.x = Math.max(8, Math.min(window.innerWidth - rect.width - 8, e.clientX - offsetX));
          floatingViewer.y = Math.max(8, Math.min(window.innerHeight - 80, e.clientY - offsetY));
        };
        const onUp = () => {
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      }

      function toggleAllTaskHeaderRows() {
        showTaskHeaderRows.value = !showTaskHeaderRows.value;
        for (const k of Object.keys(expandedTaskRows)) {
          delete expandedTaskRows[k];
        }
      }

      function toggleSingleTaskHeaderRow(taskId, ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        if (showTaskHeaderRows.value) {
          showTaskHeaderRows.value = false;
          for (const grp of sheetTaskGroups.value) {
            expandedTaskRows[grp.taskId] = grp.taskId !== taskId;
          }
        } else {
          expandedTaskRows[taskId] = !expandedTaskRows[taskId];
        }
      }

      function isTaskHeaderVisible(taskId) {
        return !!showTaskHeaderRows.value || !!expandedTaskRows[taskId] || !!collapsedTasks[taskId];
      }

      const areAllTasksCollapsed = computed(() => {
        const groups = sheetTaskGroups.value;
        return groups.length > 0 && groups.every(g => !!collapsedTasks[g.taskId]);
      });

      const allPresets = computed(() => store.promptPresets || []);
      const expandPresets = computed(() => {
        const list = allPresets.value.filter(p => p && p.role === 'expand');
        return list.length ? list : allPresets.value;
      });
      const filteredPresets = computed(() => {
        const q = presetSearch.value.trim().toLowerCase();
        if (!q) return allPresets.value;
        return allPresets.value.filter(p =>
          p && (`${p.name || ''} ${p.category || ''} ${p.template || ''}`).toLowerCase().includes(q)
        );
      });

      const curTaskFlowMode = computed({
        get: () => (selectedTask.value && selectedTask.value.flow_mode) || 'auto',
        set: (v) => { if (selectedTask.value) selectedTask.value.flow_mode = v; },
      });
      const curTaskPresetId = computed({
        get: () => (selectedTask.value && selectedTask.value.preset_id) || '',
        set: (v) => { if (selectedTask.value) selectedTask.value.preset_id = v; },
      });

      function taskColor(idx, tObj) {
        if (tObj && tObj.color) return tObj.color;
        return TASK_COLORS[idx % TASK_COLORS.length];
      }

      async function loadAllTasksTable(silent = true) {
        try {
          const r = await API.get('/api/tasks/all-segments-table', { silent });
          if (r && Array.isArray(r.tasks)) {
            allTasksTable.value = r.tasks;
            r.tasks.forEach(tk => {
              if (collapsedTasks[tk.id] === undefined) {
                collapsedTasks[tk.id] = !!tk.collapsed;
              }
              (tk.segments || []).forEach(sg => {
                const k = tk.id + ':' + sg.s;
                if (segChecked[k] === undefined) segChecked[k] = sg.checked !== false;
              });
            });
          }
        } catch (e) { /* ignore if backend route still booting */ }
      }

      async function toggleShowAllTasks() {
        showAllTasks.value = !showAllTasks.value;
        if (showAllTasks.value) {
          await loadAllTasksTable(false);
        }
      }

      function expandAllTasks(collapse) {
        if (tid.value) collapsedTasks[tid.value] = !!collapse;
        for (const tk of allTasks.value) {
          if (tk && tk.id) collapsedTasks[tk.id] = !!collapse;
        }
        for (const tk of allTasksTable.value) {
          if (tk && tk.id) collapsedTasks[tk.id] = !!collapse;
        }
      }

      function hotSwitchTask(taskId) {
        if (!taskId || tid.value === taskId) return;
        tid.value = taskId;
        ui.panelTid = taskId;
        ui.selectedTaskId = taskId;
      }

      function isSegChecked(taskId, s) {
        const k = (taskId || tid.value) + ':' + s;
        return !!segChecked[k];
      }

      function startSegDrag(taskId, s, ev) {
        if (ev && ev.button !== 0) return;
        const k = (taskId || tid.value) + ':' + s;
        const next = !segChecked[k];
        segDrag.active = true;
        segDrag.val = next;
        segChecked[k] = next;
      }

      function enterSegDrag(taskId, s) {
        if (!segDrag.active) return;
        const k = (taskId || tid.value) + ':' + s;
        segChecked[k] = segDrag.val;
      }

      function stopSegDrag() {
        segDrag.active = false;
      }

      function isTaskAllSegChecked(taskId, segList) {
        const list = segList || segments.value || [];
        const tId = taskId || tid.value;
        return list.length > 0 && list.every(sg => !!segChecked[tId + ':' + sg.s]);
      }

      function toggleTaskAllSeg(taskId, segList) {
        const list = segList || segments.value || [];
        const tId = taskId || tid.value;
        const allOn = isTaskAllSegChecked(tId, list);
        list.forEach(sg => {
          segChecked[tId + ':' + sg.s] = !allOn;
        });
      }

      async function saveBatchSegUpdates(updates, toastMsg, actionLabel) {
        if (!updates || !updates.length) return;
        try {
          await API.post('/api/tasks/batch-update-segments', {
            action_label: actionLabel || toastMsg || '',
            updates,
          });
          if (window.refreshHistoryStatus) window.refreshHistoryStatus();
          if (toastMsg) toast(toastMsg, '', 'ok');
          if (showAllTasks.value) await loadAllTasksTable(true);
        } catch (e) { /* API toast */ }
      }

      async function onTaskPresetChange(taskId, presetId) {
        const tId = taskId || tid.value;
        if (!tId) return;
        const tk = (store.tasks || []).find(x => x.id === tId);
        if (tk) tk.preset_id = presetId;
        const atk = allTasksTable.value.find(x => x.id === tId);
        if (atk) atk.preset_id = presetId;
        await saveBatchSegUpdates([{ task_id: tId, task_preset_id: presetId }], '已应用任务总预设');
        if (presetId) {
          const segs = (atk ? atk.segments : segments.value) || [];
          const targets = segs.map(sg => ({ task_id: tId, s: sg.s }));
          if (targets.length) {
            try {
              await API.post('/api/tasks/batch-apply-preset', {
                mode: 'single',
                preset_id: presetId,
                col_id: 'main',
                targets,
              });
              if (tId === tid.value) await loadTrack();
              if (showAllTasks.value) await loadAllTasksTable(true);
            } catch (e) { /* ignore */ }
          }
        }
      }

      async function onTaskFlowModeChange(taskId, flowMode) {
        const tId = taskId || tid.value;
        if (!tId) return;
        const tk = (store.tasks || []).find(x => x.id === tId);
        if (tk) tk.flow_mode = flowMode;
        const atk = allTasksTable.value.find(x => x.id === tId);
        if (atk) atk.flow_mode = flowMode;
        await saveBatchSegUpdates([{ task_id: tId, task_flow_mode: flowMode }], '已更新任务默认 Flow 模式');
      }

      async function onSegImageChange(taskId, sg, newP) {
        const tId = taskId || tid.value;
        const p = fmtP(newP);
        sg.image = p;
        syncLocalSegImage(tId, sg.s, p);
        if (tId === tid.value) {
          saveSettings();
        }
        await saveBatchSegUpdates([{ task_id: tId, s: sg.s, image: p }], `s${pad3(sg.s)} 配图已设为 ${p}`);
      }

      async function onSegFlowModeChange(taskId, sg, flowMode) {
        const tId = taskId || tid.value;
        sg.flow_mode = flowMode;
        try {
          await API.post(`/api/tasks/${encodeURIComponent(tId)}/seg-flow-mode`, { s: sg.s, flow_mode: flowMode });
          toast(`s${pad3(sg.s)} Flow模式已更新`, '', 'ok');
        } catch (e) {
          await saveBatchSegUpdates([{ task_id: tId, s: sg.s, flow_mode: flowMode }]);
        }
      }

      async function onSegConnModeChange(taskId, sg, connMode) {
        const tId = taskId || tid.value;
        sg.mode = connMode;
        if (tId === tid.value && (connMode === 'single' || connMode === 'bridge' || connMode === 'chain')) {
          setSegMode(sg, connMode);
        }
        await saveBatchSegUpdates([{ task_id: tId, s: sg.s, mode: connMode }]);
      }

      async function onSegFieldUpdate(taskId, sg, field, val) {
        const tId = taskId || tid.value;
        sg[field] = val;
        const patch = { task_id: tId, s: sg.s };
        patch[field] = val;
        await saveBatchSegUpdates([patch]);
      }

      // 预设名称变更（二选一：选择或清空预设时，同时清空手动提示词覆盖）
      async function onSegPresetChange(taskId, sg, presetId) {
        const tId = taskId || tid.value;
        const pid = String(presetId || '').trim();
        sg.preset_id = pid;
        sg.custom_prompt = '';
        await saveBatchSegUpdates(
          [{ task_id: tId, s: sg.s, preset_id: pid, custom_prompt: '' }],
          pid ? `s${pad3(sg.s)} 已应用预设「${presetNameOf(pid)}」` : `s${pad3(sg.s)} 已清空预设`
        );
      }

      // 提示词覆盖输入/修改（二选一：只要手动添加/修改了提示词，前面的预设自动清空，并始终保留【人物说的话】动态变量插槽）
      const segPromptTimers = {};
      function onSegCustomPromptInput(taskId, sg, val, immediate = false) {
        const tId = taskId || tid.value;
        const cp = stripSpeechIfPresent(String(val == null ? '' : val), sg);
        sg.custom_prompt = cp;
        sg.prompt_preview = cp;
        sg.preset_id = '';
        const key = `${tId}:${sg.s}`;
        if (segPromptTimers[key]) {
          clearTimeout(segPromptTimers[key]);
          delete segPromptTimers[key];
        }
        const commit = () => {
          saveBatchSegUpdates(
            [{ task_id: tId, s: sg.s, custom_prompt: cp, preset_id: '' }],
            '',
            `修改提示词覆盖 (${tId} s${pad3(sg.s)})`
          );
        };
        if (immediate) {
          commit();
        } else {
          segPromptTimers[key] = setTimeout(commit, 320);
        }
      }

      async function onSegPaste(taskId, segList, startIdx, field, ev) {
        const text = ev.clipboardData && ev.clipboardData.getData('text/plain');
        if (!text) return;
        const isMulti = text.includes('\n') || text.includes('\t');
        if (!isMulti && field !== 'custom_prompt') return;
        if (!isMulti && field === 'custom_prompt') {
          // 单行粘贴到提示词覆盖：同样触发二选一自动清空预设
          setTimeout(() => {
            const sg = segList[startIdx];
            if (sg && ev.target) {
              onSegCustomPromptInput(taskId, sg, ev.target.value, true);
            }
          }, 0);
          return;
        }
        ev.preventDefault();
        const tId = taskId || tid.value;
        const rowsText = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
        while (rowsText.length && rowsText[rowsText.length - 1] === '') rowsText.pop();
        const updates = [];
        for (let i = 0; i < rowsText.length; i++) {
          const targetSeg = segList[startIdx + i];
          if (!targetSeg) break;
          const cols = rowsText[i].split('\t');
          const u = { task_id: tId, s: targetSeg.s };
          if (field === 'text') {
            targetSeg.text = cols[0];
            u.text = cols[0];
            if (cols[1] !== undefined) {
              targetSeg.custom_prompt = cols[1];
              targetSeg.preset_id = '';
              u.custom_prompt = cols[1];
              u.preset_id = '';
            }
          } else if (field === 'custom_prompt') {
            targetSeg.custom_prompt = cols[0];
            targetSeg.preset_id = '';
            u.custom_prompt = cols[0];
            u.preset_id = '';
          } else {
            targetSeg[field] = cols[0];
            u[field] = cols[0];
          }
          updates.push(u);
        }
        if (updates.length) {
          await saveBatchSegUpdates(updates, `已粘贴 ${updates.length} 段数据`);
        }
      }

      // ─── 表格单元格右键上下文菜单：同图统一提示词、全段拷贝预设、定位编辑预设、清空单元格 ───
      const gsCellMenu = reactive({
        show: false,
        x: 0,
        y: 0,
        item: null,
        colIdx: -1,
      });

      function openGsCellMenu(item, colIdx, ev) {
        if (!item) return;
        hotSwitchTask(item.taskId);
        if (!isGsCellInRange(item.flatIdx, colIdx)) {
          gsSel.r0 = item.flatIdx;
          gsSel.c0 = colIdx;
          gsSel.r1 = item.flatIdx;
          gsSel.c1 = colIdx;
          gsSel.activeR = item.flatIdx;
          gsSel.activeC = colIdx;
        }
        gsCellMenu.item = item;
        gsCellMenu.colIdx = colIdx;
        gsCellMenu.x = Math.max(12, Math.min(window.innerWidth - 280, ev.clientX));
        gsCellMenu.y = Math.max(12, Math.min(window.innerHeight - 260, ev.clientY));
        gsCellMenu.show = true;
      }

      function closeGsCellMenu() {
        gsCellMenu.show = false;
      }

      function getTaskSegListForItem(item) {
        if (!item) return segments.value || [];
        if (item.taskObj && Array.isArray(item.taskObj.segments) && item.taskObj.segments.length) {
          return item.taskObj.segments;
        }
        return segments.value || [];
      }

      function getEffectiveSegImgP(taskId, sg) {
        if (!sg) return 'p01';
        if (taskId === tid.value) return fmtP(cellP(sg) || sg.image || 'p01');
        return fmtP(sg.image || 'p01');
      }

      function sameImageSegCount(item) {
        if (!item || !item.seg) return 0;
        const targetP = getEffectiveSegImgP(item.taskId, item.seg);
        const list = getTaskSegListForItem(item);
        return list.filter(sg => getEffectiveSegImgP(item.taskId, sg) === targetP).length;
      }

      // 将选中单元格的提示词（或预设）统一应用给使用“同一张图片”的所有段
      async function applyPromptToSameImageSegs() {
        const rg = gsNormRange();
        const flat = sheetFlatRows.value;
        const sourceItems = [];
        if (rg) {
          for (let r = rg.minR; r <= rg.maxR; r++) {
            if (flat[r]) sourceItems.push(flat[r]);
          }
        }
        if (!sourceItems.length && gsCellMenu.item) {
          sourceItems.push(gsCellMenu.item);
        }
        closeGsCellMenu();
        if (!sourceItems.length) return;

        const updates = [];
        const touchedImages = [];
        const processedTaskImg = new Set();

        for (const src of sourceItems) {
          const tId = src.taskId || tid.value;
          const imgP = getEffectiveSegImgP(tId, src.seg);
          const dedupKey = `${tId}:${imgP}`;
          if (processedTaskImg.has(dedupKey)) continue;

          const hasPreset = !!(src.seg.preset_id && String(src.seg.preset_id).trim());
          const manualPrompt = String(src.seg.custom_prompt || '').trim();
          const displayPrompt = String(getSegPromptDisplay(src.seg) || '').trim();
          if (!hasPreset && !manualPrompt && !displayPrompt) continue;

          processedTaskImg.add(dedupKey);
          touchedImages.push(imgP);

          const allSegsInTask = getTaskSegListForItem(src);
          const allTaskSpeeches = allSegsInTask.map(x => x && x.text).filter(Boolean);
          const cleanTemplate = (window.PresetSlotLogic && typeof window.PresetSlotLogic.stripBakedSpeech === 'function')
            ? window.PresetSlotLogic.stripBakedSpeech(manualPrompt || displayPrompt, allTaskSpeeches)
            : (manualPrompt || displayPrompt);

          for (const sg of allSegsInTask) {
            if (getEffectiveSegImgP(tId, sg) !== imgP) continue;
            if (hasPreset && gsCellMenu.colIdx === 5) {
              sg.preset_id = src.seg.preset_id;
              sg.custom_prompt = '';
              updates.push({ task_id: tId, s: sg.s, preset_id: sg.preset_id, custom_prompt: '' });
            } else if (cleanTemplate) {
              sg.custom_prompt = cleanTemplate;
              sg.prompt_preview = cleanTemplate;
              sg.preset_id = '';
              updates.push({ task_id: tId, s: sg.s, custom_prompt: cleanTemplate, preset_id: '' });
            } else if (hasPreset) {
              sg.preset_id = src.seg.preset_id;
              sg.custom_prompt = '';
              updates.push({ task_id: tId, s: sg.s, preset_id: sg.preset_id, custom_prompt: '' });
            }
          }
        }

        if (!updates.length) {
          toast('当前选中行还没有填写提示词或预设，请先输入提示词或选择预设', '', 'warn');
          return;
        }
        await saveBatchSegUpdates(
          updates,
          `🖼️ 已将提示词统一应用给同图（${touchedImages.join('、')}）的共 ${updates.length} 个分段`
        );
      }

      // 将当前选中单元格的预设（或提示词）快捷拷贝给全部段落
      async function applyCellPresetToAllSegs() {
        const item = gsCellMenu.item || (gsSel.activeR >= 0 ? sheetFlatRows.value[gsSel.activeR] : null);
        closeGsCellMenu();
        if (!item || !item.seg) return;
        const tId = item.taskId || tid.value;
        const pid = String(item.seg.preset_id || '').trim();
        const manualPrompt = String(item.seg.custom_prompt || '').trim();
        const allSegs = getTaskSegListForItem(item);
        if (!allSegs.length) return;

        if (pid) {
          const updates = allSegs.map(sg => {
            sg.preset_id = pid;
            sg.custom_prompt = '';
            return { task_id: tId, s: sg.s, preset_id: pid, custom_prompt: '' };
          });
          await saveBatchSegUpdates(updates, `📌 已将预设「${presetNameOf(pid)}」拷贝应用给全部 ${updates.length} 个段落`);
        } else if (manualPrompt) {
          const updates = allSegs.map(sg => {
            sg.custom_prompt = manualPrompt;
            sg.preset_id = '';
            return { task_id: tId, s: sg.s, custom_prompt: manualPrompt, preset_id: '' };
          });
          await saveBatchSegUpdates(updates, `📌 已将当前提示词覆盖拷贝应用给全部 ${updates.length} 个段落`);
        } else {
          toast('当前单元格尚未选择预设或填写提示词', '', 'warn');
        }
      }

      // 右键“编辑修改选中的预设” -> 直接跳转到 3 提示词 -> 预设管理并定位高亮该预设单元格
      function jumpToEditPreset(itemOverride) {
        const item = itemOverride || gsCellMenu.item || (gsSel.activeR >= 0 ? sheetFlatRows.value[gsSel.activeR] : null);
        closeGsCellMenu();
        const pid = (item && item.seg && item.seg.preset_id) || curTaskPresetId.value || '';
        ui.focusPresetId = pid;
        ui.focusPresetSeq = (Number(ui.focusPresetSeq || 0) + 1);
        ui.node = 'gemini';
      }

      // 清空选中单元格的预设名称 / 提示词覆盖 / 文案
      async function clearSelectedGsCells() {
        closeGsCellMenu();
        const rg = gsNormRange();
        if (!rg) return;
        const flat = sheetFlatRows.value;
        const updates = [];
        for (let r = rg.minR; r <= rg.maxR; r++) {
          const dst = flat[r];
          if (!dst || !dst.seg) continue;
          const u = { task_id: dst.taskId, s: dst.seg.s };
          let touched = false;
          for (let c = rg.minC; c <= rg.maxC; c++) {
            const field = GS_COLS[c];
            if (field === 'preset_id') {
              dst.seg.preset_id = '';
              dst.seg.custom_prompt = '';
              u.preset_id = '';
              u.custom_prompt = '';
              touched = true;
            } else if (field === 'custom_prompt') {
              dst.seg.custom_prompt = '';
              dst.seg.preset_id = '';
              u.custom_prompt = '';
              u.preset_id = '';
              touched = true;
            } else if (field === 'text') {
              dst.seg.text = '';
              u.text = '';
              touched = true;
            }
          }
          if (touched) updates.push(u);
        }
        if (updates.length) {
          await saveBatchSegUpdates(updates, `🧹 已清空 ${updates.length} 行的选中单元格内容`);
        }
      }

      function copySelectedGsCells() {
        closeGsCellMenu();
        const rg = gsNormRange();
        if (!rg) return;
        const flat = sheetFlatRows.value;
        const lines = [];
        for (let r = rg.minR; r <= rg.maxR; r++) {
          const rowItem = flat[r];
          if (!rowItem) continue;
          const cols = [];
          for (let c = rg.minC; c <= rg.maxC; c++) {
            cols.push(String(getCellVal(rowItem, c)).replace(/\r?\n/g, ' '));
          }
          lines.push(cols.join('\t'));
        }
        if (navigator.clipboard && lines.length) {
          navigator.clipboard.writeText(lines.join('\n')).catch(() => {});
          toast(`已复制 ${rg.maxR - rg.minR + 1} 行 × ${rg.maxC - rg.minC + 1} 列单元格`, '', 'ok');
        }
      }

      async function pasteSelectedGsCells() {
        closeGsCellMenu();
        const rg = gsNormRange();
        if (!rg || !navigator.clipboard || !navigator.clipboard.readText) return;
        try {
          const text = await navigator.clipboard.readText();
          if (!text) return;
          const flat = sheetFlatRows.value;
          const rowsText = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
          while (rowsText.length && rowsText[rowsText.length - 1] === '') rowsText.pop();
          const updates = [];
          for (let i = 0; i < rowsText.length; i++) {
            const dst = flat[rg.minR + i];
            if (!dst) break;
            const cols = rowsText[i].split('\t');
            for (let j = 0; j < cols.length; j++) {
              const cIdx = rg.minC + j;
              if (cIdx >= GS_COLS.length) break;
              applyCellValToRow(dst, cIdx, cols[j], updates);
            }
          }
          if (updates.length) {
            await saveBatchSegUpdates(updates, `已批量粘贴 ${updates.length} 个单元格`);
          }
        } catch (e) { /* ignore */ }
      }

      function stopAllMouseUp() {
        stopSegDrag();
        gsSel.selecting = false;
        if (gsSel.fillDragging) commitGsFillDrag();
      }

      function onHistoryApplied() {
        window.refreshPromptPresets({ silent: true }).catch(() => {});
        if (tid.value) loadTrack();
        if (showAllTasks.value) loadAllTasksTable(true).catch(() => {});
      }

      onMounted(() => {
        document.addEventListener('click', onDocClick);
        document.addEventListener('keydown', onMenuKey);
        document.addEventListener('keydown', handleGsCopyPasteKey);
        window.addEventListener('mouseup', stopAllMouseUp);
        window.addEventListener('orante:history-applied', onHistoryApplied);
        window.refreshPromptPresets({ silent: true }).catch(() => {});
        if (!adoptPanelTid(tid, 'track') && !tid.value && readyTasks.value.length) {
          tid.value = readyTasks.value[0].id;
        }
        if (!tid.value && allTasks.value.length) {
          tid.value = allTasks.value[0].id;
        }
        ui.panelTid = tid.value || '';
        ui.selectedTaskId = tid.value || '';
        if (showAllTasks.value) loadAllTasksTable(true).catch(() => {});
        if (window.registerFlush) window.registerFlush('track', flushTrack);
      });
      onUnmounted(() => {
        if (accGest && accGest.dispose) accGest.dispose();
        document.removeEventListener('click', onDocClick);
        document.removeEventListener('keydown', onMenuKey);
        document.removeEventListener('keydown', handleGsCopyPasteKey);
        window.removeEventListener('mouseup', stopAllMouseUp);
        window.removeEventListener('orante:history-applied', onHistoryApplied);
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
        openRescan, goDescribe, batchApplying, unarrangedCount, batchApplyAll,
        thClass, isDead, clickThumb, keepImg, discardImg, clickCell,
        isLastSeg, setSegMode, durOptions, durMenu, durDlg, openDurMenu, closeDurMenu, onDurChange, restoreDur, openBatchDurDlg, applyBatchDur,
        onPinPick, setZone, unpin, clearZone, unselect,
        planNow, reseed, onHoldInput, onUnitChange, toggleSpacing, applyTrack, runJob,
        cancelExpand, toggleLog, goExpandPrompt, canBeRef, toggleRef, itemClass, itemTitle,
        rowOf, whyMark, isNowBroll, txtOpen, toggleTxt, openImgMenu, closeImgMenu, onImgMenu, imgMenuOff, quickImgAct,
        expandAcc, expandPresetId, presetSearch, hoveredImgP, activePoolImg, showAllTasks, showTaskHeaderRows, expandedTaskRows, batchMenuOpen,
        allTasksTable, collapsedTasks, segChecked,
        colWidths, rowHeights, startColResize, startRowResize,
        sheetTaskGroups, sheetFlatRows, gsSel, isGsCellInRange, isGsCellActive, isGsFillHandleCell, isGsCellFillPreview,
        onGsCellMouseDown, onGsCellMouseEnter, startGsFillDrag,
        floatingViewer, currentFloatingRow, toggleFloatingViewer, stepFloatingViewer, startFloatingViewerDrag,
        toggleAllTaskHeaderRows, toggleSingleTaskHeaderRow, isTaskHeaderVisible, areAllTasksCollapsed,
        allPresets, expandPresets, filteredPresets, curTaskFlowMode, curTaskPresetId,
        composePresetPreview, presetNameOf, getSegPromptDisplay, analyzeSegPromptSlot,
        editingPromptKey, startEditSegPrompt, stopEditSegPrompt,
        taskColor, toggleShowAllTasks, expandAllTasks, hotSwitchTask,
        isSegChecked, startSegDrag, enterSegDrag, isTaskAllSegChecked, toggleTaskAllSeg,
        onTaskPresetChange, onTaskFlowModeChange, onSegImageChange, onSegFlowModeChange,
        onSegConnModeChange, onSegFieldUpdate, onSegPresetChange, onSegCustomPromptInput, onSegPaste,
        gsCellMenu, openGsCellMenu, closeGsCellMenu, sameImageSegCount,
        applyPromptToSameImageSegs, applyCellPresetToAllSegs, jumpToEditPreset,
        clearSelectedGsCells, copySelectedGsCells, pasteSelectedGsCells,
      };
    },
    template: `
<section class="panel-track" @click="batchMenuOpen = false">
  <!-- 顶部固定工具栏：主次分明、左侧任务切换、中部任务总预设与Flow模式固定常驻、右侧视图开关与核心操作 -->
  <div class="thd" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
    <h2>{{ t('node.track') }}</h2>
    <task-pick v-model="tid" min-status="prepared" :tasks="allTasks"></task-pick>
    <span class="tag" v-if="selectedTask" :class="selectedTask.engine">{{ selectedTask.engine }}</span>

    <!-- 固定在顶部工具栏的核心功能：任务总预设 & 默认 Flow 模式 -->
    <div style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;background:var(--panel-2);border:1px solid var(--rule-2);border-radius:6px">
      <span style="font-size:12px;font-weight:600;color:var(--accent);white-space:nowrap">📌 任务总预设:</span>
      <input v-model="presetSearch" class="trk-tp-search" style="width:96px;padding:3px 6px;font-size:12px" placeholder="搜预设…">
      <select :value="curTaskPresetId" class="trk-tp-select" style="min-width:165px;padding:3px 6px;font-size:12px"
              @change="onTaskPresetChange(tid, $event.target.value)">
        <option value="">— 选择预设应用到本任务 —</option>
        <option v-for="p in filteredPresets" :key="p.id" :value="p.id">
          {{ p.category ? '[' + p.category + '] ' : '' }}{{ p.name }}
        </option>
      </select>
      <span class="muted" style="font-size:11.5px;margin-left:4px;white-space:nowrap">Flow模式:</span>
      <select :value="curTaskFlowMode" class="trk-tp-select" style="width:120px;padding:3px 6px;font-size:12px"
              @change="onTaskFlowModeChange(tid, $event.target.value)">
        <option value="auto">继承账号</option>
        <option value="frames">🎞️ 首尾帧</option>
        <option value="ingredients">🧩 素材</option>
      </select>
    </div>

    <span style="margin-left:auto;display:inline-flex;align-items:center;gap:6px">
      <button type="button" class="btn sm tog" :class="{on: showAllTasks}" @click="toggleShowAllTasks" title="切换显示全部任务或仅当前任务">
        全部任务
      </button>
      <button type="button" class="btn sm tog" :class="{on: showTaskHeaderRows}"
              @click="toggleAllTaskHeaderRows"
              title="默认隐藏任务分组行（仅保留同组颜色外框），开启后显示任务汇总操作行">
        任务行
      </button>
      <button type="button" class="btn sm tog" :class="{on: floatingViewer.on}"
              @click="toggleFloatingViewer"
              title="开启外部悬浮图片阅读器：点击任意行清晰预览该行图片，支持 ↑/↓/J/K 快捷键切换下一行">
        🖼️ 悬浮看图
      </button>

      <!-- 次要与批量功能收纳下拉菜单 -->
      <div class="gs-toolbar-dropdown" @click.stop>
        <button type="button" class="btn sm" :class="{on: batchMenuOpen}" @click="batchMenuOpen = !batchMenuOpen">
          更多操作 ▾
        </button>
        <div class="gs-toolbar-dropdown-menu" v-if="batchMenuOpen">
          <button type="button" class="dd-item" :disabled="batchApplying || !readyTasks.length"
                  @click="batchMenuOpen=false; batchApplyAll(true)">
            🚀 跳过编排并立即排队生成
          </button>
          <button type="button" class="dd-item" :class="{on: showTaskHeaderRows}" @click="batchMenuOpen=false; toggleAllTaskHeaderRows()">
            {{ showTaskHeaderRows ? '🙈 隐藏全部任务行 (仅留色框)' : '📂 显示全部任务行 (批量操作)' }}
          </button>
          <button type="button" class="dd-item" :class="{on: !areAllTasksCollapsed}" @click="batchMenuOpen=false; expandAllTasks(false)">
            ▾ 展开全部任务组
          </button>
          <button type="button" class="dd-item" :class="{on: areAllTasksCollapsed}" @click="batchMenuOpen=false; expandAllTasks(true)">
            ▸ 收起全部任务组
          </button>
          <button type="button" class="dd-item" @click="batchMenuOpen=false; goDescribe()">
            📝 打开 3 提示词总表 ↗
          </button>
          <button type="button" class="dd-item" :disabled="!tid" @click="batchMenuOpen=false; openRescan()">
            🔄 {{ t('rescan.btn') }}
          </button>
        </div>
      </div>

      <button class="btn sm primary" type="button"
              :disabled="batchApplying || !readyTasks.length"
              title="对列表内全部已准备/已描述任务批量完成配图编排"
              @click="batchApplyAll(false)">
        ⚡ 应用编排 ({{ unarrangedCount || readyTasks.length }})
      </button>
    </span>
  </div>

  <div class="empty" v-if="!tid && !allTasks.length && !allTasksTable.length">{{ t('track.no_task') }}</div>

  <template v-else>
    <!-- 主滚动工作区：扩图栏、编排控制栏、红绿镜头条与表格全部在同一个滚动容器内一起向上滚动，释放完整表格视野 -->
    <div class="bwrap">
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
      <!-- 扩图提示横幅：随页面一起向上滚动，不再固定占据顶部空间 -->
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
        <select v-model="expandPresetId" class="trk-expand-preset-sel" title="从 3 提示词 预设库中选择扩图提示词">
          <option value="">✨ 扩图词：默认内置</option>
          <option v-for="ep in expandPresets" :key="ep.id" :value="ep.id">
            🎨 {{ ep.category ? '[' + ep.category + '] ' : '' }}{{ ep.name }}
          </option>
        </select>
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

      <!-- 编排参数与搜文案栏：随滚动条自然上滚 -->
      <div class="bctl" style="padding:6px 0">
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
        <button class="btn sm" :title="t('track.plan_title')" :disabled="planning" @click="planNow">{{ t('track.plan') }}</button>
        <span class="bstat" style="margin-left:auto">{{ statsText }}</span>
      </div>

      <div class="ts-bar" style="margin-bottom:8px">
        <input :value="ts.qLive" :placeholder="t('search.placeholder')"
               @input="ts.setQ($event.target.value)" @keydown="ts.onBoxKey">
        <span class="ts-count">{{ ts.countText }}</span>
        <button type="button" class="ts-nav" :disabled="!ts.hits.length" @click="ts.prev">‹</button>
        <button type="button" class="ts-nav" :disabled="!ts.hits.length" @click="ts.next">›</button>
        <button type="button" class="ts-x" v-if="ts.qLive" @click="ts.clear">×</button>
      </div>

      <div class="strip-hd">
        <button type="button" class="btn sm tog" :class="{on: stripView==='pool'}"
                @click="setStripView('pool')">{{ t('track.view.pool') }}</button>
        <button type="button" class="btn sm tog" :class="{on: stripView==='shots'}"
                @click="setStripView('shots')">{{ t('track.view.shots') }}</button>
        <button type="button" class="btn sm" :disabled="!previewOn" @click="revertPreview">{{ t('track.revert') }}</button>
        <span class="muted" style="font-size:11.5px;margin-left:8px" v-if="stripView==='pool'">
          💡 点击图片可切换「使用 / 🚫不使用（斜线排除）」· 鼠标右键点击图片可打开扩图/超分/翻转/裁切菜单
        </span>
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
        <button v-for="im in images" :key="im.p" :class="[thClass(im), { 'th-active-target': activePoolImg && fmtP(activePoolImg.p)===fmtP(im.p) }]"
                :style="{borderColor: pool.includes(fmtP(im.p)) ? pcolor(im.p) : ''}"
                :title="im.status==='pending_review' ? t('track.review') : (pool.includes(fmtP(im.p)) ? '点击标记为「不使用」排除此图 · 右键打开功能菜单' : '已标记为「不使用」（已排除）· 点击恢复使用 · 右键打开菜单')"
                @mouseenter="hoveredImgP = fmtP(im.p)"
                @click="hoveredImgP = fmtP(im.p); clickThumb(im)"
                @contextmenu.prevent="hoveredImgP = fmtP(im.p); openImgMenu($event, im)">
          <img :alt="fmtP(im.p)" :src="mediaUrl(tid, im.file, im.mtime)">
          <!-- 标记为「不使用」时的斜线排除遮罩 -->
          <span v-if="!pool.includes(fmtP(im.p)) && !isDead(im)" class="th-excluded-slash">
            <span>🚫 不使用</span>
          </span>
          <span class="refmark" v-if="canBeRef(im)" :class="{on: !!im.expand_ref}"
                :title="t('track.ref_mark')" @click="toggleRef(im, $event)">{{ im.expand_ref ? '✓' : '' }}</span>
          <span class="tag">{{ fmtP(im.p) }}</span>
          <span class="used">{{ t('track.used', { n: String(usedCount(im.p)) }) }}</span>
          <span class="no" v-if="im.status==='blocked' || im.blocked">×</span>
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

      <!-- 红色/彩色配图条：已取消 sticky，随页面一起滚动，不再遮挡下方表格 -->
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

      <div class="selbar" v-if="sel">
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
      </div>

      <!-- 真 Google Sheet 单元格表格：支持拖拽调整任意行高与列宽、矩形框选、Ctrl+C/V 批量复制粘贴、右下角填充柄拖拽拷贝、默认隐藏任务行并保留同组外框颜色 -->
      <table class="gs-sheet-table btable">
        <thead>
          <tr>
            <th :style="{width: colWidths.check + 'px', textAlign: 'center'}">
              <input v-if="!showAllTasks" type="checkbox"
                     :checked="isTaskAllSegChecked(tid, segments)"
                     @change="toggleTaskAllSeg(tid, segments)" title="全选/取消本任务所有段">
              <span v-else>✓</span>
              <span class="gs-col-resizer" @mousedown="startColResize('check', $event)"></span>
            </th>
            <th :style="{width: colWidths.seg + 'px'}">
              分镜
              <span class="gs-col-resizer" @mousedown="startColResize('seg', $event)"></span>
            </th>
            <th :style="{width: colWidths.image + 'px'}">
              配图 (含预览)
              <span class="gs-col-resizer" @mousedown="startColResize('image', $event)"></span>
            </th>
            <th :style="{width: colWidths.dur + 'px'}">
              时长
              <span class="gs-col-resizer" @mousedown="startColResize('dur', $event)"></span>
            </th>
            <th :style="{width: colWidths.flow + 'px'}">
              Flow模式
              <span class="gs-col-resizer" @mousedown="startColResize('flow', $event)"></span>
            </th>
            <th :style="{width: colWidths.conn + 'px'}">
              衔接
              <span class="gs-col-resizer" @mousedown="startColResize('conn', $event)"></span>
            </th>
            <th :style="{width: colWidths.text + 'px'}">
              文案 (支持框选 / Ctrl+C/V / 右下角拖拽)
              <span class="gs-col-resizer" @mousedown="startColResize('text', $event)"></span>
            </th>
            <th :style="{width: colWidths.preset + 'px'}">
              预设名称
              <span class="gs-col-resizer" @mousedown="startColResize('preset', $event)"></span>
            </th>
            <th :style="{width: colWidths.prompt + 'px'}">
              提示词覆盖
              <span class="gs-col-resizer" @mousedown="startColResize('prompt', $event)"></span>
            </th>
          </tr>
        </thead>

        <tbody>
          <template v-for="grp in sheetTaskGroups" :key="'grp-' + grp.taskId">
            <!-- 任务组行：当开启“任务行”、单独展开该任务、或该任务被收起（收起全部任务组）时始终可见 -->
            <tr v-if="isTaskHeaderVisible(grp.taskId)"
                class="trk-task-group-row"
                :class="{'active-task': tid===grp.taskId}"
                :style="{'--task-accent': grp.taskColor}"
                @click="hotSwitchTask(grp.taskId)">
              <td style="text-align:center" @click.stop>
                <input type="checkbox" :checked="isTaskAllSegChecked(grp.taskId, grp.taskObj && grp.taskObj.segments)"
                       @change="toggleTaskAllSeg(grp.taskId, grp.taskObj && grp.taskObj.segments)">
              </td>
              <td colspan="3">
                <div style="display:flex;align-items:center;gap:8px">
                  <button type="button" class="btn sm ghost" style="padding:1px 6px"
                          @click.stop="collapsedTasks[grp.taskId]=!collapsedTasks[grp.taskId]">
                    {{ collapsedTasks[grp.taskId] ? '▸ 展开' : '▾ 收起' }}
                  </button>
                  <span class="trk-task-badge" :style="{background: grp.taskColor + '22', borderColor: grp.taskColor, color: grp.taskColor}">
                    {{ grp.taskId }}
                  </span>
                  <b>{{ grp.taskName }}</b>
                  <span class="muted mono" style="font-size:11.5px">({{ grp.segCount }}段)</span>
                </div>
              </td>
              <td colspan="2" @click.stop>
                <select class="dur-sel" style="width:100%" :value="(grp.taskObj && grp.taskObj.flow_mode) || 'auto'"
                        @change="onTaskFlowModeChange(grp.taskId, $event.target.value)">
                  <option value="auto">任务Flow: 继承</option>
                  <option value="frames">🎞️ 全任务首尾帧</option>
                  <option value="ingredients">🧩 全任务素材</option>
                </select>
              </td>
              <td colspan="3" @click.stop>
                <div style="display:flex;align-items:center;gap:8px">
                  <span class="muted" style="font-size:11.5px">📌 任务预设:</span>
                  <select class="dur-sel" style="min-width:180px" :value="(grp.taskObj && grp.taskObj.preset_id) || ''"
                          @change="onTaskPresetChange(grp.taskId, $event.target.value)">
                    <option value="">— 选择预设应用到 {{ grp.taskId }} 全段 —</option>
                    <option v-for="p in allPresets" :key="p.id" :value="p.id">{{ p.name }}</option>
                  </select>
                  <button v-if="!collapsedTasks[grp.taskId]" type="button" class="btn sm ghost" style="margin-left:auto;padding:1px 6px"
                          @click.stop="toggleSingleTaskHeaderRow(grp.taskId, $event)">隐藏任务行 ▲</button>
                  <button v-else type="button" class="btn sm ghost" style="margin-left:auto;padding:1px 6px"
                          @click.stop="collapsedTasks[grp.taskId]=false">展开段落 ▾</button>
                </div>
              </td>
            </tr>

            <!-- 标准 Google Sheet 分镜单元格行：当该任务未收起时显示，保留同组颜色外框分区 -->
            <template v-if="!collapsedTasks[grp.taskId]">
              <tr v-for="item in grp.rows" :key="item.rowKey"
                  :data-s="item.seg.s"
                  :data-rowkey="item.rowKey"
                  class="gs-group-row"
                  :class="{
                    'gs-group-first': item.isFirstInGroup,
                    'gs-group-last': item.isLastInGroup,
                    'row-selected': isSegChecked(item.taskId, item.seg.s),
                    'seq-hi': tid===item.taskId && seqHi===item.seg.s
                  }"
                  :style="{
                    '--group-border-color': item.taskColor,
                    height: (rowHeights[item.rowKey] || 38) + 'px'
                  }"
                  @click="hotSwitchTask(item.taskId); floatingViewer.rowIdx = item.flatIdx">
                <!-- 勾选列 -->
                <td class="ap-td-sel" style="text-align:center"
                    @mousedown.prevent="startSegDrag(item.taskId, item.seg.s, $event)"
                    @mouseenter="enterSegDrag(item.taskId, item.seg.s)"
                    @contextmenu.prevent="openGsCellMenu(item, 0, $event)">
                  <input type="checkbox" :checked="isSegChecked(item.taskId, item.seg.s)" tabindex="-1" style="pointer-events:none">
                  <span class="gs-row-resizer" @mousedown.stop.prevent="startRowResize(item.rowKey, $event)"></span>
                </td>

                <!-- 分镜序号列（组首行带可点击的任务徽标，用于随时展开/收起该任务的任务行） -->
                <td class="sn" @contextmenu.prevent="openGsCellMenu(item, 0, $event)">
                  <div style="display:flex;align-items:center;gap:3px;flex-wrap:wrap">
                    <span v-if="item.isFirstInGroup"
                          class="gs-task-mini-badge"
                          :style="{background: item.taskColor}"
                          :title="'点击展开/收起 ' + item.taskId + ' 任务批量操作行'"
                          @click.stop="toggleSingleTaskHeaderRow(item.taskId, $event)">
                      {{ item.taskId }}
                    </span>
                    <span>s{{ pad3(item.seg.s) }}</span>
                  </div>
                  <span class="gs-row-resizer" @mousedown.stop.prevent="startRowResize(item.rowKey, $event)"></span>
                </td>

                <!-- Col 0: 配图 + 行内缩略图预览 -->
                <td class="pp"
                    :class="{
                      'gs-cell-in-range': isGsCellInRange(item.flatIdx, 0),
                      'gs-cell-active': isGsCellActive(item.flatIdx, 0),
                      'gs-cell-fill-preview': isGsCellFillPreview(item.flatIdx, 0)
                    }"
                    @mousedown="onGsCellMouseDown(item.flatIdx, 0, $event)"
                    @mouseenter="onGsCellMouseEnter(item.flatIdx, 0)"
                    @contextmenu.prevent="openGsCellMenu(item, 0, $event)">
                  <div style="display:flex;align-items:center;gap:6px">
                    <img v-if="item.thumbUrl"
                         :src="item.thumbUrl"
                         class="gs-row-thumb"
                         :alt="item.pCode"
                         title="点击在悬浮看图器中放大查看"
                         @click.stop="floatingViewer.rowIdx = item.flatIdx; floatingViewer.on = true"
                         onerror="this.style.display='none'">
                    <span class="swatch" :style="{background: pcolor(item.pCode)}"></span>
                    <select v-if="item.taskImages && item.taskImages.length"
                            class="dur-sel gs-img-select" style="flex:1;min-width:0"
                            :value="item.pCode"
                            @change="onSegImageChange(item.taskId, item.seg, $event.target.value)">
                      <option v-for="im in item.taskImages" :key="im.p" :value="fmtP(im.p)">{{ fmtP(im.p) }}</option>
                    </select>
                    <input v-else class="trk-inline-input mono gs-img-select" style="flex:1;min-width:0"
                           :value="item.pCode"
                           @change="onSegImageChange(item.taskId, item.seg, $event.target.value)">
                  </div>
                  <span class="gs-row-resizer" @mousedown.stop="startRowResize(item.rowKey, $event)"></span>
                  <span v-if="isGsFillHandleCell(item.flatIdx, 0)" class="gs-fill-handle"
                        title="向下或向上拖拽批量拷贝填充"
                        @mousedown="startGsFillDrag($event)"></span>
                </td>

                <!-- Col 1: 时长 -->
                <td class="sn dur-cell"
                    :class="{
                      'gs-cell-in-range': isGsCellInRange(item.flatIdx, 1),
                      'gs-cell-active': isGsCellActive(item.flatIdx, 1),
                      'gs-cell-fill-preview': isGsCellFillPreview(item.flatIdx, 1)
                    }"
                    @mousedown="onGsCellMouseDown(item.flatIdx, 1, $event)"
                    @mouseenter="onGsCellMouseEnter(item.flatIdx, 1)"
                    @contextmenu.prevent="openDurMenu($event, item.seg)">
                  <select class="dur-sel" style="width:100%" :value="item.seg.duration || 6"
                          @change="item.taskId===tid ? onDurChange(item.seg, $event) : onSegFieldUpdate(item.taskId, item.seg, 'duration', Number($event.target.value))">
                    <option v-for="d in (item.seg.duration_options || [4,6,8,10,15])" :key="d" :value="d">{{ d }}s</option>
                  </select>
                  <span class="gs-row-resizer" @mousedown.stop="startRowResize(item.rowKey, $event)"></span>
                  <span v-if="isGsFillHandleCell(item.flatIdx, 1)" class="gs-fill-handle"
                        @mousedown="startGsFillDrag($event)"></span>
                </td>

                <!-- Col 2: Flow模式 -->
                <td :class="{
                      'gs-cell-in-range': isGsCellInRange(item.flatIdx, 2),
                      'gs-cell-active': isGsCellActive(item.flatIdx, 2),
                      'gs-cell-fill-preview': isGsCellFillPreview(item.flatIdx, 2)
                    }"
                    @mousedown="onGsCellMouseDown(item.flatIdx, 2, $event)"
                    @mouseenter="onGsCellMouseEnter(item.flatIdx, 2)"
                    @contextmenu.prevent="openGsCellMenu(item, 2, $event)">
                  <select class="dur-sel" style="width:100%" :value="item.seg.flow_mode || 'auto'"
                          @change="onSegFlowModeChange(item.taskId, item.seg, $event.target.value)">
                    <option value="auto">继承账号</option>
                    <option value="frames">🎞️ 首尾帧</option>
                    <option value="ingredients">🧩 素材</option>
                  </select>
                  <span class="gs-row-resizer" @mousedown.stop="startRowResize(item.rowKey, $event)"></span>
                  <span v-if="isGsFillHandleCell(item.flatIdx, 2)" class="gs-fill-handle"
                        @mousedown="startGsFillDrag($event)"></span>
                </td>

                <!-- Col 3: 生成衔接方式 -->
                <td :class="{
                      'gs-cell-in-range': isGsCellInRange(item.flatIdx, 3),
                      'gs-cell-active': isGsCellActive(item.flatIdx, 3),
                      'gs-cell-fill-preview': isGsCellFillPreview(item.flatIdx, 3)
                    }"
                    @mousedown="onGsCellMouseDown(item.flatIdx, 3, $event)"
                    @mouseenter="onGsCellMouseEnter(item.flatIdx, 3)"
                    @contextmenu.prevent="openGsCellMenu(item, 3, $event)">
                  <select class="dur-sel" style="width:100%"
                          :value="item.seg.mode || (item.seg.role==='bridge' ? 'bridge' : 'single')"
                          @change="onSegConnModeChange(item.taskId, item.seg, $event.target.value)">
                    <option value="single">首帧</option>
                    <option value="bridge">首尾帧</option>
                    <option value="chain">接龙</option>
                  </select>
                  <span class="gs-row-resizer" @mousedown.stop="startRowResize(item.rowKey, $event)"></span>
                  <span v-if="isGsFillHandleCell(item.flatIdx, 3)" class="gs-fill-handle"
                        @mousedown="startGsFillDrag($event)"></span>
                </td>

                <!-- Col 4: 文案（默认单行溢出截断，用户手动拉高行高后自动多行显示，编辑不跳行高） -->
                <td :class="{
                      'gs-cell-in-range': isGsCellInRange(item.flatIdx, 4),
                      'gs-cell-active': isGsCellActive(item.flatIdx, 4),
                      'gs-cell-fill-preview': isGsCellFillPreview(item.flatIdx, 4)
                    }"
                    @mousedown="onGsCellMouseDown(item.flatIdx, 4, $event)"
                    @mouseenter="onGsCellMouseEnter(item.flatIdx, 4)"
                    @contextmenu.prevent="openGsCellMenu(item, 4, $event)">
                  <div class="trk-cell-fixed-box"
                       :class="{'is-row-tall': (rowHeights[item.rowKey] || 38) > 46}"
                       :style="{height: Math.max(28, (rowHeights[item.rowKey] || 38) - 6) + 'px'}">
                    <textarea class="trk-inline-input trk-fixed-textarea"
                              rows="1"
                              :value="item.seg.text || ''"
                              placeholder="输入或粘贴本段文案…"
                              @change="onSegFieldUpdate(item.taskId, item.seg, 'text', $event.target.value)"
                              @paste="onSegPaste(item.taskId, (item.taskObj && item.taskObj.segments) || segments, item.sIdx, 'text', $event)"></textarea>
                  </div>
                  <span class="gs-row-resizer" @mousedown.stop="startRowResize(item.rowKey, $event)"></span>
                  <span v-if="isGsFillHandleCell(item.flatIdx, 4)" class="gs-fill-handle"
                        @mousedown="startGsFillDrag($event)"></span>
                </td>

                <!-- Col 5: 预设名称（仅显示预设名字，清晰独立一列，支持 Delete 清空与二选一联动） -->
                <td :class="{
                      'gs-cell-in-range': isGsCellInRange(item.flatIdx, 5),
                      'gs-cell-active': isGsCellActive(item.flatIdx, 5),
                      'gs-cell-fill-preview': isGsCellFillPreview(item.flatIdx, 5)
                    }"
                    @mousedown="onGsCellMouseDown(item.flatIdx, 5, $event)"
                    @mouseenter="onGsCellMouseEnter(item.flatIdx, 5)"
                    @contextmenu.prevent="openGsCellMenu(item, 5, $event)">
                  <select class="dur-sel" style="width:100%" :value="item.seg.preset_id || ''"
                          @change="onSegPresetChange(item.taskId, item.seg, $event.target.value)">
                    <option value="">— 无预设 (清空) —</option>
                    <option v-for="p in allPresets" :key="p.id" :value="p.id">{{ p.name }}</option>
                  </select>
                  <span class="gs-row-resizer" @mousedown.stop="startRowResize(item.rowKey, $event)"></span>
                  <span v-if="isGsFillHandleCell(item.flatIdx, 5)" class="gs-fill-handle"
                        @mousedown="startGsFillDrag($event)"></span>
                </td>

                <!-- Col 6: 提示词覆盖内容（默认溢出截断单行紧凑显示，进入编辑绝不跳行高，始终保留红色【人物说的话】动态变量对应左侧文案） -->
                <td :class="{
                      'gs-cell-in-range': isGsCellInRange(item.flatIdx, 6),
                      'gs-cell-active': isGsCellActive(item.flatIdx, 6),
                      'gs-cell-fill-preview': isGsCellFillPreview(item.flatIdx, 6)
                    }"
                    @mousedown="onGsCellMouseDown(item.flatIdx, 6, $event)"
                    @mouseenter="onGsCellMouseEnter(item.flatIdx, 6)"
                    @dblclick="startEditSegPrompt(item, $event)"
                    @contextmenu.prevent="openGsCellMenu(item, 6, $event)">
                  <div class="trk-cell-fixed-box"
                       :class="{'is-row-tall': (rowHeights[item.rowKey] || 38) > 46}"
                       :style="{height: Math.max(28, (rowHeights[item.rowKey] || 38) - 6) + 'px'}">
                    <textarea v-if="editingPromptKey === item.rowKey"
                              :data-prompt-edit="item.rowKey"
                              class="trk-inline-input trk-fixed-textarea is-editing"
                              rows="1"
                              :value="getSegPromptDisplay(item.seg)"
                              placeholder="手动输入提示词模板（最大换行处自动作为【人物说的话】变量插槽，自动清空预设）…"
                              @input="onSegCustomPromptInput(item.taskId, item.seg, $event.target.value, false)"
                              @change="onSegCustomPromptInput(item.taskId, item.seg, $event.target.value, true)"
                              @blur="stopEditSegPrompt()"
                              @paste="onSegPaste(item.taskId, (item.taskObj && item.taskObj.segments) || segments, item.sIdx, 'custom_prompt', $event)"></textarea>
                    <div v-else
                         class="gs-cell-text trk-prompt-slot-view"
                         :class="(rowHeights[item.rowKey] || 38) > 46 ? 'wrap' : 'clip'"
                         title="单击进入编辑或选中（可右键批量套用给同图的所有段；拖拽行底边可手动拉高行高）"
                         @click="startEditSegPrompt(item, $event)">
                      <template v-if="!getSegPromptDisplay(item.seg)">
                        <span class="muted" style="font-size:11.5px">点击输入提示词模板（换行处为【人物说的话】变量）…</span>
                      </template>
                      <template v-else-if="analyzeSegPromptSlot(item.seg).hasNewline">
                        <span>{{ analyzeSegPromptSlot(item.seg).prefix }}</span>
                        <span class="speech-slot-badge" :title="'动态变量：对应左侧文案「' + (item.seg.text || '人物说的话') + '」，仅在提交 Flow 与收片时合成'">人物说的话</span>
                        <span>{{ analyzeSegPromptSlot(item.seg).suffix }}</span>
                      </template>
                      <template v-else>
                        <span>{{ getSegPromptDisplay(item.seg) }}</span>
                      </template>
                    </div>
                  </div>
                  <span class="gs-row-resizer" @mousedown.stop="startRowResize(item.rowKey, $event)"></span>
                  <span v-if="isGsFillHandleCell(item.flatIdx, 6)" class="gs-fill-handle"
                        @mousedown="startGsFillDrag($event)"></span>
                </td>
              </tr>
            </template>
          </template>
        </tbody>
      </table>
    </div>

    <!-- 单元格鼠标右键菜单：支持同图同步提示词、拷贝预设到全部段落、跳转定位编辑预设、清空/复制/粘贴 -->
    <div v-if="gsCellMenu.show"
         class="gs-context-menu"
         :style="{left: gsCellMenu.x + 'px', top: gsCellMenu.y + 'px'}"
         @click.stop>
      <div class="ctx-item" @click="applyPromptToSameImageSegs">
        🖼️ 将选中单元格提示词应用给同图「{{ gsCellMenu.item ? gsCellMenu.item.pCode : '' }}」的所有段 (共 {{ sameImageSegCount(gsCellMenu.item) }} 段)
      </div>
      <div class="ctx-item" @click="applyCellPresetToAllSegs">
        📌 将当前选中单元格的预设拷贝给所有全部段落
      </div>
      <div class="ctx-item" @click="jumpToEditPreset(gsCellMenu.item)">
        ✏️ 编辑修改选中的预设{{ gsCellMenu.item && gsCellMenu.item.seg && gsCellMenu.item.seg.preset_id ? '「' + presetNameOf(gsCellMenu.item.seg.preset_id) + '」' : '' }}
      </div>
      <div class="ctx-sep"></div>
      <div class="ctx-item" @click="clearSelectedGsCells">
        🧹 清空选中单元格预设 / 提示词 (Delete)
      </div>
      <div class="ctx-item" @click="copySelectedGsCells">
        📋 复制选中单元格 (Ctrl+C)
      </div>
      <div class="ctx-item" @click="pasteSelectedGsCells">
        📥 粘贴到选中单元格 (Ctrl+V)
      </div>
    </div>

    <!-- 外部悬浮图片阅读器窗口 (支持点击任意行实时联动预览，支持 ↑/↓/J/K/Space 切换上/下一行图片) -->
    <div v-if="floatingViewer.on && currentFloatingRow"
         class="gs-floating-img-viewer"
         :style="floatingViewer.x != null ? {left: floatingViewer.x + 'px', top: floatingViewer.y + 'px', right: 'auto', bottom: 'auto'} : {}"
         @click.stop>
      <div class="fv-hd" @mousedown="startFloatingViewerDrag($event)">
        <span>🖼️ 悬浮图片阅读器 · 第 {{ currentFloatingRow.flatIdx + 1 }} / {{ sheetFlatRows.length }} 行 (共 {{ sheetFlatRows.length }} 张图)</span>
        <button type="button" class="btn sm ghost" style="padding:1px 6px" @click="floatingViewer.on = false">×</button>
      </div>
      <div class="fv-body">
        <img v-if="currentFloatingRow.thumbUrl"
             :src="currentFloatingRow.thumbUrl"
             class="fv-img"
             :alt="currentFloatingRow.pCode">
        <div style="width:100%;display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--ink)">
          <span>
            <b :style="{color: currentFloatingRow.taskColor}">{{ currentFloatingRow.taskId }}</b>
            · s{{ pad3(currentFloatingRow.seg.s) }}
            · <span class="mono" style="color:var(--accent)">{{ currentFloatingRow.pCode }}</span>
          </span>
          <span class="muted">{{ currentFloatingRow.seg.duration || 6 }}s</span>
        </div>
        <div class="muted" style="width:100%;font-size:11.5px;max-height:44px;overflow:hidden;text-overflow:ellipsis">
          {{ currentFloatingRow.seg.text || '' }}
        </div>
      </div>
      <div class="fv-ft">
        <button type="button" class="btn sm" :disabled="currentFloatingRow.flatIdx <= 0" @click="stepFloatingViewer(-1)">
          ◀ 上一行 (↑/K)
        </button>
        <span class="muted" style="font-size:11px">快捷键: ↑/↓ 或 J/K 切换</span>
        <button type="button" class="btn sm" :disabled="currentFloatingRow.flatIdx >= sheetFlatRows.length - 1" @click="stepFloatingViewer(1)">
          下一行 (↓/J) ▶
        </button>
      </div>
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
    <li @click="clickThumb(menu.im); closeImgMenu()">
      {{ pool.includes(fmtP(menu.im.p)) ? '🚫 标记为「不使用」（排除此图）' : '✅ 恢复使用此图（加入图池）' }}
    </li>
    <li class="sep"></li>
    <li @click="quickImgAct(menu.im, 'expand', $event); menu.show = false">✨ 设为参考并扩图 ({{ fmtP(menu.im.p) }})</li>
    <li :class="{off: imgMenuOff(menu.im, 'upscale')}" @click="onImgMenu('upscale')">🔍 {{ t('track.ctx.upscale') }}</li>
    <li :class="{off: imgMenuOff(menu.im, 'flip')}" @click="onImgMenu('flip')">↔ {{ t('track.ctx.flip') }}</li>
    <li :class="{off: imgMenuOff(menu.im, 'crop')}" @click="onImgMenu('crop')">✂ {{ t('track.ctx.crop') }}</li>
    <li :class="{off: imgMenuOff(menu.im, 'copy')}" @click="onImgMenu('copy')">📋 {{ t('track.ctx.copy') }}</li>
    <li @click="quickImgAct(menu.im, 'engine', $event); menu.show = false">
      {{ (expandAcc && expandAcc.id==='STUDIO') ? '⚡ 切换生图引擎为：付费 Flow/Grok' : '🖥️ 切换生图引擎为：本地 AI' }}
    </li>
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
