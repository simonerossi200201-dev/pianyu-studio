// 所有 <img>/<video> src 走这里：路径 + 毫秒 mtime，不用随机数、不用旧 sha1。
// 同路径换了内容，mtime 变了，浏览器才会丢掉缓存。
window.srcWithV = function srcWithV(relPath, mtimeMs, tid) {
  tid = tid || (window.ui && ui.panelTid) || '';
  const rel = String(relPath || '').replace(/\\/g, '/').split('/').filter(Boolean)
    .map(encodeURIComponent).join('/');
  let url = '/media/' + encodeURIComponent(tid) + '/' + rel;
  const v = Number(mtimeMs);
  if (Number.isFinite(v) && v > 0) url += '?v=' + String(Math.trunc(v));
  return url;
};
