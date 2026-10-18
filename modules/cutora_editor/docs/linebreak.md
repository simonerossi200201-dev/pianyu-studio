# 拉丁断行优先级

`cutora.subtitle.layout.break_lines` 在 `line_break=space` 且 `max_lines=2`、
`balance_lines` 打开时，用 `_best_two_line_split` 给每个空格切分打分
`(bad_end, not_punct, spread)`，取最小。因此**当前代码的优先级**是：

1. **避免行尾冠词 / 介词**（`resources/data/linebreak_<lang>.json` 的 `no_break_after`；en / it 有词表，其它语言缺省为空，只走空格 + 标点）
2. **标点后优先断**（`, . ; : — …`）
3. **两行长度尽量均衡**

不是「标点 > 避免行尾 > 平衡」。g09 seg1（`Friends arrived,` / 下一行更长）是第 2 条压过第 3 条的例子。

两行都装不下时退回 `_greedy_pack`：行数到 `max_lines` 之后，余下的词全部追加到最后一行。最后一行可以超过 `max_chars_per_line`，渲染器按 `segment.lines` 原样画，画面裁掉左右溢出。这是已知错误，g09 seg3 把它冻住，计划 T04e 随风格包溢出策略一起改。
