# Q152 · Cutora 合成系统总设计：关键帧 · 遮罩 · 抠像 · AI 跟踪 · 节点 · 图层

> Luca 2026-10-01：关键帧系统升级（运动曲线面板 + 关键帧面板）；所有视频 / 图片类
> 视觉素材加遮罩、蓝绿幕抠像、AI 抠像、高级 AI 跟踪，跟踪在节点编辑器里操作；中央
> 监视器的图层面板专业化。对标国际一线；AI 全部本地、合规、安全；操作和界面符合
> 人体工程学和审美。
>
> 这份文件是**总设计 + 分期计划**，不是一期就全做完。每期做完单独提交、单独跑
> 基线。状态标记：✅ 已有　🅰–🅶 分期　⚠ 需要 Luca 拍板

---

## 0. 一句话

给 Cutora 加一层「合成」：每个视频 / 图片图层背后挂一张小小的效果图（遮罩 →
抠像 → 跟踪 → 变换），预览和导出走同一份求值代码；关键帧从「几个参数能打点」
升级成有曲线编辑器和关键帧面板的完整动画系统；节点编辑器是这张效果图的可视化
操作面；图层面板是它的目录。

---

## 1. 对标：一线软件各自最好的那一块

| 能力 | 取谁的做法 | 具体学什么 |
|---|---|---|
| 关键帧面板（Dope Sheet） | After Effects 时间线属性行 / Resolve Edit 页 Inspector 关键帧轨 | 每个参数一行、菱形可框选整体拖、Alt 拖缩放时距、U 展开有动画的属性、J/K 跳关键帧 |
| 运动曲线（Graph Editor） | After Effects 图表编辑器 / Resolve 曲线编辑器 / Blender Graph Editor | 值曲线 + 速度曲线两种视图、贝塞尔手柄（可断开）、缓动预设（Easy Ease F9）、框选 + 整体缩放、吸附、自动归一化显示 |
| 画布运动路径 | After Effects / Motion | 位置关键帧在画布上显示路径和切线手柄，可以直接拖 |
| 遮罩 | After Effects 蒙版 / Resolve Power Window / Fusion Polygon | 矩形 / 椭圆 / 多边形 / 贝塞尔、羽化（内外分开）、扩展、反转、多遮罩叠加模式（加 / 减 / 交 / 差）、遮罩路径可打关键帧 |
| 蓝绿幕 | Fusion Delta Keyer / Resolve 3D Keyer / Premiere Ultra Key / Nuke Keylight | 吸管选屏幕色、Key 增益 / 平衡、溢色抑制、Matte 控制（黑白裁切 / 收缩扩张 / 柔化 / 去斑点）、Light Wrap、四种查看（原图 / Matte / 合成 / 溢色） |
| AI 抠像 | Resolve Magic Mask（点 / 划线提示、向前向后传播、每帧可修）/ Runway 绿幕 | 提示式交互（点一下 / 框一下）、传播、哪帧错就在哪帧补一点 |
| AI 跟踪 | Resolve 跟踪器（云点 / 点 / 平面）/ Mocha 平面跟踪 / AE 跟踪器 | 跟踪得到的运动直接驱动遮罩、文字、图片，Position / Scale / Rotation 可单独勾选，有偏移 |
| 节点编辑器 | Fusion / Nuke | 从左到右的流、节点上有缩略图、Shift+Space 加节点搜索、1 / 2 送到左右查看器、框选成组、节点着色、备注 |
| 图层面板 | Photoshop 图层 / Resolve 图层面板 / Premiere 项目面板 | 缩略图、显隐 / 锁 / 独奏、拖拽排 z 序、行内混合模式 + 不透明度、颜色标签、搜索过滤、遮罩和抠像作为子项挂在图层下 |

共同规律（设计依据）：
1. **所见即所得**：改参数画面立刻动，拖动时降画质松手精修（Cutora 已有这条肌肉）。
2. **一份数据三个入口**：同一个关键帧，在 Inspector 的菱形、关键帧面板、曲线编辑器、画布路径里看到的是一回事，改哪都行。
3. **可逆、可查**：每个 AI 结果都是数据（遮罩序列 / 跟踪数据），存在工程里，能看、能删、能重算。
4. **AI 是提示式的**：用户给一个点，机器给一个结果，错了用户再补一个点。不要一键黑箱。
5. **渐进披露**：默认只露最常用的三五个参数，高级的收在「高级」里。

---

## 2. 现状审计（HEAD f383e3d）

✅ 已有：
- `render/keyframe.py`：按 path 的关键帧（clip：x / y / scale / rotation / opacity / gain_db；文字：opacity / scale / tracking_em / fill 颜色 / 阴影），线性 / 缓入出 / 保持 / 贝塞尔，OKLab 颜色插值，切割时拆关键帧，命令总线 `keyframe.*` 八条，Inspector 行上有 ◇ 开关（`KeyframeWatch`），时间线条底部画菱形并能右键。
- `render/track_transform.py`：轨道级变换；`render/split_screen.py`：分屏遮罩（硬边 / 羽化 / 曲线分界）。
- `media/alpha_proxy.py`：带 alpha 的源（ProRes 4444 / GIF / VP9）解码。
- `ui/link_graph.py`：QGraphicsScene 做的两列连线画布（Q146 节点连线）。
- `ui/layer_panel.py`：中央监视器右侧图层列表（130 行，QListWidget，只做复制素材 / 属性）。
- 命令总线 + 撤销（每个操作一条 undoable 命令）、`qt_isolate` 测试、ui_golden。
- 运行时：Python 3.10 venv，torch 2.7.1+cu128，onnxruntime 1.23，opencv 4.9，numpy 1.26。

缺口：
1. 关键帧没有曲线编辑器、没有面板，看不到两点之间是怎么走的；可打关键帧的参数只有十来个；没有画布运动路径。
2. 没有任何遮罩（分屏格子不算）。没有抠像。没有跟踪。
3. 没有通用的「效果图」概念：每个效果都要在 canvas.py 和 render_worker.py 各写一遍（Q117 的教训）。
4. 图层面板只有名字列表。

---

## 3. 总架构：每个图层一张效果图

### 3.1 数据模型（存进工程）

每个视频 clip、图片 overlay 多一个可选键 `fx_graph`（没有 = 老工程原样）：

