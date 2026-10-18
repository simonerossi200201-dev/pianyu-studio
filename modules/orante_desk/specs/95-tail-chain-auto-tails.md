# 规格 95 · 尾帧接龙：收片后自动截尾帧、登记成下一张图、接着生成；找不到 ffmpeg 要说清楚

日期：2026-09-26。用户：④ 生成选了尾帧接龙，生成了第一个视频之后就不继续了，尾帧检查台也是空的。
期望：每段收片后自动把尾帧截下来，**一一对应原视频、按顺序编号命名、直接纳入这个任务的图序**，
下一段就用这张尾帧起头；描述语默认沿用上一段，除非用户选「每次尾帧都使用新的描述语」（那就自动给每张新图写）。
Grok 和 Flow 都要一样。

## 根因：ffmpeg 没了

看用户机器上 T0083 的作业日志：

```
✓ s001 收片 T0083-s001-p01.mp4
· s001 尾帧：s001 抽尾帧失败
还在等前置：s002 s001 的尾帧没抽出来（检查台可重抽）
```

`clips/_tails/_scan_s001/` 是空目录——10 帧一帧都没抽出来。再看 `verify` 给 take 记的 `frame_ahash` 是空串，
说明 `checks.find_ffmpeg()` 返回空：**这台机器上 Orante 找不到 ffmpeg**（9 月 13 日之前还能找到，之后所有任务都没有尾帧）。
Orante 自己不带 ffmpeg，只查 `Orante/bin` 和 PATH；分发出去的用户机器上更不会有。
接龙的调度链路（收片 → `after_collect_chain` 抽尾帧 → `stamp_waits` → `promote` 放行下一段）本身是通的，
卡就卡在第一环：没有尾帧文件，`_chain_wait_reason` 永远返回 `tail_missing`，s002 永远「等前置」。

日志只打了 `e.message`（「抽尾帧失败」），把真正的原因（hint 里的「找不到 ffmpeg」）吞了，所以用户看不出来。

## A. ffmpeg：找得到、装得上、说得清

1. **查找顺序**（`checks._ffmpeg_candidates`）：设置 `tools.ffmpeg`（可填文件或目录）→ `Orante/bin` →
   pip 装的 `imageio-ffmpeg` → PATH → Windows 常见位置（winget / choco / scoop / `C:\ffmpeg\bin` …）→ 旧 grok_pipe 配置。
2. **没找到不长期记住**：以前 `_FFMPEG = ""` 一记就是整个进程；现在只记 20 秒，用户拷进 bin / 装好立刻生效。记住的路径没了也自动重找。
3. **随包自带**：`requirements.txt` 加 `imageio-ffmpeg`（约 30 MB 的 wheel，带 ffmpeg 二进制）；`Orante2.bat` 的依赖检查加 `imageio_ffmpeg`，
   老安装下次双击 bat 会自动补装。
4. **一键安装**：`POST /api/env/ffmpeg/install` 用本 venv 的 pip 装（pythonw 下换 python.exe 跑）。
   首次设置向导的环境自检多一行「ffmpeg（截尾帧用）」（软项，不挡下一步，带「一键安装」按钮）；
   ④ 生成在接龙 / 锚点模式下找不到 ffmpeg 时顶部横幅：「一键安装 ffmpeg」「重新检测」，并写明另两种修法。
   设置 → 生成方式 多一项「ffmpeg 路径」。
5. **开跑前拦住**：接龙 / 锚点模式 `generate()` 在连引擎之前查 ffmpeg，没有就抛 `OranteError`（不烧额度、不出一段孤片），
   hint 写三种修法。
6. **原因写进去**：抽帧失败把 `message：hint` 存到 `seg.chain.tail_error`，等待理由变成
   「s001 的尾帧没抽出来：找不到 ffmpeg：…」，检查台卡片头部也显示这句。

## B. 尾帧登记成图（用户要的那套流转）

`tails.register_tail_image(task_dir, m, seg, policy)`，在 `after_collect_chain` 抽完尾帧后调用：

- 尾帧文件仍是 `clips/_tails/sNNN.jpg`（检查台、提清、手挑都认这个路径）；同时**拷一份进图池**：
  `images/pNN-sNNN尾帧.jpg`，p 号顺着现有图池排（p01~p03 之后就是 p04），`origin="tail"`、`tail_of=NNN`、`src` 写成片名。
  ② 多模态里这张图带「s001 尾帧」标签；③ 编排 / 成片名（`T0083-s002-p04.mp4`）都能看出 s002 起于 s001 的尾帧。
