# Orante 2.0 · 架构总纲（所有模块规格书的上位文件）

> 给 Grok 的话：写任何模块前先读完这一份。规格书里没写的，按这里的规矩来；两边冲突以这里为准。
> 语言：Python 3.11+（后端）、原生 ES2020 + Vue 3（前端，**不打包、不用 npm**）。注释和界面文字用中文。

## 1. 目录（全部相对 `Orante\` 根）

```
Orante\
  Orante.bat / Orante.command      唯一入口（bat 必须纯 ASCII）
  orante\                          Python 包（后端）
    __init__.py
    paths.py        ROOT、DATA、UI、WORKSPACE 等路径常量；find_legacy_root()
    config.py       settings 读写（SQLite settings 表 + 默认值）
    db.py           SQLite 连接、建表、迁移
    models.py       Task / Segment / Manifest dataclass + manifest.json 读写校验
    accounts.py     账号（Flow/Grok）增删改、Chrome 拉起、登录态探测
    workspace.py    「这批存哪」：根目录 / 日期 / 版本 A,B… 文件夹分配
    scheduler.py    每账号一条队列、一次只跑一个任务；任务=一连串 step
    events.py       进程内事件总线 → SSE 推给前端
    server.py       FastAPI 路由（只做 HTTP 转发，不写业务）
    main.py         启动：建库 → 起 uvicorn 线程 → pywebview 或浏览器
    i18n\zh.json    全部界面文字（前端 fetch 这个文件；以后加 en.json / it.json）
    engines\
      base.py       Engine 抽象基类（见 §4）
      chrome.py     Chrome 进程/CDP 端口/Playwright 连接 公用工具（含防冻结注入）
      flow_api.py   Flow：走 tRPC 接口
      flow_dom.py   Flow：DOM 兜底（Plan B，从 longtik_flow_newui 搬）
      grok.py       Grok：从 grok_pipe 搬
    services\
      split.py      文案拆段（包 flow_split，规则可配置）
      images.py     图片来源解析：直链 / Drive 文件夹 / 本地文件夹；下载、编号、标记已使用
      gemini.py     Gemini REST 客户端（普通 Key 直连；referrer Key 走隐藏页）
      prompts.py    四块提示词的默认值 + 用户覆盖 + 成品提示词拼装（包 flow_shots.build_prompt）
      lens.py       看图运镜（搬 Orante1 bridge\lens.py）
      track.py      配图轨（搬 Orante1 bridge\track.py）
      expand.py     多镜头扩图 / 被拒补图（Gemini 写生图描述 → Flow 出图 → 翻转 → 替换）
      checks.py     体检（搬 Orante1 app\checks.py）
      sheets.py     可选：Google Sheet 链接导入（包 表格自动下载器 的 fetch_sheet_rows）
    legacy\         从 下载图片\core 原样拷进来的、被上面 services 包住的老模块（不改）
  ui\                              前端（静态文件，FastAPI 直接 serve）
    index.html      壳：顶栏 + 主区 + 底部节点栏
    css\app.css     主题变量（沿用 Orante1 的暗色配色）
    js\app.js       createApp、路由（当前节点）、全局 store、API 封装、SSE
    js\api.js       fetch 封装（所有请求经这里）
    js\panels\      tasks.js / gemini.js / track.js / generate.js / review.js  五个节点面板
    js\parts\       accounts.js（账号抽屉）settings.js（设置抽屉）nodebar.js  logdrawer.js
    vendor\         vue.global.prod.js  tabulator.min.js  tabulator_midnight.min.css
  data\             orante.db、logs\、capture\   （程序数据，随软件走）
  workspace\        默认工作区（用户可改到别处）
  specs\            规格书
  tools\            开发/诊断小工具
```

## 2. 硬规矩