```json
"fx_graph": {
  "version": 1,
  "nodes": [
    {"id": "n1", "type": "mask",   "params": {...}, "pos": [120, 40], "enabled": true, "label": "脸"},
    {"id": "n2", "type": "keyer",  "params": {...}, "pos": [300, 40], "enabled": true},
    {"id": "n3", "type": "ai_matte", "params": {"session": "m_7f3a"}, "pos": [300, 160]},
    {"id": "n4", "type": "tracker",  "params": {"session": "t_1c02"}, "pos": [480, 40]}
  ],
  "edges": [["src", "n2", "in"], ["n1", "n2", "mask"], ["n2", "out", "in"]],
  "keyframes": {"n1.feather": [...], "n1.shape.points": [...]}
}
```

- 节点种类第一批：`mask`（形状遮罩）、`keyer`（蓝绿幕）、`ai_matte`（AI 抠像）、`tracker`（跟踪，输出运动数据不改画面）、`transform`（由跟踪驱动的位移 / 缩放 / 旋转）、`matte_ops`（遮罩运算：合并 / 反转 / 收缩 / 羽化）。
- 隐式的 `src`（这个图层的源画面）和 `out`（送回合成器的画面 + alpha）。没有 fx_graph，或图是空的，就等于 `src → out`，字节不变。
- AI 产物（遮罩序列、跟踪点）**不进工程 JSON**，放工程旁边的 `工程名.cutora_cache/sessions/<id>/`，JSON 里只存 session id、参数、哈希。删掉缓存可以重算；工程文件保持小、可 diff。
- 关键帧继续用 `render/keyframe.py` 的 path 机制，path 前面加节点 id（`n1.feather`）。**数组型关键帧**（遮罩的点）是新能力：按点逐个插值，点数不同的两帧之间不插值（保持）。

### 3.2 求值（预览 / 导出一份代码）

`cutora/fx/graph.py`：`evaluate(layer, frame_rgb, t_local, ctx) -> (rgb, alpha)`。
- 纯函数 + 显式缓存键（源帧哈希 + 节点参数哈希 + t）。
- canvas 和 render_worker 都只调这一个函数，在它们现在调 `paint_fill` 的位置之前。
- 交互时（拖动滑杆）用半分辨率求值，松手全分辨率（沿用 `_preview_interaction_scale`）。
- GPU：遮罩 / 抠像的像素运算先用 numpy + cv2，瓶颈处再上 torch（和 ASR 共用 CUDA 上下文，内存由 `export_guard` 的同一套守卫管）。

### 3.3 命令

每种编辑一条命令：`fx.add_node` / `fx.remove_node` / `fx.set_param` / `fx.connect` / `fx.disconnect` / `fx.move_node`；`mask.set_shape` / `mask.add_point` …；`ai.matte.prompt` / `ai.matte.propagate` / `ai.track.run` …。全部 undoable，全部能从 MCP 调（Grok 以后可以用）。

---

## 🅰 阶段 A · 关键帧系统升级

> 状态（2026-10-01）：**第一批已实现** —— A.2 关键帧面板 + A.3 运动曲线编辑器 + A.5 快捷键
> （`cutora/ui/keyframe_editor.py`，纯函数 `cutora/render/kf_edit.py`，命令 `keyframe.replace`，
> 测试 `tests/test_q152a_keyframe_editor.py`）。时间线下方独立 dock，视图 → 关键帧编辑器 / Alt+K。
> 实现上的取舍：贝塞尔手柄**沿用现有的每段 bez 四元组**（出手柄 = 本段 x1/y1，入手柄 = 上一段
> x2/y2），不新增切线字段，老工程字节不变；y 放开到 −2…3 表示过冲。「平滑」= 按前后点算
> Catmull-Rom 切线写成 bez（AE 自动贝塞尔的「转换一次」做法），不新增求值模式。
> **第二批已实现（同日）**：A.1 参数扩充 —— 文本 / 图片层的描边宽度、发光强度 / 柔度 / 膨胀、
> 阴影角度 / 距离、字距，视频的背景模糊 / 压暗都有 ⏱；`keyframe.bake_host` 烘所有打了关键帧的
> 路径，`fill_bg.fill_spec_at` 给背景填充求值。A.4 画布运动路径（`cutora/ui/canvas_motion.py`）：
> 虚线路线 + 每帧小点 + 关键帧方块，拖方块改那一刻位置（Shift 锁轴），双击跳播放头，一次撤销。
> 顺手修了三个老 bug：PropertyPanel 两个同名 `_current_task`（后者覆盖前者，文本层 ⏱ 一直点不动）；
> 位置打了关键帧的文本命中遮罩全空（画布上点不中）；选中框不跟着关键帧动。
> 还没做：数组型关键帧（等遮罩）、空间插值与时间插值分离、框宽 / 框高关键帧的检查器入口。

### A.1 数据层
- `keyframe.py` 的 path 开放到任意可数值化字段：`ALL_PATHS` 改成「白名单前缀 + 节点 path」。新增可打关键帧的：clip 的 `scale_y`、`fade_*`、bg_fill 的 `blur` / `dim` / `x` / `y`；文字层的 `x` / `y` / `w` / `rotation`、`fx.*` 里所有数值；图片层同 clip。
- 贝塞尔手柄从「一组 (x1,y1,x2,y2) 预设」升级成每个点有**入 / 出切线**（值域 + 时间域），支持断开手柄；旧的 `bez` 字段读进来换算成等价切线，写回时仍写旧字段（老工程字节不变）。
- 新插值：`ease_auto`（Catmull-Rom 自动平滑，AE 默认）、`hold`。

### A.2 关键帧面板（Dope Sheet）
- 位置：时间线下方可折叠的一个 dock（和音频波形同级），或者 Inspector 里「动画」一节 ⚠（见 §13）。
- 行 = 有动画的参数（U 展开当前选中对象全部有动画的参数，UU 展开所有改过的参数）；每行菱形，形状按插值不同（◆ 线性、● 平滑、■ 保持）。
- 操作：点选 / Shift 多选 / 框选；拖移；Alt+拖两端 = 整体缩放时距；J / K 上下一个关键帧；双击菱形进该点的数值；右键：插值、复制、粘贴、删除、反转、「均匀分布」。
- 和 Inspector 的 ◇、时间线条上的菱形、画布路径同步高亮。

