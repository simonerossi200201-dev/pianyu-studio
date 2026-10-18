# 45 · 从 Google Sheet 粘过来的超链接丢了（只拿到显示名，拿不到网址）

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/28-tasks-grid.md`、
> `specs/44-tasks-toolbar.md` 和它们的报告，再读 `ui/js/panels/tasks.js` 里
> `clipboardPasteParser`（约 1803 行）、`applyPaste()`（约 640 行）、`pendingPaste`、
> `TasksSheet.parseTSVGrid` / `stripHeaderRow`，再读本规格。
> **先做完 41~44。** 只改 `ui/js/panels/tasks.js`（以及 `TasksSheet` 所在的那个文件）、
> `ui/css/app.css`、`orante/i18n/zh.json`。
> 改前备份 `*.bak-0911link`。报告写到 `specs/reports/45-paste-hyperlinks.md`。中文注释。

## 现象

Google Sheet 里「图片来源」那一列是**超链接单元格**：显示的是网盘名字（比如 `李良素材`），
底下挂着 Drive 网址。从表里复制、粘到 ① 任务的单元格里，**只进来名字，网址丢了**。

## 根因

浏览器的剪贴板里同一份内容有**两种格式**：

| 格式 | 内容 |
|---|---|
| `text/plain` | 只有**显示文字**：`李良素材`。**网址根本不在里面。** |
| `text/html` | 完整的表格 HTML，链接在这里：`<td data-sheets-hyperlink="https://drive.google.com/…">李良素材</td>` |

而 `buildTable()` 里那条：

    clipboardPasteParser: text => { … TasksSheet.parseTSVGrid(text) … }

Tabulator 的 `clipboardPasteParser` **只拿得到 `text/plain`**。
所以不是解析写错了，是**从一开始就没读那一半数据**。纯文本里没有的东西，怎么解析都解析不出来。

## 要做的

### 第 0 步：先把真实的剪贴板 HTML 抓出来看（别照我写的属性名硬编）

Google Sheet 的 HTML 结构会变，**照着实测来写**。先做一个临时的抓取：

在表格上监听一次 `paste`，把 `e.clipboardData.getData('text/html')` 原样写到
`data/clip-dump.html`（走后端一个临时路由，或者直接 `console.log` 再手抄）。
**用用户给的那张表的 N 列真实复制一次**，把**前 800 个字符**贴进报告。
后面的选择器按实测的结果写，报告里说明实际用到了哪些属性。

我这边的预期是下面三种任选其一命中，**但以实测为准**：

1. `td[data-sheets-hyperlink]` → 属性值就是网址
2. `td a[href]` → 第一个 `<a>` 的 href
3. `td[data-sheets-formula]` 里形如 `=HYPERLINK("https://…","名字")` → 抠出第一个参数

### 1. 自己接管 paste，两种格式一起读

在表格容器上加一个 **capture 阶段**的 `paste` 监听，**跑在 Tabulator 前面**：

    holder.addEventListener('paste', ev => {
      const html = ev.clipboardData && ev.clipboardData.getData('text/html');
      pendingPasteLinks = html ? parseHtmlGridLinks(html) : null;   // 二维数组，同形状
      // 不要 preventDefault，让 Tabulator 照常走它的 text/plain 流程
    }, true);

`parseHtmlGridLinks(html)`：用 `DOMParser` 解析 → 找第一个 `<table>` → 遍历 `tr` / `td|th`
→ 每格产出 `{ text, href }`，`href` 按第 0 步实测的优先级取，取不到就 `null`。
**要处理 `colspan` / `rowspan`**（Google Sheet 的合并单元格），
展开成和 `text/plain` 那个网格**同样的行列形状**，否则两边对不齐就会串列。
行列数对不上时（合并单元格没展开干净等）**整个丢弃 `pendingPasteLinks`**，
退回纯文本，**宁可少拿网址，也不能串列**。

### 2. 谁优先：按目标列的性质决定

在 `applyPaste()` 里写入每一格时：

| 目标列 | 取哪个 |
|---|---|
| `image_source`（图片来源） | **有 `href` 就用 `href`**，没有才用 `text` |
| 其余所有列 | 一律用 `text`（任务名、文案里要的是文字，不是链接） |

列的性质做成一张表 `LINK_PREFER_FIELDS = new Set(['image_source'])`，
以后要加别的链接列改这一处就行。

### 3. 顺手把 Google 的跳转包装拆掉

Sheet 里的链接经常被包成
`https://www.google.com/url?q=<被编码的真实地址>&sa=D&source=editors&ust=…`。
取到 href 后：如果主机是 `www.google.com` 且路径是 `/url`，
就把查询参数 `q` 解码出来当真实地址。其余情况原样保留。

### 4. 三个入口都要能吃到链接

1. **表格里 `Ctrl+V`** —— 上面第 1 步。
2. **44 号那个「导入…」对话框**的粘贴文本框 —— 同样监听它的 `paste` 事件读 `text/html`，
   把链接并进解析结果。**这个别漏，不然刚做的导入对话框会重现同一个 bug。**
3. **单个格子在编辑态里 `Ctrl+V`**（图片来源列是内联 input）——
   给这个 editor 的 input 挂 `paste`：目标是 `image_source` 且剪贴板 HTML 里第一个链接存在时，
   直接填网址而不是显示名。

### 5. 界面上让人看得出来

- `fmtImage` 已经在格子里显示链接。粘进来是从超链接还原的网址时，
  格子右侧加一个很淡的 🔗 小标，`title` 写 `从 Google Sheet 的超链接还原：<显示名>`。
  用户一眼能确认「名字背后的网址进来了」。
- 粘贴完 toast 补一句：`其中 12 格从超链接还原了网址`。

### 6. 兜底：什么都没有的时候别骗人

如果 `image_source` 那一格最后拿到的仍然是个**不像网址的纯文字**（不以 `http` 开头、
也不是本地路径），保持 28 号的黄色警告（`cell-warn`），
悬停提示 `这里需要网址；如果你是从 Google Sheet 复制的名字，请确认那一列是超链接`。
**不要自作聪明拿名字去猜网址。**

## 验收

1. 报告里贴出真实剪贴板 `text/html` 的前 800 字符，并说明最终用的是哪个属性/选择器。
2. 从用户那张表复制含 N 列的 5 行 × 6 列 → 粘进 ① 任务：
   **图片来源那一列进来的是 Drive 网址**，其余列还是文字；toast 报了还原了几格。
3. 同一份数据粘进 44 号的「导入…」对话框 → 列对应预览里图片来源那一列显示的也是网址。
4. 双击图片来源格进编辑态，在里面 `Ctrl+V` → 填进去的是网址。
5. 复制一段**没有超链接**的普通文字 → 行为和现在完全一样，没有任何回退。
6. 复制含**合并单元格**的区域 → 要么正确展开、要么整体退回纯文本，
   **绝不能串列**（这一条专门造一个合并单元格的例子测）。
7. 链接被包成 `google.com/url?q=…` 时，落进格子里的是解码后的真实地址。
8. 从 Excel 复制带超链接的一列 → 同样能拿到网址（走 `td a[href]` 那条）。
9. 回归：`node tools\test_spec28.mjs`、`python tools\test_spec28_ui.py`、
   `test_spec31_ui.py`、`test_spec33_ui.py` 全过；41~44 各点一遍没退。
