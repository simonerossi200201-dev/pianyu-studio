# Golden baselines (T00)

These cases were generated with the **unmodified** AutoSub_v6 program
(`D:\0_Dev\AutoSub_v6\core`) and frozen into this tree.

## Non-determinism / ASR

Whisper / whisperX on GPU is not bit-stable across runs. Every case therefore
**freezes ASR output inside the `.asproj.json`**. `tools/golden_check.py`
re-renders only; it must not re-run recognition.

## Layout

| case | what |
|---|---|
| g01_vertical_basic | 20s 1080x1920 EN + default style |
| g02_vertical_fx | same + highlight / bounce / glow / keyword / overlays / transition |
| g03_horizontal_it | 30s 1920x1080 IT + spatial(stage) + sfx clip |
| g04_stitch | 3 groups, `01\|text` captions |
| g05_scatter_insert | scatter_broll + insert_image |
| g06_parallel | three tasks, parallel workers=3, compared to serial |
| g07_cli | `AutoSub_debug.py --do batch_export` on a g01 copy |
| g08_fx_static | 20s 1080x1920 v2 `style.fx` static hold, 8 roles (g01 media) |
| g09_linebreak_latin | EN+IT linebreak; stored `lines` |
| g10_fx_anim | 20s 1080x1920 v2 `fx.anim` + emphasis, 6 cues (g01 media) |
| g11_vertical_basic_fx | T11g-1 fx-path mirror of g01 |
| g12_vertical_fx_path | T11g-1 fx-path mirror of g02 |
| g13_horizontal_it_fx | T11g-1 fx-path mirror of g03 |
| g14_stitch_fx | T11g-1 fx-path mirror of g04 |
| g15_scatter_insert_fx | T11g-1 fx-path mirror of g05 |
| g16_parallel_fx | T11g-1 fx-path mirror of g06 |
| g17_cli_fx | T11g-1 fx-path mirror of g07 |
| g18_linebreak_latin_fx | T11g-1 fx-path mirror of g09 |
| g19_stitch_timeline | retired (msec seek). Replaced by g19-2 |
| g19b_stitch_crossfade | retired (msec seek). Replaced by g19b-2 |
| g19-2_stitch_timeline | T20a timeline; export seek POS_FRAMES + half-open clips |
| g19b-2_stitch_crossfade | g19-2 + cross_dissolve 0.35s on every seam |

Media lives in `_media/` (gitignored). `expected.mp4` is also gitignored
(`tests/golden/**/expected.mp4` and `expected_*.mp4`); `expected.sha256`
and the project JSON are tracked. The protected baseline is the sha256
text file, not the video. A fresh clone has no `expected.mp4` (~20 MB
each) until you record; `tools/golden_check.py` still runs — it re-renders
to a temp file and compares that hash to `expected.sha256`. Record only
if you want local mp4s for visual diff.

Export parameters for every case: `gpu=false`, `mode=quality` (libx264 CRF 18).
Product loudness default is **-14 LUFS**; golden harness pins
`loudness_mode=off` in the copied `_fixture_user_data/app_settings.json`
so hashes stay still when the product default changes.

## Recording discipline (T04f-6b3)

Record **one case per process**. Sharing `boot_host()` across cases lets
process-global caches (FontManager `_font_cache`, textfx ROI cache) leak
from one case into the next. That is how g10's first hash `147f58ee29d4`
was written: `tools/t04f6b2_record_g08_g10.py` ran g08 then g10 after a
single boot.

`tools/golden_check.py --isolate` is the release gate for this class of
bug. For every case it compares a **solo process** hash with the hash of
the same case when it runs in `--all` order. `--repeat 3` cannot catch
order pollution: the pollution is deterministic when the order is fixed.

`--isolate` does **not** catch leaks that `reset_case_caches()` already
clears (ROI cache, FontManager `_font_cache`). Those are prevented
upstream. The comparison's job is to fail when the clear-list is
**missing** a process-global. Do not "helpfully" dump every cache into
that list just to keep `--isolate` green — that would make the net
blind. Add a cache to the list only with a unit test that still fails
if the cache leaks **without** the clear (see
`tests/test_t04f6b3_font_leak.py`).

Before recording, also run:

    git status --porcelain -- tests tools

and read it. `source_dirty` only watches `tests/golden/` plus a few
other prefixes; untracked files under `tests/` or `tools/` can still
affect a record.

Before a release:

    python tools/golden_check.py --isolate
    python tools/golden_check.py --all --repeat 3

## USER_DATA fixture

Golden may only depend on files inside this repository. Any code path that
reads `user_data/` during a golden run must be taken over by the versioned
fixture at `_fixture_user_data/` (T00 baseline: `spatial_wet=0.1`,
`mic_amount=0.2`, `mic_texture=true`, no `*_key` / `*_token` / `*_secret`
fields).

`tools/golden_check.py` copies that fixture to a temp directory and sets
`CUTORA_USER_DATA` **before** importing `cutora`. Child processes
(`python -m cutora --render-job`, g06 parallel workers, g07 CLI) inherit the
same environment variable; `cutora.core.paths.resolve_user_data()` reads it.
Do not re-record baselines to hide a machine-settings leak.