### A.3 运动曲线编辑器（Graph Editor）
- 和面板同一个 dock，Tab 切换「表 / 曲线」。
- 值视图（每个参数一条曲线，不同颜色，可勾选显示哪些）、速度视图（Alt+点切换）。
- 手柄：默认联动，Alt 拖断开；Shift 锁 0° / 45°；Ctrl 拖只动时间；缓动预设按钮（线性 / 缓入 / 缓出 / 缓入出 / 平滑 / 保持），F9 = 缓入出。
- 框选后出现变换框，拖边缩放值 / 时间，Alt 以中心缩放。
- 自动缩放到可见曲线（Shift+Z），滚轮缩放时间、Ctrl+滚轮缩放值。
- 右键：烘焙（每帧一个点）、简化（Douglas-Peucker，容差滑杆）、翻转、复制曲线到另一个参数。

### A.4 画布运动路径
- 选中有位置关键帧的对象时，画布画出路径（点线）+ 关键帧位置的方块 + 贝塞尔切线。拖方块 = 改该关键帧的值，拖切线 = 改空间曲线。
- 位置关键帧的空间插值和时间插值分开（AE 的做法）：路径形状由空间切线决定，快慢由曲线编辑器决定。

### A.5 快捷键（接 Q145 的体系）
| 键 | 作用 |
|---|---|
| U / UU | 展开有动画 / 改过的属性 |
| J / K | 上 / 下一个关键帧（有选中对象时） |
| F9 / Shift+F9 / Ctrl+Shift+F9 | 缓入出 / 缓入 / 缓出 |
| Ctrl+Shift+K | 在播放头给选中参数打点 |
| Shift+F3 | 表 / 曲线切换 |

### A.6 验收
- 旧工程所有关键帧求值字节不变（现有 golden g16 等有动画的工程照常过）。
- 新测试：切线换算往返、数组插值、面板 / 曲线 / Inspector 三处同步、撤销一致。

---

## 🅱 阶段 B · 遮罩系统

- 形状：矩形（圆角）、椭圆、多边形、贝塞尔（每点有手柄）、画笔（位图遮罩，带软硬度）。
- 参数：羽化（内 / 外分开）、扩展 / 收缩、不透明度、反转、叠加模式（加 / 减 / 交 / 差 / 亮度最大）、`enabled`。
- 多个遮罩按顺序运算得到一张 alpha；再和抠像 / AI 抠像的 matte 相乘或相加（在 `matte_ops` 节点里选）。
- 画布交互：遮罩工具（M）—— 拖出矩形 / 椭圆，点出多边形，双击闭合；G 在边上加点，Alt+点删点，Ctrl+拖拉手柄；选中遮罩时显示羽化外圈可拖；Shift 约束正方 / 正圆；按住 Alt 以中心画。
- 遮罩所有参数和点都可打关键帧（走 A.1 的数组关键帧）；也可以绑定一个跟踪结果（🅴），跟着走。
- 渲染：形状在 numpy 里用 cv2.fillPoly / 抗锯齿 4× 超采样；羽化用距离变换；缓存按（形状哈希, 画布尺寸）。
- 分屏格子的现有 mask 代码不动（PROTECTED），遮罩系统是图层级的另一层。

> 状态（2026-10-01）：**第一批已实现**。数据：直接存在图层上（Clip.masks / 覆层 dict["masks"]），
> 坐标 0–1 归一到图层自己的框里（视频 = 裁切 / 翻转后的那一帧；覆层 = 它的 x/y/w/h，整体缩放绕中心），
> 羽化 / 扩展单位是画布像素（`px_scale = 帧宽 / 画布上占宽` 换算），所以同一工程预览和导出一致。
> 求值：`cutora/fx/masks.py`（cv2.fillPoly 2× 超采样 + INTER_AREA，expand 用形态学，feather 用高斯，按形状签名缓存）；
> `cutora/fx/layer_fx.py::apply_layer_fx` 是预览（canvas）和导出（render_worker）唯一入口，紧跟 `_fade_qimage` 之后；
> 覆层走 `mask_canvas_image`（先整幅画出再按框的仿射把遮罩乘上去，命中图 `_no_mask` 不受影响）。
> 命令：`fx.mask_add / update / delete / reorder`（可撤销）。
> Inspector：`LayerFxPanel`（视频 + 覆层都挂），矩形 / 椭圆 / 多边形按钮、遮罩列表（勾选 = enabled、双击改名）、
> 模式 / 羽化 / 扩展 / 不透明度 / 圆角 / 旋转 / 反转；画布：`canvas_mask.py` 8 个角点 / 边中点手柄 + 整体拖动、
> 多边形点出 + 双击 / Enter 闭合 + Esc 取消、G 加点 / Alt+点删点、Shift 正方 / 正圆。
> 还没做：画笔遮罩、羽化内外分开、遮罩参数打关键帧（数组关键帧）、绑定跟踪（等 🅴）、图层面板子项。
>
> 状态（2026-10-04，Q167）：**贝塞尔遮罩已实现**。`type="bezier"`，points 每点 `[nx, ny, 进dx, 进dy, 出dx, 出dy]`
> （手柄 = 相对锚点的偏移，拖锚点 / 平移时手柄跟着走），≥ 2 个锚点、首尾闭合；栅格化每段 24 段折线逼近（直段只取端点）。
> 编辑函数：`split_bezier`（de Casteljau 插点，形状不变）、`with_handle`（对称 / Alt 打断）、`toggle_smooth`（尖角 ↔ 平滑）。
> 画布钢笔（「＋贝塞尔」）：点 = 尖角、按住拖 = 对称手柄（Alt 只动出手柄）、点回起点 / 双击 / Enter 闭合、右键退一步、Esc 取消；
> 编辑：拖锚点 / 手柄、点曲线中点小圆插点、Alt+点锚点删除、双击锚点切换尖角 / 平滑。顺带修了：遮罩 / AI 点选模式里右键以前先弹右键菜单。

---

## 🅲 阶段 C · 蓝绿幕抠像（无 AI，纯算法）

参考 Fusion Delta Keyer 的参数集，但默认只露五个：

| 露出 | 参数 |
|---|---|
| 基本 | 屏幕色（吸管，画布上拖出一块取平均）、Key 增益、平衡（偏青 / 偏品）、溢色抑制量、Matte 柔化 |
| 高级 | Matte 黑 / 白裁切、Gamma、收缩 / 扩张（px）、去斑点（黑 / 白）、Light Wrap 量 + 柔度、Pre-blur、Despill 方式（平均 / 最大 / 自定义色） |
| 查看 | 原图 / Matte（黑白）/ 合成 / 溢色（叠加显示）/ 棋盘格 |

