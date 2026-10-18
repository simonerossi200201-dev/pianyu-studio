# 82 · Flow 出状况时说人话：项目页 404、账号被限流

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/57-flow-credit-audit.md`、
> `specs/58-run-to-completion.md`、`specs/77-multi-instance.md` 和它们的报告，再读
> `orante/engines/flow_api.py`（`FlowEngine.__init__`(350) 里解析 `project_url` 那段、`connect`(387)、
> `health`、`_ensure_page`、`_open_own_page`、`_wiz_of`、`_reject_snippet`、`_REJECT_HINT`、
> `_REJECT_ERR`、`eb1hJf` 提交与重试那一段）、`orante/accounts.py`（`health`、`open_login`、`extra`）、
> `orante/services/generate.py`（选账号、提交循环、本轮汇总那几行、结尾的 `✓ 完成`）、
> `orante/locks.py`、`ui/js/parts/accounts.js`、`ui/js/panels/generate.js`，再读本规格。
> 改前备份 `*.bak-0921flowerr`。报告写到 `specs/reports/82-flow-errors-in-plain-words.md`。中文注释。

## 用户原话

> 打开出来的浏览器却并不能正常的显示出来专案，这会让人误以为添加进来的新的 Flow 的链接是无效的，其实是有效的，可以正常生成的。
> 还有就是昨天遇到的那种 Flow 受限的情况，是不是得有个提示，当前 Flow 账号过载受限，请等待一会儿，暂时使用其他账号替代之类的，**不然人家还以为我这个软件写的有问题**。

两件事的共同点：**软件其实知道发生了什么，但要么没说，要么说的是只有开发者看得懂的话。**

---

# 第一部分 · 项目页 404，但生成正常

## 现场

TRE-1（端口 9301）的任务专用标签页停在 `flow.google.com/404?reason=project`，页面上写着「找不到專案」。
**同一时刻 Orante 日志显示连接、提交都正常**，片子也生成出来了。

## 为什么会这样

`_open_own_page` 直接 `goto(self.project_url)`，然后**只等 `WIZ_global_data.SNlM0e`（XSRF）出现**。
Flow 的 404 页也是同一个前端应用，照样带着 XSRF，所以引擎认为"就绪了"。
而提交接口用的是 `__init__` 里从 URL 解析出来的 `project_id`，拼成 `source-path` 发出去 ——
**接口那边不在乎这个项目页在 UI 里能不能打开**，所以生成正常。

结果就是：后台一切正常，前台挂着一张「找不到專案」，用户以为链接坏了。

目前所有 Flow 账号存的链接格式都是 `/project/<uuid>/collection/<uuid>`，格式本身没错。
页面打不开，常见原因只有两个：

1. **collection 那一段失效了**（合集被删、或者 Flow 改版后不再用这个路径），
   去掉 `/collection/<uuid>` 只留 `/project/<uuid>` 就能打开；
2. **项目属于另一个 Google 账号** —— 在自己常用的浏览器里复制的链接，
   但 TRE-1 那个 Chrome 里登录的是别的账号。

## 要做的

### A. 打开之后看一眼落地地址

`_open_own_page` 在拿到 XSRF 之后读 `pg.url`：

- 不含 `/404` → 一切照旧。
- 含 `/404`（或 `reason=project`）→ 走 B。

### B. 自动诊断，能修就修

1. 原链接带 `/collection/<uuid>` → 改试 `https://flow.google.com/project/<pid>`。
   打开了 → 结论「合集那一段失效」。
2. 还是 404 → 结论「这个项目在当前登录的账号里看不到」。
3. 诊断结果写进 `account.extra["project_page"] = {"ok": false, "why": "collection"|"not_visible", "at": ISO}`，
   打开成功就写 `{"ok": true, "at": …}`。

### C. 不许把 404 留在屏幕上

诊断完之后，这个任务专用标签页要落在一个**看起来正常**的地方：

- 结论是 collection 失效 → 就停在 `/project/<pid>`（能看到项目，能看到生成进度）。
- 结论是项目看不到 → 导航到 Flow 首页 `https://flow.google.com/`（项目列表），
  **重新等一次 XSRF**，用这个页面继续干活。
- 两种情况**都不能影响生成**：提交用的 `src_path` 不变，只是换了个看得见的页面挂着。

### D. 日志和界面说人话

日志一行（只在第一次发现时打）：

    · Flow 项目页打不开（找不到专案），但生成接口正常，不影响出片。
      原因：链接里的合集那一段失效了，已自动改用项目地址。

或者：

    · Flow 项目页打不开，但生成接口正常，不影响出片。
      原因：这个项目在 TRE-1 登录的 Google 账号里看不到。浏览器里没法看进度，
      要修好就在 TRE-1 那个 Chrome 里打开 Flow、新建或打开一个项目，把地址栏复制到账号设置里。

账号抽屉里那个账号的状态行，`project_page.ok` 为假时显示一个黄色小标签
`项目页打不开 · 生成正常`，鼠标悬停显示上面那段原因和处理办法。

