# 热修 C — 根因钉死：PyQt6 自带的老 MSVCP140.dll 先进了内存

> 替代热修 B 的取证实验（D1–D5 不用做了）。在 `fix/asr-gui-cuda-init` 分支上继续。
> 证据来自 Windows 事件查看器（Luca 提供）：
> ```
> 出错模块名称：MSVCP140.dll  版本 14.26.28720.3（2020 年，VS2019）
> Faulting 模块路径：D:\0_Dev\CUTORA\runtime\venv\lib\site-packages\PyQt6\Qt6\bin\MSVCP140.dll
> ```
> 而 AutoSub_v6 在同一台机器、同一天跑同一个文件，正常且流畅。

## 一、机制（已核实，不用再验证根因，只验证修复）

1. `PyQt6-Qt6` 的 wheel 在 `PyQt6\Qt6\bin\` 里**自带一套 2020 年的 MSVC 运行库**：`msvcp140.dll / msvcp140_1.dll / msvcp140_2.dll / vcruntime140.dll / vcruntime140_1.dll / concrt140.dll`。v6 与 CUTORA 两个 venv 里都有，一模一样。
2. Windows 加载器按**文件名**认已加载的 DLL：谁先把 `MSVCP140.dll` 带进进程，后面所有人都绑到那一份上。
3. **v6 的顺序**：`AutoSub_debug.py` 第 313 行 `import cv2` → `cv2.pyd` 从 `C:\Windows\System32` 带进**新版**系统运行库 → 第 324 行 `from PyQt6...` 复用它 → 后面 torch / ctranslate2 / speechbrain 都绑到新库 → 正常。
4. **CUTORA 的顺序**：`cutora/app.py::main()` 第 147/150 行先 `from cutora.ui.panels.fonts / main_window import ...` → PyQt6 先加载 → 把 `Qt6\bin\` 里那份 **14.26 老库**带进进程 → 第 174 行才 `init_torch_runtime()` → torch 及其后的 ctranslate2 / speechbrain（都是 VS2022 编的，要求更新的运行库）绑到老库上 → 在 speechbrain 那步访问违例。
5. **为什么无头测试永远绿**：`test_asr_smoke` 先 `import torch` 再 `import PyQt6`——顺序恰好和 v6 一样"对"。所以它测的从来不是产品的真实启动顺序。CUDA 预热是对的但不是根因；拆包改变了 DLL 加载顺序才是。

## 二、修法

### 1. 把 torch 初始化提到**任何 PyQt6 import 之前**
`cutora/__main__.py` 的 `__main__` 段，非渲染子进程路径：
```python
_early_dispatch()
from cutora.core.runtime import init_torch_runtime      # 此时 sys.modules 里不得有任何 PyQt6.*
init_torch_runtime(render_child=False)
from cutora.app import main
main()
```
`app.py::main()` 里原来第 174–175 行那次调用保留（幂等），但已不是第一次。
渲染子进程（`--render-job`）**不调**——它不用 torch，用 Qt 自带的老库没关系（golden 一直是这么绿的）。

### 2. 让它不可能悄悄回退（这条比修复本身重要）
`cutora/core/runtime.py::init_torch_runtime()` 开头加：
```python
if any(m == "PyQt6" or m.startswith("PyQt6.") for m in sys.modules):
    raise RuntimeError("init_torch_runtime 必须在任何 PyQt6 import 之前调用（MSVCP140 加载顺序）")
```
结尾加 `loaded_msvc_runtime_path()`：用 `ctypes.windll.kernel32.GetModuleHandleW("msvcp140.dll")` + `GetModuleFileNameW` 取出实际加载的路径，写进启动日志。若路径落在 `site-packages\PyQt6\` 下：**测试里直接失败**；产品里打 ERROR 日志并弹一次警告框"运行库加载顺序错误，自动字幕将崩溃，请把日志发给开发者"——不要让它再静默地崩在 20%。
新增 `tests/test_runtime_dll_order.py`：用 `subprocess` 启动 `python -m cutora --check-runtime`（新增的自检参数：跑 `init_torch_runtime` + 导入 PyQt6 + 打印 MSVCP140 路径 + 退出），断言路径以 `C:\Windows\System32` 开头。**再加一个反向用例**：子进程先 `import PyQt6.QtCore` 再调 `init_torch_runtime()`，断言抛 `RuntimeError`。

### 3. 无头复现 + 验证（你自己就能做，不用等 Luca）
- 复现：`python -c "import PyQt6.QtCore; from cutora.asr.engine import get_whisperx_model; get_whisperx_model()"`——预期退出码 `-1073741819`。把结果贴报告。这一条证明"顺序反了就崩"，与 GUI 一致。
- 验证：同样命令但先 `from cutora.core.runtime import init_torch_runtime; init_torch_runtime()` 再 `import PyQt6.QtCore`——预期正常返回。
- `pytest`（含 `test_asr_smoke`、新的 `test_runtime_dll_order`）全绿。
- `golden --all --repeat 3` PASS；g06 子进程仍无显存、import < 0.5 s。
- 全仓库 grep：`cutora/batch/cli.py` 等其他入口（无头 CLI 跑 ASR 的路径）也要保证先 `init_torch_runtime()` 再碰 PyQt6，列出每个入口的调用位置。

### 4. 备选方案（只做实验、记录数据、不启用）
在一个临时复制的 venv 里删掉 `PyQt6\Qt6\bin\` 的六个运行库 DLL，用"先 PyQt6 后 torch"的错误顺序跑上面的复现命令，看是否也不崩。结果写进报告——这是 T10 打包时的备选保险（安装后清理 Qt 自带运行库 + 安装官方 `vc_redist.x64.exe`），由 Claude 决定要不要用。**正式 venv 不动。**

## 三、验收与合并
- Luca 真桌面跑 T007 自动字幕到 100%（这次应该过了）。
- 上面 §二.3 全绿。
- 报告 `docs/plan/reports/HOTFIX_C_report.md`：机制、diff、复现/验证输出、各入口调用位置表、备选方案数据。
- 通过后合并 main 打 `hotfix-dll-order`，把热修 B 的取证要求（faulthandler + 启动版本日志）也一并保留在这个分支里——它们本来就是 T06 要做的。
- 通用规范 §3 追加一条：**任何新入口（GUI / CLI / 测试 / 子进程）必须先 `init_torch_runtime()` 再 import PyQt6，渲染子进程除外。**
