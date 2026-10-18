# 片语 Studio / CUTORA — 总体改版计划

> 版本：v1.0 · 2026-09-08 · 作者：Claude（分析与指导）· 执行：Grok 4.6 Heavy（全部编码、编译、测试）
> 适用范围：`D:\0_Dev\AutoSub_v6` → `D:\0_Dev\CUTORA` 的彻底改版。本文是"为什么和做什么"，具体"怎么做"见 `phase1_tasks/` 下的任务书。
>
> **编号三层规范（2026-09-15 起，永不混用）**：里程碑 `T00`–`T13` 以本文原表为准，永不新增、永不改义；切片 `T04f-6b2` 挂在里程碑下逐级细分；运维 `OPS-nn`（打包 / 门禁 / 发布 / 流程）不占里程碑号。

---

## 0. 一句话定位

**片语 Studio（CUTORA）= 以 Transcript 为核心数据层的、本地优先的 AI 视频剪辑器。**
Premiere 是 frame-first，Descript 是 text-first，CapCut 是 creator-first；片语 Studio 是 **semantic-first**：先理解语音和文字，再驱动专业时间线，最后按平台一键交付。

这句话决定了所有取舍：凡是让"语音 → 文字 → 字幕 → 剪辑 → 交付"这条链更顺的功能优先做；凡是 Premiere/Resolve 有但这条链不需要的（节点调色、Fusion、多机位、协作云）一律不做。

---

## 1. 现状盘点（AutoSub_v6 到底有什么）

分析了 `core/` 下全部代码后的结论：**这不是一个"字幕软件"，它已经是一个功能相当密集的竖屏短视频渲染器**，只是架构和外观还停留在工具脚本阶段。

### 1.1 已有资产（必须保留并升级）

| 领域 | 现有能力 | 所在文件 | 评价 |
|---|---|---|---|
| 语音识别 | faster-whisper (small/medium) + whisperX 对齐 (wav2vec2, en/it)，GPU 自动探测含 RTX 50 系 | `AutoSub_debug.py` AlignmentWorker, `config.py` | 核心资产，词级时间戳是卡拉OK字幕的基础 |
| 字幕渲染 | 逐字高亮（已读/未读色）、弹力条、光晕、闪光、打字机、歌词滚动、多层扩展描边、渐变背景、关键词规则（KeywordRules）、角色映射（RoleMapping）、字块（WordBlock） | `SubtitleStylePanel`, `anim_engine.py`, `preset_manager.py` | 行业里少见的丰富度，是"字幕设计系统"的雏形 |
| 图层系统 | 文本覆层（富文本、混合模式、渐变背景）、图片/图形覆层（7 种形状）、视频适配（fill/fit/stretch）、裁剪羽化、变换 | `PropertyPanel`, `VideoCanvas` | 已接近 CapCut 的贴纸/文字层 |
| 时间线 | 多轨（visual/audio domain）、转场 26 种、片段模型（Clip/Track/TransitionItem）、撤销历史（HistoryManager） | `timeline_model.py`, `timeline.py`, `TimelineWidget` | 模型可用，交互（ripple/trim/snap）还很弱 |
| 音频 | 空间立体声（5 组 IR 卷积混响）、麦克风质感、环境音效库（自合成 wav，无版权问题）、视觉 AI 选音效（Gemini/Ollama/xAI） | `spatial_audio.py`, `sfx_*.py`, `sfx_ai.py` | 特色功能，保留 |
| 批量生产 | 片段智能拼接（文案对齐 ASR 自动剪）、片段散落、批量插图、工程队列、自动拆分工程、并行多进程导出、导出保活/看门狗、无头 CLI | `script_stitcher.py`, `script_*.py`, `autosub_cli.py`, `export_guard.py`, `media_guard.py` | 这是与 flow 流水线打通的部分，**一行算法都不能坏** |
| 导出 | NVENC/x264，压缩(CRF18)/无损两档，逐字节可复现的并行导出 | `RenderWorker`, `ParallelExportManager` | 稳定但缺平台预设、缺响度标准化、缺 SRT 侧车 |
| 字体 | 568 个 Google Fonts（OFL，`fonts/LICENSES` 在） | `FontManager` | 可分发，但要审计一遍 |

