// 规格 15 / 75：右键菜单定位。超出视口自动翻边。编排 / 多模态共用，不要再复制一份。
window.placeMenu = function placeMenu(x, y, w, h, vw, vh, pad) {
  pad = pad == null ? 8 : pad;
  w = w || 0; h = h || 0;
  let left = x, top = y;
  if (left + w > vw - pad) left = vw - w - pad;
  if (top + h > vh - pad) top = vh - h - pad;
  if (left < pad) left = pad;
  if (top < pad) top = pad;
  return { left, top };
};

// 先按估算尺寸翻边，nextTick 量到真实宽高再校正一次（和编排面板原来的两步一样）。
window.openCtxMenu = function openCtxMenu(ev, menu, menuRef, nextTick, guessW, guessH) {
  if (ev && ev.preventDefault) ev.preventDefault();
  if (ev && ev.stopPropagation) ev.stopPropagation();
  menu.show = true;
  const vw = (typeof window !== 'undefined' && window.innerWidth) || 0;
  const vh = (typeof window !== 'undefined' && window.innerHeight) || 0;
  const cx = ev && ev.clientX != null ? ev.clientX : 0;
  const cy = ev && ev.clientY != null ? ev.clientY : 0;
  const guess = window.placeMenu(cx, cy, guessW || 180, guessH || 180, vw, vh, 8);
  menu.x = guess.left;
  menu.y = guess.top;
  const tick = typeof nextTick === 'function' ? nextTick : (fn) => fn();
  tick(() => {
    const el = menuRef && menuRef.value;
    if (!el) return;
    const p = window.placeMenu(cx, cy, el.offsetWidth, el.offsetHeight, vw, vh, 8);
    menu.x = p.left;
    menu.y = p.top;
  });
};
