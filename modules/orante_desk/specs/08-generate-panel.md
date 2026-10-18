# 规格 08 · 生成面板 `ui/js/panels/generate.js`（节点 ④）

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先完整读 `specs/00-architecture.md`，再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**，需要就拷到 `orante/legacy/`。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的、需要后端补的」写到 `specs/reports/<本文件名>.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。

后端已经有：`services/generate.py`（kind `generate` / `regenerate`，占账号；调度器保证一个账号一次一个任务、不同账号并行）、`GET /api/tasks`、`GET /api/tasks/{id}/manifest`、`POST /api/jobs`、`POST /api/jobs/{id}/cancel`、SSE 事件 `job.log` / `job.state` / `task.update{task_id,counts}` / `tasks.changed`（`store.jobs`、`store.logs` 已在前端维护，看 `ui/js/store.js`）。

## 布局

1. 顶栏：标题 `生成`；右侧 `全部生成`（primary：对所有 status ∈ {arranged, generating, collected 且有 planned/failed 段} 的任务各起一个 generate job）、`停止全部`（取消所有 running/queued 的 generate job）、视图切换 `进行中 | 过时 | 回收`。
2. 任务列表（不是 Tabulator，用普通表格/卡片行，因为每行要嵌进度条）。每行：
   - 复选框
   - 引擎色条（Flow 青 / Grok 金）+ 任务 id + 任务名 + 账号名
   - **段进度条**：一段一个小格（宽 6px，高 14px，间隔 1px，最多 60 格，多了按比例合并），颜色：planned 灰 / submitted 蓝闪 / collected 绿 / failed 红 / blocked 橙。数据来自 `counts`；细粒度来自 `GET /api/tasks/{id}/manifest`（首次展开时拉一次，之后靠 `task.update` 事件更新计数；展开态每 10 秒重新拉一次 manifest）。
   - 文字：`12 / 47 已收`，`失败 2`，`被拒 1`
   - 状态 tag；正在跑的显示 job id 和 `停止` 按钮
   - 展开箭头 → 展开区：该任务最近 40 行日志（`store.logs[jobId]`），以及每段一行的小表（s / 时长 / 图 / 状态 / 错误）
3. **右键菜单**（自绘一个 `<ul>` 浮层，点击其他地方关闭）：开始生成 / 停止生成 / 重生失败的段 / 重生被拒的段（先跑 `refill`，再 `generate`）/ 重生选中段…（弹小窗输 `3,7,12-15`）/ 打开任务文件夹 / 移到过时 / 移到回收 / 恢复到进行中。
4. 底部：`已选 N` + `生成选中` + `停止选中`。

## 规则

- 「过时」= `status === 'finished'`；「回收」= `trash`。自动归档由后端做（收片面板检测到「字幕成品」→ finished），这里只提供手动移动：`POST /api/tasks/{id}/status {status}`。
- 同一账号多个任务点「全部生成」：直接全部 POST，调度器自己排队；界面上排队的显示 `排队中（前面还有 N 个）`——N 用 `store.jobs` 里同 account_id 且 status=queued 的数量算。
- 每个账号的当前状态用一行小字显示在顶栏下：`TIK-L1 · 正在跑 T0003 · 队列 2`。
- 没有任何 arranged 任务时，主区显示空态：「先到 ③ 编排 把配图轨应用一下」，带按钮跳到编排节点（`ui.node='track'`）。

## 验收
- 两个不同账号的任务同时「全部生成」，两条进度条同时在动；同一账号的第二个任务显示排队。
- 停止后再开始，已 collected 的格子保持绿色，只补剩下的。
- 右键菜单在窗口边缘不溢出。
