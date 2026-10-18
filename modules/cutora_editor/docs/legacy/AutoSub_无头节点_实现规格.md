# AutoSub 无头节点入口 — 实现规格（修订稿）

给编码代理执行。目标是让 AutoSub 的核心能力**不经过图形界面**也能被调用，
从而可以被命令行、编排脚本、以及任何 AI 代理驱动。

本文是对初稿的修订：以仓库当前代码为准，补上初稿漏掉或写错的接口。
实现时以本文为准；若与代码再有出入，**以代码为准，不要为了迎合规格去改算法**。

- 仓库：`D:\0_Dev\AutoSub_v6\core`
- 主文件：`AutoSub_debug.py`（约 27760 行，UTF-8，**CRLF 行尾**）
- 外挂模块：`script_stitcher.py`、`export_guard.py`、`media_guard.py`
- 运行环境：`D:\0_Dev\AutoSub_v6\core\venv_autosub\Scripts\python.exe`（Python 3.10）
- CLI 新文件：`core/autosub_cli.py`（新建，不要塞进 2.7 万行主文件）

---

## 0. 绝对不能碰的东西

违反任何一条都会造成回归，而且多半不会立刻暴露。

1. **不改渲染逻辑。** `RenderWorker`、`ExportRenderer`、`render_wysiwyg_export`、
   FFmpeg 参数构造，一行都不动。并行导出与串行导出的输出必须保持逐字节一致。
2. **不改分析逻辑。** `script_stitcher.py` 里的 `AnalyzeWorker` / `EncodeWorker` /
   `match_clip_global` / `plan_group_dedup` / `PreflightWorker` 等，只调用、不修改。
3. **不破坏图形界面。** 现有按钮的行为必须完全不变。无头入口是**旁路**，不是替换。
   不要顺手重构 UI。
4. **保持 `AutoSub_debug.py` 的 CRLF 行尾和 UTF-8 编码。** 只做定点插入。
   读写用 `io.open(..., encoding="utf-8", newline="")` 或按二进制处理行尾。
   写回前确认没被转成 LF——否则整个文件显示为全量改动。
5. **不整文件重写。** 27760 行的文件只做定点插入和精确替换。
6. **改动前先备份**：`AutoSub_debug.py.bak-0824`。
7. **无头模式不写 `user_data/app_settings.json`。** 那是图形界面的用户设置。
   命令行跑一次不该改变用户下次开软件看到的状态。要覆盖设置就用命令行参数。
8. **按符号搜，不要按行号贴。** 主文件还在改，行号会漂。

---

## 1. 当前代码状态

- `AutoSub_debug.py` 已加入 v6.2 导出保活（`export_guard.py`、并行导出后台轮询、
  `--render-job` 子进程降优先级、FFmpeg 线程限流）。备份：`AutoSub_debug.py.bak-0823`。
- `export_guard.py` 提供资源体检、看门狗、心跳文件、独立保活进度窗。
  `user_data/export_state.json` 就是长任务进度协议，`--status` 直接读它。
- 图形界面拼接路径在分析前**必跑** `PreflightWorker` → `media_guard.preflight()`。
  无头必须同样跑，否则会重演「坏文件被静默跳过、成品少几条」的事故。

动手前先通读 `export_guard.py` 模块 docstring、`ParallelExportManager` 类注释、
`script_stitcher.PreflightWorker` / `AnalyzeWorker` / `EncodeWorker`。

---

## 2. 已经存在的、必须复用的东西

**不要重新发明这些。**

### 2.1 无头运行范式（已验证）

`if __name__ == "__main__":` 里的 `--render-job` 分支是样板：

- 建 `QApplication`，但从不 `show()`
- 不要设 `QT_QPA_PLATFORM=offscreen`
- 字体缺失时 `load_embedded_fonts()` 会弹模态框，无头里没人点得掉。
  先自检 `FONT_DIR` 和 `_iter_font_files()`，有问题直接非零退出
- `QThread` 直接调 `.run()` 同步执行，不调 `.start()`
  （同线程 DirectConnection，信号回调会在 `run()` 里当场触发）

`--describe` / `--status` **不要**走这条重路径，见 §3。

### 2.2 拼接模块真实签名（以代码为准）

