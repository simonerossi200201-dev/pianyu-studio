# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""v2 subtitle effects. Unique public entry: draw().

This package init is Qt-free so spec/pure-black helpers can be imported
from non-isolated tests. draw() lazy-imports the raster pipeline.
"""
from __future__ import annotations

from cutora.render.textfx.spec import from_style, task_has_glass_bg


def draw(painter, event, style, t, video_w, video_h, backdrop=None,
         hit_recorder=None, seg_idx=None, dynamic_layout_list=None,
         _hit_char_offset=0):
    """Raster one subtitle event into an ROI and blit it through *painter*."""
    from cutora.render.textfx.pipeline import draw as _draw
    return _draw(
        painter, event, style, t, video_w, video_h, backdrop,
        hit_recorder=hit_recorder, seg_idx=seg_idx,
        dynamic_layout_list=dynamic_layout_list,
        _hit_char_offset=_hit_char_offset,
    )


def bgr_numpy_to_qimage(frame):
    from cutora.render.textfx.raster import bgr_numpy_to_qimage as _fn
    return _fn(frame)


def clear_roi_cache():
    from cutora.render.textfx.cache import clear_roi_cache as _fn
    _fn()
    from cutora.render.textfx.pipeline import reset_degrade
    reset_degrade()


__all__ = [
    "draw",
    "from_style",
    "task_has_glass_bg",
    "bgr_numpy_to_qimage",
    "clear_roi_cache",
]
