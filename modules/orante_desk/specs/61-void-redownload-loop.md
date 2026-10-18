# 61 · 无限下载循环：Flow 没有 forget_url，非接片作废又没有上限

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/52-grok-bridge-single-image.md`、
> `specs/53-bridge-fail-accounting.md` 和它们的报告，再读
> `orante/services/verify.py`（`_apply_void` 第 637~666 行，特别是最后那两行非接片分支）、
> `orante/services/generate.py`（第 396~425 行 `done` 分支里 `vr.get("voided")` → `continue` 那一段、
> 第 491 行的 `GEN_TIMEOUT` 判断）、`orante/engines/grok.py`（`forget_url` 第 960 行、`_rejected` 第 252 行）、
> `orante/engines/flow_api.py`（**没有** `forget_url`）、`orante/engines/base.py`，再读本规格。
> **这一份插队排第一**，用户正卡在死循环里。
> 改前备份 `*.bak-0913loop`。报告写到 `specs/reports/61-void-redownload-loop.md`。中文注释。

## 现象

T0020/T0021 的 s024，每 7 秒一轮，跑了几百次停不下来：

    ⬇ T0021-s024-p03-1789286458.mp4  2056 KB
    ✗ s024 首帧对不上p03（距 185），已作废，重新取地址
      … 在生成 1 条，待提交 0，已收 0
    ⬇ T0021-s024-p03-1789286465.mp4  2056 KB
    ✗ s024 首帧对不上p03（距 185），已作废，重新取地址
    …

**每次下的都是同一个文件**（大小恒为 2056 KB、距离恒为 185），作废了又去取同一个地址。
永远不会超时、永远不会失败、永远不会停。

## 根因（两条，缺一不可）

### 甲：`FlowEngine` 根本没有 `forget_url`

`generate.py` 作废之后这么写：

    if vr.get("voided"):
        forget = getattr(eng, "forget_url", None)
        if callable(forget):
            forget(gid, url)

`forget_url` **只有 `grok.py` 有**（第 960 行，把地址塞进 `self._rejected[gid]`，
`poll` 再拿到同一个就跳过）。`flow_api.py` 里没有这个方法，`getattr` 返回 None，
于是**什么都没发生**——下一轮 `poll` 原样返回同一个 URL。

### 乙：非接片作废没有任何计数和上限

`_apply_void` 最后两行：

    # 非接片：生成物还在那边，打回 submitted，gen_id 保留，重新取 URL
    seg["status"] = "submitted"

接片那半边 53 号给了 `mismatch_count` 和两次出局，**非接片这半边一个计数器都没有**。
而且 `generate.py` 里 `voided` 走的是 `continue`，**跳过了 `GEN_TIMEOUT` 那条 `elif`**，
所以连 15 分钟兜底都不会触发。submitted → poll → 同一个 URL → 下载 → 距 185 → 作废 →
submitted，闭环。

### 顺带：距 185 说明重试本来就没意义

16×16 aHash 满值 256，185 意味着这条片子和 p03 毫无关系。同一个 gen_id 再取一百次
也还是这条。**这不是「地址取错了」，是 Flow 那边这个 gen_id 对应的就是别的东西。**

## 要做的

### A. `FlowEngine.forget_url()`（`flow_api.py`）

- 照 `grok.py` 的形状实现：`self._rejected: dict[str, set]`，`forget_url(gen_id, url)`
  把地址加进去；`poll` / 取地址的地方拿到已作废的 URL 就当没出片，继续等。
- 顺手把 `forget_url` 提到 `engines/base.py` 的 `Engine` 基类上做成空实现 + 文档，
  **以后新引擎不实现就会被基类挡住**，不会再出现「靠 getattr 静默跳过」这种坑。
  `generate.py` 里那段 `getattr(eng, "forget_url", None)` 改成直接 `eng.forget_url(...)`。

### B. 非接片作废也要计数、要有上限（`verify.py::_apply_void`）

- 新增 `wrongclip_count`，非接片判 `wrong_clip` 时 +1：
  - 1 次 → 照旧 `submitted` 重新取地址（有 A 节之后这次是真的换地址了）；
  - **2 次 → `status = "planned"`、`gen_id = None`**，整条重新提交（换个 gen_id 才可能换内容）；
  - **4 次 → `status = "failed"`**，`error` 写 `首帧一直对不上配图（距 N），换图或手动检查`。
- `wrongclip_count` 在这一段成功收片时清零。

### C. 同一个文件出现两次就立刻升级（最硬的那道闸）

计数器还是慢。加一条**立即判定**：

- 每次作废时记下这条 take 的 `sha256`（`after_download` 已经在算了）到
  `seg["voided_sha"]`（保留最近 3 个）。
- 新下载的文件 sha256 **命中 `voided_sha`** → 说明地址/内容根本没变，
  **不再走「重新取地址」**，直接跳到 B 节的下一档（submitted → planned → failed），
  日志写 `✗ s024 又下到同一个文件（sha 前 8 位），重试无意义，直接换一档`。
- 这条对接片和非接片都生效。

### D. 作废路径不许绕过超时（`generate.py`）

- `vr.get("voided")` 那个 `continue` 之前，先做一次 `GEN_TIMEOUT` 检查；
  或者把超时判断提到 `for gid, r in res.items()` 循环开头，对所有分支统一生效。
- 另外加一个**每段作废次数上限**的兜底：同一个 `gid` 在本 Job 内作废超过 6 次
  → 无条件判 failed，日志点名。这是防止以后再出现别的闭环。

### E. 日志要能看出在原地打转

- 现在每轮都打一模一样的三行，看不出是不是在循环。改成：
  同一段连续作废时只打第一次完整信息，之后合并成
  `✗ s024 首帧对不上 p03（距 185）× N 次，同一个文件`，N 每次递增，刷屏立刻可见。

## 测试 `tools/test_spec61.py`（**先设 ORANTE_DATA**，FakeEngine）

- `FlowEngine.forget_url` 存在且生效：作废一个 URL 后 `poll` 不再返回它。
- `Engine` 基类有 `forget_url`；`generate.py` 里不再用 `getattr` 探测（grep 断言）。
- 非接片连续 wrong_clip：1 次 → submitted；2 次 → planned 且 `gen_id` 为空；
  4 次 → failed 且 error 含「首帧一直对不上配图」。
- sha256 命中 `voided_sha` → 直接降档，不走 submitted。
- 作废 6 次 → 无条件 failed。
- 作废路径也会被 `GEN_TIMEOUT` 判掉（构造一个 started 时间很早的 gid）。
- 日志合并：连续 5 次作废只出现 1 条完整行 + 计数行。
- 回归：`python -m pytest tools/` 全过，`test_spec52` / `test_spec53` 不回退。

## 验收

1. 造一个「下载到的片子和配图完全不符」的场景 → **最多 4 轮**就判 failed，不再无限转。
2. 同一个文件第二次下下来 → 立刻降档，日志点名「又下到同一个文件」。
3. Flow 任务作废一次之后，下一轮拿到的是**不同的地址**（或者干脆等下去），不再是同一个。
4. 日志不再出现几十行一模一样的「已作废，重新取地址」。