```python
script_stitcher.scan_groups(folder)   # → {组号: [片段路径, ...]}

script_stitcher.parse_scripts(text, group_keys)
    # → (result_dict | None, error_str | None)
    # 不是只返回 dict。组数对不上时已经是硬失败。
    # 文案格式：
    #   A. 显式编号: "01|文案" / "01:文案" / "01<TAB>文案"
    #   B. 空行分段，段数必须等于组数

script_stitcher.PreflightWorker(folder, groups, quarantine_bad, parent=None)
    信号: log(str) / done(Report)
    Report.fatal / Report.blocking / Report.quarantined / Report.text()
    副作用: 不通过时在 <folder>/_invalid/ 写 preflight_report.txt/.json
            quarantine_bad=True 时把致命文件挪进 _invalid/

script_stitcher.AnalyzeWorker(groups, scripts, get_model, get_align,
                              language=None, tail_pad=0.35, trim_head=True,
                              out_dir="", parent=None)
    信号: progress(int) / log(str) / analyzed(dict) / failed(str)
    属性: .missing_map  = {组号: {"clips": [...], "missing": [{s,e,text}]}}
    副作用: 有缺失时自己会往 out_dir 写「缺失文案清单.txt」
    注意: 即便有 missing_map，analyzed 仍会 emit cuts_map。
          图形界面随后仍可编码；无头必须自己闸住，不调 EncodeWorker。

script_stitcher.EncodeWorker(cuts_map, scripts, out_dir,
                             recycle_enabled=True, parent=None)
    信号: progress(int) / log(str) / done(list, str) / failed(str)
    done 的第一个参数是 [(输出路径, 文案), ...]
    recycle_enabled=True 会把「多余片段」移入回收站 —— 无头默认 False
    配方未变且成品已在时会跳过重编（_encoded.json）
```

模型加载器：`get_whisperx_model`、`get_align_model`（在 `AutoSub_debug.py`）。

图形界面走 `open_stitcher_dialog` → `StitcherDialog`。无头不要碰 Dialog。
界面确认框、人工复核、完成后导入列表，全部由无头自己用显式参数替代。

### 2.3 主窗口方法：不要直接抽 GUI 入口

这些方法都和界面耦合（`file_list`、播放器、`QMessageBox`、`QTimer.singleShot`）。
无头不要「把 `start_batch_ai` 抽成纯函数再两边调用」——那会把事件循环和弹窗拖进来。

| 方法 | 无头应对 |
|---|---|
| `start_batch_ai` / `process_next_batch` / `run_align` | 直接循环调用 `AlignmentWorker.run()` + `on_align_done` 里那三段后处理函数（`apply_time_offset` / `merge_short_segments` / `fix_segment_durations`）。`video_w` 用探测，不读 `video_canvas`。默认 `enable_time_postprocess=False`（与界面默认一致）。 |
| `save_project` | 弹成功框。无头用 `serialize_render_task` 写 JSON，不弹窗、不写最近项目列表。 |
| `open_project` / `apply_project_data` | 无头读 JSON + `_normalize_project_data` + `deserialize_render_task`。 |
| `on_drop` / `_on_stitcher_done` | 抽「由视频路径 + 文案构造 task dict」的旁路函数，放 `autosub_cli.py`，逻辑对齐 `on_drop`（画布、默认样式缩放、音视频轨道）。 |
| `start_batch_export` | 不要走。它读勾选状态、弹并行警告（会写 `app_settings.json`）、结束弹窗。无头直接建 `ParallelExportManager`。 |
| `_batch_output_path(task)` | 无耦合，可直接复用：`<视频目录>/字幕成品/<stem>_sub.ext`。 |
| `ParallelExportManager` | 必须有 `QApplication` 事件循环（内部 `QTimer`）。不能只 `.run()`。失败项用 `RenderWorker.run()` 串行重试，不要调 `process_next_export`。 |

---

## 3. 分派架构（T1 地基）

新建 `core/autosub_cli.py`。**本文件模块级不得 import `AutoSub_debug` / `config` / `torch` / `whisperx`。**
`config.py` 在 import 时就会 `import torch` 并打印 GPU 检测结果。

### 3.1 两段分派

**轻量（文件最顶端，几乎只 `import sys, os` 之后）：**

`AutoSub_debug.py` 在 `self_check_dependencies()` **之前**判断 argv：

- `--describe` / `--status`：`from autosub_cli import early_dispatch`，处理完 `sys.exit`
- `--do` / `--run`：若带 `--json`，立刻把 `sys.stdout` 指到 `sys.stderr`（把真正的 stdout 存起来），然后继续加载主模块
- `--render-job`：什么都不做，热路径零开销

