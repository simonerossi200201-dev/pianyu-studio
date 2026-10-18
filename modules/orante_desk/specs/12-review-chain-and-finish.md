# 规格 12 · 收片面板：修连播 bug + 「完成」绿灯与庆贺

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先读 `specs/00-architecture.md`，再读 `specs/09-review-panel.md`（收片面板原规格），再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的」写到 `specs/reports/12-review-chain-and-finish.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。**先改之前把 `ui/js/panels/review.js` 备份成 `ui/js/panels/review.js.bak-0906`。**

涉及文件：`ui/js/panels/review.js`、`ui/css/app.css`、`orante/i18n/zh.json`、`ui/js/panels/generate.js`（只看，可能不用改）、`说明文档/使用说明.md`（补一行快捷键）。后端**不用改**——用现成的 `POST /api/tasks/{tid}/status {status}`。

---

## 一、Bug：「连播 开」不是真连播

**现象**：连播开着，当前段播完后，同一段又重播一次，然后停下，不跳下一段。

**根因**（已定位，直接按此修）：`review.js` 里 `onEnded(seg)` → `move(1)` → `focusS()` 只改了 `curS.value`，紧接着同步调用 `play()`；`play()` 靠 `curVideo()` = `document.querySelector('.rv-card.cur video')` 找视频，可 Vue 还没重渲染，`.cur` 还挂在**旧卡**上，于是旧视频又播一遍。第二次 `ended` 时 `curS.value !== seg.s`，守卫拦住，就停了。`play()` 里 `!v` 分支那段自递归也同理。

**修法**：不要靠 DOM class 找当前视频。

1. 给 `<video>` 加 `:data-s="seg.s"`；`curVideo()` 改成 `document.querySelector('.panel-review video[data-s="' + curS.value + '"]')`（或用 Vue 的 `ref` 数组按 `seg.s` 存，二选一）。`curCard()` 同样改为按 `data-s` 找卡片（给 `.rv-card` 也加 `:data-s`），滚动定位用。
2. `onEnded` 和 `move()` 之后的 `play()` 都改成 `await nextTick()` 再 `play()`，双保险。
3. `onEnded` 里的守卫保留（`chain.value && curS.value === seg.s`），加一条：如果下一段没视频（缺片），继续往后跳，直到找到有视频的段；到末尾则停并 `playingS = 0`。
4. `play()` 里 `v.play()` 前先 `v.currentTime = 0`（连播时从头放）；被用户手动空格暂停后再空格，则不要归零——区分办法：`play(fromStart)` 传参，连播/J/K 切段时 `fromStart=true`，`toggle()` 里 `false`。
5. 连播中切段时要顺手把上一段的进度条 `.bar i` 归零。

验收：连播开、4×，从 001 按空格，能一路播到最后一段自动停；中间有缺片会跳过；J/K 时也从头放。

---

## 二、任务「完成」绿灯 + 庆贺

**目的**：全部片子看过没问题后，一键把这个任务点绿，归入「过时」（`status = finished`，生成面板已有这个视图和右键项，`runall/全部生成` 本来就只跑非 finished 的——**核实一下 `services/generate.py` 的全部生成确实跳过 finished，若没跳过就补上**）。这样下次开新批次，这个任务自然不计。

### 2.1 左栏加「✓ 完成」按钮
- 位置：`重生…` 按钮下面，独立一块，样式绿色描边（`--ok` 色；CSS 变量看 `app.css` 已有的）。
- 可用条件：当前任务 `status === 'collected'` 或 `generating` 且没有缺片（`isMissing` 全 false）。有缺片/有 failed/有 `mark` 未处理时按钮变灰，tooltip 写明「还有 N 段缺片/N 段已标记」。**体检没跑过也允许点**（体检是可选的，别强制）。
- 点击流程：
  1. `pauseAll()`。
  2. `POST /api/tasks/{tid}/status {status:'finished'}`。
  3. 播庆贺动画（见 2.3）。
  4. 整个 `.rv-scroll` 上盖一层半透明绿色成功状态：左上角大号「✓ 已完成」徽章 + 任务 id + 完成时间（本地时间，`new Date().toLocaleString()` 就行；不落盘，只显示）。视频墙仍可看、可播，但 `标记/重生/完成` 按钮禁用。
  5. `await loadExtra()` 刷新任务列表。当前下拉里这个任务名字前加 `✓ `，保持选中不跳走。
- 快捷键：`F`（finish）。写进左下角快捷键提示区和 `说明文档/使用说明.md`「键盘」一节。
- 已完成的任务再进收片面板：不弹庆贺，只显示绿色成功状态层。左栏加一个小按钮「撤销完成」（把 status 改回 `collected`），防止手滑。

### 2.2 任务下拉
- `readyTasks` 现在把 finished 也列进去（它满足 `atLeastGen`），保留；但排序改成未完成在前、已完成在后，已完成的名字前缀 `✓ `，字色用弱色。
- 面板打开时如果 `tid` 为空，默认选第一个**未完成**的任务。

### 2.3 庆贺动画（不用任何库，纯 canvas）
- 在 `review.js` 里加一个 `burstConfetti(canvasEl)`：全屏固定定位的 `<canvas>`（`pointer-events:none; z-index` 高于面板），从底部左右两个角各喷一束，共约 160 片纸屑，颜色用编排面板那套 `colors.js` 的 p 色 + 金色 + `--ok` 绿，形状矩形/圆混合，有重力、有旋转、有轻微阻尼，2.2 秒内落完后自动移除 canvas，`requestAnimationFrame` 驱动，动画期间不要卡住播放。
- 同时中间弹一个大徽章：圆形绿底白色 ✓，缩放弹入（`transform: scale(0.3)→1.05→1`，300ms），下面一行文字「任务完成 · {task.id}」，1.6 秒后淡出，留下 2.1 的常驻状态层。
- `prefers-reduced-motion: reduce` 时跳过纸屑，只弹徽章。

### 2.4 收片面板顶部小条
- 顶部 `共 N 段 · 已下载 M` 旁边，任务完成后加一个绿点 + 「已完成」。

### 2.5 i18n
所有新文案进 `orante/i18n/zh.json`（键名 `review.finish`, `review.finish_tip_missing`, `review.finish_tip_marked`, `review.finished_badge`, `review.finished_at`, `review.unfinish`, `review.confetti_title` 等，按现有命名风格）。

验收：
1. 一个 collected 任务，按 `F` → 纸屑 + 徽章 → 状态层常驻 → 生成面板「过时」视图里能看到它，「全部生成」不再带它。
2. 有缺片的任务，`F` 无效，按钮灰且 tooltip 说明原因。
3. 刷新页面重进这个任务：无动画，绿色状态层在，「撤销完成」能把它拉回来。
4. 第一节的连播修好且不回归：J/K/空格/倍速/M 全部照旧。