### 1.2 硬伤（改版必须解决）

1. **单文件怪物**：`AutoSub_debug.py` 29,587 行 / 1.4 MB，13 个 `.bak` 副本散落在源码目录。任何新功能都在给这个文件加重量。这是第一优先级要拆的东西——**不拆，后面所有阶段都做不动**。
2. **没有字幕文件的进出口**：全代码搜不到 SRT/VTT。一个字幕软件不能导出 SRT，在 2026 年是不可接受的。
3. **API 密钥明文**：`user_data/app_settings.json` 里 `gemini_api_key` / `xai_api_key` 明文存储。必须迁到 Windows 凭据管理器。
4. **安装链脆弱**：`1_Install.bat` 手工串了 20 多条 `pip install --no-deps`，版本靠运气对齐；`requirements_autosub_LOCKED.txt` 写的 `torch 2.5.1+cu121`，安装脚本装的是 `2.7.1+cu128`，两处已经不一致。要改成带哈希的锁文件。
5. **UI 是"工具面板堆叠"**：所有功能都在，但没有信息层级，新用户打开就是几十个颜色按钮。需要"快速模式 / 专业模式"分层。
6. **项目可靠性缺口**：没有自动保存、没有崩溃恢复、没有项目版本快照、缺失媒体没有重新链接。
7. **`.gitignore` 忽略了 `*.json` 和 `*.txt`**，导致 `requirements`、预设等关键文件根本没进版本库。

---

## 2. 取舍：做什么、不做什么

用户原始讨论里列了七层能力，逐条判断（★ = Phase 1 做，◆ = Phase 2/3 做，✗ = 不做或很后）。

### 2.1 专业 NLE 基础（Premiere / FCP / Avid 的启示）

| 功能 | 决定 | 理由 |
|---|---|---|
| 多轨视频/音频、无限轨道 | ★ 已有模型，补交互 | 地基 |
| Ripple / Roll / Slip / Slide / Trim / Split / Snap | ★ Ripple、Trim、Split、Snap；◆ Roll/Slip/Slide | 前四个是 90% 场景，后三个专业向 |
| 标记 (Markers) | ★ | 简单且高频 |
| 关键帧 | ◆ 位置/缩放/透明度三项 | 贴纸动画需要，但现有 anim_engine 已覆盖大半 |
| 转场、变速、定格 | ★ 转场已有；◆ 变速/定格 | 短视频常用 |
| 多机位、嵌套序列、复合片段、调整图层 | ✗ | 目标用户不用 |
| 媒体库：文件夹、标签、搜索、缩略图、元数据 | ★ | 已有文件夹，补齐 |
| 代理媒体 (Proxy) | ◆ | 4K 素材才需要，先做自动预览降采样 |
| 离线/重新链接 | ★ | media_guard 已有探测基础 |
| 自动保存、崩溃恢复、项目版本、备份 | ★ | 专业可信度的底线 |
| 撤销/重做 | ★ 已有，扩展覆盖到时间线全部操作 | |
| 磁性时间线（FCP） | ◆ 做"闭合间隙"和"字幕跟随主轨"两个开关，不做完整磁性 | 完整磁性会推翻现有模型 |

### 2.2 语义层（Descript / Premiere Text-Based Editing）

| 功能 | 决定 |
|---|---|
| Transcript 面板：可编辑文本、点句跳转、合并/拆分句、查找替换 | ★ |
| 删文字 = 删视频（ripple） | ◆ Phase 2 首要 |
| 静音检测与一键删除长停顿 | ◆ |
| 填充词（um/uh/呃/就是）检测删除 | ◆ |
| 说话人分离（pyannote 已在依赖里） | ◆ |
| 翻译（可配置 LLM 提供商） | ◆ |
| 主题/章节、情绪、物体识别 | ✗ 情绪与物体；◆ 章节（LLM 从 transcript 做） |
| 语义搜索"找所有讲婚姻的地方" | ◆ 用 transcript 文本 + LLM，不做向量库 |
| Shorts 候选片段自动挑选 | ◆ Phase 3 |

