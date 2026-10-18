# 87 · Grok 同账号拼单：小任务一起跑，总段数不超过账号并发

> **给执行者的工作约定**：你在 `D:\0_Dev\Orante` 里工作，Python 一律用 `.venv\Scripts\python.exe`。
> 先读 `specs/19-priority-queue.md`、`specs/77-multi-instance.md`、`specs/83-zombie-jobs.md` 及其报告，
> 再读 `orante/scheduler.py`（`Job`、`_pump`(147)、`_run`(171)、`_finish`）、`orante/locks.py`（`AccountLock`）、
> `orante/services/generate.py`（`generate()`(537) 整个函数，重点 `make_engine`(649)、提交循环(836~)、`finally: eng.close()`(1274~)）、
> `orante/engines/grok.py`（`connect`/`close`/`capacity`(415)/`submit`/`poll`/`max_conc`(252)）、
> `ui/js/panels/generate.js`（账号行「正在跑 T0050 · 队列 0」、任务行「排队中 · 前面还有 N 个」）。
> 改前备份 `*.bak-0922pack`，保持原文件换行符。不碰 `data\`。报告写到 `specs/reports/87-grok-pack-small-tasks.md`。
> **不许去 Grok 网页上试，不花生成额度。** 全部用假引擎测。

## 现场

用户 13 个任务，每个只有 **1 段**，全在同一个 Grok 账号（并发 4）。现在一个账号同时只许跑一个 Job，
所以是一个任务一个任务地排：T0050 在跑，T0051 前面还有 1 个……账号 4 个并发位只用了 1 个，**慢 4 倍**。

用户要的规则（原话意思）：**同一账号上，正在出片的段数加起来不超过账号并发（Grok 默认 4），就可以同时开下一个任务。**
每个任务 1 段 → 4 个任务一起跑；每个任务 2 段 → 2 个一起跑。**不能乱套、不能出错。**

## 为什么不能简单地"一个账号放两个 Job"

每个 Job 在自己的线程里 `make_engine()` → 连同一个 Chrome。Playwright 同步对象**只能在创建它的线程里用**，
两个线程各开一套引擎去点同一个 Grok 页面，会互相踩（铺页、选时长、上传），这正是 77 号账号锁要防的事。

## 做法：同账号共用一个引擎，由一个专门线程驱动

### A. 共享引擎 `orante/engines/shared.py`（新文件）

- `SharedEngine`：每个账号（本进程内）最多一个。内部一个**专用线程**持有真正的引擎对象（`make_engine` 造出来的那个），
  所有调用（`connect / submit / poll / capacity / adopt / attach_seg / conv_of / download...` 以及 `generate()` 里用到的任何引擎方法）
  都**排队投递到这个线程执行**，调用方阻塞等结果；异常原样抛回调用方线程。
- `EngineProxy`：发给每个 Job 的"假引擎"，接口和真引擎一样（用 `__getattr__` 转发方法，属性读取也转发）。
  每次调用时把真引擎的 `log` 临时换成**调用方 Job 的 `job.log`**，调完换回（调用是串行的，不会串台）。
- 引用计数：`acquire(acc, job)` 拿代理，`release(job)` 还；最后一个用户还掉时在引擎线程里 `close()` 并结束线程。
  `connect()` 只在第一次真正执行，之后是空操作。
- **`capacity()` 天然是全账号共享的**（真引擎 `_active` 里是所有任务在飞的段），所以每个任务的提交循环
  `while queue and eng.capacity() > 0` 不用改，就不会超发。

### B. `generate()` 用共享引擎（仅 Grok、仅可拼单的任务）

- 可拼单 = 引擎是 grok **且** 不是尾帧接龙 / 锚点接片模式（`pipeline.is_chain_mode` / `is_anchor_mode` 为假）。
  其它情况（Flow、接龙、锚点、regenerate 等）**完全走老路**，一行行为都不变。
- 可拼单时：`eng = shared.acquire(acc, job)` 替代 `make_engine(acc, job.log)`；`finally` 里 `shared.release(job)` 替代 `eng.close()`。
- `generate()` 里其余逻辑不动。**不要**把多个任务的段合并到一个循环里——每个任务照旧在自己的 Job 线程里跑自己的循环，
  只是引擎共用。

### C. 调度器准入：`scheduler._pump`

现在 `elif acc not in self.running` 一个账号只放一个。改成：`self.running` 按账号存**列表**，准入规则：

1. 账号上没有在跑的 → 照旧挑 `(prio, seq)` 最小的放行。
2. 账号上已有在跑的 → 只有同时满足下面**全部**条件才放行队首（仍按 `(prio, seq)` 挑）：
   - 在跑的和待放行的**都是可拼单的 generate Job**（按 B 节判定，读 manifest）；
   - `sum(各在跑任务的剩余段数) + 待放行任务的剩余段数 <= 账号并发`。
     剩余段数 = manifest 里状态为 `planned / failed / submitted` 的段数（开了「跑到全部出片」时 `blocked` 也算）。
   - 待放行任务的剩余段数 > 0。
   只要有一条不满足就等，**绝不插队**：队首不能放行时，后面的也不放（保持旗子顺序）。
3. 任一在跑 Job 结束时照旧 `_pump()`，会再按上面规则补人。
4. 一个任务剩余段数 ≥ 账号并发（比如 5 段）→ 它只能独占账号，和现在一样。

### D. 账号锁（跨窗口）

- 同账号拼单的这几个 Job 共用**一把** `AccountLock`：第一个 Job 真去 `acquire()`，后来加入的不再 acquire；
  最后一个离开时才 `release()`（在 `shared.py` 里和引擎一起做引用计数）。别的 Orante 窗口看到的仍是"这个账号被占着"，
  排队行为和 77 号一样。心跳由持锁的那份负责，不要出现"第一个 Job 结束了锁被放掉、但后面还有同组 Job 在跑"的情况。

### E. 界面

- ④生成 账号那一行：`正在跑 T0050 · T0051 · T0052 · T0053（4/4 段在出）· 队列 9`。
- 任务行：拼进来的任务显示「生成中」；还在排的显示「排队中 · 等空位（账号 4 段并发已满）」，
  不要再写"前面还有 N 个（T0050 在跑）"这种会误导的话（因为前面的可能一起在跑）。
- 停止某一个任务只停它自己（它的 Job 线程抛 `JobCancelled`，已提交的段照现在的规则处理），不影响同组其它任务。

## 测试 `tools/test_spec87.py`（先设 `ORANTE_DATA`；全部用假引擎，**真引擎一个都不许连**）

- 假 Grok 引擎：记录每次调用所在的**线程 id**，`capacity()` 按 `max_conc - len(_active)` 算，`poll` 过两轮就出片。
- **线程安全**：4 个 1 段任务同时跑 → 假引擎所有方法都在**同一个线程**里被调用；`connect` 只调 1 次、`close` 只调 1 次。
- **不超发**：任何时刻假引擎 `_active` 不超过 4（在 `submit` 里断言）。
- 准入：
  - 13 个 1 段任务、并发 4 → 同时在跑的 Job 最多 4 个，全部跑完，13 个都 `collected`。
  - 2 段任务 × 5、并发 4 → 同时最多 2 个。
  - 队首是 3 段、后面是 1 段、并发 4、在跑的剩 2 段 → 队首放不下时**后面的也不放**（不插队）。
  - 5 段任务 → 独占。
  - 接龙模式 / Flow 任务 → 永远独占，行为与改前一致（跑 `test_spec67`、Flow 相关旧测试不回退）。
- 日志不串台：每个任务的 `J*.log` 里只出现自己任务号的段。
- 停止其中一个：另外 3 个照常出片；锁在最后一个结束后才释放（查 `account_locks` 表）。
- 跨进程：模拟另一个窗口的等待者，同组全部结束前它拿不到锁。
- 回归：`python -m pytest tools/` 全过（已知的 `test_static_files` / `test_spec75` 两个 GBK 旧失败除外）。

## 验收

1. 用户那批 1 段的 Grok 任务点「全部生成」→ 账号行显示同时 4 个在跑，4 个一组出片，总时间约为原来的 1/4。
2. 出来的片子各归各的任务，不串、不重、不漏；每个任务日志只有自己的段。
3. 中途停掉其中一个，其余照常。
4. Flow 任务、接龙任务、多段长文案任务的行为和现在完全一样。
