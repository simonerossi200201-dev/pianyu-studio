# UI golden rerecord log

## 2026-10-10 Q214 (先加后退役，不覆盖旧哈希)

授权：Luca 2026-10-10 选择「接受新画面，退役旧用例」。
新增 `u01c` `u02c` `u03f` `u04f` `u06b` `u07c` `u08c`，捕获函数复用旧用例。
`u01b` `u02b` `u03e` `u04e` `u06` `u07b` `u08b` 移进 `RETIRED`，`expected.sha256` 原文件不动。
`u05c` 仍是 2026-10-07 已批准的时间线用例。导出十二例未重录。
新哈希由这台机器第一次跑 `tools/ui_golden.py --all` 自动写入（没有 `--record`）。


Do not `--record` a signed case without Luca.

Fingerprint: PySide6=6.10.2 · DPI=96 · dpr=1.0
fonts_digest=80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f

## 2026-10-05 Q169 (先加后退役 u05 → u05b)

授权来源：Luca 2026-10-05 点名要求时间线片段改成实色色块、轨道头加把手、视频条显示缩略图。
u05 锁的就是 TimelineWidget 的样子，这次改的就是它 —— 按规矩不重录 u05：新增 `u05b`（捕获函数复用
`case_u05`），把 `u05` 移进 `RETIRED`（文件留在磁盘）。`u05b/expected.sha256` 由 Luca 在自己机器上
第一次跑 `RETEST_ONE.bat` / `python tools/ui_golden.py --case u05b` 时自动录（云端字体 / DPI 不同，不在云端录）。
`tests/golden/`（导出 mp4）未动。

## 2026-09-21 Q83 (先加后退役 u03e/u04e/u07b/u08b)

授权来源：`docs/plan/queue/Q83.md`。
Luca 2026-09-20 把 UI 基线的决定权交给 Claude（「你按照你的设计找最好的方案来吧」）。
这次右侧检查器重做是 Luca 点名要求 Claude 亲自做的。本任务只落地补丁，不改布局。

HEAD=`a2e9a12a984e5c531ff067e12ccd5b7a085d1913`（补丁提交；静帧在这棵树上录）。
Fingerprint unchanged: PySide6=6.10.2 · DPI=96 · dpr=1.0 · fonts 240 /
`80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`.

做法：新增 `u03e` / `u04e` / `u07b` / `u08b`，捕获函数复用 `case_u03` / `case_u04` /
`case_u07` / `case_u08`。`u04e` 的对照图读 `u03e/got.png`。`--record --case` 只录这四例。
然后把 `u03d` / `u04d` / `u07` / `u08` 移进 `RETIRED`（文件留在磁盘，没有重录）。
`tests/golden/`（导出 mp4）未动。

| case | sha256 | why |
|---|---|---|
| u03e | `2c747ee69bfbd006755db4af285df702b4b9079df0ade5f03a3e49ecd03b4247` | Q83 SubtitleStylePanel 280 |
| u04e | `37029e115740b7ccad3d0b4792368fc4cc982d8596af7221ad33d48f248c03b1` | same + 描边 click; diff vs u03e |
| u07b | `f2d9ad2696b5bcec7e58315c1439de93b6bff0c3b7721d1020f5bc7f290b4c99` | same panel at 480 |
| u08b | `7aa2cdfc336ce85b291bd990f81efc0004e1f5f9e03b18f21311101613ef9bfb` | Q83 text property panel |

u01b `556b5a4f13003fd6cac4065cb930f74917bfd05e1b28ab494a073fb082005e67` /
u02b `131bda934932c85b9069cc021894f5fd5866749544284451bd36f781b1bd6087` /
u05 `d0ca5c3aefd3cedcf702e48aa101dfce7dc0bb50ce57c4a8fd17445c2bd823ce` /
u06 `35cb5c9269fecb007716bce224f3d982aa4670561495a786c477214c2419b44e` unchanged.
Copies for Claude: `docs/plan/reports/Q83/`.