### 2.3 字幕引擎（片语 Studio 要做到最好的地方）

| 功能 | 决定 |
|---|---|
| SRT / VTT / ASS / TXT 导入导出，导出时可选侧车文件 | ★ |
| 智能断行（中日韩按字数、拉丁按词、标点优先、每行字数上限）| ★ |
| 阅读速度 (CPS) 检查与警告 | ★ |
| 字幕风格包（风格 = 一个 JSON，带缩略图画廊，内置 12 套原创设计） | ★ |
| 关键词/人名/数字/经文独立样式 | ★ 已有 KeywordRules，升级 UI |
| 说话人配色 | ◆ 随 diarization |
| 时间轴优化（最短显示时长、最小间隔、防闪） | ★ |
| 翻译双语字幕 | ◆ |

### 2.4 音频

| 功能 | 决定 |
|---|---|
| 响度标准化（-14 LUFS 平台标准） | ★ 纯 FFmpeg loudnorm |
| 人声清理预设（降噪/齿音/压缩）"播客/YouTube/采访" | ★ 纯 FFmpeg 滤镜链，零新依赖 |
| 音乐自动闪避 (ducking) | ★ FFmpeg sidechaincompress |
| 空间立体声 / 音效库 | ★ 保留 |
| AI 人声分离、AI 母带 | ✗ |

### 2.5 导出与交付（VEED / CapCut 的启示）

| 功能 | 决定 |
|---|---|
| 平台预设：YouTube 16:9、Shorts/TikTok/Reels 9:16、IG 4:5、1:1、LinkedIn | ★ |
| 一个工程导出多版本（横屏 + 竖屏） | ★ 基础版（中心裁切 + 手动定位）；◆ 人脸自动构图 |
| 安全区叠加显示（TikTok/Shorts UI 遮挡区） | ★ |
| 封面帧导出、SRT 侧车、章节文件 | ★ |
| 导出队列、后台导出（已有并行） | ★ |
| 硬件编码：NVENC 已有；AMD AMF / Intel QSV 探测 | ★ |
| 直接发布到平台 | ✗ 版权与 OAuth 风险，不做 |

### 2.6 AI 编排层（InVideo / Descript Underlord）

| 功能 | 决定 |
|---|---|
| 统一的 LLM 提供商接口（OpenAI / Anthropic / Google Gemini / xAI / 本地 Ollama），密钥入凭据管理器 | ★ 接口先立好（Phase 1），功能 Phase 2+ |
| "Ask 片语" 自然语言命令框 → 生成可预览、可撤销的时间线操作 | ◆ Phase 3 |
| 生成式视频（Runway/Kling）| ✗ 不进核心；未来做插件位 |
| TTS 配音 | ◆ 仅本地开源引擎（Piper / Kokoro，MIT/Apache）或用户自带 API |

### 2.7 资产库与设计系统（"不再需要打开 Canva"）

Luca 的明确要求：设计元素、边框、卡片、配乐、音效、特效、动态都要有**大量预设库**。判断：现有图层系统（文本覆层 + 图形覆层 + 图片覆层 + 混合模式 + anim_engine）已经是一个小型 Canva 引擎，缺的是**内容**和**入口**。所以先立"资产包"格式与资产库面板（Phase 1），再持续灌内容（Phase 2 起每个阶段都有"内容扩充"任务）。

