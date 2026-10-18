# 片语工作室 (Cutora LTX Studio v3.0)

> **“让每一句话，找到它的位置。”**  
> **CUTORA · ORANTE · ROBOTA · LTX-2.5 — 一体化 AI 影视制片、智能剪辑与自动化协同工作站**

---

## 一、核心架构设计理念：统一外壳 · 乐高式可拆可装 (Single Project, Modular Everything)

**片语工作室 (`PianYu Studio`)** 是一个完整的单一软件工程。无论内部成员下载的是**极简专享版**、**25GB 标准核心版**还是**126GB 旗舰完整版**：
1. **统一入口与总控台 (`studio_launcher.py`)**：所有使用者均统一从《片语工作室》9:16 罗马黑金海报级总控台进入，外框视觉、信号总线 (`SpineDB`) 与代码设计 100% 保持一致。
2. **轻量高频工具默认随附**：体积小巧的 `03 右键表格工具` (`modules/table_tool`)、`02 片语 Studio · CUTORA` (`modules/cutora_editor`) 与底层共享核心 (`core`) 默认内置于所有分发包中。
3. **从大模块到小模块全可拆可装 (`.ltxpack`)**：
   - 如果某位成员最初只使用 **CUTORA**（未下载 `Orante` 或 `Robota`），总控台界面依然完整展示五大功能卡片；当其后续点击 `01 Orante 2.0 制片台` 或 `Robota · 协同操作代理` 时，系统会自动弹出**乐高扩展包中心**，提示一键无缝安装对应的 `.ltxpack` 扩展包，免重装软件即可热挂载激活！
   - 同様に，对于占用约 100 GB 磁盘空间的重型生成式模型（LTX-2.5 视频生成、IC-LoRA 特效、FLUX.2 生图、AI 配音配乐、4x 超分与 ComfyUI 推理引擎底座），均封装为独立的 `.ltxpack` 扩展包，按需下载、一键拼装。

---

## 二、版本档位与乐高扩展包矩阵

### 1. 基础发行档位（由 [`打包分发.py`](./打包分发.py) 一键生成）

| 发行档位参数 (`--edition`) | 适用人群与硬件配置 | 默认包含内容 | 预估体积 |
| :--- | :--- | :--- | :--- |
| **`cutora-only`** | 仅需剪辑台与表格工具的极简办公电脑 | 统一总控台 + CUTORA 剪辑台 + 右键表格工具 + 跨窗口总线 (`Orante` 与 `Robota` 可后续一键加装) | ~90 MB (代码底座) |
| **`standard`** | 已配好本地环境的快速热更新包 | 统一总控台 + 四大软件完整源码 (`CUTORA` + `Orante 2.0` + `右键表格工具` + `Robota` + `7870 网关`) | ~110 MB |
| **`base-25gb`** *(推荐主力分发)* | **全体内部成员**（含 i7 + 32GB 内存 + **无独显**笔记本） | 四大软件完整程序 + 便携 Python/FFmpeg/rclone 运行时 + **四大高频核心 AI 模型**（自动字幕 Whisper、智能拼接、SAM2.1/RMBG2 视频抠像、Demucs/Resemble 立体声与降噪、人像提亮修复） | ~15.4 GB (解压后) |

### 2. 可选乐高扩展包 (`.ltxpack` 按需一键安装)

将任意 `.ltxpack` 文件放入项目根目录下的 `packs/` 文件夹，或在总控台点击 **【🧩 扩展包】** 按钮，即可执行防路径穿越校验与原子级热挂载：

