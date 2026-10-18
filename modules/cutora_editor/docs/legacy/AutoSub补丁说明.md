# AutoSub 长文案补丁说明（11.2 A–D）

升级 AutoSub 后按本文重打。改动只在 `core\script_stitcher.py`（外加无头 `allow_missing` 一行，默认关）。
备份：`core\script_stitcher.py.bak-0824-s7`。

没有 `_order.json`、没有 Tik 文件名时，行为与打补丁前一字不变（Normal 回归）。

## A · `GROUP_RE` / `TIK_RE` / `scan_groups`

文件约 58 行旁，原：

```python
GROUP_RE = re.compile(r"^(\d+)\s*(?:([a-zA-Z]+)|\(\s*(\d+)\s*\))?$")
```

加并行：

```python
TIK_RE = re.compile(r"^(Tik\d+)-s(\d+)(?:-p\d+)?(?:-([a-zA-Z]))?$", re.I)
```

`scan_groups`：先 `GROUP_RE`，不中再 `TIK_RE`。Tik 组名统一成 `Tik008`，组内按 s 号排。老命名仍用原来的三元组排序键。

## B · 组排序键不能 `int()`

```python
def _gkey(k):
    s = str(k)
    return (0, int(s), "") if s.isdigit() else (1, 0, s.lower())
for num in sorted(groups.keys(), key=_gkey):
```

数字组照旧在前按数值升序，Tik 组在后按名字。

## C · `parse_scripts` 显式编号认 Tik

```python
m = re.match(r"^(\d+|Tik\d+)\s*[|:：\t]\s*(.+)$", p, flags=re.S | re.I)
```

`lstrip("0")` 只能对纯数字做，用 `_norm_key`。

## D · `_order.json` 钉死顺序

`AnalyzeWorker`「第二步 顺序纠正」：有 `_order.json` 且名单对得上 `clips[i]["name"]`（带或不带 `.mp4` 都认）→ 用名单顺序，打日志：

```
⛓ 组Tik001: 已按 _order.json 钉死顺序，跳过自动纠正
```

这句是补丁 D 生效的唯一凭据。对不上就退回自动纠正并打警告。

名单是 clips 的真子集时**截断**（备份 take `-b` 不进成品）。

`plan_group_dedup(..., pinned=True)` 时跳过结尾按 `s_start` 再排一次，否则 ASR 会把钉死的顺序打乱。

`AnalyzeWorker` 结束时自己写 `_stitched/cutlist.json`（GUI 原来写，无头原来漏了）。

`PreflightWorker`：组名不全是数字时 `check_gaps=False`，避免 `int("Tik001")`。

## 无头 stitch

`autosub_cli.op_stitch` 增加 `allow_missing`（默认 false）。长文案传 true，有缺失仍编码，和 GUI 一致。Normal 命令行默认行为不变。

## 升级后必做

重跑第 8 期验收 ②：人为换两段位置，日志必须再出现「已按 _order.json 钉死顺序」，成品顺序仍正确。名单和 `clips[i]["name"]` 对不上是**静默失效**。
