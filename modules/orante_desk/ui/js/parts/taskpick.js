// 共用任务选择器：没到门槛的任务也列出、置灰、说明差哪一步并能跳转。
(() => {
  const ST_ORDER = ['draft', 'prepared', 'described', 'arranged', 'generating', 'collected', 'finished'];

  // 没到门槛时：提示文案 key + 跳到哪个节点。按「还差的那一步」而不是目标面板。
  const JUMP = {
    draft: { node: 'tasks', hint: 'taskpick.hint.draft' },
    prepared: { node: 'gemini', hint: 'taskpick.hint.prepared' },
    described: { node: 'track', hint: 'taskpick.hint.described' },
    arranged: { node: 'generate', hint: 'taskpick.hint.arranged' },
  };

  // 可用组空态：跳到「要迈过的那一门槛」的上一站。
  const EMPTY_FROM = { prepared: 'draft', described: 'prepared', generating: 'arranged', arranged: 'described' };

  function rank(st) {
    const i = ST_ORDER.indexOf(st);
    return i < 0 ? -1 : i;
  }

  function atLeast(st, min) {
    return rank(st) >= rank(min);
  }

  function jumpOf(st) {
    return JUMP[st] || JUMP.draft;
  }

  function emptyFrom(min) {
    return EMPTY_FROM[min] || 'draft';
  }

  function matches(task, q) {
    if (!q) return true;
    const id = String((task && task.id) || '').toLowerCase();
    const name = String((task && task.name) || '').toLowerCase();
    return id.includes(q) || name.includes(q);
  }

  function listedOf(tasks) {
    return (tasks || []).filter(x => x && x.status !== 'trash');
  }

  function splitTasks(tasks, minStatus) {
    const listed = listedOf(tasks);
    const ready = [];
    const done = [];
    const waiting = [];
    for (const x of listed) {
      if (!atLeast(x.status, minStatus)) waiting.push(x);
      else if (x.status === 'finished') done.push(x);
      else ready.push(x);
    }
    return { listed, ready: ready.concat(done), waiting, nReady: ready.length + done.length, nTotal: listed.length };
  }

  function triggerText(task) {
    if (!task) return '';
    return [task.id, task.name || '', task.engine || ''].filter(x => x !== '').join(' · ');
  }

  function takePendingTid(tidRef, nodeId) {
    const id = (window.ui && window.ui.pendingTid) || '';
    if (!id) return false;
    // 源面板也 watch 了 pendingTid；跳转时 node 已改，对不上的不吃，留给目标面板。
    if (nodeId && window.ui.node && window.ui.node !== nodeId) return false;
    window.ui.pendingTid = '';
    tidRef.value = id;
    if (window.ui) window.ui.panelTid = id;
    return true;
  }

  // 挂载时：先吃跳转指定，再吃全局当前任务。绝不因为准入不够改成别的任务。
  function adoptPanelTid(tidRef, nodeId) {
    if (takePendingTid(tidRef, nodeId)) return true;
    const cur = (window.ui && window.ui.panelTid) || '';
    if (!tidRef.value && cur) {
      tidRef.value = cur;
      return true;
    }
    return false;
  }

  function gateKey(st) {
    if (st === 'draft' || st === 'prepared' || st === 'described' || st === 'arranged') return st;
    return 'draft';
  }

  function gateInfo(task, minStatus) {
    if (!task) return null;
    if (atLeast(task.status, minStatus || 'described')) return null;
    const j = jumpOf(task.status);
    return {
      id: task.id,
      status: task.status,
      key: gateKey(task.status),
      node: j.node,
      hintKey: 'taskpick.gate.' + gateKey(task.status),
      goKey: 'taskpick.go.' + j.node,
    };
  }

  window.TaskPickLogic = {
    ST_ORDER, JUMP, EMPTY_FROM, rank, atLeast, jumpOf, emptyFrom,
    matches, listedOf, splitTasks, triggerText, takePendingTid, adoptPanelTid,
    gateKey, gateInfo,
  };

  window.takePendingTid = takePendingTid;
  window.adoptPanelTid = adoptPanelTid;
  if (window.ui && window.ui.pendingTid === undefined) window.ui.pendingTid = '';
  if (window.ui && window.ui.pendingP === undefined) window.ui.pendingP = '';
  if (window.ui && window.ui.panelTid === undefined) window.ui.panelTid = '';

  window.TaskPick = {
    name: 'TaskPick',
    props: {
      modelValue: { type: String, default: '' },
      minStatus: { type: String, default: 'described' },
      tasks: { type: Array, default: () => [] },
      variant: { type: String, default: '' }, // capsule = 顶栏胶囊
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      const { ref, computed, watch, onMounted, onUnmounted, nextTick } = Vue;

      const open = ref(false);
      const query = ref('');
      const hi = ref(0);
      const popStyle = ref({});
      const rootRef = ref(null);
      const trigRef = ref(null);
      const popRef = ref(null);

      const split = computed(() => splitTasks(props.tasks, props.minStatus || 'described'));
      const ready = computed(() => split.value.ready);
      const waiting = computed(() => split.value.waiting);
      const nReady = computed(() => split.value.nReady);
      const nTotal = computed(() => split.value.nTotal);
      const showSearch = computed(() => split.value.nTotal > 8);

      const qnorm = computed(() => (query.value || '').trim().toLowerCase());
      const readyView = computed(() => ready.value.filter(x => matches(x, qnorm.value)));
      const waitView = computed(() => waiting.value.filter(x => matches(x, qnorm.value)));
      const nomatch = computed(() => !!(qnorm.value && !readyView.value.length && !waitView.value.length));
      const showEmpty = computed(() => !qnorm.value && !readyView.value.length);

      const current = computed(() => {
        const id = props.modelValue;
        if (!id) return null;
        return listedOf(props.tasks).find(x => x.id === id) || null;
      });
      // 滚轮 / 拖拽只改触发器显示，停手才 emit（避免连发加载）
      const previewId = ref('');
      const shownTask = computed(() => {
        const id = previewId.value || props.modelValue;
        if (!id) return null;
        return listedOf(props.tasks).find(x => x.id === id) || current.value;
      });
      const isCap = computed(() => props.variant === 'capsule');
      const currentLabel = computed(() => {
        const task = shownTask.value;
        if (!task) return t('taskpick.placeholder');
        if (isCap.value) return [task.id, task.name || ''].filter(x => x !== '').join(' · ');
        return triggerText(task);
      });
      const gate = computed(() => {
        if (isCap.value) return null;
        return gateInfo(current.value, props.minStatus || 'described');
      });
      const gateText = computed(() => {
        const g = gate.value;
        if (!g) return '';
        const s = t(g.hintKey, { id: g.id });
        return s === g.hintKey ? (g.id + ' 还没到这一步') : s;
      });
      const gateGo = computed(() => {
        const g = gate.value;
        if (!g) return '';
        const s = t(g.goKey);
        return s === g.goKey ? t('taskpick.go') : s;
      });
      const countText = computed(() => t('taskpick.count', { a: String(nReady.value), b: String(nTotal.value) }));
      const readyTitle = computed(() => {
        const k = 'taskpick.ready.' + (props.minStatus || '');
        const s = t(k);
        return s === k ? t('taskpick.ready.default') : s;
      });
      const emptyText = computed(() => {
        const k = 'taskpick.empty.' + (props.minStatus || '');
        const s = t(k);
        return s === k ? t('taskpick.empty.described') : s;
      });

      const rows = computed(() => {
        const out = [];
        if (nomatch.value) {
          out.push({ kind: 'nomatch' });
          return out;
        }
        if (showEmpty.value) out.push({ kind: 'empty' });
        readyView.value.forEach(task => out.push({ kind: 'ready', task }));
        waitView.value.forEach(task => out.push({ kind: 'wait', task }));
        return out;
      });

      function statusLab(st) {
        const k = 'tasks.status.' + st;
        const s = t(k);
        return s === k ? st : s;
      }

      function hintOf(st) {
        return t(jumpOf(st).hint);
      }

      function place() {
        const el = trigRef.value;
        if (!el) return;
        const r = el.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const width = Math.min(Math.max(r.width, 360), vw - 16);
        let left = r.left;
        if (left + width > vw - 8) left = Math.max(8, vw - width - 8);
        if (left < 8) left = 8;
        const gap = 4;
        const spaceBelow = vh - r.bottom - gap - 8;
        const spaceAbove = r.top - gap - 8;
        const flip = spaceBelow < 220 && spaceAbove > spaceBelow;
        const maxH = Math.max(160, Math.min(420, flip ? spaceAbove : spaceBelow));
        if (flip) {
          popStyle.value = {
            left: left + 'px',
            top: 'auto',
            bottom: (vh - r.top + gap) + 'px',
            width: width + 'px',
            maxHeight: maxH + 'px',
          };
        } else {
          popStyle.value = {
            left: left + 'px',
            top: (r.bottom + gap) + 'px',
            bottom: 'auto',
            width: width + 'px',
            maxHeight: maxH + 'px',
          };
        }
      }

      function defaultHi() {
        const id = props.modelValue;
        const list = rows.value;
        if (id) {
          const i = list.findIndex(r => r.task && r.task.id === id);
          if (i >= 0) return i;
        }
        const j = list.findIndex(r => r.kind === 'ready');
        return j >= 0 ? j : 0;
      }

      function syncHi() {
        nextTick(() => {
          const box = popRef.value;
          if (!box) return;
          const el = box.querySelector('.tp-item.hi, .tp-empty.hi');
          if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
        });
      }

      function openMenu() {
        query.value = '';
        open.value = true;
        place();
        hi.value = defaultHi();
        nextTick(() => {
          place();
          const box = popRef.value;
          const inp = box && box.querySelector('input');
          if (inp) inp.focus();
          else if (trigRef.value) trigRef.value.focus();
          syncHi();
        });
      }

      function close() {
        if (!open.value) return;
        open.value = false;
        query.value = '';
      }

      function toggle() {
        if (open.value) close();
        else openMenu();
      }

      const gest = window.usePickerGestures({
        items: () => listedOf(props.tasks).map(task => ({
          value: task.id,
          disabled: !atLeast(task.status, props.minStatus || 'described'),
        })),
        current: () => previewId.value || props.modelValue,
        open: () => open.value,
        onPreview(item) { previewId.value = item ? (item.value || '') : ''; },
        onCommit(item) {
          const id = item && item.value;
          if (id && id !== props.modelValue) pick(id);
          else previewId.value = '';
        },
        onCancel() { previewId.value = ''; },
        onOpen: toggle,
      });

      function pick(id) {
        emit('update:modelValue', id);
        if (id && window.ui) window.ui.panelTid = id;
        close();
        nextTick(() => { if (trigRef.value) trigRef.value.focus(); });
      }

      async function jumpTo(node, tid) {
        if (!window.ui) return;
        if (window.flushAll) await window.flushAll();
        else if (window.flushEdits) await window.flushEdits();
        // pendingTid = 跳转时顺带指定；panelTid = 全局当前任务。一条链路。
        if (tid) {
          window.ui.pendingTid = tid;
          window.ui.panelTid = tid;
        } else {
          window.ui.pendingTid = '';
        }
        window.ui.node = node;
      }

      function goTask(task) {
        if (!task) return;
        jumpTo(jumpOf(task.status).node, task.id);
      }

      function goEmpty() {
        const j = jumpOf(emptyFrom(props.minStatus));
        const wait = waiting.value;
        const hit = wait.find(x => jumpOf(x.status).node === j.node) || wait[0];
        jumpTo(j.node, hit ? hit.id : '');
      }

      function onItemClick(row) {
        if (!row || row.kind !== 'ready' || !row.task) return;
        pick(row.task.id);
      }

      function typingIn(el) {
        if (!el) return false;
        const tag = (el.tagName || '').toLowerCase();
        return tag === 'input' || tag === 'textarea' || el.isContentEditable;
      }

      function eat(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      }

      function onKey(ev) {
        if (!open.value) {
          if (ev.target !== trigRef.value) return;
          if (ev.key === 'ArrowDown' || ev.key === 'Enter' || ev.key === ' ') {
            eat(ev);
            openMenu();
          }
          return;
        }
        const k = ev.key;
        if (k === 'Escape') {
          eat(ev);
          close();
          nextTick(() => { if (trigRef.value) trigRef.value.focus(); });
          return;
        }
        if (k === 'ArrowDown' || k === 'ArrowUp') {
          eat(ev);
          const n = rows.value.length;
          if (!n) return;
          const dir = k === 'ArrowDown' ? 1 : -1;
          hi.value = (hi.value + dir + n) % n;
          syncHi();
          return;
        }
        if (k === 'Enter') {
          eat(ev);
          const row = rows.value[hi.value];
          // 置灰 / 空态按 Enter 无效
          if (row && row.kind === 'ready' && row.task) pick(row.task.id);
          return;
        }
        if (!typingIn(ev.target)) {
          ev.stopPropagation();
          if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
          if (k === ' ') ev.preventDefault();
        }
      }

      function onDocPointer(ev) {
        if (!open.value) return;
        const t = ev.target;
        if (rootRef.value && rootRef.value.contains(t)) return;
        if (popRef.value && popRef.value.contains(t)) return;
        close();
      }

      function onWin() { if (open.value) place(); }

      watch(qnorm, () => { hi.value = defaultHi(); syncHi(); });
      watch(() => props.tasks, () => { if (open.value) nextTick(place); }, { deep: true });
      watch(() => props.modelValue, () => { previewId.value = ''; });

      onMounted(() => {
        document.addEventListener('keydown', onKey, true);
        document.addEventListener('pointerdown', onDocPointer, true);
        window.addEventListener('resize', onWin);
        window.addEventListener('scroll', onWin, true);
      });
      onUnmounted(() => {
        if (gest && gest.dispose) gest.dispose();
        document.removeEventListener('keydown', onKey, true);
        document.removeEventListener('pointerdown', onDocPointer, true);
        window.removeEventListener('resize', onWin);
        window.removeEventListener('scroll', onWin, true);
      });

      return {
        t, open, query, hi, popStyle, rootRef, trigRef, popRef,
        readyView, waitView, nomatch, showEmpty, showSearch,
        current, shownTask, currentLabel, countText, readyTitle, emptyText, rows,
        nReady, nTotal, isCap, gate, gateText, gateGo, gest,
        statusLab, hintOf, toggle, onItemClick, pick, goTask, goEmpty,
      };
    },
    template: `
<div class="tp" ref="rootRef" :class="{open: open, cap: isCap, blocked: !!gate}">
  <button type="button" class="tp-trig" ref="trigRef"
          :class="{ dragging: gest.dragging, bump: gest.bump,
                    'pg-in-down': gest.slide==='down', 'pg-in-up': gest.slide==='up' }"
          :aria-expanded="open ? 'true' : 'false'" aria-haspopup="listbox"
          @wheel="gest.onWheel"
          @pointerdown="gest.onPointerDown"
          @pointermove="gest.onPointerMove"
          @pointerup="gest.onPointerUp"
          @pointercancel="gest.onPointerCancel"
          @lostpointercapture="gest.onLostPointerCapture"
          @click="gest.onClick"
          @contextmenu="gest.onContextMenu">
    <span v-if="isCap && shownTask" class="tp-dot" :class="'st-' + shownTask.status"></span>
    <span class="tp-lab" :class="{ph: !shownTask}">{{ currentLabel }}</span>
    <span v-if="shownTask && !isCap" class="tag" :class="'st-' + shownTask.status">{{ statusLab(shownTask.status) }}</span>
    <span class="pg-ew" v-if="gest.dragging">◂ ▸</span>
    <span class="tp-caret">▾</span>
    <span class="pg-pos" v-if="gest.posHint">{{ gest.posHint }}</span>
  </button>
  <span class="tp-count" v-if="!isCap">{{ countText }}</span>
  <div class="tp-gate" v-if="gate">
    <span>{{ gateText }}</span>
    <button type="button" class="tp-go-line" @click.stop="goTask(current)">{{ gateGo }}</button>
  </div>
  <teleport to="body">
    <div class="tp-pop" ref="popRef" v-if="open" :style="popStyle" @keydown.stop>
      <div class="tp-search" v-if="showSearch">
        <input v-model="query" :placeholder="t('taskpick.search')" @keydown.stop>
      </div>
      <div class="tp-list">
        <div v-if="nomatch" class="tp-empty" :class="{hi: hi===0}">{{ t('taskpick.nomatch') }}</div>
        <template v-else>
          <div class="tp-g">{{ readyTitle }}</div>
          <div v-if="showEmpty" class="tp-empty" :class="{hi: rows[hi] && rows[hi].kind==='empty'}">
            <span class="tp-empty-txt">{{ emptyText }}</span>
            <button type="button" class="tp-go" :title="t('taskpick.go')" @click.stop="goEmpty">→</button>
          </div>
          <div v-for="task in readyView" :key="'r-'+task.id"
               class="tp-item ready" :class="{hi: rows[hi] && rows[hi].task && rows[hi].task.id===task.id, done: task.status==='finished'}"
               @click="onItemClick({kind:'ready', task: task})">
            <span class="tp-id mono">{{ task.id }}</span>
            <span class="tp-name">{{ task.status==='finished' ? '✓ ' : '' }}{{ task.name || '' }}</span>
            <span class="tag" :class="task.engine" v-if="task.engine">{{ task.engine }}</span>
            <span class="tag" :class="'st-' + task.status">{{ statusLab(task.status) }}</span>
          </div>
          <template v-if="waitView.length">
            <div class="tp-g">{{ t('taskpick.notyet') }}</div>
            <div v-for="task in waitView" :key="'w-'+task.id"
                 class="tp-item wait" :class="{hi: rows[hi] && rows[hi].task && rows[hi].task.id===task.id}">
              <span class="tp-id mono">{{ task.id }}</span>
              <span class="tp-name">{{ task.name || '' }}</span>
              <span class="tag" :class="task.engine" v-if="task.engine">{{ task.engine }}</span>
              <span class="tag" :class="'st-' + task.status">{{ statusLab(task.status) }}</span>
              <span class="tp-hint">{{ hintOf(task.status) }}</span>
              <button type="button" class="tp-go" :title="t('taskpick.go')" @click.stop="goTask(task)">→</button>
            </div>
          </template>
        </template>
      </div>
    </div>
  </teleport>
</div>`
  };
})();
