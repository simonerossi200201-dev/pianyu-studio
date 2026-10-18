# 55 · 重生主片后，下游接片可能还留着用旧尾帧拍的那一条

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/37-anchor-bridge-pipeline.md`、
> `specs/38-anchor-bridge-review.md`、`specs/52-grok-bridge-single-image.md`、`specs/53-bridge-fail-accounting.md`
> 和它们的报告，再读 `orante/services/tails.py`（`tail_path` 第 361 行、`extract`、
> `_fill_bridge` 第 460 行、`after_collect` 第 658 行）、
> `orante/services/pipeline.py`（`cascade_after_anchor` 第 815 行、`_reset_seg_planned`、`GAP_ENQUEUE`）、
> `orante/services/verify.py`（`take["frame_ahash"]` 第 584 行、`ahash_from_hex`、`hamming`）、
> `ui/js/panels/bench.js`，再读本规格。**先做完 54。**
> 改前备份 `*.bak-0912stale`。报告写到 `specs/reports/55-stale-tail-cascade.md`。中文注释。

## 现象（用户问的正是这个）

「有的片段生成得不好，我点重生拿到新片段之后，新的尾帧也会自动替换对吧？」

**指针会自动换，片子不一定会重拍。** 重生一段主片之后：

1. `after_collect` 无条件重抽尾帧，`_fill_bridge` 把 `bridge.tail_frame` 指到新的——**这一步是对的**；
2. 但尾帧文件名是固定的 `clips/_tails/sNNN.jpg`（`tail_path`），**新尾帧直接覆盖旧的**，
   路径没变、没有任何版本标记；
3. 接着 `cascade_after_anchor` 决定下游接片动不动，判据在第 842 行：

       if nxt.status == "collected" and g <= GAP_ENQUEUE and ready:
           return {"action": "keep"}        # 跨度还小，不动

   **这个判据问的是「新尾帧和下一张锚点图的跨度大不大」，不是「这条接片是不是拿现在这张尾帧拍的」。**
   于是只要新尾帧跟原来差不多（跨度仍 ≤3），已收的接片就被原样留下——
   而它的第一帧还是**旧尾帧**的样子。剪在一起，那一刀会跳。

跨度小恰恰是最容易中招的情况：你重生主片往往只是因为画面质量差（糊、闭眼、手势难看），
人物位置景别没怎么变 → 跨度照样 ≤3 → 判 `keep` → 那条用旧尾帧拍的接片留下了。

## 要做的

### A. 尾帧要有身份，别只有路径

- `_fill_bridge` 写 `bridge.tail_frame` 时一并写 `bridge.tail_ahash`
  （对尾帧图算 16×16 aHash，十六进制，复用 `verify.ahash_image` / `ahash_hex`）
  和 `bridge.tail_at`（ISO 时间戳）。
- `extract` 覆盖 `clips/_tails/sNNN.jpg` 之前，若已有旧文件，挪到
  `clips/_tails/_旧/sNNN-<时间戳>.jpg`（保留 3 份，多的删最旧的）。
  排查时能对着看「换之前长什么样」。

### B. `cascade_after_anchor` 判 `keep` 之前先验血缘

在第 842 行那条 `keep` 之前插一道**陈旧检查**：

- 取下游接片最后一条 take 的 `frame_ahash`（它就是这条接片成品的第一帧）；
- 取 `bridge.tail_ahash`（现在这张尾帧）；
- 两者汉明距 > `DIST_SOFT`（16）→ **判定这条接片是拿旧尾帧拍的**，
  走 `_reset_seg_planned` 打回重拍，`why` 写 `上游重生，尾帧已换（首帧距 N）`，
  返回 `{"action": "reset", "reason": "stale_tail"}`。
- 两者距 ≤16 → 说明新旧尾帧本来就几乎一样，`keep` 成立，日志
  `· s002 上游换了尾帧但几乎没变（距 N），不动`。
- 任一 hash 缺失（老数据、ffmpeg 不在）→ **保守起见判 reset**，日志写明是因为算不出。
  宁可多拍一条，也别把跳变留在成片里。

这条检查对 Flow 接片和 Grok 接片一视同仁。

### C. 打回要连锁

一条接片被判 stale 打回后，它自己的尾帧也随之作废（如果它后面还跟着别的段）。
`cascade_after_anchor` 现在只看 `next_of` 一层。改成：被 reset 的段如果是
`role == "bridge"` 且它后面还有接片依赖它，沿链往下再查一层，直到遇到
`anchor`（主片自带 HD 图，不依赖上游尾帧）为止。日志把整条链一次写清楚：
`· 重生 s001 → 打回 s002（尾帧已换）`。

### D. 用户要看得见

- 检查台里，因为上游重生而被打回的接片，行上标一个 `上游已重生` 的小标记，
  和普通的「跨度大」区分开——原因不同，处理办法也不同（这个只要重拍，不用换锚点）。
- ⑤ 收片里，主片卡片上加一行小字：`重生这一段会让 sNNN 接片一起重拍`，
  **点重生之前**就告诉用户代价，别等跑完才发现多烧了一条。

### E. 测试 `tools/test_spec55.py`（**先设 ORANTE_DATA**）

- s001 主片 collected、s002 接片 collected 且 `take.frame_ahash` == 当前 `tail_ahash` →
  重生 s001 后新尾帧几乎一样（距 ≤16）→ `cascade_after_anchor` 返回 `keep`。
- 同上但新尾帧换了（距 30）→ 返回 `reset`、`reason == "stale_tail"`，
  s002 变 `planned`、旧片进 `clips/_弃用`。
- `bridge.tail_ahash` 缺失 → 判 `reset`，日志点名「算不出尾帧指纹」。
- 跨度 g=2（≤3）但 hash 距 30 → **仍然 reset**（证明新判据优先于跨度判据，这是本规格的核心）。
- 链式：s001 主片 → s002 接片 → s003 接片（人为构造）→ 重生 s001 后两段都被打回。
- `extract` 覆盖尾帧时旧图进 `clips/_tails/_旧/`，超过 3 份删最旧。
- 回归：`python -m pytest tools/` 全过，`test_spec37` / `test_spec38` / `test_spec52` / `test_spec53` 不回退。

## 验收

1. T0015 拿一段主片重生，故意让新尾帧明显不同（换配图或改描述）→
   日志出现 `重生 s00X → 打回 s00Y（尾帧已换）`，那条接片自动重拍。
2. 同一段重生但画面几乎没变 → 下游接片不动，日志写明距离。
3. ⑤ 收片点重生之前，卡片上就能看到「会让 sNNN 一起重拍」。
4. `clips/_tails/_旧/` 里能找到被换掉的旧尾帧。