| 库 | 决定 | 版权策略 |
|---|---|---|
| 设计元素（形状、徽章、箭头、下三分之一条、标题卡、引言卡、进度条、社交账号条、片尾卡、边框/相框） | ★ 参数化 SVG 模板，颜色/文字/圆角/描边可改，首批 ≥ 80 个 | 全部由 Grok 原创绘制，不得临摹任何已有模板 |
| 封面/缩略图静态画布模式（导出 PNG/JPG，任意尺寸） | ★ | 复用图层系统 |
| 项目模板（竖屏语录、讲道片段、播客片段、新闻卡、教程） | ★ 首批 10 个 | 原创 |
| 视觉特效（亮度/对比/饱和/色温/暗角/颗粒/模糊/锐化、镜头推拉、抖动、闪白、光漏、色差、老电影） | ★ 首批 20 个，OpenCV/QPainter 实现 | 程序生成，无外部素材 |
| 动画库（入场/出场/强调，作用于任何图层；现有 26 转场扩到 ≥ 40） | ★ | anim_engine 扩展 |
| 音效库 | ★ 现有 14 个自合成 wav 扩到 ≥ 60（UI 提示音、转场音、whoosh、pop、click、铃、风、雨、人群） | 全部 `sfx_synth.py` 程序合成，天然无版权 |
| 配乐库 | ★ 三路并行：① 程序化生成的氛围床（pad/drone/钢琴琶音/心跳节奏，可无限生成、可控 BPM/调性/时长）；② 精选 **CC0** 曲目包（每首记录来源 URL、许可证、SHA256，Claude 逐首复核）；③ 用户自有音乐文件夹导入 + BPM 检测 + 自动闪避 | 内置只收 CC0；CC-BY 不内置（避免署名遗漏）；不接任何在线素材库 API |
| 字体库 | ★ 现有 568 个 Google Fonts 全部审计；扩充 Noto 全语系（OFL）以覆盖多语种字幕；可再纳入 OFL/Apache/UFL 许可的其他字体 | 见 §5 第 5 条：只允许 OFL 1.1 / Apache 2.0 / UFL 1.0 / Bitstream Vera 四种许可；每个字体附许可证文件；禁止"仅限个人使用"、Fontshare(ITF)、DaFont 等来源 |
| 用户资产库 | ★ 用户自己的图片/视频/音乐/字体文件夹加入库，打标签、搜索、收藏 | 用户自负 |

### 2.8 多语种自动字幕

现状只有 en/it 两个对齐模型。决定：
- 识别：faster-whisper 本身支持 ~99 种语言，开放全部；模型档位 `small / medium / large-v3 / large-v3-turbo`，首次使用按需从 huggingface.co 官方下载并校验。
- 自动检测语言，也可手选。
- 词级对齐：whisperX 官方对齐表覆盖约 30+ 语言（en, zh, ja, ko, fr, de, es, it, pt, nl, ru, uk, pl, cs, ar, tr, el, he, fa, hi, vi, hu, fi, da, sv, nb, ca, eu, gl, ka, lv, sk, sl, hr, ml, te, ur, tl …），按需下载。没有对齐模型的语言，回退到 faster-whisper 的 `word_timestamps=True`，UI 标明"近似词级"。
- 中/日/韩/泰等无空格语言的断行规则单独实现；阿拉伯/希伯来的 RTL 渲染要验证。
- 模型管理器界面：显示已下载/未下载、大小、许可证、删除。

### 2.10 效率、自动化与代理机器人接口（贯穿全部阶段的设计原则）

Luca 的要求：不只好看好用，还要**高效**——重复性工作要能批量化、自动化；并且要有**对代理机器人友好的 API**，将来用自然语言指挥机器人（Grok Bot / Claude 等）直接读接口文档操作软件。

这条要求决定了一个架构级决定：**所有操作都走命令总线（Command Bus）**。UI 按钮、快捷键、CLI、本地 API、未来的"Ask 片语"，调用的是同一组命令；每条命令可撤销、可记录、可回放、可被机器读。

