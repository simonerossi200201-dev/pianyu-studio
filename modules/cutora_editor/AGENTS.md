# CUTORA / 片语 Studio —— 给接手这个仓库的 AI 的交接说明

> **读法**：你是来继续维护、修 bug、加功能的 AI（Claude、Grok、Codex、Gemini……都一样）。
> 开工前把这份文件从头读完，再读第 4 节列出的三份文件。读完之后你应该知道：什么绝对不能碰、
> 代码在哪、怎么验证、怎么提交、怎么和 Luca 说话。
> 这份文件本身也要维护：你学到了这里没写的「下一个 AI 必须知道」的东西，就补进来，和代码一起提交。

---

## 1. 这是什么、给谁用、谁说了算

- **产品**：片语 Studio（内部代号 CUTORA），Windows 桌面视频剪辑软件，PySide6 + OpenCV + FFmpeg + PyTorch。
  核心用途：批量做竖屏短视频——自动字幕（whisperX）、风格化字幕动画、绿幕抠像、调整图层、拼接工程、
  批量加载、并行导出（NVENC）。一个工程常有 40 来条素材，一次批量导出 40 多个成片。
- **只做非盈利内部使用，不对外分发。**
- **Luca** 是唯一的决策人和使用者。他不读代码，看的是：软件开起来顺不顺、画面对不对、
  导出多快、内存/CPU 占多少。**所有对他的汇报用中文**，简短、具体、给数字，不讲术语堆砌。
- 过去的分工：Claude 做设计、审查、疑难问题；Grok 按任务书在本机写代码。现在任何 AI 都可能接手，
  规矩一样。

## 2. 机器与路径（Luca 的电脑）