## 2026-09-21 Q58 (Luca 授权统一重录 u03d/u04d/u05/u07/u08)

Why: Q56 漏了 Q51，u03d/u04d/u07 当时停住。本条补上 Q50/Q51/Q53·Q69/Q60·Q64·Q65·Q66/Q36。
取证见 `docs/plan/reports/Q58_ui_diff/`。
Fingerprint unchanged: PySide6=6.10.2 · DPI=96 · dpr=1.0 · fonts 240 /
`80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`.

Record hashes matched the immediately preceding `--all` `got=` byte-for-byte;
`--all` after record and a second `--case u03d,u04d,u05,u07,u08` also matched.

| case | sha256 prefix | why |
|---|---|---|
| u03d | `570acb705c15` | inspector remodel (Q50/Q51/Q64/Q65/Q66) + slider/gray (Q45); 1100px one-shot |
| u04d | `474928379b6d` | same + qWait(160) settled layout; stroke below 1100px fold |
| u05 | `d0ca5c3aefd3` | Q69: no track_video fill, clip lift, 9-patch glow; playhead x=599–600 |
| u07 | `0303ad74fea5` | same as u03d at 480px |
| u08 | `8acfdcc29b19` | Q50 geom section visible + Q36 3-item writing_mode + Q69 track_text |

u01b `556b5a4f1300` / u02b `131bda934932` / u06 `35cb5c9269fe` unchanged.

## 2026-09-20 Q56 (Luca 授权重录 u05/u06/u08)

Why: Q45 滑条拇指+禁用变灰、Q46 间距/行高/圆角/对比度、Q52 文本面板重排、
Q53 时间线深灰底+内发光。取证见 `docs/plan/reports/Q56_ui_diff/`。
Fingerprint unchanged: PySide6=6.10.2 · DPI=96 · dpr=1.0 · fonts 240 /
`80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`.

Record hashes matched the immediately preceding `--all` `got=` byte-for-byte;
a second `--case u05,u06,u08` run also matched.

| case | sha256 prefix | why |
|---|---|---|
| u05 | `0fa87fca42b1` | timeline clips: dark bg1 fill + track_* inner glow (Q53); playhead x=599–600 unchanged |
| u06 | `35cb5c9269fe` | gallery spacing grid + radius set (Q46); thumbs unchanged |
| u08 | `4a90b55b397e` | text property panel rearrange (Q52) + label widths (Q46) + slider thumb (Q45) |
| u03d / u04d / u07 | **not rerecorded** | extra Q51 checkboxes + hint copy (u04d also Q47 image-mount removal) |

u01b `556b5a4f1300` / u02b `131bda934932` unchanged.

## 2026-09-17 十报 u01b/u02b (canvas.grab)

Why: u01/u02 hashed `PreviewEngine.frame_at`, not the widget Luca sees.
Add-then-retire: new u01b/u02b from `VideoCanvas.grab()`; u01/u02 kept on
disk, dropped from CASES.

| case | sha256 prefix | why |
|---|---|---|
| u01b | `556b5a4f1300` | canvas.grab glass; box variance > 0 |
| u02b | `131bda934932` | + line_bar; diff vs u01b only in bar band y=520–620 |
| u03b | `ac41863ff925` | T32 panel (signed) |
| u04b | `1ddcce1d5a90` | T32 + 描边 click; diff vs u03b only at capsule (12,513)–(43,528) |

## 2026-09-19 Q24 section capsules (u03c/u04c)

Why: 配色方案 / 自动着色 / 3D 挤出 / 背景纹理 / 双字体 / 弹出散落 gained
BUG-18 capsules; off sections gray the body. u03b/u04b kept on disk,
dropped from CASES. **Needs Claude sign-off.**

| case | sha256 prefix | why |
|---|---|---|
| u03c | `d65503b73c07` | six new header capsules + gray-when-off |
| u04c | `81293b1ccdeb` | same + 描边 capsule click; diff in x=0–55, y=400–650 |
| u05 | `ec9bdc9416dc` | unchanged |
| u06 | `f02779976fed` | unchanged |