算法：色差键（`alpha = 1 - k·max(0, G - max(R,B)·balance)` 的广义版，支持任意屏幕色经色相旋转）；溢色抑制在 YCbCr 里把屏幕色方向的饱和度拉回；Matte 用 erode / dilate / GaussianBlur；Light Wrap = 背景模糊后按 matte 边缘加回前景。全部 numpy / cv2，1080p 单帧 < 8 ms（目标）。

内置 3 个预设：标准绿幕、标准蓝幕、手机绿幕（压缩严重：Pre-blur 1px + 去斑点加强）。

> 状态（2026-10-01）：**已实现**。`cutora/fx/keyer.py`：YCbCr 色差键（屏幕色方向 proj / 垂直 perp，
> alpha = clip(1 + gain·perp_w − gain·proj)，balance 给垂直两侧不同权重），溢色抑制 = 色度沿屏幕色方向的正分量按比例减掉（线性 RGB），
> Matte 整理 black / white / gamma / despot（开闭）/ erode / blur，pre-blur；查看 comp / matte / source / spill；
> 3 个预设（标准绿幕 / 标准蓝幕 / 手机绿幕）。全 cv2 单通道 transform，1080p 单帧 **~110–135 ms**（云端 2 线程机器；
> 目标 < 8 ms 没到，Luca 机器上会快不少，后续按需加半分辨率交互 / GPU 路径）。
> 命令 `fx.keyer_set`（可撤销，全默认且关闭时清空 dict）。Inspector：开关 / 预设 / 屏幕色色块 + 画布吸管（取 7×7 平均）/
> 增益 / 平衡 / 溢色 / Matte 柔化 / 查看，高级折叠：黑 / 白 / Gamma / 收缩 / 去斑 / Pre-blur。图片覆层也能抠（`get_cached_image` 之后）。
> 还没做：Light Wrap（要背景 backdrop）、棋盘格查看、Despill 方式选择、抠像参数打关键帧。
>
> 状态（2026-10-04，Q165）：**消除溢色单独开关**（`despill_on`，默认关 = 只抠像、颜色一点不动）+ 方式
> （`limit` 标准：屏幕色主通道超出另外两通道平均的那一截才减；`axis` 强力 = 老算法）+ 范围（`edges` 只处理边缘 / 半透明处，
> 权重 = 附近透明度；`all` 整个画面）。老工程（整份老参数、没有 despill_on）按老样子：开、axis、all —— 打开画面不变。
> 透明阈值 / 不透明阈值（= 黑 / 白裁切，即蒙版对比度）挪到主区。预览：画面里有抠像 / AI 抠像 / 遮罩时画布底下铺棋盘格
> （`cutora/ui/alpha_checker.py`，右键菜单可关），以前铺黑，抠掉的地方看着还是黑的。导出不变：透明处露出下面的轨道，下面没有就是黑。

---

## 🅳 阶段 D · AI 抠像（本地）

### D.1 选型（已核对许可，2026-10-01）

| 模型 | 作者 | 许可 | 用途 | 大小 | 运行时 | 结论 |
|---|---|---|---|---|---|---|
| **SAM 2.1**（Hiera B+ / L） | Meta | Apache 2.0（代码 + 权重） | 提示式分割 + 视频传播（masklet），多目标、中途加提示 | B+ 81M / L 224M 参数 | Python ≥3.10、torch ≥2.5.1 —— 和现有 venv 兼容 | **主力**：AI 抠像 + 对象跟踪一个模型两用 |
| SAM 3 / 3.1 | Meta | SAM License（允许商用和再分发，禁军用 / 受制裁地区；权重在 HF 需申请访问） | 还能用文字提示（「所有人」「那只狗」） | 848M 参数 | **Python ≥3.12、CUDA ≥12.6** | 现在的 venv 是 3.10，**先不上**；运行时升到 3.12 时作为可选升级 |
| BootsTAPIR / TAPNext++ | Google DeepMind | Apache 2.0（代码 + 权重） | 任意点跟踪（在线 / 因果版可边播边跟） | ~120 MB | PyTorch 版可用 | **点 / 平面跟踪主力**（🅴） |
| CoTracker3 | Meta | CC-BY-NC 4.0 | 点跟踪 | — | — | 非商用许可。Cutora 是非盈利内部使用，法律上可以，但**不选**：TAPIR 同能力且许可干净 |
| MatAnyone / MatAnyone 2 | NTU S-Lab（有 SenseTime 新加坡参与） | NTU S-Lab License 1.0，禁商用 | 发丝级 alpha 精修 | — | — | SenseTime 是商汤（中国），**不引入** |
| RVM、BiRefNet、ViTMatte、Depth Anything 等 | 字节跳动 / 南开 / 华科 等 | 各异 | — | — | — | **按「不引入 CCP 关联依赖」不引入** |
| VideoMaMa | 高丽大 / Adobe / KAIST | 待公布 | 粗 mask → 精 alpha | 扩散模型，重 | — | 关注，许可出来再评估 |

**发丝级精修**不靠第三方模型：SAM 2 的 mask 当 trimap，再用经典 **Closed-form Matting / 导向滤波** 做边缘 alpha（Levin 2008、He 2010，算法公开、无许可问题，numpy + scipy.sparse 可做，边缘带宽 ≤ 20 px 时 1080p 单帧 ~50 ms，离线算、缓存）。

### D.2 交互（对标 Resolve Magic Mask）
- 在任一帧点一下（正点）/ Alt+点（负点）/ 拖一个框 → 立刻出 mask 预览（彩色半透明盖在画面上）。
- 「向前传播」「向后传播」「全部」按钮；传播时有进度条和取消；传播在后台线程，UI 不卡。
- 时间线上该图层出现一条 mask 轨：绿色 = 有 mask，黄色 = 低置信度（SAM 的 IoU 预测 < 0.7），没有 = 没算。点黄色帧补一个点再传播。
- 多个对象 = 多个 masklet，每个一个颜色，可以分别开关、合并、相减。
- 输出选项：硬边 / 羽化 / 精修（closed-form）；收缩扩张；和形状遮罩、蓝绿幕 matte 一起进 `matte_ops`。

