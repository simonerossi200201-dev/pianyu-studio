# T04f 附录 G — 强调词、自动高亮、散排（T04f-3）

> 前置：T04f-2 / T04f-2b 已收口。schema 里 `emphasis` / `scatter` 两组字段和面板控件在 T04f-1d2 就建好了，但渲染侧还没有真正消费它们。本片是把它们接通。
> 涉及字段（已存在，不要改名）：`emphasis.auto / words / color / palette / scale / font / anim`、`scatter.enabled / size_jitter / pos_jitter_em / rot_jitter_deg`。

## 0. 先说这一片最容易做错的地方

**强调会改变文字的度量，所以必须在断行之前就定下来。**

`emphasis.scale`（默认 1.25）把某个词放大，`emphasis.font` 可能换成另一套字体——两者都会改变该词的像素宽度。如果先按正常字号断行、再把词放大，那么：

- 行会超宽；
- Luca 那条**绝对硬约束**（"设置成一行就必须是一行，不可以偶然出现两行"）会被破坏。

所以顺序必须是：**判定强调词 → 用强调后的真实字体和字号测量每个词 → 断行 → `fit_lines_to_width` 收尾**。`_pixel_wrap_lines` / `_fit_event_lines` / `fit_lines_to_width` 拿到的宽度必须已经是放大后的宽度。

验收里有一条专门测这个，见 §5.2。

## 1. 强调词的判定

### 1.1 判定顺序

1. `emphasis.words`（用户显式列出的词）——**最高优先级，永远命中**，不受任何规则和上限限制，大小写不敏感，整词匹配（不匹配子串）。
2. `emphasis.auto` 里勾选的规则，按 §1.2 各自产出候选。
3. 去重，按 §1.4 截断。

### 1.2 五条 auto 规则

| 规则 | 命中什么 | 注意 |
|---|---|---|
| `numbers` | 含阿拉伯数字的词，连同紧贴的符号与单位一起算作一个强调单元：`50%`、`$20`、`3.5x`、`12:30`、`2026`、`1,200` | 不要把 `50` 和 `%` 拆成两半染色 |
| `caps` | 全大写且长度 ≥ 2 的词（`NASA`、`API`、`OK`） | **必须在 `letter_case` 变换之前判定**。否则 `letter_case="upper"` 会让整句全大写，规则命中全部词 |
| `glossary` | 命中项目术语表（T12d 已有的那份）的词 | 复用现成的术语表，不要新建一份 |
| `longest` | 本条字幕里最长的**实词** | 长度相同取最先出现的那个；停用词永不入选，见 §1.3 |
| `list` | 枚举/序号标记：`1.`、`2)`、`First`、`Second`、`Third`、`Finally` | 只认行首位置的 |

### 1.3 停用词表（这条是从真实 bug 来的）

之前真机上出现过把 `at` 高亮的情况。**功能词永远不能被 auto 规则选中**（`emphasis.words` 显式指定的除外）。

建一份英文停用词表放 `cutora/subtitle/stopwords.py`，至少覆盖：冠词、介词、连词、系动词、助动词、代词（a an the / in on at to of for from with by / and or but so / is are was were be been being am / do does did / will would can could should may might must / i you he she it we they this that these those）。

- 项目**不面向中日韩**，只做英文停用词，不要引入任何 CJK 分词。
- 表放在代码里，不要下载、不要新依赖。

### 1.4 数量上限

一条字幕里全是高亮等于没有高亮。新增字段：

```
emphasis.max_per_cue : int, 0–8, 默认 2      # 0 = 不限制
```

- `emphasis.words` 命中的**不占**这个额度。
- auto 规则命中超额时，按规则优先级 `words > numbers > glossary > list > caps > longest` 取前 N 个；同级按在句中出现的先后。
- 面板加一个数字框，进注册表。

### 1.5 确定性

判定结果必须**只由文本和 spec 决定**，同样输入永远同样输出。任何需要随机的地方（例如调色板轮转的起点）一律用 `anim.stable_seed`（blake2s），**不许用 Python `hash()`**，理由和 T04f-0 一样：`PYTHONHASHSEED` 会打散它，预览和导出会不一致。

## 2. 强调词怎么画

