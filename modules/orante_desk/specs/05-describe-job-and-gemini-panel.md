# 规格 05 · 描述语作业 `orante/services/describe.py` + 多模态面板 `ui/js/panels/gemini.js`

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先完整读 `specs/00-architecture.md`、`specs/04-prompts-and-gemini.md`（以及它的 report），再读本规格。只改本规格点名的文件；老流水线只读。写完自己验收，报告写到 `specs/reports/<本文件名>.md`。中文注释。不要问问题。

## A. 后端 `services/describe.py`

注册三个 job kind（用 `jobs.register`，都**不占账号**）：

### `describe`（task_id）
对 manifest 里每张 `role=主体` 且 `desc` 为空的图：
1. `Gemini().ask_image(prompts.get_block('desc'), 图)` → 解析：第一行描述语、第二行音色（空镜只有一行）。老规则见 `legacy/longtik_images.parse_sidecar_text`——**复用它**。
2. **空镜判定**：再问一次（json_mode）`{"is_broll": bool, "reason": str, "shot_size": 远景|全景|中景|近景|特写, "people": int, "crowd": bool}`，提示词写死：观众席 / 大全景（主体很小、看得见整个场地或几千人）/ 没有可辨认主角 → is_broll=true。判为空镜的图：`role="空镜"`，文件从 `images/` 移到 `空镜头/`（`file` 字段同步改），日志一行。
3. **看图测量**（给运镜用）：复用 Orante1 `bridge/lens.py` 的测量 prompt 和 JSON 收敛逻辑（拷到 `orante/services/lens.py`，改成用 `services.gemini.Gemini`，其它不动），结果写进 `image.measure`。
4. 每张图处理完立刻 `m.save()`（中途取消不丢）。`config.get('gemini.concurrency')` 条线程并行（`concurrent.futures.ThreadPoolExecutor`）。
5. 全部有 desc 后：`tasks.set_status(id,'described')`。

### `scan_problems`（task_id）
`prompts.gemini_scan_problems(m.copy.text)` → 新句子加进库；返回 `{added:[...]}`。

### `preview_prompt`（不是 job，是普通函数，server 已有 `/api/tasks/{id}/manifest`，你在 server.py 里**只加**一个路由）：
`GET /api/tasks/{tid}/prompt/{s}` → `{prompt: prompts.build_final_prompt(m, seg)}`。这是「最终提交预览」。

## B. 前端 `ui/js/panels/gemini.js`（节点 ②）

布局（左右两栏，左 380px 固定，右自适应）：

**左栏 · 设置（四张可折叠卡片，默认前两张展开）**
1. `Gemini` 卡：API Key（password 输入 + 「测试」按钮：调 `POST /api/gemini/test` —— **需要后端补**，返回模型列表；成功显示绿字）、模型下拉（`list_models` 结果，默认钉 `gemini-3.5-flash-lite`）。改动直接 `POST /api/settings`。
2. `描述语提示词` 卡：textarea（monospace，高度 260），底部 `恢复默认` `保存`。数据来自 `GET /api/prompts`（**需要后端补**：返回 `prompts.all_blocks()`；`POST /api/prompts {name, value}` 保存；`POST /api/prompts/reset {name}`）。
3. `运镜规则` 卡：以 JSON 编辑器（textarea）展示 `camera` 块，上方一行说明「人物不动、镜头轻微运动是默认；想让人物动就改『人物原地_安静』那一条」。保存前 `JSON.parse` 校验。
4. `固定结语` 卡：`tail` 块里每个字符串键一个小 textarea（闭眼 / 睁眼 / 情绪基调_闭眼 … ），字典键（情绪推进）再展开一层；`tail_notes` 灰字折叠显示。
5. `问题句子库` 卡：列表（每条：kind 标签 regex/phrase、value、lang、来源 tag、删除 ✕）；底部一行输入框 + 「加一句」；按钮「让 Gemini 扫当前任务文案自动补充」（选中任务后可用，跑 `scan_problems` job）。

**右栏 · 任务与图片**
- 顶部：任务下拉（`store.tasks` 里 status ≥ prepared 的），右侧按钮 `写描述语（跑 Gemini）`（primary，跑 `describe`）、`最终提交预览`。
- 图片墙：每张图一卡：缩略图（`/media/{tid}/images/p01.jpg`）、编号 `p01`、role tag（主体青 / 空镜灰）、描述语正文（可直接改，失焦保存 → `POST /api/tasks/{tid}/image/{p}` **需要后端补**：改 desc / voice / role）、音色一行、测量结果一行小字（景别·朝向·人数）。空镜头单独一栏在下面。
- 「最终提交预览」抽屉：段下拉（s001…）+ 只读 textarea 显示 `GET /api/tasks/{tid}/prompt/{s}`，右上「复制」。**这里显示的就是要发给 Flow/Grok 的完整字符串，一个字不藏。**

## C. 验收
- 没填 Key 点「写描述语」→ toast 说去设置里填 Key（后端的 OranteError 原话）。
- 改描述语提示词、保存、刷新页面还在；「恢复默认」回到 v7。
- 一个 3 张图的任务跑完 describe，三张都有 desc；把其中一张手动改成空镜 role，它出现在空镜头栏。
- 预览抽屉里能看到含台词的完整提示词。

## D. 需要后端补的
- `POST /api/gemini/test`、`GET/POST /api/prompts`、`POST /api/prompts/reset`、`POST /api/tasks/{tid}/image/{p}`、`GET /api/tasks/{tid}/prompt/{s}`（这一条你自己在 server.py 加；其余也允许你加——**只加路由，不改已有路由**）
