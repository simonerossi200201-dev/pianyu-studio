# Q155–Q157 · 整幅背景 · 中央监视器显卡不爆 · 播放头不受原视频时长限制 + 达芬奇手感

> 状态（2026-10-04）：已实现。

## Q155 整幅背景（`cutora/render/backdrop.py`）

分屏时留白处垫什么，四选一（同一时刻只认一种）：

| 模式 | 存在哪 | 画法 |
|---|---|---|
| 每格各自（默认 / 老样子） | 片段 `bg_fill` + 左右完整的 `split.backdrop.mode=each` | 每格垫自己那条的背景（左右分屏就是左右两块） |
| 单个视频撑满整幅 | `split.backdrop = {"mode": "full", "cell": "A"}` | 整幅只垫 cell 那格那条视频的镜头模糊，**任何布局**都行；格子里片段自己的背景填充这一帧不画 |
| 外部图片 | `task["bg_image"] = {"path", "blur", "dim"}` | 铺满（cover）画在最底下，缓存按 (文件, 尺寸, 模糊, 压暗) |
| 外部视频（循环铺满时长） | 时间线最底下一条 `Track.bg_role="backdrop"` 的视频轨 | 片段 `loop=True, match_duration=True, _match_target_dur=时长, fit=fill`，静音；不进分屏格子、不算主轨 |

- `fill_bg.fill_spec_at` 读 `backdrop.fill_suppressed()`（按线程）：整幅背景生效时片段自己的填充 → none。
  三个渲染入口（canvas 预览 / preview.engine 监视器 / render_worker 导出）都 `begin_frame(task)` … `end_frame()`。
- `split_screen.paint_split_video_tracks`：bg_role 轨先铺满画在最底；`full` 模式一张整幅 `_blur_plate(源轨)` DestinationOver。
- `Track.bg_role` 进 `TRACK_KEYS`，project_io / main_window 读写都认。`monitor_model.video_tracks / main_video_track` 排除背景轨；
  `view_signature` 含 bg_role / bg_image；快照含 `bg_image` 和每条片段的 `bg_fill`（撤销能回来）。
- 入口：中央监视器右键「整幅背景 ▸」（每格各自 / 单个视频撑满（原视频 / 指定哪一格）/ 外部图片… / 外部视频…），多选批量、可撤销；
  批量加载「视频」分页的「分屏背景」下拉（每格各自 / 单个视频撑满整幅，所有布局可选；老偏好 first → full）；
  检查器「分屏 › 分屏背景」。右键「背景填充」任一项 = 回到每格各自（并去掉外部背景）。

## Q156 中央监视器显卡不爆（`cutora/ui/central_monitor.py`）

根因：监视器解码器走 `cv2 VIDEO_ACCELERATION_ANY`（D3D11VA / NVDEC），几十条视频轮流开关硬解会话 → `DXGI_ERROR_DEVICE_REMOVED`，黑屏。
导出早在 BUG-42 就改软解了，监视器没跟上。

- `CapturePool(capacity, hw=False)`；监视器池 `MONITOR_POOL=8` 软解；`batch_preview.read_video_frame` 也明确软解。
- 开窗渐进：没 show 之前 `viewport_rect()` 为空 → 什么都不排队；`showEvent` 才开始，头 `WARM_COUNT` 格之间 `WARM_GAP_MS`；
  relayout 按 `tile.req_w` 比，不再把后台正在画的格子作废重开。
- 同时播放上限 `PLAY_MAX`（= 池大小）：「全部播放」先播屏幕里的，超了把最早开播的那格换下来。
- 关窗 / 闲置渐进：`_LazyPool.release_gradually` 一个一个 release、中间歇 `RELEASE_STEP_SEC`。

## Q157 播放头 / 达芬奇手感

- `preview.engine.playback_end_sec(task)` = max(时间线最远片段, 渲染终点 trim_end_sec)；主窗口 `_timeline_end_ms` 用它。
  原视频时长不是上限。
- 预览混音 `render_preview_wav` 末尾 `apad=whole_dur=播放终点`（指纹含它），播放器时钟能走完整条时间线；
  万一混音还是比时间线短（老缓存），`sync_ui` 看到 `EndOfMedia` 且没到结尾 → 改墙钟接着走，不再跳回开头。
- 时间线快捷键：**Delete = 波纹删除**（连空隙），**Backspace = 普通删除**；**Shift+[ / Shift+]** = 删掉播放头左 / 右（= Q / W）。
- 标尺上按住播放头头部（±7 px）优先抓播放头一直拖；擦洗期间不让音频播放器每步 seek，松手再 seek 一次。

