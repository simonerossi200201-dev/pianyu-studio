# 规格 06 · Grok 引擎 `orante/engines/grok.py`

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先完整读 `specs/00-architecture.md`，再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**，需要就拷到 `orante/legacy/`。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的、需要后端补的」写到 `specs/reports/<本文件名>.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。

## 目标

把老脚本 `..\下载图片\core\grok_pipe.py`（2,900 行，踩过很多坑）里的浏览器操作，包成 `engines/base.Engine` 接口，让调度器像用 Flow 一样用 Grok。**先读 `orante/engines/base.py`、`orante/engines/flow_api.py`（Flow 的实现，照它的风格）、`orante/engines/chrome.py`、`orante/services/generate.py`（调用方）。**

老代码原样拷到 `orante/legacy/grok_pipe.py`、`orante/legacy/grok_batch.py`（以及它们 import 的兄弟模块，比如 flow_shots、video_types、desc_files——只拷需要的），不改。你的 `grok.py` 通过 `paths.add_legacy_to_sys_path()` 后 `import grok_pipe` 来用它的函数。

## 映射

| Engine 方法 | 用 grok_pipe 里的什么 |
|---|---|
| `connect()` | `chrome.launch(port, profile, "https://grok.com/imagine")` + `chrome.Browser(port)`；老代码的 `connect(pw)` 用的是 `CDP_URL` 全局——**不要用它**，端口要从 `self.account['port']` 来。登录判断：页面上有没有登录按钮 / 是否在 `accounts.x.com`（参考 `grok_batch.pass_tos_gate` 和 `verify_tab`） |
| `submit(seg, image, prompt, duration)` | `prepare(ctx, row, folder)` → `click_submit(page)` → `capture_anchor(page)`。老函数吃的是 `row` 字典（键：`图片`、`完整提示词`、`时长` 等——去 `_collect/_build_prompts` 里看 row 有哪些键），你在这里现拼一个 row。返回 `gen_id = postId`（把 conversation 也记下来：`self._conv[postId]`），并把那个 page 留在 `self._pages[postId]`（老流程是一段一个标签页） |
| `poll(gen_ids)` | 对每个 postId：`probe(page, post=...)` → `已生成` → `{state:'done', url}`；`生成中` → pending；`失败` → failed（原因原样带回）。页面丢了就 `goto_own_post(page, post, conv)` 重开 |
| `download(gen_id, dest)` | `download_one(ctx, row, dest)`（row 里要有 `视频URL`、`postId`、`视频来源`）。下完把那个标签页关掉 |
| `capacity()` | `account.max_concurrent - len(inflight)`；老代码 `gen_max=4` 是经验值，默认 4 |
| `gen_images` | 不实现（Grok 不负责生图），抛 `NotImplementedError` |
| `BlockedImage` | `prepare` 里如果传图被拒（老代码里搜 "违规" / "policy" / "moderat" 的分支），抛 `BlockedImage` |

## 规矩

- 时长：Grok 的 6 / 10 / 15s 由 `duration` 参数决定，用 `grok_batch.set_params` 设（看它的签名）。
- 老代码大量 `print/log` 到自己的日志文件——保留，但同时把关键节点用 `self.log()` 写一行（提交成功 / 抓到 postId / 出片 / 失败原因）。
- 不要碰老代码的台账（`Grok台账.json`）——Orante 2.0 的状态只在 manifest。
- 所有 `time.sleep` 累计不要超过老代码的等待策略；不要新加"保险等待"。
- 异常一律转成 `OranteError`（人话 + hint）或 `BlockedImage` / `EngineUnavailable`。

## 验收

`tools/test_grok_engine.py`：用 `accounts.list_all()` 里第一个 grok 账号，`connect()` → `health()` 打印；**不真的提交**（除非命令行带 `--live 图片路径 "提示词"`），live 时提交一段 6s、轮询到出片、下载到 `data/tmp/`。