## 2026-09-19 Q25 inspector (u03d/u04d/u07)

Why: parameter rows rebuilt as Resolve-style InspectorRow (label column +
slider/spin + ↺). Color chips are 28×28 squares. u03c/u04c kept on disk,
dropped from CASES. **Needs Claude sign-off.**

| case | sha256 prefix | why |
|---|---|---|
| u03d | `38970e2ba0dd` | inspector two-column rows + per-row reset |
| u04d | `efb78d2ec929` | same + 描边 capsule on (body un-grays full width) |
| u07 | `5aaf3e8b6837` | whole inspector at 480px |
| u05 | `ec9bdc9416dc` | unchanged |
| u06 | `f02779976fed` | unchanged |

## 2026-09-17 T32 panel (u03b/u04b)

Why: subtitle style panel dropped v1 box rows, 背景渐变 section, lyrics
header toggle, `fx.bg.blend`. Old u03/u04 retired.

| case | sha256 prefix | why |
|---|---|---|
| u03b | `ac41863ff925` | T32 panel layout after v1 box/gradient rows gone |
| u04b | `1ddcce1d5a90` | same + 描边 capsule click |

## 2026-09-24 Q98 (Luca 授权重录 u04e/u08b)

授权来源：`docs/plan/queue/Q98.md`。
Luca 2026-09-24 批准重录 `u04e` / `u08b`，只这两例。
问的是：「u04e / u08b 这两张 UI 截图基线要不要重录？（渲染 golden 一直是 PASS，
变的只是面板截图，因为背景色块段多了「尺寸」这一行）」，他答「批准重录两张」。
这条授权覆盖 Q93、Q95、Q97 三次累积下来的同一处变化。
`docs/plan/queue/_done/Q93.stop` 与 `Q97.stop` 记的就是它。
`tests/golden/` 未动。`--record` 只带 `--case u04e --case u08b`，没有 `--all`。

HEAD=`c0e005fa65ed3427ed9a59fdd0a1152fe113ddf0`（静帧在这棵树上录）。
静帧录完后 Claude 提交了 `922cf7b8aac6897a9c5924e786262e4cf325a473`，
只改 `docs/plan/queue/Q102.md`，没有动 `cutora/` 或 `tests/`。
`tools/golden_check.py --all` 跑在 `922cf7b` 上。
Fingerprint unchanged: PySide6=6.10.2 · DPI=96 · dpr=1.0 · fonts 240 /
`80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`.
工具打印 `logicalDotsPerInch=96.0`、`devicePixelRatio=1.0`，与文件开头那组一致。

重录哈希与紧挨着的重录前 `--all` 的 `got=` 逐字节相同；
重录后 `--all` 的 `got=` 也相同。

| case | sha256 | why |
|---|---|---|
| u04e | `384d256986ec72cba3c1a0ef11028b83b632f3592373f57aea234d22fae921aa` | 背景色块段新增「尺寸」下拉（贴合文字），且内边距排到宽/高/偏移之后；预设下拉从「默认」变成风格包名（Q95，同一次授权）。旧 `37029e115740b7ccad3d0b4792368fc4cc982d8596af7221ad33d48f248c03b1` |
| u08b | `170b2ed6bc3def84149218017640bdd5c4075ffe7e0d65534a70803f64637f66` | 文本覆层背景段同一处「尺寸」行。旧 `7aa2cdfc336ce85b291bd990f81efc0004e1f5f9e03b18f21311101613ef9bfb` |

u01b `556b5a4f1300` / u02b `131bda934932` / u03e `2c747ee69bfb` /
u05 `d0ca5c3aefd3` / u06 `35cb5c9269fe` / u07b `f2d9ad2696b5` unchanged.
取证：`docs/plan/reports/Q98_ui_diff/`。
