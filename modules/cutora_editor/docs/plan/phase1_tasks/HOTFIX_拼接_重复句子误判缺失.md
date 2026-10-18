# HOTFIX — 智能拼接：文案里重复出现的句子会把片段"搬"到第一次出现处，导致第二处被判缺失

> 独立小修，分支 `fix/stitcher-duplicate-sentence` 从 `main`（`T03c-done`）拉；做完合回 `main` 打 `stitcher-dup-fix`，再把 `main` 合进 `task/T13-command-bus` 后继续 T13c。先读 `docs/plan/01_GROK_通用规范.md`。
> 这是 Luca 生产数据里抓到的真实误判，Claude 已在 `D:\1_PROJECT\2026-09-07\B` 的缓存与 cutlist 里逐字核实过，根因确定，不需要再猜。

## 现象
`T0010-李良v01\clips\_stitched\缺失文案清单.txt` 报组 T011 缺「There is only one God.」（文案第 289–293 词），但 `T0011-s022-p08.mp4` 明明就是这句（ASR：`there is only 1 god`），且它按文件序号正好落在 s021（275–288）与 s023（293–311）之间。

## 根因（`cutora/batch/script_stitcher.py`）
1. 文案里这句话出现了**两次**：第 63–68 词（"First, there is only one God"）与第 289–293 词（"The Bible reveals: There is only one God."）。
2. `match_clip_global()`（1411 行）用 `difflib.SequenceMatcher.get_matching_blocks()` 做全局对齐，等长命中时它**固定取文案里最早的那一处**，完全不看片段的文件序号和前后邻居。于是 s022 被对到 63–68。
3. 接着"顺序纠正"把它从第 22 位搬到第 7 位（cutlist 注记 `↻ 顺序纠正(原第22位→第7位)`），又因为 s006（`there is only 1 god and the bible says…`，本来就覆盖 63–78）与它重叠，把 s006 **裁头 1.2 s**（s006 命中率因此掉到 74%）。
4. 第 289–293 词再没有片段能对上 → 写进缺失清单。

一句话：**歧义句子选位置时没用"文件顺序 + 邻居窗口"做裁决，默认取最早出现处。**

## 修法
在 `match_clip_global()` 之后、"顺序纠正"之前加一步**歧义裁决**（新函数 `resolve_ambiguous_positions(clips, script_tokens)`，纯函数，可单测）：
1. 对每个已命中的片段，用它命中的 token 序列在整段文案里找**全部**出现位置（`_find_all_occurrences(script_tokens, clip_hit_tokens)`，允许与主命中同样的模糊程度：把 `SequenceMatcher` 换成对每个候选起点做局部比对，命中率 ≥ 主命中率 − 0.05 的都算候选）。只有一个候选 → 不动。
2. 多候选时按以下优先级选：
   a. **邻居窗口**：按文件序号取前一个和后一个"无歧义且已命中"的片段，窗口 = `[prev.s_end − 3, next.s_start + 3]`；落在窗口内的候选优先（本例：s021 结束 288、s023 开始 293 → 选 289–293）。
   b. 窗口内没有 → 选让"顺序纠正"位移最小的候选（`|新位置 − 文件序号|` 最小）。
   c. 仍并列 → 选**尚未被其他片段覆盖**的候选（本例 63–68 已被 s006 覆盖）。
3. 裁决后再进入现有的顺序纠正与重叠裁剪逻辑；裁头/裁尾只在裁决完成后计算，避免像 s006 那样被误裁。
4. 日志：歧义片段在 cutlist `note` 里加 `⇄ 文案重复句, 按邻居窗口定位到第 289–293 词 (另一处 63–68 已由 s006 覆盖)`；缺失清单末尾加一行统计 `歧义裁决: N 处`。

## 顺手（同一分支，单独一次提交）
缺失清单里组 T010 / T013 的"缺失"都是括号里的经文出处 `(John 8:34-35)`、`(John 8:34–35) 16:13)`——配音从来不会念这些。加一个默认开启的开关 `ignore_scripture_refs=True`：匹配 `\(\s*[1-3]?\s*[A-Za-z]+\.?\s+\d+:\d+(?:[-–]\d+)?(?:;\s*\d+:\d+)?\s*\)` 的 token 串**不参与缺失判定**（仍保留在字幕文案里，不影响任何其他逻辑）。清单里对这类不再报缺失，改在末尾写一行 `已忽略 N 处经文出处`。GUI 的拼接对话框加对应勾选框，CLI `stitch` 加同名参数并进 `--describe`（**只加字段**，`tests/test_cli_describe_compat.py` 超集测试仍要过）。

## 测试（先写、先红、再改）
`tests/test_stitcher_duplicate_sentence.py`，**不用媒体文件**，直接用纯函数：
- 夹具：`tests/fixtures/stitcher_dup/copy.txt`（就是 `D:\1_PROJECT\2026-09-07\B\T0011-李良v02\copy.txt`，复制进仓库）和五个片段的词表（从 `T0011-李良v02\clips\_stitched\_asr_cache.json` 里抠出 s005 / s006 / s021 / s022 / s023 的 `words`，存成 JSON）。
- 断言：s022 的 `s_start == 289 and s_end == 293`；s006 的 `in_t == 0.0`（不裁头）且命中率 ≥ 0.95；缺失清单中**不含**「There is only one God」；s022 的 note 含 `⇄`。
- 第二个用例：同一句只出现一次的文案 → 行为与改前完全一致（把改前的 cutlist 片段存成期望值对比）。
- 经文出处用例：文案含 `(John 8:34-35)` 且没有片段念它 → 开关开时不报缺失，关时报。

## 验收
- [ ] 上述测试全绿，且报告附改动前红灯输出
- [ ] `golden --all --repeat 3` PASS。**g07 走的就是拼接**：若它的文案没有重复句，输出必须逐字节一致；若变了，停下报告，不要自己"修"
- [ ] Luca 真机：对 `D:\1_PROJECT\2026-09-07\B` 重新跑一次智能拼接（ASR 缓存还在，很快）→ 组 T011 不再报缺失，s022 落在 s021 与 s023 之间；T010/T013 只剩"已忽略经文出处"
- [ ] `pyflakes` undefined 0；`security_scan` 业务代码 0；报告 `docs/plan/reports/HOTFIX_stitcher_dup_report.md`

## 不要做
- 不动 ASR、不动渲染、不改 cutlist 的字段名（只加 note 文字与统计行）。
- 不把"顺序纠正"整个关掉——它对真正乱序的批次仍然有用，只是要在歧义时让位给邻居窗口。
