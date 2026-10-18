- 2026-09-20 00:29:00  **Q45**  golden=2，回滚后 golden=2  → `_blocked/Q45.report.md`
- 2026-09-20 08:51:46  **Q45**  （上一轮遗留）golden **新增失败：g16_parallel_fx**  → `_blocked/Q45.report.md`
- 2026-09-20 09:0x  **更正**：上面两条 Q45 的判定都是运行器的 bug，不是 Q45 的问题。
  第一条 golden 从没跑起来（缺 `--allow-dirty`，见 9adc32a）；第二条是已知名单里写了简称
  `g16` 而真名是 `g16_parallel_fx`，于是把老红当成了新红。Q45 的活是好的，已复原。
  唯一还需要人拍板的是 UI 静帧 u03d/u04d/u07/u08 —— 交给 Q56。
- 2026-09-20 15:1x  **Q45 / Q55 的 .stop 已处置**，手工移进 `_blocked/`，不再触发开工自检。
  Q45：运行器误判（golden 从没跑起来），活已复原，剩下的 UI 静帧归 Q56。
  Q55：`.stop` 是对的 —— g19b-2 的基线录的就是发黑本身。Luca 已批准重录，归 Q57。
- 2026-09-20 16:18:34  **Q53**  golden 无新增失败  → `_blocked/Q53.report.md`
- 2026-09-20 17:41:36  **Q56**  golden 无新增失败  → `_blocked/Q56.report.md`
- 2026-09-20 20:11:01  **Q50**  golden 无新增失败  → `_blocked/Q50.report.md`
- 2026-09-21 00:03:27  **Q36**  golden 无新增失败  → `_blocked/Q36.report.md`
- 2026-09-21 01:16:59  **Q10**  golden 无新增失败  → `_blocked/Q10.report.md`
- 2026-09-21 08:10:07  **Q32**  golden 无新增失败  → `_blocked/Q32.report.md`
- 2026-09-21 09:13:35  **Q10**  golden 无新增失败  → `_blocked/Q10.report.md`
- 2026-09-21 16:01:53  **Q69**  golden 无新增失败  → `_blocked/Q69.report.md`
- 2026-09-21 17:34:03  **Q32**  golden 无新增失败  → `_blocked/Q32.report.md`
- 2026-09-21 18:12:27  **Q36**  golden 无新增失败  → `_blocked/Q36.report.md`
- 2026-09-21 18:4x  **Q69 / Q36 / Q32 的 .stop 已处置。**
  Q69、Q36：UI 基线变更（u05 时间线配色、u08 竖排下拉），随后 Q58 已按授权统一重录，八例全绿。
  Q32：功能完成（片段调色不影响字幕、示波器接上、全关零成本），停在性能门禁 —— 全开 506 ms/帧。转交 Q73。
- 2026-09-22 03:00:28  **Q85**  golden 无新增失败  → `_blocked/Q85.report.md`
- 2026-09-22 08:22:12  **Q86**  golden 无新增失败  → `_blocked/Q86.report.md`
- 2026-09-22 10:08:57  **Q89**  golden 无新增失败  → `_blocked/Q89.report.md`
- 2026-09-22 13:39:03  **Q90**  golden 无新增失败  → `_blocked/Q90.report.md`
- 2026-09-22 13:47:02  **Q90**  （上一轮遗留）golden 无新增失败  → `_blocked/Q90.report.md`
- 2026-09-23 15:48:36  **Q92**  golden 无新增失败  → `_blocked/Q92.report.md`
- 2026-09-23 16:34:08  **Q93**  golden 无新增失败  → `_blocked/Q93.report.md`
- 2026-09-23 17:04:04  **Q93**  （上一轮遗留）golden 无新增失败  → `_blocked/Q93.report.md`
- 2026-09-23 18:44:35  **Q94**  golden 无新增失败  → `_blocked/Q94.report.md`
- 2026-09-23 20:19:54  **Q95**  golden 无新增失败  → `_blocked/Q95.report.md`
- 2026-09-23 20:54:00  **Q97**  golden 无新增失败  → `_blocked/Q97.report.md`
- 2026-09-24 00:12:32  **Q94**  golden 无新增失败  → `_blocked/Q94.report.md`
- 2026-09-24 02:27:56  **Q95**  （上一轮遗留）golden 无新增失败  → `_blocked/Q95.report.md`
- 2026-09-24 02:27:56  **Q97**  （上一轮遗留）golden 无新增失败  → `_blocked/Q97.report.md`
- 2026-09-24 02:30:35  **Q98**  golden 无新增失败  → `_blocked/Q98.report.md`
- 2026-09-24 03:08:05  **Q94**  golden 无新增失败  → `_blocked/Q94.report.md`
- 2026-09-24 12:13:52  **Q107**  golden 无新增失败  → `_blocked/Q107.report.md`
- 2026-09-24 12:39:35  **Q107**  （上一轮遗留）golden 无新增失败  → `_blocked/Q107.report.md`
- 2026-09-24 13:1x  **Q107 的 .stop 已处置。** 探针结论 B（MMS 需罗马化）复核成立，词表只有 31 个拉丁符号。
  **不重跑** —— 已拆成 Q108（不加依赖）+ Q109（装 uroman）并入队，Q108 正在跑。裁决见 `_blocked/Q107.ruling.md`。