- **下一段配这张图**：`nxt.image = pNN`（下一段没换图起头、还没提交时）。`ensure_chain` / 改起头图不再把这种段打回起头图。
  提交时 `apply_chain_ref` 把段的配图对齐到**真正用的参考**（尾帧严重穿帮退到更早尾帧 / 起头图时，配图跟着退），配图 = 实际首帧，永远一致。
- **重抽同一段**（重生、手挑、提清）只更新那张图和副本，不新开号。`sync_tail_image` 在提清、手挑后把副本换掉。
- **描述语**：
  - `never`（**新默认**，「沿用上一段」）：新图的 desc 直接抄本段用的描述语，`desc_from=p01`；提交时不调 Gemini。
  - `always`（「每次尾帧都写新的描述语」）：收片时就对这张新图跑 `describe_file`，`desc_from=gemini`；提交时直接用，不写第二遍。写失败沿用，不留空。
  - `auto`（「变化大才重写」，老行为）：提交时按 aHash 距离决定；重写的写回那张图。
  - 用户在 ② 里手改过这张图（`desc_manual`）→ 一律照用。
  - 老任务存的 `auto` 是旧代码默认、不是用户选的，一次性迁成 `never`（`chain.policy_v95` 标记）；用户明确选过 `always` 的不动。
  - 设置 → 生成方式 多一项「尾帧接龙：尾帧描述语」定新任务默认；④ 里每个任务仍可单独改（顺序改成：沿用 / 每次重写 / 变化大才重写）。
- 检查台：第一段（或换图起头）左格显示「起头图 p01」而不是「s000 尾帧」；后面的段显示「s001 尾帧 = p04」；
  卡片头部「s002 尾帧接龙 · 首帧 p04」；接龙段也能「手挑尾帧」（以前 `_bridge_of` 只认接片，接龙点了就报错）。
- 接龙提示词每次现拼：以前 `final_prompt` 拿上一轮存的 `prompt_final`（已带延续句）当 body 再包一层，重试会叠两个头。
- Grok / Flow 同一条路：收片在 `generate()` 里是同一个分支，引擎无关。

## C. 顺手：限流「第一发就被拒」

用户自己的机器 9 月 21 日以后 Flow 没再碰到 `UNUSUAL_ACTIVITY`（J00479/J00480 一口气交了 40 / 35 段）。
「一直限流」是分发用户那边。同样的错误码有两种病：
- 这轮连打之后被拒 → 提交太快，91/94 号的间隔和探路管这个；
- **这轮第一发就被拒**（之前 10 分钟没交过）→ 跟快慢无关。多半是 ① Google 判那个刚建的空白专用 Chrome「异常」（reCAPTCHA 分数低），
  ② 账号没有无限模型资格（Google 新规：只有 Ultra 本人，家庭组成员不算）。
`mark_throttled` 的 reason 多记 `submitted_before` / `since_last_sec`，`throttle_info` 算出 `first_shot`；
日志和 ④ 横幅在第一发被拒时写明这两种可能和各自的验证办法（专用 Chrome 里手动生成一条；看 Flow 下拉有没有 Lite [Lower Priority]）。

模型名：0926 Flow 网页下拉是 Omni 1.1 Flash / Veo 3.1 - Lite / Fast / Quality / Lite [Lower Priority]。
Orante 用的四个名字和 id（`veo_3_1_i2v_lite_low_priority` 等）都还在，接口不用重抓；
Omni 1.1 Flash 是新模型，id 没抓过，要用得先跑 `tools/Capture-Flow.bat` 抓一次。

## 验收

`tools/test_spec95.py`：ffmpeg 查找顺序 / 负缓存 / 一键安装走 pip / 自检软项；接龙没 ffmpeg 开跑前拦住；
尾帧登记编号与命名、下一段配图、重抽不新开号、ensure_chain 不打回；always 调 describe、失败沿用；
resolve_chain_desc 读登记图、手改优先、不重复写；提示词不叠头；等待理由带原因、检查台第一段显示起头图；
整条链三段跑通（p04/p05/p06，成片名 `-s002-p04.mp4`）；严重穿帮不配给下一段；策略默认与迁移；第一发限流诊断；模型名核对。
真 ffmpeg（云端 imageio-ffmpeg）对合成 mp4 跑过一遍 `after_collect_chain`：倒数第 3 帧、登记 p04、s002 改配。
Playwright 真页面：④ 横幅、三项描述语单选、检查台「起头图 p01」「s001 尾帧 = p02」、② 的「s001 尾帧」标签，无脚本错误。

改前备份：`*.bak-0926chain`。
