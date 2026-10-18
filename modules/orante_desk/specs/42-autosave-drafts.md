# 42 · 草稿一关软件就没了：自动保存 + 关闭前落盘 + 崩溃快照

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/01-tasks-panel.md`、
> `specs/28-tasks-grid.md`、`specs/30-refresh-and-spacing.md`（里面的 flush 机制）和它们的报告，
> 再读 `ui/js/panels/tasks.js` 的 `dirty` / `markDirty` / `saveTable` / `flushTable`、
> `ui/js/app.js`、`orante/main.py`（pywebview 窗口）、`orante/server.py`。
> 改前备份 `*.bak-0911save`。报告写到 `specs/reports/42-autosave-drafts.md`。中文注释。

## 现象

在 ① 任务里加了一批草稿行（还没点「保存表格」），关掉 Orante 再打开，**全没了**。

## 根因

- 草稿行只活在 Tabulator 的内存数据里。**只有点「保存表格」才会 `POST /api/tasks/rows` 落库。**
- `markDirty()` 只是把 `dirty` 置真，**没有任何自动保存**。
- `flushTable()` 确实写了，但只挂在 `window.registerFlush('tasks', …)` 上 ——
  而全仓**只有 `tasks.js` 自己在注册，没有任何地方调用它**
  （`grep -rn "registerFlush" ui/js/` 只有注册和注销两行）。spec 30 的刷新按钮本该调它。
- 没有 `beforeunload`，pywebview 关窗口也没有回调。断电/崩溃更是一点保护都没有。

## 要做的（四层，从常规到兜底）

### 1. 去抖自动保存

`markDirty()` 里挂一个 **1.5 秒**去抖：静下来就 `saveTable({ quiet: true })`。

- 正在编辑（弹层开着、单元格在编辑态）时**不触发**，等编辑结束再算。
- 正在拖行 / 拖填充柄 / 框选时不触发。
- 保存中再有改动 → 保存完再排一次，不要并发发两个请求。
- 保存失败**不清 `dirty`**，红字提示，下一次去抖再试。

### 2. 明确的保存状态指示

工具条「保存表格」按钮旁边一个状态点，和 spec 29 提示词那套一致：

    ● 未保存        琥珀色
    ⟳ 保存中        转圈
    ✓ 已保存 14:32  灰色

用户随时知道自己的东西在不在盘上。「保存表格」按钮保留（有人习惯点）。

### 3. 关闭之前一定落盘

三个入口都要接上，缺一不可：

- **`window.addEventListener('beforeunload')`**：`dirty` 为真时用
  **`navigator.sendBeacon('/api/tasks/rows', blob)`**（`beforeunload` 里 `fetch`/`await` 不可靠，
  `sendBeacon` 才保证发出去）。后端那条路由要能吃 `application/json` 的 beacon。
- **pywebview 窗口关闭**：`orante/main.py` 里给窗口挂 `on('closing')`，
  先让前端 flush（`window.evaluate_js('window.__flushAll && window.__flushAll()')`），
  拿到结果再放行关闭。留 2 秒超时，超时也放行，**不要因为保存卡住让用户关不掉软件**。
- **补上 `__flushAll`**：`ui/js/app.js` 里实现 `registerFlush` / `unregisterFlush` / `__flushAll`
  （现在只有注册方，没有调用方）。spec 30 的刷新按钮、切面板、切任务也都调它。

### 4. 崩溃兜底：本地草稿快照

断电和崩溃不会给你 `beforeunload`，所以要有快照：

- `markDirty()` 去抖 **800ms** 把当前表格的完整行数据 `POST /api/tasks/draft`，
  后端原样写 `data/draft-tasks.json`（带时间戳和一个内容哈希）。
  **写快照不做任何校验、不建任务、不分配任务号**，就是一坨 JSON，要足够便宜。
- 成功 `saveTable` 之后**删掉快照**。
- 启动时 `GET /api/tasks/draft`：如果快照存在、且里面有库里没有的行，
  ① 任务面板顶部出一条横幅：

      上次有 12 行没保存（3 分钟前）  [恢复]  [查看差异]  [丢弃]

  「恢复」把这些行合并回表格并标黄；「丢弃」删快照。**不要自动恢复**，让用户决定。
- 快照只留最新一份，别攒历史。

## 顺带一条：不要静默丢行

现在 `store.tasks` 一刷新，`dirty` 为假时会用服务端数据整个覆盖表格（约 767~778 行那段）。
加一条保护：**只要表里存在没有 `id` 的行（草稿），就不许整表覆盖**，
改成按 `_uid` 合并，草稿行原样留着。这条能挡住「后台刷新一下草稿就没了」这类更隐蔽的丢失。

## 验收

1. 加 5 行草稿，**不点保存**，等 2 秒 → 状态点变「✓ 已保存」；关掉软件重开，**5 行还在**。
2. 加 5 行草稿，2 秒内**立刻**关掉软件（抢在去抖之前）→ 重开还在（走的是 beforeunload / on_closing）。
3. 加 5 行草稿，用任务管理器**直接结束进程**（模拟崩溃）→ 重开出现恢复横幅，
   点「恢复」12 行回来，点「丢弃」快照没了、不再提示。
4. 编辑某个格子的过程中不会触发自动保存（看网络面板，编辑期间没有 `/api/tasks/rows`）。
5. 保存失败（临时把后端停掉）→ 状态点停在「未保存」，红字提示，后端恢复后下一次去抖自动补上。
6. 后台任务状态变化导致 `store.tasks` 刷新时，表里的草稿行**不会被覆盖掉**。
7. 关软件时如果后端没响应，2 秒后窗口照样关掉，不卡死。
8. 28 / 33 / 39 / 41 的功能各点一遍没退。
