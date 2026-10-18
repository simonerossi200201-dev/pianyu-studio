# P1 报告 — Antigravity SDK 验证

时间：2026-10-05 02:47 · SDK 版本：`google-antigravity 0.1.20`（官方 PyPI）

## 结果

| 测试 | 结果 |
|---|---|
| 安装 SDK | ✅ 装在 `runtime\venv`（Python 3.12.7） |
| 云端模式（`LocalAgentConfig`，Gemini 模型） | ❌ **需要 Gemini API key**：`A Gemini API key is required. Set it via GEMINI_API_KEY...` |
| **本地模式**（`LocalOpenAIAgentConfig` + Ollama） | ✅ **能用**。gemma4:12b 在低功耗下 26 秒完成"新建文件 → 读回 → 汇报"，正确调用了 `edit_file` 和 `view_file` |
| 权限策略 | ✅ SDK 自带：`workspace_only`、`confirm_run_command`、`ask_user`、`deny_all`、`allow` 等 |

## 结论

1. **SDK 不走 Ultra 订阅额度**。它的云端模式只能用 Gemini API key（Google AI Studio，独立计费，有免费层）或 Vertex AI。
2. **意外收获**：SDK 可以直接接本地 Ollama。也就是说，**本地大脑和云端大脑可以用同一套代理框架**（同样的工具、权限策略、MCP、日志），只需要换模型配置。→ **可以不装 Goose**，架构更简单、更统一。
3. 要用到 Ultra 订阅额度，需要走 **Antigravity CLI（`agy`）**。本机还没有安装，它是否支持类似 `grok --prompt-file` 的无人值守模式也有待确认。

## 第 3 层的可选方案

| 方案 | 费用 | 隐私 | 自动化程度 |
|---|---|---|---|
| A. 安装 `agy` CLI，用 Ultra 账号登录 | 包含在订阅里 | 数据发给 Google | 取决于 CLI 是否支持无人值守模式（待验证） |
| B. Gemini API key，免费层 | 免费，有频率限制 | ⚠️ 免费层数据可能被用于改进产品 | 完全支持（SDK） |
| C. Gemini API key，付费 | 按用量计费 | 付费层不用于训练 | 完全支持（SDK） |

## 测试文件
- `tests\p1_probe_auth.py`：云端模式的鉴权测试
- `tests\p1_probe_local.py`：本地模式的测试
