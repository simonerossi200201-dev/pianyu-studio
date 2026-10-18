# 54 · 生成方式改成真正的「每个任务自己选」：Grok 走逐段首帧，Flow 走锚点+接片

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/36-anchor-bridge-foundation.md`、
> `specs/37-anchor-bridge-pipeline.md`、`specs/52-grok-bridge-single-image.md`、`specs/53-bridge-fail-accounting.md`
> 和它们的报告，再读 `orante/services/pipeline.py`（`mode_of` / `last_used_mode` / `remember_mode` / `set_mode`）、
> `orante/server.py`（`POST /api/tasks/{tid}/mode`）、`ui/js/panels/generate.js`
> （`genMode` 第 208 行、`modeOf` 第 354 行、`applyMode` 第 419 行、模板第 1420~1434 行）、
> `ui/js/panels/tasks.js`（① 任务表列定义）、`orante/tasks.py`、`orante/services/prepare.py`、
> 再读本规格。**先做完 53。** 改前备份 `*.bak-0912mode`。
> 报告写到 `specs/reports/54-per-task-gen-mode.md`。中文注释。

## 背景：为什么必须按引擎分开

探针 40 的 11 条实测（`data/capture/probe40/decision.json`、`b-rescore.json`）已经定论：
**Grok 没有真正的首尾帧入口**，拖两张参考图 + 提示词说明谁是首谁是尾，三种写法下
首帧↔起始图的距离从没低于 41（单图起片是 0~7）。52 号因此把 Grok 接片改成单图起于上一段尾帧。
而 Flow 的首尾帧是原生功能，36 号探针里两头都对得上。

结论：**锚点+接片这个模式对 Flow 成立，对 Grok 只剩「延长一段」的半吊子价值。**
用户的判断是对的：Grok 任务走逐段首帧，Flow 任务走锚点+接片。

## 现状：数据层已经是按任务了，UI 层在骗人

- `manifest.mode` 是任务级字段，`POST /api/tasks/{tid}/mode` 是任务级接口，
  `modeOf(task)` 也确实先读 `manifests[task.id].mode`。**后端不用改。**
- 但 ④ 生成工具栏那两个单选钮绑的是 `genMode`，它来自
  `store.settings['generate.mode']` —— **一个全局值**。所以：
  1. 单选钮显示的是「上次用的默认值」，**不是你选中那个任务的真实模式**；
  2. `applyMode(mode, selected.length ? selected : null)`：**一个任务都没选中时，
     `targets` 是 `rows.value.filter(t => eligible(t) && !isLive(t))`，也就是全部可生成的任务。**
     点一下单选钮，T0015、T0016、T0017 一起被改。
  3. 传了 `list` 时**跳过了 `!isLive` 过滤**——选中一个正在跑的任务再点单选钮，
     会把它跑到一半的模式改掉。

## 要做的

### A. ① 任务表加「生成方式」列（这是用户要的下拉菜单）

- 新列 `gen_mode`，下拉：`逐段首帧` / `锚点+接片`，另有 `跟随引擎`（默认值）。
- 存在任务行上（`tasks` 表加一列 `gen_mode TEXT`，`db.py::_migrate` 里加，允许空）。
- **`跟随引擎` 的解析规则**：Grok 账号 → `first_frame`，Flow 账号 → `anchor_bridge`。
  规则写成 `pipeline.default_mode_for_engine(engine)` 一个函数，注释里写明探针 40 的理由。
- 「开始准备（下图 + 拆段）」建 manifest 时，用这一列解析出的值写 `manifest.mode`，
  **不再用全局 `generate.mode`**。`last_used_mode` / `remember_mode` 保留但降级为
  「新任务在任务表里的列默认值」，不再决定已有任务。
- 改任务行的账号（Grok↔Flow）时，若 `gen_mode` 是 `跟随引擎`，模式跟着变；
  若用户显式选过，**不动**，只在该行打一个小提示图标
  `这个任务手动选了「锚点+接片」，但它是 Grok 账号`。

### B. ④ 生成的单选钮改成「当前选中任务」的开关

- `genMode` 不再来自全局设置，改成计算属性：选中恰好 1 个任务 → 它的 `modeOf`；
  选中多个且模式一致 → 那个值；多个且不一致 → 两个钮都不高亮，显示 `多个任务模式不同`。
  **一个都没选中 → 两个钮都不高亮且禁用**，旁边小字 `先选任务`。
- `applyMode` 的 `targets` **永远等于 selected**，删掉「没选就改全部」那条后路。
- `targets` 里过滤掉 `isLive(t)`；被跳过的在 toast 里点名
  `T0017 正在跑，没改（停止后再改）`。
- 每个任务卡片的标题行加一个小徽章显示它自己的模式（`逐段首帧` / `锚点+接片`），
  和进度条同一行，一眼能看出哪个任务是哪种。

### C. 锚点 → 逐段首帧 的切换要说清楚发生了什么

`set_mode(m, MODE_FIRST)` 现在只改 `m.mode` 就返回，`role` / `bridge` 字段留在段上当死数据。
功能上没问题（`is_anchor_mode` 一关，`wait_reason` / `can_enqueue` 立刻全放行），
**但用户不知道自己的已收片会怎样**。要补：

- `set_mode` 切到 `MODE_FIRST` 时返回一段 `hint`，内容按实际算：
  `已收的 N 段主片全部保留（主片本来就是逐段首帧出的）；M 段接片改用各自的配图重新生成`。
  切回 `MODE_ANCHOR` 同理，说明哪些段会变成接片。
- 前端把这段 hint 用确认对话框显示，**用户点确认才真的改**（现在是点了就改）。
- `role` / `bridge` / `wait_on` / `wait_why` 不删（切回来还要用），但
  `stamp_waits` 已经会清 `wait_on`/`wait_why`，保持。

### D. 测试 `tools/test_spec54.py`（**先设 ORANTE_DATA**）

- `default_mode_for_engine("grok") == "first_frame"`；`("flow") == "anchor_bridge"`。
- 建两个任务（一个 Grok 一个 Flow），`gen_mode` 都留 `跟随引擎` → 准备后
  `manifest.mode` 分别是 `first_frame` / `anchor_bridge`。
- 显式选了 `anchor_bridge` 的 Grok 任务，换账号后模式不变。
- `applyMode` 没有选中任务时不发任何请求（前端测试，`tools/test_spec54_ui.py` 或 mjs）。
- 选中一个 running 任务 → 它被跳过，返回里点名。
- 一个 `anchor_bridge` 任务有 6 段 collected（role=anchor）+ 6 段 planned（role=bridge），
  `set_mode(m, "first_frame")` 后：12 段的 `status` 一个没变，`can_enqueue` 对那 6 段
  planned 全部返回 True，hint 里的数字是「6 段主片保留 / 6 段接片改用配图」。
- 回归：`python -m pytest tools/` 全过。

## 验收

1. ① 任务表里每行能单独选生成方式；新建 Grok 任务默认逐段首帧，Flow 任务默认锚点+接片。
2. ④ 生成里不选任务时两个单选钮是灰的；选中 T0015 时钮反映的是 T0015 自己的模式；
   改它不影响 T0016 / T0017。
3. T0017（Flow，正在跑）在选中状态下点单选钮 → 不被改，toast 点名。
4. T0015 从锚点切到逐段首帧：弹确认框写明「6 段主片保留，6 段接片改用配图重新生成」，
   确认后 6 段待生成段立刻可进队（包括原来被卡住的 s010）。
