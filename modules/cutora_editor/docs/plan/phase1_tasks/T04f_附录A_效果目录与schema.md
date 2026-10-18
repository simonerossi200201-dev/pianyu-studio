# T04f 附录 A — 效果目录与 `fx` schema（与总任务书一起贴给 Grok）

> 与 `T04f_字幕风格系统与动画库.md` 冲突时以本附录为准。所有长度单位 **em**（= 当前字号像素），时间单位秒，颜色 `#RRGGBB`，透明度 0–1。所有"随机"都用 `random.Random(seed_of(segment.id, unit_index, fx.anim.seed))`。

---

## 1. `style.fx` 完整 schema（`spec.py` 里用 dataclass 表达，`from_style()` 负责默认值与校验）

| 路径 | 类型 / 取值 | 默认 | 说明 |
|---|---|---|---|
| `fill.type` | `solid` / `gradient` / `metal` | `solid` | 字面填充 |
| `fill.colors` | 1–3 个颜色 | `["#F4EBD8"]` | gradient 用 2–3 个；metal 时忽略，改看 `fill.metal` |
| `fill.angle` | 0–360 | 90 | 渐变方向（90 = 上→下，0 = 左→右） |
| `fill.metal` | `gold` / `silver` / `chrome` / `rose` | `gold` | 预设色带见 §2.3 |
| `stroke.width_em` | 0–0.3 | 0 | 0 = 无描边 |
| `stroke.color` | 颜色 | `#141A26` | **不许纯黑** |
| `stroke.join` | `round` / `miter` | `round` | |
| `glow.sigma_em` | 0–0.6 | 0 | 0 = 无发光 |
| `glow.spread_em` | 0–0.2 | 0.02 | 模糊前的膨胀量 |
| `glow.color` | 颜色 | `#22D3EE` | |
| `glow.strength` | 0–1.5 | 0.8 | alpha 乘数，>1 允许饱和 |
| `glow.mode` | `soft` / `neon` | `soft` | neon = 双层（§2.4） |
| `shadow.sigma_em` | 0–0.4 | 0 | 0 = 无投影 |
| `shadow.dx_em` / `dy_em` | -0.3–0.3 | 0.04 / 0.06 | |
| `shadow.color` | 颜色 | `#141A26` | **不许纯黑** |
| `shadow.alpha` | 0–1 | 0.6 | |
| `extrude.depth_em` | 0–0.4 | 0 | 0 = 无 3D |
| `extrude.angle` | 0–360 | 135 | 挤出方向（135 = 右下） |
| `extrude.color` | 颜色 | 自动 = 字面色变暗 45% | 显式给时**不许纯黑** |
| `bg.type` | `none` / `box` / `glass` / `texture` / `bubble` | `none` | box = 整段一个框；bubble = 每行一个框 |
| `bg.color` | 颜色 | `#0B1A33` | **不许纯黑** |
| `bg.alpha` | 0–1 | 0.55 | glass 时是叠色透明度 |
| `bg.radius_em` | 0–1 | 0.4 | 圆角 |
| `bg.pad_em` | `[x, y]` | `[0.5, 0.25]` | 内边距 |
| `bg.glass_sigma_em` | 0.2–1.5 | 0.7 | 毛玻璃模糊量 |
| `bg.glass_border` | bool | true | 1 px 渐变玻璃边（顶亮 α0.55 → 底暗 α0.15，颜色 = 白） |
| `bg.texture` | `paper` / `linen` / `grid` | `paper` | `bg.type == texture` 时 |
| `bg.texture_tint` | 颜色 | `#F1E9D8` | 纹理着色 |
| `bg.align` | `center` / `left` / `right` / `alternate` | `center` | bubble 的行对齐；alternate = 奇数行左、偶数行右（聊天气泡） |
| `bg.line_gap_em` | 0–1 | 0.18 | bubble 行间距 |
| `fonts.body` | 打包字体名 | 沿用 `font_family` | 叙述体 |
| `fonts.emphasis` | 打包字体名 | 同 body | 强调词字体（手写体等） |
| `emphasis.auto` | 数组，取自 `numbers` `caps` `glossary` `longest` `list` | `[]` | 自动强调规则；`list` 读 `emphasis.words`。**永远跳过虚词**（冠词 a/an/the、介词、连词、系动词）。虚词表 `resources/data/stopwords_<lang>.json`，先做 `en` / `it`（T04f-3 实现，本片只定规则） |
| `emphasis.words` | 字符串数组 | `[]` | 用户词表（不区分大小写、NFKC 归一后比较，复用 T12e 的归一函数） |
| `emphasis.color` | 颜色 | `#22D3EE` | |
| `emphasis.palette` | 颜色数组 | `[]` | 非空时强调词轮流取色（双词紫青：`["#7C3AED", "#22D3EE"]`） |
| `emphasis.scale` | 0.8–2.0 | 1.25 | |
| `emphasis.font` | `body` / `emphasis` | `emphasis` | |
| `emphasis.anim` | `none` / `line_draw` / `pop` | `none` | 强调词自己的小动画 |
| `anim.in` | §3 表中 phase 含 `in` 的 id | `none` | |
| `anim.loop` | §3 表中 phase 含 `loop` 的 id | `none` | 停留期间持续/按词触发 |
| `anim.out` | §3 表中 phase 含 `out` 的 id | `none` | |
| `anim.unit` | `segment` / `line` / `word` / `char` | `segment` | 入场/出场的动画单元 |
| `anim.stagger_s` | 0–0.3 | 0.05 | 单元间错峰 |
| `anim.in_s` / `out_s` | 0.05–1.5 | 0.35 / 0.25 | 单元时长；总时长 = in_s + stagger × (n−1)，超过段长一半时自动等比压缩 |
| `anim.easing` | `anim_engine.EASING_MAP` 的键 | `ease_out` | |
| `anim.seed` | int | 0 | 随机效果的种子 |
| `scatter.enabled` | bool | false | 单词随机大小/位置（弹出词） |
| `scatter.size_jitter` | 0–0.6 | 0.35 | 字号 ×(1 ± jitter) |
| `scatter.pos_jitter_em` | `[x, y]` | `[1.5, 0.8]` | 相对基准位置的随机偏移上限 |
| `scatter.rot_jitter_deg` | 0–20 | 6 | 随机小旋转 |
| `tracking_em` | -0.05–0.5 | 0 | 字间距（静态） |
| `letter_case` | `none` / `upper` | `none` | 海报级大写 |

