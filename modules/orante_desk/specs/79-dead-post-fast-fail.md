# 79 · post 已经死了就赶紧认，别每次重启都再等 15 分钟

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/50-grok-post-not-found.md`、
> `specs/51-grok-resume-collect.md`、`specs/58-run-to-completion.md`、`specs/61-void-redownload-loop.md`
> 和它们的报告，再读 `orante/engines/grok.py`（第 260~266 那几个 dict、`adopt`(509)、
> `_poll_one`(566)、`_page_of`(944)、`_close_tab`(1050)）、
> `orante/legacy/grok_pipe.py`（`goto_own_post`、`probe`、`IS_GENERATING_JS`、`IS_FAILED_JS`）、
> `orante/services/generate.py`（第 126 行 inflight 续跑、755~768 超时分支、`GEN_TIMEOUT`、`apply_timeout`），
> 再读本规格。改前备份 `*.bak-0920deadpost`。报告写到 `specs/reports/79-dead-post-fast-fail.md`。中文注释。

## 现场

Grok 任务，11 段收了 9 段，剩 s001 卡住。用户等了 5 分钟全是同一行：

    [15:13:31]  … 在生成 1 条，待提交 0，已收 9
    …（每 8 秒一条，一模一样）…
    [15:18:12]  ↩ s001 被 Grok 踢回首页（第 1 次）：Post not found，地址 https://grok.com/imagine
    [15:18:12]  · s001 不在详情页（https://grok.com/imagine），本轮不数首页视频
    [15:18:20]  · 24c71865 标签页丢了，已按会话重开
    [15:18:29]  … 在生成 1 条，待提交 0，已收 9
    [15:18:37]  … 在生成 1 条，待提交 0，已收 9

用户只能停掉 Job，到「缺片」里重生——**这个办法是对的**，但不该需要人工介入。

## 四个洞，一个一个堵

### 洞 1 ·「在详情页但里面什么都没有」是完全静默的

50 号处理了"被踢回首页"，51 号加了"看见 N 个 video 但没一个过闸"的日志。
**但 post 被删掉之后，页面打得开、上面 0 个 video、也不在生成、也没有失败字样**——
`probe` 返回"未知"，`_poll_one` 返回 pending，**一个字都不打**，直到 `GEN_TIMEOUT`（15 分钟）。
51 号那条日志的条件是"页面上确实有 `<video>`"，0 个的时候根本不触发。

**要做的**：

- `self._empty_rounds: dict[str, int]`。在详情页（`on_post` 为真）且 `probe` 既不是 READY 也不是
  FAILED、`detail` 里 `videos` 数为 0、`IS_GENERATING_JS` 也为假 → 计数 +1；
  任何一轮出现 video、出现"生成中"、或拿到地址 → 归零。
- **每 5 轮打一条**（不管有没有 video，51 号那条的条件放宽到"总是打"）：

      · s001 详情页开着，0 个 video，不在生成（第 15 轮）

- 连续 **`EMPTY_STOP = 20` 轮**（按 `POLL_EVERY=6` 约 2 分钟）→ 判失败：

      {"state":"failed","error":"post 打得开但里面没有视频（多半已被删或被拒）"}

  `_active.discard` + `_close_tab`，交给 `generate.py` 现有的失败分支去重生 / 换图。

### 洞 2 · `_page_of` 里的"被踢"不算数

`_page_of`（944）发现标签页没了就新开一页、**自己调一次 `goto_own_post`**，
返回 `"bounced"` 时把页留着（注释写明是"让 `_poll_one` 去计次数"），
**但它没有把这次被踢记进 `self._bounced`**。

于是这一次被踢是隐形的。再叠上 `_hold_nav`（被踢一次之后下一轮不导航），
50 号"连续 2 次被踢就判失败"这条快速失败的路径就被绕开了——
日志里只看得到"第 1 次"，永远等不到"第 2 次"。

**要做的**：`_page_of` 把 `nav` 的结果返回给调用方（返回 `(page, nav)`，或者直接在里面
`self._bounced[post] = self._bounced.get(post,0)+1` 并打同样格式的日志）。
选哪种都行，但**必须保证：一次被踢 = 计数 +1，不管是谁触发的导航**。
计到 2 就按 50 号判失败。

### 洞 3 · `adopt()` 把证据清零，于是每次重启都重新等 15 分钟

这是"死循环"感觉的真正来源。`adopt()`（509）最后三行：

    self._submitted_at[gid] = time.time()
    self._bounced[gid] = 0
    self._hold_nav.discard(gid)

续跑接手时**把等待时钟和被踢计数全部归零**。`generate.py:126` 又会把上一个 Job 留下的
`submitted` 段直接接过来轮询。所以：

> 一个早就被 Grok 删掉的 post，**每重开一次 Job 就再获得一个全新的 15 分钟**，
> 而且被踢计数也从 0 开始。用户重启十次就是十个 15 分钟。

`_submitted_at` 归零本身是对的（51 号的理由成立，不要改）。**错的是证据不落盘。**

**要做的**：把"这个 post 有多不对劲"记进 **manifest**，跨进程、跨重启都还在：

    seg["gen_health"] = {"bounced": 2, "empty_rounds": 18, "first_bad_at": "<ISO 时间>"}

- `adopt()` 从 `seg` 里把它读回来填进内存的 `_bounced` / `_empty_rounds`，**不再无脑清零**。
- 每次计数变化就写回 `seg`（`generate.py` 那边已经有 `save()`，跟着它走，别自己存盘）。
- 收到片子 / 重新提交（拿到新的 `gen_id`）→ 整个 `gen_health` 删掉。
- 这样：第一次跑攒到 1 次被踢，重启之后再被踢一次就是第 2 次，**立刻判失败**，而不是从头再数。

### 洞 4 · 续跑的段应该有更短的绳子

全新提交的段等 15 分钟是合理的（Grok 本来就慢）。但**续跑接手的段**已经等过一轮了，
再给满 15 分钟没有道理。

**要做的**：`generate.py` 里区分两种：

- 新提交：`GEN_TIMEOUT`（15 分钟）不变。
- **`adopt` 接手的**：`ADOPT_TIMEOUT = 5 * 60`。`seg["gen_health"]["first_bad_at"]` 存在的话，
  超时从**那个时刻**算起，而不是从接手那一刻。
- 超时文案分开，让人看得懂：`等了太久没出片（续跑接手，已累计 8 分钟）`。

## 顺带：日志要能回答"它到底在等什么"

现在那行 `… 在生成 1 条，待提交 0，已收 9` 重复几百遍，**信息量为零**。

- 这一行在**内容没变**的时候，从每轮一条改成**每 5 轮一条**，并且把在等的段号带上：

      … 在生成 1 条（s001，已等 4 分 12 秒），待提交 0，已收 9

- 状态一有变化（收到片、失败、被踢、段数变了）立刻打，不受 5 轮限制。

## 测试 `tools/test_spec79.py`（FakePage，**先设 ORANTE_DATA**）

- 详情页 0 个 video、不在生成 → 第 5/10/15 轮各打一条日志（正好 3 条），第 20 轮返回 `failed`，
  error 含「没有视频」；标签页被关掉。
- 中途某一轮出现一个 video → `_empty_rounds` 归零，不再判失败。
- `_page_of` 触发的 bounce **计进 `_bounced`**：FakePage 让第一次 `_page_of` 就被踢 →
  `_bounced` 为 1，日志格式和 `_poll_one` 里那条一致。
- 被踢累计到 2（**一次来自 `_page_of`，一次来自 `_poll_one`**）→ 判 failed，error 含「找不到这个 post」。
- `gen_health` 落盘：被踢一次 → `seg["gen_health"]["bounced"] == 1`；
  新建引擎实例 + `adopt(seg)` → 内存里的 `_bounced` 是 1（**不是 0**）；再被踢一次 → 立刻 failed。
- 收到片之后 `gen_health` 从 seg 里消失。
- 续跑接手的段：`ADOPT_TIMEOUT` 5 分钟生效；`first_bad_at` 是 8 分钟前 → 立刻超时，
  文案含「续跑接手」。
- 全新提交的段仍然按 15 分钟。
- 汇总行：内容不变时每 5 轮一条；`已收` 数字一变立刻打。
- 回归：`python -m pytest tools/` 全过，`test_spec50` / `test_spec51` / `test_spec58` 不回退。

## 验收

1. 人为造一个死 post（提交后在 Grok 网页上把那条删掉）：日志在 **2 分钟内**出现
   `详情页开着，0 个 video，不在生成` 然后判失败并自动重生，**不是**干等 15 分钟。
2. 同一个死 post，被踢一次之后**停掉 Job 再重开**：接手时日志写明
   `接手 s001，之前已被踢 1 次`，再被踢一次就立刻失败——不是重新从 0 数。
3. 正常的段不受影响：T00xx 跑一遍，收片数和以前一样，没有误判失败。
4. 汇总行不再每 8 秒刷一条一模一样的，而是每 5 轮一条并带上段号和已等时长。
