# T01 附录 C — 切片 B（UI 拆包）指引

> 新会话开工顺序：`01_GROK_通用规范.md` → **本附录** → `T01_拆包_单文件到cutora包.md`（附录 B 的决定继续有效，不必重贴）。
> 起点：`main` @ `T01a-done`，主文件 `core/AutoSub_debug.py` 还剩 21,567 行（全部是 UI）。
> 新分支：`task/T01-unpack-b`。

## 一、切片 B 再分两段（和切片 A 同样的道理）

**B1 — 叶子 UI（机械活）**：`ui/widgets/` → `ui/panels/*` → `ui/timeline/*` → `ui/canvas.py`
每抽一个模块跑 `golden --all`，提交。全部完成后 `--all --repeat 3` + **GUI 冒烟测试（§三）** 全绿 → 合并 main，打 `T01b1-done`。
此时 `MainWindow` 仍留在主文件里，程序照常能开——这是一个可以安全停下的检查点。

**B2 — 收尾（危险活）**：`ui/main_window.py` → `app.py` → 切换 `--render-job` → 删垫片 → 删 `core/` → 搬 `core/test_*.py` → `tools/_legacy/debug_print.py`。
完成后 `--all --repeat 3` + GUI 冒烟全绿 → 合并 main，打 `T01-done`。

## 二、切片 B 特有的三个坑

1. **循环 import 是这一段的头号敌人。** 面板类构造时几乎都要摸到 `MainWindow`。规则：**面板不许在模块顶层 `import` main_window**；已有的父引用一律走 `self.parent()` / 已连接的信号 / 构造参数；实在绕不开的，函数体内延迟 import，并在报告里逐个列出（附录 B 已要求，切片 B 要列全）。**不许为了解耦改信号连接方式或改构造签名**——那是 T03 的活。

2. **`--render-job` 切换是全任务最危险的一步，单独一个提交。** 现在 `ParallelExportManager._script_path()` 指向 `CORE_DIR/AutoSub_debug.py`，子进程把主文件当 `__main__` 加载（`_t01_host()` 认 `__main__`）。B2 里改成 `python -m cutora --render-job` 后必须当场验证三件事，缺一不可：
   - 子进程 import 耗时 < 0.5 s（打印计时贴进报告）
   - `nvidia-smi` 看不到子进程显存占用（截图或文本贴报告）
   - `golden --case g06_parallel --repeat 3` 通过（parallel==serial）
   然后才允许删主文件。顺序反了就等着 debug 一整晚。

3. **`_sync_device_globals` 现在会回写主文件**（给还没搬走的 UI 用）。主文件删掉的同时，这段回写必须一起删干净，`DEVICE` 只剩 `cutora.asr.engine` 一处。收尾时 grep 确认没有第二个 `DEVICE` 赋值点。

4. **字体加载**：`load_embedded_fonts()` 在字体缺失时会弹模态框——搬它之前先把 `FONT_DIR` 改走 `cutora.core.paths`，否则无头入口会被一个没人点得掉的对话框卡死（T01 §2.1 已写过，这里再强调一次）。

## 三、GUI 冒烟测试（切片 B 的验收核心）

**golden 只验渲染，验不了界面。** 切片 B 是整个 T01 唯一有"程序能跑但界面坏了"风险的一段，所以 B1 和 B2 结束时各做一次下面的清单，逐条在报告里写"通过/未通过"。你有桌面就自己做并截图；没有就明确写"待 Luca 执行"，由他补。

启动 `CUTORA.bat`，依次：
1. 主窗口正常出现，无控制台异常
2. 拖入一条视频，出现在列表与画布
3. 播放 / 暂停 / 拖动播放头
4. 时间线：拖动片段、缩放、多轨显示
5. 跑一次自动字幕（small，英语），字幕出现在画布与时间线
6. 字幕样式面板：改字体、字号、颜色、描边、位置，画面实时变化
7. 逐字高亮 / 弹力条 / 光晕 各开一次，预览正常
8. 关键词规则面板：加一条规则并生效
9. 角色映射面板、字块面板：各打开一次，不报错
10. 字体管理对话框：打开、切换字体
11. 富文本编辑对话框：打开、改字、确定
12. 插入文字 / 插入图形 各一次，可拖动可改属性
13. 属性面板全部分区展开一遍（变换、裁剪羽化、背景、描边、投影、动画、图片、音频、转场、层级）
14. 音效对话框：打开、切换空间立体声预设、试听
15. 撤销 / 重做 各三次
16. 保存工程、关闭、重新打开，内容一致
17. 打开最近项目菜单
18. 导出设置对话框：切换压缩/原始
19. 导出一条，能出片
20. 批量：片段智能拼接对话框能打开（不必跑完）
21. 批量：片段散落、批量插图对话框能打开
22. 工程队列对话框能打开
23. 性能菜单：显存占用情况、释放显存
24. 画布尺寸切换（竖屏↔横屏）
25. 关闭程序，无残留进程、无未捕获异常

## 四、收尾清单（B2 结束前逐条打钩）

- [ ] `core/` 目录已删除，`AutoSub_debug.py` 不存在
- [ ] `grep -rn "AutoSub_debug" cutora/ tools/ tests/ *.bat` 为零（`docs/` 除外）
- [ ] 全部垫片已删除
- [ ] `core/test_*.py` 已搬到 `tests/`，`pytest` 全绿
- [ ] `debug_print.py` → `tools/_legacy/debug_print.py`
- [ ] `python -m cutora` / `--describe` / `--status` / `--render-job` 四个入口正常
- [ ] `cutora_cli` 相关入口正常（g07 通过）
- [ ] `pyflakes cutora/` 无 undefined name（imported-but-unused 报数量即可）
- [ ] 233 个符号 → 模块的完整映射 CSV 已提交
- [ ] 全局可变状态去向表、循环引用清单已写进报告
- [ ] 超 800 行的文件清单与行数（豁免见附录 B §1.1–1.2）
- [ ] `golden --all --repeat 3` PASS
- [ ] GUI 冒烟 25 条结果