校验规则：超范围 → 钳制并 `logger.warning` 一次；未知键 → 忽略并 warning 一次；颜色非法 → 默认值；**纯黑（三通道 ≤ 0x10）** → 内置包测试失败、用户包画廊角标。

---

## 2. 静态效果算法与质量标准（`raster.py`）

通用：文字先用 `QPainterPath.addText` 生成路径（每个 WordRun 一条，字体/字号/字距各自独立），所有光栅在 **ROI ARGB32_Premultiplied** 上进行，ROI = 所有路径包围盒 ∪ 各效果外扩（glow: 3σ+spread；shadow: 3σ+|d|；extrude: depth；bg: pad + radius）。

### 2.1 fill
- `solid`：直接 `fillPath`。
- `gradient`：`QLinearGradient` 沿 `angle` 穿过路径包围盒，2–3 个 stop 等距；**先在 float32 numpy 上按 1024 级采样色带再交给 Qt**，避免 8-bit 直接插值出现色带。
- `metal`（§2.3）。

### 2.2 stroke
`QPen(color, width_px, RoundJoin/RoundCap)` 描 path，画在 fill **之下**（描边不吃掉字面）。width_px = width_em × fontsize_px。

### 2.3 metal 预设（色带 stop：位置 → 颜色）
- gold：0 `#7A5A12` · 0.30 `#F5D77A` · 0.48 `#FFF2C2` · 0.55 `#D9B24C` · 0.80 `#8C6A1C` · 1.0 `#F0CC6A`
- silver：0 `#5C6470` · 0.30 `#E9EDF2` · 0.48 `#FFFFFF` · 0.55 `#C5CBD3` · 0.80 `#6E7681` · 1.0 `#DDE2E8`
- chrome：0 `#1E2A3A` · 0.35 `#C9D6E4` · 0.50 `#FFFFFF` · 0.52 `#3A4656` · 0.80 `#8FA0B4` · 1.0 `#E8EEF5`
- rose：0 `#7A3B3B` · 0.30 `#F4C2B8` · 0.48 `#FFF0EA` · 0.55 `#D89A8C` · 0.80 `#8C4E44` · 1.0 `#F1BCAF`
角度默认 100°（略斜）。叠一条**高光带**：白色 α 0.35 的细线性渐变，位置 40–48%（这个位置拿不准就在报告里问）。深色 stop 已经都不是纯黑，**不许改成纯黑**。