| 能力 | 决定 |
|---|---|
| 命令总线 `cutora/core/commands.py` | ★ Phase 1（T13）。每个命令 = 名称 + JSON 参数 schema + 执行 + 撤销 + 中文说明。UI 只是命令的调用者 |
| 配方（Recipe）：把一串命令保存成"配方"，对一个文件夹的视频批量套用 | ★ T13。例：`导入 → 自动字幕(auto) → 风格包"琥珀高亮" → 人声清理"播客" → 响度-14 → 导出 9:16 + 16:9 + SRT` |
| 操作录制器：在 UI 里做一遍 → 自动生成配方 | ★ T13 |
| 监视文件夹：新视频落入即按配方处理 | ◆ Phase 2 |
| 本地 API：`http://127.0.0.1:<port>`，JSON，OpenAPI 3.1 文档自动生成，默认关闭、仅本机、Bearer token | ★ T13（基础命令集）→ 逐阶段扩充 |
| MCP 服务器（Model Context Protocol）把同一组命令暴露为 tools，任何支持 MCP 的代理可直接连接 | ★ T13 最小版；Phase 4 完整 |
| `cutora_cli` 无头命令与 API 一一对应 | ★ |
| 机器可读文档 `docs/api/`：每个命令的 schema + 示例 + 副作用说明，由代码自动生成，保证不过时 | ★ |
| 批量维度：多文件、多风格、多平台尺寸、多语言（Phase 2）四个维度可组合 | ★ 前三个 Phase 1 |

安全边界（与 §5 一致）：API 只绑定 127.0.0.1；token 存凭据管理器；命令白名单，没有"执行任意代码"的命令；删除类命令必须带 `confirm=true`；每次 API 调用写审计日志；UI 里有明显的"代理连接中"指示与一键断开。

### 2.11 明确不做

节点式调色与 Fusion、多机位、协作云、网页版、自训练模型、插件市场、任何社交平台直传、任何指向中国大陆服务器或中共关联企业的服务（包括 CapCut/剪映、Kling、Seedance、通义、百度、腾讯、阿里云镜像、hf-mirror、清华 pypi 镜像等）。

---

## 3. 品牌与视觉

- **名字**：中文 **片语 Studio**，英文 **CUTORA**。窗口标题、关于对话框、启动画面显示"片语 Studio"，英文界面下显示"CUTORA"。文件、目录、脚本、包名、进程名、注册表键、安装目录一律 `CUTORA` / `cutora`。
- **含义**：片语 = 片段 + 话语。CUT + ORA（拉丁语"说/祈祷"，也呼应意大利语 ora"现在"）。品牌语：**让每一句话，找到它的位置。** / **Every word finds its place.**
- **图标概念**（交给 Grok 出 3 版 SVG 供选）：
  1. 一个圆角方形深色底，中间一个大写 C 的字形，C 的开口处被一道锋利的斜切线切开，切线下方渐变成一条声音波形——"把话语切成片"。
  2. 引号「 」的两个半片错位，中间空隙是一条剪辑刀口线。
  3. 一枚方形胶片框，框内一行文字被光标高亮到一半（卡拉OK字幕的隐喻）。
  要求：单色可识别、16px 仍清晰、深浅两版、无渐变时依然成立。配色主色 `#0E1116`（石墨黑）+ 强调色 `#F5B700`（琥珀金，呼应"祈祷/烛光"语境，同时在黑底上极醒目），辅色 `#5CE1E6`（冰青，用于选中/进度）。
- **界面设计系统**："Studio Graphite"深色主题，8pt 间距网格，字体 Inter + Noto Sans SC（均 OFL），图标 Lucide（ISC 许可）。三栏 + 底部时间线的经典 NLE 布局，顶部一条"快速模式"工具带（导入 → 字幕 → 风格 → 导出 四步），右上角"专业模式"开关展开全部面板。**新用户 3 分钟出片，老用户不失去任何现有功能。**

---

## 4. 技术架构决定

