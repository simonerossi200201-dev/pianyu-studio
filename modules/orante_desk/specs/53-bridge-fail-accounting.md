# 53 · 接片失败计数记错账：s010 被两次不相干的失败永久锁死

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/51-grok-resume-collect.md`、
> `specs/52-grok-bridge-single-image.md` 和它们的报告，再读
> `orante/services/pipeline.py`（`wait_reason` 第 185~210 行、`grok_bridge_is_single`、`GAP_SWAP`）、
> `orante/services/verify.py`（第 640~660 行 `fail_count` / 「接片两次都对不上」）、
> `orante/services/generate.py`（第 455~500 行两处 `fail_count += 1`）、
> `orante/engines/grok.py`（`_net_candidates` / `_mark_ready` / `download`）、
> `orante/legacy/grok_pipe.py::is_real_mp4`、`ui/js/panels/bench.js`（尾帧检查台），再读本规格。
> 改前备份 `*.bak-0912acct`。报告写到 `specs/reports/53-bridge-fail-accounting.md`。中文注释。

## 现象

T0015 s010 现在是 `failed`、`fail_count=2`，④ 生成里每轮都打
`· s010 接片两次都对不上，先不提`，永远进不了队，s011 跟着一起卡死。

但翻它真正的两次失败：

| 第几次 | 真实原因 | 属于「对不上」吗 |
|---|---|---|
| 1 | 09:34 `首帧对不上 s009 尾帧（距 87）` | 是，**但那是 52 号之前的双图老路** |
| 2 | 10:2x `下载失败：下载失败：HTTP 200 / 99176 字节 / 不是合法 mp4` | **不是**，这是下载到了错东西 |

manifest 里 `s010.bridge_engine = "grok"`（双图的旧标记），`error` 就是那句下载失败。
也就是说：**一次已经作废的老路失败 + 一次下载失败 = 判它「两次都对不上」永久出局。**
52 号刚把 Grok 接片改成单图起于尾帧，这条新路 s010 **一次都还没走过**。

## 根因（四条，都要修）

1. **`fail_count` 是一个大杂烩计数器。** `verify.py:649`（首帧不符）、`generate.py:463`（提交/生成失败）、
   `generate.py:494`（超时）三处都往同一个 `fail_count` 上加，而 `pipeline.py:192` 读这一个数
   就断言「两次都对不上」。下载失败、超时、被拒，全都算进了「对不上」。
2. **换路不清账。** 双图 → 单图是换了一条完全不同的路（52 号的数据：双图首帧距从没低于 41，
   单图 0~7）。旧路攒的账不该记在新路头上。
3. **网络层候选没有体积闸。** 51 号加的 `_net_candidates` 挑中了一个 99 KB 的地址——15 秒的片子
   不可能 99 KB，那是海报/预览图。`is_real_mp4`（>200000 且 `ftyp`）在最后一道拦住了，是对的，
   但拦住之后**当成本段失败**，而不是「这个地址不对，继续等真的」。
4. **错误信息双前缀**：`下载失败：下载失败：…`。

## 要做的

### A. 把「对不上」单独计数

- 段上新增 `mismatch_count`，**只有** `verify.py` 判 `wrong_clip`（首帧对不上参考）时 +1。
  `fail_count` 保留原样当通用计数（图的 `gen_fail`、blocked 判定还在用它），但
  **`pipeline.py:192` 的两次出局闸改读 `mismatch_count`**，文案跟着改成
  `接片两次首帧都对不上（检查台可重试）`。
- `verify.py` 里现在 `fail = fail_count + 1` 那段：`mismatch_count` 和 `fail_count` 各加各的，
  判「两次」用 `mismatch_count`。
- 下载失败、超时、提交失败**不加** `mismatch_count`。超时那条（`generate.py:494`）另给
  `timeout_count`，连续 3 次超时才停，文案 `等了太久没出片 ×3`。

### B. 换路清账

- `grok_bridge_is_single(m, sg)` 算出本段这次要走的路（`"grok-single"` / `"grok"` / `"flow"`），
  和段上记的 `bridge_engine` 不一致时：**清 `mismatch_count`**，日志
  `· s010 接片换了路（双图→单图），旧的对不上记录清零`。
  清完把 `bridge_engine` 更新成新值。放在 `generate.py` 组队之前（`wait_reason` 之前就要生效）。
- 一次性迁移：任务载入时，`role=bridge` 且 `bridge_engine` 是旧值 `"grok"` 且
  `GROK_BRIDGE_MODE == "single_tail"` 的段，`mismatch_count` 归零一次（幂等，
  用 `bridge.acct_migrated_53 = true` 打标记，别每次载入都清）。

### C. 网络层候选要过体积闸，不合格的地址进作废名单

- `_net_candidates` 挑地址时先拿 `Content-Length`（response 监听已经有 headers；
  performance 那路用 `transferSize` / `encodedBodySize`）。**小于 300 KB 的一律不当候选**，
  日志 `· s010 网络层候选 <url 前 60> 只有 99 KB，跳过`。
- `download` 里 `is_real_mp4` 不通过时：**不抛 `OranteError`**，改抛 `NeedRedownload`，
  同时 `self._rejected[gid].add(url)`、`self._urls.pop(gid)`，让轮询继续等真正的地址。
  （`generate.py` 已经有 `NeedRedownload` 分支，`↻ 下载地址过不了闸，重新取` 就是它。）
  连续 5 次都取到不合格地址才判本段失败，错误写明 `网络层一直只给到缩略图/预览`。
- 顺手修 `下载失败：下载失败：` 的双前缀。

### D. 检查台给用户一个逃生口

- 尾帧检查台里每个 `failed` / 被两次闸挡住的接片，加一个 **「重试接片」** 按钮：
  清 `mismatch_count` / `timeout_count`、`status` → `planned`、`error` → null、
  `gen_id` → null，日志一条。这样以后不用改 manifest 也能放行。
- 按钮旁边用小字说明它为什么被挡：`两次首帧都对不上` / `下载一直不合格` / `超时 3 次`，
  别再笼统一句「对不上」。

### E. 测试 `tools/test_spec53.py`（**先设 ORANTE_DATA**）

- `wrong_clip` 两次 → `mismatch_count=2` → `wait_reason` 返回「两次首帧都对不上」。
- 一次 `wrong_clip` + 一次下载失败 → `mismatch_count=1` → **能进队**（正是 s010 这个场景）。
- `bridge_engine="grok"` 的 `failed` 段，在 `single_tail` 下组队 → `mismatch_count` 清零、
  `bridge_engine` 变 `"grok-single"`；再跑一次不重复清（幂等）。
- 网络层候选 99 KB → 不被选中；`is_real_mp4` 不过 → `NeedRedownload` + 地址进 `_rejected`，
  段仍是 `submitted`；连续 5 次不合格 → failed 且错误文案点名。
- 超时两次仍能重试，第三次才停。
- 错误串里不出现 `下载失败：下载失败：`。
- 回归：`python -m pytest tools/` 全过。

## 验收

1. T0015 点「开始生成」：s010 不再被挡，日志出现 `接片(Grok 单图) 首帧=s009 尾帧`，
   收片时首帧距 ≤ 10。
2. 检查台上 s010 那行（若仍被挡）显示具体原因，点「重试接片」后立刻能进队。
3. 故意喂一个 99 KB 的假地址 → 日志 `↻ 下载地址过不了闸，重新取`，段不失败。
