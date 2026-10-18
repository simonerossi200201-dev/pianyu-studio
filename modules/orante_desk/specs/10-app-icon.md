# 规格 10 · Orante 应用图标（设计 + 生成全套图标文件）

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先读 `specs/00-architecture.md` 第 6 节（配色）。只改本规格点名的文件。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的」写到 `specs/reports/10-app-icon.md`。中文注释。不要问问题。

## 设计意图

「Orante」念起来像意大利语的 **arancia（橙子）**，用户希望图标以**橙子**为核心意象，同时表达这个软件的本质——**批量制片台 / 节点工作流**。要求：专业、耐看、有表现力，不要卡通贴纸感，不要 emoji 风。

设计方向（按这个做，细节你定）：
- **主体**：一枚橙子的横切面（圆形），8 瓣果肉。**果瓣不是普通果瓣，而是电影胶片/帧的形态**——每一瓣是一段带圆角的扇形"帧"，瓣与瓣之间留细细的暗缝，像胶片齿孔/时间线上的段落；正中果心是一个小圆点（像节点）。这样既是橙子，也是"一条环形的时间线/节点工作流"。
- **配色**：沿用软件主题——底色深炭 `#121110` → `#1c1915` 的极浅径向渐变（圆角方形底板，圆角半径 22%）；橙子皮 `#E8823A`→`#F0A24A` 渐变；果肉用主题金 `#D9A34F` 到 `#F3C878` 的渐变，其中**一瓣**用 Flow 的青 `#6BB2AF`、**一瓣**用更深的橙 `#D9705F`，暗示两种引擎/多账号；果肉高光极轻，不要塑料反光。
- **构图**：橙子占底板的 72%，略微向右上偏 3%；左下角有一段极细的弧形高光；不要文字（小尺寸看不清）。
- 16px 下要还能认出"一个橙色圆盘"；32px 下要能看出分瓣。为此提供 **小尺寸专用简化版**（去掉细缝和高光，瓣数减到 6）用于 16/24/32。

## 实现（全部代码化，不用任何设计软件）

1. `ui/assets/icon.svg`（主图标，1024×1024 视口，纯手写 SVG，渐变用 `<linearGradient>/<radialGradient>`，瓣用 `<path>` 扇形 + `stroke` 暗缝）；`ui/assets/icon-small.svg`（简化版）。
2. `tools/build_icons.py`：用 **Playwright 的 Chromium**（`..\下载图片\.venv` 里有 playwright；分发版走 Orante 自己的 .venv）把 SVG 渲染成 PNG：1024 / 512 / 256 / 128 / 64 / 48 / 32 / 24 / 16（≤32 用 icon-small.svg），输出到 `ui/assets/png/icon-<size>.png`；再用 Pillow 把 256/128/64/48/32/16 合成 `ui/assets/orante.ico`；Mac 的 `.icns` 用纯 Python 写（icns 容器格式很简单：`icns` 头 + 每个尺寸一个 `ic07/ic08/ic09/ic10/ic11/ic12/ic13/ic14` 块，块内容就是 PNG 字节——不要依赖 iconutil），输出 `ui/assets/orante.icns`。
3. 接进软件：
   - `ui/index.html` `<head>` 里加 `<link rel="icon" href="/ui/assets/png/icon-64.png">`，顶栏 `.brand` 左侧放 22px 的 `icon-64.png`。
   - `orante/main.py` 的 pywebview 窗口：`webview.create_window(..., )` 之后 Windows 下用 `ctypes` 给窗口设 `orante.ico`（找不到窗口句柄就跳过，不报错）；这是允许改 main.py 的唯一一处。
   - 写一个 `tools/Make-Shortcut.bat`（纯 ASCII）：在桌面建一个指向 `Orante2.bat` 的快捷方式并把图标设为 `ui\assets\orante.ico`（用 PowerShell 一行 `WScript.Shell` 完成）。
4. 顺手出一张 `ui/assets/preview.png`：把 1024/256/64/32/16 五个尺寸并排贴在深色底上（Pillow），方便一眼审。

## 验收
- `python tools/build_icons.py` 无报错，产出上面所有文件；`preview.png` 用 Pillow 打开尺寸正确。
- `.ico` 用 Pillow 重新打开能读到 6 个尺寸；`.icns` 头四字节是 `icns` 且总长度字段正确。
- 16px 图看起来仍是橙色圆盘（把 icon-16.png 放大 8 倍存成 `ui/assets/png/check-16x8.png` 供人眼审）。
