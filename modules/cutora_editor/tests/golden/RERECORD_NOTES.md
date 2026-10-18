# Q57 g19b-2_stitch_crossfade — 2026-09-20

**Why**: 这条基线录的是转场发黑本身。五个片段四个 `in_t=0.0`，交叉溶解要入点之前的帧，
`_get_video_frame_at_time` 范围外 `return None`，离屏 `fill(0)` 叠到黑画布 = 半透明半黑。
Q55 冻结补帧之后哈希必然变。Luca 2026-09-20 当面批准**只重录这一例**，必须留完整证据。

证据：`docs/plan/reports/Q57_g19b2/`（三联图、亮度/alpha 曲线、左右分屏 `Q57_before_after.mp4`、数字表）。
曲线上修复前四条接缝平均亮度从 ~130 掉到 ~60，修复后不再下陷；视频层 alpha 110–139 → 255。

Fingerprint: fonts 240 / `80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`
· ffmpeg `2026-01-07-git-af6a1dd0b2` · cv2 4.9.0 · numpy 1.26.4 · PySide6 6.10.2
（与 2026-09-19 十二例重录逐项相同。）

| case | 旧（发黑基线） | 新（Q55 冻结补帧） |
|---|---|---|
| g19b-2_stitch_crossfade | `e0048cebe258` | `802a86a1d2c7` |

其余十一例未碰。`g16_parallel_fx` 仍归 Q47。
永久保险：`tests/test_q57_g19b2_no_black.py::test_q57_g19b2_has_no_black_frames`
（转场窗口视频层 alpha=255，编码后窗口平均亮度 ≥ 100；哈希只说「和那天一样」，这条说「没有发黑」。）

---

# 全部十二例重录 — 解码器一致性 — 2026-09-19

**Why**: 修 BUG-42（导出用硬件解码，D3D11VA 表面锁失败 `887a0005` =
DXGI_ERROR_DEVICE_REMOVED，显卡驱动被重置 → Luca 两次黑屏 + 6 个花屏文件中的 5 个）
时，导出解码改成软件解码。之后 golden 十二例**全部**哈希漂移。

排查顺序与结论：
1. 字体排除 —— 本次指纹 `240 / 80429c09…` 与 `fonts_digest.txt` 逐字符相同。
2. 解码池排除 —— g08/g10–g13/g17/g18 是单源、池里只有一路永不淘汰，照样漂。
3. A/B 实锤 —— `CUTORA_EXPORT_HW_DECODE=1` 跑 golden **exit 0**，软件解码跑 **exit 1**。
4. **证明谁对**（`tools/decode_proof.py`，与 ffmpeg CLI 解出的参考帧比）：

| 源 | 帧 | 软件解码 (cv2 NONE) | 硬件解码 (cv2 ANY) |
|---|---|---|---|
| g01 | 0 / 30 / 90 / 150 | MAD **0.000** max **0** 差异 **0.0%** | MAD 0.851–0.891 max 79–97 差异 40.0–40.4% |
| g02 | 0 / 30 / 90 / 150 | MAD **0.000** max **0** 差异 **0.0%** | MAD 0.851–0.891 max 79–97 差异 40.0–40.4% |
| g03 | 0 / 30 / 90 / 150 | MAD **0.000** max **0** 差异 **0.0%** | MAD 1.218–1.287 max **255** 差异 40.2–40.4% |

H.264 按规范逐比特确定。软件解码与参考**逐字节相同**；硬件解码在 **40% 的像素**上偏离，
g03 最大偏差达 **255**（整像素错误，正是花屏的同一族）。
三个源两种模式都落在 `T1/T2 cv2`，不是分层差异。

**结论：旧的十二例基线是用一条偏离规范的解码路录出来的，本来就是错的。**
不是「新结果变了要不要认」，是旧的一直错。Luca 2026-09-19 签字：就地重录 + 完整留痕。

