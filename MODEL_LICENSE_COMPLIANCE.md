# 片语工作室 (Cutora LTX Studio v3.0) — AI 模型版权、开源许可证与安全合规台账

**文档版本**：v3.0 (2026-10-18)  
**适用范围**：片语工作室（含标准核心底座 25GB 及全部可选 `.ltxpack` 模块化扩展包）  
**安全与合规结论**：**全部通过 (PASS)** — 所有内置及可选扩展模型均来源于官方开源仓库，许可证清晰可溯源，核心生成式权重 100% 采用无代码执行风险的 `safetensors` / `CTranslate2` 格式封装，且已按合规要求**彻底移除并物理拦截 MiniMax H3 相关权重**。

---

## 一、模型合规与安全总则

1. **零盗版 / 零闭源泄露权重**：本项目所使用的全部 AI 模型均为官方公开发布的开源/开放权重模型，对应的许可证原文均随附于仓库 [`licenses/`](./licenses/) 目录中。
2. **反序列化安全 (`safetensors` 优先)**：
   - 视频生成（LTX-2.5 22B）、视觉导演编码器（Gemma-4）、图像生成（FLUX.2 [klein]）、背景抠图（RMBG-2.0）、全部 7 款 IC-LoRA 均 100% 采用 Hugging Face **`safetensors`** 格式，从文件格式层彻底杜绝 Python `pickle` 反序列化任意代码执行漏洞。
   - 自动字幕模型采用 **CTranslate2** 纯张量二进制格式（`model.bin`），不经过 Python `pickle`。
   - 少数历史 PyTorch 格式模型（如 SAM 2.1、DPT-Hybrid、Nomos2、Resemble Enhance）在加载时均强制执行**目录边界锚定校验 (`Path.resolve().relative_to()`)** 与 `weights_only=True` 优先策略。
3. **离线隐私保护 (`HF_HUB_OFFLINE=1`)**：所有本地模型推理过程均默认设置 `HF_HUB_OFFLINE=1`，所有音视频素材、字幕文稿与人像画面 100% 在本机内存/显存闭环处理，**零外部遥测、零云端上传、零大陆镜像节点 (`hf-mirror.com` / `modelscope.cn`) 调用**。

---

## 二、标准核心底座内置模型台账 (`base-25gb` 默认随附)

> 面向全体内部成员（含 i7 + 32GB 内存 + 无独显笔记本）默认内置的四大日常高频模型组：

| 序号 | 功能模块 | 模型名称 | 研发机构 / 官方来源 | 序列化格式 | 开源许可证 | 内部办公与分发合规性 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **M-01** | 自动语音识别 (ASR) | **Faster-Whisper Large-v3-Turbo & Small** | OpenAI / Systran | CTranslate2 (`model.bin`) | **MIT License** | ✅ 允许免费商用、修改与内部自由分发 |
| **M-02** | 词级时间戳强制对齐 | **Wav2Vec2-Base-960h (English)** | Meta AI (Facebook) | PyTorch / Safetensors | **Apache-2.0 / MIT** | ✅ 允许免费商用与内部分发 |
| **M-03** | 视频智能交互式抠像 | **SAM 2.1 (Hiera-Base-Plus & Large)** | Meta AI (`facebook/sam2.1`) | PyTorch (`weights_only`) | **Apache-2.0 License** | ✅ 允许免费商用、修改与内部分发 |
| **M-04** | 高精度边缘图像去背景 | **RMBG-2.0** | BRIA AI (`briaai/RMBG-2.0`) | `safetensors` | **CC-BY-NC-4.0 / BRIA 内部研究许可** | ✅ 允许内部非公开售卖制作与团队内部使用 |
| **M-05** | 人像法线估计与提亮修复 | **MiDaS DPT-Hybrid + MediaPipe FaceMesh** | Intel ISL / Google | PyTorch / TFLite | **MIT / Apache-2.0** | ✅ 允许免费商用与内部分发 |
| **M-06** | 空间立体声伴奏人声分离 | **Hybrid Demucs (htdemucs)** | Meta Research | PyTorch StateDict | **MIT License** | ✅ 允许免费商用与内部分发 |
| **M-07** | 人声去混响与降噪增强 | **Resemble Enhance (Stage 2)** | Resemble AI | DeepSpeed Pt (受控目录) | **MIT License** | ✅ 允许免费商用与内部分发 |

---

## 三、可选乐高扩展包模型台账 (`.ltxpack` 按需安装)

> 面向具备高配独显（RTX 12GB~16GB+）或特定进阶需求的成员按需下载安装：

| 扩展包 ID | 包含模型名称 | 研发机构 / 官方来源 | 序列化格式 | 开源许可证 | 合规说明与硬件要求 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`pack-ltx25-video`** | **LTX-2.5 22B Distilled (NVFP4)** + Dual Audio/Video VAE + 2x Latent Upscaler | Lightricks (`Lightricks/LTX-Video`) | `safetensors` | **LTX-Video Open Weights License** | ✅ 官方开放权重许可，支持内部制作与分发（需 16GB 显存） |
| **`pack-ltx25-video`** | **Gemma-4 E2B-IT / 12B Multimodal Director Encoder** | Google DeepMind | `safetensors` | **Gemma Terms of Use** | ✅ 符合 Google Gemma 开放权重使用条款 |
| **`pack-ltx25-vfx-loras`** | **LTX-2.5 IC-LoRA 7件套** (`restore`, `deblur`, `clean_plate`, `colorization`, `inpaint`, `sdr2hdr`, `refine`) | Lightricks | `safetensors` | **LTX-Video Open Weights License** | ✅ 官方配套控制适配器权重（需 16GB 显存） |
| **`pack-flux2-image`** | **FLUX.2 [klein] 4B (NVFP4/FP8)** + Qwen3-4B Text Encoder | Black Forest Labs / Alibaba Qwen | `safetensors` | **Apache-2.0 / Non-Commercial Community** | ✅ 允许内部视频首帧制作与分镜生产（需 12GB 显存） |
| **`pack-audio-tts-bgm`** | **Chatterbox TTS / Coqui XTTS-v2 / Stable Audio Open / MusicGen Small** | Resemble AI / Coqui / Stability AI / Meta | `safetensors` / `pt` | **MIT / CPML / Stability Community** | ✅ 允许内部配音与背景音乐制作（支持 CPU / GPU） |
| **`pack-pro-vision-multilang`** | **TAPNext++ (256/512) / 4xNomos2_realplksr / 多语种 MMS 词对齐** | Google DeepMind / Philip Hofmann / Meta | `pth` / `pt` | **Apache-2.0 / MIT / CC-BY-4.0** | ✅ 允许内部视频跟踪、4x 超分与多国语字幕对齐 |

---

## 四、明确排除项声明 (Compliance Exclusion Notice)

- **MiniMax H3 模型**：根据安全与合规策略要求，本项目**不包含、不引用、不分发任何 MiniMax H3 模型文件**。在打包构建器 [`打包分发.py`](./打包分发.py) 中已配置硬性拦截规则（`FORBIDDEN_PATH_KEYWORDS` 包含 `"minimax_h3"` 与 `"qwen3vl_32b_minimax"`），并在本地网关 `/v1/health` 接口中显式返回 `"minimax_h3_bundled": false` 供自动化合规审计核验。