| 东西 | 位置 / 数值 |
|---|---|
| 仓库 | `D:\0_Dev\CUTORA`（分支 `main`） |
| Python | `runtime\venv\Scripts\python.exe`（3.10.11；底座 `runtime\python`）。**不在 git 里** |
| FFmpeg | `runtime\ffmpeg\ffmpeg.exe` / `ffprobe.exe` |
| 模型 | `runtime\models\` |
| 用户数据 | `%LOCALAPPDATA%\CUTORA`（`CUTORA_USER_DATA` 环境变量可覆盖）：`logs\`、`cache\`、`autosave\`、`app_settings.json` |
| 主日志 | `logs\cutora.log`（4 MB 轮转成 `.1 … .5`），`logs\faulthandler.log`，`logs\commands.jsonl` |
| 导出取证 | `logs\export\parallel\<时间>\w*.log / *.progress / result / gpu_server.log` |
| 硬件 | i9-13900KF（8P+16E，32 线程）、RTX 5060 Ti 16 GB（sm_120，NVENC 实测 8 路）、内存约 32 GB |
| 软件栈 | PySide6 6.10.2、torch 2.7.1+cu128、OpenCV 4.9、numpy 1.26、mimalloc（已打进 runtime） |
| 运行库里**没有** | `psutil`（要量内存/线程用 ctypes 调 Windows API，参考 `render/export_guard.py`） |

## 3. 红线（违反任何一条 = 这次工作作废，不管功能多好）

一字不改，照做：

1. **golden 基线变更一律停下报 Luca，不许自行重录**；基线只允许「先加后退役」
   （新加一个用例录新画面，旧用例等 Luca 批准再退役）。任何自动化、任何 AI 会话都无权批准重录。
   这同样适用于 UI 静帧（`tests/ui_golden/*/expected.sha256`）。
2. **不引入中国服务器 / CCP 关联依赖**——这是安全问题不是许可问题。域名黑名单与白名单见
   `docs/plan/01_GROK_通用规范.md` §2。运行期只有用户点击才联网。
3. **GitHub 只放源码**：runtime / 模型 / FFmpeg / 成片 / build 产物永不入库（`.gitignore` 已覆盖，别绕过）。
4. **字体只用已入库的 OFL / Apache**。图标 Lucide 或自绘；不内置他人素材；**Grok 生成的媒体素材不许打包进软件**。
5. **模型只用 Apache / MIT / BSD 许可**，并且用可用的里最强的，不许为省事降级。
6. 风格预设不得用纯黑背景 / 描边 / 文字色；「圣咏」风格不加外发光。
7. **不动 CRF / preset，不加「快速模式」**——画质不拿来换速度。
8. **导出永远软件解码**（`PROTECTED_INVARIANTS.md` 第 1 条）。`export_hw_decode()` 只是诊断开关。
9. `PROTECTED_INVARIANTS.md` 里的不变量不许碰；要碰先停下问。
10. **任何时候只允许一个 AI 动这个仓库。** 开工前 `git status` 看有没有别人没提交的改动——
    有就先问 Luca 是谁的、要不要先提交，**不许覆盖别人的未提交改动**。
    Grok 不许自己启动 `RUN_GROK_QUEUE.bat`；队列在跑时不许另开会话改仓库。
11. 不删除既有功能；Grok 做的功能 Luca 说「很好用」——有问题就修，不许回退。
12. 文字工具：不碰 `render/textfx/`；老工程 overlay 的 x/y/w/h 原样读取。
13. 不打印、不记录任何 API key；不改名 / 删除 `~/.grok`。
14. 打包：除少数大语言模型外全部离线打包；`build/wheels` 保留。

## 4. 开工前必读（按顺序）

1. 本文件。
2. `片语工作室备忘录.md` —— **AI 必读**：分屏、多轨解码器池、镜头模糊与并行导出的全部性能根因与严禁回退的技术手段。
3. `docs/plan/PROTECTED_INVARIANTS.md` —— 13 条受保护不变量和锁住它们的测试。
4. `docs/plan/01_GROK_通用规范.md` —— 安全红线细则、代码规范、golden 规则、报告模板。
5. 需要时：`docs/plan/BACKLOG.md`（待办唯一真源，汇报照它念，不凭记忆）、
   `docs/plan/STABLE_POINTS.md`（可退回的稳定标签 + 每一次 golden 重录的授权记录）、
   `docs/plan/QUEUE_SAFETY_NET.md`（Grok 队列运行器的规矩）、`tests/golden/README.md`。

## 5. 代码地图

```
cutora/
  app.py            GUI 入口：启动顺序、闪屏、主窗口、启动后台任务（NVENC 实测、字体预热、内存采样）
  __main__.py       python -m cutora；也是各种子进程入口（--gpu-fx-server、渲染 worker）
  core/             runtime（运行库加载顺序）、config（GPU 判断唯一入口 detect_gpu_config）、paths、log、
                    commands（命令总线）、history/undo_snapshot（撤销）、autosave、stall_watchdog（界面卡顿取栈）、
                    counters（活动计数，进 [Memory] 行）、net（联网白名单）、secrets（keyring）
  model/            Timeline / Track / Clip 数据模型、画布预设
  ui/               main_window.py（约 1.5 万行的主窗口，CommandBodies 混入）、canvas.py（预览画布）、
                    timeline/（时间线控件）、panels/（检查器、字体、风格）、central_monitor.py、keyframe_editor.py、
                    theme/（tokens、UI 字体）、i18n.py（tr()）、idle_wake.py（空闲停帧的叫醒）、
                    rooms.py（房间 + 底部页面栏）、inwindow.py（所有弹窗进主窗口的浮层 / 侧滑面板）
  preview/          engine.py（CapturePool、预览帧合成）、worker.py（PreviewWorker 线程）
  render/           导出：render_worker（单进程导出、_ExportCapPool）、parallel（并行导出管理、看门狗）、
                    export_guard（并发规划、NVENC 实测、内存诊断）、compose_parallel、segment_export、
                    readers（解码器、AsyncVideoDecoder）、decode_prefetch、grade、adjust_layer、visual_stack、
                    export_verify（成片抽帧 MAD 验收）、textfx/（文字特效，别碰）
  fx/               抠像 keyer、遮罩、AI 抠像、跟踪；gpu.py + gpu_ops.py = GPU 像素运算服务（见 §6）
  subtitle/         字幕排版、断句、字体覆盖（font_coverage）、track_source（字幕唯一真源 = subs 轨）
  asr/              whisperX 识别 / 对齐（只在真正用到时加载 torch）
  batch/            批量加载、拼接工程（timeline_stitch、script_stitcher）
  audio/            stem_cache.py（原声 / 配乐 / 音效三条 PCM，全进程一台 sink）、
                    stem_worker.py（人声分离子进程；界面不 import 模型）、空间立体声、音效、降噪、闪避
  media/ assets/ api/（本地 API / MCP）
tests/              pytest；test_<编号>_<主题>.py；golden/（渲染基线）、ui_golden/（界面静帧基线）
tools/              golden_check.py、ui_golden.py、pytest_each.py、queue_runner.py、security_scan.ps1……
docs/plan/          计划、规范、不变量、报告（reports/）、队列（queue/）
```

**进程结构**（性能问题几乎都和这个有关）：

- **主界面进程**：Qt 界面 + 预览。**不 import torch**（Q187，省下 2～3 GB）。预览解码器是有上限的 LRU 池
  （`PREVIEW_DECODER_POOL`=6，闲置 30 s 回收，每个 2 条解码线程、16 帧预读），预览默认软件解码。
  人声分离是另一个子进程 `python -m cutora.audio.stem_worker`，不进 gpu-fx-server，导出进行中不启动。
- **GPU 像素运算服务**（`python -m cutora --gpu-fx-server`）：抠像 / 调整图层的 GPU 计算都在这里，
  共享内存 + 本机认证连接，CUDA graph 池。主界面启动后在后台起一个常驻的，导出 worker 复用它。
  日志：`logs\gpu_fx\` 和每次并行导出目录下的 `gpu_server.log`。
- **导出 worker 进程**：`render/parallel.py` 按内存和 NVENC 路数规划并发数（`plan_parallel_export`），
  LPT 按工作量分配视频。少于 4 个视频走分段导出（`segment_export`）。

**界面结构：只有一个窗口**（Q191/Q192，Luca 的硬要求：主窗口任何时候都能拖动，不许被子窗口钉住）：

- 底部页面栏 = 剪辑页 + 房间（`cutora/ui/rooms.py`）：批量加载 / 中央监视器 / 片段拼接。
  片段散落在批量加载的最后一个分页里，不再占底部栏一项（Q201）。
  大型功能做成房间：`host.register(key, 标题, 图标, factory, keep_alive, on_enter, on_leave)`，
  factory 里用 `embed_dialog(dlg)` 把原来的 QDialog 变成普通子控件；它自己的「关闭」= 完成并退出。
  进房间会收起停靠面板、暂停预览、让出剪辑页快捷键（`ShortcutGate`，Ctrl+S 等保留）；离开时释放解码器 / 播放器。
- 其它所有对话框自动进主窗口（`cutora/ui/inwindow.py`）：`QDialog.exec`、`QMessageBox.warning/question/…`、
  `QInputDialog.getText/…`、`QColorDialog.getColor` 已被接管，**写新功能照常 `dlg.exec()` / `QMessageBox.question(...)` 就行**，
  会自动变成窗口中间的浮层卡片；设置类在 `RIGHT_CLASSES` 里或 `dlg.setProperty("cutora_sheet", "right")` → 右侧滑出。
  `dlg.setProperty("cutora_sheet", "fill")` → 铺满主窗口客户区，并跟着窗口一起缩放（视频连线导入、图片节点连线都用这个）。
  以前用 `dlg.show()` 的非模态窗口改用 `inwindow.show_sheet(dlg)`（不压暗幕、不挡旁边）。
  例外：文件选择框保持系统原生；独立导出监视器和它的提示框在外面；`dlg.setProperty("cutora_external", True)` 强制做独立窗口。
- **不要再新建独立的顶层窗口**（除非 Luca 点名要）。
- 房间内容外面套了滚动区（`_scroll_box`）：房间再高也不会把主窗口撑大（1366×768 小屏实测过）。

**低配电脑**（Q193）：目标用户内存 32 GB 为标准、16 GB 最低；并行导出路数按可用内存/CPU 自动定，不用改。
没有 N 卡：剪辑 / 预览 / 导出照常（导出改 CPU 编码）。**新加要 GPU 才流畅的功能**，开跑前调
`cutora.ui.gpu_notice.confirm(parent, "<key>")`（在 `FEATURES` 里登记名字和「会慢多少」），没 N 卡时会问一次。
自动触发的地方用 `declined(key)` 判断用户是否已拒绝。模拟没显卡：`CUTORA_FAKE_NO_GPU=1`；日志 `[HW]`。
测试里 conftest 默认 `CUTORA_FAKE_GPU=1`，防止测试机没 N 卡时弹框卡住。

## 6. 现在的性能水平（别退回去）

2026-10-07 / 2026-10-15 实测，这些都是一点点抠出来的，改动相关代码后要能复现：

| 指标 | 数值 | 之前 |
|---|---|---|
| 42 个视频批量导出 | 22 分钟，峰值 205～250 fps | 120 分钟（退化时曾跌至 62 fps / 5 格分屏 0.4 fps） |
| 主界面常驻内存（工作集） | 1.2～1.4 GB | 3.4～5.1 GB |
| 开着工程空闲时 CPU | 约 0.06 核 | 0.4～0.56 核 |

靠的是：mimalloc；GPU 抠像/调整层服务 + CUDA graph；NVENC 并发路数由软件自己实测（设置里 0 = 自动，
手动值只给个别用户）；按工作量 LPT 分配；主界面不加载 torch；预览解码器有上限会回收；
预读线程每个解码状态一条常驻而不是每帧一条；字体 cmap 用位图并缓存到磁盘；
暂停时心跳不再重复要预览帧（Q189g，有输入/编辑或 5 s 兜底就恢复，Q189h）。

**2026-10-15 导出性能红线（详见 `片语工作室备忘录.md`，严禁破坏）**：
1. **解码器池容量随轨道数动态抬升**：`_ExportCapPool` 的 `max_open` 必须为 `max(EXPORT_MAX_OPEN_CAPS, len(_video_tracks_for_export) + 2)`。严禁对 5 格分屏（`top1_bottom4`）或 9 格分屏（`grid3x3`）硬卡 4 路上限，否则每帧 5 路源轮流淘汰重开 + 冷 Seek 会把帧率打到 **0.4 fps**。
2. **解码线程只取帧、不画分屏**：`split_screen.py::paint_split_video_tracks` 遇到 `getattr(painter, "is_record_only", False)`（`_RecordOnlyPainter`）时只遍历轨道取帧，严禁在解码线程创建离屏画布、跑 `lens_blur` 或合成蒙版（否则每帧重画两遍，取帧 CPU 暴涨 40 倍）。
3. **镜头模糊帧级缓存与单次填底**：`fill_bg.blur_image` 保持 `_BLUR_MEMO` 缓存；分屏通过 `lp._split_layer_rect` 与 `lp._split_qimg` 让每格每帧最多算一次模糊，且支持 24fps 素材在 30fps 时间线上命中 `_fx_memo_get` 复用与 OpenCV 源帧预缩（`_export_shrink_fx`）。
4. **严禁中途调 `cv2.setNumThreads`**：后台预读解码线程运行期间调用 `cv2.setNumThreads()` 会在 Windows 下触发 OpenCV 线程池死锁，只能在 `parallel.py::run_render_job` 进程入口调用一次。
5. **多进程日志轮转防锁死**：`cutora/core/log.py` 使用 `_SafeRotatingFileHandler`，子进程不轮转 `cutora.log`，主进程捕获 `PermissionError`（`WinError 32`）。

导出现在卡在 CPU 软件解码上（不变量 #1 不许硬解）。再提速只剩会改变像素的路（GPU 合成、代理文件），
**必须先找 Luca 决定，并走 golden「先加后退役」**。

## 7. 诊断：日志就是一切

Luca 的原话：「全部信息你自己保存到日志文件里，我不粘贴给你。」所以：

- **不要让 Luca 复制粘贴日志或截图里的数字。** 你需要的信息，先写进 `cutora.log`，让他重启/操作一次，
  然后你自己去读日志文件。
- 已有的诊断（都在 `cutora.log`）：
  - `STARTUP git=<提交> …`：每次启动一行，先确认他跑的是不是你的提交。
  - `[Memory]`：启动 25 s、120 s，之后每 10 分钟一行：工作集 / 私有提交 / Python 堆里的图像 /
    模块按家族 / mimalloc / 线程按起始模块 / 各线程 CPU / 「活动计数」（时间线、画布重画、预览请求次数）。
  - `cutora.stall`：界面线程卡 ≥0.5 s 时的 Python 调用栈；「没有 Python 代码在跑」= 卡在 Qt 的 C++ 里。
  - `[Export] 各源取帧`、并行导出的计划说明行（为什么开几个进程、NVENC 上限还是内存在限制）。
- 要加新诊断：用 `cutora.core.log.get_logger(__name__)`，**不 print**；采样类的放后台线程，别让诊断本身卡界面。
- 先问「量的是谁」：以前量到过启动壳的 RSS、量到过错的进程。

## 8. 加功能 / 修 bug 的做法

1. 先读相关代码和对应的测试，**以代码为准**，文档和任务书可能过时。
2. 用户可见的新操作要注册成命令（`cutora/core/commands.py`），界面只调命令；用户可见文字包 `tr()`。
3. 能批量的事给批量入口（多选 / 配方），不要只做单个版本。
4. 新功能附测试（`tests/test_q<下一个编号>_<主题>.py`）。测试里不许写产品没有的环境绕过。
5. 动到 `render/ subtitle/ audio/ batch/` → 跑 golden：
   `runtime\venv\Scripts\python.exe tools\golden_check.py --all`（或 `CHECK_BASELINE.bat`）。
   新功能**关闭时逐字节相同**；开启时另存新用例，Luca 看片通过才加入。
6. 动到界面外观 → 跑 `tools\ui_golden.py --all`。哈希变了 = 停下，出前后对照图给 Luca，
   等他批准后新增用例（如 `u05c`），旧用例从 `CASES` 拿掉、写进 `RETIRED`，授权记进 `STABLE_POINTS.md`。
   新用例的 `expected.sha256` **由 Luca 的机器第一次跑时自动录**（云端 / 其他机器字体和 DPI 不同，不在别处录）。
   对照图放 `docs/plan/reports/<编号>_ui_diff/`（`reports` 下的 png 不入库，留在本地当证据）。
7. 不顺手重构任务范围外的代码；看起来没用的功能写进建议，不删。
8. 任何新入口（GUI / CLI / 测试 / 子进程）先 `init_torch_runtime()` 再 import PySide6（渲染子进程除外），
   运行库加载顺序靠它。
9. 编号：任务叫 `Q<数字>`（2026-10-11 这批做到 Q263，内部包 v7.0.0-beta9 = b9e3f083，在分支 `execute-plan/7bea48a7-pr-197-audio-tracks`，尚未合 main），子修订加字母（Q189g、Q189h）。看 `git log` 接着往下编。
10. 批量加文本的标题定位（Q190，`cutora/batch/title_align.py`）：按词对齐，数字 / 序数 / 缩写 / 标点归一，
    对不上必须写明原因（不符 / 超出识别范围 / 没听到说话 / 模型失败）。别退回「整串原样查找」。

## 9. 测试

- **永远用仓库自己的 Python**：`runtime\venv\Scripts\python.exe -m pytest tests\test_xxx.py -q`。
  **不要在仓库根直接 `pytest`**（会收进 runtime 里 CPython 自带的测试）。
- 全量：`tools\pytest_each.py`（每个文件一个进程，带超时，可续跑），日志进 `docs\plan\queue\_log\`。
- **出包门禁 0 = `tools\gate0.py`**（Q262）。按内容记账：绿过且内容没变的文件不重跑，
  改了哪个文件 / 产品代码，只重跑受影响的。红了自动单独重跑一次，「重跑后过」在 `_gate0.log` 单列。
  不要再拿一口气 `pytest tests` 当门禁，也不要为了它在套件负载下调超时。
  `--status` 只看要跑哪些。`pack_internal.py` 的门禁 0 调的就是它。
- `RETEST_ONE.bat`：重跑最近改动相关的测试文件。**每次新增测试文件，把它加到第 16 行列表最前面。**
  Luca 双击它，你去读 `docs\plan\queue\_log\_retest.log`。
- 不在 Windows 上跑（例如云端 Linux）时，这些失败与改动无关：需要 `runtime\venv\Scripts\python.exe` 的测试、
  golden 媒体缺失、UI 静帧（字体 / DPI 不同）、个别计时类测试、Qt 退出时的 "Aborted"。
  判断方法：把你的改动退掉再跑一次，失败还在就是环境问题。
- 每个任务结束跑 `pip_audit` 和 `tools\security_scan.ps1`（细则见通用规范 §2.7）。

## 10. 提交

- 直接在 `main` 上提交（近期惯例），一个主题一个提交。说明行：`Q189h: <英文一句话>`，
  正文写为什么改、改了什么、Luca 实测数字（有的话）。作者用你自己的名字（如 `Claude`、`Grok`）：
  `git -c user.name="<名字>" -c user.email="<邮箱>" commit`。
- 只 `git add` 你改的文件，**不要 `git add -A`**（根目录有不入库的 `说明文档/`、`build/` 等）。
- 不改写历史：不 `reset --hard`、不 `push --force`、不删标签。要撤销用 `git revert`。
- Luca 实机确认「这次没问题」后，在 `docs/plan/STABLE_POINTS.md` 加一行并打 `stable-<日期>-<主题>` 标签。
- 从外部（Linux 虚拟机 / 云端）操作这个 Windows 仓库时，git 常留下 `.git/index.lock`、`tmp_obj_*`，
  提交后清掉，否则下一条 git 命令会报 "File exists"。

## 11. 和 Luca 打交道

- 中文、直接、短。先说结论和数字，再说做了什么；不要把每一步过程复述一遍。
- 需要他验证时，说清楚**做什么动作、做多久**（例：「重启软件，打开工程，空放 10 分钟再关」），
  然后自己读日志给结论。
- 遇到红线（golden 要变、要改不变量、要换模型/依赖、要动像素）：停下，说明为什么、前后对比、
  给他选项，等他拍板。
- 别说「应该没问题」——跑过测试 / golden、读过日志才算数。
- 他报的现象（帧率掉、风扇停、窗口没出来）都是真实信号，去日志里找对应时间点。

## 12. 已知遗留 / 下一步候选（需要 Luca 决定的标 ★）

- ★ GPU 合成（导出阶段二）/ 导出代理文件：会改像素，要 golden 先加后退役。
- ★ ASR / AI 抠像 / 跟踪搬出主界面进程（进一步降主界面内存峰值）。
- 字体按需注册（Qt 字体数据约 136 MB）。
- 打开工程时偶发 `sync_ui` / `_get_video_time_offset_ms` IndexError（老问题，日志里有）。
- 更多以 `docs/plan/BACKLOG.md` 为准。

## 13. 开工清单（每次都过一遍）

- [ ] `git status` 干净？不干净 → 问 Luca，不覆盖别人的改动
- [ ] `git log --oneline -10` 看最近在干什么；`cutora.log` 最后一个 `STARTUP` 是哪个提交
- [ ] 读完 §3 红线、`PROTECTED_INVARIANTS.md`
- [ ] 改动范围会不会碰 golden / 不变量？会 → 先问
- [ ] 改完：相关测试 + 新测试 + （必要时）golden / UI golden 全过
- [ ] 新测试加进 `RETEST_ONE.bat`
- [ ] 只 add 自己的文件，提交说明写清楚
- [ ] 告诉 Luca：改了什么、怎么验证、你会看日志里的哪一行
