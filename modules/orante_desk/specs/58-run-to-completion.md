# 58 · 大面积被拒也要跑到全部出片：自动接力 + 换参考图 + 最后借图兜底

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/17-upload-rejected.md`、
> `specs/24-gapfill-triage.md`、`specs/47-expand-before-describe.md` 和它们的报告，再读
> `orante/services/generate.py`（第 25/30 行 `MAX_AUTO_REFILL` / `MAX_AUTO_REFILL_TASK`、
> 第 123 行 `todo`、第 239~284 行 `try_auto_refill`、第 285~300 行 `maybe_block_for_fails`、
> 第 512 行收尾那句）、`orante/services/expand.py`（第 819 行 `refill_one`、第 1035 行 `refill` job、
> `preview_ref_p`）、`orante/services/pipeline.py::pin_image`、
> `ui/js/panels/generate.js`（第 192 行 `waitRefill`、第 1140 行 `redoBlocked`、第 1284 行自动接力）、
> 再读本规格。**先做完 57。** 改前备份 `*.bak-0912complete`。
> 报告写到 `specs/reports/58-run-to-completion.md`。中文注释。

## 现象

T0021 这一轮（16:03~16:05）：`本轮：提交 7，收片 7，失败 0，被拒 5（已自动换图 3，放弃 1），等前置 5`。
其中 p07 走到了头：

    ✗ p07.jpg 被 Flow 拒收（中性图可传、这张图任何尺寸都不可）→ 换一张图
    ✗ s019 图 p07 被 flow 拒了 → 标 blocked
      p07 自动换了 3 次仍被拒，先放着

**s019 现在停在 `blocked`，而且它不会自己回来。** 用户要的是「哪怕大面积被拒，最后也要全部出片」，
现在做不到——有三道天花板，都是终点而不是减速带。

## 三道天花板

| 位置 | 规则 | 触到之后 |
|---|---|---|
| `generate.py:25` | `MAX_AUTO_REFILL = 3`：**一张图**最多自动换 3 次 | `先放着`，图和段都留在 blocked |
| `generate.py:30` | `MAX_AUTO_REFILL_TASK = 4`：**一轮里**成功换了 4 张还在被拒，就整轮停止自动换图 | 剩下的全标 blocked |
| `generate.py:123` | `todo` 只收 `planned` / `failed`；`blocked` 要 `redo=True` 才收 | 再点「开始生成」也不管它们 |

唯一的出口是**手动**：④ 右键任务 →「重跑被拒的」→ 跑 `refill` job（每张图再试 3 轮），
UI 那边 `waitRefill` 会在 refill 完成后自动接一次 generate。但 refill 里那 3 轮也用完之后
（`✗ pXX 补了 3 轮还是被拒或空镜，仍标记 blocked`），就彻底没人管了。

**`MAX_AUTO_REFILL_TASK` 这道闸不要拆。** 它防的是「被拒判断本身错了」——那种情况下猛换图
会把整个图池毁掉，旧图虽然在 `images/_replaced/` 但没人想去捡。要改的是：
触到之后不能是终点，而是换一种策略继续。

## 要做的

### A. 「跑到全部出片」开关（任务级，默认开）

- 任务上新增 `run_to_completion`（① 任务表一列，或 ④ 任务卡片上一个开关，默认开）。
- 打开时，generate job **收尾前**自己判断：还有 `blocked` 段吗？有就**自动接力**，不用人点右键：
  `generate → refill → generate → …`，由后端排队，别靠前端 `waitRefill`
  （前端那条链关掉窗口就断了）。
- **接力有总预算**，不是无限循环：`ROUND_MAX = 5` 轮，且**每轮必须有进展**
  （blocked 段数比上一轮少，或有新段收片）。连续 2 轮零进展 → 停，日志写明
  `连续 2 轮没有进展，剩 N 段仍被拒，停下来等你看一眼`。
- 每轮之间退避 30s / 60s / 120s（Flow 拒图有时是瞬时的）。
- 收尾那句 `本轮：…` 后面补一行**整体进度**：
  `任务 T0021：32 段中已收 27，仍被拒 3（p07/p12/p19），已接力 2/5 轮`。

### B. 反复被拒就换策略，别拿同一个参考图硬磕

`try_auto_refill` 现在每次都用 `expand.preview_ref_p` 挑参考图，同一张图被拒 3 次，
很可能 3 次都是照着同一张参考生的、长得差不多，自然一样被拒。

- `refill_one` 加参数 `attempt`：
  - 第 1 次：现在的行为（`preview_ref_p` 挑的参考）；
  - 第 2 次：**换一张参考图**（图池里 role=主体、且和第 1 次不同的；没有别的就换 seed）；
  - 第 3 次：换参考图 **且** 换构图方向——从 32 号图库里挑一个和当前差异最大的
    `景别 + 角度`（`coverage.py` 已有相似度函数，用它选最远的那个）。
- 日志每次写明这次换了什么：`第 2/3 次：换参考图 p01 → p05`。
- `refill` job 里那 3 轮同样按 `attempt` 递进，不要 3 轮都一样。

### C. 最后的兜底：借一张已经过关的图（这是「保证全部出片」的关键）

一张图怎么都过不了（B 节 3 次都被拒），**不能让这一段就这么死掉**。
一段画面稍微重复，远好过整段缺片——用户的原话是宁可有瑕疵也要跑顺。

- 新函数 `generate.borrow_image(m, d, sg)`：从**本任务已经成功出过片**的图里挑一张给这一段，
  优先顺序：① 和本段描述语最接近的（有 `desc` 就用文本相似度，没有就随便）；
  ② 满足「间隔排列」（不和相邻段同一张）；③ 用得最少的那张。
- 触发时机：某段的图三次补图都失败、且 `run_to_completion` 开着 → 借图，段回 `planned` 重提，
  日志 `⚠ s019 的 p07 三次都被拒，借用 p03 出片（画面会和 s0xx 重复）`。
- 借来的段在 ⑤ 收片上打一个 `借图` 小标记，你事后想换掉一眼就找得到。
- `run_to_completion` 关着 → 不借，保持现在的 blocked 行为。
- 借图**不改** `images/` 里的任何文件，只改这一段的 `image` 字段（走 `pipeline.pin_image`），
  原来那张 blocked 的图留着，以后手动补好了还能换回来。

### D. 剩下真的过不去的，要看得见

- ④ 任务卡片上，`blocked` 段数单独显示一个红色小徽章 `3 段被拒`，点它列出是哪几段、哪几张图、
  各自试了几次、最后一次的拒绝原因（`中性图可传、这张图任何尺寸都不可` 这种要原样留着，
  它说明是图的内容问题，不是接口问题）。
- 那个列表里每行给三个按钮：`再试一次` / `借图出片` / `我自己换图`（跳到 ② 多模态定位到这张）。
- `MAX_AUTO_REFILL_TASK` 触发时那句警告要更明确：现在说「多半是被拒判断错了」，
  再补一句 `已停止自动换图以免毁掉图池；确认是图的问题就点「再试一次」，
  确认是接口抽风就先歇十分钟`。

### E. 测试 `tools/test_spec58.py`（**先设 ORANTE_DATA**，FakeEngine 按脚本拒图）

- FakeEngine：p07 永远被拒，其余正常。`run_to_completion=True` →
  第 1 轮 3 次补图失败 → 第 2 轮借图 → s019 收片；任务最终 0 段 blocked。
- `run_to_completion=False` → s019 停在 blocked，不借图（保持旧行为）。
- 接力预算：让所有图都被拒 → 最多 5 轮，连续 2 轮零进展就停，日志有那句话。
- `refill_one(attempt=2)` 用的参考图和 `attempt=1` 不同；`attempt=3` 的景别/角度和第 1 次差异最大。
- `borrow_image`：不会挑相邻段同款（间隔排列），优先挑用得最少的；借图后段的 `image` 变了、
  `images/` 文件一个没动、`takes` 里标了 `borrowed`。
- `MAX_AUTO_REFILL_TASK` 仍然在 4 触发，触发后不再自动换图（这条**不许**被 A 节的接力绕过——
  接力是下一轮重新开始计数，不是同一轮里继续换）。
- 回归：`python -m pytest tools/` 全过，`test_upload_rejected` 不回退。

## 验收

1. 造一个任务，图池里放一张 Flow 一定拒的图 → 开着「跑到全部出片」跑一遍，
   **最后 0 段 blocked**，那一段是借图出的，⑤ 收片上有 `借图` 标记。
2. 日志里能看到：三次补图各用了不同参考/构图，然后才借图。
3. 关掉开关重跑 → 行为和现在完全一样（段停在 blocked）。
4. 全图池都被拒的极端情况 → 5 轮内停下，提示写明还剩几段、是哪几张图。
5. ④ 卡片上的 `N 段被拒` 徽章点开能看到每段的拒绝原因和三个按钮。
