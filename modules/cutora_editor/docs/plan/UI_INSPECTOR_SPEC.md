# 检查器外观规范（Q25 → Q83 重做）

右侧 dock 所有分页（风格、字幕样式、属性、角色映射、字块样式）共用这一套。
数字全部来自 `cutora/ui/theme/tokens.py`，产品代码里不许写裸像素。

Q83（2026-09-21，Claude 亲自做）把 Q25 的布局推倒重来，原因是：QSS 给输入框
`min-height 28 + padding 4×2 + 边框 2 = 38px`，Python 却 `setFixedHeight(22)`，
行又被钉死 24px，结果每个控件都被裁掉一半、变换段整段挤成一条。

## 0. 高度只有一个权威（最重要）

| 用途 | token | 值 | 说明 |
|---|---|---|---|
| 行高（单行控件） | `INSPECTOR_ROW_H` | 32 | 行按内容长高：单行 = 32，高控件（文本框、色标、换行提示）= 自身高度 |
| 控件外高 | `INSPECTOR_CONTROL_H` | 28 | 下拉 / 数值框 / 输入框 / 按钮 / 图标按钮 / 分段控件 |
| 控件内容高 | `INSPECTOR_CONTROL_INNER_H` | 26 | QSS `min-height = max-height = 26`，**竖向 padding 0**，边框 1px ×2 → 外高 28 |

Qt QSS 盒模型：`min-height / max-height` 指内容盒，padding 与边框另加。所以：

- 检查器输入控件的 QSS 竖向 padding 必须是 0；高度只写 `insp_ctl_inner`。
- Python 里**不许**对这些控件 `setFixedHeight` / `setMaximumHeight`（`_apply_field_limits` 只管宽度）。
- `InspectorRow` 竖向策略 Fixed、`sizeHint = max(INSPECTOR_ROW_H, 字段高, 换行后高度)`；不许再 `setFixedHeight(ROW_H)`。
- 换行提示用 heightForWidth：行宽变了就 `updateGeometry()`，不会压住下一行。

全局 `ROW_H / CONTROL_H / CONTROL_H_LG / SECTION_HEAD_H`（24/22/28/26）只给顶栏、时间线等非检查器区域，检查器不用。

## 1. 两列对齐

| 用途 | token | 值 | 来源 |
|---|---|---|---|
| 标签列最小宽 | `INSPECTOR_LABEL_MIN` | 32 | `SPACE_6` |
| 标签列最大宽 | `INSPECTOR_LABEL_MAX` | 112 | `SPACE_6 * 3 + SPACE_4`；标签是短名词，宽度留给控件 |
| 右侧 dock 最小 | `DOCK_RIGHT_MIN` | 300 | |
| 右侧 dock 最大 | `DOCK_RIGHT_MAX` | 16777215 | 即不设上限（Luca：没有限制的必要），可自由拖宽 |
| 右侧 dock 默认 | `DOCK_RIGHT_DEFAULT` | 360 | |
| 标签字号 | `INSPECTOR_LABEL_FONT` | 12 | `FONT_LABEL`，色 `fg1` |

- 标签**左对齐**，一个滚动页（整页，不是每段）共用一条标签列：按全页最长标签定宽，夹在 min–max，所以各段的控件左边缘对齐。
- 标签不写冒号：`InspectorRow` 自动去掉末尾 `:` / `：`。
- 每行右侧永远保留 `INSPECTOR_RESET_PX` 的重置列（没有重置时放等宽空位），所有控件右边缘对齐。
- 组合控件（变换网格、文本编辑器、HSL 页）用 `InspectForm.addSpanRow()`：不占标签列，整行宽。

## 2. 滑条 + 数值框

| 用途 | token | 值 | 来源 |
|---|---|---|---|
| 滑条旁数值框宽 | `INSPECTOR_VALUE_W` | 56 | `SPACE_6 + SPACE_5`；全页同一列 |
| 独立数值框最大宽 | `INSPECTOR_SPIN_WIDTH` | 72 | `SPACE_6 * 2 + SPACE_2` |
| 滑条最小宽 | `INSPECTOR_SLIDER_MIN` | 24 | `SPACE_5` |
| 控件内间距 | `INSPECTOR_FIELD_GAP` | 4 | `SPACE_1` |
| 成对控件 / 滑条与数值间距 | `INSPECTOR_PAIR_GAP` | 8 | `SPACE_2` |
| 同行复选项间距 | `INSPECTOR_OPTION_GAP` | 12 | `SPACE_3` |

滑条占中、数值框固定 `INSPECTOR_VALUE_W`。已有的滑条+数值框对用 `slider_with_spin()` 排版，外观与 `SliderSpin` 一致。

## 3. 控件宽度

