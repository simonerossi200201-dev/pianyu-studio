# T04f 附录 C — 回归修复、参数一一对应、排版硬约束

> **插在 T04f-2 之前做，分成 1d-1 / 1d-2 / 1d-3 三片。** T04f-2（渐变/金属/霓虹/3D/纹理/气泡/双字体/新字体）推到本附录做完之后。
> 起因：Luca 在真机上发现三类问题，其中两类是**回归**——现在的可用性比 T04e 之前还差。在把更多效果堆上去之前，先把地基修好。
> 本附录**推翻** `T04f-1c_review_by_Claude.md` §2 里我写的「允许溢出行数」那条政策，见 §3。那条是我定错了。

---

## 1. T04f-1d-1：字幕在画面上点不中、拖不动、右侧滑杆调不动（回归，最高优先级）

### 1.1 根因（我已经在代码里定位到，不用再猜）

`cutora/subtitle/stylepack.py::resolve_style_geometry` 的注释写着「Relative fields win」，实现也确实如此：

```python
if s.get("anchor") or s.get("pos_x_pct") is not None or s.get("pos_y_pct") is not None:
    if s.get("anchor"):
        s["pos_x"] = ax + float(s.get("offset_x_pct") or 0) * w
        s["pos_y"] = ay + float(s.get("offset_y_pct") or 0) * h
if s.get("fontsize_pct") is not None:
    s["fontsize"] = max(8, int(round(float(s["fontsize_pct"]) * w)))
```

每次重绘都会用 `anchor + offset_*_pct` **覆盖掉 `pos_x` / `pos_y`**，用 `fontsize_pct` 覆盖掉 `fontsize`。

而右侧面板的位置滑杆、画布拖拽写的是 `pos_x` / `pos_y`，字号框写的是 `fontsize`。**写进去，下一帧就被覆盖回去**——所以表现是"调了没反应""拖不动"。

T04e 引入相对几何之后，任何应用过风格包的工程（style 里带上了 `anchor` 与 pct）都中招，不只是 v2。

### 1.2 改法：UI 永远不直接写 `pos_x/pos_y/fontsize`，统一走一对 setter

在 `stylepack.py` 里加（或就近放在几何函数旁边）：

```python
def set_position_px(style: dict, px: float, py: float, canvas_w: int, canvas_h: int) -> None:
    """把画布像素位置写回 style，按 style 自己的表示法存。"""
    # style 用相对几何 → 反算 offset_*_pct；否则写 pos_x/pos_y

def set_fontsize_px(style: dict, fs: float, canvas_w: int, canvas_h: int) -> None:
    """同上，相对表示法就写 fontsize_pct。"""
```

反算必须是 `resolve_style_geometry` 的**严格逆运算**：`offset_x_pct = (px - ax) / w`、`offset_y_pct = (py - ay) / h`、`fontsize_pct = fs / 基准维度`（基准维度 = `min(w, h)`，与 T04f-1c 改完之后一致——**不要再用 w**，`resolve_style_geometry` 里那句 `* w` 也要一起改成 `* min(w, h)`，否则和渲染对不上）。

**往返测试**：随机 200 组 `(px, py, fs)` × 三个画幅，`set_position_px` 之后再 `resolve_style_geometry`，断言取回的 `pos_x/pos_y/fontsize` 与写入值误差 ≤ 0.5 px。

然后把所有 UI 写入点改成调这两个函数：右侧面板的位置/字号控件、画布拖拽、以及任何命令里改位置的地方。**扫一遍 `cutora/ui/` 里所有对 `["pos_x"]` `["pos_y"]` `["fontsize"]` 的赋值，一个不许剩。** 加一条 AST 测试：`cutora/ui/` 下除这两个 setter 外，不允许出现对 `pos_x` / `pos_y` / `fontsize` 的下标赋值。

### 1.3 点选区域

v2 路径 `pipeline.draw` 返回的是 `(hit_x, hit_y, hit_w, hit_h)` = **纯文字 bbox**，不含底框/毛玻璃面板。所以毛玻璃那种大面板，只有正好点在字形上才选得中。

改成返回**可见区域**：`bg.type != "none"` 时用底框/玻璃面板的矩形，否则用文字 bbox；两者都不含发光/投影的模糊外扩（那部分是半透明的，不该算可点区域）。

