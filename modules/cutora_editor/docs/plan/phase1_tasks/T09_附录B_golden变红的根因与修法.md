# T09 附录 B — golden 变红的根因（已定位）与修法

> 与 `T09_附录_开工状态.md` 一起贴。**先做本附录，再回去收 T09 剩下的部分。**

## 一、根因：不是代码，是 `user_data/app_settings.json`

Claude 已在仓库里定位，不用再二分查找：

`cutora/render/render_worker.py:789`
```python
fx_settings = project.get("audio_fx") if isinstance(project.get("audio_fx"), dict) else None
if fx_settings is None and _SFX is not None:
    fx_settings = _SFX.load_fx_settings()      # ← 读 user_data/app_settings.json
```
**七个 golden 工程的 JSON 里都没有 `audio_fx` 键**（已逐个确认：g01/g02/g03 顶层键只有 version / created_at / app / export_video_mode / tasks / curr_idx / ui / headless）。于是渲染每次都回落去读 `user_data/app_settings.json` —— 这个文件 **`.gitignore` 里，不受版本控制，是本机状态**。

而它现在的值是：
```
"spatial_wet":  0.20000000000000007     (T00 生成基线时是 0.1)
"mic_amount":   0.30000000000000004     (T00 生成基线时是 0.2)
"mic_texture":  true
```
那两个浮点尾巴（0.1+0.1、0.1+0.2）是 **QDoubleSpinBox 被点了一下** 留下的签名。时间对得上：Luca 做 GUI 冒烟第 14 条「音效对话框：打开、切换空间立体声预设、试听」时动了滑块。

`mic_amount` 作用在主音轨上，所以**每个 case 的音频都变了 → 每个哈希都变了**。这就是为什么把 `cutora/` 整棵检回 T01-done、包版本回退到 T00 都没用：**问题在数据，不在代码。**

**结论：`--repeat 3` 稳定复现同一个错哈希，不是非确定性；CVE 升级和 T09 代码都是无辜的。**

## 二、修法（两步，顺序不能反）

### 第 1 步 · 证明（5 分钟）
把 `user_data/app_settings.json` 里这两个值改回去：
```
"spatial_wet": 0.1
"mic_amount":  0.2
```
（`mic_texture` 保持 `true`，其余不动）然后 `python tools\golden_check.py --case g01_vertical_basic`。
- **绿了** → 根因确认，进第 2 步。
- **还是红** → 停下，把 `git stash list`、`pip freeze` 与 `build/freeze_from_v6.txt` 的 diff、以及 `docs/plan/reports/nondeterminism/` 里的产物一起写进报告交 Claude，不要自己继续猜。

### 第 2 步 · 让 golden 不再依赖本机状态（这才是真正要修的）
`tools/golden_check.py` 与 `tools/golden_gen.py` 在跑之前，把用户数据目录指向一份**受版本控制的固定夹具**：

- 新建 `tests/golden/_fixture_user_data/app_settings.json`（**要 `git add`，在 `.gitignore` 里加例外 `!tests/golden/_fixture_user_data/`），内容 = T00 生成基线时的那份设置：`spatial_wet 0.1`、`mic_amount 0.2`、`mic_texture true`、`export_video_mode compressed`、`parallel_export_workers 6`、`spatial_enabled true`、全部 `sfx_enabled false`、`ai_enabled false`，密钥字段一律删掉（T09 的新规矩）。
- `golden_check.py` 启动时把 `cutora.core.paths.USER_DATA`（以及子进程要用的环境变量）临时指到这个夹具目录，跑完恢复。子进程（`--render-job`、g06 并行、g07 CLI）**也必须继承**，否则并行 case 又会读回本机设置。
- 在 `tests/golden/README.md` 里写明：golden 只允许依赖仓库内的文件；任何读 `user_data/` 的路径都必须在对拍时被夹具接管。

**验收**：故意把本机 `user_data/app_settings.json` 的 `mic_amount` 改成 `0.9`，`golden_check.py --all` 仍然 PASS。这条写进报告。

## 三、Grok 三个问题的答复

1. **重录基线？不行。** 重录会把这个真实存在的环境依赖永久掩盖掉，而基线是整个改版唯一的安全网。按 §二 修，基线一个字节都不改。
2. **`t09_fonts_extra/` 不注册 = 不算「已加入」。** 但这不是缺陷，是正确的谨慎：注册新字体会改变字体回退，可能改变渲染。所以 **T09 只做审计与备料，不做启用**：把目录改名为 `resources/fonts_pending/`，每个家族附许可证文件，写进 `FONTS_MANIFEST.json` 并标 `status: "staged, not registered"`。**真正启用放到 T12**（多语种本来就要建新的 golden case，那时启用才有对拍兜底）。
3. **`raw.githubusercontent.com`：允许，但只在开发/构建期，不进产品运行期白名单。** 它是 GitHub 的原始文件服务，取 `google/fonts` 的字体与许可证走它是合理的。规则：写进 `docs/` 的**开发期允许清单**并在 `FONTS_MANIFEST.json` 里记录每个文件的 URL + SHA256；`cutora/core/net.py` 的运行期白名单**不加**（产品运行时不需要它）。

## 四、golden 绿了之后，回去收 T09 剩下的

- **重做 CVE 升级**：pillow / requests / transformers / nltk / lightning 升到修复版，`--require-hashes` 重新生成锁文件，跑 `golden --all --repeat 3` + `pytest`。之前回退是因为 golden 红，而那个红与升级无关——现在要重新判一次。仍然升不动的（lightning / nltk 无修复号）逐条写明。
- **`test_asr_smoke` 本会话没跑完**：必须跑通，它是 T01 留下的关键防线。
- 附录 A（`T09_附录_开工状态.md`）里的其余转派事项照旧。

## 五、报告要补的一条
在 `T09_report.md` 里单开一节「golden 环境依赖事故」，写清：现象、根因、为什么代码回退无效、夹具修法、以及那条「改本机设置 golden 仍绿」的验证。这是本次改版第一起"数据污染测试"的事故，值得留档。
