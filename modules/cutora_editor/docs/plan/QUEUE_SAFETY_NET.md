# 队列预防针：一条撞墙，不许全队停摆

写于 2026-09-19。起因是你的要求：

> 如果在运行的过程中，某个编号的计划碰到红线导致不可继续，它能否跳过去先执行接下来其他的
> 编号的编程任务，这样避免大量的活儿都挤压下来，避免全部的工程都停滞浪费时间。

## 旧的行为（v1，`RUN_GROK_QUEUE.bat`）

Grok 写了 `_done\Qnn.stop` → bat 打横幅 → **整个队列停在那里**。
你在上班，剩下十几条任务一条都不跑，一晚上就废了。

## 新的行为（v2，`tools\queue_runner.py`）

一条任务撞线，只隔离这一条，队列继续往下走。做法：

1. 记下开工前的 `HEAD`
2. 跑 Grok，输出同时进屏幕和日志（**不再只写日志、屏幕一片黑**）
3. 任务结束后，只要满足下面任何一条就进「隔离流程」：
   写了 `Qnn.stop` ／ 没写 `Qnn.ok` ／ Grok 退出码不是 0
4. 隔离流程：
   - **先跑 golden 十二例**，这一步不能省
   - golden 绿 → 树是干净的，跳过这条，继续下一条
   - golden 红 → 先给 `HEAD` 打一个 `rescue/Qnn-<时间>` 标签（原始工作永远找得回来），
     未提交的改动 `git stash` 存起来，然后 `git revert --no-commit HEAD_before..HEAD`
     并单独提交一条 `queue guard: revert Qnn ...`
     - 回滚后 golden 绿 → 继续下一条
     - 回滚后 golden 还红 → **写 `_HALT`，真停**。在红基线上继续盖楼没有意义
5. 不管哪条路，都写一份 `docs\plan\queue\_blocked\Qnn.report.md`：
   .stop 原文、这一轮的提交、golden 前后输出、日志尾巴、运行器做了什么

## 为什么非要先跑 golden 才能跳过

Q30 就是活证据：它**既写了 `.stop`，又把活提交进去了**，而且基线是红的（aa3613a）。
如果只是「看见 .stop 就跳过下一条」，后面每一条任务的「golden 不许变」验收都会在
同一个红基线上炸掉，一条接一条写 `.stop` —— 表面上没停摆，实际上一晚上全是废件。
所以跳过之前必须确认基线是绿的，红了就把它的提交退掉再走。

## 三条不许越的线（运行器自己也不许）

1. **永远不重录 golden 基线**。基线变了只有一个处理方式：停下来问 Luca。
   运行器不碰 `tests\**\expected.sha256`，任何自动化、任何 Claude 会话都不许批准重录。
2. **永远不丢提交**。回滚用 `git revert`（新提交），不用 `reset --hard`；
   原始提交留在 `rescue/Qnn-<时间>` 标签上，未提交的改动留在 stash 里。
3. **永远只有一个 Grok 动这个仓库**。锁文件 `_done\_RUNNING.lock` 不变。

## 怎么把撞线的任务放回队列

1. 看 `docs\plan\queue\_blocked\Qnn.report.md`
2. 裁决写进 `docs\plan\queue\_blocked\Qnn.ruling.md`（顺手把结论追加进 `Qnn.md` 任务书）
3. 跑 `runtime\venv\Scripts\python.exe tools\queue_requeue.py Qnn`

`queue_requeue.py` **不直接改队列文件**。它往 `docs\plan\queue\_inbox\` 扔一个文件，
运行器在每轮开头把 inbox 并到队尾。这样正在跑的第一行永远不会被动到 ——
这是 v1 时代「改 grok-queue.txt 要保住第一行」那条规矩的自动化版本。
没有 `.ruling.md` 就拒绝复跑（`--force` 可以强行），因为没有裁决的复跑就是再撞一次同一堵墙。

## 接手时的自检

v2 启动时如果发现 `_done\` 里还躺着上一轮留下的 `.stop`：
先跑一遍 golden，绿 → 把它们挪进 `_blocked\` 并继续；
红 → **拒绝开工**，因为在红基线上跑新任务，每个任务的验收数字都没有意义。
这种遗留的 `.stop` 运行器不会自动回滚 —— 它不知道那一轮改了哪些提交。

## 文件清单

| 文件 | 作用 |
|---|---|
| `tools\queue_runner.py` | 全部逻辑。**运行中可以安全改** |
| `RUN_GROK_QUEUE2.bat` | 十行启动器，只负责调 python。别往里加东西 |
| `SWITCH_TO_V2.bat` | 等 v1 那一轮放锁，然后无缝接手 |
| `tools\queue_requeue.py` | 把隔离的任务放回队尾 |
| `docs\plan\queue\_blocked\` | 隔离报告 + 裁决 |
| `docs\plan\queue\_inbox\` | 运行中安全追加任务的入口 |
| `docs\plan\queue\_BLOCKED.md` | 一行一条的隔离流水账 |

## 为什么 bat 里不写逻辑

cmd 是**按字节偏移**读 .bat 的：正在跑的时候改这个文件，它会从错误的位置接着读下去，
行为完全不可预测。所以逻辑全部搬进 python —— python 是进程启动时一次性读完的，
改它不影响正在跑的那一轮，下一轮自动生效。

## 守夜人（定时 Claude 会话）

2026-09-19 建了一个每小时一次的定时任务：**CUTORA 队列守夜人（隔离任务裁决）**，
绑定你这台电脑，自动批准（不会半夜卡在「要不要允许」上）。它每小时做的事只有一件：

1. 看 `docs\plan\queue\_blocked\` 里有没有还没裁决的 `Qnn.report.md`
2. 没有 → 什么都不做，安静退出（绝大多数情况）
3. 有 → 读报告 + 任务书 + BACKLOG + 不变量清单，**回源码核实**，
   把裁决写进 `_blocked\Qnn.ruling.md`，并追加进任务书末尾，
   然后 `tools\queue_requeue.py Qnn` 把它放回队尾，运行器下一轮就会捡起来
4. 给你发一条三五行的中文消息

它被明确禁止：重录 golden 基线、碰 11 条不变量、自己写 CUTORA 代码、
启动任何 bat、push / rebase / reset --hard / 删提交。

### 它做不到的事（说清楚，别指望）

- **它启动不了运行器。** 定时会话的 shell 跑在一个 Linux 侧的沙箱里，
  你的 Windows 进程它起不了。运行器如果已经退出（队列空、或者 `_HALT`、
  或者「回滚完基线还是红」），要你手点一次 `RUN_GROK_QUEUE2.bat`。
  所以守夜人的价值窗口是「运行器还活着、但某条任务被隔离了」—— 这正是要解决的那个场景。
- **它没有我们这次对话的记忆。** 每次都是全新会话，只认仓库里的文件。
  所以 `BACKLOG.md` / `PROTECTED_INVARIANTS.md` / 任务书写得全不全，直接决定它裁得准不准。
- **它一小时才醒一次**（定时任务的最短间隔就是一小时）。
  最坏情况下一条被隔离的任务要等一小时才拿到裁决 —— 但队列这一小时里在跑别的任务，不浪费。
- **`C:\Users\Public\Desktop\Grok Bot.lnk` 我看不到。** 这个会话只挂了你那几个项目文件夹，
  C 盘系统目录不在里面，所以我无法确认那个快捷方式到底启动什么。
  而且就算能看到也不该用它当调度器 —— 「同一时间只允许一个 Grok 动这个仓库」这条规矩比自动化重要。
