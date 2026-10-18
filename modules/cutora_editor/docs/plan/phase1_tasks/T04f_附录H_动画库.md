# T04f 附录 H — 动画库（T04f-4）

> 现状：`textfx/anim.py` 的基础设施已经很完整——`Transform` 有 alpha / scale / dx,dy / rot / clip+clip_dir / blur_sigma_em / tracking_em / fill_progress / extra_color 全部通道，`unit_progress` 处理错峰与时长压缩，`transform_for` 处理 in/hold/out 与 `OUT_MIRROR` 镜像，`stable_seed` / `rng_for` 就绪。
> 缺的是内容：`ANIMS` 里只有 `none` / `fade` / `float_in_up` 三条，附录 A §3 规划的其余 24 条一条没做。
> **这一片分三次交付**，不许一次性把 24 个动画砸进来——那样没法审，缺陷会被埋掉。

## 0. 三条贯穿全片的铁律

**0.1 每加一个动画，必须同时把它挂进 `_edge_clear` 的 11 点扫描。**
`tests/test_t04f_emphasis.py` 里那条测试（`t = 0, 0.1, …, 1.0`，断言 ROI 四边 1 px alpha 全为 0）搬到动画自己的测试文件里，改成**遍历 `ANIMS` 全表**：任何注册了的动画，只要没被这条扫描覆盖，测试就必须红。
同时 `FxSpec.max_transform_bounds()` 必须为该动画返回正确的四向外扩量。T04f-3 那个"版式按静态尺寸算、光栅器却在动态放大"的裁剪 bug，在这一片有 24 次重演的机会，这条是唯一的闸门。

**0.2 `Transform` 的通道必须真的被光栅器消费，不许只返回不生效。**
目前 `clip` / `clip_dir` / `blur_sigma_em` / `fill_progress` / `extra_color` / `tracking_em` / `rot_deg` 里有几条大概率还没有接线。每交付一个用到某通道的动画，**必须附一条证明该通道确实改变了像素的测试**（同一 `t` 下改该通道的值 → 渲染 sha256 不同）。
理由：本项目出过"宽度溢出的 clip 分支只设了个警告字符串、根本没截断"这种事，验收要看像素，不看代码里有没有那个字段。

**0.3 规则 A 与静态缓存。**
`anim.in_name / loop / out_name` 全为 `none` 时，渲染必须与本片之前**逐字节相同**；`golden --all --repeat 3` PASS 且**不重录**。
`loop` 类动画（`float_loop` / `scroll`）会让 `is_static_hold` 永远为假、每帧重栅——这是这一片最大的性能风险，见各片的 bench 门槛。

## 1. T04f-4a：纯 Transform 类（9 个新动画）

只用 alpha / scale / dx,dy / rot，**不需要动光栅器**，所以先做这一批把 §0.1 的机制和测试骨架立起来。

`float_in_down`、`scale_in`、`drop_in`、`pop_in`、`impact_pop`、`stomp`、`slide`、`zoom_slow`、`float_loop`

- `slide` 需要方向参数（左/右/上/下）。不要为它单开字段——用 `anim.in_name` 的 `"slide+left"` 后缀形式，`_anim_id()` 已经在按 `split("+")` 处理了，把参数解析补上。
- `impact_pop` / `stomp` 是过冲回弹，`max_transform_bounds()` 要按**过冲峰值**算，不是按终值。这是最容易漏的一个。
- `zoom_slow` / `float_loop` 是 hold 期的循环动画：幅度必须小（`zoom_slow` 全程缩放差 ≤ 3%，`float_loop` 位移 ≤ 0.15 em），否则长句字幕会一直在动，观感廉价。
- `OUT_MIRROR` 集合把新增的可镜像项补齐。
- 面板：`anim.in_name / loop / out_name` 三个下拉列出已实现项；未实现项**不要出现在下拉里**（不是灰掉，是不列出），避免用户选了没反应。

**验收**：§0.1 全表扫描（本片覆盖 12 个已实现项）；§0.3；三画幅行数硬约束仍全过；bench 三遍 `dropped=0`，`float_loop` / `zoom_slow` 开启时播放 p50 增量 ≤ 1.5 ms；contact-sheet 每个动画三时刻（t=0.0 / 0.5 / 1.0）一格。标签 `T04f-4a-done`，停下等审。