### D.3 合规与安全（硬性）
1. **全程本地**：推理不碰网络。模型只能通过现有 `cutora.core.net` 白名单（huggingface.co）从应用内「模型管理」下载，下载前显示许可全文和 sha256，用户点「同意并下载」才下；下载后校验 sha256。
2. **模型不入 git**（现有规则），打包时放 `runtime/models/`，`pack_internal` 的许可闸门把 SAM 2.1 的 Apache 2.0 LICENSE 和 NOTICE 一起打进包。
3. **许可清单**：`cutora/asr/models.py` 现有的模型登记表扩展成「所有模型」登记表（id、来源 URL、许可、sha256、大小、用途），`tests/test_t09_*` 的审计测试覆盖它。
4. **无遥测、无上报**：AI 节点不记录任何画面内容到日志；日志只记时长和帧数。
5. **用户数据**：mask 缓存在工程旁，删工程时一起删；导出时不会把 mask 之外的任何东西写进成片。
6. **不做的事**：不做人脸识别 / 身份匹配；不做「换脸」；不自动联网更新模型。
7. 不改 CRF / preset；AI 抠像只改 alpha，不碰编码链路。

### D.4 性能预算
- SAM 2.1 B+ 在 8 GB 显存、1080p：传播约 15–25 fps（Meta 数据，A100 更快；RTX 40 系笔记本预计 8–15 fps）。缓存命中后预览零开销。
- 无 NVIDIA 显卡：CPU 也能跑，慢 10–20×，UI 要明示「大约 X 分钟」而不是假死。
- 显存和 ASR 互斥：抠像时 ASR 排队（复用 `export_guard` 的内存守卫）。

> 状态（2026-10-01）：**第一批已实现**（真模型在 Luca 机器上首跑验证前，SAM 后端标记为「未在真机验证」）。
> - 选型落地：SAM 2.1 Hiera-L（默认，16 GB 显存）+ Hiera-B+（轻量），走 **transformers 自带的 `Sam2VideoModel`**
>   （transformers 5.10.4 已在运行时里），**零新依赖**：不装 sam2 / hydra / iopath；transformers 的图像处理器要 torchvision，
>   所以预处理（1024² 双线性 + ImageNet 归一化）、提示点缩放、后处理（双线性放回 + sigmoid）在 `cutora/fx/segment.py` 自己做。
> - 登记：`resources/data/models.json` 两行 `kind=matte`，钉死 revision（HF 提交 665f8e2a / b7320756）和全部 5 个文件的 sha256
>   （safetensors 用 LFS oid；小文件按 git blob sha1 校验后算 sha256）；`model_provenance.json`、`THIRD_PARTY_LICENSES.md` 已加。
> - 下载：`net.download_to_file` 加 progress_cb / cancel_event（取消保留 .part 可续传）；`download_model` 带总进度、跳过已校验文件；
>   `ModelDownloadDialog` 许可闸门（作者 / 许可 / 来源 / revision / 大小 / sha256 → 「同意并下载」→ 后台进度 → 取消）；
>   设置 → 模型 页多了「AI 抠像」组，下载改走这个对话框（ASR 模型也受益，不再卡 GUI 线程）。
> - 数据：`Clip.ai_matte`（session / model / objects / prompts（源帧归一化坐标）/ feather / shrink / refine / view），进 CLIP_KEYS；
>   结果缓存 `cutora/fx/matte_store.py`：`<user_cache>/matte/<session>/f%06d_o%d.png` + index.json（fps / 尺寸 / 每帧分数），
>   **没有放在工程旁**（渲染子进程拿不到工程路径；和波形 / 代理缓存同一套位置），工程只存 session id，缓存丢了重传播即可。
> - 求值：`layer_fx.apply_layer_fx(..., src=源帧信息)`：蓝绿幕 → AI 抠像 → 形状遮罩；mask 按图层同样的裁切 / 翻转顺序变换；
>   收缩 / 羽化 / 导向滤波精修（He 2010，纯 cv2）；查看 comp / matte / overlay；预览（canvas / PreviewEngine）和导出（render_worker）同一入口。
> - 后端接口 `segment(rgb, prompts)` / `propagate(frames, prompts_by_offset, on_frame, cancel)`；SAM 流式一帧帧喂（倒着传播 = 倒着喂），
>   每帧后把旧像素帧和窗口外的非条件输出弹掉，显存不随长度涨；`FakeBackend`（颜色泛洪）给测试和 `CUTORA_MATTE_BACKEND=fake`。
> - 命令：`ai.matte.session`（create / clear / detach）、`ai.matte.object`（add / remove）、`ai.matte.prompt`（add / clear）、`ai.matte.set`，可撤销。
> - 交互：Inspector「AI 抠像」节（状态 / 模型 / 启用 / 查看 / 对象列表 / 点选对象 / 清这帧的点 / ◀ 向后 · 向前 ▶ · ⟷ 全部 / 进度 + 取消 /
>   羽化 / 收缩 / 精修 / 清除 / 移除）；画布 `canvas_matte.py`：点 = 正点、Alt+点 = 负点、拖 = 框、右键 / Esc 退出，当前帧彩色结果叠加 + 标记；
>   时间线片段顶部 3 px 覆盖条（绿 / 黄）。任务在 `MatteWorker`（QThread）里跑，GPU 互斥锁 + `is_ai_running`；「释放显存」会一起卸 SAM。
> - 第二批（2026-10-02）：
>   - **显卡排队** `cutora/core/gpu_gate.py`：AI 字幕（AlignmentWorker、文本定位识别）、AI 抠像（MatteWorker）、以后的 AI 跟踪
>     先到先得，后来的在自己的工作线程里排队（界面提示「排队中：显卡正被「X」使用」，排队中可取消）。
>     更正第一批报告：第一批其实只有抠像任务之间互斥，**AI 字幕并没有加锁**，两者可能同时占显卡；现在才真正排队。
>   - **对象 保留 / 挖掉**：objects[].mode = add / subtract；`combined_raw` = 保留对象并集 ×（1 − 挖掉对象并集）；
>     有保留对象但这一帧它们没结果 → 不套（避免和相邻帧对不上闪烁）；没有保留对象 → 全画面减掉挖掉对象。
>   - **缓存 mask 最长边 ≤ 1920**（`MASK_MAX_EDGE`）：SAM 后端直接按这个尺寸放大 logits，渲染时 `transform_mask` 按比例换算裁切再放大到图层。
>     说明：SAM 网络输入固定 1024²，「半分辨率传播」不会让模型本身变快；4K 素材省的是 mask 放大 + PNG 编码 + 磁盘。
>   - **估时**：`estimate_seconds`（有实测用实测：每次传播 ≥ 8 帧记一次 `speed.json`，按 模型+设备 滑动平均；没有用保守默认）；
>     传播开始显示「预计约 X」；CPU 上超过 1 分钟先弹窗确认。
>   - **低置信度跳转**：面板「◀ 上一处低置信 / 下一处低置信 ▶」；时间线上点片段顶部的黄段 → 播放头跳到那段开头（片段照常选中）。
> - 还没做：裁切改变后提示点的重算提示、4K 解码本身的降采样（OpenCV 取不到缩小解码）。
> - 第三批（2026-10-04，Q166）：
>   - **去接缝**：`merge`（默认开）—— 两个以上「保留」对象先取并集，再只在「两个对象都很近」的交界带（各自外扩 2r 后重叠处，
>     r = 缓存短边 0.8%）做闭运算把缝补实；外轮廓不动，离得远的对象不会被连起来（`ai_matte.merge_masks`）。面板「合成一整块（去接缝）」。
>   - **点完一批一次算**：点选不再每点一下就算（「每点一下就算」复选框，默认不勾）；「计算这一帧（N 个点）」/ 画布 Enter 一次算完；
>     勾上自动时，算的过程中点的会排队，算完用最新的全部点再算一次（以前直接拒绝「上一个任务还在跑」）。
>   - **彩色区域显示开关**：面板「显示彩色区域」/ 画布 H，只影响点选时的半透明叠加，不进工程。

