// 规格 54：每个任务自己的生成方式。探针 40：Grok 没有真正的首尾帧入口，
// 所以跟随引擎时 Grok → 逐段首帧，Flow → 锚点+接片。
window.GenModeLogic = (() => {
  const MODE_FIRST = 'first_frame';
  const MODE_ANCHOR = 'anchor_bridge';
  const MODE_CHAIN = 'tail_chain';
  const MODES = [MODE_FIRST, MODE_ANCHOR, MODE_CHAIN];

  function defaultModeForEngine(engine) {
    return String(engine || '').toLowerCase() === 'flow' ? MODE_ANCHOR : MODE_FIRST;
  }

  function parseGenMode(raw) {
    const v = String(raw == null ? '' : raw).trim();
    if (!v || v === 'follow' || v === '跟随引擎') return '';
    if (v === MODE_FIRST || v === '逐段首帧') return MODE_FIRST;
    if (v === MODE_ANCHOR || v === '锚点+接片' || v === '锚点 + 接片') return MODE_ANCHOR;
    if (v === MODE_CHAIN || v === '尾帧接龙') return MODE_CHAIN;
    return '';
  }

  function resolveTaskMode(task) {
    if (!task) return MODE_FIRST;
    const stored = parseGenMode(task.gen_mode);
    if (stored) return stored;
    if (task.mode === MODE_FIRST || task.mode === MODE_ANCHOR || task.mode === MODE_CHAIN) return task.mode;
    return defaultModeForEngine(task.engine);
  }

  function selectedModeState(selected, modeOf) {
    if (!selected || !selected.length) {
      return { mode: '', disabled: true, mixed: false, hintKey: 'gen.mode.pick' };
    }
    const modes = selected.map(t => (modeOf ? modeOf(t) : resolveTaskMode(t)));
    const uniq = [];
    modes.forEach(m => { if (uniq.indexOf(m) < 0) uniq.push(m); });
    if (uniq.length === 1) {
      return { mode: uniq[0], disabled: false, mixed: false, hintKey: '' };
    }
    return { mode: '', disabled: false, mixed: true, hintKey: 'gen.mode.mixed' };
  }

  function applyModeTargets(selected, isLive) {
    const sel = selected || [];
    if (!sel.length) return { targets: [], skipped: [], blocked: true };
    const targets = [];
    const skipped = [];
    sel.forEach(t => {
      if (isLive && isLive(t)) skipped.push(t);
      else targets.push(t);
    });
    return { targets, skipped, blocked: false };
  }

  function skipToast(skipped) {
    return (skipped || []).map(t => {
      const id = (t && t.id) ? t.id : String(t || '');
      return id + ' 正在跑，没改（停止后再改）';
    }).join('\n');
  }

  function mismatchWarn(task) {
    const stored = parseGenMode(task && task.gen_mode);
    if (!stored) return '';
    const eng = String((task && task.engine) || '').toLowerCase();
    const def = defaultModeForEngine(eng);
    if (stored === def) return '';
    const label = stored === MODE_ANCHOR ? '锚点+接片'
      : (stored === MODE_CHAIN ? '尾帧接龙' : '逐段首帧');
    const who = eng === 'flow' ? 'Flow' : (eng === 'studio' ? '本地模型' : 'Grok');
    return '这个任务手动选了「' + label + '」，但它是 ' + who + (eng === 'studio' ? '' : ' 账号');
  }

  async function runApplyMode(opts) {
    const mode = opts.mode;
    const post = opts.post;
    const confirmFn = opts.confirmFn;
    const toastFn = opts.toastFn;
    const alreadyMode = opts.alreadyMode;
    const { targets, skipped, blocked } = applyModeTargets(opts.selected, opts.isLive);
    const posted = [];
    if (blocked) return { applied: [], skipped: [], posted };
    if (skipped.length && toastFn) toastFn(skipToast(skipped));
    const need = (targets || []).filter(t => !alreadyMode || alreadyMode(t) !== mode);
    if (!need.length) {
      return { applied: [], skipped: skipped.map(t => t.id || t), posted };
    }
    const parts = [];
    for (const tsk of need) {
      const url = '/api/tasks/' + encodeURIComponent(tsk.id) + '/mode';
      posted.push({ url, body: { mode, preview: true } });
      const r = await post(url, { mode, preview: true });
      if (r && r.hint) parts.push({ id: tsk.id, hint: r.hint });
    }
    const msg = parts.length === 1
      ? parts[0].hint
      : parts.map(p => p.id + '：' + p.hint).join('\n');
    if (msg && confirmFn && !confirmFn(msg)) {
      return { applied: [], skipped: skipped.map(t => t.id || t), cancelled: true, posted };
    }
    const applied = [];
    for (const tsk of need) {
      const url = '/api/tasks/' + encodeURIComponent(tsk.id) + '/mode';
      posted.push({ url, body: { mode } });
      await post(url, { mode });
      applied.push(tsk.id);
    }
    return { applied, skipped: skipped.map(t => t.id || t), posted };
  }

  function modeItems() {
    return [
      { value: MODE_FIRST, labelKey: 'gen.mode.first', subKey: 'gen.mode.first_sub' },
      { value: MODE_ANCHOR, labelKey: 'gen.mode.anchor', subKey: 'gen.mode.anchor_sub' },
      { value: MODE_CHAIN, labelKey: 'gen.mode.chain', subKey: 'gen.mode.chain_sub' },
    ];
  }

  function usePickerGestures(opts) {
    return window.usePickerGestures(opts);
  }

  return {
    MODE_FIRST, MODE_ANCHOR, MODE_CHAIN, MODES,
    defaultModeForEngine, parseGenMode, resolveTaskMode,
    selectedModeState, applyModeTargets, skipToast, mismatchWarn, runApplyMode,
    modeItems, usePickerGestures,
  };
})();

