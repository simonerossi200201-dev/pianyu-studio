(async () => {
  // i18n 放到 bootstrap 里一起重试：这里 await 失败会连错误页都画不出来。
  const app = Vue.createApp({
    components: { NodeBar, AccountsDrawer, SettingsDrawer, LogDrawer,
      SetupWizard: window.SetupWizard,
      TaskPick: window.TaskPick,
      SuiteBar: window.SuiteBar,
      Panel_tasks, Panel_imgstudio: window.Panel_imgstudio, Panel_gemini, Panel_track, Panel_generate, Panel_review },
    setup() {
      const { computed, ref, onMounted, onUnmounted } = Vue;
      const panels = { tasks: 'Panel_tasks', imgstudio: 'Panel_imgstudio', gemini: 'Panel_gemini', track: 'Panel_track', generate: 'Panel_generate', review: 'Panel_review' };
      const currentPanel = computed(() => panels[ui.node] || 'Panel_tasks');
      const panelKey = computed(() => (ui.node || 'tasks') + '-' + (store.suiteTick || 0));
      const capTid = computed({
        get: () => ui.panelTid || '',
        set: (v) => { ui.panelTid = v || ''; },
      });
      const capTasks = computed(() => (store.tasks || []).filter(x => x && x.status !== 'trash'));
      const activeJobs = computed(() => store.jobs.filter(j => j.status === 'running' || j.status === 'queued' || j.status === 'halted'));
      const creditHalt = computed(() => store.creditHalt);
      async function ackCredit() {
        const h = store.creditHalt || {};
        const jid = h.job_id;
        if (jid) {
          try { await API.post('/api/jobs/' + encodeURIComponent(jid) + '/credit-continue'); }
          catch (e) { /* 接口失败也先把红条收掉 */ }
        }
        store.creditHalt = null;
      }
      const readyAccounts = computed(() => store.accounts.filter(a => a.alive).length);
      const portLabel = computed(() => {
        if (store.primary !== false) return '';
        const p = Number(store.port || 0);
        return p ? (':' + p) : '';
      });
      const cfgNotice = computed(() => store.cfgNotice);
      const cfgNoticeText = computed(() => {
        const n = store.cfgNotice;
        if (!n) return '';
        return n.hint
          ? t('top.cfg_changed_hint', { hint: n.hint })
          : t('top.cfg_changed');
      });
      async function newWindow() {
        try {
          await API.post('/api/system/new-window');
        } catch (e) { /* API 已经 toast */ }
      }
      async function applyCfgRefresh() {
        store.cfgNotice = null;
        store.seenRev = null;   // 当首次加载，刷完记下当前世代，不会立刻再弹
        store.suiteTick = (store.suiteTick || 0) + 1;
        try { await refreshState({ probe: false }); }
        catch (e) { /* API 已经 toast */ }
      }
      const descQueue = computed(() => store.descQueue || { on: false });
      const refreshing = ref(false);
      const refreshOk = ref(false);
      const refreshMenu = ref(false);
      const sseOk = computed(() => store.sseOk !== false);
      let refreshOkTimer = 0;

      function typingTarget(el) {
        if (!el) return false;
        const tag = (el.tagName || '').toLowerCase();
        return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
      }

      async function goNode(id) {
        if (!id || id === ui.node) return;
        if (window.flushAll) await window.flushAll();
        else if (window.flushEdits) await window.flushEdits();
        ui.node = id;
      }

      async function doRefresh(deep) {
        if (refreshing.value) return;
        refreshMenu.value = false;
        refreshing.value = true;
        refreshOk.value = false;
        try {
          if (window.flushAll) await window.flushAll();
          else if (window.flushEdits) await window.flushEdits();
          const tid = (ui.panelTid || '').trim();
          const r = await API.post('/api/refresh', { deep: !!deep, tid });
          // 浅层不探端口：/api/state 默认会连每个 Chrome，死端口每个 0.4s
          await refreshState({ probe: !!deep });
          const es = window._es;
          if (!store.sseOk || !es || es.readyState !== EventSource.OPEN) {
            connectEvents(true);
          }
          store.refreshSeq = (store.refreshSeq || 0) + 1;
          if (deep) {
            toast(t('top.refresh_deep_toast', {
              n: String(r.frames_recomputed || 0),
              acc: r.accounts_alive || '0/0',
            }), '', 'ok');
          }
          refreshOk.value = true;
          if (refreshOkTimer) clearTimeout(refreshOkTimer);
          refreshOkTimer = setTimeout(() => { refreshOk.value = false; }, 800);
        } catch (e) { /* API 已经 toast */ } finally {
          refreshing.value = false;
        }
      }

      function onRefreshKey(ev) {
        const isF5 = ev.key === 'F5';
        const isCtrlR = (ev.ctrlKey || ev.metaKey) && !ev.altKey && !ev.shiftKey
          && (ev.key === 'r' || ev.key === 'R');
        if (!isF5 && !isCtrlR) return;
        // F5 输入框里也走刷新（提示词框敲完直接 F5 要存字）；Ctrl+R 输入时不抢
        if (isCtrlR && typingTarget(ev.target)) return;
        ev.preventDefault();
        doRefresh(false);
      }
      function onDocClick() { refreshMenu.value = false; }

      function openRescan() {
        if (window.Rescan) window.Rescan.open(ui.panelTid);
      }

      async function bootRetry() {
        store.boot.status = 'connecting';
        store.boot.attempt = 0;
        store.boot.copied = false;
        const ok = await bootstrap();
        if (ok) connectEvents();
      }
      async function bootOpenLogs() {
        try {
          if (window.pywebview && window.pywebview.api && window.pywebview.api.open_logs) {
            await window.pywebview.api.open_logs();
            return;
          }
        } catch (e) { /* 没有 js_api 就走 HTTP */ }
        try {
          const r = await fetch('/api/system/open-logs', { method: 'POST' });
          if (r.ok) return;
        } catch (e) { /* 后端也连不上：把路径交给用户 */ }
        const p = (store.root ? (String(store.root).replace(/[\\/]+$/, '') + '/data/logs') : 'data\\logs');
        copyText(p, '日志路径');
      }
      function bootCopyDiag() {
        const text = [
          (typeof location !== 'undefined' ? location.href : ''),
          store.boot && store.boot.error ? store.boot.error : '',
          (typeof navigator !== 'undefined' ? navigator.userAgent : ''),
          new Date().toISOString(),
        ].join('\n');
        copyText(text, '诊断信息');
        // 错误页盖住 toast，成功字写在块里
        if (store.boot) store.boot.copied = true;
      }

      function onAppUnload() {
        // beforeunload / pagehide 里 await fetch 会被浏览器丢掉，只发 beacon
        try { if (window.__beaconFlush) window.__beaconFlush(); }
        catch (e) { /* 关页时尽力发出去 */ }
      }
      // 节点灯：这一步能进就亮。悬停看还缺什么。
      const lamps = computed(() => {
        const ts = (store.tasks || []).filter(x => x.status !== 'trash');
        const busyKinds = new Set(activeJobs.value.map(j => j.kind));
        const order = ['draft','prepared','described','arranged','generating','collected','finished'];
        const at = (x, s) => order.indexOf(x.status) >= order.indexOf(s);
        const lit = (can, busyKind) => busyKinds.has(busyKind) ? 'busy' : (can ? 'done' : 'idle');
        return {
          tasks: lit(ts.length > 0, 'prepare'),
          imgstudio: (store.imgStudioBusy ? 'busy' : 'done'),
          gemini: (busyKinds.has('describe') || (store.descQueue && store.descQueue.on))
            ? 'busy' : lit(ts.some(x => at(x, 'prepared')), 'describe'),
          track: lit(ts.some(x => at(x, 'prepared')), 'arrange'),
          generate: busyKinds.has('generate') ? 'busy' : lit(ts.some(x => at(x, 'arranged')), 'generate'),
          review: (busyKinds.has('check') || busyKinds.has('regenerate'))
            ? 'busy' : lit(ts.some(x => at(x, 'generating')), 'check'),
        };
      });
      
      const nodeSubs = computed(() => {
        const ts = (store.tasks || []).filter(x => x.status !== 'trash');
        const tid = (ui.panelTid || '').trim();
        const cur = (tid && ts.find(x => x.id === tid)) || ts[0] || null;
        if (!cur) return {};
        // 规格 86 收窄：节点「可跳过」只认没有文案
        const noCopy = !String(cur.copy || '').trim();
        if (!noCopy) return {};
        return {
          gemini: t('node.gemini.sub_skip'),
          track: t('node.track.sub_skip'),
        };
      });
const lampTips = computed(() => {
        const ts = (store.tasks || []).filter(x => x.status !== 'trash');
        const order = ['draft','prepared','described','arranged','generating','collected','finished'];
        const at = (x, s) => x && order.indexOf(x.status) >= order.indexOf(s);
        const tid = (ui.panelTid || '').trim();
        const cur = (tid && ts.find(x => x.id === tid)) || ts[0] || null;
        if (!cur) {
          const empty = t('node.tip.no_task');
          return { tasks: empty, imgstudio: t('node.tip.imgstudio_ok'), gemini: empty, track: empty, generate: empty, review: empty };
        }
        const miss = Number((cur.counts && cur.counts.desc_missing) || 0);
        const ready = !!(cur.counts && cur.counts.images_ready);
        const tip = {};
        tip.tasks = at(cur, 'prepared') ? t('node.tip.prepared') : t('node.tip.need_prepare');
        tip.imgstudio = t('node.tip.imgstudio_ok');
        if (!at(cur, 'prepared')) tip.gemini = t('node.tip.need_prepare');
        else if ((!(String(cur.copy||'').trim()) || Number((cur.counts&&cur.counts.subjects)||0)===1)) tip.gemini = t('node.tip.gemini_skip');
        else if (miss > 0) tip.gemini = t('node.tip.need_desc', { n: String(miss) });
        else tip.gemini = t('node.tip.desc_ok');
        if (!at(cur, 'prepared')) tip.track = t('node.tip.need_prepare');
        else if ((!(String(cur.copy||'').trim()) || Number((cur.counts&&cur.counts.subjects)||0)===1) && at(cur, 'arranged')) tip.track = t('node.tip.track_skip');
        else if (!ready && !at(cur, 'arranged')) tip.track = t('node.tip.need_arrange');
        else if (miss > 0) tip.track = t('node.tip.track_ok_desc', { n: String(miss) });
        else tip.track = t('node.tip.track_ok');
        if (!at(cur, 'arranged') && !ready) tip.generate = t('node.tip.need_arrange');
        else if (miss > 0) tip.generate = t('node.tip.need_desc', { n: String(miss) });
        else if (!at(cur, 'collected')) tip.generate = t('node.tip.need_gen');
        else tip.generate = t('node.tip.gen_ok');
        if (!at(cur, 'generating')) tip.review = t('node.tip.need_gen');
        else if (!at(cur, 'finished')) tip.review = t('node.tip.need_review');
        else tip.review = t('node.tip.review_ok');
        return tip;
      });
      onMounted(() => {
        window.addEventListener('keydown', onRefreshKey);
        document.addEventListener('click', onDocClick);
        window.addEventListener('beforeunload', onAppUnload);
        window.addEventListener('pagehide', onAppUnload);
      });
      onUnmounted(() => {
        window.removeEventListener('keydown', onRefreshKey);
        document.removeEventListener('click', onDocClick);
        window.removeEventListener('beforeunload', onAppUnload);
        window.removeEventListener('pagehide', onAppUnload);
        if (refreshOkTimer) clearTimeout(refreshOkTimer);
      });
      return {
        store, ui, t, currentPanel, panelKey, capTid, capTasks, activeJobs, readyAccounts, lamps, lampTips, nodeSubs, descQueue,
        refreshing, refreshOk, refreshMenu, sseOk, doRefresh, goNode,
        creditHalt, ackCredit, portLabel, cfgNotice, cfgNoticeText, newWindow, applyCfgRefresh,
        rescan: window.Rescan, srcWithV: window.srcWithV, openRescan,
        bootRetry, bootOpenLogs, bootCopyDiag,
        runToastAction: window.runToastAction,
        showSetup: computed(() => !!ui.showSetup),
      };
    }
  });
  // 先 mount 再连后端：连不上时错误页才能画出来，不能再空壳。
  app.mount('#app');
  try {
    const ok = await bootstrap();
    if (ok) {
      if (store.start_node && ['tasks','imgstudio','gemini','track','generate','review'].includes(store.start_node)) {
        ui.node = store.start_node;
      }
      connectEvents();
      try {
        const st = await API.get('/api/setup/state', { silent: true });
        store.legacy_available = !!st.legacy_available;
        if (st.need_setup) ui.showSetup = true;
      } catch (e) { /* 向导状态读失败不挡主界面 */ }
    }
  } catch (e) {
    store.boot.status = 'error';
    store.boot.error = (e && e.message) || String(e);
  }
})();