### 2.4 glow / neon（唯一允许的实现：cv2.GaussianBlur）
```
mask  = alpha channel of text ROI (uint8)
mask  = cv2.dilate(mask, ellipse(spread_px))          # spread_em > 0 时
blur  = cv2.GaussianBlur(mask.astype(float32), (0,0), sigma_px)
glowA = clip(blur * strength, 0, 1)
layer = premultiply(glow.color, glowA)                # 着色
```
- `soft`：一层，画在 shadow 之上、stroke 之下，`SourceOver`。
- `neon`：两层：核心 σ = 0.35×sigma_em、颜色 = glow.color 与白混 60%、strength 1.0；光晕 σ = sigma_em、颜色 = glow.color、strength 0.9；两层都用 `CompositionMode_Screen`。字面填充色自动 = glow.color 与白混 80%（除非 fill 显式给了）。
- **质量标准**：放大 400% 看不到台阶/圈层；σ 变化时亮度平滑；strength 到 1.5 时中心饱和但边缘仍是渐变。

### 2.5 shadow
同 §2.4 一层，σ = shadow.sigma_em，偏移 (dx, dy)，alpha 乘 shadow.alpha，画在最底层（bg 之上）。

### 2.6 extrude（3D 厚字）
`depth_px = depth_em × fontsize_px`；沿 `angle` 方向，从 depth_px 到 1 每 1 px 一层 `fillPath(offset)`，颜色 = extrude.color（默认字面色 HSL 亮度 ×0.55，饱和度 ×1.1）；最远层再暗 15% 做渐变。字面最后画在最上。**不做模糊**，侧面必须锐利连续。

### 2.7 bg
- `box`：整段包围盒 + pad，`drawRoundedRect`，颜色 α。
- `bubble`：每行一个包围盒 + pad，各自圆角；`align` 决定每行在段宽内的水平位置（bubble 的文字对齐跟随 align）；`alternate` 奇数行靠左偶数行靠右。行间距 `line_gap_em`。
- `glass`：见总任务书 §1.3；backdrop 缺失退化为 box。玻璃边：先画 ROI 圆角 path 的 1 px 描边，颜色白，alpha 沿垂直方向 0.55 → 0.15 的线性渐变。
- `texture`：从 `resources/textures/<name>.png`（灰度+alpha，无缝）平铺填充圆角框，叠 `texture_tint`；`paper` 类型边缘用**程序生成的撕边**（沿边框 path 加 seeded 噪声位移 0.05–0.12 em，再用该 path 裁切）。

### 2.8 纹理生成（`tools/gen_textures.py`，numpy，固定 seed，入库）
- `paper`：多倍频 value-noise（4 层，幅度 1/2 递减）+ 轻微颗粒；alpha 全 1。
- `linen`：正交两组正弦条纹 + 噪声调制，周期 6 px。
- `grid`：1 px 网格线（间距 32 px）α 0.18 + 极淡纸底。
输出 1024×1024，`np.roll` 验证无缝（边缘差 < 2/255）。

---

## 3. 动画注册表（`anim.py`：`ANIMS[id] = AnimDef(label_zh, phases, units, fn)`）

`fn(u, ctx) -> Transform`，其中 `u ∈ [0,1]` 是该单元在该 phase 的进度（已过 easing），`ctx` 含单元索引/总数/随机器/词时间/字号。`Transform` = {alpha, scale_x, scale_y, dx_em, dy_em, rot_deg, clip(0–1, 方向), blur_sigma_em, tracking_em, fill_progress, extra_color}。所有函数**纯**、无状态。

