// 每个 pNN 固定色：按编号在色环上均匀取。编排 / 收片共用。
// b 开头（空镜编号）固定灰色，不进色环。
window.pcolor = function (p) {
  const s = String(p || '');
  if (/^b/i.test(s)) return '#6E6860';
  const n = parseInt(s.replace(/^p/i, ''), 10);
  if (!Number.isFinite(n) || n < 1) return '#3D372E';
  const hue = ((n - 1) * 37) % 360;
  return 'hsl(' + hue + ' 48% 46%)';
};
