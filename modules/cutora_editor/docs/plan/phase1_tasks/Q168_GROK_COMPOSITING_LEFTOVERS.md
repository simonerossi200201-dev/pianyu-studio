# Q168 · 交给 Grok 4.7 的合成剩余项（Claude 写，2026-10-04）

> 这些是 docs/plan/Q152_COMPOSITING_DESIGN.md 里还没做的部分。Q163–Q167（统一叠压、下拉菜单、
> 抠像溢色开关 / 棋盘格、AI 抠像去接缝 / 批量计算、贝塞尔遮罩）Claude 已经做完并提交，**不要重做、不要改它们的行为**。
>
> **流程**：一次只做一个任务；每个任务一个分支 `task/Q168-Gx`，做完打 tag `Q168-Gx-done`，
> 报告写到 `docs/plan/reports/Q168_Gx.md`（改了哪些文件、新增测试、跑了哪些测试、结果）。Luca 在真机上验收后才做下一个。

## 红线（每个任务都适用，违反任何一条 = 任务作废）

1. **任何时候只允许一个 Grok 动这个仓库。** 不许自己启动 `RUN_GROK_QUEUE.bat`；队列在跑时不许往交互会话粘任务。
2. **golden 基线一律不许重录。** 任何 golden 对不上 → 停下，写进报告，等 Luca。基线只允许「先加后退役」。
3. `PROTECTED_INVARIANTS.md` 里的不变量不许碰；`textfx/` 不许碰；老工程 overlay 的 x/y/w/h 原样读取。
4. 不引入中国服务器 / CCP 关联依赖。新模型只允许 Apache / MIT / BSD 许可。
5. GitHub 只放源码：runtime / 模型 / FFmpeg / 成片永不入库。Grok 生成的媒体素材不许打包进仓库。
6. 不动 CRF / preset；不加「快速模式」。字体只用已入库的 OFL / Apache。
7. 不许改名 / 删除 `~/.grok`；不许打印任何 API key。
8. 老工程打开后画面必须和以前一样（新字段缺省 = 老行为）。
9. 新 Qt 测试文件要加进 `tests/qt_isolate.py`；跑测试用 `CUTORA_PYTEST_ISOLATED=1`。
10. 已有功能（包括 Grok 以前加的）保留，不许为了省事删掉。

---

## G1 · 遮罩参数打关键帧（数组关键帧）

**目标**：遮罩的形状（rect / points，含贝塞尔手柄）和 feather / expand / opacity 能打关键帧，在时间上插值。

- 数据：在遮罩 dict 上加 `kf`：`{"rect": [[t, [nx,ny,nw,nh], interp], …], "points": [[t, [[…], …], interp], …], "feather": …}`；
  t 是**片段内时间**（和现有 `cutora/render/anim_engine` 的关键帧同一套约定，先读它再动手）。
- 求值：`cutora/fx/masks.py` 加 `mask_at(mask, t)` → 插值后的普通遮罩 dict；`layer_fx.apply_layer_fx` / `masks_of` 的调用方在拿遮罩前按当前时间求值。
  points 插值要求点数一致；点数不同的两个关键帧之间 = 保持（hold）。贝塞尔每点 6 个数一起线性插值。
- 缓存键：`signature` 已经包含形状，插值后形状变了键自然变；注意别让 `_CACHE` 爆（现在 64 条上限，够用）。
- UI：`LayerFxPanel` 遮罩表单每个参数旁边加关键帧菱形按钮（照抄现有运动属性的关键帧按钮），画布拖手柄时如果这个遮罩已有关键帧 → 写当前时间的关键帧。
- 关键帧面板（Dope Sheet）里遮罩出一行。
- 测试：插值数值、点数不一致 hold、预览和导出同一帧一致、撤销。

## G2 · 画笔遮罩（位图遮罩）

