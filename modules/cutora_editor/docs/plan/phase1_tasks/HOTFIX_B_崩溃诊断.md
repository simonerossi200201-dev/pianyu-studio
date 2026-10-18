# 热修 B — 0xC0000005 仍在，改为先取证再修

> 分支 `fix/asr-gui-cuda-init` 上的 CUDA 预热是对的（保留），但不够：Luca 在该分支上真桌面复现，仍在同一位置崩（speechbrain 钩子注册之后、模型真正可用之前）。
> **不要再猜第二个原因然后改代码。** 这一轮的产出是证据，不是修复。取证结束后由 Claude 决定修法。

## 一、已知事实
- 无头 `test_asr_smoke`（`QT_QPA_PLATFORM=offscreen`，`AlignmentWorker.run()` 同步、主线程）→ 通过。
- GUI（真 windows 平台插件，`worker_ai.start()` 在 **QThread 里**跑，同时 `QMediaPlayer` 预览在跑、`_preview_audio` 抽音频刚完成）→ 崩。
- v6 的 GUI 同样用 `_LazyModule` 惰性加载 torch / whisperx，所以"导入时机"不是与 v6 的差异。
- 崩溃发生在 GUI 主线程之外的地方，pytest 覆盖不到。

GUI 与测试的**三个差异**就是嫌疑范围：① 工作线程而非主线程；② 真平台插件 + QtMultimedia 在同一进程里占着 GPU/解码器；③ T09 升级的 transformers 5.10.4 / pillow / lightning 在 GUI 路径上被以不同顺序加载。

## 二、Grok 要做的（全部无头可做）

### 1. 加崩溃取证，永久保留（这是 T06 本来要做的，提前）
`cutora/app.py::main()` 最前面：
```python
import faulthandler
_fh = open(os.path.join(USER_DATA, "logs", "faulthandler.log"), "w", encoding="utf-8")
faulthandler.enable(file=_fh, all_threads=True)
```
`faulthandler` 在 Windows 上会接管访问违例，把**所有线程的 Python 栈**写进文件。Luca 复现一次，我们就知道崩在哪一行、哪个线程。
同时在启动日志第一行打印：git 短 hash、分支、`torch.__version__`、`ctranslate2.__version__`、`transformers.__version__`、CUDA 预热是否成功及设备名。以后每份崩溃日志都自带版本。

### 2. 用无头方式逼近 GUI 的三个差异，看哪个能复现
写 `tests/diag_asr_gui_delta.py`（诊断脚本，不进 pytest），每个实验独立进程运行、记录退出码：
- **D1 线程**：`QApplication` + `AlignmentWorker.start()`（QThread）+ 事件循环等 `finished`，而不是 `.run()`。
- **D2 多媒体**：先建 `QMediaPlayer` + `QAudioOutput` 播放同一个视频 2 秒，再跑 D1。
- **D3 平台插件**：`QT_QPA_PLATFORM=windows`（如果你的会话有桌面就能跑；没有就标"无法执行"）。
- **D4 长文件**：用 Luca 那个 3:45 的文件（`D:\1_PROJECT\2026-09-07\A\T0007-文雯v01\clips\_stitched\T007.mp4`，只读，不要改它）替代 4 秒样本跑 D1。
- **D5 依赖回退**：在一个临时 venv（`runtime\venv_diag`，不动正式 venv）里把 transformers 回到 5.3.0、其余不动，重跑 D1/D2。
哪个实验退出码是 `-1073741819`，就把它的 `faulthandler.log` 原样贴进报告。

### 3. 检查原生 DLL 冲突（只查不改）
在正式 venv 里列出进程加载的 FFmpeg / cuDNN / cuBLAS DLL 来源：`python -c "import torch, ctranslate2, av; ..."` 后用 `psutil.Process().memory_maps()` 或 `tasklist /m`，把 `avcodec*`、`cudnn*`、`cublas*`、`nvrtc*` 的路径列出来。重点看是否有**同名 DLL 来自两个目录**（PyQt6 的 QtMultimedia 与 PyAV/torchaudio 各带一套 FFmpeg；torch 与 ctranslate2 各带 cuDNN）。

### 4. 报告
`docs/plan/reports/HOTFIX_B_diag_report.md`：D1–D5 结果表、DLL 清单、faulthandler 日志、以及你对根因的一句话判断（可以是"不确定"）。**不改产品代码**，除了 §1 的取证。

## 三、Luca 要做的（两件，各 3 分钟，比 Grok 的实验都更决定性）

### A. 旧版今天还能跑吗？
双击 `D:\0_Dev\AutoSub_v6\2_Launcher.bat`，对同一个 T007 跑自动字幕。
- **能跑通** → 机器和驱动没问题，原因在 CUTORA 自己（代码或 venv）。
- **也崩** → 是系统层变了（Windows 更新 / NVIDIA 驱动更新），和改版无关，修法完全不同。

### B. 事件查看器里的"故障模块"
`Win + R` → `eventvwr` → Windows 日志 → 应用程序 → 找最近一条 **错误**，来源 "Application Error"，故障应用程序 `python.exe`。把里面的 **"故障模块名称"**（例如 `ctranslate2.dll` / `cudnn64_9.dll` / `torch_cuda.dll` / `Qt6Multimedia.dll` / `avcodec-61.dll`）那一整段文字复制给 Claude。这一行字直接指出是谁崩的。

### C. Grok 加完 §二.1 之后
再复现一次崩溃，把 `%用户数据%\logs\faulthandler.log` 发给 Claude。
