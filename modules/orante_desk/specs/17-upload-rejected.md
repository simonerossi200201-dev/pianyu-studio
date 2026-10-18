# 规格 17 · Flow 拒收参考图：分清「图被拒」和「接口坏了」，直接告诉用户换图

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先读 `specs/00-architecture.md`，再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的」写到 `specs/reports/17-upload-rejected.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。**改之前把 `orante/engines/flow_api.py`、`orante/services/expand.py` 各备份成 `*.bak-0907a`。**

涉及文件：`orante/engines/flow_api.py`、`orante/services/expand.py`、`orante/services/generate.py`、`orante/i18n/zh.json`（如有新文案）。备份也要包括 `generate.py`。

## 背景（真实案例，2026-09-07 凌晨）
T0008 扩图时 p03.jpeg 上传（rpc `maseQ`）三次全失败，界面报「Flow 接口 maseQ 调用失败 ({'status': 200, 'payload': None, 'err': [13], 'raw': ...})」，用户完全看不懂。事后验证：
- 同任务的 p02（同样 1536×2752、2.5 MB、同样 Gemini 出图）上传成功；
- **同一张 p03 缩到 327×600 后上传成功**；
- 全尺寸 p03 反复被拒，第一次 `err=[3]`，后两次 `err=[13]`。

结论：**Flow 服务端按内容拒收了这张图**（大概率是人脸/名人识别，低分辨率时不触发），上传接口本身没坏。后两次的 `[13]` 是我们自己的 bug：`_rpc` 重试时复用了同一份 `inner`，里面的 reCAPTCHA token 是一次性的，第二次起必然失败。

## 要做的事

### 1. `flow_api.py` · 重试要换新 token
- `_rpc(rpc, inner, retries)` 改为接受 `inner` **或** 一个无参函数 `make_inner()`；是函数就每次重试都重新调用，这样每次都是新 token。
- `upload_image`、`_gen_inner` 的调用方（`submit`）、`gen_image_once` 三处都改成传函数，函数内部重新 `self._recaptcha(...)` 再拼 inner。其他 rpc（`as29s`、`jwpduf`、`nzlxg`、`pGCYOe`）不带 token，照旧传列表。

### 2. `flow_api.py` · 上传被拒要认出来
在 `upload_image` 里：
- 把 `_rpc` 对 `maseQ` 的失败结果拿到手（改 `_rpc` 让失败时抛的 `OranteError` 带上 `err` 码，或者给 `_rpc` 加一个 `raise_=False` 的返回模式，你自己定）。
- 若失败且 `err` 码是 `[3]` 或 `[13]`、HTTP 200、payload 为空（就是被服务端明确拒掉，不是断网/登录态丢失/HTTP 非 200 那类）：
  1. 用 PIL 把原图缩到**宽 512**（等比，JPEG 质量 85）存到 `data/tmp/probe-<sha1前8位>.jpg`，用同样的方法再传一次（**这一次不写 `_cache`**）。
  2. 缩图传成功 → 说明接口通、是这张图的内容被拒：抛 `BlockedImage(f"Flow 拒收了 {img.name}（缩小后能传，全尺寸不能，多半是人脸/名人识别）")`。传上去的那个探测 mediaId 顺手用 `pGCYOe` 归档掉，归档失败忽略。
  3. 缩图也失败 → 才是真的接口问题：抛 `OranteError("Flow 上传接口不可用", "缩小的探测图也传不上去；刷新那个 Flow 标签页、确认没被登出，再试。原始错误：" + raw[:200])`。
- 日志一律人话：`  ✗ p03.jpeg 被 Flow 拒收（缩图可传、原图不可）→ 换一张图` / `  ✗ 上传接口不可用（探测图也失败）`。**不要再把 `{'status': 200, 'payload': None, ...}` 这种原始字典塞进用户能看到的错误标题里**，原始内容只进 `OranteError` 的第二个参数（详情）和日志。
- `gen_images` 里 `self.upload_image(ref)` 抛 `BlockedImage` 时不要吞掉，原样往上抛（现在 `except BlockedImage` 只包着 `gen_image_once`，注意不要把上传也包进去）。

### 3. `expand.py` · 参考图被拒不算任务失败
`expand` 和 `refill` 里每处调用 `eng.gen_images(...)` / `_gen_one(...)` 的地方：
- `except BlockedImage as e`：
  - 把这张参考图 `im["status"] = "blocked"`，`m.save(d)`，`events.emit("task.update", ...)`（和 generate.py 第 88–94 行的做法一致）；
  - `job.log(f"  ✗ 参考图 {ref_p} 被 Flow 拒收 → 已标 blocked，跳过它；点「补图」会用 Gemini 换机位重生一张")`；
  - `_prog(job, item=_item(None, None, "被拒", ref_p, 0, ""))`；
  - `continue` 到下一张参考图。**任务不失败**。
- 一轮下来如果所有参考图都被拒、一张没扩出来：`_prog(job, phase="done", current="参考图都被 Flow 拒收了，换张图或点「补图」")`，日志同句，正常结束（不抛）。
- `refill` 里被拒那张本来就是 blocked，优先用它自己当参考的逻辑（第 514 行附近）碰到 `BlockedImage` 时，改用其它 ok 主体当参考再试，别死循环。

### 4. 界面文案
扩图面板顶部那条红字现在显示的是 `OranteError.message`。按上面改完后，被拒的场景不会再有红字（任务是正常结束的），进度列表里那张参考图显示「被拒」；接口真坏时红字是「Flow 上传接口不可用」+ 详情。如果 zh.json 里有相关文案键，跟着补。

### 5. `generate.py` · 出片阶段反复失败的图也要认出来（第二个真实案例）
T0007 的 p14（扩图产出的 Gemini 图）上传正常、提交正常，但 s005/s006/s026 每次轮询都回 `status=4`——三次运行共 7 次提交 0 次成功；同任务的 p01 曾失败 1 次后重试成功。也就是说 `status=4` 有两种：偶发的（换次重试就好）和这张图必然失败的（多半是 Veo 在生成阶段做的真人/名人审核，跟上传审核不是同一道）。现在两种都只标 `failed`，用户只能一遍遍点重生。

- `flow_api.poll` 里 `state=="failed"` 时把**整条记录**（不截断）写进日志文件（`job.log` 那条可以只截 300 字，但另外 `self.log` 一行 `raw=` 全文，或者写到 `data/logs/flow-failed-<gen_id前8>.json`），并在记录里搜 `prominent|policy|violat|unsafe|celebrit|blocked|SAFETY|RAI`，搜到就把 `error` 写成 `"被拒：" + 那段文字`。这是为了下次能看到 Flow 到底说了什么理由——报告里请贴一条真实的失败记录全文。
- `generate.py` 收到 `state=="failed"` 时：
  - 段上记 `seg["fail_count"] = (seg.get("fail_count") or 0) + 1`；
  - 图上记 `im["gen_fail"] = (im.get("gen_fail") or 0) + 1`，图有任何一段成功收片就把 `im["gen_fail"]` 清零；
  - 当 `error` 含「被拒」，**或** 这张图 `gen_fail >= 3` 且从没成功过（`m.segments` 里没有一段用它 `collected`），就把图标成 `blocked`、这张图的所有未完成段标 `blocked`（和第 88–94 行被拒的处理一样），日志：`  ✗ p14 连续 3 次生成失败、从没成功过 → 判定被 Flow 拒了，标 blocked；点「补图」换一张`；
  - 否则维持现在的 `failed`（下次重生会再试）。
- 「重生」（`regenerate`）对 `blocked` 段直接跳过并在日志里说「s005 的图 p14 已被拒，先补图」，不要再提交。
- `expand.py` 的 `refill`（补图）本来就是处理 `blocked` 图的：确认它对这种「出片阶段被拒」的图同样能跑——用其它 ok 主体当参考、换机位、镜像，覆盖同名文件、打回 planned。补出来的新图上传若又被拒（第 2 节的 `BlockedImage`），继续换参考再来，最多 3 轮。

### 6. 全自动：被拒 → 自动换图 → 自动重提，不要人工介入（用户 2026-09-07 明确要求）
第二个真实案例 T0008（J00057 + J00060）：缺的 9 段全部落在 3 张图上——p03、p15 上传被拒（两次运行、两个账号都一样），p04 上传能过但出片阶段 10 次提交 0 次成功。用户对着 9 个「缺片」一遍遍点重生，什么都不会变。目标：**这类情况用户一次都不用点。**

- `generate.py` 的主循环里（`regenerate` 走的也是它）：一旦某张图被判 `blocked`（第 2 节上传被拒，或第 5 节出片反复失败），**不要等任务结束**，当场调用 `expand.refill_one(job, m, d, p, eng, gemini)`（把现在 `refill` 里"补一张"的那段抽成一个可以单独调用的函数）：
  1. 参考图：优先挑**同任务里已经有段成功收片**的 ok 主体图（`m.segments` 里有 `collected` 的那些），按成功段数从多到少；没有就任意 ok 主体。
  2. 提示词：直接用编排面板里现成的那条补图预设（`_prompt_block(refill=True)`），不额外问 Gemini。
  3. 用当前这个 Flow 账号的 `gen_image_once` 生 1 张，落盘后**必须水平翻转**（`_flip_to`，无视 `expand.flip_replacements` 的开关——用户要求默认翻转以避免雷同；把那个开关删掉或者默认锁 True 并在报告里说明）。
  4. 新图覆盖同名文件（还叫 p15），旧文件**直接删除**，不进 `_弃用`、不留备份（用户原话："被拒的图直接删掉不要了"）；`im["status"]="ok"`，`im["origin"]="auto_refill"`，`im["flipped"]=True`，`im["sha1"]` 更新，`gen_fail` 清零；配图轨、段的 image 指向不动；描述语沿用原来的（同一人物同一现场）。
  5. 这张图的所有 `blocked` 段打回 `planned` 并**塞回本轮的提交队列**，继续跑。
  6. 每张图最多自动换 **3** 次；三次仍被拒就保持 `blocked`，日志一句「p15 自动换了 3 次仍被拒，先放着」，任务结束时汇总在「本轮」那行：`被拒 2（已自动换图 2，放弃 0）`。
- `refill` 里上传新图又被拒（第 2 节 `BlockedImage`）的情况：换下一张参考再生，算作同一张图的一次尝试。
- 日志要让人看得懂发生了什么：`  ✗ p15 被 Flow 拒收 → 自动换图（参考 p19，第 1/3 次）` → `  ✓ p15 已换成新图（已翻转），s012/s016 打回重提`。
- 「补图」按钮保留（手动也能触发），但正常情况下用户不需要碰它。

## 验收
1. 用 T0008 的 p03 跑扩图：日志出现「p03.jpeg 被 Flow 拒收（缩图可传、原图不可）」，p03 状态变 blocked，任务不报错，若还勾了 p01/p02 则继续扩它们。
2. 用 T0008 的 p02 跑扩图：行为和现在一样，正常出图。
3. 人为把 `RECAPTCHA_SITE_KEY` 改错一位再跑：应报「Flow 上传接口不可用」而不是「被拒收」（改回来）。
4. `_rpc` 对带 token 的三个 rpc 每次重试都走一次 `grecaptcha.enterprise.execute`（日志里加一行 `  · 重新取 token` 或在报告里贴证据）。
5. `data/tmp/probe-*.jpg` 跑完可以留着，但 `flow_media_cache.json` 里不能出现探测图的条目。
6. T0007：对 s005/s006/s026 点「重生」——p14 已经累计失败 ≥ 3 次且从未成功，应直接判 blocked、日志提示补图，不再提交；然后点「补图」，p14 被新图覆盖、三段打回 planned，再重生能正常出片（或者新图又被拒时日志说清楚、继续换）。
7. 报告里贴一条 `status=4` 记录的完整原文，标出你认为是拒绝理由的字段。
8. T0008：对 9 个缺片段直接点「重生」，中途不碰任何东西——p03、p15、p04 应各自被判拒、自动换图（翻转）、段自动重提；最终日志里能看到每张图换了几次、9 段里收回来几段；`images/` 里旧的 p03/p15/p04 文件已不存在，同名新文件是翻转过的新图。