### 1.4 v2 丢掉的另外两个参数

`draw_subtitle` 的 v2 分支只传了 `painter, event, s, t, w, h, backdrop`，把 `seg_idx`、`dynamic_layout_list`、`_hit_char_offset` 全丢了。逐字命中（双击词进入行内编辑）在 v2 风格下因此是坏的。

本片**至少**要把 `seg_idx` 透传并保证 `draw_layers` 里那条 `hit_recorder.append({'type':'sub', ...})` 对 v2 也记录正确的 idx；逐字命中如果工作量大，可以留到 1d-2，但要在报告里写明现状。

### 1.5 卡顿：先量，不许猜

Luca 说"很卡"。**不要直接开始优化**，先用 `CUTORA_PERF=1` + `tools/preview_bench.py` 量出时间花在哪。按可能性排序的候选（量完再动手，量的结果写进报告）：

1. **每帧都在重建 `FxSpec`**：`from_style()` 里 `asdict(spec)` + `collect_pure_black()` 会把整个嵌套字典走一遍，每帧每条字幕各一次。
2. **拖滑杆时每一格都让 ROI 缓存失效**：`cache_hash` 变 → 整块重新光栅（含高斯）。滑杆应该在拖动过程中节流（例如 50 ms 合并一次重绘），松手再出最终帧。
3. **`layout_event` 每帧重新量字体**：有 LRU 32，但 key 里若含每帧都变的量就等于没有。
4. 毛玻璃每帧重算（已知、且是必须的），只有在 ROI 特别大时才该成为主因。

量完只修真正占大头的那一两条。**不要碰解码器。**

---

## 2. T04f-1d-2：风格包 = 参数预设，右侧面板必须与参数一一对应

Luca 的原则（照抄）：「风格预设和字幕参数都必须是一一对应的，而不是写死的」「每个字幕的背景都可以选择是否要打开模糊功能，这属于一个参数，而不是某个独有的风格」。

他说得对，而且这正是 T04f-0 里我留的口子（当时写的是「UI 不是本任务重点」）。现在补上。

### 2.1 原则

- `fx` 里**每一个字段**都必须在右侧「字幕样式」面板里有一个对应控件。
- 风格包做的事 = **把一组参数值写进当前字幕的 style**，仅此而已。应用之后用户改任何一个参数，都必须立刻生效并且保持住（不会被风格包"弹回去"）。
- 反过来，用户调完参数点「存成风格包」，存下来的必须能完整还原。

### 2.2 背景模糊（毛玻璃）作为独立参数

面板里「背景色块」那一组已经有 圆角 / 不透明度 / 水平扩 / 垂直扩 / **背景柔化** / 背景随朗读过渡变色。在这一组里加：

- **☐ 背景模糊（毛玻璃）** —— 对应 `fx.bg.type` 在 `box` ↔ `glass` 之间切换
- **模糊强度** 滑杆 —— 对应 `fx.bg.glass_sigma_em`，范围 0.2–1.5，显示成 0–100 的百分比
- **☐ 玻璃反光边** —— 对应 `fx.bg.glass_border`

`背景柔化` 与 `fx.bg.glass_sigma_em` 如果语义重合，就**合并成一个控件**，不要放两个互相打架的滑杆；报告里写明你怎么处理的。

### 2.3 其余 fx 字段的归位

按 `T04f_附录A_效果目录与schema.md` §1 的表，把每个字段放进面板里已有的分组（描边 → `fx.stroke`；阴影/发光 → `fx.shadow` / `fx.glow`；字幕排版 → `tracking_em` / `letter_case`；自动着色 → `fx.emphasis`；动画效果 → `fx.anim`），缺分组的新建一个折叠区（填充 `fx.fill`、3D `fx.extrude`、背景纹理 `fx.bg.texture*`）。

T04f-2 之后才会出现的效果（金属/霓虹/3D/纹理）**这一片就先把控件位置留好并接通**，值改了要能存进 style；渲染端还没实现的，控件先禁用并标「T04f-2 启用」。这样后面加效果不用再动 UI 布局。

### 2.4 一一对应的测试（这条是本片最重要的产出）