---

## 🅴 阶段 E · AI 跟踪

三种跟踪器，都在节点里：

| 跟踪器 | 用什么 | 输出 | 典型用途 |
|---|---|---|---|
| 对象跟踪 | SAM 2.1 masklet 的外接框 / 质心 / 面积 | 位置 + 缩放（+ 粗旋转） | 文字跟着人走、马赛克跟着脸、遮罩跟着车 |
| 点跟踪 | BootsTAPIR 在线版（1–8 个点） | 每个点的轨迹 + 可见性 | 贴一张图到某个角落、稳定 |
| 平面跟踪 | 4+ 个 TAPIR 点解单应性（RANSAC） | 4 角 → 透视变换 | 屏幕替换、海报替换（🅵 之后，需要透视贴图） |

- 跟踪结果 = 一条「运动数据」，可以**烘焙成关键帧**（进 🅰 的曲线编辑器微调）或**实时绑定**（源改了重算）。
- 绑定面板（对标 Resolve）：勾选 位置 / 缩放 / 旋转，偏移 X / Y，平滑（帧数），「从这帧起 / 到这帧止」。
- 稳定：把点跟踪的运动反向应用到图层，附带「裁切 / 缩放填充」选项。
- 失败处理：遮挡时 TAPIR 给出可见性 < 0.5 的帧，画成红段，用户可以在红段两头各加一个点重跟。

> 状态（2026-10-02）：**第一批已实现**（真模型首跑前，TAPNext++ 后端标记为「未在真机验证」）。
> - **选型更新**：设计稿写的是 BootsTAPIR（DAVIS AJ 62.4）；2026-07 Google DeepMind 在 HF `google/tapnet` 放出了
>   **TAPNext++**（512：67.0 / 256：65.6，Apache 2.0，PyTorch），按「用可用的里最强的」改用它。默认 512 档，另登记 256 档（快 3–4 倍）。
>   两个检查点各约 2.4 GB（训练检查点，含优化器状态），钉死 HF 提交 5d3fb48e 和 sha256。
> - **代码收录**：`cutora/third_party/tapnext/`（Apache 2.0，LICENSE 原文同目录）。改动：去掉 einops（等价 permute / reshape）、
>   去掉 torchvision（同参数布局的 EncoderBlock 重写，含老命名兼容）、去掉 torch.export 注册、RGB 输入。
>   随机权重下和上游代码**逐位一致**（离线 / 在线 / 512 输入三种都比过）。零新依赖。
> - **安全加载**：`load_state_dict_safely` 强制 weights_only=True（运行时全局设的 TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD 调用期间摘掉）；
>   训练检查点里夹带的超参数对象按名字换成什么都不执行的占位类；含 os.system 之类的恶意检查点会被拒绝（有测试）。
> - 数据：`Clip.tracking`（session / model / points / queries（源帧归一化）/ bindings），进 CLIP_KEYS；
>   结果缓存 `cutora/fx/track_store.py`（`<user_cache>/track/<session>.json`，每帧每点 [x, y, 可见概率]）。
> - 跟踪：`tracking.plan_runs` 每帧归离它最近的那次放点，从放点处往前 / 往后各跑一段；TAPNext++ 在线逐帧（倒着跑 = 倒着喂），
>   所有点一次跟；`FakePointBackend`（模板匹配 + 亚像素）给测试和 `CUTORA_TRACK_BACKEND=fake`。和 AI 字幕 / 抠像共用显卡队列。
> - 运动求解 `cutora/fx/motion.py`：1 点 = 平移；≥2 点 = 加权最小二乘相似变换（平移 + 旋转 + 缩放），只剩一个点可见时退化为平移、
>   旋转 / 缩放插值；跟丢帧插值补齐并标记；居中滑动平均平滑（参考帧保持不动）；对象跟踪 = AI 抠像 mask 的质心 / √面积比 / 主轴（粗旋转）。
> - 烘焙 `cutora/fx/track_bind.py`：和导出同一条坐标链（裁切 → 翻转 → 适配框 → 绕中心旋转）；跟随（文字 / 图片 / 另一条视频，
>   以当前帧为基准保持相对位置，可选旋转 / 缩放，偏移）、稳定（片段自己反着动，可选「自动放大不露黑边」二分求最小倍数，上限 3×）；
>   每帧一个线性关键帧，RDP 精简（0.25 px / 0.05° / 0.0005）。结果是普通关键帧，导出路径没改，golden 不受影响。
> - 命令：`track.session / point / set / apply / unbind`（可撤销；apply = 写关键帧 + 记绑定一次撤销）。
> - 交互：Inspector「AI 跟踪」节（模型 / 跟踪点列表 / 在画面上放点 / 去掉这帧的点 / 向后·向前·全部 / 进度 + 取消 + 排队提示 /
>   上一处·下一处跟丢 / 应用到：运动来源、方式、目标、位置·旋转·缩放、平滑、自动填满、精简 / 应用、更新全部绑定、绑定记录）；
>   画布 `canvas_track.py`：点 = 放 / 挪选中的点，Alt+点 = 去掉，轨迹 ±12 帧，实心 = 看得见、空心红 = 跟丢，方框 = 本帧放点；
>   时间线片段顶部蓝条（红 = 跟丢，点红段跳过去）。
> - 顺手修：模型状态检查每次都重算几 GB 权重的 sha256（抠像面板刷新会卡几秒）→ 按 路径 + 大小 + 修改时间 缓存。
> - 还没做：平面跟踪（4 点单应性 + 透视贴图，等 🅵）、遮罩跟着跟踪走（遮罩参数还不能打关键帧）、实时绑定（现在是「更新全部绑定」重烘焙）、
>   分屏格子里的片段（坐标按整画布算）、轨道级变换。