1. **不换技术栈。** 继续 Python 3.10 + PySide6 + FFmpeg + faster-whisper/whisperX + OpenCV。重写成 C++/Electron 的风险与成本远超收益，而且现有渲染逻辑已被逐字节验证。
2. **拆成包**：
   ```
   D:\0_Dev\CUTORA\
   ├── cutora/                 # 主包
   │   ├── app.py              # 入口：QApplication、启动画面、崩溃恢复
   │   ├── core/               # config, paths, settings, secrets(凭据), logging, history
   │   ├── model/              # timeline_model, project(.cutora 文件、迁移), media
   │   ├── asr/                # whisper 加载、对齐、后处理、diarization(占位)
   │   ├── subtitle/           # 分段、断行、CPS、srt/vtt/ass 读写、风格包
   │   ├── render/             # FrameRenderer, ExportRenderer, RenderWorker, 并行导出
   │   ├── audio/              # spatial, sfx, loudness, voice presets, ducking
   │   ├── ai/                 # providers(openai/anthropic/gemini/xai/ollama), sfx_ai
   │   ├── batch/              # script_stitcher, scatter, insert_image, queue, cli
   │   ├── ui/                 # main_window, panels/, timeline/, widgets/, theme/
   │   └── resources/          # icons, qss, fonts(引用), presets
   ├── runtime/                # venv（不入库）、ffmpeg、models（不入库）
   ├── tests/                  # golden 渲染对拍 + 单元测试
   ├── build/                  # build.ps1, installer.iss, portable 打包
   ├── docs/plan/              # 本目录的副本
   └── CUTORA.bat / CUTORA_Install.bat / CUTORA_CLI.bat
   ```