1. **路径**：只准从 `orante.paths` 拿根；`Path` 拼接；不出现 `D:\`、`C:\`。用户自己选的工作区路径存 settings，是唯一的绝对路径。
2. **平台**：`os.name`/`sys.platform` 分支只允许出现在 `paths.py`、`engines/chrome.py`、`main.py` 三处。
3. **老代码不改**：`legacy\` 里的文件一个字节不动，需要不同行为就在 `services\` 里包一层。
4. **真值只有一份**：任务的段状态只在它自己的 `manifest.json`；任务列表/账号/设置只在 SQLite。不做第三份缓存文件。
5. **每个长任务都是 Job**：`scheduler.run(task_id, kind)` 返回 job_id；日志逐行进 `data\logs\<job_id>.log` 并通过 SSE 推前端；可取消（cooperative：step 之间检查 `job.cancelled`）。
6. **一个账号同一时刻一个任务**：`scheduler` 按 `account_id` 排队；不同账号并行；一个账号连接的 Chrome 由 `engines/chrome.py` 保证只开一个进程。
7. **前端不算业务**：拆段、排轨、拼提示词全在后端算，前端只展示 + 收集输入。
8. **界面文字**：一律 `t('key')` 从 `i18n/zh.json` 取；不在模板里写死中文。
9. **错误要说人话**：后端抛 `OranteError(message_zh, hint=...)`，server 统一转成 `{ok:false, error, hint}`；前端 toast 显示 error 和 hint。
10. **不问第二遍**：任务表里选好的账号、拆段规则、保存位置，执行时不再弹窗确认（除首次选保存位置）。

## 3. 数据模型

### 3.1 SQLite（data\orante.db）

```sql
accounts(id TEXT PK, name TEXT, engine TEXT CHECK(engine IN('flow','grok')), port INTEGER UNIQUE,
         profile TEXT, project_url TEXT, model TEXT, aspect TEXT DEFAULT '9:16',
         max_concurrent INTEGER DEFAULT 5, enabled INTEGER DEFAULT 1, extra TEXT /*json*/, created TEXT)
tasks(id TEXT PK, name TEXT, engine TEXT, account_id TEXT, type TEXT CHECK(type IN('long','short')),
      copy TEXT, image_source TEXT, duration_pref TEXT, expand_account_id TEXT,
      status TEXT, dir TEXT, batch_id TEXT, row_order INTEGER, created TEXT, updated TEXT, meta TEXT)
batches(id TEXT PK, root TEXT, day TEXT, version TEXT, dir TEXT, created TEXT)
jobs(id TEXT PK, task_id TEXT, kind TEXT, status TEXT, started TEXT, finished TEXT, log TEXT, error TEXT)
settings(key TEXT PK, value TEXT /*json*/)
```

`tasks.status`：`draft`（表里刚填）→ `prepared`（已下图+拆段）→ `described`（描述语/运镜齐）→ `arranged`（配图轨已定）→ `generating` → `collected`（片齐）→ `finished`（有字幕成品，自动进「过时」）；另有 `trash`。

### 3.2 任务目录（工作区）

```
<工作区根>\2026-09-06\A\T0007-圣母祷文\
  copy.txt              原文
  images\p01.jpg …      已编号；被拒替换 = 覆盖同名文件 + 旧图移到 images\_replaced\
  空镜头\               Gemini 判定的大全景 / 观众席
  manifest.json         §3.3
  clips\T0007-s001-p03.mp4 …   ；clips\_弃用\   ；字幕成品\
  logs\
