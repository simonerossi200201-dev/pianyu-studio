# 83 · 僵尸 Job：8 天前的一条"排队中"挡住了套装切换

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/19-priority-queue.md`、
> `specs/25-status-monotonic.md`、`specs/76-template-suites.md`、`specs/77-multi-instance.md`
> 和它们的报告，再读 `orante/scheduler.py`（`submit`(92 的 INSERT)、`cancel`(101，**已经有"库里僵尸"的分支**)、
> `list`(137)、`_finish`、`stuck_jobs`(270)、`_job_last_ts`）、`orante/services/suites.py`
> （`_active_jobs`(248)、`switch`(300)）、`orante/locks.py`（`live_holders`、心跳常量）、
> `orante/db.py`（`jobs` 表结构、迁移写法）、`orante/main.py`（启动流程 855 附近）、
> `ui/js/parts/suites.js`（那个"先停下来再切"的对话框），再读本规格。
> 改前备份 `*.bak-0921zombie`。报告写到 `specs/reports/83-zombie-jobs.md`。中文注释。

## 现场

用户把所有任务都标完成了，点「切换」到「哑巴视频」套装，弹框：

    有 1 个任务正在生成，提示词现在换掉会影响正在出的片子。先停下来再切。
    [ 取消 ]  [ 去 ④ 生成 ]

去 ④生成 看，什么都没在跑。**他没有任何办法知道是哪个任务、怎么让它停。**

## 查到的根因

库里 `jobs` 表有一行：

    J00204 · T0021 · generate · status=queued · started=2026-09-13 01:25:09 · finished=NULL

- 这条记录**已经排了 8 天队**。当时那个 Orante 进程早就没了，这行一直没人改。
- `data/logs/` 里**没有 `J00204.log`**（J00203 之后直接是 J00205）——它从来没真正开始跑过。
- 它对应的任务 **T0021 早就进回收站了**（`status='trash'`）。
- `account_locks` 是空的，不是锁的问题。

它是怎么一路活下来、最后挡住套装切换的：

1. **启动时没人清理**。`scheduler` 只管本进程内存里的 Job，库里的遗留行没人看。
2. **`stuck_jobs()` 只查 `status='running'`**（第 277 行）。这行是 `queued`，所以生成面板那个"卡住的 Job"提示也从来没出现过。
3. **`suites._active_jobs()` 只看状态**：`WHERE status IN ('queued','running','halted')`，
   **不看任务是不是已经在回收站 / 已完成，也不看这行还有没有人认领**。
4. **弹框不说是谁**：只报"有 1 个任务"，不给任务号、不给清理入口。用户只能干瞪眼。

## 要做的

### A. `jobs` 表能看出"这行还有没有主人"

加三列（`db.py` 按现有 migration 写法，旧库自动补列，旧行填 NULL）：

    owner_pid   INTEGER   -- 登记这行的 Orante 进程
    owner_port  INTEGER   -- 那个进程的端口（77 号多开用）
    beat        REAL      -- 最后一次心跳（time.time()）

- `scheduler.submit` 插入时写 `owner_pid` / `owner_port` / `beat`。
- 本进程每 **20 秒**给自己名下所有 `queued` / `running` / `halted` 的 Job 刷一次 `beat`
  （一条 UPDATE 搞定：`WHERE owner_pid=? AND status IN (...)`），和 `locks.BEAT` 同频。
- 判定"没主人"：`beat` 为 NULL（旧数据），或者 `now - beat > 90 秒`（和 `locks.STALE` 同一个数，别另起常量）。

### B. 启动时收尸

`main.py` 起服务之前（和 `auto_archive_on_startup` 放一起），调一次 `scheduler.reap_orphans()`：

- 把所有**没主人**的 `queued` / `running` / `halted` 行改成 `status='lost'`，
  `finished=now`，`error='程序关闭时还没做完（上次运行遗留）'`。
- **只收没主人的**。77 号多开时，别的窗口活着的 Job 心跳是新鲜的，绝对不能动它们——这一条写进测试。
- 对应任务如果是 `generating` 状态、而且这时库里已经没有它的活 Job → 状态回退成 `arranged`
  （按 25 号的单调规则走，别直接写库）。
- 收了几条就打一行启动日志：`收拾了 1 条上次遗留的 Job（J00204 · T0021）`。
- `lost` 这个状态要在前端各处（Job 列表、任务行）当成"已结束"来显示，别当成活的。

### C. "正在生成"的判定要讲道理

`suites._active_jobs()` 以及**所有**"有没有 Job 在跑"的判断（生成面板、归档、完成、切套装）统一走一个
新函数 `scheduler.live_jobs()`，它**只算**：

- 本进程内存里 `queued` / `running` 的；
- 库里**有主人**（心跳新鲜）的 `queued` / `running` / `halted`；
- 77 号 `locks.live_holders()` 里的（这部分已经有心跳，照旧）。

**不算**：没主人的行；`lost` / `cancelled` / `done` / `failed`；
**任务已经在回收站或已完成（`trash` / `finished`）的**——那种任务不可能还"在出片"。

grep 断言：`suites.py` 里不再有自己写的 `WHERE status IN ('queued','running','halted')`，
只调 `scheduler.live_jobs()`。

### D. 拦人的时候要说清是谁、并给出路

切套装被拦时，对话框**列出每一个挡路的 Job**：

    有 1 个任务还在跑，提示词现在换掉会影响正在出的片子。

      T0021 · 认真0912（在回收站）   generate · 排队中 · 8 天前   [ 清理掉 ]

      [ 取消 ]   [ 去 ④ 生成 ]

- 每行带任务号、任务名、任务当前状态、Job 类型、Job 状态、**多久以前**。
- 这一行**看起来不对劲**（任务在回收站 / 已完成，或者没主人，或者排队超过 1 小时）时，
  行尾给一个<span class="btn-ref">清理掉</span>，点了调现成的 `POST /api/jobs/{id}/cancel`
  ——`scheduler.cancel` 第 101 行起**已经有"库里僵尸"的分支**，会把状态改成 `cancelled` 并放锁，直接复用。
- 清理完如果已经没有挡路的，对话框自动变成"现在可以切了"并**直接切过去**，不用再点一次。
- 正常活着的 Job 不给"清理掉"，只给"去 ④ 生成"。
- 同样的列表格式也用在：批量完成（73 号）、自动归档、多开下关窗口这些所有会被"有 Job 在跑"拦住的地方。

### E. `stuck_jobs()` 也看 `queued`

生成面板的"卡住的 Job"提示现在只查 `running`。改成也查**排队超过 30 分钟且没主人**的 `queued`，
这样这种东西以后在生成面板上就能被看见，不会埋 8 天。

## 测试 `tools/test_spec83.py`（**先设 ORANTE_DATA**）

- 旧库（`jobs` 没有新三列）启动 → 自动补列，旧行三列为 NULL，不报错。
- 造一行 `queued`、`beat=NULL` → `reap_orphans()` 后变 `lost`，`error` 含「遗留」；日志含任务号。
- 造一行 `running`、`beat=now-10`、`owner_pid=别的进程` → `reap_orphans()` **不动它**（多开保护）。
- 造一行 `running`、`beat=now-300` → 被收。
- 心跳：提交一个 Job，推进时间 20 秒 → `beat` 被刷新。
- `live_jobs()`：
  - 没主人的 `queued` 不算；
  - 任务在 `trash` 的不算（**就是 J00204 这种**）；
  - 任务 `finished` 的不算；
  - 本进程内存里活着的算；别的窗口心跳新鲜的算。
- `suites.switch`：库里只有一条 T0021 那样的僵尸 → **能直接切**，不被拦。
- 被拦时返回的 `extra` 里每个 Job 带 `task_id` / `task_name` / `task_status` / `kind` / `status` / `age_sec` / `stale:bool`。
- 对僵尸调 `cancel` → 变 `cancelled`（回归 `scheduler.cancel` 的现有分支）。
- `stuck_jobs()` 能报出"排队 40 分钟且没主人"的 `queued`。
- grep：`suites.py` 里没有自写的状态过滤 SQL。
- 回归：`python -m pytest tools/` 全过，`test_spec19` / `test_spec25` / `test_spec76` / `test_spec77` 不回退。

## 验收

1. 装上之后**重启一次 Orante**：启动日志出现「收拾了 1 条上次遗留的 Job（J00204 · T0021）」。
2. 右上角切套装 → **直接能切**，不再弹框。
3. 人为造一个真正在跑的任务，再去切套装 → 弹框列出那个任务的号和名字，**没有**"清理掉"按钮，只有"去 ④ 生成"。
4. 开两个 Orante 窗口，A 窗口在跑，B 窗口重启 → A 的 Job **没有被 B 收掉**，照常跑完。