原因：主模块第 ~68 行就 `print("DEBUG: 已强制开启离线模式")`，`self_check` 会 import `transformers`。
分派点若放在文件末尾的 `__main__`，`--describe` 已经被污染、并且要等好几秒。

**重量（原 `__main__` 里、`--render-job` 分支之前）：**

`--do` / `--run` 在主模块加载完毕后交给 `autosub_cli.late_dispatch`。
此时 `__main__` 就是宿主，用 `sys.modules["__main__"]` 取 `get_whisperx_model` 等，
不要 `import AutoSub_debug`（会再加载一份）。

`--render-job` 检测必须仍然是廉价的 `in sys.argv`，排在 CLI 重量分派之后也可以，
因为两条命令互斥；不要给 `--render-job` 增加 import。

### 3.2 必须实现的命令

```
AutoSub_debug.py --describe                          # 能力清单，JSON → stdout
AutoSub_debug.py --do <op> --params <json|@file> [--json] [--async]
AutoSub_debug.py --run <graph.json> [--json]
AutoSub_debug.py --status [job_id]
```

`--params` 支持：JSON 字符串、`@路径`（读文件）。缺省为 `{}`。
`--json`：结果对象打到**真正的** stdout，除此之外 stdout 不许有别的东西。日志走 stderr。
`--async`：仅 `batch_export` 有效，见 T4。

### 3.3 `--describe` 输出格式（对外契约）

```json
{
  "app": "AutoSub",
  "version": "6.2",
  "ops": [ { "...见各 op 定义..." } ],
  "recipes": [
    {
      "name": "batch_subtitles",
      "summary": "一批口播素材：拼接 → 按 80 切开工程 → 打轴 → 导出字幕成品",
      "steps": ["stitch", "split_project", "auto_subtitle", "batch_export"],
      "note": "stitch 若以退出码 3 停下，先补缺失文案再重跑 stitch，不要继续导出"
    }
  ]
}
```

给代理看的字段：`summary`、`desc`、`note` 写成没见过这软件的人读一遍就会调。
`ops[].implemented` 一律 true（四个提交在同一份实现里一次交齐）。

### 3.4 结果 JSON

位置：

- 以素材目录为输入的 op（`stitch`）：`<dir>/_stitched/_state/<序号>_<op>.json`
- 以工程文件为输入的 op：`<工程所在目录>/_state/<序号>_<op>.json`
- 写不出上述目录时落到 `core/user_data/_state/`

```json
{
  "op": "stitch",
  "ok": false,
  "started_at": "2026-08-24T14:02:11+08:00",
  "finished_at": "2026-08-24T14:19:40+08:00",
  "counts": {"groups": 225, "clips": 1084, "missing_groups": 11},
  "artifacts": ["D:\\...\\_stitched\\缺失文案清单.txt"],
  "next_hint": "补齐缺失文案后重跑 stitch",
  "human_needed": true,
  "error": null
}
```

所有路径一律绝对路径。

### 3.5 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 失败（异常、业务失败） |
| 2 | 用法错误 |
| 3 | 业务闸门未通过（缺失文案 / 体检需确认）——按规矩不能往下走 |
| 4 | 被单实例锁挡住 |

### 3.6 单实例锁

`user_data/autosub.lock`，内容 `{pid, started_at, op}`。

- 用 `O_CREAT|O_EXCL` 原子创建。已存在则读 PID：活着 → 退出码 4；已死 → 删锁重试。
- PID 探活用现成的 `export_guard.pid_alive`，不要再写一遍。
- `--describe` / `--status` **不占锁**。
- `--do` / `--run` 占锁，`try/finally` 释放；只删自己 PID 写的那份。
- **图形界面启动时也占锁**（`op: "gui"`）。抢不到（CLI 正在跑）→ 弹警告但仍允许打开，
  不把用户挡在自己的软件外面。CLI 抢不到 GUI 的锁 → 退出码 4，stderr 写明是 GUI 的 PID。
- 主窗口 `closeEvent` 释放；进程崩溃靠 PID 探活自动清理。
- `--async` 的父进程不占锁；子进程占锁。父进程 spawn 前若锁已被占，直接 4。

### 3.7 `--run` 图契约

```json
{
  "ops": [
    {"op": "stitch", "params": {"dir": "D:\\素材", "captions": "@captions.txt"}},
    {"op": "split_project", "params": {"project": "D:\\素材\\_stitched\\stitched.asproj.json", "chunk": 80}}
  ]
}
```

也接受顶层就是数组。顺序执行。任一步非 0（含闸门码 3）立即停，不再跑后面的 op。
本版不做 `$上一步.artifacts` 变量替换，调用方把路径写死。

