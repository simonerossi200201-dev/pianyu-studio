# 41 · 文案单元格没法编辑：拆掉内联编辑，改成弹层编辑器

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/28-tasks-grid.md`、
> `specs/33-grid-rows-and-polish.md`、`specs/39-grid-drag-and-fit.md` 和它们的报告，
> 再读 `ui/js/panels/tasks.js` 的 `columns()` / `placeFillHandle()` / `buildTable()` 里
> `tableBuilt` 那两个 holder mousedown，以及 `ui/css/app.css` 第 387 行那条
> `.panel-tasks .tabulator-row .tabulator-cell`。
> 只改 `ui/js/panels/tasks.js`、`ui/css/app.css`、`orante/i18n/zh.json`。
> 改前备份 `*.bak-0911copy`。报告写到 `specs/reports/41-copy-cell-editor.md`。中文注释。
> **28 / 31 / 33 / 39 的功能一条都不许退。**

## 现象

① 任务的「文案」格双击进了编辑态，但：选不中文字（一拖就退出编辑）、打不了字、改不了、
内容显示不全、想复制其中一段也做不到。

## 根因（四条，都查实了，不用再猜）

### (1) 编辑中的格子被 CSS 压成 34px 还 `overflow:hidden`

`ui/css/app.css:387`：

    .panel-tasks .tabulator-row .tabulator-cell{
      border-right:none!important;
      padding:0 10px; height:34px;
      overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
    }

**没有排除 `.tabulator-editing`。** 它和第 164 行那条
`.tabulator-row .tabulator-cell.tabulator-editing{…}` 特异度打平（都是 3 个 class），
**写在后面的赢**。于是 textarea 编辑器被钉死在 34px 高、超出部分裁掉、还 `white-space:nowrap`。
两千字的文案当然看不全、也没法正常改。

### (2) 填充柄在编辑时没有隐藏，而且它的命中判定只看坐标

`placeFillHandle()`（约 1099 行）完全不看有没有在编辑，选区还在，柄就还画着，
位置正好在选区右下角 —— 也就是**编辑框的内部或边缘**。

而 `buildTable()` 里 `tableBuilt` 的第一个 holder `mousedown`（**capture 阶段**）是这样判的：

    const r = fillEl.getBoundingClientRect();
    if (ev.clientX >= r.left - 2 && … ) onFillDown(ev);

**只比坐标，不看你按在哪个元素上。** 在 textarea 里靠近右下角按一下 = 开始拖填充柄 = 编辑当场取消。

### (3) 两个 capture 阶段的 mousedown 都没有给编辑态开豁免

上面那个，和紧接着处理 `_sel` 列的那个，都是 `addEventListener(..., true)`，
**跑在 Tabulator 自己的处理之前**，两个都没有 `.tabulator-editing` 的提前 return。

### (4) 拖选文字会被区域选择吃掉

`selectableRange: true` 时，Tabulator 的区域选择模块跟着 `mousemove` 扩选区；
选区一变就把当前编辑关掉。vendor CSS 里那条
`.tabulator.tabulator-ranges .tabulator-cell:not(.tabulator-editing){user-select:none}`
说明它本来有豁免，但配合我们 capture 阶段抢跑的监听，实际效果就是**一拖就退出编辑**。

### 另外：非草稿行连看都看不了

`columns()` 里文案列的 `editable` 是「只有 `status` 为空或 `draft` 才能编辑」。
**这条业务逻辑是对的**（已开工的任务改文案，会和已经生成的段对不上），
但它导致已开工的任务**连把文案看全、复制一段出来都做不到**。这也要一起解决。

---

## 要做的

### A. 文案不再内联编辑，改成弹层编辑器

两千字的文案本来就不该在 34px 的格子里改。文案列去掉 `editor: 'textarea'`，改成：

**打开方式**（都指向同一个弹层）：
- 双击文案格
- 选中文案格后按 `Enter` 或 `F2`
- 选中文案格后**直接打字** → 打开弹层，并把刚打的那个字作为起始内容（沿用 Sheet 手感）
- 格子右侧常驻一个很淡的展开图标（⤢，12px，悬停才变亮），点它也能打开

**弹层长相**（居中，宽 min(880px, 90vw)，高 min(640px, 80vh)）：

    ┌─ 文案 · T0014 李星辰v01 ───────────────────────── ✕ ┐
    │ ┌───────────────────────────────────────────────┐ │
    │ │  （整篇文案，等宽行距 1.7，可滚动，可随便选中）    │ │
    │ │                                               │ │
    │ └───────────────────────────────────────────────┘ │
    │  1842 字 · 约 11 段            [全选复制]           │
    │                        [取消]  [保存 Ctrl+Enter]   │
    └──────────────────────────────────────────────────┘

- textarea 占满，`white-space: pre-wrap`，`font-family: var(--ui)`，行距 1.7。
- 底部左侧实时显示 **字数** 和**按当前引擎/时长档位估算的段数**（复用 `split` 服务的规则，
  只算不落盘）。这一条很有用：贴完文案立刻知道会切成几段。
- `Ctrl+Enter` 保存，`Esc` 取消（有改动时先问一句「放弃修改？」）。
- 保存后写回该行并走现有的保存链路（`pushUndo` → 行 update → 保存表格），
  `Ctrl+Z` 能整体撤销这一次修改。
- 弹层里 `Ctrl+A / Ctrl+C / Ctrl+X / Ctrl+V` 一律交给浏览器原生处理 ——
  **面板级快捷键在弹层打开时全部让路**（`onPanelKey` 开头加一句：弹层开着就直接 return）。

**只读模式**：`status` 不是 draft / 空时，弹层照常打开，但 textarea 设 `readonly`，
顶部加一条琥珀色提示：

    这个任务已经开工，文案不能改。要改先把它打回草稿。

**readonly 的 textarea 仍然可以选中和复制** —— 这就解决了「想复制某一段也没法复制」。
按钮区只留 `[全选复制]` 和 `[关闭]`。

### B. 修掉四条根因（就算不走弹层，其他列的编辑也要正常）

1. **CSS 排除编辑态**：第 387 行那条选择器改成
   `.panel-tasks .tabulator-row .tabulator-cell:not(.tabulator-editing)`，
   另外补一条：

       .panel-tasks .tabulator-row .tabulator-cell.tabulator-editing{
         height:auto; min-height:34px; overflow:visible;
         white-space:normal; padding:0;
       }
       .panel-tasks .tabulator-row .tabulator-cell.tabulator-editing textarea,
       .panel-tasks .tabulator-row .tabulator-cell.tabulator-editing input{
         width:100%; padding:4px 8px; line-height:1.5; user-select:text;
       }

2. **编辑时藏掉填充柄**：`placeFillHandle()` 开头加
   `if (table?.modules?.edit?.currentCell) { fillEl.style.display='none'; return; }`；
   `cellEditing` 时主动藏，`cellEdited` / `cellEditCancelled` 之后再 `placeFillHandle()`。

3. **两个 capture mousedown 开头都加豁免**：

       if (ev.target && ev.target.closest && ev.target.closest('.tabulator-editing')) return;

   填充柄那个还要**同时判元素**，不能只比坐标：
   `if (ev.target !== fillEl && !fillEl.contains(ev.target)) return;`
   坐标判定只作为辅助。

4. **编辑态下让区域选择彻底让路**：`cellEditing` 时给表格根加一个类 `tg-editing`，
   CSS `.panel-tasks.tg-editing .tabulator-range-overlay{display:none}`，
   并在 `cellEditing` / `cellEditCancelled` / `cellEdited` 里配对加删这个类。

### C. 格子里的显示

- `fmtCopy` 现在截 60 字 + `el.title = 全文`。**`title` 放两千字的原生 tooltip 很难看也很慢**，
  改成 `title` 只放前 200 字 + `…`，末尾补一行 `（双击查看全文）`。
- 保留单行省略。悬停时右侧那个 ⤢ 图标变亮。

---

## 验收

1. 双击一个**草稿行**的文案格 → 弹层打开，能看到全文；
   **鼠标拖选一段文字不会退出**，能选、能复制、能改；`Ctrl+Enter` 保存后格子和数据都更新了；
   `Ctrl+Z` 能整体撤销这次修改。
2. 双击一个**已开工行**（如 T0014）的文案格 → 弹层只读打开，顶部有琥珀色说明，
   **文字仍能选中并复制**，`[全选复制]` 可用。
3. 选中文案格直接打字 → 弹层打开且首字已经在里面。
4. 弹层底部字数和估算段数显示正确（拿 T0013 的文案对一下，应当是 11 段）。
5. 弹层开着时按 `Ctrl+F`、`Delete`、方向键 —— **面板快捷键不抢**，都作用在 textarea 里。
6. **其他列**（任务名、图片来源）内联编辑正常：双击进入后能选中文字、能拖选、不会被填充柄打断。
7. 编辑态下填充柄不可见；退出编辑后回来。
8. 28 / 31 / 33 / 39 全部点一遍：粘贴 200 行、填充柄递增、复选框拖选、查找替换、撤销、
   多行拖拽、批量删除、列宽自适应、拖行落点 —— 一条没退。跑一遍现有的
   `test_spec28*.py` / `test_spec31_ui.py` / `test_spec33_ui.py` 并贴结果。
