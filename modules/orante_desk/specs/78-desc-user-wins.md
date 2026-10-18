# 78 · 描述语以用户改的为准，永远不许被覆盖回去

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/05-describe-job-and-gemini-panel.md`、
> `specs/29-prompts-persist-and-queue.md`、`specs/42-autosave-drafts.md`（<span>①</span>任务 那张表的草稿保护
> **已经做过一次同类的事，照着它的思路做**）、`specs/75-gemini-card-menu.md` 和它们的报告，再读
> `ui/js/panels/gemini.js`（`loadManifest`、`loadManifestSafe`、`patchImage`、`onDescBlur` / `onDescInput`、
> 那几个 `watch(... loadManifest*)`、模板里的 `.desc-edit` / `.voice-edit`）、
> `orante/services/describe.py`（`describe_one`、批量那条路）、`orante/services/images.py`（`patch`）、
> `orante/services/expand.py`（`_auto_describe`、`_apply_new_file` 里清空 desc 那两行），再读本规格。
> **这条排在队列最前面，先于 76 / 77。** 改前备份 `*.bak-0920userwins`。
> 报告写到 `specs/reports/78-desc-user-wins.md`。中文注释。

## 用户原话

> 描述语这里必须可以让用户随便改，**而且以用户改后的为准**。我现在改着改着，它就自动恢复回去了，这样是不行的。

## 已经打过的补丁（别推翻，在它上面继续）

`gemini.js` 里 0920 已经加了一层应急保护，注释以 `// ---- 描述语手改保护（0920）----` 开头：

- `isEditingDesc()` / `loadManifestSafe()` / `flushReload()`：**正在输入时不重载 manifest**，
  挂起来等失焦再补。三个自动刷新的 watch 已经改走 safe 版。
- `onDescInput` / `onVoiceInput`：停手 **700 ms** 自动存一次（防抖），失焦时取消防抖并立即存。

这层解决了"打字打到一半被重渲染吞掉"。**但根子上还有三个洞没堵**，本规格堵它们。

## A. 洞一：`:value` 单向绑定，本质上是不可靠的

只要 `manifest.value` 被整体替换，所有 textarea 都会被绑回仓库里的值。
`loadManifestSafe` 只防住了"**当前有焦点**"那一个框；别的框里如果有没保存的内容（比如用户改了 A 框，
没失焦就直接去点 B 框），照样会被冲掉。

**要做的**：给每张卡的 desc / voice 建**本地草稿态**。

- 一个 `drafts` 响应式对象，键 `"<p>:desc"` / `"<p>:voice"`，值是用户正在编辑的文本。
- 模板改成 `:value="draftOf(im,'desc')"`，`draftOf` 有草稿用草稿、没有才用 `im.desc`。
- 保存成功 → 删掉那条草稿（此后跟随仓库值）。
- **`loadManifest` 改成合并式**：新数据回来后，凡是 `drafts` 里还有未保存内容的字段，**保留草稿**，
  其余照常更新。这样任何一次后台刷新都不会吃掉任何一个框里的内容，不只是有焦点那个。
- 草稿写进 `localStorage`（键里带 `tid`），**软件崩了 / 不小心关了也不丢**——42 号给任务表做过一模一样的事，
  照抄它的做法和过期清理策略。

## B. 洞二：自动写描述语会盖掉手改的

这是用户说的"自动恢复回去"的另一半：手改完，之后任何一次"写描述语"（Gemini 批量、跨任务队列、
扩图后的 `_auto_describe`、换图卡片生成后的自动描述）都会把手改的内容原地盖掉。

**要做的**：

1. `images` 里每张图加 `desc_manual: true`（`voice_manual` 同理）。
   **只有用户在界面上手改**才置 true —— `patchImage` 收到 `desc` 字段且请求里带 `by:"user"` 时置。
   Gemini 写的、拷贝卡片带来的、扩图自动写的，一律**不**置。
2. `describe.describe_one` 和它的批量入口加一道门：
   目标图 `desc_manual` 为真 → **跳过，不调 Gemini，不改内容**，日志一行
   `· p03 描述语是手改的，跳过（要重写请在卡片上点"恢复自动"）`。
3. `expand._auto_describe`、68 号卡片生成后的自动描述，同样尊重这个标志。
4. **例外，且只有这一个**：75 号的<span class="btn-ref">同步描述语到选中的 N 张</span>是用户明确下的指令，
   可以覆盖手改的（它本来就有撤销）。覆盖之后目标的 `desc_manual` 也置为 true。

### 要有出口

锁死了就得有解锁的办法，否则用户手改一次之后再也没法用自动描述了。

