# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 153-155, 157-168, 170-178, 180-184, 186-190, 235-238, 240-260, 262-290, 351-392

import os
import json
from cutora.core import safe_json
# === Export Global Mode (compressed/original) ===
def _get_app_settings_path():
    from cutora.core.paths import settings_file
    return settings_file("app_settings.json")

def _load_app_settings():
    """Load persistent app-wide settings from user_data/app_settings.json."""
    try:
        p = _get_app_settings_path()
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = safe_json.load(f)
            if isinstance(data, dict):
                from cutora.core.secrets import strip_secret_fields
                from cutora.core.migrate_user_data import migrate_legacy_keys
                stripped = strip_secret_fields(data)
                migrated = migrate_legacy_keys(stripped)
                if migrated != stripped:
                    _save_app_settings(migrated)
                return migrated
    except Exception:
        pass
    return {}

def _save_app_settings(settings):
    """Save persistent app-wide settings to user_data/app_settings.json."""
    try:
        from cutora.core.secrets import strip_secret_fields
        settings = strip_secret_fields(settings if isinstance(settings, dict) else {})
        p = _get_app_settings_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _load_export_video_mode():
    """Read export_video_mode from app settings, default to compressed."""
    s = _load_app_settings()
    mode = s.get("export_video_mode", "compressed")
    return "original" if mode == "original" else "compressed"

def _save_export_video_mode(mode):
    """Persist export_video_mode to app settings."""
    s = _load_app_settings()
    s["export_video_mode"] = mode
    _save_app_settings(s)

def _load_gpu_enabled():
    """Persisted GPU checkbox. None if the user has never set it."""
    s = _load_app_settings()
    if "gpu_enabled" not in s:
        return None
    return bool(s.get("gpu_enabled"))

def _save_gpu_enabled(on):
    """Persist the GPU 加速 checkbox (export encoder + ASR device)."""
    s = _load_app_settings()
    s["gpu_enabled"] = bool(on)
    _save_app_settings(s)

# Initialise from persistent app settings (survives across projects / restarts)
EXPORT_VIDEO_MODE = _load_export_video_mode()

def _get_recent_projects_file():
    from cutora.core.paths import settings_file
    return settings_file("recent_projects.json")

