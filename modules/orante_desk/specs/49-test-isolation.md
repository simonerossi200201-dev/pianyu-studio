# 49 · 测试污染了真实任务库：隔离 + 清理

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `orante/paths.py`、`orante/db.py`、
> `tools/test_wenan_norm.py`、`tools/test_track.py`（前 30 行）、`tools/test_spec22.py`（前 30 行），
> 再读本规格。**先做完 48。**
> 改前备份 `*.bak-0911iso`。报告写到 `specs/reports/49-test-isolation.md`。中文注释。
> **禁止**在真实库 `data/orante.db` 上跑任何测试；本规格第 B 节的清理脚本要先 `--dry-run` 给用户看。

## 现象

规格 48 跑完，用户打开 Orante，任务表里**凭空多了 6 个任务**：
`T1007~T1010 配图轨测`、`T2202/T2203 规格22`，图片来源指向
`C:\Users\Timco\AppData\Local\Temp\orante-task-22-…\images` 这种临时目录。

这些全是测试夹具（`test_track.py::_make_manifest` 和 `test_spec22.py::_make_task` 里
`INSERT INTO tasks` 的那几行），被写进了**真实**的 `data/orante.db`。

## 根因

隔离靠的是每个测试文件开头这三行：

    TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-…"))
    os.environ["ORANTE_DATA"] = str(TEST_DATA)
    from orante import db, …

而 `orante/paths.py:20` 在**首次 import 时**就把 `DATA` 定死了：

    DATA = Path(os.environ["ORANTE_DATA"]).resolve() if os.environ.get("ORANTE_DATA") else ROOT / "data"

规格 48 的验收命令把四个文件放进**同一个 pytest 进程**：

    python -m pytest tools/test_wenan_norm.py tools/test_split.py tools/test_track.py tools/test_spec22.py

pytest 按顺序收集，第一个被收集的 `test_wenan_norm.py` 里 `from orante import paths`
**没有先设 `ORANTE_DATA`** → `paths.DATA` 锁死成 `ROOT/data`（真实库）。
后面 `test_track.py` / `test_spec22.py` 再设环境变量已经晚了，它们的 `db.init()` 和 `INSERT`
全部落进真实库。以前每个测试单独 `python tools/test_xx.py` 跑，所以从没露出来。

## 要做的

### A. 隔离要三道保险，缺一不可

1. **`tools/conftest.py`**（新建）：pytest 收集任何测试模块之前先执行，里面
   `os.environ.setdefault("ORANTE_DATA", tempfile.mkdtemp(prefix="orante-pytest-"))`。
   这样一个进程里不管收集顺序如何，`paths.DATA` 永远不会指向真实库。
2. **`orante/paths.py`**：`DATA` 改成函数 `data_dir()`，每次调用时读环境变量；
   `DB_FILE` 等派生路径同样改成函数或 property。全项目 `paths.DATA` / `paths.DB_FILE`
   的引用一起改（grep 一遍，`orante/`、`tools/`、`ui/` 后端都要查）。
   如果改动面太大，退一步：保留常量，但加 `paths.reset()`，`db.conn()` 在开连接前先
   `paths.reset()` 一次；报告里说明选了哪种。
3. **`orante/db.py::conn()` 加安全网**：如果 `"pytest" in sys.modules` 或 `sys.argv[0]` 是
   `tools/test_*.py`，而 `paths.DB_FILE` 落在 `ROOT/data` 下 → 直接 `raise RuntimeError(
   "测试进程不许碰真实库 data/orante.db，先设 ORANTE_DATA")`。宁可测试挂掉，也不许再污染。
4. `tools/test_wenan_norm.py` 补上和其他测试一样的三行头（虽然它不碰库，但规矩要统一）。
   顺手 grep `tools/test_*.py`，凡是 `from orante` 之前没设 `ORANTE_DATA` 的都补上。

### B. 清理脚本 `tools/purge_test_tasks.py`

- 只删满足**全部**条件的任务：`name in ("配图轨测", "规格22")` **且**
  `image_source` 或 `dir` 位于 `%TEMP%` 下的 `orante-task-*` 目录。
  两个条件同时成立才删，避免误伤用户真实任务。
- 默认 `--dry-run`，只列出将删的 id/name/dir；加 `--yes` 才真删。
- 删的时候用 `tasks.delete(id, hard=True)`，并顺带清掉 `jobs` 等关联表里引用这些 id 的行
  （看 `_migrate` 里的外键，`PRAGMA foreign_keys=ON` 下该级联的级联，不级联的手动删）。
- 删完打印剩余任务数。**不动** `counters` 表（T 号发号器不用回退，跳几个号无所谓）。
- 报告里贴 `--dry-run` 的输出；**真删由用户自己执行**，你不要执行 `--yes`。

### C. 测试

- `tools/test_spec49.py`：
  - 在一个 pytest 子进程里先收集一个「不设 ORANTE_DATA 直接 import orante」的假测试模块，
    再收集一个会 `INSERT INTO tasks` 的模块 → 断言真实 `data/orante.db` 的 `tasks` 行数**前后不变**
    （用 conftest 兜住 + 安全网兜住，两条路径各测一次：一次让 conftest 生效，一次故意绕过 conftest
    验证 `db.conn()` 抛 `RuntimeError`）。
  - `purge_test_tasks.py --dry-run` 对一个临时库：塞入 2 条测试夹具 + 1 条真实样子的任务，
    断言只列出 2 条；`--yes` 后剩 1 条。
- 回归：`python -m pytest tools/`（**整目录一次性跑**）全过，跑完 `data/orante.db` 的 tasks 表
  行数和跑前一致（把这条也写进报告）。

## 验收

1. `python -m pytest tools/` 整目录跑一遍，真实任务表一行没多。
2. 故意写一个不设 `ORANTE_DATA` 的脚本 `from orante import db; db.init()` 用 `python -m pytest` 跑 →
   被安全网拦下，报错信息里有「先设 ORANTE_DATA」。
3. `python tools/purge_test_tasks.py --dry-run` 列出的正是那 6 个（T1007~T1010、T2202、T2203），
   一个真实任务都没列进去。
