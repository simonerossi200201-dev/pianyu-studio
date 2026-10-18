window.LogDrawer = {
  name: 'LogDrawer',
  emits: ['close'],
  setup() {
    const { ref, computed, watch, nextTick } = Vue;
    const sel = ref(null);
    const jobs = computed(() => [...store.jobs].sort((a, b) => (b.id > a.id ? 1 : -1)));
    const lines = computed(() => sel.value ? (store.logs[sel.value] || []) : []);
    const box = ref(null);
    watch(lines, () => nextTick(() => { if (box.value) box.value.scrollTop = box.value.scrollHeight; }), { deep: true });
    async function open(j) { sel.value = j.id; if (!store.logs[j.id]) { const txt = await API.get(`/api/jobs/${j.id}/log`); store.logs[j.id] = String(txt).split('\n'); } }
    async function cancel(j) { await API.post(`/api/jobs/${j.id}/cancel`); }
    if (jobs.value.length) open(jobs.value[0]);
    return { sel, jobs, lines, box, open, cancel, t, copyText: window.copyText };
  },
  template: `
  <div><div class="drawer-mask" @click="$emit('close')"></div>
  <aside class="drawer wide">
    <div class="hd"><h2>{{ t('top.logs') }}</h2>
      <button class="btn ghost sm" :disabled="!lines.length" @click="copyText(lines.join('\\n'), sel || '')">{{ t('log.copy') }}</button>
      <button class="btn ghost sm" @click="$emit('close')">✕</button></div>
    <div class="bd" style="display:grid;grid-template-columns:260px 1fr;gap:12px">
      <div class="joblist">
        <div v-if="!jobs.length" class="empty">—</div>
        <div v-for="j in jobs" :key="j.id" class="j" :class="{sel: sel===j.id}" @click="open(j)">
          <span class="lamp" :class="{busy:j.status==='running'||j.status==='halted'}" style="width:8px;height:8px;border-radius:50%;background:var(--muted)" :style="{background: j.status==='running'?'var(--info)':j.status==='done'||j.status==='lost'?'var(--ok)':j.status==='failed'?'var(--bad)':j.status==='halted'?'var(--bad)':'var(--muted)'}"></span>
          <span class="mono" style="font-size:12px">{{ j.id }}</span>
          <span style="flex:1;font-size:12.5px">{{ j.kind }} <span class="muted">{{ j.task_id||'' }}</span></span>
          <button v-if="j.status==='running'||j.status==='queued'||j.status==='halted'" class="btn sm ghost danger" @click.stop="cancel(j)">{{ t('job.cancel') }}</button>
        </div>
      </div>
      <div class="logbox" ref="box" style="max-height:none;height:calc(100vh - 120px)">{{ lines.join('\\n') }}</div>
    </div>
  </aside></div>`
};
