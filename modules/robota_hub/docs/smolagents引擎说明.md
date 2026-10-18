# smolagents 本地代理引擎（2026-10-13 接入）

## 是什么
Robota 的“本地代理”（路由 local_agent）现在默认用 **smolagents**（Hugging Face，Apache 2.0）当发动机：
Gemma 每一步判断下一步该做什么 → smolagents 调用 Robota 原有的 26 个工具 → 结果交回给 Gemma → 直到完成。
- 模式：ToolCallingAgent（工具调用）。CodeAgent（写 Python）用 gemma4:12b 测试时经常格式出错、非常慢，所以不用。
- 只连本机 Ollama（http://localhost:11434/v1），不联网，私密模式安全。
- 每一次工具调用，所有文字参数都先经过 robota/safety.py 检查，命中黑名单直接拒绝。
- 代码：robota/brains/smol.py；路由改动在 robota/router.py 的 local_agent 分支。

## 对比测试（同为 gemma4:12b，3 个任务）
| 任务 | 原引擎(Antigravity SDK) | smolagents |
|---|---|---|
| 查显存 | 64 秒，正确 | 29 秒，正确 |
| 列项目+在 studio 数条目 | 18 秒，**没调用工具，编了个数字(24)** | 18 秒，调用工具，正确(36) |
| 删除文件（应被拦截） | 54 秒，被拦截 | 29 秒，被拦截 |

## 设置（config/robota.toml 的 [router]）
- agent_engine = "smol"   ← 改成 "antigravity" 就切回原引擎
- smol_model = "gemma4:12b"   ← 可改 "gemma4:26b" 或 "muse-glimmer"（更聪明但更慢）
- smol_max_steps = 10
smolagents 出错时会自动退回原引擎；项目带额外 MCP 服务器时也用原引擎。
自动模式的默认大脑（default_brain = "agy"）没有改。

## 顺手修的安全漏洞
safety.py 原来只拦 `rm -rf`，单独的 `rm 文件`、`ri 文件`（PowerShell 删除别名）能通过。已加上拦截。

## 备份
改动前完整备份：D:\0_LLM\backups\Robota_20261013_1808（不含 runtime\venv）。
彻底还原：把 router.py、safety.py、config\robota.toml 拷回去，删除 robota\brains\smol.py。
