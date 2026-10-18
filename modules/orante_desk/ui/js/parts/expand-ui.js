// 扩图进度卡 + 参考勾选。② 多模态和 ③ 编排共用，后端仍走 expand.py。
window.ExpandUi = {
  name: 'ExpandUi',
  props: {
    tid: { type: String, default: '' },
    images: { type: Array, default: () => [] },
    expandAcc: { type: Object, default: null },
    // gemini：标题旁一颗「扩图…」按钮，点开才出横条
    trigger: { type: Boolean, default: false },
  },
  emits: ['done', 'prompt'],
  setup(props, { emit }) {
    const { ref, computed, watch, onUnmounted } = Vue;
    const RING = 2 * Math.PI * 30;
    const refsOpen = ref(false);
    const open = ref(!props.trigger);
    const runningKind = ref('');
    const card = Vue.reactive({
      on: false, kind: '', jid: '', failed: false, error: '',
      logOpen: false, logText: '', holdMsg: '', hold: false,
    });
    let jobPoll = null;
    let logPoll = null;
    let holdTimer = null;
    let finishing = false;

    const minImages = computed(() => Number(store.settings['expand.min_images'] || 6) || 6);
    const okSubjects = computed(() =>
      (props.images || []).filter(im => im.role === '主体' && im.status === 'ok' && im.exists !== false));
    const needExpand = computed(() =>
      props.tid && (props.images || []).length > 0 && okSubjects.value.length < minImages.value);
    const pickedRefs = computed(() => okSubjects.value.filter(im => im.expand_ref === true));
    const canExpand = computed(() => pickedRefs.value.length > 0);
    const expandBtnLabel = computed(() => {
      const a = props.expandAcc;
      if (!a) return t('track.expand_btn');
      const eng = a.engine === 'grok' ? 'Grok' : (a.engine === 'flow' ? 'Flow' : a.engine);
      return t('track.expand_btn_acc', { acc: eng + ' · ' + a.name });
    });
    const refLabel = computed(() =>
      pickedRefs.value.map(im => fmtP(im.p)).join('、') || '—');
    const showBar = computed(() => {
      if (card.on) return false;
      if (!props.trigger) return needExpand.value;
      return open.value;
    });
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
    const busyExpand = computed(() =>
      (store.jobs || []).some(j =>
        j.task_id === props.tid && j.kind === 'expand'
        && (j.status === 'running' || j.status === 'queued')));

    function fmtP(v) {
      if (v == null || v === '') return '';
      const s = String(v).trim();
      const m = s.match(/^([pb])(\d+)$/i);
      if (m) return m[1].toLowerCase() + String(parseInt(m[2], 10)).padStart(2, '0');
      const n = parseInt(s.replace(/^p/i, ''), 10);
      if (!Number.isFinite(n) || n < 1) return s;
      return 'p' + String(n).padStart(2, '0');
    }
    function mediaUrl(file, ver) {
      return window.srcWithV(file, ver, props.tid);
    }
    function canBeRef(im) {
      return im && im.role === '主体' && im.status === 'ok' && im.exists !== false;
    }
    function stopPolls() {
      if (jobPoll) { clearInterval(jobPoll); jobPoll = null; }
      if (logPoll) { clearInterval(logPoll); logPoll = null; }
    }
    function openCard(kind) {
      finishing = false;
      card.on = true;
      card.kind = kind;
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
        } catch (e) { /* 下一秒再试 */ }
      }, 1000);
    }
    async function refreshLog() {
      if (!card.jid || !card.logOpen) return;
      try {
        const r = await fetch('/api/jobs/' + encodeURIComponent(card.jid) + '/log');
        card.logText = await r.text();
      } catch (e) { /* 稍后 */ }
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
        const n = r.added != null ? Number(r.added) : 0;
        card.holdMsg = t('track.expand_done', { n: String(n), m: String(n) });
        stopPolls();
        holdTimer = setTimeout(async () => {
          closeCard();
          emit('done');
        }, 3000);
      } else if (j.status === 'cancelled' || j.status === 'lost') {
        finishing = true;
        card.hold = true;
        card.holdMsg = t('track.phase.cancelled');
        stopPolls();
        holdTimer = setTimeout(async () => {
          closeCard();
          emit('done');
        }, 3000);
      } else if (j.status === 'failed') {
        finishing = true;
        card.failed = true;
        card.error = j.error || t('track.phase.failed');
        stopPolls();
        runningKind.value = '';
      }
    }
    async function runExpand() {
      if (!props.tid) { toast(t('track.need_task'), '', 'bad'); return; }
      if (!canExpand.value) {
        toast(t('track.expand_need_ref'), '', 'bad');
        return;
      }
      runningKind.value = 'expand';
      openCard('expand');
      try {
        const res = await fetch('/api/jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind: 'expand', task_id: props.tid }),
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
    async function toggleRef(im, ev) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      if (!props.tid || !canBeRef(im)) return;
      const on = ev && ev.target && ev.target.type === 'checkbox'
        ? ev.target.checked
        : !im.expand_ref;
      const prev = !!im.expand_ref;
      im.expand_ref = on;
      try {
        const r = await API.post(
          '/api/tasks/' + encodeURIComponent(props.tid)
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
    function clickTrigger() {
      open.value = !open.value;
    }

    watch(() => props.tid, () => {
      closeCard();
      refsOpen.value = false;
      if (props.trigger) open.value = false;
    });
    watch(okSubjects, (list) => {
      if (!props.tid || !list.length) return;
      if (list.some(im => im.expand_ref === true)) return;
      toggleRef(list[0], { target: { type: 'checkbox', checked: true } });
    });
    watch(() => (store.jobs || []).map(j => j.id + ':' + j.status).join('|'), () => {
      if (card.on && card.jid) {
        const j = (store.jobs || []).find(x => x.id === card.jid);
        if (j) maybeFinish(j);
      }
      if (!card.on && props.tid) {
        const running = (store.jobs || []).find(j =>
          j.task_id === props.tid && j.kind === 'expand'
          && (j.status === 'running' || j.status === 'queued'));
        if (running) {
          openCard('expand');
          card.jid = running.id;
          runningKind.value = 'expand';
          startJobPoll(running.id);
        }
      }
    });
    onUnmounted(() => {
      stopPolls();
      if (holdTimer) clearTimeout(holdTimer);
    });

    return {
      t, RING, refsOpen, open, card, minImages, okSubjects, needExpand,
      canExpand, expandBtnLabel, refLabel, showBar, prog, cardLive, phaseText,
      ringOff, barPct, shownItems, hiddenN, busyExpand, runningKind,
      fmtP, mediaUrl, runExpand, cancelExpand, toggleLog, toggleRef,
      itemClass, itemTitle, clickTrigger,
    };
  },
  template: `
<div class="expand-ui">
  <button v-if="trigger" class="btn sm" type="button"
          :disabled="!tid" @click="clickTrigger">{{ t('gemini.expand_btn') }}</button>
  <div class="expand-ui-body" v-if="card.on || showBar">
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
          <img v-if="it.thumb" :src="mediaUrl(it.thumb)" alt="">
          <span class="xbadge" v-if="it.ref">{{ fmtP(it.ref) }}</span>
        </div>
        <div class="xmore" v-if="hiddenN">+{{ hiddenN }}</div>
      </div>
    </div>
    <pre class="xlog" v-if="card.logOpen">{{ card.logText || '…' }}</pre>
  </div>
  <div class="banner warn" v-if="showBar">
    <span>{{ t('track.expand_banner', { n: String(okSubjects.length) }) }}</span>
    <span class="refpick">
      <button class="btn sm ghost" type="button" @click="refsOpen=!refsOpen">
        {{ t('track.expand_refs', { ps: refLabel }) }} ▾
      </button>
      <div class="refpick-menu" v-if="refsOpen">
        <label v-for="im in okSubjects" :key="'r'+im.p" class="refpick-item">
          <input type="checkbox" :checked="!!im.expand_ref" @change="toggleRef(im, $event)">
          <img :src="mediaUrl(im.file, im.mtime)" :alt="fmtP(im.p)">
          <span>{{ fmtP(im.p) }}</span>
        </label>
      </div>
    </span>
    <button class="btn sm" :disabled="busyExpand || !!runningKind || !canExpand"
            :title="canExpand ? expandBtnLabel : t('track.expand_need_ref')"
            @click="runExpand">
      {{ expandBtnLabel }}
    </button>
    <a class="xlink" href="#" @click.prevent="$emit('prompt')">{{ t('track.expand_prompts') }}</a>
  </div>
  </div>
</div>`
};
