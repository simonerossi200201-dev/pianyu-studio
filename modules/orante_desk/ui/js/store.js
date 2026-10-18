// 全局状态 + SSE。所有面板共用 window.store。
window.store = Vue.reactive({
  version: '', accounts: [], studio: null, tasks: [], settings: {}, defaults: {}, jobs: [], i18n: {}, root: '',
  flow_models: [],
  legacy_available: false,
  creditHalt: null,    // {job_id, b0, b, message, recent}
  flowThrottle: null,  // {account_id, account_name, until, network, alts, task_id}
  network_throttled: false,
  logs: {},            // job_id -> [lines]
  verify: {},          // tid -> {running, done, total, results, at}
  descQueue: { on: false, paused: false, done: 0, total: 0, current: null, results: [], summary: '' },
  loaded: false,
  sseOk: true,         // SSE 断了顶栏刷新按钮变琥珀
  boot: {              // 起动连接：connecting | error | ok。绝不能默默空着
    status: 'connecting',
    attempt: 1,
    url: (typeof location !== 'undefined' && location.origin && String(location.origin).indexOf('http') === 0)
      ? location.origin : 'http://127.0.0.1:9400',
    error: '',
  },
  refreshSeq: 0,       // 全局刷新成功后 +1，各面板自己重新拉数据
  suiteTick: 0,        // 切套装后 +1，主面板强制重挂，避免界面还是旧配置
  segAt: {},           // task_id -> 上次段计数变化的时间戳（生成面板「没动静」用）
  port: 0,             // 本窗口端口
  primary: true,       // 绑的是配置里的 ui.port → 标题不带端口
  seenRev: null,       // 上次本窗口认过的 settings._rev
  cfgNotice: null,     // {rev, hint} 别的窗口改了配置，不要自动刷
});

// 就地改 counts，不整页重刷。生成面板折叠行方块靠这份。
window.applyTaskCounts = (tid, counts) => {
  if (!tid || !counts) return;
  const t = (store.tasks || []).find(x => x.id === tid);
  const prev = t && t.counts;
  const keys = ['planned', 'submitted', 'collected', 'failed', 'blocked', 'total', 'waiting'];
  const cellsChanged = JSON.stringify((prev && prev.cells) || []) !== JSON.stringify(counts.cells || []);
  const changed = !prev || cellsChanged || keys.some(k => Number(prev[k] || 0) !== Number(counts[k] || 0));
  if (t) t.counts = counts;
  // 没变就不刷新时间戳，不然 10 秒轮询会把「90 秒没动静」永远冲掉
  if (changed) store.segAt[tid] = Date.now();
};

function _fetchCountsSilent(tid) {
  if (!tid) return;
  fetch('/api/tasks/' + encodeURIComponent(tid) + '/counts').then(r => r.json()).then(d => {
    if (d && d.ok && d.counts) applyTaskCounts(tid, d.counts);
  }).catch(() => {});
}

// 各面板挂自己的 flush：刷新 / 切面板 / 关窗口前先把没存的字写盘
window._flushers = {};
window.registerFlush = (name, fn) => { window._flushers[name] = fn; };
window.unregisterFlush = (name) => { delete window._flushers[name]; };
window._beaconFlushers = {};
window.registerBeaconFlush = (name, fn) => { window._beaconFlushers[name] = fn; };
window.unregisterBeaconFlush = (name) => { delete window._beaconFlushers[name]; };
window.__beaconFlush = () => {
  Object.values(window._beaconFlushers || {}).forEach(fn => {
    try { fn(); } catch (e) { /* 单个 beacon 失败不挡其余 */ }
  });
};
window.flushEdits = async () => {
  try {
    const el = document.activeElement;
    if (el && el !== document.body && typeof el.blur === 'function') el.blur();
  } catch (e) { /* 失焦失败不挡刷新 */ }
  await new Promise(r => setTimeout(r, 50));
  const fns = Object.values(window._flushers || {});
  await Promise.all(fns.map(fn => Promise.resolve(fn()).catch(() => {})));
};
// 刷新 / 切面板走 await fetch，不要顺手 beacon：没 id 的草稿会被建成两份。
// beforeunload 才 beacon（fetch 在卸载时不可靠）。pywebview 关窗调 __flushAll 等 fetch。
window.__flushDone = true;
window.flushAll = async () => {
  if (window.flushEdits) await window.flushEdits();
};
window.__flushAll = () => {
  window.__flushDone = false;
  Promise.resolve(window.flushAll()).catch(() => {}).finally(() => { window.__flushDone = true; });
  return true;
};
window.t = (k, vars) => {
  let s = store.i18n && store.i18n[k];
  if (s === undefined && store.i18nZh) s = store.i18nZh[k];
  if (s === undefined) {
    // 设置项中文名/说明缺翻译时回落到中文；都没有也不许把键名当标签
    if (String(k).indexOf('set.key.') === 0 || String(k).indexOf('set.hint.') === 0) return '';
    return k;
  }
  if (vars) for (const [a, b] of Object.entries(vars)) s = s.replaceAll('{' + a + '}', b);
  return s;
};
window.ui = Vue.reactive({ node: 'tasks', drawer: null, toasts: [], panelTid: '', pendingTid: '', showSetup: false });

