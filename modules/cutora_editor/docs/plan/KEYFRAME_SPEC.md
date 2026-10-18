# 关键帧数据规格（Q31 / T30 第一版）

预览和导出只经过一个取值函数：`cutora.render.keyframe.value_at`。
没有 `keyframes` 的老对象走原来的静态字段，像素与音频字节必须与改前一致。

## 挂载位置

`keyframes` 挂在被动画的对象上，不是时间线根上：

| 对象 | 容器 | `t` 的零点 |
|---|---|---|
| 视频 / 音频 clip | `clip.keyframes` | `clip.start_t` |
| 文本覆层 | `text_overlays[i].keyframes` | `ov["start"]` |
| 图片覆层 | `image_overlays[i].keyframes` | `ov["start"]` |
| 字幕样式 | `task.style.keyframes`（角色样式同） | 当前这条字幕 `event.start` |

`t` 是**相对该对象起点的秒数**。把 clip 在时间线上右移 5 秒，关键帧跟着走，动画相对 clip 起点不变。

## JSON 形状

```json
"keyframes": {
  "x": [
    {"t": 0.0, "v": 0.0, "ease": "linear"},
    {"t": 1.0, "v": 240.0, "ease": "ease_in_out"}
  ],
  "fill.colors[0]": [
    {"t": 0.0, "v": "#FF3300", "ease": "linear"},
    {"t": 0.8, "v": "#2244FF", "ease": "bezier", "bez": [0.32, 0.0, 0.67, 1.0]}
  ]
}
```

- 每个参数路径一条数组，按 `t` 升序存储。
- `ease` 落在这一段的**左端**关键帧上，控制从本点插到下一点。最后一点的 `ease` 无意义。
- `bez` 只在 `ease=bezier` 时出现，四个 0–1 的数：`[x1, y1, x2, y2]`，与 CSS `cubic-bezier` 相同（P0=(0,0) P3=(1,1)）。
- 缺 `keyframes`、空字典、该路径没有点：全部等于「没用过关键帧」，返回 `fallback`。

## 字段

| 字段 | 类型 | 规则 |
|---|---|---|
| `t` | number | ≥ 0，相对对象起点的秒 |
| `v` | number 或 `#RRGGBB` / `#RRGGBBAA` | 与该路径静态字段同单位 |
| `ease` | string | `linear` / `ease_in` / `ease_out` / `ease_in_out` / `hold` / `bezier` |
| `bez` | `[x1,y1,x2,y2]` | 仅 `ease=bezier` |

第一版 UI 只提供菱形 + 六种缓动下拉。贝塞尔手柄编辑不在本任务。

## 参数路径（第一批）

路径写法跟已有 fx 路径一致。clip 顶层字段没有 `fx.` 前缀。

**clip（视频）**

| 路径 | 单位 | 静态字段 |
|---|---|---|
| `x` | 画布像素 | `clip.x` |
| `y` | 画布像素 | `clip.y` |
| `scale` | 倍率，1 = 原大 | `clip.scale` |
| `rotation` | 度，顺时针 | `clip.rotation` |
| `opacity` | 0–255 | `clip.opacity`（缺省 255） |

**clip（音频）**

| 路径 | 单位 | 静态字段 |
|---|---|---|
| `gain_db` | dB，0 = 原音量 | `clip.gain_db` |

**文本覆层 / 字幕样式**

| 路径 | 单位 | 静态字段 |
|---|---|---|
| `opacity` | 0–255 | `ov["opacity"]` |
| `scale` | 倍率 | `ov["scale"]` |
| `tracking_em` | em | `fx.tracking_em` |
| `fill.colors[0]` | `#RRGGBB` | `fx.fill.colors[0]` |
| `shadow.angle_deg` | 度 | `fx.shadow.angle_deg` |
| `shadow.distance_em` | em | `fx.shadow.distance_em` |

`fill.colors[0]` 读不到顶层时，实现会再试 `fx.fill.colors[0]`（覆层和 style 都把 fx 放在 `fx` 子树里）。

## 取值

```
value_at(obj, path, t_local, fallback) -> 与 fallback 同类型的值
```

1. 该路径没有点 → `fallback`（老工程走这里）。
2. 一个点 → 常量，等于该点的 `v`。
3. `t_local` 在第一点之前 → 第一点的 `v`；在最后一点之后 → 最后一点的 `v`。
4. 落在两点之间：用**左端**的 `ease` 把 `u = (t - t0) / (t1 - t0)` 映到 `[0,1]`，再插值。
   - 数字：`v0 + (v1 - v0) * eased_u`
   - `hold`：`eased_u` 在到达下一点之前都是 0，所以中点等于 `v0`，不是平均。
   - 颜色：先转到 OKLab，再按 OKLCH（L、C、最短弧 hue）插，最后回 sRGB。禁止在 sRGB 里线性插。

缓动函数复用 `cutora.render.anim_engine.EASING_MAP` 的 `linear` / `ease_in` / `ease_out` / `ease_in_out`（三次）。`bezier` 是 CSS cubic-bezier。`hold` 是阶跃。

## 命令（全部 `undoable=True`，锁定轨拒绝）

| 命令 | 作用 |
|---|---|
| `keyframe.set` | 在 `t` 打/改一个点（同 t 则覆盖） |
| `keyframe.delete` | 删一个点；该路径空了就摘掉路径 |
| `keyframe.move` | 改一个点的 `t` |
| `keyframe.set_interp` | 改左端 `ease`（及可选 `bez`） |
| `keyframe.enable` | 打开路径：用当前值在播放头打第一个点；关掉：删掉该路径全部点 |
| `keyframe.copy` / `keyframe.paste` | 复制当前点 / 粘贴到播放头 |

属性面板改值时，若该路径已经启用关键帧，**必须**走 `keyframe.set`，不许直接写 `clip.x = …`。Ctrl+Z 靠命令总线的工程快照，不另做差分。

## 例子

视频 clip 从左侧滑到中央，1 秒，渐入渐出：

```json
{
  "source_id": "…",
  "in_t": 0.0,
  "out_t": 4.0,
  "start_t": 8.0,
  "x": 0.0,
  "keyframes": {
    "x": [
      {"t": 0.0, "v": -480.0, "ease": "ease_in_out"},
      {"t": 1.0, "v": 0.0, "ease": "linear"}
    ]
  }
}
```

把这条 clip 的 `start_t` 改成 `13.0`，`t_local=0.5` 时 `x` 仍是 `ease_in_out(0.5)` 映射后的值，与 `start_t=8` 时相同。时间线绝对时刻 `13.5` 对得上原来的 `8.5`。

字幕字色红 → 蓝（中点走品红，不是灰）：

```json
"keyframes": {
  "fill.colors[0]": [
    {"t": 0.0, "v": "#FF0000", "ease": "linear"},
    {"t": 1.0, "v": "#0000FF", "ease": "linear"}
  ]
}
```

## 本版不做

- 曲线编辑器、贝塞尔手柄拖动（下一条任务）。
- clip 裁剪 / 模糊 / 调色参数（T31）。
- 轨道级 `track.props` 关键帧。
