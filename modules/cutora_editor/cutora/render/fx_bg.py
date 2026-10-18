# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared background effector. Subtitle and text overlay import this module.

Do not copy raster_bg / BgSpec. Both callers must use this one function.
"""
from __future__ import annotations

from cutora.render.textfx.raster import raster_bg
from cutora.render.textfx.spec import BgSpec

__all__ = ["raster_bg", "BgSpec"]
