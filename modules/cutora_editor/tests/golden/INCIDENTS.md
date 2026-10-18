# INCIDENTS

Permanent ops notes. Next to `RERECORD_NOTES.md`.

---

## OPS-03 · 2026-09-16 · Windows `git worktree remove` followed a runtime symlink

**Instruction was wrong.** Luca wrote: `git worktree remove` correctly handles symlinks and will not follow `runtime` to delete models. On Windows that is false. Grok followed the instruction. The fault is the instruction, not the follow-through.

### What happened

Four worktrees hung off `D:\0_Dev\CUTORA\.git`:

- `D:\0_Dev\_wt_CUTORA_T11c` (`task/T11c-gallery-edit`)
- `D:\0_Dev\CUTORA-T11d` (`task/T11d-text-presets`)
- `D:\0_Dev\CUTORA-wt-T11e` (`task/T11e-auto-credit`)
- `D:\0_Dev\CUTORA-qw-before` (detached)

Each had `runtime` as a **directory symlink / junction pointing at the main-repo `runtime/`**. Porcelain was empty. `git worktree remove "<path>"` then `git worktree prune` ran as instructed.

`D:\0_Dev\CUTORA\runtime` was left as an empty directory (mtime 17:22, same pass). Recycle Bin had no hits.

### Loss

The whole main-repo `runtime/`:

- `venv` (Python + CUDA stack)
- `ffmpeg`
- every model on disk, including VoxPopuli `wav2vec2_voxpopuli_base_10k_asr_it.pt` (`620bad57…` / 377,689,881 B)
- the four comparison weights downloaded the same day (large-v3 CT2, large-v3-turbo, XLSR-italian, mms-300m-1130-forced-aligner)

Source tree and golden hashes were untouched. Dist cleanup (67.51 GB) was a separate, intended delete.

The 67.51 GB freed vs the 50–60 GB estimate was **~10 GB of `runtime`**, not extra pack junk. A gap ≥ 5 GB against estimate must stop and inspect.

### Recovery

Restore `runtime/` from `D:\0_Dev\CUTORA-dist\CUTORA-7.0.0-internal-internal.zip` (built at `3324bed`). Zip `BUILD.txt` sha256 of every model file is the check. Then re-download the four comparison weights **after** they are listed in the pack catalog (Zero unpinned).

### Permanent rules

1. **Worktrees must not contain a symlink/junction to the main `runtime/`.** New worktrees: set env `CUTORA_RUNTIME` to the main-repo runtime path. Do not create a `runtime` link.
2. **Before deleting any directory that may contain a symlink or junction on Windows:** `dir /s /a:l` list every link. `rmdir <link>` each link first (`rmdir` removes the link, not the target). Only then delete the directory. Never `rm -r` / `Remove-Item -Recurse` a tree whose links still point at shared data. `git worktree remove` is not exempt.
3. **Before any bulk delete:** write `dir /s` (or equivalent) to `_predelete_<YYYYMMDD_HHMMSS>.txt` so a loss can be compared.
4. **Freed-bytes vs estimate:** if the actual free delta is **≥ 5 GB** off the estimate, stop and inspect before the next delete.

Do not reopen the “git worktree remove is safe with runtime symlinks” claim.

## 2026-09-19 · golden 十二例曾经是错的（解码器）

三天里我们一直拿一份**用偏离规范的解码器录出来的基线**当红线。它没有骗过我们，
是因为它一直没变；一旦导出改成正确的软件解码，它就整片翻红，看起来像是新代码闯了祸。

教训（已进 `docs/plan/BACKLOG.md` 的规矩一节）：
1. **基线红了，先问「谁是对的」，不要默认「基线是对的」。** 基线只是「上次录的样子」。
2. 判定对错要有**外部参照**，不能拿两份自家产物互比。这次的外部参照是 ffmpeg CLI 的解码。
3. 录基线时应当同时钉住**解码路径**，就像已经钉住字体指纹那样。
   现在由 `tests/test_decoder_conformance.py` 承担。

顺带记一笔待办：golden 夹具的字体是 240 个，而软件实际运行时加载 **607** 个
（见 worker 日志 `[FONT] Loaded 607 embedded font files`）。
**golden 没有覆盖用户真正渲染用的那套字体集**。这不影响本次判断，但是个真实缺口。
