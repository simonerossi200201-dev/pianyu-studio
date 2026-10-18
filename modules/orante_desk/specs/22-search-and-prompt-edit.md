# 22 · 文案搜索 · 收片按钮排版 · 提示词逐段可编辑

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先完整读
> `specs/00-architecture.md`，再读 `specs/07-arrange-and-expand.md`、
> `specs/09-review-panel.md`，再读本规格。**先做完 `specs/21`**，那个是正确性问题，
> 这个是易用性问题。本规格以前端为主，只有第 3 节要动两个 .py。改前每个文件备份成
> `*.bak-0909ux`。写完把报告写到 `specs/reports/22-search-and-prompt-edit.md`。
> 全程中文注释。不要问问题。

---

## 1. 文案搜索（③ 编排 和 ⑤ 收片 两个面板都要）

### 1.1 为什么要

用户手里拿着一份「缺失文案清单」，上面写的是英文句子，他要回答的问题是
**「这句话是第几段？」**。现在只能靠肉眼一段段翻。11 段还好，35 段就不行了。

### 1.2 交互

两个面板各自的工具栏（编排：配图轨上方；收片：现在放筛选按钮那一列的最上面）
加一个搜索框：

    [🔍 搜文案…                      ]  3/7   ‹ ›   [×]

- 输入即搜，无需回车，**去抖 150ms**。
- 匹配范围：`seg.text`（分段文案）为主；**另外也搜 `seg.prompt_final`**，
  这样「这段提示词里写了什么」也能搜到。两者都命中只算一次。
- 匹配规则：**大小写不敏感**；把两侧的空白折叠成单个空格再比；
  智能引号 `“ ” ‘ ’ ′` 和破折号 `— – −` 一律先归一成 `" ' -`，
  这样用户从 txt 里复制一句带弯引号的英文也能搜到。
- 结果计数 `当前/总数`，`‹ ›` 或 `Enter` / `Shift+Enter` 在命中之间跳，
  跳到时把该段卡片滚到视口中间并给一圈高亮描边（1.2s 后淡出）。
- 命中的段在**卡片上**给一个小徽标，命中的关键词在展开的文案/提示词里 `<mark>` 高亮。
- `Esc` 或 `×` 清空。搜索框有内容时，收片那一列的筛选（全部/有问题/已标记/缺片/新）
  与搜索是**且**的关系。
- 快捷键：面板内按 `/` 聚焦搜索框。注意收片面板已经占了 `J K 空格 X E C M V P A F 1..0 Esc`，
  **`/` 不冲突，用它**；聚焦在输入框里时，那些单键快捷键全部让位（判断
  `document.activeElement` 是 input/textarea 就直接 return）。

### 1.3 编排面板的额外一条

编排面板搜到的时候，除了高亮该段，还要在配图轨上把对应的那一格也点亮，
让用户一眼看到「这句话现在配的是哪张图」。

### 1.4 改哪些文件

`ui/js/panels/review.js`、`ui/js/panels/track.js`、`ui/css/app.css`、
`orante/i18n/zh.json`（新增 `search.placeholder` / `search.count` / `search.none`）。
搜索逻辑两边一模一样 —— **抽成 `ui/js/parts/textsearch.js` 一份，两个面板都 import**，
不要复制两遍。

---

## 2. 收片卡片：把「换图」挪上去

现在底部工具条挤了 5 个按钮（标记重生 / 提示词 / 视频 / 原图 / 换图），
中等尺寸下每个都被压成「提…」「视…」「原…」「换…」，字都看不全。

**改法**：把 `换图` 从 `.tools` 那一行**挪到 `.flags` 那一行的最右侧**，
和「已收 / 缺片 / 新」这些旗子同一排，右对齐（`margin-left:auto`）。
样式做成小号 chip（和旗子等高，带边框、可点），不要做成大按钮。

底部 `.tools` 剩 4 个：`标记重生 / 提示词 / 视频 / 原图`。四个平分一行，
**保证在「中」尺寸下四个字都能完整显示**——不够就把 `.tools button` 的
`padding` 收到 `4px 6px`、`font-size` 降到 12px，`min-width:0` + `flex:1`，
禁止 `text-overflow: ellipsis` 把字截掉。做完自己截图确认。

改 `ui/js/panels/review.js` 第 976~990 行附近那两块，和 `ui/css/app.css` 里
`.tools` / `.flags` 的样式。

---

## 3. 提示词：逐段精准编辑，编辑后的提示词说了算

### 3.1 需求

现在展开的「成品提示词」是只读的，只能复制。要改成**可编辑并保存**，
而且**重生必须用用户改过的那一版**。

