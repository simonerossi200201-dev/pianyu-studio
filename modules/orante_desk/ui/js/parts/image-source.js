// 规格 81：图片来源单元格的判别 / 粘贴 / 拖放 / 徽章 / 探测缓存。
// 不碰 DOM；tasks.js 负责画格子。测试直接挂这份。
window.ImageSourceLogic = (function () {
  const CACHE_MS = 60000;
  const DEBOUNCE_MS = 400;
  const IMG_EXT = /\.(jpe?g|png|webp)$/i;

  function stripQuotes(s) {
    return String(s == null ? '' : s).trim().replace(/^["']+|["']+$/g, '');
  }

  function looksLikeWinPath(s) {
    const v = stripQuotes(s);
    if (!v) return false;
    if (/^[a-zA-Z]:[\\/]/.test(v)) return true;
    if (v.indexOf('\\\\') === 0) return true;
    return false;
  }

  function looksLikeUrl(s) {
    return /^https?:\/\//i.test(String(s || '').trim());
  }

  function looksLikeDrive(s) {
    const v = String(s || '').toLowerCase();
    return v.indexOf('drive.google.com') >= 0
      || v.indexOf('docs.google.com') >= 0
      || v.indexOf('drive.usercontent.google.com') >= 0;
  }

  function guessProbe(src) {
    const v = stripQuotes(src);
    if (!v) return null;
    if (looksLikeUrl(v) && looksLikeDrive(v)) {
      if (/\/folders\//i.test(v)) {
        return { kind: 'drive_folder', ok: true, n: 0, n_deep: 0, sample: [], note: '' };
      }
      if (/\/file\/d\//i.test(v) || /[?&]id=/i.test(v) || /\/uc\?/i.test(v)) {
        return { kind: 'drive_file', ok: true, n: 0, n_deep: 0, sample: [], note: '' };
      }
      return { kind: 'drive_folder', ok: true, n: 0, n_deep: 0, sample: [], note: '' };
    }
    if (looksLikeUrl(v)) return { kind: 'http', ok: true, n: 0, n_deep: 0, sample: [], note: '' };
    return null;
  }

  function dirname(p) {
    const s = String(p || '').replace(/\//g, '\\').replace(/\\+$/, '');
    const i = s.lastIndexOf('\\');
    if (i <= 0) return s;
    if (s.charAt(i - 1) === ':') return s; // C:\foo → keep parent C:\foo's parent as C:\
    return s.slice(0, i);
  }

  function commonParent(paths) {
    const list = (paths || []).map(p => String(p || '').replace(/\//g, '\\')).filter(Boolean);
    if (!list.length) return '';
    if (list.length === 1) return dirname(list[0]);
    let prefix = dirname(list[0]);
    for (let i = 1; i < list.length; i++) {
      let d = dirname(list[i]);
      while (prefix && d.toLowerCase() !== prefix.toLowerCase()
        && d.toLowerCase().indexOf(prefix.toLowerCase() + '\\') !== 0) {
        const next = dirname(prefix);
        if (next === prefix) { prefix = ''; break; }
        prefix = next;
      }
    }
    return prefix;
  }

  function pickPasteValue(field, textVal, linkCell, linkPrefer) {
    const text = textVal == null ? '' : String(textVal);
    const stripped = stripQuotes(text);
    if (linkPrefer && linkPrefer.has && linkPrefer.has(field) && looksLikeWinPath(stripped)) {
      return { val: stripped, from: '', linked: false, path: true };
    }
    if (linkPrefer && linkPrefer.has && linkPrefer.has(field) && linkCell && linkCell.href) {
      const href = String(linkCell.href);
      if (looksLikeWinPath(href)) {
        return { val: stripQuotes(href), from: '', linked: false, path: true };
      }
      return { val: href, from: linkCell.text || text, linked: true, path: false };
    }
    return { val: text, from: '', linked: false, path: looksLikeWinPath(stripped) };
  }

  function folderFromDrop(parsed) {
    const files = (parsed && parsed.files) || [];
    const text = stripQuotes((parsed && parsed.text) || '');
    const dirs = files.filter(f => f && f.isDir && f.path);
    if (dirs.length === 1) return { folder: dirs[0].path, n: 0, fromFiles: false };
    if (dirs.length > 1) {
      const folder = commonParent(dirs.map(d => d.path + '\\x'));
      return { folder: folder, n: 0, fromFiles: false };
    }
    const imgs = files.filter(f => f && f.path && !f.isDir && IMG_EXT.test(f.name || f.path));
    if (imgs.length) {
      return { folder: commonParent(imgs.map(f => f.path)), n: imgs.length, fromFiles: true };
    }
    const any = files.filter(f => f && f.path);
    if (any.length === 1 && any[0].isDir) return { folder: any[0].path, n: 0, fromFiles: false };
    if (looksLikeWinPath(text)) return { folder: text, n: 0, fromFiles: false };
    return null;
  }

  function parseDropEvent(ev) {
    const files = [];
    const dt = ev && ev.dataTransfer;
    if (!dt) return { files: [], text: '' };
    const items = dt.items;
    const fileList = dt.files;
    if (items && items.length) {
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        let entry = null;
        try { entry = it.webkitGetAsEntry && it.webkitGetAsEntry(); } catch (e) { entry = null; }
        let f = null;
        try { f = it.getAsFile && it.getAsFile(); } catch (e) { f = null; }
        const path = (f && (f.path || '')) || '';
        files.push({
          path: path,
          name: (f && f.name) || (entry && entry.name) || '',
          isDir: !!(entry && entry.isDirectory),
        });
      }
    } else if (fileList && fileList.length) {
      for (let i = 0; i < fileList.length; i++) {
        const f = fileList[i];
        files.push({
          path: f.path || '',
          name: f.name || '',
          isDir: !f.type && !f.size,
        });
      }
    }
    let text = '';
    try { text = dt.getData('text/plain') || ''; } catch (e) { text = ''; }
    return { files: files, text: text };
  }

  function badgeView(p) {
    if (!p) return { icon: '⚠', text: '认不出', extra: '', tone: 'bad', include: false };
    if (p.kind === 'drive_folder') {
      return { icon: '☁', text: '网盘', extra: '文件夹', tone: 'ok', include: false };
    }
    if (p.kind === 'drive_file') {
      return { icon: '☁', text: '网盘', extra: '单文件', tone: 'ok', include: false };
    }
    if (p.kind === 'http') {
      return { icon: '🔗', text: '直链', extra: '', tone: 'ok', include: false };
    }
    if (p.kind === 'local_file') {
      return { icon: '📄', text: '本地', extra: '', tone: p.ok ? 'ok' : 'bad', include: false };
    }
    if (p.kind === 'local_dir') {
      const nShow = p.recursive ? (p.n_deep || 0) : (p.n || 0);
      if (nShow > 0) {
        return { icon: '📁', text: '本地', extra: nShow + ' 张', tone: 'ok', include: false };
      }
      if (!p.recursive && (p.n_deep || 0) > 0) {
        return {
          icon: '⚠', text: '顶层 0 张，子文件夹里有 ' + p.n_deep + ' 张',
          extra: '', tone: 'warn', include: true,
        };
      }
      return { icon: '⚠', text: p.note || '文件夹里 0 张图片', extra: '', tone: 'bad', include: false };
    }
    if (p.kind === 'local_missing' || (p.note && p.note.indexOf('找不到') >= 0)) {
      return { icon: '⚠', text: p.note || '找不到这个文件夹', extra: '', tone: 'bad', include: false };
    }
    return { icon: '⚠', text: p.note || '认不出', extra: '', tone: 'bad', include: false };
  }

  function badgeText(view) {
    const v = view || badgeView(null);
    if (v.text === '本地' && v.extra) return v.icon + ' ' + v.text + ' · ' + v.extra;
    if (v.text === '网盘' && v.extra) return v.icon + ' ' + v.text;
    if (v.extra && v.tone === 'ok' && v.text !== '本地') return v.icon + ' ' + v.text;
    return v.icon + (v.text ? ' ' + v.text : '');
  }

  function sampleTitle(p) {
    const names = (p && p.sample) || [];
    return names.filter(Boolean).join('\n');
  }

  function localModeOf(row, settings) {
    const m = row && row.meta && row.meta.image_src_mode;
    if (m === 'copy' || m === 'copy_mark' || m === 'move') return m;
    const g = settings && settings['images.local_mode'];
    if (g === 'copy' || g === 'copy_mark' || g === 'move') return g;
    return 'copy';
  }

  function prepareReason(row, probe, mode) {
    const p = probe || {};
    if (!p.ok) {
      if (p.kind === 'local_missing' || (p.note && p.note.indexOf('找不到') >= 0)) {
        const path = p.path || (row && row.image_source) || '';
        return path ? ('找不到文件夹 ' + path) : (p.note || '找不到这个文件夹');
      }
      return p.note || '认不出图片来源';
    }
    if (p.kind === 'local_dir') {
      const n = p.recursive ? (p.n_deep || 0) : (p.n || 0);
      if (p.recursive) return '素材 ' + n + ' 张（本地 · 包含子文件夹）';
      const lab = mode === 'move' ? '移动' : (mode === 'copy_mark' ? '复制并标记' : '复制');
      return '素材 ' + n + ' 张（本地 · ' + lab + '）';
    }
    if (p.kind === 'local_file') return '本地单文件';
    if (p.kind === 'drive_folder') return 'Drive 文件夹';
    if (p.kind === 'drive_file') return 'Drive 单文件';
    if (p.kind === 'http') return '直链';
    return p.kind || '';
  }

  function splitPrepare(items) {
    const ready = [];
    const blocked = [];
    (items || []).forEach(it => {
      if (it && it.ok) ready.push(it);
      else blocked.push(it);
    });
    return { ready: ready, blocked: blocked };
  }

  function createProbe(opts) {
    const post = opts && opts.post;
    const nowFn = (opts && opts.now) || (() => Date.now());
    const cacheMs = (opts && opts.cacheMs != null) ? opts.cacheMs : CACHE_MS;
    const debounceMs = (opts && opts.debounceMs != null) ? opts.debounceMs : DEBOUNCE_MS;
    const cache = new Map();
    const requests = [];
    let timer = 0;
    let pending = null;
    let seq = 0;

    function keyOf(src, recursive) {
      return stripQuotes(src) + '\0' + (recursive ? '1' : '0');
    }

    async function probeNow(src, recursive) {
      const raw = stripQuotes(src);
      const rec = !!recursive;
      const k = keyOf(raw, rec);
      const now = nowFn();
      const hit = cache.get(k);
      if (hit && now - hit.at < cacheMs) return hit.data;
      requests.push({ src: raw, recursive: rec });
      let data = post ? await post('/api/images/probe-source', { src: raw, recursive: rec }) : {};
      data = data || {};
      if (data.src_ok != null) data = Object.assign({}, data, { ok: data.src_ok });
      cache.set(k, { at: nowFn(), data: data });
      return data;
    }

    function schedule(src, recursive, cb) {
      const my = ++seq;
      pending = { src: src, recursive: !!recursive, cb: cb, seq: my };
      if (timer) {
        try { clearTimeout(timer); } catch (e) { /* node 测试自己 flush */ }
      }
      const wait = debounceMs;
      if (wait <= 0) {
        return probeNow(src, recursive).then(data => {
          if (pending && pending.seq === my && cb) cb(data);
          return data;
        });
      }
      timer = setTimeout(() => {
        timer = 0;
        const job = pending;
        if (!job || job.seq !== my) return;
        probeNow(job.src, job.recursive).then(data => {
          if (pending && pending.seq === job.seq && job.cb) job.cb(data);
        });
      }, wait);
    }

    async function flush() {
      if (timer) {
        try { clearTimeout(timer); } catch (e) { /* ignore */ }
        timer = 0;
      }
      const job = pending;
      if (!job) return null;
      const data = await probeNow(job.src, job.recursive);
      if (pending && pending.seq === job.seq && job.cb) job.cb(data);
      return data;
    }

    return {
      probeNow: probeNow,
      schedule: schedule,
      flush: flush,
      requests: requests,
      cache: cache,
      CACHE_MS: cacheMs,
      DEBOUNCE_MS: debounceMs,
    };
  }

  return {
    CACHE_MS: CACHE_MS,
    DEBOUNCE_MS: DEBOUNCE_MS,
    stripQuotes: stripQuotes,
    looksLikeWinPath: looksLikeWinPath,
    looksLikeUrl: looksLikeUrl,
    looksLikeDrive: looksLikeDrive,
    guessProbe: guessProbe,
    dirname: dirname,
    commonParent: commonParent,
    pickPasteValue: pickPasteValue,
    folderFromDrop: folderFromDrop,
    parseDropEvent: parseDropEvent,
    badgeView: badgeView,
    badgeText: badgeText,
    sampleTitle: sampleTitle,
    localModeOf: localModeOf,
    prepareReason: prepareReason,
    splitPrepare: splitPrepare,
    createProbe: createProbe,
  };
})();
