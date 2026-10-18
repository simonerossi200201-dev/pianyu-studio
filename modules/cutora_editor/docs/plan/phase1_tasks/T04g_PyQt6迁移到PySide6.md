# T04g — PyQt6 → PySide6 迁移（授权阻塞项）

> **为什么做**：PyQt6 是 GPL-3.0-only，Riverbank 只提供 GPL v3 或付费商业授权，没有 LGPL 选项。只要分发 CUTORA，就必须以 GPL-3.0 发布并向使用者提供源码。Luca 已决定：**迁到 PySide6（Qt 官方绑定，LGPL-3.0）**，允许闭源分发。
> **为什么现在做**：T04f-6 要重建 14 个风格包并录制 g08/g10 新基线。在一个即将被替换的框架上录基线是白做，而且会把刚刚才关上的"字节一致性"这扇门重新打开。**T04g 排在 T04f-6 之前。**
> **分支**：从 `task/T04f-style-fx` 的当前 HEAD 开 `task/T04g-pyside6`。T04f 的收尾（T04f-6）在迁移完成之后进行。

## 0. 这一片的唯一判据

**golden `--all --repeat 3` 必须逐字节 PASS，`g01` 仍是 `2fea55c62e09`。**

换绑定层不应该改变任何一个像素——两边用的是同一套 Qt 6.10 库，字形栅格化、QPainter 合成走的都是同一份 C++ 代码。所以：

- **PASS** → 迁移是干净的，可以往下走。
- **FAIL** → **立刻停下报我，附上差异帧的像素分析（照 `T04f-4_golden_regression` 那次的做法：ffprobe 容器信息 → 逐帧 PSNR → 最差帧的差异包围盒 → 局部放大对比）。绝对不许重录基线。** 绑定层换了导致像素变化，要么是我们哪里写错了，要么是一个必须理解清楚的真实差异——两种都得先看明白再决定。

这是我们花两天修好的那张网，这一片正是它存在的理由。

## 1. 迁移方式：机械、单一目的、可复核

- **一次性切换**，不做兼容层。PyQt6 与 PySide6 不能在同一进程共存，抽象层只会增加两倍的坑。
- **只做 API 替换，不做任何其他改动。** 不重构、不改命名、不"顺手优化"、不动任何渲染逻辑。这一片的 diff 应该几乎全是 import 和符号名。
- 分成两个提交：`refactor(T04g): PyQt6 → PySide6 API swap`（机械替换）+ `fix(T04g): <具体问题>`（如果有必须的行为修正，单独提交并在报告里逐条说明为什么非改不可）。

## 2. 已知的坑（不用自己重新踩一遍）

**最危险的一条，先处理它**：

- **`QImage.bits()` 的返回类型不同。** PyQt6 返回 `sip.voidptr`，要调 `.setsize(n)`；**PySide6 返回 `memoryview`，没有 `setsize`**。
  这直接打在 `cutora/render/textfx/raster.py::as_bgra()` 上——那是整个 v2 渲染路径的地基，`apply_fade_bgra`、`glass`、所有像素锁全都经过它。
  改完**先单独验它**：构造一张已知内容的 `ARGB32_Premultiplied`，走 `as_bgra` 取 numpy 视图，断言 (a) 数值与直接构造的数组一致，(b) 修改视图能写回 QImage（确认是视图不是拷贝），(c) `bytesPerLine != width*4` 的对齐情况下切片仍然正确。这三条过了再动别的。

其余：

| PyQt6 | PySide6 |
|---|---|
| `pyqtSignal` / `pyqtSlot` / `pyqtProperty` | `Signal` / `Slot` / `Property` |
| `import sip` | `import shiboken6` |
| `sip.isdeleted(obj)` | `not shiboken6.isValid(obj)` |
| `sip.voidptr` | `memoryview` |
| `QtCore.QT_VERSION_STR` 等常量 | 名称大多相同，逐个确认 |

