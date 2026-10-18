// 优先级旗子：生成面板和任务面板共用。序号只在同一个账号里比大小。
(() => {
  const COLORS = [
    '#ff5f56', '#ff9f43', '#ffd166', '#4ade80', '#2dd4bf',
    '#60a5fa', '#818cf8', '#c084fc', '#f472b6', '#a3e635',
  ];
  const AUTO = '#6b7280';
  const AUTO_PRIO = 9999; // 和后端 locks.AUTO_PRIO 对齐：接口里「自动」会写成 9999

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function clampPrio(v) {
    const n = parseInt(v, 10);
    if (!Number.isFinite(n) || n < 0) return 0;
    if (n >= AUTO_PRIO) return 0;
    if (n > 999) return 999;
    return n;
  }

  function colorOf(n) {
    n = clampPrio(n);
    if (n < 1) return AUTO;
    return COLORS[(n - 1) % 10];
  }

  function hexRgb(hex) {
    const h = String(hex || '').replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
    if (!Number.isFinite(n)) return { r: 107, g: 114, b: 128 };
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }

  function dim(hex, f) {
    const { r, g, b } = hexRgb(hex);
    const to = x => Math.max(0, Math.min(255, Math.round(x * f))).toString(16).padStart(2, '0');
    return '#' + to(r) + to(g) + to(b);
  }

  function inkOf(hex) {
    const { r, g, b } = hexRgb(hex);
    return (0.299 * r + 0.587 * g + 0.114 * b) > 168 ? '#1a1712' : '#fffaf4';
  }

  function glowOf(hex) {
    const { r, g, b } = hexRgb(hex);
    return `rgba(${r},${g},${b},.30)`;
  }

  // 内联 SVG：旗杆 + 三角旗面。w/h 默认 16×18；顶栏小旗传 12。
  function svgFlag(n, size) {
    n = clampPrio(n);
    const w = size || 16;
    const h = Math.round(w * 18 / 16);
    const auto = n < 1;
    const c = colorOf(n);
    const pole = auto ? AUTO : dim(c, 0.6);
    const glow = auto ? 'none' : `drop-shadow(0 0 3px ${glowOf(c)})`;
    const face = auto
      ? `<polygon class="pf-face" points="3,1.6 14.6,5.5 3,9.4" fill="none" stroke="${AUTO}" stroke-width="1.25" stroke-linejoin="round"/>`
      : `<polygon class="pf-face" points="3,1.6 14.6,5.5 3,9.4" fill="${c}" stroke="${c}" stroke-width=".4" stroke-linejoin="round"/>`;
    let text = '';
    if (!auto && n < 100) {
      const fs = n >= 10 ? 5.6 : 7;
      text = `<text x="8.1" y="6.55" text-anchor="middle" font-size="${fs}" font-weight="700" `
        + `font-family="IBM Plex Mono,SF Mono,Consolas,monospace" fill="${inkOf(c)}">${n}</text>`;
    }
    return `<svg class="pf" viewBox="0 0 16 18" width="${w}" height="${h}" aria-hidden="true" `
      + `style="filter:${glow}">`
      + `<line class="pf-pole" x1="3" y1="1" x2="3" y2="17" stroke="${pole}" stroke-width="1.35" stroke-linecap="round"/>`
      + `<g class="pf-face">${face}${text}</g>`
      + `</svg>`;
  }

  function miniFlag(n) {
    return svgFlag(n, 12);
  }

  function tipOf(n) {
    n = clampPrio(n);
    return n < 1 ? t('prio.tip.auto') : t('prio.tip.flag', { n: String(n) });
  }

  function btnClass(n, state) {
    const cls = ['pf-btn'];
    if (clampPrio(n) < 1) cls.push('pf-auto');
    if (state === 'running') cls.push('is-run');
    else if (state === 'queued') cls.push('is-wait');
    return cls.join(' ');
  }

  function buttonHtml(n, state, tid) {
    n = clampPrio(n);
    const num = n >= 100 ? `<span class="pf-num mono">${n}</span>` : '';
    return `<button type="button" class="${btnClass(n, state)}" title="${esc(tipOf(n))}" `
      + `data-tid="${esc(tid || '')}" data-prio="${n}">${svgFlag(n)}${num}</button>`;
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

  function applyLocal(tid, n) {
    if (!tid) return;
    const hit = (store.tasks || []).find(x => x.id === tid);
    if (hit) hit.priority = n;
  }

  async function postPriority(tid, body) {
    if (!tid) return null;
    const hit = (store.tasks || []).find(x => x.id === tid);
    const old = hit ? (Number(hit.priority) || 0) : 0;
    if (hit) {
      if (body && body.top) hit.priority = 1;
      else hit.priority = clampPrio(body && body.priority);
    }
    try {
      const r = await API.post('/api/tasks/' + encodeURIComponent(tid) + '/priority', body || {});
      if (hit && r && r.priority != null) hit.priority = clampPrio(r.priority);
      return r;
    } catch (e) {
      if (hit) hit.priority = old;
      throw e;
    }
  }

  // 浮层单例：生成面板 Vue 组件和任务表 formatter 都走这里。
  let popVm = null;
  let popEl = null;
  let popTid = null;

  function closePop() {
    if (popVm) {
      try { popVm.unmount(); } catch (e) { /* 已经卸了 */ }
      popVm = null;
    }
    if (popEl && popEl.parentNode) popEl.parentNode.removeChild(popEl);
    popEl = null;
    popTid = null;
  }

  function openPop(opts) {
    opts = opts || {};
    const tid = opts.tid;
    if (!tid) return;
    if (popTid === tid && popVm) { closePop(); return; }
    closePop();

    let x = opts.x, y = opts.y;
    if (opts.anchorEl && opts.anchorEl.getBoundingClientRect) {
      const r = opts.anchorEl.getBoundingClientRect();
      x = r.left;
      y = r.bottom + 4;
    }
    x = x == null ? 8 : x;
    y = y == null ? 8 : y;
    const n0 = clampPrio(opts.priority);

    popTid = tid;
    popEl = document.createElement('div');
    document.body.appendChild(popEl);
    popVm = Vue.createApp({
      setup() {
        const { ref, onMounted, onUnmounted, nextTick } = Vue;
        const root = ref(null);
        const style = ref({ left: x + 'px', top: y + 'px' });
        const cur = ref(n0);
        const num = ref(n0 >= 1 ? String(n0) : '');

        function place() {
          const el = root.value;
          if (!el) return;
          const p = placeMenu(x, y, el.offsetWidth, el.offsetHeight,
            window.innerWidth, window.innerHeight, 8);
          style.value = { left: p.left + 'px', top: p.top + 'px' };
        }

        async function pick(n) {
          n = clampPrio(n);
          cur.value = n;
          try {
            await postPriority(tid, { priority: n });
            closePop();
          } catch (e) { /* API 已经 toast，本地已回滚 */ }
        }

        async function doTop() {
          try {
            await postPriority(tid, { top: true });
            closePop();
          } catch (e) { /* toast */ }
        }

        async function doAuto() {
          return pick(0);
        }

        function confirmNum() {
          const raw = String(num.value || '').trim();
          if (!raw) return;
          let n = parseInt(raw, 10);
          if (!Number.isFinite(n)) return;
          if (n < 0) n = 0;
          if (n > 999) n = 999;
          return pick(n);
        }

        function bump(d) {
          let n = parseInt(num.value, 10);
          if (!Number.isFinite(n) || n < 1) n = 1;
          else n += d;
          if (n < 1) n = 1;
          if (n > 999) n = 999;
          num.value = String(n);
        }

        function onDoc(ev) {
          if (root.value && !root.value.contains(ev.target)) closePop();
        }
        function onKey(ev) {
          if (ev.key === 'Escape') {
            ev.stopPropagation();
            closePop();
          }
        }

        onMounted(() => {
          nextTick(place);
          // 推迟到这次点击冒泡结束，免得刚打开就被关掉
          setTimeout(() => {
            document.addEventListener('mousedown', onDoc, true);
            document.addEventListener('keydown', onKey, true);
          }, 0);
        });
        onUnmounted(() => {
          document.removeEventListener('mousedown', onDoc, true);
          document.removeEventListener('keydown', onKey, true);
        });

        return {
          t, root, style, cur, num, svgFlag, colorOf,
          pick, doTop, doAuto, confirmNum, bump,
        };
      },
      template: `
      <div class="pf-pop" ref="root" :style="style" @mousedown.stop @click.stop>
        <div class="pf-title">{{ t('prio.title') }}</div>
        <div class="pf-chips">
          <button v-for="i in 9" :key="i" type="button" class="pf-chip"
                  :class="{on: cur===i}" :style="{'--pf-c': colorOf(i)}"
                  :title="t('prio.tip.flag', {n: String(i)})"
                  @click="pick(i)" v-html="svgFlag(i)"></button>
          <span class="pf-input-wrap" :class="{on: cur>=10}" :style="{'--pf-c': colorOf(cur>=10?cur:10)}">
            <input class="pf-input mono" v-model="num" maxlength="3"
                   inputmode="numeric" placeholder="12"
                   @keydown.enter.prevent="confirmNum">
            <span class="pf-spin">
              <button type="button" @click="bump(1)">▲</button>
              <button type="button" @click="bump(-1)">▼</button>
            </span>
          </span>
        </div>
        <div class="pf-sep"></div>
        <div class="pf-acts">
          <button type="button" class="pf-act" @click="doTop">⇧ {{ t('prio.top') }}</button>
          <button type="button" class="pf-act" :class="{on: cur===0}" @click="doAuto">⚐ {{ t('prio.auto') }}</button>
        </div>
        <div class="pf-hint">{{ t('prio.hint') }}</div>
      </div>`
    });
    popVm.mount(popEl);
  }

  const PrioFlag = {
    name: 'PrioFlag',
    props: {
      task: { type: Object, default: null },
      priority: { type: Number, default: undefined },
      state: { type: String, default: '' },
    },
    setup(props) {
      const { computed } = Vue;
      const n = computed(() => {
        if (props.task && props.task.priority != null) return clampPrio(props.task.priority);
        return clampPrio(props.priority);
      });
      const tid = computed(() => (props.task && props.task.id) || '');
      const cls = computed(() => ({
        'pf-auto': n.value < 1,
        'is-run': props.state === 'running',
        'is-wait': props.state === 'queued',
      }));
      const tip = computed(() => tipOf(n.value));
      const svg = computed(() => svgFlag(n.value));
      const showNum = computed(() => n.value >= 100);

      function onClick(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (!tid.value) return;
        openPop({
          tid: tid.value,
          priority: n.value,
          anchorEl: ev.currentTarget,
        });
      }

      return { n, cls, tip, svg, showNum, onClick };
    },
    template: `
      <button type="button" class="pf-btn" :class="cls" :title="tip"
              @click="onClick" @mousedown.stop>
        <span v-html="svg"></span>
        <span class="pf-num mono" v-if="showNum">{{ n }}</span>
      </button>`
  };

  window.PrioFlag = PrioFlag;
  window.PrioFlagLogic = {
    COLORS, AUTO_PRIO, colorOf, clampPrio, svgFlag, miniFlag, buttonHtml,
    tipOf, btnClass, placeMenu, openPop, closePop, postPriority, applyLocal,
  };
})();
