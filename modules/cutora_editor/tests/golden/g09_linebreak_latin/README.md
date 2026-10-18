# g09_linebreak_latin

Latin (en + it) line-break case for T04b. Segments include pre-broken `lines`
so the renderer uses them instead of pixel wrapping.

Source media: `tests/golden/_media/g01/video.mp4` (20 s, 1080×1920).

Frozen layout / style (also on `task.layout_rules` and `task.style`):

- `max_chars_per_line` = 42
- `max_lines` = 2
- `balance_lines` = true
- `line_break` = space
- font = SN Pro SemiBold
- fontsize = 35
- `wrap_lines` = 2
- `anim_in_type` / `anim_out_type` = none

## Sentences vs Claude §三

| seg | id | req | what it pins |
|---|---|---|---|
| 0 | hang_en | 1, 2 | 66 chars → 2 lines. Naive midpoint leaves `the` at EOL; rule moves the break after `old`. |
| 1 | punct | 3 | Breaks after the comma (`Friends arrived,`), not at the space closer to midpoint (`then we`). Punctuation outranks balance (16+35). |
| 2 | balance | 4 | Greedy pack (balance off) is 40+9; actual balance is 24+25. Naive midpoint already 24+25. |
| 3 | overflow | 5 | See **known-bad overflow** below. |
| 4 | it_hang | 1, 6 | Italian, `linebreak_it.json`. Naive leaves `della` at EOL; rule moves to `casa`. 63 chars → 2 lines. |
| 5 | it_apo | 1, 6 | Italian. `dell'anima` stays one token. 65 chars → 2 lines. |

Stills: `docs/plan/reports/img/g09_seg0.png` … `g09_seg5.png` and `g09_contact_sheet.png`.

## Overflow (seg3) — T06-2

Baseline `1a60d144d517` (2026-09-15): width-pack, shrink ≥ 0.85, prefix-fill
with visible `…` inside the 9:16 safe zone. Visible text ends `and…`.
Segs 0–2 byte-identical to the previous baseline; 4–5 layout bbox unchanged
(x264 residual after the changed cue).

## Keyword highlight also baked in

The g01 style (`preset_name` 周圆青色) paints karaoke / keyword highlight on the current words (amber on `the old`, `sat by`, `bringing`, `extra`, `letters`, `della casa`, `sala` / `del`). Changing keyword-highlight rules can make this linebreak case go red even if `break_lines()` is untouched. Check `style.color_high` / `high_grad` before blaming layout.
