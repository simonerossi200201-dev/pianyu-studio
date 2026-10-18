# 20 · 多模态接口池（多条 API key 动态均衡）

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先完整读 `specs/00-architecture.md`，再读本规格。**后端的池子已经全部做完并自测通过**（见下「后端：已完成」）。你做三件事：① 把各处调用点从 `Gemini()` 切到 `ai.client()` ② 描述语的线程数改成跟着 key 数量走 ③ 做「设置 → AI 接口」那一页界面。只改本规格点名的文件；`orante/services/ai.py`、`aipool.py`、`aikeys.py`、`gemini.py` **不要动**。老流水线 `..\下载图片\` 只读不改。写完自己跑一遍验收，报告写到 `specs/reports/20-ai-pool.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。

## 为什么要做（0908 事故）

四个任务同时「全部生成」，每个任务自己开 4 个线程补描述语，每张图两个请求
（描述语 + 测量）→ 瞬间十几个请求砸向**同一个** Gemini key → 全部 429，
四个任务一起失败。账号队列（规格 19）管的是渲染账号（四个 Chrome），
管不到 Gemini —— 那是所有任务、所有窗口共用的一份配额。

用户定的方案不是限流，是**扩容**：名册里可以放任意多条能看图的接口
（Gemini、任何 OpenAI 兼容的服务、本地 Ollama），多条一起发力、活不重复，
吞吐和额度同时翻倍；某条被限流就自动绕开，不再卡住整批。

## 已经定死的两条规矩（用户 0908 选的）

1. **优先级分档 + 同档均衡**：每条 key 有个 `priority`（1 最优先）。高档只要还有空位就轮不到低档；同档之间「谁手上活最少给谁」。这样能让本地模型先跑、云端兜底，或者先烧免费额度再动付费的。
2. **并发跟着 key 数量走，但卡总上限**：每条 key 有 `max_inflight`（默认 2），全池容量 = 各条相加，再卡一个总上限（默认 16），别把本地机器打爆。加一条 key 就自动提速。

## 后端：已完成（0908，Claude 写的，别再动）

新文件：

- `orante/services/aikeys.py` —— 名册的增删改查。表 `ai_providers`：
  `id name kind base_url api_key model priority max_inflight daily_limit vision enabled row_order`。
  `kind` ∈ `gemini` / `openai`（任何 OpenAI 兼容的 `/chat/completions`）/ `ollama`（本地原生 `/api/chat`）。
  `masked()` 把 key 打码成 `…尾四位` 给界面用。
  `migrate_from_settings()` 在启动时把老的单条 `settings['gemini.api_key']` 自动搬成第一条，升级不用重填。
- `orante/services/aipool.py` —— 跨进程调度。表 `ai_leases`（谁在用哪条，心跳过期自动回收）、`ai_state`（冷却时间、当天用量）。`lease()` / `capacity()` / `workers(cap)` / `status()` / `clear_cooldown()`。
- `orante/services/ai.py` —— 统一入口。**方法名和参数跟老的 `Gemini` 一模一样**：
  `ask_text(prompt, system, temperature, json_mode)` /
  `ask_image(prompt, image_path, ...)` / `ask_images(prompt, image_paths, ...)`。
  内部：借 key → 按协议发请求 → 成功记用量 / 被 429 就冷却这条并**换下一条重发** →
  一轮全试过才报错。三个 adapter（gemini / openai / ollama）都写好了。
  `ai.probe(p)` 是自检：先问一句文字，再拿一张内嵌的纯红小图问颜色，确认真能看图。

改动：`orante/db.py`（三张新表）、`orante/main.py`（启动时迁移）、
`orante/services/gemini.py`（`Gemini(retries=0)` 让 429 立刻抛出，好换 key；429 异常上挂 `e.quota`）、
`orante/server.py`（下面这些接口）。

自测：`tools\aipool_test.py`（双击 `tools\6-AIPool-Test.bat`），7 组全过——
均衡、优先级、429 换号、全挂时的报错、容量计算、僵尸位回收、老 key 迁移。

### 接口

    GET    /api/ai/providers          → {providers:[打码后的名册], status:[每条实时状态], capacity, workers}
    POST   /api/ai/providers          {name,kind,base_url,api_key,model,priority,max_inflight,daily_limit,vision,enabled}
    POST   /api/ai/providers/{pid}    同上；api_key 传空或 "****" = 保留原值，别把 key 洗掉
    DELETE /api/ai/providers/{pid}
    POST   /api/ai/providers/reorder  {ids:[...]}
    POST   /api/ai/providers/{pid}/test  → {result:{text, vision, ok_text, ok_vision}}
    POST   /api/ai/test               同上，但传的是还没保存的草稿（新建对话框里的「测一下」）
    POST   /api/ai/thaw               {id?}  手动解冻；不传 id = 全部解冻

`status` 每条长这样：

    {id, name, kind, model, enabled, priority, inflight, max_inflight,
     calls, fails, daily_limit, day, cool_left, cool_why, last_error, state}

`state` 是现成的中文短语：`空闲` / `在飞 2` / `冷却 27s` / `停用`，界面直接显示。

## 你要做的

### A. 切调用点（`orante/services/` 下这几处）

把 `Gemini()` 换成 `ai.client(job)`（能拿到 `job` 就传，拿不到就 `ai.client()`）。
**函数签名和参数名一律不动**——那些 `gemini=` 形参照旧，传进去的对象换成池化的就行。

