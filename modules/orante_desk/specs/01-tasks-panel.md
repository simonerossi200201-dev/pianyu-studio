# 规格 01 · 任务面板（前端）`ui/js/panels/tasks.js`

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先完整读 `specs/00-architecture.md`，再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**，需要就拷到 `orante/legacy/`。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的、需要后端补的」写到 `specs/reports/<本文件名>.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。


先读 `specs/00-architecture.md`。你只改 `ui/js/panels/tasks.js`、`orante/i18n/zh.json`（加键）和必要时 `ui/css/app.css`（追加，不删）。后端接口已存在，见 §4；缺什么接口就在本文件末尾「需要后端补的」里写下来，不要自己改后端。

## 1. 这个面板是什么

节点 ①。一张内置电子表格，一行 = 一个任务。用户在这里：粘贴 / 导入 / 手填任务 → 选账号 → 点「开始准备」。
目标：一个从没用过的人，看一眼表头注释就知道每列填什么；从 Google Sheet 里 Ctrl+C，到这里 Ctrl+V 就进表。

## 2. 布局（从上到下）

1. 标题行：`任务` + 右侧按钮组：`从 Google Sheet 粘贴`（打开一个 textarea 弹层，粘进去点确认 → 调 `/api/tasks/parse` → 追加成行）、`导入 csv/xlsx`（`<input type=file>`，csv 前端读文本走同一个 parse；xlsx 用 SheetJS 时**不要**引 CDN——没有就先只支持 csv，写进「需要后端补的」）、`＋ 加一行`、`保存表格`（primary）。
2. **注释行**（`.hintrow`，网格列宽与表格列一一对齐）：每列一句话，文字取 `t('tasks.hint.*')`，灰色小字，不可编辑。
3. Tabulator 表格，`layout:"fitColumns"`, `height:"calc(100vh - 330px)"`, `clipboard:true`, `clipboardPasteAction:"insert"`（粘贴 TSV 直接成行；粘贴解析规则和后端 `tasks.parse_tsv` 一致：无表头按列顺序 名/文案/图片/类型/账号/时长）, `selectableRows:true`, `movableRows:true`, `rowContextMenu` 见 §3。
4. 底部操作条：左边 `已选 N / 共 M`；右边 `只准备选中的`、`开始准备（下图 + 拆段）`（primary）。

列（顺序固定）：

| field | 标题 | 编辑器 | 备注 |
|---|---|---|---|
| `id` | # | 只读 | 新行为空；保存后回填 T0001 |
| `name` | 任务名 | input | 可空 |
| `copy` | 文案 | textarea（Tabulator `editor:"textarea"`，`verticalNavigation:"editor"`） | 单元格里显示前 60 字 + `…`，tooltip 全文；宽度最大 |
| `image_source` | 图片来源 | input | 直链 / Drive 文件夹 / 本地路径；格式化：`drive.google.com` 显示 🗂 前缀，`http` 单图显示 🖼，本地路径显示 📁 |
| `type` | 任务类型 | `list` 下拉：长文案 / 短文案（值 `long`/`short`，显示中文） | 必填，红色底表示空 |
| `account` | 账号 | `list` 下拉，选项 = `store.accounts`，分两组 Flow / Grok；显示名字，值也用名字 | 下拉项左边带颜色圆点：flow 青 `var(--flow)`、grok 金 `var(--grok)` |
| `duration_pref` | 时长 | list 下拉：空 / 4 / 6 / 8 / 10 / 15，只作提示，后端可能忽略 | 可空 |
| `status` | 状态 | 只读 tag：草稿/已准备/描述语齐/已编排/生成中/片齐 | 已准备之后的行，`copy` 列不可编辑（`editable: cell => row.status==='draft'`） |

行的颜色：Flow 任务在行首 3px 青色边；Grok 金色边（用 `rowFormatter` 加 class）。

## 3. 右键菜单（`rowContextMenu`）

- 准备这一行（POST job prepare）
- 打开任务文件夹（`POST /api/tasks/{id}/open-folder` —— **需要后端补**）
- 复制文案
- 移到回收（`DELETE /api/tasks/{id}`）
- 彻底删除（草稿才显示；`DELETE /api/tasks/{id}?hard=1`）

## 4. 后端接口（已存在）

- `GET /api/tasks` → `{tasks:[{id,name,copy,image_source,type,account_id,engine,duration_pref,status,dir,counts}]}`；表里的 `account` 列要用 `store.accounts` 把 `account_id` 映射成名字，保存时再传名字（后端认名字或 id 都行）。
- `POST /api/tasks/rows {rows:[{id?,name,copy,image_source,type,account,duration_pref}]}` → `{saved:[id...], errors:[{row,error,hint}]}`。有 errors 就把对应行标红并 toast 第一条。
- `POST /api/tasks/parse {text}` → `{rows:[...]}`
- `DELETE /api/tasks/{id}[?hard=1]`
- `POST /api/workspace/batch {root?}` → `{batch:{id,dir,day,version}}`：开一个新的 `根\日期\版本` 文件夹。
- `POST /api/workspace/pick` → `{folder}`（弹系统文件夹选择框；返回 null 表示当前环境弹不出来，前端退回让用户手填路径）
- `POST /api/jobs {kind:"prepare", task_id, batch_dir, batch_id}` → `{job}`
- `store.settings['workspace.root']`、`['workspace.ask_each_batch']` 已经在前端。

## 5. 「开始准备」的流程（前端负责编排）

1. 先 `保存表格`（有错误就停）。
2. 取要准备的任务列表：选中的，或全部 `status==='draft'` 的。一条都没有就 toast。
3. **保存位置**：若 `settings['workspace.ask_each_batch']` 为真或 `workspace.root` 为空 → 弹一个小对话框（组件内实现，不用第三方）：显示当前根目录、`选择文件夹…` 按钮（调 pick，null 就变成可编辑输入框）、复选 `记住这个位置`（勾了就 `POST /api/settings {set:{'workspace.ask_each_batch':false}}`）、确定。
4. `POST /api/workspace/batch {root}` 拿到 `batch`。
5. 对每个任务 `API.job('prepare', id, {batch_dir: batch.dir, batch_id: batch.id})`。prepare 不占账号，调度器会并行跑几条。
6. toast `已排队 N 个任务，看右上角「日志」`，并把节点灯交给 app.js（不用你管）。

## 6. 验收

- 从一个 Google Sheet 复制 3 行（含表头行、不含表头行各试一次）粘到表里，列都对。
- 不选账号点保存 → 那行变红，toast 说清楚哪行缺什么。
- 「开始准备」第一次弹保存位置，勾「记住」后第二次不再弹。
- 已准备的行文案列灰掉不能改。
- 全程没有任何 `alert()`（可以用 `confirm()` 做删除确认）。

## 7. 需要后端补的（写在这里，别自己改后端）

- `POST /api/tasks/{id}/open-folder`
- （你发现的其他）
