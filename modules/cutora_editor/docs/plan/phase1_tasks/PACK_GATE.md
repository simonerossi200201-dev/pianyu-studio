# 出包门禁（两级）

应急包 `v7.0.0-beta1` 在 pytest 22 红时放行，而那 22 条就是「主窗口起不来」。
以后 **pytest 红着不许出包**，也没有「并行修」。

## 两级

| | 内测包（自用 / 可信内部人员） | 对外分发（商用 / 第三方） |
|---|---|---|
| 门禁 | **0–7** | **0–14** |
| 字体全语种覆盖 | 不要求 | **要求** |
| 零豆腐 / 绝不碰系统字体测试 | 不要求 | **要求** |
| 包名与 README | 必须标 `internal` | — |

内测包可以在 T04L 字体承诺完成前打。对外分发必须 0–14 全绿。

## 0–7（每次出包都走）

0. `pytest` **0 failed**：`tools/gate0.py`（2026-10-11 Luca 批准，Q262）。
   每个测试文件一个进程；每个文件都必须有一条**在当前内容上**跑绿的记录。
   钥匙 = 产品代码指纹（`cutora/ resources/ tools/ vendor/`、`tests/` 里的非 `test_*.py`、根目录文件）
   + 运行环境指纹（解释器、`requirements.lock`、`.cutora_ready`、`runtime/` 下 ffmpeg / 模型 / Python 的大小和时间戳）
   + 测试文件本身；读文档的测试再加 `docs/**/*.md` 和根目录 `*.md`。
   任何一个字节变了就重跑；没变且绿过的不重跑。第一次红的单独再跑一次，
   第二次绿记为「重跑后过」并在 `_gate0.log` 里单列；第二次还红就是红。
   不再要求一口气 `pytest tests` 整套同进程跑绿。
1. **把 app 真的启动一次**（截图 + 同一次运行产出的 `*_evidence.txt`）
2. **再点一遍**：新建项目 / 打开字幕样式面板 / 停靠+浮动一次面板 / 切一次主题
   （`apply_dock_style` 崩过，停靠必须点到）
3. `git rev-parse HEAD`；`git status --porcelain -- tests tools`
4. PySide6 版本；`cutora/` 与 `tools/` 不得有 PyQt6
5. `golden --all --repeat 3` + `--isolate` 全绿，哈希与上一版逐个相同
6. 许可包齐全：`THIRD_PARTY_LICENSES.md` / `licenses/` 169 /
   字体 count=240 digest `80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`
   （T04L 加字体后本条的 count/digest 以 `RERECORD_NOTES.md` 为准，golden 哈希仍必须不动）
7. 打 tag

第 0、1、2 条不许跳。

## 截图证据协议（T04i-1 门禁漏修）

不可证伪的证据不是证据。固定离屏欢迎页可以每次逐字节相同，所以单张 PNG
无法证明「这次真的跑了」。

1. 每张门禁截图由**同一次运行**产出配套 `*_evidence.txt`，三者写在一起提交：
   - `HEAD`（`git rev-parse HEAD`）
   - `captured_at`（ISO-8601 UTC）
   - 该 PNG 的 `sha256`
2. **本该不同的两张截图，脚本必须断言它们确实不同。**
   主题前后、浮动/停靠前后 —— 相同就是这一步没生效，直接红。
3. 主题那一步必须前后各一张（`_theme_before` / `_theme_after`），并断言不同。
   主题 id 必须是 `BUILTIN_THEME_IDS` 里真实存在的（没有 `obsidian`；
   未知 id 会静默落到 `roma-gold`，截图就会和切之前逐字节相同）。

脚本：`tools/t04i1_boot_clicks.py`。

## 8–14（仅对外分发）

T04L 全语种字体承诺完成前，**内测包不走这些**。

```
8)  字体全量覆盖：font_script_coverage.json 与磁盘一致
9)  零豆腐测试通过（全部 100 语种）
10) 绝不碰系统字体测试通过
11) 所有新字体在 git 里、在 MANIFEST 里、在 PROVENANCE 里、在 THIRD_PARTY 里 —— 四处齐全
12) golden 全部哈希未变（加字体是渲染中性的）
13) fonts_digest.txt 已更新，RERECORD_NOTES 记了原因
14) 报告包体积增量
15) runtime/ffmpeg 是 gyan.dev **full_build**（含 x264/x265，**GPL**）。
    内测不管。对外商用分发前：要么换 **LGPL 构建**（H.264 走 NVENC/openh264），
    要么按 GPL 义务附源码获取声明。
```

L1 只出覆盖矩阵，不加字体。L2 先报清单 + 体积，批准后再下载。
任何非 Google Fonts 来源的字体，先报，不要自行加入。