| id | 中文标签 | phase | 单元 | 参考名 | 算法要点 |
|---|---|---|---|---|---|
| `none` | 无 | in/out/loop | – | None | 恒等 |
| `fade` | 淡入淡出 | in/out | any | – | alpha = u |
| `float_in_up` | 上浮入 | in/out | any | Float In ↑ | dy = (1−u)×0.6 em，alpha = u |
| `float_in_down` | 下浮入 | in/out | any | Float In ↓ | dy = −(1−u)×0.6 em，alpha = u |
| `scale_in` | 缩放入 | in/out | any | Scale In | scale = 0.6 + 0.4u，alpha = u |
| `drop_in` | 落下 | in/out | word/line | Drop In | dy = −(1−u)×2 em，easing 强制 `bounce`，落地瞬间 scale_y 0.92→1（挤压 80 ms） |
| `pop_in` | 弹出 | in/out | any | Impact | scale 走 `compute_pop_scale`（过冲 1.15），alpha 前 30% 淡入 |
| `impact_pop` | 冲击弹出 | in | word | Impact Pop | 同 pop_in + 过冲 1.3 + rot = ±4°×(1−u)（符号由随机器定）+ 单元错峰 |
| `stomp` | 重踏 | in | word/line | Stomp | scale 1.7→1，easing `ease_in_expo`，落定后 120 ms 内 dx/dy 做 0.06 em 衰减抖动 |
| `reveal_wipe` | 擦除揭示 | in/out | segment/line | Reveal | clip 从左到右（out 时从右到左），alpha 1 |
| `reveal_zoom_blur` | 大→正常·模糊→清晰 | in | segment | – | scale 1.35→1，blur_sigma 0.5 em→0，alpha 0.4→1 |
| `flip_clock` | 逐字翻牌 | in | char | Flip Clock | scale_y：−1→1（u<0.5 画背面 = 字面色暗 40%，u≥0.5 画正面），单元错峰 0.04 s |
| `rotate_flip` | 旋转翻转 | in | word | Rotate & Flip | rot −14°→0，scale_x −1→1（前半背面暗色），alpha = u |
| `stack` | 堆叠 | in | line | Stack | 每行从下方 1.2 em 上推到位，前一行到位后下一行才开始（stagger = in_s），已到位的行整体再上移一行高 |
| `tracking_in` | 字距收拢 | in | segment/line | – | tracking 0.35 em→0（可与 scale_in / reveal_zoom_blur / fade 叠加：`anim.in` 允许写成 `"tracking_in+scale_in"`，用 `+` 组合最多 2 个，Transform 逐字段合成） |
| `outline_to_fill` | 描边→填充 | in | any | – | 前 60% 只画 stroke（fill alpha 0），后 40% fill alpha 0→1，stroke 保持 |
| `typewriter` | 打字机 | in | char | – | 已出现字符 alpha 1，未出现 0，无缓动；可选 `cursor` |
| `slide` | 滑入 | in/out | any | – | 方向参数 `anim.dir`（left/right/up/down），位移 1.5 em |
| `karaoke` | 卡拉OK | loop | word | Karaoke | 当前词按 `(t−w.start)/(w.end−w.start)` 从左到右填 `emphasis.color`（clip 方式，非逐字变色） |
| `highlight` | 高亮当前词 | loop | word | Highlight | 当前词 fill = emphasis.color，其余 body 色，切换 80 ms 淡变 |
| `box_highlight` | 框高亮 | loop | word | Box Highlight | 一个圆角框（bg.color / emphasis.color）在词与词之间**平滑移动**（spring_interp），文字色不变或反色 |
| `colour_highlight` | 彩色高亮 | loop | word | Colour Highlight | 当前词 fill = 2–3 色渐变（`emphasis.palette`，方向 `fill.angle`）+ scale 1→1.15→1（bounce，200 ms） |
| `rotate_highlight` | 旋转高亮 | loop | word | Rotate & Highlight | 当前词 rot ±6°（符号交替）+ emphasis.color，120 ms 进出 |
| `line_draw` | 划线强调 | loop | word | – | 强调词下方一条 0.08 em 粗的线从左画到右（`emphasis.color`，端点圆），180 ms |
| `zoom_slow` | 慢速缩放 | loop | segment | – | scale 1.0→1.06 线性贯穿整段（导出与预览一致，因为只依赖 t） |
| `float_loop` | 漂浮 | loop | segment | – | `compute_floating_offset`（现有） |
| `scroll` | 滚动歌词 | loop | segment | – | 委托现有 `draw_lyrics_scroll`；风格包里 `anim.loop="scroll"` 即启用 |

out 阶段允许的 id：`fade` `float_in_up` `float_in_down` `scale_in` `pop_in` `reveal_wipe` `slide` `drop_in`（镜像播放 u→1−u）。参考截图列表 16 项全部覆盖。

**时序规则**（`anim.py::unit_progress`）：
- 单元 k 的入场起点 = `seg.start + k × stagger_s`（`unit=word` 时若词有自己的 `start`，取 `max(seg.start + k×stagger, w.start − in_s)`，让词动画不早于词本身）；
- 出场终点 = `seg.end`，起点 = `seg.end − out_s − (n−1−k) × stagger_s`；
- in 与 out 重叠时（段太短）按比例压缩两者，保证 in 完成后至少停留 `max(0.15, 0.2 × 段长)`；
- loop 类在 `[in 完成, out 开始]` 之间生效，词级 loop 的"当前词" = `t ∈ [w.start, w.end)`，段内没有词时间时退化为按字符数线性分配。

