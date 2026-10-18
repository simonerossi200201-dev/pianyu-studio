# Robota — 你的本地通用 AI 助手

> 双击桌面 **Robota** 图标 → 浏览器打开 `http://127.0.0.1:9600` → 直接说话或打字。
> 不只为 CUTORA 服务：任何项目都可以"插"进来（见下面"接入新项目"）。

## 能做什么

| 功能 | 怎么用 |
|---|---|
| 💬 聊天 | 顶部"大脑"选 **自动**：闲聊用本地 gemma4，要动手的事交给本地代理，写代码/难题交给 agy（Ultra） |
| 🔒 私密 | 消息里加 `#私密`，或选私密项目（右键表格工具）→ 强制只用本地模型，数据不出本机 |
| 🛠️ 动手干活 | 本地代理 / agy 都能调用 26 个 Robota 工具：显存、通知、项目命令、队列、Google Drive (rclone)、Studio Zero、ComfyUI、Orante、文件监控、记忆 |
| 📋 任务队列 | 右侧"添加任务"：代理任务 / 命令 / CUTORA 队列插件；可暂停、中止、重试、看日志，完成后 Windows 通知 |
| 🎤 语音 | 🎤 按一下说话、再按一下结束 → 本地 Whisper 识别；勾"朗读回复"→ 本地 Kokoro 朗读（英语、意大利语）。见 [robota/voice/README.md](robota/voice/README.md) |
| 🎓 语言导师 | 大脑选"导师"，右侧填目标语言 → 陪练；"把本次陪练记进学习档案"会把错误和新词存进记忆 |
| 🧠 记忆 | `data\memory\*.md`（sop / habits / tutor…），代理会读写；你也可以直接编辑 |
| 🖥️ 显存 | 右侧"释放显存"：卸载 Ollama 模型 + 语音模型（跑 ComfyUI 前用） |

## 大脑分级

| 级别 | 是什么 | 什么时候用 |
|---|---|---|
| L1 本地 | Ollama：gemma4:12b（快）/ muse-glimmer（强） | 默认；私密内容只走这里 |
| L2 外部 | Mistral、DeepL、Nebius、Groq…（OpenAI 兼容） | 在 `secrets\<名字>.key` 放 key 后自动启用，见 `config\providers.toml` |
| L3 agy | Antigravity CLI，用你的 Google Ultra 订阅 | 写代码、复杂任务、本地失败时自动升级 |

## 文件地图

```
Robota.bat                启动（Robota.bat stop = 停止）
config\robota.toml        端口、默认模型、导师语言
config\projects\*.toml    已接入的项目（cutora / orante / studio / table）
config\agy_settings.json  agy 权限白名单/黑名单的备份
robota\console\           控制台（网页 + API）
robota\brains\            三级大脑
robota\router.py          自动选大脑
robota\tasks\             任务队列
robota\mcp_server.py      26 个工具（MCP），robota\tools\ 是实现
robota\voice\             语音服务
data\                     对话、队列、记忆、用量（不进 git）
logs\                     日志
RUN_AGY_QUEUE.bat         CUTORA 队列：用 agy 代替 Grok（原 RUN_GROK_QUEUE.bat 未动）
```

## 接入新项目

在 `config\projects\` 新建一个 `xxx.toml`（照抄 `studio.toml` 改路径和说明），重启 Robota 即可。`private = true` 表示该项目只用本地模型。

## 安全

- 所有服务只监听 `127.0.0.1`。
- 命令先过 `robota\safety.py` 黑名单（删除、格式化、git push/reset、下载执行等一律拒绝）。
- agy 无头模式只放行白名单命令，被拒的操作会终止本次任务而不是硬闯。
- 不会修改你现有项目的代码；CUTORA 的黄金基线、Orante 的台账/manifest 只读。

## 测试

```
runtime\venv\Scripts\python.exe tests\test_tools.py
runtime\venv\Scripts\python.exe tests\test_console.py local agent agy auto queue
runtime\venv\Scripts\python.exe tests\voice\test_voice.py        (需先启动语音服务)
```

更多：[架构.md](架构.md)（模块接口）· [计划书.md](计划书.md) · [SOP.md](SOP.md) · [docs/工具调研.md](docs/工具调研.md)
