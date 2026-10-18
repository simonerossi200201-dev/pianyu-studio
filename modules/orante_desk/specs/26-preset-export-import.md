# 26 · 预设：把「我的一整套配置」导出 / 导入，分发时干净出厂

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/00-architecture.md`，
> 再读 `orante/config.py`、`orante/accounts.py`、`orante/services/aikeys.py`、
> `orante/services/prompts.py`，再读本规格。先做完 `specs/29`（提示词得先存得住，
> 不然导出的是残缺的）。改前备份成 `*.bak-0910preset`。
> 报告写到 `specs/reports/26-preset-export-import.md`。中文注释。

## 目标

一句话：**软件分发出去是干净的出厂状态，用户把自己的一套配置导回来，立刻万事俱备。**

导出一个 `.orpreset` 文件，换机 / 重装 / 分发后导入，账号、API key、提示词、
各种参数一步到位，不用再一项项手填。

---

## 1. 预设里装什么（这份清单就是全集，别漏）

| 分组 | 来源 | 说明 |
|---|---|---|
| `settings` | `settings` 表全部行 | 覆盖 `config.DEFAULTS` 的每一个键：workspace.* / split.* / gemini.* / ai.max_workers / flow.* / expand.* / track.* / images.* / ui.* / engine.* |
| `prompts` | `settings` 里 `prompts.*` | 六块（desc / camera / tail / problem / expand + refill_clause）+ `prompts.format_version`。**单独成组**，因为用户最常想单独分享这一份 |
| `accounts` | `accounts` 表 | id / name / engine / port / profile / project_url / model / aspect / max_concurrent / enabled / extra |
| `ai_providers` | `ai_providers` 表 | 多模态接口池，**含 key / base_url / 优先级 / 每日上限 / 并发** |
| `cookies` | 每个账号 Chrome profile 里的 Cookies + Local Storage | 见第 2 节。**用户选了这一档** |
| `counters` | `counters` 表 | 下一个 T / A / B 号。导入时**可选**是否沿用 |
| `meta` | — | 预设格式版本、Orante 版本、导出时间、机器名、是否含 cookie、各组条数 |

**明确不进预设**：`tasks` / `jobs` / `batches`（那是 spec 27 工程文件的事）、
完整的 `data/chrome` profile（1.3 GB）、`data/logs`、`data/ahash-cache.json`、
`data/capture`、`tools/grok-account.txt`（那是开发工具）、任何 `.bak-*` 文件。

---

## 2. 登录态：只带 cookie，不带整个 profile

用户已经定了这一档。做法：

- 每个账号从它的 Chrome profile 目录里读 **`Default/Cookies`（SQLite）**
  和 **`Default/Local Storage/leveldb`**，只取 `grok.com` / `google.com` /
  `flow.google.com` / `labs.google` 这几个域，打包进预设。整包量级 MB，不是 GB。
- Chrome 的 cookie 值是用 **DPAPI 绑定当前 Windows 用户**加密的
  （`Local State` 里的 `os_crypt.encrypted_key`）。所以导出时必须**先解密成明文**再
  重新用预设自己的密码加密，否则换台机器导入就是一堆解不开的乱码。
  解密要用 `win32crypt.CryptUnprotectData` + AES-GCM（Chrome v10/v11 格式）。
  **拿不到明文就老实报错**，不要塞一堆解不开的字节进去然后假装成功。
- 导入时反过来：写进新机器的 profile 时用**新机器的** DPAPI 重新加密。
  Chrome 必须**没在跑**才能写（Cookies 文件被独占锁），导入前检测端口，
  在跑就提示用户先关掉那个账号的 Chrome。
- 导入后在账号抽屉里，每个账号显示 `登录态：已带入 / 需重登`，
  用户点一下就能单独重登某一个，不用全部重来。
- **如果这套 cookie 搬运在某个账号上失败，不要让整个导入失败** ——
  把那个账号标成「需重登」，其余照常导入，最后汇总告诉用户哪几个要重登。

---

## 3. 加密

用户已经定了：**导出时设一个密码，整包加密。**

- 密钥派生：`PBKDF2-HMAC-SHA256`，随机 16 字节 salt，**迭代 200000 次**，出 32 字节密钥。
- 加密：`AES-256-GCM`，随机 12 字节 nonce，认证标签一起存。
- 依赖 `cryptography` 包。**先检查 `.venv` 里有没有**，没有就加进 `requirements.txt`
  并在报告里写明要 `pip install`。不要自己手写 AES，也不要用 `zipfile` 自带的
  ZipCrypto（那个是弱加密，等于没加密）。

**文件结构**（前面一段明文，后面一段密文）：

    ORANTE-PRESET\n
    {"format":1,"orante":"2.0.0-dev","created":"2026-09-10T11:20:00",
     "machine":"luca","has_cookies":true,
     "counts":{"settings":38,"accounts":6,"ai_providers":3,"prompts":6},
     "kdf":{"algo":"pbkdf2-sha256","iter":200000,"salt":"<base64>"},
     "cipher":{"algo":"aes-256-gcm","nonce":"<base64>"}}\n
    <密文字节>

**头部必须是明文**，这样导入前能先给用户看「这份预设是什么、几个账号、含不含登录态、
什么时候导的」，不用先输密码。头部里**绝对不许有任何 key、cookie、账号名**。

密码错误 → 一句人话：`密码不对，或者这个文件不是这台机器能认的预设`。不要吐 traceback。

---

## 4. 界面

「设置」抽屉里新开一个 **「预设」** 区：

    预设
    ┌──────────────────────────────────────────┐
    │  导出预设…    把当前所有配置存成一个文件         │
    │  导入预设…    从文件恢复配置                   │
    │  恢复出厂…    清空账号 / API / 提示词改动        │
    └──────────────────────────────────────────┘

**导出**弹一个对话框，勾选要带哪几组（默认全勾），
`☑ 一起带上登录态（cookie，约 8 MB）` 单独一行、默认勾上、旁边一行小字
`带上就不用重新登录；这个文件请当成密码一样保管`。然后输两遍密码。
默认文件名 `Orante-预设-<机器名>-<YYYYMMDD>.orpreset`，默认存到工作区根目录。

**导入**选文件后**先显示明文头部的摘要 + 变更预览**，再让用户输密码：

    这份预设：luca · 2026-09-10 · 含登录态
      设置     38 项  →  覆盖 12 项，新增 26 项
      提示词    6 块  →  覆盖 3 块（desc / camera / tail 你本机改过）
      账号      6 个  →  新增 4 个，同名冲突 2 个
      接口池    3 条  →  新增 3 条
    合并方式：  ( ) 合并，冲突以预设为准   (•) 合并，冲突保留本机   ( ) 整包替换

「整包替换」要**二次确认**，并且**自动先导出一份当前配置**到
`data/预设备份-<时间>.orpreset`（用同一个密码），免得手滑覆盖了找不回来。

**恢复出厂**：清空 `accounts` / `ai_providers` / `settings` 里所有非默认项 /
`prompts.*` 覆盖 / `data/chrome/*`。**保留** `tasks` 和已完成的工程。
要输一次「确认清空」四个字才执行，并且同样先自动备份一份预设。

---

## 5. 分发时的干净出厂

加一个 `tools\Make-Clean-Build.bat`：把 `data/` 下这些清掉再打包 —— 
`orante.db`（或只清 accounts / ai_providers / settings 三张表）、`chrome/`、`logs/`、
`tmp/`、`capture/`、`ahash-cache.json`、`google/`、`used_images.json`、
`flow_media_cache.json`、`marks.json`、`lens_job.json`，以及所有 `*.bak-*`。
跑完打印一句「已清理，可以打包分发」并**列出还剩哪些文件**，让人自己扫一眼。

另外：`orante/config.py` 的 `DEFAULTS` 里 `gemini.api_key` 默认已经是空串，很好，
检查一遍别的键有没有藏着私人路径（`workspace.root` 默认值要是通用路径，不能是
`D:\1_PROJECT` 这种一看就是本机的）。

---

## 6. 接口

    GET  /api/preset/preview          # 当前配置各组条数，给导出对话框用
    POST /api/preset/export           body {"groups":[...], "cookies":true, "password":"…", "path":"…"}
                                      → {ok, path, bytes, warnings:[...]}
    POST /api/preset/inspect          body {"path":"…"}     # 只读明文头部，不要密码
                                      → {meta:{...}}
    POST /api/preset/diff             body {"path":"…","password":"…"}
                                      → {settings:{overwrite,add}, prompts:{...}, accounts:{add,conflict:[...]}, ...}
    POST /api/preset/import           body {"path":"…","password":"…","mode":"merge_preset|merge_local|replace","groups":[...]}
                                      → {ok, applied:{...}, need_relogin:["A002"], warnings:[...]}
    POST /api/preset/factory_reset    body {"confirm":"确认清空"}

## 7. 验收

1. 导出一份全量预设（含 cookie），文件 < 30 MB，头部能用记事本打开看懂，
   **在里面搜 `AIza` / `sk-` / 任何一个账号名，一个都搜不到**。
2. 把 `data/orante.db` 挪走（模拟新机器），启动 Orante → 干净出厂状态。
   导入刚才那份预设 → 账号、接口池、提示词、所有设置回来了，
   打开 ① 任务就能直接开工。
3. 密码输错 → 一句人话提示，不崩、不吐 traceback。
4. 导入时选「合并，冲突保留本机」→ 本机改过的 3 块提示词没被覆盖。
5. 「整包替换」前自动生成的备份文件确实存在，且能被导回来。
6. 某个账号的 cookie 解不开（手动把它的 Cookies 文件改坏）→ 其余账号照常导入，
   那一个标「需重登」，汇总里点名。
7. 跑 `tools\Make-Clean-Build.bat` 后再启动 → 出厂状态，没有任何私人数据。