### 3.2 界面（`ui/js/panels/review.js` 的 `.pblock.final`）

    ┌ 成品提示词        1024 字   [复制] [编辑] ┐
    │ （只读段落，同现在）                       │
    └──────────────────────────────────┘

点「编辑」→ 段落变成 `<textarea>`（自动撑高，最少 8 行），按钮区变成：

    [保存]  [取消]  [恢复默认]      ● 已改过

- **保存**：调接口写盘，成功后 toast「已保存 · N 字」，右上角挂一个
  **🔒 已锁定** 的小标（表示这段用的是手写提示词）。
- **取消**：丢弃改动回只读。
- **恢复默认**：清空手写版，回到由「多模态」面板规则自动拼装的那一版；
  要**二次确认**（这一步不可撤销）。
- `Ctrl+Enter` = 保存，`Esc` = 取消。textarea 聚焦时禁用面板单键快捷键。
- 编辑中离开这一段 / 切任务 → 提示「有未保存的改动」，别静默丢掉。

### 3.3 后端语义（这是重点，别做错）

`manifest.json` 的段上加一个字段：

    "prompt_locked": true        // 用户手写过，这段的 prompt_final 是权威版

现有代码有 **4 个地方**会把 `prompt_final` 清空，它们都必须**尊重 `prompt_locked`**：

| 文件 | 位置 | 现在的行为 | 改成 |
|---|---|---|---|
| `orante/services/generate.py` | `regenerate()` 里 `s["prompt_final"] = ""` | 重生前一律清空重拼 | `prompt_locked` 为真时**不清空**，直接用手写版重生 |
| `orante/services/review.py` | `set_image()` 里 `sg["prompt_final"] = ""` | 换图就清空 | `prompt_locked` 为真时不清空，但要在返回里带 `kept_locked:true`，前端 toast 提醒「这段的提示词是你手写的，换图后没有自动改，需要的话去改一下」 |
| `orante/services/track.py` | `apply()` 里 `sg["prompt_final"] = ""` | 应用配图轨就清空 | 同上，跳过锁定的段；应用完的统计里报「N 段因为锁定没动」 |
| `orante/services/expand.py` | 三处 `sg["prompt_final"] = ""` | 扩图后清空 | 同上 |

`generate.py::_final_prompt()` 现在已经是「有 `prompt_final` 就直接用」，
所以只要不被清掉，出片和重生自然就会用手写版 —— **不要在那里再加逻辑**。

「恢复默认」= 把 `prompt_locked` 置 false 且 `prompt_final=""`，下次出片时重新拼。

### 3.4 接口

    POST /api/tasks/{tid}/review/prompt
      body {"s": 4, "text": "……"}          # text 非空 → 保存并置 prompt_locked=true
      body {"s": 4, "reset": true}          # → prompt_final="" 且 prompt_locked=false
    → {ok:true, s:4, chars:1024, locked:true}

服务端放在 `orante/services/review.py`，路由放 `orante/server.py`（照
`/api/tasks/{tid}/review/image` 那条抄）。写盘后 `events.emit("tasks.changed")`。

**已完成（`isFinished`）的任务不允许改**，接口直接拒绝，前端把「编辑」按钮置灰。

### 3.5 收片墙上的可见性

`prompt_locked` 的段，在 `.flags` 那一排加一个 🔒 小旗（i18n `review.prompt_locked`），
让用户一眼看出哪几段是手写提示词。「查漏补缺」和「重生」抽屉里也要显示这个标，
免得重生时以为会自动重拼。

---

## 4. 验收

1. 收片面板打开 T0013，搜 `Nineveh` → 命中 s003，卡片滚到中间并高亮。
2. 搜 `(John 8:34–35)`（**从缺失文案清单里直接复制，带弯破折号**）→ 必须能命中，
   不能因为 `–` vs `-` 搜不到。
3. 编排面板搜同一句 → 命中同一段，配图轨对应格子点亮。
4. 中尺寸下截图收片卡片：底部四个按钮的中文全部完整显示；换图 chip 在旗子那一排右端。
5. 改 s004 的提示词，保存，重生 s004 → 在日志里确认提交时用的是改过的那一版
   （打印提示词前 40 字对一下），且重生后 `manifest.json` 里 `prompt_final`
   仍是手写版、`prompt_locked` 仍是 true。
6. 对 s004 换图 → 提示词没被清掉，toast 出现提醒。
7. 「恢复默认」→ `prompt_final` 清空、`prompt_locked` 变 false、🔒 旗消失。