```

### 3.3 manifest.json（version 2）

```json
{
  "version": 2, "task_id": "T0007", "name": "圣母祷文", "engine": "flow", "account": "luca-1", "type": "long",
  "copy": {"text": "...", "sha1": "..."},
  "images": [
    {"p": "p01", "file": "images/p01.jpg", "sha1": "...", "role": "主体", "status": "ok",
     "desc": "描述语正文", "voice": "音色行", "eye": "闭眼", "scene": "常规",
     "measure": {"shot_size": "中景", "facing": "正面", "...": "..."}, "origin": "download|expand|replace"}
  ],
  "split": {"rule": {"engine": "flow", "max_chars": 145, "dur_4s_max_words": 8, "dur_6s_max_words": 13}},
  "segments": [
    {"s": 1, "text": "...", "duration": 6, "image": "p01", "role": "主体",
     "shot": "【镜头】…", "prompt_final": "提交那一刻的完整字符串",
     "status": "planned", "gen_id": null, "takes": [{"file": "clips/T0007-s001-p01.mp4", "at": "..."}], "error": null}
  ],
  "track": {"pins": {"1": "p01", "47": "p01"}, "pools": [{"from": 10, "to": 20, "images": ["p02","p03"]}],
            "hold": 3, "jitter": 1, "unit": "seg", "seed": 1234},
  "prompts_snapshot": {"desc_version": "v7", "tail_version": "...", "problem_sentences": ["..."]}
}
```

段状态：`planned` → `submitted` → `collected`；失败 `failed`；被 Flow 拒图 `blocked`（触发补图）。

## 4. Engine 接口（engines/base.py）

```python
class Engine:
    name: str                      # 'flow' | 'grok'
    def __init__(self, account: Account, log): ...
    def connect(self) -> None                       # 拉起/连上这个账号的 Chrome，确认已登录
    def health(self) -> dict                        # {ok, logged_in, url, note}
    def submit(self, seg: Segment, image: Path, prompt: str, duration: int) -> str   # 返回 gen_id
    def poll(self, gen_ids: list[str]) -> dict[str, dict]   # gen_id -> {state: pending|done|failed|blocked, url, error}
    def download(self, gen_id: str, dest: Path) -> Path
    def capacity(self) -> int                       # 还能同时挂几条
    # 只有 Flow 实现：
    def gen_images(self, prompt: str, ref: Path | None, n: int = 4, aspect: str = '9:16') -> list[Path]
```

调度器只认这个接口，不认 Flow/Grok。

## 5. HTTP API 约定

- 全部在 `/api/...`，JSON。成功 `{ok:true, ...}`；失败 `{ok:false, error, hint}` HTTP 200（前端好处理）。
- 长任务：`POST /api/jobs {task_id, kind}` → `{job_id}`；`GET /api/jobs/<id>`；`POST /api/jobs/<id>/cancel`；`GET /api/events`（SSE：`job.log`、`job.state`、`task.update`、`toast`）。
- 静态：`/` → `ui/index.html`；`/ui/*`；`/media/<task_id>/<相对路径>` 支持 Range。

## 6. 前端约定

- 一个 Vue 根实例；`store`（reactive）里有 `node`（当前节点）、`tasks`、`accounts`、`settings`、`jobs`、`i18n`。
- 面板组件放 `js/panels/`，用 `template:` 字符串（不用 .vue 文件）；每个面板导出 `{ name, template, setup }`。
- 样式变量沿用 Orante1：`--ground #121110 --panel #191714 --ink #EFEAE0 --grok #D9A34F --flow #6BB2AF --ok #8FB06A --bad #D9705F`。Flow 的东西一律青色，Grok 一律金色，一眼分清。
- 表格用 Tabulator（`clipboard: true`，粘贴 TSV 即导入）。
- 节点栏在底部，高 46px；五个节点 + 连线；每个节点带状态灯。

## 7. 时长与拆段默认值（不可丢）

| 引擎 | 规则 |
|---|---|
| Flow | 段上限 145 字；≤8 词 → 4s；≤13 词 → 6s；否则 8s；付费模型允许 10s（设置里勾） |
| Grok | 6s / 10s / 15s：按现 grok_pipe `_pick_duration` 的阈值（搬过来时把阈值抽成设置项，默认值原样） |

## 8. 提示词四块（services/prompts.py 默认值来源）

1. 描述语提示词 ← `描述语生成器.html` 的 `DEFAULT_PROMPT`（v7·0905）
2. 运镜规则 ← `flow_shots.json`（安静模式池、坐姿保护、禁令）
3. 固定结语 ← `flow_shots.json["固定结语"]`（闭眼 / 睁眼 / 空镜 / 情绪推进 / 禁令 / 停顿强调模板）
4. 问题句子库 ← `flow_shots.py` 里 stay/pause/wait 的正则改成**用户可编辑的句子列表** + 一颗「Gemini 扫文案自动补充」

用户改动存 settings（`prompts.*`），永远可以「恢复默认」。
