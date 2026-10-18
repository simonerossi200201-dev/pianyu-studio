# 66 · 扩图换账号 + 右键「提升画质」

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/07-arrange-and-expand.md`、
> `specs/14-expand-prompt-and-progress.md`、`specs/15-track-image-context-menu.md`、
> `specs/47-expand-before-describe.md`、`specs/17-upload-rejected.md` 和它们的报告，再读
> `orante/services/expand.py`（**重点**：`REPL_DIR`、`_borrow_flow`、`resolve_account_id`、
> `account_hint`、`_move_file`、`_apply_new_file`、第 735 行 `job.account_id or resolve_account_id`、
> 第 767/1019/1331 行三处 `eng.gen_images`）、`orante/services/imgedit.py`（`_backup`、`crop`、
> `flip`、`duplicate`、`_clone_rec`）、`orante/engines/base.py`（`can_gen_images`、`make_engine`、
> `BlockedImage`）、`orante/services/jobs.py` 的注册表、`orante/server.py`（`/api/jobs`、
> `/api/tasks/{tid}/image/{p}/*` 那几条）、`ui/js/panels/track.js`（第 215 `expandAcc`、
> 266 `expandBtnLabel`、437 载入、588~700 右键菜单、1415~1422 菜单 DOM）、
> `orante/i18n/zh.json` 的 `track.ctx.*`，再读本规格。
> **先做完 65。** 改前备份 `*.bak-0916upscale`。报告写到 `specs/reports/66-expand-account-and-upscale.md`。中文注释。

## 先说清楚：一半的地基已经有了

别重新造。现在已经存在的：

- `tasks` 表有 **`expand_account_id`** 列，`resolve_account_id()` 的优先级已经是
  ① `expand_account_id` ② 任务自己的账号 ③ 借一个最闲的 Flow。
- `account_hint()` 已经把「这次扩图会落到哪个账号」喂给界面，
  `expandBtnLabel` 已经把它显示成 `扩图 · flow · 账号名`。
- `expand.py:735` 已经是 `aid = job.account_id or resolve_account_id(t)` ——
  **job 本来就能带账号覆盖**。
- `_apply_new_file()` 已经把「旧图挪进 `images/_replaced/`、新图顶上、相关段打回、
  重写描述语」整套做好了。

**缺的只有两样**：一个能改 `expand_account_id` 的界面，和一个新的 job 类型。

---

## A. 扩图账号选择器

### A1. 界面

③编排 顶部那个扩图按钮现在长这样：`扩图 · flow · TIK-L1`。
在它右边加一个 `▾`（或者整个按钮拆成「主按钮 + 下拉箭头」两段，你选一种，报告里说明）。
点开是一个列表：

    ┌────────────────────────────────┐
    │ ● 跟随任务账号（flow · TIK-L1） │   ← 默认项，expand_account_id 为空时选中
    │ ──────────────────────────────  │
    │   flow · TIK-L2        ● 在线   │
    │   flow · TIK-L3        ○ 离线   │
    │   grok · TIK-G-SIMONE  ● 在线   │
    └────────────────────────────────┘

- 列表来源：`accounts.list_all()` 里 `enabled` 且 `can_gen_images(engine)` 的全部。
  **Flow 和 Grok 都要列出来** —— 用户明确要「Flow 做的任务也能选 Grok 扩图」。
- 在线状态用 `list_all()` 已有的 `alive`。离线的**不禁用**（点了会自动拉起 Chrome），
  但要显示 `○ 离线`，并在 title 里写「选了会先弹出 Chrome 让你登录」。
- **「跟随任务账号」必须是第一项，而且必须能选回去。** 选它 = 把
  `expand_account_id` 置空（`UPDATE tasks SET expand_account_id=NULL`）。
  现在 `resolve_account_id` 的报错文案里写着「清空 expand_account_id 让它自己挑」，
  可界面上根本没有清空的入口 —— 这次补上。
- 选中之后按钮标题立刻跟着变，不用刷新整页。

### A2. 接口

    GET  /api/tasks/{tid}/expand-account
         → {current:{id,name,engine,alive,source:"task"|"pinned"|"borrowed"},
            options:[{id,name,engine,alive,can_gen:true}...]}
    POST /api/tasks/{tid}/expand-account   body {"account_id": "A003"}   # "" 或 null = 跟随任务账号
         → 同上结构

- `source` 三态，给界面显示用：`pinned` = 用户在这里钉过的；`task` = 跟着任务账号；
  `borrowed` = 任务引擎不能生图、系统借的。**`borrowed` 要在下拉里显眼标一句
  「你的任务账号不能生图，现在借的这个」**，别让人以为是自己选的。
- 写入走 `taskmod`，不要在路由里直接拼 SQL。

### A3. 这个选择要管住三件事

`扩图`、`补图（refill）`、以及 B 节新增的 `提升画质`，**全部走同一个
`resolve_account_id`**。不许任何一条路径自己再挑账号。
写一个测试 grep 断言：`expand.py` 里 `make_engine(` 的每一处，账号都来自
`job.account_id or resolve_account_id(...)`，没有第二个来源。

---

## B. 右键「提升画质」

### B1. 它解决什么

用户把一张图裁切过（`imgedit.crop`），分辨率掉下来、糊了。
拿这张糊图当参考图，让 Flow / Grok **原模原样重画一遍**，出来的是干净的高分辨率版本，
换回去。后面拿它生成视频，首帧就不糊了。

### B2. 菜单

`track.js` 第 1415~1422 那个 `<ul class="ctx">`，在 `裁切…` 和分隔线之间插一项：

    复制
    水平翻转
    裁切…
    提升画质…          ← 新增
    ───────────
    删除

- `imgMenuOff(im,'upscale')` 复用 `imgLimited(im)`（空镜 / blocked / 文件不存在 → 灰掉）。
- i18n 键 `track.ctx.upscale`，中文「提升画质…」。带省略号，因为它会先弹确认框。

### B3. 点下去之后：先确认，别直接干

弹一个小对话框，把代价说清楚：

    提升画质 · p03
    用 flow · TIK-L1 重新生成这张图（跟扩图用同一个账号）
    原图 864×1536 → 会换成新生成的高清版
    旧图挪到 images\_replaced\，不会删
    [ 取消 ]  [ 开始 ]

账号那一行**要能点**，点开就是 A1 那个下拉 —— 用户明确说了「跟着扩图任务来选账号」。
在这里改，和在扩图按钮那里改是同一个 `expand_account_id`。

### B4. 干活：新 job 类型 `upscale`

进 `jobs.py` 注册表，和 `expand` / `refill` 平级，这样进度、日志、取消全都白捡。

    POST /api/jobs  {"kind":"upscale","task_id":"T0022","ps":["p03"]}

`ps` 收数组（现在界面一次只传一个，以后要批量不用改接口）。

每张图的流程：

1. `aid = job.account_id or expand.resolve_account_id(t)` → `make_engine(acc)`。
   引擎不能生图 → 直接报错，文案照抄 `resolve_account_id` 那两句的风格。
2. 记下原图的 `宽×高`、Laplacian 方差（清晰度）、aHash。
3. `eng.gen_images(prompt, ref=原图, n=1, dest_dir=d/"images/_upscale")`。
   **参考图就是这张图本身**，不是别的。
4. 拿到候选图，跑 B5 的三道闸。
5. 过闸 → B6 换图。不过闸 → 候选留在 `images/_upscale/`，日志和界面说明是哪一闸没过。

### B5. 三道闸——这三条是这个功能的全部价值所在

**不许跳过任何一条。** 少了任何一条，这个功能就变成「随机换一张图」。

1. **是不是同一张画面**：新图 vs 原图 的 16×16 aHash 汉明距离 ≤ `UPSCALE_SAME_DIST`
   （先定 **12**，报告里贴实测分布再定稿）。超了说明模型自己发挥了，画的是别的东西 → 不换。
   注意方向：扩图那边 `expand.dup_dist=40` 是「小于就算雷同、要丢掉」；
   这里正好相反，**我们要的就是雷同**。这两个常量含义相反，代码里各写一句注释说清楚，
   别让后来人看混。
2. **是不是真的更清楚**：新图像素面积 ≥ 原图 × 1.2，**或者** Laplacian 方差 ≥ 原图 × 1.3。
   两条都不满足 → 不换，日志写 `新图没有更清楚（864×1536 → 832×1472，锐度 61→58），没换`。
   这一条防的是「换了个寂寞」。
3. **人物还是不是同一个**：复用 `coverage` / 现有的人脸判断（`bench` 或 `verify` 里
   哪个现成用哪个，报告里写明用了谁）。判不出来就跳过这一条并在日志里说明，
   **不要为此新造一套人脸识别**。

三道闸的实测数字（每张图的距离、前后分辨率、前后锐度）都要写进 job 日志，
用户看得见才信得过。

### B6. 换图：**不要翻转**

复用 `_apply_new_file` 的思路，但**必须单独走一条路径，或者给它加 `flip: bool = True` 参数**：

- `_apply_new_file` 现在无条件 `_flip_to(pick, dest)`（那是扩图为了避免雷同才翻的）。
  **提升画质翻了就毁了** —— 用户要的是原模原样。`flipped` 也不要置 True。
- 旧图 → `d / REPL_DIR / f"{p}-{stamp}{后缀}"`，即 `images/_replaced/`。
  这就是这个工程现成的回收站（0908 那次事故之后定的规矩：**原图一律不删，只挪**），
  别另造一个目录。
- 新图写到原来那个相对路径（`im["file"]` 不变），刷新 `sha1`、`w`、`h`、`mtime`。
- `im["origin"] = "upscale"`，加 `im["upscaled"] = {"at":..., "from":[w,h], "to":[w,h],
  "dist":..., "acc":...}` 留痕。
- **`desc` 和 `measure` 不要清空、不要重写。** 画面没变，描述语还对；
  `_apply_new_file` 里那两行 `im["desc"]=""` / `im["measure"]={}` 加上 `_auto_describe`，
  在这条路径上**必须跳过**，白烧 Gemini 额度还可能把描述语改坏。
- 缩略图要刷新（地址带 `?v=mtime`，换了 mtime 就自然刷新，确认一下界面确实带了）。

### B7. 用过这张图的段，交给用户决定

**不要自动把段打回 `planned`。** 画面没变，只是更清楚了；已经出好的视频没必要作废 ——
用户上次为「重新编排图轨」的事说过一次，别再自作主张给他加活。

做法：换完之后，如果有 N 段在用这张图，弹一条带按钮的 toast：

    ✓ p03 已提清（864×1536 → 1728×3072）
    有 3 段用了这张图，已出的片子还是旧图的清晰度
    [ 打回这 3 段重生 ]   [ 先不管 ]

点「打回」才 `_reset_refs`。点「先不管」就什么都不做。

### B8. 提示词

新增可编辑块 `prompts.upscale`（跟 desc / camera / tail 那几块一样进设置→提示词，
能改、能单块恢复默认）。默认值：

    以上传的这张图为唯一参考，原样重绘一遍：同一个人物、同一张脸、同一个姿态和手势、
    同一个构图和取景、同一个背景和光线、同一套衣着。不要增减任何元素，不要改变镜头角度，
    不要重新构图。只把清晰度和细节做到最高：皮肤、发丝、布料纹理、背景细节都要锐利干净。
    Photorealistic, maximum detail and sharpness, identical composition to the reference.

报告里要贴一次实际发出去的完整提示词（拼好之后的）。

---

## C. 测试 `tools/test_spec66.py`（**先设 ORANTE_DATA**）

账号选择：

- `expand_account_id` 为空 → `GET /api/tasks/{tid}/expand-account` 的 `current.source == "task"`。
- POST 一个 Grok 账号给 Flow 任务 → 落库、`source == "pinned"`、`resolve_account_id` 跟着变。
- POST `""` → 置空，`source` 回到 `task`。
- `options` 里 Flow 和 Grok 都在；不能生图的引擎不在；停用的不在。
- grep 断言：`expand.py` 里每处 `make_engine(` 的账号都来自 `job.account_id or resolve_account_id`。

提升画质：

- 闸 1：伪造一张 aHash 距离 30 的候选 → **不换**，原图还在原处，候选留在 `images/_upscale/`。
- 闸 2：候选和原图同尺寸同锐度 → **不换**，日志含「没有更清楚」。
- 全过 → 原图出现在 `images/_replaced/` 里、新图在原路径、`im["origin"]=="upscale"`、
  **`im["flipped"]` 不为 True**、`im["desc"]` 和 `im["measure"]` **和换之前一模一样**。
- grep 断言：upscale 这条路径上不出现 `_flip_to`、不出现 `_auto_describe`。
- 段不会被自动打回：换完之后用这张图的段状态不变（`_reset_refs` 没被调）。
- 手动调「打回」接口之后才变 `planned`。
- 空镜 / blocked 的图：`imgMenuOff(im,'upscale')` 为真。
- 引擎抛 `BlockedImage` → job 以人话失败，不留半个文件。
- 回归：`python -m pytest tools/` 全过。

---

## 验收

1. ③编排 扩图按钮旁边的 ▾ 能列出所有能生图的账号（Flow 和 Grok 都在），选一个 Grok，
   按钮变成 `扩图 · grok · TIK-G-SIMONE`；重开软件还是它。再选「跟随任务账号」能回去。
2. 用那个 Grok 账号真跑一次扩图，出图落在本任务目录里，日志里的账号名对得上。
3. 找一张自己裁切过、明显糊的图，右键 →「提升画质…」→ 确认框里能看到账号和原图尺寸 →
   开始 → 日志里有三道闸的实测数字 → 新图顶上、**画面和原来是同一张**（不是镜像！）、
   肉眼看明显更清楚 → 旧图在 `images\_replaced\` 里躺着。
4. 用过这张图的段**没有**被自动打回；toast 上点「打回这 N 段重生」之后才变 planned。
5. 故意拿一张已经很清楚的图点提升画质 → 闸 2 拦下，提示「没有更清楚」，图没被换。
6. 设置→提示词 里能看到并修改 `upscale` 这一块，改完再跑一次生效。
