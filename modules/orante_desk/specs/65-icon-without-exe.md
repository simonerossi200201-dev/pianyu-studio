# 65 · 要图标、要独立进程，但不要那个会被杀毒软件误杀的 exe

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/64-share-package.md`
> 和它的报告，再读 `orante/main.py`（`APP_USER_MODEL_ID`、`_set_app_user_model_id`、
> `_bind_win_icon`、`_apply_win_icons`、`webview.create_window` 那两处）、
> `tools/Install-Shortcuts.bat`、`tools/launcher.cs`、`tools/build_launcher.py`、
> `tools/make_share.py`（`ROOT_KEEP` / `TOOLS_KEEP`）、`Orante2.bat`、
> `说明文档/使用说明-分发版.md`，再读本规格。
> 改前备份 `*.bak-0914icon`。报告写到 `specs/reports/65-icon-without-exe.md`。中文注释。
> **不许为了图标把任何编译产物放回分发包。** 这一条没得商量，64 号已经付过代价了。

## 背景：exe 其实什么都没多干

`Orante.exe`（csc 现编、无签名、95 KB）发出去一解压就被杀软删掉。64 号把它从白名单里拿掉了，
但用户问得对：图标和独立进程不该因此丢掉。

查过 `main.py` 之后，结论是**那个 exe 本来就是多余的**：

| exe 号称干的事 | 其实谁在干 |
|---|---|
| 登记 AppUserModelID（任务栏分组/图标） | `main.py:338 _set_app_user_model_id()`，ctypes 调 `shell32`，**Python 自己早就在做** |
| 给窗口贴 orante.ico | `main.py:744 _bind_win_icon()` + `_apply_win_icons()`，`LoadImageW` + `WM_SETICON` |
| 没有控制台黑窗 | `pythonw.exe` 本身就没有控制台 |
| 缺依赖时拉起安装 | `Orante2.bat --install-only` |

所以 exe 唯一真正提供的，是**资源管理器里一个带图标、能双击的文件**。
这个用一个**在对方机器上现生成的 `.lnk`** 就能替代 —— 快捷方式不进分发包，
没有文件可供杀软扫描，图标、独立进程、无黑窗一样不少。

## 要做的

### A. 重写 `tools/Install-Shortcuts.bat` → `tools/创建快捷方式.bat`

**旧文件名保留一份转发**（`Install-Shortcuts.bat` 里 `call` 新的那个就行），免得旧习惯失效。

现在这份 bat 自己就是杀软的靶子，它干了三件敏感的事，**逐条去掉**：

1. `powershell -ExecutionPolicy Bypass` —— **去掉 `-ExecutionPolicy Bypass`**。
   执行策略只管**脚本文件**，`-Command` 传进去的内联代码根本不受它管，这个开关从头到尾就是多余的，
   却是国内外杀软最敏感的字样之一。
2. `Add-Type -TypeDefinition` 运行时编译一段 C# —— **整段删掉**。
   它存在的唯一目的是给 `.lnk` 盖 AppUserModelID。不需要了，理由见 B。
3. P/Invoke `shell32.dll` 的 `SHGetPropertyStoreFromParsingName` —— 跟 2 一起删。

剩下的只有创建快捷方式这一件事，用 `WScript.Shell` COM，干干净净：

- **目标指向 `.venv\Scripts\pythonw.exe`，参数 `-m orante`**，不是 bat、不是 exe。
  这样双击起来**完全没有控制台黑窗**，任务管理器里就是一个独立的 pythonw 进程。
- `WorkingDirectory` = 软件根目录。
- `IconLocation` = `ui\assets\orante.ico`。
- `Description` = `Orante 制片台`。
- 桌面一个、开始菜单 `Orante\` 下一个。
- **前置检查**：`.venv\Scripts\pythonw.exe` 不存在 → 打印
  `还没装好。请先双击 Orante2.bat 跑一次，装完依赖再运行本脚本。` 然后 `exit /b 1`，不要生成半残的快捷方式。
- 文件保持 **PURE ASCII**（bat 的老规矩），中文提示交给它调的 PowerShell 用 `[char]0x…` 或者
  干脆用英文 + 一行中文写在 `使用说明` 里。哪种都行，报告里说明选了哪种、为什么。

### B. 任务栏身份：不盖 .lnk，改走「从运行中的窗口固定」

`main.py` 启动时已经把进程登记成 `Orante.Studio`（`APP_USER_MODEL_ID`）。
所以窗口跑起来之后，**右键任务栏图标 →「固定到任务栏」**，Windows 会自己生成一个
身份正确的快捷方式，图标、分组全对。这条路不需要任何 P/Invoke。

- 确认 `_set_app_user_model_id()` 在 `webview.create_window` **之前**被调到（现在看是的，报告里贴调用顺序证明）。
- `_bind_win_icon` 找不到 `orante.ico` 时只打印一行就跳过 —— 保留这个宽容行为，但在
  **分发包里确认 `ui/assets/orante.ico` 确实带上了**（`make_share.py` 的 `ui/**` 应该覆盖到，
  报告里列出 zip 内 `ui/assets/` 的文件清单证明）。

### C. `make_share.py` 白名单

- `TOOLS_KEEP` 加回 `创建快捷方式.bat` 和 `Install-Shortcuts.bat`（转发那份）。
- `ROOT_KEEP` **不要**加回 `Orante.exe`。64 号那段注释保留。
- `tools/launcher.cs`、`tools/build_launcher.py` 留在源目录（你自己本机还能编来用），
  **不进分发包**。

### C2. 真正的「一键」：第一次装完就把快捷方式建好

用户问的是有没有一键工具。分两步（先 `Orante2.bat` 再 `创建快捷方式.bat`）的原因只有一个：
快捷方式要指向 `.venv\Scripts\pythonw.exe`，而那个文件第一次跑完 `Orante2.bat` 才存在。

所以把第二步塞进第一步的尾巴里：

- `Orante2.bat` 在 `:after_deps` 之后、真正启动之前，判断
  **「这次刚建过 .venv」或者「桌面上还没有 Orante.lnk」** → `call tools\创建快捷方式.bat --quiet`。
- `--quiet` 模式：不 `pause`、不 `timeout`、只打一行 `Desktop shortcut created.`，
  失败也只打一行警告然后**继续启动**（建快捷方式失败绝不能挡着软件起不来）。
- 只在第一次做。已经有桌面快捷方式就跳过，别每次启动都重建。
- `--install-only`（`Orante.exe` 那条老路）下也照做。
- `Orante2.bat` 仍然必须 **PURE ASCII**。

这样对方的实际操作就是：解压 → 双击 `Orante2.bat` → 走向导 → **桌面上已经有图标了**，
以后都从图标进。手动的 `创建快捷方式.bat` 保留，给「不小心删了快捷方式」和「想重建」用。

**退路**：万一哪个杀软对 `Orante2.bat` 里新增的这次 PowerShell 调用有意见，
把这一段去掉就退回两步流程，功能不受影响。报告里写明这个开关在哪一行。

### D. 说明书

`说明文档/使用说明-分发版.md`：

- 第 2 节「第一次启动」末尾加一段：
  「第一次跑完，桌面和开始菜单上就会出现带图标的 **Orante**，以后从那里启动，
  没有黑窗口。想钉到任务栏：先正常启动，右键任务栏上的 Orante 图标 → 固定到任务栏。
  快捷方式不小心删了，双击 `tools\创建快捷方式.bat` 可以重建。」
- 第 9 节（杀毒软件那节）加一句：本包不含任何 `.exe`；快捷方式是在你自己机器上生成的，不是传过来的。

### E. 如果将来真的还想要 exe（只写进报告，**本规格不实现**）

报告末尾用三五行说清代价，供以后决策：

- **代码签名证书**：OV 约每年两三百欧，签完仍要攒 SmartScreen 信誉（几周）；EV 证书贵不少，
  但 SmartScreen 信誉是即时的。
- **向微软提交误报**：`microsoft.com/wdsi` 按哈希白名单，正式版**编一次、交一次**可行；
  但无签名时每次重编哈希都变、信誉归零，开发期用不了。
- **PyInstaller / Nuitka 打包成 exe 只会更糟** —— 那类产物被误杀的比例比 csc 出的还高。

### F. 测试 `tools/test_spec65.py`（**先设 ORANTE_DATA**）

- grep 断言：`创建快捷方式.bat` 里**不含** `ExecutionPolicy`、`Add-Type`、`P/Invoke`、
  `SHGetPropertyStoreFromParsingName`、`DllImport`。
- grep 断言：脚本里 `TargetPath` 指的是 `pythonw.exe`，`Arguments` 含 `-m orante`，
  `IconLocation` 指向 `orante.ico`。
- `.venv` 不存在时脚本 `exit /b 1`（用一个假根目录跑一次，断言退出码和提示文案）。
- 白名单：出包目录里**有** `tools/创建快捷方式.bat` 和 `ui/assets/orante.ico`，
  **没有** `Orante.exe`、`tools/launcher.cs`、`tools/build_launcher.py`。
- `main.py` 里 `_set_app_user_model_id` 的调用位置在 `create_window` 之前（AST 或行号断言）。
- grep 断言：`Orante2.bat` 里有 `创建快捷方式.bat` 的调用，且该调用带 `--quiet`；
  文件仍是 PURE ASCII（逐字节断言 < 0x80）。
- `--quiet` 模式下脚本不含 `pause`、不含 `timeout`（grep 断言）。
- 回归：`python -m pytest tools/` 全过。

## 验收

1. 在一个装好的 Orante 目录里双击 `tools\创建快捷方式.bat` → 桌面出现带 Orante 图标的快捷方式，
   双击它：**没有黑窗口闪过**，窗口起来，标题栏和任务栏都是 Orante 图标，不是 Python 的。
1b. 把 `.venv` 和桌面快捷方式都删掉，只双击 `Orante2.bat` 一次 → 装完依赖、软件起来，
   **桌面上自动出现了快捷方式**，全程没有第二次双击。再双击 `Orante2.bat` 一次 → 不重复建。
2. 右键任务栏图标 →「固定到任务栏」→ 关掉软件 → 从任务栏那个图标再启动，图标正确、不重复出现两个条目。
3. 删掉 `.venv` 再跑一次这个 bat → 提示先跑 `Orante2.bat`，**没有**生成快捷方式。
4. 重新出包（`tools\15-Make-Share-Package.bat`）：体检过、冒烟过，zip 里
   **没有任何 `.exe`**（脚本自己断言一遍：遍历 zip，扩展名为 `.exe`/`.dll`/`.pyd` 的条目数为 0）。
5. 解压到干净目录，走一遍：`Orante2.bat` → 向导 → `创建快捷方式.bat` → 从快捷方式启动，全程没有杀软报警。
