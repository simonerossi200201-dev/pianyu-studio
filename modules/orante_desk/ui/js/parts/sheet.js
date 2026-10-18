// 表格互贴 / 填充柄的纯函数：不碰 DOM，方便 Node 单测。
// TSV 规则对齐 Google Sheet：引号包起来的字段里，换行和 Tab 都是内容；"" = 一个字面双引号。
(function (root) {
  const LIST_FIELDS = { type: 1, account: 1, duration_pref: 1, status: 1 };
  const SKIP_FIELDS = { _sel: 1, _uid: 1, _error: 1, _warn: 1 };

  function parseTSVGrid(text) {
    text = String(text == null ? '' : text).replace(/^\uFEFF/, '');
    if (!text) return [];
    const rows = [];
    let row = [];
    let cur = '';
    let inQ = false;
    const n = text.length;
    let i = 0;
    while (i < n) {
      const ch = text[i];
      if (inQ) {
        if (ch === '"') {
          if (text[i + 1] === '"') { cur += '"'; i += 2; continue; }
          inQ = false; i += 1; continue;
        }
        cur += ch; i += 1; continue;
      }
      if (ch === '"') { inQ = true; i += 1; continue; }
      if (ch === '\t') { row.push(cur); cur = ''; i += 1; continue; }
      if (ch === '\r') {
        if (text[i + 1] === '\n') i += 1;
        row.push(cur); rows.push(row); row = []; cur = ''; i += 1; continue;
      }
      if (ch === '\n') {
        row.push(cur); rows.push(row); row = []; cur = ''; i += 1; continue;
      }
      cur += ch; i += 1;
    }
    if (inQ || cur.length || row.length) { row.push(cur); rows.push(row); }
    while (rows.length && rows[rows.length - 1].every(c => c === '')) rows.pop();
    return rows;
  }

  function quoteField(s) {
    s = String(s == null ? '' : s);
    if (/[\t\n\r"]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function serializeTSV(grid) {
    if (!grid || !grid.length) return '';
    return grid.map(row => (row || []).map(quoteField).join('\t')).join('\n');
  }

  function parseToken(s) {
    s = String(s == null ? '' : s);
    if (s === '') return { kind: 'empty', raw: s, prefix: '', n: 0, width: 0 };
    if (/^\d+$/.test(s)) {
      return { kind: 'num', raw: s, prefix: '', n: parseInt(s, 10), width: s[0] === '0' ? s.length : 0 };
    }
    const m = s.match(/^(.*?)(\d+)$/);
    if (m) {
      return { kind: 'tail', raw: s, prefix: m[1], n: parseInt(m[2], 10), width: m[2].length };
    }
    return { kind: 'text', raw: s, prefix: '', n: 0, width: 0 };
  }

  function formatToken(tok, n) {
    if (tok.kind === 'text' || tok.kind === 'empty') return tok.raw;
    const neg = n < 0;
    const body = String(Math.abs(n));
    const digits = (tok.width > 0 && !neg) ? body.padStart(tok.width, '0') : body;
    return (tok.prefix || '') + (neg ? '-' + digits : digits);
  }

  function seriesInfo(src) {
    const tokens = src.map(parseToken);
    if (!tokens.length) return null;
    if (tokens.some(t => t.kind === 'text' || t.kind === 'empty')) return null;
    const prefix = tokens[0].prefix || '';
    if (tokens.some(t => (t.prefix || '') !== prefix)) return null;
    const width = Math.max.apply(null, tokens.map(t => t.width));
    if (tokens.length === 1) {
      return { step: 1, width, prefix, kind: tokens[0].kind };
    }
    const steps = [];
    for (let i = 1; i < tokens.length; i++) steps.push(tokens[i].n - tokens[i - 1].n);
    if (!steps.length || steps.some(s => s !== steps[0]) || steps[0] === 0) return null;
    return { step: steps[0], width, prefix, kind: tokens[0].kind };
  }

  // src: 源区沿填充方向的值。dir=1 往下/右（紧挨源区的下一格是 out[0]）；dir=-1 往上/左。
  // mode 'copy' 一律复制；'auto' 能认出规律就递增。
  function fillValues(src, count, dir, mode, isList) {
    src = (src || []).map(v => (v == null ? '' : String(v)));
    count = Number(count) || 0;
    if (count <= 0 || !src.length) return [];
    const copy = mode === 'copy' || isList;
    if (!copy) {
      const info = seriesInfo(src);
      if (info) {
        const tokens = src.map(parseToken);
        const out = [];
        const tok0 = { kind: tokens[0].kind, prefix: info.prefix, width: info.width, raw: '' };
        if (dir === 1) {
          const last = tokens[tokens.length - 1].n;
          for (let i = 1; i <= count; i++) out.push(formatToken(tok0, last + info.step * i));
        } else {
          const first = tokens[0].n;
          for (let i = 1; i <= count; i++) out.push(formatToken(tok0, first - info.step * i));
        }
        return out;
      }
    }
    const out = [];
    const L = src.length;
    for (let i = 0; i < count; i++) {
      if (dir === 1) out.push(src[i % L]);
      else out.push(src[(L - 1 - (i % L) + L) % L]);
    }
    return out;
  }

  // 选区 R×C，粘贴 r×c。单格：铺开到 r×c。整数倍：平铺填满选区。否则从左上贴一次。
  function pastePlan(selRows, selCols, dataRows, dataCols, singleCell) {
    selRows = Math.max(1, selRows | 0);
    selCols = Math.max(1, selCols | 0);
    dataRows = Math.max(1, dataRows | 0);
    dataCols = Math.max(1, dataCols | 0);
    if (singleCell || (selRows === 1 && selCols === 1)) {
      return { destRows: dataRows, destCols: dataCols, tile: false, addRows: true };
    }
    if (selRows % dataRows === 0 && selCols % dataCols === 0) {
      return { destRows: selRows, destCols: selCols, tile: true, addRows: false };
    }
    return { destRows: dataRows, destCols: dataCols, tile: false, addRows: true };
  }

  function pasteValue(grid, r, c) {
    const rr = grid.length ? r % grid.length : 0;
    const row = grid[rr] || [];
    const cc = row.length ? c % row.length : 0;
    return row[cc] == null ? '' : String(row[cc]);
  }

  const HEADER_KEYS = {
    '任务名': 'name', '名称': 'name', name: 'name',
    '文案': 'copy', copy: 'copy', '内容': 'copy',
    '图片': 'image_source', '图片链接': 'image_source', '链接': 'image_source',
    image: 'image_source', '图片来源': 'image_source',
    '类型': 'type', '任务类型': 'type', type: 'type',
    '账号': 'account', account: 'account',
    '时长': 'duration_pref', duration: 'duration_pref',
    '#': 'id', id: 'id',
  };

  function looksLikeHeader(row) {
    if (!row || !row.length) return false;
    let n = 0;
    for (const c of row) {
      const k = String(c || '').trim().toLowerCase();
      if (k && (k in HEADER_KEYS || HEADER_KEYS[String(c || '').trim()])) n += 1;
    }
    return n >= 1;
  }

  function stripHeaderRow(grid) {
    if (grid && grid.length && looksLikeHeader(grid[0])) return grid.slice(1);
    return grid || [];
  }

  // 规格 45：从剪贴板 HTML 里抠超链接。Google Sheet 的网址只在 text/html 里。
  // 2026 实测：没有 data-sheets-hyperlink，链接在 td > a.in-cell-link[href]。
  // 三条都认：data-sheets-hyperlink → a[href] → data-sheets-formula 的 =HYPERLINK("url")。
  function decodeEntities(s) {
    return String(s == null ? '' : s)
      .replace(/&nbsp;/gi, ' ')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&#x27;/gi, "'");
  }

  function hrefFromFormula(s) {
    const raw = String(s == null ? '' : s);
    const m = raw.match(/^\s*=\s*HYPERLINK\s*\(\s*"([^"]*)"/i)
      || raw.match(/^\s*=\s*HYPERLINK\s*\(\s*'([^']*)'/i);
    return m ? m[1] : '';
  }

  // Sheet 经常把真实地址包成 google.com/url?q=…，把 q 解出来。
  function unwrapGoogleUrl(href) {
    const raw = String(href == null ? '' : href).trim();
    if (!raw) return '';
    try {
      const u = new URL(raw);
      if (u.hostname === 'www.google.com' && u.pathname === '/url') {
        const q = u.searchParams.get('q');
        if (q) return q;
      }
    } catch (e) { /* 不是合法 URL 就原样 */ }
    return raw;
  }

  function attrOf(el, name) {
    if (!el) return '';
    if (typeof el.getAttribute === 'function') return el.getAttribute(name) || '';
    return '';
  }

  function cellFromTd(td) {
    const text = String((td && td.textContent) || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
    let href = attrOf(td, 'data-sheets-hyperlink');
    if (!href && td && typeof td.querySelector === 'function') {
      const a = td.querySelector('a[href]');
      if (a) href = attrOf(a, 'href');
    }
    if (!href) href = hrefFromFormula(attrOf(td, 'data-sheets-formula'));
    href = href ? unwrapGoogleUrl(decodeEntities(href)) : '';
    return { text: text, href: href || null };
  }

  function expandSpanRows(rawRows) {
    const occ = [];
    const out = [];
    function mark(r, c) {
      while (out.length <= r) { out.push([]); occ.push([]); }
      while (out[r].length <= c) { out[r].push(null); occ[r].push(false); }
    }
    for (let r = 0; r < rawRows.length; r++) {
      let c = 0;
      const cells = rawRows[r] || [];
      for (let k = 0; k < cells.length; k++) {
        mark(r, c);
        while (occ[r][c]) { c += 1; mark(r, c); }
        const cell = cells[k] || {};
        const cs = Math.max(1, Number(cell.colspan) || 1);
        const rs = Math.max(1, Number(cell.rowspan) || 1);
        const packed = { text: cell.text || '', href: cell.href || null };
        for (let i = 0; i < rs; i++) {
          for (let j = 0; j < cs; j++) {
            mark(r + i, c + j);
            occ[r + i][c + j] = true;
            out[r + i][c + j] = packed;
          }
        }
        c += cs;
      }
    }
    const width = out.reduce((m, row) => Math.max(m, row.length), 0);
    for (let i = 0; i < out.length; i++) {
      while (out[i].length < width) out[i].push({ text: '', href: null });
      for (let j = 0; j < out[i].length; j++) {
        if (!out[i][j]) out[i][j] = { text: '', href: null };
      }
    }
    return out;
  }

  function collectTrList(table) {
    const out = [];
    const kids = table.children || [];
    for (let i = 0; i < kids.length; i++) {
      const el = kids[i];
      const tag = (el.tagName || '').toLowerCase();
      if (tag === 'tr') out.push(el);
      else if (tag === 'tbody' || tag === 'thead' || tag === 'tfoot') {
        const inner = el.children || [];
        for (let j = 0; j < inner.length; j++) {
          if ((inner[j].tagName || '').toLowerCase() === 'tr') out.push(inner[j]);
        }
      }
    }
    return out;
  }

  function collectTdList(tr) {
    const out = [];
    const kids = tr.children || [];
    for (let i = 0; i < kids.length; i++) {
      const tag = (kids[i].tagName || '').toLowerCase();
      if (tag === 'td' || tag === 'th') out.push(kids[i]);
    }
    return out;
  }

  function expandHtmlTable(table) {
    const trs = collectTrList(table);
    const raw = [];
    for (let i = 0; i < trs.length; i++) {
      const tds = collectTdList(trs[i]);
      const row = [];
      for (let j = 0; j < tds.length; j++) {
        const td = tds[j];
        const packed = cellFromTd(td);
        packed.colspan = Math.max(1, parseInt(attrOf(td, 'colspan') || '1', 10) || 1);
        packed.rowspan = Math.max(1, parseInt(attrOf(td, 'rowspan') || '1', 10) || 1);
        row.push(packed);
      }
      raw.push(row);
    }
    return expandSpanRows(raw);
  }

  function parseHtmlTableLite(html) {
    const m = String(html || '').match(/<table\b[^>]*>([\s\S]*?)<\/table>/i);
    if (!m) return null;
    const body = m[1];
    const raw = [];
    const trRe = /<tr\b[^>]*>([\s\S]*?)<\/tr>/gi;
    let tr;
    while ((tr = trRe.exec(body))) {
      const row = [];
      const cellRe = /<(td|th)\b([^>]*)>([\s\S]*?)<\/\1>/gi;
      let c;
      while ((c = cellRe.exec(tr[1]))) {
        const attrs = c[2] || '';
        const inner = c[3] || '';
        const span = name => {
          const hit = attrs.match(new RegExp(name + '\\s*=\\s*["\']?(\\d+)', 'i'));
          return Math.max(1, (hit && parseInt(hit[1], 10)) || 1);
        };
        let href = '';
        const hy = attrs.match(/data-sheets-hyperlink\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
        if (hy) href = hy[1] || hy[2] || '';
        if (!href) {
          const a = inner.match(/<a\b[^>]*href\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
          if (a) href = a[1] || a[2] || '';
        }
        if (!href) {
          const f = attrs.match(/data-sheets-formula\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
          href = hrefFromFormula(decodeEntities(f ? (f[1] || f[2] || '') : ''));
        }
        const text = decodeEntities(inner.replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, ''))
          .replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
        row.push({
          text: text,
          href: href ? unwrapGoogleUrl(decodeEntities(href)) : null,
          colspan: span('colspan'),
          rowspan: span('rowspan'),
        });
      }
      raw.push(row);
    }
    const grid = expandSpanRows(raw);
    return grid.length ? grid : null;
  }

  function parseHtmlGridLinks(html) {
    html = String(html == null ? '' : html);
    if (!html.trim()) return null;
    try {
      if (typeof DOMParser !== 'undefined') {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const table = doc.querySelector('table');
        if (!table) return null;
        const grid = expandHtmlTable(table);
        return grid && grid.length ? grid : null;
      }
    } catch (e) { /* DOMParser 失败就走轻量解析 */ }
    return parseHtmlTableLite(html);
  }

  function firstHrefFromHtml(html) {
    const grid = parseHtmlGridLinks(html);
    if (grid) {
      for (let i = 0; i < grid.length; i++) {
        const row = grid[i] || [];
        for (let j = 0; j < row.length; j++) {
          if (row[j] && row[j].href) return row[j].href;
        }
      }
    }
    const a = String(html || '').match(/<a\b[^>]*href\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
    if (a) return unwrapGoogleUrl(decodeEntities(a[1] || a[2] || ''));
    const hy = String(html || '').match(/data-sheets-hyperlink\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
    if (hy) return unwrapGoogleUrl(decodeEntities(hy[1] || hy[2] || ''));
    return null;
  }

  function pasteLink(grid, r, c) {
    if (!grid || !grid.length) return null;
    const rr = r % grid.length;
    const row = grid[rr] || [];
    if (!row.length) return null;
    const cc = c % row.length;
    return row[cc] || null;
  }

  // 纯文本网格和 HTML 网格必须同行；HTML 可以更宽（尾列空格），但不能更窄。
  function linksAlign(textGrid, linkGrid) {
    if (!textGrid || !linkGrid || !textGrid.length) return false;
    if (textGrid.length !== linkGrid.length) return false;
    const tCols = textGrid.reduce((m, row) => Math.max(m, (row || []).length), 0);
    const lCols = linkGrid.reduce((m, row) => Math.max(m, (row || []).length), 0);
    if (lCols < tCols) return false;
    if (lCols > tCols) {
      for (let i = 0; i < linkGrid.length; i++) {
        const row = linkGrid[i] || [];
        for (let j = tCols; j < row.length; j++) {
          const cell = row[j];
          if (cell && String(cell.text || '').trim()) return false;
        }
      }
    }
    return true;
  }

  const api = {
    parseTSVGrid, serializeTSV, quoteField, parseToken, formatToken,
    seriesInfo, fillValues, pastePlan, pasteValue, looksLikeHeader, stripHeaderRow,
    unwrapGoogleUrl, parseHtmlGridLinks, firstHrefFromHtml, pasteLink, linksAlign,
    LIST_FIELDS, SKIP_FIELDS, HEADER_KEYS,
  };
  root.TasksSheet = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