| 文件 | 行（大概） | 现在 | 改成 |
|---|---|---|---|
| `describe.py` | 157 / 191 | `gemini = Gemini()` | `gemini = ai.client(job)` |
| `generate.py` | 133-135 | `from .gemini import Gemini` / `Gemini()` | `from . import ai` / `ai.client(job)` |
| `expand.py` | 610 / 869 / 955 / 1037 | `Gemini()` | `ai.client(job)` |
| `broll.py` | 194-195 | `Gemini()` | `ai.client()` |
| `lens.py` | 362-363 | `gemini or Gemini()` | `gemini or ai.client()` |
| `prompts.py` | 244-245 | `Gemini().ask_text(...)` | `ai.client().ask_text(...)` |

`server.py:261` 那个 `Gemini(api_key=..., model=...)` 是「设置里测 Gemini 连通性」的老按钮，**保持不动**（新的自检走 `/api/ai/.../test`）。

注意 `import` 别写成循环依赖：`ai.py` 自己 import 了 `gemini`，所以这些文件里 `from . import ai` 就行。

### B. 线程数跟着 key 走

`describe.py:203` 现在是 `n_workers = int(_cfg("gemini.concurrency", 4) or 4)`。
改成：

    n_workers = ai.workers(int(_cfg("ai.max_workers", 16) or 16))

并在 `orante/config.py` 的 `DEFAULTS` 里加 `"ai.max_workers": 16`。
`gemini.concurrency` 这个老设置**保留**（迁移时用来当第一条 key 的 `max_inflight`），
但描述语不再读它。日志里开跑前写一行：`本轮用 N 条接口、M 个线程`。

### C. 设置面板加「AI 接口」一页

改 `ui/js/parts/settings.js`、`ui/css/app.css`、`orante/i18n/zh.json`（只加 key）。
先备份成 `*.bak-0908ai`。

顶部一句灰字说明 `t('ai.intro')`：
「可以加任意多条能看图的接口。多条会同时干活、互不重复，额度也叠加；某条被限流会自动绕开。」

**列表**（一行一条，可拖动排序 → `POST reorder`）：

    ⠿  ● Gemini-主      gemini  gemini-3.5-flash-lite   优先级1  在飞 2/2   今日 148/250   [测一下] [编辑] [删除]
    ⠿  ● 本地 Qwen      ollama  qwen2.5vl:7b            优先级1  空闲 0/2   今日 12        [测一下] [编辑] [删除]
    ⠿  ○ 中转-备用      openai  gpt-4o-mini             优先级2  冷却 27s   今日 3  失败 1  [测一下] [编辑] [删除] [解冻]

- 左边圆点 = `enabled`，点一下开关。
- 状态直接用 `status[].state`；冷却中的那条整行淡一点，并显示 `cool_why`，右边多一个「解冻」按钮（`POST /api/ai/thaw {id}`）。
- `今日 X/Y`：`daily_limit` 为 0 时只显示 `今日 X`。`fails>0` 时把失败数标红，hover 显示 `last_error`。
- 顶栏右边：`容量 N 并发 / 线程 M`（来自 `capacity` / `workers`），加一条 key 这两个数会变大——让用户看得见「加 key 就是提速」。
- 2 秒轮询 `GET /api/ai/providers` 刷新状态（面板关掉要 `clearInterval`）。

**新建 / 编辑对话框**：

    类型   ( ) Gemini   ( ) OpenAI 兼容   ( ) 本地 Ollama
    名称   [ Gemini-主            ]
    地址   [ https://api.openai.com/v1 ]   ← Gemini 隐藏这一行；ollama 默认 http://127.0.0.1:11434
    Key    [ ****                 ]        ← ollama 可以空；编辑时显示 …尾四位，不动就不会被覆盖
    模型   [ gemini-3.5-flash-lite ]
    优先级 [ 1 ]  同时接活 [ 2 ]  每日上限 [ 0 = 不限 ]
    [x] 支持看图
                       [ 测一下 ]   [ 取消 ] [ 保存 ]

「测一下」调 `/api/ai/test`（草稿）或 `/api/ai/providers/{id}/test`（已存在）。
结果就在对话框里显示：

    ✓ 文字通了：「可以」
    ✓ 看图通了：这张图它认成「红色」
    ✗ 看图没通 → 红字提示「这条接口好像不支持看图，只能拿来做纯文字的活；要用它写描述语必须支持看图」

`ok_vision` 为 false 时不拦着保存，但把「支持看图」自动取消勾选，并 toast 提醒一次。

### D. i18n / CSS

`ai.*` 前缀，类名 `.aip-*`。视觉跟设置面板现有的账号列表是一家人，别另起一套。
状态圆点的颜色：空闲=灰、在飞=青、冷却=橙、停用=暗。

## 验收（自己跑一遍，写进报告）

1. `python tools\aipool_test.py` 仍然全过（你不该动到后端，跑一遍确认没碰坏）。
2. 只有一条 Gemini key 时，行为跟以前一样——描述语能写出来，线程数 = 那条的 `max_inflight`。
3. 加第二条（可以先加一条假的 OpenAI 地址）→ 顶栏的「容量/线程」变大。
4. 把第一条的 key 改成错的 → 跑描述语时日志里出现「… 出错，换下一条」，最终**还是写出来了**，而不是整个任务失败。
5. 两条全填错 → 报错是「所有多模态接口都没能完成这次请求」，并带上最后一条的错。
6. 编辑一条已有的 key，不碰 Key 那一栏 → 保存后 key 没被清掉（`…尾四位` 还在，测一下仍然通）。
7. 删掉所有 key → 写描述语时报「还没有可用的多模态接口」，提示去设置里加。
8. 升级路径：把 `ai_providers` 清空，重启 Orante → 老的 `gemini.api_key` 自动变成第一条。

## 别做的

- 不要给 key 做「自动探测模型列表」（各家协议不一样，先手填）。
- 不要在界面上显示完整的 API key，任何时候都只显示尾四位。
- 不要动规格 19 的账号队列——那是渲染账号，跟这里的接口池是两回事。