---

## 🅵 阶段 F · 节点编辑器

- 新模块 `cutora/ui/nodes/`：`NodeScene` / `NodeView`（QGraphicsView，复用 `link_graph` 的贝塞尔连线、主题取色、滚轮缩放）、`NodeItem`（标题、输入 / 输出端口、缩略图、启停灯、颜色标签）、`PortItem`。**不认识业务**，节点类型由注册表给（和 `fx/graph.py` 的节点注册表是同一张）。
- 打开方式：选中图层 → Inspector「效果」一节的「在节点里编辑」，或双击图层面板里的 fx 徽章；作为底部 dock，和关键帧面板并列 Tab。
- 操作（Fusion 习惯）：Shift+Space 搜索加节点；拖线到空白处松开 = 弹出加节点菜单并自动接上；Ctrl+T 交换输入；1 / 2 送到左右查看器（Cutora 先只做一个查看器，1 = 在画布上看这个节点的输出，Esc 回最终）；Ctrl+P 直通（旁路）；Delete 删节点自动把上下游接上；框选、Ctrl+G 成组、F2 改名、右键着色 / 加备注。
- 节点内的参数编辑仍在 Inspector 里（选中节点，Inspector 显示该节点参数），不在节点上放一堆控件 —— 画面干净，参数面板统一。
- 小地图（右下角，可关）；网格对齐；自动排布（Ctrl+L）。

> 状态（2026-10-02）：**第一批已实现**。
> - 数据 / 求值：`cutora/fx/graph.py`（无 Qt）。图层上可选键 `fx_graph`（Clip + 图片层 dict；文字层不开放，遮罩照旧在检查器里）；
>   隐式 `src` / `out`，边 = [上游, 下游, 端口]，每个输入一根线，成环 / 遮罩接画面端口一律拒绝；画面接到遮罩端口自动取它的 alpha（Fusion 习惯）。
>   节点：蓝绿幕 · AI 抠像 · 形状遮罩 · 遮罩运算（合并 / 相减 / 相交 / 取亮 / 取暗 / 差值 + 反转 / 收缩 / 羽化）· 套用遮罩（只留 / 挖掉 / 替换）·
>   模糊（可限定区域）· 调色（亮度 / 对比 / 饱和 / 增益，可限定区域）。旁路 = 主输入原样传下去。
>   **有图就只按图求值**，没图 = 老流水线（字节不变）。「转为节点」= `from_legacy`：src → 蓝绿幕 → AI 抠像 → 套用遮罩(形状遮罩) → out，
>   和老流水线**逐位一致**（测试覆盖 6 种组合 × 2 个像素比例）。
> - 节点 params 和图层老键同形（`keyer` / `masks` / `ai_matte`），所以老命令（fx.mask_* / fx.keyer_set / ai.matte.*）带 `node_id` 就改节点；
>   检查器里的遮罩 / 抠像 / AI 抠像面板、画布手柄、吸管、AI 点选全部原样复用。新命令 `fx.graph`（create / delete / replace）、
>   `fx.node`（add / remove / set / move / swap）、`fx.link`（connect / disconnect），一次手势一次撤销。
> - 渲染：视频片段走 RenderWorker（预览 / 导出同一份）；图片层在 frame_renderer 里整张图按图求值。
> - 查看器（1 / Esc）只在预览里生效：视频片段靠 PreviewEngine 给 RenderWorker 的 `_fx_preview` 标记，图片层靠画布画覆层时的 `preview_scope()`
>   （线程局部）；导出 / 子进程永远不在里面。不存工程，换图层 / 关面板自动关。
> - UI：`cutora/ui/nodes/`（items / view / editor），底部 dock「节点」和关键帧面板同一排 tab（视图 → 节点编辑器 / Alt+N）。
>   手势：拖端口连线；从接了线的输入往外拖 = 拎起（改接 / 丢空白处断开）；拖到空白处 = 弹菜单、新节点直接接好；右键边「在中间插入」；
>   拖节点吸附 8 px 网格；Delete（上下游接上）/ Ctrl+P / Ctrl+T / F2 / Ctrl+L / 1 / Esc / Shift+空格或 Tab 搜索 / F 适应；双击节点 = 在检查器里编辑。
>   检查器「节点图」卡片：转为节点 / 打开节点编辑器 / 删除节点图 / 回到图层；选中节点时只显示那个节点的参数（新节点类型用通用参数面板）。
>   时间线的 AI 抠像覆盖条也读节点图里的 AI 抠像节点。
> - 和原稿不同：**跟踪仍挂在图层上**（不做成节点）—— 它输出的是写到别的图层上的关键帧，不改这一层的画面，放进图里反而要多一种「数据线」类型。
>   `tracker` / `transform` 节点等平面跟踪（透视贴图）时再定。
> - 还没做：节点缩略图、小地图、Ctrl+G 成组 / 备注、复制粘贴节点、第二个查看器、节点参数打关键帧（`n1.feather` 路径）、
>   图层面板 fx 徽章双击打开。ui_golden 的 u11 节点 case 按「先加后退役」等 Luca 同意后再录。

---

## 🅶 阶段 G · 中央监视器图层面板专业化（可以早做，不依赖 A–F）

> 状态（2026-10-01）：**第一批已实现**（`cutora/batch/layer_model.py` + `cutora/ui/layer_panel.py`，
> 测试 `tests/test_q152g_layer_panel.py`）：QTreeView + 委托、缩略图、👁 / 🔒 / S、拖拽排 z 序、
> 搜索 + 类型过滤、8 色标签、重命名、删除、右键、键盘、和监视器双向选择同步、全部可撤销。
> `hidden` / `locked` 进了 Clip 与 CLIP_KEYS，预览 / 导出 / 混音 / 画布命中一条路。
> 还没做：行内混合模式 + 不透明度（等 🅰 的 Inspector 联动一起），遮罩 / 抠像子项（等 🅱 🅳），
> 主时间线上隐藏层的灰显。

从 QListWidget 换成 QTreeView + 自定义委托：