- 新 `type="brush"`：存一张灰度 PNG（放在工程旁的 `<工程>.cutora_masks/` 目录，**不进仓库**），dict 里只存相对路径 + 尺寸。
- 画布画笔工具：大小 / 软硬度 / 加减（Alt = 擦），`[` `]` 调大小（注意别和 Q157 的 Shift+[ ] 冲突：只在画笔模式里生效）。
- 栅格化：读 PNG → resize 到图层尺寸 → 和其他遮罩一样走 expand / feather / opacity / invert / mode。
- 撤销：一笔一步（按下到松开）。
- 测试：画一笔 → 栅格化有值；擦 → 变 0；保存工程 → 重开还在。

## G3 · 羽化内外分开

- 遮罩加 `feather_in` / `feather_out`（缺省 = 都用老的 `feather` 的一半… **不对，缺省必须和老画面一模一样**：缺省时走老的单高斯；只有用户动了内 / 外才走新算法）。
- 新算法用距离变换：外羽化 = 形状外侧 d ≤ feather_out 的地方 alpha 从 1 渐到 0；内羽化 = 形状内侧 d ≤ feather_in 的地方从 0 渐到 1。
- 面板：「羽化」下面加「内 / 外分开」勾选，勾上露出两个滑杆。
- 测试：老遮罩逐像素不变；内 / 外各自生效。

## G4 · 抠像 Light Wrap + 抠像参数打关键帧

- Light Wrap：前景边缘按 matte 边缘（1 − alpha 的模糊）加回一点「下面那一层」的模糊颜色。需要下面那层的像素：
  导出走 `render_worker` 合成时能拿到已经画好的下层（参考 Q163 `visual_stack.paint_stack` 的视频带），预览走 canvas 的 `_program_bands`。
  缺省 `wrap=0` = 老画面不变。参数：量、宽度。
- 抠像参数（gain / balance / black / white / despill …）能打关键帧：做法同 G1。
- 测试：wrap=0 逐像素不变；wrap>0 边缘颜色向背景靠。

## G5 · 遮罩跟着跟踪走

- 前提：G1 做完。AI 跟踪（`cutora/fx/tracking.py`、`track_bind.py`）现在能把跟踪点绑定到图层位置；加一种绑定目标 = 某个遮罩：
  按跟踪点的位移（1 点 = 平移；2 点 = 平移 + 旋转 + 缩放）把遮罩整体变换，烘焙成 G1 的关键帧（和现有「更新全部绑定」一样重烘焙）。
- UI：AI 跟踪面板的绑定下拉里出现「遮罩：xxx」。
- 测试：方块往右走 → 遮罩跟着往右。

## G6 · 平面跟踪（4 点单应性）

- 在现有点跟踪上加：选 4 个点 → 每帧算单应矩阵（cv2.findHomography）→ 绑定到图片层做透视贴图（四角 corner pin）。
- 图片层要先支持四角变形（corner pin）：新字段 `corners`（缺省没有 = 老画面），渲染用 `QTransform.quadToQuad`。
- 测试：已知单应的合成视频 → 四角误差 < 2 px。

## G7 · 中央监视器图层面板：遮罩 / 抠像子项

- 图层面板（Q152-G）每个图层下面展开子项：遮罩（每个一行，眼睛 = enabled）、抠像、AI 抠像；点子项 = 选中图层并在 Inspector 里滚到对应节。
- 行内混合模式 + 不透明度（设计文档 🅶 里写的）。
- 测试：子项数量对、点子项选中对、眼睛切换走命令可撤销。

## G8 · 节点编辑器补全

- 节点缩略图（节点上显示该节点输出的小图，节流：拖动时不刷）、小地图、复制粘贴节点（Ctrl+C / V，id 重新分配）、Ctrl+G 成组 / 备注框。
- 测试：复制粘贴后 id 不重复、连线保留；缩略图不在导出路径里跑。

## G9 ·（先问 Luca）导出带透明通道

- 只有 Luca 同意了才做。ProRes 4444（`prores_ks -profile:v 4`，`yuva444p10le`）或 PNG 序列；只用于「整片透明背景」的场合。
- **不许**改现有 mp4 的 CRF / preset / 编码参数。

---

完成每一项都在报告里写：改动文件列表、新增 / 修改的测试、跑过的测试和结果、和 golden 的关系（应该是「没碰」）。
