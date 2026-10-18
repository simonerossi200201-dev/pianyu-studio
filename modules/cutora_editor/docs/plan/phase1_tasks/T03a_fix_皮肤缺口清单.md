# T03a-fix — 换皮遗漏清单（与 T03b 同一会话做掉）

> 依据：Luca 在真机截的 5 张图（主窗口 / 字幕样式面板 / 片段智能拼接 / 字体许可 / 空间立体声）。
> 根因一句话：**QSS 覆盖了常见控件，漏了几个控件类，漏掉的就退回 Qt 默认浅色样式**。所以修法不是逐处打补丁，而是把这几类补进 `tools/build_qss.py` 的模板。
> 先做本清单并单独提交（`fix(T03a): cover unstyled widget classes`），再开始 T03b。

## 一、白色方块（Luca 报的问题，共三处，同一病根）

1. **表格的表头没样式**：`QHeaderView::section`（横向 + 纵向）、以及左上角那个 `QTableView QTableCornerButton::section`。
   - 现象：`帮助 → 字体许可` 表格左上角一个纯白方块、行号列发白；`片段智能拼接` 的文案表左侧行号列发白。
2. **表格视口背景没样式**：`QAbstractScrollArea::viewport` / `QTableView` 的空白区。
   - 现象：`片段智能拼接` 文案表第 3 行以下的空白区是一大块白。
3. **表格选中色是遗留的橄榄绿**：`片段智能拼接` 里 `T007` 那格。改用 `accent_dim` 底 + `fg0` 字。

补齐这一组：`QHeaderView::section`、`QTableCornerButton::section`、`QTableView`、`QTreeView`、`QListView`、`::item:selected`、`::item:hover`、`alternate-background-color`、`QAbstractScrollArea` 及其 `viewport`。

## 二、其余没被 QSS 覆盖的控件

4. **`QSpinBox` / `QDoubleSpinBox` 的上下箭头**：现在是右边缘一个几乎看不见的小点（`原生音量`、`配乐音量`、`大小`、`旋转`、`尾部余量` 都是）。要给 `::up-button` / `::down-button` / `::up-arrow` / `::down-arrow` 明确尺寸、底色、hover 态，箭头用 Lucide 的 chevron 着 `fg1` 色。
5. **`QCheckBox::indicator` 分不清勾选状态**：现在选中是一个实心金方块、未选是空方块，**没有对勾**。（`粗体`、`斜体`、`启用渐变`、`GPU加速`、`裁剪开头空白` 等全是。）要画真正的 ✓，并区分 unchecked / checked / hover / disabled 四态。
6. **轨道头的图标还是遗留彩色**：时间线左侧 ↑ ↓ 图钉 ⇄ 眼睛 ＋ 用的是红/粉/绿原色，跟 Roma 冲突。全部改走 `cutora/ui/icons.py` 按 token 着色（普通态 `fg1`，激活态 `accent`，危险态 `err`）。

## 三、内容层面的毛病（不是 QSS，但一眼就看见）

7. **小数点是逗号**：`1,00` / `0,10` / `0,30 s` / `0,35`——跟着 Windows 的意大利语区域设置走，但界面是中文，读起来别扭且不一致。在 `app.main()` 里给界面固定一个数字区域设置（`QLocale.setDefault` 用 `C` 或 `zh_CN`），让所有 spinbox 显示 `1.00`。**改完跑 golden**（工程 JSON 存的是 float，不受影响，但要确认）。
8. **`位置 Y: 964.6561679790027`**：13 位小数直接甩在界面上。位置/旋转/时长这类数值显示统一保留 1 位小数（内部值不变，只改显示）。
9. **中英文混排**：`Play`、`Save`、`Cancel` 还是英文，同一个对话框里 `应用到当前视频` 却是中文。全部走 `tr()` 并补中文。全仓库扫一遍按钮文案。
10. **右上角 `Total 03:47.21` 压在时间标尺上**：与刻度文字重叠。挪到时间线右下角或状态栏。

## 四、验收

- [ ] 逐条对照 §一~§三，报告里每条写「修了什么 / 截图」
- [ ] 重新截这 5 张图（主窗口、字幕样式、片段智能拼接、字体许可、空间立体声），**放大看表格边缘与勾选框**
- [ ] 全仓库再扫一遍还有哪些控件类没进 QSS：`QComboBox QAbstractItemView`、`QMenu`、`QToolTip`、`QProgressBar`、`QSlider::groove/handle`、`QScrollBar`、`QTabBar::tab`、`QGroupBox::title`、`QSplitter::handle`、`QDialogButtonBox`、`QLineEdit:disabled`、`QTextEdit`、`QPlainTextEdit`——逐个确认有样式或明确说明不需要
- [ ] `pytest` 全绿；`golden --all --repeat 3` PASS；`security_scan` 业务代码栏 0
