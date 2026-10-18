# 31 · 任务表格配色回归：灰底黑字，改回深色皮肤

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/00-architecture.md`
> 和 `specs/28-tasks-grid.md`，再读本规格。**只改 `ui/css/app.css`**（必要时再动
> `ui/index.html` 一行），不要碰 `ui/js/panels/tasks.js` 的逻辑，功能一个都不许退。
> 改前备份成 `app.css.bak-0910theme`。报告写到 `specs/reports/31-grid-theme-fix.md`。中文注释。

## 现象

28 号做完之后，① 任务表格一框选，选中的那片就变成**浅灰底 + 黑字**，
在整个深色界面里像一块补丁，文字也看不清了。

## 根因（已定位，不用再猜）

`ui/vendor/tabulator_midnight.min.css` 里有这么几条，用的是**裸类名**（特异度只有 0,1,0），
而且是按浅色思路写的：

    .tabulator-range-highlight { background-color:#999; color:#000 }
    .tabulator-range-selected  { background-color:#ccc; color:#333 }
    .tabulator .tabulator-header .tabulator-col.tabulator-range-highlight { background-color:#999; color:#000 }
    .tabulator .tabulator-header .tabulator-col.tabulator-range-selected  { background-color:#ccc; color:#333 }
    .tabulator-row .tabulator-cell.tabulator-range-row-header             { background-color:#ccc; color:#333 }
    .tabulator-row.tabulator-row-even { background-color:#444 }
    .tabulator-row.tabulator-selectable:hover { background-color:#999 }

28 号在 `app.css` 里**只覆盖了 `.tabulator-range-selected` 这一条**（第 198 行那个长选择器），
`-highlight` 那几条一条都没盖。`#999` 就是截图里那片灰，`color:#000` 就是看不清的黑字。

**教训写进注释里**：vendor 那份 midnight 主题里凡是出现 `#999 / #ccc / #444 / #888 /
color:#000 / color:#333` 的地方，都要当成「会漏出来的浅色」来防。

---

## 要做的

### 1. 先中和，再一层层加回来

在 `app.css` 的 tabulator 段落**最前面**加一段兜底（带上这段中文注释）：

    /* vendor 的 midnight 主题是按浅色思路写的：range-highlight / range-selected /
       row-header 这几条用裸类名写死了 #999 / #ccc 底色和 #000 / #333 文字色，
       在我们的深色皮肤下会漏成"灰底黑字"。这里先统一中和，下面再按状态分层加回来。
       以后升级 vendor CSS，先回来核对这份清单。 */
    .tabulator-range-highlight,
    .tabulator-range-selected,
    .tabulator .tabulator-header .tabulator-col.tabulator-range-highlight,
    .tabulator .tabulator-header .tabulator-col.tabulator-range-selected,
    .tabulator-row .tabulator-cell.tabulator-range-row-header,
    .tabulator-row.tabulator-range-highlight .tabulator-cell.tabulator-range-row-header,
    .tabulator-row.tabulator-range-selected  .tabulator-cell.tabulator-range-row-header{
      background-color:transparent;
      color:var(--ink);
    }

**铁律：任何状态都不许改文字颜色。** 深色界面里靠「底色深浅」表达状态，
不靠「文字变黑」。全表只有两种文字色：`var(--ink)` 正文、`var(--ink-2)` 次要。

### 2. 状态分层（照抄这张表的值，别自己发挥）

底色一律用**半透明叠色**，这样斑马纹、悬停、选中三层叠在一起也不会打架：

| 状态 | 值 | 叠在 `--panel` #191714 上大致是 |
|---|---|---|
| 奇数行 | `transparent` | #191714 |
| 偶数行（斑马） | `rgba(255,255,255,.022)` | #1D1B18 |
| 悬停行 | `rgba(255,255,255,.055)` | #24211D |
| 选区单元格 | `rgba(217,163,79,.13)` | #32291C |
| 行头/列头随选区高亮 | `rgba(217,163,79,.20)` | #3E3324 |
| 表头里被选中的列 | `rgba(217,163,79,.22)` | #413526 |
| 整行被勾选（复选框） | `rgba(217,163,79,.10)` + 左侧 2px `--accent` 竖条 | #2E2720 |
| 编辑中的格 | 底 `var(--panel-2)` + 1px `var(--focus)` 边框 | #211E19 |

选区的**边框**才是主要视觉信号，底色只是轻轻一染：

    .tabulator-range           → 1px solid rgba(217,163,79,.85)
    .tabulator-range-cell-active → 2px solid var(--accent)

对比度自己核一遍：`#EFEAE0` 落在上表任何一个底色上都要 **≥ 7:1**。
（`#EFEAE0` on `#32291C` 约 11:1，够。）

### 3. 顺手修掉两个也在变糊的地方

- `.cell-locked{color:var(--muted);opacity:.75}` —— muted 本来就淡，再乘 .75 就快看不见了
  （截图里 T0014 那行的文案就是这样）。改成 `color:var(--ink-2)`，**去掉 opacity**。
  想表达"锁住"就在格子右上角放一个 10px 的小锁图标，或者左侧 2px 的暗色竖条，
  **不要靠调暗文字**。
- 第 128 行 `.tabulator-row.tabulator-selected{background:#2a2a20!important}` 那个橄榄绿
  和上表的暖金色不是一套，统一成上表「整行被勾选」那一档。

### 4. 别破坏已经对的东西

现有这些是好的，保留：行左侧 `--flow` / `--grok` 的 3px 色条、当前行的 accent 竖条、
`cell-warn` 的 `rgba(201,162,39,.28)`、`cell-miss` 的 `rgba(217,112,95,.22)`、
填充柄 `.tg-fill`、复选框 `.tg-check`、紧凑档 `.tg-compact`。
色条和背景叠加时**色条要压在最上层**，别被选区底色盖掉。

---

## 验收

1. 写一个小自查脚本 `tools\css_theme_check.py`：扫 `ui/css/app.css` 里所有
   选择器含 `tabulator` 的规则，**断言没有任何一条把 `color` 设成
   `#000 / #333 / black`，也没有把 `background-color` 设成 `#999 / #ccc / #444 / #888`**。
   顺便扫一遍 vendor 的 midnight css，把里面所有这类裸类名规则列出来，
   和第 1 节的中和清单比对，**漏了哪条就报出来**。跑通并把输出贴进报告。
2. 起 Orante，① 任务面板：
   - 不选任何东西 → 斑马纹只是极淡的一层，整体还是深色。
   - 鼠标划过一行 → 微微亮一点，文字始终是浅色。
   - 框选 4 行 × 5 列 → **暖金色的淡染 + 清晰的金色外框**，
     选区里的文字和没选中时一样清楚，**没有任何一块浅灰、没有一个黑字**。
   - 表头对应的那几列也跟着淡染，表头文字仍是浅色。
   - 双击进编辑 → 焦点边框是 `--focus`，输入的字看得清。
3. 勾几行复选框 → 整行淡染 + 左侧竖条；再框选别的区域，两种高亮叠在一起
   仍然分得清、都不刺眼。
4. 切到「紧凑」档再看一遍，同样干净。
5. 能截图就截两张（选中前 / 选中后）放进 `specs/reports/`，命名
   `31-grid-normal.png` / `31-grid-selected.png`；截不了就在报告里写明没有浏览器工具。
6. 28 号的功能一条都不许退：粘贴、填充柄、复选框拖选、查找替换、撤销，各点一遍确认还在。
