# 51 · 重生/续跑后 Grok 页上明明有视频却永远不收

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/50-grok-post-not-found.md`
> 和它的报告，再读 `orante/engines/grok.py`（`__init__` 的那几个 dict、`_poll_one`、`_page_of`、
> `download`）、`orante/legacy/grok_pipe.py`（`FIND_VIDEO_JS`、`is_own_video`、`probe`、
> `goto_own_post`、`download_one`）、`orante/services/generate.py`（第 126 行 `inflight` 的续跑、
> 第 389~503 行轮询）、`tools/9-Grok-Account-Probe.bat` / `tools/1-Grok-Probe.bat`（学它怎么连 CDP），
> 再读本规格。**先做完 50。** 改前备份 `*.bak-0912resume`。
> 报告写到 `specs/reports/51-grok-resume-collect.md`。中文注释。
> **第 A 节的诊断要先做、先跑、把结果贴进报告，再动手改代码。** 不许凭猜测改 FIND_VIDEO_JS。

## 现象

T0015 s010（锚点+接片，15s，双图）：J00155 里被 Post not found 踢来踢去（规格 50），
用户点了「重生」→ J00157。日志：

    [08:49:08]  · b8901485 标签页丢了，已按会话重开
    [08:49:09]  … 在生成 1 条，待提交 0，已收 0
    （之后每 6 秒一条，一直到 15 分钟超时）

同时 Chrome 里那个标签页停在 `grok.com/imagine/post/b8901485-…?conversation=7efea7c6-…`，
**视频已经生成、正在播放**（有「Riattiva audio」静音角标、能拖进度）。
既没有「页面上有 N 个视频」的警告，也没有失败，就是静悄悄地永远 pending。

## 已经确认的链路

1. 「重生」并没有重新提交。`generate.py:126`
   `inflight = {s["gen_id"]: s for s in m.segments if s["status"] == "submitted" and s.get("gen_id")}`
   把上一个 Job 留下的 `submitted` 段直接接过来轮询（续跑设计，见文件头注释）。
2. 引擎实例按账号缓存，`self._conv[b8901485]` 还在，但 `self._pages` 里那页已经关了，
   于是 `_page_of` 走 `ctx.new_page()` + `goto_own_post` **深链**打开详情页 → 「已按会话重开」。
   这次没被踢（说明 50 里的 not found 至少有一部分是暂时性的）。
3. 之后每轮 `_poll_one` → `probe` → `FIND_VIDEO_JS`。结果既不是 READY 也不是 FAILED，
   而且**没有任何日志**。`probe` 只有两条静默路径：
   - `strong` 为空（0 个 `<video>`，或它的 `src` 是 `blob:`——`FIND_VIDEO_JS` 的 `uniq` 把 blob 过滤了，
     或 `<video>` 不在 `looksLikePost` 找到的那个容器里）→ `IS_GENERATING_JS` 假 → `IS_FAILED_JS` 空 → `未知`；
   - 恰好 1 个 `strong`，但 `is_own_video` 不认：地址里没有 `.mp4`、或主机不是 `assets.grok.com`
     且路径里没有 `/generated/` → 返回 `S_SUBMITTED, ""`，一句话不说。
   前 6 段能收，是因为它们一直在**提交时的原标签页**上；深链重开的详情页，播放器/资源地址
   很可能不一样。到底是哪一条，A 节的诊断说了算。

## 要做的

### A. 先诊断：`tools/11-Grok-Post-Dump.bat` + `tools/grok_post_dump.py`

- 连账号的 CDP 端口（TIK-G-SIMONE 是 9251；从 `orante/accounts.py` 或 `grok-account.txt` 取，
  命令行可覆盖），在所有 context 的所有页里找 URL 含 `/imagine/post/` 的那一页（多页就全 dump）。
- 对每一页输出到 `tools/grok-post-dump.txt`：
  - `page.url`；
  - 每个 `<video>`：`src`、`currentSrc`、`<source>` 列表、`readyState`、`duration`、
    `getBoundingClientRect()`、是否可见、它往上 8 层祖先的 `tagName.className`（判断 `looksLikePost` 会不会命中）；
  - `performance.getEntriesByType('resource')` 里 `name` 含 `grok.com|x.ai|mp4|video|m3u8|generated` 的条目；
  - `FIND_VIDEO_JS`、`IS_GENERATING_JS`、`IS_FAILED_JS` 三个现成 JS 的原样返回值；
  - `is_own_video(u, post, True, True)` 对每个 strong 的判定；
  - `document.body.innerText` 前 600 字。
- **现在就跑一次**：用户的 Chrome 里 T0015 s010 那页大概率还开着（J00157 或它超时后的残留）。
  结果整段贴进报告。如果那页已经没了，报告里写明，并让用户下次卡住时双击这个 bat。

### B. 按诊断结果修 `probe` 链，但以下几条不管诊断结果如何都要做

1. **不许静默。** `_poll_one` 收到 `S_SUBMITTED`/`未知` 而页面上又确实有 `<video>` 时，
   每段**每 5 轮**打一条：`· s010 看见 N 个 video，但没有一个过闸：<每个 src 前 80 字 / blob: / 空>`。
   `probe` 返回值加第三项 `detail`（dict：videos 列表、strong、weak、why），供上层打日志。
2. **`is_own_video` 放宽扩展名**：主机是 `assets.grok.com`、或路径含 `/generated/` 的地址，
   **不再要求** `.mp4`（签名地址常常没有扩展名）。`data.x.ai` 宣传片照旧不认。
3. **`blob:` 兜底——从网络层拿真实地址**：
   - `_page_of` 新开页、以及 `submit` 里的原页，都挂 `page.on("response", …)`：
     收 `content-type` 以 `video/` 开头、或 URL 含 `assets.grok.com` + `/generated/` 的响应，
     记到 `self._netvid[postId] = [url…]`（去重、保序）。
   - `probe` 之外，`_poll_one` 再看一眼 `performance.getEntriesByType('resource')`（页内 JS），
     同样规则筛出候选。
   - `strong` 为空或全是 `blob:` 时，用这两路的候选：**恰好 1 个** → 当 `unique_strong` 走 READY；
     多个 → 用 `url_matches_post` 筛；仍判不出 → 打日志，继续等。
4. **最后兜底——点页面上的下载按钮**：详情页右侧竖栏有下载图标。上面三路都拿不到地址、
   但页面上确有可播放的 `<video>`（`readyState >= 2`、`duration > 0`）且已经这样连续 5 轮 →
   `with page.expect_download(timeout=60000)` 点下载，把文件存成 `dest`，来源标 `"下载按钮"`，
   同样走 `verify.after_download`（首帧 aHash 自证仍然要过）。按钮定位用 `aria-label`/`title`
   含 `download|scarica|下载` 或图标的 `svg` 祖先，找不到就日志一条、放弃这路。
5. **会话 Id 要落盘**：`submit` 抓到的 `conv` 写进 `seg["gen_conv"]`（generate.py 在 `seg["gen_id"] = gid`
   旁边加一行 `seg["gen_conv"] = eng.conv_of(gid)`），续跑时 `generate.py` 在 `eng.poll` 之前
   调 `eng.adopt(gid, seg)` 把 `gen_conv`、`postId`、`image`/`尾帧` 路径塞回 `_conv/_rows`。
   这样软件重启后续跑也不会「又没有会话Id，导不回详情页」。
6. **续跑的 `_submitted_at`**：`adopt` 时设成 `time.time()`，让「3 分钟刷一次」的自救和
   规格 50 的被踢计数从接手那一刻算，而不是 `age = 现在 - 0`。

### C. 测试 `tools/test_spec51.py`（FakePage，**先设 ORANTE_DATA**）

- `<video src="blob:…">` + 网络层记到 1 个 `assets.grok.com/…/generated/…` 地址（无 `.mp4`）→ `_poll_one` 返回 done，url 是那个地址。
- 1 个 `<video>` 的 src 是 `https://assets.grok.com/users/u/generated/abc` （无扩展名）→ READY。
- `<video src="https://data.x.ai/xxx.mp4">` → 仍然不认。
- 0 个可用地址、`<video readyState=4 duration=15>` 连续 5 轮 → 触发下载按钮兜底（FakePage 记录点击 + 假 download）。
- 连续 5 轮 pending 且页面有 video → 日志里出现「看见 N 个 video，但没有一个过闸」恰好 1 次。
- `adopt` 后 `_conv/_rows/_submitted_at` 都有值；`_page_of` 不再返回 None。
- 回归：`python -m pytest tools/` 全过（49 之后整目录是安全的）。

## 验收

1. 报告第一节是 A 的 dump 原文，并明确写出 s010 那页走的是哪条静默路径。
2. 重现：对一个 `submitted` 的段停止 Job → 再开一个 Job 续跑（不重生）→ 深链重开详情页后
   **60 秒内**收片，来源写明是「strong / 网络层 / 下载按钮」哪一路。
3. 软件完全重启后续跑同一段，也能收（会话 Id 从 manifest 来）。
4. 收到的片子过首帧 aHash 自证；T0015 其余段和 T0017（Flow）不受影响。
