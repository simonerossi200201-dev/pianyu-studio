# T00 — 建立 CUTORA 仓库、运行环境与 golden 基线

> 前提：先读 `docs/plan/01_GROK_通用规范.md`（如果 `D:\0_Dev\CUTORA` 还不存在，本任务书自带的 §2 规则同样适用）。
> 本任务**不改任何一行业务代码**。目的只有三个：把项目安全地复制出来、让它在新位置跑起来、留下"改版前"的渲染基线。

## 1. 目标

1. `D:\0_Dev\CUTORA` 成为一个可独立运行的副本，`D:\0_Dev\AutoSub_v6` 一字不动。
2. 新 venv、模型、ffmpeg 全部在 `CUTORA\runtime\` 下，路径不依赖旧目录。
3. `tests\golden\` 下有一组用**旧版 AutoSub_v6** 生成的基线输出与哈希。
4. `tools\golden_check.py` 与 `tools\security_scan.ps1` 可用。
5. Git 仓库初始化，`.gitignore` 修正（不再忽略 `*.json` / `*.txt`）。

## 2. 步骤

### 2.1 复制
```powershell
robocopy D:\0_Dev\AutoSub_v6 D:\0_Dev\CUTORA /E /XD venv_autosub __pycache__ .git _archive _backup_20260804_113708 _to_delete /XF *.bak* *.bak-* x_AutoSub_v4.0*.py python-3.10.11-amd64.exe
```
- 复制后目录结构先保持 `CUTORA\core\...`（T01 才拆包，本任务不动结构）。
- 把 `CUTORA\core\ffmpeg.exe`、`ffprobe.exe` 移到 `CUTORA\runtime\ffmpeg\`；把 `CUTORA\core\models\` 移到 `CUTORA\runtime\models\`；`CUTORA\core\fonts\` 保持原位（T09 处理）。
- 在 `core\config.py` 里把 `local_ffmpeg` / `local_ffprobe` 的查找路径改为先查 `..\runtime\ffmpeg\`，再查旧位置，再查 PATH；`models` 路径同理指到 `..\runtime\models\`（查 `download_full_models.py` 与 `model_paths.json` 里的路径写法，确保一致）。**这是本任务唯一允许的代码改动**，且改动后要在报告里贴 diff。
- 删除复制过来的 `.bak` 残留、`dump_keys.py`/`find_keys.py` 之类的调试脚本（如果有）。`AutoSub_debug.py` 里如有硬编码的 `D:\0_Dev\AutoSub_v6` 字符串，grep 出来列在报告里，**不要改**（T02 统一处理）。

### 2.2 运行环境
- 在 `CUTORA\runtime\venv` 用 Python 3.10.11 新建 venv。
- 依赖来源**只允许** `https://pypi.org/simple` 与 `https://download.pytorch.org/whl/cu128`。
- 先以旧机器上实际可用的包为准：在旧 venv 里 `pip freeze > D:\0_Dev\CUTORA\build\freeze_from_v6.txt`（这是读取旧目录，不是修改），据此在新 venv 复现同样版本（torch 用 cu128 官方 index）。whisperX 装 `CUTORA\core\whisperX` 本地目录（`--no-deps --no-build-isolation`）。
- 装完运行 `python -c "import torch, whisperx, faster_whisper, PyQt6, cv2; print(torch.cuda.is_available(), torch.cuda.get_device_capability())"`，要求 `True (12, 0)`。
- 生成 `requirements.lock`：`pip install pip-tools`，`pip-compile --generate-hashes --output-file requirements.lock requirements.in`（`requirements.in` 由你从 freeze 整理，只列直接依赖）。锁文件里如有任何非白名单 index URL，任务失败。
- 写 `CUTORA.bat`（等价旧 `2_Launcher.bat`，指向新 venv 与 `core\AutoSub_debug.py`）、`CUTORA_Install.bat`（从锁文件安装，`--require-hashes`）。旧的 `1_Install.bat`…`4_*.bat` 删除。

### 2.3 Golden 基线（用旧版生成！）
用 **`D:\0_Dev\AutoSub_v6`** 原封不动的程序生成，而不是新副本：