// 当前任务落盘：panelTid 一变就写 config，刷新 / 重启从 settings 读回。
window.persistPanelTid = (id) => {
  const v = String(id || '');
  if (!store.settings) store.settings = {};
  if (store.settings['ui.current_task'] === v) return;
  store.settings['ui.current_task'] = v;
  if (window.API && API.post) {
    API.post('/api/settings', { set: { 'ui.current_task': v } }).catch(() => {});
  }
};
window.setPanelTid = (id) => {
  const v = String(id || '');
  if (ui.panelTid === v) return;
  ui.panelTid = v;
  persistPanelTid(v);
};
if (Vue.watch) {
  Vue.watch(() => ui.panelTid, (v) => persistPanelTid(v));
}
// 复制一段文字（日志等）。剪贴板 API 不可用时退回隐藏 textarea + execCommand。
window.copyText = (text, label) => {
  const body = String(text == null ? '' : text);
  if (!body.trim()) { toast(t('log.copy_empty')); return; }
  const n = body.split('\n').filter(x => x !== '').length;
  const done = () => toast(t('log.copied', { n: String(n) }), label || '', 'ok');
  const fallback = () => {
    try {
      const ta = document.createElement('textarea');
      ta.value = body; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
      done();
    } catch (e) { toast(t('log.copy_fail'), '', 'bad'); }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(body).then(done).catch(fallback);
  else fallback();
};
let _tid = 0;
window.toast = (text, hint = '', kind = '', actions, ms) => {
  const id = ++_tid;
  const rec = { id, text, hint, kind, actions: Array.isArray(actions) ? actions : [] };
  ui.toasts.push(rec);
  const dur = (typeof ms === 'number' && ms >= 0) ? ms
    : (rec.actions.length ? 20000 : (kind === 'bad' ? 7000 : 3200));
  rec.timer = setTimeout(() => window.dismissToast(id), dur);
};
window.dismissToast = (id) => {
  const i = ui.toasts.findIndex(x => x.id === id);
  if (i < 0) return;
  const rec = ui.toasts[i];
  if (rec && rec.timer) clearTimeout(rec.timer);
  ui.toasts.splice(i, 1);
};
window.runToastAction = (m, a) => {
  if (a && typeof a.run === 'function') a.run();
  if (m) window.dismissToast(m.id);
};

window.applyWindowTitle = () => {
  try {
    if (typeof document === 'undefined') return;
    if (store.primary !== false || !store.port) document.title = 'Orante 制片台';
    else document.title = 'Orante 制片台 · :' + store.port;
  } catch (e) { /* 标题失败不挡 */ }
};
window.applyCfgRev = (s) => {
  const rev = Number((s && s.settings && s.settings._rev) || 0);
  const fromPort = Number((s && s.settings && s.settings._rev_port) || 0);
  if (store.seenRev == null) {
    store.seenRev = rev;
    return;
  }
  if (rev === store.seenRev) return;
  const mine = Number(store.port || 0);
  // 本窗口自己写的不弹「另一个窗口改了配置」；别的窗口才弹。
  if (fromPort && mine && fromPort === mine) {
    store.seenRev = rev;
    return;
  }
  store.cfgNotice = {
    rev,
    hint: (s.settings && s.settings._rev_hint) || '',
  };
};

window.refreshState = async (opts) => {
  const q = (opts && opts.probe === false) ? '?probe=0' : '';
  const silent = !!(opts && opts.silent);
  const s = await API.get('/api/state' + q, { silent });
  Object.assign(store, { version: s.version, accounts: s.accounts, tasks: s.tasks, settings: s.settings, jobs: s.jobs, root: s.root,
    flow_models: s.flow_models || store.flow_models || [],
    legacy_available: !!s.legacy_available,
    port: Number(s.port || 0),
    primary: s.primary !== false,
    network_throttled: !!s.network_throttled });
  if (s.studio) store.studio = s.studio;   // 规格 98：本地模型状态（不在 accounts 里）
  if (s.flow_throttle && s.flow_throttle.on) {
    store.flowThrottle = Object.assign({}, store.flowThrottle || {}, {
      network: true,
      until: s.flow_throttle.until,
      until_ts: s.flow_throttle.until_ts,
    });
  }
  store.loaded = true;
  applyCfgRev(s);
  applyWindowTitle();
  // 重启 / 刷新：当前任务从 config 读回，各面板挂载时再接。
  const saved = String((s.settings && s.settings['ui.current_task']) || '');
  if (saved) ui.panelTid = saved;
  try {
    const q = await API.get('/api/queue/describe', { silent: true });
    store.descQueue = {
      on: !!q.on, paused: !!q.paused, done: q.done || 0, total: q.total || 0,
      current: q.current || null, results: q.results || [],
      job_id: q.job_id || null, summary: q.summary || '',
      owner_port: q.owner_port || 0, foreign: !!q.foreign,
    };
  } catch (e) { /* 队列接口挂了不挡刷新 */ }
};

// 起动重试间隔：0.3 / 0.6 / 1.2 / 2.4 / 4s，之后一直 4s，最多 20 秒。
window.BOOT_INTERVALS = [300, 600, 1200, 2400, 4000];
window.BOOT_TIMEOUT_MS = 20000;

window._loadI18nSilent = async (lang) => {
  try {
    if (!store.i18nZh || !store.i18nZh['app.version']) {
      const rz = await fetch('/api/i18n/zh');
      if (rz.ok) store.i18nZh = await rz.json();
    }
    const want = lang || (store.settings && store.settings['ui.lang']) || 'zh';
    if (!want || want === 'zh') {
      store.i18n = store.i18nZh || {};
      return;
    }
    const r = await fetch('/api/i18n/' + encodeURIComponent(want));
    store.i18n = r.ok ? await r.json() : (store.i18nZh || {});
  } catch (e) { /* 连不上时用硬编码文案 */ }
};

window.bootstrap = async (opts) => {
  // 首次 /api/state 失败必须重试，否则 store.loaded 永远 false，窗口就是空壳。
  const timeout = (opts && opts.timeoutMs != null) ? opts.timeoutMs : window.BOOT_TIMEOUT_MS;
  const intervals = (opts && opts.intervals) || window.BOOT_INTERVALS;
  const now = (opts && opts.now) || (() => Date.now());
  const sleep = (opts && opts.sleep) || (ms => new Promise(r => setTimeout(r, ms)));
  store.boot = store.boot || {};
  store.boot.status = 'connecting';
  store.boot.attempt = 0;
  store.boot.error = '';
  if (typeof location !== 'undefined' && location.origin && String(location.origin).indexOf('http') === 0) {
    store.boot.url = location.origin;
  } else if (!store.boot.url) {
    store.boot.url = 'http://127.0.0.1:9400';
  }
  const start = now();
  let lastErr = '';
  while (now() - start < timeout) {
    store.boot.attempt += 1;
    store.boot.status = 'connecting';
    try {
      await window._loadI18nSilent();
      await refreshState({ silent: true, probe: false });
      await window._loadI18nSilent(store.settings && store.settings['ui.lang']);
      store.boot.status = 'ok';
      store.boot.error = '';
      return true;
    } catch (e) {
      lastErr = (e && e.message) || String(e);
      store.boot.error = lastErr;
      const remain = timeout - (now() - start);
      if (remain <= 0) break;
      const idx = Math.min(store.boot.attempt - 1, intervals.length - 1);
      await sleep(Math.min(intervals[idx], remain));
    }
  }
  store.boot.status = 'error';
  store.boot.error = lastErr || 'timeout';
  store.loaded = false;
  return false;
};
window.refreshTasks = async () => { store.tasks = (await API.get('/api/tasks')).tasks; };
window.refreshAccounts = async () => {
  const r = await API.get('/api/accounts');
  store.accounts = r.accounts;
  if (r.studio) store.studio = r.studio;
  if (r.flow_models) store.flow_models = r.flow_models;
};

window._es = null;
window._sseWasOpen = false;
window.connectEvents = (force) => {
  if (window._es && !force && window._es.readyState !== EventSource.CLOSED) return;
  if (window._es) {
    try { window._es.close(); } catch (e) { /* 旧连接已经死了 */ }
    window._es = null;
  }
  const es = new EventSource('/api/events');
  window._es = es;
  es.onopen = () => {
    const reopen = !!window._sseWasOpen;
    store.sseOk = true;
    window._sseWasOpen = true;
    // 断线重连：2 秒内用任务列表里的 counts 追上真实进度
    if (reopen && store.loaded) refreshTasks().catch(() => {});
  };
  es.onerror = () => {
    store.sseOk = false;
    // 断线重连：曾经打开过就只把顶栏按钮变琥珀（原逻辑），不盖错误页
    if (window._sseWasOpen) return;
    // 首次建连失败：bootstrap 还在连就别抢；否则走同一套提示，别静默
    if (store.boot && store.boot.status === 'connecting') return;
    if (store.boot && !store.loaded) {
      store.boot.status = 'error';
      store.boot.error = store.boot.error || '实时通道（SSE）连不上';
    }
  };
  es.addEventListener('job.log', e => {
    const d = JSON.parse(e.data);
    (store.logs[d.job_id] ||= []).push(d.line);
    if (store.logs[d.job_id].length > 3000) store.logs[d.job_id].splice(0, 500);
  });
  es.addEventListener('flow.credit_halt', e => {
    const d = JSON.parse(e.data);
    store.creditHalt = d;
  });
  es.addEventListener('flow.throttled', e => {
    const d = JSON.parse(e.data);
    store.flowThrottle = d;
    store.network_throttled = !!(d && d.network);
  });
  es.addEventListener('job.state', e => {
    const j = JSON.parse(e.data).job;
    const i = store.jobs.findIndex(x => x.id === j.id);
    if (i >= 0) store.jobs[i] = j; else store.jobs.push(j);
    const qOn = store.descQueue && store.descQueue.on && j.kind === 'describe';
    if (j.status === 'halted') {
      store.creditHalt = Object.assign({ job_id: j.id, task_id: j.task_id, message: j.error || '' }, j.credit_halt || {});
    }
    if (j.flow_throttle) {
      store.flowThrottle = Object.assign({ job_id: j.id, task_id: j.task_id }, j.flow_throttle);
      store.network_throttled = !!j.flow_throttle.network;
    }
    if (j.status === 'failed' && !qOn) toast(`${j.kind} 失败`, j.error || '', 'bad');
    if (j.status === 'halted' && !qOn) toast(t('job.halted'), j.error || '', 'bad');
    if (j.status === 'done' && j.kind !== 'noop' && !qOn) {
      if (j.kind === 'prepare' && j.result) {
        const n = j.result.subjects != null ? j.result.subjects : (j.result.images || 0);
        const m = j.result.brolls || 0;
        toast(`${j.kind} 完成`, t('tasks.prepare_result', { n: String(n), m: String(m) }), 'ok');
      } else {
        toast(`${j.kind} 完成`, '', 'ok');
      }
    }
    // Job 结束兜底：强制拉一次 counts，不依赖面板是否展开
    const ended = j.status === 'done' || j.status === 'failed' || j.status === 'cancelled' || j.status === 'lost';
    // halted 还占着账号（等「我知道，继续」），不当结束
    const genKind = j.kind === 'generate' || j.kind === 'regenerate' || j.kind === 'refill';
    if (ended && genKind && j.task_id) _fetchCountsSilent(j.task_id);
  });
  es.addEventListener('queue.describe', e => {
    const d = JSON.parse(e.data);
    store.descQueue = {
      on: !!d.on,
      paused: !!d.paused,
      done: d.done || 0,
      total: d.total || 0,
      current: d.current || null,
      results: d.results || [],
      job_id: d.job_id || null,
      summary: d.summary || '',
      owner_port: d.owner_port || 0,
      foreign: !!d.foreign,
    };
  });
  es.addEventListener('task.update', e => {
    const d = JSON.parse(e.data);
    applyTaskCounts(d.task_id, d.counts);
  });
  es.addEventListener('seg.changed', e => {
    const d = JSON.parse(e.data);
    applyTaskCounts(d.task_id, d.counts);
  });
  es.addEventListener('tasks.changed', () => refreshTasks());
  es.addEventListener('accounts.changed', () => refreshAccounts());
  es.addEventListener('toast', e => { const d = JSON.parse(e.data); toast(d.text, d.hint, d.level || d.kind); });
  es.addEventListener('image.replaced', e => {
    const d = JSON.parse(e.data) || {};
    const p = d.p || '';
    const kept = Array.isArray(d.kept) ? d.kept : [];
    const reset = Array.isArray(d.reset) ? d.reset : [];
    const nKept = kept.length;
    const ss = reset.map(n => 's' + String(n).padStart(3, '0')).join(' ');
    const text = t('track.refill_swapped', { p });
    let hint = '';
    if (nKept) hint = t('track.refill_kept_hint', { n: String(nKept) });
    else if (reset.length) hint = t('track.refill_reset_hint', { ss });
    const actions = nKept ? [
      {
        label: t('track.refill_redo_kept', { n: String(nKept) }),
        kind: 'primary',
        run: async () => {
          try {
            await API.post(
              '/api/tasks/' + encodeURIComponent(d.task_id)
              + '/image/' + encodeURIComponent(p) + '/reset-refs',
              { only_kept: true });
            store.refreshSeq = (store.refreshSeq || 0) + 1;
            toast(t('track.refill_redo_ok', { n: String(nKept) }), '', 'ok');
          } catch (err) { /* API 已经 toast */ }
        },
      },
      { label: t('track.refill_leave'), run: () => {} },
    ] : [];
    toast(text, hint, 'ok', actions);
  });
  es.addEventListener('review.verifying', e => {
    const d = JSON.parse(e.data);
    if (!d.tid) return;
    store.verify[d.tid] = {
      running: true,
      done: d.done || 0,
      total: d.total || 0,
    };
  });
  es.addEventListener('review.verified', e => {
    const d = JSON.parse(e.data);
    if (!d.tid) return;
    const n = (d.results || []).length;
    store.verify[d.tid] = {
      running: false,
      done: n,
      total: n,
      results: d.results || [],
      at: Date.now(),
    };
  });
  API.get('/api/queue/describe').then(d => {
    store.descQueue = {
      on: !!d.on, paused: !!d.paused, done: d.done || 0, total: d.total || 0,
      current: d.current || null, results: d.results || [],
      job_id: d.job_id || null, summary: d.summary || '',
      owner_port: d.owner_port || 0, foreign: !!d.foreign,
    };
  }).catch(() => {});
};

// 多开：轻量轮询 _rev / 账号占用 / 描述语队列归属。不要整页刷 settings。
if (typeof window !== 'undefined' && typeof setInterval === 'function' && !window._revPoll) {
  window._revPoll = setInterval(() => {
    if (!store.loaded) return;
    API.get('/api/state?probe=0', { silent: true }).then(s => {
      if (!s) return;
      applyCfgRev(s);
      if (s.accounts) store.accounts = s.accounts;
      if (s.studio) store.studio = s.studio;
      if (s.jobs) store.jobs = s.jobs;
      if (s.port != null) store.port = Number(s.port || 0);
      if (s.primary != null) store.primary = s.primary !== false;
    }).catch(() => {});
    API.get('/api/queue/describe', { silent: true }).then(d => {
      if (!d) return;
      const cur = store.descQueue || {};
      store.descQueue = Object.assign({}, cur, {
        on: !!d.on, paused: !!d.paused, done: d.done || 0, total: d.total || 0,
        current: d.current || null, results: d.results || cur.results || [],
        job_id: d.job_id || null, summary: d.summary || '',
        owner_port: d.owner_port || 0, foreign: !!d.foreign,
      });
    }).catch(() => {});
  }, 3000);
}
