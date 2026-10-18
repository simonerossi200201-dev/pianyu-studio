# 80 · 换一张图，不要把已经收好的片子也拉下水

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/17-upload-rejected.md`、
> `specs/24-gapfill-triage.md`、`specs/58-run-to-completion.md`、
> `specs/66-expand-account-and-upscale.md`（**它的"提升画质"已经把这件事做对了，照着它的做法改**）
> 和它们的报告，再读 `orante/services/expand.py`（`_reset_refs`(647)、`_apply_new_file`、
> `refill_one`、`refill`）、`orante/services/generate.py`（`_block_image`(122)、
> 自动换图那一段 530~590、`apply_timeout`）、`orante/services/review.py`（`set_image`）、
> `ui/js/panels/track.js` 和 `ui/js/panels/review.js` 里显示换图结果的地方，再读本规格。
> 改前备份 `*.bak-0920collateral`。报告写到 `specs/reports/80-no-collateral-regen.md`。中文注释。

## 现场

Flow 任务快做完了，**只有最后一段被拒**。结果软件把**另外 4 段已经收好的片子也打回重做了**。
最后虽然都生成出来了，但那 4 段是白跑的。

## 根因：`_reset_refs` 把 `collected` 也打回 `planned`

`expand.py:647`：

```python
st = sg.get("status")
if st in ("collected", "failed", "blocked", "done"):
    sg["status"] = "planned"
```

链条是这样的：

1. 最后一段提交给 Flow，图被拒 → `_block_image` 把这张图标 `blocked`。
2. 自动换图（`refill_one`）生成一张新图顶替它，走 `_apply_new_file`。
3. `_apply_new_file` 最后调 `_reset_refs(m, p)` —— 它把**所有引用这张图的段**里，
   状态是 `collected` 的也一并打回 `planned`。

那张被拒的图正好还被另外 4 段用着，那 4 段早就收片了。于是 4 条好好的片子全被作废重做。

## 这个行为原本的理由，和它为什么不成立

`_reset_refs` 的文档字符串写着「图被覆盖后…已完成/失败/被拒打回 planned」。
理由大概是"图换了，成片就对不上图池了"。但：

- **成片是用旧图生成的，而旧图并没有被删** —— 0908 之后的规矩是原图一律挪到
  `images/_replaced/`，不删（`_apply_new_file` 自己的注释就是这么写的）。
- 观众看的是视频，不是图池。已经收好的那一段**画面本身没有任何问题**。
- 唯一的"不一致"是 ③编排 面板上那一段显示的是新图、而视频里是旧图——
  这是个记账上的别扭，不是质量问题，**不值得用重跑 4 条视频去换**。

66 号的「提升画质」遇到同样的岔路口时选对了：换完图**不自动打回**，
而是弹一条带按钮的提示让用户自己决定。自动换图这条路没跟上。

## 要做的

### A. `_reset_refs` 不再动 `collected`

```python
# collected = 这一段已经有成片了，而且那条片子是用旧图生成的、旧图还在
# images/_replaced/ 里躺着。图池换了张新图，不该让已经做好的片子作废重来——
# 用户为这个白跑过 4 条。只有还没做完的段才需要按新图重做。
if st in ("failed", "blocked", "done"):
    sg["status"] = "planned"
    ...
```

- `collected` 的段：**状态不动、`prompt_final` 也不清**（那是这条片子当时用的提示词，
  清掉就查不到它是怎么生成的了）。
- 给它打一个记号：`sg["image_replaced"] = {"p": p, "at": "<ISO>", "old_file": "<_replaced 里的相对路径>"}`。
- `submitted` 维持现状（正在跑，只清提示词不动状态）。

### B. 返回值要分开，日志要说清楚

`_reset_refs` 现在返回一个"所有引用段"的列表，上层拿它报数，分不清谁被打回了、谁只是被记了一笔。

改成返回 `{"reset": [段号…], "kept": [段号…]}`，`_apply_new_file` 的日志写成：

    · 换图 p07：s003 s005 s008 已收片，保留不动；s011 打回重做

前端的 toast 同样分两句，别再笼统地说"N 段打回 planned"。

### C. 给用户一个"我就是要重做"的出口

和 66 号一致：换完图之后，如果有被保留的 `collected` 段，弹一条带按钮的提示：

    ✓ p07 已换成新图
    有 3 段已经用旧图收过片了，保留没动
    [ 这 3 段也按新图重做 ]   [ 就这样 ]

点第一个才 `_reset_refs` 那 3 段。**默认什么都不做。**

### D. ③编排 / ⑤收片 上标出来

被保留的段在两个面板上都要能看出"片子和图池对不上"：

- ③编排 段表那一行：图号后面一个小小的灰色角标 `旧图`，`title` 写
  「这一段的片子是用换掉之前那张图生成的，画面没问题」。
- ⑤收片 卡片上同样一个角标，点一下可以只重做这一段。
- 角标在这一段被重新生成之后自动消失（`image_replaced` 删掉）。

### E. 顺带堵掉另外两处同类的浪费

同一个毛病可能不止这一处，**逐个确认并在报告里列出结论**：

1. `_block_image`：现在只 block 非 `collected`/`submitted` 的段，**这个是对的**，别改。
2. `generate.py` 自动换图那一段（530~590）：确认它只对"还没收片"的段触发，
   不会因为一张图被拒就把整张图的历史都推倒。
3. `review.set_image`（手动换图）：用户手点的，**保持现在的行为**（打回重做正是他要的）。
4. 66 号「提升画质」：确认它没有走 `_reset_refs` 的自动打回路径。

## 测试 `tools/test_spec80.py`（**先设 ORANTE_DATA**）

- 造一个任务：图 `p07` 被 5 段引用，其中 s003/s005/s008 是 `collected`，s011 是 `blocked`，s013 是 `planned`。
  跑 `_apply_new_file` 换图：
  - s003/s005/s008 **仍然是 `collected`**，`prompt_final` **没被清空**，各自有 `image_replaced` 记号；
  - s011 变 `planned`、`error` 清空；s013 还是 `planned`；
  - 返回值 `{"reset":[11], "kept":[3,5,8]}`；
  - 日志里同时出现「已收片，保留不动」和「打回重做」两句。
- `submitted` 的段：状态不动，`prompt_final` 被清（维持原行为）。
- 手动触发"这 3 段也按新图重做" → 那 3 段才变 `planned`，`image_replaced` 被删掉。
- 段重新收片之后 `image_replaced` 自动消失。
- `review.set_image` 手动换图：行为和以前**完全一样**（回归断言，别误伤）。
- 66 号的提升画质：`collected` 段不被打回（和 66 号报告一致，回归断言）。
- 端到端：11 段的任务，收到第 10 段时最后一段的图被拒 → 自动换图 → **只有最后一段重新提交**，
  统计里 `已收` 不倒退。
- 回归：`python -m pytest tools/` 全过，`test_spec17` / `test_upload_rejected` / `test_spec66` 不回退。

## 验收

1. 做一个 Flow 任务，故意让最后一段的图被拒（或者手动把某张已被多段使用的图标成 blocked 再跑）：
   日志写明哪几段保留、哪一段重做，**已收的数字不往回掉**，只有那一段重新提交。
2. 提示里点<span class="btn-ref">这 N 段也按新图重做</span>，那几段才被打回。
3. ③编排 和 ⑤收片 上那几段带着 `旧图` 角标；重做过之后角标消失。
4. 在 ⑤收片 手动<span class="btn-ref">换图并重生</span> —— 行为和以前一模一样，没被这次改动影响。
