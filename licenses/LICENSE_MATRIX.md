# 片语工作室（`D:\0_LTX`）开源与社区许可证合规及安全审计矩阵

- **审计日期**：2026-10-14
- **软件名称**：片语工作室 (Cutora LTX Studio v3.0)
- **使用范围声明**：
  1. **非商业与内部自用**：本软件套件及所含模型权重**仅限内部非商业（Non-Commercial）、非盈利目的使用**；
  2. **模型不公开、不上网**：所有本地 AI 模型均通过环境变量 `HF_HUB_OFFLINE=1` 与 `TRANSFORMERS_OFFLINE=1` 强制物理断网运行，不对外提供公共 API 或模型权重下载；
  3. **零大陆服务器节点**：全套件禁止连接任何 `.cn`、阿里云、清华源、ModelScope、百度、字节跳动或 DeepSeek 等服务器节点；
  4. **不含 MiniMax H3 模型**：按合规要求，`D:\0_LTX` 架构、算力网关及最终打包分发中**完全不包含 MiniMax H3 模型及权重**（原 `D:\0_LLM` 中的历史文件仅原地保留不删，不在本软件中加载或分发）。

---

## 一、 软件模块与运行库许可证清单

| 模块 / 依赖组件 | 许可证类型 | 内部非商用与内部分发合规性 |
| :--- | :--- | :--- |
| **片语工作室核心底座 (`core/`, `studio_launcher.py`)** | 自研内部软件 | ✅ 100% 自研可控 |
| **CUTORA 剪辑内核 (`modules/cutora_editor`)** | GPL-3.0-or-later | ✅ 合规（随附 `THIRD_PARTY.md`） |
| **Orante 2.0 制片内核 (`modules/orante_desk`)** | 自研内部软件 | ✅ 100% 自研可控 |
| **右键表格工具 (`modules/table_tool`)** | 自研内部软件 | ✅ 100% 自研可控 |
| **Robota 代理内核 (`modules/robota_hub`)** | 自研内部软件 | ✅ 100% 自研可控 |
| **Python 运行时 (`CPython 3.10 / 3.12`)** | PSF License | ✅ 允许自由使用与内部分发 |
| **PySide6 (Qt 6.10)** | LGPL-3.0 | ✅ 动态链接使用，完全合规 |
| **pywebview / FastAPI / Uvicorn / Starlette / Pydantic** | BSD-3-Clause / MIT | ✅ 宽松开源许可 |
| **Playwright / smolagents / HuggingFace Transformers** | Apache-2.0 | ✅ 宽松开源许可 |
| **FFmpeg (`ffmpeg.exe`, `ffprobe.exe`)** | GPL-3.0 | ✅ 作为独立外部命令行进程调用，完全合规 |
| **Rclone (`rclone.exe`)** | MIT License | ✅ 宽松开源许可 |
| **ComfyUI & ComfyUI-VideoHelperSuite** | GPL-3.0 | ✅ 仅本机环回 `127.0.0.1:8188` 内部调用 |

---

## 二、 本地 AI 模型权重许可证清单（仅限内部非商用，不公开权重）

| 模型名称 | 开发机构 | 许可证类型 | 合规说明 |
| :--- | :--- | :--- | :--- |
| **LTX-2.5 22B (Distilled NVFP4) + 视频/音频 VAE + 2x Upscaler** | Lightricks (以色列) | LTX-2 Community License | ✅ 符合社区许可与内部非盈利使用条款 |
| **LTX-2.5 IC-LoRA 系列 (Restore / Deblur / Clean Plate / Colorization / SDR→HDR / Refine)** | Lightricks (以色列) | LTX-2 Community License | ✅ 符合社区许可与内部非盈利使用条款 |
| **Gemma 4 (12B / E2B / E4B)** | Google (美国) | Gemma Terms of Use | ✅ 本地离线运行，符合 Google Gemma 条款 |
| **FLUX.2 [klein] 4B (含离线文本编码器)** | Black Forest Labs (德国) | Apache-2.0 | ✅ 宽松开源许可，100% 离线断网运行 |
| **Faster-Whisper (`small` / `large-v3-turbo`)** | Systran / OpenAI | MIT License | ✅ 宽松开源许可 |
| **Wav2Vec2 英语/德语/西语/法语/葡语对齐模型** | Meta / 社区 | Apache-2.0 | ✅ 宽松开源许可 |
| **Wav2Vec2 意语对齐 (`align-it`) & 多语对齐 (`align-mms`)** | Meta | **CC-BY-NC-4.0** | ✅ **仅限内部非商用 (Non-Commercial)**，符合当前非盈利定位 |
| **Meta SAM 2.1 (`large` / `base_plus`)** | Meta (美国) | Apache-2.0 | ✅ 宽松开源许可 |
| **Google DeepMind TAPNext++ (`512` / `256`)** | Google DeepMind | Apache-2.0 | ✅ 宽松开源许可 |
| **MediaPipe Face Landmarker & Intel DPT-Hybrid (MiDaS)** | Google / Intel (美国) | Apache-2.0 | ✅ 宽松开源许可 |
| **Nomos2 4x (`4xNomos2_realplksr_dysample.pth`)** | Philip Hofmann | CC-BY-4.0 | ✅ 开放知识共享许可 |
| **RMBG-2.0 (BiRefNet)** | BRIA AI (以色列) | **CC-BY-NC-4.0** | ✅ **仅限内部非商用 (Non-Commercial)**，符合当前非盈利定位 |
| **Chatterbox & Resemble Enhance** | Resemble AI (美国) | MIT License | ✅ 宽松开源许可 |
| **Demucs (`htdemucs`)** | Meta (美国) | MIT License | ✅ 宽松开源许可 |
| **MusicGen-small** | Meta (美国) | 代码 MIT / 权重 **CC-BY-NC-4.0** | ✅ **仅限内部非商用 (Non-Commercial)** |
| **XTTS-v2** | Coqui | **CPML (Coqui Public Model License)** | ✅ **仅限非商业用途** |
| **Stable Audio Open 1.0 + Google T5-base** | Stability AI / Google | Stability AI Community License / Apache-2.0 | ✅ 符合社区非商用条款 |
| ~~**MiniMax H3**~~ | ~~MiniMax~~ | **已排除** | 🚫 **未纳入 `D:\0_LTX` 及任何分发包** |

---

## 三、 网络与数据安全防线说明

1. **环回地址强制绑定**：所有本机微服务端口（Orante `9400`、Studio Gateway `7870`、ComfyUI `8188`、Robota `9600`/`9611`、表格工具随机端口）100% 绑定 `127.0.0.1`，外部局域网或公网物理不可访问，且均配置 `Host`/`Origin` 校验与 Session Token 保护。
2. **零明文凭据**：通过 `core/secrets_vault.py` 将 API Key 托管至 Windows 系统凭据管理器 (`WinVaultKeyring` / `CredWriteW`)，配置文件中不存储任何明文密钥。
3. **出站网络仅限用户明确授权的海外官方接口**：
   - Google Drive (`googleapis.com` — 仅在用户点击表格工具下载/上传时通过官方 `rclone` 连接)；
   - Google Flow (`flow.google.com` / `labs.google`)、Grok (`grok.com`)、Gemini (`generativelanguage.googleapis.com`)。
   - 其余所有本地 AI 推理进程全程处于 `HF_HUB_OFFLINE=1` 断网沙箱中。
