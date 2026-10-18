# 规格 07 · 编排面板（配图轨）+ 扩图/补图服务

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先完整读 `specs/00-architecture.md`，再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**，需要就拷到 `orante/legacy/`。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的、需要后端补的」写到 `specs/reports/<本文件名>.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。

涉及文件：`orante/services/track.py`、`orante/services/expand.py`、`orante/services/arrange.py`（job 注册）、`ui/js/panels/track.js`、server.py **只加路由**。

## A. `services/track.py` —— 把 Orante1 的配图轨算法搬过来

源：`app\..\bridge\track.py`（在 Orante1 里，`D:\0_Dev\Orante\bridge\track.py`，就在本目录）。`plan(segments, images, pins, hold, jitter, unit, pools, seed)` 这个纯函数**原样保留算法**（钉区 / 洗牌袋 / 限定图池 / 空镜不碰），只把输入输出改成 manifest v2 的 `segments` / `images` / `track` 结构（见 00 §3.3）。`summarize(rows)` 也搬。
去掉 `apply(gid, rows, pipe_root)`（那是写老 manifest 的），改成 `apply(m: Manifest, rows) -> Manifest`：把每段的 `image` 填上、`status` 仍是 planned、`prompt_final` 清空（图换了提示词要重拼）。**已经 submitted/collected 的段不许改**（抛 OranteError 指出是哪几段）。

## B. `services/arrange.py` —— job 注册

- `arrange`（task_id, track: dict）：把 track 存进 manifest.track，跑 plan，apply，`tasks.set_status('arranged')`。不占账号。
- `arrange_preview`（不是 job）：server 路由 `POST /api/tasks/{tid}/track/preview {track}` → `{rows, summary}`，纯算。
- 路由 `GET /api/tasks/{tid}/track` → `{images, segments, track}`；`POST /api/tasks/{tid}/track/apply {track}` → 起 arrange job。

## C. `services/expand.py` —— 多镜头扩图 & 被拒补图（**占账号**：用任务的 `expand_account_id`，为空就用第一个启用的 Flow 账号）

### `expand`（task_id, target: int | None）
目标张数 `target` 默认 `config.get('expand.min_images')`（6）。当 `role=主体 且 status=ok` 的图 < target：
1. 取现有主体图逐张当参考图，对每张：`Gemini().ask_image(EXPAND_PROMPT, 图)` 生成一句**简短**生图描述（中文，≤ 60 字，模板写死在代码里：「同一个人物，同样的服装、发型、姿态和表情，只改变拍摄距离和角度：{景别}。背景延续原图环境。」其中 {景别} 轮换：近景侧面 / 中景略俯 / 全景正面 / 近景略仰 …；**绝不出现人名**）。
2. `FlowEngine.gen_images(prompt, ref=图, n=config.get('expand.per_round'), dest_dir=任务目录/images/_候选/)`。
3. 每张新图：Gemini 空镜判定（复用 describe.py 的判定函数——先 import，没有就在这里写一个最小版并在报告里说明）→ 空镜进 `空镜头/`，否则进 `images/_候选/` 并在 manifest 里加一条 `{"p": 下一个编号, "file": ..., "role": "主体", "status": "pending_review", "origin": "expand"}`。
4. 凑够 target 就停；一轮不够再来一轮（最多 3 轮）。
`status=pending_review` 的图**不进配图轨**，等用户在面板上点「留」→ `status=ok`，文件从 `_候选/` 挪到 `images/`；点「弃」→ 删记录 + 文件进 `images/_弃用/`。（models.py 的 IMG 状态集合里加 `pending_review`——这是允许你改 models.py 的唯一一处。）

### `refill`（task_id）—— 被拒补图
对每张 `status=blocked` 的图 `pX`：
1. 找同任务一张 `status=ok` 的主体图当参考；Gemini 写简短描述（同上模板，强调「换景别、角度、距离」）。
2. `gen_images(..., n=per_round)`，取第一张不被拒、且 Gemini 判不是空镜的；**左右翻转**（Pillow `ImageOps.mirror`，设置 `expand.flip_replacements` 控制）后**覆盖写到 pX 原来的文件名**（旧文件先挪到 `images/_replaced/pX-<时间>.jpg`）；manifest 里 pX 的 `sha1` 更新、`status=ok`、`origin=replace`。**配图轨里所有指向 pX 的段一律不动**——这就是"补槽位不重排"。
3. 把所有 `status=blocked 且 image=pX` 的段改回 `planned`、清空 `prompt_final`。
4. 一次生成 4 张都不行就换描述再来，最多 3 轮；仍不行就留 blocked 并 toast。

## D. `ui/js/panels/track.js`（节点 ③）

照 Orante1 `web/index.html` 里「配图轨编排台」那一块（第 211~260 行样式、531~560 行结构、1178~1400 行逻辑）搬成 Vue 组件，交互**一模一样**：缩略图条（进/出图池；被拒 × ；pending_review 的带「留 / 弃」两个小按钮）、色条（一格一段，Shift 选范围；底部黑线=钉；顶部蓝线=限定过图池；斜纹=改不了）、钉住用 pNN / 限定图池 / 取消钉住 / 清除限定、一张图撑几段滑杆 + 起伏、预演、换一批、应用。
新增：
- 顶部任务下拉（status ≥ described 的任务；Flow 和 Grok 一样都能选——**这是这次改造的核心目的**）。
- 每个色条格子里显示 `p07` 这样的编号（字号 9px，颜色相近时靠它分辨）；缩略图卡片上也显示编号 + 被用了几段。
- 当主体图 < `expand.min_images` 时顶部黄条提示「图片只有 N 张，建议扩图」+ 按钮「多镜头扩图（用 Flow）」→ 跑 `expand` job；有 blocked 图时红条 + 「补生成被拒的图」→ 跑 `refill`。
- 图片颜色：给每个 pNN 分配固定色（HSL 均匀取色，按编号顺序），Flow/Grok 任务都一样。

## E. 验收
- `tools/test_track.py`：构造 30 段 5 图的 v2 manifest，钉 s1/s30 用 p01，限定 s10~s20 只用 p02/p03，hold 3 jitter 1 seed 42，plan 两次结果一致；apply 后每段都有 image。
- 界面上：选一个 Grok 任务能打开配图轨（不再被拦）；色条格子看得到编号。