结论是 collection 失效时，账号抽屉给一个按钮 <span class="btn-ref">改成项目地址</span>，
点了把存的 `project_url` 去掉 `/collection/<uuid>` 那段。**不要自动改库** —— 用户存的东西由用户点头再动。

### E. 加链接的时候就查，别等到生成

账号抽屉 / 向导里<span class="btn-ref">我登好了，检测一下</span>（`accounts.health`）对 Flow 账号也跑一遍 A~B：
打开 `project_url`，看落地地址，把结论显示在检测结果里。
这样用户**粘链接的当下**就知道能不能在浏览器里看到项目，而不是生成时被吓一跳。

---

# 第二部分 · 账号被 Google 限流

## 现场（昨天 T0044）

15:56~15:58 用 TRE-1 连续提交 8 段，全部成功。两分钟后 16:00 再跑：

    · eb1hJf 失败 status=200 err=[7, None, [['type.googleapis.com/google.rpc.ErrorInfo', ['PUBLIC_ERROR_UNUSUAL_ACTIVITY']]]]，重试 1/2
    · 重新取 token
    · eb1hJf 失败 status=200 err=[7, None, …['PUBLIC_ERROR_UNUSUAL_ACTIVITY']]]]，重试 2/2
    · 重新取 token
    · eb1hJf 失败 …
    ✗ s018 提交失败：Flow 接口 eb1hJf 调用失败
    …
    本轮：提交 0，收片 0，失败 2 …
    ✓ 完成

`J00406` 到 `J00411` 六个日志**一字节不差**（都是 2088 字节）——用户换着法子重试了六次，每次撞同一堵墙，
软件一次都没告诉他发生了什么。

## 这段日志的四个问题

1. **报错是给开发者看的**：`eb1hJf 失败 status=200 err=[7, None, [['type.googleapis.com/…`。
   用户看到只会觉得软件坏了。
2. **结论是错的**：「Flow 接口 eb1hJf 调用失败」听起来像是 Orante 的 bug，实际是 Google 那边限流。
3. **重试是有害的**：`PUBLIC_ERROR_UNUSUAL_ACTIVITY` 是 Google 的反滥用节流，一般持续十几分钟到几小时。
   每段隔 2~5 秒重试 2 次，一轮下来又多打了好几次请求——**只会让"异常活动"更异常**。
4. **结尾是绿色的 `✓ 完成`**，而这一轮失败了 2 段、一段都没做成。

## 要做的

### F. 给 Flow 的错误码建一张表，每种说人话、各有各的处理

在 `flow_api.py` 里集中一个 `FLOW_ERRORS` 表（从现在散落在 `_REJECT_HINT` / `_REJECT_ERR` / 重试逻辑里的判断收拢过来）：

| 错误码 | 人话 | 怎么处理 |
|---|---|---|
| `PUBLIC_ERROR_UNUSUAL_ACTIVITY` | **这个 Flow 账号被 Google 暂时限流了**（短时间内提交太多） | 见 G：立刻停、冷却、提示换号。**不重试** |
| `PUBLIC_ERROR_UNSAFE_GENERATION` 及其它审核类 | 画面被 Flow 审核拦下了 | 现有逻辑：自动换图 |
| `PUBLIC_ERROR_VIDEO_GENERATION_TIMED_OUT` | Flow 服务器那边生成超时了 | 可以重试，按现有退避 |
| HTTP 429 / `RESOURCE_EXHAUSTED` / high demand 类 | Flow 服务现在很忙 | 退避重试（30s → 60s → 120s），不是 2 秒 |
| 额度类 | 额度不够 | 57 号现有逻辑 |
| 其它没见过的 | Flow 返回了一个没见过的错误（原始码附在后面） | 按现有逻辑，但**原始码进日志的第二行**，第一行说人话 |

日志统一成**两行**：第一行人话，第二行缩进的灰色原始信息（给排查用）：

    ⏸ s018 没提交成：TRE-1 这个 Flow 账号被 Google 暂时限流了（短时间内提交太多）
        [PUBLIC_ERROR_UNUSUAL_ACTIVITY · eb1hJf · err=7]

### G. 限流：停下来、说清楚、给出路

碰到 `PUBLIC_ERROR_UNUSUAL_ACTIVITY`：

1. **不重试**。这一段不算失败，状态回到 `planned`，`error` 写「账号限流中」。
   这一轮里**同一个账号的其余段也不要再提交了**（一个被限了，下一个也会被限，白撞墙）。
2. 账号记冷却：`account.extra["throttled_until"]`。第一次 **20 分钟**，
   冷却期结束后 30 分钟内又被限 → 翻倍（40 → 80），上限 **2 小时**；
   冷却期过后连续成功提交 5 段 → 计数清零，下次又从 20 分钟起。
3. **跨窗口生效**：冷却写在库里，77 号多开的其它窗口选到这个账号时也知道它在冷却。
4. 调度器选账号时**跳过冷却中的账号**；这个任务的账号在冷却中 → 任务不提交，
   在 ④生成 那一行显示 `⏸ 账号限流中 · 还剩 17 分钟`。
5. **冷却到了自动恢复**，不需要人去点。

### H. 界面上的提示：让人一看就懂不是软件的问题