写一个测试，**遍历 `FxSpec` 的所有字段**（用 `dataclasses.fields` 递归），对每个字段断言：

1. 面板里存在一个注册到该字段路径的控件（用一张 `FX_FIELD_WIDGETS: dict[str, widget_name]` 注册表，测试断言表的键集合 == schema 的字段集合，**少一个就红**）；
2. 通过控件设值 → `style["fx"]` 里对应路径的值跟着变；
3. `apply_pack_to_task` 应用一个包之后，面板读回的值 == 包里的值。

以后 `fx` 加字段却忘了加控件，这条测试当场抓住。

---

## 3. T04f-1d-3：排版参数必须被绝对遵守（**推翻我上一轮的政策**）

### 3.1 我上一轮写错了

`T04f-1c_review_by_Claude.md` §2 里我写的是「字号大到 2 行装不下时允许溢出行数、不缩字号」。Luca 明确否掉了：「设置成一行就必须是一行，不可以偶然出现两行」。**以他的为准，把 `layout.py` 里那条注释和相应实现一起改掉。**

### 3.2 新政策：行数是硬约束，可调的是"每条字幕放几个词"

装不下时的处理顺序：

1. **重新切分句子**——把这一条字幕拆成两条（或与下一条合并），让每条都能在规定行数内放下。这正是 Luca 说的「增加一行里面单词数量，或者是减少一行里面显示的单词数量」。时间按词时间切分；没有词时间时按字符比例。
2. 仍然装不下（单个词本身就比一行还宽，例如超长专有名词）→ **只对这一个词**按比例缩字号，并在 Transcript 那一条上标一个提示。
3. **任何情况下都不允许输出超过 `字幕行数` 设定的行数。**

`逐词出现` / 单字模式设为单字时，**必须严格一个词一条**，不许出现两个词。

### 3.3 「重新断句」入口

Luca 要的：「改变排版之后，用户也可以手动再点一下自动字幕重新配对」。

加一个命令 `subtitle.relayout`（headless、undoable，`doc_zh` ≥ 40 字）：按当前排版参数重新断行 + 重新切分，保留原文与词时间，不重新跑 ASR。菜单放「字幕 → 按当前排版重新断句」，并在排版参数改动后于面板上给一个「重新断句」按钮。

注意与现有 `subtitle.auto_relayout` 设置的关系：自动重排是"改参数就自动跑"，这个命令是"手动再跑一次"，两者共用同一个实现，不要写两套。

### 3.4 测试

- 参数 `max_lines = 1`：取 20 条真实字幕（长短不一）跑重新断句，断言**每一条渲染后都正好 1 行**，且没有任何一条的 bbox 超出安全边距。
- 参数"单字模式"：断言每条字幕的词数 == 1。
- `max_lines = 2`：断言 ≤ 2 行。
- 切分之后的时间轴：断言新字幕的时间区间首尾相接、不重叠、总时长不变。

---

## 4. 每片固定验收

`pytest tests -x -q` 0 failed（附冷启动、命令、收集总数 = `tests/test_*.py` 文件数）· `golden --all --repeat 3` PASS（**规则 A**；1d-3 会改断行，若 g09 变红要停下来报，不许重录）· `pyflakes cutora/` undefined 0 · `security_scan` 业务代码 0 · 新增依赖 0 · 新命令 schema 过校验、`headless` 句句为真。

每片单独提交、单独打标签：`T04f-1d1-done` / `T04f-1d2-done` / `T04f-1d3-done`，报告 `T04f-1d1_report.md` 等。

**1d-1 做完必须给 Luca 真机验一次**（能不能点中、能不能拖、滑杆动不动、还卡不卡），他点头才开 1d-2。1d-2、1d-3 做完各交 Claude 审一次。

## 5. 不要做

- 不要为了修这些去重构旧 `draw_subtitle`。
- 不要动解码器、不要动 `textfx` 的模糊实现（有效 σ ±10% 那条测试要一直绿）。
- 不要在 1d-1 里顺手做 1d-2 的 UI。
- 不要提前做 T04f-2 的渲染效果。
- 卡顿不许靠"感觉"优化，必须先有 `CUTORA_PERF=1` 的数字。
