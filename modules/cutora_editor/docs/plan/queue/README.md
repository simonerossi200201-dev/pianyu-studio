# 任务队列编号规则

**队列编号用 `Q`，里程碑编号用 `T`。两套编号互不相干。**

以前两边都用 `T`，到 2026-09-18 已经撞车：队列里的 T29/T30 是「句子页空白」和「空间立体声」，
而里程碑 T29/T30 是「时间线专业化 II」和「关键帧系统」—— 完全不同的东西。
所以队列全部改成 `Q`。

- `Q01`–`Q09`：已完成（`_done/T01.ok` … `T09.ok` 是改名前留下的，不动）
- `Q10` 起：待执行，顺序见 `_order.txt` 和 `tools/grok-queue.txt`
- 任务书标题里若写「Qnn · Tmm 某某」，`Tmm` 是它对应的**里程碑**编号（例如 `Q16 · T33 轨道属性`）

Grok 写标记文件时用队列编号：`_done/Qnn.ok` / `_done/Qnn.stop`。

## 运行器（2026-09-19 起）

平时点 `RUN_GROK_QUEUE2.bat`（逻辑在 `tools\queue_runner.py`）。
旧的 `RUN_GROK_QUEUE.bat` 保留只为了「万一 python 侧出问题还有退路」，
它的行为是撞线即全队停摆，不要再当主力用。

- 撞线处理、回滚规则、红线：`docs\plan\QUEUE_SAFETY_NET.md`
- 运行中往队列加任务：`tools\queue_requeue.py`，或直接往 `_inbox\` 扔一个每行一条路径的
  txt。**任何情况下都不要手改 `tools\grok-queue.txt` 的第一行**
- 被隔离的任务：`_blocked\Qnn.report.md`（报告）+ `_blocked\Qnn.ruling.md`（裁决）