| 扩展包文件名 | 模块类别 | 体积 | 硬件要求 | 功能说明 |
| :--- | :--- | :--- | :--- | :--- |
| **`pack-app-orante.ltxpack`** | 独立软件模块 | `15 MB` | 任意电脑 (CPU) | **Orante 2.0 制片台**：Grok / Flow / LTX-2.5 批量排片、生片、审片与缺片回流补生 |
| **`pack-app-robota.ltxpack`** | 独立软件模块 | `7 MB` | 任意电脑 (CPU) | **Robota 协同操作代理**：对话发网盘链接自动建夹、下载改名 `v01(1)(2)`、全流程闭环巡检 |
| **`pack-engine-comfyui.ltxpack`** | 本地推理引擎底座 | `4.5 GB` | RTX 12GB+ 独显 | **便携式 ComfyUI 推理引擎底座**：内置 Custom Nodes、Gemma-4 多模态视觉导演节点与 CUDA 环境，通过 [`config/comfy_extra_model_paths.yaml`](./config/comfy_extra_model_paths.yaml) 自动映射 `runtime/models` 权重 |
| **`pack-ltx25-video.ltxpack`** | 重型 AI 模型包 | `39.2 GB` | RTX 16GB+ 独显 | **LTX-2.5 22B 本地视频生成包**：NVFP4 蒸馏主模型 + Gemma-4 视觉导演编码器 + 音视频双流 VAE + 2x 潜空间超分器 |
| **`pack-ltx25-vfx-loras.ltxpack`** | 重型 AI 模型包 | `7.6 GB` | RTX 16GB+ 独显 | **LTX-2.5 IC-LoRA 7合1 特效包**：画质修复、去运动模糊、去杂物、黑白上色、局部重绘、SDR转HDR、细节精修 |
| **`pack-flux2-image.ltxpack`** | 重型 AI 模型包 | `7.85 GB` | RTX 12GB+ 独显 | **FLUX.2 [klein] 4B 极速生图与改图包**：供分镜首帧生成与海报制作 |
| **`pack-audio-tts-bgm.ltxpack`** | 进阶 AI 模型包 | `25.6 GB` | 支持 CPU / GPU | **AI 语音克隆与配乐生成包**：Chatterbox / XTTS-v2 零样本配音 + Stable Audio / MusicGen 配乐 |
| **`pack-pro-vision-multilang.ltxpack`** | 进阶 AI 模型包 | `22.3 GB` | 支持 CPU / GPU | **专业视觉与多国语扩展包**：TAPNext++ 运动追踪、Nomos2 4x 超分、德/西/法/葡/多语种词级对齐 |

---

## 三、性能标准与质量保障

1. **CUTORA 极速预览与导出约定**：
   - 实时预览播放帧率 **≥ 40 fps**（智能预渲染代理缓存 + QAudioSink 连续 PCM 音频时钟主控，音画丝滑无卡顿）。
   - 成片导出帧率 **≥ 400 fps**（硬件 NVENC / CPU 超线程自适应流水线，实测复杂工程导出速度达 **560+ fps**）。
2. **LTX-2.5 多模态视觉导演架构 (Gemma-4 Vision-Grounded Director)**：
   - 针对中文复杂指令（如“禁止说话、嘴巴自然闭合、面带微笑认真聆听、手部自然放松、不能像照片一动不动”），本地推理管线通过 `TextGenerateLTX2Prompt` 将首帧参考图与中文提示词同步送入 **Gemma-4 多模态编码器**，自动生成与首帧人物姿态严格锚定的三阶段时序英文分镜剧本，并配合 `LTXVDualCFGGuider` 与 `LTXVSpatioTemporalGuidance (STG)` 实现媲美付费版 Grok 的连贯微表情与自然手部动作。

---

## 四、安全审核与模型合规文档索引

- 🛡️ **[《应用安全审核与修复完整报告》(SECURITY_AUDIT_REPORT.md)](./SECURITY_AUDIT_REPORT.md)**：包含全部直接/间接依赖 CVE 核验、代码漏洞逐行审计与修复记录、密钥防泄露机制与修复汇总表。
- 📜 **[《AI 模型版权、开源许可证与安全合规台账》(MODEL_LICENSE_COMPLIANCE.md)](./MODEL_LICENSE_COMPLIANCE.md)**：包含所有内置及扩展包模型的官方来源、开源许可证（MIT / Apache-2.0 / Open Weights）及 `safetensors` 安全反序列化说明。
- ⚙️ **[环境变量配置模板 (.env.example)](./.env.example)** 与 **[核心依赖锁定清单 (requirements.txt)](./requirements.txt)**。

---

## 五、快速启动与打包命令

```powershell
# 1. 启动片语工作室 9:16 总控台
.\片语工作室.bat

# 2. 查看版本与乐高扩展包精算表
.\runtime\venv\Scripts\python.exe 打包分发.py --edition plan

# 3. 打包标准代码底座版 / 极简 CUTORA 版 / 25GB 标准核心版
.\runtime\venv\Scripts\python.exe 打包分发.py --edition standard
.\runtime\venv\Scripts\python.exe 打包分发.py --edition cutora-only
.\runtime\venv\Scripts\python.exe 打包分发.py --edition base-25gb

# 4. 单独打包指定乐高扩展包 (.ltxpack)
.\runtime\venv\Scripts\python.exe 打包分发.py --edition pack --pack-id pack-app-orante
.\runtime\venv\Scripts\python.exe 打包分发.py --edition pack --pack-id pack-app-robota
.\runtime\venv\Scripts\python.exe 打包分发.py --edition pack --pack-id pack-engine-comfyui
.\runtime\venv\Scripts\python.exe 打包分发.py --edition pack --pack-id pack-ltx25-video
```
