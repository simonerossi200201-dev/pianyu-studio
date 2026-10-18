# 规格 13 · 空镜判定提前到「准备/下图」阶段，并从主体编号里剔除

> **0923 作废一半（见规格 88）**：准备阶段不再自动判空镜（太慢，还常误判），描述语阶段也不再问那一题。图全部收下当主体，哪张是空镜由用户自己标。本号里的搬家（`空镜头/`）、重编号（`p01…` / `b01…`）、与磁盘同步、有文案时拦住空镜图出片，全部照旧有效。

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先读 `specs/00-architecture.md`，再读 `specs/02-images-service.md`、`specs/05-describe-job-and-gemini-panel.md`，再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的」写到 `specs/reports/13-broll-at-prepare.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。**改之前把 `orante/services/prepare.py`、`describe.py`、`images.py` 各备份成 `*.py.bak-0906`。**

涉及文件：`orante/services/prepare.py`、`orante/services/describe.py`、`orante/services/images.py`（可能）、`ui/js/panels/tasks.js`（准备日志/结果里显示空镜数）、`orante/i18n/zh.json`。

## 背景 / 问题
现在空镜判定（`describe.py` 的 `BROLL_PROMPT`）只在 **描述语作业** 里跑。下图刚完成时，大全景照片（例：`D:\1_PROJECT\2026-09-06\A\T0003-董瑞01-v04\images\p02.jpeg`——几千人的会场，讲员在画面里只有一小条）仍然占着 `p02` 这个主体编号，直到用户想起去多模态面板跑描述语。用户要的是：**下图那一刻就判、就搬、就不占主体编号**。另外现有提示词最后一句「有一个明确被拍摄的人（面朝镜头…）→ false」会和口径 2「大全景」打架——上面那张图讲员确实面朝镜头，Gemini 很可能因此判成主体。要改。

## 一、判定搬到 prepare
1. 把 `describe.py` 里的 `BROLL_PROMPT`、`_parse_broll`、`_relocate` 抽到新文件 `orante/services/broll.py`，提供 `judge(gemini, img_path) -> dict` 和 `relocate(task_dir, im, role)`；`describe.py`、`expand.py` 改成 import 它（expand 里那个"最小版"删掉）。
2. `prepare.py`：`images.fetch()` 回来之后、拆段之前，加一步 **空镜筛**：
   - 有 Gemini Key 才跑（`Gemini()` 构造失败 → 记日志「未配置 Gemini，空镜留到描述语阶段判」，跳过，不报错——下图不能因为没 Key 而失败）。
   - 并发按 `gemini.concurrency`。每张图只问 `judge()`（不写描述语、不测量，那些仍留在 describe）。
   - 判为空镜的：`role="空镜"`、`eye="空镜"`、记 `broll_reason`，文件搬到 `空镜头/`。
   - 日志每张一行：`p02 → 空镜（几千人的会场，讲员很小）` / `p01 主体`。结尾一行 `✓ 主体 N 张，空镜 M 张`。
3. **编号**：空镜筛完成后，在 prepare 里**重新编号**——主体图按原顺序连续编 `p01…`，空镜按原顺序编 `b01…`（文件名同步改：`空镜头/b01.jpeg`），manifest 的 `p` 字段跟着改。这一步只在 prepare 里做（此时还没有配图轨，不存在引用冲突）。describe / gemini 面板里手动改 role 的路径**不重编号**，只搬文件（保持现状）。
   - `images.fetch` 的返回结构、以及后面所有按 `p` 找图的地方（track / arrange / generate / 各面板 `pcolor(seg.image)`）要能接受 `b01` 这种编号；`colors.js` 给 b 开头的编号固定用灰色。检查 `fmtP` 之类的格式函数别把 `b01` 当坏数据。
4. `describe.py` 的 `run` 保留判定逻辑作为兜底（prepare 没 Key 时的图），逻辑改为调用 `broll.judge`。

## 二、提示词口径改成"景别优先"
`BROLL_PROMPT` 改成下面这个意思（措辞可润色，规则不能丢）：
- 先判景别。**远景/大全景（看得见整个场地、看台、几百上千人，主要人物的身高不足画面高度的 1/4）→ 一律 is_broll=true，哪怕有人面朝镜头、哪怕有大屏幕上放着人脸。** 大屏幕上的脸不算主体。
- 观众席 / 一排排背影后脑勺 / 空场地 / 器物特写 / 人人平均无主角 → true。
- 只有当一个人**本身**（不是屏幕上的）占画面主要面积（身高 ≥ 画面 1/4，或中景以内）且被明确拍摄时 → false。
- 多加一个输出字段 `"subject_height": 0~1`（主要人物身高占画面比例的估计），解析时存进 manifest `im["broll_check"]`，方便以后调阈值。

## 三、任务面板
准备完成的结果/日志里显示「主体 N · 空镜 M」；空镜 M > 0 时行末加一个灰色小标 `空镜 M`。

## 验收
1. 用上面那个 T0003 的三张图新建一个测试任务（拷图到临时 Drive 链接或本地路径都行，按 images.py 支持的来源），点「准备」：p02 应被判空镜、搬到 `空镜头/b01.jpeg`，剩下两张变 `p01`/`p02`；日志有原因。
2. 无 Key 时准备照常成功，日志有「留到描述语阶段判」。
3. 描述语作业对已判过的图不再二次判（`broll_check` 已有就跳过判定那一问，省一次 API）。
4. 配图轨、生成、收片面板对含 `b01` 的 manifest 不报错，`b01` 不进配图轨。

## 四、与磁盘同步：用户手动把图搬进/搬出 `空镜头/`，Orante 要自动认账
**场景**：T0003 准备完之后，用户在资源管理器里把 `images/p02.jpeg` 直接拖到 `空镜头/`。现在多模态面板仍把 p02 当主体显示（图裂了、显示 alt 文字 `p02`），编排也会把它算进去。要改成：**manifest 里的 role 以磁盘为准**。

实现（放在 `broll.py`，函数 `sync_with_disk(task_dir, manifest) -> list[str]`，返回变更日志行）：
- 对 manifest 每张图（按文件名 `Path(im.file).name`）在 `images/`、`images/_候选/`、`空镜头/` 三处找文件：
  - 记录路径存在 → 不动。
  - 记录路径不在，但 `空镜头/` 里有同名 → `role="空镜"`、`eye="空镜"`、`file` 改过来、`origin_sync="manual"`，日志 `p02 用户已手动移到 空镜头/`。**编号不改**（准备之后可能已有配图轨引用），只改 role；配图轨里若有段引用它，`track.plan` 本来就跳过 `role=空镜`，前端 track.js 已把空镜当 dead 处理——但要**确认 arrange/generate 遇到 `seg.image` 指向一张现在是空镜的图时的行为**：这样的段在配图轨面板里标红「图已转空镜，需重排」，`全部生成` 前拦住并提示，不能带着空镜图去生成主体片。
  - 记录路径不在，但 `images/` 里有同名（用户从空镜头搬回来）→ `role="主体"`，`eye` 恢复成 sidecar/默认 `闭眼`，`file` 改回。
  - 哪都没有 → `exists=false`，前端显示「文件不在」，不裂图。
- 顺带扫 `空镜头/` 和 `images/` 里 manifest 没登记的新文件（用户手动放进来的）：登记进 manifest，空镜头里的编 `b` 系列下一个号，images 里的编 `p` 系列下一个号，`origin="manual"`。
- **调用时机**：`GET /api/tasks/{tid}/manifest`、`/track`、`/review` 三个读接口进来时先跑一次 `sync_with_disk`，有变更就落盘并把日志行放进响应的 `sync_log` 字段；前端（gemini.js / track.js / review.js）收到非空 `sync_log` 就 toast 一次「已按磁盘同步：p02 → 空镜」。describe / arrange / generate 三个 job 开头也各跑一次。成本只是几次 `stat`，不用做 watcher。

验收补充：
5. T0003 手动把 `images/p02.jpeg` 拖进 `空镜头/`，切到多模态面板：p02 从「主体图」消失、出现在下方「空镜头」栏、role 标签是空镜、无裂图；编排面板的可用图只剩 p01/p03；再拖回 `images/` 又变回主体。
