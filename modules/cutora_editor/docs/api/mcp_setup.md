# 片语 Studio MCP 设置

手写 stdio MCP 服务器（JSON-RPC 2.0，协议 `2025-06-18`），**不**经过 `CUTORA.bat`，不加载 torch / PySide6。所有 `tools/call` 转发到本机 API（`127.0.0.1`，默认端口 47321）。

请先打开片语 Studio，在 **设置 → 代理接口** 里启用，并保持软件运行。

## Claude Desktop

配置文件（Windows）通常在 `%APPDATA%\Claude\claude_desktop_config.json`。把 `D:\\0_Dev\\CUTORA` 换成你的安装目录：

```json
{
  "mcpServers": {
    "cutora": {
      "command": "D:\\0_Dev\\CUTORA\\cutora_mcp.bat"
    }
  }
}
```

通用 MCP 客户端同理：启动命令是 `cutora_mcp.bat`（或 `runtime\\venv\\Scripts\\python.exe -m cutora.api.mcp_server`，工作目录为仓库根）。

## 工具与资源

- 工具名 = 命令名把 `.` 换成 `_`（例如 `app.version` → `app_version`）。危险命令的 description 末尾有「需要 confirm=true」。
- 资源：
  - `cutora://state` — 当前工程摘要
  - `cutora://project/current` — 当前工程
  - `cutora://docs/commands` — `docs/api/commands.md`

## 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `tools/call` 返回 `isError`，正文是「请先打开片语 Studio 并在 设置 → 代理接口 里启用」 | 软件没开，或代理接口关着，或 token 对不上 | 打开软件，打开「代理接口」，必要时点「重新生成」 |
| Claude Desktop 里服务器立刻退出 | `cutora_mcp.bat` 找不到 venv | 先跑 `CUTORA_Install.bat` |
| stdout 出现非 JSON | 不要改 bat 往标准输出 `echo` | 日志只允许 stderr |
| `Host 不合法` / 401 | 客户端绕过 MCP 直接打 API 且头不对 | 用 MCP 转发；API 只接受 `127.0.0.1` / `localhost` 的 Host，且需要 Bearer token |
