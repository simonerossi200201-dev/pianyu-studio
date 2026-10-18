# g10_fx_anim

v2 `fx.anim` phases + emphasis rules. 6 cues × 3 s, media g01 (20 s, last 2 s empty). Glossary is `_glossary: ["CUTORA"]` on the cue style — does not read USER_DATA.

Font: same exact-family path as g08 (`SN Pro` + `bold: true`, tier **bold**).

Recorded 2026-09-14; T04f-6b3 re-record `e22b02c35fc9` (solo process). Cue 1 text is `SDK READY.` at 56 pt so stomp 1.70× stays inside SAFE_MARGIN; do not restore the long line.

| # | t | anim | emphasis |
|---|---|---|---|
| 0 | 0–3 | in=pop_in loop=none out=none unit=word | 入场 pop_in / numbers |
| 1 | 3–6 | in=stomp loop=none out=none unit=word | 入场 stomp / caps |
| 2 | 6–9 | in=none loop=zoom_slow out=none unit=segment | 循环 zoom_slow / longest |
| 3 | 9–12 | in=none loop=karaoke out=none unit=word | 循环 karaoke / list |
| 4 | 12–15 | in=none loop=none out=fade unit=word | 出场 fade / 双色跳字 |
| 5 | 15–18 | in=typewriter loop=none out=none unit=word | 入场 typewriter / glossary |

Stills: `docs/plan/reports/img/T04f-6b2_g10_c*_*.png` (start / mid / end per cue).
