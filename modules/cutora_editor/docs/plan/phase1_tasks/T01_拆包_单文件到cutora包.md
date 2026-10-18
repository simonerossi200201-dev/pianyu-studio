# T01 — 拆包：29,587 行单文件 → `cutora/` 包（零逻辑改动）

> 前提：T00 已完成且 golden 全绿。先读 `docs/plan/01_GROK_通用规范.md`。
> 这是 Phase 1 **最危险**的任务。规则只有一条：**搬家，不装修。** 只移动代码、修 import、修相对路径；不重命名函数、不改签名、不合并重复代码、不删"看起来没用"的东西、不格式化。
> 验收只有一条：`golden_check.py --all` 逐字节一致。

## 1. 目标结构

```
cutora/
├── __init__.py            # __version__ = "7.0.0-dev"
├── __main__.py            # python -m cutora  → app.main()
├── app.py                 # 原 if __name__ == "__main__" 段：QApplication、字体加载、--render-job / --describe / --status 分支
├── core/
│   ├── config.py          # 原 core/config.py（唯一 GPU 判断入口）
│   ├── paths.py           # 新：集中定义 RUNTIME_DIR / FFMPEG / MODELS / USER_DATA / RESOURCES 路径（本任务只做"把散落的路径拼接收拢到这里"）
│   ├── log.py             # 新：get_logger()；本任务只提供函数，不替换现有 print
│   ├── history.py         # HistoryManager
│   ├── utils.py           # 原 utils.py + debug_print.py
│   └── storage.py         # 原 storage.py
├── model/
│   ├── timeline_model.py  # 原文件
│   ├── timeline.py        # 原文件
│   └── project_io.py      # serialize_render_task / deserialize_render_task / _normalize_project_data 及 .asproj 读写相关函数
├── asr/
│   ├── engine.py          # get_whisperx_model / get_align_model / _sync_device_globals / trim_gpu_cache / 模型缓存全局
│   ├── align_worker.py    # AlignmentWorker + apply_time_offset / merge_short_segments / fix_segment_durations
│   ├── fonts.py           # FontManager / load_embedded_fonts / _iter_font_files（字体与识别无关，但它们在 AlignmentWorker 前后耦合；放 ui/ 更合适——见 §3 决策）
│   └── download.py        # download_full_models.py / download_en_align_model.py
├── render/
│   ├── frame_renderer.py  # FrameRenderer + 全局缓存 (_SE_SUB_CACHE, _SE_OV_CACHE, _IMAGE_CACHE, _DBI_*)
│   ├── export_renderer.py # ExportRenderer
│   ├── readers.py         # _RawARGBMOVReader, _FFmpegFrameReader, AsyncVideoDecoder
│   ├── render_worker.py   # RenderWorker, PreviewFxWorker, build_overlay_index, _has_visible_overlay
│   ├── parallel.py        # ParallelExportManager
│   ├── export_guard.py    # 原文件
│   └── anim_engine.py     # 原文件
├── subtitle/
│   ├── presets.py         # 原 preset_manager.py
│   └── text_utils.py      # clean_text / format_time_ass 等从 utils 里与字幕相关的
├── audio/
│   ├── spatial.py         # spatial_audio.py
│   ├── spatial_ir.py
│   ├── sfx_library.py / sfx_synth.py / sfx_ai.py
│   ├── spectrum.py        # audio_spectrum.py
│   └── waveform.py        # waveform.py + _WaveformWorker
├── ai/
│   └── (空 __init__.py；T09 建 providers.py)
├── batch/
│   ├── script_stitcher.py / script_broll_scatter.py / script_insert_image.py / script_ui.py
│   ├── media_guard.py
│   ├── wenan_norm.py (+ wenan_words.txt 移到 resources/data/)
│   ├── queue_ui.py        # project_queue_ui.py + queue_relaunch.py
│   └── cli.py             # autosub_cli.py
└── ui/
    ├── main_window.py     # MainWindow（本任务允许它仍然巨大；T03 再拆）
    ├── canvas.py          # VideoCanvas, InlineTextEdit, MultiGridTextEdit
    ├── timeline/
    │   ├── widget.py      # TimelineWidget
    │   ├── header.py      # TrackHeaderPanel
    │   └── container.py   # TimelineContainer
    ├── panels/
    │   ├── subtitle_style.py   # SubtitleStylePanel, SubtitleEditDialog
    │   ├── property.py         # PropertyPanel, ColorOptionPanel
    │   ├── role_mapping.py     # RoleMappingPanel
    │   ├── keyword_rules.py    # KeywordRuleRow, KeywordRulesWidget
    │   ├── word_block.py       # WordBlockPanel
    │   ├── media_list.py       # DraggableListWidget, TaskTreeWidgetItem
    │   ├── fonts.py            # FontManager, FontManagerDialog, load_embedded_fonts
    │   ├── text_editors.py     # RichTextEditorDialog, SimpleTextEditorDialog
    │   └── audio_fx.py         # sfx_panel.py (AudioFxDialog, _AiWorker)
    ├── widgets/
    │   ├── collapsible.py      # CollapsibleSection, ToggleSwitch, davinci_panels.py
    │   └── lazy.py             # _LazyModule
    └── theme/
        └── legacy.qss          # 原 ui_style.qss
resources/
├── icons/ (原 core/icons)
├── data/wenan_words.txt
└── (assets/ir, assets/sfx 移到 resources/audio/ir, resources/audio/sfx)
tests/
├── golden/ (T00)
└── test_*.py (原 core/test_*.py 移过来并修 import)
```

