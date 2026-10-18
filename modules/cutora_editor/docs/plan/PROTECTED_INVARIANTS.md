# 受保护的不变量（改之前先读；改了必须让对应测试仍然绿）

2026-09-18 之前导出连续三天：花屏 / 黑屏 / 崩溃 / 6 个只出 1 个。当天一次修完，Luca 实机
「没有黑屏、没有崩坏、没有卡顿、关掉一切正常、速度也不错」。下面每一条都是那次修出来的，
**任何人（含 Grok 队列任务）动到这些地方，必须先跑对应测试，并在报告里单独写一节说明为什么要动。**
不许「顺手重构」这些文件里的相关段落。

| # | 不变量 | 在哪 | 锁它的测试 |
|---|---|---|---|
| 1 | 导出一律**软件解码**：`_open_video_capture(…, hw=False)`；导出路径不许出现硬解调用 | `render/readers.py`、`render/render_worker.py` | `test_bug42_export_decoders.py::test_export_sites_use_software_decoding`、`test_decoder_conformance.py`（软解必须与 ffmpeg 参考逐字节相同） |
| 2 | 导出解码器**按需开、上限 `max(EXPORT_MAX_OPEN_CAPS, 视频轨数 + 2)` 路、播完即放**（`_ExportCapPool`）；严禁将多轨分屏（5 格 `top1_bottom4`、9 格 `grid3x3`）卡死在 4 路导致每帧 LRU 抖动重开解码器 | `render/render_worker.py` | `test_bug42…::test_pool_*` |
| 3 | 并行规划：系统余量 `MEM_HEADROOM_GB=6.0`、每进程地板 `MEM_PER_WORKER_MIN_GB=2.5`、`<0.2 GB` 的 RSS 样本不记不算 | `render/export_guard.py` | `test_bug42…::test_planner_on_lucas_machine_numbers`、`test_bug40_41…::test_bogus_rss_samples_are_ignored`、`test_bug33_rss_plan.py` |
| 4 | worker **自报 RSS**（进度文件 `rss_gb`），父进程照抄，不量启动壳 | `render/parallel.py` | `test_bug40_41…::test_worker_reports_own_rss_in_progress_file / test_parent_takes_rss_from_progress` |
| 5 | 看门狗**甩负载**：可用内存 < `SHED_RAM_GB` 杀最晚启动的 worker，6 s 内最多一个，只剩一个不杀 | `render/parallel.py` | `test_bug42…::test_watchdog_sheds_newest_worker` |
| 6 | 降级只掐 **NVENC 在编那一路**（`level == gpu_off and use_gpu`），CPU 路和 pause_spawn/serial_rest 不中断在编文件 | `render/render_worker.py` | `test_bug40_41…::test_render_worker_degrade_only_for_nvenc_in_flight` |
| 7 | QThread **先等再换**：`_retire_thread_attr("render"/"worker_ai")`，关窗先叫停再等；finished/error 按本次编号去重 | `ui/main_window.py` | `test_bug39_render_thread_retire.py`（含负对照：不等就崩） |
| 8 | 拼接工程画布 = **用户点的 → 素材尺寸 → 1080×1920 兜底**，两条产出路径都传 `canvas_wh` | `batch/timeline_stitch.py`、`batch/script_stitcher.py` | `test_bug38_stitch_canvas.py` |
| 9 | ffmpeg 管道每帧字节数哨兵 `FrameSizeMismatch`；`QImage(numpy…)` 必须 `.copy()` | `render/pipe_writer.py`、`render/render_worker.py` | （十八报；golden 十二例） |
| 10 | 并行临时目录删前留取证：`logs/export/parallel/<时间>/w*.log, *.progress, degrade.flag` | `render/parallel.py` | `test_bug40_41…::test_cleanup_keeps_forensics` |
| 11 | 欢迎页尺寸按钮由 `CANVAS_PRESETS` 生成，720×1280 第一；左栏 ≤ 380 px | `ui/welcome.py`、`model/canvas.py` | `test_t03_shell.py::test_welcome_size_presets_and_layout_cap / test_welcome_has_no_dead_signals` |
| 12 | 字幕唯一真源是 **subs 轨**（`track.subtitle_events`）；`task["segments"]` 只是派生视图。写字幕只走 `set_segments`，`TranscriptPanel.segments()` 只从轨道读。不许两边各存一份。 | `subtitle/track_source.py`、`ui/panels/transcript.py` | `test_t29_transcript_source.py` |
| 13 | 分屏与背景填充导出性能不变量：(a) `paint_split_video_tracks` 遇 `_RecordOnlyPainter`（`is_record_only`）只取帧记录，严禁在解码线程做离屏合成与镜头模糊；(b) `fill_bg.blur_image` 保持 `_BLUR_MEMO` 帧级缓存，分屏单格通过 `_split_layer_rect` 一次填满，禁止单帧二次模糊；(c) 严禁在解码线程运行中调 `cv2.setNumThreads`（Windows 死锁）；(d) `_SafeRotatingFileHandler` 子进程不触发日志轮转 | `render/split_screen.py`、`render/fill_bg.py`、`render/render_worker.py`、`core/log.py` | `test_t34_split.py`、`test_bug42_export_decoders.py` |

原则（从这三天里学到的，和静默失败目录并列）：
- 上游改了，下游没接 = 没改（BUG-38）。
- 量到的数字先问「量的是谁」（BUG-41：量的是启动壳）。
- 取证材料不许随手 rmtree（没有 w0.log 就抓不到 887a0005）。
- 「应当一致」不算数，golden 跑了才算。
- 详细导出性能根因与反模式见仓库根目录 `片语工作室备忘录.md`。
