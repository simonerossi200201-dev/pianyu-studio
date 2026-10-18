# 69 · 设置页：每个参数都用人话中文，别再露英文键名

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `orante/config.py`（`DEFAULTS` 全表）、
> `ui/js/parts/settings.js`（第 8 行 `groups`、第 398~445 行渲染循环，尤其第 437 行那个
> `<label>{{ k.split('.').slice(1).join('.') }}<small>{{ k }}</small></label>`）、
> `orante/i18n/zh.json`（`set.group.*`）、`specs/64-share-package.md`（分发的目的），再读本规格。
> **先做完 68。** 改前备份 `*.bak-0916labels`。报告写到 `specs/reports/69-settings-chinese-labels.md`。中文注释。

## 问题

设置页现在长这样：

    dup_dist
    expand.dup_dist                        [ 40 ]

    no_frontal
    expand.no_frontal                      [☐]

标签就是键名去掉前缀，下面再来一遍完整键名。自己人知道 `dup_dist` 是什么，
拿到分发包的人看着一屏英文完全不知道这些数字管什么。软件要发出去了，这页必须让人一眼看懂。

## 要做的

### A. 每个键三样东西：中文名、一句话说明、合适的控件

`zh.json` 加两组键：`set.key.<完整键名>`（中文名）和 `set.hint.<完整键名>`（一句话说明，说清
**调大/调小会怎样**，不是把中文名再说一遍）。渲染改成：

    重复图判定距离                                  [ 40 ]
    两张图的相似距离小于这个数就当成同一张丢掉；调小 = 更容易留下相似的图，调大 = 更严
    expand.dup_dist  ← 灰色、小号、只在鼠标悬停整行时显示（title 属性也放一份）

英文键名不许作为主标签出现。**`DEFAULTS` 里每一个键都必须有 `set.key` 和 `set.hint`**，
写一个测试遍历 `DEFAULTS` 逐个断言，缺一个就红 —— 以后谁加新设置忘了写中文，测试替用户挡住。

### B. 控件按含义选，不是全塞文本框

| 类型 | 控件 |
|---|---|
| 布尔 | 开关/复选框（现状保留） |
| 有限几个取值的字符串 | **下拉**，选项也是中文（见 C 表里标「下拉」的） |
| 数字 | 数字框，标签后面带单位或范围，比如「（秒）」「（0~100）」 |
| 路径 | 文本框 + 「选择…」按钮（`workspace.root` 现在就该有） |
| 密钥 | 密码框 + 眼睛（`gemini.api_key` 现状保留） |

### C. 全表（中文名 / 说明 / 控件）——**照这个写，措辞可以润色，意思不能变**

分组标题（`set.group.*`）也换成人话：工作区 / 自动归档 / 拆段 / 多模态接口 / Gemini / 多镜头扩图 /
配图轨 / 引擎 / 界面 / 生成方式。

**工作区**

| 键 | 中文名 | 说明 | 控件 |
|---|---|---|---|
| `workspace.root` | 成片保存位置 | 生成好的视频、图片、工程文件都放这里 | 路径 |
| `workspace.by_date` | 按日期分文件夹 | 开了就是 位置\日期\版本\任务，关了直接放在位置下 | 开关 |
| `workspace.ask_each_batch` | 每批开跑前问一次存哪 | 关了就一直用上面的位置，不再弹窗 | 开关 |

**自动归档**

| `archive.auto_on` | 自动归档做完的任务 | 完成超过下面天数的任务打包成工程文件并移出列表，列表不会越来越长 | 开关 |
| `archive.auto_days` | 完成多少天后归档 | 单位：天 | 数字 |

**拆段**

| `split.flow.max_chars` | Flow 每段最多字数 | 文案按这个长度切成段，每段一条视频；调大 = 段少、每段更长 | 数字 |
| `split.flow.dur_4s_max_words` | Flow 4 秒档最多词数 | 一段不超过这么多词就按 4 秒生成 | 数字 |
| `split.flow.dur_6s_max_words` | Flow 6 秒档最多词数 | 超过 4 秒档、不超过这个就按 6 秒 | 数字 |
| `split.flow.allow_10s` | Flow 允许 10 秒段 | 关了最长 6 秒 | 开关 |
| `split.grok.max_chars_10s` | Grok 10 秒档最多字数 | 一段不超过这么多字就按 10 秒生成 | 数字 |
| `split.grok.max_chars_15s` | Grok 15 秒档最多字数 | 超过 10 秒档、不超过这个就按 15 秒 | 数字 |
| `split.grok.dur_6s_max_chars` | Grok 6 秒档最多字数 | 一段不超过这么多字就按 6 秒 | 数字 |

**多模态接口**

| `ai.max_workers` | 同时写描述语的线程上限 | 接口池里所有 Key 加起来最多同时跑这么多个；调大更快但更容易触发限流 | 数字 |

**Gemini**

| `gemini.api_key` | Gemini API Key | 去 aistudio.google.com 免费申请；没有它②多模态 不能自动写描述语 | 密钥 |
| `gemini.model` | Gemini 模型 | 一般不用改 | 文本 |
| `gemini.mode` | 请求方式 | 直连 / 走隐藏页（Key 被绑定了来源网址时选后者） | 下拉：直连=direct，隐藏页=page |
| `gemini.concurrency` | Gemini 同时请求数 | 单个 Key 的并发 | 数字 |

**多镜头扩图**

