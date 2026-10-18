# 规格 90 · 不给杀毒软件送靶子

日期：2026-09-24。用户反馈：分发给同事时大量报毒，`Orante.exe` 首当其冲，
`.bat` 启动脚本和 `.venv` / playwright 里的文件也被拦。

## A. 项目里不再有任何编译产物（已删）

删掉：`Orante.exe`、`tools/launcher.cs`、`tools/build_launcher.py`。

65 号已经查清楚：那个 exe 干的四件事（AppUserModelID、窗口图标、无黑窗、拉起安装）
Python 自己全都在做，它唯一真正提供的是「资源管理器里一个能双击的带图标的文件」——
而这个用 `.lnk` 就有了。64 号当时只是不把它放进分发包，源目录还留着，用户把整个开发目录
发给别人时照样中招。现在从项目里彻底删掉。

守卫：`tools/test_spec90.py::test_no_compiled_artifacts_in_repo` 扫全仓（跳过 `.venv`、
`_share`、`data` 等），出现 `.exe / .dll / .pyd / .cs` 就红。

## B. 建快捷方式不再用 powershell

原来 `tools/创建快捷方式.bat` 里是一长串 `powershell -NoProfile -Command "... New-Object
-ComObject WScript.Shell ..."`。65 号已经去掉了 `-ExecutionPolicy Bypass`，但**一长串内联
powershell 命令本身**就是杀软最敏感的画像之一。

现在：`orante/shortcut.py` 用 ctypes 直接调 Windows 自己的 `IShellLinkW` + `IPersistFile`
建 `.lnk`，不需要 pywin32、不需要任何编译产物。bat 只剩「把参数转给 Python」一行。

快捷方式本身没变味：目标仍是 `.venv\Scripts\pythonw.exe -m orante`（不指向 bat、更不指向 exe），
工作目录是软件根，桌面和开始菜单各两个（普通 / 新窗口）。**日常启动完全不碰脚本文件。**

## C. 桌面图标用 `Orante me.png` 转的 .ico

`tools/build_me_icon.py`：裁掉透明边 → 居中放进正方形（留 4% 边距）→ 一次写进
16/24/32/48/64/128/256 七个尺寸 → `ui/assets/orante-me.ico`。
快捷方式的 `IconLocation` 优先用它，没生成过就退回原来的 `orante.ico`。

`.ico` 是图片，杀软不会因为它报毒；真正招误报的那个 exe 已经没了。

## D. 还会被误报的两样，以及为什么

这两样是 pip 装出来的，改不掉，只能解释清楚 + 加白名单：

| 文件 | 为什么在 | 为什么被盯上 |
|---|---|---|
| `.venv\Scripts\python.exe` / `pythonw.exe` | 建虚拟环境时 Windows 会**复制**一份解释器 | 用户目录下多了个没信誉记录的 PE 文件 |
| `.venv\Lib\site-packages\playwright\driver\node.exe` | playwright 自带的 node 驱动 | 同上，而且它会开子进程、连本地端口 |

要跟对方讲清楚的一句话：**Orante 连的是你自己电脑上的 Chrome（CDP 调试端口），
从不下载任何浏览器**——代码里没有一处 `playwright install`，也没有 `chromium.launch()`，
只有 `connect_over_cdp`。

对策写进《使用说明-分发版》：把 Orante 文件夹整个加进杀毒软件的信任 / 排除列表，
再双击安装。

## 验收

`tools/test_spec90.py`：全仓没有编译产物、三个被删的文件确实不在、
启动脚本里不出现 powershell / certutil / mshta 等字样且是纯 ASCII、
分发白名单带上 `Orante me.png` 和 `build_me_icon.py` 且不含任何 exe、
`build_me_icon` 七个尺寸都在且裁边居中、快捷方式表指向 pythonw 与新图标（含回退）。
`tools/test_spec65.py` 里两条对着 bat 文本的断言改到 Python 那一侧，其余照旧。
