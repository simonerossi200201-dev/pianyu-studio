# g08_fx_static

v2 `style.fx` static hold. One role per cue, 8 cues, 20 s, 1080×1920, media = g01 `video.mp4`.

Font: `font_family: "SN Pro"` + `bold: true` (exact family path, same as the 14 stylepacks). Scan-line at 35 pt: **SN Pro + bold:true matches explicit Bold (stem 7 / ink 3583)**.

Recorded 2026-09-14. Font path is A: exact family `SN Pro` + `bold: true` = **Bold** (stem 7 / ink 3583), not SemiBold.

| # | t | role | pins |
|---|---|---|---|
| 0 | 0.00–2.05 | `fx_grad` | fill.gradient + stroke |
| 1 | 2.10–4.93 | `fx_metal` | fill.metal=gold + extrude (not pure black) |
| 2 | 4.97–7.38 | `fx_neon` | glow.neon + shadow |
| 3 | 7.43–9.31 | `fx_glass` | bg.glass + four-side fade=0.18 (live video backdrop) |
| 4 | 9.36–12.00 | `fx_fonts` | fonts.body/emphasis + tracking + letter_case=upper + words=journey |
| 5 | 12.10–14.70 | `fx_paper` | bg.texture=paper (_feather_alpha / un-premultiply) |
| 6 | 14.80–17.40 | `fx_bubble` | bg.bubble + per-line fade |
| 7 | 17.50–20.00 | `fx_box` | bg.box |

Stills: `docs/plan/reports/img/T04f-6b2_g08_c*.png`.
