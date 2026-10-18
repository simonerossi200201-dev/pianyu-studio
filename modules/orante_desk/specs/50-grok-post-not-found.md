# 50 · Grok「Post not found」被踢回首页后 6 秒一刷的死循环

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/06-grok-engine.md`、
> `specs/21-collect-integrity.md`、`specs/37-anchor-bridge-pipeline.md` 和它们的报告，再读
> `orante/engines/grok.py`（`submit` / `_poll_one` / `_page_of`）、
> `orante/legacy/grok_pipe.py`（`post_url` / `capture_anchor` / `goto_own_post` / `probe` /
> `IS_FAILED_JS`）、`orante/services/generate.py`（第 389~503 行的轮询）、再读本规格。
> **先做完 49。** 改前备份 `*.bak-0912notfound`。报告写到 `specs/reports/50-grok-post-not-found.md`。中文注释。

## 现象

锚点+接片模式，T0015 的 J00155：11 段收了 6 段后，最后一段 s010 卡在「提交中」，
Chrome 里那个 Grok 标签页**每 6 秒跳一次**：先到 `/imagine/post/<id>?conversation=…`，
Grok 弹出 `Post non trovato. Reindirizzamento alla home di Imagine…`（意大利语界面，
= Post not found. Redirecting to imagine home），踢回 `/imagine` 首页，6 秒后再来一遍。
日志一直是 `… 在生成 1 条，待提交 0，已收 6`，要等满 `GEN_TIMEOUT`（15 分钟）才会以
「等了太久没出片」失败。用户看到的就是一个刷不完的死循环。

## 根因（三层）

1. **`_poll_one` 每次轮询都无条件 `goto_own_post`**（`grok.py:500`）。
   `goto_own_post` 发现 `page.url` 里没有本段 postId（因为上一轮已经被踢回首页），
   而 `conv` 非空，就再次 `page.goto(post_url(post, conv))` → 再次被踢。
   没有任何「上一次就被踢了」的记忆，没有退避，没有次数上限。
2. **被踢回首页这件事没人识别。** `IS_FAILED_JS` 只匹配
   `失败|failed|error|unavailable|try again|rate limit|moderat`，
   「Post not found」「Post non trovato」都不在里面；URL 落到 `/imagine` 根路径也没人查。
   于是 `probe` 在**首页**上数 `<video>`：首页信息流有多个视频 → 打「页面上有 N 个视频，
   判不出哪个是本段，继续等」（`跑批日志.txt` 里 09:44 / 16:38 / 02:15 三大串就是这个），
   或者 0 个 → `未知` → pending。永远 pending。
3. **兜底只有 15 分钟的 `GEN_TIMEOUT`**，而且到期只算「等太久」，不说明 post 已经不存在，
   用户和重生逻辑都不知道该怎么办。

至于 Grok 为什么说这个 post 不存在，两种可能，本规格都要覆盖：
- (a) 生成在服务端被删/被审核掉了（post 真的没了）；
- (b) `conversation` 对不上：`_poll_one` 第 486~499 行的「锚点自愈」是从**当时的 `page.url`**
  里抠 conversation。如果那一刻页面已经不在本段的地址上（比如刚被踢回首页、或首页信息流
  跳到别的 post），抠到的就是别人的会话 Id，从此每次拼出来的地址都是错的。
  这条要在日志里证明或排除（见 C）。

## 要做的

### A. `goto_own_post` 要认得「被踢」并且不再硬闯

- `goto` 之后除了看 `page.url` 含不含 postId，还要判定**是否落在首页**：
  `re.search(r"grok\.com/imagine/?(\?|$)", url)` 或路径里没有 `/post/`。
  同时抓一次 toast 文本（`document.body.innerText`）匹配
  `/post (not found|non trovato|introuvable|nicht gefunden|no encontrado)|未找到|找不到/i`，
  能抓到就记进返回值。
- 返回值从 `bool` 改成三态：`"on_post"` / `"bounced"`（导航了但被踢回首页）/ `"stayed"`
  （没会话 Id、原地没动）。原来 `if goto_own_post(...)` 的调用点一律按 `== "on_post"` 处理。
- 在 `grok_pipe` 里加一个模块级 `BOUNCE_TEXT_RE`，`IS_FAILED_JS` **不要**直接加 not found
  （首页信息流里也可能出现这些词，会误杀），被踢的判定只走 URL + toast 这条路。

### B. `_poll_one`：被踢两次就判失败，中间要退避

- `self._bounced: dict[str, int]`（postId → 连续被踢次数）。
  `goto_own_post` 返回 `"bounced"` → 计数 +1，日志
  `↩ s010 被 Grok 踢回首页（第 1 次）：Post not found，地址 …`；返回 `"on_post"` → 归零。
- 第 1 次被踢：**不立刻重试**。下一轮轮询先原地不动（不 goto），只 `probe`；
  再下一轮才允许重新导航。也就是每次被踢至少间隔 2 个轮询周期。
- 第 1 次被踢之后、第 2 次导航之前，做一次 (b) 的自救：如果 `self._rows[gid]["会话Id"]`
  （提交时 `capture_anchor` 抓到的）和 `self._conv[gid]`（可能被「锚点自愈」改过）**不一样**，
  用提交时那个再试一次，并在日志里写明两者各是什么。
- **连续 2 次被踢** → 直接返回
  `{"state": "failed", "error": "Grok 找不到这个 post（已被删或被拒）", "raw": toast文本}`，
  `self._active.discard(gid)`，关掉那个标签页（`_close_tab`）。
  不再等 15 分钟；`generate.py` 现有的 failed 分支会走重生/换图/`stamp_waits`，不用改。
- 「锚点自愈」（486~499 行）加一道门：**只有当 `page.url` 里含本段 postId 时**才允许从 URL 里
  补 conversation。不在自己页面上抠到的会话 Id 一律不要。

### C. `probe` 不在详情页时不数视频

- `probe(page, post, …)` 开头：如果 `post` 非空且 `page.url` 里没有它，直接返回
  `("不在详情页", "")`，不去数 `<video>`。这样首页信息流的视频不会再产生
  「页面上有 N 个视频，判不出哪个是本段」的假警报。
- `_poll_one` 收到 `"不在详情页"` 按 pending 处理，但日志里写清楚（每段只写一次，别刷屏）。

### D. 日志要能回答「为什么 not found」

- 提交时已经打 `抓到 postId … 会话 …`。补一条：轮询第一次导航前打
  `→ s010 导航 https://grok.com/imagine/post/<id>?conversation=<conv>`（完整地址，只打一次）。