| `flow.image_model_key` | Flow 生图模型 | 留空用 Nano Banana Pro；填 NARWHAL 用 Nano Banana 2 | 下拉：默认(留空) / NARWHAL |
| `flow.allow_paid` | 允许用扣额度的模型 | 默认关。打开后才能选白名单外的模型，**会消耗 Flow 额度** | 开关 |
| `flow.credit_guard` | 额度异常时停提交 | 单次请求显示扣了额度就停下来等你确认 | 开关 |
| `expand.min_images` | 图池最少几张 | 少于这个数就自动扩图补到这个数 | 数字 |
| `expand.per_round` | 每轮扩几张 | 一次向引擎要几张；调大快但被拒时浪费多 | 数字 |
| `expand.flip_replacements` | 补图时左右翻转 | 换进来的图镜像一下，减少和原图雷同 | 开关 |
| `expand.dup_dist` | 重复图判定距离 | 相似距离小于这个数当成同一张丢掉；调小更宽松、调大更严 | 数字（0~255） |
| `expand.no_frontal` | 扩图不生成正脸 | 开了扩图时避开人物正对镜头的机位（收片的「正脸角度」卡片不受此限） | 开关 |

**配图轨**

| `track.hold` | 一张图连用几段 | 同一张图连续用在几段上再换下一张 | 数字 |
| `track.jitter` | 连用段数的随机浮动 | 在上面的基础上 ± 这个数，避免太规律 | 数字 |
| `track.no_adjacent_repeat` | 相邻两段不用同一张 | 开了保证换图时不会和上一段一样 | 开关 |
| `track.strip_view` | ③编排 中间那条默认显示 | 图池 / 镜头序列 | 下拉：图池=pool，镜头序列=shots |

**引擎**

| `engine.flow.mode` | Flow 提交方式 | 接口直连 / 模拟点网页（接口失效时切后者） | 下拉：接口=api，网页=dom |
| `engine.flow.model` | Flow 视频模型 | 默认是不扣额度的 Lite 档；带「扣额度」的要先打开上面的允许开关 | 下拉（现有 `flow_video_models()`） |
| `engine.flow.max_concurrent` | Flow 同时生成几条 | 每个 Flow 账号同时在做的视频数 | 数字 |
| `engine.grok.max_concurrent` | Grok 同时生成几条 | 每个 Grok 账号同时在做的视频数 | 数字 |

**生成方式**

| `generate.mode` | 新任务默认生成方式 | 逐段首帧 / 锚点+接片 / 尾帧接龙；每个任务还能在 ④生成 里单独改 | 下拉 |
| `chain.upscale_every` | 接龙每几段提一次清（如果 67 号把它放进 settings 而不是 manifest） | 0 = 不提 | 数字 |

**界面**

| `ui.lang` | 界面语言 | 中文 / English | 下拉 |
| `ui.open_mode` | 启动后怎么打开 | 独立窗口 / 系统浏览器 | 下拉：窗口=window，浏览器=browser |
| `ui.port` | 本机端口 | 被占用时改一个，比如 9401；改完要重启 | 数字 |
| `ui.bench.density` | 检查台密度 | 紧凑 / 舒适 / 大图 | 下拉 |
| `ui.bench.zoom` | 检查台缩放 | 40~200，也可以在检查台里按快捷键调 | 数字 |

**不在设置页显示的**（它们是程序状态，不是给人调的）：`ui.current_task`、`setup.done`、
`prompts.*`（提示词有自己的编辑区）。渲染循环里按一个 `HIDDEN_KEYS` 集合跳过，
测试断言这几个键不出现在设置页 DOM 里。

### D. 顺便

- 每组标题右边的「恢复默认」保留；每一行鼠标悬停时行尾出现一个小的「恢复此项」（调现有 `config.reset(key)`），
  改错一个不用整组恢复。
- 输入框改了还没保存时，那一行左侧一道细的高亮边，底部「保存」按钮亮起；和现在一样，只是让人看得见哪些改了。
- 英文界面（`ui.lang=en`）用 `en.json` 里对应的 `set.key.*` / `set.hint.*`，**没有的回落到中文**，
  不许回落到键名。

### E. 测试 `tools/test_spec69.py`

- 遍历 `config.DEFAULTS`：每个不在 `HIDDEN_KEYS` 里的键，`zh.json` 都有 `set.key.<k>` 和 `set.hint.<k>`，且都非空、都含中文字符。
- `set.hint.*` 每条不等于 `set.key.*`（防止偷懒复制一遍）。
- 渲染（Node 里挂 Vue 组件或现有的 `test_spec*_ui.js` 那套）：设置页 DOM 里主标签不含 `.`（没有键名当标签），
  `ui.current_task` / `setup.done` 不出现。
- 下拉类键的选项数和 C 表一致，选项文字是中文。
- 回归：`python -m pytest tools/` 全过。

## 验收

1. 打开设置，从头滚到尾：**没有一个主标签是英文键名**，每项下面都有一句说明，说明能看懂「调大调小会怎样」。
2. 鼠标悬停某一行，才看到灰色的 `expand.dup_dist`。
3. `gemini.mode`、`engine.flow.mode`、`track.strip_view`、`ui.open_mode`、`generate.mode` 都是中文下拉，不是文本框。
4. 随便改一个数字不保存，那一行有高亮；点「恢复此项」它回默认。
5. `python -m pytest tools/test_spec69.py` 里那条「每个 DEFAULTS 键都有中文」的测试是绿的；
   往 `DEFAULTS` 临时加一个新键不写中文 → 它变红。