// ④ 生成方式下拉：点选 / 滚轮 / 拖拽走 pickgest。
window.GenModePick = {
  name: 'GenModePick',
  props: {
    modelValue: { type: String, default: '' },
    disabled: { type: Boolean, default: false },
    hint: { type: String, default: '' },
  },
  emits: ['update:modelValue', 'pick'],
  setup(props, { emit }) {
    const { ref, computed, watch, onMounted, onUnmounted } = Vue;
    const open = ref(false);
    const preview = ref('');
    const rootRef = ref(null);

    const items = computed(() => {
      const L = window.GenModeLogic;
      return (L && L.modeItems ? L.modeItems() : []).map(m => ({
        value: m.value,
        label: t(m.labelKey),
        sub: t(m.subKey),
        tip: m.value === 'anchor_bridge' ? t('gen.mode.anchor_tip')
          : (m.value === 'tail_chain' ? t('gen.mode.chain_tip') : ''),
        disabled: false,
      }));
    });

    const shown = computed(() => preview.value || props.modelValue);
    const shownItem = computed(() => items.value.find(m => m.value === shown.value) || null);
    const trigLabel = computed(() => {
      if (shownItem.value) return shownItem.value.label;
      return props.hint || t('gen.mode.pick');
    });

    function close() { open.value = false; }
    function toggle() {
      if (props.disabled) return;
      open.value = !open.value;
    }
    function pick(val) {
      preview.value = '';
      close();
      emit('update:modelValue', val);
      emit('pick', val);
    }

    const gest = window.usePickerGestures({
      items: () => items.value,
      current: () => shown.value,
      disabled: () => !!props.disabled,
      open: () => open.value,
      onPreview(item) { preview.value = item ? item.value : ''; },
      onCommit(item) {
        preview.value = '';
        if (item && item.value && item.value !== props.modelValue) pick(item.value);
      },
      onCancel() { preview.value = ''; },
      onOpen: toggle,
    });

    watch(() => props.modelValue, () => { preview.value = ''; });

    function onDoc(ev) {
      if (!open.value) return;
      const tgt = ev.target;
      if (rootRef.value && rootRef.value.contains(tgt)) return;
      close();
    }
    function onKey(ev) {
      if (!open.value) return;
      if (ev.key === 'Escape') { close(); ev.preventDefault(); }
    }
    onMounted(() => {
      document.addEventListener('pointerdown', onDoc, true);
      document.addEventListener('keydown', onKey, true);
    });
    onUnmounted(() => {
      if (gest && gest.dispose) gest.dispose();
      document.removeEventListener('pointerdown', onDoc, true);
      document.removeEventListener('keydown', onKey, true);
    });

    return { t, open, items, shown, shownItem, trigLabel, gest, rootRef, toggle, pick };
  },
  template: `
<div class="g-mode-pick" ref="rootRef" :class="{open: open, off: disabled}">
  <button type="button" class="tp-trig pg-trig g-mode-trig"
          :class="{ dragging: gest.dragging, bump: gest.bump, open: open,
                    'pg-in-down': gest.slide==='down', 'pg-in-up': gest.slide==='up' }"
          :disabled="disabled"
          :aria-expanded="open ? 'true' : 'false'" aria-haspopup="listbox"
          @wheel="gest.onWheel"
          @pointerdown="gest.onPointerDown"
          @pointermove="gest.onPointerMove"
          @pointerup="gest.onPointerUp"
          @pointercancel="gest.onPointerCancel"
          @lostpointercapture="gest.onLostPointerCapture"
          @click="gest.onClick"
          @contextmenu="gest.onContextMenu">
    <span class="tp-lab pg-lab" :class="{ph: !shownItem}">{{ trigLabel }}</span>
    <span class="pg-ew" v-if="gest.dragging">◂ ▸</span>
    <span class="tp-caret">▾</span>
    <span class="pg-pos" v-if="gest.posHint">{{ gest.posHint }}</span>
  </button>
  <div class="g-mode-pop" v-if="open" @click.stop>
    <div v-for="m in items" :key="m.value" class="tp-item ready"
         :class="{hi: m.value===shown}" :title="m.tip" @click="pick(m.value)">
      <span class="tp-name"><b>{{ m.label }}</b><small>{{ m.sub }}</small></span>
    </div>
  </div>
</div>`
};

