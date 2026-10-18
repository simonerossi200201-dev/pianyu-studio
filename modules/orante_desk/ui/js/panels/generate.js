// 节点 ④ 生成：按任务看段进度、排队、开/停 generate job。不是 Tabulator，每行要嵌进度条。
window.Panel_generate = (() => {
  const MAX_CELLS = 60;
  const ACTIVE_ST = new Set(['arranged', 'generating', 'collected']);
  const GEN_KINDS = new Set(['generate', 'regenerate']);
  const OCCUPY_KINDS = new Set(['generate', 'regenerate', 'refill', 'expand']);
  const SEG_ORDER = ['collected', 'submitted', 'planned', 'failed', 'blocked'];
  const SEG_RANK = { failed: 5, blocked: 4, submitted: 3, waiting: 2.5, planned: 2, collected: 1 };

  function parseSegs(raw) {
    const out = [];
    const seen = new Set();
    String(raw || '').split(/[,，;；\s]+/).forEach(tok => {
      tok = String(tok || '').trim();
      if (!tok) return;
      const m = tok.match(/^(\d+)\s*[-~～到至]\s*(\d+)$/);
      if (m) {
        let a = parseInt(m[1], 10), b = parseInt(m[2], 10);
        if (!Number.isFinite(a) || !Number.isFinite(b)) return;
        if (a > b) { const x = a; a = b; b = x; }
        for (let n = a; n <= b; n++) {
          if (n > 0 && !seen.has(n)) { seen.add(n); out.push(n); }
        }
        return;
      }
      const n = parseInt(tok, 10);
      if (Number.isFinite(n) && n > 0 && !seen.has(n)) { seen.add(n); out.push(n); }
    });
    return out;
  }

  function statusesFromCounts(c) {
    c = c || {};
    const out = [];
    for (const st of SEG_ORDER) {
      const n = Number(c[st] || 0);
      for (let i = 0; i < n; i++) out.push(st);
    }
    const total = Number(c.total || out.length);
    while (out.length < total) out.push('planned');
    return out;
  }

  // 多于 60 段按比例合成一格；格子颜色取桶里最需要盯的状态（失败 > 被拒 > 提交中 > 待做 > 已收）。
  function bucketCells(statuses) {
    const n = (statuses || []).length;
    if (n <= 0) return [];
    if (n <= MAX_CELLS) return statuses.slice();
    const out = [];
    for (let i = 0; i < MAX_CELLS; i++) {
      const a = Math.floor(i * n / MAX_CELLS);
      const b = Math.max(a + 1, Math.floor((i + 1) * n / MAX_CELLS));
      let best = 'planned', rank = -1;
      for (let j = a; j < b && j < n; j++) {
        const st = statuses[j] || 'planned';
        const r = SEG_RANK[st] || 0;
        if (r > rank) { rank = r; best = st; }
      }
      out.push(best);
    }
    return out;
  }

  function placeMenu(x, y, w, h, vw, vh, pad) {
    pad = pad == null ? 8 : pad;
    w = w || 0; h = h || 0;
    let left = x, top = y;
    if (left + w > vw - pad) left = vw - w - pad;
    if (top + h > vh - pad) top = vh - h - pad;
    if (left < pad) left = pad;
    if (top < pad) top = pad;
    return { left, top };
  }

  function pad3(n) {
    return String(n || 0).padStart(3, '0');
  }

  function accOf(t) {
    if (!t) return null;
    return (store.accounts || []).find(a => a.id === t.account_id) || null;
  }

  function accName(t) {
    const a = accOf(t);
    return a ? a.name : (t.account || t.account_id || '');
  }

  function countsOf(t) {
    return (t && t.counts) || { planned: 0, submitted: 0, collected: 0, failed: 0, blocked: 0, total: 0 };
  }

  function remainOf(c) {
    c = c || {};
    return (c.planned || 0) + (c.failed || 0) + (c.submitted || 0);
  }

  // 全部生成：arranged / generating 都起；collected 只在还有 planned/failed（或 submitted 续跑）时起。
  // prepared/described 哪怕配图已齐也不起——状态还没到，让用户去 ③ 编排点「应用」。
  function missingDescN(t) {
    const c = countsOf(t);
    const n = Number(c && c.desc_missing);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  function eligible(t) {
    if (!t) return false;
    if (t.archived) return false;
    if (missingDescN(t)) return false;
    if (t.status === 'arranged' || t.status === 'generating') return true;
    if (t.status === 'collected' && remainOf(countsOf(t)) > 0) return true;
    return false;
  }

  function segsHaveImages(segs) {
    return !!(segs && segs.length && segs.every(s => String((s && s.image) || '').trim()));
  }

  function occupyJobs() {
    return (store.jobs || []).filter(j =>
      OCCUPY_KINDS.has(j.kind) && (j.status === 'running' || j.status === 'queued' || j.status === 'halted'));
  }

  function jobsOf(tid) {
    return (store.jobs || []).filter(j => j.task_id === tid && OCCUPY_KINDS.has(j.kind));
  }

  function liveJob(tid) {
    return jobsOf(tid).find(j => j.status === 'running' || j.status === 'queued' || j.status === 'halted') || null;
  }

  function lastJob(tid) {
    const js = jobsOf(tid);
    return js.length ? js[js.length - 1] : null;
  }

  // 同一账号、正在占号的 job 里，排在这个任务前面的条数（running 算 1 个）。
  function aheadOf(jobs, accountId, jobId) {
    const q = (jobs || []).filter(j =>
      j.account_id === accountId
      && (j.status === 'queued' || j.status === 'running' || j.status === 'halted')
      && OCCUPY_KINDS.has(j.kind));
    const mine = q.find(j => j.id === jobId);
    if (!mine || mine.status !== 'queued') return 0;
    const i = q.findIndex(j => j.id === jobId);
    return i > 0 ? i : 0;
  }

  function queueAhead(task) {
    if (!task) return 0;
    const mine = liveJob(task.id);
    if (!mine) return 0;
    return aheadOf(store.jobs, mine.account_id, mine.id);
  }

  // /api/queue 里 prio=9999 表示自动；显示时折成 0。
  function displayPrio(p) {
    const n = Number(p);
    if (!Number.isFinite(n) || n <= 0 || n >= 9999) return 0;
    return n;
  }

  // 静默拉队形：轮询失败不能走 API.get（会 toast）。
  async function fetchQueueSilent() {
    const res = await fetch('/api/queue');
    const body = await res.json();
    if (!body || body.ok === false) throw new Error((body && body.error) || 'queue');
    return { queue: body.queue || {}, stuck: body.stuck || [] };
  }

  function findInQueue(qmap, tid) {
    if (!qmap || !tid) return null;
    for (const [aid, slot] of Object.entries(qmap)) {
      if (slot && slot.running && slot.running.task_id === tid) {
        return { aid, slot, where: 'running', index: -1 };
      }
      // 规格 87：同账号拼单一起跑的任务也算在跑
      if (slot && slot.pack && Array.isArray(slot.pack.tasks) && slot.pack.tasks.indexOf(tid) >= 0) {
        return { aid, slot, where: 'running', index: -1 };
      }
      const waiting = (slot && slot.waiting) || [];
      const i = waiting.findIndex(w => w && w.task_id === tid);
      if (i >= 0) return { aid, slot, where: 'waiting', index: i };
    }
    return null;
  }

  function restoreStatus(t) {
    const c = countsOf(t);
    const total = c.total || 0;
    if (total && (c.collected || 0) === total) return 'collected';
    if ((c.collected || 0) + (c.submitted || 0) + (c.failed || 0) > 0) return 'generating';
    return 'arranged';
  }

  const waitRefill = {}; // task_id -> refill job_id，完成后自动 generate

  return {
    name: 'Panel_generate',
    setup() {
      const { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } = Vue;

      const view = ref('active');
      const tid = ref('');
      const extraTasks = ref([]);
      const checked = reactive({});
      const expanded = reactive({});
      const manifests = reactive({});
      const menu = reactive({ show: false, x: 0, y: 0, task: null });
      const menuRef = ref(null);
      const redoOn = ref(false);
      const redoText = ref('');
      const blockedDlg = reactive({ on: false, tid: '', rows: [] });
      const chainHint = reactive({}); // tid -> 一行提示
      const bench = reactive({
        on: false, tid: '', items: [], pool: [],
        summary: { red: 0, yellow: 0, green: 0, need: 0 },
        mode: 'first_frame', sort: 'severity', busy: false, jobId: '',
        filter: 'all', cursor: 0,
        density: (store.settings && store.settings['ui.bench.density']) || 'comfy',
        zoom: Number((store.settings && store.settings['ui.bench.zoom']) || 100) || 100,
        densityHi: true, keysOn: false, peek: false, peekSaved: null,
      });
      const benchEl = ref(null);
      const benchPick = reactive({ on: false, s: 0, scores: {} });
      const benchTail = reactive({ on: false, s: 0, frames: [], k: 1, auto: 0, current: 0, loading: false });
      const benchPrompt = reactive({ on: false, s: 0, text: '', orig: '', saving: false });
      const chainCfg = reactive({
        start_image: '', desc_policy: 'never', reuse_dist: 18, upscale_every: 5,
        images: [],
      });
      // 规格 95：接龙 / 接片要靠 ffmpeg 抽尾帧。没有就在 ④ 顶部说清楚 + 一键装
      const ffm = reactive({ checked: false, ok: true, path: '', how: '', busy: false, msg: '' });
      async function checkFfmpeg() {
        try {
          const r = await API.get('/api/env/ffmpeg', { silent: true });
          ffm.ok = !!r.found; ffm.path = r.path || ''; ffm.how = r.how || '';
        } catch (e) { ffm.ok = true; }
        ffm.checked = true;
      }
      async function installFfmpeg() {
        ffm.busy = true; ffm.msg = '';
        try {
          const r = await API.post('/api/env/ffmpeg/install', {});
          ffm.ok = !!r.found; ffm.path = r.path || ''; ffm.how = r.how || '';
          if (r.found) toast(t('gen.ffmpeg.done', { p: r.path || '' }), '', 'ok');
          else ffm.msg = r.error || t('gen.ffmpeg.fail');
        } catch (e) {
          ffm.msg = (e && e.message) || t('gen.ffmpeg.fail');
        } finally { ffm.busy = false; }
      }
      function needFfmpeg() {
        return genMode.value === 'tail_chain' || genMode.value === 'anchor_bridge';
      }
      const redoTid = ref('');
      const redoAcc = ref('');      // 重生时临时换账号，'' = 任务自己的账号
      const posting = ref(false);
      const clearDlg = ref(null);
      const queueMap = ref({});
      const queueOk = ref(false);
      const stuckList = ref([]);
      const nowTick = ref(Date.now());
      const seenRun = reactive({}); // tid -> 首次看到 running 的时间
      const prevJobSt = {};         // tid -> 上一拍 job 状态（收尾兜底拉 counts）
      let pollTimer = null;
      let queueTimer = null;
      let idleTimer = null;

      // 配图已齐但状态还停在 prepared/described：编排其实做过，被补活儿拽回去了。
      function isImagesReady(t) {
        if (!t) return false;
        const m = manifests[t.id];
        const segs = (m && m.segments) || [];
        if (segs.length) return segsHaveImages(segs);
        return !!(t.counts && t.counts.images_ready);
      }

      function isStaleTrack(t) {
        return !!(t && (t.status === 'prepared' || t.status === 'described') && isImagesReady(t));
      }

      const rows = computed(() => {
        if (view.value === 'active') {
          return (store.tasks || []).filter(t => ACTIVE_ST.has(t.status) || isStaleTrack(t));
        }
        const src = extraTasks.value.length ? extraTasks.value : (store.tasks || []);
        if (view.value === 'finished') return src.filter(t => t.status === 'finished');
        if (view.value === 'trash') return src.filter(t => t.status === 'trash');
        return [];
      });

      const selected = computed(() => rows.value.filter(t => checked[t.id]));
      const pickerTasks = computed(() => {
        const map = new Map();
        for (const x of [...(store.tasks || []), ...extraTasks.value]) {
          if (x && x.id && x.status !== 'trash') map.set(x.id, x);
        }
        return [...map.values()];
      });
      const selectedTask = computed(() => pickerTasks.value.find(x => x.id === tid.value) || null);
      const ST_ORDER = ['draft', 'prepared', 'described', 'arranged', 'generating', 'collected', 'finished'];
      function atLeastArranged(st) {
        return ST_ORDER.indexOf(st) >= ST_ORDER.indexOf('arranged');
      }
      const tidReady = computed(() => !!(selectedTask.value && atLeastArranged(selectedTask.value.status)));

      function setCurrent(tsk) {
        const id = (tsk && tsk.id) || '';
        if (!id) return;
        tid.value = id;
        ui.panelTid = id;
      }

      const emptyActive = computed(() => view.value === 'active' && !rows.value.length);
      const canAll = computed(() => (store.tasks || []).some(t => eligible(t) && !isLive(t)));
      // 兜底列出来的 prepared/described 不能生成，勾了也不点亮「生成选中」
      const canStartSel = computed(() => selected.value.some(t => eligible(t) && !isLive(t)));

      const lanes = computed(() => {
        const seen = new Set();
        const out = [];
        const accNameOf = (aid) => {
          const acc = (store.accounts || []).find(a => a.id === aid);
          return acc ? acc.name : aid;
        };
        const stuckOf = (aid, slot) => {
          const fromSlot = slot && slot.stuck && slot.stuck[0];
          if (fromSlot) return fromSlot;
          return (stuckList.value || []).find(s => s && s.account_id === aid) || null;
        };
        const addFromQueue = (aid, slot) => {
          if (!aid || seen.has(aid)) return;
          seen.add(aid);
          const waiting = (slot && slot.waiting) || [];
          const running = slot && slot.running;
          out.push({
            id: aid,
            name: accNameOf(aid),
            running: running ? {
              task_id: running.task_id || '',
              job_id: running.job_id || '',
              prio: displayPrio(running.prio),
            } : null,
            waiting: waiting.map(w => ({
              task_id: w.task_id || '',
              job_id: w.job_id || '',
              prio: displayPrio(w.prio),
            })),
            preview: waiting.slice(0, 3).map(w => ({
              task_id: w.task_id || '',
              job_id: w.job_id || '',
              prio: displayPrio(w.prio),
            })),
            more: Math.max(0, waiting.length - 3),
            stuck: stuckOf(aid, slot),
            pack: (slot && slot.pack) || null,
          });
        };
        if (queueOk.value) {
          const q = queueMap.value || {};
          Object.keys(q).forEach(aid => addFromQueue(aid, q[aid]));
          if (view.value === 'active') {
            rows.value.forEach(tsk => addFromQueue(tsk.account_id, q[tsk.account_id] || { running: null, waiting: [] }));
          }
          (stuckList.value || []).forEach(s => {
            if (s && s.account_id) addFromQueue(s.account_id, q[s.account_id] || { running: null, waiting: [] });
          });
          return out;
        }
        // 接口挂了：退回本窗口 store.jobs
        const addLocal = (aid) => {
          if (!aid || seen.has(aid)) return;
          seen.add(aid);
          const q = occupyJobs().filter(j => j.account_id === aid);
          const running = q.find(j => j.status === 'running' || j.status === 'halted');
          const waiting = q.filter(j => j.status === 'queued');
          const prioOf = (tid) => {
            const hit = (store.tasks || []).find(x => x.id === tid);
            return hit ? (Number(hit.priority) || 0) : 0;
          };
          out.push({
            id: aid,
            name: accNameOf(aid),
            running: running ? { task_id: running.task_id, job_id: running.id, prio: prioOf(running.task_id) } : null,
            waiting: waiting.map(j => ({ task_id: j.task_id, job_id: j.id, prio: prioOf(j.task_id) })),
            preview: waiting.slice(0, 3).map(j => ({ task_id: j.task_id, job_id: j.id, prio: prioOf(j.task_id) })),
            more: Math.max(0, waiting.length - 3),
            stuck: (stuckList.value || []).find(s => s && s.account_id === aid) || null,
          });
        };
        occupyJobs().forEach(j => addLocal(j.account_id));
        if (view.value === 'active') rows.value.forEach(tsk => addLocal(tsk.account_id));
        (stuckList.value || []).forEach(s => { if (s && s.account_id) addLocal(s.account_id); });
        return out;
      });

      // 规格 87：同账号几个任务一起跑时，账号行列出全部在跑的任务和占用的并发位
      function lanePacked(ln) {
        return !!(ln && ln.pack && Array.isArray(ln.pack.tasks) && ln.pack.tasks.length > 1);
      }

      function laneRunTid(ln) {
        if (!ln || !ln.running) return '';
        if (lanePacked(ln)) return ln.pack.tasks.join(' · ');
        return ln.running.task_id || '';
      }

      function laneRunParts(ln) {
        if (!ln || !ln.running) return { pre: '', post: '' };
        const tid = laneRunTid(ln);
        const full = lanePacked(ln)
          ? t('gen.lane.run_pack', {
            name: ln.name,
            tid,
            used: String(ln.pack.used || 0),
            cap: String(ln.pack.cap || 0),
            n: String((ln.waiting || []).length),
          })
          : t('gen.lane.run2', {
            name: ln.name,
            tid,
            n: String((ln.waiting || []).length),
          });
        const i = full.indexOf(tid);
        if (i < 0) return { pre: full, post: '' };
        return { pre: full.slice(0, i), post: full.slice(i + tid.length) };
      }

      function miniFlag(n) {
        return (window.PrioFlagLogic && PrioFlagLogic.miniFlag(n)) || '';
      }

      function modeOf(task) {
        const m = manifests[task.id];
        if (m && (m.mode === 'anchor_bridge' || m.mode === 'first_frame' || m.mode === 'tail_chain')) return m.mode;
        const c = countsOf(task);
        if (c.mode === 'anchor_bridge' || c.mode === 'first_frame' || c.mode === 'tail_chain') return c.mode;
        return (window.GenModeLogic && GenModeLogic.resolveTaskMode(task)) || 'first_frame';
      }

      if (!window.usePickerGestures) console.warn('pickgest 未加载');

      const modeState = computed(() => {
        const L = window.GenModeLogic;
        if (!L) return { mode: '', disabled: true, mixed: false, hintKey: 'gen.mode.pick' };
        return L.selectedModeState(selected.value, modeOf);
      });
      const genMode = computed(() => modeState.value.mode);
      const modeDisabled = computed(() => !!modeState.value.disabled);
      const modeHint = computed(() => {
        const k = modeState.value.hintKey;
        return k ? t(k) : '';
      });

      function modeLabel(task) {
        const m = modeOf(task);
        if (m === 'anchor_bridge') return t('gen.mode.anchor');
        if (m === 'tail_chain') return t('gen.mode.chain');
        return t('gen.mode.first');
      }

      function isWaitingSeg(sg) {
        if (!sg) return false;
        if (sg.wait_why || sg.wait_on) return true;
        return false;
      }

      function cellsOf(task) {
        // 折叠行优先用 counts.cells（含 waiting）；展开用段落真顺序
        void (store.segAt && store.segAt[task.id]);
        if (expanded[task.id]) {
          const m = manifests[task.id];
          if (m && m.segments && m.segments.length) {
            return bucketCells(m.segments.map(s => isWaitingSeg(s) ? 'waiting' : (s.status || 'planned')));
          }
        }
        const c = countsOf(task);
        if (c.cells && c.cells.length) return bucketCells(c.cells);
        return bucketCells(statusesFromCounts(c));
      }

      function chainOf(task) {
        const m = manifests[task.id];
        if (m && m.segments && m.segments.length) {
          return m.segments.map(sg => ({
            s: sg.s,
            role: sg.role === 'bridge' ? 'bridge' : 'anchor',
            wait: isWaitingSeg(sg),
            status: sg.status || 'planned',
            role_manual: !!sg.role_manual,
            image: sg.image || '',
          }));
        }
        const c = countsOf(task);
        const roles = c.roles || [];
        const waits = new Set(c.waits || []);
        const n = (c.total || roles.length || 0);
        const out = [];
        for (let i = 0; i < n; i++) {
          const s = i + 1;
          out.push({
            s,
            role: roles[i] === 'bridge' ? 'bridge' : 'anchor',
            wait: waits.has(s),
            status: (c.cells && c.cells[i]) || 'planned',
          });
        }
        return out;
      }

      function showChain(task) {
        return modeOf(task) === 'anchor_bridge';
      }

      async function ensureChain(task) {
        if (!task || modeOf(task) !== 'anchor_bridge') return;
        if (manifests[task.id] && manifests[task.id].segments) return;
        try { await loadManifest(task.id); } catch (e) { /* */ }
      }

      async function applyMode(mode) {
        const L = window.GenModeLogic;
        if (!L) return { applied: [], skipped: [] };
        const want = (mode === 'anchor_bridge' || mode === 'tail_chain') ? mode : 'first_frame';
        const r = await L.runApplyMode({
          mode: want,
          selected: selected.value.slice(),
          isLive,
          post: (url, body) => API.post(url, body),
          confirmFn: (msg) => window.confirm(msg),
          toastFn: (msg) => toast(msg, '', ''),
          alreadyMode: modeOf,
        });
        for (const id of (r.applied || [])) {
          const tsk = (store.tasks || []).find(x => x.id === id) || selected.value.find(x => x.id === id);
          if (tsk && manifests[tsk.id]) manifests[tsk.id].mode = want;
          if (tsk) {
            tsk.gen_mode = want;
            if (tsk.counts) tsk.counts.mode = want;
          }
          if ((want === 'anchor_bridge' || want === 'tail_chain') && tsk) {
            await loadManifest(tsk.id).catch(() => {});
            if (want === 'tail_chain') await loadChain(tsk.id).catch(() => {});
          }
        }
        return r;
      }

      async function loadChain(tid) {
        if (!tid) return;
        try {
          const r = await API.get('/api/tasks/' + encodeURIComponent(tid) + '/chain');
          const c = r.chain || {};
          chainCfg.start_image = c.start_image || '';
          chainCfg.desc_policy = c.desc_policy || 'never';
          chainCfg.reuse_dist = c.reuse_dist != null ? c.reuse_dist : 18;
          chainCfg.upscale_every = c.upscale_every != null ? c.upscale_every : 5;
          chainCfg.images = r.images || [];
          if (!chainCfg.start_image && chainCfg.images.length) {
            const hit = chainCfg.images.find(x => x.expand_ref) || chainCfg.images[0];
            chainCfg.start_image = hit.p;
          }
        } catch (e) { /* */ }
      }

      async function saveChain(patch) {
        const tsk = selected.value[0];
        if (!tsk) return;
        try {
          const r = await API.post('/api/tasks/' + encodeURIComponent(tsk.id) + '/chain', patch || {
            start_image: chainCfg.start_image,
            desc_policy: chainCfg.desc_policy,
            upscale_every: Number(chainCfg.upscale_every) || 0,
          });
          const c = r.chain || {};
          chainCfg.start_image = c.start_image || chainCfg.start_image;
          chainCfg.desc_policy = c.desc_policy || chainCfg.desc_policy;
          chainCfg.upscale_every = c.upscale_every != null ? c.upscale_every : chainCfg.upscale_every;
        } catch (e) { /* */ }
      }

      function chainCascade(it) {
        if (!it || !bench.tid) return;
        return benchAct(async () => {
          const r = await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/chain/cascade', { s: it.s });
          toast(t('gen.bench.cascade_ok', { n: String((r.reset || []).length) }), '', 'ok');
          await loadBench(bench.tid);
        });
      }

      function mediaUrl(tid, file, mtime) {
        if (!file) return '';
        const rel = String(file).replace(/\\/g, '/').replace(/^\/+/, '');
        return '/media/' + encodeURIComponent(tid) + '/' + rel + (mtime ? ('?v=' + mtime) : '');
      }

      function benchNeed() {
        let n = 0;
        rows.value.forEach(tsk => {
          if (modeOf(tsk) !== 'anchor_bridge') return;
          const c = countsOf(tsk);
          n += Number(c.bench_red || 0) + Number(c.bench_yellow || 0);
        });
        return n;
      }

      function benchTaskOf() {
        if (bench.tid) {
          const hit = rows.value.find(x => x.id === bench.tid);
          if (hit) return hit;
        }
        const sel = selected.value.find(x => modeOf(x) === 'anchor_bridge' || modeOf(x) === 'tail_chain');
        if (sel) return sel;
        return rows.value.find(x => modeOf(x) === 'anchor_bridge' || modeOf(x) === 'tail_chain')
          || rows.value.find(x => (countsOf(x).bench_red || 0) + (countsOf(x).bench_yellow || 0) > 0)
          || rows.value[0]
          || null;
      }

      function benchSorted() {
        let items = (bench.items || []).slice();
        items.sort((a, b) => {
          const da = a.first_dist == null ? -1 : a.first_dist;
          const db = b.first_dist == null ? -1 : b.first_dist;
          if (db !== da) return db - da;
          const ga = a.gap == null ? -1 : a.gap;
          const gb = b.gap == null ? -1 : b.gap;
          if (gb !== ga) return gb - ga;
          return a.s - b.s;
        });
        if (bench.filter === 'red') items = items.filter(x => x.level === 'red');
        else if (bench.filter === 'yellow') items = items.filter(x => x.level === 'yellow');
        return items;
      }

      function benchPos() {
        const list = benchSorted();
        const n = list.length;
        if (!n) return { n: 0, m: 0 };
        let i = list.findIndex(x => x.s === bench.cursor);
        if (i < 0) i = 0;
        return { n: i + 1, m: n };
      }

      function benchCurrent() {
        const list = benchSorted();
        if (!list.length) return null;
        let i = list.findIndex(x => x.s === bench.cursor);
        if (i < 0) i = 0;
        return list[i];
      }

      function benchThumbPx() {
        const base = bench.density === 'compact' ? 52 : (bench.density === 'large' ? 280 : 96);
        const z = Math.max(40, Math.min(200, Number(bench.zoom) || 100));
        return Math.round(base * z / 100);
      }

      function benchStyle() {
        return { '--bench-thumb': benchThumbPx() + 'px' };
      }

      function persistBenchView() {
        const dens = (bench.density === 'compact' || bench.density === 'large') ? bench.density : 'comfy';
        const zoom = Math.max(40, Math.min(200, Number(bench.zoom) || 100));
        API.post('/api/settings', { set: { 'ui.bench.density': dens, 'ui.bench.zoom': zoom } })
          .then(r => { if (r && r.settings) Object.assign(store.settings, r.settings); })
          .catch(() => {});
      }

      function setBenchDensity(d) {
        if (d !== 'compact' && d !== 'comfy' && d !== 'large') return;
        bench.density = d;
        bench.densityHi = true;
        bench.zoom = 100;
        bench.peek = false;
        persistBenchView();
      }

      function setBenchZoom(z, fromSlider) {
        let n = Math.round(Number(z) || 100);
        n = Math.max(40, Math.min(200, n));
        bench.zoom = n;
        if (fromSlider) bench.densityHi = false;
        persistBenchView();
      }

      function bumpBenchZoom(delta) {
        setBenchZoom((Number(bench.zoom) || 100) + delta, true);
      }

      function setBenchFilter(f) {
        bench.filter = f === 'red' || f === 'yellow' ? f : 'all';
        const list = benchSorted();
        if (list.length && !list.some(x => x.s === bench.cursor)) bench.cursor = list[0].s;
        scrollBenchCur();
      }

      function moveBench(d) {
        const list = benchSorted();
        if (!list.length) return;
        let i = list.findIndex(x => x.s === bench.cursor);
        if (i < 0) i = 0;
        i = Math.max(0, Math.min(list.length - 1, i + d));
        bench.cursor = list[i].s;
        scrollBenchCur();
      }

      function scrollBenchCur() {
        const root = benchEl.value;
        if (!root) return;
        const el = root.querySelector('.bc.is-cur');
        if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
      }

      function toggleBenchPeek() {
        if (bench.peek) {
          if (bench.peekSaved) {
            bench.density = bench.peekSaved.density;
            bench.zoom = bench.peekSaved.zoom;
            bench.densityHi = bench.peekSaved.densityHi;
          }
          bench.peek = false;
          bench.peekSaved = null;
          return;
        }
        bench.peekSaved = { density: bench.density, zoom: bench.zoom, densityHi: bench.densityHi };
        bench.density = 'large';
        bench.zoom = 100;
        bench.densityHi = true;
        bench.peek = true;
      }

      function gapWord(g) {
        if (g == null) return '—';
        if (g >= 4) return t('gen.bench.gap_big');
        if (g === 3) return t('gen.bench.gap_mid');
        return t('gen.bench.gap_ok');
      }

      function applyBench(r) {
        if (!r) return;
        if (r.items) bench.items = r.items;
        if (r.pool) bench.pool = r.pool;
        if (r.summary) bench.summary = r.summary;
        if (r.mode) bench.mode = r.mode;
        if (r.tid) bench.tid = r.tid;
        if (r.density) bench.density = r.density;
        if (r.zoom != null) {
          bench.zoom = Number(r.zoom) || bench.zoom;
          bench.densityHi = Number(bench.zoom) === 100;
        }
        if (r.item) {
          const i = bench.items.findIndex(x => x.s === r.item.s);
          if (i >= 0) bench.items.splice(i, 1, r.item);
          else bench.items.push(r.item);
        }
        if (r.job_id) bench.jobId = r.job_id;
        const list = benchSorted();
        if (list.length && !list.some(x => x.s === bench.cursor)) bench.cursor = list[0].s;
      }

      async function loadBench(tid) {
        const id = tid || (benchTaskOf() && benchTaskOf().id);
        if (!id) return;
        bench.tid = id;
        const r = await API.get('/api/tasks/' + encodeURIComponent(id) + '/bench');
        applyBench(r);
      }

      async function openBench() {
        return openBenchFor();
      }

      async function openBenchFor(tid, s) {
        if (tid && typeof tid === 'object') { tid = ''; s = 0; }
        if (tid) bench.tid = tid;
        const tsk = tid ? { id: tid } : benchTaskOf();
        if (!tsk) { toast(t('gen.bench.need_task')); return; }
        bench.on = true;
        bench.keysOn = false;
        bench.peek = false;
        try { await loadBench(tsk.id); } catch (e) { /* toast */ }
        if (s) bench.cursor = s;
        await nextTick();
        if (benchEl.value && benchEl.value.focus) benchEl.value.focus();
        if (s) {
          scrollBenchCur();
          const it = (bench.items || []).find(x => x.s === s);
          if (it) openTail(it);
        }
      }

      function closeBench() {
        bench.on = false;
        benchPick.on = false;
        benchTail.on = false;
        benchPrompt.on = false;
        bench.keysOn = false;
        bench.peek = false;
      }

      async function benchAct(fn) {
        if (bench.busy) return;
        bench.busy = true;
        try { await fn(); } catch (e) { /* toast */ }
        finally { bench.busy = false; }
      }

      function toastSwap(s, swap) {
        if (!swap) return;
        if (swap.changed) {
          toast(t('gen.bench.swapped', {
            s: 's' + pad3(s), a: swap.from || '—', b: swap.to || '—',
            x: String(swap.gap_from == null ? '—' : swap.gap_from),
            y: String(swap.gap_to == null ? '—' : swap.gap_to),
          }), '', 'ok');
        } else {
          toast(t('gen.bench.swapped_same', { s: 's' + pad3(s), p: swap.to || swap.from || '—' }));
        }
      }

      function benchSwap(it) {
        return benchAct(async () => {
          const r = await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/swap', { s: it.s });
          applyBench(r);
          toastSwap(it.s, r.swap);
          loadManifest(bench.tid).catch(() => {});
        });
      }

      function benchSwapRed() {
        return benchAct(async () => {
          const r = await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/swap-red', {});
          applyBench(r);
          toast(r.summary_text || t('gen.bench.swap_sum', {
            n: String(r.swapped != null ? r.swapped : (r.done || []).length),
            m: String(r.stuck != null ? r.stuck : 0),
          }), '', 'ok');
          loadManifest(bench.tid).catch(() => {});
        });
      }

      function benchPassAll() {
        const nGap = (bench.items || []).filter(x => x.gap != null && x.gap >= 4 && !x.passed).length;
        if (!confirm(t('gen.bench.pass_all_confirm', { n: String(nGap) }))) return;
        return benchAct(async () => {
          const r = await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/pass-all', {});
          applyBench(r);
          toast(t('gen.bench.passed_n', { n: String(r.passed || 0) }), '', 'ok');
          loadManifest(bench.tid).catch(() => {});
        });
      }

      function setSegMode(it, mode) {
        if (!it || !bench.tid) return;
        if (bench.mode !== 'anchor_bridge' && bench.mode !== 'tail_chain') {
          toast(t('gen.bench.seg.off')); return;
        }
        return benchAct(async () => {
          const r = await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/seg-mode', {
            s: it.s, mode,
          });
          await loadBench(bench.tid);
          toast(r.hint || t('gen.bench.seg.' + (mode === 'single' ? 'single' : 'bridge')), '', 'ok');
          loadManifest(bench.tid).catch(() => {});
        });
      }

      function openPick(it) {
        benchPick.on = true;
        benchPick.s = it.s;
      }

      function closePick() { benchPick.on = false; benchPick.s = 0; }

      function pickItem() {
        return bench.items.find(x => x.s === benchPick.s) || null;
      }

      async function hoverScore(p) {
        const s = benchPick.s;
        if (!s || !p) return;
        const key = s + ':' + p;
        if (benchPick.scores[key] && benchPick.scores[key].gap != null) return;
        benchPick.scores[key] = { loading: true };
        try {
          const r = await API.get(
            '/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/score?s=' + s
            + '&p=' + encodeURIComponent(p));
          benchPick.scores[key] = { gap: r.gap, why: r.why, loading: false };
        } catch (e) {
          benchPick.scores[key] = { gap: null, why: '', loading: false };
        }
      }

      function scoreOf(p) {
        return benchPick.scores[benchPick.s + ':' + p] || null;
      }

      function scoreClass(g) {
        if (g == null) return '';
        if (g >= 4) return 'red';
        if (g === 3) return 'yellow';
        return 'green';
      }

      function pickAnchor(p) {
        return benchAct(async () => {
          const s = benchPick.s;
          const r = await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/pick', { s, p });
          applyBench(r);
          closePick();
          toast(t('gen.bench.picked', { s: 's' + pad3(s), p: r.to || p }), '', 'ok');
          loadManifest(bench.tid).catch(() => {});
        });
      }

      function benchPass(it) {
        if (!it.can_pass) { toast(t('gen.bench.pass_blocked')); return; }
        return benchAct(async () => {
          const r = await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/pass', { s: it.s });
          applyBench(r);
          toast(t('gen.bench.passed', { s: 's' + pad3(it.s) }), '', 'ok');
          loadManifest(bench.tid).catch(() => {});
        });
      }

      function benchRetry(it) {
        return benchAct(async () => {
          const r = await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/retry', { s: it.s });
          applyBench(r);
          toast(t('gen.bench.retried', { s: 's' + pad3(it.s) }), '', 'ok');
          loadManifest(bench.tid).catch(() => {});
        });
      }

      function benchPassYellow() {
        return benchAct(async () => {
          const r = await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/pass-yellow', {});
          applyBench(r);
          toast(t('gen.bench.passed_n', { n: String(r.passed || 0) }), '', 'ok');
          loadManifest(bench.tid).catch(() => {});
        });
      }

      function toggleBenchSort() {
        bench.sort = bench.sort === 'gap' ? 'severity' : 'gap';
      }

      // 规格 98：acc = 分体按钮 ▾ 里选的账号（'' = 原账号，'STUDIO' = 本地模型）
      function regenArgs(segs, acc) {
        const a = { segs };
        if (acc) { a.account_id = acc; window.RegenAcc.regenToast(acc, segs); }
        return a;
      }
      function benchTask() { return (store.tasks || []).find(x => x.id === bench.tid) || null; }
      function segsOf(tid, nums) {
        const m = manifests[tid];
        const want = new Set((nums || []).filter(Boolean));
        return ((m && m.segments) || []).filter(sg => want.has(sg.s));
      }

      async function chainRegen(it, acc) {
        if (!it) return;
        await benchAct(async () => {
          const r = await API.job('regenerate', bench.tid, regenArgs([it.s], acc));
          bench.jobId = (r.job && r.job.id) || '';
        });
      }

      async function benchRegen(it, acc) {
        if (!it.from_seg) return;
        await benchAct(async () => {
          await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/acted', { s: it.s, why: 'regen' });
          const r = await API.job('regenerate', bench.tid, regenArgs([it.from_seg], acc));
          bench.jobId = (r.job && r.job.id) || '';
        });
      }

      async function openTail(it) {
        benchTail.on = true;
        benchTail.s = it.s;
        benchTail.loading = true;
        benchTail.frames = [];
        try {
          const r = await API.get('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/tails?s=' + it.s);
          benchTail.frames = r.frames || [];
          benchTail.auto = r.auto_k || 0;
          benchTail.current = r.current_k || r.auto_k || 1;
          benchTail.k = benchTail.current || 1;
        } catch (e) {
          benchTail.on = false;
        } finally {
          benchTail.loading = false;
        }
      }

      function closeTail() { benchTail.on = false; }

      function tailFrame() {
        return (benchTail.frames || []).find(f => f.k === Number(benchTail.k)) || null;
      }

      function saveTail() {
        return benchAct(async () => {
          const r = await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/tail', {
            s: benchTail.s, k: Number(benchTail.k),
          });
          applyBench(r);
          closeTail();
          toast(t('gen.bench.tail_ok', { k: String(r.k) }), '', 'ok');
          loadManifest(bench.tid).catch(() => {});
        });
      }

      function openPrompt(it) {
        benchPrompt.on = true;
        benchPrompt.s = it.s;
        benchPrompt.text = it.prompt_final || '';
        benchPrompt.orig = it.prompt_final || '';
      }

      function closePrompt() {
        if (benchPrompt.text !== benchPrompt.orig && !confirm(t('review.prompt_unsaved'))) return;
        benchPrompt.on = false;
      }

      async function savePrompt() {
        if (benchPrompt.saving) return;
        benchPrompt.saving = true;
        try {
          const r = await API.post(
            '/api/tasks/' + encodeURIComponent(bench.tid) + '/review/prompt',
            { s: benchPrompt.s, text: benchPrompt.text });
          await API.post('/api/tasks/' + encodeURIComponent(bench.tid) + '/bench/acted', { s: benchPrompt.s, why: 'prompt' });
          toast(t('review.prompt_saved', { n: String(r.chars || benchPrompt.text.length) }), '', 'ok');
          benchPrompt.on = false;
          await loadBench(bench.tid);
        } catch (e) { /* toast */ }
        finally { benchPrompt.saving = false; }
      }

      function chainTitle(task, nd, last) {
        if (nd.wait) return t('gen.wait_on', { n: String(nd.s) });
        if (nd.role_manual) {
          return nd.role === 'bridge' ? t('gen.bench.manual_bridge') : t('gen.bench.manual');
        }
        if (last) return t('gen.chain.last');
        return nd.role === 'bridge' ? t('gen.chain.bridge') : t('gen.chain.single');
      }

      async function toggleRole(task, node) {
        if (!task || !node || isLive(task)) return;
        try {
          await API.post('/api/tasks/' + encodeURIComponent(task.id) + '/use-current-track', { start: true });
          chainHint[task.id] = r.hint || '';
          await loadManifest(task.id).catch(() => {});
        } catch (e) { /* toast */ }
      }

      function cellTitle(task, i, nCells) {
        const m = manifests[task.id];
        const total = (m && m.segments && m.segments.length)
          || (countsOf(task).total) || nCells;
        if (total <= MAX_CELLS) return 's' + pad3(i + 1);
        const a = Math.floor(i * total / MAX_CELLS) + 1;
        const b = Math.floor((i + 1) * total / MAX_CELLS);
        return 's' + pad3(a) + '–s' + pad3(b);
      }

      function segsOf(task) {
        const m = manifests[task.id];
        return (m && m.segments) || [];
      }

      function logLines(task) {
        const j = liveJob(task.id) || lastJob(task.id);
        if (!j) return [];
        const lines = store.logs[j.id] || [];
        return lines.slice(-40);
      }

      function logJobId(task) {
        const j = liveJob(task.id) || lastJob(task.id);
        return j ? j.id : '';
      }

      function isLive(task) {
        return isRunning(task) || isQueued(task);
      }

      function isRunning(task) {
        if (!task) return false;
        if (queueOk.value) {
          const hit = findInQueue(queueMap.value, task.id);
          if (hit && hit.where === 'running') return true;
        }
        const j = liveJob(task.id);
        return !!(j && (j.status === 'running' || j.status === 'halted'));
      }

      function isQueued(task) {
        if (!task) return false;
        if (queueOk.value) {
          const hit = findInQueue(queueMap.value, task.id);
          if (hit && hit.where === 'waiting') return true;
        }
        const j = liveJob(task.id);
        return !!(j && j.status === 'queued');
      }

      function canStop(task) {
        return jobsOf(task.id).some(j => j.status === 'running' || j.status === 'queued' || j.status === 'halted');
      }

      function flagState(task) {
        if (isRunning(task)) return 'running';
        if (isQueued(task)) return 'queued';
        return '';
      }

      function queueInfo(task) {
        if (!task) return { n: 0, head: '' };
        if (queueOk.value) {
          const hit = findInQueue(queueMap.value, task.id);
          if (hit && hit.where === 'waiting') {
            const head = (hit.slot.running && hit.slot.running.task_id) || '';
            return { n: hit.index, head };
          }
        }
        const mine = liveJob(task.id);
        const n = queueAhead(task);
        let head = '';
        if (mine) {
          const run = occupyJobs().find(j => j.account_id === mine.account_id && (j.status === 'running' || j.status === 'halted'));
          head = run ? run.task_id : '';
        }
        return { n, head };
      }

      function queuedText(task) {
        const info = queueInfo(task);
        if (queueOk.value) {
          const hit = findInQueue(queueMap.value, task.id);
          const run = hit && hit.slot && hit.slot.running;
          const op = run && Number(run.owner_port || 0);
          const mine = Number(store.port || 0);
          if (op && mine && op !== mine) {
            const pos = (hit.index >= 0 ? hit.index + 2 : 2);
            return t('gen.queued_held', { port: String(op), n: String(pos) });
          }
        }
        // 规格 87：账号上是一组小任务在一起跑 → 说"等空位"，别说"前面还有 N 个（某某在跑）"
        if (queueOk.value && task && task.account_id) {
          const slot = (queueMap.value || {})[task.account_id];
          const pk = slot && slot.pack;
          if (pk && pk.packable && Array.isArray(pk.tasks) && pk.tasks.length) {
            // 前面只数"还在排队"的，一起在跑的那几个不算
            const mine = liveJob(task.id);
            const qd = (store.jobs || []).filter(j => j.account_id === task.account_id
              && j.status === 'queued' && OCCUPY_KINDS.has(j.kind));
            const idx = mine ? qd.findIndex(j => j.id === mine.id) : -1;
            const n = idx > 0 ? idx : 0;
            const vars = { used: String(pk.used || 0), cap: String(pk.cap || 0), n: String(n) };
            return n ? t('gen.queued_pack', vars) : t('gen.queued_pack_next', vars);
          }
        }
        const head = info.head || '—';
        return t('gen.queued2', { n: String(info.n), head });
      }

      async function pullQueue() {
        try {
          const r = await fetchQueueSilent();
          queueMap.value = r.queue;
          stuckList.value = r.stuck || [];
          queueOk.value = true;
        } catch (e) {
          queueOk.value = false;
        }
      }

      async function setPrio(task, body) {
        if (!task || !task.id) return;
        try {
          await PrioFlagLogic.postPriority(task.id, body);
          pullQueue();
        } catch (e) { /* toast */ }
      }

      function canStart(task) {
        if (!task || isLive(task)) return false;
        if (task.archived || task.missing_dir) return false;
        if (isStaleTrack(task)) return false; // 状态没到 arranged，不给「生成」
        if (missingDescN(task)) return false; // 缺描述语：按钮灰，去多模态补
        if (task.status === 'arranged' || task.status === 'generating') return true;
        if (task.status === 'collected' && remainOf(countsOf(task)) > 0) return true;
        return false;
      }

      function isArchived(t) { return !!(t && t.archived); }
      function isGone(t) { return !!(t && t.missing_dir); }
      function clipChanged(task, sg) {
        const xs = (task && task.changed_files) || [];
        return xs.some(x => Number(x.s) === Number(sg && sg.s));
      }
      function parentDir(p) {
        const s = String(p || '').replace(/[/\\]+$/, '');
        const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'));
        return i >= 0 ? s.slice(0, i) : s;
      }
      function toastExport(r) {
        const files = (r && r.files) || [];
        if (!files.length) { toast(t('gen.archive.none'), '', ''); return; }
        const n = files.length;
        const m = files.reduce((s, f) => s + (f.tasks || 0), 0);
        const dirs = [];
        files.forEach(f => {
          const d = parentDir(f.path);
          if (d && !dirs.includes(d)) dirs.push(d);
        });
        const dirStr = dirs.length <= 1 ? (dirs[0] || '') : (dirs[0] + ' 等');
        toast(t('gen.archive.exported', { n: String(n), m: String(m), dirs: dirStr }), '', 'ok');
        (r.warnings || []).forEach(w => toast(w, '', 'bad'));
      }
      async function exportIds(ids, allFinished) {
        const body = allFinished ? { all_finished: true } : { task_ids: ids };
        const r = await API.post('/api/project/export', body);
        toastExport(r);
        return r;
      }
      function exportSelected() {
        const ids = selected.value.map(x => x.id);
        if (!ids.length) { toast(t('gen.archive.none'), '', ''); return; }
        return exportIds(ids, false);
      }
      function exportAll() { return exportIds([], true); }
      async function doClear(ids, force) {
        if (!ids.length) { toast(t('gen.archive.clear_none'), '', ''); return; }
        const res = await fetch('/api/project/clear', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_ids: ids, force: !!force }),
        });
        const body = await res.json();
        if (body && body.ok === false && (body.need_export || []).length) {
          clearDlg.value = { ids, need: body.need_export };
          return;
        }
        if (body && body.ok === false) {
          toast(body.error || '', body.hint || '', 'bad');
          throw new Error(body.error || '');
        }
        toast(t('gen.archive.cleared', { n: String(body.removed || ids.length) }), '', 'ok');
        ids.forEach(id => { checked[id] = false; expanded[id] = false; });
        clearDlg.value = null;
        await loadExtra();
        try { await refreshTasks(); } catch (e) { /* 列表已 loadExtra */ }
      }
      function clearSelected() { return doClear(selected.value.map(x => x.id), false); }
      async function clearAll() {
        await loadExtra();
        const ids = extraTasks.value
          .filter(x => x.status === 'finished' || x.archived)
          .map(x => x.id);
        return doClear(ids, false);
      }
      function exportThenClear() {
        const dlg = clearDlg.value;
        if (!dlg) return;
        return doClear(dlg.ids, true);
      }
      async function importProj() {
        const r = await API.post('/api/project/pick-open');
        const paths = r.paths || [];
        if (!paths.length) return;
        const res = await API.post('/api/project/import', { paths });
        toast(t('gen.archive.imported', { n: String(res.imported || 0) }), '', 'ok');
        if ((res.skipped || []).length) {
          toast(t('gen.archive.skipped', { n: String(res.skipped.length) }),
            res.skipped.map(x => x.id).join('、'), '');
        }
        if ((res.missing_dir || []).length) {
          toast(t('gen.archive.missing_n', { n: String(res.missing_dir.length) }),
            res.missing_dir.join('、'), '');
        }
        if ((res.changed_files || []).length) {
          toast(t('gen.archive.changed_n', { n: String(res.changed_files.length) }), '', '');
        }
        view.value = 'finished';
        await loadExtra();
      }
      async function unfreezeTask(task) {
        if (!task || !task.archived) return;
        if (!confirm(t('gen.archive.unfreeze_confirm'))) return;
        await API.post('/api/project/unfreeze', { task_ids: [task.id] });
        toast(t('gen.archive.unfrozen', { n: '1' }), '', 'ok');
        checked[task.id] = false;
        await loadExtra();
        try { await refreshTasks(); } catch (e) { /* */ }
      }

      async function loadExtra() {
        try {
          const r = await API.get('/api/tasks?all=1');
          extraTasks.value = r.tasks || [];
        } catch (e) { /* API 已经 toast */ }
      }

      async function setView(v) {
        view.value = v;
        if (v !== 'active') await loadExtra();
      }

      function applyCounts(tid, counts) {
        if (!tid || !counts) return;
        if (window.applyTaskCounts) applyTaskCounts(tid, counts);
        const ex = extraTasks.value.find(x => x.id === tid);
        if (ex) ex.counts = counts;
      }

      async function fetchCountsSilent(tid) {
        if (!tid) return;
        try {
          const res = await fetch('/api/tasks/' + encodeURIComponent(tid) + '/counts');
          const body = await res.json();
          if (body && body.ok && body.counts) applyCounts(tid, body.counts);
        } catch (e) { /* 轮询失败不 toast */ }
      }

      function runningTaskIds() {
        const ids = [];
        const seen = new Set();
        (store.jobs || []).forEach(j => {
          if (!j || !j.task_id || seen.has(j.task_id)) return;
          if (!GEN_KINDS.has(j.kind) && j.kind !== 'refill') return;
          if (j.status !== 'running') return;
          seen.add(j.task_id);
          ids.push(j.task_id);
        });
        if (queueOk.value) {
          Object.values(queueMap.value || {}).forEach(slot => {
            const tid = slot && slot.running && slot.running.task_id;
            if (tid && !seen.has(tid)) { seen.add(tid); ids.push(tid); }
          });
        }
        return ids;
      }

      async function loadManifest(tid) {
        const r = await API.get('/api/tasks/' + encodeURIComponent(tid) + '/manifest');
        const m = r.manifest || {};
        manifests[tid] = m;
        // counts 由 /api/tasks 和 seg.changed 维护；这里只拉段落明细
        return m;
      }

      async function ensureMan(tid) {
        if (manifests[tid] && manifests[tid].segments) return manifests[tid];
        return loadManifest(tid);
      }

      async function loadLogs(task) {
        const j = liveJob(task.id) || lastJob(task.id);
        if (!j) return;
        if (store.logs[j.id] && store.logs[j.id].length) return;
        try {
          const txt = await API.get('/api/jobs/' + encodeURIComponent(j.id) + '/log');
          if (typeof txt === 'string' && txt) {
            store.logs[j.id] = txt.replace(/\r\n/g, '\n').split('\n').filter(x => x !== '');
          }
        } catch (e) { /* 没有日志就算了 */ }
      }

      async function toggle(task) {
        if (expanded[task.id]) {
          expanded[task.id] = false;
          return;
        }
        expanded[task.id] = true;
        try {
          await loadManifest(task.id);
        } catch (e) { /* toast 过了 */ }
        await loadLogs(task);
      }

      function closeMenu() {
        menu.show = false;
        menu.task = null;
      }

      function openMenu(ev, task) {
        ev.preventDefault();
        ev.stopPropagation();
        PrioFlagLogic.closePop();
        menu.task = task;
        menu.cx = ev.clientX;
        menu.cy = ev.clientY;
        menu.show = true;
        const guess = placeMenu(ev.clientX, ev.clientY, 220, 310, window.innerWidth, window.innerHeight, 8);
        menu.x = guess.left;
        menu.y = guess.top;
        nextTick(() => {
          const el = menuRef.value;
          if (!el) return;
          const p = placeMenu(ev.clientX, ev.clientY, el.offsetWidth, el.offsetHeight,
            window.innerWidth, window.innerHeight, 8);
          menu.x = p.left;
          menu.y = p.top;
        });
      }

      function onDocClick(ev) {
        if (menu.show && menuRef.value && !menuRef.value.contains(ev.target)) closeMenu();
      }

      function typingEl(el) {
        if (!el) return false;
        const tag = (el.tagName || '').toLowerCase();
        return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
      }

      function onBenchKey(ev) {
        if (!bench.on) return false;
        if (typingEl(ev.target) && ev.key !== 'Escape') return false;
        const overlay = benchPick.on || benchTail.on || benchPrompt.on;
        const k = ev.key;
        if (k === 'Escape') {
          if (benchPrompt.on) { closePrompt(); ev.preventDefault(); return true; }
          if (benchTail.on) { closeTail(); ev.preventDefault(); return true; }
          if (benchPick.on) { closePick(); ev.preventDefault(); return true; }
          closeBench(); ev.preventDefault(); return true;
        }
        if (overlay) return false;
        const cur = benchCurrent();
        if (k === '-' || k === '_') { bumpBenchZoom(-10); ev.preventDefault(); return true; }
        if (k === '=' || k === '+') { bumpBenchZoom(10); ev.preventDefault(); return true; }
        if (k === '0') { setBenchZoom(100, false); bench.densityHi = true; ev.preventDefault(); return true; }
        if (k === '1') { setBenchDensity('compact'); ev.preventDefault(); return true; }
        if (k === '2') { setBenchDensity('comfy'); ev.preventDefault(); return true; }
        if (k === '3') { setBenchDensity('large'); ev.preventDefault(); return true; }
        if (k === 'j' || k === 'J' || k === 'ArrowDown') { moveBench(1); ev.preventDefault(); return true; }
        if (k === 'k' || k === 'K' || k === 'ArrowUp') { moveBench(-1); ev.preventDefault(); return true; }
        if (k === ' ' || k === 'Spacebar') {
          if (cur) benchPass(cur);
          ev.preventDefault(); return true;
        }
        if (k === 's' || k === 'S') { if (cur) benchSwap(cur); ev.preventDefault(); return true; }
        if (k === 'f' || k === 'F') { if (cur) setSegMode(cur, 'single'); ev.preventDefault(); return true; }
        if (k === 'Enter') { toggleBenchPeek(); ev.preventDefault(); return true; }
        return false;
      }

      function onKey(ev) {
        if (onBenchKey(ev)) return;
        if (ev.key === 'Escape') {
          closeMenu();
          redoOn.value = false;
          closeBlocked();
          clearDlg.value = null;
        }
      }

      function brollHitsOf(m) {
        const byP = {};
        ((m && m.images) || []).forEach(im => { byP[im.p] = im; });
        return ((m && m.segments) || []).filter(s => {
          if (s.status !== 'planned' && s.status !== 'failed') return false;
          const im = byP[s.image];
          return im && im.role === '空镜';
        }).map(s => 's' + pad3(s.s) + '→' + s.image);
      }

      function copyTextOf(task, m) {
        if (task && String(task.copy || '').trim()) return String(task.copy);
        const c = m && m.copy;
        if (!c) return '';
        if (typeof c === 'string') return c;
        return String(c.text || '');
      }

      async function startGenerate(tid, extra) {
        const m = await loadManifest(tid);
        const task = (store.tasks || []).find(tk => tk && tk.id === tid) || null;
        // 没文案的空镜头：图是不是主体无所谓，不要在这里拦住。
        const noCopy = !String(copyTextOf(task, m) || '').trim();
        if (!noCopy) {
          const bad = brollHitsOf(m);
          if (bad.length) {
            toast(t('gen.broll_block'), bad.join('、'), 'bad');
            throw new Error(t('gen.broll_block'));
          }
        }
        const r = await API.job('generate', tid, extra || {});
        if (r && r.account_held_by) {
          toast(t('gen.held_toast', { port: String(r.account_held_by) }), '', '');
        }
        return r;
      }

      async function startMany(list) {
        const todo = list.filter(t => eligible(t) && !isLive(t));
        if (!todo.length) {
          toast(t('gen.none_work'), '', '');
          return;
        }
        posting.value = true;
        let n = 0;
        try {
          for (const tsk of todo) {
            try {
              await startGenerate(tsk.id);
              n += 1;
            } catch (e) { /* 单条失败已 toast（含空镜拦住） */ }
          }
        } finally {
          posting.value = false;
        }
        if (n) toast(t('gen.started', { n: String(n) }), '', 'ok');
      }

      function startAll() {
        return startMany((store.tasks || []).filter(t => eligible(t)));
      }

      function startSelected() {
        return startMany(selected.value);
      }

      async function cancelJob(j) {
        await API.post('/api/jobs/' + encodeURIComponent(j.id) + '/cancel');
      }

      async function stopTask(task) {
        delete waitRefill[task.id];
        const js = jobsOf(task.id).filter(j => j.status === 'running' || j.status === 'queued' || j.status === 'halted');
        for (const j of js) await cancelJob(j);
      }

      async function stopSelected() {
        for (const tsk of selected.value) await stopTask(tsk);
      }

      async function stopAll() {
        const js = occupyJobs().filter(j => GEN_KINDS.has(j.kind) || j.kind === 'refill');
        for (const k of Object.keys(waitRefill)) delete waitRefill[k];
        for (const j of js) await cancelJob(j);
        if (js.length) toast(t('gen.stopped'), '', '');
      }

      async function redoFailed(task, acc) {
        const m = await ensureMan(task.id);
        const segs = ((m && m.segments) || []).filter(s => s.status === 'failed').map(s => s.s);
        if (!segs.length) { toast(t('gen.none_fail'), '', ''); return; }
        await API.job('regenerate', task.id, regenArgs(segs, acc));
      }

      // 被拒（blocked）的段直接用本地模型出片：用原图，不换图
      async function blockedToStudio(task) {
        const m = await ensureMan(task.id);
        const segs = ((m && m.segments) || []).filter(s => s.status === 'blocked').map(s => s.s);
        if (!segs.length) { toast(t('gen.none_block'), '', ''); return; }
        await API.job('regenerate', task.id, regenArgs(segs, 'STUDIO'));
      }

      // 右键菜单里「选账号重做失败段」：在点右键的位置弹账号菜单
      async function redoFailedPick(task, cx, cy) {
        const m = await ensureMan(task.id);
        const fails = ((m && m.segments) || []).filter(s => s.status === 'failed');
        if (!fails.length) { toast(t('gen.none_fail'), '', ''); return; }
        const anchor = { getBoundingClientRect: () => ({ left: cx, right: cx, top: cy, bottom: cy }) };
        const acc = await window.RegenAcc.menu(anchor, { task, segs: fails });
        if (acc !== null && acc !== undefined) await redoFailed(task, acc);
      }

      function studioOn() { return !!(store.studio && store.studio.enabled); }

      // 规格 98：排给本地模型的段，不等这一轮结束 / 不等每分钟的重试，现在就交
      async function studioHandoffNow() {
        const tid = blockedDlg.tid;
        if (!tid) return;
        try {
          const r = await API.post('/api/tasks/' + encodeURIComponent(tid) + '/studio-handoff');
          if (r.started) closeBlocked();
          else if (r.skip === 'offline') toast(t('gen.studio_offline'), '', 'warn');
          else if (r.skip === 'none') toast(t('gen.studio_none'), '', '');
        } catch (e) { /* toast */ }
      }

      function rtcOn(task) {
        if (!task) return true;
        const v = task.run_to_completion;
        if (v === 0 || v === false || v === '0') return false;
        return true;
      }

      async function setRtc(task, ev) {
        if (!task) return;
        const on = !!(ev && ev.target && ev.target.checked);
        try {
          const r = await API.post('/api/tasks/' + encodeURIComponent(task.id) + '/rtc', { on });
          task.run_to_completion = (r && r.run_to_completion != null) ? r.run_to_completion : (on ? 1 : 0);
        } catch (e) {
          if (ev && ev.target) ev.target.checked = rtcOn(task);
        }
      }

      async function openBlocked(task) {
        if (!task) return;
        try {
          const r = await API.get('/api/tasks/' + encodeURIComponent(task.id) + '/blocked');
          blockedDlg.tid = task.id;
          blockedDlg.rows = (r && r.rows) || [];
          blockedDlg.on = true;
        } catch (e) { /* toast 过了 */ }
      }

      function closeBlocked() {
        blockedDlg.on = false;
        blockedDlg.tid = '';
        blockedDlg.rows = [];
      }

      async function retryBlocked(row, acc) {
        if (!blockedDlg.tid || !row) return;
        try {
          const body = { s: row.s };
          if (acc) body.account_id = acc;
          await API.post('/api/tasks/' + encodeURIComponent(blockedDlg.tid) + '/blocked/retry', body);
          if (acc) window.RegenAcc.regenToast(acc, [row.s]);
          else toast(t('gen.blocked_retry'), 's' + pad3(row.s), 'ok');
          closeBlocked();
        } catch (e) { /* */ }
      }

      async function borrowBlocked(row) {
        if (!blockedDlg.tid || !row) return;
        try {
          await API.post('/api/tasks/' + encodeURIComponent(blockedDlg.tid) + '/blocked/borrow', { s: row.s });
          toast(t('gen.blocked_borrow'), 's' + pad3(row.s), 'ok');
          closeBlocked();
        } catch (e) { /* */ }
      }

      async function goSwapImage(row) {
        const tid = blockedDlg.tid;
        closeBlocked();
        if (window.flushAll) await window.flushAll();
        else if (window.flushEdits) await window.flushEdits();
        if (tid) {
          ui.pendingTid = tid;
          ui.panelTid = tid;
        }
        if (row && row.p) ui.pendingP = row.p;
        ui.node = 'gemini';
      }

      async function redoBlocked(task) {
        const m = await ensureMan(task.id);
        const n = ((m && m.segments) || []).filter(s => s.status === 'blocked').length;
        if (!n) { toast(t('gen.none_block'), '', ''); return; }
        const r = await API.job('refill', task.id);
        const jid = r.job && r.job.id;
        if (jid) waitRefill[task.id] = jid;
      }

      function openRedo(task) {
        redoTid.value = task.id;
        redoText.value = '';
        redoAcc.value = '';
        redoOn.value = true;
        ensureMan(task.id).catch(() => {});
      }

      const redoTask = computed(() => (store.tasks || []).find(x => x.id === redoTid.value) || null);
      const redoSegs = computed(() => {
        const want = new Set(parseSegs(redoText.value));
        const m = manifests[redoTid.value];
        return ((m && m.segments) || []).filter(sg => want.has(sg.s));
      });

      async function confirmRedo() {
        const segs = parseSegs(redoText.value);
        if (!segs.length) { toast(t('gen.redo.none'), '', ''); return; }
        const tid = redoTid.value;
        const args = { segs };
        if (redoAcc.value) args.account_id = redoAcc.value;
        redoOn.value = false;
        await API.job('regenerate', tid, args);
      }

      async function openFolder(task) {
        await API.post('/api/tasks/' + encodeURIComponent(task.id) + '/open-folder');
      }

      async function moveStatus(task, st) {
        await API.post('/api/tasks/' + encodeURIComponent(task.id) + '/status', { status: st });
        checked[task.id] = false;
        expanded[task.id] = false;
        await loadExtra();
      }

      function restore(task) {
        return moveStatus(task, restoreStatus(task));
      }

      async function goTrack() {
        if (window.flushAll) await window.flushAll();
        ui.node = 'track';
      }

      async function goDescribe(task) {
        if (window.flushAll) await window.flushAll();
        else if (window.flushEdits) await window.flushEdits();
        if (task && task.id) {
          ui.pendingTid = task.id;
          ui.panelTid = task.id;
        }
        ui.node = 'gemini';
      }

      function missHint(task) {
        const n = missingDescN(task);
        const ps = ((task && task.counts && task.counts.desc_missing_ps) || []).join('、');
        return t('gen.need_desc.tip', { n: String(n), ps: ps || '' });
      }

      async function stopStuck(s) {
        if (!s || !s.job_id) return;
        try {
          await API.post('/api/jobs/' + encodeURIComponent(s.job_id) + '/cancel');
          toast(t('gen.stopped'), s.job_id, '');
        } catch (e) { /* API 已经 toast */ }
        pullQueue();
      }

      function segsAllImaged(task) {
        const segs = (task && task.segments) || (task && task.counts && task.counts.segs) || null;
        // prefer explicit segments; else counts.images_on_segs / segs
        if (Array.isArray(segs) && segs.length) {
          return segs.every(s => !!(s.image || s.img));
        }
        const c = task && task.counts;
        if (c && c.segs && c.imaged_segs != null) return Number(c.imaged_segs) >= Number(c.segs);
        if (c && c.images_ready && atLeastArrangedLike(task)) return true;
        // fallback: if backend says images_ready and stale, allow button
        return !!(c && c.images_ready);
      }
      function atLeastArrangedLike(task) { return true; }
      function isNoCopyTask(task) {
        return !(task && String(task.copy || '').trim());
      }
      async function useCurrentTrack(task) {
        if (!task || !task.id) return;
        try {
          await API.post('/api/tasks/' + encodeURIComponent(task.id) + '/use-current-track', { start: true });
          if (store.refreshTasks) store.refreshTasks();
        } catch (e) { /* toast */ }
      }

      function staleHint(task) {
        return t('gen.stale_track', { st: t('tasks.status.' + (task && task.status || '')) });
      }

      function stuckText(ln) {
        const s = ln && ln.stuck;
        if (!s) return '';
        return t('gen.lane.stuck', {
          name: ln.name,
          jid: s.job_id || '',
          kind: s.kind || '',
          n: String(s.idle_min || 0),
        });
      }

      function menuOff(task, key) {
        if (!task) return true;
        if (task.archived && ['start', 'stop', 'fail', 'block', 'pick',
            'finished', 'trash', 'restore', 'prio-top', 'prio-set', 'prio-clear'].includes(key)) {
          return true;
        }
        if (key === 'start') return !canStart(task);
        if (key === 'stop') return !canStop(task);
        if (key === 'fail') return !(countsOf(task).failed);
        if (key === 'block') return !(countsOf(task).blocked);
        if (key === 'folder') return !task.dir || !!task.missing_dir;
        if (key === 'finished') return task.status === 'finished';
        if (key === 'trash') return task.status === 'trash';
        if (key === 'restore') return task.status !== 'finished' && task.status !== 'trash';
        if (key === 'prio-clear') return !(Number(task.priority) > 0);
        return false;
      }

      async function onMenu(key) {
        const task = menu.task;
        const cx = menu.cx, cy = menu.cy;
        closeMenu();
        if (!task) return;
        try {
          if (key === 'prio-top') return await setPrio(task, { top: true });
          if (key === 'prio-set') {
            PrioFlagLogic.openPop({ tid: task.id, priority: Number(task.priority) || 0, x: cx, y: cy });
            return;
          }
          if (key === 'prio-clear' && !menuOff(task, 'prio-clear')) return await setPrio(task, { priority: 0 });
          if (key === 'start' && !menuOff(task, 'start')) return await startGenerate(task.id);
          if (key === 'stop' && !menuOff(task, 'stop')) return await stopTask(task);
          if (key === 'fail' && !menuOff(task, 'fail')) return await redoFailed(task);
          if (key === 'fail-acc' && !menuOff(task, 'fail')) return await redoFailedPick(task, cx, cy);
          if (key === 'block-studio' && !menuOff(task, 'block') && studioOn()) return await blockedToStudio(task);
          if (key === 'block' && !menuOff(task, 'block')) return await redoBlocked(task);
          if (key === 'pick') return openRedo(task);
          if (key === 'folder' && !menuOff(task, 'folder')) return await openFolder(task);
          if (key === 'finished' && !menuOff(task, 'finished')) return await moveStatus(task, 'finished');
          if (key === 'trash' && !menuOff(task, 'trash')) return await moveStatus(task, 'trash');
          if (key === 'restore' && !menuOff(task, 'restore')) return await restore(task);
        } catch (e) { /* API 已经 toast */ }
      }

      function runPct(task) {
        const c = countsOf(task);
        const tot = c.total || 0;
        if (!tot) return 0;
        return Math.max(0, Math.min(100, (c.collected || 0) * 100 / tot));
      }

      function isIdle(task) {
        if (!isRunning(task)) return false;
        const runAt = seenRun[task.id] || 0;
        const segAt = (store.segAt && store.segAt[task.id]) || 0;
        const at = Math.max(runAt, segAt);
        if (!at) return false;
        return (nowTick.value - at) >= 90000;
      }

      function noteRunning(tid) {
        if (tid && seenRun[tid] == null) seenRun[tid] = Date.now();
      }

      watch(() => (store.jobs || []).map(j => j.id + ':' + j.status).join(','), async () => {
        pullQueue();
        for (const [tid, jid] of Object.entries(waitRefill)) {
          const j = (store.jobs || []).find(x => x.id === jid);
          if (!j) continue;
          if (j.status === 'done') {
            delete waitRefill[tid];
            try { await startGenerate(tid); } catch (e) { /* toast 过了 */ }
          } else if (j.status === 'failed' || j.status === 'cancelled' || j.status === 'lost') {
            delete waitRefill[tid];
          }
        }
        const live = {};
        (store.jobs || []).forEach(j => {
          if (!j || !j.task_id) return;
          if (!GEN_KINDS.has(j.kind) && j.kind !== 'refill') return;
          live[j.task_id] = j.status;
        });
        const tids = new Set([...Object.keys(prevJobSt), ...Object.keys(live)]);
        tids.forEach(tid => {
          const was = prevJobSt[tid];
          const now = live[tid];
          const wasRun = was === 'running' || was === 'queued';
          const nowEnd = !now || now === 'done' || now === 'failed' || now === 'cancelled' || now === 'lost';
          if (wasRun && nowEnd) fetchCountsSilent(tid);
          if (now === 'running') noteRunning(tid);
          else delete seenRun[tid];
        });
        Object.keys(prevJobSt).forEach(k => { delete prevJobSt[k]; });
        Object.assign(prevJobSt, live);
        rows.value.forEach(tsk => {
          if (expanded[tsk.id]) loadLogs(tsk);
        });
      });

      watch(() => rows.value.map(tsk => tsk.id + ':' + modeOf(tsk)).join('|'), () => {
        rows.value.forEach(tsk => {
          if (modeOf(tsk) === 'anchor_bridge') ensureChain(tsk);
        });
      });

      watch([() => genMode.value, () => (selected.value[0] && selected.value[0].id) || ''], () => {
        if (genMode.value === 'tail_chain' && selected.value[0]) loadChain(selected.value[0].id);
        if (needFfmpeg()) checkFfmpeg();
      });

      watch(() => {
        if (!bench.jobId) return '';
        const j = (store.jobs || []).find(x => x.id === bench.jobId);
        return j ? j.status : '';
      }, (st, was) => {
        if (!bench.jobId) return;
        if (st === 'done' || st === 'failed' || st === 'cancelled' || st === 'lost') {
          const id = bench.tid;
          bench.jobId = '';
          if (bench.on && id) loadBench(id).catch(() => {});
          if (id) loadManifest(id).catch(() => {});
        }
      });

      watch(() => store.refreshSeq, () => {
        pullQueue();
        loadExtra();
        runningTaskIds().forEach(id => {
          fetchCountsSilent(id);
          if (expanded[id]) loadManifest(id).catch(() => {});
        });
      });
      watch(() => (store.tasks || []).map(x => x.id + ':' + x.status + ':' + (x.priority || 0) + ':' + (x.updated || '')).join('|'), () => {
        pullQueue();
        if (view.value !== 'active') loadExtra();
        const by = {};
        (store.tasks || []).forEach(x => { by[x.id] = Number(x.priority) || 0; });
        extraTasks.value.forEach(x => {
          if (x.id in by) x.priority = by[x.id];
        });
      });

      watch(tid, () => {
        ui.panelTid = tid.value || '';
        nextTick(() => {
          const el = document.querySelector('.panel-generate .g-item.current');
          if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
        });
      });
      watch(() => ui.pendingTid, (v) => { if (v) takePendingTid(tid, 'generate'); });
      watch(() => ui.panelTid, (v) => { if (v && v !== tid.value) tid.value = v; });

      onMounted(() => {
        document.addEventListener('click', onDocClick);
        document.addEventListener('keydown', onKey);
        // 描述语是在多模态里存的。进来时重读一次，避免生成行还显示保存前的「缺描述语」。
        if (window.refreshTasks) refreshTasks().catch(() => {});
        loadExtra();
        pullQueue();
        if (needFfmpeg()) checkFfmpeg();
        if (!adoptPanelTid(tid, 'generate') && !tid.value) {
          const cur = (ui.panelTid || '').trim();
          if (cur) tid.value = cur;
        }
        ui.panelTid = tid.value || ui.panelTid || '';
        const pb = window.ui && window.ui.pendingBench;
        if (pb && pb.tid) {
          window.ui.pendingBench = null;
          openBenchFor(pb.tid, pb.s);
        }
        runningTaskIds().forEach(id => {
          noteRunning(id);
          fetchCountsSilent(id);
        });
        queueTimer = setInterval(pullQueue, 3000);
        // 只轮询正在跑的任务：折叠也轮 counts；展开才顺手拉段落明细。没在跑的一个都不轮。
        pollTimer = setInterval(() => {
          runningTaskIds().forEach(id => {
            fetchCountsSilent(id);
            if (expanded[id]) loadManifest(id).catch(() => {});
          });
        }, 10000);
        idleTimer = setInterval(() => { nowTick.value = Date.now(); }, 1000);
        // 切走节点会卸掉 watch；回来时把还等着的 refill 接上
        Object.entries(waitRefill).forEach(([tid, jid]) => {
          const j = (store.jobs || []).find(x => x.id === jid);
          if (!j || j.status === 'failed' || j.status === 'cancelled' || j.status === 'lost') {
            delete waitRefill[tid];
          } else if (j.status === 'done') {
            delete waitRefill[tid];
            startGenerate(tid).catch(() => {});
          }
        });
      });

      const throttleBanner = computed(() => {
        const ft = store.flowThrottle;
        if (ft && !ft.dismissed) return ft;
        if (store.network_throttled && !(ft && ft.dismissed)) {
          return { network: true, account_name: '', alts: [] };
        }
        return null;
      });
      const throttleAlts = computed(() => {
        const ft = throttleBanner.value;
        if (!ft || ft.network) return [];
        if (ft.alts && ft.alts.length) return ft.alts;
        const except = ft.account_id;
        return (store.accounts || []).filter(a =>
          a.engine === 'flow' && a.id !== except && a.enabled !== 0
          && a.login_state !== 'need_relogin'
          && !(((a.extra || {}).throttled_until_ts || 0) * 1000 > nowTick.value));
      });
      function accThrottleMin(task) {
        const a = accOf(task);
        if (!a) return 0;
        const extra = a.extra || {};
        const ts = Number(extra.throttled_until_ts || 0);
        let end = ts ? ts * 1000 : 0;
        if (!end && extra.throttled_until) {
          const p = Date.parse(extra.throttled_until);
          if (Number.isFinite(p)) end = p;
        }
        if (!end) return 0;
        const ms = end - nowTick.value;
        return ms > 0 ? Math.max(1, Math.ceil(ms / 60000)) : 0;
      }
      function dismissThrottle() {
        if (store.flowThrottle) store.flowThrottle = Object.assign({}, store.flowThrottle, { dismissed: true });
      }
      // 规格 91：把「为什么被限」摊开写在横幅里
      const throttleWhy = computed(() => {
        const ft = throttleBanner.value;
        if (!ft) return [];
        const out = [];
        if (ft.network) {
          const n = ft.net || {};
          const ids = (n.accounts || []).length;
          out.push(t('gen.th.why_net', { n: String(ids || 0), at: n.at || '?' }));
          out.push(t('gen.th.why_net2'));
          return out;
        }
        const w = ft.why || {};
        if (w.code) {
          out.push(t('gen.th.why_code', {
            code: w.code, rpc: w.rpc || '-', at: w.at || '?',
            seg: w.seg ? ('（' + w.seg + '）') : '',
          }));
        }
        if (w.minutes) {
          out.push(t('gen.th.why_cool', { min: String(w.minutes), times: String(w.times || 1) }));
        }
        if (w.first_shot === true && w.code === 'PUBLIC_ERROR_UNUSUAL_ACTIVITY') {
          // 规格 95：第一发就被拒 ≠ 提交太快
          out.push(t('gen.th.why_first'));
          out.push(t('gen.th.why_first_env'));
          out.push(t('gen.th.why_first_plan'));
        }
        const pm = Number(ft.probe_min || 0);
        if (pm > 0) out.push(t('gen.th.why_probe', { min: String(pm) }));
        out.push(t('gen.th.why_browser'));
        return out;
      });

      async function tryNowThrottle() {
        const ft = store.flowThrottle || throttleBanner.value || {};
        const body = {
          account_id: ft.account_id || (accOf(selectedTask.value) || {}).id || '',
          task_id: ft.task_id || tid.value || '',
          network: !!ft.network || !!store.network_throttled,
        };
        try {
          await API.post('/api/flow/throttle/clear', body);
          store.flowThrottle = null;
          store.network_throttled = false;
          toast(t('gen.th.try_now_ok'), '', 'ok');
        } catch (e) { /* toast */ }
      }

      async function waitThrottle() {
        const ft = store.flowThrottle || {};
        const taskId = ft.task_id || tid.value;
        if (taskId) {
          try { await API.post('/api/flow/throttle/wait', { task_id: taskId }); }
          catch (e) { /* toast */ }
        }
      }
      async function switchThrottleAcc(ev) {
        const aid = ev && ev.target && ev.target.value;
        const ft = store.flowThrottle || {};
        const taskId = ft.task_id || tid.value;
        if (!aid || !taskId) return;
        try {
          await API.post('/api/tasks/' + encodeURIComponent(taskId) + '/account', { account_id: aid });
          store.flowThrottle = null;
        } catch (e) { /* toast */ }
        if (ev && ev.target) ev.target.value = '';
      }

      onUnmounted(() => {
        document.removeEventListener('click', onDocClick);
        document.removeEventListener('keydown', onKey);
        if (pollTimer) clearInterval(pollTimer);
        if (queueTimer) clearInterval(queueTimer);
        if (idleTimer) clearInterval(idleTimer);
        PrioFlagLogic.closePop();
      });

      return {
        t, view, tid, rows, selected, emptyActive, canAll, canStartSel, lanes, checked, expanded, manifests,
        pickerTasks, selectedTask, tidReady, setCurrent,
        menu, menuRef, redoOn, redoText, redoAcc, redoTask, redoSegs, posting, clearDlg, genMode, modeDisabled, modeHint, modeLabel, chainHint,
        rtcOn, setRtc, blockedDlg, openBlocked, closeBlocked, retryBlocked, borrowBlocked, goSwapImage,
        benchTask, segsOf, studioOn, studioHandoffNow,
        setView, laneRunParts, laneRunTid, lanePacked, miniFlag, accName, countsOf, cellsOf, cellTitle, segsOf, logLines, logJobId,
        isLive, isRunning, isQueued, queuedText, canStart, canStop, flagState, pad3,
        modeOf, showChain, chainOf, applyMode, toggleRole, ensureChain, chainTitle,
        chainCfg, saveChain, chainCascade, chainRegen, mediaUrl,
        ffm, checkFfmpeg, installFfmpeg, needFfmpeg,
        bench, benchPick, benchTail, benchPrompt, benchNeed, benchSorted, gapWord,
        benchEl, benchPos, benchStyle, benchCurrent,
        openBench, openBenchFor, closeBench, benchSwap, benchSwapRed, benchPass, benchPassYellow, benchPassAll, benchRetry,
        setBenchDensity, setBenchZoom, setBenchFilter, setSegMode, onBenchKey,
        toggleBenchSort, openPick, closePick, hoverScore, scoreOf, scoreClass, pickAnchor, pickItem,
        openTail, closeTail, tailFrame, saveTail, benchRegen, openPrompt, closePrompt, savePrompt,
        isStaleTrack, staleHint, stuckText, stopStuck, isIdle, runPct,
        missingDescN, missHint, goDescribe,
        isArchived, isGone, clipChanged,
        toggle, openMenu, closeMenu, onMenu, menuOff,
        startAll, startSelected, stopAll, stopSelected, stopTask, startGenerate,
        exportSelected, exportAll, clearSelected, clearAll, exportThenClear, importProj, unfreezeTask,
        goTrack, segsAllImaged, isNoCopyTask, useCurrentTrack, confirmRedo, liveJob, copyText: window.copyText,
        throttleBanner, throttleAlts, accThrottleMin, dismissThrottle, waitThrottle, switchThrottleAcc,
        throttleWhy, tryNowThrottle,
      };
    },
    components: { PrioFlag: window.PrioFlag, TaskPick: window.TaskPick, GenModePick: window.GenModePick,
      RegenAccPick: window.RegenAccPick, RegenAccMenu: window.RegenAccMenu },
    template: `
<section class="panel-generate">
  <div class="g-hd">
    <h2>{{ t('node.generate') }}</h2>
    <task-pick v-model="tid" min-status="arranged" :tasks="pickerTasks"></task-pick>
    <button class="btn primary" :disabled="posting || !canAll" @click="startAll">{{ t('gen.all') }}</button>
    <button class="btn" @click="stopAll">{{ t('gen.stop_all') }}</button>
    <div class="g-mode" :class="{'is-off': modeDisabled}">
      <span class="lab">{{ t('gen.mode') }}</span>
      <gen-mode-pick :model-value="genMode" :disabled="modeDisabled" :hint="modeHint"
                     :title="t('gen.mode.anchor_tip')"
                     @pick="applyMode"></gen-mode-pick>
      <span class="g-mode-hint" v-if="modeHint">{{ modeHint }}</span>
    </div>
    <button class="btn sm g-bench-btn" @click="openBench">{{ t('gen.bench') }}</button>
    <span class="spacer"></span>
    <div class="g-archbar">
      <button class="btn sm" :disabled="!selected.length" @click="exportSelected">{{ t('gen.archive.export_sel') }}</button>
      <button class="btn sm" @click="exportAll">{{ t('gen.archive.export_all') }}</button>
      <span class="g-sep"></span>
      <button class="btn sm" :disabled="!selected.length" @click="clearSelected">{{ t('gen.archive.clear_sel') }}</button>
      <button class="btn sm" @click="clearAll">{{ t('gen.archive.clear_all') }}</button>
      <span class="g-sep"></span>
      <button class="btn sm" @click="importProj">{{ t('gen.archive.import') }}</button>
    </div>
    <div class="g-tabs">
      <button :class="{on: view==='active'}" @click="setView('active')">{{ t('gen.view.active') }}</button>
      <button :class="{on: view==='finished'}" @click="setView('finished')">{{ t('gen.view.finished') }}</button>
      <button :class="{on: view==='trash'}" @click="setView('trash')">{{ t('gen.view.trash') }}</button>
    </div>
  </div>

  <div class="g-ffm" v-if="needFfmpeg() && ffm.checked && !ffm.ok">
    <span class="warn">⚠ {{ t('gen.ffmpeg.missing') }}</span>
    <button class="btn sm primary" :disabled="ffm.busy" @click="installFfmpeg">{{ ffm.busy ? t('gen.ffmpeg.installing') : t('gen.ffmpeg.install') }}</button>
    <button class="btn sm" :disabled="ffm.busy" @click="checkFfmpeg">{{ t('gen.ffmpeg.recheck') }}</button>
    <span class="muted">{{ t('gen.ffmpeg.alt') }}</span>
    <span class="muted" v-if="ffm.msg">{{ ffm.msg }}</span>
  </div>
  <div class="g-chain-row" v-if="genMode==='tail_chain' && !modeDisabled">
    <span class="lab">{{ t('gen.chain.start') }}</span>
    <select class="g-chain-start" :value="chainCfg.start_image" @change="chainCfg.start_image=$event.target.value; saveChain({start_image:$event.target.value})">
      <option v-for="im in chainCfg.images" :key="im.p" :value="im.p">{{ im.p }}</option>
    </select>
    <span class="thumb" v-if="chainCfg.start_image">
      <img :src="mediaUrl(selected[0] && selected[0].id, (chainCfg.images.find(x=>x.p===chainCfg.start_image)||{}).file)" alt="">
    </span>
    <span class="lab">{{ t('gen.chain.desc') }}</span>
    <label><input type="radio" name="c-desc" value="never" :checked="chainCfg.desc_policy==='never'"
                  @change="chainCfg.desc_policy='never'; saveChain({desc_policy:'never'})"> {{ t('gen.chain.desc_never') }}</label>
    <label><input type="radio" name="c-desc" value="always" :checked="chainCfg.desc_policy==='always'"
                  @change="chainCfg.desc_policy='always'; saveChain({desc_policy:'always'})"> {{ t('gen.chain.desc_always') }}</label>
    <label><input type="radio" name="c-desc" value="auto" :checked="chainCfg.desc_policy==='auto'"
                  @change="chainCfg.desc_policy='auto'; saveChain({desc_policy:'auto'})"> {{ t('gen.chain.desc_auto') }}</label>
    <span class="lab">{{ t('gen.chain.upscale_pre') }}</span>
    <input type="number" min="0" max="99" class="g-chain-n" :value="chainCfg.upscale_every"
           @change="chainCfg.upscale_every=Number($event.target.value)||0; saveChain({upscale_every:chainCfg.upscale_every})">
    <span class="lab">{{ t('gen.chain.upscale_post') }}</span>
  </div>

  <div class="g-throttle-banner" v-if="throttleBanner">
    <div class="g-th-title">{{ throttleBanner.network ? t('gen.th.net_title') : t('gen.th.title', { name: throttleBanner.account_name || '' }) }}</div>
    <div class="g-th-body">{{ throttleBanner.network ? t('gen.th.net_body') : t('gen.th.body') }}</div>
    <ul class="g-th-why" v-if="throttleWhy.length">
      <li v-for="(ln, i) in throttleWhy" :key="i">{{ ln }}</li>
    </ul>
    <div class="g-th-acts">
      <button type="button" class="btn sm primary" @click="tryNowThrottle">{{ t('gen.th.try_now') }}</button>
      <select v-if="!throttleBanner.network" class="g-th-switch"
              :disabled="!throttleAlts.length" @change="switchThrottleAcc">
        <option value="">{{ throttleAlts.length ? t('gen.th.switch') : t('gen.th.switch_none') }}</option>
        <option v-for="a in throttleAlts" :key="a.id" :value="a.id">{{ a.name }}</option>
      </select>
      <button v-if="!throttleBanner.network" type="button" class="btn sm" @click="waitThrottle">{{ t('gen.th.wait') }}</button>
      <button type="button" class="btn sm ghost" @click="dismissThrottle">{{ t('gen.th.ok') }}</button>
    </div>
  </div>

  <div class="g-bench-banner" v-if="benchNeed() > 0 && !bench.on">
    <span>{{ t('gen.bench.banner', { n: String(benchNeed()) }) }}</span>
    <button class="btn sm" @click="openBench">{{ t('gen.bench.open') }}</button>
  </div>

  <div class="g-lanes" v-if="lanes.length">
    <span v-for="ln in lanes" :key="ln.id" class="g-lane">
      <button v-if="ln.stuck" type="button" class="g-stuck" :title="t('gen.stop')" @click="stopStuck(ln.stuck)">{{ stuckText(ln) }}</button>
      <template v-else-if="ln.running">
        {{ laneRunParts(ln).pre }}<span class="mono">{{ laneRunTid(ln) }}</span><span class="pf-mini" v-if="!lanePacked(ln)" v-html="miniFlag(ln.running.prio)"></span>{{ laneRunParts(ln).post }}<template v-if="ln.preview.length">：<span v-for="(w,i) in ln.preview" :key="w.job_id || (w.task_id + i)">{{ i ? '、' : '' }}{{ w.task_id }}<span class="pf-mini" v-html="miniFlag(w.prio)"></span></span><span v-if="ln.more">{{ t('gen.lane.more', { n: String(ln.more) }) }}</span></template>
      </template>
      <template v-else-if="ln.waiting && ln.waiting.length">
        {{ t('gen.lane.queue', { name: ln.name, n: String(ln.waiting.length) }) }}：<span v-for="(w,i) in ln.preview" :key="w.job_id || (w.task_id + i)">{{ i ? '、' : '' }}{{ w.task_id }}<span class="pf-mini" v-html="miniFlag(w.prio)"></span></span><span v-if="ln.more">{{ t('gen.lane.more', { n: String(ln.more) }) }}</span>
      </template>
      <template v-else>{{ t('gen.lane.idle', { name: ln.name }) }}</template>
    </span>
  </div>

  <div class="g-body">
    <div class="empty" v-if="emptyActive">
      <p>{{ t('gen.empty') }}</p>
      <button class="btn primary" @click="goTrack">{{ t('gen.empty.go') }}</button>
    </div>
    <div class="empty" v-else-if="view==='finished' && !rows.length">{{ t('gen.none_finished') }}</div>
    <div class="empty" v-else-if="view==='trash' && !rows.length">{{ t('gen.none_trash') }}</div>

    <div v-else class="g-list">
      <div v-for="tsk in rows" :key="tsk.id" class="g-item"
           :class="[tsk.engine, {'is-gone': isGone(tsk), current: tsk.id===tid}]"
           @click="setCurrent(tsk)"
           @contextmenu="openMenu($event, tsk)">
        <div class="g-main">
          <input type="checkbox" v-model="checked[tsk.id]" @click.stop>
          <span v-if="isGone(tsk)" class="g-mark" :title="t('gen.archive.missing_dir')">🚫</span>
          <span v-else-if="isArchived(tsk)" class="g-mark" :title="t('gen.archive.readonly')">📦</span>
          <prio-flag :task="tsk" :state="flagState(tsk)"></prio-flag>
          <span class="g-ebar" :class="tsk.engine"></span>
          <div class="g-id">
            <div class="g-name">
              <span class="mono">{{ tsk.id }}</span>
              <b>{{ tsk.name || '' }}</b>
            </div>
            <div class="muted g-acc">{{ accName(tsk) }}
              <span v-if="accThrottleMin(tsk)" class="g-th-tag">{{ t('gen.throttled_row', { n: String(accThrottleMin(tsk)) }) }}</span>
            </div>
            <div class="g-gone" v-if="isGone(tsk)">{{ t('gen.archive.missing_dir') }}</div>
          </div>
          <div class="g-barwrap">
            <div class="g-barline">
              <span class="g-mode-badge" :class="modeOf(tsk)">{{ modeLabel(tsk) }}</span>
              <div class="g-segbar">
                <i v-for="(st, i) in cellsOf(tsk)" :key="i" class="g-seg" :class="st"
                   :title="cellTitle(tsk, i, cellsOf(tsk).length)"></i>
              </div>
            </div>
            <div class="g-thin" v-if="isRunning(tsk) && countsOf(tsk).total">
              <i :style="{width: runPct(tsk) + '%'}"></i>
            </div>
          </div>
          <div class="g-nums mono">
            <span>{{ t('gen.collected', { a: String(countsOf(tsk).collected || 0), b: String(countsOf(tsk).total || 0) }) }}</span>
            <span v-if="countsOf(tsk).failed" class="bad">{{ t('gen.failed', { n: String(countsOf(tsk).failed) }) }}</span>
            <button type="button" class="g-block-badge" v-if="countsOf(tsk).blocked"
                    @click.stop="openBlocked(tsk)">{{ t('gen.blocked_badge', { n: String(countsOf(tsk).blocked) }) }}</button>
            <label class="g-rtc" :title="t('gen.rtc_tip')" @click.stop>
              <input type="checkbox" :checked="rtcOn(tsk)" @change="setRtc(tsk, $event)">
              {{ t('gen.rtc') }}
            </label>
          </div>
          <button v-if="isIdle(tsk)" type="button" class="g-idle" @click.stop="toggle(tsk)">{{ t('gen.idle_warn') }}</button>
          <span class="tag" :class="'st-' + tsk.status">{{ t('tasks.status.' + tsk.status) }}</span>
          <span class="g-miss" v-if="missingDescN(tsk)"
                :title="missHint(tsk)">{{ isNoCopyTask(tsk) ? t('gen.need_desc.no_copy', { n: String(missingDescN(tsk)) }) : t('gen.need_desc', { n: String(missingDescN(tsk)) }) }}</span>
          <div class="g-job">
            <template v-if="!isLive(tsk) && missingDescN(tsk)">
              <button class="btn sm primary" disabled :title="missHint(tsk)">{{ t('gen.ctx.start') }}</button>
              <button class="btn sm" @click.stop="goDescribe(tsk)">{{ isNoCopyTask(tsk) ? t('gen.need_desc.fill') : t('gen.need_desc.go') }}</button>
            </template>
            <button v-else-if="canStart(tsk)" class="btn sm primary"
                    :disabled="posting" @click.stop="startGenerate(tsk.id)">{{ t('gen.ctx.start') }}</button>
            <template v-if="isRunning(tsk)">
              <span class="mono muted">{{ liveJob(tsk.id) && liveJob(tsk.id).id }}</span>
              <button class="btn sm danger" v-if="canStop(tsk)" @click.stop="stopTask(tsk)">{{ t('gen.stop') }}</button>
            </template>
            <template v-else-if="isQueued(tsk)">
              <span class="muted">{{ queuedText(tsk) }}</span>
              <button class="btn sm danger" v-if="canStop(tsk)" @click.stop="stopTask(tsk)">{{ t('gen.stop') }}</button>
            </template>
            <button v-if="isArchived(tsk)" class="btn sm" @click.stop="unfreezeTask(tsk)">{{ t('gen.archive.unfreeze') }}</button>
          </div>
          <button class="g-chev" @click.stop="toggle(tsk)">{{ expanded[tsk.id] ? '▼' : '▶' }}</button>
        </div>
        <div class="g-stale" v-if="isStaleTrack(tsk)">
          <span>{{ staleHint(tsk) }}</span>
          <button class="btn sm" v-if="segsAllImaged(tsk)" @click.stop="useCurrentTrack(tsk)">{{ t('gen.stale_track.use_current') }}</button>
          <button class="btn sm" v-else @click.stop="goTrack">{{ t('gen.stale_track.go') }}</button>
        </div>
        <div class="g-chain" v-if="showChain(tsk)">
          <template v-for="(nd, i) in chainOf(tsk)" :key="nd.s">
            <span class="g-link" v-if="i">──</span>
            <button type="button" class="g-node" :class="[nd.role, {wait: nd.wait, manual: nd.role_manual}]"
                    :title="chainTitle(tsk, nd, i===chainOf(tsk).length-1)"
                    @click.stop="toggleRole(tsk, nd)">
              <i class="dot"></i>s{{ pad3(nd.s) }}<sup v-if="nd.role_manual" class="man">*</sup>
            </button>
          </template>
        </div>
        <div class="g-chain-hint" v-if="showChain(tsk) && chainHint[tsk.id]">{{ chainHint[tsk.id] }}</div>
        <div class="g-ex" v-if="expanded[tsk.id]">
          <div class="g-log">
            <div class="g-exhd">{{ logJobId(tsk) ? logJobId(tsk) : t('gen.log_empty') }}
              <button class="btn ghost xs" v-if="logLines(tsk).length" @click.stop="copyText(logLines(tsk).join('\\n'), logJobId(tsk))">{{ t('log.copy') }}</button>
            </div>
            <pre v-if="logLines(tsk).length">{{ logLines(tsk).join('\\n') }}</pre>
            <div v-else class="muted">{{ t('gen.log_empty') }}</div>
          </div>
          <div class="g-segs">
            <table class="g-stable">
              <thead><tr>
                <th>{{ t('gen.col.s') }}</th>
                <th>{{ t('gen.col.dur') }}</th>
                <th>{{ t('gen.col.img') }}</th>
                <th>{{ t('gen.col.st') }}</th>
                <th>{{ t('gen.col.err') }}</th>
              </tr></thead>
              <tbody>
                <tr v-for="sg in segsOf(tsk)" :key="sg.s">
                  <td class="mono">s{{ pad3(sg.s) }}</td>
                  <td class="mono">{{ sg.duration }}s
                    <div class="aspect-muted" v-if="sg.aspect_label">{{ sg.aspect_label }}</div></td>
                  <td class="mono">{{ sg.image || '' }}</td>
                  <td><span class="tag" :class="'sg-' + (sg.wait_why ? 'waiting' : sg.status)">{{ sg.wait_why || t('gen.seg.' + sg.status) }}</span></td>
                  <td class="err">{{ sg.error || '' }}<span v-if="clipChanged(tsk, sg)" class="muted"> {{ t('gen.archive.changed') }}</span></td>
                </tr>
              </tbody>
            </table>
            <div v-if="!segsOf(tsk).length" class="muted">{{ t('gen.log_empty') }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="g-ft">
    <span class="muted">{{ t('gen.selected', { n: String(selected.length) }) }}</span>
    <button class="btn primary" :disabled="!canStartSel || posting" @click="startSelected">{{ t('gen.start_sel') }}</button>
    <button class="btn" :disabled="!selected.length" @click="stopSelected">{{ t('gen.stop_sel') }}</button>
  </div>

  <ul v-if="menu.show && menu.task" class="g-ctx" ref="menuRef"
      :style="{left: menu.x + 'px', top: menu.y + 'px'}"
      @click.stop>
    <li @click="onMenu('prio-top')">⇧ {{ t('prio.top') }}</li>
    <li @click="onMenu('prio-set')">⚑ {{ t('prio.set') }}</li>
    <li :class="{off: menuOff(menu.task, 'prio-clear')}" @click="onMenu('prio-clear')">⚐ {{ t('prio.clear') }}</li>
    <li class="sep"></li>
    <li :class="{off: menuOff(menu.task, 'start')}" @click="onMenu('start')">{{ t('gen.ctx.start') }}</li>
    <li :class="{off: menuOff(menu.task, 'stop')}" @click="onMenu('stop')">{{ t('gen.ctx.stop') }}</li>
    <li :class="{off: menuOff(menu.task, 'fail')}" @click="onMenu('fail')">{{ t('gen.ctx.redo_fail') }}</li>
    <li :class="{off: menuOff(menu.task, 'fail')}" @click="onMenu('fail-acc')">{{ t('gen.ctx.redo_fail_acc') }}</li>
    <li :class="{off: menuOff(menu.task, 'block')}" @click="onMenu('block')">{{ t('gen.ctx.redo_block') }}</li>
    <li :class="{off: menuOff(menu.task, 'block') || !studioOn()}"
        :title="studioOn() ? '' : t('regen.acc.studio_off_tip')"
        @click="onMenu('block-studio')">{{ t('gen.ctx.block_studio') }}</li>
    <li @click="onMenu('pick')">{{ t('gen.ctx.redo_pick') }}</li>
    <li class="sep"></li>
    <li :class="{off: menuOff(menu.task, 'folder')}" @click="onMenu('folder')">{{ t('gen.ctx.folder') }}</li>
    <li :class="{off: menuOff(menu.task, 'finished')}" @click="onMenu('finished')">{{ t('gen.ctx.finished') }}</li>
    <li :class="{off: menuOff(menu.task, 'trash')}" @click="onMenu('trash')">{{ t('gen.ctx.trash') }}</li>
    <li :class="{off: menuOff(menu.task, 'restore')}" @click="onMenu('restore')">{{ t('gen.ctx.restore') }}</li>
  </ul>

  <div class="dlg-mask" v-if="clearDlg" @click.self="clearDlg=null">
    <div class="dlg">
      <h3>{{ t('gen.archive.need_export', { n: String(clearDlg.need.length) }) }}</h3>
      <p class="muted mono" style="margin:8px 0 14px">{{ clearDlg.need.join('、') }}</p>
      <div class="row" style="justify-content:flex-end">
        <button class="btn" @click="clearDlg=null">{{ t('common.cancel') }}</button>
        <button class="btn primary" @click="exportThenClear">{{ t('gen.archive.export_and_clear') }}</button>
      </div>
    </div>
  </div>

  <div class="dlg-mask" v-if="blockedDlg.on" @click.self="closeBlocked">
    <div class="dlg g-block-dlg">
      <h3>{{ t('gen.blocked_title') }}</h3>
      <div v-if="!blockedDlg.rows.length" class="muted">{{ t('gen.blocked_empty') }}</div>
      <ul class="g-block-list" v-else>
        <li v-for="row in blockedDlg.rows" :key="row.s">
          <div class="g-block-meta">
            <b class="mono">s{{ pad3(row.s) }}</b>
            <span class="mono">{{ row.p || '—' }}</span>
            <span class="muted">{{ t('gen.blocked_tries', { n: String(row.tries || 0) }) }}</span>
            <span class="tag studio" v-if="row.studio_pending">{{ t('gen.blocked_studio_pending') }}</span>
          </div>
          <div class="g-block-why" v-if="row.reason">{{ row.reason }}</div>
          <div class="g-block-acts">
            <button class="btn sm" @click="retryBlocked(row)">{{ t('gen.blocked_retry') }}</button>
            <button class="btn sm studio-btn" :disabled="!studioOn()"
                    :title="studioOn() ? t('gen.blocked_studio_tip') : t('regen.acc.studio_off_tip')"
                    @click="retryBlocked(row, 'STUDIO')">{{ t('gen.blocked_studio') }}</button>
            <button class="btn sm" @click="borrowBlocked(row)">{{ t('gen.blocked_borrow') }}</button>
            <button class="btn sm" @click="goSwapImage(row)">{{ t('gen.blocked_swap') }}</button>
          </div>
        </li>
      </ul>
      <div class="row" style="justify-content:flex-end;margin-top:12px;gap:8px">
        <button v-if="blockedDlg.rows.some(r => r.studio_pending)" class="btn studio-btn" :disabled="!studioOn()"
                :title="t('gen.studio_handoff_tip')" @click="studioHandoffNow">{{ t('gen.studio_handoff_now') }}</button>
        <button class="btn" @click="closeBlocked">{{ t('common.cancel') }}</button>
      </div>
    </div>
  </div>

  <div class="dlg-mask" v-if="redoOn" @click.self="redoOn=false">
    <div class="dlg">
      <h3>{{ t('gen.redo.title') }}</h3>
      <p class="muted">{{ t('gen.redo.hint') }}</p>
      <input class="mono" v-model="redoText" @keydown.enter="confirmRedo" style="width:100%;margin:8px 0 14px">
      <regen-acc-pick :task="redoTask" v-model="redoAcc" :segs="redoSegs"></regen-acc-pick>
      <div class="row" style="justify-content:flex-end">
        <button class="btn" @click="redoOn=false">{{ t('common.cancel') }}</button>
        <button class="btn primary" @click="confirmRedo">{{ t('common.ok') }}</button>
      </div>
    </div>
  </div>

  <div class="g-bench" v-if="bench.on" ref="benchEl" tabindex="0"
       :data-density="bench.density" :class="['den-'+bench.density, {custom: !bench.densityHi}]"
       :style="benchStyle()"
       data-bench-keys="- = 0 1 2 3 J K Space S F Enter Esc"
       @keydown="onBenchKey">
    <div class="bh">
      <h3>{{ t('gen.bench') }}</h3>
      <span class="sum" v-if="bench.summary">
        <span class="r">{{ t('gen.bench.level.red') }} {{ bench.summary.red }}</span>
        · <span class="y">{{ t('gen.bench.level.yellow') }} {{ bench.summary.yellow }}</span>
        · <span class="g">{{ t('gen.bench.level.green') }} {{ bench.summary.green }}</span>
      </span>
      <button class="btn sm" :disabled="bench.busy || !bench.summary.red" @click="benchSwapRed">{{ t('gen.bench.swap_red') }}</button>
      <button class="btn sm" :disabled="bench.busy || !bench.summary.yellow" @click="benchPassYellow">{{ t('gen.bench.pass_yellow') }}</button>
      <button class="btn sm" :disabled="bench.busy || !benchSorted().length" @click="benchPassAll">{{ t('gen.bench.pass_all') }}</button>
      <span class="den">
        <button type="button" class="btn sm" :class="{on: bench.densityHi && bench.density==='compact'}"
                @click="setBenchDensity('compact')">{{ t('gen.bench.density.compact') }}</button>
        <button type="button" class="btn sm" :class="{on: bench.densityHi && bench.density==='comfy'}"
                @click="setBenchDensity('comfy')">{{ t('gen.bench.density.comfy') }}</button>
        <button type="button" class="btn sm" :class="{on: bench.densityHi && bench.density==='large'}"
                @click="setBenchDensity('large')">{{ t('gen.bench.density.large') }}</button>
        <input class="zoom" type="range" min="40" max="200" step="10"
               :value="bench.zoom" @input="setBenchZoom($event.target.value, true)">
        <span class="zoom-n">{{ bench.zoom }}%</span>
      </span>
      <button class="btn sm" :class="{on: bench.keysOn}" @click="bench.keysOn=!bench.keysOn"
              :title="t('gen.bench.keys')">?</button>
      <span class="spacer"></span>
      <button class="btn sm" @click="closeBench">×</button>
    </div>
    <div class="bh2">
      <span class="pos">{{ t('gen.bench.pos', { n: String(benchPos().n), m: String(benchPos().m) }) }}</span>
      <button type="button" class="btn sm" :class="{on: bench.filter==='all'}" @click="setBenchFilter('all')">{{ t('gen.bench.filter.all') }}</button>
      <button type="button" class="btn sm" :class="{on: bench.filter==='red'}" @click="setBenchFilter('red')">{{ t('gen.bench.filter.red') }}</button>
      <button type="button" class="btn sm" :class="{on: bench.filter==='yellow'}" @click="setBenchFilter('yellow')">{{ t('gen.bench.filter.yellow') }}</button>
    </div>
    <div class="bhelp" v-if="bench.keysOn">
      <div><kbd>-</kbd>/<kbd>=</kbd> {{ t('gen.bench.keys.zoom') }}</div>
      <div><kbd>1</kbd><kbd>2</kbd><kbd>3</kbd> {{ t('gen.bench.keys.den') }}</div>
      <div><kbd>J</kbd>/<kbd>K</kbd> {{ t('gen.bench.keys.nav') }}</div>
      <div><kbd>Space</kbd> {{ t('gen.bench.keys.pass') }}</div>
      <div><kbd>S</kbd> {{ t('gen.bench.keys.swap') }}</div>
      <div><kbd>F</kbd> {{ t('gen.bench.keys.single') }}</div>
      <div><kbd>Enter</kbd> {{ t('gen.bench.keys.peek') }}</div>
    </div>
    <div class="bb">
      <div class="empty" v-if="bench.mode!=='anchor_bridge' && bench.mode!=='tail_chain'">{{ t('gen.bench.empty_mode') }}</div>
      <div class="empty" v-else-if="!benchSorted().length">{{ t('gen.bench.empty') }}</div>
      <article v-for="it in benchSorted()" :key="it.s" class="bc"
               :class="[it.level, { 'is-cur': it.s===bench.cursor }]"
               @click="bench.cursor=it.s">
        <div class="bc-hd">
          <span>{{ it.kind==='chain' ? t('gen.bench.card_chain', { s: 's'+pad3(it.s), p: it.image || '' }) : (it.gap==null ? t('gen.bench.card_na', { s: 's'+pad3(it.s) }) : t('gen.bench.card', { s: 's'+pad3(it.s), g: String(it.gap) })) }}</span>
          <span class="eng" v-if="it.engine_label">{{ it.engine_label }}</span>
          <span class="lv">{{ t('gen.bench.level.' + it.level) }}</span>
          <span class="fail" v-if="it.block_why">{{ it.block_why }}</span>
          <span class="stale" v-if="it.stale_tail || it.stale_from">{{ t('gen.bench.stale') }}</span>
          <span class="warn" v-if="it.tail_severe">{{ it.tail_severe }}</span>
          <span class="warn" v-if="it.tail_warn">{{ it.tail_warn }}</span>
          <span class="muted" v-if="it.swapped_from">{{ it.swapped_from }} → {{ it.to_image }}</span>
        </div>
        <div class="tri">
          <div class="cell arrow">
            <div class="cap">{{ (it.kind==='chain' && !it.from_seg) ? (it.src_label || t('gen.chain.start')) : t('gen.bench.tail', { s: it.src_label || ('s'+pad3(it.from_seg||0)) }) }}<small class="muted" v-if="it.tail_image"> = {{ it.tail_image }}</small></div>
            <div class="thumb">
              <img v-if="it.tail_url" :src="it.tail_url" alt="">
              <div class="ph" v-else>—</div>
            </div>
          </div>
          <div class="cell arrow">
            <div class="cap">{{ t('gen.bench.head') }}</div>
            <div class="thumb">
              <img v-if="it.first_url" :src="it.first_url" alt="">
              <div class="ph" v-else>—</div>
            </div>
          </div>
          <div class="cell mid dist" :class="{hot: it.dist_red, warn: it.dist_yellow && !it.dist_red}">
            <div class="cap">{{ it.first_dist==null ? t('gen.bench.dist_na') : t('gen.bench.dist', { n: String(it.first_dist) }) }}</div>
            <div class="dist-n" :class="{hot: it.dist_red, warn: it.dist_yellow && !it.dist_red}">{{ it.first_dist==null ? '—' : it.first_dist }}</div>
            <div class="gap-bars">
              <i v-for="n in 5" :key="n" :class="{on: it.gap!=null && n<=it.gap}"
                 :style="{height: (6+n*5)+'px'}"></i>
            </div>
            <div class="gap-word">{{ gapWord(it.gap) }}</div>
          </div>
        </div>
        <div class="lines">
          <div><b>{{ t('gen.bench.now') }}</b>{{ it.now_text || '—' }}</div>
          <div><b>{{ t('gen.bench.target') }}</b>{{ it.target_text || '—' }}</div>
          <div><b>{{ t('gen.bench.why') }}</b>{{ it.gap_why || '—' }}</div>
          <div><b>{{ t('gen.bench.line') }}</b>{{ it.text || '—' }}</div>
        </div>
        <div class="seg-mode" v-if="bench.mode!=='tail_chain'">
          <button class="btn sm" v-if="it.role==='bridge'" :disabled="bench.busy"
                  :title="t('gen.bench.seg.to_single_hint', { p: it.image || 'p—' })"
                  @click.stop="setSegMode(it, 'single')">{{ t('gen.bench.seg.to_single') }}</button>
          <button class="btn sm" v-else :disabled="bench.busy"
                  :title="t('gen.bench.seg.to_bridge_hint', { s: it.src_label || ('s'+pad3((it.s||1)-1)) })"
                  @click.stop="setSegMode(it, 'bridge')">{{ t('gen.bench.seg.to_bridge') }}</button>
          <small v-if="it.role==='bridge'">{{ t('gen.bench.seg.to_single_hint', { p: it.image || 'p—' }) }}</small>
          <small v-else>{{ t('gen.bench.seg.to_bridge_hint', { s: it.src_label || ('s'+pad3((it.s||1)-1)) }) }}</small>
        </div>
        <div class="seg-mode" v-else>
          <regen-acc-menu btn-class="btn sm" :disabled="bench.busy" :task="benchTask()" :segs="segsOf(bench.tid, [it.s])"
                          @go="acc => chainRegen(it, acc)">{{ t('gen.bench.chain.regen') }}</regen-acc-menu>
          <button class="btn sm" :disabled="bench.busy" @click.stop="setSegMode(it, 'single')">{{ t('gen.bench.chain.restart') }}</button>
          <button class="btn sm" v-if="it.can_cascade" :disabled="bench.busy" @click.stop="chainCascade(it)">{{ t('gen.bench.chain.cascade') }}</button>
        </div>
        <div class="acts">
          <button class="btn sm" :disabled="bench.busy" @click.stop="benchSwap(it)">{{ t('gen.bench.act_swap') }}</button>
          <button class="btn sm" :disabled="bench.busy" @click.stop="openPick(it)">{{ t('gen.bench.act_pick') }}</button>
          <regen-acc-menu btn-class="btn sm" :disabled="bench.busy || !it.from_seg" :task="benchTask()"
                          :segs="segsOf(bench.tid, [it.from_seg])"
                          @go="acc => benchRegen(it, acc)">{{ t('gen.bench.act_regen', { s: it.src_label || '' }) }}</regen-acc-menu>
          <button class="btn sm" :disabled="bench.busy" @click.stop="openPrompt(it)">{{ t('gen.bench.act_prompt') }}</button>
          <button class="btn sm" :disabled="bench.busy || !it.from_seg" @click.stop="openTail(it)">{{ t('gen.bench.act_tail') }}</button>
          <button class="btn sm pass" :disabled="bench.busy || !it.can_pass || it.level==='green'"
                  :title="it.level==='red' && !it.can_pass ? t('gen.bench.pass_blocked') : ''"
                  @click.stop="benchPass(it)">{{ t('gen.bench.act_pass') }}</button>
          <button class="btn sm" v-if="it.can_retry" :disabled="bench.busy" @click.stop="benchRetry(it)">{{ t('gen.bench.act_retry') }}</button>
          <small class="retry-why" v-if="it.can_retry && it.block_why">{{ it.block_why }}</small>
        </div>
      </article>
    </div>

    <div class="bpk" v-if="benchPick.on" @click.self="closePick">
      <div class="box">
        <h4>{{ t('gen.bench.pick_title', { s: 's'+pad3(benchPick.s) }) }}</h4>
        <p class="hint">{{ t('gen.bench.pick_hint') }}</p>
        <div class="bpk-grid">
          <button v-for="im in bench.pool" :key="im.p" type="button" class="pk"
                  :class="{cur: pickItem() && pickItem().to_image===im.p}"
                  :disabled="bench.busy"
                  @mouseenter="hoverScore(im.p)" @click="pickAnchor(im.p)">
            <img :src="im.url" :alt="im.p">
            <span class="pn">{{ im.p }}</span>
            <span class="sc" v-if="scoreOf(im.p) && scoreOf(im.p).loading">…</span>
            <span class="sc" v-else-if="scoreOf(im.p) && scoreOf(im.p).gap!=null"
                  :class="scoreClass(scoreOf(im.p).gap)"
                  :title="scoreOf(im.p).why || ''">{{ scoreOf(im.p).gap }}</span>
            <span class="sc" v-else>{{ t('gen.bench.hover') }}</span>
          </button>
        </div>
      </div>
    </div>

    <div class="btl" v-if="benchTail.on" @click.self="closeTail">
      <div class="box">
        <h4>{{ t('gen.bench.tail_title', { s: 's'+pad3(benchTail.s) }) }}</h4>
        <p class="hint" v-if="benchTail.loading">…</p>
        <div class="prev" v-else>
          <img v-if="tailFrame() && tailFrame().url" :src="tailFrame().url" alt="">
          <div>
            {{ t('gen.bench.tail', { s: String(benchTail.k) }) }}
            <span class="star" v-if="Number(benchTail.k)===benchTail.auto">★ {{ t('gen.bench.tail_auto') }}</span>
          </div>
          <input type="range" min="1" max="15" v-model.number="benchTail.k">
        </div>
        <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="btn" @click="closeTail">{{ t('common.cancel') }}</button>
          <button class="btn primary" :disabled="bench.busy || benchTail.loading" @click="saveTail">{{ t('common.ok') }}</button>
        </div>
      </div>
    </div>

    <div class="bpr" v-if="benchPrompt.on" @click.self="closePrompt">
      <div class="box">
        <h4>{{ t('gen.bench.prompt_title', { s: 's'+pad3(benchPrompt.s) }) }}</h4>
        <textarea class="mono" v-model="benchPrompt.text"></textarea>
        <div class="row" style="justify-content:flex-end;gap:8px">
          <button class="btn" @click="closePrompt">{{ t('review.prompt_cancel') }}</button>
          <button class="btn primary" :disabled="benchPrompt.saving" @click="savePrompt">{{ t('review.prompt_save') }}</button>
        </div>
      </div>
    </div>
  </div>
</section>`,
    util: { parseSegs, bucketCells, placeMenu, statusesFromCounts, eligible, restoreStatus, aheadOf, displayPrio, findInQueue, segsHaveImages, MAX_CELLS },
  };
})();
