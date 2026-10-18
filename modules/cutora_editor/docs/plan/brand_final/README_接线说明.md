# T02 附录 D — 品牌定案：接线 + 清场

> Luca 已定案。**设计部分已经全部完成，不要再改任何视觉**；本任务只做两件事：**清掉全部旧的品牌尝试**，**把定稿资产接进软件**。
> 在 main 上开 `task/T02-brand-final`。

## 一、定案内容

- **标志**：Luca 提供的金色双形标（左＝引号/话语，右＝播放三角，中间共用一道切口）。已完成抠底、重做为全尺寸图标资产。
- **标语**：`让每一句话，找到它的位置。` / `Every word finds its place.`
- **配色**：沿用已合入的 Roma（`cutora/ui/theme/tokens.py`），不动。
- **来源与许可**：`resources/brand/source/mark_source.png` 是 Luca 本人用外部 AI 绘图工具生成的原始图，版权归他本人；`resources/brand/` 下的所有派生资产由 `tools/brand/` 的脚本从它生成。**在 `resources/THIRD_PARTY_LICENSES.md` 里如实登记这一条**（来源：所有者自制，非第三方素材）。

## 二、清场（先做）

删除以下全部文件，一个不留：

- `docs/plan/reports/img/` 下所有 `t02_concept_*`、`t02_splash*`、`t02_about*`、`t02b_*`、`t02_logo*` 等历次尝试图
- `resources/brand/` 下 T02 / T02-brand2 生成的全部旧图标、splash、色卡
- `tools/render_brand.py` 及其产物（被 `tools/brand/` 取代）
- 分支 `task/T02-logo2`、`task/T02-brand2` 上遗留的品牌图（合并时不要带进来）
- `docs/plan/reports/nondeterminism/`（早就该删的残留）

`docs/plan/reports/T02_report.md` 与 `T02_brand2_report.md` 的**文字**保留（是过程记录），但把其中引用的图片链接改为指向定稿资产或删掉。

## 三、接线

把 `docs/plan/brand_final/assets/` 的内容放到 `resources/brand/`，脚本放到 `tools/brand/`，原始图放到 `resources/brand/source/`。然后：

| 用途 | 资产 |
|---|---|
| 窗口 / 任务栏图标 | `resources/brand/app.ico`（内含 256/128/64/48/32/16） |
| `QApplication.setWindowIcon` | `icon_256.png`（或直接用 .ico） |
| 启动画面 | `splash_bg.png`（**不含**进度文字与进度条，运行时自己画，见 §四） |
| 关于对话框 | `mark_1024.png`（透明底，按需缩放） |
| 浅色场合 / 文档 | `icon_512_light.png` |
| 单色 | `icon_mono_512.png` |
| 桌面快捷方式 / 安装包（T10） | `app.ico` |

`.cutora` 文件关联图标先沿用 `app.ico`，T10 再做专门的文档图标。

## 四、Splash 运行时绘制规范（1280×720）

背景用 `splash_bg.png` 原样铺满，然后**只画这两样**：

- 进度文字：左上角 `(132, 632)`，18px，颜色 `#B0A392`，左对齐（例：`加载字体…`）
- 进度槽：`(132, 668)` 到 `(1148, 675)`，圆角 4，槽色 `#302921`，填充色 `#CCA650`

其余文字（片语 Studio / CUTORA / 标语 / 版本号）已经烤进背景图，不要重画。**版本号变化时重新生成背景图**：`python tools/brand/splash2.py`（脚本里 `v7.0.0` 一处改即可）。

## 五、验收

- [ ] 启动：splash 显示定稿背景，进度文字与进度条位置正确；主窗口标题栏、任务栏、Alt-Tab 都是新图标
- [ ] 16px 与 32px 下图标清晰可辨（截图贴报告）
- [ ] 关于对话框显示新标志与新标语
- [ ] 全仓库检索 T02 原文那句中英直译品牌语，命中为零
- [ ] 清场清单逐条确认已删除（报告里列出删除的文件数）
- [ ] `pytest` 全绿；`golden --all --repeat 3` PASS；`security_scan` 业务代码栏 0；`tests/test_startup_order.py` 仍通过
- [ ] 报告 `docs/plan/reports/T02_final_report.md`

全绿后合并 main，打 `T02-done-final`。**然后 T02 才算真正结束，下一个是 T03。**