- 每行：缩略图（视频 / 图片取当前帧、文字取渲染小样）、名字（双击改）、类型图标、时长；右侧三个小按钮：👁 显隐、🔒 锁定、S 独奏（Alt+点 = 只独奏这个）。
- 行内第二排（选中时展开）：混合模式下拉 + 不透明度滑杆，和 Inspector 同步。
- 拖拽排 z 序（跨轨道 = 换轨），拖到另一条视频的面板 = 搬图层（接 Q149 的 layer_transfer）。
- 子项：遮罩、抠像、AI 抠像、跟踪 作为图层下的子项（Photoshop 蒙版缩略图那种），可单独开关 / 删除，点它 = Inspector 跳到对应节点。
- 颜色标签（8 色）、搜索框（按名字 / 类型过滤）、按类型折叠分组。
- 多选：Ctrl / Shift；多选时 Inspector 显示共有属性，改一个全改（已有的多选机制）。
- 右键：复制素材 / 属性 / 全部（现有）、复制到 选中的 N 条 / 全部、重命名、着色、删除、「在时间线里定位」。
- 键盘：↑↓ 选、Space 显隐、L 锁、Delete、Ctrl+D 复制、F2 改名、Ctrl+F 搜索。
- 和画布 / 时间线选择双向同步（已有 `_sync_selection` 的路子）。

---

## 10. 人体工程学与审美规范（所有阶段共用）

1. **一致的手势语法**（Q145 已定）：Shift 约束、Alt 复制 / 从中心 / 反向、Ctrl 另一种变换；一个手势一次撤销。
2. **面板尺寸**：所有新面板遵守 `UI_INSPECTOR_SPEC.md`：280 / 480 两档宽度锁、行高 28、字号 FONT_LABEL、间距只用 `tokens.SPACE_*`、颜色只用 tokens（深色主题，对比度 ≥ 4.5:1，`theme/contrast.py` 的测试覆盖新控件）。
3. **渐进披露**：每个新节点默认露 ≤ 5 个参数，其余在「高级」折叠里；首次使用有一行提示，不弹窗。
4. **反馈**：任何超过 300 ms 的操作有进度 + 取消；超过 3 s 的在状态栏留一条结果；AI 结果用颜色叠加预览而不是黑白弹窗。
5. **不打断**：传播 / 跟踪在后台线程，期间可以继续剪。
6. **容错**：删节点自动续线；删关键帧不丢值；AI 失败给出「为什么 + 怎么办」一句话。
7. **可达性**：所有按钮有 tooltip 含快捷键；键盘能走完整个面板；颜色标签同时有形状差异（色盲友好）。
8. **美学**：曲线编辑器和节点图用和时间线同一套网格 / 线宽 / 圆角（RADIUS_SM）；关键帧菱形 8 px、选中 10 px 带外描边；曲线 1.5 px，手柄 6 px 圆点；节点 160×48 圆角卡片，端口 8 px，连线 2 px 贝塞尔带轻微发光；不用纯黑、不用描边字（风格预设红线延伸到 UI）。

---

## 11. 测试与基线

- **golden 不动**：golden 工程没有 fx_graph，求值入口「没有 fx_graph 就不进入」，字节不变。每期跑 `CHECK_BASELINE.bat`。
- **ui_golden**：新面板各加一个 case（u09 关键帧面板、u10 曲线、u11 节点、u12 图层面板），按「先加后退役」；现有待重录的 u03f / u04f / u07c / u08c 仍等 Luca 的「同意重录」。
- 每期新增 `tests/test_q152x_*.py`：数据往返、求值像素、命令撤销、UI 行为（qt_isolate）。
- AI：用 2 秒合成视频（移动色块）做确定性测试，不下载真模型跑 CI（没有模型时跳过并标记）。
- 性能：每期在 1080p 测单帧耗时写进 `docs/plan/reports/Q152_perf.md`，退化 > 20% 不合入。

---

## 12. 工程量估计（我做，按提交粒度）

| 阶段 | 内容 | 预计提交数 | 依赖 |
|---|---|---|---|
| 🅶 图层面板 | QTreeView + 委托、显隐锁独奏、拖拽、搜索 | 2–3 | 无 |
| 🅰 关键帧 | 数据层 → 面板 → 曲线 → 画布路径 | 4–5 | 无 |
| 🅱 遮罩 | 数据 + 求值 + 画布工具 + Inspector | 3–4 | fx_graph 骨架（和 🅱 一起做） |
| 🅲 蓝绿幕 | 算法 + Inspector + 预设 | 2 | 🅱 的 matte_ops |
| 🅳 AI 抠像 | 模型登记 / 下载 / 许可闸门 → SAM 2.1 推理封装 → 交互 → 精修 | 4–5 | 🅱、显存守卫 |
| 🅴 AI 跟踪 | TAPIR 封装 → 对象 / 点跟踪 → 绑定 / 烘焙 / 稳定 | 3–4 | 🅰 🅳 |
| 🅵 节点编辑器 | NodeView → 注册表 → 查看器联动 → 快捷键 | 3–4 | 🅱 起的 fx_graph |

每个提交都是能用、能回退的一小块；每期结束让 Luca 实际用一遍再进下一期。

---

## 13. ⚠ 需要 Luca 拍板

1. **起步顺序**：建议 🅶 图层面板 → 🅰 关键帧 → 🅱 遮罩 + 🅲 蓝绿幕 → 🅳 AI 抠像 → 🅴 跟踪 → 🅵 节点编辑器。节点编辑器放最后是因为它只是「操作面」，底下的效果图先用 Inspector 就能用；也可以把 🅵 提前到 🅱 之后。
2. **显卡**：你这台机器的显卡型号和显存？决定 SAM 2.1 默认选 B+（81M，4 GB 够）还是 L（224M，建议 8 GB+）。
3. **许可边界**：只用 Apache 2.0 / MIT / BSD 的模型（现方案），还是允许 CC-BY-NC（Cutora 非盈利内部用，法律上可以）？现方案不需要 NC 模型，问一下只是定规矩。
4. **SAM 3**：要不要为它把运行时 Python 从 3.10 升到 3.12？牵涉整个 venv 重建、所有依赖重锁、重新过 whisperX，是一个单独的大任务。建议先用 SAM 2.1，等 Phase D 稳了再评估。
5. **关键帧面板的位置**：时间线下方独立 dock（AE 式，一眼看全部）还是 Inspector 里的「动画」节（Resolve 式，省地方）？建议 dock，可折叠。
6. **ui_golden 重录**：A / F / G 都会动面板布局，要在期末各加一个 case；现有 5 个待重录的还是等你一句「同意重录」。
