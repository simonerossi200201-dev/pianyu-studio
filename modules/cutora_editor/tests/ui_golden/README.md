# UI golden stills

Pixel sha256 of Qt widgets. Unlike `tests/golden/` (export mp4), these
lock what the user sees. Record with `python tools/ui_golden.py --record --case uNN`.

Fingerprint header (printed every `--all` / `--record`):

```
HEAD / PySide6 / logicalDotsPerInch / devicePixelRatio / fonts_digest
```

Fixed DPI: do not set `QT_SCALE_FACTOR`. Panel stills `assert grab.width() == 280`.

| case | widget | lock |
|---|---|---|
| u01 | retired | engine.frame_at glass; replaced by u01b |
| u02 | retired | engine + line_bar; replaced by u02b |
| u03 | retired | panel still; replaced by u03b |
| u04 | retired | panel + stroke click; replaced by u04b |
| u01b | retired | canvas grab; replaced by u01c (Q214, Luca 2026-10-10) |
| u02b | retired | canvas + line_bar; replaced by u02c |
| **u01c** | `VideoCanvas.grab()` | same capture as u01b |
| **u02c** | `VideoCanvas.grab()` | same capture as u02b; diff vs a fresh u01 grab only in the bar band |
| **u03b** | retired | panel still; replaced by u03c (Q24 section capsules) |
| **u04b** | retired | panel + stroke click; replaced by u04c |
| **u03c** | retired | panel still; replaced by u03d (Q25 inspector) |
| **u04c** | retired | panel + stroke click; replaced by u04d |
| **u03d** | retired | panel still; replaced by u03e (Q83 inspector) |
| **u04d** | retired | panel + stroke click; replaced by u04e |
| **u07** | retired | 480px inspector; replaced by u07b |
| **u08** | retired | text property panel; replaced by u08b. Was in `CASES`, not in the pytest gate |
| u03e | retired | panel still; replaced by u03f (Q214) |
| u04e | retired | panel + stroke click; replaced by u04f |
| **u03f** | `SubtitleStylePanel.grab()` 280 | same capture as u03e |
| **u04f** | `SubtitleStylePanel.grab()` 280 | same capture as u04e; diff vs `u03f/got.png` |
| u07b | retired | 480px inspector; replaced by u07c (Q214) |
| u08b | retired | text property panel; replaced by u08c |
| **u07c** | `SubtitleStylePanel.grab()` 480 | same capture as u07b |
| **u08c** | `PropertyPanel.grab()` text overlay | same capture as u08b. In `CASES`, not in the pytest gate |
| u05 | retired | timeline still; replaced by u05b (Q169 实色色块 + 轨道头把手) |
| **u05b** | `TimelineWidget.grab()` | g19 playhead 20s; same capture as u05 |
| u06 | retired | style gallery; replaced by u06b (Q214) |
| **u06b** | `StyleGalleryPanel.grab()` 280 | same capture as u06 |

Pytest gate: u01c u02c u03f u04f u05c u06b u07c.
Also recorded (`--all` / `--case u08c`), not in that gate: u08c.

Any live hash move: stop, do not `--record`, report HEAD + got/exp.
