# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Section header capsule: remember last on-value, write an off-value.

Signature locked for Q24: SectionToggle(path, off_value, default_on).
``path`` is dotted under style['fx'] (e.g. 'stroke.enabled', 'bg.type').

Stroke / glow / bg / shadow headers write ``*.enabled`` (Q66). Value knobs
stay in the project; paint reads ``enabled``, not a zeroed width/sigma.

When ``off_value`` is a dict, ``path`` is a family root and only those keys
are snapshotted / restored (fill.type+colors, emphasis.auto+words).

``on_when='equals_on'``: the family is on iff the path equals ``default_on``
(background texture: on = bg.type == 'texture'; off restores the last
non-texture type, defaulting to ``off_value``).
"""
from __future__ import annotations

import copy

from cutora.ui.panels.fx_schema import nested_get, nested_set


class SectionToggle:
    def __init__(self, path, off_value, default_on, *, on_when=None):
        self.path = path
        self.off_value = copy.deepcopy(off_value)
        self.default_on = copy.deepcopy(default_on)
        self.on_when = on_when
        self._saved = None

    def reset(self) -> None:
        self._saved = None

    def get(self, fx: dict):
        if not isinstance(fx, dict):
            return None
        return nested_get(fx, self.path)

    def set(self, fx: dict, value) -> None:
        nested_set(fx, self.path, copy.deepcopy(value))

    def is_on(self, fx: dict) -> bool:
        cur = self.get(fx)
        if self.on_when == "equals_on":
            return self._values_equal(cur, self.default_on)
        return self._is_on_value(cur)

    def _is_on_value(self, value) -> bool:
        off = self.off_value
        if isinstance(off, dict):
            if not isinstance(value, dict):
                return False
            for key, ov in off.items():
                if not self._values_equal(value.get(key), ov):
                    return True
            return False
        if isinstance(off, (int, float)) and not isinstance(off, bool):
            try:
                return float(value or 0) != float(off)
            except (TypeError, ValueError):
                return False
        if off in ("none", ""):
            return str(value or "none") not in ("none", "")
        if isinstance(off, bool):
            return bool(value) != bool(off)
        if isinstance(off, list):
            return not self._values_equal(list(value or []), list(off))
        return value != off

    @staticmethod
    def _values_equal(a, b) -> bool:
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                return False
            return all(SectionToggle._values_equal(x, y) for x, y in zip(a, b))
        if isinstance(a, str) and isinstance(b, str):
            if a.startswith("#") and b.startswith("#"):
                return a.upper() == b.upper()
            return a == b
        if isinstance(a, bool) or isinstance(b, bool):
            return bool(a) is bool(b)
        if isinstance(a, (int, float)) or isinstance(b, (int, float)):
            try:
                return float(a or 0) == float(b or 0)
            except (TypeError, ValueError):
                return a == b
        return a == b

    def _snapshot(self, cur):
        if isinstance(self.off_value, dict):
            src = cur if isinstance(cur, dict) else {}
            return {k: copy.deepcopy(src.get(k)) for k in self.off_value}
        return copy.deepcopy(cur)

    def _write(self, fx: dict, payload) -> None:
        if isinstance(self.off_value, dict):
            target = self.get(fx)
            if not isinstance(target, dict):
                target = {}
                self.set(fx, target)
                target = self.get(fx)
            for key, val in payload.items():
                target[key] = copy.deepcopy(val)
            return
        self.set(fx, payload)

    def apply_checkbox(self, fx: dict, checked: bool) -> bool:
        """Flip the family on or off. Off remembers the current value.

        Returns True when a value was written.
        """
        if not isinstance(fx, dict):
            return False
        cur = self.get(fx)
        if self.on_when == "equals_on":
            if checked:
                if self._values_equal(cur, self.default_on):
                    return False
                if cur is not None and not self._values_equal(cur, self.default_on):
                    self._saved = copy.deepcopy(cur)
                self.set(fx, self.default_on)
                return True
            if not self._values_equal(cur, self.default_on):
                return False
            restore = self.off_value if self._saved is None else self._saved
            self.set(fx, restore)
            return True
        if checked:
            if not self._is_on_value(cur):
                restore = self.default_on if self._saved is None else self._saved
                self._write(fx, restore)
                return True
            return False
        if self._is_on_value(cur):
            self._saved = self._snapshot(cur)
        self._write(fx, self.off_value)
        return True