---

## 4. 版式（`layout.py`）

- 输入：`segment.lines`（没有时用 `text` 一行）+ `words` + fx。输出：`LineBox[]`，每个含 `WordRun[]`（文本、字体、字号 px、字距、fill 覆盖、是否强调、词时间、包围盒），每个 WordRun 可按需展开 `GlyphBox[]`（逐字动画时）。
- 基准位置沿用 T04e 的 `anchor + offset_pct`；`fontsize_pct` 决定 body 字号；强调词字号 = body × `emphasis.scale`，行高按行内最大字号取。
- **双字体**：强调词用 `fonts.emphasis`，基线对齐（用各自 `QFontMetrics.ascent` 对齐基线，不对齐顶边）。
- **scatter**：每个词 = 独立单元，字号 ×(1 + jitter × r1)，位置 = 基准行内位置 + `pos_jitter_em × (r2, r3)`，旋转 `rot_jitter_deg × r4`，r 来自 seeded 随机器 ∈ [−1,1]；最后整体做一次 bbox 钳制到画面内（T04e 的三画布测试要过）。
- **bubble / align**：见 §2.7。
- **letter_case=upper**：布局前转大写（只影响渲染，不改数据）。
- 所有 WordRun 的 `QPainterPath` 缓存在 `cache.py`（key 含字体、字号、字距、文本），动画只变 Transform 不重建 path。

---

## 5. 贴纸（T04f-5）

- 数据：`segment.extra["stickers"] = [{"id": "fire", "attach": "word|segment", "word_index": 3, "pos": "above|left|right", "size_em": 1.4, "anim_in": "pop_in", "loop": "wobble|pulse|none"}]`。
- 渲染：在 `textfx.draw` 里作为最后一层；`wobble/pulse` 复用 `compute_dyn_img_wobble / _pulse`；贴纸 PNG 用 `get_cached_image`。
- 命令：`subtitle.sticker.add` / `subtitle.sticker.remove`（headless、undoable，schema 里 `id` 为 enum = `resources/stickers/` 目录内容）。
- Transcript 卡片右键「添加贴纸…」→ 60 格网格选择器；画布上贴纸可拖动微调（写回 `offset_em`）。
- 署名：`关于` 面板与 `NOTICE.md`：`Twemoji graphics © Twitter, Inc and other contributors, CC-BY 4.0`，附 commit sha。

---

## 6. Luca 的 16 张参考截图 → 本任务对应项

| 截图主题 | 对应 |
|---|---|
| 专业风格选择器（卡片只显示字幕条） | T04f-1 §3.1 |
| 单词随机大小/位置弹出 | `scatter` + `impact_pop`，包 `pop-word` |
| 叙述体 + 手写强调（带色） | `fonts.emphasis` + `emphasis.*`，包 `handwrite-duo` |
| 自动高亮重要词（可配置） | `emphasis.auto`，命令 `subtitle.emphasis.auto` |
| 双词紫 + 青弹出 | `emphasis.palette` + `pop_in` unit=word，包 `duo-pop` |
| 金属质感 + 海报级混排 | `fill.metal` + `letter_case` + `tracking_em` + Bebas/Anton |
| 白字 + 毛玻璃圆角 + 玻璃边 | `bg.glass`，包 `glass-white`（T04f-0 就做） |
| 高亮弹跳 2–3 色渐变 | `colour_highlight` |
| 分行独立圆角、左右对齐（聊天） | `bg.bubble` + `align=alternate`，包 `chat-bubbles` |
| 贴纸动画 | T04f-5 |
| 霓虹 + 光带描边（独立效果） | `glow.mode=neon`，包 `neon-cyan` |
| 厚 3D 挤出 | `extrude`，包 `chunky-3d` |
| 滚动字幕 | `anim.loop=scroll`，包 `lyrics-scroll` |
| 擦除揭示 大→正常 模糊→清晰 | `reveal_zoom_blur` |
| 逐字翻牌 / 描边→填充 | `flip_clock` / `outline_to_fill` |
| 纹理不规则背景 + 划线强调 | `bg.texture` + `line_draw`，包 `paper-torn` |
| 慢速持续缩放 / 字距收拢入场 | `zoom_slow` / `tracking_in`（可 `+` 组合） |
| 平台参考层 | T04f-5 §3.4 |
| 动画列表（None … Stomp） | §3 全覆盖 |
