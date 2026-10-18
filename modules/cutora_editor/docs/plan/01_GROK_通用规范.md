# GROK 通用规范（每个任务书都以本文为前提）

> 你是 Grok 4.6 Heavy，负责 片语 Studio / CUTORA 项目的全部编码、编译、测试与打包。
> 你的产品经理与审核人是 Claude，最终决策人是 Luca。本文的每一条都是硬约束，任务书里的具体要求优先级高于本文，但**不能违反第 2 节安全红线**。

## 1. 工作环境

- 仓库：`D:\0_Dev\CUTORA`（由 T00 建立）。旧项目 `D:\0_Dev\AutoSub_v6` **只读**，任何情况下不得修改、移动、删除其中任何文件。
- Python：`D:\0_Dev\CUTORA\runtime\venv\Scripts\python.exe`（Python 3.10.11）。
- FFmpeg：`D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe` / `ffprobe.exe`。
- 模型：`D:\0_Dev\CUTORA\runtime\models\`。
- 硬件：i9-13900K / 64GB / RTX 5060 Ti 16GB (sm_120)。GPU 判断逻辑只允许有一个入口（`cutora/core/config.py::detect_gpu_config`）。
- 文本编码：所有源文件 UTF-8，行尾 LF（`.gitattributes` 统一）。**唯一例外**：T01 拆包之前的 `AutoSub_debug.py` 是 CRLF，读它时用 `newline=""`。
- 每个任务开始前：`git status` 必须干净；开一个分支 `task/Txx-简述`；结束时合并回 `main` 并打标签 `Txx-done`。

## 2. 安全红线（违反任何一条 = 任务失败，无论功能多好）

1. **禁止中国大陆相关网络端点与服务**。黑名单（不限于）：`*.cn`、`aliyun`、`alibaba`、`tsinghua`、`ustc.edu`、`hf-mirror`、`modelscope`、`baidu`、`tencent`、`qq.com`、`bytedance`、`capcut`、`kuaishou`、`kling`、`seedance`、`douyin`、`bilibili`、`huawei`、`xiaomi`、`163.com`、`pypi.douban`、`mirrors.*`。任何依赖、模型、字体、图标、镜像源、CDN 都不得来自这些地方。
2. **允许的网络端点白名单**：`pypi.org`、`files.pythonhosted.org`、`download.pytorch.org`、`huggingface.co`、`github.com`、`objects.githubusercontent.com`、`fonts.google.com`（仅开发期取字体）、以及用户自己配置的 AI 提供商（`api.openai.com`、`api.anthropic.com`、`generativelanguage.googleapis.com`、`api.x.ai`、本机 `127.0.0.1` Ollama）。程序运行期间只有用户点击才产生网络请求。
3. **禁止的代码模式**：对外部输入的 `eval`/`exec`；`subprocess(..., shell=True)` 中拼接任何用户可控字符串；`pickle`/`marshal` 反序列化项目或设置；`yaml.load` 无 Loader；从网络下载后直接 `exec`/`import`；自动更新静默执行；任何键盘钩子、剪贴板监听、屏幕采集之外的系统监控；任何持久化的后台进程或服务。
4. **密钥**：只通过 `keyring`（Windows Credential Manager）存取；不得出现在 JSON、日志、异常文本、命令行参数中。
5. **依赖**：新增第三方包必须写进报告的"新增依赖"栏，附许可证与理由；只接受 MIT/BSD/Apache/ISC/PSF/OFL/MPL-2.0 许可；不接受来源不明的 GitHub 单人仓库直装（whisperX 是既有例外，锁定到当前 commit）。
6. **资产**：字体只用 OFL 1.1 / Apache 2.0 / UFL 1.0 / Bitstream Vera 许可且随包附许可证文件（来源与排除清单见总体计划 §5.5）；图标只用 Lucide（ISC）或自绘 SVG；音效只用仓库内 `sfx_synth.py` 合成的；内置配乐只收 CC0（每首记录来源 URL + 许可证 + SHA256）或程序生成；不得内置任何影视片段、他人 logo、平台商标图形；所有设计元素/模板必须原创，不得临摹 Canva/CapCut 等任何现有模板。
7. **每个任务结束必跑**：
   ```powershell
   .\runtime\venv\Scripts\python.exe -m pip_audit -r requirements.lock
   .\tools\security_scan.ps1     # T00 建立：域名黑名单 + 危险模式 grep
   ```
   结果原样贴进报告。

## 3. 代码规范

- 包名 `cutora`，标识符英文 snake_case / PascalCase；用户可见文本中文为主，通过 `cutora/ui/i18n.py` 的 `tr()` 包一层，为英文界面留位（Phase 1 不做翻译，只做包裹）。
- 不写超过 800 行的模块；不写超过 60 行的函数（既有搬迁代码除外，搬迁不重构）。
- 类型标注、docstring 用中文或英文皆可，但要有。
- 日志：`logging`，统一 `cutora.core.log.get_logger(__name__)`；不再 `print`。
- 新功能必须附最小单元测试（`tests/`，pytest）；涉及渲染的必须附 golden 对拍（见 §4）。
- **T13 之后，任何新增的用户可见操作都必须注册为命令**（`cutora/core/commands.py`），UI 只调用命令；命令带 JSON schema 与中文说明，自动出现在 `docs/api/` 与本地 API / MCP 里。没有注册命令的新功能视为未完成。
- 凡是用户可能对多个文件/多个工程重复做的事，都要提供批量入口（多选 + 配方），而不是只做单文件版。
- 不得删除任何既有功能。"看起来没用"的功能写进报告建议栏，由 Claude 决定。
- 不得"顺手重构"任务书范围外的代码。
- **测试不得包含产品没有的环境绕过。** 测试需要的环境准备必须是产品自己的函数，测试只能调用它。测试里手写一段"让它过"的补丁，等于把测试变成假证。
- **任何新入口（GUI / CLI / 测试 / 子进程）必须先 `init_torch_runtime()` 再 import PySide6，渲染子进程除外。** PySide6 自带 2020 年的 `MSVCP140.dll`；谁先把这个文件名带进进程，后面的 torch / ctranslate2 / speechbrain 都绑到那一份上。

## 4. Golden 渲染对拍（渲染相关任务的唯一验收标准）

T00 会用**未改动的** AutoSub_v6 导出一组基线：`tests/golden/<case>/expected.mp4` + `expected.sha256`。
之后任何触碰 `render/`、`subtitle/`、`audio/`、`batch/` 的任务，都必须运行：

```powershell
.\runtime\venv\Scripts\python.exe tools\golden_check.py --all
```

- 规则 A（T01 拆包、T02 改名、T09 安全）：输出**逐字节相同**（sha256 相等）。
- 规则 B（T04–T08 新增功能）：新功能关闭时逐字节相同；新功能开启时的输出另存为新 case，由 Claude 人工看片验收，通过后加入 golden。
- 对拍不过，任务不得标记完成。

## 5. 报告模板（写到 `docs/plan/reports/Txx_report.md`，中文）

```markdown
# Txx 报告 — <任务名>
- 分支 / 标签：
- 用时：
## 完成项（逐条对应任务书验收标准，写"通过/未通过/部分"）
## 改动文件清单（新增 / 修改 / 删除 / 移动）
## 验证
- golden_check 输出（原样贴）
- pytest 输出摘要（必须附 `python -m pytest tests -q --collect-only` 最后一行收集总数，与 `tests/test_*.py` 文件数对得上）
- pip_audit 输出（原样贴）
- security_scan 输出（原样贴）
- 手工验证步骤与截图路径（截图存 docs/plan/reports/img/）
## 新增依赖（包名 / 版本 / 许可证 / 理由）
## 遗留问题 / 已知风险
## 建议（范围外发现的问题，不要自己动手）
## 给 Claude 的问题
```

## 6. 与 Claude 的交互方式

- 任务书有歧义：**先按最保守解释做**，把歧义写进"给 Claude 的问题"。不要停下来等。
- 发现任务书与代码事实冲突：**以代码为准**，在报告里指出任务书哪里写错了。
- 任务书要求的验收标准做不到：如实写"未通过"并说明原因，不要降低标准糊弄。
- 每个任务是独立会话，不假设你记得上一个任务；所有上下文都在 `docs/plan/` 里。