- 卡片右键菜单（75 号那个）加一项 **`恢复为自动生成`**：清掉 `desc_manual`，下次写描述语时正常参与。
  灰掉条件：本来就不是手改的。
- <span>②</span>多模态 顶上"写描述语"按钮旁边加一个小勾选框
  **`连手改过的一起重写（N 张）`**，默认**不勾**。勾上才覆盖，且点击时二次确认。
- 手改过的卡片上给一个**小标记**（比如描述语框左上角一个小小的 ✎ 角标，`title` 写
  「这条是你手改的，自动写描述语时会跳过」），让人一眼看出哪些是自己写的。

## C. 洞三：换图 / 提升画质会清空描述语

`expand._apply_new_file` 里有两行：

    im["desc"] = ""
    im["measure"] = {}

换图之后重写描述语是对的（画面真变了），但**如果那条描述语是手改的，清空就等于把用户的劳动直接删了**。

**要做的**：`desc_manual` 为真时：

- **不清空**，保留原文；
- 在卡片上标一条黄色提示 `图换过了，描述语还是你原来写的那条` + 一个<span class="btn-ref">按新图重写</span>按钮；
- 66 号「提升画质」那条路径本来就不该重写描述语（画面没变），确认它确实没走这两行。

## D. 存得住要看得见

用户现在完全不知道自己改的存没存上。

- 每个描述语框右下角一个极小的状态字：`● 未保存` → `⟳ 保存中` → `✓ 已保存`（2 秒后淡出）。
  样式沿用提示词块现有的 `.g-save`（`saveClass` / `saveLabel` 那一套），**别另造**。
- 保存失败 → 红色 `● 保存失败`，`title` 是原因，草稿**留着不删**，下次还能重试。

## E. 测试

### `tools/test_spec78.py`（**先设 ORANTE_DATA**）

- `patch` 带 `by:"user"` → `desc_manual` 为 true；不带 → 不置。
- `describe_one` 对 `desc_manual` 为 true 的图：**不调 Gemini**（打桩计数为 0）、内容不变、日志含「跳过」。
- 批量写描述语：3 张里 1 张是手改的 → 只写 2 张，返回的汇总里点名跳过了哪张。
- `force:true`（对应界面那个勾选框）→ 手改的也重写。
- 75 号的 `copy-desc` → 可以覆盖手改的，覆盖后目标 `desc_manual` 为 true。
- `_apply_new_file` 对 `desc_manual` 为 true 的图：`desc` **没被清空**，且打了"图换过了"的标记。
- 「恢复为自动生成」接口 → `desc_manual` 清掉。
- 回归：`python -m pytest tools/` 全过，`test_spec05` / `test_describe` 不回退。

### `tools/test_spec78_ui.js`

- 在 A 框输入但**不失焦**，触发一次 `loadManifest`（后台刷新）→ **A 框内容还在**。
- 在 A 框输入、点到 B 框（A 失焦触发保存）、再触发刷新 → A 的新内容还在，B 也没被清。
- 改了 3 个框都不失焦，触发刷新 → **三个框的内容全在**（这条是 A 节合并式加载的核心，必须有）。
- 输入停 700 ms → 发出一次保存请求；再输入 → 又一次；中途失焦 → 防抖被取消、只发一次。
- 状态字依次出现 未保存 → 保存中 → 已保存。
- 刷新页面（模拟重开）→ 未保存的草稿从 localStorage 恢复。
- 手改过的卡片显示 ✎ 角标；右键菜单里「恢复为自动生成」可点，点完角标消失。

## 验收

1. 在 <span>②</span>多模态 随便改一条描述语，**一直打字不失焦**，同时让后台跑个别的（比如在另一个任务扩图），
   你打的字**一个都不掉**。
2. 连改 3 张的描述语都不失焦，切去别的面板再回来 —— 三张的改动都还在。
3. 改完一条，然后点<span class="btn-ref">写描述语</span>批量重写：**手改的那条纹丝不动**，
   日志里写明跳过了它；其余的正常重写。
4. 手改过的卡片左上角有 ✎ 标记，鼠标停上去写着"这条是你手改的，自动写描述语时会跳过"。
5. 勾上<span class="btn-ref">连手改过的一起重写</span>并确认 → 这次才会覆盖。
6. 右键<span class="btn-ref">恢复为自动生成</span> → 标记没了，下次批量会重写它。
7. 对一张手改过描述语的图换图 → 描述语**没被清空**，卡片上提示"图换过了"，点<span class="btn-ref">按新图重写</span>才重写。
8. 改到一半直接关掉软件，重开 → 草稿还在框里。