### 3.8 T1 验收

```
python AutoSub_debug.py --describe
```

- 输出是合法 JSON，`json.loads` 能解析
- `ops` 字段齐全，`recipes` 非空
- stdout 除 JSON 外没有任何其他内容（含那行「已强制开启离线模式」）
- 应在约 200ms 内返回，进程里没有 CUDA 上下文

```
python AutoSub_debug.py --do nonexistent_op --params "{}"
```

- 退出码 2，stderr 有可用 op 列表

同时开两个 `--do`（可用一个故意 sleep 的占锁手段或两个 stitch 指向同一目录）→ 第二个退出码 4。

图形界面必须仍能双击打开。

---

## 4. 交付物：四个可独立验收的能力（一次实现，按能力验收）

初稿要求四个 git 提交。本仓库若无 git 流程，按能力分别跑验收即可，不要四个一起测完才看结果。

---

### T2 — `stitch`

#### 流程（必须含体检）

```
groups  = scan_groups(dir)
scripts, err = parse_scripts(captions_text, list(groups.keys()))
if err: 失败，exit 1

PreflightWorker.run()          # 与界面同一道闸
if fatal:     写结果, exit 1
if blocking 且 allow_blocking 不为 true: 写结果 human_needed, exit 3
若隔离过坏文件: 再 scan_groups 一次，丢弃已不存在的路径

AnalyzeWorker.run()
if missing_map: 绝不编码, exit 3

EncodeWorker(recycle_enabled=False).run()
写出工程文件 stitched.asproj.json（把成品路径+文案做成 tasks，对齐 on_drop）
```

#### 参数

```
dir          path     必填  素材目录（NN (n).mp4）
captions     text|path 必填  字面文本、@路径、或 "-" 读 stdin
language     string   默认 en
out_dir      path     默认 <dir>\_stitched
allow_blocking bool   默认 false  体检 blocking 项是否放行
quarantine   bool     默认 true   致命坏文件是否挪到 _invalid/
write_project bool    默认 true
```

#### 闸门语义（有意严于界面）

图形界面缺文案时仍弹出确认框，用户点「直接编码」就会出片。
无头：**有 `missing_map` 就 exit 3，不编码。** 这是无头自己的闸门，不是去改 AnalyzeWorker。

体检 `blocking`（无音轨、断号、组内分辨率/帧率不一致）默认同样 exit 3。
`allow_blocking: true` 才继续。不要静默放行。

#### T2 验收（真批次）

`D:\0_Dev\下载图片\AAA\Flow2\0823\视频输出`（225 组 / 1000+ 片段，已知有缺失文案）。

1. 退出码 3，未编码
2. `_stitched\缺失文案清单.txt` 写出
3. 结果 JSON `counts.groups == 225`，`missing_groups` 与清单一致
4. `_stitched` 下没有**新的**成品 mp4
5. 全程无模态框（不要 QMessageBox）
6. 补齐后再跑：退出码 0，产出 225 条 mp4，并写出 `stitched.asproj.json`
7. 与图形界面产出抽样 SHA-256 至少 5 条一致

比对字节前：若要用「界面刚编过的成品」当对照，允许 EncodeWorker 按 `_encoded.json` 跳过未变组；
若要从零比，先清掉 `_stitched/*.mp4` 和 `_encoded.json`。两种都要在验收记录里写明。

---

### T3 — `import_media` / `auto_subtitle` / `save_project` / `split_project`

#### `import_media`

把一组成品视频（通常是 `_stitched\*.mp4`）写成工程文件。
T2 成功时已经写过一份；这个 op 给「只拿得到视频和文案」的调用方。

```
videos   path|list  目录或路径列表
captions text|path  可选，格式同 stitch
out      path       工程输出路径，默认 <dir>/imported.asproj.json
```

构造 task 的规则对齐 `on_drop`：实际分辨率当画布、按 1080x1920 基准缩放默认字幕样式、
主视频+主音频磁性链接、空轨道补齐。

#### `auto_subtitle`

对应「一键全自动」，但**不要**走 `start_batch_ai`。

```
project         path  必填
out             path  可选，默认原地覆盖
force_relayout  bool  默认 false
```

- Mode A：存在「有文案、无 segments」的条目 → 只处理这些
- Mode B：全都已有字幕 → 默认**什么都不做**（防误伤）。必须 `force_relayout: true` 才重打轴
- 每条：`AlignmentWorker(video, text, style, video_w).run()`，然后
  `fix_segment_durations(...)`（`enable_time_postprocess` 默认关，不要擅自打开
  `apply_time_offset` / `merge_short_segments`）