## Q160 单个视频分段并行导出（`cutora/render/segment_export.py`）

- 媒体库只有 1～5 条要导出时，原来一条视频一个渲染进程（一条解码线程 + 几个受 GIL 限制的合成线程），多核机器大半闲着。
- `SegmentExportWorker`（和 RenderWorker 同样的信号 / stop / produced_paths）：按帧切成 K 段
  （`segment_count`：CPU 每 4 线程一段、2～8；每段 ≥ 4 秒；内存 / NVENC 会话再砍），每段一个 `--render-job` 子进程，
  任务项带 `frame_range=[s,e)` + `video_only=True` + `export_profile_id`；子进程里仍是 RenderWorker（只画这一段、只出视频、
  不许自己换编码器）。父进程 `ffmpeg -f concat -c copy` 拼接 → `RenderWorker.finish_from_video`（整条导出同一段收尾：混音 /
  验收 / 旁车 / 平台副本）。任何一段失败 → 本线程串行重做整条。
- 入口：`run_export_worker` 单条导出；批量条数 < 并行进程数时逐条分段。性能菜单「单个视频分段并行导出」开关
  （设置 `segment_export_enabled`，环境变量 `CUTORA_SEGMENT_EXPORT=0/1` 压过）。
- 和整条导出的差别：每段开头多一个关键帧；段首一两帧比整条导出「准一帧」（整条导出的顺序解码几十帧后落后源一帧，
  老问题，改它会动金样例，没动）。金样例仍走 RenderWorker.run()。
- RenderWorker 新参数 `frame_range` / `video_only`；`export_timing()`、`finish_from_video()` 从 `render_wysiwyg_export` 里拆出来共用；
  进度按「这次要画的范围」算。

## Q161 历史记录：写清楚、去噪、合并、不吃内存

- 每一步的说明由 `core/undo_labels.describe_command(命令, 参数)` 生成：「移动片段 → 12.30 s」「改字幕「你好…」」「关键帧 x @ 1.20 s」「分屏布局：左右裁切」……拿不准退回命令标题。
- 没改任何东西的一步（点一下、拖了又放回去）：之后 == 之前（`UndoSnapshot.same_as` 结构比较）→ 不留。
- 同一个命令、同一批身份参数、1.2 秒内连着来（拖滑杆 / 微移连按 / 拖关键帧，`undo_labels.MERGEABLE`）→ 合成一步，
  只更新上一条的「之后」；撤销一次回到手势开始前。
- 内存预算 `HISTORY_BUDGET_MB=256`（按快照里拷了多少对象估算），超了从最老的丢；加上 Q159 的按视频存，
  一步通常只有几十 KB～几 MB。
- 撤销时媒体列表只在名单真的变了才重建（以前每次撤销清空重建 + 每条重新开视频解缩略图）。
- 检查器换面板时标签列宽重算攒到事件循环空档做一次（以前几十个 resizeEvent 各扫一遍整棵树，点一下片段卡 200 ms）。
- 专业软件怎么做：命令模式（每一步只记「怎么撤回」的差量，内存和改动大小成正比）。我们现在是「按视频的快照 +
  去噪 + 合并 + 预算」，一步 ≈ 一条视频的大小、毫秒级；真正的差量式撤销要给 150 多条命令各写逆操作，列为后续项。

## Q162 批量加载 → 「语音转文本」分页（`ui/batch_asr_tab.py` + `batch/asr_export.py`）

- 来源：左边勾选的媒体库视频 / 外部文件或文件夹（音频也行，`MEDIA_EXTS`）。语言（自动检测 / 列表）、模型（未下载的会提示下载）。
- 识别 = 自动字幕同一条 AlignmentWorker 不给脚本（纯识别），`AsrBatchThread` 后台逐个跑，进度 / 取消（取消在当前文件到下一阶段时生效）。
- 输出：纯文本 .txt（一行一句）/ 带时间戳 .txt / 字幕 .srt，放源文件旁或指定文件夹；可再合成一份「转写合集_时间.txt」；
  可选同时写进媒体库这条视频的字幕（替换）。结果列表 + 日志 + 「打开输出文件夹」。偏好记在 `batch_asr_prefs`。
- 对话框图片预览里不画转场（Q158 之后入场动画真的会画，预览停在开头那帧图片是透明的）。
