# 39 · 任务表格两个回归：拖行有偏移 · 表宽写死不随窗口变

> **给 Grok 的工作约定**：先读 `specs/28-tasks-grid.md`、`specs/33-grid-rows-and-polish.md` 和它们的报告，
> 再读 `ui/js/panels/tasks.js` 的 `buildTable()`（约 1518 行起）、`columns()`（约 860 行起）、
> `onRowMoving / onRowMoved`，以及 `ui/css/app.css` 的 tabulator 段。
> 只改 `tasks.js` 和 `app.css`。改前备份成 `*.bak-0910fit`。报告写到 `specs/reports/39-grid-drag-and-fit.md`。中文注释。
> 28 / 31 / 33 做过的功能一条都不许退。

---

## 1. 表宽写死，不随窗口自适应

### 根因

`buildTable()` 里 `layout: 'fitColumns'` 是对的，但 `columns()` 给**每一列**都塞了硬 `width`
（`width: w('copy', 280)` 这种），而且用户拖过一次列宽后，`columnResized` 把宽度存进
`ui.tasks.colwidths`，下次建表又原样塞回去。Tabulator 的规则是：**有显式 `width` 又没有
`widthGrow/widthShrink` 的列，fitColumns 不会去动它**。结果整张表 = 各列固定宽度之和，
窗口拉宽拉窄都不跟着变。另外 `height: 'calc(100vh - 330px)'` 也是写死的。

### 改法

1. **非冻结列一律去掉硬 `width`**，只留 `minWidth` + 权重：

       copy          widthGrow: 4
       image_source  widthGrow: 2
       name          widthGrow: 1
       account       widthGrow: 1
       其余（type / priority / duration / status）  widthGrow: 0, widthShrink: 1

   冻结的三列（`_sel` / `id` / `name`）保持固定，`name` 例外：冻结但允许 grow。
2. `ui.tasks.colwidths` 只在用户**手动拖过**那一列时才生效，并且存的是**比例**不是像素
   （`该列宽 / 表总宽`），建表时按当前表宽换算回去。这样换个窗口尺寸也不会爆。
   工具条加一个「重置列宽」小按钮，清掉这个设置。
3. 容器 `.panel-tasks .tabulator{ width:100%; min-width:0 }`；外层 section 改成
   `display:flex; flex-direction:column`，表格 `flex:1; min-height:0`，
   Tabulator 的 `height` 改成 `'100%'`。**去掉 `calc(100vh - 330px)`**。
4. 确认 `autoResize: true`（默认就是），另外在容器上挂一个 `ResizeObserver`，
   容器尺寸一变就 `table.redraw(true)` —— 窗口没变但侧栏/抽屉开关导致的宽度变化，
   `autoResize` 是听不到的。
5. 表比窗口宽时（列的 `minWidth` 之和超过容器）才出横向滚动条，否则**不许出现横向滚动条**。

---

## 2. 拖行时有偏移

### 根因

用的是 Tabulator `movableRows` 自带的「把整行克隆一份跟着鼠标走」的幽灵行。
它用 `position:absolute` 相对表体定位，而我们这张表同时有**冻结列**（三列在另一层）、
**虚拟滚动**、行上还有 `box-shadow:inset` 的色条 —— 幽灵行只克隆了非冻结那一半，
定位又没算冻结区的宽度和滚动偏移，看起来就是「拖的东西不在鼠标底下」。
和 Tabulator 硬磕这个定位不值得，而且以后升级还会再坏。

### 改法：不要它的幽灵行，自己画落点线

1. CSS 把 Tabulator 的幽灵行藏掉：`.tabulator-row.tabulator-moving{ opacity:0 !important; pointer-events:none }`。
   Tabulator 的**排序逻辑保留**（`rowMoved` 照常触发、`row_order` 照常存）。
2. 自己画两样东西（都 `position:fixed`，直接用 `clientX/clientY`，和表格内部定位无关）：
   - **落点线**：2px `--accent` 横线，宽度 = 表格可视宽度，贴在目标行的上边缘或下边缘
     （鼠标在目标行上半部就贴上边，下半部贴下边）。`rowMoving` 期间跟着 `mousemove` 更新。
   - **跟手小标签**：一个小胶囊跟在光标右下 12px 处，写 `T0014 李星辰v01`，
     多行拖拽时写 `3 行`。半透明背景 + `backdrop-filter: blur(6px)`，和 33 号填充气泡同一套样式。
3. 被拖的原行（以及多行拖拽时所有勾选行）本身 `opacity:.4`，松手恢复。
4. **从把手按下时要先 `preventDefault` 并阻断区域选择**：现在 `holder` 上那个
   `mousedown` 的 capture 监听会同时把它当成框选的起点，拖行和框选叠在一起也是
   「偏移感」的来源之一。把手（`rowHandle` 那一列的左 14px）上按下 → 只走拖行，不走框选。
5. 松手落点 = 落点线所在的位置，**不是 Tabulator 自己算的位置** —— 在 `rowMoved` 里用
   我们记录的目标行和上下边把结果校正一次（`row.move(target, above)`），确保
   「线画在哪儿，行就落在哪儿」。
6. `Esc` 取消拖拽，线和标签立刻消失，行回原位。

---

## 3. 验收

1. 把窗口从最大化拉到一半宽再拉回去：表格始终占满面板宽度，**没有横向滚动条**，
   文案列吃掉多余宽度，小列不动。
2. 打开/关闭右侧抽屉（账号 / 设置）→ 表宽跟着重新分配，不用手动刷新。
3. 拖某一列改宽 → 重开软件后**比例**保留；点「重置列宽」恢复默认。
4. 窗口高度变化时表格高度跟着变，底部没有一截空白或被截断。
5. 拖行：小标签始终在光标右下角，落点线在鼠标所指的行边缘，**松手后行落在线画的位置**，
   连做 10 次一次不偏。有冻结列、滚动到中间、紧凑档三种情况各试一遍。
6. 从把手按下拖行时**不会同时拉出一个框选区域**。
7. `Esc` 取消拖拽正常。
8. 28 / 31 / 33 的功能各点一遍：粘贴、填充柄、复选框拖选、查找替换、撤销、多行一起拖、批量删除。