- 每条结束后调用已有的 `trim_gpu_cache`，避免占着显存影响后续导出
- 不弹窗、不碰播放器、不 `QTimer.singleShot`

#### `save_project`

```
project  path  必填（读）
out      path  可选（写；默认覆盖 project）
```

内部就是 `_normalize_project_data` + `serialize_render_task` 落盘。给拆分/打轴复用。

#### `merge_projects`

把多个 `.asproj.json` 的 `tasks` 拼成一个工程。不重跑 whisper，每条视频已有的 `segments` / `text` / 覆层 / 配乐原样保留。

```
projects  path|list  工程文件、目录、或数组。目录下按文件名自然序收取 .asproj.json
out       path       默认 <第一份所在目录>/merged.asproj.json
sort      name|mtime|given   默认 name
dedupe    bool       默认 true，同一视频路径只留一条
```

图形界面：拖文件夹 / 拖多个 asproj / 「文件 → 导入工程文件」走同一套语义（追加进当前列表）。

#### `split_project`

```
project       path  必填
chunk         int   默认 80
outdir        path  默认工程所在目录
name_pattern  可选  默认 <原名>_<start>-<end>.asproj.json
```

按列表顺序切。100 条 chunk=40 → 40 / 40 / 20。不改原工程。
`artifacts` 列出全部产出的绝对路径。
图形界面：`文件 → 自动拆分工程…`（默认每份 40 条，可改）。

#### T3 验收

- 用 T2 写出的工程：`split_project chunk=80` → 3 个文件，界面分别打开是 80 / 80 / 65
- `auto_subtitle` 后用界面打开，抽查 3 条时间轴上有字幕分段
- 对已有字幕的工程不带 `force_relayout` 再跑 → 工程文件内容不变（或 segments 的哈希不变）

---

### T4 — `batch_export` + `--status`

#### `batch_export`

复用现成并行导出路径（资源体检、看门狗、心跳、保活窗）。不要另起一套。

```
project  path  必填
workers  int|null  默认 null → 用 plan_parallel_export
gpu      bool|null 默认 null → 用当前 DEVICE/用户显卡，不读「性能」菜单
mode     string    默认 quality
async    由命令行 --async 控制，不是 params 字段
```

要点：

- **无视** `PARALLEL_EXPORT_ENABLED`（菜单默认是关的）。无头只要条数 > 1 就走并行。
- 不要调用 `_maybe_warn_before_parallel`（它会写 `app_settings.json`）。
- 不要结束弹窗、不要改窗口标题。
- `workers`/`gpu` 为 null 时：可先 `release_gpu_models()` 再 `plan_parallel_export`。
- 用 `QEventLoop` 等到 `ParallelExportManager.finished`。
- 失败下标用 `RenderWorker.run()` 串行重试。
- 输出路径走 `_batch_output_path` 的同等公式，不要改「字幕成品」目录规则。

前台：跑完再返回。
后台 `--async`：立刻返回

```json
{"job": "<id>", "state": "<export_state.json 绝对路径>", "log": "<jobs/<id>.log>"}
```

实现：再 spawn 一个 `python AutoSub_debug.py --do batch_export --params @file --json --job-id <id>`
子进程（Windows 下 `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`），父进程不占锁。
`--job-id` 是内部标志，写到 `user_data/jobs/<id>.json`。

#### `--status [job_id]`

直接读 `export_guard` 已经在写的 `user_data/export_state.json`，不要新造进度机制。
有 `job_id` 时先读 `user_data/jobs/<id>.json` 拿到 pid/state 路径。

映射为：

```json
{
  "op": "batch_export",
  "running": true,
  "done": 59,
  "total": 80,
  "latest": "59_sub.mp4",
  "degraded": "",
  "human_needed": false,
  "pid": 12345,
  "failed": 0,
  "project_queue": {
    "present": true,
    "active": true,
    "current": 2,
    "total": 3,
    "items": []
  }
}
```

`running` ← `status == "running"` 且 pid 仍活着。
`project_queue` 读 `user_data/project_queue.json`，给代理看工程队列走到第几份。

T2 的 stitch **禁止**弹任何窗口；T4 的保活监视器窗口**允许**出现（它是独立进程，本来就是给人看进度的）。

#### T4 验收

