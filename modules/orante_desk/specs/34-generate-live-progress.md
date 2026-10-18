# 34 · ④ 生成：进度条不展开就不更新

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/00-architecture.md`、
> `specs/08-generate-panel.md`，再读 `ui/js/panels/generate.js` 和
> `orante/services/generate.py`、`orante/tasks.py`、`orante/server.py`。
> 改前备份成 `*.bak-0910live`。报告写到 `specs/reports/34-generate-live-progress.md`。中文注释。

## 现象

④ 生成里那排小方块进度显示做得挺好看，但**不展开就不刷新**：
任务明明已经生成完了，方块还是旧的颜色，非要手动点右边展开一下才全变绿。

## 根因（已定位）

`ui/js/panels/generate.js`：那排方块画的是 `t.counts`，
而 `t.counts` **只有 `loadManifest(tid)` 会写**（第 573~584 行）。
`loadManifest` 的四个调用点，全都挂着「展开」这个条件：

    onMounted        只对 status 是 prepared / described 的任务拉一次
    toggleExpand     展开时拉                              ← 就是它
    setInterval 10s  Object.keys(expanded).forEach(...)     ← 只拉展开的
    watch refreshSeq Object.keys(expanded).forEach(...)     ← 只拉展开的

所以**折叠着的行，counts 永远停在最后一次加载时的样子**。
第 863 行那个 `store.tasks` 的 watch 虽然能感知到任务变了，但它只重排队列和优先级，
**没有重新拉 counts**。

## 要做的

### 1. 根治：counts 由后端随任务列表一起给出

前端不该为了一个进度条去逐个拉 manifest。改成后端算好一起发：

- `orante/tasks.py::list_all()`（或 `/api/tasks` 那条路由）给每个任务补一个 `counts`：

      {"planned":N,"submitted":N,"collected":N,"failed":N,"blocked":N,
       "total":N,"images_ready":bool}

  数据源就是任务目录里的 `manifest.json`。
- **按 manifest 的 (size, mtime) 缓存**，没变就不重读。几十个任务也不会有明显开销。
- 前端 `loadManifest` 里那段自己算 counts 的代码删掉，
  改成直接用 `t.counts`；`loadManifest` 只在展开时拉**段落明细**用。

这样只要 `store.tasks` 刷新，**所有行**（展开的、折叠的）的方块一起更新。

### 2. 让它真正「实时」：段落状态一变就广播

现在 `generate.py` 只在开跑和跑完时 `events.emit("tasks.changed")`，
中间十几分钟一片安静。加一个轻量事件：

- 每当某一段的 `status` 变化（`planned→submitted`、`submitted→collected`、
  `→failed`、`→blocked`）并写盘之后，发一条：

      events.emit("seg.changed", {"task_id": tid, "counts": {...}})

  **只带 counts，不带整个 manifest**，一条几十字节。
- 前端收到就地更新那个任务的 `counts`，方块当场变色。
  **不要整页重刷**，不要打断用户正在做的事。
- 节流：同一个任务 500ms 内最多发一条（一波收片可能几秒内连着好几段）。

### 3. 兜底：收尾那一下不能丢

「生成完了却不全绿」最常见的就是**最后一次更新恰好丢了**（SSE 抖动、前端刚好切走）。
所以：

- Job 结束时（`done` / `failed` / `cancelled`），**无条件**再发一次
  `seg.changed` + `tasks.changed`。
- 前端在 `store.jobs` 的 watch 里，发现某个任务的 job 从运行变成结束 →
  **强制拉一次该任务的 counts**（不管展不展开）。
- 10 秒那个轮询保留，但改成：**只轮询「有 running job 的任务」**，
  展不展开都轮；没有在跑的任务一个都不轮。
  比现在「只轮展开的」既更准，开销又更小。

### 4. 顺手把「看起来在跑」这件事做实

- 有 running job 的那一行，进度条右边显示 `12 / 33`（已收 / 总数）和一个细进度条。
- 正在提交的那几个方块用**呼吸动画**（`submitted` 状态），
  和已收的实心绿区分开 —— 现在光看颜色分不出「在跑」和「卡住」。
- 一行如果 **90 秒没有任何段落状态变化**，在行尾显示一个琥珀色小字
  `90 秒没动静`，点一下能展开看日志。**卡住要看得见**，这比全绿灯还重要。

## 验收

1. 起一个 9 段的生成任务，**全程不展开任何一行**：
   方块从灰逐个变绿，最后**自动**全绿，`12/33` 这样的计数跟着走。不用手点。
2. 生成过程中切到 ⑤ 收片再切回来 → 进度是最新的，没有停在切走那一刻。
3. 故意在中途重启后端（模拟 SSE 断线）→ 重连后 2 秒内进度追上真实状态。
4. Job 结束的瞬间就全绿，不需要等下一次轮询。
5. 打开开发者工具看网络：**没有在跑的任务时，不该有任何 manifest 轮询请求**。
6. 把一个任务的 Grok 卡住不动 → 90 秒后那一行出现「没动静」提示。
7. 20 个任务的列表，`/api/tasks` 的响应时间和改之前对比，写进报告（缓存要真的生效）。