④生成 面板顶上浮出一条横幅（不是一闪而过的 toast）：

    ⏸ Flow 账号 TRE-1 被 Google 暂时限流了
       这是 Google 那边的保护机制：短时间内提交太多，账号会被暂停一阵子。
       不是软件的问题，也不是你的链接有问题。一般 20 分钟左右会自己恢复。

       [ 换一个 Flow 账号继续 ▾ ]   [ 等它恢复，到时自动接着跑 ]   [ 知道了 ]

- **换一个 Flow 账号继续**：下拉列出**同引擎（Flow）**、未在冷却、已登录的账号。
  选了就把受影响任务的账号换过去，立刻接着提交。
  只列 Flow —— Flow 和 Grok 的人声不一样，同一条片子里混用会跳戏。
- **等它恢复**：什么都不改，冷却一到自动接着跑，横幅上的倒计时实时走。
- 没有可换的账号 → 下拉那一项灰掉，写「没有别的可用 Flow 账号」。
- 账号抽屉里那个账号：`⏸ 限流中 · 还剩 17 分钟`，橙色。

### I. 两个账号接连被限 = 多半不是账号的事

换到第二个 Flow 账号后 **10 分钟内它也被限流** → 这时候大概率是**网络 / IP 层面**被盯上了，
继续换第三个号只会把第三个也搭进去。于是：

- **停下所有 Flow 提交**，横幅改成：

      ⏸ 连着两个 Flow 账号都被限流了
         多半是同一个网络在短时间里提交太多，Google 按网络限的，换账号也没用。
         建议先暂停 30 分钟到 1 小时。现在不会再自动提交了。

- 这种状态下"换账号继续"那个按钮不出现。

### J. 结尾别再说谎

`generate.py` 结尾那句 `✓ 完成`：

- 这一轮 `失败 > 0` 或者还有段因限流没提交 → `⚠ 这一轮结束，还有 2 段没做成（账号限流中）`，**不是绿色勾**。
- 全部成功才是 `✓ 完成`。

---

## K. 测试 `tools/test_spec82.py`（**先设 ORANTE_DATA**，FakePage / FakeRPC，不碰真账号）

项目页：

- FakePage `goto(project_url)` 落在 `/404?reason=project`，改 `/project/<pid>` 能打开 →
  `project_page.why == "collection"`，标签页停在 `/project/<pid>`；日志含「合集那一段失效」。
- 两种都 404 → `why == "not_visible"`，标签页被导航到 Flow 首页，XSRF 重新取过一次；日志含「看不到」。
- 以上两种情况下，后续提交用的 `src_path` **和原来一样**（生成不受影响）。
- 落地地址正常 → `project_page.ok == True`，不导航、不多打日志。
- `accounts.health` 对 Flow 账号返回里带 `project_page` 结论。
- 「改成项目地址」接口 → `project_url` 去掉了 `/collection/<uuid>`，其余不变；**没点就不改**。

限流：

- FakeRPC 对 `eb1hJf` 返回 `PUBLIC_ERROR_UNUSUAL_ACTIVITY` → **只调用 1 次**（不重试）；
  段状态 `planned`、`error` 含「限流」；`throttled_until` 约为 now+20 分钟。
- 同一轮里这个账号后面的段**一个都没再提交**。
- 冷却结束后 30 分钟内再被限 → 冷却 40 分钟；再一次 → 80；上限 120。
- 冷却期间调度器不选这个账号；冷却到期后自动恢复可选。
- 换到第二个账号、10 分钟内它也被限 → 进入"全局暂停 Flow"状态，不再自动提交任何 Flow 段，
  返回给前端的状态里有 `network_throttled: true`。
- `PUBLIC_ERROR_VIDEO_GENERATION_TIMED_OUT` → 仍然重试（回归现有行为）。
- 审核类错误 → 仍然走自动换图（回归）。
- 日志：第一行不含 `eb1hJf` / `type.googleapis.com` 这类字样，第二行（缩进）含原始码。
- 结尾：有失败时最后一行**不以 `✓` 开头**。

回归：`python -m pytest tools/` 全过，`test_flow_api` / `test_spec57` / `test_spec58` / `test_spec77` 不回退。

## 验收

1. TRE-1 用现在这个链接跑一段：Chrome 里那个标签页**不再停在「找不到專案」**，
   日志里有一行说清楚为什么打不开、而且写明"不影响出片"；账号抽屉里 TRE-1 有个黄色小标签。
2. 在账号抽屉点 TRE-1 的「检测」：检测结果里直接告诉你项目页能不能打开、为什么。
3. 模拟一次限流（或者真碰上）：日志第一行是人话，不再出现 `eb1hJf` 那串；**不再连着重试**；
   ④生成 顶上有横幅，写明是 Google 限流、不是软件问题，给出换号 / 等待两个出路。
4. 点「换一个 Flow 账号继续」→ 只列出能用的 Flow 账号，选了立刻接着跑。
5. 点「等它恢复」→ 倒计时走完自动接着跑，不用再点。
6. 这一轮有段没做成时，结尾不再是绿色的 `✓ 完成`。