| case | 内容 | 目的 |
|---|---|---|
| `g01_vertical_basic` | 1 条 20 秒竖屏视频 + 自动字幕（英语）+ 默认风格 | 最基本路径 |
| `g02_vertical_fx` | 同上 + 逐字高亮 + 弹力条 + 光晕 + 关键词规则 + 一个文本覆层 + 一个图形覆层 + 一个转场 | 渲染全覆盖 |
| `g03_horizontal_it` | 1 条 30 秒横屏意大利语视频 + 空间立体声(stage) + 音效铺设 | 音频链路 |
| `g04_stitch` | 片段智能拼接：一个含 3 组片段的文件夹 + 文案（用 `拼接切口精修与多文件夹_0901.md` 描述的格式） | 批量流水线 |
| `g05_scatter_insert` | 片段散落 + 批量插图各一次 | 批量流水线 |
| `g06_parallel` | g01+g02+g03 三个工程并行导出 | 并行 = 串行 |
| `g07_cli` | `autosub_cli.py` 无头跑 g01 | CLI |

- 素材：自己录制或用 FFmpeg 合成（`testsrc2` + `sine` + 一段用 Windows SAPI 朗读的英语/意大利语语音——SAPI 是系统自带，无版权问题）。**不要**从网上下任何视频。素材放 `tests\golden\_media\`，总大小控制在 200 MB 内。
- 每个 case 保存：工程文件（`.asproj`）、输入素材相对路径、导出参数、`expected.mp4`（或多文件）、`expected.sha256`、当时的日志。
- 因为 AI 识别在 GPU 上可能有非确定性，**golden 的字幕结果要冻结**：先跑一次识别，把识别结果保存进工程文件，之后对拍时只做渲染不做识别。在 `tests\golden\README.md` 里写清楚这一点。

### 2.4 工具
- `tools\golden_check.py --all | --case g02`：在新副本里加载每个 case 的工程、按保存的参数导出到临时目录、算 sha256、与 expected 对比，打印表格，任何不等返回非零退出码。并行 case 要同时验证"并行 = 串行"。
- `tools\security_scan.ps1`：对仓库（排除 `runtime\`、`fonts\`、`tests\golden\_media\`）grep 以下内容并输出命中行；命中数 > 0 返回非零：
  - 域名黑名单（见通用规范 §2.1）
  - `eval(`、`exec(`、`shell=True`、`pickle.load`、`marshal.load`、`yaml.load(` 不带 Loader、`os.system(`
  - 疑似密钥：`api_key\s*[:=]\s*["'][A-Za-z0-9_\-]{16,}`
- `tools\report_template.md`：复制通用规范 §5 的模板。

### 2.5 Git
- `git init`，`.gitattributes`：`* text=auto eol=lf`、`*.bat text eol=crlf`、`*.ps1 text eol=crlf`、二进制列出。
- `.gitignore`：`runtime/`、`__pycache__/`、`*.pyc`、`user_data/`、`tests/golden/_media/`、`tests/golden/**/expected.mp4`、`build/dist/`、`.env*`。**不要**忽略 `*.json` / `*.txt`。
- 首个提交：`chore: bootstrap CUTORA from AutoSub_v6 (no logic change)`。

## 3. 验收标准

- [ ] `D:\0_Dev\AutoSub_v6` 的文件数、总字节数与开始前一致（报告里贴前后 `Get-ChildItem -Recurse | Measure-Object -Sum Length` 的结果）。
- [ ] `CUTORA.bat` 能启动程序，GPU 显示可用，能完成一次自动字幕与导出。
- [ ] `tools\golden_check.py --all` 在**新副本**上全部通过（此时只有路径改动，必须逐字节一致）。
- [ ] `requirements.lock` 带哈希，index 只有白名单。
- [ ] `security_scan.ps1` 报告：预期会命中 `user_data\app_settings.json` 里的密钥字段名（空值）——列出即可，T09 处理。其他命中必须为零或逐条解释。
- [ ] 报告写入 `docs\plan\reports\T00_report.md`，并把 `_CUTORA_PLAN\` 整个目录复制为 `CUTORA\docs\plan\`。

## 4. 不要做

- 不拆文件、不改名、不改 UI、不升级任何依赖版本、不"顺手修 bug"。
- 不联网下载任何素材、字体、模型（模型已在旧目录，复制即可）。