**永久保险**：`tests/test_decoder_conformance.py` —— 锁「软件解码 == ffmpeg 参考（max 差 0）」
和「导出路径默认软件解码」。谁再把硬件解码放回导出路径都会当场红。
`CUTORA_EXPORT_HW_DECODE` 只是诊断开关，默认关，也被测试锁死。

Fingerprint: HEAD=`0fff391` · fonts 240 / `80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`
· cv2 4.9.0 · PySide6 6.10.2 · numpy 1.26.4

| case | 旧（硬解，已作废） | 新（软解） |
|---|---|---|
| g08_fx_static | `24b8953560f0` | `0c300e9b0656` |
| g10_fx_anim | `25008c0f8b5c` | `c9fc5e20e51d` |
| g11_vertical_basic_fx | `b91bb3555a4a` | `e92bb37279c9` |
| g12_vertical_fx_path | `04f3fc72ac0c` | `1b47c4d43e9c` |
| g13_horizontal_it_fx | `6aa9c911916f` | `237c6e00f384` |
| g14_stitch_fx _00/_01/_02 | `940c4133ad68` / `56cac19e1745` / `4b5d6876402e` | `08aaeb33ce8b` / `0c7351bd3897` / `29e8261b2226` |
| g15_scatter_insert_fx | `f2c6485b5747` | `711a21721946` |
| g16_parallel_fx _00/_01/_02 | `b91bb3555a4a` / `04f3fc72ac0c` / `33a14f2f8033` | `e92bb37279c9` / `1b47c4d43e9c` / `8eb46a915c9e` |
| g17_cli_fx | `b91bb3555a4a` | `e92bb37279c9` |
| g18_linebreak_latin_fx | `a3e209006484` | `12e9d5ffaa42` |
| g19-2_stitch_timeline | `5b939df6dc4f` | `752f19ff8a70` |
| g19b-2_stitch_crossfade | `e1ad0d67b3b7` | `e0048cebe258` |

g16 的 `parallel==serial` 在漂移期间始终 PASS —— 渲染本身是自洽的，变的只是解码。

---

# 十报 g19-2 / g19b-2 — 2026-09-17

Why: export seek `CAP_PROP_POS_MSEC` was one frame off vs
`timeline_frame_index` / `CAP_PROP_POS_FRAMES`. Clip occupancy is now
`[start, start+eff)`. Audio bitstream is unchanged (wav md5).
g08 / g10–g18 not re-recorded (byte-identical). g19 / g19b retired
on disk.

Fingerprint: HEAD=404f03b worktree · fonts 240 /
`80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`

| case | sha256 prefix |
|---|---|
| g19-2_stitch_timeline | `5b939df6dc4f` |
| g19b-2_stitch_crossfade | `e1ad0d67b3b7` |

---

# T20c g19b_stitch_crossfade record — 2026-09-16

Add-then-retire. Product tree: T20c transition library (`24a79fd`).
Fonts 240 / `80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`.
One process: `tools/_diag/t11g_record_one.py g19b_stitch_crossfade`.
`asr_frozen`. g19 + cross_dissolve 0.35s on every seam (4 joins).
Existing live eleven (g08, g10–g19) were not re-recorded.

| case | sha256 prefix |
|---|---|
| g19b_stitch_crossfade | `031f2f3ba8fc` |

---

# T20a g19_stitch_timeline record — 2026-09-16

Product tree: T20a timeline stitch (this commit). Fonts 240 /
`80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`.
One process: `tools/_diag/t20a_record_g19.py`. `asr_frozen`. No subtitles.

| case | sha256 prefix |
|---|---|
| g19_stitch_timeline | `219b3dd09e2a` |

T0035's 27 clips copied into `tests/golden/_media/g19_stitch_timeline` (gitignored).
Hash gate is 5 clips / 29.26 s so `--all` does not grow by three minutes.
Existing live ten (g08, g10–g18) were not re-recorded.

---

# T11g-B g10 + g11–g18 record — 2026-09-15

