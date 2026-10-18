# 25 · 任务状态不许倒退（新任务在 ④ 生成里凭空消失）

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/00-architecture.md`、
> `specs/01-tasks-panel.md`、`specs/08-generate-panel.md`，再读本规格。
> 改前每个文件备份成 `*.bak-0909status`。报告写到 `specs/reports/25-status-monotonic.md`。
> 中文注释。不要问问题。

## 现象

用户新建 T0014，① 任务里准备好、② 描述语写好、③ 编排点了「应用」，
**④ 生成面板里却没有这个任务**，像凭空消失。

## 根因（已定位）

`ui/js/panels/generate.js:4`

    const ACTIVE_ST = new Set(['arranged', 'generating', 'collected']);

T0014 在库里的状态是 `described`，所以被过滤掉了。而它的 Job 历史是：

    J00131  describe  done
    J00132  expand    running
    J00133  arrange   done   → set_status("arranged")   ✔
    J00134  describe  done   → set_status("described")  ✘ 把状态拽回去了

`orante/services/describe.py:295` 的 `_mark_described()` 无条件
`taskmod.set_status(tid, "described")`；而 `orante/tasks.py:141` 的 `set_status`
只是一条 UPDATE，**完全不看新旧先后**：

    def set_status(task_id, status):
        if status not in STATUS_ORDER: raise ...
        db.run("UPDATE tasks SET status=?, updated=? WHERE id=?", ...)

`STATUS_ORDER = ["draft","prepared","described","arranged","generating","collected","finished","trash"]`
明明就在同一个文件的第 17 行，却没有人拿它做单调性检查。
结果：任何一次补跑的小活儿（补描述语、补图、扩图）都能把任务的进度往回推，
任务就从 ④ 生成里消失，用户完全看不出为什么。

## 要做的

### 1. `set_status` 加单调性（`orante/tasks.py`）

    def set_status(task_id: str, status: str, force: bool = False) -> str:
        """把任务状态推进到 status。默认只许沿 STATUS_ORDER 往前走。

        往回退是真实需求（打回重做、进回收站），但必须显式 force=True，
        免得补描述语这种小活儿把整个任务的进度拽回去（0909 实际事故）。
        返回真正生效的状态。
        """

规则：

- `status` 不在 `STATUS_ORDER` → 照旧报错。
- `trash` **不受单调性限制**，任何时候都能进（回收是横切操作）。
- `force=True` → 直接写，并 `log` 一行 `状态回退 T0014 arranged → planned（force）`。
- 否则：新状态在 `STATUS_ORDER` 里的下标 **≥** 当前下标才写；小于就**不写**，
  并 log 一行 `跳过状态回退 T0014 arranged ← described`。**不要抛异常** ——
  补描述语本身是成功的，不能因为状态没动就把那个 Job 判失败。

把现有调用点过一遍，只有这些地方该带 `force=True`：

| 调用点 | 为什么要 force |
|---|---|
| 「打回 planned / 重生」把任务从 `collected` 拉回 `arranged` | 用户主动要重做 |
| 进回收站 / 恢复 | 横切操作 |
| ① 任务面板改了文案或图源，需要重新准备 | 内容变了，前面的成果作废 |

`describe` / `expand` / `refill` / `broll` 这些补活儿**一律不带 force**。

### 2. 补活儿干脆别改任务状态

更干净的做法，两条一起做：

- `describe.py::_mark_described()` 改成：**只有当前状态低于 `described` 时才推进**
  （靠上面的单调性自动实现，代码里那句 `set_status` 可以原样留着）。
- 检查 `expand.py` / `broll.py` / 补图相关的服务里有没有别的 `set_status`，
  有的话同样处理。任务进度只由 **prepare / arrange / generate / review** 这四个主步骤推进。

### 3. 生成面板不许让任务凭空消失（`ui/js/panels/generate.js`）

**任务无声消失是最难排查的一类 bug。** 「进行中」这一档加一条兜底：

- 状态是 `prepared` / `described`，**但 manifest 里每一段都有 `image`** 的任务，
  照样列出来，行内用灰字标一句
  `配图已齐，但状态还停在「描述语齐」—— 去 ③ 编排点一下「应用」`，
  并给一个直接跳到 ③ 编排的按钮。**不要给它「生成」按钮**（状态确实还没到）。
- 状态是 `draft` / `prepared` 且**没有配图**的，仍然不列（那是真的还没准备好）。

### 4. 卡住的 Job 要看得见

`J00132 expand` 在库里一直是 `running`，界面上没有任何提示。
生成面板的账号那一行（`TIK-G-SIMONE · 空闲`）改成：如果这个账号上有 `running` 的 Job
**超过 10 分钟没有新日志**，显示 `TIK-G-SIMONE · 卡住 J00132 expand 23分钟`，
点一下能停掉它。**账号显示「空闲」但锁其实被一个僵尸 Job 占着**，
会让后面排队的任务莫名其妙起不来。

## 验收

1. 对 T0014 跑一次 `describe`，跑完查库：`status` 仍是 `arranged`，没有被拽回 `described`；
   日志里有一行 `跳过状态回退`。
2. T0014 出现在 ④ 生成的「进行中」里，可以正常勾选生成。
3. 手工把 T0014 的状态改回 `described`（`UPDATE tasks SET status='described'`），
   重开生成面板：它**仍然出现**，带灰字提示和跳转 ③ 编排的按钮，没有「生成」按钮。
4. 「打回 planned」这条路仍然能把 `collected` 的任务退回去（说明 `force=True` 接对了），
   日志里有 `状态回退 …（force）`。
5. 把某个任务丢进回收站，状态能变 `trash`（不受单调性挡）。
6. 造一个假的 running Job（10 分钟不写日志），账号行显示「卡住」并且能点停。