3. **拆包是"搬家不装修"**：只移动代码、改 import，不改任何逻辑。用 golden 渲染对拍（改前改后导出结果 MD5 相同）作为唯一验收标准。这是整个 Phase 1 最重要、最危险的一步，必须单独做、单独验。
4. **用户数据搬到 `%LOCALAPPDATA%\CUTORA\`**（设置、预设、最近项目、自动保存、日志），不再写在程序目录里——这是安装到 Program Files 的前提。首次启动检测 `AutoSub_v6\core\user_data` 并提供一键导入。
5. **项目文件 `.cutora`**：JSON，带 `schema_version`，读旧 `.asproj` 自动迁移。
6. **依赖锁定**：`requirements.lock`（`pip-compile --generate-hashes`），安装只允许 `pypi.org` 与 `download.pytorch.org`，`--require-hashes`。
7. **AI 提供商抽象**：一个 `ai/providers.py`，统一 `chat(messages, images=None)` 接口；密钥经 `keyring`（Windows Credential Manager）存取；设置界面显示的是"已保存"而非密钥本身。

---

## 5. 安全与版权红线（对 Grok 的硬约束，每个任务书都会重复）

1. 禁止引入任何指向中国大陆的域名、镜像、SDK、模型托管（含 modelscope、hf-mirror、aliyun、tsinghua、baidu、tencent、bytedance/CapCut、kuaishou/Kling、字节 Seedance）。模型只从 `huggingface.co` 官方与 `github.com` 官方仓库下载，并校验 SHA256。
2. 禁止任何形式的遥测、自动上报、未经用户点击的网络请求。软件启动不联网。检查更新只在用户点击时进行，且只读取 GitHub Releases 的 JSON，不自动执行下载的任何东西。
3. 禁止 `eval` / `exec` 处理外部输入、禁止 `shell=True` 拼接用户路径、禁止 pickle 反序列化项目文件、禁止从网络加载代码。FFmpeg 调用一律参数列表。
4. 密钥不落明文；日志脱敏；崩溃报告只写本地文件。
5. 只使用许可明确的第三方资产：字体仅 OFL 1.1 / Apache 2.0 / UFL 1.0 / Bitstream Vera 许可，且每个字体家族随包附带其许可证文件、遵守 OFL 的保留字体名规则（不改名、不单独出售）；图标用 Lucide（ISC）或自绘；音效只用仓库内自合成 wav；内置配乐只收 CC0 或程序生成；预设名称不得使用任何商标（不出现 TikTok/抖音/Instagram 商标图形，文字可作平台尺寸说明）。

   可纳入的字体来源（全部可商用、可随软件再分发）：Google Fonts 全库（OFL/Apache/UFL）、Noto 全语系（OFL）、Adobe Source Sans/Serif/Code 与 Source Han Sans/Serif（OFL）、IBM Plex（OFL）、Inter（OFL）、JetBrains Mono（OFL）、Fira Sans/Code（OFL）、Atkinson Hyperlegible（OFL）、Public Sans（OFL）、Recursive（OFL）、Cooper Hewitt（OFL）、The League of Moveable Type 全部（OFL）、Velvetyne（OFL）、Ubuntu（UFL）、DejaVu（Bitstream Vera）、Liberation（OFL）。
   明确排除：Fontshare/ITF Free Font License（禁止再分发）、DaFont 与任何"仅限个人使用"字体、任何未附许可证文件的字体、微软/苹果系统自带字体、方正/汉仪/华康等商业中文字体。
6. 分发物：可复现构建、GitHub Actions 出 provenance attestation（现有 `release.yml` 的做法保留）、SHA256SUMS 文件。代码签名证书由 Luca 决定是否购买；任务书里预留签名步骤但不阻塞。
7. 每个任务结束 Grok 必须跑 `pip-audit` 与仓库 grep 扫描（域名黑名单、`eval(`、`shell=True`、`pickle.load`），把结果写进报告。

---

## 6. 分阶段路线

### Phase 1 — 改头换面 + 专业剪辑基础（本轮写细，见 `phase1_tasks/`）

| 编号 | 任务 | 产物 |
|---|---|---|
| T00 | 建立 CUTORA 仓库、运行环境、golden 基线 | 可运行的复制品 + `tests/golden/` |
| T01 | 拆包：29,587 行单文件 → `cutora/` 包（零逻辑改动） | golden 对拍全绿 |
| T02 | 品牌落地：改名、图标、启动画面、关于、用户数据迁移 | 打开就是"片语 Studio" |
| T03 | 设计系统与新界面外壳：主题、布局、快速/专业模式、快捷键、欢迎页 | 新皮肤，旧功能全在 |
| T04 | 字幕引擎升级 A：SRT/VTT/ASS 进出口、Transcript 面板、断行、CPS、时间轴优化、风格包画廊 | |
| T05 | 时间线专业化：ripple/trim/split/snap/marker/波形/JKL/多选/间隙闭合 | |
| T06 | 媒体库与项目可靠性：标签搜索缩略图、重新链接、自动保存、崩溃恢复、版本快照、`.cutora` 格式 | |
| T07 | 音频基础：响度标准化、人声清理预设、音乐闪避、静音检测（数据） | |
| T08 | 导出与交付：平台预设、多版本导出、安全区、SRT 侧车、封面帧、AMF/QSV | |
| T09 | 安全与版权审计：密钥入凭据库、锁文件、字体/资产审计、扫描脚本 | 审计报告 |
| T10 | 打包分发：安装包 + 便携 zip、首启模型下载器、构建脚本、CI | 两个可分发产物 |
| T11 | 资产库与预设系统：资产包格式、资产库面板、静态画布(封面)模式、首批设计元素/特效/动画/音效/氛围配乐/项目模板 | "不用开 Canva" 的基础 |
| T12 | 多语种自动字幕：全语种识别、自动检测、30+ 语言词级对齐、回退策略、模型管理器、CJK/RTL 断行与渲染 | |
| T13 | 命令总线、配方批处理、操作录制、本地 API + OpenAPI 文档 + 最小 MCP 服务器、CLI 对齐 | 代理机器人可操作的软件 |

建议执行顺序：T00 → T01 → T09（先把安全底座立好）→ T02 → T03 → **T13**（命令总线要在功能任务之前立好，之后 T04–T08、T11 的每个新功能都必须注册为命令）→ T12 → T04 → T05 → T06 → T07 → T08 → T11 → T10。T04–T08、T11 相互独立，可穿插；T10 必须最后。

### Phase 2 — 语义编辑器 + 内容库扩充（骨架）

- P2-00 内容扩充 I：设计元素 → 200+，特效 → 40，动画 → 60，项目模板 → 25，CC0 配乐包复核扩充，Noto 之外的 OFL 字体家族扩充。

- P2-01 Transcript 驱动剪辑：在 Transcript 面板删词/删句 → 时间线 ripple 删除，可撤销。
- P2-02 静音与填充词检测：可调阈值，列表预览 + 勾选 + 一键应用。
- P2-03 说话人分离（pyannote，本地）+ 说话人配色 + 按说话人筛选。
- P2-04 翻译与双语字幕（走 `ai/providers`）。
- P2-05 章节/主题生成 + 章节文件导出（YouTube 章节格式）。
- P2-06 Transcript 全文搜索与语义搜索（LLM 归纳，不做向量库）。
- P2-07 人脸感知自动构图（OpenCV YuNet，Apache 2.0）用于 16:9 → 9:16。
- P2-08 关键帧（位置/缩放/透明度）。

### Phase 3 — AI 协同剪辑（骨架）

- P3-01 "Ask 片语" 命令框：意图 → 操作计划（JSON）→ 预览差异 → 用户确认 → 执行到真实时间线 → 可撤销。
- P3-02 内置意图：剪紧凑、去废话、压到 N 秒、挑 N 个 Shorts、套用默认风格、音乐压低、把开头做钩子。
- P3-03 Shorts 候选评分（钩子强度、完整句、时长窗口）。
- P3-04 本地 TTS（Piper/Kokoro）+ 用户自带 API 的 TTS 提供商。
- P3-05 B-roll 建议：从用户自己的素材库按 transcript 关键词匹配（不接任何在线素材库）。

### Phase 4 — 代理式生产（骨架）

- P4-01 "一个工程 → 正片 + N 个 Shorts + 多语字幕"的项目模板与批处理。
- P4-02 与 flow 视频流水线的无头对接（`cutora_cli` 完整命令集、job JSON 协议、进度文件）。
- P4-03 插件位：外部生成式模型（Runway 等）作为可选插件接入，不进核心。

---

## 7. 协作方式（Claude ↔ Grok ↔ Luca）

1. Luca 把 `phase1_tasks/Txx_*.md` 的全文粘给 Grok（PowerShell 的 Grok 4.6 Heavy 或 Grok Bot 均可），Grok 在 `D:\0_Dev\CUTORA` 里干活。
2. 每个任务结束，Grok 必须写 `docs/plan/reports/Txx_report.md`（模板见 `01_GROK_通用规范.md`），内容包括：改了哪些文件、怎么验证、golden 对拍结果、安全扫描结果、遗留问题。
3. Luca 把报告回传给 Claude（或让 Claude 直接读 `D:\0_Dev\CUTORA`），Claude 审核后给出"通过 / 返工清单"。返工清单同样是一份可粘贴的短任务书。
4. **Grok 不得跨任务擅自扩展范围**。发现应该改但不在任务书里的东西，写进报告的"建议"栏，由 Claude 决定放进哪个任务。
5. 所有报告用中文。代码注释中英皆可，标识符一律英文。

---

## 8. 验收的总标准（Phase 1 结束时）

- 双击 `CUTORA.bat` 或安装后的快捷方式，3 秒内出现"片语 Studio"启动画面，主窗口为新设计系统。
- 拖入一条视频 → 点"自动字幕" → 选一个风格包 → 点"导出 · 竖屏 9:16"，全程不打开任何专业面板即可完成，导出物带 SRT 侧车，响度 -14 LUFS。
- 专业模式下，原 AutoSub_v6 的每一个功能都能找到并工作；片段智能拼接、散落、批量插图、工程队列、并行导出、无头 CLI 的输出与 v6 逐字节一致（golden 对拍）。
- `pip-audit` 零高危；域名黑名单扫描零命中；`app_settings.json` 中不存在任何密钥字段。
- `CUTORA-Setup-x.y.z.exe` 与 `CUTORA-x.y.z-portable.zip` 各一份，附 `SHA256SUMS.txt`，在一台干净的 Windows 11 虚拟机上安装/解压后能完成上面第二条。