Product commit (record HEAD): `9afa64c5da661ee822fee4169e42dd0adcfbd7e9`
Fingerprint at g10 record: `source_dirty=no` fonts_count=240
digest `80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`
One process per case: `tools/_diag/t11g_record_one.py`.

g10 authorized (karaoke tint 69 frames + warp_layer_xf fade 9 frames, unknown=0).
g11–g18 were drafts until this record (signed look, not the pre-2a hashes).

| case | sha256 prefix |
|---|---|
| g10_fx_anim | `25008c0f8b5c` |
| g11_vertical_basic_fx | `b91bb3555a4a` |
| g12_vertical_fx_path | `04f3fc72ac0c` |
| g13_horizontal_it_fx | `6aa9c911916f` |
| g14_stitch_fx | `940c4133ad68` / `56cac19e1745` / `4b5d6876402e` |
| g15_scatter_insert_fx | `f2c6485b5747` |
| g16_parallel_fx | `b91bb3555a4a` / `04f3fc72ac0c` / `33a14f2f8033` |
| g17_cli_fx | `b91bb3555a4a` |
| g18_linebreak_latin_fx | `a3e209006484` |

g08_fx_static unchanged (`24b8953560f0`). After this record, g11–g18 are protected baselines (Rule A).

---

# T11g-B v1 path retired — 2026-09-15（退役，不是失败，不是重录）

`--all` 不再跑 PATH v1 用例。哈希文件留在盘上。

g01  退役原因：v1 draw_subtitle 路径删除（T11g-B）
     退役时哈希：`af4f9223eec8fcb3b8fa5a9f3fde1fb5fc689ad80bcc01aa307b9b4cd45db65a`  expected.mp4
     接替者：g11_vertical_basic_fx（PATH fx 镜像，见 T11g-1 镜像完整性对照表）

g02  退役原因：v1 draw_subtitle 路径删除（T11g-B）
     退役时哈希：`e7d48cb0bfcc905fce0ec0a46ca84eaf50dbeebf4694280d2d8d1488edeb625f`  expected.mp4
     接替者：g12_vertical_fx_path

g03  退役原因：v1 draw_subtitle 路径删除（T11g-B）
     退役时哈希：`21c46f1917b1e665a27fdf0c03c25cc1a87ca2258b20d00ff7d79bc1d2ce1806`  expected.mp4
     接替者：g13_horizontal_it_fx

g04  退役原因：v1 draw_subtitle 路径删除（T11g-B）
     退役时哈希：`a5f96c14610fbc092de713143ca95272fe90e102ab3ed2b8ff3f2b2be6247e27`  expected_00.mp4
                  `35ea7086719d7a820734548ca1bec1b7ae19f4e2c9f918f85b7510468198f8f8`  expected_01.mp4
                  `0c96dfd025e22a8f9f638dea0227e521eae3c91db16d8bf7582bca92ec136596`  expected_02.mp4
     接替者：g14_stitch_fx

g05  退役原因：v1 draw_subtitle 路径删除（T11g-B）
     退役时哈希：`bdeba017a5e2a8f8cd1507423b96c75aeec17c5d698a53fece263a44f539aeb8`  expected.mp4
     接替者：g15_scatter_insert_fx

g06  退役原因：v1 draw_subtitle 路径删除（T11g-B）
     退役时哈希：`af4f9223eec8fcb3b8fa5a9f3fde1fb5fc689ad80bcc01aa307b9b4cd45db65a`  expected_00.mp4
                  `e7d48cb0bfcc905fce0ec0a46ca84eaf50dbeebf4694280d2d8d1488edeb625f`  expected_01.mp4
                  `8d0c0f23ca5690ec81c64e8d15a5319e6e6a5250561c872e48daa5dc5d9bfd71`  expected_02.mp4
     接替者：g16_parallel_fx

g07  退役原因：v1 draw_subtitle 路径删除（T11g-B）
     退役时哈希：`af4f9223eec8fcb3b8fa5a9f3fde1fb5fc689ad80bcc01aa307b9b4cd45db65a`  expected.mp4
     接替者：g17_cli_fx

