# 52 · Grok 接片改「单图起于上一段尾帧」

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/36-anchor-bridge-foundation.md`、
> `specs/37-anchor-bridge-pipeline.md`、`specs/38-anchor-bridge-review.md`、`specs/40-grok-bridge-order-probe.md`
> 和它们的报告，看 `data/capture/probe40/decision.json` / `b-rescore.json`，再读
> `orante/services/pipeline.py`（`GROK_BRIDGE_UPLOAD`、`BRIDGE_MATCH_DIST`、`stage_bridge_images`、
> `wrap_bridge_prompt`、`score_collected_bridge`、`_retry_grok_bridge`、`cascade_after_anchor`）、
> `orante/services/generate.py`（第 330~361 行提交、第 396~500 行收片/失败）、
> `orante/services/verify.py`（`_first_frame_ref`、`after_download` 4b、`_apply_void`）、
> `orante/engines/grok.py::submit`、`orante/legacy/grok_pipe.py::prepare`（双图那段）、
> `ui/js/panels/generate.js`（生成方式说明文字）、`ui/js/panels/bench.js`（检查台，若存在），再读本规格。
> **先做完 51。** 改前备份 `*.bak-0912single`。报告写到 `specs/reports/52-grok-bridge-single-image.md`。中文注释。

## 结论先行：Grok 双图接片这条路走不通，数据说的

探针 40 三种写法共 11 条实测（`decision.json` + `b-rescore.json`）：

| 写法 | 上传顺序 | 首帧↔起始图 距离 | 尾帧↔结束图 距离 |
|---|---|---|---|
| ④ 起→终 | 3 次 | 47 / 41 / 51 | 24 / 18 / 18 |
| ⑤ 终→起 | 3 次 | 56 / 55 / 50 | 46 / 37 / 38 |
| ⑥ 终→起 + 改词 | 2 次 | 60 / 56 | 24 / 37 |

单图起片时首帧距离是 0~7。**双图时首帧从没低于 41**，不管谁先传、提示词怎么写。
今天 T0015 s010 是 87。也就是说 Grok 把两张图都当参考图，起始画面它不照抄；
结束图倒是基本照抄（18~29）。用户手动操作也是同样的拖两张图 + 描述，没有别的入口。

用户拍板：**Grok 接片改成只传一张图——上一段的尾帧**。首帧必对上（单图起片 0~7），
接片是上一段的自然延长；接片→下一段主片那一刀仍是硬切（和原来主片之间的硬切一样，没有变差）。
Flow 的首尾帧是原生功能、两头都对得上（36 探针），**Flow 接片不动**。

## 要做的

### A. Grok 接片 = 单图起于尾帧

- `pipeline.py` 新常量 `GROK_BRIDGE_MODE = "single_tail"`（另一个值 `"dual"` 保留给以后 Grok 修好了再切回）。
  `GROK_BRIDGE_UPLOAD` / `GROK_BRIDGE_FLIP_PROMPT` 只在 `"dual"` 下有意义，加注释说明。
- `stage_bridge_images` 加参数或新函数 `stage_bridge_start(task_dir, m, sg)`：只写 `01_首帧_start.jpg`，
  返回 `(start, None, tmp)`。`generate.py:334` 按引擎分：Grok 且 `single_tail` → 只拿起始图，
  `eng.submit(..., last_image=None)`；Flow 照旧双图。日志改成
  `→ s010 接片(Grok 单图) 首帧=s009 尾帧`。
- `seg["bridge_engine"]` 记 `"grok-single"`（Flow 记 `"flow"`，旧值 `"grok"` 视同双图旧数据）。
- **接片提示词**（`wrap_bridge_prompt`）：Grok 单图时不再用「第一张/第二张」三句，换一段专用模板
  `BRIDGE_PROMPT_SINGLE`（进 `prompts` 可编辑块，键 `bridge_single`，默认值如下），
  仍然拼在常规成品提示词前面，台词在 body 里不动：

      画面从上传图片的这一刻自然延续：{start}。
      镜头继续当前的运动，人物保持同一姿态和位置，讲话不中断，不切镜、不跳剪。

  `{start}` 仍是 `tail_pose` 的文字（`_pose_text`）。**不要提结束画面**，不要提下一张图。
- `grok_pipe.prepare` 双图分支不删，但加一句日志 `· 双图接片（写法 %s）` 方便以后辨认。

### B. 校验按「参考是什么」分档，两边不再打架

- `verify.py` 4b：`ref_kind == "tail"` 且 `bridge_engine == "grok-single"` → 用 `DIST_OK 10 / DIST_SOFT 16`
  （单图起片本来就是 0~7）；`ref_kind == "tail"` 且 Flow 双图 → 用 `pipeline.BRIDGE_MATCH_DIST`（30）
  当红线，`DIST_SOFT` 当黄线。把这个选择写成一个函数 `_thresholds_for(seg)`，两处（`after_download` 和
  `score_collected_bridge`）都调它，**只留一处常量定义**。
- 接片 `wrong_clip` 作废后**不再「重新取地址」**：地址没错，是片子错了。`_apply_void` 对接片改成：
  片子挪 `_弃用`，段打回 `planned`（不是 `submitted`），`gen_id` 清掉，`fail_count += 1`，
  日志改成 `✗ s010 接片首帧对不上 s009 尾帧（距 87），作废，重新提交`。
  `fail_count >= 2` 才停下来留给用户（检查台里标「接片两次都对不上」）。
  非接片（对配图）的 `wrong_clip` 逻辑不变——那种情况确实可能是拿错了邻居的片。
- `grok.py` 51 号加的「网络层」出片路径必须查 `self._rejected`（今天日志里作废之后又
  `✓ 出片 … 网络层 唯一strong`，就是它漏了）。

### C. 尾帧质量是这条路的命门

- 单图起于尾帧，尾帧模糊/闭眼/张嘴半截，接片开头就难看。`tails.py` 现有「最后 3~8 帧取最锐」保留，
  再加两条硬门槛：Laplacian 方差低于 `TAIL_MIN_SHARP`（先定 60，报告里写实测分布）→ 换下一候选；
  全部候选都不及格 → 用最锐的那张但 `bridge.tail_warn = "尾帧偏糊"`，检查台上黄旗。
- 检查台（38 号的尾帧检查台）每个接片显示三格：上一段尾帧 / 接片第一帧 / 距离。距离 >16 红。

### D. 界面文字

- ④ 生成里「锚点 + 接片」的副标题改成 `主片锁身份，接片起于上一段尾帧`；鼠标悬停说明：
  Flow 接片首尾双图，Grok 接片单图延长。
- 检查台的接片行显示 `Grok 单图 / Flow 双图`。

### E. 测试 `tools/test_spec52.py`（**先设 ORANTE_DATA**）

- Grok 任务的接片：`stage` 只产出一张图；`eng.submit` 收到 `last_image=None`；提示词含「自然延续」、
  不含「第二张」「结束画面」。
- Flow 任务的接片：仍然两张图、仍然旧模板。
- `after_download` 对 `grok-single` 接片：距 8 → ok；距 14 → soft；距 20 → wrong_clip → 段回 `planned`、
  `gen_id` 空、`fail_count` 1、`_弃用` 里有文件；第二次仍 wrong → `fail_count` 2、状态 failed、不再自动重提。
- Flow 双图接片距 25 → ok（30 红线）。
- `_thresholds_for` 是 `after_download` 和 `score_collected_bridge` 唯一的阈值来源（grep 断言）。
- 网络层出片路径对 `_rejected` 里的地址返回 pending。
- 回归：`python -m pytest tools/` 全过。

## 验收

1. T0015 把 s010 重生（这次会真正重提）：日志 `接片(Grok 单图) 首帧=s009 尾帧`，收片时首帧距 ≤ 10，
   s011 随后自动进队。
2. 把 s010 的成片和 s009 的成片首尾拼起来看（Cutora 或随便一个播放器）：s009→s010 之间没有跳变。
3. 故意给一个接片挂错尾帧（换成别的图）→ 作废 → 自动重提一次 → 再错 → 停下并在检查台标红，
   全程没有「重新取地址」。
4. Flow 任务（T0017 那种）的接片行为和 38 号验收时一样。
