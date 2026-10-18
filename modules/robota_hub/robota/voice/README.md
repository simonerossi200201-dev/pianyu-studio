# Robota 语音服务

**端口**：只监听本机 `127.0.0.1:9611`。控制台第一次用到语音（🎤 或"朗读回复"）时会自动启动它，不用手动开。

## 工作方式

```
浏览器 🎤(webm) → 控制台 9600 /api/voice/* → 网关 9611 (Robota 自己的 venv)
                                         ├─ ASR 进程：local_tts\venv_asr    faster-whisper large-v3-turbo
                                         └─ TTS 进程：local_tts\venv_kokoro Kokoro-82M
```

- 两个工作进程**第一次用到时才加载**，之后常驻（模型热着，延迟很低）。
- `POST /unload`（控制台"释放显存"也可调）会结束这两个进程，显存立刻释放。
- 只**只读地使用**你现有的 `D:\0_Dev\local_tts`（不安装、不修改）。离线模式（`HF_HUB_OFFLINE=1`），不会偷偷下载模型。
- faster-whisper 用 GPU 需要的 cuBLAS/cuDNN，是从 `venv_kokoro` 里 torch 自带的 DLL 目录借来用的（只是加进搜索路径）。没有它就自动降级为 CPU（慢约 15 倍，仍可用）。

## 实测（RTX 5060 Ti）

| 项目 | 冷启动 | 热状态 |
|---|---|---|
| TTS 英语/意大利语（5 秒句子） | ~5 秒（首次加载） | 0.1 秒 |
| STT（5 秒语音，GPU） | ~2.5 秒（首次加载） | 0.2 秒 |
| STT（CPU 降级时） | ~14 秒 | ~4 秒 |

识别结果与原文几乎一致（英语、意大利语都测过）。

## 语言

- **听（STT）**：Whisper 支持约 99 种语言，自动识别语种（中文、意大利语、英语、德语、法语……都可以）。
- **说（TTS）**：本地已下载的 Kokoro 声音只有 4 个：`af_heart`、`am_michael`（美式英语），`if_sara`、`im_nicola`（意大利语）。
  - 其他语言（例如中文）本地没有声音时，控制台会自动改用浏览器自带的系统朗读。
  - 想加声音（如西班牙语、法语、日语、中文）：从 Kokoro 官方仓库 `hexgrad/Kokoro-82M` 下载对应的 `voices\*.pt`，需要你同意后再做。

## 手动启动 / 测试

```
robota\voice\start_voice.bat
runtime\venv\Scripts\python.exe tests\voice\test_voice.py
```

日志：`logs\voice.log`、`logs\voice_asr.log`、`logs\voice_tts.log`。