g09  退役原因：v1 draw_subtitle 路径删除（T11g-B）
     退役时哈希：`1a60d144d5179eef79a772979d5c73264a482d7bc08dc97f2881e78550002aa4`  expected.mp4
     接替者：g18_linebreak_latin_fx

g08 / g10 / g11–g18 留在 `--all`。

---

# T11g-1 g11–g18 fx-path mirrors — 2026-09-15 (new cases, not a rerecord)

Product code unchanged (`cutora/` = `599013d`). New fixtures only.
Record: one process per case, `tools/_diag/t11g_record_one.py`.
fonts_count=240, digest `80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`.

g01–g10 hashes must not move. g11–g18 are **new** baselines (Rule A: add, don't rewrite).

| new | mirrors | sha256 prefix |
|---|---|---|
| g11_vertical_basic_fx | g01 | `feddc0925a88` |
| g12_vertical_fx_path | g02 | `d0a4bdf341ea` |
| g13_horizontal_it_fx | g03 | `b20ef0a415e8` |
| g14_stitch_fx | g04 | `9c01049d6a0d` / `9986fa6bccb8` / `fee7799b36b6` |
| g15_scatter_insert_fx | g05 | `ed7b5ee6744c` |
| g16_parallel_fx | g06 | `feddc0925a88` / `d0a4bdf341ea` / `70596639e386` |
| g17_cli_fx | g07 | `feddc0925a88` |
| g18_linebreak_latin_fx | g09 | `25aeaa410e8e` |

`style.fx` injected (fill from `color_text`, `bg.type=none`) so `draw_subtitle` takes PATH fx. v1 keys otherwise identical.

---

# T06-2 g09_linebreak_latin — 2026-09-15 (authorized rerecord 2)

Authorized by `docs/plan/reports/T06-2_g09签字2_授权重录_by_Claude.md`.
HEAD at record: `e17ebde`. One process: `tools/_diag/t06_record_g09.py`.
Fingerprint: source_dirty=no, fonts_count=240, digest
`80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`.

## Why

max_lines=2 last-line pile: width-pack (`horizontalAdvance`, min max-width),
safe-zone cap only when a line already overflows wrap, shrink to ≥ 0.85,
then prefix-fill + visible `…`. Draw uses fitted tokens when truncated.

Three defects closed:

1. Word-count split (spec proxy) → width overflow.
2. Loop exited on line count → 15% floor and `…` never ran.
   At 30 px / cap 687 the 25-word cue is ~1300 px/line, so dropping
   words with `…` is correct.
3. Per-word paint used original `words[i]` after truncate → `…` discarded.

## Hashes

| case | file | before | after |
|---|---|---|---|
| g09_linebreak_latin | expected.mp4 | `bb92e999bd3f` | `1a60d144d517` |

Full: `bb92e999bd3f39f2746e4d5d932212ccae53a31512b4712452190e4783c00705`
→ `1a60d144d5179eef79a772979d5c73264a482d7bc08dc97f2881e78550002aa4`

g01–g08 and g10 **must not change**.

## Per-segment vs old baseline (measured)

- seg0 / seg1 / seg2: **byte-identical** PNG md5 (cues before the change).
- seg3: target change (overflow fix + visible `and…`).
- seg4 / seg5: **layout bbox identical** (all four edges unmoved);
  PNG md5 differs by x264 inter-frame residual after the changed seg3
  (ink Δ 0.018% / 0.009%, Δ>24 on 0.062% / 0.132% of pixels).
  Do not treat that md5 mismatch as a layout regression.

---

# T04L L2 fonts — 2026-09-14


Added 25 Noto (and Noto Serif Tibetan) files from `google/fonts@809e4d8b8d7e`.
New families only; no existing font file replaced.
`fonts_digest` count 215 → **240**, digest
`80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f`.
**All golden hashes must remain unchanged** (render-neutral add).

---

# Golden re-record — 2026-09-13

This is the only T04f re-record of g01–g07 and g09. g08_fx_static and
g10_fx_anim were first recorded 2026-09-14 (same fonts_digest).

## Why

The previous baselines were recorded on a machine whose `resources/fonts/` directory
contained **197 extra font files that were never in git**. Those files had no
manifest entry, no THIRD_PARTY listing, and no deletion commit; their licenses
cannot be identified and the files no longer exist. They must not be copied back.

The written record of what this product ships has always been **234** font files
in `FONTS_MANIFEST.json` / `THIRD_PARTY_LICENSES.md` / the T09 report
(216 registered + 18 pending). **There was never a 412.** `[FONT] Loaded 412`
was the untracked extras. The loaded set today is **215** files (Noto Color Emoji
is on disk but excluded from the subtitle scan).

Fixture strings `"font_family": "SN Pro SemiBold"` are a **family+weight**
legacy spelling. After the extras disappeared, that name no longer registered as
a family, and export **silently fell back to Arimo** (`g01` = `85825f00ff17`).
Parser 2.2 splits that spelling into family `SN Pro` + style `SemiBold`.
Scan-line proof: resolved stem **6 px** / ink **3143**, identical to an explicit
`setStyleName("SemiBold")` render, +24% ink vs Regular.

**Known consequence:** font metrics changed. Re-running `break_lines` on a project
can move wrap points (confirmed for g09 seg1/2/3). Export uses stored `lines`,
so already-exported videos are unaffected. New or re-laid-out subtitles may wrap
differently than before.

Control (g01, one character changed, frame 521, upper 55%): mean Δ **0**, max Δ **0**,
flat-region Δ>24 **0** — encoder residual from a subtitle-only edit stays in range.

## Hash table (prefix 12)

| case | file | before | after |
|---|---|---|---|
| g01_vertical_basic | expected.mp4 | `2fea55c62e09` | `af4f9223eec8` |
| g02_vertical_fx | expected.mp4 | `6daf5fca4ef5` | `e7d48cb0bfcc` |
| g03_horizontal_it | expected.mp4 | `8cfb622c5d5e` | `21c46f1917b1` |
| g04_stitch | expected_00.mp4 | `6b0c9ba42a47` | `a5f96c14610f` |
| g04_stitch | expected_01.mp4 | `d887fadcc9bf` | `35ea7086719d` |
| g04_stitch | expected_02.mp4 | `bfb77360cfae` | `0c96dfd025e2` |
| g05_scatter_insert | expected.mp4 | `20b822236873` | `bdeba017a5e2` |
| g06_parallel | expected_00.mp4 | `2fea55c62e09` | `af4f9223eec8` |
| g06_parallel | expected_01.mp4 | `6daf5fca4ef5` | `e7d48cb0bfcc` |
| g06_parallel | expected_02.mp4 | `68ff9e0de4a4` | `8d0c0f23ca56` |
| g07_cli | expected.mp4 | `2fea55c62e09` | `af4f9223eec8` |
| g09_linebreak_latin | expected.mp4 | `4fbf421f08b6` | `bb92e999bd3f` |

Full sha256 values are in each case's `expected.sha256`.

## fonts_digest for this re-record

```
count=215
f7a704636d8deb384ff68b9a8c9f718734de705d7d2debda569e442a3c0e9fab
```

Pinned in `tests/golden/fonts_digest.txt`. A later drift prints
`FONT SET CHANGED` on the golden fingerprint screen.

## Recording discipline

One case per process. `--isolate` is a release gate: solo hash must equal
the hash of the same case in `--all` order. `--repeat 3` does not catch
order pollution. Before a record, read `git status --porcelain -- tests tools`
as well as `source_dirty` (the latter only watches `tests/golden/` plus a
few other prefixes). See `tests/golden/README.md`.

## g08 / g10 — first record 2026-09-14, g08 then g10 re-records after QFont leak

Same `fonts_digest` as the g01–g07/g09 re-record. g08 stills signed off in
`T04f-6b2_冷暖光栅修复验收_by_Claude.md`. g10 first write was polluted;
re-record is T04f-6b3.

g08 first write `64089f8097c0` / polluted-warm `77d9b7899ca9` were not a Qt
glyph/ramp cold start. `FontManager.get_font` handed out a cached `QFont`;
`layout._qfont` did `setLetterSpacing` on that instance when `fx_fonts`
used `tracking_em=0.08`. Later roles — and the next export — inherited
that spacing. Fix `78e010b`: `get_font` returns `QFont(cached)`; `_qfont`
sets letterSpacing every time. Re-record `77d9b7899ca9` → `24b8953560f0`.

g10 first write `147f58ee29d4` was recorded by `tools/t04f6b2_record_g08_g10.py`
in the **same process** after g08. g08 `fx_fonts` `tracking_em: 0.08`
polluted the FontManager key SN Pro / Bold / 124px
(`fontsize_pct` 0.064815 × 1920 = 124.44 px; 0.08 em ≈ 9.96 px per gap).
Seven of g10's eight role styles share that key. **E0:** on `d5ad96d`
itself (old code: no QFont copy, no PercentageSpacing else, no
`clear_roi_cache`) a **solo** g10 export already produced `e22b02c35fc9`,
not `147f58ee29d4`. So `78e010b` is pixel-neutral for g10; this re-record
corrects the baseline, not the renderer.

Commits: `78e010b` (fix) → `84b2514` (g08 re-record) → this g10 re-record.

| case | file | sha256 prefix 12 | export wall-clock | notes |
|---|---|---|---|---|
| g08_fx_static | expected.mp4 | `24b8953560f0` | 11.44 s | was `77d9b7899ca9`; fix `78e010b` |
| g10_fx_anim | expected.mp4 | `e22b02c35fc9` | 8.71 s | was `147f58ee29d4`; T04f-6b3 |

Full sha256 values are in each case's `expected.sha256`.

**g08_fx_static** pins v2 `style.fx` static hold: eight named roles, 20 s,
g01 media, anim all `none`. Font path is A: `font_family: "SN Pro"` +
`bold: true` (exact family, same as the 14 stylepacks) = **Bold**, not
SemiBold.

Scan-line at 35 pt against explicit Regular / SemiBold / Bold:

| | stem | ink | vs bold:true |
|---|---|---|---|
| Regular | 5 | 2528 | different |
| SemiBold | 6 | 3143 | different |
| Bold | **7** | **3583** | **identical (max Δ 0)** |
| `bold: true` | 7 | 3583 | — |

`QFontInfo.styleName()=Bold`, weight=700. This is a real 700 instance, not
synthetic bold. g08 pins **Bold**, not SemiBold. SemiBold stays on the
g01–g07/g09 split path (`"SN Pro SemiBold"`).

**g10_fx_anim** pins v2 `fx.anim` phases + emphasis (six 3 s cues, last 2 s
of the 20 s tape empty). Glossary is `_glossary: ["CUTORA"]` on the cue
style — does not read USER_DATA. Cue 1 text is `SDK READY.` at 56 pt so
stomp 1.70× stays inside SAFE_MARGIN; the long line was not frozen because
that would pin canvas clipping together with the animation.

Product gap parked as T04i (not this slice): `bold` is still true/false.
Luca can only pick Regular or Bold, while 215 bundled fonts include many
variable faces with Light / Medium / SemiBold / Black. T04i should add a
weight dropdown + variable-axis support + panel control + migration.

Second T04i item (stills review): overshoot animations (`stomp` 1.70×,
`impact_pop` 1.30×) clip at the **canvas** edge on long lines. `_edge_clear`
only checks the ROI. Fix later: clamp overshoot to available margin, and
extend the 11-point scan to assert the transformed bbox stays inside
SAFE_MARGIN. g10 c1 was shortened so this case does not freeze that bug.
