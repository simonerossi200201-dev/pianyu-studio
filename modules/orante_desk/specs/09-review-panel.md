# 规格 09 · 收片面板 `ui/js/panels/review.js` + `orante/services/review.py`（节点 ⑤）

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先完整读 `specs/00-architecture.md`，再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**，需要就拷到 `orante/legacy/`。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的、需要后端补的」写到 `specs/reports/<本文件名>.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。

这是 Orante1 的主界面（`web/index.html` 里的视频墙 + 体检 + 重生抽屉，以及 `app/checks.py`），搬到 manifest v2 上。**交互和快捷键一模一样**（见 `说明文档/使用说明.md`「键盘」一节：J/K、空格、1/2/3/4/6/8/0 倍速、M、A、X、E、C、V/P、Esc）。

## A. 后端 `services/review.py`

- 拷 `app/checks.py` → `orante/services/checks.py`（改 import，不改逻辑）：四查（缺片、空/过小、时长与档位不符、两段同 md5）+ 音轨三查（无音轨 / 音频过短 / 静音，ffmpeg 找不到就跳过；ffmpeg 查找顺序：`ROOT/bin/ffmpeg(.exe)` → PATH → 老 `legacy` 里的 `find_ffmpeg`）。
- job `check`（task_id，不占账号）：跑体检，结果写进每段 `seg['check'] = {issues:[...]}`，manifest 落盘。
- 路由（只加）：`GET /api/tasks/{id}/review` → `{segments:[{s,text,duration,image,status,takes,check,prompt_final}], images, marks}`；`POST /api/tasks/{id}/marks {s, mark: true|false}`（标记存 manifest 每段 `mark` 字段——这一版真值就在 manifest，不另设 marks.json）；`POST /api/tasks/{id}/finish-check`：任务目录下有 `字幕成品/*.mp4` 就把任务 status 设 finished（自动归入过时），返回 `{finished: bool}`。
- job `regenerate` 已有（services/generate.py）：`{segs:[...]}`，Flow 和 Grok 都走它——**这是「重生支援 Grok」的落点，不要另写一套**。

## B. 前端 `panels/review.js`

- 左：任务下拉（status ≥ generating）+ 筛选（全部 / 有问题 / 已标记 / 缺片）+ 大小（3/5/7 列）+ `运行体检` + `重生…`。
- 中：视频墙。每卡：`<video>`（`/media/{tid}/{take.file}`，preload=metadata）、段号、图编号（带那张图的固定色小方块，和编排面板同一套配色函数——把配色函数放 `ui/js/colors.js` 共用，index.html 里加一行 script）、时长档、体检结果小标、`X` 标记态。一次只播一个；连播默认开；倍速默认 4×。
- 卡片底部「提示词」展开：第一块是 `prompt_final`（真正发出去的），右上「复制」。
- 右侧「重生」抽屉：候选 = 已标记 + 缺片 + failed，默认全选；一个按钮 `重生`（用任务自己的账号，跑 `regenerate`）；下面日志。**不再有"换卡/不换卡"两个变体**——重生就是挪走旧片、同图同段按当前提示词重拼、重提、下载。
- 顶部小条：`拼接成片` 按钮先放着（灰，tooltip「下一版接 AutoSub」）。

## 验收
- 打开一个已收片的任务，J/K 能走，空格播放，4× 有声。
- 运行体检后有问题的卡有红标；筛选「有问题」只剩它们。
- 标记两段 → 重生 → 它们回到 planned 又变绿；旧片在 `clips/_弃用/`。
- 对 Grok 任务重生同样能跑（走 GrokEngine）。
