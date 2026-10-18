// 文案搜索：编排 / 收片共用。项目不打包，所以挂 window.TextSearch，两面板都用这一份。
window.TextSearch = (() => {
  // 智能引号、弯撇号、破折号 → 直引号 / 连字符，方便从 txt 里复制过来还能命中
  const FOLD = {
    '\u201C': '"', '\u201D': '"', '\u201E': '"', '\u00AB': '"', '\u00BB': '"',
    '\u2018': "'", '\u2019': "'", '\u201A': "'", '\u2032': "'",
    '\u2014': '-', '\u2013': '-', '\u2212': '-',
  };

  function typing(el) {
    if (!el) return false;
    const tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
  }

  function normalize(s) {
    s = String(s == null ? '' : s);
    let out = '';
    for (let i = 0; i < s.length; i++) {
      const ch = s[i];
      out += FOLD[ch] || ch;
    }
    return out.replace(/\s+/g, ' ').trim().toLowerCase();
  }

  function matches(seg, q) {
    const nq = normalize(q);
    if (!nq) return false;
    const text = normalize(seg && seg.text);
    const prompt = normalize(seg && seg.prompt_final);
    return (text && text.indexOf(nq) >= 0) || (prompt && prompt.indexOf(nq) >= 0);
  }

  function findS(segs, q) {
    if (!normalize(q)) return [];
    const out = [];
    const seen = new Set();
    (segs || []).forEach(sg => {
      if (!sg || seen.has(sg.s)) return;
      if (matches(sg, q)) {
        seen.add(sg.s);
        out.push(sg.s);
      }
    });
    return out;
  }

  // 把用户输入收成能对上弯引号 / 破折号 / 空白的正则，用来在原文里 <mark>
  function queryRe(q) {
    const raw = String(q == null ? '' : q).replace(/\s+/g, ' ').trim();
    if (!raw) return null;
    const parts = [];
    for (let i = 0; i < raw.length; i++) {
      const ch = raw[i];
      if (/\s/.test(ch)) parts.push('\\s+');
      else if ('\u201C\u201D\u201E\u00AB\u00BB"'.indexOf(ch) >= 0) parts.push('[\\u201C\\u201D\\u201E\\u00AB\\u00BB"]');
      else if ('\u2018\u2019\u201A\u2032\''.indexOf(ch) >= 0) parts.push('[\\u2018\\u2019\\u201A\\u2032\']');
      else if ('\u2014\u2013\u2212-'.indexOf(ch) >= 0) parts.push('[\\u2014\\u2013\\u2212-]');
      else parts.push(ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    }
    try { return new RegExp(parts.join(''), 'gi'); }
    catch (e) { return null; }
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function markHtml(text, q) {
    const src = String(text == null ? '' : text);
    const re = queryRe(q);
    if (!re || !src) return escapeHtml(src);
    let out = '', last = 0, m;
    re.lastIndex = 0;
    while ((m = re.exec(src))) {
      out += escapeHtml(src.slice(last, m.index))
        + '<mark class="ts-mark">' + escapeHtml(m[0]) + '</mark>';
      last = m.index + m[0].length;
      if (!m[0].length) re.lastIndex += 1;
    }
    out += escapeHtml(src.slice(last));
    return out;
  }

  // Vue 面板用：去抖 150ms、当前/总数、‹ › / Enter、闪一下描边
  function create(opts) {
    opts = opts || {};
    const { ref, computed, watch, nextTick, onUnmounted } = Vue;
    const qLive = ref('');
    const q = ref('');
    const idx = ref(0);
    const flashS = ref(0);
    let debounceTimer = null;
    let flashTimer = null;
    let lastS = 0;

    const hits = computed(() => findS(opts.getSegs ? opts.getSegs() : [], q.value));
    const hitSet = computed(() => new Set(hits.value));
    const countText = computed(() => {
      if (!String(qLive.value || q.value || '').trim()) return '';
      if (!hits.value.length) return t('search.none');
      return t('search.count', { i: String(idx.value + 1), n: String(hits.value.length) });
    });

    function flush() {
      if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
      q.value = qLive.value;
    }

    function setQ(v) {
      qLive.value = v;
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        debounceTimer = null;
        q.value = v;
      }, 150);
    }

    function clear() {
      if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
      qLive.value = '';
      q.value = '';
      idx.value = 0;
      flashS.value = 0;
      lastS = 0;
    }

    function flash(s) {
      flashS.value = s;
      if (flashTimer) clearTimeout(flashTimer);
      flashTimer = setTimeout(() => {
        if (flashS.value === s) flashS.value = 0;
      }, 1200);
    }

    function jumpTo(i, doScroll) {
      const h = hits.value;
      if (!h.length) return;
      idx.value = ((i % h.length) + h.length) % h.length;
      const s = h[idx.value];
      lastS = s;
      flash(s);
      if (doScroll === false) return;
      nextTick(() => {
        const sel = (opts.cardSel || '[data-s="{s}"]').replace('{s}', String(s));
        const el = document.querySelector(sel);
        if (el && el.scrollIntoView) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        if (typeof opts.onJump === 'function') opts.onJump(s);
      });
    }

    function next() { jumpTo(idx.value + 1); }
    function prev() { jumpTo(idx.value - 1); }

    watch(hits, h => {
      if (!String(q.value || '').trim()) return;
      if (!h.length) { idx.value = 0; lastS = 0; return; }
      const keep = lastS ? h.indexOf(lastS) : -1;
      jumpTo(keep >= 0 ? keep : 0);
    });

    function onBoxKey(ev) {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        ev.stopPropagation();
        flush();
        nextTick(() => { if (ev.shiftKey) prev(); else next(); });
      } else if (ev.key === 'Escape') {
        ev.preventDefault();
        ev.stopPropagation();
        clear();
        if (ev.target && ev.target.blur) ev.target.blur();
      }
    }

    function focus() {
      nextTick(() => {
        const el = document.querySelector(opts.inputSel || '.ts-bar input');
        if (el && el.focus) {
          el.focus();
          if (el.select) el.select();
        }
      });
    }

    function onPanelKey(ev) {
      if (ev.defaultPrevented) return false;
      if (typing(ev.target) || typing(document.activeElement)) return false;
      const k = ev.key;
      if (k === '/' && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        ev.preventDefault();
        focus();
        return true;
      }
      const has = String(qLive.value || q.value || '').trim();
      if (!has) return false;
      if (k === 'Enter') {
        ev.preventDefault();
        if (ev.shiftKey) prev(); else next();
        return true;
      }
      if (k === 'Escape') {
        ev.preventDefault();
        clear();
        return true;
      }
      return false;
    }

    function isHit(s) { return hitSet.value.has(s); }
    function isFlash(s) { return flashS.value === s; }
    function highlight(text) { return markHtml(text, q.value); }

    onUnmounted(() => {
      if (debounceTimer) clearTimeout(debounceTimer);
      if (flashTimer) clearTimeout(flashTimer);
    });

    return {
      q, qLive, hits, idx, flashS, countText, hitSet,
      setQ, clear, flush, next, prev, jumpTo, onBoxKey, onPanelKey, focus,
      isHit, isFlash, highlight, markHtml,
    };
  }

  return { normalize, matches, findS, queryRe, markHtml, escapeHtml: escapeHtml, create, typing };
})();