- 被踢时把 toast 文本和落地 URL 一起打出来。
- 报告里附一段：用 `tools/9-…` 之类的探针或直接读 `跑批日志.txt`，说明 T0015 s010 那次
  属于 (a) 还是 (b)。如果日志不够，就在报告里写「需要用户下次复现时复制 J 日志」。

### E. 测试 `tools/test_spec50.py`（用 FakePage，不碰真账号，**先设 ORANTE_DATA**）

- FakePage 的 `goto` 把 url 设成 `https://grok.com/imagine`、`innerText` 含
  `Post non trovato` → `goto_own_post` 返回 `"bounced"`。
- 连续两轮 `_poll_one` 都被踢 → 第二轮返回 `failed`，error 含「找不到这个 post」，
  两轮之间 `goto` 调用次数 ≤ 2（不能每轮都导航）。
- 会话 Id 不一致时：第二次导航用的是提交时的 `会话Id`。
- 页面在首页、`post` 非空 → `probe` 返回 `"不在详情页"`，不数视频。
- 页面在自己的 post 上、`conversation` 缺失 → 「锚点自愈」照常补；页面不在自己 post 上 → 不补。
- 回归：`python -m pytest tools/`（整目录，49 做完之后应该安全）全过。

## 验收

1. 人为构造：提交后手动把那个 Grok 标签页导航到首页 → 日志出现「被踢回首页（第 1 次）」，
   下一轮不导航，再下一轮导航一次，第 2 次被踢 → 段标 failed、error 是「Grok 找不到这个 post」，
   整个过程 ≤ 30 秒，标签页被关掉，Chrome 不再每 6 秒刷。
2. 正常段不受影响：T0015 剩下的段照常收片；`test_spec21` / `test_spec37` / `test_grok_engine` 不回退。
3. `跑批日志.txt` 里不再出现「页面上有 N 个视频，判不出哪个是本段」这种在首页上数视频的假警报。
