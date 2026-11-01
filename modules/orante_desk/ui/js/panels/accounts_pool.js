// 节点 0 多账号面板：对标 Google Sheet + Hedra 账号管理器的分组轮换与多账号中心。
window.Panel_accounts_pool = (() => {
  const GROUP_COLORS = [
    '#d4a24c', '#3b82f6', '#10b981', '#a855f7',
    '#ec4899', '#f97316', '#06b6d4', '#84cc16',
  ];
  const DEFAULT_MODELS = [
    'Veo 3.1 - Lite [Lower Priority]',
    'Omni 1.1 Flash',
    'Veo 3.1 - Lite',
    'Veo 3.1 - Fast',
    'Veo 3.1 - Quality',
  ];

  function fmtTimeAgo(ts) {
    if (!ts) return '未登录';
    const n = typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(ts);
    if (!Number.isFinite(n) || n <= 0) return String(ts);
    const diff = Math.max(0, Math.floor((Date.now() - n) / 1000));
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    return Math.floor(diff / 86400) + ' 天前';
  }

  return {
    name: 'Panel_accounts_pool',
    setup() {
      const { ref, reactive, computed, onMounted, onBeforeUnmount, watch } = Vue;

      const loading = ref(false);
      const searchQuery = ref('');
      const activeFilter = ref('all'); // 'all' | 'fav' | 'ungrouped' | group_id
      const selectedIds = reactive(new Set());
      const totpMap = reactive({}); // id -> { code, remaining_seconds }
      const collapsedMap = reactive({}); // gid -> boolean
      const fileInputEl = ref(null);

      // Mouse drag-select state for checkboxes
      const dragSel = reactive({ active: false, targetVal: true });

      // Modals
      const addModal = reactive({
        open: false,
        text: '',
        group_id: '',
        flow_mode: 'frames',
        model: 'Veo 3.1 - Lite [Lower Priority]',
        busy: false,
      });

      const editAccModal = reactive({
        open: false,
        id: '',
        email: '',
        password: '',
        twoFactorCode: '',
        group_id: '',
        flow_mode: 'frames',
        model: 'Veo 3.1 - Lite [Lower Priority]',
        flow_credits: '',
        is_favorite: false,
        is_paid: false,
        note: '',
        busy: false,
      });

      const groupModal = reactive({
        open: false,
        isEdit: false,
        id: '',
        name: '',
        color: '#d4a24c',
        url: '',
        flow_mode: 'frames',
        model: 'Veo 3.1 - Lite [Lower Priority]',
        busy: false,
      });

      const batchModal = reactive({
        open: false,
        group_id: '__keep__',
        flow_mode: '__keep__',
        model: '__keep__',
        is_favorite: '__keep__',
        busy: false,
      });

      const logModal = reactive({
        open: false,
        acc: null,
      });

      const availableModels = computed(() => {
        const list = (store.flow_models || []).map(m => (typeof m === 'string' ? m : m.id)).filter(Boolean);
        for (const m of DEFAULT_MODELS) {
          if (!list.includes(m)) list.push(m);
        }
        return list;
      });

      const accounts = computed(() => store.multiAccounts || []);
      const groups = computed(() => store.multiAccountGroups || []);
      const cacheStats = computed(() => store.multiCacheStats || {});

      const groupById = computed(() => {
        const m = {};
        for (const g of groups.value) {
          if (g && g.id) m[g.id] = g;
        }
        return m;
      });

      const filteredAccounts = computed(() => {
        const q = searchQuery.value.trim().toLowerCase();
        return accounts.value.filter(a => {
          if (!a) return false;
          if (activeFilter.value === 'fav' && !(a.is_favorite || a.is_paid)) return false;
          if (activeFilter.value === 'ungrouped' && a.group_id && groupById.value[a.group_id]) return false;
          if (activeFilter.value !== 'all' && activeFilter.value !== 'fav' && activeFilter.value !== 'ungrouped') {
            if (a.group_id !== activeFilter.value) return false;
          }
          if (q) {
            const gName = (groupById.value[a.group_id] && groupById.value[a.group_id].name) || '';
            const hay = `${a.id || ''} ${a.email || ''} ${a.note || ''} ${(a.tags || []).join(' ')} ${gName}`.toLowerCase();
            if (!hay.includes(q)) return false;
          }
          return true;
        });
      });

      const groupedSections = computed(() => {
        const list = filteredAccounts.value;
        const byGid = {};
        const ungrouped = [];
        for (const a of list) {
          const gid = a.group_id && groupById.value[a.group_id] ? a.group_id : '';
          if (gid) {
            (byGid[gid] ||= []).push(a);
          } else {
            ungrouped.push(a);
          }
        }
        const out = [];
        for (const g of groups.value) {
          if (!g || !g.id) continue;
          const accs = byGid[g.id] || [];
          if (activeFilter.value !== 'all' && activeFilter.value !== g.id && !accs.length) continue;
          const totalCredits = accs.reduce((s, x) => s + (Number(x.flow_credits) > 0 ? Number(x.flow_credits) : 0), 0);
          const openCount = accs.filter(x => x.browser_open || x.status === 'open' || x.status === 'ready').length;
          out.push({
            id: g.id,
            name: g.name,
            color: g.color || '#d4a24c',
            url: g.url || '',
            flow_mode: g.flow_mode || 'frames',
            model: g.model || 'Veo 3.1 - Lite [Lower Priority]',
            isUngrouped: false,
            accounts: accs,
            totalCredits,
            openCount,
            collapsed: collapsedMap[g.id] != null ? !!collapsedMap[g.id] : !!g.collapsed,
          });
        }
        if (ungrouped.length || (activeFilter.value === 'all' && !out.length) || activeFilter.value === 'ungrouped') {
          const totalCredits = ungrouped.reduce((s, x) => s + (Number(x.flow_credits) > 0 ? Number(x.flow_credits) : 0), 0);
          const openCount = ungrouped.filter(x => x.browser_open || x.status === 'open' || x.status === 'ready').length;
          out.push({
            id: '__ungrouped__',
            name: '未分组账号',
            color: '#71717a',
            url: '',
            flow_mode: 'frames',
            model: 'Veo 3.1 - Lite [Lower Priority]',
            isUngrouped: true,
            accounts: ungrouped,
            totalCredits,
            openCount,
            collapsed: !!collapsedMap['__ungrouped__'],
          });
        }
        return out;
      });

      const totalStats = computed(() => {
        const all = accounts.value;
        const total = all.length;
        const fav = all.filter(a => a.is_favorite || a.is_paid).length;
        const open = all.filter(a => a.browser_open || a.status === 'open' || a.status === 'ready').length;
        const credits = all.reduce((s, a) => s + (Number(a.flow_credits) > 0 ? Number(a.flow_credits) : 0), 0);
        return { total, fav, open, credits };
      });

      const allFilteredSelected = computed(() => {
        const list = filteredAccounts.value;
        return list.length > 0 && list.every(a => selectedIds.has(a.id));
      });

      async function loadData(silent = false) {
        if (!silent) loading.value = true;
        try {
          await window.refreshMultiAccounts({ silent: !!silent });
          refreshAllTotp();
        } finally {
          if (!silent) loading.value = false;
        }
      }

      async function refreshTotpFor(acc) {
        if (!acc || !acc.id || !acc.has_totp) return;
        try {
          const r = await API.get(`/api/multi-accounts/${encodeURIComponent(acc.id)}/totp`, { silent: true });
          if (r && r.ok && r.code) {
            totpMap[acc.id] = {
              code: r.code,
              remaining_seconds: Number(r.remaining_seconds || 30),
            };
          }
        } catch (e) { /* ignore single totp err */ }
      }

      function refreshAllTotp() {
        for (const a of accounts.value) {
          if (a && a.has_totp) refreshTotpFor(a);
        }
      }

      let totpTimer = null;
      onMounted(() => {
        loadData(false);
        window.addEventListener('mouseup', stopDragSelect);
        totpTimer = setInterval(() => {
          let needFetch = false;
          for (const [id, info] of Object.entries(totpMap)) {
            if (!info) continue;
            if (info.remaining_seconds <= 1) {
              needFetch = true;
            } else {
              info.remaining_seconds -= 1;
            }
          }
          if (needFetch) refreshAllTotp();
        }, 1000);
      });

      onBeforeUnmount(() => {
        window.removeEventListener('mouseup', stopDragSelect);
        if (totpTimer) clearInterval(totpTimer);
      });

      watch(() => store.refreshSeq, () => loadData(true));

      // Drag selection handlers
      function startDragSelect(acc, ev) {
        if (ev && ev.button !== 0) return;
        const nextVal = !selectedIds.has(acc.id);
        dragSel.active = true;
        dragSel.targetVal = nextVal;
        if (nextVal) selectedIds.add(acc.id);
        else selectedIds.delete(acc.id);
      }
      function enterDragSelect(acc) {
        if (!dragSel.active || !acc) return;
        if (dragSel.targetVal) selectedIds.add(acc.id);
        else selectedIds.delete(acc.id);
      }
      function stopDragSelect() {
        dragSel.active = false;
      }

      function toggleSelectAll() {
        const list = filteredAccounts.value;
        if (allFilteredSelected.value) {
          list.forEach(a => selectedIds.delete(a.id));
        } else {
          list.forEach(a => selectedIds.add(a.id));
        }
      }

      function isGroupAllSelected(sec) {
        return sec.accounts.length > 0 && sec.accounts.every(a => selectedIds.has(a.id));
      }

      function toggleSelectGroup(sec) {
        const allOn = isGroupAllSelected(sec);
        sec.accounts.forEach(a => {
          if (allOn) selectedIds.delete(a.id);
          else selectedIds.add(a.id);
        });
      }

      async function toggleCollapseSection(sec) {
        const next = !sec.collapsed;
        collapsedMap[sec.id] = next;
        if (!sec.isUngrouped) {
          try {
            await API.post(`/api/multi-account-groups/${encodeURIComponent(sec.id)}`, { collapsed: next }, { silent: true });
          } catch (e) { /* ignore */ }
        }
      }

      function expandOrCollapseAll(collapse) {
        for (const sec of groupedSections.value) {
          collapsedMap[sec.id] = !!collapse;
        }
      }

      // Click-to-copy credentials
      async function copyCredential(acc, field) {
        if (!acc || !acc.id) return;
        if (field === 'email') {
          window.copyText(acc.email || '', '账号邮箱');
          return;
        }
        if (field === 'totp') {
          try {
            const r = await API.get(`/api/multi-accounts/${encodeURIComponent(acc.id)}/totp`);
            if (r && r.code) {
              totpMap[acc.id] = { code: r.code, remaining_seconds: Number(r.remaining_seconds || 30) };
              window.copyText(r.code, '2FA 验证码');
              return;
            }
          } catch (e) { /* fallback to secret */ }
        }
        try {
          const r = await API.post(`/api/multi-accounts/${encodeURIComponent(acc.id)}/secret`, { field });
          if (r && r.value) {
            window.copyText(r.value, field === 'password' ? '密码' : '密钥');
          } else {
            toast('未设置该凭证', '', 'warn');
          }
        } catch (e) { /* API toast */ }
      }

      async function updateAccountField(acc, patch, msg) {
        if (!acc || !acc.id) return;
        Object.assign(acc, patch);
        try {
          await API.post(`/api/multi-accounts/${encodeURIComponent(acc.id)}`, patch);
          if (msg) toast(msg, '', 'ok');
          await loadData(true);
        } catch (e) { /* API toast */ }
      }

      async function toggleFavorite(acc) {
        const next = !(acc.is_favorite || acc.is_paid);
        await updateAccountField(acc, { is_favorite: next, is_paid: next }, next ? '已标记为 ❤️ 付费/收藏账号' : '已取消收藏');
      }

      function openAddModal() {
        addModal.text = '';
        addModal.group_id = (activeFilter.value !== 'all' && activeFilter.value !== 'fav' && activeFilter.value !== 'ungrouped')
          ? activeFilter.value
          : (groups.value[0] ? groups.value[0].id : '');
        addModal.flow_mode = 'frames';
        addModal.model = 'Veo 3.1 - Lite [Lower Priority]';
        addModal.open = true;
      }

      async function submitAddAccounts() {
        if (!addModal.text.trim()) {
          toast('请粘贴或输入至少一行账号数据', '', 'bad');
          return;
        }
        addModal.busy = true;
        try {
          const r = await API.post('/api/multi-accounts/add', {
            text: addModal.text,
            group_id: addModal.group_id || '',
            flow_mode: addModal.flow_mode || 'frames',
            model: addModal.model || 'Veo 3.1 - Lite [Lower Priority]',
          });
          addModal.open = false;
          toast(`成功添加 ${(r && r.added_count) || (r && r.accounts && r.accounts.length) || ''} 个账号`, '', 'ok');
          await loadData(false);
        } catch (e) { /* API toast */ } finally {
          addModal.busy = false;
        }
      }

      function openEditAccModal(acc) {
        editAccModal.id = acc.id;
        editAccModal.email = acc.email || '';
        editAccModal.password = '';
        editAccModal.twoFactorCode = '';
        editAccModal.group_id = acc.group_id || '';
        editAccModal.flow_mode = acc.flow_mode || 'frames';
        editAccModal.model = acc.model || 'Veo 3.1 - Lite [Lower Priority]';
        editAccModal.flow_credits = acc.flow_credits != null ? String(acc.flow_credits) : '';
        editAccModal.is_favorite = !!(acc.is_favorite || acc.is_paid);
        editAccModal.is_paid = !!(acc.is_paid || acc.is_favorite);
        editAccModal.note = acc.note || '';
        editAccModal.open = true;
      }

      async function submitEditAccount() {
        if (!editAccModal.id) return;
        editAccModal.busy = true;
        try {
          const payload = {
            email: editAccModal.email.trim(),
            group_id: editAccModal.group_id || '',
            flow_mode: editAccModal.flow_mode || 'frames',
            model: editAccModal.model || 'Veo 3.1 - Lite [Lower Priority]',
            is_favorite: !!editAccModal.is_favorite,
            is_paid: !!editAccModal.is_paid,
            note: editAccModal.note || '',
          };
          if (editAccModal.password.trim()) payload.password = editAccModal.password.trim();
          if (editAccModal.twoFactorCode.trim()) payload.twoFactorCode = editAccModal.twoFactorCode.trim();
          if (editAccModal.flow_credits !== '') payload.flow_credits = Number(editAccModal.flow_credits);
          await API.post(`/api/multi-accounts/${encodeURIComponent(editAccModal.id)}`, payload);
          editAccModal.open = false;
          toast('账号信息已保存', '', 'ok');
          await loadData(false);
        } catch (e) { /* API toast */ } finally {
          editAccModal.busy = false;
        }
      }

      function openCreateGroupModal() {
        groupModal.isEdit = false;
        groupModal.id = '';
        groupModal.name = '';
        groupModal.color = GROUP_COLORS[groups.value.length % GROUP_COLORS.length];
        groupModal.url = '';
        groupModal.flow_mode = 'frames';
        groupModal.model = 'Veo 3.1 - Lite [Lower Priority]';
        groupModal.open = true;
      }

      function openEditGroupModal(g) {
        if (!g || g.isUngrouped) return;
        groupModal.isEdit = true;
        groupModal.id = g.id;
        groupModal.name = g.name || '';
        groupModal.color = g.color || '#d4a24c';
        groupModal.url = g.url || '';
        groupModal.flow_mode = g.flow_mode || 'frames';
        groupModal.model = g.model || 'Veo 3.1 - Lite [Lower Priority]';
        groupModal.open = true;
      }

      async function submitGroupModal() {
        if (!groupModal.name.trim()) {
          toast('请输入账号组名称', '', 'bad');
          return;
        }
        groupModal.busy = true;
        try {
          const payload = {
            name: groupModal.name.trim(),
            color: groupModal.color || '#d4a24c',
            url: groupModal.url.trim(),
            flow_mode: groupModal.flow_mode || 'frames',
            model: groupModal.model || 'Veo 3.1 - Lite [Lower Priority]',
          };
          if (groupModal.isEdit && groupModal.id) {
            await API.post(`/api/multi-account-groups/${encodeURIComponent(groupModal.id)}`, payload);
            toast('账号组已更新', '', 'ok');
          } else {
            await API.post('/api/multi-account-groups', payload);
            toast('账号组已创建', '', 'ok');
          }
          groupModal.open = false;
          await loadData(false);
        } catch (e) { /* API toast */ } finally {
          groupModal.busy = false;
        }
      }

      async function deleteGroup(g) {
        if (!g || !g.id || g.isUngrouped) return;
        if (!confirm(`确定删除账号组【${g.name}】吗？（组内账号将保留并移至未分组）`)) return;
        try {
          await API.del(`/api/multi-account-groups/${encodeURIComponent(g.id)}`);
          if (activeFilter.value === g.id) activeFilter.value = 'all';
          toast('账号组已删除', '', 'ok');
          await loadData(false);
        } catch (e) { /* API toast */ }
      }

      function openBatchModal() {
        if (!selectedIds.size) {
          toast('请先勾选要批量设置的账号', '', 'warn');
          return;
        }
        batchModal.group_id = '__keep__';
        batchModal.flow_mode = '__keep__';
        batchModal.model = '__keep__';
        batchModal.is_favorite = '__keep__';
        batchModal.open = true;
      }

      async function submitBatchModal() {
        const ids = Array.from(selectedIds);
        if (!ids.length) return;
        const payload = { ids };
        if (batchModal.group_id !== '__keep__') payload.group_id = batchModal.group_id;
        if (batchModal.flow_mode !== '__keep__') payload.flow_mode = batchModal.flow_mode;
        if (batchModal.model !== '__keep__') payload.model = batchModal.model;
        if (batchModal.is_favorite !== '__keep__') {
          payload.is_favorite = batchModal.is_favorite === 'true';
          payload.is_paid = batchModal.is_favorite === 'true';
        }
        batchModal.busy = true;
        try {
          await API.post('/api/multi-accounts/batch-group', payload);
          batchModal.open = false;
          toast(`已批量更新 ${ids.length} 个账号`, '', 'ok');
          await loadData(false);
        } catch (e) { /* API toast */ } finally {
          batchModal.busy = false;
        }
      }

      async function openBrowsers(ids) {
        const targetIds = Array.isArray(ids) && ids.length ? ids : Array.from(selectedIds);
        if (!targetIds.length) {
          toast('请先勾选要打开的账号', '', 'warn');
          return;
        }
        try {
          toast(`正在启动并自动登录 ${targetIds.length} 个账号（最多 3 并发）…`, '', 'ok');
          await API.post('/api/multi-accounts/open', { ids: targetIds, auto_login: true });
          await loadData(true);
        } catch (e) { /* API toast */ }
      }

      async function closeBrowsers(ids) {
        const targetIds = Array.isArray(ids) && ids.length ? ids : Array.from(selectedIds);
        if (!targetIds.length) return;
        try {
          await API.post('/api/multi-accounts/close', { ids: targetIds });
          toast(`已关闭 ${targetIds.length} 个浏览器实例`, '', 'ok');
          await loadData(true);
        } catch (e) { /* API toast */ }
      }

      async function probeCredits(ids, groupId) {
        const payload = {};
        if (Array.isArray(ids) && ids.length) payload.ids = ids;
        else if (groupId && groupId !== '__ungrouped__') payload.group_id = groupId;
        else if (selectedIds.size > 0) payload.ids = Array.from(selectedIds);
        try {
          toast('正在检测 Flow 剩余额度…', '', 'ok');
          const r = await API.post('/api/multi-accounts/probe-credits', payload);
          toast(`额度刷新完成`, (r && r.hint) || '', 'ok');
          await loadData(true);
        } catch (e) { /* API toast */ }
      }

      async function deleteAccounts(ids) {
        const targetIds = Array.isArray(ids) && ids.length ? ids : Array.from(selectedIds);
        if (!targetIds.length) {
          toast('请先勾选要删除的账号', '', 'warn');
          return;
        }
        if (!confirm(`确定删除选中的 ${targetIds.length} 个账号吗？`)) return;
        try {
          await API.del('/api/multi-accounts', { ids: targetIds });
          targetIds.forEach(id => selectedIds.delete(id));
          toast(`已删除 ${targetIds.length} 个账号`, '', 'ok');
          await loadData(false);
        } catch (e) { /* API toast */ }
      }

      async function cleanCache() {
        const ids = selectedIds.size > 0 ? Array.from(selectedIds) : undefined;
        try {
          toast('正在智能清理浏览器缓存（保留 Cookie 与登录态）…', '', 'ok');
          const r = await API.post('/api/multi-accounts/clean-cache', { ids, deep: false });
          toast(`清理完成：${r.cleaned_accounts || 0} 个账号，释放 ${r.freed_mb != null ? r.freed_mb : 0} MB`, '', 'ok');
          await loadData(true);
        } catch (e) { /* API toast */ }
      }

      function triggerImportConfig() {
        if (fileInputEl.value) {
          fileInputEl.value.value = '';
          fileInputEl.value.click();
        }
      }

      async function onImportFileChange(ev) {
        const file = ev.target && ev.target.files && ev.target.files[0];
        if (!file) return;
        try {
          const text = await file.text();
          const config = JSON.parse(text);
          const r = await API.post('/api/multi-accounts/import-config', { config });
          toast(`成功导入配置`, `账号 ${(r && r.imported_accounts) || ''} 个 · 分组 ${(r && r.imported_groups) || ''} 个`, 'ok');
          await loadData(false);
        } catch (e) {
          toast('导入配置文件失败', e.message || 'JSON 格式不正确', 'bad');
        }
      }

      async function exportConfig() {
        try {
          const r = await API.get('/api/multi-accounts/export-config');
          const cfg = (r && r.config) || r;
          const filename = (r && r.filename) || `hedra-config-backup-${new Date().toISOString().slice(0, 10)}.json`;
          const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json;charset=utf-8' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
          toast('配置已导出', filename, 'ok');
        } catch (e) { /* API toast */ }
      }

      // Support pasting multi-row notes or accounts when focused in note cell
      async function onNotePaste(acc, secAccounts, idxInSec, ev) {
        const text = ev.clipboardData && ev.clipboardData.getData('text/plain');
        if (!text || !text.includes('\n')) return;
        ev.preventDefault();
        const lines = text.replace(/\r\n/g, '\n').split('\n');
        for (let i = 0; i < lines.length; i++) {
          const target = secAccounts[idxInSec + i];
          if (!target) break;
          target.note = lines[i].trim();
          API.post(`/api/multi-accounts/${encodeURIComponent(target.id)}`, { note: target.note }, { silent: true }).catch(() => {});
        }
        toast(`已批量粘贴 ${Math.min(lines.length, secAccounts.length - idxInSec)} 行备注`, '', 'ok');
      }

      function creditClass(credits) {
        if (credits === null || credits === undefined || credits === '') return 'muted';
        const n = Number(credits);
        if (!Number.isFinite(n)) return 'muted';
        if (n <= 0) return 'bad';
        if (n < 200) return 'warn';
        return 'ok';
      }

      return {
        loading, searchQuery, activeFilter, selectedIds, totpMap, fileInputEl,
        addModal, editAccModal, groupModal, batchModal, logModal,
        GROUP_COLORS, availableModels,
        accounts, groups, cacheStats, groupById, filteredAccounts, groupedSections, totalStats, allFilteredSelected,
        loadData, fmtTimeAgo, creditClass,
        startDragSelect, enterDragSelect, stopDragSelect,
        toggleSelectAll, isGroupAllSelected, toggleSelectGroup,
        toggleCollapseSection, expandOrCollapseAll,
        copyCredential, updateAccountField, toggleFavorite,
        openAddModal, submitAddAccounts,
        openEditAccModal, submitEditAccount,
        openCreateGroupModal, openEditGroupModal, submitGroupModal, deleteGroup,
        openBatchModal, submitBatchModal,
        openBrowsers, closeBrowsers, probeCredits, deleteAccounts, cleanCache,
        triggerImportConfig, onImportFileChange, exportConfig, onNotePaste,
      };
    },
    template: `
    <section class="panel ap-panel">
      <!-- 顶部工具栏 -->
      <div class="ap-toolbar">
        <div class="ap-toolbar-row">
          <button class="btn primary" type="button" @click="openAddModal">
            ➕ 添加账号 <small class="muted">(支持 Excel/Sheet 粘贴)</small>
          </button>
          <button class="btn" type="button" @click="openCreateGroupModal">📁 新建账号组</button>
          <button class="btn" type="button" :disabled="!selectedIds.size" @click="openBatchModal">
            🏷️ 批量归组 / 设模式 <span v-if="selectedIds.size" class="mono">({{ selectedIds.size }})</span>
          </button>
          <button class="btn" type="button" :disabled="!selectedIds.size" @click="openBrowsers()">
            🚀 批量打开并登录 Flow <small class="muted">(最多3并发)</small>
          </button>
          <button class="btn" type="button" @click="probeCredits()">📊 批量刷新额度</button>
          <button class="btn" type="button" @click="cleanCache()" :title="cacheStats.total_mb ? ('当前缓存约 ' + cacheStats.total_mb + ' MB') : '清理缓存并保留登录 Cookie'">
            🧹 智能清理缓存
          </button>
          <span class="spacer" style="flex:1"></span>
          <input ref="fileInputEl" type="file" accept=".json,application/json" style="display:none" @change="onImportFileChange">
          <button class="btn ghost sm" type="button" @click="triggerImportConfig">📂 导入配置 (兼容 Hedra JSON)</button>
          <button class="btn ghost sm" type="button" @click="exportConfig">💾 导出配置 (Hedra JSON)</button>
          <button v-if="selectedIds.size" class="btn ghost sm danger" type="button" @click="deleteAccounts()">
            🗑 批量删除 ({{ selectedIds.size }})
          </button>
        </div>

        <!-- 分组筛选与搜索栏 -->
        <div class="ap-filter-row">
          <div class="ap-pills">
            <button type="button" class="ap-pill" :class="{active: activeFilter==='all'}" @click="activeFilter='all'">
              全部账号 <span class="mono">{{ totalStats.total }}</span>
            </button>
            <button type="button" class="ap-pill" :class="{active: activeFilter==='fav'}" @click="activeFilter='fav'">
              ❤️ 付费/收藏 <span class="mono">{{ totalStats.fav }}</span>
            </button>
            <button v-for="g in groups" :key="g.id" type="button" class="ap-pill"
                    :class="{active: activeFilter===g.id}" @click="activeFilter=g.id">
              <span class="acc-dot" :style="{background: g.color || '#d4a24c'}"></span>
              <span>{{ g.name }}</span>
              <span class="mono">{{ g.account_count != null ? g.account_count : '' }}</span>
            </button>
            <button type="button" class="ap-pill" :class="{active: activeFilter==='ungrouped'}" @click="activeFilter='ungrouped'">
              未分组
            </button>
          </div>
          <div class="ap-search-box">
            <input v-model="searchQuery" type="search" placeholder="🔍 搜索邮箱 / 备注 / 分组 / ID…">
            <button class="btn ghost sm" type="button" @click="expandOrCollapseAll(false)">▾ 展开全部</button>
            <button class="btn ghost sm" type="button" @click="expandOrCollapseAll(true)">▸ 收起全部</button>
          </div>
        </div>
      </div>

      <!-- 账号组折叠区 + Google Sheet 风格表格 -->
      <div class="ap-body">
        <div v-if="!accounts.length && !loading" class="empty" style="padding:48px 20px;text-align:center">
          <div style="font-size:15px;margin-bottom:8px">暂无多账号数据</div>
          <div class="muted" style="margin-bottom:16px">支持从 Excel / Google Sheet 批量粘贴（邮箱|密码|2FA密钥），或直接导入 Hedra JSON 备份配置。</div>
          <div style="display:flex;gap:10px;justify-content:center">
            <button class="btn primary" type="button" @click="openAddModal">➕ 批量添加账号</button>
            <button class="btn" type="button" @click="triggerImportConfig">📂 导入 Hedra JSON 配置</button>
          </div>
        </div>

        <div v-for="sec in groupedSections" :key="sec.id" class="ap-group-sec" :style="{'--grp-color': sec.color}">
          <div class="ap-group-hd" @click="toggleCollapseSection(sec)">
            <span class="ap-caret">{{ sec.collapsed ? '▸' : '▾' }}</span>
            <input type="checkbox" :checked="isGroupAllSelected(sec)" @click.stop="toggleSelectGroup(sec)" title="全选/取消本组">
            <span class="ap-group-badge" :style="{background: sec.color + '22', borderColor: sec.color, color: sec.color}">
              🏷️ {{ sec.name }}
            </span>
            <span class="muted mono" style="font-size:12px">{{ sec.accounts.length }} 个账号</span>
            <span class="tag" :style="{borderColor: sec.flow_mode==='ingredients' ? 'var(--warn)' : 'var(--flow)', color: sec.flow_mode==='ingredients' ? 'var(--warn)' : 'var(--flow)'}">
              默认: {{ sec.flow_mode === 'ingredients' ? '🧩 素材模式 (Ingredients)' : '🎞️ 首尾帧模式 (Frames)' }}
            </span>
            <span class="tag muted mono" v-if="sec.model">{{ sec.model }}</span>
            <span class="tag ok mono" v-if="sec.totalCredits > 0">⚡ 组总剩余额度: {{ sec.totalCredits }}</span>
            <span class="spacer" style="flex:1"></span>
            <div class="ap-group-acts" @click.stop>
              <button class="btn sm ghost" type="button" v-if="sec.accounts.length"
                      @click="openBrowsers(sec.accounts.map(a => a.id))">🚀 打开本组</button>
              <button class="btn sm ghost" type="button" v-if="sec.accounts.length"
                      @click="probeCredits(sec.accounts.map(a => a.id), sec.id)">📊 测本组额度</button>
              <button class="btn sm ghost" type="button" v-if="!sec.isUngrouped"
                      @click="openEditGroupModal(sec)">✏️ 编辑组</button>
              <button class="btn sm ghost danger" type="button" v-if="!sec.isUngrouped"
                      @click="deleteGroup(sec)">🗑️ 删除组</button>
            </div>
          </div>

          <div v-if="!sec.collapsed" class="ap-table-wrap">
            <table class="ap-sheet-table">
              <thead>
                <tr>
                  <th style="width:36px;text-align:center">
                    <input type="checkbox" :checked="isGroupAllSelected(sec)" @change="toggleSelectGroup(sec)">
                  </th>
                  <th style="width:42px;text-align:center">❤️</th>
                  <th style="width:58px">ID</th>
                  <th style="min-width:270px">账号凭证 (点按复制邮箱 / 密码 / 实时 2FA)</th>
                  <th style="width:140px">所属分组</th>
                  <th style="width:165px">默认 Flow 模式</th>
                  <th style="width:195px">默认模型</th>
                  <th style="width:110px;text-align:right">剩余额度</th>
                  <th style="width:150px">状态 / 最近登录</th>
                  <th style="min-width:160px">备注 (双击/直接编辑)</th>
                  <th style="width:185px;text-align:right">快捷操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!sec.accounts.length">
                  <td colspan="11" class="muted" style="text-align:center;padding:16px">本组暂无账号</td>
                </tr>
                <tr v-for="(acc, idx) in sec.accounts" :key="acc.id"
                    :class="{'row-selected': selectedIds.has(acc.id), 'row-fav': acc.is_favorite || acc.is_paid}">
                  <td class="ap-td-sel" @mousedown.prevent="startDragSelect(acc, $event)" @mouseenter="enterDragSelect(acc)">
                    <input type="checkbox" :checked="selectedIds.has(acc.id)" tabindex="-1" style="pointer-events:none">
                  </td>
                  <td style="text-align:center;cursor:pointer" @click="toggleFavorite(acc)" title="点击标记/取消 付费收藏账号（轮换时最优先调用）">
                    <span class="ap-heart" :class="{on: acc.is_favorite || acc.is_paid}">{{ (acc.is_favorite || acc.is_paid) ? '❤️' : '🤍' }}</span>
                  </td>
                  <td class="mono muted">{{ acc.id }}</td>
                  <td>
                    <div class="ap-cred-cell">
                      <button type="button" class="ap-cred-email mono" @click="copyCredential(acc, 'email')" title="点击复制邮箱">
                        {{ acc.email || '(未填邮箱)' }}
                      </button>
                      <button v-if="acc.has_password" type="button" class="ap-cred-pill pwd" @click="copyCredential(acc, 'password')" title="点击复制密码（密文保护，不直接回显）">
                        [PWD]
                      </button>
                      <button v-if="acc.has_totp" type="button" class="ap-cred-pill totp mono" @click="copyCredential(acc, 'totp')" title="点击复制实时 6 位 2FA 验证码">
                        🔐 {{ (totpMap[acc.id] && totpMap[acc.id].code) || '2FA' }}
                        <small v-if="totpMap[acc.id]">({{ totpMap[acc.id].remaining_seconds }}s)</small>
                      </button>
                    </div>
                  </td>
                  <td>
                    <select class="ap-cell-select" :value="acc.group_id || ''"
                            @change="updateAccountField(acc, { group_id: $event.target.value }, '已切换分组')">
                      <option value="">— 未分组 —</option>
                      <option v-for="g in groups" :key="g.id" :value="g.id">🏷️ {{ g.name }}</option>
                    </select>
                  </td>
                  <td>
                    <select class="ap-cell-select" :value="acc.flow_mode || 'frames'"
                            @change="updateAccountField(acc, { flow_mode: $event.target.value }, '已更新默认 Flow 模式')">
                      <option value="frames">🎞️ 首尾帧模式</option>
                      <option value="ingredients">🧩 素材模式</option>
                    </select>
                  </td>
                  <td>
                    <select class="ap-cell-select" :value="acc.model || 'Veo 3.1 - Lite [Lower Priority]'"
                            @change="updateAccountField(acc, { model: $event.target.value }, '已更新默认模型')">
                      <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
                    </select>
                  </td>
                  <td style="text-align:right">
                    <span class="tag mono" :class="creditClass(acc.flow_credits)"
                          style="cursor:pointer" @click="probeCredits([acc.id])" title="点击刷新该账号剩余额度">
                      {{ acc.flow_credits != null && acc.flow_credits !== '' ? (Number(acc.flow_credits) <= 0 ? '0 (耗尽)' : acc.flow_credits) : '未测' }} ↻
                    </span>
                  </td>
                  <td>
                    <div class="ap-status-cell">
                      <span class="lamp" :class="acc.browser_open || acc.status==='ready' || acc.status==='open' ? 'done' : (acc.status==='logging_in' ? 'busy' : 'idle')"></span>
                      <span>{{ acc.browser_open ? '已打开' : (acc.status || '空闲') }}</span>
                      <small class="muted">{{ fmtTimeAgo(acc.last_login_ts) }}</small>
                    </div>
                  </td>
                  <td>
                    <input class="ap-cell-input" :value="acc.note || ''" placeholder="添加备注…"
                           @change="updateAccountField(acc, { note: $event.target.value })"
                           @paste="onNotePaste(acc, sec.accounts, idx, $event)">
                  </td>
                  <td style="text-align:right;white-space:nowrap">
                    <button class="btn sm ghost" type="button" @click="openBrowsers([acc.id])" title="打开独立浏览器并自动登录 Flow">🚀 打开</button>
                    <button v-if="acc.browser_open" class="btn sm ghost" type="button" @click="closeBrowsers([acc.id])" title="关闭浏览器">⏹</button>
                    <button class="btn sm ghost" type="button" @click="openEditAccModal(acc)" title="编辑账号密码/2FA">✏️</button>
                    <button class="btn sm ghost danger" type="button" @click="deleteAccounts([acc.id])" title="删除账号">🗑</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- 批量添加账号弹窗 -->
      <div class="rs-mask" v-if="addModal.open" @click.self="addModal.open=false">
        <div class="rs-box" style="width:620px;max-width:94vw">
          <h3 style="margin-top:0">➕ 批量添加账号 (支持 Excel / Google Sheet 粘贴)</h3>
          <p class="muted" style="font-size:12px;margin:6px 0 10px">
            每行一个账号，支持 <code>邮箱|密码|2FA密钥|备注</code> 或直接从 Google Sheet / Excel 复制多列（Tab / 逗号分隔）粘贴：
          </p>
          <textarea v-model="addModal.text" rows="8" class="ap-textarea mono"
                    placeholder="user1@gmail.com&#9;Pass123!&#9;JBSWY3DPEHPK3PXP&#9;主力号1&#10;user2@gmail.com|Pass456!|JBSWY3DPEHPK3PXQ|备用号2"></textarea>
          <div class="row" style="gap:10px;margin-top:12px;flex-wrap:wrap">
            <div class="field" style="flex:1;min-width:160px">
              <label>归入账号组</label>
              <select v-model="addModal.group_id">
                <option value="">— 未分组 —</option>
                <option v-for="g in groups" :key="g.id" :value="g.id">🏷️ {{ g.name }}</option>
              </select>
            </div>
            <div class="field" style="flex:1;min-width:180px">
              <label>默认 Flow 生成模式</label>
              <select v-model="addModal.flow_mode">
                <option value="frames">🎞️ 首尾帧模式 (Frames)</option>
                <option value="ingredients">🧩 素材模式 (Ingredients)</option>
              </select>
            </div>
            <div class="field" style="flex:1;min-width:200px">
              <label>默认模型</label>
              <select v-model="addModal.model">
                <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
              </select>
            </div>
          </div>
          <div class="rs-acts" style="margin-top:16px">
            <button class="btn" type="button" @click="addModal.open=false">取消</button>
            <button class="btn primary" type="button" :disabled="addModal.busy" @click="submitAddAccounts">确认导入添加</button>
          </div>
        </div>
      </div>

      <!-- 编辑单个账号弹窗 -->
      <div class="rs-mask" v-if="editAccModal.open" @click.self="editAccModal.open=false">
        <div class="rs-box" style="width:500px;max-width:94vw">
          <h3 style="margin-top:0">✏️ 编辑账号 ({{ editAccModal.id }})</h3>
          <div class="field"><label>邮箱 (Email)</label><input v-model="editAccModal.email"></div>
          <div class="field"><label>新密码 (留空则保持原密码不变)</label><input v-model="editAccModal.password" type="password" placeholder="••••••••"></div>
          <div class="field"><label>2FA 密钥 TOTP Secret (留空则保持不变)</label><input v-model="editAccModal.twoFactorCode" placeholder="JBSWY3DPEHPK3PXP"></div>
          <div class="row" style="gap:10px">
            <div class="field" style="flex:1">
              <label>所属分组</label>
              <select v-model="editAccModal.group_id">
                <option value="">— 未分组 —</option>
                <option v-for="g in groups" :key="g.id" :value="g.id">{{ g.name }}</option>
              </select>
            </div>
            <div class="field" style="flex:1">
              <label>默认 Flow 模式</label>
              <select v-model="editAccModal.flow_mode">
                <option value="frames">🎞️ 首尾帧模式 (Frames)</option>
                <option value="ingredients">🧩 素材模式 (Ingredients)</option>
              </select>
            </div>
          </div>
          <div class="row" style="gap:10px">
            <div class="field" style="flex:1">
              <label>默认模型</label>
              <select v-model="editAccModal.model">
                <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
              </select>
            </div>
            <div class="field" style="width:140px">
              <label>剩余额度</label>
              <input v-model="editAccModal.flow_credits" type="number" placeholder="如 1000">
            </div>
          </div>
          <div class="field"><label>备注</label><input v-model="editAccModal.note"></div>
          <label style="display:flex;align-items:center;gap:6px;font-size:13px;margin:8px 0">
            <input type="checkbox" v-model="editAccModal.is_favorite"> ❤️ 标记为付费/收藏主力账号（组内轮换最优先）
          </label>
          <div class="rs-acts" style="margin-top:14px">
            <button class="btn" type="button" @click="editAccModal.open=false">取消</button>
            <button class="btn primary" type="button" :disabled="editAccModal.busy" @click="submitEditAccount">保存修改</button>
          </div>
        </div>
      </div>

      <!-- 新建 / 编辑账号组弹窗 -->
      <div class="rs-mask" v-if="groupModal.open" @click.self="groupModal.open=false">
        <div class="rs-box" style="width:480px;max-width:94vw">
          <h3 style="margin-top:0">{{ groupModal.isEdit ? '✏️ 编辑账号组' : '📁 新建账号组' }}</h3>
          <div class="field"><label>账号组名称</label><input v-model="groupModal.name" placeholder="例如：主力首尾帧组 A / 素材模式组 B" @keyup.enter="submitGroupModal"></div>
          <div class="field">
            <label>分组标识颜色</label>
            <div style="display:flex;gap:8px;align-items:center;margin-top:4px">
              <button v-for="c in GROUP_COLORS" :key="c" type="button"
                      :style="{width:'24px',height:'24px',borderRadius:'50%',background:c,border:groupModal.color===c?'2px solid #fff':'1px solid transparent',cursor:'pointer'}"
                      @click="groupModal.color=c"></button>
              <input type="color" v-model="groupModal.color" style="width:32px;height:26px;padding:0;border:none;background:transparent">
            </div>
          </div>
          <div class="field">
            <label>组默认 Flow 生成模式（1 任务表选此组时自动继承）</label>
            <select v-model="groupModal.flow_mode">
              <option value="frames">🎞️ 首尾帧模式 (Fotogrammi / Frames)</option>
              <option value="ingredients">🧩 素材模式 (Riferimenti / Ingredients)</option>
            </select>
          </div>
          <div class="field">
            <label>组默认模型</label>
            <select v-model="groupModal.model">
              <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
            </select>
          </div>
          <div class="field">
            <label>默认 Flow 项目链接 (选填)</label>
            <input v-model="groupModal.url" placeholder="https://labs.google/fx/tools/flow/project/...">
          </div>
          <div class="rs-acts" style="margin-top:16px">
            <button class="btn" type="button" @click="groupModal.open=false">取消</button>
            <button class="btn primary" type="button" :disabled="groupModal.busy" @click="submitGroupModal">保存账号组</button>
          </div>
        </div>
      </div>

      <!-- 批量归组 / 设模式弹窗 -->
      <div class="rs-mask" v-if="batchModal.open" @click.self="batchModal.open=false">
        <div class="rs-box" style="width:460px;max-width:94vw">
          <h3 style="margin-top:0">🏷️ 批量设置选中的 {{ selectedIds.size }} 个账号</h3>
          <div class="field">
            <label>批量归入分组</label>
            <select v-model="batchModal.group_id">
              <option value="__keep__">（保持原分组不变）</option>
              <option value="">— 移至未分组 —</option>
              <option v-for="g in groups" :key="g.id" :value="g.id">🏷️ {{ g.name }}</option>
            </select>
          </div>
          <div class="field">
            <label>批量设置默认 Flow 模式</label>
            <select v-model="batchModal.flow_mode">
              <option value="__keep__">（保持原模式不变）</option>
              <option value="frames">🎞️ 首尾帧模式 (Frames)</option>
              <option value="ingredients">🧩 素材模式 (Ingredients)</option>
            </select>
          </div>
          <div class="field">
            <label>批量设置默认模型</label>
            <select v-model="batchModal.model">
              <option value="__keep__">（保持原模型不变）</option>
              <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
            </select>
          </div>
          <div class="field">
            <label>付费/收藏标记</label>
            <select v-model="batchModal.is_favorite">
              <option value="__keep__">（保持不变）</option>
              <option value="true">❤️ 批量设为付费/收藏</option>
              <option value="false">🤍 批量取消收藏</option>
            </select>
          </div>
          <div class="rs-acts" style="margin-top:16px">
            <button class="btn" type="button" @click="batchModal.open=false">取消</button>
            <button class="btn primary" type="button" :disabled="batchModal.busy" @click="submitBatchModal">应用批量修改</button>
          </div>
        </div>
      </div>
    </section>
    `,
  };
})();