| 用途 | token | 值 | 来源 |
|---|---|---|---|
| 下拉最大宽 | `INSPECTOR_COMBO_MAX_W` | 256 | `SPACE_6 * 8`；下拉先铺到上限再留白，行与行右边缘一致 |
| 色块边长 | `INSPECTOR_SWATCH_PX` | 24 | `SWATCH_PX` |
| 色块+hex 最大宽 | `INSPECTOR_COLOR_MAX_W` | 128 | `SPACE_6 * 4` |
| 锁宽测试面板宽 | `INSPECTOR_PANEL_W` | 480 | `SPACE_6 * 15` |
| 图标按钮字形 | `INSPECTOR_ICON_PX` | 16 | `ICON_MD`，按钮本身 28×28 |

- 成对数值（位置 X/Y、尺寸 W/H、边距、裁剪）用 `paired_fields()` 放一行，前缀写在框里（`X  0`）；每个框 56–72px，宽 dock 时左对齐，窄 dock 时一起缩。
- 固定选项少的枚举用 `SegmentedChoice`（图标分段，如文字对齐）；展示中文、存英文值的下拉用 `ValueCombo`（`currentText()` 返回存储值，信号 `valueChanged`）。
- 单色色标显示色块 + hex；两个及以上色标才出渐变预览条。
- 字段被代码隐藏时，整行（标签 + 重置）跟着隐藏；组合字段里所有部件都隐藏也算隐藏。

## 4. 段标题

| 用途 | token | 值 | 来源 |
|---|---|---|---|
| 标题条高度 | `INSPECTOR_HEADER_H` | 32 | `SPACE_6` |
| 标题字号 | `INSPECTOR_TITLE_FONT` | 15 | `FONT_SECTION`（QSS `QLabel#dockSectionTitle` 必须同值） |
| 重置按钮边长 | `INSPECTOR_RESET_PX` | 20 | `RESET_PX` |
| 段间距 | `INSPECTOR_SECTION_GAP` | 16 | `SPACE_4` |

标题条透明底、顶部一条 `fg0_08` 细线；段满宽（容器左右边距 0，表单内部 `INSPECTOR_PAD_X`）。
展开箭头 13px `fg2`。启用开关（`ToggleSwitch` 36×20）在右，段重置「↺」最右。

## 5. 行重置

每行右侧 `INSPECTOR_RESET_PX` 的「↺」（`QToolButton#inspectorReset`），只恢复这一行。
视频变换的「位置 / 缩放 / 旋转」重置也改成行内 ↺（旧按钮保留为隐藏 API）。

## 6. 层次靠间距

| 用途 | token | 值 | 来源 |
|---|---|---|---|
| 组内行距 | `INSPECTOR_ROW_GAP` | 6 | `SPACE_1H` |
| 组间距 | `INSPECTOR_SECTION_GAP` | 16 | `SPACE_4` |
| 水平内边距 | `INSPECTOR_PAD_X` | 12 | `SPACE_3` |
| 垂直内边距 | `INSPECTOR_PAD_Y` | 8 | `SPACE_2` |
| 多行文本高 | `INSPECTOR_TEXT_H` | 64 | `SPACE_6 * 2`（富文本编辑器最小 1.5 倍） |
| 渐变预览条高 | `INSPECTOR_GRAD_BAR_H` | 8 | `SPACE_2` |
| 棋盘格格宽 | `INSPECTOR_CHECKER_PX` | 4 | `SPACE_1` |

输入底 `bg2`、边 `fg0_16`、悬停边 `fg0_25`、焦点边 `accent`、圆角 `RADIUS_CTL`。
禁止纯黑底 / 纯黑描边 / 纯黑字。

## 7. 专用控件

| 用途 | token | 值 | 说明 |
|---|---|---|---|
| 色轮默认边长 | `INSPECTOR_WHEEL_PX` | 128 | `SPACE_6 * 4` |
| 色轮最小 | `INSPECTOR_WHEEL_MIN_PX` | 72 | 窄 dock 时三个色轮一起缩，不再被裁 |
| 色轮最大 | `INSPECTOR_WHEEL_MAX_PX` | 160 | 宽 dock 时放大 |

- HSL 限定：一排六个色相圆点选颜色（改过的带小点），下面只显示该颜色的 色相 / 饱和 / 亮度 三条滑条。
- 富文本编辑器：按 11pt 预览文字（真实字号只存在数据里，`_rich_scale` 仅用于显示），工具条是图标按钮（粗 / 斜 / 下划线 / 颜色 / 字号 / 清除格式）+ 选区字体。
- 转场参数：只显示该转场真正读取的参数（从渲染函数签名推出，`transition_param_info()`）；「线性擦除」显示角度滑条，方向下拉只列该转场支持的方向。