- 无头导出一个 80 条工程，数量正确，成品与界面导出抽样逐字节一致
- 导出期间另一条命令 `--status` 能拿到实时进度
- 保活监视器窗口出现并走进度
- 输出目录「导出进行中.lock」更新，结束后变成「导出完成.txt」

---

### T5 — `queue_run`（工程队列）

图形界面：`文件 → 工程队列…`。一个工程批量导出结束后，软件退出并重启，再打开下一份继续导出。无头侧用独立进程模拟同一种「做完就重启」：

```
projects       path|list   工程文件 / 目录 / 数组。sort 默认 given（保持传入顺序）
project        path        单个工程。和 chunk 一起用时先拆再排队
chunk          int         与 project 联用。100 条 chunk=40 → 3 个工程
outdir         path        拆分输出目录
stop_on_error  bool        默认 true
auto_subtitle  bool        默认 true。每个工程导出前先跑 auto_subtitle 并写回工程
workers/gpu/mode           原样传给每一份 batch_export
```

```
python AutoSub_debug.py --do queue_run --params "{\"projects\":[\"a.asproj.json\",\"b.asproj.json\"]}" --json
python AutoSub_debug.py --do queue_run --params "{\"project\":\"big.asproj.json\",\"chunk\":40}" --json --async
```

每个工程默认顺序：有缺字幕才 `spawn auto_subtitle` → 写回工程 → 再 `spawn batch_export`（带 `--queue-child`）。子进程退出后再开下一个。拆分后的工程没有字幕，不要把 `auto_subtitle` 设成 false。`--async` 现在也支持 `queue_run`。
图形界面：`文件 → 自动拆分工程…` / `工程队列…`，勾选「导出前先执行一键全自动」。

`--status` 同时返回当前这份 `batch_export` 进度和 `project_queue`。

#### T5 验收

- `split_plan(100, 40)` → 40 / 40 / 20，三个 `.asproj.json`
- `queue_run` 对两个小工程：先 auto_subtitle（若缺字幕）再 batch_export，第一份进程退出后才启动第二份
- 图形界面：文件菜单能看到「自动拆分工程…」「工程队列…」；队列进行中点「停止批量导出」会暂停队列，不会再自动重启

---

## 5. 已知陷阱

1. **stdout 污染** — 主模块到处是 `print`，且有模块级 print。`--json` 必须在**加载主模块之前**重定向 stdout。
2. **字体加载会弹模态框** — 照抄 `--render-job` 的自检。
3. **`add_text_ov` 依赖播放器** — 本次不做这个节点。
4. **QThread 用 `.run()` 不用 `.start()`** — stitch / AlignmentWorker / RenderWorker 重试。
   **例外：** `ParallelExportManager` 必须事件循环。
5. **`_load_app_settings()` 是全局的** — 无头跑不要改它。并行开关、并行警告勾选都不能写回去。
6. **中文路径** — 素材目录就叫「视频输出」，成品目录叫「字幕成品」。
7. **别在 `--describe` 里 import torch / config / AutoSub_debug。**
8. **Windows 控制台 QuickEdit** — CLI 入口同样调用 `export_guard.disable_console_quickedit()`，
   否则跑 40 分钟时用户点一下终端就假死。
9. **`parse_scripts` 返回元组。** 漏看第二返回值会把错误字符串当 dict 用。
10. **界面缺文案仍编码，无头不编码。** 不要回头去改 AnalyzeWorker 让界面也停下。
11. **`QApplication(sys.argv)` 可能吞掉参数。** 无头建应用用 `QApplication([])`。

---

## 6. 有疑问先读代码，不要猜

- `serialize_render_task` / `deserialize_render_task` / `_normalize_project_data`
- `AlignmentWorker.run` 与 `on_align_done` 的后处理
- `ParallelExportManager.start` / `_poll` / `finished`
- `media_guard.preflight` 的 fatal vs blocking
- `EncodeWorker` 的 `recycle_enabled` 与 `_encoded.json` 跳过逻辑

读完发现本文哪里写得不对，以代码为准，并把差异告诉用户。
不要为了让代码符合规格而去改分析/渲染代码。

---

## 7. 交付时要给出的东西

1. 改动的文件清单 + 每个文件的改动行数
2. 每个能力的验收测试**实际输出**（不是「应该会通过」）
3. 逐字节一致性对比的结果（若本轮没跑完 225 条，写明跑了什么、没跑什么、为什么）
4. 一份 `--describe` 的完整实际输出
5. 发现的、本规格没写到的问题

**图形界面必须保持完全正常。**
