// 规格 74：下拉共用的滚轮 / 拖拽手势。点选仍由各组件自己的菜单负责。
(() => {
  const WHEEL_UNIT = 40;     // 累计 delta 达到 ±40 才算一格（触摸板惯性）
  const THROTTLE_MS = 70;    // 一格最快 70ms，防止一下滚出去十几个
  const COMMIT_MS = 250;     // 停手后才真正提交，中间只预览
  const STEP_PX = 28;        // 水平拖每 28px 一格
  const CLICK_PX = 6;        // 小于 6px 松手 = 普通点击
  const HINT_MS = 600;       // 位置指示停手后淡出
  const BUMP_MS = 120;       // 到头顶住抖动
  const SLIDE_MS = 120;

  function read(x, fallback) {
    if (typeof x === 'function') {
      try { return x(); } catch (e) { return fallback; }
    }
    return x === undefined ? fallback : x;
  }

  function valOf(item) {
    if (item == null) return item;
    if (typeof item === 'object' && 'value' in item) return item.value;
    return item;
  }

  function isDisabled(item) {
    return !!(item && typeof item === 'object' && item.disabled);
  }

  function reduceMotion() {
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) { return false; }
  }

  function wrap(obj) {
    if (typeof Vue !== 'undefined' && Vue.reactive) return Vue.reactive(obj);
    return obj;
  }

  function enabledOf(items) {
    return (items || []).filter(it => it != null && !isDisabled(it));
  }

  function indexOfEnabled(list, cur) {
    const v = valOf(cur);
    return list.findIndex(it => valOf(it) === v);
  }

  function sameVal(a, b) {
    return valOf(a) === valOf(b);
  }

  /**
   * @param {object} opts
   *   items / current / disabled / open：值或 getter
   *   onPreview(item) 立刻改显示；onCommit(item) 停手才发
   *   onOpen() 位移 < 6px 的点击；onCancel() 拖拽取消
   */
  function usePickerGestures(opts) {
    opts = opts || {};
    const nowFn = opts.now || (() => Date.now());
    const schedule = opts.schedule || ((fn, ms) => setTimeout(fn, ms));
    const unschedule = opts.unschedule || (id => clearTimeout(id));

    const api = wrap({
      dragging: false,
      bump: false,
      posHint: '',
      slide: '',
      onWheel: null,
      onPointerDown: null,
      onPointerMove: null,
      onPointerUp: null,
      onPointerCancel: null,
      onLostPointerCapture: null,
      onClick: null,
      onContextMenu: null,
      dispose: null,
    });

    let lastTrig = null;
    let wheelAcc = 0;
    let lastStepAt = -1e9;
    let commitTimer = null;
    let hintTimer = null;
    let bumpTimer = null;
    let slideTimer = null;
    let pendingCommit = null;   // 预览到、尚未提交的项
    let committed = null;       // 按下 / 开始滚时的已提交值

    let pressing = false;       // pointerdown 之后、可能在拖
    let dragging = false;
    let cancelled = false;
    let eatClick = false;
    let startX = 0;
    let startY = 0;
    let startItem = null;
    let startEnabled = [];
    let startIdx = 0;
    let pointerId = null;
    let captureEl = null;
    let lastPreview = null;
    let docKeyOn = false;
    let lastBumpAt = 0;

    function items() { return read(opts.items, []) || []; }
    function current() { return read(opts.current, null); }
    function isOpen() { return !!read(opts.open, false); }
    function isOff() { return !!read(opts.disabled, false); }

    function trigOf(ev) {
      if (typeof opts.trigger === 'function') {
        const el = opts.trigger();
        if (el) return el;
      }
      if (opts.trigger) return opts.trigger;
      if (ev && ev.currentTarget) return ev.currentTarget;
      return lastTrig;
    }

    function setClass(el, name, on) {
      if (!el || !el.classList) return;
      if (on) el.classList.add(name);
      else el.classList.remove(name);
    }

    function applyTrig(el) {
      if (el) lastTrig = el;
      const t = lastTrig;
      if (!t) return;
      setClass(t, 'dragging', !!api.dragging);
      setClass(t, 'bump', !!api.bump);
      setClass(t, 'pg-in-down', api.slide === 'down');
      setClass(t, 'pg-in-up', api.slide === 'up');
    }

    function clearTimer(slot) {
      if (slot === 'commit' && commitTimer) { unschedule(commitTimer); commitTimer = null; }
      if (slot === 'hint' && hintTimer) { unschedule(hintTimer); hintTimer = null; }
      if (slot === 'bump' && bumpTimer) { unschedule(bumpTimer); bumpTimer = null; }
      if (slot === 'slide' && slideTimer) { unschedule(slideTimer); slideTimer = null; }
    }

    function showHint(item, list) {
      const en = enabledOf(list || items());
      const i = indexOfEnabled(en, item);
      if (i < 0 || !en.length) { api.posHint = ''; applyTrig(); return; }
      api.posHint = (i + 1) + ' / ' + en.length;
      applyTrig();
      clearTimer('hint');
      hintTimer = schedule(() => {
        hintTimer = null;
        api.posHint = '';
        applyTrig();
      }, HINT_MS);
    }

    function playSlide(dir) {
      if (reduceMotion() || !dir) return;
      api.slide = dir > 0 ? 'down' : 'up';
      applyTrig();
      clearTimer('slide');
      slideTimer = schedule(() => {
        slideTimer = null;
        api.slide = '';
        applyTrig();
      }, SLIDE_MS);
    }

    function bump(el) {
      const t = el || lastTrig;
      api.bump = true;
      applyTrig(t);
      const n = nowFn();
      if (n - lastBumpAt < BUMP_MS) {
        // 已经在抖，只续时
      }
      lastBumpAt = n;
      clearTimer('bump');
      bumpTimer = schedule(() => {
        bumpTimer = null;
        api.bump = false;
        applyTrig();
      }, BUMP_MS);
    }

    function preview(item, dir) {
      if (!item) return;
      const changed = !sameVal(item, lastPreview);
      lastPreview = item;
      pendingCommit = item;
      if (changed && dir) playSlide(dir);
      if (opts.onPreview) opts.onPreview(item);
      showHint(item);
    }

    function scheduleCommit(item) {
      pendingCommit = item;
      clearTimer('commit');
      if (item == null) return;
      if (committed != null && sameVal(item, committed)) return;
      commitTimer = schedule(() => {
        commitTimer = null;
        const it = pendingCommit;
        pendingCommit = null;
        if (it == null) return;
        committed = valOf(it);
        lastPreview = it;
        if (opts.onCommit) opts.onCommit(it);
      }, COMMIT_MS);
    }

    function commitNow(item) {
      clearTimer('commit');
      pendingCommit = null;
      if (item == null) return;
      committed = valOf(item);
      lastPreview = item;
      if (opts.onCommit) opts.onCommit(item);
    }

    function step(dir, el) {
      const list = items();
      const en = enabledOf(list);
      if (!en.length) { bump(el); return { item: null, bumped: true }; }
      let i = indexOfEnabled(en, current());
      if (i < 0) i = indexOfEnabled(en, lastPreview);
      if (i < 0) {
        const hit = dir > 0 ? en[0] : en[en.length - 1];
        preview(hit, dir);
        return { item: hit, bumped: false };
      }
      const n = i + dir;
      if (n < 0 || n >= en.length) {
        bump(el);
        return { item: en[i], bumped: true };
      }
      preview(en[n], dir);
      return { item: en[n], bumped: false };
    }

    function onWheel(ev) {
      if (isOff() || isOpen() || pressing || dragging) return;
      const el = trigOf(ev);
      if (el) lastTrig = el;
      if (ev && ev.preventDefault) ev.preventDefault();
      if (ev && ev.stopPropagation) ev.stopPropagation();
      const dy = ev && ev.deltaY != null ? Number(ev.deltaY) : 0;
      if (!dy) return;
      if (committed == null) committed = current();
      wheelAcc += dy;
      const t = nowFn();
      if (t - lastStepAt < THROTTLE_MS) return;
      if (wheelAcc >= WHEEL_UNIT) {
        wheelAcc -= WHEEL_UNIT;
        lastStepAt = t;
        const r = step(1, el);
        if (r && r.bumped) wheelAcc = 0;
        else if (r && r.item) scheduleCommit(r.item);
      } else if (wheelAcc <= -WHEEL_UNIT) {
        wheelAcc += WHEEL_UNIT;
        lastStepAt = t;
        const r = step(-1, el);
        if (r && r.bumped) wheelAcc = 0;
        else if (r && r.item) scheduleCommit(r.item);
      }
    }

    function bindDocKey() {
      if (docKeyOn) return;
      document.addEventListener('keydown', onDocKey, true);
      document.addEventListener('pointerdown', onDocAux, true);
      docKeyOn = true;
    }

    function unbindDocKey() {
      if (!docKeyOn) return;
      document.removeEventListener('keydown', onDocKey, true);
      document.removeEventListener('pointerdown', onDocAux, true);
      docKeyOn = false;
    }

    function restoreStart() {
      clearTimer('commit');
      pendingCommit = null;
      lastPreview = startItem;
      if (startItem != null && opts.onPreview) opts.onPreview(startItem);
      if (opts.onCancel) opts.onCancel();
      api.posHint = '';
      api.slide = '';
      applyTrig();
    }

    function endPress() {
      pressing = false;
      dragging = false;
      api.dragging = false;
      applyTrig();
      unbindDocKey();
      if (captureEl && captureEl.releasePointerCapture && pointerId != null) {
        try { captureEl.releasePointerCapture(pointerId); } catch (e) { /* 已经丢了 */ }
      }
      captureEl = null;
      pointerId = null;
    }

    function cancelPress() {
      if (!pressing && !dragging) return;
      cancelled = true;
      restoreStart();
      endPress();
    }

    function onDocKey(ev) {
      if (!pressing && !dragging) return;
      if (ev.key === 'Escape' || ev.key === 'Esc') {
        if (ev.preventDefault) ev.preventDefault();
        if (ev.stopPropagation) ev.stopPropagation();
        if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
        cancelPress();
      }
    }

    function onDocAux(ev) {
      if (!pressing && !dragging) return;
      if (ev.button === 2 || ev.button === 1) {
        if (ev.preventDefault) ev.preventDefault();
        cancelPress();
      }
    }

    function onPointerDown(ev) {
      if (isOff()) return;
      if (ev && ev.button != null && ev.button !== 0) return;
      const el = trigOf(ev);
      if (el) lastTrig = el;
      if (isOpen()) {
        // 菜单开着：不拖、不滚，松手当点击（关菜单）
        pressing = false;
        dragging = false;
        cancelled = false;
        startX = ev.clientX || 0;
        startY = ev.clientY || 0;
        eatClick = false;
        return;
      }
      pressing = true;
      dragging = false;
      cancelled = false;
      eatClick = false;
      startX = ev.clientX || 0;
      startY = ev.clientY || 0;
      startEnabled = enabledOf(items());
      startItem = startEnabled.find(it => sameVal(it, current())) || startEnabled[0] || null;
      startIdx = indexOfEnabled(startEnabled, startItem);
      if (startIdx < 0) startIdx = 0;
      committed = current();
      lastPreview = startItem;
      pointerId = ev.pointerId != null ? ev.pointerId : 1;
      captureEl = el;
      if (el && el.setPointerCapture) {
        try { el.setPointerCapture(pointerId); } catch (e) { /* 测试桩可能没有 */ }
      }
      bindDocKey();
    }

    function dragTo(ev) {
      if (!pressing || cancelled || isOpen()) return;
      const x = ev.clientX || 0;
      const dx = x - startX;
      const adx = Math.abs(dx);
      const ady = Math.abs((ev.clientY || 0) - startY);
      if (!dragging) {
        if (adx < CLICK_PX && ady < CLICK_PX) return;
        if (adx < ady) return; // 更像纵向，先不抢
        dragging = true;
        api.dragging = true;
        applyTrig();
        if (ev.preventDefault) ev.preventDefault();
      }
      if (!dragging) return;
      if (ev.preventDefault) ev.preventDefault();
      if (!startEnabled.length) { bump(); return; }
      const steps = Math.trunc(dx / STEP_PX);
      // 右 = 下一项，左 = 上一项。向 0 取整：27px 还不算一格。
      let idx = startIdx + steps;
      let bumped = false;
      if (idx < 0) { idx = 0; bumped = steps < 0; }
      if (idx >= startEnabled.length) { idx = startEnabled.length - 1; bumped = steps > 0; }
      const hit = startEnabled[idx];
      if (bumped && sameVal(hit, lastPreview)) bump();
      else if (hit && !sameVal(hit, lastPreview)) {
        const dir = idx > startIdx ? 1 : (idx < startIdx ? -1 : (steps > 0 ? 1 : -1));
        preview(hit, dir);
      }
    }

    function onPointerMove(ev) {
      if (cancelled) return;
      if (pressing) dragTo(ev);
    }

    function finishPointer(ev) {
      if (cancelled) {
        cancelled = false;
        eatClick = true;
        return;
      }
      if (isOpen()) {
        // 开着时这一下当点击，交给 onOpen 关菜单
        const dx = Math.abs((ev.clientX || 0) - startX);
        const dy = Math.abs((ev.clientY || 0) - startY);
        if (dx < CLICK_PX && dy < CLICK_PX && opts.onOpen) {
          eatClick = true;
          opts.onOpen();
        }
        return;
      }
      if (!pressing && !dragging) return;
      const wasDrag = dragging;
      const hit = lastPreview;
      endPress();
      if (wasDrag) {
        eatClick = true;
        if (ev && ev.preventDefault) ev.preventDefault();
        if (hit) commitNow(hit);
        return;
      }
      // 位移 < 6px：普通点击，打开菜单
      eatClick = true;
      if (opts.onOpen) opts.onOpen();
    }

    function onPointerUp(ev) {
      finishPointer(ev);
    }

    function onPointerCancel(ev) {
      if (pressing || dragging) cancelPress();
    }

    function onLostPointerCapture(ev) {
      if (pressing || dragging) finishPointer(ev || {});
    }

    function onClick(ev) {
      if (eatClick) {
        if (ev.preventDefault) ev.preventDefault();
        if (ev.stopPropagation) ev.stopPropagation();
        eatClick = false;
        return;
      }
      // 没有走过 pointerdown 的点击（键盘激活等）照常打开
      if (isOff()) return;
      if (opts.onOpen) opts.onOpen();
    }

    function onContextMenu(ev) {
      if (pressing || dragging) {
        if (ev.preventDefault) ev.preventDefault();
        cancelPress();
      }
    }

    api.onWheel = onWheel;
    api.onPointerDown = onPointerDown;
    api.onPointerMove = onPointerMove;
    api.onPointerUp = onPointerUp;
    api.onPointerCancel = onPointerCancel;
    api.onLostPointerCapture = onLostPointerCapture;
    api.onClick = onClick;
    api.onContextMenu = onContextMenu;
    api.dispose = function dispose() {
      clearTimer('commit');
      clearTimer('hint');
      clearTimer('bump');
      clearTimer('slide');
      unbindDocKey();
      pressing = false;
      dragging = false;
      api.dragging = false;
      api.bump = false;
      api.posHint = '';
      api.slide = '';
    };
    return api;
  }

  window.usePickerGestures = usePickerGestures;
  window.PickGest = {
    usePickerGestures,
    WHEEL_UNIT, THROTTLE_MS, COMMIT_MS, STEP_PX, CLICK_PX, HINT_MS, BUMP_MS,
  };
})();
