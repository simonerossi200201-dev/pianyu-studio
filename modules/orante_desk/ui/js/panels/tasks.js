// 节点 ① 任务面板：一张内置电子表格，一行 = 一个任务。
// 粘贴解析与后端 orante.tasks.parse_tsv 对齐（无表头按 名/文案/图片/类型/账号/时长）。
// 规格 28：Sheet 式选区 / 粘贴 / 填充柄 / 复选框刷选 / 查找替换 / 撤销。
// 规格 39：列宽随窗口 fitColumns；拖行不用幽灵行，改画落点线和跟手标签。
// 规格 41：文案列不再内联编辑，改弹层；编辑态 CSS / 填充柄 / 框选让路。
// 规格 42：去抖自动保存 + 关窗落盘 + 崩溃快照；有草稿行时 store 刷新不许整表覆盖。
// 规格 43：Ctrl+X 当场剪切；拖选区边框移动 / Ctrl 复制；右键补单元格菜单。
// 规格 44：工具条分组统一规格；粘贴/导入合成「导入…」对话框（列对应可改，追加不是覆盖）。
// 规格 45：粘贴同时读 text/html，图片来源列用超链接网址而不是显示名。
window.Panel_tasks = (() => {
  const COLUMNS = ['name', 'copy', 'image_source', 'type', 'account', 'duration_pref', 'gen_mode'];
  const PASTEABLE = COLUMNS.slice();
  const TYPE_ALIASES = {
    '长文案': 'long', long: 'long', '长': 'long', l: 'long',
    '短文案': 'short', short: 'short', '短': 'short', normal: 'short', s: 'short',
  };
  const KEYMAP = {
    '任务名': 'name', '名称': 'name', name: 'name',
    '文案': 'copy', copy: 'copy', '内容': 'copy',
    '图片': 'image_source', '图片链接': 'image_source', '图片来源': 'image_source',
    '链接': 'image_source', image: 'image_source',
    '类型': 'type', '任务类型': 'type', type: 'type',
    '账号': 'account', account: 'account',
    '时长': 'duration_pref', duration: 'duration_pref',
    '生成方式': 'gen_mode', '方式': 'gen_mode', gen_mode: 'gen_mode',
  };
  // 规格 84：时长档位跟引擎走（与 orante.engines.caps.DURATIONS 对齐）
  // 本地模型（studio）：不说话的任务 4–15 秒随便定（这里列常用几档）；有文案的自动按 6/10/15 秒（学 Grok，段数少），手选 4/6/8/10/15
  const ENGINE_DURS = { flow: ['4', '6', '8'], grok: ['6', '10', '15'], studio: ['4', '6', '8', '10', '12', '15'] };
  const STUDIO_COPY_DURS = ['4', '6', '8', '10', '15'];
  const DUR_OK = new Set(['4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15']);
  const NO_COPY_DEFAULT = { flow: '8', grok: '10', studio: '8' };
  const ENGINE_COLOR = { flow: 'var(--flow)', grok: 'var(--grok)', studio: 'var(--studio)' };
  function accColor(a) { return ENGINE_COLOR[(a && a.engine) || 'flow'] || 'var(--flow)'; }
  const IMP_FIELDS = [
    { v: 'name', k: 'tasks.col.name' },
    { v: 'copy', k: 'tasks.col.copy' },
    { v: 'image_source', k: 'tasks.col.image' },
    { v: 'type', k: 'tasks.col.type' },
    { v: 'account', k: 'tasks.col.account' },
    { v: 'duration_pref', k: 'tasks.col.duration' },
    { v: 'gen_mode', k: 'tasks.col.gen_mode' },
  ];
  const UNDO_MAX = 50;
  const SAVE_MS = 1500;
  const DRAFT_MS = 800;
  const LIST_FIELDS = { type: 1, account: 1, duration_pref: 1, gen_mode: 1 };
  // 规格 45：这些列粘贴时优先用超链接网址，其余列只用显示文字。
  const LINK_PREFER_FIELDS = new Set(['image_source']);

  let _uid = 0;
  function uid() { return 'u' + Date.now().toString(36) + '-' + (++_uid); }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function typingTarget(el) {
    if (!el) return false;
    const tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
  }

  function normType(v) {
    const k = String(v || '').trim();
    return TYPE_ALIASES[k.toLowerCase()] || TYPE_ALIASES[k] || (k ? k : '');
  }

  function normDur(v) {
    const s = String(v == null ? '' : v).trim();
    if (!s) return '';
    const m = s.match(/^(\d+)\s*s?$/i);
    if (m && DUR_OK.has(m[1])) return m[1];
    return s;
  }

  function fieldOfHeader(cell) {
    const s = String(cell == null ? '' : cell).trim();
    if (!s) return '';
    return KEYMAP[s] || KEYMAP[s.toLowerCase()] || '';
  }

  // 导入对话框用：有 Tab 当 TSV（和 Google Sheet 复制一致），否则当 CSV。
  function parseDelimited(text, delim) {
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
      if (ch === delim) { row.push(cur); cur = ''; i += 1; continue; }
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

  function parseImportGrid(text) {
    text = String(text == null ? '' : text).replace(/^\uFEFF/, '');
    if (!String(text).trim()) return [];
    const delim = text.indexOf('\t') >= 0 ? '\t' : ',';
    let grid;
    if (delim === '\t' && typeof TasksSheet !== 'undefined' && TasksSheet.parseTSVGrid) {
      grid = TasksSheet.parseTSVGrid(text);
    } else {
      grid = parseDelimited(text, delim);
    }
    return (grid || []).filter(r => (r || []).some(c => String(c == null ? '' : c).trim()));
  }

  // 本地模型在表里也认这些写法（跟 orante.accounts.STUDIO_ALIASES 对齐）
  const STUDIO_ALIASES = new Set(['studio', '本地模型', 'studio zero', '本地模型 · studio zero', '本地模型·studio zero']);
  function accountBy(idOrName) {
    if (!idOrName) return null;
    const hit = store.accounts.find(a => a.id === idOrName || a.name === idOrName);
    if (hit) return hit;
    if (STUDIO_ALIASES.has(String(idOrName).trim().toLowerCase())) {
      return store.accounts.find(a => a.engine === 'studio') || null;
    }
    return null;
  }

  function engineOf(row) {
    if (row.engine) return row.engine;
    const a = accountBy(row.account || row.account_id);
    return a ? a.engine : '';
  }

  function hasCopy(d) {
    return !!(d && String(d.copy || '').trim());
  }

  function dursFor(d) {
    const eng = engineOf(d) || 'flow';
    if (eng === 'studio' && hasCopy(d)) return STUDIO_COPY_DURS;   // 有文案：4/6/8/10/15 档
    return ENGINE_DURS[eng] || ENGINE_DURS.flow;
  }

  function durationEditorValues(d) {
    const eng = engineOf(d) || 'flow';
    const def = NO_COPY_DEFAULT[eng] || '8';
    // 有文案：留空 = 按语速自动；没文案：留空 = 引擎默认秒数
    const emptyLab = hasCopy(d) ? (eng === 'studio' ? '自动（6 / 10 / 15 秒）' : '自动（按语速）') : ('自动（按 ' + def + ' 秒）');
    const vals = [{ label: emptyLab, value: '' }];
    for (const x of dursFor(d)) vals.push({ label: x + ' 秒', value: x });
    return vals;
  }

  function genModeCell(raw) {
    const p = window.GenModeLogic ? GenModeLogic.parseGenMode(raw) : String(raw || '').trim();
    return p || 'follow';
  }

  function taskToRow(t, keepUid) {
    const a = accountBy(t.account_id);
    return {
      _uid: keepUid || t.id || uid(),
      id: t.id || '',
      name: t.name || '',
      copy: t.copy || '',
      image_source: t.image_source || '',
      type: normType(t.type),
      account: a ? a.name : (t.account || t.account_id || ''),
      duration_pref: normDur(t.duration_pref),
      gen_mode: genModeCell(t.gen_mode),
      status: t.status || 'draft',
      engine: t.engine || (a && a.engine) || '',
      dir: t.dir || '',
      meta: t.meta || {},
      priority: Number(t.priority) || 0,
      _sel: false,
      _error: '',
      _warn: '',
      _restored: false,
      _link_from: null,
      updated: t.updated || '',
    };
  }

  function blankRow() {
    return {
      _uid: uid(), id: '', name: '', copy: '', image_source: '',
      type: '', account: '', duration_pref: '', gen_mode: 'follow', status: 'draft', engine: '', dir: '', meta: {},
      priority: 0, _sel: false, _error: '', _warn: '', _restored: false, _link_from: null,
    };
  }

  function parseCsv(text, delim) {
    const rows = [];
    let row = [], cur = '', i = 0, inQ = false;
    const n = text.length;
    while (i < n) {
      const ch = text[i];
      if (inQ) {
        if (ch === '"') {
          if (text[i + 1] === '"') { cur += '"'; i += 2; continue; }
          inQ = false; i++; continue;
        }
        cur += ch; i++; continue;
      }
      if (ch === '"') { inQ = true; i++; continue; }
      if (ch === delim) { row.push(cur); cur = ''; i++; continue; }
      if (ch === '\n') { row.push(cur); rows.push(row); row = []; cur = ''; i++; continue; }
      cur += ch; i++;
    }
    if (cur.length || row.length) { row.push(cur); rows.push(row); }
    return rows;
  }

  function parseTSV(raw) {
    let text = String(raw || '').replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (!text.trim()) return [];
    let all;
    if (text.includes('\t') && window.TasksSheet) {
      all = TasksSheet.parseTSVGrid(raw);
    } else {
      const delim = text.includes('\t') ? '\t' : ',';
      all = parseCsv(text, delim);
    }
    all = (all || []).filter(r => r.some(c => String(c || '').trim()));
    if (!all.length) return [];
    const head = all[0].map(c => String(c).trim().toLowerCase());
    let cols, body;
    if (head.some(h => h in KEYMAP)) {
      cols = head.map(h => KEYMAP[h] || null);
      body = all.slice(1);
    } else {
      cols = COLUMNS.slice(0, all[0].length);
      body = all;
    }
    return body.map(r => {
      const d = blankRow();
      cols.forEach((c, i) => { if (c) d[c] = r[i] == null ? '' : String(r[i]); });
      d.type = normType(d.type);
      d.duration_pref = normDur(d.duration_pref);
      d.gen_mode = genModeCell(d.gen_mode);
      d.status = 'draft';
      return d;
    });
  }

  function payloadOf(d) {
    const meta = {};
    if (d.meta && d.meta.image_src_recursive) meta.image_src_recursive = true;
    if (d.meta && (d.meta.image_src_mode === 'copy' || d.meta.image_src_mode === 'copy_mark' || d.meta.image_src_mode === 'move')) {
      meta.image_src_mode = d.meta.image_src_mode;
    }
    const out = {
      id: d.id || undefined,
      name: d.name || '',
      copy: d.copy || '',
      image_source: d.image_source || '',
      type: d.type || '',
      account: d.account || '',
      duration_pref: d.duration_pref || '',
      gen_mode: d.gen_mode || 'follow',
      updated: d.updated || undefined,
      force: !!d._force,
    };
    if (Object.keys(meta).length) out.meta = meta;
    return out;
  }

  function fmtId(cell, _p, onRendered) {
    const v = cell.getValue();
    const body = v ? `<span class="mono">${esc(v)}</span>` : '<span class="muted">—</span>';
    if (typeof onRendered === 'function') onRendered(() => bindIdHandle(cell));
    return `<span class="tg-handle" title="${esc(t('tasks.handle'))}">⠿</span>${body}`;
  }

  function bindIdHandle(cell) {
    const el = cell.getElement();
    if (!el || el._idHandleBound) return;
    el._idHandleBound = true;
    // 只有点在 14px 把手上才拖行。按下立刻 preventDefault + 拦住冒泡，
    // 避免 selectableRange 把这次按下当成框选起点（和拖行叠在一起就是「偏移」）。
    el.addEventListener('mousedown', ev => {
      if (ev.button !== 0) return;
      const onHandle = ev.target && ev.target.closest && ev.target.closest('.tg-handle');
      if (!onHandle) {
        ev.stopImmediatePropagation();
        return;
      }
      ev.preventDefault();
      ev.stopImmediatePropagation();
      const tableInst = typeof cell.getTable === 'function' ? cell.getTable() : null;
      const rowComp = cell.getRow();
      const internal = (rowComp && rowComp._row) ? rowComp._row : rowComp;
      const mod = tableInst && tableInst.modules && tableInst.modules.moveRow;
      if (!mod || !internal || typeof mod.startMove !== 'function') return;
      if (mod.moving) return;
      if (mod.checkTimeout) clearTimeout(mod.checkTimeout);
      mod.startMove(ev, internal);
    }, true);
  }

  function fmtName(cell) {
    const v = cell.getValue() || '';
    const el = cell.getElement();
    if (el) el.title = v;
    return esc(v);
  }

  function fmtCopy(cell) {
    const d = cell.getRow().getData();
    const v = d.copy || '';
    const el = cell.getElement();
    // 两千字塞进原生 title 又慢又难看，只留前 200 字 + 打开提示
    if (el) {
      const tip = v.length > 200 ? v.slice(0, 200) + '…' : v;
      el.title = (tip ? tip + '\n' : '') + t('tasks.copyed.hint');
      el.classList.toggle('cell-locked', !!(d.status && d.status !== 'draft'));
      el.classList.toggle('cell-warn', !!(d._warn && d._warn.indexOf('copy') >= 0));
    }
    if (!String(v).trim()) {
      if (el) {
        el.title = t('tasks.copy.placeholder');
        el.classList.add('cell-muted');
      }
      return `<span class="tg-copy-ph">${esc(t('tasks.copy.placeholder'))}</span><button type="button" class="tg-copy-open" tabindex="-1" title="${esc(t('tasks.copyed.open'))}">⤢`;
    }
    const show = v.length > 60 ? v.slice(0, 60) + '…' : v;
    return `<span class="tg-copy-text">${esc(show)}</span><button type="button" class="tg-copy-open" tabindex="-1" title="${esc(t('tasks.copyed.open'))}">⤢</button>`;
  }

  function fmtImage(cell) {
    const v = String(cell.getValue() || '').trim();
    const d = cell.getRow().getData();
    const el = cell.getElement();
    const warn = !!(d._warn && d._warn.indexOf('image_source') >= 0);
    const L = window.ImageSourceLogic;
    const probe = d._img_probe || (L && L.guessProbe ? L.guessProbe(v) : null);
    const view = (L && probe) ? L.badgeView(probe) : null;
    const tone = view ? view.tone : '';
    el.classList.toggle('cell-warn', warn || tone === 'warn');
    el.classList.toggle('cell-img-bad', tone === 'bad');
    const from = d._link_from && d._link_from.image_source;
    const sample = L && probe ? L.sampleTitle(probe) : '';
    if (warn && !probe) el.title = t('tasks.warn.image_link');
    else el.title = sample || v;
    if (!v) return '';
    let badge = '';
    if (view && v) {
      const tip = sample ? ` title="${esc(sample)}"` : '';
      badge = `<span class="tg-img-badge tg-img-${esc(tone)}"${tip}>${esc(L.badgeText(view))}</span>`;
      if (view.include) {
        badge += `<button type="button" class="tg-img-inc" tabindex="-1">${esc(t('tasks.img.include'))}</button>`;
      }
    }
    const path = `<span class="tg-img-path">${esc(v)}</span>`;
    const browse = `<button type="button" class="tg-img-browse" tabindex="-1" title="${esc(t('tasks.img.browse'))}">📁</button>`;
    let html = badge + path + browse;
    if (from) {
      html += `<span class="tg-link-mark" title="${esc(t('tasks.paste.link_title', { name: from }))}">🔗</span>`;
    }
    return html;
  }

  function fmtType(cell) {
    const v = cell.getValue();
    const d = cell.getRow().getData();
    const el = cell.getElement();
    el.classList.toggle('cell-miss', !v && hasCopy(d));
    el.classList.toggle('cell-warn', !!(d._warn && d._warn.indexOf('type') >= 0));
    if (!hasCopy(d)) {
      el.classList.add('cell-muted');
      el.title = t('tasks.type.none_tip');
      return esc(t('tasks.type.none'));
    }
    if (!v) return '';
    const key = 'tasks.type.' + v;
    const lab = t(key);
    return esc(lab === key ? v : lab);
  }

  function fmtDur(cell) {
    const v = cell.getValue();
    const d = cell.getRow().getData();
    const el = cell.getElement();
    const eng = engineOf(d) || 'flow';
    const allowed = dursFor(d);
    const bad = !!v && allowed.indexOf(String(v)) < 0;
    el.classList.toggle('cell-warn', !!(bad || (d._warn && d._warn.indexOf('duration_pref') >= 0)));
    el.classList.toggle('cell-muted', !v);
    const limit = eng === 'grok' ? 'Grok 可选 6 / 10 / 15 秒'
      : eng === 'studio' ? (hasCopy(d) ? '本地模型可选 4 / 6 / 8 / 10 / 15 秒' : '本地模型可选 4–15 秒')
      : 'Flow 可选 4 / 6 / 8 秒';
    el.title = bad
      ? (limit + '；当前值不合法，保留原文。')
      : (hasCopy(d)
        ? (v ? ('每段都按 ' + v + ' 秒：拆段时按这一档的字数上限切。留空 = 按语速自动。')
             : ('留空 = 按语速自动定每段时长。想自己定，就在这里选（' + limit + '）。'))
        : '不说话的任务：每段的秒数。');
    if (!v) {
      const def = NO_COPY_DEFAULT[eng] || '8';
      return esc(hasCopy(d) ? (eng === 'studio' ? '自动（6/10/15）' : '自动（按语速）') : ('自动（按 ' + def + ' 秒）'));
    }
    return esc(String(v) + ' 秒');
  }

  function fmtGenMode(cell) {
    const d = cell.getRow().getData();
    const v = genModeCell(cell.getValue());
    const key = v === 'first_frame' ? 'gen.mode.first'
      : v === 'anchor_bridge' ? 'gen.mode.anchor'
      : v === 'tail_chain' ? 'gen.mode.chain'
      : 'tasks.gen_mode.follow';
    const lab = t(key);
    const warn = window.GenModeLogic
      ? GenModeLogic.mismatchWarn({ gen_mode: v, engine: engineOf(d) })
      : '';
    const icon = warn ? `<span class="tg-mode-warn" title="${esc(warn)}">⚠</span>` : '';
    return esc(lab === key ? v : lab) + icon;
  }

  function fmtStatus(cell) {
    const d = cell.getRow().getData();
    const v = d.status || 'draft';
    const key = 'tasks.status.' + v;
    const lab = t(key);
    let html = `<span class="tag st-${esc(v)}">${esc(lab === key ? v : lab)}</span>`;
    const nBroll = Number((d.meta && d.meta.brolls) || 0);
    if (nBroll > 0) {
      html += ` <span class="tag st-broll">${esc(t('tasks.broll_n', { n: String(nBroll) }))}</span>`;
    }
    return html;
  }

  function accountValues() {
    const flow = [], grok = [], studio = [];
    for (const a of store.accounts) {
      const item = { label: a.name, value: a.name };
      if (a.engine === 'studio') {
        // 本地模型没启用就不列（选了也跑不了）；到「账号」里本地模型那一栏点启用
        if (a.enabled) studio.push(item);
      } else if (a.engine === 'grok') grok.push(item);
      else flow.push(item);
    }
    const out = [];
    if (flow.length) out.push({ label: t('tasks.group.flow'), options: flow });
    if (grok.length) out.push({ label: t('tasks.group.grok'), options: grok });
    if (studio.length) out.push({ label: t('tasks.group.studio'), options: studio });
    return out;
  }

  function accItemFormatter(label, value) {
    if (value === undefined || value === null || value === '') return esc(label);
    const a = accountBy(value);
    const color = accColor(a);
    const sub = (a && a.engine === 'studio') ? `<small class="acc-sub">${esc(t('acc.studio.q.' + (a.quality || a.model || 'balanced')))}</small>` : '';
    return `<span class="acc-dot" style="background:${color}"></span>${esc(label)}${sub}`;
  }

  function canEditField(d, field) {
    if (PASTEABLE.indexOf(field) < 0) return false;
    if (field === 'copy' && d.status && d.status !== 'draft') return false;
    // 0923：时长列一律可手选（有文案就按这一档的字数上限拆段；留空 = 自动）
    return true;
  }

  function looksLikePathOrUrl(s) {
    s = String(s == null ? '' : s).trim();
    if (!s) return true;
    if (/^https?:\/\//i.test(s)) return true;
    if (/^file:\/\//i.test(s)) return true;
    if (/^[a-zA-Z]:[\\/]/.test(s)) return true;
    if (s.indexOf('\\\\') === 0 || s.indexOf('//') === 0) return true;
    if (s.charAt(0) === '/' || s.indexOf('./') === 0 || s.indexOf('../') === 0) return true;
    if (s.indexOf('\\') >= 0) return true;
    return false;
  }

  function pickLinkedValue(field, textVal, linkCell) {
    const L = window.ImageSourceLogic;
    if (L && L.pickPasteValue) return L.pickPasteValue(field, textVal, linkCell, LINK_PREFER_FIELDS);
    const text = textVal == null ? '' : String(textVal);
    if (LINK_PREFER_FIELDS.has(field) && linkCell && linkCell.href) {
      return { val: linkCell.href, from: linkCell.text || text, linked: true };
    }
    return { val: text, from: '', linked: false };
  }

  function alignPasteLinks(raw, stripped, links) {
    if (!links) return null;
    let g = links;
    if (raw && raw.length && TasksSheet.looksLikeHeader(raw[0]) && g.length === raw.length) {
      g = g.slice(1);
    }
    if (!TasksSheet.linksAlign(stripped, g)) return null;
    return g;
  }

  function warnOf(d) {
    const flags = [];
    const hasOther = !!(d.name || d.image_source || d.type || d.account || d.duration_pref);
    if (!(d.name || d.copy || d.image_source || d.type || d.account || d.duration_pref)) return '';
    // 规格 86：文案可以空（不说话的任务），不再标红
    if (d.type && d.type !== 'long' && d.type !== 'short') flags.push('type');
    if (d.account && !accountBy(d.account)) flags.push('account');
    if (d.duration_pref && !/^\d+$/.test(String(d.duration_pref))) flags.push('duration_pref');
    if (d.image_source && !looksLikePathOrUrl(d.image_source)) flags.push('image_source');
    return flags.join(',');
  }

  async function migrateCopyToDesc(ids) {
    if (!ids || !ids.length) return;
    const head = ids.length > 1 ? `一共 ${ids.length} 个任务。\n\n` : '';
    if (!confirm(head + t('tasks.ctx.migrate_copy_desc_confirm'))) return;
    try {
      const res = await API.post('/api/tasks/migrate-copy-to-desc', { ids, confirm: true });
      const n = (res && res.ok && res.ok.length) || 0;
      const sk = (res && res.skipped && res.skipped.length) || 0;
      const errs = (res && res.errors) || [];
      const notPrepared = errs.filter(e => /还没准备/.test(String(e && e.error || ''))).length;
      const otherErr = errs.length - notPrepared;
      let msg = t('tasks.ctx.migrate_copy_desc_done', { n: String(n) })
        + (sk ? (' · ' + t('tasks.ctx.migrate_copy_desc_skip', { n: String(sk) })) : '');
      if (notPrepared) msg += ' · ' + t('tasks.ctx.migrate_not_prepared', { n: String(notPrepared) });
      if (otherErr) msg += ' · ' + t('tasks.ctx.migrate_failed', { n: String(otherErr) });
      toast(msg, otherErr ? String(errs.find(e => !/还没准备/.test(String(e && e.error || ''))).error || '') : '',
        (notPrepared || otherErr) && !n ? 'bad' : 'ok');
      if (store.refreshTasks) await store.refreshTasks();
      else if (window.refreshTasks) await window.refreshTasks();
    } catch (e) { /* API 已 toast */ }
  }

  return {
    name: 'Panel_tasks',
    setup() {
      const { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } = Vue;
      const tableEl = ref(null);
      const hintEl = ref(null);
      const fileEl = ref(null);
      const panelEl = ref(null);
      const barEl = ref(null);
      const moreEl = ref(null);
      const selectedN = ref(0);
      const selectedM = ref(0);
      const busy = ref(false);
      const dirty = ref(false);
      const saving = ref(false);
      const savedAt = ref(0);
      const saveError = ref('');
      const draftBanner = ref(null);
      const draftDiffOn = ref(false);
      const dlg = ref(null);
      const conflictDlg = ref(null);
      const finishDlg = ref(null);
      const prepDlg = ref(null);
      const moveDlg = ref(null);
      const pasteText = ref('');
      const impSrc = ref('paste');
      const impText = ref('');
      const impFileName = ref('');
      const impGrid = ref([]);
      const impMap = ref([]);
      const impSkip = ref(false);
      const impMapEdit = ref(false);
      const moreOpen = ref(false);
      const foldDen = ref(false);
      const foldAdd = ref(false);
      const foldImport = ref(false);
      const whereRoot = ref('');
      const whereRemember = ref(false);
      const queueMap = ref({});
      const queueOk = ref(false);
      const addN = ref(String(store.settings['ui.tasks.add_n'] || ''));
      const density = ref(store.settings['ui.tasks.density'] === 'compact' ? 'compact' : 'comfy');
      const onlySel = ref(false);
      const warnN = ref(0);
      const findOn = ref(false);
      const findQ = ref('');
      const findR = ref('');
      const findCase = ref(false);
      const findCell = ref(false);
      const findHit = ref('');
      const delPlan = ref({ total: 0, draft: [], trash: [], running: [], trashIds: '', runIds: '' });
      const copyEd = ref(null);
      const copyEdText = ref('');
      const copyEdSegs = ref(null);
      const copyTaEl = ref(null);
      const gridEditing = ref(false);
      let copySegTimer = 0;
      let copySegSeq = 0;
      let queueTimer = null;
      let pendingPaste = null;
      let pendingPasteLinks = null;
      let pendingImportLinks = null;
      let importPasteTick = false;
      let undoStack = [];
      let redoStack = [];
      let holdUndo = false;
      let fillEl = null;
      let fillTip = null;
      let fillDrag = null;
      let moveHit = null;
      let moveGhost = null;
      let movePlus = null;
      let moveDrag = null;
      let brush = null;
      let lastSelUid = null;
      let warnJump = -1;
      let findIdx = -1;
      let findHits = [];
      let colSaveTimer = 0;
      let currentUid = '';
      let movingGroup = null;
      let holdMove = false;
      let rowDrag = null;
      let dropLine = null;
      let dragTip = null;
      let resizeObs = null;
      let barObs = null;
      let fittingBar = false;
      let lastTableBox = { w: 0, h: 0 };
      let saveTimer = 0;
      let draftTimer = 0;
      let saveAgain = false;
      let saveChain = Promise.resolve();
      let rangeDrag = false;
      const COL_HINT = {
        _sel: 'tasks.hint.sel', id: 'tasks.hint.id', name: 'tasks.hint.name',
        copy: 'tasks.hint.copy', image_source: 'tasks.hint.image', type: 'tasks.hint.type',
        account: 'tasks.hint.account', priority: 'prio.hint',
        duration_pref: 'tasks.hint.duration', status: 'tasks.hint.status',
        gen_mode: 'tasks.hint.gen_mode',
      };

      const hints = [
        'tasks.hint.sel', 'tasks.hint.id', 'tasks.hint.name', 'tasks.hint.copy', 'tasks.hint.image',
        'tasks.hint.type', 'tasks.hint.account', 'tasks.hint.gen_mode', 'prio.hint', 'tasks.hint.duration', 'tasks.hint.status',
      ];

      let table = null;
      let holdWatch = false;
      let whereWait = null;

      function hhmm(ts) {
        const d = new Date(ts);
        return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
      }

      function hasDraftRows() {
        if (!table) return false;
        return allRowComps().some(r => !r.getData().id);
      }

      function leftoverUnsaved() {
        if (!table) return false;
        return allRowComps().some(r => {
          const d = r.getData();
          return !d.id && (d.name || d.copy || d.image_source);
        });
      }

      function cellIsEditing() {
        return !!(table && table.modules && table.modules.edit && table.modules.edit.currentCell);
      }

      // Tabulator 在 .tabulator 根上冒泡委托 mousedown → cell-mousedown → newSelection。
      // 编辑框里拖选文字会冒泡成扩选区，选区一变就把编辑关掉。
      // 编辑器 input/textarea 上拦住冒泡（目标元素自己仍能收到事件）。
      function silenceEditorPointer(cell) {
        try {
          const el = cell && cell.getElement && cell.getElement();
          const inp = el && el.querySelector && el.querySelector('input, textarea, select');
          if (!inp || inp._oranteStopRange) return;
          inp._oranteStopRange = true;
          const stop = ev => ev.stopPropagation();
          inp.addEventListener('mousedown', stop);
          inp.addEventListener('mousemove', stop);
          inp.addEventListener('mouseup', stop);
          inp.addEventListener('pointerdown', stop);
          inp.addEventListener('pointermove', stop);
          inp.addEventListener('pointerup', stop);
          bindImageLinkPaste(cell, inp);
          bindImageProbe(cell, inp);
        } catch (e) { /* 格子还没画完 */ }
      }

      // 图片来源列在编辑态粘贴：Windows 路径原样填；否则 HTML 里有链接就填网址。
      function bindImageLinkPaste(cell, inp) {
        if (!inp || inp._oranteLinkPaste) return;
        if (!cell || cell.getField() !== 'image_source') return;
        inp._oranteLinkPaste = true;
        inp.addEventListener('paste', ev => {
          const text = ev.clipboardData && ev.clipboardData.getData('text/plain');
          const L = window.ImageSourceLogic;
          const stripped = L ? L.stripQuotes(text || '') : String(text || '').trim();
          if (L && L.looksLikeWinPath(stripped)) {
            ev.preventDefault();
            ev.stopPropagation();
            inp.value = stripped;
            try { inp.setSelectionRange(stripped.length, stripped.length); } catch (e) { /* 有的 input 不能设光标 */ }
            inp.dispatchEvent(new Event('input', { bubbles: true }));
            return;
          }
          const html = ev.clipboardData && ev.clipboardData.getData('text/html');
          if (!html) return;
          const href = TasksSheet.firstHrefFromHtml(html);
          if (!href) return;
          ev.preventDefault();
          ev.stopPropagation();
          inp.value = href;
          try { inp.setSelectionRange(href.length, href.length); } catch (e) { /* 有的 input 不能设光标 */ }
          inp.dispatchEvent(new Event('input', { bubbles: true }));
        });
      }

      const imgProbe = (window.ImageSourceLogic && window.ImageSourceLogic.createProbe)
        ? window.ImageSourceLogic.createProbe({
          post: (url, body) => API.post(url, body, { silent: true }),
        })
        : null;

      function rowRecursive(d) {
        return !!(d && d.meta && d.meta.image_src_recursive);
      }

      function applyProbe(row, data) {
        if (!row || !data) return;
        try { row.update({ _img_probe: data }); } catch (e) { /* 行可能没了 */ }
        try {
          const cell = row.getCell('image_source');
          if (cell) cell.getColumn().getTable(); // 触发重画
        } catch (e) { /* ignore */ }
      }

      function sweepImageProbes() {
        if (!table || !imgProbe) return;
        allRowComps().forEach(row => {
          const d = row.getData();
          if (d.image_source && !d._img_probe) scheduleImageProbe(row);
        });
      }

      function scheduleImageProbe(row) {
        if (!imgProbe || !row) return;
        const d = row.getData();
        const src = String(d.image_source || '').trim();
        if (!src) {
          try { row.update({ _img_probe: null }); } catch (e) { /* ignore */ }
          return;
        }
        imgProbe.schedule(src, rowRecursive(d), data => applyProbe(row, data));
      }

      async function probeImageNow(row) {
        if (!imgProbe || !row) return null;
        const d = row.getData();
        const src = String(d.image_source || '').trim();
        if (!src) return null;
        try {
          const data = await imgProbe.probeNow(src, rowRecursive(d));
          applyProbe(row, data);
          return data;
        } catch (e) { return null; }
      }

      function bindImageProbe(cell, inp) {
        if (!inp || inp._oranteImgProbe) return;
        if (!cell || cell.getField() !== 'image_source') return;
        inp._oranteImgProbe = true;
        const row = cell.getRow();
        inp.addEventListener('input', () => {
          if (!imgProbe) return;
          const rec = rowRecursive(row.getData());
          imgProbe.schedule(inp.value, rec, data => applyProbe(row, data));
        });
        scheduleImageProbe(row);
      }

      function findRowByEl(el) {
        if (!table || !el) return null;
        const rowEl = el.closest && el.closest('.tabulator-row');
        if (!rowEl) return null;
        return table.getRows().find(r => r.getElement() === rowEl) || null;
      }

      async function browseImageFolder(row) {
        if (!row) return;
        try {
          const r = await API.post('/api/workspace/pick', { title: t('tasks.img.browse') });
          if (!r || !r.folder) return;
          pushUndo();
          row.update({ image_source: r.folder, _error: '' });
          markDirty();
          paintRow(row);
          await probeImageNow(row);
        } catch (e) { /* 选框取消或失败就手填 */ }
      }

      async function includeSubfolders(row) {
        if (!row) return;
        const d = row.getData();
        const meta = Object.assign({}, d.meta || {}, { image_src_recursive: true });
        row.update({ meta: meta });
        markDirty();
        if (d.id) {
          try { await API.post('/api/tasks/' + encodeURIComponent(d.id) + '/meta', { image_src_recursive: true }, { silent: true }); }
          catch (e) { /* 保存表格时还会再写一次 */ }
        }
        await probeImageNow(row);
        paintRow(row);
      }

      async function fillImageFolder(row, folder, fromFiles) {
        if (!row || !folder) return;
        pushUndo();
        row.update({ image_source: folder, _error: '' });
        markDirty();
        paintRow(row);
        const data = await probeImageNow(row);
        const n = data ? (rowRecursive(row.getData()) ? data.n_deep : data.n) : 0;
        if (fromFiles) toast(t('tasks.img.drop_folder', { n: String(n || 0) }), '', 'ok');
      }

      function askMoveConfirm() {
        if (store.settings && store.settings['images.local_move_confirmed']) return Promise.resolve(true);
        dlg.value = 'move-warn';
        return new Promise((resolve) => {
          moveDlg.value = {
            resolve: (ok) => {
              moveDlg.value = null;
              dlg.value = null;
              resolve(!!ok);
            },
          };
        });
      }

      async function confirmMoveWarn() {
        const d = moveDlg.value;
        try {
          const r = await API.post('/api/settings', { set: { 'images.local_move_confirmed': true } });
          if (r && r.settings) Object.assign(store.settings, r.settings);
        } catch (e) { /* 记住失败也不挡这次 */ }
        if (d && d.resolve) d.resolve(true);
      }

      function cancelMoveWarn() {
        const d = moveDlg.value;
        if (d && d.resolve) d.resolve(false);
        else { moveDlg.value = null; dlg.value = null; }
      }

      async function setRowImageMode(row, mode) {
        if (!row) return;
        if (mode === 'move') {
          const ok = await askMoveConfirm();
          if (!ok) return;
        }
        const d = row.getData();
        const meta = Object.assign({}, d.meta || {});
        if (!mode) delete meta.image_src_mode;
        else meta.image_src_mode = mode;
        row.update({ meta: meta });
        markDirty();
        if (d.id) {
          try { await API.post('/api/tasks/' + encodeURIComponent(d.id) + '/meta', { image_src_mode: mode || '' }, { silent: true }); }
          catch (e) { /* ignore */ }
        }
      }

      function setGridEditing(on) {
        gridEditing.value = !!on;
      }

      function isBusyEditing() {
        if (dlg.value || copyEd.value) return true;
        if (fillDrag) return true;
        if (moveDrag) return true;
        if (rowDrag || holdMove) return true;
        if (brush) return true;
        if (rangeDrag) return true;
        if (gridEditing.value || cellIsEditing()) return true;
        return false;
      }

      function collectPayloads() {
        if (!table) return [];
        return allRowComps().map(r => payloadOf(r.getData()));
      }

      function collectDraftRows() {
        if (!table) return [];
        return allRowComps().map(r => {
          const d = r.getData();
          return Object.assign(payloadOf(d), {
            _uid: d._uid || '',
            status: d.status || 'draft',
            priority: Number(d.priority) || 0,
          });
        });
      }

      function markDirty() {
        dirty.value = true;
        saveError.value = '';
        scheduleAutosave();
        scheduleDraftSnap();
      }

      function scheduleAutosave() {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(runAutosave, SAVE_MS);
      }

      function runAutosave() {
        if (!dirty.value || !table) return;
        if (isBusyEditing()) {
          saveTimer = setTimeout(runAutosave, 400);
          return;
        }
        if (saving.value) {
          saveAgain = true;
          return;
        }
        saveTable({ quiet: true });
      }

      function scheduleDraftSnap() {
        clearTimeout(draftTimer);
        draftTimer = setTimeout(postDraftSnap, DRAFT_MS);
      }

      function postDraftSnap() {
        if (!table) return;
        const rows = collectDraftRows();
        fetch('/api/tasks/draft', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rows }),
        }).catch(() => {});
      }

      function clearDraftSnap() {
        clearTimeout(draftTimer);
        fetch('/api/tasks/draft', { method: 'DELETE' }).catch(() => {});
      }

      function beaconFlush() {
        if (!table || !dirty.value) return;
        const body = JSON.stringify({ rows: collectPayloads() });
        try {
          const blob = new Blob([body], { type: 'application/json' });
          if (navigator.sendBeacon) navigator.sendBeacon('/api/tasks/rows', blob);
          else {
            fetch('/api/tasks/rows', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body, keepalive: true,
            }).catch(() => {});
          }
        } catch (e) { /* 关窗时尽力发出去 */ }
        try {
          const snap = JSON.stringify({ rows: collectDraftRows() });
          const blob2 = new Blob([snap], { type: 'application/json' });
          if (navigator.sendBeacon) navigator.sendBeacon('/api/tasks/draft', blob2);
        } catch (e) { /* 快照失败不挡 rows beacon */ }
      }

      const saveLabelText = computed(() => {
        if (saving.value) return t('tasks.save.saving');
        if (dirty.value) return t('tasks.save.dirty');
        if (savedAt.value) return t('tasks.save.ok', { time: hhmm(savedAt.value) });
        return '';
      });
      const saveClassName = computed(() => {
        if (saving.value) return 'is-saving';
        if (dirty.value) return 'is-dirty';
        if (savedAt.value) return 'is-ok';
        return '';
      });

      const impN = computed(() => {
        const n = (impGrid.value || []).length;
        const skip = impSkip.value ? 1 : 0;
        return Math.max(0, n - skip);
      });

      function impFieldLabel(v) {
        if (!v) return t('tasks.import_dlg.ignore');
        const hit = IMP_FIELDS.find(f => f.v === v);
        return hit ? t(hit.k) : v;
      }

      function agoText(at) {
        const sec = Math.max(0, Math.floor((Date.now() / 1000) - Number(at || 0)));
        if (sec < 60) return t('tasks.draft.ago.s');
        if (sec < 3600) return t('tasks.draft.ago.m', { n: String(Math.floor(sec / 60)) });
        if (sec < 86400) return t('tasks.draft.ago.h', { n: String(Math.floor(sec / 3600)) });
        return t('tasks.draft.ago.d', { n: String(Math.floor(sec / 86400)) });
      }

      async function checkDraftBanner() {
        try {
          const r = await fetch('/api/tasks/draft').then(x => x.json());
          if (!r || !r.ok || !r.exists || !r.missing_n) {
            draftBanner.value = null;
            return;
          }
          draftBanner.value = {
            n: r.missing_n,
            ago: agoText(r.at),
            at: r.at,
            missing: r.missing || [],
            rows: r.rows || [],
          };
        } catch (e) { draftBanner.value = null; }
      }

      function restoreDraft() {
        const b = draftBanner.value;
        if (!b || !table) return;
        const haveUid = new Set(allRowComps().map(r => r.getData()._uid));
        const haveId = new Set(allRowComps().map(r => r.getData().id).filter(Boolean));
        holdUndo = true;
        for (const raw of (b.missing || [])) {
          if (raw.id && haveId.has(raw.id)) continue;
          if (raw._uid && haveUid.has(raw._uid)) continue;
          const d = Object.assign(blankRow(), {
            id: raw.id || '',
            name: raw.name || '',
            copy: raw.copy || '',
            image_source: raw.image_source || '',
            type: normType(raw.type),
            account: raw.account || '',
            duration_pref: normDur(raw.duration_pref),
            gen_mode: genModeCell(raw.gen_mode),
            status: raw.status || 'draft',
            _restored: true,
          });
          if (raw._uid) d._uid = raw._uid;
          table.addRow(d, false);
        }
        holdUndo = false;
        draftBanner.value = null;
        draftDiffOn.value = false;
        markDirty();
        recount();
        scanWarn();
      }

      function discardDraft() {
        draftBanner.value = null;
        draftDiffOn.value = false;
        clearDraftSnap();
      }

      function onPointerUp() {
        if (!rangeDrag) return;
        rangeDrag = false;
        if (dirty.value) scheduleAutosave();
      }

      function flagStateOf(tid) {
        if (!tid) return '';
        if (queueOk.value) {
          const q = queueMap.value || {};
          for (const slot of Object.values(q)) {
            if (slot && slot.running && slot.running.task_id === tid) return 'running';
            if (((slot && slot.waiting) || []).some(w => w && w.task_id === tid)) return 'queued';
          }
        }
        const j = (store.jobs || []).find(x =>
          x.task_id === tid && (x.status === 'running' || x.status === 'queued')
          && ['generate', 'regenerate', 'refill', 'expand'].includes(x.kind));
        if (!j) return '';
        return j.status === 'running' ? 'running' : 'queued';
      }

      function fmtPrio(cell, _p, onRendered) {
        const d = cell.getRow().getData();
        if (!d.id) return '<span class="muted">—</span>';
        const html = PrioFlagLogic.buttonHtml(Number(d.priority) || 0, flagStateOf(d.id), d.id);
        if (typeof onRendered === 'function') {
          onRendered(() => {
            const btn = cell.getElement().querySelector('.pf-btn');
            if (!btn || btn._pfBound) return;
            btn._pfBound = true;
            btn.addEventListener('mousedown', ev => ev.stopPropagation());
            btn.addEventListener('click', ev => {
              ev.preventDefault();
              ev.stopPropagation();
              const now = cell.getRow().getData();
              PrioFlagLogic.openPop({
                tid: now.id,
                priority: Number(now.priority) || 0,
                anchorEl: btn,
              });
            });
          });
        }
        return html;
      }

      let lastFlagSig = '';
      function flagSig(q) {
        const parts = [];
        Object.keys(q || {}).sort().forEach(aid => {
          const s = q[aid] || {};
          const run = s.running ? (s.running.task_id + ':' + (s.running.prio || '')) : '';
          const wait = ((s.waiting) || []).map(w => (w.task_id || '') + ':' + (w.prio || '')).join(',');
          parts.push(aid + '>' + run + '>' + wait);
        });
        return parts.join('|');
      }

      async function pullQueue() {
        try {
          const res = await fetch('/api/queue');
          const body = await res.json();
          if (body && body.ok) {
            const q = body.queue || {};
            queueMap.value = q;
            queueOk.value = true;
            const sig = flagSig(q);
            if (sig !== lastFlagSig && table) {
              lastFlagSig = sig;
              table.getRows().forEach(row => {
                try { row.reformat(); } catch (e) { /* 格子还没建完 */ }
              });
            } else {
              lastFlagSig = sig;
            }
            return;
          }
        } catch (e) { /* 静默，旗子退回 store.jobs */ }
        queueOk.value = false;
      }

      function syncHint() {
        if (!table || !hintEl.value) return;
        const cols = table.getColumns();
        hintEl.value.style.gridTemplateColumns = cols.map(c => Math.max(0, c.getWidth()) + 'px').join(' ');
      }

      function allRowComps() {
        if (!table) return [];
        try { return table.getRows('all') || []; }
        catch (e) { return table.getRows() || []; }
      }

      function recount() {
        if (!table) { selectedN.value = 0; selectedM.value = 0; return; }
        const rows = allRowComps();
        selectedM.value = rows.length;
        selectedN.value = rows.filter(r => r.getData()._sel).length;
        syncHeadCheck();
      }

      function paintRow(row) {
        const d = row.getData();
        const el = row.getElement();
        el.classList.remove('row-flow', 'row-grok', 'row-studio', 'row-error', 'row-current', 'row-restored');
        const eng = engineOf(d);
        if (eng === 'flow') el.classList.add('row-flow');
        if (eng === 'grok') el.classList.add('row-grok');
        if (eng === 'studio') el.classList.add('row-studio');
        if (d._error) el.classList.add('row-error');
        if (d._restored) el.classList.add('row-restored');
        if (d._uid && d._uid === currentUid) el.classList.add('row-current');
      }

      function lastTemplate() {
        const rows = allRowComps();
        if (!rows.length) return { type: '', account: '', duration_pref: '' };
        const d = rows[rows.length - 1].getData();
        return {
          type: d.type || '',
          account: d.account || '',
          duration_pref: d.duration_pref || '',
        };
      }

      function makeBlankRows(n) {
        const tpl = lastTemplate();
        const out = [];
        for (let i = 0; i < n; i++) out.push(Object.assign(blankRow(), tpl));
        return out;
      }

      function snap() {
        return allRowComps().map(r => Object.assign({}, r.getData()));
      }

      function packUndo(extra) {
        const rec = Object.assign({ data: snap() }, extra || {});
        return rec;
      }

      function pushUndo(extra) {
        if (holdUndo || !table) return;
        undoStack.push(packUndo(extra));
        if (undoStack.length > UNDO_MAX) undoStack.shift();
        redoStack = [];
      }

      function orderedIds() {
        return allRowComps().map(r => r.getData().id).filter(Boolean);
      }

      async function persistOrder() {
        const ids = orderedIds();
        if (!ids.length) return;
        try { await API.post('/api/tasks/reorder', { ids }); }
        catch (e) { /* API 已 toast */ }
      }

      async function restore(entry) {
        if (!table) return;
        const data = (entry && entry.data) ? entry.data : entry;
        holdWatch = true;
        holdUndo = true;
        holdMove = true;
        try {
          await table.replaceData((data || []).map(d => Object.assign({}, d)));
          if (entry && entry.resurrect) {
            try { await API.post('/api/tasks/undelete', entry.resurrect); }
            catch (e) { /* API 已 toast */ }
          }
          if (entry && entry.replayDelete) {
            try { await API.post('/api/tasks/batch-delete', entry.replayDelete); }
            catch (e) { /* API 已 toast */ }
          }
          if (entry && (entry.persistOrder || entry.resurrect || entry.replayDelete)) {
            await persistOrder();
          }
        } finally {
          holdUndo = false;
          holdWatch = false;
          holdMove = false;
        }
        if (!(entry && (entry.resurrect || entry.replayDelete || entry.persistOrder))) markDirty();
        recount();
        scanWarn();
        placeFillHandle();
      }

      async function undo() {
        if (!undoStack.length) return;
        const prev = undoStack.pop();
        const extra = {};
        if (prev && prev.resurrect) {
          extra.replayDelete = {
            hard: (prev.resurrect.hard || []).map(r => r.id).filter(Boolean),
            trash: (prev.resurrect.trash || []).map(r => r.id).filter(Boolean),
          };
        }
        if (prev && prev.persistOrder) extra.persistOrder = true;
        redoStack.push(packUndo(extra));
        await restore(prev);
      }

      async function redo() {
        if (!redoStack.length) return;
        const next = redoStack.pop();
        const extra = {};
        if (next && next.replayDelete) {
          extra.resurrect = {
            hard: snap().filter(d => (next.replayDelete.hard || []).includes(d.id)),
            trash: snap().filter(d => (next.replayDelete.trash || []).includes(d.id))
              .map(d => ({ id: d.id, status: d.status })),
          };
        }
        if (next && next.persistOrder) extra.persistOrder = true;
        undoStack.push(packUndo(extra));
        await restore(next);
      }

      function visibleCols() {
        return table ? table.getColumns().filter(c => { try { return c.isVisible(); } catch (e) { return true; } }) : [];
      }

      function getRangeBox() {
        if (!table || typeof table.getRanges !== 'function') return null;
        const ranges = table.getRanges();
        if (!ranges || !ranges.length) return null;
        const r = ranges[0];
        try {
          return {
            top: r.getTopEdge(),
            bottom: r.getBottomEdge(),
            left: r.getLeftEdge(),
            right: r.getRightEdge(),
            range: r,
          };
        } catch (e) { return null; }
      }

      function pasteableFrom(left) {
        const cols = visibleCols();
        const out = [];
        for (let i = Math.max(0, left); i < cols.length; i++) {
          const f = cols[i].getField();
          if (PASTEABLE.indexOf(f) >= 0) out.push(cols[i]);
        }
        return out;
      }

      function pasteableColsInRange(left, right) {
        const cols = visibleCols();
        const out = [];
        for (let i = left; i <= right && i < cols.length; i++) {
          const f = cols[i].getField();
          if (PASTEABLE.indexOf(f) >= 0) out.push(cols[i]);
        }
        return out;
      }

      function selectedGrid() {
        const box = getRangeBox();
        if (!box) return [];
        const rows = table.getRows();
        const cols = visibleCols();
        const grid = [];
        for (let r = box.top; r <= box.bottom; r++) {
          const row = rows[r];
          if (!row) continue;
          const d = row.getData();
          const line = [];
          for (let c = box.left; c <= box.right; c++) {
            const col = cols[c];
            if (!col) continue;
            const f = col.getField();
            if (f === '_sel') continue;
            let v = d[f];
            if (f === 'type' && (v === 'long' || v === 'short')) {
              const key = 'tasks.type.' + v;
              const lab = t(key);
              v = lab === key ? v : lab;
            }
            line.push(v == null ? '' : String(v));
          }
          grid.push(line);
        }
        return grid;
      }

      async function applyPaste(grid) {
        if (!table || !grid || !grid.length) return;
        const links = pendingPasteLinks;
        pendingPasteLinks = null;
        pushUndo();
        holdUndo = true;
        table.blockRedraw();
        let restored = 0;
        let need = 0;
        try {
          const rowsNow = table.getRows();
          const box = getRangeBox();
          let top = 0, left = 0, selR = 1, single = true;
          if (rowsNow.length && box) {
            top = box.top;
            left = box.left;
            selR = box.bottom - box.top + 1;
            single = selR === 1 && box.left === box.right;
          }
          let destCols = single ? pasteableFrom(left) : pasteableColsInRange(left, box ? box.right : left);
          if (!destCols.length) destCols = pasteableFrom(0);
          const dataR = grid.length;
          const dataC = grid.reduce((m, r) => Math.max(m, r.length), 0);
          const plan = TasksSheet.pastePlan(selR, Math.max(1, destCols.length), dataR, dataC, single);
          const destR = plan.destRows;
          const destC = Math.min(plan.destCols, destCols.length);
          const have = allRowComps().length;
          need = top + destR - have;
          if (need > 0) {
            await table.addData(makeBlankRows(need), false);
          }
          const now = table.getRows();
          for (let i = 0; i < destR; i++) {
            const row = now[top + i];
            if (!row) continue;
            const d = row.getData();
            const patch = {};
            let linkFrom = d._link_from ? Object.assign({}, d._link_from) : {};
            let linkFromChanged = false;
            for (let j = 0; j < destC; j++) {
              const col = destCols[j];
              const f = col.getField();
              if (!canEditField(d, f)) continue;
              const picked = pickLinkedValue(f, TasksSheet.pasteValue(grid, i, j), links ? TasksSheet.pasteLink(links, i, j) : null);
              let val = picked.val;
              if (f === 'type') val = normType(val);
              if (f === 'duration_pref') val = normDur(val);
              patch[f] = val;
              if (LINK_PREFER_FIELDS.has(f)) {
                linkFromChanged = true;
                if (picked.linked) {
                  linkFrom[f] = picked.from;
                  restored += 1;
                } else {
                  delete linkFrom[f];
                }
              }
            }
            if (linkFromChanged) patch._link_from = linkFrom;
            if (Object.keys(patch).length) row.update(patch);
          }
          markDirty();
          recount();
          scanWarn();
        } finally {
          table.restoreRedraw();
          holdUndo = false;
        }
        let msg = '';
        if (need > 0) msg = t('tasks.paste_added', { n: String(need) });
        if (restored > 0) {
          const extra = t('tasks.paste.links', { n: String(restored) });
          msg = msg ? msg + '，' + extra : extra;
        }
        if (msg) toast(msg, '', 'ok');
      }

      function scanWarn() {
        if (!table) { warnN.value = 0; return; }
        let n = 0;
        allRowComps().forEach(row => {
          const d = row.getData();
          const w = warnOf(d);
          if ((d._warn || '') !== w) {
            try { row.update({ _warn: w }); } catch (e) { /* 行已删 */ }
          }
          if (w) n += w.split(',').length;
        });
        warnN.value = n;
      }

      function jumpWarn() {
        if (!table || !warnN.value) return;
        const rows = allRowComps();
        const cells = [];
        rows.forEach(row => {
          const d = row.getData();
          if (!d._warn) return;
          d._warn.split(',').forEach(f => { if (f) cells.push({ row, field: f }); });
        });
        if (!cells.length) return;
        warnJump = (warnJump + 1) % cells.length;
        const hit = cells[warnJump];
        try {
          hit.row.scrollTo();
          const cell = hit.row.getCell(hit.field);
          if (cell && table.addRange) table.addRange(cell, cell);
        } catch (e) { /* 滚动失败就算了 */ }
      }

      function appendRows(rows) {
        if (!table || !rows || !rows.length) return;
        pushUndo();
        holdUndo = true;
        const tpl = lastTemplate();
        for (const r of rows) {
          const d = Object.assign(blankRow(), {
            name: r.name || '',
            copy: r.copy || '',
            image_source: r.image_source || '',
            type: normType(r.type) || tpl.type,
            account: r.account || tpl.account,
            duration_pref: normDur(r.duration_pref) || tpl.duration_pref,
            gen_mode: genModeCell(r.gen_mode),
            status: 'draft',
            _link_from: r._link_from || null,
          });
          table.addRow(d, false);
        }
        holdUndo = false;
        markDirty();
        recount();
        scanWarn();
      }

      function mergeFromStore(tasks) {
        if (!table) return;
        const byId = {};
        for (const t of tasks) byId[t.id] = t;
        const seen = new Set();
        // 有没 id 的草稿行就绝不整表覆盖，按 _uid 留着
        const freeze = dirty.value || hasDraftRows();
        for (const row of table.getRows()) {
          const d = row.getData();
          if (!d.id) continue;
          if (byId[d.id]) {
            seen.add(d.id);
            const srv = byId[d.id];
            const patch = {};
            if (d.status !== srv.status) patch.status = srv.status;
            if ((d.engine || '') !== (srv.engine || '')) patch.engine = srv.engine || '';
            if ((d.dir || '') !== (srv.dir || '')) patch.dir = srv.dir || '';
            patch.meta = srv.meta || {};
            const sp = Number(srv.priority) || 0;
            if ((Number(d.priority) || 0) !== sp) patch.priority = sp;
            if (!freeze) {
              const local = taskToRow(srv, d._uid);
              local._sel = !!d._sel;
              row.update(local);
            } else if (Object.keys(patch).length) {
              row.update(patch);
            }
          } else if (!d._error && !freeze) {
            row.delete();
          }
        }
        if (!dirty.value) {
          for (const t of tasks) {
            if (!seen.has(t.id)) table.addRow(taskToRow(t));
          }
        }
        recount();
      }

      function checkHtml(on, ind) {
        const cls = 'tg-check' + (on ? ' on' : '') + (ind && !on ? ' ind' : '');
        return `<span class="${cls}"></span>`;
      }

      function fmtSel(cell, _p, onRendered) {
        const d = cell.getRow().getData();
        if (typeof onRendered === 'function') {
          onRendered(() => bindSelCell(cell));
        }
        return checkHtml(!!d._sel, false);
      }

      function fmtSelHead() {
        return `<div class="tg-hd-sel">${checkHtml(false, false)}<span>${esc(t('tasks.col.sel'))}</span></div>`;
      }

      function syncHeadCheck() {
        if (!table) return;
        const el = tableEl.value && tableEl.value.querySelector('.tg-hd-sel .tg-check');
        if (!el) return;
        const rows = table.getRows();
        const n = rows.length;
        const s = rows.filter(r => r.getData()._sel).length;
        el.classList.toggle('on', n > 0 && s === n);
        el.classList.toggle('ind', s > 0 && s < n);
      }

      function startSelMouse(ev, row) {
        ev.preventDefault();
        ev.stopPropagation();
        const rows = table.getRows();
        const d = row.getData();
        if (ev.shiftKey && lastSelUid) {
          const i0 = rows.findIndex(r => r.getData()._uid === lastSelUid);
          const i1 = rows.findIndex(r => r.getData()._uid === d._uid);
          if (i0 >= 0 && i1 >= 0) {
            pushUndo();
            holdUndo = true;
            const lo = Math.min(i0, i1), hi = Math.max(i0, i1);
            for (let i = lo; i <= hi; i++) rows[i].update({ _sel: true });
            holdUndo = false;
            recount();
            return;
          }
        }
        const next = !d._sel;
        pushUndo();
        holdUndo = true;
        row.update({ _sel: next });
        lastSelUid = d._uid;
        brush = {
          value: next,
          startUid: d._uid,
          orig: rows.map(r => !!r.getData()._sel),
        };
        const idx = rows.findIndex(r => r.getData()._uid === d._uid);
        if (idx >= 0) brush.orig[idx] = !next;
        holdUndo = false;
        recount();
        document.addEventListener('mousemove', onBrushMove);
      }

      function bindSelCell(cell) {
        const el = cell.getElement();
        if (!el || el._selBound) return;
        el._selBound = true;
        el.addEventListener('mousedown', ev => {
          if (ev.button !== 0) return;
          startSelMouse(ev, cell.getRow());
        });
        el.addEventListener('mouseenter', () => applyBrushTo(cell.getRow()));
      }

      function bindHeadSel() {
        const wrap = tableEl.value && tableEl.value.querySelector('.tg-hd-sel');
        if (!wrap || wrap._selBound) return;
        wrap._selBound = true;
        wrap.addEventListener('mousedown', ev => ev.stopPropagation());
        wrap.addEventListener('click', ev => {
          ev.preventDefault();
          ev.stopPropagation();
          const rows = table.getRows();
          if (!rows.length) return;
          const allOn = rows.every(r => r.getData()._sel);
          pushUndo();
          holdUndo = true;
          rows.forEach(r => r.update({ _sel: !allOn }));
          holdUndo = false;
          recount();
        });
      }

      function tableWidthPx() {
        const el = (table && table.element) || tableEl.value;
        if (el && el.clientWidth > 40) return el.clientWidth;
        const panel = panelEl.value;
        if (panel && panel.clientWidth > 80) return panel.clientWidth;
        return 0;
      }

      // colwidths 只认 0~1 的比例。旧版存的是像素（>1），再塞回去会把 fitColumns 锁死，直接忽略。
      function savedRatio(field) {
        const saved = store.settings['ui.tasks.colwidths'] || {};
        const n = Number(saved[field]);
        return (n > 0 && n <= 1) ? n : null;
      }

      function widthFromRatio(field, fallback) {
        const r = savedRatio(field);
        const tw = tableWidthPx();
        if (r != null && tw > 40) {
          const minW = 40;
          return Math.max(minW, Math.round(r * tw));
        }
        return fallback;
      }

      function columns() {
        // 非冻结列不写死 width，只留 minWidth + 权重，fitColumns 才会跟着窗口走。
        // Tabulator 的 fitColumns 用 `widthGrow || 1`，写 0 等于没写会去抢剩余空间；
        // 所以 type / priority / duration / status 给自然宽度、进固定桶。
        // 不设 widthShrink：剩余像素会被加到 shrink 列表最后一列，状态列会无故被撑宽。
        const wId = widthFromRatio('id', 86);
        const wName = widthFromRatio('name', null);
        const wCopy = widthFromRatio('copy', null);
        const wImg = widthFromRatio('image_source', null);
        const wType = widthFromRatio('type', 108);
        const wAcc = widthFromRatio('account', null);
        const wGen = widthFromRatio('gen_mode', 118);
        const wPrio = widthFromRatio('priority', 64);
        const wDur = widthFromRatio('duration_pref', 88);
        const wSt = widthFromRatio('status', 96);
        return [
          { title: t('tasks.col.sel'), field: '_sel', width: 36, minWidth: 36, maxWidth: 40,
            frozen: true, headerSort: false, hozAlign: 'center', clipboard: false,
            editor: false, resizable: false, cssClass: 'tg-selcol',
            titleFormatter: fmtSelHead, formatter: fmtSel },
          { title: t('tasks.col.id'), field: 'id', width: wId, minWidth: 70, frozen: true,
            headerSort: false, hozAlign: 'center', formatter: fmtId, clipboard: false, rowHandle: true,
            tooltip: false, cssClass: 'tg-idcol' },
          Object.assign(
            { title: t('tasks.col.name'), field: 'name', minWidth: 90, frozen: true, widthGrow: 1,
              headerSort: false, editor: 'input', formatter: fmtName, tooltip: true },
            wName ? { width: wName } : {}
          ),
          Object.assign(
            { title: t('tasks.col.copy'), field: 'copy', minWidth: 220, widthGrow: 4,
              headerSort: false, editor: false, tooltip: false, formatter: fmtCopy },
            wCopy ? { width: wCopy } : {}
          ),
          Object.assign(
            { title: t('tasks.col.image'), field: 'image_source', minWidth: 120, widthGrow: 2,
              headerSort: false, editor: 'input', formatter: fmtImage },
            wImg ? { width: wImg } : {}
          ),
          { title: t('tasks.col.type'), field: 'type', width: wType, minWidth: 88,
            headerSort: false,
            editor: 'list',
            editorParams: { values: [
              { label: t('tasks.type.long'), value: 'long' },
              { label: t('tasks.type.short'), value: 'short' },
            ] },
            formatter: fmtType },
          Object.assign(
            { title: t('tasks.col.account'), field: 'account', minWidth: 110, widthGrow: 1,
              headerSort: false, editor: 'list',
              editorParams: { valuesLookup: accountValues, itemFormatter: accItemFormatter, clearable: true, placeholderEmpty: '先到右上角「账号」里添加' },
              formatter: cell => {
                const v = cell.getValue() || '';
                const d = cell.getRow().getData();
                cell.getElement().classList.toggle('cell-warn', !!(d._warn && d._warn.indexOf('account') >= 0));
                if (!v) return '';
                const a = accountBy(v);
                const color = accColor(a);
                if (a && a.engine === 'studio') {
                  cell.getElement().title = a.enabled
                    ? t('acc.studio.cell_tip', { q: t('acc.studio.q.' + (a.quality || a.model || 'balanced')) })
                    : t('regen.acc.studio_off_tip');
                }
                return `<span class="acc-dot" style="background:${color}"></span>${esc(v)}`;
              } },
            wAcc ? { width: wAcc } : {}
          ),
          { title: t('tasks.col.gen_mode'), field: 'gen_mode', width: wGen, minWidth: 108,
            headerSort: false,
            editor: 'list',
            editorParams: { values: [
              { label: t('tasks.gen_mode.follow'), value: 'follow' },
              { label: t('gen.mode.first'), value: 'first_frame' },
              { label: t('gen.mode.anchor'), value: 'anchor_bridge' },
              { label: t('gen.mode.chain'), value: 'tail_chain' },
            ] },
            formatter: fmtGenMode },
          { title: t('prio.col'), field: 'priority', width: wPrio, minWidth: 52,
            headerSort: false,
            hozAlign: 'center', clipboard: false, formatter: fmtPrio },
          { title: t('tasks.col.duration'), field: 'duration_pref', width: wDur, minWidth: 72,
            headerSort: false,
            editable: function(cell) { return canEditField(cell.getRow().getData(), 'duration_pref'); },
            editor: 'list',
            editorParams: function(cell) {
              return { values: durationEditorValues(cell.getRow().getData()), clearable: true };
            },
            formatter: fmtDur },
          { title: t('tasks.col.status'), field: 'status', width: wSt, minWidth: 80,
            headerSort: false,
            hozAlign: 'center', formatter: fmtStatus, clipboard: false },
        ].map(def => {
          const key = COL_HINT[def.field];
          if (key) def.headerTooltip = t(key);
          if (def.tooltip == null && PASTEABLE.indexOf(def.field) >= 0) def.tooltip = true;
          return def;
        });
      }

      function fieldLabel(f) {
        const keys = {
          name: 'tasks.col.name', copy: 'tasks.col.copy', image_source: 'tasks.col.image',
          type: 'tasks.col.type', account: 'tasks.col.account', duration_pref: 'tasks.col.duration',
          gen_mode: 'tasks.col.gen_mode',
          status: 'tasks.col.status', id: 'tasks.col.id', _sel: 'tasks.col.sel',
        };
        return keys[f] ? t(keys[f]) : f;
      }

      function rangeWriteState() {
        const box = getRangeBox();
        if (!box || !table) return { any: false, started: 0, nocol: 0, total: 0 };
        const rows = table.getRows();
        const cols = visibleCols();
        let any = false, started = 0, nocol = 0, total = 0;
        for (let r = box.top; r <= box.bottom; r++) {
          const row = rows[r];
          if (!row) continue;
          const d = row.getData();
          for (let c = box.left; c <= box.right; c++) {
            const col = cols[c];
            if (!col) continue;
            const f = col.getField();
            total += 1;
            if (canEditField(d, f)) any = true;
            else if (f === 'copy') started += 1;
            else nocol += 1;
          }
        }
        return { any, started, nocol, total };
      }

      function menuItem(label, key, action, disabled, reason) {
        const lab = key
          ? `<span class="tg-menu-lab">${esc(label)}</span><span class="tg-menu-key">${esc(key)}</span>`
          : `<span class="tg-menu-lab">${esc(label)}</span>`;
        const item = {
          label: reason ? `<span title="${esc(reason)}">${lab}</span>` : lab,
          action,
        };
        if (disabled) item.disabled = true;
        return item;
      }

      function contextMenu(_e, row) {
        const d = row.getData();
        const draft = !d.status || d.status === 'draft';
        const items = [];
        const box = getRangeBox();
        const rows = table.getRows();
        const uid = d._uid;
        const idx = rows.findIndex(r => r.getData()._uid === uid);
        const onSel = box && idx >= 0 && idx >= box.top && idx <= box.bottom;
        if (onSel) {
          const st = rangeWriteState();
          let why = '';
          if (!st.any) why = st.started ? t('tasks.ctx.started') : t('tasks.ctx.nowrite');
          items.push(menuItem(t('tasks.ctx.cut'), 'Ctrl+X', () => cutSelected(), !st.any, why));
          items.push(menuItem(t('tasks.ctx.copy_range'), 'Ctrl+C', () => copySelected(), false, ''));
          items.push(menuItem(t('tasks.ctx.paste'), 'Ctrl+V', () => pasteFromClipboard(), !st.any, why));
          items.push(menuItem(t('tasks.ctx.clear'), 'Delete', () => clearSelected(), !st.any, why));
          items.push({ separator: true });
        }
        items.push({ label: t('tasks.ctx.prepare'), action: () => prepareThis(row) });
        ;(function () {
          // 右键批量：勾选框选中的行 > 框选的单元格区域（包含右键那一行时）> 右键那一行
          const rows = ctxTargetData(row);
          // 已准备但表里文案是空的（上次迁移半路出错）也给机会重做：后端会从任务目录里的原文案取
          const withCopy = rows.filter(x => x && (hasCopy(x) || x.status === 'prepared'));
          const isBlocked = x => ['generating', 'collected', 'finished'].indexOf(x.status || '') >= 0
            || !!(x.counts && x.counts.takes);
          const okRows = withCopy.filter(x => !isBlocked(x));
          const ids = okRows.map(x => x.id).filter(Boolean);
          const nBlocked = withCopy.length - okRows.length;
          let tip = '';
          if (!withCopy.length) tip = t('tasks.ctx.migrate_none');
          else if (!ids.length) tip = t('tasks.ctx.migrate_blocked');
          else if (nBlocked) tip = t('tasks.ctx.migrate_skip_some', { n: String(nBlocked) });
          items.push({
            label: t('tasks.ctx.migrate_copy_desc') + (ids.length > 1 ? `（${ids.length} 个）` : ''),
            disabled: !ids.length,
            tip,
            action: () => migrateCopyToDesc(ids),
          });
        })();
        items.push({ label: t('tasks.ctx.open_folder'), action: () => openFolder(d) });
        items.push({ label: t('tasks.ctx.copy'), action: () => copyCopy(d) });
        const L = window.ImageSourceLogic;
        const curMode = L ? L.localModeOf(d, store.settings) : 'copy';
        const modeItems = [
          { v: 'copy', k: 'tasks.img.mode.copy' },
          { v: 'copy_mark', k: 'tasks.img.mode.copy_mark' },
          { v: 'move', k: 'tasks.img.mode.move' },
        ];
        modeItems.forEach(m => {
          const mark = curMode === m.v ? '✓ ' : '';
          items.push({
            label: mark + t('tasks.ctx.img_mode', { mode: t(m.k) }),
            action: () => setRowImageMode(row, m.v),
          });
        });
        items.push({ separator: true });
        items.push({ label: t('tasks.ctx.trash'), action: () => removeRow(row, false) });
        if (draft) items.push({ label: t('tasks.ctx.hard'), action: () => removeRow(row, true) });
        return items;
      }

      function saveColWidths(col) {
        if (!table) return;
        const tw = tableWidthPx();
        if (tw < 40) return;
        const prev = store.settings['ui.tasks.colwidths'] || {};
        const w = {};
        Object.keys(prev).forEach(k => {
          const n = Number(prev[k]);
          if (n > 0 && n <= 1) w[k] = n;
        });
        const cols = col ? [col] : [];
        cols.forEach(c => {
          const f = c.getField();
          if (!f || f === '_sel') return;
          w[f] = Math.round((c.getWidth() / tw) * 10000) / 10000;
        });
        API.post('/api/settings', { set: { 'ui.tasks.colwidths': w } })
          .then(r => { Object.assign(store.settings, r.settings || { 'ui.tasks.colwidths': w }); })
          .catch(() => {});
      }

      function applyColWidthRatios() {
        if (!table) return;
        const tw = tableWidthPx();
        if (tw < 40) return;
        table.getColumns().forEach(c => {
          const f = c.getField();
          const r = savedRatio(f);
          if (r == null) return;
          const minW = (c.getDefinition() && c.getDefinition().minWidth) || 40;
          const px = Math.max(minW, Math.round(r * tw));
          const def = c.getDefinition();
          if (def) def.width = px;
          try { c.setWidth(px); } catch (e) { /* 列还没画完 */ }
        });
      }

      function syncHScroll() {
        if (!table || !table.element) return;
        let minSum = 0;
        table.getColumns().forEach(c => {
          const def = c.getDefinition() || {};
          minSum += Number(def.minWidth) || 0;
        });
        const holder = table.element.querySelector('.tabulator-tableholder');
        const w = holder ? holder.clientWidth : table.element.clientWidth;
        table.element.classList.toggle('tg-cols-overflow', minSum > w + 1);
      }

      function resetColWidths() {
        store.settings['ui.tasks.colwidths'] = {};
        API.post('/api/settings', { set: { 'ui.tasks.colwidths': {} } })
          .then(r => { Object.assign(store.settings, r.settings || { 'ui.tasks.colwidths': {} }); })
          .catch(() => {});
        if (!table) return;
        table.setColumns(columns());
        nextTick(() => { applyColHints(); placeFillHandle(); syncHint(); syncHScroll(); });
      }

      function markCurrent() {
        const box = getRangeBox();
        const rows = table ? table.getRows() : [];
        let uid = '';
        let id = '';
        if (box && rows[box.top]) {
          const d = rows[box.top].getData() || {};
          uid = d._uid || '';
          id = d.id || '';
        }
        currentUid = uid;
        rows.forEach(row => {
          const el = row.getElement();
          if (!el) return;
          el.classList.toggle('row-current', row.getData()._uid === currentUid);
        });
        if (id && window.ui) ui.panelTid = id;
        placeFillHandle();
      }

      function ensureFillEls() {
        if (!tableEl.value) return;
        const overlay = tableEl.value.querySelector('.tabulator-range-overlay');
        if (!overlay) return;
        if (!fillEl) {
          fillEl = document.createElement('div');
          fillEl.className = 'tg-fill';
          fillEl.style.display = 'none';
          fillEl.addEventListener('mousedown', onFillDown);
          fillEl.addEventListener('pointerdown', onFillDown);
        }
        if (fillEl.parentNode !== overlay) overlay.appendChild(fillEl);
        if (!fillTip) {
          fillTip = document.createElement('div');
          fillTip.className = 'tg-fill-tip';
          fillTip.style.display = 'none';
          document.body.appendChild(fillTip);
        }
      }

      // 选区命中层按表头/单元格真实矩形算，不信 Tabulator overlay 的 left（冻结列会偏到文案列上）。
      function rangeOverlayBox(overlay) {
        const box = getRangeBox();
        if (box) {
          const left = colHeaderRect(box.left);
          const right = colHeaderRect(box.right);
          const top = cellClientRect(box.top, box.left);
          const bot = cellClientRect(box.bottom, box.right);
          if (left && right && top && bot && overlay) {
            const ov = overlay.getBoundingClientRect();
            return {
              l: left.left - ov.left,
              tp: top.top - ov.top,
              w: right.right - left.left,
              h: bot.bottom - top.top,
            };
          }
        }
        const active = overlay && (overlay.querySelector('.tabulator-range-active') || overlay.querySelector('.tabulator-range'));
        if (!active) return null;
        return {
          l: parseFloat(active.style.left) || 0,
          tp: parseFloat(active.style.top) || 0,
          w: parseFloat(active.style.width) || active.offsetWidth,
          h: parseFloat(active.style.height) || active.offsetHeight,
        };
      }

      function placeFillHandle() {
        ensureFillEls();
        if (!fillEl || fillDrag || moveDrag) {
          if (moveHit && (fillDrag || moveDrag)) moveHit.style.display = 'none';
          return;
        }
        // 编辑中柄会压在输入框右下角，先藏掉
        if (gridEditing.value || cellIsEditing()) {
          fillEl.style.display = 'none';
          if (moveHit) moveHit.style.display = 'none';
          return;
        }
        const overlay = tableEl.value && tableEl.value.querySelector('.tabulator-range-overlay');
        if (!overlay) { fillEl.style.display = 'none'; if (moveHit) moveHit.style.display = 'none'; return; }
        const geo = rangeOverlayBox(overlay);
        if (!geo || geo.w < 2 || geo.h < 2) {
          fillEl.style.display = 'none';
          if (moveHit) moveHit.style.display = 'none';
          return;
        }
        const { l, tp, w, h } = geo;
        fillEl.style.display = 'block';
        // 7px 方块的左上角对准选区右下角，再靠 CSS translate(50%,50%) 骑在角上
        fillEl.style.left = (l + w - 7) + 'px';
        fillEl.style.top = (tp + h - 7) + 'px';
        placeMoveHit(overlay, l, tp, w, h);
      }

      function rowIndexAtClientY(y) {
        const rows = table.getRows();
        if (!rows.length) return 0;
        let last = 0;
        let lastEl = null;
        for (let i = 0; i < rows.length; i++) {
          const el = rows[i].getElement();
          if (!el || !el.parentNode) continue;
          last = i;
          lastEl = el;
          const r = el.getBoundingClientRect();
          if (y < r.top) return i === 0 ? 0 : (y < (rows[i - 1].getElement() || el).getBoundingClientRect().bottom ? i - 1 : i);
          if (y <= r.bottom) return i;
        }
        if (!lastEl) return last;
        const rh = lastEl.getBoundingClientRect().height || (density.value === 'compact' ? 26 : 34);
        const extra = Math.max(0, Math.round((y - lastEl.getBoundingClientRect().bottom) / rh));
        return last + extra;
      }

      function fillPreview(box, toRow, copyMode) {
        const rows = table.getRows();
        const cols = pasteableColsInRange(box.left, box.right);
        if (!cols.length) return { last: '', count: 0, dir: 1, clear: false };
        const field = cols[0].getField();
        if (toRow > box.bottom) {
          const count = toRow - box.bottom;
          const src = [];
          for (let r = box.top; r <= box.bottom; r++) {
            const row = rows[r];
            src.push(row ? String(row.getData()[field] == null ? '' : row.getData()[field]) : '');
          }
          const vals = TasksSheet.fillValues(src, count, 1, copyMode ? 'copy' : 'auto', !!LIST_FIELDS[field]);
          return { last: vals.length ? vals[vals.length - 1] : '', count, dir: 1, clear: false, vals, field, cols };
        }
        if (toRow < box.top) {
          const count = box.top - toRow;
          const src = [];
          for (let r = box.top; r <= box.bottom; r++) {
            const row = rows[r];
            src.push(row ? String(row.getData()[field] == null ? '' : row.getData()[field]) : '');
          }
          const vals = TasksSheet.fillValues(src, count, -1, copyMode ? 'copy' : 'auto', !!LIST_FIELDS[field]);
          return { last: vals.length ? vals[vals.length - 1] : '', count, dir: -1, clear: false, vals, field, cols };
        }
        return { last: '', count: 0, dir: 0, clear: true, clearFrom: toRow, cols };
      }

      function onFillDown(ev) {
        if (ev.button !== 0) return;
        if (fillDrag) return;
        if (gridEditing.value || cellIsEditing()) return;
        ev.preventDefault();
        ev.stopPropagation();
        const box = getRangeBox();
        if (!box) return;
        fillDrag = { box, copy: !!(ev.ctrlKey || ev.metaKey) };
        if (moveHit) moveHit.style.display = 'none';
        document.addEventListener('mousemove', onFillMove);
        document.addEventListener('mouseup', onFillUp);
        document.addEventListener('pointermove', onFillMove);
        document.addEventListener('pointerup', onFillUp);
      }

      function onFillMove(ev) {
        if (!fillDrag) return;
        const toRow = rowIndexAtClientY(ev.clientY);
        const prev = fillPreview(fillDrag.box, toRow, fillDrag.copy);
        fillDrag.toRow = toRow;
        fillDrag.prev = prev;
        if (fillTip) {
          fillTip.style.display = 'block';
          fillTip.style.left = (ev.clientX + 12) + 'px';
          fillTip.style.top = (ev.clientY + 14) + 'px';
          fillTip.textContent = prev.clear ? '' : ('→ ' + (prev.last || ''));
          if (prev.clear) fillTip.style.display = 'none';
        }
        const holder = tableEl.value && tableEl.value.querySelector('.tabulator-tableholder');
        if (holder) {
          const r = holder.getBoundingClientRect();
          if (ev.clientY > r.bottom - 24) holder.scrollTop += 18;
          else if (ev.clientY < r.top + 24) holder.scrollTop -= 18;
        }
      }

      async function onFillUp(ev) {
        document.removeEventListener('mousemove', onFillMove);
        document.removeEventListener('mouseup', onFillUp);
        document.removeEventListener('pointermove', onFillMove);
        document.removeEventListener('pointerup', onFillUp);
        if (fillTip) fillTip.style.display = 'none';
        const drag = fillDrag;
        fillDrag = null;
        placeFillHandle();
        if (!drag || drag.toRow == null) return;
        const box = drag.box;
        const prev = drag.prev || fillPreview(box, drag.toRow, drag.copy);
        const cols = pasteableColsInRange(box.left, box.right);
        if (!cols.length) return;
        pushUndo();
        holdUndo = true;
        table.blockRedraw();
        try {
          if (prev.clear) {
            const rows = table.getRows();
            const from = Math.min(Math.max(box.top, drag.toRow + 1), box.bottom);
            for (let r = from; r <= box.bottom; r++) {
              const row = rows[r];
              if (!row) continue;
              const d = row.getData();
              const patch = {};
              cols.forEach(c => {
                const f = c.getField();
                if (canEditField(d, f)) patch[f] = '';
              });
              if (Object.keys(patch).length) row.update(patch);
            }
          } else if (prev.dir === 1) {
            const have = allRowComps().length;
            const need = box.bottom + 1 + prev.count - have;
            if (need > 0) await table.addData(makeBlankRows(need), false);
            const rows = table.getRows();
            cols.forEach(col => {
              const f = col.getField();
              const src = [];
              for (let r = box.top; r <= box.bottom; r++) {
                const row = rows[r];
                src.push(row ? String(row.getData()[f] == null ? '' : row.getData()[f]) : '');
              }
              const vals = TasksSheet.fillValues(src, prev.count, 1, drag.copy ? 'copy' : 'auto', !!LIST_FIELDS[f]);
              for (let i = 0; i < vals.length; i++) {
                const row = rows[box.bottom + 1 + i];
                if (!row) continue;
                const d = row.getData();
                if (!canEditField(d, f)) continue;
                let val = vals[i];
                if (f === 'type') val = normType(val);
                if (f === 'duration_pref') val = normDur(val);
                row.update({ [f]: val });
              }
            });
          } else if (prev.dir === -1) {
            const rows = table.getRows();
            const start = Math.max(0, box.top - prev.count);
            const count = box.top - start;
            cols.forEach(col => {
              const f = col.getField();
              const src = [];
              for (let r = box.top; r <= box.bottom; r++) {
                const row = rows[r];
                src.push(row ? String(row.getData()[f] == null ? '' : row.getData()[f]) : '');
              }
              const vals = TasksSheet.fillValues(src, count, -1, drag.copy ? 'copy' : 'auto', !!LIST_FIELDS[f]);
              for (let i = 0; i < vals.length; i++) {
                const row = rows[box.top - 1 - i];
                if (!row) continue;
                const d = row.getData();
                if (!canEditField(d, f)) continue;
                let val = vals[i];
                if (f === 'type') val = normType(val);
                if (f === 'duration_pref') val = normDur(val);
                row.update({ [f]: val });
              }
            });
          }
          markDirty();
          scanWarn();
        } finally {
          table.restoreRedraw();
          holdUndo = false;
        }
        placeFillHandle();
      }

      function writeClipboard(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text);
        else {
          const ta = document.createElement('textarea');
          ta.value = text; document.body.appendChild(ta); ta.select();
          document.execCommand('copy'); ta.remove();
        }
      }

      function copySelected() {
        const grid = selectedGrid();
        if (!grid.length) return false;
        writeClipboard(TasksSheet.serializeTSV(grid));
        return true;
      }

      function cutSelected() {
        const grid = selectedGrid();
        if (!grid.length) return;
        writeClipboard(TasksSheet.serializeTSV(grid));
        const box = getRangeBox();
        if (!box) return;
        const nR = grid.length;
        const nC = grid.reduce((m, line) => Math.max(m, line.length), 0);
        const rows = table.getRows();
        const cols = visibleCols();
        let skipped = 0;
        const patches = [];
        for (let r = box.top; r <= box.bottom; r++) {
          const row = rows[r];
          if (!row) continue;
          const d = row.getData();
          const patch = {};
          for (let c = box.left; c <= box.right; c++) {
            const col = cols[c];
            if (!col) continue;
            const f = col.getField();
            if (canEditField(d, f)) patch[f] = '';
            else if (f === 'copy') skipped += 1;
          }
          if (Object.keys(patch).length) patches.push({ row, patch });
        }
        if (patches.length) {
          pushUndo();
          holdUndo = true;
          patches.forEach(p => p.row.update(p.patch));
          holdUndo = false;
          markDirty();
          scanWarn();
        }
        let msg = t('tasks.cut.done', { r: String(nR), c: String(nC) });
        if (skipped) msg += '，' + t('tasks.cut.skipped', { n: String(skipped) });
        toast(msg, '', 'ok');
      }

      async function pasteFromClipboard() {
        let text = '';
        let html = '';
        try {
          if (navigator.clipboard && navigator.clipboard.read) {
            const items = await navigator.clipboard.read();
            for (const it of items) {
              if (!html && it.types && it.types.includes('text/html')) {
                html = await (await it.getType('text/html')).text();
              }
              if (!text && it.types && it.types.includes('text/plain')) {
                text = await (await it.getType('text/plain')).text();
              }
            }
          }
        } catch (e) { /* 权限拦了就退回 readText */ }
        if (!text) {
          try {
            if (navigator.clipboard && navigator.clipboard.readText) text = await navigator.clipboard.readText();
          } catch (e2) { /* 没内容 */ }
        }
        if (!text) return;
        const raw = TasksSheet.parseTSVGrid(text);
        const grid = TasksSheet.stripHeaderRow(raw);
        if (!grid.length) return;
        pendingPasteLinks = html ? alignPasteLinks(raw, grid, TasksSheet.parseHtmlGridLinks(html)) : null;
        await applyPaste(grid);
      }

      function setRangeBox(top, left, bottom, right) {
        if (!table) return;
        const rows = table.getRows();
        const cols = visibleCols();
        const r0 = rows[top], r1 = rows[bottom];
        const c0 = cols[left], c1 = cols[right];
        if (!r0 || !r1 || !c0 || !c1) return;
        try {
          const a = r0.getCell(c0.getField());
          const b = r1.getCell(c1.getField());
          if (!a || !b) return;
          const ranges = (typeof table.getRanges === 'function') ? table.getRanges() : [];
          if (ranges && ranges.length && typeof ranges[0].setBounds === 'function') ranges[0].setBounds(a, b);
          else if (typeof table.addRange === 'function') table.addRange(a, b);
        } catch (e) { /* 选区模块还没就绪 */ }
      }

      function ensureMoveEls() {
        if (!tableEl.value) return;
        const overlay = tableEl.value.querySelector('.tabulator-range-overlay');
        if (!overlay) return;
        if (!moveHit) {
          moveHit = document.createElement('div');
          moveHit.className = 'tg-move-hit';
          moveHit.style.display = 'none';
          ['n', 's', 'w', 'e'].forEach(dir => {
            const ed = document.createElement('div');
            ed.className = 'tg-move-edge tg-move-' + dir;
            ed.addEventListener('mousedown', onMoveDown);
            ed.addEventListener('contextmenu', onMoveContext);
            moveHit.appendChild(ed);
          });
        }
        if (moveHit.parentNode !== overlay) overlay.appendChild(moveHit);
        if (!moveGhost) {
          moveGhost = document.createElement('div');
          moveGhost.className = 'tg-move-ghost';
          moveGhost.style.display = 'none';
          movePlus = document.createElement('div');
          movePlus.className = 'tg-move-plus';
          movePlus.textContent = '+';
          moveGhost.appendChild(movePlus);
          document.body.appendChild(moveGhost);
        }
      }

      function placeMoveHit(overlay, l, tp, w, h) {
        ensureMoveEls();
        if (!moveHit || moveDrag) return;
        if (!overlay) {
          overlay = tableEl.value && tableEl.value.querySelector('.tabulator-range-overlay');
        }
        if (!overlay) { moveHit.style.display = 'none'; return; }
        if (l == null) {
          const active = overlay.querySelector('.tabulator-range-active') || overlay.querySelector('.tabulator-range');
          if (!active) { moveHit.style.display = 'none'; return; }
          w = parseFloat(active.style.width) || active.offsetWidth;
          h = parseFloat(active.style.height) || active.offsetHeight;
          l = parseFloat(active.style.left) || 0;
          tp = parseFloat(active.style.top) || 0;
        }
        moveHit.style.display = 'block';
        moveHit.style.left = l + 'px';
        moveHit.style.top = tp + 'px';
        moveHit.style.width = w + 'px';
        moveHit.style.height = h + 'px';
      }

      function gridRowAtY(y) {
        const rows = table.getRows();
        if (!rows.length) return 0;
        for (let i = 0; i < rows.length; i++) {
          const el = rows[i].getElement();
          if (!el || !el.parentNode) continue;
          const r = el.getBoundingClientRect();
          if (y < r.top) {
            const h = r.height || (density.value === 'compact' ? 26 : 34);
            return i + Math.floor((y - r.top) / h);
          }
          break;
        }
        return rowIndexAtClientY(y);
      }

      // 列命中用表头可视矩形，不用 row.getCell（冻结列会克隆行，文案格的 rect 会盖到后面几列上）。
      function colHeaderRect(colIdx) {
        const cols = visibleCols();
        const nC = cols.length;
        if (!nC) return null;
        const ci = Math.max(0, Math.min(Math.max(colIdx, 0), nC - 1));
        let base = null;
        try {
          const el = cols[ci].getElement();
          if (el) {
            const r = el.getBoundingClientRect();
            if (r.width >= 2) base = r;
          }
        } catch (e) { /* 列头还没画 */ }
        if (!base) {
          const w = cols[ci].getWidth() || 80;
          const holder = tableEl.value && tableEl.value.querySelector('.tabulator-tableholder');
          const hl = holder ? holder.getBoundingClientRect().left : 0;
          let left = hl;
          for (let i = 0; i < ci; i++) left += cols[i].getWidth() || 80;
          base = { left, width: w, right: left + w };
        }
        const w = base.width || cols[ci].getWidth() || 80;
        const left = base.left + (colIdx - ci) * w;
        return { left, width: w, right: left + w };
      }

      function gridColAtX(x) {
        const cols = visibleCols();
        if (!cols.length) return 0;
        let last = 0;
        let lastRect = null;
        let firstRect = null;
        for (let i = 0; i < cols.length; i++) {
          const r = colHeaderRect(i);
          if (!r || r.width < 2) continue;
          if (!firstRect) {
            firstRect = r;
            if (x < r.left) return Math.floor((x - r.left) / (r.width || 80));
          }
          last = i;
          lastRect = r;
          if (x <= r.right) return i;
        }
        if (!lastRect) return last;
        const w = lastRect.width || 80;
        const extra = Math.max(0, Math.round((x - lastRect.right) / w));
        return last + extra;
      }

      function planMove(box, dRow, dCol) {
        const cols = visibleCols();
        const rows = table.getRows();
        const nC = cols.length;
        const nR = rows.length;
        const dest = {
          top: box.top + dRow,
          bottom: box.bottom + dRow,
          left: box.left + dCol,
          right: box.right + dCol,
        };
        const oobCol = dest.left < 0 || dest.right >= nC;
        const oobTop = dest.top < 0;
        const need = Math.max(0, dest.bottom + 1 - nR);
        const blocked = [];
        if (!oobCol && !oobTop) {
          for (let r = box.top; r <= box.bottom; r++) {
            for (let c = box.left; c <= box.right; c++) {
              const srcCol = cols[c];
              if (!srcCol) continue;
              const sf = srcCol.getField();
              if (PASTEABLE.indexOf(sf) < 0) continue;
              const dc = c + dCol;
              const dr = r + dRow;
              const destCol = cols[dc];
              if (!destCol) continue;
              const df = destCol.getField();
              const destRow = rows[dr];
              const d = destRow ? destRow.getData() : { status: 'draft' };
              if (!canEditField(d, df)) {
                blocked.push({
                  row: dr, field: df,
                  id: (destRow && destRow.getData().id) || '',
                  name: (destRow && destRow.getData().name) || '',
                });
              }
            }
          }
        }
        return {
          dest, dRow, dCol,
          nR: box.bottom - box.top + 1,
          nC: box.right - box.left + 1,
          legal: !oobCol && !oobTop && !blocked.length,
          blocked, oobCol, oobTop, need,
        };
      }

      function cellClientRect(rowIdx, colIdx) {
        const rows = table.getRows();
        const cols = visibleCols();
        const nR = rows.length;
        const nC = cols.length;
        const rh = density.value === 'compact' ? 26 : 34;
        if (!nR || !nC) return null;
        const ci = Math.max(0, Math.min(colIdx, nC - 1));
        const ri = Math.max(0, Math.min(Math.max(rowIdx, 0), nR - 1));
        let base = null;
        try {
          const cell = rows[ri].getCell(cols[ci].getField());
          const el = cell && cell.getElement();
          if (el && el.parentNode) base = el.getBoundingClientRect();
        } catch (e) { /* 虚拟滚动没画出这格 */ }
        if (!base) {
          const overlay = tableEl.value && tableEl.value.querySelector('.tabulator-range-overlay');
          const active = overlay && (overlay.querySelector('.tabulator-range-active') || overlay.querySelector('.tabulator-range'));
          if (active) {
            const ar = active.getBoundingClientRect();
            const cw = cols[ci].getWidth() || 80;
            base = { left: ar.left, right: ar.left + cw, top: ar.top, bottom: ar.top + rh, width: cw, height: rh };
          } else return null;
        }
        const cw = base.width || cols[ci].getWidth() || 80;
        const ch = base.height || rh;
        const left = base.left + (colIdx - ci) * cw;
        const top = base.top + (rowIdx - ri) * ch;
        return { left, top, right: left + cw, bottom: top + ch, width: cw, height: ch };
      }

      function destGhostRect(dest) {
        const ca = colHeaderRect(dest.left);
        const cb = colHeaderRect(dest.right);
        const a = cellClientRect(dest.top, dest.left);
        const b = cellClientRect(dest.bottom, dest.right);
        if ((!ca && !a) || (!cb && !b) || !a || !b) return null;
        const left = ca ? ca.left : Math.min(a.left, b.left);
        const right = cb ? cb.right : Math.max(a.right, b.right);
        const top = Math.min(a.top, b.top);
        return {
          left, top,
          width: right - left,
          height: Math.max(a.bottom, b.bottom) - top,
        };
      }

      function showMoveGhost(plan, copy, ev) {
        if (!moveGhost || !plan) return;
        const rect = destGhostRect(plan.dest);
        if (!rect) { moveGhost.style.display = 'none'; return; }
        moveGhost.style.display = 'block';
        moveGhost.style.left = rect.left + 'px';
        moveGhost.style.top = rect.top + 'px';
        moveGhost.style.width = rect.width + 'px';
        moveGhost.style.height = rect.height + 'px';
        moveGhost.classList.toggle('is-bad', !plan.legal);
        moveGhost.classList.toggle('is-copy', !!copy);
        ensureDragEls();
        if (dragTip && ev) {
          dragTip.style.display = 'block';
          dragTip.style.left = (ev.clientX + 12) + 'px';
          dragTip.style.top = (ev.clientY + 12) + 'px';
          dragTip.textContent = copy
            ? t('tasks.copy.range', { r: String(plan.nR), c: String(plan.nC) })
            : t('tasks.move.range', { r: String(plan.nR), c: String(plan.nC) });
        }
      }

      function hideMoveGhost() {
        if (moveGhost) moveGhost.style.display = 'none';
        if (dragTip && !rowDrag) dragTip.style.display = 'none';
      }

      function onMoveContext(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (!moveHit) return;
        // 边框 pointer-events:auto，先藏掉才能点到底下的格子
        moveHit.style.display = 'none';
        const under = document.elementFromPoint(ev.clientX, ev.clientY);
        moveHit.style.display = 'block';
        const cell = under && under.closest && under.closest('.tabulator-cell');
        if (!cell) return;
        cell.dispatchEvent(new MouseEvent('contextmenu', {
          bubbles: true, cancelable: true,
          clientX: ev.clientX, clientY: ev.clientY, button: 2,
        }));
      }

      function onMoveDown(ev) {
        if (ev.button !== 0) return;
        if (moveDrag || fillDrag || rowDrag) return;
        if (gridEditing.value || cellIsEditing()) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        const box0 = getRangeBox();
        if (!box0) return;
        const box = { top: box0.top, bottom: box0.bottom, left: box0.left, right: box0.right };
        const sr = Math.max(box.top, Math.min(box.bottom, gridRowAtY(ev.clientY)));
        const sc = Math.max(box.left, Math.min(box.right, gridColAtX(ev.clientX)));
        moveDrag = { box, startRow: sr, startCol: sc, copy: !!(ev.ctrlKey || ev.metaKey), plan: null };
        rangeDrag = false;
        if (fillEl) fillEl.style.display = 'none';
        if (moveHit) moveHit.style.display = 'none';
        document.addEventListener('mousemove', onMoveMove, true);
        document.addEventListener('mouseup', onMoveUp, true);
        document.addEventListener('pointermove', onMoveMove, true);
        document.addEventListener('pointerup', onMoveUp, true);
        document.addEventListener('keydown', onMoveKey, true);
        showMoveGhost(planMove(box, 0, 0), moveDrag.copy, ev);
      }

      function onMoveKey(e) {
        if (e.key !== 'Escape') return;
        e.preventDefault();
        e.stopPropagation();
        cancelMoveDrag();
      }

      function cancelMoveDrag() {
        document.removeEventListener('mousemove', onMoveMove, true);
        document.removeEventListener('mouseup', onMoveUp, true);
        document.removeEventListener('pointermove', onMoveMove, true);
        document.removeEventListener('pointerup', onMoveUp, true);
        document.removeEventListener('keydown', onMoveKey, true);
        moveDrag = null;
        hideMoveGhost();
        placeFillHandle();
      }

      function onMoveMove(ev) {
        if (!moveDrag) return;
        if (ev.cancelable) ev.preventDefault();
        moveDrag.copy = !!(ev.ctrlKey || ev.metaKey);
        const dRow = gridRowAtY(ev.clientY) - moveDrag.startRow;
        const dCol = gridColAtX(ev.clientX) - moveDrag.startCol;
        const plan = planMove(moveDrag.box, dRow, dCol);
        moveDrag.plan = plan;
        showMoveGhost(plan, moveDrag.copy, ev);
        const holder = tableEl.value && tableEl.value.querySelector('.tabulator-tableholder');
        if (holder) {
          const r = holder.getBoundingClientRect();
          if (ev.clientY > r.bottom - 24) holder.scrollTop += 18;
          else if (ev.clientY < r.top + 24) holder.scrollTop -= 18;
          if (ev.clientX > r.right - 24) holder.scrollLeft += 18;
          else if (ev.clientX < r.left + 24) holder.scrollLeft -= 18;
        }
      }

      async function onMoveUp(ev) {
        document.removeEventListener('mousemove', onMoveMove, true);
        document.removeEventListener('mouseup', onMoveUp, true);
        document.removeEventListener('pointermove', onMoveMove, true);
        document.removeEventListener('pointerup', onMoveUp, true);
        document.removeEventListener('keydown', onMoveKey, true);
        const drag = moveDrag;
        moveDrag = null;
        hideMoveGhost();
        if (!drag) { placeFillHandle(); return; }
        const dRow = gridRowAtY(ev.clientY) - drag.startRow;
        const dCol = gridColAtX(ev.clientX) - drag.startCol;
        const copy = !!(ev.ctrlKey || ev.metaKey) || drag.copy;
        const plan = planMove(drag.box, dRow, dCol);
        if (dRow === 0 && dCol === 0) { placeFillHandle(); return; }
        if (!plan.legal) {
          if (plan.blocked.length) toast(blockedToast(plan.blocked), '', 'bad');
          placeFillHandle();
          return;
        }
        await applyMove(drag.box, plan, copy);
        placeFillHandle();
      }

      function blockedToast(blocked) {
        const bits = blocked.slice(0, 8).map(b => {
          const who = b.id || b.name || '';
          return (who ? who + ' ' : '') + fieldLabel(b.field);
        });
        const started = blocked.some(b => b.field === 'copy');
        const reason = started ? t('tasks.move.started') : t('tasks.move.nocol');
        return t('tasks.move.blocked', { cells: bits.join('、'), reason });
      }

      async function applyMove(box, plan, copy) {
        if (!table || !plan || !plan.legal) return;
        if (plan.dRow === 0 && plan.dCol === 0) return;
        {
          const cols0 = visibleCols();
          let hasData = false;
          for (let c = box.left; c <= box.right; c++) {
            const col = cols0[c];
            if (col && PASTEABLE.indexOf(col.getField()) >= 0) { hasData = true; break; }
          }
          if (!hasData) return;
        }
        pushUndo();
        holdUndo = true;
        table.blockRedraw();
        try {
          if (plan.need > 0) {
            await table.addData(makeBlankRows(plan.need), false);
            toast(t('tasks.move.added', { n: String(plan.need) }), '', 'ok');
          }
          const rows = table.getRows();
          const cols = visibleCols();
          const payload = [];
          for (let r = box.top; r <= box.bottom; r++) {
            const row = rows[r];
            if (!row) continue;
            const d = row.getData();
            for (let c = box.left; c <= box.right; c++) {
              const col = cols[c];
              if (!col) continue;
              const f = col.getField();
              if (PASTEABLE.indexOf(f) < 0) continue;
              payload.push({ sr: r, sc: c, sf: f, val: d[f] == null ? '' : d[f] });
            }
          }
          if (!copy) {
            const byRow = {};
            payload.forEach(p => {
              const row = rows[p.sr];
              if (!row) return;
              const d = row.getData();
              if (!canEditField(d, p.sf)) return;
              if (!byRow[p.sr]) byRow[p.sr] = { row, patch: {} };
              byRow[p.sr].patch[p.sf] = '';
            });
            Object.keys(byRow).forEach(k => byRow[k].row.update(byRow[k].patch));
          }
          const destByRow = {};
          payload.forEach(p => {
            const dr = p.sr + plan.dRow;
            const dc = p.sc + plan.dCol;
            const row = rows[dr];
            const col = cols[dc];
            if (!row || !col) return;
            const f = col.getField();
            const d = row.getData();
            if (!canEditField(d, f)) return;
            let val = p.val;
            if (f === 'type') val = normType(val);
            if (f === 'duration_pref') val = normDur(val);
            if (!destByRow[dr]) destByRow[dr] = { row, patch: {} };
            destByRow[dr].patch[f] = val;
          });
          Object.keys(destByRow).forEach(k => destByRow[k].row.update(destByRow[k].patch));
          markDirty();
          recount();
          scanWarn();
        } finally {
          table.restoreRedraw();
          holdUndo = false;
        }
        setRangeBox(plan.dest.top, plan.dest.left, plan.dest.bottom, plan.dest.right);
      }

      function activeCell() {
        const box = getRangeBox();
        if (!box) return null;
        const rows = table.getRows();
        const cols = visibleCols();
        const row = rows[box.top];
        const col = cols[box.left];
        if (!row || !col) return null;
        try { return row.getCell(col.getField()); } catch (e) { return null; }
      }

      function copyWho(d) {
        const id = (d && d.id) || '';
        const name = (d && d.name) || '';
        const who = [id, name].filter(Boolean).join(' ');
        return who || t('tasks.copyed.unsaved');
      }

      function isCopyReadonly(d) {
        return !!(d && d.status && d.status !== 'draft');
      }

      function closeCopyEd() {
        clearTimeout(copySegTimer);
        copySegTimer = 0;
        if (dlg.value === 'copy') dlg.value = null;
        copyEd.value = null;
        copyEdText.value = '';
        copyEdSegs.value = null;
      }

      function askCloseCopyEd() {
        if (dlg.value !== 'copy' || !copyEd.value) { closeCopyEd(); return; }
        if (!copyEd.value.readonly && copyEdText.value !== copyEd.value.orig) {
          if (!confirm(t('tasks.copyed.discard'))) return;
        }
        closeCopyEd();
      }

      async function runCopySegs() {
        const seq = ++copySegSeq;
        const text = copyEdText.value || '';
        if (!String(text).trim()) {
          if (seq === copySegSeq) copyEdSegs.value = 0;
          return;
        }
        const engine = (copyEd.value && copyEd.value.engine) || 'flow';
        const eng = (engine === 'grok' || engine === 'studio') ? engine : 'flow';
        // 不走 API.post：拆段失败会 toast。预演只是提示，失败就显示 —。
        try {
          const res = await fetch('/api/split/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, engine: eng }),
          });
          const body = await res.json();
          if (seq !== copySegSeq) return;
          if (body && body.ok && Array.isArray(body.segments)) copyEdSegs.value = body.segments.length;
          else copyEdSegs.value = null;
        } catch (e) {
          if (seq === copySegSeq) copyEdSegs.value = null;
        }
      }

      function scheduleCopySegs() {
        clearTimeout(copySegTimer);
        copySegTimer = setTimeout(runCopySegs, 280);
      }

      function openCopyEd(row, opts) {
        if (!row) return;
        const d = row.getData();
        const readonly = isCopyReadonly(d);
        const seed = opts && opts.seed;
        const orig = d.copy || '';
        let text = orig;
        // 草稿行直接打字 = Sheet 覆盖式进入；已开工只读，不吃这个字
        if (!readonly && seed != null) text = String(seed);
        copyEd.value = {
          uid: d._uid,
          id: d.id || '',
          name: d.name || '',
          who: copyWho(d),
          orig,
          readonly,
          engine: engineOf(d) || 'flow',
        };
        copyEdText.value = text;
        copyEdSegs.value = null;
        dlg.value = 'copy';
        scheduleCopySegs();
        nextTick(() => {
          const ta = copyTaEl.value;
          if (!ta) return;
          ta.focus();
          const n = ta.value.length;
          try { ta.setSelectionRange(n, n); } catch (e) { /* 有的环境不让设光标 */ }
        });
      }

      function onCopyEdInput() { scheduleCopySegs(); }

      async function saveCopyEd() {
        if (!copyEd.value || copyEd.value.readonly) { closeCopyEd(); return; }
        const text = copyEdText.value;
        if (text === copyEd.value.orig) { closeCopyEd(); return; }
        const uid = copyEd.value.uid;
        const row = allRowComps().find(r => r.getData()._uid === uid);
        if (!row) { closeCopyEd(); return; }
        pushUndo();
        row.update({ copy: text, _error: '' });
        markDirty();
        paintRow(row);
        scanWarn();
        closeCopyEd();
        await saveTable({ quiet: true });
      }

      function selectAllCopyEd() {
        const ta = copyTaEl.value;
        const text = copyEdText.value || '';
        if (ta) {
          ta.focus();
          try { ta.select(); } catch (e) { /* ignore */ }
        }
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text);
          else if (ta) document.execCommand('copy');
        } catch (e) { /* 浏览器拦了就只全选 */ }
      }

      function startEdit(replaceWith) {
        const cell = activeCell();
        if (!cell) return;
        if (cell.getField() === 'copy') {
          openCopyEd(cell.getRow(), replaceWith != null ? { seed: replaceWith } : null);
          return;
        }
        const d = cell.getRow().getData();
        if (!canEditField(d, cell.getField())) return;
        try { cell.edit(true); } catch (e) { return; }
        if (replaceWith == null) return;
        requestAnimationFrame(() => {
          const input = cell.getElement().querySelector('input, textarea');
          if (!input) return;
          input.value = replaceWith;
          input.dispatchEvent(new Event('input', { bubbles: true }));
        });
      }

      function clearSelected() {
        const box = getRangeBox();
        if (!box) return;
        const rows = table.getRows();
        const cols = visibleCols();
        pushUndo();
        holdUndo = true;
        for (let r = box.top; r <= box.bottom; r++) {
          const row = rows[r];
          if (!row) continue;
          const d = row.getData();
          const patch = {};
          for (let c = box.left; c <= box.right; c++) {
            const col = cols[c];
            if (!col) continue;
            const f = col.getField();
            if (canEditField(d, f)) patch[f] = '';
          }
          if (Object.keys(patch).length) row.update(patch);
        }
        holdUndo = false;
        markDirty();
        scanWarn();
      }

      function selectAll() {
        if (!table) return;
        const rows = table.getRows();
        const cols = visibleCols();
        if (!rows.length || !cols.length) return;
        const firstField = PASTEABLE.find(f => cols.some(c => c.getField() === f)) || cols[0].getField();
        const lastField = [...PASTEABLE].reverse().find(f => cols.some(c => c.getField() === f)) || cols[cols.length - 1].getField();
        try {
          const a = rows[0].getCell(firstField);
          const b = rows[rows.length - 1].getCell(lastField);
          if (!a || !b) return;
          const ranges = (typeof table.getRanges === 'function') ? table.getRanges() : [];
          if (ranges && ranges.length && typeof ranges[0].setBounds === 'function') ranges[0].setBounds(a, b);
          else if (typeof table.addRange === 'function') table.addRange(a, b);
        } catch (e) { /* 选区模块还没就绪 */ }
      }

      function collectFindCells() {
        if (!table) return [];
        const box = getRangeBox();
        const rows = table.getRows();
        const cols = visibleCols();
        const multi = box && (box.bottom > box.top || box.right > box.left);
        const out = [];
        const push = (row, f, v) => {
          if (PASTEABLE.indexOf(f) < 0) return;
          out.push({ row, field: f, value: v == null ? '' : String(v) });
        };
        if (multi) {
          for (let r = box.top; r <= box.bottom; r++) {
            const row = rows[r];
            if (!row) continue;
            const d = row.getData();
            for (let c = box.left; c <= box.right; c++) {
              const col = cols[c];
              if (col) push(row, col.getField(), d[col.getField()]);
            }
          }
        } else {
          rows.forEach(row => {
            const d = row.getData();
            PASTEABLE.forEach(f => push(row, f, d[f]));
          });
        }
        return out;
      }

      function matchFind(val, q) {
        if (!q) return false;
        let a = String(val == null ? '' : val);
        let b = q;
        if (!findCase.value) { a = a.toLowerCase(); b = b.toLowerCase(); }
        if (findCell.value) return a === b;
        return a.indexOf(b) >= 0;
      }

      function rebuildFindHits() {
        const q = findQ.value;
        findHits = collectFindCells().filter(c => matchFind(c.value, q));
        if (!findHits.length) { findIdx = -1; findHit.value = t('tasks.find.none'); return; }
        if (findIdx < 0 || findIdx >= findHits.length) findIdx = 0;
        findHit.value = t('search.count', { i: String(findIdx + 1), n: String(findHits.length) });
      }

      function gotoFind(delta) {
        rebuildFindHits();
        if (!findHits.length) return;
        findIdx = (findIdx + delta + findHits.length) % findHits.length;
        findHit.value = t('search.count', { i: String(findIdx + 1), n: String(findHits.length) });
        const hit = findHits[findIdx];
        try {
          hit.row.scrollTo();
          const cell = hit.row.getCell(hit.field);
          if (cell && table.addRange) table.addRange(cell, cell);
        } catch (e) { /* ignore */ }
      }

      function replaceAll() {
        const q = findQ.value;
        if (!q) return;
        const hits = collectFindCells().filter(c => matchFind(c.value, q) && canEditField(c.row.getData(), c.field));
        if (!hits.length) { toast(t('tasks.find.none'), '', 'bad'); return; }
        if (!confirm(t('tasks.find.replace_n', { n: String(hits.length) }))) return;
        pushUndo();
        holdUndo = true;
        const repl = findR.value;
        hits.forEach(h => {
          const cur = String(h.row.getData()[h.field] == null ? '' : h.row.getData()[h.field]);
          let next;
          if (findCell.value) next = repl;
          else if (findCase.value) next = cur.split(q).join(repl);
          else {
            const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
            next = cur.replace(re, repl);
          }
          const f = h.field;
          if (f === 'type') next = normType(next);
          if (f === 'duration_pref') next = normDur(next);
          h.row.update({ [f]: next });
        });
        holdUndo = false;
        markDirty();
        scanWarn();
        toast(t('tasks.find.replaced', { n: String(hits.length) }), '', 'ok');
        rebuildFindHits();
      }

      function openFind() {
        findOn.value = true;
        nextTick(() => {
          const el = panelEl.value && panelEl.value.querySelector('.tg-find input[type=text]');
          if (el) el.focus();
        });
      }

      function onPanelKey(e) {
        // 文案弹层开着：面板快捷键全部让路，只接 Ctrl+Enter 保存 / Esc 取消
        if (dlg.value === 'copy') {
          if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            e.preventDefault();
            e.stopPropagation();
            saveCopyEd();
            return;
          }
          if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            askCloseCopyEd();
            return;
          }
          return;
        }
        if (e.key === 'Escape' && moveDrag) {
          e.preventDefault();
          cancelMoveDrag();
          return;
        }
        if (e.key === 'Escape' && (rowDrag || (table && table.modules && table.modules.moveRow && table.modules.moveRow.moving))) {
          e.preventDefault();
          cancelRowDrag();
          return;
        }
        if (e.key === 'Escape' && moreOpen.value) {
          e.preventDefault();
          moreOpen.value = false;
          return;
        }
        if (dlg.value) return;
        const inPanel = panelEl.value && panelEl.value.contains(e.target);
        const editing = table && table.modules && table.modules.edit && table.modules.edit.currentCell;
        const ctrl = e.ctrlKey || e.metaKey;
        if (ctrl && (e.key === 's' || e.key === 'S')) {
          if (!inPanel && ui.node !== 'tasks') return;
          e.preventDefault();
          saveTable();
          return;
        }
        if (ctrl && (e.key === 'f' || e.key === 'F')) {
          if (!inPanel && ui.node !== 'tasks') return;
          e.preventDefault();
          openFind();
          return;
        }
        if (ctrl && (e.key === 'z' || e.key === 'Z')) {
          if (typingTarget(e.target) && !e.target.classList.contains('tg-add-n')) return;
          if (editing) return;
          e.preventDefault();
          if (e.shiftKey) redo(); else undo();
          return;
        }
        if (!table) return;
        const holder = tableEl.value && tableEl.value.querySelector('.tabulator-tableholder');
        const inTable = holder && (holder.contains(e.target) || e.target === holder || tableEl.value.contains(e.target));
        if (!inTable || typingTarget(e.target)) {
          if (e.key === 'Escape' && findOn.value && !editing) findOn.value = false;
          return;
        }
        if (editing) return;
        if (ctrl && (e.key === 'a' || e.key === 'A')) {
          e.preventDefault();
          selectAll();
          return;
        }
        if (ctrl && (e.key === 'c' || e.key === 'C')) {
          if (!selectedGrid().length) return;
          e.preventDefault();
          copySelected();
          return;
        }
        if (ctrl && (e.key === 'x' || e.key === 'X')) {
          if (!selectedGrid().length) return;
          e.preventDefault();
          cutSelected();
          return;
        }
        if (e.key === 'Enter') {
          const cell = activeCell();
          if (cell && cell.getField() === 'copy') {
            e.preventDefault();
            startEdit(null);
            return;
          }
        }
        if (e.key === 'F2') { e.preventDefault(); startEdit(null); return; }
        if (e.key === 'Delete') {
          e.preventDefault();
          e.stopPropagation();
          if (selectedN.value > 0) askDeleteSel();
          else clearSelected();
          return;
        }
        if (e.key === 'Backspace') {
          e.preventDefault();
          e.stopPropagation();
          clearSelected();
          return;
        }
        if (e.key === 'Escape' && findOn.value) { findOn.value = false; return; }
        if (e.isComposing || e.keyCode === 229) { startEdit(null); return; }
        if (ctrl || e.altKey || e.metaKey) return;
        if (e.key.length === 1 && e.key !== ' ') {
          e.preventDefault();
          startEdit(e.key);
        }
      }

      function applyBrushTo(row) {
        if (!brush || !row || !table) return;
        const rows = table.getRows();
        const i0 = rows.findIndex(r => r.getData()._uid === brush.startUid);
        const i1 = rows.findIndex(r => r.getData()._uid === row.getData()._uid);
        if (i0 < 0 || i1 < 0) return;
        const lo = Math.min(i0, i1), hi = Math.max(i0, i1);
        holdUndo = true;
        rows.forEach((r, i) => {
          const want = (i >= lo && i <= hi) ? brush.value : brush.orig[i];
          if (!!r.getData()._sel !== !!want) r.update({ _sel: !!want });
        });
        holdUndo = false;
        recount();
      }

      function onBrushMove(ev) {
        if (!brush || !table) return;
        const idx = Math.min(table.getRows().length - 1, Math.max(0, rowIndexAtClientY(ev.clientY)));
        const row = table.getRows()[idx];
        if (row) applyBrushTo(row);
      }

      function endBrush() {
        document.removeEventListener('mousemove', onBrushMove);
        brush = null;
      }

      function applyColHints() {
        if (!table) return;
        table.getColumns().forEach(c => {
          const key = COL_HINT[c.getField()];
          if (!key) return;
          const el = c.getElement();
          if (el) el.title = t(key);
        });
      }

      function ensureDragEls() {
        if (!dropLine) {
          dropLine = document.createElement('div');
          dropLine.className = 'tg-drop-line';
          dropLine.style.display = 'none';
          document.body.appendChild(dropLine);
        }
        if (!dragTip) {
          dragTip = document.createElement('div');
          dragTip.className = 'tg-drag-tip';
          dragTip.style.display = 'none';
          document.body.appendChild(dragTip);
        }
      }

      function finishRowDragVisual() {
        document.removeEventListener('mousemove', onRowDragMove, true);
        document.removeEventListener('keydown', onRowDragKey, true);
        if (dropLine) dropLine.style.display = 'none';
        if (dragTip) dragTip.style.display = 'none';
        rowDrag = null;
        if (!table) return;
        table.getRows().forEach(r => {
          const el = r.getElement();
          if (el) el.classList.remove('tg-row-moving');
        });
      }

      function cancelRowDrag() {
        const mod = table && table.modules && table.modules.moveRow;
        if (mod && mod.moving) {
          mod.toRow = false;
          mod.toRowAfter = false;
          try { mod.endMove({ which: 1 }); } catch (e) { /* 已经松手 */ }
        }
        finishRowDragVisual();
        if (table) try { table.redraw(true); } catch (e) { /* 表格正在销毁 */ }
      }

      function onRowDragKey(e) {
        if (e.key !== 'Escape') return;
        e.preventDefault();
        e.stopPropagation();
        cancelRowDrag();
      }

      function onRowDragMove(ev) {
        if (!rowDrag) return;
        if (dragTip) {
          dragTip.style.display = 'block';
          dragTip.style.left = (ev.clientX + 12) + 'px';
          dragTip.style.top = (ev.clientY + 12) + 'px';
        }
        updateDropLine(ev.clientX, ev.clientY);
      }

      function updateDropLine(x, y) {
        if (!table || !dropLine) return;
        const root = table.element;
        const holder = root && root.querySelector('.tabulator-tableholder');
        const box = (holder || root).getBoundingClientRect();
        const rows = table.getRows();
        let hit = null;
        let hitRect = null;
        let lastRect = null;
        let firstRect = null;
        for (let i = 0; i < rows.length; i++) {
          const el = rows[i].getElement();
          if (!el || !el.parentNode) continue;
          if (el.classList.contains('tabulator-moving')) continue;
          if (el.classList.contains('tabulator-row-placeholder')) continue;
          const rr = el.getBoundingClientRect();
          if (!firstRect) firstRect = rr;
          lastRect = rr;
          if (y >= rr.top && y <= rr.bottom) {
            hit = rows[i];
            hitRect = rr;
            break;
          }
        }
        if (!hit && firstRect && y < firstRect.top) {
          hit = rows.find(r => {
            const el = r.getElement();
            return el && el.parentNode && !el.classList.contains('tabulator-moving');
          });
          hitRect = firstRect;
        }
        if (!hit && lastRect) {
          for (let i = rows.length - 1; i >= 0; i--) {
            const el = rows[i].getElement();
            if (!el || !el.parentNode) continue;
            if (el.classList.contains('tabulator-moving')) continue;
            hit = rows[i];
            hitRect = lastRect;
            break;
          }
        }
        if (!hit || !hitRect) {
          dropLine.style.display = 'none';
          return;
        }
        const above = y < (hitRect.top + hitRect.height / 2);
        rowDrag.targetUid = hit.getData()._uid;
        rowDrag.above = above;
        const lineY = (above ? hitRect.top : hitRect.bottom) - 1;
        dropLine.style.display = 'block';
        dropLine.style.left = box.left + 'px';
        dropLine.style.width = box.width + 'px';
        dropLine.style.top = lineY + 'px';
      }

      function rawMove(fromComp, toComp, after) {
        const rm = table && table.rowManager;
        if (!rm) return;
        const from = rm.findRow(fromComp);
        const to = rm.findRow(toComp);
        if (!from || !to || from === to) return;
        // 走 rowManager.moveRowActual：第三参就是 after。
        // 公开的 row.move / table.moveRow 会再 ! 一次，落点和线会对不上。
        rm.moveRowActual(from, to, after);
      }

      function applyDrop(group, drop) {
        if (!table || !group || !group.length) return;
        const rows = table.getRows();
        const byUid = {};
        rows.forEach(r => { byUid[r.getData()._uid] = r; });
        const groupSet = new Set(group);
        const groupRows = group.map(u => byUid[u]).filter(Boolean);
        if (!groupRows.length) return;
        let target = drop && drop.uid ? byUid[drop.uid] : null;
        let after = drop ? !drop.above : true;
        if (target && groupSet.has(target.getData()._uid)) {
          const idx = rows.findIndex(r => r.getData()._uid === target.getData()._uid);
          if (drop && drop.above) {
            let i = idx;
            while (i > 0 && groupSet.has(rows[i - 1].getData()._uid)) i--;
            if (i > 0) { target = rows[i - 1]; after = true; }
            else {
              target = rows.find(r => !groupSet.has(r.getData()._uid)) || null;
              after = false;
            }
          } else {
            let i = idx;
            while (i < rows.length - 1 && groupSet.has(rows[i + 1].getData()._uid)) i++;
            if (i < rows.length - 1) { target = rows[i + 1]; after = false; }
            else {
              const rest = rows.filter(r => !groupSet.has(r.getData()._uid));
              target = rest.length ? rest[rest.length - 1] : null;
              after = true;
            }
          }
        }
        if (!target || groupSet.has(target.getData()._uid)) return;
        let cursor = target;
        let cursorAfter = after;
        for (const r of groupRows) {
          if (r === cursor) { cursorAfter = true; continue; }
          try { rawMove(r, cursor, cursorAfter); } catch (e) { /* 行已经不在表里 */ }
          cursor = r;
          cursorAfter = true;
        }
        try {
          table.rowManager.regenerateRowPositions();
          table.rowManager.refreshActiveData('display', false, true);
        } catch (e) { /* 刷新失败时行顺序已经改完 */ }
      }

      function onRowMoving(row) {
        if (holdMove) return;
        pushUndo({ persistOrder: true });
        const d = row.getData();
        const rows = table.getRows();
        movingGroup = d._sel
          ? rows.filter(r => r.getData()._sel).map(r => r.getData()._uid)
          : [d._uid];
        rows.forEach(r => {
          const el = r.getElement();
          if (el) el.classList.toggle('tg-row-moving', movingGroup.indexOf(r.getData()._uid) >= 0);
        });
        const idPart = d.id || '';
        const namePart = d.name || '';
        const label = movingGroup.length > 1
          ? (movingGroup.length + ' 行')
          : ([idPart, namePart].filter(Boolean).join(' ') || '#');
        rowDrag = { uids: movingGroup.slice(), label, targetUid: d._uid, above: false };
        ensureDragEls();
        dragTip.textContent = label;
        dragTip.style.display = 'block';
        document.addEventListener('mousemove', onRowDragMove, true);
        document.addEventListener('keydown', onRowDragKey, true);
      }

      function onRowMoveCancelled() {
        if (holdMove) return;
        const last = undoStack[undoStack.length - 1];
        if (last && last.persistOrder) undoStack.pop();
        movingGroup = null;
        finishRowDragVisual();
      }

      async function onRowMoved(row) {
        if (holdMove) return;
        const group = (rowDrag && rowDrag.uids) || movingGroup || [row.getData()._uid];
        const drop = rowDrag
          ? { uid: rowDrag.targetUid, above: rowDrag.above }
          : null;
        movingGroup = null;
        finishRowDragVisual();
        holdMove = true;
        holdUndo = true;
        table.blockRedraw();
        try { applyDrop(group, drop); }
        finally {
          table.restoreRedraw();
          holdUndo = false;
          holdMove = false;
        }
        await persistOrder();
      }

      function buildTable() {
        if (!tableEl.value) return;
        table = new Tabulator(tableEl.value, {
          index: '_uid',
          data: store.tasks.map(t => taskToRow(t)),
          columns: columns(),
          layout: 'fitColumns',
          height: '100%',
          autoResize: true,
          placeholder: t('tasks.empty'),
          renderVertical: 'virtual',
          renderVerticalBuffer: 400,
          rowHeight: density.value === 'compact' ? 26 : 34,
          clipboard: true,
          clipboardCopyStyled: false,
          clipboardCopyConfig: { columnHeaders: false },
          clipboardCopyRowRange: 'range',
          clipboardPasteAction: () => { applyPaste(pendingPaste); return true; },
          clipboardPasteParser: text => {
            const raw = TasksSheet.parseTSVGrid(text);
            const grid = TasksSheet.stripHeaderRow(raw);
            if (!grid.length) {
              pendingPasteLinks = null;
              return false;
            }
            pendingPaste = grid;
            pendingPasteLinks = alignPasteLinks(raw, grid, pendingPasteLinks);
            return [{}];
          },
          clipboardCopyFormatter: (type, output) => {
            if (type !== 'plain') return output;
            if (table.modules && table.modules.edit && table.modules.edit.currentCell) return output;
            const grid = selectedGrid();
            if (!grid.length) return output;
            return TasksSheet.serializeTSV(grid);
          },
          selectableRange: true,
          selectableRangeColumns: true,
          selectableRangeRows: true,
          selectableRangeClearCells: true,
          editTriggerEvent: 'dblclick',
          editorEmptyValue: undefined,
          movableRows: true,
          rowContextMenu: contextMenu,
          columnDefaults: { headerSort: false, resizable: true },
          rowFormatter: paintRow,
        });
        table.on('tableBuilt', () => {
          syncHint(); recount(); bindHeadSel(); applyColHints(); syncHScroll();
          nextTick(() => { ensureFillEls(); placeFillHandle(); applyColHints(); bindTableResize(); });
          const holder = tableEl.value.querySelector('.tabulator-tableholder');
          if (holder) {
            // capture 阶段先读 text/html，再让 Tabulator 走它的 text/plain。
            const onPasteCapture = ev => {
              if (ev.target && typingTarget(ev.target) && ev.target.closest && ev.target.closest('.tabulator-editing')) {
                pendingPasteLinks = null;
                return;
              }
              const html = ev.clipboardData && ev.clipboardData.getData('text/html');
              pendingPasteLinks = html ? TasksSheet.parseHtmlGridLinks(html) : null;
            };
            holder.addEventListener('paste', onPasteCapture, true);
            tableEl.value.addEventListener('paste', onPasteCapture, true);
            holder.addEventListener('scroll', placeFillHandle);
            holder.addEventListener('mousedown', ev => {
              if (ev.target && ev.target.closest && ev.target.closest('.tabulator-editing')) return;
              if (ev.target && ev.target.closest && ev.target.closest('.tg-move-edge')) {
                onMoveDown(ev);
                return;
              }
              if (ev.button === 0 && ev.target && ev.target.closest
                  && ev.target.closest('.tabulator-cell')) rangeDrag = true;
            }, true);
            holder.addEventListener('mousedown', ev => {
              if (ev.target && ev.target.closest && ev.target.closest('.tabulator-editing')) return;
              if (ev.target && ev.target.closest && ev.target.closest('.tg-handle')) {
                ev.preventDefault();
                return;
              }
              if (!fillEl || fillEl.style.display === 'none' || fillDrag) return;
              // 必须点在柄上，坐标只作辅助。只比坐标会把编辑框右下角当成填充柄。
              if (ev.target !== fillEl && !fillEl.contains(ev.target)) return;
              const r = fillEl.getBoundingClientRect();
              if (ev.clientX >= r.left - 2 && ev.clientX <= r.right + 2
                && ev.clientY >= r.top - 2 && ev.clientY <= r.bottom + 2) {
                onFillDown(ev);
              }
            }, true);
            holder.addEventListener('mousedown', ev => {
              if (ev.target && ev.target.closest && ev.target.closest('.tabulator-editing')) return;
              if (ev.target && ev.target.closest && ev.target.closest('.tg-handle')) return;
              const cellEl = ev.target && ev.target.closest && ev.target.closest('.tabulator-cell[tabulator-field="_sel"]');
              if (!cellEl || ev.button !== 0) return;
              const rowEl = cellEl.closest('.tabulator-row');
              const row = table.getRows().find(r => r.getElement() === rowEl);
              if (!row) return;
              startSelMouse(ev, row);
            }, true);
            holder.addEventListener('click', ev => {
              const copyBtn = ev.target && ev.target.closest && ev.target.closest('.tg-copy-open');
              if (copyBtn && table) {
                ev.preventDefault();
                ev.stopPropagation();
                const rowEl = copyBtn.closest('.tabulator-row');
                const row = table.getRows().find(r => r.getElement() === rowEl);
                if (row) openCopyEd(row);
                return;
              }
              const browse = ev.target && ev.target.closest && ev.target.closest('.tg-img-browse');
              if (browse && table) {
                ev.preventDefault();
                ev.stopPropagation();
                browseImageFolder(findRowByEl(browse));
                return;
              }
              const inc = ev.target && ev.target.closest && ev.target.closest('.tg-img-inc');
              if (inc && table) {
                ev.preventDefault();
                ev.stopPropagation();
                includeSubfolders(findRowByEl(inc));
              }
            });
            holder.addEventListener('dragover', ev => {
              const cell = ev.target && ev.target.closest && ev.target.closest('.tabulator-cell[tabulator-field="image_source"]');
              if (!cell) return;
              ev.preventDefault();
              try { ev.dataTransfer.dropEffect = 'copy'; } catch (e) { /* ignore */ }
              cell.classList.add('tg-img-drop');
            });
            holder.addEventListener('dragleave', ev => {
              const cell = ev.target && ev.target.closest && ev.target.closest('.tabulator-cell[tabulator-field="image_source"]');
              if (cell) cell.classList.remove('tg-img-drop');
            });
            holder.addEventListener('drop', ev => {
              const cell = ev.target && ev.target.closest && ev.target.closest('.tabulator-cell[tabulator-field="image_source"]');
              if (!cell || !table) return;
              ev.preventDefault();
              cell.classList.remove('tg-img-drop');
              const L = window.ImageSourceLogic;
              if (!L) return;
              const parsed = L.parseDropEvent(ev);
              const hit = L.folderFromDrop(parsed);
              if (!hit || !hit.folder) return;
              const rowEl = cell.closest('.tabulator-row');
              const row = table.getRows().find(r => r.getElement() === rowEl);
              if (row) fillImageFolder(row, hit.folder, hit.fromFiles);
            });
          }
        });
        table.on('cellDblClick', (_e, cell) => {
          if (cell && cell.getField() === 'copy') openCopyEd(cell.getRow());
        });
        table.on('columnResized', col => {
          syncHint();
          placeFillHandle();
          syncHScroll();
          clearTimeout(colSaveTimer);
          colSaveTimer = setTimeout(() => saveColWidths(col), 400);
        });
        table.on('renderComplete', () => { syncHint(); placeFillHandle(); bindHeadSel(); applyColHints(); syncHScroll(); sweepImageProbes(); });
        table.on('rowAdded', recount);
        table.on('rowDeleted', recount);
        table.on('rowMoving', onRowMoving);
        table.on('rowMoved', onRowMoved);
        table.on('rowMoveCancelled', onRowMoveCancelled);
        let pushedForEdit = false;
        table.on('cellEditing', cell => {
          setGridEditing(true);
          if (fillEl) fillEl.style.display = 'none';
          if (moveHit) moveHit.style.display = 'none';
          const sr = table.modules && table.modules.selectRange;
          if (sr) sr.mousedown = false;
          silenceEditorPointer(cell);
          nextTick(() => silenceEditorPointer(cell));
          if (holdUndo) { pushedForEdit = false; return; }
          pushUndo();
          pushedForEdit = true;
        });
        table.on('cellEditCancelled', () => {
          setGridEditing(false);
          if (pushedForEdit && undoStack.length) undoStack.pop();
          pushedForEdit = false;
          placeFillHandle();
        });
        table.on('cellEdited', cell => {
          setGridEditing(false);
          pushedForEdit = false;
          markDirty();
          const row = cell.getRow();
          const upd = { _error: '' };
          if (cell.getField() === 'account') {
            // 换账号马上换引擎（色条、时长档跟着变），不用等保存回来
            const a = accountBy(cell.getValue());
            upd.engine = a ? a.engine : '';
          }
          row.update(upd);
          paintRow(row);
          scanWarn();
          if (cell.getField() === 'image_source') scheduleImageProbe(row);
          placeFillHandle();
        });
        table.on('clipboardPasted', () => { markDirty(); recount(); scanWarn(); sweepImageProbes(); });
        ['rangeChanged', 'rangeAdded', 'rangeRemoved'].forEach(ev => {
          try { table.on(ev, markCurrent); } catch (e) { /* 旧版没有这些事件 */ }
        });
        window.addEventListener('resize', onWindowFit);
      }

      function onWindowFit() {
        syncHint();
        fitTableNow();
        requestFitToolbar();
      }

      function fitTableNow() {
        if (!table) return;
        if (moveDrag) return;
        if (rowDrag || (table.modules && table.modules.moveRow && table.modules.moveRow.moving)) return;
        const el = table.element || tableEl.value;
        if (!el) return;
        const w = el.clientWidth;
        const h = el.clientHeight;
        if (w === lastTableBox.w && h === lastTableBox.h) return;
        lastTableBox = { w, h };
        applyColWidthRatios();
        try { table.redraw(true); } catch (e) { /* 表格正在销毁 */ }
        syncHScroll();
        placeFillHandle();
        syncHint();
      }

      function bindTableResize() {
        if (resizeObs) { try { resizeObs.disconnect(); } catch (e) { /* ignore */ } resizeObs = null; }
        const wrap = tableEl.value && tableEl.value.parentElement;
        const target = wrap || tableEl.value;
        if (!target || typeof ResizeObserver === 'undefined') return;
        lastTableBox = { w: target.clientWidth, h: target.clientHeight };
        let timer = 0;
        resizeObs = new ResizeObserver(() => {
          clearTimeout(timer);
          timer = setTimeout(fitTableNow, 40);
        });
        resizeObs.observe(target);
      }

      async function saveTable(opts) {
        const quiet = !!(opts && opts.quiet);
        if (!table) return false;
        if (saving.value) {
          saveAgain = true;
          try { await saveChain; } catch (e) { /* 等当前这次结束 */ }
          if (!saving.value && dirty.value) return saveTable(opts);
          try { return await saveChain; } catch (e) { return false; }
        }
        saving.value = true;
        saveError.value = '';
        clearTimeout(saveTimer);
        clearTimeout(draftTimer);
        let result = false;
        saveChain = (async () => {
          const rows = table.getRows();
          const data = rows.map(r => r.getData());
          const body = data.map(payloadOf);
          let json;
          try {
            const res = await fetch('/api/tasks/rows', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ rows: body }),
            });
            json = await res.json();
          } catch (e) {
            saveError.value = t('tasks.save.fail');
            dirty.value = true;
            if (!quiet) toast('连不上 Orante 后端', (e && e.message) || '', 'bad');
            return false;
          }
          if (!json || json.ok === false) {
            saveError.value = (json && json.error) || t('tasks.save.fail');
            dirty.value = true;
            if (!quiet) toast(saveError.value, (json && json.hint) || '', 'bad');
            return false;
          }
          const errors = json.errors || [];
          const errAt = new Map(errors.map(e => [e.row, e]));
          let si = 0;
          holdWatch = true;
          rows.forEach((row, i) => {
            const d = row.getData();
            const err = errAt.get(i);
            if (err) { row.update({ _error: err.error }); paintRow(row); return; }
            const empty = !(d.name || d.copy || d.image_source);
            if (empty) { row.update({ _error: '' }); paintRow(row); return; }
            const tid = (json.saved || [])[si++];
            const srv = (json.tasks || []).find(t => t.id === tid);
            if (srv) {
              const keep = { _sel: !!d._sel, _uid: d._uid, _restored: false };
              const next = Object.assign(taskToRow(srv, d._uid), keep);
              row.update(next);
            } else if (tid) row.update({ id: tid, _error: '', status: d.status || 'draft', _restored: false });
            else row.update({ _error: '' });
            paintRow(row);
          });
          if (json.tasks) store.tasks = json.tasks;
          await nextTick();
          holdWatch = false;
          const conflicts = errors.filter(e => e && (e.conflict || e.code === 'conflict'));
          if (conflicts.length) {
            dirty.value = true;
            saveError.value = t('tasks.conflict');
            conflictDlg.value = { items: conflicts, i: 0 };
            recount();
            return false;
          }
          if (errors.length) {
            dirty.value = true;
            saveError.value = errors[0].error || t('tasks.save.fail');
            const first = errors[0];
            // 去抖自动保存只亮红字，避免每 1.5 秒弹一次；手点保存 / Ctrl+S 才 toast
            if (!quiet) toast(first.error, first.hint || t('tasks.err.row', { n: first.row + 1 }), 'bad');
            recount();
            return false;
          }
          dirty.value = leftoverUnsaved();
          savedAt.value = Date.now();
          saveError.value = '';
          if (hasDraftRows()) scheduleDraftSnap();
          else clearDraftSnap();
          if (!quiet) toast(t('toast.saved'), '', 'ok');
          recount();
          return true;
        })();
        try { result = await saveChain; }
        catch (e) {
          result = false;
          dirty.value = true;
          saveError.value = t('tasks.save.fail');
        } finally {
          saving.value = false;
          // 保存没把草稿清干净时把快照补上（开头为防赛跑清过 timer）
          if (dirty.value) scheduleDraftSnap();
          if (saveAgain) {
            saveAgain = false;
            if (dirty.value) scheduleAutosave();
          } else if (dirty.value && saveError.value === t('tasks.save.fail')) {
            // 网络失败不清 dirty，1.5 秒后再试
            scheduleAutosave();
          }
        }
        return result;
      }

      async function addRows() {
        if (!table) return;
        const raw = String(addN.value || '').trim();
        let n = raw === '' ? 1 : parseInt(raw, 10);
        if (!Number.isFinite(n) || n < 1 || n > 2000) {
          toast(t('tasks.add_range'), '', 'bad');
          return;
        }
        if (n >= 100 && !confirm(t('tasks.add_confirm', { n: String(n) }))) return;
        pushUndo();
        holdUndo = true;
        const rows = makeBlankRows(n);
        await table.addData(rows, false);
        holdUndo = false;
        markDirty();
        recount();
        if (raw !== '') {
          API.post('/api/settings', { set: { 'ui.tasks.add_n': raw } })
            .then(r => { Object.assign(store.settings, r.settings || {}); })
            .catch(() => {});
        }
        if (n === 1) {
          const last = table.getRows();
          const row = last[last.length - 1];
          try { if (row) row.getCell('name').edit(); } catch (e) { /* 表格还没画完就不必强行进入编辑 */ }
        }
      }

      function toggleDensity() {
        density.value = density.value === 'compact' ? 'comfy' : 'compact';
        API.post('/api/settings', { set: { 'ui.tasks.density': density.value } })
          .then(r => { Object.assign(store.settings, r.settings || {}); })
          .catch(() => {});
        if (!table) return;
        table.options.rowHeight = density.value === 'compact' ? 26 : 34;
        table.redraw(true);
        nextTick(() => { placeFillHandle(); syncHScroll(); });
      }

      function toggleOnlySel() {
        onlySel.value = !onlySel.value;
        if (!table) return;
        if (onlySel.value) table.setFilter('_sel', '=', true);
        else table.clearFilter();
        recount();
      }

      function openPaste() { openImport('paste'); }

      function openImport(src) {
        moreOpen.value = false;
        impSrc.value = src === 'file' ? 'file' : 'paste';
        impText.value = '';
        pasteText.value = '';
        impFileName.value = '';
        impGrid.value = [];
        impMap.value = [];
        impSkip.value = false;
        impMapEdit.value = false;
        pendingImportLinks = null;
        importPasteTick = false;
        dlg.value = 'import';
        nextTick(() => {
          const el = panelEl.value && panelEl.value.querySelector('.dlg-import .paste-box');
          if (el && impSrc.value === 'paste') el.focus();
        });
      }

      function rebuildImpPreview() {
        const grid = parseImportGrid(impText.value);
        impGrid.value = grid;
        if (pendingImportLinks && !TasksSheet.linksAlign(grid, pendingImportLinks)) {
          pendingImportLinks = null;
        }
        if (!grid.length) {
          impMap.value = [];
          impSkip.value = false;
          return;
        }
        const width = grid.reduce((m, r) => Math.max(m, (r || []).length), 0);
        const head = grid[0] || [];
        const mapped = [];
        for (let i = 0; i < width; i++) mapped.push(fieldOfHeader(head[i]));
        const hasHead = mapped.some(Boolean);
        impSkip.value = hasHead;
        if (hasHead) {
          while (mapped.length < width) mapped.push('');
          impMap.value = mapped;
        } else {
          const cols = COLUMNS.slice(0, width);
          while (cols.length < width) cols.push('');
          impMap.value = cols;
        }
        // 列对应是这条路多出来的价值，预览出来就把下拉打开
        impMapEdit.value = true;
      }

      function onImpPaste(ev) {
        const html = ev.clipboardData && ev.clipboardData.getData('text/html');
        pendingImportLinks = html ? TasksSheet.parseHtmlGridLinks(html) : null;
        importPasteTick = true;
      }

      function onImpText() {
        if (!importPasteTick) pendingImportLinks = null;
        importPasteTick = false;
        pasteText.value = impText.value;
        rebuildImpPreview();
      }

      function impSample(i) {
        const grid = impGrid.value || [];
        const start = impSkip.value ? 1 : 0;
        const row = grid[start] || [];
        const field = (impMap.value || [])[i];
        const text = row[i] == null ? '' : String(row[i]);
        const linkCell = pendingImportLinks && pendingImportLinks[start] && pendingImportLinks[start][i];
        const picked = pickLinkedValue(field, text, linkCell);
        const s = picked.val || '';
        return s.length > 48 ? s.slice(0, 48) + '…' : s;
      }

      function setImpMap(i, v) {
        const next = (impMap.value || []).slice();
        // 同一字段只许一列：改到已占用的字段时，原来那列改成忽略
        if (v) {
          for (let j = 0; j < next.length; j++) {
            if (j !== i && next[j] === v) next[j] = '';
          }
        }
        next[i] = v;
        impMap.value = next;
      }

      function confirmPaste() { confirmImport(); }

      function confirmImport() {
        const grid = impGrid.value || [];
        if (!grid.length) { toast(t('tasks.import_dlg.empty'), '', 'bad'); return; }
        const start = impSkip.value ? 1 : 0;
        const map = impMap.value || [];
        const rows = [];
        let restored = 0;
        for (let i = start; i < grid.length; i++) {
          const r = grid[i] || [];
          const d = {};
          for (let c = 0; c < map.length; c++) {
            const f = map[c];
            if (!f) continue;
            const linkCell = pendingImportLinks && pendingImportLinks[i] && pendingImportLinks[i][c];
            const picked = pickLinkedValue(f, r[c] == null ? '' : r[c], linkCell);
            d[f] = picked.val;
            if (picked.linked) {
              d._link_from = d._link_from || {};
              d._link_from[f] = picked.from;
              restored += 1;
            }
          }
          rows.push(d);
        }
        if (!rows.length) { toast(t('tasks.import_dlg.empty'), '', 'bad'); return; }
        appendRows(rows);
        dlg.value = null;
        pendingImportLinks = null;
        let msg = t('tasks.import.added', { n: rows.length });
        if (restored > 0) msg += '，' + t('tasks.paste.links', { n: String(restored) });
        toast(msg, '', 'ok');
      }

      function triggerImport() {
        if (dlg.value !== 'import') openImport('file');
        else impSrc.value = 'file';
        nextTick(() => { if (fileEl.value) fileEl.value.click(); });
      }

      async function onFile(ev) {
        const f = ev.target.files && ev.target.files[0];
        ev.target.value = '';
        if (!f) return;
        const name = (f.name || '').toLowerCase();
        if (name.endsWith('.xlsx') || name.endsWith('.xls')) {
          toast(t('tasks.import.xlsx'), t('tasks.import.xlsx_hint'), 'bad');
          return;
        }
        try {
          const text = await f.text();
          impFileName.value = f.name || '';
          impSrc.value = 'file';
          impText.value = text;
          pasteText.value = text;
          pendingImportLinks = null;
          importPasteTick = false;
          if (dlg.value !== 'import') dlg.value = 'import';
          rebuildImpPreview();
        } catch (e) { toast(t('tasks.import_dlg.empty'), (e && e.message) || '', 'bad'); }
      }

      function toggleMore() {
        moreOpen.value = !moreOpen.value;
        if (moreOpen.value) nextTick(placeMoreMenu);
      }

      function placeMoreMenu() {
        const wrap = moreEl.value;
        if (!wrap) return;
        const btn = wrap.querySelector('.tg-more-btn');
        const menu = wrap.querySelector('.tg-more-menu');
        if (!btn || !menu) return;
        const r = btn.getBoundingClientRect();
        menu.style.top = (r.bottom + 4) + 'px';
        menu.style.right = (window.innerWidth - r.right) + 'px';
        menu.style.left = 'auto';
      }

      function onMoreAway(ev) {
        if (!moreOpen.value) return;
        const wrap = moreEl.value;
        if (wrap && wrap.contains(ev.target)) return;
        moreOpen.value = false;
      }

      function closeMore() { moreOpen.value = false; }

      const durPrefDlg = reactive({
        on: false, note: '', needFlow: false, needGrok: false, needStudio: false,
        flowDur: 8, grokDur: 10, studioDur: 8, ids: [],
      });

      function selectedRows() {
        if (!table) return [];
        return allRowComps().map(r => r.getData()).filter(d => d && d._sel);
      }

      function ctxTargetData(row) {
        const picked = selectedRows();
        if (picked.length) return picked;
        const me = row && row.getData ? row.getData() : null;
        const box = getRangeBox();
        if (box && table) {
          const all = table.getRows();
          const out = [];
          for (let r = box.top; r <= box.bottom; r++) {
            const rw = all[r];
            if (rw) out.push(rw.getData());
          }
          if (me && out.some(x => x && x.id && x.id === me.id)) return out;
        }
        return me ? [me] : [];
      }

      const canSetDur = computed(() => {
        const rows = selectedRows();
        const pool = rows.length ? rows : allRowComps().map(r => r.getData());
        return pool.some(d => !!d);
      });

      function openDurPrefDlg() {
        closeMore();
        if (!canSetDur.value) return;
        const rows = selectedRows();
        const pool = rows.length ? rows : allRowComps().map(r => r.getData());
        const all = pool.filter(d => !!d);
        const withCopy = pool.filter(d => d && hasCopy(d));
        const engines = new Set(all.map(d => engineOf(d) || 'flow'));
        durPrefDlg.on = true;
        durPrefDlg.ids = all.map(d => d.id).filter(Boolean);
        durPrefDlg.needFlow = engines.has('flow');
        durPrefDlg.needStudio = engines.has('studio');
        durPrefDlg.needGrok = engines.has('grok');
        durPrefDlg.flowDur = 8;
        durPrefDlg.grokDur = 10;
        durPrefDlg.studioDur = 8;
        durPrefDlg.note = t('tasks.dur_set_note', { n: String(withCopy.length) });
      }

      async function applyDurPref() {
        const by = {};
        if (durPrefDlg.needFlow) by.flow = Number(durPrefDlg.flowDur);
        if (durPrefDlg.needStudio) by.studio = Number(durPrefDlg.studioDur);
        if (durPrefDlg.needGrok) by.grok = Number(durPrefDlg.grokDur);
        try {
          const body = { ids: durPrefDlg.ids, by_engine: by };
          // 没勾选时 ids 为空数组表示「当前表全部」——改成 null 让后端当全部
          const sel = selectedRows();
          if (!sel.length) body.ids = null;
          const r = await API.post('/api/tasks/duration-pref', body);
          toast(t('tasks.dur_set_ok', { n: String((r.updated || []).length) }), '', 'ok');
          durPrefDlg.on = false;
          // 刷新表
          if (typeof reload === 'function') await reload();
          else if (typeof loadTasks === 'function') await loadTasks();
        } catch (e) { /* toast */ }
      }



      function exportSelTsv() {
        closeMore();
        if (!table) return;
        const rows = allRowComps().filter(r => r.getData()._sel);
        if (!rows.length) { toast(t('tasks.export_none'), '', 'bad'); return; }
        const head = COLUMNS.map(f => {
          const hit = IMP_FIELDS.find(x => x.v === f);
          return hit ? t(hit.k) : f;
        });
        const grid = [head];
        rows.forEach(row => {
          const d = row.getData();
          grid.push(COLUMNS.map(f => {
            let v = d[f];
            if (f === 'type' && (v === 'long' || v === 'short')) {
              const lab = t('tasks.type.' + v);
              v = lab === ('tasks.type.' + v) ? v : lab;
            }
            return v == null ? '' : String(v);
          }));
        });
        const tsv = TasksSheet.serializeTSV(grid);
        const blob = new Blob([tsv], { type: 'text/tab-separated-values;charset=utf-8' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'tasks.tsv';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        toast(t('tasks.export_ok', { n: String(rows.length) }), '', 'ok');
      }

      function menuResetCols() { closeMore(); resetColWidths(); }
      function menuOnlySel() { closeMore(); toggleOnlySel(); }
      function menuDensity() { closeMore(); toggleDensity(); }
      function menuImport() { closeMore(); openImport('paste'); }

      function barNeedFold() {
        const bar = barEl.value;
        if (!bar) return false;
        let used = 0;
        Array.from(bar.children).forEach(ch => {
          if (ch.classList.contains('file-hidden')) return;
          const cs = getComputedStyle(ch);
          if (cs.display === 'none' || cs.visibility === 'hidden') return;
          used += ch.offsetWidth + (parseFloat(cs.marginLeft) || 0) + (parseFloat(cs.marginRight) || 0);
        });
        return used > bar.clientWidth + 1;
      }

      function fitToolbar() {
        if (!barEl.value || fittingBar) return;
        fittingBar = true;
        const w = barEl.value.clientWidth;
        // 约 720px 起按 舒适 → +行 → 导入 折；实际溢出时继续折
        foldDen.value = w > 0 && w < 720;
        foldAdd.value = w > 0 && w < 700;
        foldImport.value = w > 0 && w < 680;
        const steps = [
          () => { foldDen.value = true; },
          () => { foldAdd.value = true; },
          () => { foldImport.value = true; },
        ];
        let i = 0;
        const run = () => {
          nextTick(() => {
            if (barNeedFold() && i < steps.length) {
              steps[i++]();
              run();
            } else {
              fittingBar = false;
            }
          });
        };
        run();
      }

      function requestFitToolbar() {
        fitToolbar();
      }

      function bindBarResize() {
        if (barObs) { try { barObs.disconnect(); } catch (e) { /* ignore */ } barObs = null; }
        const el = barEl.value;
        if (!el || typeof ResizeObserver === 'undefined') return;
        let timer = 0;
        barObs = new ResizeObserver(() => {
          if (fittingBar) return;
          clearTimeout(timer);
          timer = setTimeout(fitToolbar, 40);
        });
        barObs.observe(el);
        nextTick(fitToolbar);
      }

      function askWhere() {
        const root = store.settings['workspace.root'] || '';
        const ask = !!store.settings['workspace.ask_each_batch'];
        if (!ask && root) return Promise.resolve(root);
        whereRoot.value = root;
        whereRemember.value = false;
        dlg.value = 'where';
        return new Promise((resolve, reject) => { whereWait = { resolve, reject }; });
      }

      async function pickFolder() {
        try {
          const r = await API.post('/api/workspace/pick');
          if (r.folder) whereRoot.value = r.folder;
        } catch (e) { /* pick 失败就手填，输入框本来就能改 */ }
      }

      async function confirmWhere() {
        const root = String(whereRoot.value || '').trim();
        if (!root) { toast(t('tasks.where.need'), '', 'bad'); return; }
        const set = { 'workspace.root': root };
        if (whereRemember.value) set['workspace.ask_each_batch'] = false;
        try {
          const r = await API.post('/api/settings', { set });
          Object.assign(store.settings, r.settings);
        } catch (e) { return; }
        dlg.value = null;
        if (whereWait) whereWait.resolve(root);
        whereWait = null;
      }

      const finishSelOk = computed(() => {
        if (!selectedN.value) return false;
        return allRowComps().some(r => {
          const d = r.getData();
          return d && d._sel && d.id && (d.status === 'collected' || d.status === 'generating');
        });
      });

      function fireBatchCele(d) {
        if (!d || !d.done || d.done.length < 1) return;
        if (!window.ReviewCelebrate) return;
        const ids = d.done.map(x => x.tid);
        window.ReviewCelebrate.launchCelebrate({
          title: t('review.confetti_title_n', { n: String(d.done.length) }),
          sub: window.ReviewCelebrate.formatBatchSub(ids),
        });
      }

      function closeFinishDlg() {
        const d = finishDlg.value;
        dlg.value = null;
        finishDlg.value = null;
        if (d && d.phase === 'done' && d.done && d.done.length >= 1) fireBatchCele(d);
      }

      function onFinishMask() {
        if (finishDlg.value && finishDlg.value.phase === 'run') return;
        closeFinishDlg();
      }

      
    function formatJobAge(sec) {
      sec = Number(sec) || 0;
      if (sec < 60) return sec + ' 秒前';
      if (sec < 3600) return Math.floor(sec / 60) + ' 分钟前';
      if (sec < 86400) return Math.floor(sec / 3600) + ' 小时前';
      return Math.floor(sec / 86400) + ' 天前';
    }
    async function cleanupFinishJob(j, row) {
      if (!j || !j.id) return;
      try {
        await API.post('/api/jobs/' + encodeURIComponent(j.id) + '/cancel', {}, { silent: true });
        if (row && Array.isArray(row.jobs)) {
          row.jobs = row.jobs.filter(x => x.id !== j.id);
        }
        // 清完该行 jobs 后，若只因 job 被拦，重新预检一次
        if (finishDlg.value && finishDlg.value.phase === 'preview') {
          const ids = (finishDlg.value.ready || []).map(x => x.tid)
            .concat((finishDlg.value.blocked || []).map(x => x.tid));
          if (ids.length) {
            const r = await API.post('/api/tasks/finish-preview', { ids: ids });
            finishDlg.value = {
              ...finishDlg.value,
              ready: r.ready || [],
              blocked: r.blocked || [],
            };
          }
        }
      } catch (e) {
        toast((e && e.message) || String(e), (e && e.hint) || '', 'bad');
      }
    }

    async function openFinishSel() {
        closeMore();
        if (!finishSelOk.value) return;
        const ids = allRowComps().map(r => r.getData()).filter(d => d && d._sel && d.id).map(d => d.id);
        if (!ids.length) return;
        finishDlg.value = {
          phase: 'loading', nSel: ids.length,
          ready: [], blocked: [], i: 0, cur: '', done: [], failed: [],
        };
        dlg.value = 'finish';
        try {
          const r = await API.post('/api/tasks/finish-preview', { ids: ids });
          finishDlg.value = {
            phase: 'preview', nSel: ids.length,
            ready: r.ready || [], blocked: r.blocked || [],
            i: 0, cur: '', done: [], failed: [],
          };
        } catch (e) {
          dlg.value = null;
          finishDlg.value = null;
        }
      }

      function jumpFinishBlocked(row) {
        if (!row || !row.tid) return;
        dlg.value = null;
        finishDlg.value = null;
        if (window.ui) {
          ui.panelTid = row.tid;
          ui.pendingTid = row.tid;
          ui.node = 'review';
        }
      }

      async function confirmFinishSel() {
        const d = finishDlg.value;
        if (!d || d.phase !== 'preview') return;
        const ready = d.ready || [];
        if (!ready.length) return;
        d.phase = 'run';
        d.i = 0;
        d.done = [];
        d.failed = [];
        for (let i = 0; i < ready.length; i++) {
          const item = ready[i];
          d.i = i + 1;
          d.cur = item.tid;
          try {
            await API.post(
              '/api/tasks/' + encodeURIComponent(item.tid) + '/status',
              { status: 'finished' },
              { silent: true }
            );
            d.done.push(item);
            const hit = (store.tasks || []).find(t => t.id === item.tid);
            if (hit) hit.status = 'finished';
            allRowComps().forEach(row => {
              const rd = row.getData();
              if (rd && rd.id === item.tid) {
                try { row.update({ status: 'finished' }); } catch (e) { /* 行可能没了 */ }
              }
            });
          } catch (e) {
            d.failed.push({
              tid: item.tid,
              name: item.name || '',
              error: (e && e.message) || String(e),
            });
          }
        }
        d.phase = 'done';
        try { if (window.refreshTasks) await refreshTasks(); } catch (e) { /* 表格已就地改 */ }
        if (d.done.length >= 1 && !d.failed.length) {
          dlg.value = null;
          finishDlg.value = null;
          fireBatchCele(d);
        }
      }

      function cancelDlg() {
        if (dlg.value === 'copy') { askCloseCopyEd(); return; }
        if (dlg.value === 'finish') { closeFinishDlg(); return; }
        if (dlg.value === 'move-warn') { cancelMoveWarn(); return; }
        if (dlg.value === 'prep') { closePrepDlg(); return; }
        if (dlg.value === 'where' && whereWait) {
          whereWait.reject(Object.assign(new Error('cancel'), { cancelled: true }));
          whereWait = null;
        }
        dlg.value = null;
      }

      function conflictCur() {
        const d = conflictDlg.value;
        if (!d || !d.items || !d.items.length) return null;
        return d.items[d.i] || d.items[0];
      }
      function conflictAdvance() {
        const d = conflictDlg.value;
        if (!d) return;
        if (d.i + 1 < d.items.length) {
          conflictDlg.value = { items: d.items, i: d.i + 1 };
          return;
        }
        conflictDlg.value = null;
        const need = table && table.getRows().some(r => r.getData() && r.getData()._force);
        if (need) saveTable();
      }
      function conflictUseMine() {
        const c = conflictCur();
        if (!c || !table) { conflictDlg.value = null; return; }
        const rows = table.getRows();
        const row = rows[c.row];
        if (row) row.update({ _force: true });
        conflictAdvance();
      }
      function conflictUseTheirs() {
        const c = conflictCur();
        if (!c || !table) { conflictDlg.value = null; return; }
        const rows = table.getRows();
        const row = rows[c.row];
        const th = c.theirs || {};
        if (row) {
          const d = row.getData();
          row.update(Object.assign({}, taskToRow(th, d._uid), { _sel: !!d._sel, _force: false, _error: '' }));
        }
        conflictAdvance();
      }

      function idsToPrepare(onlySelected) {
        if (!table) return [];
        if (onlySelected) {
          return allRowComps().map(r => r.getData()).filter(r => r._sel && r.id).map(r => r.id);
        }
        return table.getData().filter(r => (r.status || 'draft') === 'draft').map(r => r.id).filter(Boolean);
      }

      async function runPrepare(ids) {
        if (!ids.length) { toast(t('tasks.none'), '', 'bad'); return; }
        let root;
        try { root = await askWhere(); }
        catch (e) { return; }
        let batch;
        try { batch = (await API.post('/api/workspace/batch', { root })).batch; }
        catch (e) { return; }
        let n = 0;
        for (const id of ids) {
          try {
            await API.job('prepare', id, { batch_dir: batch.dir, batch_id: batch.id });
            n++;
          } catch (e) { /* 单条失败已 toast，其余继续 */ }
        }
        if (n) toast(t('tasks.queued', { n }), '', 'ok');
      }

      function closePrepDlg() {
        dlg.value = null;
        prepDlg.value = null;
      }

      function jumpPrepBlocked(row) {
        if (!row || !row.tid) return;
        closePrepDlg();
        focusImageCell(row.tid);
      }

      function focusImageCell(tid) {
        if (!table || !tid) return;
        const row = table.getRows().find(r => r.getData() && r.getData().id === tid);
        if (!row) return;
        try {
          row.scrollTo();
          const cell = row.getCell('image_source');
          if (cell && table.addRange) table.addRange(cell, cell);
          if (cell && cell.edit) cell.edit(true);
        } catch (e) { /* 格子可能不可编辑 */ }
      }

      async function openPrepCheck(ids) {
        if (!ids.length) { toast(t('tasks.none'), '', 'bad'); return; }
        const L = window.ImageSourceLogic;
        const byId = {};
        allRowComps().forEach(r => {
          const d = r.getData();
          if (d && d.id) byId[d.id] = r;
        });
        prepDlg.value = { phase: 'loading', nSel: ids.length, ready: [], blocked: [] };
        dlg.value = 'prep';
        const items = [];
        for (const id of ids) {
          const row = byId[id];
          const d = row ? row.getData() : { id: id, image_source: '', name: id };
          let probe = d._img_probe;
          if (!probe && imgProbe && d.image_source) {
            try { probe = await imgProbe.probeNow(d.image_source, rowRecursive(d)); applyProbe(row, probe); }
            catch (e) { probe = { ok: false, note: String(e.message || e) }; }
          }
          if (!d.image_source) probe = { ok: false, note: '没有图片来源', kind: '' };
          const mode = L ? L.localModeOf(d, store.settings) : 'copy';
          const rec = {
            tid: id,
            name: d.name || '',
            uid: d._uid || '',
            ok: !!(probe && probe.ok),
            reason: L ? L.prepareReason(d, probe, mode) : ((probe && probe.note) || ''),
            probe: probe,
          };
          items.push(rec);
        }
        const split = L ? L.splitPrepare(items) : { ready: items.filter(x => x.ok), blocked: items.filter(x => !x.ok) };
        prepDlg.value = {
          phase: 'preview', nSel: ids.length,
          ready: split.ready, blocked: split.blocked,
        };
      }

      async function confirmPrepSel() {
        const d = prepDlg.value;
        if (!d || d.phase !== 'preview') return;
        const ids = (d.ready || []).map(x => x.tid).filter(Boolean);
        closePrepDlg();
        if (!ids.length) return;
        await runPrepare(ids);
      }

      async function prepare(onlySelected) {
        if (busy.value) return;
        busy.value = true;
        try {
          const okSave = await saveTable({ quiet: true });
          if (!okSave) return;
          const ids = idsToPrepare(onlySelected);
          await openPrepCheck(ids);
        } finally { busy.value = false; }
      }

      async function prepareThis(row) {
        if (busy.value) return;
        busy.value = true;
        try {
          const okSave = await saveTable({ quiet: true });
          if (!okSave) return;
          const id = row.getData().id;
          if (!id) { toast(t('tasks.no_id'), t('tasks.need_save'), 'bad'); return; }
          await openPrepCheck([id]);
        } finally { busy.value = false; }
      }

      async function openFolder(d) {
        if (!d.id) { toast(t('tasks.no_id'), t('tasks.need_save'), 'bad'); return; }
        try { await API.post(`/api/tasks/${d.id}/open-folder`); }
        catch (e) { /* 后端还没文件夹会 toast */ }
      }

      async function copyCopy(d) {
        const text = d.copy || '';
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(text);
          else {
            const ta = document.createElement('textarea');
            ta.value = text; document.body.appendChild(ta); ta.select();
            document.execCommand('copy'); ta.remove();
          }
          toast(t('tasks.ctx.copied'), '', 'ok');
        } catch (e) { toast(t('tasks.ctx.copy'), String(e.message || e), 'bad'); }
      }

      function isDraftRow(d) {
        if (!d.id) return true;
        return (d.status === 'draft' || !d.status) && !d.dir;
      }

      function isTaskRunning(tid) {
        if (!tid) return false;
        if (queueOk.value) {
          const q = queueMap.value || {};
          for (const slot of Object.values(q)) {
            if (slot && slot.running && slot.running.task_id === tid) return true;
          }
        }
        return (store.jobs || []).some(x => x.task_id === tid && x.status === 'running');
      }

      function classifySel() {
        const draft = [], trash = [], running = [];
        allRowComps().forEach(row => {
          const d = row.getData();
          if (!d._sel) return;
          if (d.id && isTaskRunning(d.id)) { running.push(row); return; }
          if (isDraftRow(d)) draft.push(row);
          else trash.push(row);
        });
        return { draft, trash, running };
      }

      function askDeleteSel() {
        const { draft, trash, running } = classifySel();
        const total = draft.length + trash.length + running.length;
        if (!total) { toast(t('tasks.del.none'), '', 'bad'); return; }
        delPlan.value = {
          total,
          draft,
          trash,
          running,
          trashIds: trash.map(r => r.getData().id).filter(Boolean).join(' / ') || '—',
          runIds: running.map(r => r.getData().id).filter(Boolean).join(' / ') || '—',
        };
        dlg.value = 'delsel';
      }

      async function confirmDeleteSel() {
        const plan = delPlan.value || {};
        const draft = plan.draft || [];
        const trash = plan.trash || [];
        if (!draft.length && !trash.length) { dlg.value = null; return; }
        const resurrect = {
          hard: draft.map(r => Object.assign({}, r.getData())).filter(d => d.id),
          trash: trash.map(r => { const d = r.getData(); return { id: d.id, status: d.status }; }).filter(x => x.id),
        };
        pushUndo({ persistOrder: true, resurrect });
        dlg.value = null;
        holdWatch = true;
        holdUndo = true;
        try {
          const hardIds = resurrect.hard.map(d => d.id);
          const trashIds = resurrect.trash.map(d => d.id);
          if (hardIds.length || trashIds.length) {
            try { await API.post('/api/tasks/batch-delete', { hard: hardIds, trash: trashIds }); }
            catch (e) { /* API 已 toast */ }
          }
          draft.forEach(row => { try { row.delete(); } catch (e) { /* 已不在 */ } });
          trash.forEach(row => { try { row.delete(); } catch (e) { /* 已不在 */ } });
          const gone = new Set(hardIds.concat(trashIds));
          store.tasks = (store.tasks || []).filter(t => !gone.has(t.id));
        } finally {
          holdUndo = false;
          holdWatch = false;
        }
        recount();
        const dN = draft.length, tN = trash.length;
        let msg = '';
        if (dN && tN) msg = t('tasks.del.toast', { d: String(dN), t: String(tN) });
        else if (dN) msg = t('tasks.del.toast_d', { d: String(dN) });
        else msg = t('tasks.del.toast_t', { t: String(tN) });
        toast(msg, '', 'ok');
      }

      async function removeRow(row, hard) {
        const d = row.getData();
        const msg = hard ? t('tasks.confirm.hard') : t('tasks.confirm.trash');
        if (!confirm(msg + (d.name ? ` 「${d.name}」` : ''))) return;
        const extra = { persistOrder: !!d.id };
        if (d.id && hard) extra.resurrect = { hard: [Object.assign({}, d)], trash: [] };
        else if (d.id && !hard) extra.resurrect = { hard: [], trash: [{ id: d.id, status: d.status }] };
        pushUndo(extra);
        if (!d.id) { row.delete(); markDirty(); recount(); return; }
        try {
          await API.del(`/api/tasks/${d.id}${hard ? '?hard=1' : ''}`);
          row.delete();
          store.tasks = store.tasks.filter(t => t.id !== d.id);
          recount();
        } catch (e) { /* API 已 toast */ }
      }

      watch(() => store.tasks.map(t => `${t.id}:${t.status}:${t.updated || ''}:${t.account_id || ''}:${t.priority || 0}:${t.gen_mode || ''}`).join('|'), () => {
        if (holdWatch || !table) return;
        mergeFromStore(store.tasks);
      }, { flush: 'sync' });

      watch(() => (store.jobs || []).map(j => j.id + ':' + j.status).join(','), () => {
        pullQueue();
      });

      async function flushTable() {
        clearTimeout(saveTimer);
        if (dirty.value) await saveTable({ quiet: true });
      }

      watch(() => store.refreshSeq, () => { pullQueue(); });

      // 初次打开时表格可能比中文文案先建好，表头就会露出 tasks.col.xxx 这种键名。
      // 文案一到就把列定义重新套一遍（宽度照旧从设置里取）。
      watch(() => t('tasks.col.id') + '|' + t('tasks.col.copy'), (v, old) => {
        if (!table || v === old) return;
        try { table.setColumns(columns()); } catch (e) { /* 表还没建好时忽略 */ }
      });

      onMounted(() => nextTick(() => {
        buildTable();
        recount();
        pullQueue();
        checkDraftBanner();
        queueTimer = setInterval(pullQueue, 3000);
        if (window.registerFlush) window.registerFlush('tasks', flushTable);
        if (window.registerBeaconFlush) window.registerBeaconFlush('tasks', beaconFlush);
        document.addEventListener('keydown', onPanelKey, true);
        document.addEventListener('mousedown', onMoreAway);
        document.addEventListener('mouseup', endBrush);
        document.addEventListener('mouseup', onPointerUp);
        bindBarResize();
        window.__tasksDebug = {
          data: () => table ? allRowComps().map(r => Object.assign({}, r.getData())) : [],
          count: () => table ? allRowComps().length : 0,
          sel: () => table ? allRowComps().filter(r => r.getData()._sel).length : 0,
          copyTSV: () => TasksSheet.serializeTSV(selectedGrid()),
          cutSelected,
          copySelected,
          setRange: (top, left, bottom, right) => setRangeBox(top, left, bottom, right),
          rangeBox: () => {
            const b = getRangeBox();
            if (!b) return null;
            return { top: b.top, bottom: b.bottom, left: b.left, right: b.right };
          },
          moveBy: (dRow, dCol, copy) => {
            const box = getRangeBox();
            if (!box) return false;
            const src = { top: box.top, bottom: box.bottom, left: box.left, right: box.right };
            const plan = planMove(src, dRow, dCol);
            if (!plan.legal) return { ok: false, blocked: plan.blocked, oobCol: plan.oobCol, oobTop: plan.oobTop };
            return applyMove(src, plan, !!copy).then(() => ({ ok: true, dest: plan.dest, need: plan.need }));
          },
          cols: () => visibleCols().map(c => c.getField()),
          colAtX: x => gridColAtX(x),
          plan: (dRow, dCol) => {
            const box = getRangeBox();
            if (!box) return null;
            const p = planMove(box, dRow, dCol);
            return {
              legal: p.legal, oobCol: p.oobCol, oobTop: p.oobTop, need: p.need,
              dest: p.dest, nC: visibleCols().length, blocked: p.blocked.length,
            };
          },
          selectAll,
          orderedIds,
          classifySel: () => {
            const p = classifySel();
            return {
              draft: p.draft.map(r => r.getData()),
              trash: p.trash.map(r => r.getData()),
              running: p.running.map(r => r.getData()),
            };
          },
          askDeleteSel,
          undo,
          redo,
          persistOrder,
          dirty: () => dirty.value,
          saving: () => saving.value,
          markDirty,
          saveNow: () => saveTable({ quiet: true }),
          patchRow: (i, patch) => {
            const r = table && table.getRows()[i];
            if (r) r.update(patch || {});
          },
          setJobs: jobs => { store.jobs = jobs || []; },
          openCopy: i => {
            const r = table && table.getRows()[i];
            if (r) openCopyEd(r);
          },
          copyDlg: () => copyEd.value ? {
            who: copyEd.value.who,
            readonly: !!copyEd.value.readonly,
            orig: copyEd.value.orig,
            text: copyEdText.value,
            segs: copyEdSegs.value,
          } : null,
          saveCopy: () => saveCopyEd(),
          closeCopy: () => closeCopyEd(),
          openImport,
          confirmImport,
          rebuildImpPreview,
          setImpMap,
          impState: () => ({
            n: impN.value, skip: impSkip.value, map: (impMap.value || []).slice(),
            grid: impGrid.value.length,
          }),
          fold: () => ({ den: foldDen.value, add: foldAdd.value, imp: foldImport.value }),
          fitToolbar,
          parseLinks: html => TasksSheet.parseHtmlGridLinks(html),
          firstHref: html => TasksSheet.firstHrefFromHtml(html),
          unwrap: href => TasksSheet.unwrapGoogleUrl(href),
        };
      }));
      onBeforeUnmount(() => {
        clearTimeout(saveTimer);
        clearTimeout(draftTimer);
        beaconFlush();
        window.removeEventListener('resize', onWindowFit);
        document.removeEventListener('keydown', onPanelKey, true);
        document.removeEventListener('mousedown', onMoreAway);
        if (barObs) { try { barObs.disconnect(); } catch (e) { /* ignore */ } barObs = null; }
        document.removeEventListener('mouseup', endBrush);
        document.removeEventListener('mouseup', onPointerUp);
        document.removeEventListener('mousemove', onFillMove);
        document.removeEventListener('mouseup', onFillUp);
        finishRowDragVisual();
        if (resizeObs) { try { resizeObs.disconnect(); } catch (e) { /* ignore */ } resizeObs = null; }
        cancelMoveDrag();
        if (fillTip && fillTip.parentNode) fillTip.parentNode.removeChild(fillTip);
        fillTip = null; fillEl = null;
        if (moveGhost && moveGhost.parentNode) moveGhost.parentNode.removeChild(moveGhost);
        if (moveHit && moveHit.parentNode) moveHit.parentNode.removeChild(moveHit);
        moveGhost = null; movePlus = null; moveHit = null;
        if (dropLine && dropLine.parentNode) dropLine.parentNode.removeChild(dropLine);
        if (dragTip && dragTip.parentNode) dragTip.parentNode.removeChild(dragTip);
        dropLine = null; dragTip = null;
        clearTimeout(copySegTimer);
        if (queueTimer) clearInterval(queueTimer);
        try { delete window.__tasksDebug; } catch (e) { /* ignore */ }
        if (window.unregisterFlush) window.unregisterFlush('tasks');
        if (window.unregisterBeaconFlush) window.unregisterBeaconFlush('tasks');
        PrioFlagLogic.closePop();
        if (table) { table.destroy(); table = null; }
        if (whereWait) { whereWait.reject(Object.assign(new Error('cancel'), { cancelled: true })); whereWait = null; }
      });

      return {
        t, hints, tableEl, hintEl, fileEl, panelEl, barEl, moreEl, selectedN, selectedM, busy, dlg, conflictDlg, finishDlg, finishSelOk, prepDlg, moveDlg,
        pasteText, whereRoot, whereRemember, addN, density, onlySel, warnN,
        findOn, findQ, findR, findCase, findCell, findHit, delPlan,
        copyEd, copyEdText, copyEdSegs, copyTaEl, gridEditing,
        dirty, saving, saveError, saveLabelText, saveClassName, draftBanner, draftDiffOn,
        impSrc, impText, impFileName, impGrid, impMap, impSkip, impMapEdit, impN, impFields: IMP_FIELDS,
        moreOpen, foldDen, foldAdd, foldImport,
        addRows, saveTable, openPaste, confirmPaste, triggerImport, onFile,
        conflictCur, conflictUseMine, conflictUseTheirs,
        openImport, confirmImport, onImpText, onImpPaste, impSample, setImpMap, impFieldLabel,
        toggleMore, closeMore, exportSelTsv, menuResetCols, menuOnlySel, menuDensity, menuImport,
        prepare, pickFolder, confirmWhere, cancelDlg,
        toggleDensity, toggleOnlySel, jumpWarn, gotoFind, replaceAll,
        askDeleteSel, confirmDeleteSel, resetColWidths,
        openFinishSel, formatJobAge, cleanupFinishJob, confirmFinishSel, jumpFinishBlocked, closeFinishDlg, onFinishMask,
        confirmPrepSel, jumpPrepBlocked, closePrepDlg, confirmMoveWarn, cancelMoveWarn,
        askCloseCopyEd, closeCopyEd, saveCopyEd, selectAllCopyEd, onCopyEdInput,
        restoreDraft, discardDraft,
        durPrefDlg, openDurPrefDlg, applyDurPref, canSetDur,
      };
    },
    template: `
    <section class="panel-tasks" :class="{'tg-compact': density==='compact', 'tg-editing': gridEditing}" ref="panelEl">
      <div class="row panel-hd">
        <h2>{{ t('node.tasks') }}</h2>
        <div class="tg-bar" ref="barEl">
          <input type="file" ref="fileEl" class="file-hidden" accept=".csv,.txt,text/csv,.xlsx,.xls" @change="onFile">
          <div class="tg-bar-g" v-show="!foldImport">
            <button type="button" class="btn tg-imp" @click="openImport('paste')">{{ t('tasks.import_dlg.open') }}</button>
          </div>
          <div class="tg-bar-div" v-show="!foldImport"></div>
          <div class="tg-bar-g">
            <div class="tg-add-seg" v-show="!foldAdd">
              <button type="button" class="btn" @click="addRows">{{ t('tasks.add_row') }}</button>
              <input class="tg-add-n" v-model="addN" maxlength="4" @keydown.enter.prevent="addRows" inputmode="numeric">
            </div>
            <button type="button" class="btn tg-den" v-show="!foldDen" @click="toggleDensity">{{ density==='compact' ? t('tasks.density.compact') : t('tasks.density.comfy') }}</button>
            <div class="tg-more" ref="moreEl">
              <button type="button" class="btn tg-more-btn" :title="t('tasks.more')" @click.stop="toggleMore">⋯</button>
              <div v-if="moreOpen" class="tg-more-menu" @click.stop>
                <button v-if="foldDen" type="button" class="mi" @click="menuDensity">{{ density==='compact' ? t('tasks.density.compact') : t('tasks.density.comfy') }}</button>
                <div v-if="foldAdd" class="tg-more-add">
                  <div class="tg-add-seg">
                    <button type="button" class="btn" @click="addRows(); closeMore()">{{ t('tasks.add_row') }}</button>
                    <input class="tg-add-n" v-model="addN" maxlength="4" @keydown.enter.prevent="addRows" inputmode="numeric">
                  </div>
                </div>
                <button v-if="foldImport" type="button" class="mi" @click="menuImport">{{ t('tasks.import_dlg.open') }}</button>
                <div v-if="foldDen || foldAdd || foldImport" class="tg-more-split"></div>
                <button type="button" class="mi" :title="t('tasks.colreset_tip')" @click="menuResetCols">{{ t('tasks.colreset') }}</button>
                                <button type="button" class="mi"
                        :class="{off: !canSetDur}"
                        :disabled="!canSetDur"
                        :title="canSetDur ? t('tasks.dur_set_tip') : t('tasks.dur_set_disabled')"
                        @click="openDurPrefDlg">{{ t('tasks.dur_set') }}</button>
<button type="button" class="mi" @click="exportSelTsv">{{ t('tasks.export_tsv') }}</button>
                <button type="button" class="mi" :class="{off: !finishSelOk}" :disabled="!finishSelOk" @click="openFinishSel">{{ t('tasks.finish_sel', { n: String(selectedN) }) }}</button>
                <button type="button" class="mi" :class="{on: onlySel}" @click="menuOnlySel">{{ t('tasks.filter.sel') }}</button>
              </div>
            </div>
          </div>
          <div class="tg-bar-div"></div>
          <div class="tg-bar-g">
            <button type="button" class="btn tg-del" :class="{on: !!selectedN}" :disabled="busy || !selectedN" @click="askDeleteSel">{{ t('tasks.del_sel') }}</button>
          </div>
          <div class="tg-bar-div"></div>
          <div class="tg-bar-g tg-bar-save">
            <button type="button" class="btn tg-save" :disabled="busy || saving" @click="saveTable()">{{ t('tasks.save') }}</button>
            <span class="tg-savestat" :class="saveClassName">{{ saveLabelText }}</span>
            <span v-if="saveError && dirty && !saving" class="tg-save-err">{{ saveError }}</span>
          </div>
        </div>
      </div>
      
  <div class="dur-mask" v-if="durPrefDlg.on" @click.self="durPrefDlg.on=false">
    <div class="dur-card" @click.stop>
      <h3>{{ t('tasks.dur_set') }}</h3>
      <p class="muted">{{ durPrefDlg.note }}</p>
      <p v-if="durPrefDlg.needFlow">
        Flow
        <select v-model.number="durPrefDlg.flowDur">
          <option :value="4">4s</option><option :value="6">6s</option><option :value="8">8s</option>
        </select>
      </p>
      <p v-if="durPrefDlg.needGrok">
        Grok
        <select v-model.number="durPrefDlg.grokDur">
          <option :value="6">6s</option><option :value="10">10s</option><option :value="15">15s</option>
        </select>
      </p>
      <p v-if="durPrefDlg.needStudio">
        本地模型
        <select v-model.number="durPrefDlg.studioDur">
          <option v-for="n in [4,6,8,10,15]" :key="n" :value="n">{{ n }}s</option>
        </select>
      </p>
      <div class="dur-ft">
        <button class="btn sm" type="button" @click="durPrefDlg.on=false">{{ t('tasks.dur_cancel') }}</button>
        <button class="btn sm primary" type="button" @click="applyDurPref">{{ t('tasks.dur_apply') }}</button>
      </div>
    </div>
  </div>

      <div v-if="draftBanner" class="tg-draft-banner">
        <span class="tg-draft-txt">{{ t('tasks.draft.banner', { n: String(draftBanner.n), ago: draftBanner.ago }) }}</span>
        <button class="btn sm" @click="restoreDraft">{{ t('tasks.draft.restore') }}</button>
        <button class="btn sm" @click="draftDiffOn=true">{{ t('tasks.draft.diff') }}</button>
        <button class="btn sm" @click="discardDraft">{{ t('tasks.draft.discard') }}</button>
      </div>
      <div v-show="findOn" class="tg-find">
        <span>{{ t('tasks.find.find') }}</span>
        <input type="text" v-model="findQ" @keydown.enter.prevent="gotoFind(1)" @keydown.esc="findOn=false">
        <span>{{ t('tasks.find.replace') }}</span>
        <input type="text" v-model="findR" @keydown.esc="findOn=false">
        <label><input type="checkbox" v-model="findCase"> {{ t('tasks.find.case') }}</label>
        <label><input type="checkbox" v-model="findCell"> {{ t('tasks.find.cell') }}</label>
        <button class="btn sm" @click="gotoFind(-1)">{{ t('tasks.find.prev') }}</button>
        <button class="btn sm" @click="gotoFind(1)">{{ t('tasks.find.next') }}</button>
        <button class="btn sm" @click="replaceAll">{{ t('tasks.find.all') }}</button>
        <span class="muted">{{ findHit }}</span>
      </div>
      <div class="hintrow" ref="hintEl"><div v-for="h in hints" :key="h">{{ t(h) }}</div></div>
      <div class="tg-table-wrap"><div ref="tableEl"></div></div>
      <div class="row panel-ft">
        <button class="btn ghost tg-count" :class="{on: onlySel}" @click="toggleOnlySel">{{ t('tasks.selected', { n: selectedN, m: selectedM }) }}</button>
        <span v-if="warnN" class="tg-warn">{{ t('tasks.warn.n', { n: warnN }) }} <button class="btn sm" @click="jumpWarn">{{ t('tasks.warn.jump') }}</button></span>
        <span style="flex:1"></span>
        <button class="btn" :disabled="busy" @click="prepare(true)">{{ t('tasks.prepare_selected') }}</button>
        <button class="btn primary" :disabled="busy" @click="prepare(false)">{{ t('tasks.prepare') }}</button>
      </div>

      <div v-if="draftDiffOn && draftBanner" class="dlg-mask" @click.self="draftDiffOn=false">
        <div class="dlg">
          <h3>{{ t('tasks.draft.diff_title') }}</h3>
          <ul class="tg-draft-diff">
            <li v-for="(r,i) in draftBanner.missing" :key="i">
              {{ r.id || t('tasks.copyed.unsaved') }} · {{ r.name || (r.copy || '').slice(0, 24) || t('tasks.draft.empty') }}
            </li>
          </ul>
          <div class="row" style="justify-content:flex-end;margin-top:12px">
            <button class="btn ghost" @click="draftDiffOn=false">{{ t('common.cancel') }}</button>
            <button class="btn primary" @click="restoreDraft">{{ t('tasks.draft.restore') }}</button>
          </div>
        </div>
      </div>

      <div v-if="conflictDlg" class="dlg-mask">
        <div class="dlg">
          <h3>{{ t('tasks.conflict') }}</h3>
          <p class="muted" v-if="conflictCur()">{{ (conflictCur().theirs && conflictCur().theirs.id) || '' }}</p>
          <div class="row" style="justify-content:flex-end;margin-top:12px;gap:8px">
            <button class="btn" type="button" @click="conflictUseTheirs">{{ t('tasks.conflict.theirs') }}</button>
            <button class="btn primary" type="button" @click="conflictUseMine">{{ t('tasks.conflict.mine') }}</button>
          </div>
        </div>
      </div>

      <div v-if="dlg==='import'" class="dlg-mask" @click.self="cancelDlg">
        <div class="dlg dlg-import">
          <div class="dlg-import-hd">
            <h3>{{ t('tasks.import_dlg.title') }}</h3>
            <button type="button" class="dlg-copy-x" @click="cancelDlg">✕</button>
          </div>
          <div class="imp-src">
            <span>{{ t('tasks.import_dlg.src') }}</span>
            <label><input type="radio" value="paste" v-model="impSrc"> {{ t('tasks.import_dlg.src_paste') }}</label>
            <label><input type="radio" value="file" v-model="impSrc"> {{ t('tasks.import_dlg.src_file') }}</label>
          </div>
          <textarea v-show="impSrc==='paste'" class="paste-box" v-model="impText"
            :placeholder="t('tasks.import_dlg.placeholder')" @paste="onImpPaste" @input="onImpText"></textarea>
          <div v-show="impSrc==='file'" class="imp-file">
            <button type="button" class="btn" @click="triggerImport">{{ t('tasks.import_dlg.pick') }}</button>
            <span class="muted">{{ impFileName }}</span>
          </div>
          <div v-if="impGrid.length" class="imp-preview">
            <div class="imp-preview-hd">
              <span>{{ t('tasks.import_dlg.detected', { n: String(impN) }) }}</span>
              <button type="button" class="btn ghost imp-edit" @click="impMapEdit=!impMapEdit">{{ t('tasks.import_dlg.edit') }}</button>
            </div>
            <div class="imp-map-sum">
              <span v-for="(m,i) in impMap" :key="'s'+i" class="imp-chip">
                {{ t('tasks.import_dlg.col_to', { n: String(i+1), field: impFieldLabel(m) }) }}
                <em v-if="impSample(i)" class="imp-chip-val" :title="impSample(i)">{{ impSample(i) }}</em>
              </span>
            </div>
            <div v-show="impMapEdit" class="imp-map">
              <label v-for="(m,i) in impMap" :key="'m'+i" class="imp-col">
                <span>{{ t('tasks.import_dlg.col', { n: String(i+1) }) }}</span>
                <select :value="m" @change="setImpMap(i, $event.target.value)">
                  <option value="">{{ t('tasks.import_dlg.ignore') }}</option>
                  <option v-for="f in impFields" :key="f.v" :value="f.v">{{ t(f.k) }}</option>
                </select>
              </label>
            </div>
            <label class="imp-skip"><input type="checkbox" v-model="impSkip"> {{ t('tasks.import_dlg.skip') }}</label>
          </div>
          <div class="row" style="justify-content:flex-end;margin-top:12px">
            <button type="button" class="btn ghost" @click="cancelDlg">{{ t('common.cancel') }}</button>
            <button type="button" class="btn primary" :disabled="!impN" @click="confirmImport">{{ t('tasks.import_dlg.append', { n: String(impN) }) }}</button>
          </div>
        </div>
      </div>

      <div v-if="dlg==='finish' && finishDlg" class="dlg-mask" @click.self="onFinishMask">
        <div class="dlg dlg-finish" @click.stop>
          <h3>{{ t('tasks.finish_dlg.title', { n: String(finishDlg.nSel) }) }}</h3>
          <div v-if="finishDlg.phase==='loading'" class="tg-fin-prog">{{ t('tasks.finish_dlg.loading') }}</div>
          <template v-if="finishDlg.phase==='preview'">
            <div class="tg-fin-sec" v-if="finishDlg.ready.length">
              <h4>{{ t('tasks.finish_dlg.ready', { n: String(finishDlg.ready.length) }) }}</h4>
              <ul class="tg-fin-list">
                <li v-for="row in finishDlg.ready" :key="'ok-'+row.tid" class="ready">
                  <span class="mark">✓</span>
                  <span class="id">{{ row.tid }}</span>
                  <span class="nm">{{ row.name }}</span>
                  <span class="why">{{ row.reason }}</span>
                </li>
              </ul>
            </div>
            <div class="tg-fin-sec" v-if="finishDlg.blocked.length">
              <h4>{{ t('tasks.finish_dlg.blocked', { n: String(finishDlg.blocked.length) }) }}</h4>
              <ul class="tg-fin-list">
                <li v-for="row in finishDlg.blocked" :key="'no-'+row.tid" class="blocked">
                  <div class="row" style="gap:8px;align-items:flex-start;cursor:pointer" @click="jumpFinishBlocked(row)">
                    <span class="mark">✗</span>
                    <span class="id">{{ row.tid }}</span>
                    <span class="nm">{{ row.name }}</span>
                    <span class="why">{{ row.reason }}</span>
                  </div>
                  <div v-if="row.jobs && row.jobs.length" class="jobs-block-list" style="margin:6px 0 0 24px;font-size:12px">
                    <div v-for="j in row.jobs" :key="j.id" class="row" style="justify-content:space-between;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid rgba(127,127,127,.2)">
                      <div style="flex:1;min-width:0;line-height:1.4">
                        <div>{{ j.task_id || row.tid }} · {{ j.task_name || row.name || '' }}<span v-if="j.task_status">（{{ j.task_status }}）</span></div>
                        <div style="opacity:.75">{{ j.kind }} · {{ j.status }} · {{ formatJobAge(j.age_sec) }}</div>
                      </div>
                      <button v-if="j.stale" class="btn sm danger" type="button" @click.stop="cleanupFinishJob(j, row)">清理掉</button>
                    </div>
                  </div>
                </li>
              </ul>
            </div>
            <div class="row" style="justify-content:flex-end;margin-top:12px">
              <button class="btn ghost" @click="cancelDlg">{{ t('common.cancel') }}</button>
              <button class="btn primary" :disabled="!finishDlg.ready.length" @click="confirmFinishSel">{{ t('tasks.finish_dlg.go', { n: String(finishDlg.ready.length) }) }}</button>
            </div>
          </template>
          <template v-if="finishDlg.phase==='run'">
            <div class="tg-fin-prog">{{ t('tasks.finish_dlg.progress', { i: String(finishDlg.i), n: String(finishDlg.ready.length), id: finishDlg.cur }) }}</div>
            <div class="tg-fin-bar"><i :style="{width: (finishDlg.ready.length ? Math.round(finishDlg.i / finishDlg.ready.length * 100) : 0) + '%'}"></i></div>
          </template>
          <template v-if="finishDlg.phase==='done'">
            <div class="tg-fin-sec">
              <h4>{{ t('tasks.finish_dlg.done', { n: String(finishDlg.done.length) }) }}</h4>
            </div>
            <div class="tg-fin-sec" v-if="finishDlg.failed.length">
              <h4>{{ t('tasks.finish_dlg.fail', { n: String(finishDlg.failed.length) }) }}</h4>
              <ul class="tg-fin-list">
                <li v-for="row in finishDlg.failed" :key="'fail-'+row.tid" class="fail">
                  <span class="mark">✗</span>
                  <span class="id">{{ row.tid }}</span>
                  <span class="nm">{{ row.name }}</span>
                  <span class="why">{{ row.error }}</span>
                </li>
              </ul>
            </div>
            <div class="row" style="justify-content:flex-end;margin-top:12px">
              <button class="btn primary" @click="closeFinishDlg">{{ t('tasks.finish_dlg.close') }}</button>
            </div>
          </template>
        </div>
      </div>

      <div v-if="dlg==='prep' && prepDlg" class="dlg-mask" @click.self="closePrepDlg">
        <div class="dlg dlg-finish dlg-prep" @click.stop>
          <h3>{{ t('tasks.prep_dlg.title', { n: String(prepDlg.nSel) }) }}</h3>
          <div v-if="prepDlg.phase==='loading'" class="tg-fin-prog">{{ t('tasks.prep_dlg.loading') }}</div>
          <template v-if="prepDlg.phase==='preview'">
            <div class="tg-fin-sec" v-if="prepDlg.ready.length">
              <h4>{{ t('tasks.prep_dlg.ready', { n: String(prepDlg.ready.length) }) }}</h4>
              <ul class="tg-fin-list">
                <li v-for="row in prepDlg.ready" :key="'pok-'+row.tid" class="ready">
                  <span class="mark">✓</span>
                  <span class="id">{{ row.tid }}</span>
                  <span class="nm">{{ row.name }}</span>
                  <span class="why">{{ row.reason }}</span>
                </li>
              </ul>
            </div>
            <div class="tg-fin-sec" v-if="prepDlg.blocked.length">
              <h4>{{ t('tasks.prep_dlg.blocked', { n: String(prepDlg.blocked.length) }) }}</h4>
              <ul class="tg-fin-list">
                <li v-for="row in prepDlg.blocked" :key="'pno-'+row.tid" class="blocked" @click="jumpPrepBlocked(row)">
                  <span class="mark">✗</span>
                  <span class="id">{{ row.tid }}</span>
                  <span class="nm">{{ row.name }}</span>
                  <span class="why">{{ row.reason }}</span>
                </li>
              </ul>
            </div>
            <div class="row" style="justify-content:flex-end;margin-top:12px">
              <button class="btn ghost" @click="cancelDlg">{{ t('common.cancel') }}</button>
              <button class="btn primary" :disabled="!prepDlg.ready.length" @click="confirmPrepSel">{{ t('tasks.prep_dlg.go', { n: String(prepDlg.ready.length) }) }}</button>
            </div>
          </template>
        </div>
      </div>

      <div v-if="dlg==='move-warn'" class="dlg-mask" @click.self="cancelMoveWarn">
        <div class="dlg" @click.stop>
          <h3>{{ t('tasks.img.mode.move') }}</h3>
          <p class="tg-move-warn">{{ t('set.confirm.images.local_mode.move') }}</p>
          <div class="row" style="justify-content:flex-end;margin-top:12px">
            <button class="btn ghost" @click="cancelMoveWarn">{{ t('common.cancel') }}</button>
            <button class="btn primary" @click="confirmMoveWarn">{{ t('common.ok') }}</button>
          </div>
        </div>
      </div>

      <div v-if="dlg==='delsel'" class="dlg-mask" @click.self="cancelDlg">
        <div class="dlg">
          <h3>{{ t('tasks.del.title', { n: String(delPlan.total) }) }}</h3>
          <ul class="tg-del-list">
            <li v-if="delPlan.draft.length">{{ t('tasks.del.draft', { n: String(delPlan.draft.length) }) }}</li>
            <li v-if="delPlan.trash.length">{{ t('tasks.del.trash', { n: String(delPlan.trash.length), ids: delPlan.trashIds }) }}</li>
            <li v-if="delPlan.running.length">{{ t('tasks.del.running', { ids: delPlan.runIds }) }}</li>
          </ul>
          <div class="row" style="justify-content:flex-end">
            <button class="btn ghost" @click="cancelDlg">{{ t('common.cancel') }}</button>
            <button class="btn primary" :disabled="!delPlan.draft.length && !delPlan.trash.length" @click="confirmDeleteSel">{{ t('tasks.del.confirm') }}</button>
          </div>
        </div>
      </div>

      <div v-if="dlg==='where'" class="dlg-mask" @click.self="cancelDlg">
        <div class="dlg">
          <h3>{{ t('tasks.where') }}</h3>
          <div class="field">
            <label>{{ t('tasks.where.current') }}</label>
            <div class="row">
              <input v-model="whereRoot" style="flex:1">
              <button class="btn sm" @click="pickFolder">{{ t('tasks.where.pick') }}</button>
            </div>
          </div>
          <label class="row" style="margin:10px 0 16px"><input type="checkbox" v-model="whereRemember"> {{ t('tasks.where.remember') }}</label>
          <div class="row" style="justify-content:flex-end">
            <button class="btn ghost" @click="cancelDlg">{{ t('common.cancel') }}</button>
            <button class="btn primary" @click="confirmWhere">{{ t('common.ok') }}</button>
          </div>
        </div>
      </div>

      <div v-if="dlg==='copy' && copyEd" class="dlg-mask" @click.self="askCloseCopyEd">
        <div class="dlg dlg-copy">
          <div class="dlg-copy-hd">
            <h3>{{ t('tasks.copyed.title', { who: copyEd.who }) }}</h3>
            <button type="button" class="dlg-copy-x" @click="askCloseCopyEd">✕</button>
          </div>
          <p v-if="copyEd.readonly" class="dlg-copy-lock">{{ t('tasks.copyed.locked') }}</p>
          <textarea ref="copyTaEl" class="dlg-copy-ta" v-model="copyEdText"
            :readonly="copyEd.readonly" @input="onCopyEdInput"
            spellcheck="false"></textarea>
          <div class="dlg-copy-meta">
            <span>{{ t('tasks.copyed.stats', { n: String(copyEdText.length), s: copyEdSegs == null ? '—' : String(copyEdSegs) }) }}</span>
            <button type="button" class="btn sm" @click="selectAllCopyEd">{{ t('tasks.copyed.select_all') }}</button>
          </div>
          <div class="dlg-copy-ft">
            <template v-if="copyEd.readonly">
              <button type="button" class="btn ghost" @click="closeCopyEd">{{ t('common.close') }}</button>
            </template>
            <template v-else>
              <button type="button" class="btn ghost" @click="askCloseCopyEd">{{ t('common.cancel') }}</button>
              <button type="button" class="btn primary" @click="saveCopyEd">{{ t('tasks.copyed.save') }}</button>
            </template>
          </div>
        </div>
      </div>
    </section>`
  };
})();
