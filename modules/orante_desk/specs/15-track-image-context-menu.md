# 规格 15 · 编排面板：图片右键菜单（复制 / 删除 / 水平翻转 / 裁切编辑器）

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先读 `specs/00-architecture.md`，再读 `specs/07-arrange-and-expand.md`、`specs/14-expand-prompt-and-progress.md`（本规格建立在 14 之上，14 已做完），再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的」写到 `specs/reports/15-track-image-context-menu.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。**改之前把 `ui/js/panels/track.js`、`orante/services/expand.py`、`orante/server.py` 各备份成 `*.bak-0906b`。**

涉及文件：新建 `orante/services/imgedit.py`、`orante/server.py`（只加路由）、`ui/js/panels/track.js`、新建 `ui/js/parts/cropper.js`（裁切编辑器组件）、`ui/css/app.css`、`orante/i18n/zh.json`。右键菜单的样式和交互**照抄生成面板已有的 `.ctx` 菜单**（generate.js 里 `openMenu` / `onMenu` 那套），不要另起一套。

## 目的
有些图翻转一下、或者从大图里裁出一块，就等于换了一个角度/景别，比扩图快得多、也不用等 Flow。所以编排面板图片行的每张图要能右键：**复制、删除、水平翻转、裁切**。

## 一、后端 `services/imgedit.py`（Pillow，expand.py 里已经在用）
所有操作都改 manifest 并落盘；**原文件先备份**到 `images/_replaced/pX-<时间>.<ext>`（和 refill 同一个约定，复用它的 `_move_file`/备份逻辑，别重写）。返回更新后的 `image` 记录；如果这张图已被配图轨引用，还要返回 `reset_segments: [s, …]`。

| 操作 | 路由 | 行为 |
|---|---|---|
| 复制 | `POST /api/tasks/{tid}/image/{p}/duplicate` | 复制文件为下一个编号 `pN`（用 expand 的 `_next_p`），manifest 加一条：role/eye/desc/voice/measure **全部拷贝**，`origin="dup"`, `origin_from=p`, `status="ok"`。配图轨不动。 |
| 删除 | `POST /api/tasks/{tid}/image/{p}/discard` | **已有**（expand.discard_image），直接复用。前端先确认（`confirm` 用现有的确认样式）；被配图轨引用时确认框里写「有 N 段在用它，删掉后这些段回到未配图」。 |
| 水平翻转 | `POST /api/tasks/{tid}/image/{p}/flip` | `ImageOps.mirror`，覆盖原文件名（编号不变），`sha1` 更新，`flipped = not flipped`；`measure.facing` 若是「左/右」就对调；引用它的段 `prompt_final=""`、若 status 是 done/failed 则打回 planned（和 refill 的 `n_reset` 逻辑一致，抽成共用函数 `_reset_refs(m, p)`）。 |
| 裁切 | `POST /api/tasks/{tid}/image/{p}/crop` body `{x, y, w, h, as_new: bool}` | 坐标是**原图像素**。`as_new=false`：覆盖原文件（编号不变）+ `_reset_refs`；`as_new=true`：另存为下一个编号（同「复制」的字段拷贝，`origin="crop"`, `origin_from=p`），原图不动。裁完的图**不缩放**，保持裁出来的原始像素。裁切矩形不合法（越界 / 小于 256 像素短边）→ OranteError，提示原话。 |

覆盖原文件的两种操作（翻转、原地裁切）都要写 `im["edits"]` 追加一条 `{"op": "flip"|"crop", "at": 时间, "box": [...]}`，方便以后追溯。

另外加 `GET /api/tasks/{tid}/image/{p}/info` → `{w, h, file}`，裁切编辑器打开时要知道原图尺寸。

## 二、前端
### 2.1 右键菜单（track.js）
图片行每张卡片 `@contextmenu.prevent="openImgMenu($event, im)"`。菜单项：`复制`、`水平翻转`、`裁切…`、分隔线、`删除`（红字）。空镜 / blocked 图：只允许「删除」和「复制」，其余灰。操作完成后 `loadTrack()` 刷新，且如果返回了 `reset_segments`，toast「pX 已翻转，N 段已打回」。翻转/裁切后的缩略图要**破缓存**（url 后面加 `?v=sha1` 或时间戳），否则看着没变。

### 2.2 裁切编辑器 `ui/js/parts/cropper.js`
一个模态（遮罩 + 居中卡片，Esc 关），**纯 canvas，不用库**：
- 左侧大区域显示原图（等比缩放到可视区），上面一个可拖动、可拉四角/四边的裁切框，框外压暗。
- 右侧工具栏：
  - **比例**：`9:16`（默认，锁定）/ `3:4` / `1:1` / `自由`；
  - **九宫格朝向**：3×3 一组小按钮（左上 上 右上 / 左 中 右 / 左下 下 右下），点哪个就把当前大小的裁切框贴到那一侧——这就是「朝向哪里裁」；
  - **大小**：一个滑杆「裁切范围」100% → 30%（按当前比例，以框中心为准缩放）；
  - 实时显示 `裁切区域 1234 × 2193 px`，短边 < 720 时黄字提示「偏小，Flow 可能出图质量下降」，< 256 红字并禁用确定；
  - 底部两个按钮：`另存为新图`（as_new=true，主推，绿色）和 `覆盖原图`（as_new=false，普通样式；被配图轨引用时按钮 tooltip 说明会打回 N 段）。
- 小预览：右侧顶部一个 9:16 的小窗实时显示裁出来的效果。
- 键盘：方向键微移 1px（Shift 10px），Enter = 另存为新图。
- 组件接口：`openCropper({tid, p, file, w, h}) -> Promise<{x,y,w,h,as_new}|null>`，track.js 拿到结果再调后端。放 `ui/js/parts/` 是为了以后多模态面板也能用。

### 2.3 i18n
新文案全部进 `zh.json`（`track.ctx.*`、`crop.*`）。

## 验收
1. 右键 p02 → 复制：出现 p09（或下一个号），描述语和音色一样，配图轨不变。
2. 右键 p02 → 水平翻转：缩略图立刻镜像；如果配图轨有段在用 p02，这些段 `prompt_final` 清空、状态回 planned，toast 说明。
3. 右键 p02 → 裁切：选 9:16、点「右上」、范围拉到 60%，另存为新图 → 新编号出现，文件像素等于裁切区域；覆盖原图路径也能用且有备份在 `images/_replaced/`。
4. 删除有确认，被引用时确认框写明段数。
5. 空镜图右键只有复制 / 删除可用。
