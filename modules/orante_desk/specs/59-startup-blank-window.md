# 59 · 空白窗口：首次 /api/state 失败后永不重试 + 第二个实例抢不到端口

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `orante/main.py`（起 uvicorn、开 pywebview 窗口那一段）、
> `orante/__main__.py`、`ui/js/app.js`（第 160~163 行 `app.mount('#app'); await refreshState();`）、
> `ui/js/store.js`（第 97~112 行 `window.refreshState`、`store.loaded`）、`ui/js/api.js`、
> `Orante2.bat`、`tools/13-Orante-Debug.bat`（我刚加的诊断脚本），再读本规格。
> 改前备份 `*.bak-0913blank`。报告写到 `specs/reports/59-startup-blank-window.md`。中文注释。

## 现象

用户打开 Orante，窗口画出来了但**里面什么都没有**：顶栏「版本」后面没有版本号、「账号 0」、
五个面板全空，只有底部 ①~⑤ 节点条是画出来的。

## 已确认的事实（Claude，2026-09-13 排查）

1. **后端和前端本身都是好的。** 我从 Chrome 打开 `http://127.0.0.1:9400/`，
   页面完整渲染：版本 2.0.0-dev、账号 7、任务表里 T0020 / T0021 都在、
   54 号新加的「生成方式」列显示「锚点 + 接片」。`/api/state` 返回 200，
   键有 `version / accounts / tasks / settings / jobs / root / flow_models / ok`。
2. `ui/js` 下 22 个 JS 文件全部 `node --check` 通过，零语法错误；
   `index.html` 的 script 标签齐全（含 54 号新增的 `parts/gen-mode.js`）。
3. `tools/13-Orante-Debug.bat` 的 import 自检 `IMPORT OK`，
   带控制台启动能正常起服务。**不是 57 / 58 号改坏的。**
4. 出事那两次是 `pythonw` 启动，`orante.log` 里只打到
   `Orante 2.0.0-dev → http://127.0.0.1:9400/` 这一行就没下文了，没有 traceback。
   两次启动只隔 17 秒（00:28:55 / 00:29:12）。
5. 我第一次用 Chrome 连 9400 时是 `ERR_CONNECTION_REFUSED` ——
   窗口还开着，端口上却没有任何东西在听。

## 根因（两条，都要修）

### 甲：首次加载失败后永远不重试

`ui/js/app.js` 结尾：

    app.mount('#app');
    await refreshState();

`refreshState()` 里 `const s = await API.get('/api/state' + q)` 一旦抛异常（服务还没听上端口、
或者这个实例根本没起来），**这个 await 直接 reject，没有 catch，没有重试**，
`store.loaded` 永远是 false。而 `app.mount()` 已经先执行了，所以：

- 顶栏、节点条这种不依赖数据的静态结构**画得出来**；
- 版本号、账号数、任务表**永远是空的**；
- 而且**一句错误提示都没有**，用户只看到一个空壳。

截图里的样子和这条完全对得上。

### 乙：第二个实例悄悄抢不到端口

用户看窗口是空的，自然会再双击一次（日志里 17 秒两次启动就是这么来的）。
第二个实例的 uvicorn 绑 9400 失败，但 `pythonw` 没有控制台、错误没进日志，于是：
两个窗口，一个后端。等用户关掉**先起来的那个**窗口（它才是真正带服务的），
剩下那个窗口的后端就没了——这就是我看到「窗口还开着但端口拒绝连接」的由来。

## 要做的

### A. 起动阶段要会等、会重试、会说话（`app.js` + `store.js`）

- `refreshState()` 的首次调用包一层 `bootstrap()`：失败就重试，间隔
  0.3s / 0.6s / 1.2s / 2.4s / 4s，**最多 20 秒**。成功即进入正常流程。
- 重试期间在页面正中显示一个轻量状态块：`正在连接后端…（第 N 次）`，
  不要用 toast（toast 会被自动关掉，用户看不见）。
- 20 秒还连不上 → 状态块变成**可操作的错误页**：
  `连不上后端 http://127.0.0.1:9400`，下面三个按钮：
  `重试` / `打开日志文件夹`（`data\logs`）/ `复制诊断信息`
  （复制 `location.href`、错误文本、`navigator.userAgent`、当前时间）。
  **绝对不要再出现「空壳窗口、一句话都不说」这种状态。**
- SSE 断线重连已经有了（`store.js` 第 124~134 行），不动；但 `_es` 首次建连失败
  也要走同一套提示，别静默。

### B. 单实例守卫（`orante/main.py`）

- 起 uvicorn 之前先探一下 9400：能连上说明已经有实例在跑 →
  **不再起第二个服务**，直接把窗口指向已有的那个（或者给一句
  `Orante 已经在运行，已为你打开现有窗口` 然后退出）。
- 端口被别的程序占着（不是 Orante）→ 在**窗口里**显示明确错误，
  不要只写进日志。判断方法：`GET /api/state` 看返回的是不是 Orante 的那几个键。
- `main.py` 里所有启动期异常都要 `faulthandler` + 写进 `data/logs/orante.log`，
  pythonw 模式下也要落盘。现在 banner 之后什么都没有，等于瞎了。

### C. 窗口要等服务真的听上了再加载

- pywebview 创建窗口、`load_url` 之前，先轮询 `http://127.0.0.1:9400/api/state`
  直到 200（或超时 20 秒）。超时就把窗口指向一个内置的错误页，
  内容和 A 节那个错误页一致。
- 这条和 A 节是两道独立的保险，**都要做**：A 管「页面已经加载了但后端还没好」，
  C 管「后端根本没起来就别加载页面」。

### D. 诊断脚本保留并补强

`tools/13-Orante-Debug.bat` 我已经加好了（import 自检 + 带控制台启动 +
`PYTHONFAULTHANDLER=1` + 输出落 `data\logs\orante-debug.txt`）。
补两件事：脚本开头先探 9400，如果已经有实例在跑就提示用户先关掉；
`Orante2.bat` 的注释里加一行指到这个脚本，下次出事用户知道该点哪个。

## 测试 `tools/test_spec59.py`（**先设 ORANTE_DATA**）

- `bootstrap()`：前 3 次 `/api/state` 抛错、第 4 次成功 → `store.loaded` 为 true，
  重试次数 4，页面上出现过「正在连接后端」且最终消失。
- 一直失败 20 秒 → 出现错误页，三个按钮都在，点「重试」重新走一轮。
- 单实例守卫：模拟 9400 已被 Orante 占用 → `main()` 不再 bind，走「打开现有窗口」分支；
  被非 Orante 程序占用 → 走错误分支且**有窗口级提示**。
- 窗口等待：mock 一个 2 秒后才 200 的 `/api/state` → `load_url` 在 200 之后才调用。
- 回归：`python -m pytest tools/` 全过。

## 验收

1. 后端故意延迟 5 秒再起 → 窗口显示「正在连接后端…」，起来后自动进入正常界面，
   不需要用户手动刷新。
2. 开着一个 Orante 再双击一次 → **不会**出现第二个空壳窗口。
3. 把 9400 用别的程序占住再启动 → 窗口里有明确错误和按钮，日志里也有。
4. 现在这个空壳场景不可能再出现：任何「连不上」的情况下窗口都必须有字。