- **枚举一律用完全限定写法**（`Qt.PenStyle.NoPen`、`QImage.Format.Format_ARGB32_Premultiplied`）——这在两边都成立，不要因为 PySide6 允许简写就改短。
- `QAction` 在 Qt6 里两边都在 `QtGui`，不用动。
- `exec()` 两边一致，不是 `exec_()`。
- 如果有 `uic` / `.ui` 文件加载，PySide6 用 `QUiLoader`，写法不同。
- 打包脚本（PyInstaller spec 之类）里的 hidden-import、Qt 插件路径要一并改。

## 3. LGPL 的合规条件（换过去不等于自动合规）

PySide6 是 LGPL-3.0，允许闭源分发，但有条件：

1. **Qt 与 PySide6 必须动态链接**，不能静态链接进可执行文件。PyInstaller 默认打成独立 `.dll/.pyd`，符合；但要确认打包结果里 Qt 是独立文件而不是被塞进单文件 exe 的压缩包里无法替换。**如果用 `--onefile`，需要评估**。
2. **必须让使用者能够替换 Qt/PySide6 库**（LGPL 的"可重新链接"要求）。文档里写清楚 Qt 库在哪个目录、替换方法。
3. **署名**：`THIRD_PARTY_LICENSES.md` 里加 PySide6 与 Qt 的条目，附 LGPL-3.0 全文。

这三条写进报告，逐条给出证据。

## 4. 顺带做掉（都很小）

- **`sentencepiece 0.2.1` 的许可证是 `UNKNOWN`**：去它的 dist-info / 仓库确认真实许可证并写进表，不要靠推测。
- **157 个依赖的许可证正文没有随包分发**（MIT 76 / BSD 42 / Apache 34，这三类都要求随附版权声明与许可证文本，URL 不构成履行）。
  做法：`resources/THIRD_PARTY_LICENSES.md` 保持四列索引不变，**另建 `resources/licenses/<包名>-<版本>.txt` 放正文**，索引加第五列指向本地文件。预期 0.5–1.5 MB，正常，不要因为怕大又缩回去。守护测试覆盖到：单文件 ≤ 512 KB、无控制字符、索引每行都能找到对应文件。
  **这一步放在迁移之后做**——依赖表会因为 PyQt6 换 PySide6 而变，先做要返工。

## 5. 不要做

- 不改任何渲染逻辑、不动 `textfx/`、不动风格包、不碰动画库。
- 不加新依赖（PySide6 替换 PyQt6，净增为 0）。
- **不重录任何 golden 基线。**
- 不开 T04f-6。
- 不趁机重构任何东西。

## 6. 验收

1. **golden `--all --repeat 3` PASS，带指纹头 `source_dirty=no`，g01 = `2fea55c62e09`。**（若 FAIL 见 §0，停下报我。）
2. `as_bgra` 三条专项测试（§2）。
3. 全部现有像素锁未变：玻璃 a/b/c、emphasis 颜色锁、字距锁、`_edge_clear` 全表 11 点、叠层渐隐、安全区导出字节一致。
4. pytest 0 failed；`pip freeze` 里**没有任何 PyQt6**（含 `PyQt6-Qt6`、`PyQt6-sip`）。
5. bench 三遍 `dropped=0`，字幕 p50 与迁移前相比 **±10% 以内**（超出就报，不要自行调优）。
6. **Luca 真机双击启动验证**——不是离屏构造的截图。启动日志要有完整的 `STARTUP git=… branch=…`、`[Guard]`、字体数 412。（这条我们栽过一次假截图，按真机来。）
7. §3 三条 LGPL 合规证据。
8. §4 两条（`sentencepiece` 许可证、`resources/licenses/` 正文）。
9. 标签 `T04g-done`，报告 `docs/plan/reports/T04g_report.md`，停下等审。

## 7. 一句话

这一片没有任何新功能，成功的标志是**什么都没变**——同样的像素、同样的哈希、同样的手感，只是许可证从 GPL 变成了 LGPL。它枯燥，但它决定 CUTORA 最后能不能按 Luca 想要的方式发出去。
