# T13 — 命令总线、配方批处理、操作录制、本地 API + OpenAPI + 最小 MCP 服务器

> 前提：T03 完成。先读 `docs/plan/01_GROK_通用规范.md` 与总体计划 §2.10。
> 这是"让软件能被机器人操作"的地基，也是"批量化/自动化"的地基。之后 T04–T08、T11 的每个功能都要注册为命令。golden 规则 A（本任务不改渲染）。

## 1. 命令总线：`cutora/core/commands.py`

```python
@dataclass
class CommandSpec:
    name: str                 # "subtitle.import_file"，点分命名空间：project/media/subtitle/timeline/audio/style/export/batch/app
    title_zh: str             # "导入字幕文件"
    doc_zh: str               # 一段话：做什么、副作用、何时用
    params_schema: dict       # JSON Schema (draft 2020-12)
    returns_schema: dict
    undoable: bool
    dangerous: bool           # 删除/覆盖文件类，API 调用必须 confirm=true
    requires_project: bool
    run: Callable[[Context, dict], dict]
    undo: Callable[[Context, dict, dict], None] | None

registry = CommandRegistry()   # register(spec) / get(name) / list() / schema_json()
bus.execute(name, params, source="ui|cli|api|mcp|recipe")  # 校验 schema → 执行 → 进 HistoryManager → 写审计日志 → 返回结果
```
- `Context` 持有当前工程、时间线、选择状态、主窗口引用（可为 None，无头时）。命令**不得**直接碰 Qt 控件；UI 更新靠命令执行后发出的 `bus.executed` 信号让面板刷新。
- 现有 `HistoryManager` 是**快照式**（`push(description, state_snapshot)` 深拷贝整个状态，最多 40 步）。本任务不推翻它：命令执行后仍 `push` 一份快照（保证旧面板的撤销行为不变），同时在快照条目里附 `command`/`params`/`source`，供配方录制与审计使用。真正的"逆命令式撤销"留到 Phase 2 再评估（大工程下深拷贝会慢，先在报告里给出 1 小时工程的 push 耗时数据）。
- 参数校验用 `jsonschema`（MIT）。
- 审计日志 `USER_DATA\logs\commands.jsonl`：时间、来源、命令、参数（路径脱敏可选）、耗时、结果/错误。

### 1.1 本任务必须注册的命令（把现有功能包一层，不改功能本身）

`project.new / open / save / save_as / close / info`
`media.import(paths[]) / remove / list / info(id)`
`subtitle.auto(media_id?, language="auto", model="small|medium|large-v3|large-v3-turbo") / list / set_text(seg_id, text) / merge / split / delete / apply_preset(name)`
`style.list_presets / apply_subtitle_preset / apply_text_preset / apply_keyword_preset / save_current_as(name)`
`timeline.info / add_track / remove_track / split_at(time) / delete_clip / move_clip / set_playhead`
`audio.spatial.set(preset, wet, width) / sfx.apply_current / sfx.apply_all`
`export.settings / export.run(profile?, out_dir) / export.batch(project_paths[], out_dir) / export.status`
`batch.stitch(folder, script_path, opts) / batch.scatter / batch.insert_images / batch.queue.add / batch.queue.run`
`app.version / app.gpu_info / app.free_vram / app.settings.get / app.settings.set(key, value)`（settings 命令**永远不暴露密钥字段**）

每个命令的 `doc_zh` 要写给"没看过界面的机器人"看：输入什么、会发生什么、失败常见原因。

## 2. 配方（Recipe）与操作录制：`cutora/batch/recipe.py` + `ui/panels/recipes.py`

- 配方文件 `*.cutorarecipe`（JSON）：`name`、`description`、`steps: [{command, params, on_error: "stop|skip"}]`、`inputs`（声明占位符：`{media}`、`{out_dir}`、`{stylepack}` 等）。
- **录制**：`批量 → 开始录制配方`，之后所有来源为 `ui` 的、`undoable=True` 的命令被记录（媒体路径自动替换为 `{media}` 占位符），`停止录制` 弹出编辑器（可删步骤、改参数、命名、保存）。
- **套用**：`批量 → 套用配方到文件夹…`：选配方、选文件夹（递归可选、扩展名过滤）、输出目录、并行数（复用 `ParallelExportManager` 的进程池思路：识别串行占 GPU、导出并行）；进度对话框逐文件显示、失败可重试、结束汇总（成功/失败/耗时）。
- 内置配方 3 个：`竖屏字幕快出`、`横竖双版本`、`只出 SRT 不渲染`。
- CLI：`cutora_cli recipe run <recipe> --input <folder> --out <dir> [--jobs N]`。

