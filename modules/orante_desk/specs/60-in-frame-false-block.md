# 60 · 近景被误判成「人物出画」，把下游接片永久卡死，而且提示词还撒谎

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/56-flow-bench-advisory.md`
> 和它的报告，再读 `orante/services/pipeline.py`（`refresh_bridge_ready` 第 163 行、`_checks_ok`、
> `wait_reason` 第 185~210 行、`gate_mode_of`）、`orante/services/tails.py`
> （`extract` / `_fill_bridge` 里写 `tail_checks` 的地方、`apply_sharp_flags`）、
> `orante/services/bench.py`（`bench_level` / `can_pass`）、`orante/services/ai.py` 或
> 多模态提示词里判 `in_frame` 的那段，再读本规格。
> 改前备份 `*.bak-0913inframe`。报告写到 `specs/reports/60-in-frame-false-block.md`。中文注释。

## 现象

T0020（Flow，35 段）缺 s010 和 s024 两段。用户反复点「重生」，每次日志都一模一样：

    ⟲ s010 打回 planned
    ⟲ s024 打回 planned
    · s010 等 s009 收片，先不提
    · s024 等 s023 收片，先不提
    锚点模式：先提 0 段主片/已齐接片，2 段等前置
    本轮：提交 0，收片 0，失败 0，被拒 0

**s009 和 s023 明明早就收片了**（35 段收了 33 段，面板上 009 是「已收 / 体检通过 −22 dB」）。
提示说「等 s009 收片」是**假的**。

## 根因

manifest 里两段接片的 `tail_checks`：

| 段 | from | gap | face_ok | **in_frame** | sharp |
|---|---|---|---|---|---|
| s010 | s009 | 3 | True | **False** | True |
| s024 | s023 | 5 | True | **False** | True |

`refresh_bridge_ready` 里 `_checks_ok()` 要求 `face_ok and in_frame` 同时为真，
`in_frame=False` → `s009.bridge_ready = False` / `s023.bridge_ready = False`。
于是 `wait_reason` 走到第 200 行那个分支：

    if not prev.get("bridge_ready"):
        g = gap_of(sg)
        if g is not None and g >= GAP_SWAP:
            return {..., "text": "等 %s 收片（跨度 %s，检查台）"}
        return {"from": int(prev["s"]), "text": "等 %s 收片" % _lab(prev["s"])}   # ← 这里

**这条 return 的文案和第一条（prev 真的没收片）一模一样**，所以用户看到「等 s009 收片」，
完全不知道真正卡住的是 `in_frame`。56 号把 Flow 的跨度闸降成 advisory 之后，
连原来那句「（跨度 N，检查台）」的线索都没了，两条路径合并成同一句假话。

### 而 `in_frame=False` 本身就是误判

我把三张尾帧调出来看了（`clips/_tails/s007.jpg` / `s009.jpg` / `s023.jpg`）：

- `s007`：全景，人站在台上很小 → 判 `in_frame: True`
- `s009`：**中近景**，人脸完整、清晰、光线好，身体在腰部被画幅裁掉 → 判 `in_frame: False`
- `s023`：**近景**，人脸完整、头发都在画内，胸口以下被裁掉 → 判 `in_frame: False`

也就是说，多模态把「近景」理解成了「人物出画」。**近景本来就该只有上半身。**
这是个系统性误判：越是好看的近景尾帧，越会被判 `in_frame=False`，越会把下游接片卡死。

### 这是我在 56 号里写错的

56 号 §A 我写了「真正拍不成的仍然拦，不分引擎：尾帧没抽出来、`face_ok` / `in_frame` 为 False」。
当时我默认这两个标志是可靠的。`face_ok` 是可靠的（脸有没有拍到），
**`in_frame` 不可靠**，它分不清「近景」和「出画」。这条硬闸要拆。

## 要做的

### A. `in_frame` 从硬闸降级成黄旗

- `_checks_ok()` **只看 `face_ok`**（脸没拍到，接片起手就是废的，这条留着当硬闸）。
  `in_frame` 不再参与 `bridge_ready` 的判定。
- `in_frame=False` 改成在检查台上标**黄旗** + 文案「尾帧可能构图偏紧，拍完复查」，
  可以放行、不阻止生成。和 56 号对跨度的处理一致。
- `bench_level` 里 `in_frame is False` 从 red 挪到 yellow。

### B. 提示词不许再撒谎（`wait_reason`）

- `not prev.bridge_ready` 那条分支**必须说真正的原因**，把 `tail_checks` 里到底哪一项不过
  写进文案，绝不能再输出「等 sXXX 收片」这种和「prev 真的没收片」撞车的话。举例：
  - 尾帧没抽出来 → `s009 的尾帧没抽出来（检查台可重抽）`
  - `face_ok=False` → `s009 尾帧没拍到脸（检查台可换一帧）`
  - 其它 → `s009 尾帧没过检查：<字段名>`
- 顺带给 `wait_reason` 的返回值加一个 `code` 字段（`prev_not_collected` / `tail_missing` /
  `no_face` / `checks_failed` / `no_next_image` / `mismatch_stop`…），
  前端按 code 决定显示什么按钮，别再靠中文文案去猜。

### C. 卡住的东西必须能在 ⑤ 收片就地解开

用户是在 ⑤ 收片点重生的，而唯一的解法（放行）藏在检查台里。

- ⑤ 收片的缺片卡片上，如果这段 `wait_reason` 非空，**把原因直接写在卡片上**
  （现在卡片只写「首帧 ← s009 尾帧 · 尾帧 → p03」，一个字都没提为什么不动），
  并给两个按钮：`忽略这条检查，直接拍`（等价于 bench 的放行）和 `换一帧尾帧`
  （打开检查台定位到这一段）。
- 「改单图重拍」按钮保留（56 号 §C 的逃生口），但它不该是唯一出路——
  它会牺牲和上一段的连贯，用户未必想要。

### D. 一次性修数据

- 加 `tools/14-Fix-InFrame.bat` + `tools/fix_inframe.py`：扫所有任务的 manifest，
  把 `tail_checks.in_frame == False` 且 `face_ok == True` 的改成 True 并记一条
  `in_frame_note`，默认 `--dry-run`，`--yes` 才写。
- **T0020 我已经手动改过了**（s010 / s024 两段），脚本要能幂等跳过已经带 note 的。

### E. 多模态那条提示词也要改

判 `in_frame` 的提问现在会把近景判成出画。改成明确区分：

    in_frame：人物的**头部**是否完整地在画面内（不是问身体是否完整）。
    近景、特写、只拍到上半身，都算 in_frame = true。
    只有当头顶/下巴/半张脸被画幅切掉，或者人几乎已经移出画面时，才算 false。

`tools/` 下加一个小脚本，拿 `clips/_tails/` 里现有的图重跑这条判断，
报告里给出改前改后的对比（至少 10 张，含 s007 全景 / s009 中近景 / s023 近景这三张）。

## 测试 `tools/test_spec60.py`（**先设 ORANTE_DATA**）

- `_checks_ok`：`face_ok=True, in_frame=False` → **True**（不再拦）；`face_ok=False` → False。
- `wait_reason`：prev 已收片但 `bridge_ready=False` → 文案**不含**「等 sXXX 收片」，
  含具体原因，且 `code` 正确。
- `bench_level`：`in_frame=False` → `yellow`，`can_pass` 为 True。
- 构造 T0020 的场景（s009 collected、s010 planned、in_frame False）→
  `can_enqueue(s010)` 为 True，`iter_ready` 里有它。
- `fix_inframe.py --dry-run` 列出该改的段；`--yes` 改完再跑一次列出 0 条（幂等）。
- 回归：`python -m pytest tools/` 全过，`test_spec56` 不回退。

## 验收

1. T0020 点「开始生成」→ s010 / s024 **立刻进队开拍**，不再是「等 sXXX 收片」。
2. 故意把一段的 `face_ok` 改成 False → 仍然拦住，且卡片上写明「没拍到脸」。
3. ⑤ 收片的缺片卡片上能看到卡住的真实原因，点「忽略这条检查，直接拍」就能拍。
4. 重跑多模态的 `in_frame` 判断：s009 / s023 这类近景判 True，真正出画的判 False。
