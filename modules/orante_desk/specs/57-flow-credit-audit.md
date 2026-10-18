# 57 · Flow 额度去哪了：先量清楚，再上硬闸——Orante 绝不能提交任何要扣额度的生成

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `orante/engines/flow_api.py`
> （文件头的 rpc 表、`MODEL_FAMILY` / `IMAGE_MODELS` / `model_id_for`、`_gen_inner`、`submit` 第 639 行
> `credits = _g(payload, 1)`、`gen_image_once`、`_rpc`）、`orante/accounts.py`、`tools/flow_capture.py`、
> `data/capture/flow_capture_0906-031010.jsonl`（有一条手动 UI 生成的 eb1hJf 完整请求/响应）、
> `data/capture/flow_capture_0907-101352.jsonl`（两条手动 ogiZ0b），再读本规格。
> **这一份插队，排在 56 前面**——用户的 Flow 额度可能正在被烧，其它都能等。
> 改前备份 `*.bak-0912credit`。报告写到 `specs/reports/57-flow-credit-audit.md`。中文注释。
> **探针（B 节）只许用 1 张图 + 1 条 8s 视频，跑一次；报告先给用户看结论，再决定要不要跑第二次。**

## 用户看到的

日志里 `✓ 已提交 sXXX → …  8s  剩余额度 55`。用户说原本一万多，现在 55，并且确定自己手动在 Flow 里
用的都是 `0 crediti` 的模型（生图：Nano Banana 2 Lite / Nano Banana 2 / Nano Banana Pro，
视频：Veo 3.1 - Lite [Lower Priority] · 720p · 8s，UI 都写「La generazione utilizzerà 0 crediti」）。

## 我已经核过的（Claude，2026-09-12）

1. **账号表**：A001~A004、A007 五个 Flow 账号 `model` 全是 `Veo 3.1 - Lite [Lower Priority]`。
2. **视频请求**：`model_id_for` 产出 `veo_3_1_i2v_lite_low_priority`（8s）/ `…_s_lite_4s_low_priority` /
   `…_s_lite_6s_low_priority`。0906 抓的那条**手动 UI** 生成，模型 id 就是 `veo_3_1_i2v_lite_low_priority`，
   和 Orante 发的一字不差。
3. **生图请求**：`gen_image_once` 的 inner `[null,[[null,null,refs,seed,2,"GEM_PIX_2",null,ctx,…]],1,ctx,[uuid]]`
   和 0907 抓的两条手动 UI 生图（`NARWHAL` / `GEM_PIX_2`）结构、参数逐位一致。
4. **「剩余额度」这个数是猜的。** 它是 eb1hJf 响应的 `[1]`，0906 抓包时那条是 8611，被当成额度写进了 rpc 表，
   从没用 `nzlxg`（查额度）核对过。`nzlxg` 在 rpc 表里列着，**代码里根本没实现**。
5. **时间线对不上「Orante 烧的」**：`J00156`（08:26~08:33，TIK-D1）17 次提交里这个数 2115→1917；
   之后到 `J00164`（11:53）**之间 3 小时 TIK-D1 上没有任何 Orante 的 Flow 任务**，数却从 1917 掉到 439。
   再往后 15:50~15:58 TIK-D1 连提 17 条、TIK-L1 提 4 条，**两个账号这个数都是 55，纹丝不动**。
6. 两个**不同 Google 账号**（dong / luca）同一时刻都恰好是 55——要么这不是账号余额，
   要么两个账号共用一个池（用户是 Ultra 家庭计划，可能共享额度）。

所以现在有两种可能，必须用数据分出来：
- **甲**：`[1]` 根本不是额度（比如是低优先级队列的预计等待秒数之类），什么都没被扣，是日志文案吓人；
- **乙**：`[1]` 是额度（或家庭共享池的额度），确实在掉，但掉的原因不在 Orante 发的请求本身
  （模型 id 和手动一致）——那就是别的东西在扣：Flow 在**完成时**按其它规则计费、另一个 Orante 窗口/
  老 bat 流水线在同一账号上跑、或 Flow 在 09-09 的新构建里改了计价而旧 id 落到了付费档。

## 要做的

### A. 先把「真额度」读出来，日志不许再猜

- 实现 `FlowEngine.credits() -> int | None`：走 `nzlxg`（inner `[]`），解析返回的第一个数字；解析不出返回 None
  并把原文（截 300 字）打进日志一次。
- `connect()` 时打一行 `· 账户余额 N（nzlxg）`；每个 generate/expand/regenerate Job **开始和结束**各打一次；
  Job 中每 60 秒最多再查一次。
- `submit` 里那句 `剩余额度 {credits}` 改成 `响应[1]={credits}`，**不再叫额度**，直到 B 节探针定性。
  定性之后按结论改名（是额度就改回；不是就写它真实的含义）。
- `credits()` 抛错不影响生成流程，只记日志。

### B. 探针 `tools/12-Flow-Credit-Probe.bat` + `tools/flow_credit_probe.py`

在指定账号（默认 TIK-D1；参数可换 TIK-L1）上按顺序做，每一步前后都 `nzlxg`，并记下 eb1hJf/ogiZ0b 响应里的
`[1]`：