`core/` 目录最终应为空并删除；`AutoSub_debug.py` 不再存在。**如果某个符号放不进上面的表**，放到最接近的模块并在报告里列出，不要新建"misc.py"。

## 2. 方法（必须按此顺序，每一步跑 golden）

1. **先建脚手架**：建好所有目录与空 `__init__.py`，`cutora/app.py` 暂时 `from core.AutoSub_debug import *`（临时垫片）并让 `CUTORA.bat` 改跑 `python -m cutora`。跑 golden，全绿后提交。
2. **先搬外挂模块**（它们已经是独立文件）：`timeline_model / timeline / anim_engine / preset_manager / storage / utils / export_guard / media_guard / spatial_* / sfx_* / audio_spectrum / waveform / wenan_norm / script_* / autosub_cli / project_queue_ui / queue_relaunch / davinci_panels / download_*`。每搬一个：`git mv` → 改所有 `import` → 在旧位置留一个一行的 re-export 垫片（`from cutora.audio.spatial import *  # legacy shim, removed in T03`）→ 跑 golden → 提交。
3. **再拆主文件**。写一个一次性脚本 `tools/split_monolith.py`：
   - 用 `ast` 解析 `AutoSub_debug.py`，按顶层节点（class / def / 全局赋值 / import）切块，每块带原始行号范围与原始文本（用 `ast.get_source_segment` 保留原文，**不要用 unparse**，否则注释与格式全丢）。
   - 按 §1 的映射表（写成脚本里的 dict：`符号名 → 目标模块`）把块写入目标文件；模块级全局变量跟随第一个使用它的类走，并在报告中列出每个全局变量的去向。
   - 每个目标模块顶部自动生成 import：先复制原文件的全部 import 行，再由 `pyflakes` 报告的未使用项裁剪（裁剪只删 import，不删别的）。
   - 跨模块引用：由脚本生成显式 `from cutora.x.y import Z`。**禁止**用 `import *` 解决循环引用；遇到循环引用用函数内延迟 import，并在报告里逐个列出。
   - 主文件中的 `__main__` 段整体进 `app.py::main()`。
4. 全局可变状态（`_SE_SUB_CACHE`、`_IMAGE_CACHE`、`_DBI_VIDEO_CAPS`、`DEVICE` 同步等）**必须留在同一个模块里被同一组函数访问**——它们的正确性依赖"同一模块全局"。`_sync_device_globals()` 要改成同步到 `cutora.asr.engine` 的模块变量，并确认 `whisperx.align(..., DEVICE, ...)` 读的是同一个。
5. `--render-job` 子进程入口：`python -m cutora --render-job <json>` 必须和原来一样不加载 torch（`_LazyModule` 机制保留），验证方法：子进程 `import` 耗时 < 0.5 s，且 `nvidia-smi` 中无子进程显存占用。
6. 拆完后删除所有垫片、删除 `core/` 目录。跑 golden、跑 pytest、提交、打标签 `T01-done`。

## 3. 需要你做的决策（在报告里说明）

- `FontManager` 放 `ui/panels/fonts.py` 还是 `asr/`：**放 ui**，`AlignmentWorker` 若引用字体（例如自检 `FONT_DIR`），通过 `cutora.core.paths.FONT_DIR` 解耦。
- `MainWindow` 的方法里内联构造的对话框（如 `open_export_settings` 里的导出设置对话框）本任务允许留在 `main_window.py`，报告里列出，T03 再拆。`StitcherDialog` / `open_stitcher_dialog` 本来就在 `script_stitcher.py`，随它一起进 `batch/`。

## 4. 验收标准

- [ ] `golden_check.py --all` 七个 case 全部逐字节一致（含并行 = 串行）。
- [ ] `python -m cutora --describe`、`--status`、`--render-job` 三个无头入口行为不变。
- [ ] `cutora/batch/cli.py` 跑 g07 与 T00 结果一致。
- [ ] 全仓库 `grep -rn "AutoSub_debug"` 为零（文档除外）。
- [ ] 没有任何模块 > 800 行，**除了** `ui/main_window.py`、`ui/canvas.py`、`ui/timeline/widget.py`、`render/frame_renderer.py`、`ui/panels/subtitle_style.py`、`ui/panels/property.py`（这六个允许超，T03 处理）。
- [ ] `pyflakes cutora/` 无 "undefined name"；允许 "imported but unused" 但要列数量。
- [ ] `tools/split_monolith.py` 与映射表提交进仓库（`tools/` 下），方便回溯每个符号来自原文件哪一行。
- [ ] 报告附：符号 → 模块的完整映射表（可以是生成的 CSV）；全局变量去向表；循环引用处理清单。

## 5. 不要做

- 不重命名任何函数/类/变量（模块名可以变，符号名不能变）。
- 不改任何字符串、任何数值、任何默认参数。
- 不"顺便"把 print 换成 logging。
- 不改 `.qss`。
- 不动 `tests/golden/`。