## 2. T04f-4b：裁切与模糊类（5 个）

`reveal_wipe`、`typewriter`、`line_draw`、`scroll`、`reveal_zoom_blur`

- 前四个消费 `clip` + `clip_dir`，第五个消费 `blur_sigma_em`。**先按 §0.2 证明这两条通道接线**，再谈动画本身。
- `typewriter` 按**词**推进不是按字母（项目不做 CJK，英文按词更自然，也避免逐字母重排）。单位用 `anim.unit="word"`。
- `line_draw` 是描边逐段画出：需要 `QPainterPath` 的 `pointAtPercent` / 分段 stroke。**只在 `stroke.width_em > 0` 时有效**，否则等同 `none`，并在面板上注明。这条同时也是 T04f-3 里 `emphasis.anim="line_draw"` 的实现，做完把那边的"选了不生效"注记撤掉。
- `reveal_zoom_blur` 每帧一次高斯模糊：**只允许 `cv2.GaussianBlur`**（与 T04f-0 同一条纪律，不许 box blur、不许 `QGraphicsBlurEffect`）。σ 随 u 衰减到 0，衰减到 ≤ 0.3 px 时直接跳过模糊调用。
- `scroll` 是 loop 类，**必须给出滚动区域的裁切框**，不能让文字滚出背景框外还在画。

**验收**：§0.1 全表扫描（累计 17 项）；§0.2 两条通道各一条像素证明；§0.3；bench 三遍 `dropped=0`，`reveal_zoom_blur` 入场期 p50 ≤ 10 ms、`scroll` 循环期 p50 增量 ≤ 2 ms；contact-sheet 三时刻。标签 `T04f-4b-done`，停下等审。

## 3. T04f-4c：立体与逐词着色类（10 个）+ 演示片

**立体感**：`flip_clock`、`rotate_flip`、`stack`
- 不引入真 3D。用 `scale_y` 压缩 + 明暗渐变伪造翻转，翻到背面时换成暗色。`stack` 是多行依次落位。
- 这三个的 `max_transform_bounds()` 要算上翻转中途的最大外扩。

**逐词着色**：`karaoke`、`highlight`、`box_highlight`、`colour_highlight`、`rotate_highlight`、`outline_to_fill`、`tracking_in`
- 消费 `fill_progress` / `extra_color` / `tracking_em`，同样按 §0.2 各出一条像素证明。
- `tracking_em` 会改变行宽 → **必须进断行度量**，并且 `max_transform_bounds()` 之外还要保证三画幅行数硬约束不破。这是本片最危险的一条，`tracking_in` 单独出一组三画幅测试。
- **与 `emphasis` 的优先级必须写死并测试**：同一个词既被 `emphasis` 命中、又处在逐词着色动画的高亮位置时，谁的颜色赢？定为 **`emphasis` 赢**（用户显式指定的强调，优先于自动的卡拉OK扫光），高亮的其余表现（底色块、边框）仍照常画。出一条测试锁住。
- `box_highlight` 的底色块要复用 `bg` 那套圆角与四边渐隐代码，不要新写一份。

**演示片**：`tools/fx_demo_reel.py`，把全部已实现动画串成一条可播放的 mp4（每个动画 2 秒，带名称字幕），供 Luca 一次性过目。

**验收**：§0.1 全表扫描（全部 27 项，含 `none`）；§0.2 三条通道像素证明；§0.3；`tracking_in` 三画幅行数测试；emphasis 优先级测试；bench 三遍 `dropped=0`，最重的动画播放 p50 ≤ 10 ms；contact-sheet + demo reel。标签 `T04f-4-done`，停下等审。

## 4. 不要做

- 不新增依赖；不做 CJK / RTL；不碰解码与导出管线；不动 `feather`（T04f-6 欠账）。
- 不重录任何 golden 基线。**若 g01–g07/g09 任何一条不再逐字节一致，立刻停下报告，不许自己重录。**
- 不做贴纸与平台安全区（T04f-5）。
- 不因为动画多就放宽 `_edge_clear`——发现某个动画过不了，是改那个动画或改它的 bounds，不是放宽测试。

## 5. 一句话

这一片的成败不在于动画好不好看，在于 24 个动画有没有一个漏出 ROI、有没有一个把行数顶破。机制在 T04f-3 已经建好了，这一片是把它用满。
