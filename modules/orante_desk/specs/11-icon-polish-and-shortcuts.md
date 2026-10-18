# 规格 11 · 图标收尾：小尺寸放大、任务栏图标、桌面 + 开始菜单快捷方式

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/reports/10-app-icon.md`。只改本规格点名的文件。写完自己跑一遍验收；报告写到 `specs/reports/11-icon-polish-and-shortcuts.md`。中文注释。不要问问题。

用户看过 `preview.png`：主图标满意。三个问题要收：

## 1. 小尺寸太小（`ui/assets/icon-small.svg`、`tools/build_icons.py`）
16/24/32 里深色底板占了画面，橙子只剩一个小点。改成：**≤32 不要底板，橙子直径撑满 96% 画布**（透明背景）；48 和 64 保留底板但橙子放大到 84%。重新生成全部 PNG / ico / icns / preview / check-16x8。

## 2. 任务栏 / 标题栏还是 Python 图标（`orante/main.py`）
现在 `_bind_win_icon` 只在 `shown` 后找句柄贴图，用户实测标题栏仍是 Python 图标。改成三管齐下（都只在 Windows）：
1. **进程启动最早**（`main()` 开头、创建窗口之前）调 `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Orante.Studio")`——任务栏按这个分组、并认快捷方式的图标。
2. 窗口出现后：`LoadImageW(None, ico_path, IMAGE_ICON=1, 0, 0, LR_LOADFROMFILE=0x10 | LR_DEFAULTSIZE=0x40)` 分别加载大小两份（cx/cy 用 `GetSystemMetrics(SM_CXICON=11)` 和 `SM_CXSMICON=49`），`SendMessageW(hwnd, WM_SETICON=0x80, ICON_BIG=1, hIcon)` 和 `ICON_SMALL=0`。句柄查找：`EnumWindows` 找当前进程 id（`GetWindowThreadProcessId`）且可见、标题含 "Orante" 的顶层窗口——不要只靠 `FindWindowW` 标题精确匹配。
3. 失败静默跳过，写一行 `print` 到控制台即可。
另外 `Orante2.bat` 最后一行改成用 `pythonw.exe` 启动（没有控制台黑窗；`--browser` 模式除外仍用 python.exe）；日志 `print` 改为同时写 `data/logs/orante.log`（`main.py` 里 `sys.stdout` 为 None 时不要崩）。

## 3. 快捷方式（`tools/Make-Shortcut.bat` 改为 `tools/Install-Shortcuts.bat`，纯 ASCII）
一次做三件事，用 PowerShell 一段脚本（bat 里 `powershell -NoProfile -ExecutionPolicy Bypass -Command "..."`，路径全部由 `%~dp0` 推出，不写死盘符）：
- 桌面 `Orante.lnk` → 目标 `Orante2.bat`，工作目录 = Orante 根，图标 `ui\assets\orante.ico`，窗口样式最小化（`WindowStyle = 7`），描述「Orante 制片台」。
- 开始菜单 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Orante\Orante.lnk` 同上。
- 给两个 lnk 写 `System.AppUserModel.ID = Orante.Studio`（PowerShell 里用 `Shell.Application` 的 `ExtendedProperty` 写不了，改用 .NET：加载 `Windows Script Host` 不行就用 `[Runtime.InteropServices.Marshal]`+`IPropertyStore`——如果 30 分钟内做不稳，就退回不写这个属性，并在报告里说明；任务栏分组主要靠第 2 点的 `SetCurrentProcessExplicitAppUserModelID`）。
- 打完印一行 "Shortcuts installed." 并 `timeout /t 5`。
同时在设置抽屉的「界面」组下面加一颗按钮「安装桌面 / 开始菜单快捷方式」→ `POST /api/system/install-shortcuts`（server.py 只加这一条路由，调 `subprocess` 跑那个 bat）。`ui/js/parts/settings.js`、`orante/i18n/zh.json` 允许改。

## 验收
- `python tools/build_icons.py` 重新产出；`check-16x8.png` 里橙子占满。
- 起 `Orante2.bat`（窗口模式）：标题栏、任务栏都是橙子图标；任务栏不再显示 Python 分组。
- 双击 `Install-Shortcuts.bat` 后桌面与开始菜单各出现 `Orante`，图标正确，双击能启动。

## 4. 【最重要，用户实测反馈】任务栏还是 Python 图标 + 一个黑框 —— 做一个真正的 `Orante.exe`
只靠 WM_SETICON 治标不治本：任务栏按钮的图标和分组认的是**进程的 exe**（python.exe / cmd.exe）。正解是给软件一个自己的可执行文件：
- `tools/build_launcher.py`：用 Windows 自带的 C# 编译器 `%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`（Win10/11 都有，不用装任何东西；找不到就试 `Framework\`）编译 `tools/launcher.cs` → 根目录 `Orante.exe`，参数 `/target:winexe /win32icon:ui\assets\orante.ico /optimize+`。
- `tools/launcher.cs`（C#，无第三方依赖）：① `SetCurrentProcessExplicitAppUserModelID("Orante.Studio")`；② 以自身所在目录为根；③ 若 `.venv\Scripts\pythonw.exe` 不存在或 `import fastapi` 失败 → 用 `cmd /c Orante2.bat --install-only` 显示安装过程（`Orante2.bat` 加一个 `--install-only` 分支：装完依赖就退出，不启动）；④ 否则 `Process.Start(pythonw.exe, "-m orante")`，`UseShellExecute=false`，`CreateNoWindow=true`，工作目录=根，并把自己的 exe 路径通过环境变量 `ORANTE_LAUNCHER` 传给 python；⑤ 启动后自己退出（不常驻）。
- `orante/main.py`：Windows 下 `main()` 一开始就 `SetCurrentProcessExplicitAppUserModelID("Orante.Studio")`（和 exe 同一个 ID，任务栏才会把 pywebview 窗口归到 Orante.exe 的按钮下，显示橙子图标而不是 Python）。窗口图标继续用 WM_SETICON 双保险。
- `Install-Shortcuts.bat` 的目标改成 `Orante.exe`（存在时），否则退回 `Orante2.bat`。
- `Orante2.bat` 保留给命令行/排错用；默认入口变成 `Orante.exe`。`README` 不用写，报告里说明。
- 分发时 `Orante.exe` 随目录一起拷；如果目标机器没有 csc（极少），`Install-Shortcuts.bat` 仍能用 bat 目标工作。

验收补充：任务栏上只剩一个橙子图标的按钮，没有黑色 cmd 窗口按钮；桌面快捷方式指向 `Orante.exe`。
