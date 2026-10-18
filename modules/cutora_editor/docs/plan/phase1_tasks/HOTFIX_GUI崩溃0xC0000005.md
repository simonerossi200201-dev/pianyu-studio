# 热修 — GUI 自动字幕崩溃 0xC0000005（先于 T02 做）

> 单独一条热修，不要塞进 T02。工作目录 `D:\0_Dev\CUTORA`，从 main（`T09-done`）开 `fix/asr-gui-cuda-init`，修完合并 main 打 `hotfix-cuda-init`，然后 T02 从新的 main 开分支。

## 一、根因（已定位，不用查）

退出码 `-1073741819` = `0xC0000005` 访问违例，崩在 speechbrain / pyannote VAD 加载那一步。

**`tests/test_asr_smoke.py` 第 30–35 行**：
```python
# CUDA context must exist before QApplication on this GPU/driver combo,
# otherwise whisperx.load_model access-violates. config.detect_gpu_config
# already imports torch; force a tiny tensor so the context is live.
import torch
if torch.cuda.is_available():
    torch.zeros(1, device="cuda")
from PyQt6.QtWidgets import QApplication
```

**`cutora/app.py::main()`**：
```python
179:    from cutora.asr.engine import torch          # _LazyModule
180:    if os.environ.get("AUTOSUB_RENDER_CHILD") != "1":
181:        torch.set_num_threads(4)                 # ← 只设线程数，不建 CUDA 上下文
187:    app = QApplication(sys.argv)
```

也就是说：**这个 bug 你在上一轮已经发现了，但只把绕过写进了测试，没有写进产品。** 测试因此永远绿，GUI 因此必崩。三个环境变量（`TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD` / `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE`）app.main 是有的，唯独缺 CUDA 上下文预热。

## 二、修法

### 第 1 步 · 新建 `cutora/core/runtime.py`
```python
def init_torch_runtime(render_child: bool = False) -> None:
    """torch / CUDA 的启动期初始化。app.main 与测试必须调同一个函数。"""
```
里面按现在 `app.main` 的顺序做：三个环境变量 → `torch.set_num_threads(4)` → **CUDA 上下文预热**（`torch.cuda.is_available()` 为真时 `torch.zeros(1, device="cuda")`，整段 `try/except` 包住，失败只打日志不抛）。

**`render_child=True` 时必须直接返回，一行 torch 都不许碰**——渲染子进程不建 CUDA 上下文是 T01 反复验证过的红线（6 个子进程各吃 1 GB 显存会把 16 G 卡挤爆）。

### 第 2 步 · 两边都改成调它
- `cutora/app.py::main()`：把 132–134 行的环境变量与 179–181 行替换为 `init_torch_runtime(render_child=os.environ.get("AUTOSUB_RENDER_CHILD") == "1")`，**位置在 `QApplication(sys.argv)` 之前**。
- `tests/test_asr_smoke.py`：删掉自己那段 CUDA 预热与环境变量，改成调 `init_torch_runtime()`。

**这才是重点**：测试不许再有产品没有的绕过。以后任何测试需要的环境准备，都必须是产品自己的函数，测试只能调用它。测试里手写一段"让它过"的补丁，等于把测试变成假证。

### 第 3 步 · 验证（缺一不可）
1. **真桌面**双击 `CUTORA.bat`，跑一次自动字幕到 100%，字幕出现。这条不做，热修不算完。
2. `pytest`（含 `test_asr_smoke`）全绿。
3. `golden --all --repeat 3` PASS（`--render-job` 子进程仍不加载 torch）。
4. **确认子进程没被牵连**：跑 g06 时 `nvidia-smi` 看不到子进程显存，子进程 import 仍 < 0.5 s。把这两个数贴进报告。

提交信息：`fix: init CUDA context before QApplication; share runtime init between app and tests`

## 三、顺带查一件事（不阻塞热修）
日志里有 `[mov,mp4,m4a...] moov atom not found`——`D:/1_PROJECT/2026-09-07/A/T0007-文雯v01/clips/` 下有文件是坏的/没写完。`media_guard.preflight()` 本该拦住它。查一下这条路径上的预检是不是在 GUI 自动字幕入口被绕过了，结论写进报告的建议栏，**本次不要改**。

## 四、报告
写 `docs/plan/reports/HOTFIX_cuda_init_report.md`，含：根因、diff、四条验证结果、以及一句话——为什么测试没能拦住它，以及新规矩（测试不得包含产品没有的环境绕过）。这条规矩要同时加进 `docs/plan/01_GROK_通用规范.md` §3。