## 3. 本地 API：`cutora/api/server.py`

- 技术：标准库 `http.server` + `ThreadingHTTPServer` **或** `aiohttp`（Apache-2.0）二选一；**不要** FastAPI/uvicorn 全家桶（依赖多、不必要）。JSON 请求/响应。
- 绑定 **只允许** `127.0.0.1`，端口默认 47321（可改）；默认**关闭**；`设置 → 代理接口` 打开，显示 token（存 keyring）、复制按钮、"重新生成"、连接中的客户端列表、一键断开；主窗口状态栏出现"代理已连接"指示灯。
- 鉴权：`Authorization: Bearer <token>`；错误 401；每分钟每客户端限速 600 次。
- 路由：
  - `GET /v1/commands` → 全部 CommandSpec（含 schema、doc_zh）
  - `POST /v1/commands/{name}` body = params → `{ok, result, undo_id}`；`dangerous` 未带 `confirm:true` → 409
  - `POST /v1/undo` / `POST /v1/redo`
  - `GET /v1/state` → 当前工程摘要、播放头、选择、导出任务状态
  - `GET /v1/events`（SSE）→ 命令执行、进度、错误事件流
  - `GET /openapi.json`、`GET /docs`（内嵌一个离线的 Redoc/Swagger 静态页；Swagger UI 是 Apache-2.0，从 pypi 的 `swagger-ui-bundle` 取，不从 CDN 加载）
- OpenAPI 3.1 由 `registry` 自动生成，每个命令一个 path，schema 直接来自 `params_schema`。

## 4. 最小 MCP 服务器：`cutora/api/mcp_server.py`

- 用官方 `mcp` Python SDK（MIT，pypi `mcp`）实现 **stdio** 传输的 MCP 服务器：`cutora_mcp.bat` 启动它，它通过本地 API（§3）转发到运行中的 片语 Studio；若软件未运行，返回清晰错误"请先打开片语 Studio 并在设置里启用代理接口"。
- tools = 全部 CommandSpec（名称把 `.` 换成 `_`），description = `doc_zh`，inputSchema = `params_schema`；resources：`cutora://state`、`cutora://project/current`、`cutora://docs/commands`。
- 提供 `docs/api/mcp_setup.md`：如何在 Claude Desktop / 任何 MCP 客户端里配置（JSON 片段）。

## 5. 机器可读文档：`docs/api/`

`tools/gen_api_docs.py` 生成：`commands.md`（按命名空间分组，每命令：名称、中文说明、参数表、返回、可撤销、危险、示例 curl + 示例 CLI）、`openapi.json`、`recipes.md`（配方格式与内置配方）。CI（T10）里跑一遍确保与代码同步；文档过时 = 构建失败。

## 6. CLI 对齐：`cutora/batch/cli.py`

- 保留全部旧子命令的行为（无头拼接等，golden g07）；新增通用形式 `cutora_cli cmd <command.name> --json '<params>'` 直通命令总线；`cutora_cli commands` 列表；`cutora_cli recipe run`。

## 7. 验收标准

- [ ] `GET /v1/commands` 返回 ≥ 45 条命令，每条 `doc_zh` ≥ 40 字，schema 校验通过（写 pytest 用 `jsonschema` 校验每个 schema 本身合法）。
- [ ] 用 `curl` 走完整流程：新建工程 → 导入 T00 素材 → `subtitle.auto` → `style.apply_subtitle_preset` → `export.run` → 产出 mp4（报告贴命令与输出）。
- [ ] 同一流程用 MCP 客户端（可以用 `mcp` SDK 自带的测试客户端脚本）走一遍。
- [ ] 录制一个 5 步配方 → 套用到含 6 个视频的文件夹 → 6 个输出全部成功；其中故意放 1 个坏文件，`on_error: skip` 生效，汇总正确。
- [ ] API 关闭时端口不监听；开启后从另一台机器（或用 `0.0.0.0` 探测）无法连接；无 token 401；`dangerous` 无 confirm 409。
- [ ] 审计日志逐条记录，密钥字段永不出现（grep 验证）。
- [ ] 撤销：通过 API 执行的 `subtitle.set_text` 能在 UI 里 Ctrl+Z 撤销，反之亦然。
- [ ] golden 全绿（规则 A）。
- [ ] `docs/api/commands.md` 生成并提交；报告里贴前 3 条命令的文档作为样例给 Claude 审文风。

## 8. 不要做

- 不做远程访问、不做 WebSocket 之外的任何"云"功能、不做用户系统。
- 不做自然语言解析（Phase 3 的"Ask 片语"建立在本任务之上）。
- 不为了 API 改任何渲染或算法。