- `emphasis.color`：单色。所有强调词同一个颜色。
- `emphasis.palette`（1–8 色，非空时**优先于** `color`）：**双色/多色跳字**。按强调词在本条字幕里出现的次序依次取色，取完循环。这就是 Luca 要的"两种颜色的词交替蹦"。
- `emphasis.scale`（0.8–2.0）：字号倍率，见 §0。
- `emphasis.font`：`body` 或 `emphasis`，取 `fonts.emphasis` 那套字体。见 §0。
- `emphasis.anim`：`none` / `line_draw` / `pop`。本片**只实现 `pop`**（强调词在其词级时间点做一次轻微放大回弹）。`line_draw` 属于动画库，留 T04f-4，本片保持"选了不报错、不生效"，并在面板上标注。
- 强调词继承本风格的描边、发光、投影；**不要**给强调词单独加发光——Luca 明确不要外发光风格。

**基线锁**：`emphasis.auto` 为空、`emphasis.words` 为空时，渲染必须与本片之前**逐字节相同**（规则 A）。

## 3. 散排 scatter

`layout.py::_apply_scatter` 已经写了一半（`size_jitter` / `pos_jitter_em` / `rot_jitter_deg` 都在用），本片要做的是：

1. 确认它的随机源是 `stable_seed(seg_id)` 而不是 `random` / `hash()`——同一条字幕每次渲染必须抖到同一个位置，否则预览和导出对不上，golden 也没法录。
2. `size_jitter` 同样会改变词宽 → 和 §0 一样，必须在断行之前生效。
3. 抖动之后的包围盒必须仍在画面内、仍满足行数约束。溢出时**先收 jitter 幅度**，不要直接让它出界。
4. 面板控件接通（开关 + 三个滑杆），进注册表。
5. `scatter.enabled=False` 时逐字节等于本片之前。

## 4. 不要做

- 不碰动画库（`anim.py` 的 26 项留 T04f-4）。
- 不碰任何解码 / 导出管线。
- 不引入新依赖，不下载任何词表或模型。
- 不做 CJK、不做 RTL。
- 不动 `feather`（T04f-6 欠账）。
- 不重录任何 golden 基线。**若发现 g01–g07/g09 有任何一条不再逐字节一致，立刻停下报告我，不许自己重录。**

## 5. 验收

### 5.1 判定正确性（纯函数测试，不需要渲染）

给定一组句子和 spec，断言选中的词集合完全等于预期。至少覆盖：

- `"We cut costs at the office by 30% last year"` + `auto=["numbers"]` → 只有 `30%`；**`at` / `the` / `by` 一个都不许中**。
- 同句 + `auto=["longest"]` → `office`（不是 `costs`，不是任何停用词）。
- `letter_case="upper"` + `auto=["caps"]` + 原文没有全大写词 → **命中 0 个**（证明是在变换前判定的）。
- `words=["at"]` → `at` 命中（显式指定压过停用词表）。
- `max_per_cue=2` 且候选有 5 个 → 恰好 2 个，且是优先级最高的两个。
- 同一输入连跑两次（不同进程，`PYTHONHASHSEED` 不同）→ 结果相同。

### 5.2 行数硬约束不许被破坏（这条最重要）

`max_lines=1`、`emphasis.scale=2.0`、`emphasis.font` 用一套明显更宽的字体、句子长到刚好卡在边界上——在 **1080×1920 / 1920×1080 / 1080×1080 三种画布**上各断言：**渲染出来恰好 1 行**，且不超出安全边距。`scatter.enabled=True` 再跑一遍同样的断言。

### 5.3 视觉

`tools/fx_contact_sheet.py` 出一张，每格 ≥960×540，覆盖：单色强调 / 双色 palette 跳字 / 放大强调 / 双字体强调 / pop 动画的三个时刻 / scatter 开启。每格两个底（真实帧 + `#141A26`），并出 4× 放大裁切。

### 5.4 其余

- 规则 A：`emphasis` 与 `scatter` 缺省时逐字节不变；`golden --all --repeat 3` PASS。
- 玻璃像素锁 a/b/c 不变。
- 面板：所有新字段（含 `emphasis.max_per_cue`）进注册表，漏接控件测试必须红。
- 命令走总线、可撤销、headless。
- bench 三遍 `dropped=0`；开启强调 + scatter 后播放 p50 增量 ≤ 1 ms。
- pytest 0 failed；无新增依赖。
- 标签 `T04f-3-done`，报告 `docs/plan/reports/T04f-3_report.md`，停下等审。**不要开 T04f-4。**
