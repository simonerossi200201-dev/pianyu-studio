# T05 内测冲刺：一键打包 · 字体承诺 · 预设编辑 · 批量署名

**发出** Claude · 2026-09-14
**硬期限** **明天（2026-09-15）中午** Luca 发完整内测版给别人测。
**当前状态** beta3 `7596cf6`：能启动、门禁 0–7 全绿，但**模型没打进去、venv 换台机器起不来**。

---

## 0. 我读了 beta3 包之后发现的两个"别人没法测"的硬伤

### 0a. 模型没进包
`runtime\` 只有 `ffmpeg` 和 `venv`。`BUILD.txt` 自己写了 `models: not included`。
没模型 = 冒烟第 1 步「自动字幕」做不了 = 别人拿到等于没拿到。

Luca 明确说：**体积不是问题，模型必须完整。**

### 0b. 【更严重】随包的 venv 换台机器起不来
`runtime\venv\pyvenv.cfg`：
```
home = C:\Program Files\Python310
```
Windows venv 的 `Scripts\python.exe` 是个小启动器，靠 `pyvenv.cfg` 的 `home`
去找 `python310.dll`。**测试者机器上没有 `C:\Program Files\Python310`，双击
`CUTORA.bat` 直接起不来。** 在 Luca 自己机器上能跑，纯属路径碰巧一样。

而 `CUTORA_Install.bat` 也硬编码同一个路径，同样救不了。

**这两条不修，内测版就只有 Luca 一个人能用。P0。**

---

## 1. P0-A · 一键打包脚本 `tools/pack_internal.py`（Luca 每次发内测点一下）

### 1a. 行为：一条命令 = 门禁 + 打包 + 校验

```
python tools/pack_internal.py --version 7.0.0-beta4 [--out D:\0_Dev\CUTORA-dist]
```

流程，**任何一步失败就中止，不出包**：

1. 跑 PACK_GATE **0–7**（pytest / 真启动+证据协议 / porcelain / PySide6 / golden+isolate / 许可 / tag）
2. 清空并重建 `dist\CUTORA-<ver>\`
3. 复制运行所需（白名单见 1b）
4. **复制全部模型并逐个校验哈希**（见 1c）
5. **写入可移植 Python 运行时**（见 1d）
6. 生成干净 `UserData\`（见 1e）
7. 写 `BUILD.txt`（版本 / HEAD / tag / PySide6 / 模型清单+哈希 / 字体 count+digest / 打包 UTC 时间）
8. **打包后自检**：在 dist 目录里**用包内的 python** 跑一次 `python -m cutora --selftest`
   （或等价的 headless 启动检查：import 全部关键模块 + 加载 model_paths + 列出字体 215）
9. ZIP（`allowZip64=True`，包会超过 4 GB）
10. 算 zip 的 SHA256 → 写到 `<zip>.sha256`，屏幕上打印路径 + 哈希 + 体积

### 1b. 白名单（只打这些，其余一律不进）

```
cutora/            （排除 __pycache__）
resources/         （排除 fonts/fonts_pending、fonts/_quarantine、fonts/_extra）
runtime/ffmpeg/
runtime/venv/      （排除 __pycache__、*.pyc）
runtime/python/    ← 1d 新增
runtime/models/    ← 1c，排除 asr_fake、.cache
vendor/
CUTORA.bat / CUTORA_Install.bat / CUTORA_CLI.bat / CUTORA_CreateShortcut.bat / cutora_mcp.bat
requirements*.in / *.lock
README_INTERNAL.md / PACK_GATE.md / BUILD.txt / portable.flag
```

**明确排除**：`tests/`、`docs/`、`tools/`、`.git*`、`track/`、`build/`、
开发者的 `UserData/`、任何 `*.asproj.json` 工程文件、`CUTORA_trace.bat`。

### 1c. 模型：完整、可验证、不硬编码

- **不许在脚本里手写模型列表。** 从应用自己的模型管理代码（`cutora/asr/` 里
  管 `model_paths.json` 和模型目录的那一套）取"必需模型集合"，打包用同一个来源，
  这样应用要什么、包里就有什么，永远不会漂移。
- 当前磁盘上：`asr_small`（484 MB）、`asr_medium`（1.53 GB）、
  `align_wav2vec2`（`pytorch_model.bin` 1.27 GB + `wav2vec2_fairseq_large_lv60k_asr_ls960.pth`
  1.26 GB + `wav2vec2_voxpopuli_base_10k_asr_it.pt` 378 MB）。
  **`asr_fake` 是测试夹具，绝不进包。** `.cache/huggingface` 不进。
- **`vad_segmentation/` 目录是空的，`model_paths.json` 里 `vad_path: null`。**
  回答我：VAD 用的是什么？Silero 已经在 venv 里？还是根本没用？
  如果应用某条路径需要它而磁盘上没有，那是现在就存在的 bug，打包前说清楚。
- 每个模型文件复制后用 `tools/pin_model_hashes.py` 那套 pin 校验 sha256，
  **不匹配就中止**。pin 文件也进包（`resources/data/model_hashes.json` 或它现在在哪）。
- 对齐模型：`wav2vec2` 有 EN 的两种格式 + IT 一个。**其它语种的对齐模型呢？**
  whisperX 默认对 fr/es/de/pt… 会从 HF 在线拉 `WAV2VEC2_ASR_*` / `jonatasgrosman/*`。
  内测机器可能没网、也不该悄悄联网下载。**打包前列出 languages.json 里
  `subtitle_tier=complete` 的语种各自需要哪个对齐模型，缺的要么补进包，
  要么应用里明确提示"该语种对齐模型未内置"。** 不许静默联网。

### 1d. 可移植 Python 运行时（修 0b）

方案（改动最小、明天能落地）：

1. 把 `C:\Program Files\Python310\` 整个复制到包内 `runtime\python\`（约 100 MB，
   PSF 许可允许再分发，**把 PSF `LICENSE.txt` 加进 `THIRD_PARTY_LICENSES.md`**）。
2. `CUTORA.bat` **每次启动**先把 `runtime\venv\pyvenv.cfg` 的 `home` 重写成
   `%ROOT%runtime\python`（绝对路径，幂等，一行 PowerShell 或 findstr 重写）。
3. `CUTORA_Install.bat` 的 `PY310` 改成 `%ROOT%runtime\python\python.exe`，
   不再依赖系统安装。
4. 打包后自检（1a 第 8 步）**必须在一个 `home` 路径故意指错的副本上先失败、
   改对后成功** —— 证明重写逻辑真的在起作用。

如果你有更干净的方案（embeddable Python + `._pth`），**先报我再动**，
不要在期限前换运行时布局。

### 1e. 干净的 UserData

我看了 beta3 里的 `UserData\`：`logs/commands.jsonl`、`cache/_preview_audio/*.m4a`、
`settings/project_queue.json`（带 Luca 本机路径）、`recent_projects.json`……
这些是 Luca 自己跑出来的，**不许进包**。打包时生成一套空目录结构 +
默认 `settings/default_style.json`、空的 `presets/*.json`。
**开发者的任何路径、工程、缓存、日志，一个都不许出现在包里。**

### 1f. `README_INTERNAL.md` 给测试者的硬件说明

- 需要 NVIDIA 显卡 + 支持 CUDA 12.8 的驱动（写具体最低版本号）。
- 没有 NVIDIA 显卡：ASR 退到 CPU，**很慢但必须能跑**。
  **回答我：CPU 路径现在会不会崩？** `[Config] Blackwell…` 那行说明有 GPU 探测，
  没 GPU 时走到哪？加一条 `CUDA_VISIBLE_DEVICES=""` 的启动测试。
- 首次启动时间、磁盘占用（解压后约 9–10 GB）。

---

## 2. P0-B · 字体承诺（T04L L2 + L3，L4 最小化）

上一份裁决的三条前提照旧，**快速回**：
- 3a `fonts_pending` 每个文件 sha256 对上 Google Fonts 可指名 release；对不上重下；全部进 PROVENANCE
- 3b Noto Serif 是完整还是半套？半套只补缺的那半，同一 release
- 3c 族名无重复测试

回完我立刻批。然后：
- **L2 落地**（五步 commit，golden 哈希必须一个不动）
- **L3 全部**：自有回退链 `script_fallback.json` + 三层告知 + **零豆腐测试** + **绝不碰系统字体测试**
- **L4 最小版**：`languages.json` 加 `subtitle_tier`，UI 在语言选择处显示
  「字幕排版：完善 / 仅字形保证」。整形 script 禁用逐字符动画 —— 这条也做，
  因为不做就是"显示错误"，违反承诺。

---

## 3. P1-A · 风格预设：每张卡片一个编辑入口

### 3a. 现状（我读了 `style_gallery.py`）
已有：`stylepack.apply / save_current / rename / delete`、导入/导出、用户包角标、
`UserData/presets/stylepacks/`、`subtitle_style_presets.json`。
**持久化骨架是全的。缺的只是入口和"另存为"语义。**

### 3b. 要做的
1. 每张风格卡片右下角一个**小而精的悬停按钮**（铅笔图标，随主题走，
   用现有 `icons.py` 体系，不要新引 SVG 来源）。
2. 点击 → 应用该预设到当前工程 **并** 把字幕样式面板切到"编辑模式"，
   面板顶部出现一条：`正在编辑预设「amber-karaoke」  [保存] [另存为…] [放弃]`
3. **保存**：覆盖该用户预设（新增命令 `stylepack.update`，保留原 id/path，
   写回 `meta.updated_at`）。**内置的 14 个不可覆盖** —— 内置包的编辑条只有
   [另存为…] [放弃]，保存按钮不出现。
4. **另存为**：走现有 `save_current`，但默认名预填 `<原名> 副本`，
   标签继承。
5. **放弃**：Ctrl+Z 到进入编辑前的状态（用现有 undo 栈，应该已经支持）。
6. 编辑模式下切换到别的预设卡片 → 先问「放弃当前编辑？」。
7. 右键菜单加「编辑」「复制为新预设」（内置包也有这两项）。

### 3c. 锁
- `stylepack.update` 不许改动内置目录 —— 测试：对内置 path 调用必须抛 `CommandError`
- 保存后重新 `load_pack` 逐字节等于保存前面板状态序列化的结果
- 14 个内置包的文件在整个测试跑完后 sha256 不变（防止误写）

---

## 4. P1-B · 文本预设 + 批量「自动署名」

### 4a. 文本预设（新系统，`UserData/presets/text_presets.json`）
- 一个文本预设 = 文本内容 + 完整样式（字体/字重/颜色/描边/阴影/bg）+
  **相对定位**（锚点 9 宫格 + 相对边距 %）+ 动画（可 none）+ 层级。
  **定位必须相对**，同一个署名预设要在 1080×1920 和 1920×1080 上都落在同样的角落。
- 选中画布上的文本元素 → 属性面板「存为文本预设…」
- 文本元素属性面板顶部下拉「应用文本预设 ▼」；应用后可继续改，不影响预设本身
- 命令：`text_preset.save / text_preset.apply / text_preset.delete / text_preset.list`，
  **全部走 bus、可录进 recipe**（这是 4b 的前提）
- 和风格预设一样：编辑入口 + 保存 / 另存为，同一套交互，不要发明第二种

### 4b. 批量「自动署名」
- 在批量导出 / 工程队列对话框加一行：
  `☑ 自动署名：[文本预设 ▼]`
- 勾选后，队列里**每个工程**导出前执行一次 `text_preset.apply`（幂等：同一预设
  已存在则不重复加，按预设 id 识别）。
- 也做成 recipe 里可录的一步，这样 `apply_to_folder` 也能用。
- 位置用 4a 的相对定位 → 横竖版都对。
- 安全区：署名默认落在 `safe_zones.json` 的安全区内（T04f-5 那套），
  不遮平台 UI。

### 4c. 锁
- 同一预设对 1080×1920 和 1920×1080 各应用一次，署名元素的锚点相对坐标一致
- 批量对 3 个工程应用，每个工程恰好一个署名元素，重复运行不叠加
- 文本预设 JSON 往返（save → load → save）逐字节相同

---

## 5. 顺序与检查点（期限驱动，检查点减少，门禁不减）

| 检查点 | 内容 | 报什么 |
|---|---|---|
| **CP1** | 第 1 节打包脚本 + venv 可移植 + 1c 三个问题（VAD / 对齐模型 / CPU 路径） | 脚本路径、一次完整运行的日志、包体积、**在一台没有 Python 的机器上（或删掉 PATH 里的 Python 后）双击能起来的证据** |
| **CP2** | 第 2 节字体 L2+L3+L4 最小版 | 三条前提答复 → 我批 → 落地报告（golden 哈希未变 + 两条硬测试通过） |
| **CP3** | 第 3、4 节预设编辑 + 文本预设 + 自动署名 | 截图（走证据协议）+ 锁测试 |
| **CP4** | 用 `pack_internal.py` 打 `7.0.0-beta4` | zip 路径 + SHA256 + BUILD.txt |

**CP1 最先，今晚必须完成** —— 没有它，明天中午什么都发不出去。
CP2 / CP3 可并行。CP4 明天上午。

**不开 T04h / T04j / T04k。** 不做任何本任务书以外的功能。

---

## 6. 记一笔，留给对外分发（不在本轮）

`runtime\ffmpeg` 是 gyan.dev **full_build** —— 含 x264/x265，是 **GPL** 构建。
作为独立进程调用没问题，但对外商用分发前要么换 **LGPL 构建**（无 x264，H.264 走
NVENC/openh264），要么按 GPL 义务附源码获取声明。**内测不管，进 PACK_GATE 第 15 条。**