| 步 | 动作 | 记什么 |
|---|---|---|
| 0 | 连上，`nzlxg` | 余额 B0 |
| 1 | 生图 ×1，`GEM_PIX_2`，参考图用 `data/` 里任意一张已上传过的 | B1；ogiZ0b 响应有没有任何「cost」样的字段 |
| 2 | `nzlxg`，间隔 20 秒再 `nzlxg` | B2 / B2'（看有没有延迟扣费） |
| 3 | 视频 ×1，`veo_3_1_i2v_lite_low_priority`，8s，提示词一句话 | B3；eb1hJf `[1]` |
| 4 | 轮询到完成（jwpduf 状态 3），完成后立刻 `nzlxg`，再等 60 秒 `nzlxg` | B4 / B4' |
| 5 | **换到另一个账号**（TIK-L1）只做 `nzlxg` | B5，和 B4' 并排 |

输出成表写进 `data/capture/probe57/result.md`，并复制到报告里。结论只能是下面三种之一，报告里必须选一个：

- `[1]` 不是额度（B0…B4' 全程不变而 `[1]` 变了，或反之）；
- 是额度且 Orante 的生图/视频**不扣**（B0=B1=B2'=B4'）；
- 是额度且**某一步扣了**（点名是哪一步、扣了多少）。

B5 和 B4' 相等 → 家庭计划共享一个池，报告里写明。

### C. 手动 UI 对照（用户做 3 分钟，你写好脚本和说明）

- `tools/Capture-Flow.bat` 已经能录。补一个 `tools/flow_capture_parse.py`：读最新的 `flow_capture_*.jsonl`，
  把每条 `ogiZ0b` / `eb1hJf` 的**模型 id、`[1]`、请求里的数量/尺寸字段**和前后 `nzlxg` 打成表。
- 报告里给用户一段操作说明：开录 → 在 UI 里用「Nano Banana 2 Lite ×1」生 1 张图 → 用
  「Veo 3.1 - Lite [Lower Priority] 8s ×1」生 1 条视频 → 停录 → 双击解析。
- 解析结果要回答：**09-09 新构建下**，UI 这两个 0 crediti 选项发的模型 id 是什么？
  和 `MODEL_FAMILY` / `IMAGE_MODELS` 里的常量对得上吗？`Nano Banana 2 Lite` 的 id 是什么
  （0907 抓包时还没有这个模型，常量表里没有它）？

### D. 硬闸：不管探针结论如何，都要上

1. **模型 id 白名单** `FLOW_FREE_MODEL_IDS`：目前 `veo_3_1_i2v_lite_low_priority`、
   `veo_3_1_i2v_s_lite_4s_low_priority`、`veo_3_1_i2v_s_lite_6s_low_priority`、`GEM_PIX_2`、`NARWHAL`，
   C 节抓到 `Nano Banana 2 Lite` 的 id 后加进去。**不在白名单的 id 一律拒绝提交**，
   抛 `OranteError("这个模型会扣额度，已拦下", …)`；`config` 里 `flow.allow_paid=true` 才放行。
2. **账号模型下拉**（设置 → 账号）：只列 `Veo 3.1 - Lite [Lower Priority]`，其余三档灰掉并标「扣额度」，
   `flow.allow_paid` 打开才能选。现有账号表已经全是 Lite LP，不用迁移。
3. **余额守卫**（探针证明 `[1]` 或 `nzlxg` 是额度之后启用；证明不是就把这条改成只记录不拦）：
   Job 开始记 B0，之后每次查到余额 < B0 → **立刻停止本 Job 的一切提交**，段状态不动，
   顶部红条 `Flow 余额从 B0 降到 B，Orante 已停止提交，先查 57 号报告`，并把最近 5 条请求的
   模型 id 和响应 `[1]` 打进日志。守卫不误伤：手动在 UI 里生成也会让余额降，所以红条上给一个
   「我知道，继续」按钮，按了本 Job 不再拦。
4. **生图数量**：`gen_images(n=4)` 现在是 4 次 ×1 调用，保持；**永远不要**发 ×2/×3/×4 的单次请求
   （UI 里 ×1 才显示 0 crediti，多张的计价没核过）。代码里断言 inner 的数量位是 1。

### E. 测试 `tools/test_spec57.py`（**先设 ORANTE_DATA**，全部用 FakeRPC，不碰真账号）

- `credits()` 对 `[[8611, …]]` 返回 8611；对垃圾返回 None 且只打一次日志。
- `model_id_for("Veo 3.1 - Fast", 8)` → `submit` 抛「这个模型会扣额度」；`flow.allow_paid=true` 才放行。
- 白名单外的生图 key 同样拒绝。
- 余额守卫：FakeRPC 让第二次 `nzlxg` 返回 B0-10 → `submit` 抛停止、Job 状态是「已拦停」而不是 failed；
  「继续」标记置位后不再拦。
- `gen_image_once` 的 inner 数量位断言为 1（改成 2 测试要红）。
- 日志里不再出现「剩余额度」四个字（grep 断言），直到 B 节定性后再改。
- 回归：`python -m pytest tools/`（整目录）全过。

## 验收

1. 报告第一节是 B 节探针的表和三选一结论；第二节是 C 节解析出的当前构建模型 id 对照表。
2. 任一 Flow 任务开始时日志有 `账户余额 N（nzlxg）`，结束时再有一次，两者相等（或报告里解释差值来自哪一步）。
3. 把某账号模型手动改成 `Veo 3.1 - Fast` 再点生成 → 被拦，红字说明；`flow.allow_paid=true` 才能过。
4. 「剩余额度」四个字从日志里消失，换成有据可依的文案。