def _record_recent_project(path):
    import json
    import os
    import time
    try:
        # 自动保存的临时快照（untitled / backups）不算「最近项目」，否则最近列表总被同一个快照霸占
        from cutora.core.paths import user_autosave_dir
        _ad = os.path.normcase(os.path.abspath(user_autosave_dir()))
        if os.path.normcase(os.path.abspath(str(path))).startswith(_ad):
            return
    except Exception:
        pass
    rf = _get_recent_projects_file()
    data = []
    if os.path.exists(rf):
        try:
            with open(rf, "r", encoding="utf-8") as f:
                data = safe_json.load(f)
        except Exception:
            pass
    data = [x for x in data if os.path.normcase(os.path.abspath(str(x.get("path") or ""))) != os.path.normcase(os.path.abspath(str(path)))]
    data.insert(0, {"path": path, "timestamp": time.time()})
    data = data[:50]
    os.makedirs(os.path.dirname(rf), exist_ok=True)
    try:
        with open(rf, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _get_recent_projects():
    import json
    import os
    import time
    rf = _get_recent_projects_file()
    if not os.path.exists(rf):
        return []
    try:
        with open(rf, "r", encoding="utf-8") as f:
            data = safe_json.load(f)
    except Exception:
        return []
    valid = []
    now = time.time()
    changed = False
    try:
        from cutora.core.paths import user_autosave_dir
        _ad = os.path.normcase(os.path.abspath(user_autosave_dir()))
    except Exception:
        _ad = None
    for item in data:
        p = item.get("path")
        ts = item.get("timestamp", 0)
        if p and _ad and os.path.normcase(os.path.abspath(str(p))).startswith(_ad):
            changed = True
            continue
        if p and (now - ts) <= 259200 and os.path.exists(p):
            valid.append(item)
        else:
            changed = True
    if changed:
        try:
            with open(rf, "w", encoding="utf-8") as f:
                json.dump(valid, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    # 永远按最近一次打开 / 保存的时间倒序显示（最新的在最上面），并按路径去重
    seen = set()
    ordered = []
    for item in sorted(valid, key=lambda it: float(it.get("timestamp") or 0.0), reverse=True):
        key = os.path.normcase(os.path.abspath(str(item.get("path") or "")))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered

def _own(state):
    """Q159：UndoSnapshot 自己已经只深拷了该拷的那几条，照存；老式整工程字典仍深拷一份。"""
    import copy
    from cutora.core.undo_snapshot import UndoSnapshot
    if isinstance(state, UndoSnapshot):
        return state
    return copy.deepcopy(state)


# Q161：历史记录总内存预算（估算值，按快照里拷了多少对象算）。超了就从最老的开始丢。
HISTORY_BUDGET_MB = 256


def _approx_bytes(state) -> int:
    n = getattr(state, "approx_bytes", None)
    if n is not None:
        return int(n)
    return 0


class HistoryManager:
    def __init__(self, max_steps=80, budget_mb=None):
        self.max_steps = max_steps
        self.budget_bytes = int((budget_mb if budget_mb is not None else HISTORY_BUDGET_MB) * 1024 * 1024)
        self.history = []  
        self.current_index = -1 
        self.merged = 0           # 合并掉的「连续小步」计数
        self.dropped_noop = 0     # 丢掉的「没改任何东西」计数

    def push(self, description, state_snapshot, extra=None):
        import time as _t
        if self.current_index < len(self.history) - 1:
            self.history = self.history[:self.current_index + 1]
        from datetime import datetime
        entry = {
            'desc': description,
            'state': _own(state_snapshot),
            'at': datetime.now().strftime("%H:%M:%S"),
            'mono': _t.monotonic(),
        }
        if extra:
            for key in ("command", "params", "source"):
                if key in extra:
                    entry[key] = extra[key]
            try:
                from cutora.core.undo_labels import describe_command
                if extra.get("command"):
                    entry['desc'] = describe_command(extra.get("command"), extra.get("params"), description)
            except Exception:
                pass
        self.history.append(entry)
        if len(self.history) > self.max_steps:
            self.history.pop(0)
        else:
            self.current_index += 1
        self._trim_budget()

    def _trim_budget(self):
        """内存预算：最老的先丢（当前位置之前的）。"""
        if self.budget_bytes <= 0:
            return
        total = sum(_approx_bytes(e.get('state')) + _approx_bytes(e.get('after')) for e in self.history)
        while total > self.budget_bytes and len(self.history) > 1 and self.current_index > 0:
            e = self.history.pop(0)
            self.current_index -= 1
            total -= _approx_bytes(e.get('state')) + _approx_bytes(e.get('after'))

    def approx_memory_mb(self) -> float:
        return sum(_approx_bytes(e.get('state')) + _approx_bytes(e.get('after')) for e in self.history) / 1048576.0

    def _mergeable_with_previous(self, entry, prev) -> bool:
        """同一个命令、同一批身份参数、1.2 秒内连着来，而且上一条已经有「之后」 → 合成一步。"""
        try:
            from cutora.core.undo_labels import MERGE_SEC, MERGEABLE, identity_of
        except Exception:
            return False
        cmd = entry.get('command')
        if not cmd or cmd != prev.get('command') or cmd not in MERGEABLE:
            return False
        if 'after' not in prev:
            return False
        if float(entry.get('mono', 0.0)) - float(prev.get('mono', 0.0)) > MERGE_SEC:
            return False
        return identity_of(cmd, entry.get('params')) == identity_of(cmd, prev.get('params'))

    def pop_last(self):
        """Drop the tail snapshot. Only when current_index is the last entry."""
        if not self.history:
            return False
        if self.current_index != len(self.history) - 1:
            return False
        self.history.pop()
        self.current_index -= 1
        return True

    def set_after(self, state_snapshot):
        """Store the post-change image on the tail entry so redo can restore it.

        Q161：之后和之前一模一样（点了一下什么都没改）→ 这条不留；和上一条是同一个连续手势
        （拖滑杆 / 微移连按）→ 合成一步，只更新上一条的「之后」。"""
        if not (0 <= self.current_index < len(self.history)):
            return
        entry = self.history[self.current_index]
        after = _own(state_snapshot)
        same = getattr(entry['state'], "same_as", None)
        is_tail = self.current_index == len(self.history) - 1
        if is_tail and callable(same):
            try:
                if same(after):
                    self.history.pop()
                    self.current_index -= 1
                    self.dropped_noop += 1
                    return
            except Exception:
                pass
        if is_tail and self.current_index > 0:
            prev = self.history[self.current_index - 1]
            if self._mergeable_with_previous(entry, prev):
                prev['after'] = after
                prev['desc'] = entry.get('desc') or prev.get('desc')
                prev['mono'] = entry.get('mono', prev.get('mono'))
                prev['params'] = entry.get('params', prev.get('params'))
                self.history.pop()
                self.current_index -= 1
                self.merged += 1
                return
        entry['after'] = after
        self._trim_budget()

    def tail_scope(self):
        """Q159：栈顶那条记录的范围（capture_undo_after 要拷同一批视频）。"""
        if not (0 <= self.current_index < len(self.history)):
            return "all"
        st = self.history[self.current_index].get('state')
        return getattr(st, "scope", "all")

    def get_current_state(self):
        if 0 <= self.current_index < len(self.history):
            return _own(self.history[self.current_index]['state'])
        return None

    def undo(self):
        # BEFORE-push convention: the tail snapshot is the pre-change image of
        # the last action. Restore it, then step the pointer back so a later
        # redo can walk forward onto that same entry's `after` image.
        # Q159：不再多拷一份 —— 恢复时 _restore_snapshot 自己会拷（老字典）或原地写回（UndoSnapshot）。
        if self.current_index < 0:
            return None
        entry = self.history[self.current_index]
        state = entry['state']
        self.current_index -= 1
        return state

    def redo(self):
        if self.current_index >= len(self.history) - 1:
            return None
        self.current_index += 1
        entry = self.history[self.current_index]
        if 'after' in entry:
            return entry['after']
        return entry['state']
        
    def jump_to(self, index):
        if 0 <= index < len(self.history):
            self.current_index = index
            return self.get_current_state()
        return None
