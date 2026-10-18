# 36 · 「锚点 + 接片」第一步：探针 + 数据模型 + 尾帧提取器

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/00-architecture.md`、
> `specs/06-grok-engine.md`、`specs/18-parallel-accounts-and-grok-images.md`，
> 再读 `orante/engines/flow_api.py`（重点 `_gen_inner` / `submit`）和 `orante/engines/grok.py`，
> 再读本规格。这是三份一组（36 → 37 → 38）的第一份，**后两份依赖这一份的探针结论和数据模型**。
> 改前备份成 `*.bak-0910anchor`。报告写到 `specs/reports/36-anchor-bridge-foundation.md`。中文注释。

## 0. 已经确认的事实（不用再验）

- **Flow 的首尾帧模式能正常出同声语音。** 现在的出片其实走的就是这个模式，只是尾帧那一格一直空着。
- 所以「接片 = 正常文案段落 + 首帧/尾帧双约束」这条路是通的，台词不会少。

## 1. 方案里必须纠正的四点（照此实现，别按原话做）

1. **接片不是额外插入的过渡镜头。** 它就是文案里正常的一段，人物照常把这段话说完，
   只是多了「首帧 = 上一段的尾帧、尾帧 = 下一段的锚点图」两个约束。
   文案切分、时长、拼接全部不变。
2. **不按单数/双数判断。** 每段一个 `role`：`anchor` / `bridge`。默认交替；
   **最后一段强制 anchor**（偶数段时末尾排成 `bridge, anchor`）。
3. **尾帧不取最后一帧。** 倒数 3~8 帧里挑最锐的。
4. **锚点图的「进入状态」由多模态自动读出来**，不用人手写。

## 2. 探针（三个，都要真跑，结论写进 `specs/reports/36-probe.md`）

### 探针 A′ — Flow 的尾帧塞在载荷哪一格

`flow_api.py::_gen_inner` 里图片那一格是 `[None, media_id, None, None, None, crop]`。
用 `tools\Capture-Flow.bat` 的抓包套路：在 Flow 网页上**手动**做一次「首帧 + 尾帧」生成，
把 `eb1hJf` 的载荷抓下来，和现有只有首帧的载荷**逐格对比**，找出尾帧 media_id 落在哪个下标、
有没有额外标志位。把两份载荷（脱敏后）并排贴进报告。

### 探针 B — Grok 双图首尾帧，哪种写法最稳

同一组素材（一张尾帧 + 一张锚点图）跑 4 种写法各 3 次，人工看首尾是否对上号，成功率填表：

| 写法 | 内容 |
|---|---|
| ① 只靠顺序 | 先传 A 再传 B，提示词说「第一张是起始画面，第二张是结束画面」 |
| ② 顺序 + 文件名 | 文件名 `01_首帧_start.jpg` / `02_尾帧_end.jpg`，提示词里让它读文件名 |
| ③ ② + 内容描述 | 提示词里各用一句话描述两张图的内容（多模态读出来的） |
| ④ ③ + 过渡指令 | 再加「画面从第一张的状态自然过渡到第二张的状态，中间不切镜」 |

顺带记下：Grok 双图模式出不出语音、时长档位。**37 号按成功率最高的那种写法实现。**

### 探针 C — 尾帧锐度

拿 T0013 现成的片子，ffmpeg 抽倒数第 1~10 帧，每帧算拉普拉斯方差，数值贴进报告。
定出「取倒数第几帧」的范围。

## 3. 数据模型

`manifest.segments[]`：

    "role": "anchor" | "bridge",
    "bridge": {
      "from_seg": 3, "to_image": "p07",
      "tail_frame": "clips/_tails/s003.jpg",
      "tail_pose": {...同 images[].pose...},
      "tail_checks": {"face_ok": true, "in_frame": true, "sharp": true},
      "gap_score": 2, "gap_why": "只需把右手放下、视线转正",
      "auto_swapped_from": null          // 37 号自动换过锚点时记原来的
    }

`manifest.images[]` 新增 `pose`（**② 多模态写描述语时顺带生成，同一次调用**）：

    "pose": {"head":"略偏左","eyes":"看向台下听众","hands":"右手抬到胸口，掌心朝前",
             "body":"正直站立","expr":"温和恳切","text":"一句话概括"}

换图 / 重新描述时同步重算 `pose`。

## 4. 尾帧提取器 `orante/services/tails.py`

- `extract(task_dir, seg)`：按探针 C 的范围抽帧 → 选最锐 → 存 `clips/_tails/s003.jpg`（JPEG 95，分辨率同视频）。
- 全部子进程走 spec 23 的 `run_quiet`。
- **一次多模态调用三问合一**（别问三遍）：

      问：这是一段讲道视频的最后一帧。请用 JSON 回答：
          face_ok（脸是否完整无变形）、in_frame（人物是否完整在画面里）、
          pose（head/eyes/hands/body/expr/text）
  输出直接进 `bridge.tail_checks` 和 `bridge.tail_pose`。
- 锐度用**相对阈值**：低于本任务所有尾帧锐度中位数的 60% 才标 `sharp:false`。
- 主片收片后**自动触发**，不用人点。

## 5. 验收

1. `36-probe.md` 三个探针都有数据：A′ 的两份载荷对比、B 的 4×3 成功率表、C 的锐度数值。
2. 对 T0013 跑 `tails.extract` 全部段：`clips/_tails/` 每段一张，报告里说明每张挑的是倒数第几帧、为什么。
3. `manifest` 里每个 `bridge` 段的 `tail_pose` / `tail_checks` 都填上了，随机抽 3 段人工核对 pose 描述准不准。
4. ② 多模态跑一次描述语 → `images[].pose` 都有了，且**多模态调用次数没有增加**（看日志计数）。
