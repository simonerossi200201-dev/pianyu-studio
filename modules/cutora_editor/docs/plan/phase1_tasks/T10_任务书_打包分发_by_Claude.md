# BUG-01…05 全部签字 · QW 批放行 · T10 任务书 by Claude

**日期** 2026-09-16 · **点** `450c8fe` · `source_dirty=no` · golden 十个未动

---

## 1. 五个阻断 bug：全部签字

| | 证据 | 判定 |
|---|---|---|
| BUG-01 | `get_lod` 不再对 ndarray 求布尔；PaintError 按「异常类型 + 顶帧」去重，第 1 次 `_log.exception`，之后 10/100/1000 计数 | ✅ 这个节流形状正好 |
| BUG-02 | `test_fresh_load_knob_reaches_paint` **绿**：打开 g12 不套包 → 改 `color_text` → `resolve_paint_style` 已更新 | ✅ **最常见的路通了**。`prefer_task` 留到 BUG-02b 拆 |
| BUG-03 | 播放头列采样 `#e74c3c` | ✅ 截图里红线在波形之上 |
| BUG-04 | 根因是编辑菜单 `QAction` 绑了 `StandardKey.Delete → delete_selected_task()`；全局快捷键删了；三条锁绿 | ✅ 找到的是**真根因**，不是把症状盖住 |
| BUG-05 | `BUG-05_waveform.png` | ✅ 我看了：波形在、播放头在上面。**Luca 用真素材复测一次**，截图里是合成正弦 |

**Luca 现在可以复测这五条。** 复测点 `450c8fe`。

---

## 2. QW 批 + BUG-02b：放行，一批做完一起交

**每条 UI 改动交改前/改后全画布截图**，同主题同缩放。这批做完 golden 十个必须不动（全是 UI）。

| ID | 做什么 | 验收 |
|---|---|---|
| **BUG-02b** | 删 `prefer_task`。默认角色**不再有私有 style 副本**：`task.style` 就是默认角色的样式，非默认角色才有 override。`g12` fixture 把 `role.default.style` 拷进 `task.style` 后删掉 override | 哈希不动（内容相同）；`grep prefer_task` 零命中；`test_fresh_load_knob_reaches_paint` 仍绿 |
| **QW-01** | 删掉快速模式，打开即专业模式。模式切换按钮、设置项、相关代码路径全删 | `grep -i "快速模式\|quick_mode"` 零命中；截图 |
| **QW-02** | 右侧面板默认宽度减半；**控件按新宽度重排**（滑杆 + 数值框同行，标签上方或左侧窄列） | 截图：字幕样式 / 属性 / 角色映射 / 字块样式四个面板各一张 |
| **QW-03** | 「配色方案」里删掉「背景」；「启用渐变」搬到「背景色块」栏，放在背景类型旁边；选色块背景时才可用 | 截图；`fx.bg` 渐变字段是 T11g-B 已有的，**只动 UI 不动 schema** |
| **QW-04** | 历史记录面板每条显示 `spec.label`（命令的中文名）+ 时间；深度从当前值 ×2 | 截图；一条测试断言历史条目带 label |
| **QW-05** | 文本属性：「…」→ 带字按钮「预设 ▾」；菜单里「更新当前预设」→「**重新保存当前预设**」（红色）；保存写入**文本层全集** | 锁：`set(preset.keys()) == TEXT_LAYER_CANONICAL_KEYS`（照 T11b 等号锁）；截图 |
| **QW-06** | 字幕条改低饱和色（建议：主题金的 20% 饱和度版本，或 `#3d4a5c` 一类的冷灰蓝）；删轨道上下箭头；**鼠标拖拽轨头排轨**；**轨道顺序 = 图层顺序**（上面的盖下面的） | 截图；一条测试：拖轨后 `compute_render_order` 顺序随之变 |
| **QW-07** | 自动署名重做：选一个**已在 text 轨上的署名层** → 一键复制到队列里每个视频的 text 轨（同位置同样式）。不走工程队列。原「工程队列自动署名」保留但从菜单里降级为「配方」 | 多视频测试：每条视频 text 轨多出一层，参数逐字节等于源层 |

**QW-06 的颜色**：不要纯黑（老规矩），不要粉。截图给 Luca 看，他有意见会说。

---

## 3. T10 任务书 · 打包分发（**并行线**，不碰 UI 文件）

Luca 已拍板**软件开源 GPL**。T10 摸底：内测 zip 能打，对外安装包不存在，无 CI，FFmpeg 是 GPL full_build。

### T10a · 许可落地（第一块，其余都靠它）

- 仓库根加 `LICENSE`（GPL-3.0-or-later）；每个源文件头加 SPDX 行
- **`THIRD_PARTY.md`：逐项列出全部捆绑物的名称 / 版本 / 许可 / 来源链接**，
  并逐项标 **「GPL 兼容：是/否」**：
  FFmpeg gyan full_build（GPL，兼容）· PySide6（LGPL，兼容）· faster-whisper / CTranslate2 · numpy / opencv ·
  Silero VAD · 每个 ASR / 对齐模型（**模型许可逐个查，不许「应该是 MIT」**）· 240 款字体（OFL/Apache，字体非代码但也列）·
  `runtime/python`（PSF）· Inno Setup（如用）
- **任何一项不兼容 → 停下来报我**，那是要换组件的
- 安装器和 About 对话框里有「源码获取方式」（GPL 要求）
- 测试：`test_third_party_manifest_complete` —— 打包产物里每个 `.dll/.pyd/.exe/.onnx/.bin/.ttf` 都在清单里

### T10b · 安装器 + 便携包

- **Inno Setup** 出 `CUTORA-<ver>-Setup.exe`（它本身许可宽松，用来打 GPL 软件没问题）
- 同时出 `CUTORA-<ver>-portable.zip`
- 两者都附 `SHA256SUMS`
- **安装器不带模型**（9.29 GB），模型走 T10c；另出一个 `-full` 变体带全部模型（内测用，Luca 说过体积不是问题）
- 版本号单一来源：`cutora/__version__.py`，打包脚本读它，不许手填

### T10c · 首启向导

- `cutora/asr/download.py` 已有下载器；上面套一个**首启向导**：选语言 → 选模型档 → 下载（进度 / 断点续传 / **sha256 校验**）
- 离线路径：向导里可以「从本地文件夹导入模型」（内测 full 包就是这条）
- 模型来源：只从**我们自己托管或原作者官方**的地址拉，**不得指向中国境内服务器**（硬约束）
- 「检查更新」这轮不做

### T10d · CI

- `.github/workflows/ci.yml`：`pytest tests/`（CPU）+ `test_no_third_party_names` + `test_third_party_manifest_complete`
- **golden 能不能在 GitHub 的 CPU runner 上跑**：先回答 —— golden 用的编码器是 libx264（CPU）还是 NVENC？
  是 CPU → 跑，慢就慢；是 NVENC → 只能自托管 runner（Luca 的机器）或 CI 里跳过 golden 并**明确标注**
- 打包门禁 `PACK_GATE` 0–14 全部进 CI 的 release job

### 顺序
`T10a`（必须第一）→ `T10b` → `T10c` → `T10d`。T10a 交上来我先签。
**T10 和 QW 不碰同一个文件**，可以并行；碰了按老规矩合并后跑两边全部测试。

---

## 4. 之后

```
QW + BUG-02b 交 → Luca 复测 → T21 画布尺寸 → T20 时间线拼接（Phase 2 主线）
T10a…d 并行
```

T21 / T20 的任务书我在 QW 期间写。
