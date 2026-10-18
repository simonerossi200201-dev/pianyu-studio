# 27 · 工程文件：把「过时」的任务归档出去，需要时再导回来

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/00-architecture.md`、
> `specs/08-generate-panel.md`，再读 `orante/workspace.py`、`orante/db.py`、`orante/tasks.py`，
> 再读本规格。改前备份成 `*.bak-0910proj`。
> 报告写到 `specs/reports/27-project-archive.md`。中文注释。

## 目标

做完的任务不要一直堆在软件里。**把它们连同元数据归档到当天的工程文件夹，
从库里移出去，软件打开又干净又快；哪天要回头看，导一个（或一批）工程文件回来就行。**

---

## 1. 工程文件长什么样

一个批次（`batches` 表的一行 = `D:\1_PROJECT\<日期>\<版本>\`）= 一个工程文件，
就写在那个批次目录里：

    D:\1_PROJECT\2026-09-09\A\2026-09-09A.orproj.json

（旁边已经有 AutoSub 的 `2026-09-07A.asproj.json`，命名风格保持一致。）

**内容**：

    {
      "format": 1,
      "orante": "2.0.0-dev",
      "exported": "2026-09-10T11:40:00",
      "batch": { ...batches 表那一行... },
      "tasks": [ { ...tasks 表整行：id/name/engine/account_id/type/copy/image_source/
                   duration_pref/status/dir/row_order/priority/meta/created/updated... } ],
      "manifests": {
        "T0014": { "segments": N, "images": N,
                   "clips": [{"s":1,"file":"clips/T0014-s001-p09.mp4",
                              "bytes":15040233,"sha256":"…","dur":15.04}],
                   "copy_words": 462, "track": {...} }
      },
      "jobs": [ {"id":"J00133","task_id":"T0014","kind":"arrange","status":"done"} ]
    }

**不打包视频、不打包图片。** 它们本来就在同一个目录里，复制一份纯属浪费几百 GB。
只记相对路径 + 大小 + sha256，导入时用来校验「文件还在不在、有没有被动过」。

`manifest.json` 本身也不抄进来（它就在任务目录里），只抄一份摘要，
让导入后不用逐个读 manifest 就能把列表画出来。

---

## 2. ④ 生成面板「过时」档要加的按钮

顶部工具条（和「进行中 / 过时 / 回收」那一排同一行）：

    [ 导出选中… ]  [ 导出全部… ]  |  [ 清空选中 ]  [ 清空全部 ]  |  [ 导入工程… ]

规则：

- **导出**：按批次分组写 `.orproj.json`（选中的任务跨了 3 个批次就写 3 个文件）。
  已存在同名文件 → 合并（同 id 任务以本次为准），不要覆盖成只剩这几条。
  导完 toast：`已导出 2 个工程文件 · 17 个任务 → D:\1_PROJECT\2026-09-09\A\ 等`。
- **清空**：只从数据库删 `tasks` / `jobs` 行，**一个磁盘文件都不许删**。
  清空前强制检查：这些任务所在的批次**必须已经有 `.orproj.json` 且包含它们**，
  否则弹窗 `这 5 个任务还没导出，先导出再清空` 并给一个「现在就导出并清空」的按钮。
  **绝不允许在没有归档的情况下把记录删掉。**
- **导入**：多选 `.orproj.json`（支持一次选多个），载入后进「过时」档。

## 3. 自动归档（可选，默认关）

设置里加：

    archive.auto_on      false     # 默认关，用户自己开
    archive.auto_days    7         # 完成超过 N 天

开了之后，Orante 启动时扫一遍：`status` 是 `finished`、且 `updated` 超过 N 天的任务，
自动导出到它自己的批次目录并从库里移出，启动后 toast 一句
`自动归档了 12 个任务（2026-09-01 ~ 09-03），要看的话「过时」里导入`。
**永远只归档 `finished`**，`collected` 的不动（那还没验收完）。

---

## 4. 导入之后是什么状态

- 导入的任务标 `archived = 1`（`tasks` 表加这个字段，`db.py::_migrate()` 里补），
  在「过时」档里显示，行首一个小小的📦标。
- **只读**：不能生成、不能重生、不能改配图。⑤ 收片可以正常打开回看视频墙
  （文件都还在原地）。
- 行尾一个「解冻」按钮：点了把 `archived` 清掉、状态退回 `collected`，就又能重生了。
  解冻要确认一次。
- **文件不在了**怎么办：导入时逐个查 `dir` 存不存在。不存在 → 行首标🚫，
  灰字 `文件夹不在了（可能挪盘或删了）`，元数据仍然可看（文案、段数、用了哪些图）。
  clips 的 sha256 对不上 → 那一段标「文件已改动」，不阻止导入。
- **id 冲突**：库里已经有同 id 的任务 → 跳过，最后汇总里列出来，
  不要覆盖当前正在做的任务。

---

## 5. 接口

    POST /api/project/export   body {"task_ids":[...]}  或 {"batch_ids":[...]}  或 {"all_finished":true}
    → {ok, files:[{path,tasks,bytes}], warnings:[]}

    POST /api/project/clear    body {"task_ids":[...], "force":false}
    → {ok, removed:N}  |  {ok:false, need_export:["T0010","T0011"]}

    POST /api/project/import   body {"paths":["D:\\...\\2026-09-09A.orproj.json", ...]}
    → {ok, imported:N, skipped:[{id,why}], missing_dir:[...], changed_files:[...]}

    POST /api/project/unfreeze body {"task_ids":[...]}

## 6. 验收

1. 把 T0010~T0013 导出 → `D:\1_PROJECT\2026-09-07\B\2026-09-07B.orproj.json` 生成，
   打开能看懂，里面**没有 base64 视频**，只有路径和 sha256。
2. 「清空选中」→ 库里没了，**磁盘上一个文件都没少**（导出前后对 `clips` 目录做 `dir /s` 比一遍）。
3. Orante 重启 → 生成面板干净，只剩在做的任务。
4. 「导入工程…」一次选两个 `.orproj.json` → 都进「过时」，带📦标，只读，
   ⑤ 收片能打开回看视频墙。
5. 把其中一个任务目录改个名 → 重新导入，那一行标🚫且灰字说明，其余正常。
6. 「解冻」一个任务 → 能重生了。
7. 没导出就点「清空选中」→ 被拦住，弹窗给出「现在就导出并清空」的选项。
