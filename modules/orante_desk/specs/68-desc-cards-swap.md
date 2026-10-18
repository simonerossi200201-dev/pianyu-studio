# 68 · 收片换图：没图可换时，用「描述卡片」直接生一张想要的

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/12-review-chain-and-finish.md`、
> `specs/24-gapfill-triage.md`、`specs/32-coverage-library.md`、`specs/47-expand-before-describe.md`、
> `specs/66-expand-account-and-upscale.md`、`specs/67-tail-chain-mode.md` 和它们的报告，再读
> `orante/services/review.py`（`set_image`、`get_review`）、`ui/js/panels/review.js`（第 662~715 行
> `usableImgs` / `openPicker` / `setImage(p, alsoRedo)` / `swapAndRedo` / `doRegen`）、
> `orante/services/expand.py`（`_no_frontal`、第 260~290 行提示词拼法、`refill_one`、`_apply_new_file`、
> `_auto_describe`、`resolve_account_id`）、`orante/services/coverage.py`（`pick`、库结构）、
> `orante/legacy/coverage_library.json`、`orante/services/prompts.py`（可编辑块登记）、
> `orante/services/preset.py`（分组导出）、`orante/db.py`（建表）、`orante/server.py`
> `/api/tasks/{tid}/review/image`，再读本规格。
> **先做完 67。** 改前备份 `*.bak-0916cards`。报告写到 `specs/reports/68-desc-cards-swap.md`。中文注释。

## 场景

⑤收片 里某一段的片子不理想，点「换图」。现在只能从图池里挑一张现成的。
两种情况下这不够用：

1. **没别的图可换** —— 尾帧接龙（67）的任务整个图池就一张起头图。
2. **有图但都不是想要的** —— 用户就想要一张正脸、或者一张中景，图池里恰好没有，只能勉为其难。

所以在换图面板里加一排**描述卡片**：点「正脸角度」，就以这段现在的图为身份参考、按卡片里的
描述语**生一张新图**，下载、写描述语、换上去、重生视频，一气呵成。

---

## A. 卡片：存在软件里，能看、能改、能加

### A1. 数据

新表 `desc_cards`：

    id TEXT PK        -- C001…
    name TEXT         -- 卡片名，界面上显示的
    prompt TEXT       -- 描述语正文
    sort INTEGER      -- 排序
    builtin INTEGER   -- 1 = 出厂四张（可改可删，删了能「恢复出厂卡片」）
    created TEXT
    updated TEXT

第一次启动（或表为空）时播种四张出厂卡片。**用户改过的内容以后不再被播种覆盖**：只在表完全为空时播种。

### A2. 出厂四张（默认描述语，现在就写好，用户能看到、能改）

每张的 `prompt` 是**机位与景别**那一段，不含人物身份 —— 身份由参考图带进去，
拼法见 B3。默认文案：

**正脸角度**

    正面机位：人物正对镜头，脸部完整、五官清晰，双眼平视前方略低于镜头，肩膀平正。
    保持与参考图完全相同的人物、衣着、场景和光线。
    Frontal view, subject facing the camera directly, full face clearly visible, same person, same outfit, same setting as the reference.

**侧脸角度**

    侧面机位：人物身体转向一侧约四十五到六十度，露出清晰的侧脸轮廓，目光看向画面外的一侧。
    保持与参考图完全相同的人物、衣着、场景和光线。
    Three-quarter to profile view, subject turned 45–60 degrees, clear jawline, looking off-frame, same person, outfit and setting as the reference.

**人物近景**

    近景：取景从胸口以上到头顶，脸部占画面主要位置，表情和眼神清楚可见，背景虚化。
    保持与参考图完全相同的人物、衣着、场景和光线。
    Close-up from chest up, face dominant in frame, expressive eyes, shallow depth of field, same person, outfit and setting as the reference.

**人物中景**

    中景：取景从腰部以上，能看到上半身和手势，人物在画面中占三分之二左右，背景可辨。
    保持与参考图完全相同的人物、衣着、场景和光线。
    Medium shot from the waist up, upper body and hand gestures visible, subject fills about two-thirds of the frame, background recognizable, same person, outfit and setting as the reference.

### A3. 管理界面

设置抽屉里新开一区「换图卡片」：列表 + 「新建卡片」「复制」「上移/下移」「删除」「恢复出厂四张」。
点一张展开编辑：名字一行、描述语多行，「保存」落库。
同一个编辑器也要能从换图面板里直接打开（B1 的「编辑…」），改完立刻生效，不用重开。

### A4. 进预设

26 号的预设导出/导入加一组 `desc_cards`，默认勾选。合并规则和 `prompts` 一致（同名冲突按用户选的合并方式）。

---

## B. 换图面板里怎么用

### B1. 界面

`review.js` 现有的换图 picker（`picker.on`）里，图池缩略图那一栏**上面**加一排：

    用描述生一张新图（以本段当前的图为身份参考 · 账号 flow · TIK-L1 ▾）
    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌ + ┐
    │ 正脸角度  │ │ 侧脸角度  │ │ 人物近景  │ │ 人物中景  │ │新建│
    │ 正面机位… │ │ 侧面机位… │ │ 近景：取… │ │ 中景：取… │ └───┘
    │ [编辑…]   │ │ [编辑…]   │ │ [编辑…]   │ │ [编辑…]   │
    └──────────┘ └──────────┘ └──────────┘ └──────────┘
    ☑ 生成后自动写描述语并重生这一段

- 卡片显示名字 + 描述语前两行（灰字），鼠标悬停看全文。
- 「账号 ▾」就是 66 号那个扩图账号下拉，同一个 `expand_account_id`。
- 复选框默认勾上；不勾就只生图换图、不重生（用户想先看看图）。
- 卡片超过 6 张横向滚动，别把面板撑爆。
- 「身份参考」默认 = 这段现在的图；chain 模式下 s002 之后的段没有图池图，用 **它记录的参考文件**
  （`sg["chain"]["ref_file"]`，即上一段尾帧）；实在没有就用任务的 `expand_ref` 第一张。
  界面上把用的是哪一张显示出来（小缩略图）。

### B2. 点一张卡片：新 job `card_swap`

    POST /api/jobs {"kind":"card_swap","task_id":"T0022","s":5,"card_id":"C001","auto_redo":true}

流程（每一步都打日志，用户能跟着看）：

1. 解析账号：`expand.resolve_account_id(t)`（66 号），`make_engine`。
2. 定身份参考图（B1 最后一条）。
3. 拼提示词（B3），`eng.gen_images(prompt, ref=参考图, n=1, dest_dir=d/"images/_cards")`。
   引擎 `BlockedImage` → 人话失败，`gen_fail` 不动图池里任何图。
4. 新图**进图池**成为一条新的 `im` 记录：`p` 取下一个空号，`origin = "card:C001"`，`card_name`，
   `card_ref = <参考图 p 或 s>`，`status = "ok"`。文件放 `images/` 常规位置。
   **不覆盖、不挪走任何旧图** —— 这是新增一张图，不是换图那种「替换文件」。
5. `_auto_describe`（47 号那套）给新图写描述语 + 测量。失败只记日志，不挡后面。
6. 调 `review.set_image(tid, s, 新p)`：钉进配图轨、段打回 planned（现有逻辑）。
   chain 模式下同时把这一段的段级模式置为 `first_frame`（67 号「换图起头」），
   下一段从这一段的新尾帧接。
7. `auto_redo` 为真 → 直接起 `regenerate` job 重生这一段（和 `swapAndRedo` 走的是同一条路）。
8. toast：`✓ s005 已按「正脸角度」生成 p14 并换上，正在重生`。

### B3. 提示词拼法

    <卡片 prompt>
    <参考图的描述语里「人物身份」那部分：谁、衣着、发型>    ← 从 im["desc"] 或 chain.desc 里取，取不到就略过
    <prompts.expand 里的通用尾巴（画质、真实感那些）>

- **正脸卡片必须绕过 `expand.no_frontal`。** 现在扩图默认关掉正脸（`_no_frontal()` 返回 True），
  卡片路径要显式传 `no_frontal=False`，否则「正脸角度」永远生不出正脸。写测试盯住这一条。
- 不要走 `coverage.pick` 随机挑机位 —— 卡片本身就是机位，随机挑等于把用户的选择扔了。
- 报告里贴一次四张卡片各自实际发出去的完整提示词。

### B4. 生出来的图不满意

新图在图池里，和别的图一样：可以再点别的卡片、可以裁切、可以删除。
删除走现有的 `discard_image`（挪 `_replaced/`，不真删）。

---

## C. 测试 `tools/test_spec68.py`（**先设 ORANTE_DATA**，FakeEngine + FakeGemini）

- 空表启动 → 四张出厂卡片，`builtin=1`，顺序正脸/侧脸/近景/中景。
- 改一张的 prompt 再重启 → 不被覆盖；删一张 → 不回来；「恢复出厂」→ 四张回来、用户自建的不动。
- 新建 / 复制 / 排序 / 删除接口各一条。
- `card_swap`：跑完图池多一条 `origin=="card:C001"` 的图，旧图文件一个都没动（跑前跑后 `images/` 下原有文件 sha256 不变），
  段的 `image` 是新 p，段状态 planned，`auto_redo=true` 时 `regenerate` job 被起了一次。
- 正脸卡片：FakeEngine 收到的提示词含「正对镜头」，且 `no_frontal` 参数为 False；
  同一测试里普通扩图仍是 True（证明没把全局改了）。
- chain 模式 s005 点卡片：参考图是 `sg["chain"]["ref_file"]`；跑完段级模式为 `first_frame`。
- `BlockedImage` → job 失败、图池没有新记录、`images/_cards/` 没有残留。
- 预设导出含 `desc_cards` 组，导入后卡片回来。
- 回归：`python -m pytest tools/` 全过。

---

## 验收

1. 设置 → 换图卡片：能看到四张出厂卡片和它们的描述语原文；改一张的文字、新建一张「仰拍」、
   复制一张、删一张、拖排序，重启后全部还在。
2. ⑤收片 随便选一段点「换图」：面板上方一排卡片，点「正脸角度」→ 日志里看到账号、参考图、提示词 →
   十几秒后图池多一张**正脸**的新图（不是随机机位）、这段换成它、自动写好描述语、视频重生开始。
3. 尾帧接龙的任务里对 s005 点「人物中景」：参考图是 s004 的尾帧，生成后 s005 从新图起头，
   s006 从 s005 的新尾帧接。
4. 不勾「自动重生」点卡片 → 只换图不重生，段状态 planned，可以再点别的卡片换。
5. 导出预设再导入到空库 → 自己改过的卡片文字原样回来。
