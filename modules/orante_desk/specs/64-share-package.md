# 64 · 打包分发：一个干净的 Orante，别人解压就能用

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/26-preset-export-import.md`
> 和它的报告（预设导出/导入已经做完，本规格建在它上面，**不要重复造**），再读
> `orante/paths.py`、`orante/config.py`（`DEFAULTS`）、`orante/accounts.py`、`orante/db.py`、
> `orante/engines/chrome.py`（`chrome_exe` / `launch` / `connect_over_cdp`）、
> `orante/server.py`（设置相关接口、静态目录）、`Orante2.bat`、`tools/Make-Clean-Build.bat`、
> `ui/js/store.js` 与 `ui/js/drawers/`（抽屉怎么写的），再读本规格。
> 改前备份 `*.bak-0914share`。报告写到 `specs/reports/64-share-package.md`。中文注释。
> **本规格里的「泄露体检」是硬闸，不过就不许出包。** 不许为了让体检过而放宽规则。

## 目标

把 Orante 交给别人，满足四条：

1. **最便捷** —— 对方拿到一个 zip，解压，双击一个 bat，就进到向导里。
2. **最完整** —— 代码、界面、描述语、说明书齐全，不缺件、不半残。
3. **不泄露我的数据** —— 账号、登录态、Key、项目地址、文案、跑批日志、本机路径，一个字都不许带出去。
4. **对方好填自己的账号** —— 不用改文件、不用看代码，界面上一步步填完就能开工。

---

## 0. 先说三个必须知道的事实（别想当然）

- **这个仓库没有 git。** 目录里靠 `*.bak-时间戳` 留档，`.git` 不存在。
  所以**不能用 `git archive` 出包**，必须自己按白名单拷。
- **现在的 `tools/Make-Clean-Build.bat` 是个地雷。** 它 `cd /d "%~dp0.."` 之后
  直接 `del` 掉 `data\orante.db`、`rmdir /s /q data\chrome`、`data\logs` ——
  **删的是我自己正在用的那一份**，不是副本。谁哪天手滑双击它，账号、登录态、
  全部任务当场没了。本规格第 E 节处理它。
- **不需要 `playwright install`。** `chrome.py` 走的是本机装的 Google Chrome
  （`--remote-debugging-port` + `--user-data-dir`），playwright 只 `connect_over_cdp` 接上去，
  不下载它自带的浏览器。所以对方的门槛只有：Windows + Python 3.10+ + Google Chrome。
  **说明书里要把这三条写在最前面**，别让人白等 300 MB 的下载。

---

## A. 出包脚本：白名单拷贝到别处，**绝不动源目录**

新增 `tools/make_share.py` + `tools/15-Make-Share-Package.bat`。

### A1. 铁律

- 出包目录默认 `%~dp0..\..\_share\Orante-分发版-<版本>-<YYYYMMDD>\`
  （即 `D:\0_Dev\_share\...`，**在 Orante 外面**），可用 `--out` 覆盖。
- **只读源目录，只写出包目录。** 脚本里不许出现任何对源目录的 `del` / `rmdir` /
  `shutil.rmtree` / `open(..., "w")`。这条写成一个 grep 断言进测试。
- 出包目录已存在 → 先整个删掉重建（删的是 `_share` 下的副本，安全），但要先确认
  它确实在 `--out` 底下、且路径里含 `Orante-分发版`，否则报错退出。

### A2. 带什么（白名单，**不是**黑名单）

黑名单已经证明不可靠：26 号那份漏掉了 `orante/legacy/跑批日志.txt`、
`data/clip-dump.html`、`data/sha-cache.json`、`tools/grok-account.txt`。所以改成白名单。

| 带 | 具体 |
|---|---|
| 程序包 | `orante/**/*.py`（排除 `*.bak-*`、`__pycache__`） |
| 老模块 | `orante/legacy/` 下的 `.py` 与 `coverage_library.json`、`prompt_defaults.json`、`video_types.json`、`flow_shots.json`、`grok_dur_probe.json`、`wenan_words.txt`（排除 `*.bak-*`、`__pycache__`、**`跑批日志.txt`**） |
| 界面 | `ui/**`（排除 `*.bak-*`） |
| 多语言 | `orante/i18n/**` |
| 启动 | `Orante2.bat`、`Orante.exe`、`Orante.command`、`requirements.txt` |
| 工具 | 只带对方真用得上的那几个：`tools/13-Orante-Debug.bat`（出问题时看日志）、`tools/Install-Shortcuts.bat`、`tools/build_icons.py`。**其余一个都不带。** |
| 说明 | `说明文档/使用说明.md`（**新写的那一份**，见 D 节） |
| 空目录 | `data\`、`data\logs\`、`data\chrome\`、`workspace\`（各放一个 `.keep`） |
| 清单 | `分发清单.txt`（见 A4） |

**明确不带**（逐条写进脚本的注释里，说明为什么）：

- `app\`、`bridge\`、`web\`、`Orante.bat` —— 这是 Orante 1.0 的旧服务端和旧启动器，
  已经被 `orante\` + `ui\` 取代。带上去只会让对方双击错文件然后看到一个坏掉的旧界面。
- `specs\`、`specs\reports\` —— 里面有我的账号名、额度数字、任务文案、
  `reports/img/` 下还有我成片的截图。**这是最容易被忽略的一个泄露口。**
- `data\` 下任何文件 —— `orante.db`（账号表：名字、端口、`profile` 绝对路径、
  Flow 项目地址；settings 表：`gemini.api_key`、`workspace.root`；tasks 表：全部文案）、
  `chrome\`（登录态 cookie，**等于把我的 Google 和 Grok 账号送人**）、`google\`、
  `logs\`、`capture\`（抓包里有 XSRF / session / reCAPTCHA token）、
  `ahash-cache.json`、`sha-cache.json`、`used_images.json`、`flow_media_cache.json`、
  `marks.json`、`lens_job.json`、`tmp_prompt.txt`、`clip-dump.html`、`tmp\`。
- `tools\grok-account.txt`、`grok-queue.txt`、`grok-done.txt`、`grok-post-dump.txt`、
  `tools\test_*.py`、`tools\probe*.py`、`tools\_*.py`、`tools\*.html` —— 开发用的。
- `.venv\`、`.pytest_cache\`、任何 `__pycache__\`、任何 `*.bak-*`。
- `说明文档\Orante-2.0-规划书.md`、`Flow接口协议-0906.md` —— 内部文档，不必给。

### A3. 出厂状态

- **不带数据库。** 第一次启动由 `db` 建表 → 天然干净出厂（26 号验收第 2 条已经验过这条路）。
  脚本里**不要**为了「让它看起来有东西」而塞一个预置 db。
- `config.DEFAULTS` 里 `workspace.root` 已经是 `paths.DEFAULT_WORKSPACE`（`ROOT\workspace`）、
  `gemini.api_key` 已经是空串，这两条对。**再把 `DEFAULTS` 全表过一遍**，任何默认值
  只要长得像本机路径（含 `D:\`、`C:\Users\`、`下载图片`、`1_PROJECT`）就是 bug，报告里点名。
- `accounts.import_legacy_lanes()` 在分发版上必然找不到老流水线目录，
  现在会抛「这台机器上没有老流水线目录」，文案已经对（「分发版不需要这一步」）。
  **但界面上那个按钮要在找不到时直接隐藏**，别让对方看见一个点了就报错的按钮。

### A4. `分发清单.txt`

出包目录根下生成，纯文本：

    Orante 2.0 分发版
    版本：2.0.0   出包时间：2026-09-14 15:20   文件数：412   合计：18.3 MB

    <相对路径>  <字节数>  <sha256 前 16 位>
    ...

`orante/__init__.py` 加 `__version__ = "2.0.0"`，脚本和界面「关于」都从这里读，
只留这一处定义。对方可以对着清单核一遍，也让「保证不出错」这句话有据可查。

---

## B. 泄露体检 `tools/scan_share_leak.py` —— 硬闸

**出包脚本在打 zip 之前必须调它；它不返回 0，就不许有 zip。**

### B1. 怎么扫

对出包目录下**每一个文件**（文本按 utf-8/gbk 两种编码试读；二进制按字节找 ASCII 串）
以及**每一个文件名/目录名**，找下面这些。命中就打印 `文件:行号: 命中的那一行`（长行截断 200 字），
最后汇总数量并 `sys.exit(1)`。

1. **我的账号名**——**从真实库现读**：`SELECT name FROM accounts`，再加
   `extra` 里的 `account` 字段。这样以后加了新账号也不会漏，名单不会过期。
2. **项目地址 / 会话 id**：`flow.google.com/project/`、`labs.google`、
   `grok.com/imagine/post/`、`conversation=`、形如 UUID 的 36 位串。
3. **Key / token**：`AIza`、`sk-`、`gsk_`、`xai-`、`Bearer `、`XSRF`、`__Secure-`、
   `SAPISID`、`encrypted_key`，以及长度 ≥ 32 的连续 base64/hex 串。
4. **本机痕迹**：`D:\`、`C:\Users\`、`下载图片`、`1_PROJECT`、`0_Dev`、`Timco`、
   `.grok-lucy`、机器名（`platform.node()`）。
5. **邮箱**：`[\w.+-]+@[\w-]+\.\w+`。
6. **泳道/分表名**：`TIK-`、`LUCA-`（老说明书里全是这些）。

### B2. 放行名单

真有几个字符串是合法的，比如 `chrome.py` 里的 `C:\Program Files`、
`C:\Program Files (x86)`。**逐条写在脚本顶部的 `ALLOW` 列表里，每条后面跟一句为什么**，
不许用一个大正则一笔带过。放行名单超过 15 条 = 说明白名单出问题了，报告里要解释。

### B3. 顺带扫源目录

加 `--source` 参数，对 `D:\0_Dev\Orante` 本身跑一遍同样的规则，**只报不拦**。
目的是提前发现「代码里写死了我的路径」这种会跟着出包的东西。报告里贴结果。

---

## C. 第一次运行向导（对方怎么填自己的账号）

### C1. 什么时候弹

`accounts` 表为空，**或** `settings` 里 `setup.done` 不为真 → 启动后自动打开向导，
盖在 ①任务 上面的模态，不是新页面。设置抽屉里加一行「重新运行向导」，随时可以再开。

第 0 步先给一个岔路口：

    ┌────────────────────────────────────────────────┐
    │  欢迎使用 Orante                                │
    │                                                │
    │  ( ) 我是第一次用，一步步来          （默认）      │
    │  ( ) 我有一份预设文件（.orpreset），直接导入        │
    └────────────────────────────────────────────────┘

第二项直接走 26 号已经做好的 `/api/preset/import`（我自己换机器时走这条，
别人拿不到我的预设文件，所以两条路都要有）。

### C2. 四步

**① 环境自检**（全自动，逐项打 ✓ / ✗，✗ 后面跟一句人话怎么办）

| 检查 | 不过时说什么 |
|---|---|
| Python ≥ 3.10 | 「去 python.org 装 3.10 以上，安装时勾上 Add to PATH」 |
| 找到 Google Chrome（`chrome.chrome_exe()`） | 「请先装 Google Chrome；装在别处的话，设置里填 chrome 路径」 |
| 端口 `ui.port`（9400）没被占 | 「9400 被别的程序占了，这里可以改成 9401」+ 一个输入框 |
| 工作区目录可写 | 「这个盘写不进去，换一个目录」 |
| 依赖装齐（fastapi/uvicorn/playwright/PIL/webview/requests/cryptography） | 「关掉重新双击 Orante2.bat，它会自己装」 |

**② 工作区**：选成片输出目录，默认 `Orante\workspace`，旁边一句
「生成好的视频、图片、manifest 都会按 日期\版本\任务 放在这里」。

**③ 账号**（重点，对方最会卡在这里）

一行一个账号的表格，「＋添加账号」：

    引擎  ( • ) Flow    ( ) Grok
    名字  [ 随便起，自己认得就行，比如 我的flow1 ]
    项目地址 [ https://flow.google.com/project/... ]   ← 只有 Flow 要填，旁边「这是什么？」
    [ 打开浏览器登录 ]   [ 我登好了，检测一下 ]   状态：未登录 / 已就绪 ✓

- 「打开浏览器登录」调现成的 `accounts.open_login`，端口由 `chrome.free_port` 自动分配，
  **不要让对方填端口**（现在的账号抽屉里端口是自动的，向导里连显示都不用显示，
  放进「高级」折叠里）。
- 「检测」调现成的 `accounts.health`，`state == "ready"` 才给绿勾。
- Flow 的「这是什么？」展开：告诉他在 Flow 里新建一个项目，把浏览器地址栏那一串复制过来。
- **模型选择**：默认就是免费档（`engine.flow.model` 默认值 + `flow.allow_paid=False`），
  向导里给一句显眼的话：「默认只用不扣额度的模型；要用扣额度的得去设置里单独打开」。
  这是我自己踩过的坑（57 号），别人更该被提醒。
- 至少 1 个账号并且状态就绪，才让点「下一步」；可以加任意多个。

**④ 多模态 Key**（可跳过）

Gemini API Key 输入框 + 「测一下」按钮（真发一次最小请求，返回 ok/失败原因）+
一句「去 aistudio.google.com 免费申请」。跳过时明确提示：
「不填的话，②多模态 那一步的自动描述语用不了，只能自己手写」。

完成 → 写 `setup.done = true`，关闭，落到 ①任务。

### C3. 描述语原样保留

`orante/legacy/prompt_defaults.json` 和 `coverage_library.json` **原样打包**，
向导不碰、不重置、不要求对方填。设置→提示词里对方可以自己改。
体检脚本仍然要扫这两个文件；万一里面哪句话带了我的名字或路径，
改那一行，**不是**把文件踢出白名单。

---

## D. 新的《使用说明》

现在的 `说明文档\使用说明.md`（554 行）**是我自己的开发手册，绝对不能出包**：
里面有 `TIK-L1`、`TIK-G-SIMONE`、`LUCA-1`、`core\config_luca1.json`、
`D:\0_Dev\下载图片\AAA\Grok\0830`、`0-长文案\lanes.json`……

新写一份 `说明文档\使用说明.md`（出包用的那一份；源目录里那份原封不动留着自己用，
新的放 `说明文档\使用说明-分发版.md`，出包时改名为 `使用说明.md`）。内容：

1. **开始之前**：Windows 10/11、Python 3.10+（装时勾 Add to PATH）、Google Chrome。
   **解压到纯英文路径**，比如 `D:\Orante`（路径里有中文，venv 和 bat 都可能出岔子）。
2. **第一次启动**：双击 `Orante2.bat` → 它会自己建 `.venv` 并联网装依赖（约两三分钟，
   只有第一次）→ 弹出向导 → 按四步填完。
3. **五个面板各干什么**：①任务 ②多模态 ③编排 ④生成 ⑤收片，每个一小段 + 一张截图。
   截图**必须是干净出厂状态下自己截的**，不许用带我任务的旧图。
4. **加/换账号**、**改描述语**、**导出预设备份**各一小节。
5. **出问题怎么办**：窗口空白 / 起不来 → `tools\13-Orante-Debug.bat`，
   把 `data\logs\orante-debug.txt` 发来；Chrome 找不到 → 设 `ORANTE_CHROME`；
   端口被占 → 设置里改 `ui.port`。
6. **一句话说清额度**：默认只用免费模型；要开扣额度的自己去设置里开，后果自负。

---

## E. 拆掉 `Make-Clean-Build.bat` 这颗雷

保留文件名（省得我哪天凭记忆双击它），但把内容整个换成一段拦截：

    这个脚本会删掉你正在用的数据库和登录态，已停用。
    要出分发包，请用 tools\15-Make-Share-Package.bat（拷副本，不动本目录）。

打印完 `exit /b 1`，**不做任何删除动作**。旧内容留成 `Make-Clean-Build.bat.bak-0914share`。
`specs/26` 第 5 节和它的报告里提到这个脚本的地方，在 26 号报告末尾补一句
「已被 64 号取代」，不要去改 26 号规格正文。

---

## F. 冒烟测试 `tools/16-Test-Share-Package.bat` + `tools/smoke_share.py`

出包脚本最后一步自动调它（`--no-smoke` 可跳过，但默认跑）。**这一步就是「保证不出错」的兑现。**

在 `%TEMP%\orante-smoke-<时间>\` 里：

1. 把出包目录整个拷过去（模拟对方解压）。
2. 在里面建**全新的** `.venv`，`pip install -r requirements.txt`。
3. 设 `ORANTE_DATA` 指向这个临时目录下的 `data`，
   `python.exe -m orante --browser` 起起来，等端口通（最多 60 秒）。
4. 打这几个接口并断言：
   - 健康/状态接口 200；
   - `GET /api/accounts` 返回空列表；
   - `GET /api/config` 里 `gemini.api_key` 为空、`workspace.root` 不含 `D:\`、`setup.done` 不为真；
   - 提示词接口能读到 `prompt_defaults.json` 的六块（证明描述语确实带上了）。
5. 关进程，**删临时目录**，打印 `冒烟通过`。
6. 任何一步失败 → 打印失败在哪一步 + 最后 40 行日志，`exit 1`，**不出 zip**。

通过之后才压 zip：`_share\Orante-2.0-分发版-<YYYYMMDD>.zip`，
压完再对 zip 跑一次 B 节的体检（防止压包时手滑带进别的东西），打印大小和文件数。

---

## G. 测试 `tools/test_spec64.py`（**先设 ORANTE_DATA**）

- 造一个假源目录（含 `data/orante.db`、`data/chrome/x`、`a.py`、`a.py.bak-0101`、
  `__pycache__/`、`specs/reports/img/x.png`、`orante/legacy/跑批日志.txt`、
  `orante/legacy/prompt_defaults.json`、`app/server.py`、`.venv/`），跑 `make_share`：
  出包目录里**有** `orante/legacy/prompt_defaults.json`，
  **没有** db、chrome、`*.bak-*`、`__pycache__`、`specs`、`跑批日志.txt`、`app`、`.venv`。
- **源目录在跑完之后逐字节不变**（跑前跑后各算一次全目录 sha256，断言相等）。这条最重要。
- grep 断言：`make_share.py` 里不含对源目录的写操作（`rmtree`/`unlink`/`os.remove`/`"w"` 打开源路径）。
- 体检脚本：往出包目录塞一个含 `AIza1234…` 的文件 → 退出码 1 且报出文件名；
  塞一个含 `C:\Program Files` 的 → 放行；塞一个含真实账号名的 → 退出码 1。
- `分发清单.txt` 的行数 = 实际文件数，sha256 抽查 3 个对得上。
- 向导：`accounts` 空 → `GET /api/setup/state` 返回 `need_setup: true`；
  加一个账号并 `setup.done=true` 后返回 false。
- `import_legacy_lanes` 在找不到老目录时，`/api/setup/state` 里 `legacy_available: false`。
- `Make-Clean-Build.bat` 的内容里不再含 `del` / `rmdir`（grep 断言）。
- 回归：`python -m pytest tools/` 全过。

---

## 验收

1. 双击 `tools\15-Make-Share-Package.bat`，全程无人值守，最后打印
   `体检通过 / 冒烟通过 / 已出包 _share\Orante-2.0-分发版-20260914.zip（xx MB，xxx 个文件）`。
2. **跑完之后，`D:\0_Dev\Orante` 里我的 `data\orante.db`、`data\chrome\`、`data\logs\`
   一个字节都没变。**（脚本自己在报告里贴出跑前跑后的目录 sha256。）
3. 把 zip 解压到 `D:\Orante-test`（一台没装过 Orante 的干净环境，或者至少是一个
   全新目录 + 全新 venv），双击 `Orante2.bat`：装依赖 → 弹向导 → 四步填完
   （用我的某个账号登一次就行，验完删掉）→ 能新建任务、能看到完整的描述语。
4. 在解压出来的目录里全文搜我的账号名、`AIza`、`D:\0_Dev`、`1_PROJECT`、`TIK-`、
   `@gmail.com`、`grok.com/imagine/post/` —— **一条都搜不到**。
5. 解压目录里没有 `specs\`、`app\`、`web\`、`bridge\`、`Orante.bat`、任何 `.bak-*`。
6. `说明文档\使用说明.md` 是新写的那份，通篇没有 `TIK-` / `lanes.json` / 我的路径，
   截图是干净出厂状态截的。
7. 双击 `tools\Make-Clean-Build.bat` → 只打印一句停用提示就退出，**什么都没删**。
