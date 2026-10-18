# SPDX-License-Identifier: GPL-3.0-or-later
"""NLE Gen-2 Timeline — 唯一时间线真实对象。

运行时 task["timeline"] 始终是 Timeline 对象（不再是 dict）。
序列化为 dict 仅在 collect_project_data 时发生；
反序列化在 apply_project_data / _tl_of 惰性升级时发生。

tracks 列表规则：
  - 索引 0 = 最底层（先画）
  - 索引 -1 = 最顶层（后画）
  - visual 域在前，audio 域在后（分域不影响渲染，只影响 UI 布局）
  - pinned 的 Track 位于其域内最顶部

Domain 强约束：
  - Track.domain 一经创建不可更改（跟随 track_type 自动确定）
  - add_track / move_track_up/down 全部做 domain 边界保护
  - 跨域操作（如把 audio Track 移动到 visual 区域）被硬性拒绝

FrameRenderer 不做排序推断，直接遍历 tracks 列表即可。
"""

# Domain 分类规则（强约束）
AUDIO_TYPES = frozenset(("audio", "bgm", "sfx"))

def domain_of(track_type):
    """根据 track_type 返回 domain。这是唯一的 domain 判定入口。"""
    return "audio" if track_type in AUDIO_TYPES else "visual"


class Timeline:
    def __init__(self, width, height, fps):
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        # === 唯一轨道列表（顺序 = 叠压顺序 = 渲染顺序）===
        self.tracks = []
        self.sources = {}

    # ---------- 逻辑分组视图（只读，不存储）----------
    @property
    def video_tracks(self):
        """返回 visual 域轨道列表（保持 tracks 中的相对顺序）。"""
        return [t for t in self.tracks if getattr(t, "domain", "visual") == "visual"]

    @property
    def audio_tracks(self):
        """返回 audio 域轨道列表（保持 tracks 中的相对顺序）。"""
        return [t for t in self.tracks if getattr(t, "domain", "visual") == "audio"]

    # ---------- 叠压排序（向后兼容 API）----------
    _TYPE_PRIORITY = {
        "video": 0,
        "image": 1,
        "text": 2,
        "subs": 3,
    }

    def get_sorted_video_tracks(self):
        """NLE Gen-2: 返回 visual 域的可见轨道（按 tracks 列表顺序）。
        因为 tracks 顺序本身就是叠压顺序，所以不再重新排序。"""
        return [t for t in self.tracks
                if getattr(t, "domain", "visual") == "visual"
                and getattr(t, "visible", True)]

    def get_ordered_video_tracks(self):
        return self.video_tracks

    def get_ordered_audio_tracks(self):
        return self.audio_tracks

    # ---------- 轨道管理 ----------
    def add_track(self, track):
        """将 Track 加入列表。visual 域插入 audio 域之前，保持分域布局。
        Track.domain 在添加时做强约束校验。"""
        from cutora.model.timeline_model import Track
        if not isinstance(track, Track):
            return
        # 强制 domain 与 type 一致（防止外部篡改）
        correct_domain = domain_of(track.type)
        if track.domain != correct_domain:
            track.domain = correct_domain
        if track.domain == "audio":
            self.tracks.append(track)
        elif getattr(track, "type", None) == "video" and any(
                getattr(t, "type", None) in ("image", "text", "subs", "adjust") for t in self.tracks):
            # Q163 统一叠压：新视频轨放在最上面那条视频轨之后 —— 叠在视频上，但不盖住图片 / 文字 / 字幕
            from cutora.render.visual_stack import place_new_video_track
            self.tracks.append(track)
            place_new_video_track(self, track)
        else:
            insert_idx = len(self.tracks)
            for i, t in enumerate(self.tracks):
                if getattr(t, "domain", "visual") == "audio":
                    insert_idx = i
                    break
            self.tracks.insert(insert_idx, track)

    def remove_track(self, track):
        """移除轨道。"""
        if track in self.tracks:
            self.tracks.remove(track)

    # ---------- 轨道顺序操作（直接交换在 tracks 中的位置）----------
    def _domain_indices(self, domain):
        """返回指定域的 Track 在 self.tracks 中的索引列表（保持顺序）。"""
        return [i for i, t in enumerate(self.tracks)
                if getattr(t, "domain", "visual") == domain]

    def move_track_up(self, track):
        """轨道上移 = 在同域内向 tracks 列表的后方移动（更高 z-index）。
        跨域移动被硬性拒绝。"""
        if track not in self.tracks:
            return
        domain = getattr(track, "domain", "visual")
        indices = self._domain_indices(domain)
        pos = self.tracks.index(track)
        if pos not in indices:
            return
        idx_in_domain = indices.index(pos)
        if idx_in_domain >= len(indices) - 1:
            return  # 已经是域内最顶
        next_pos = indices[idx_in_domain + 1]
        self.tracks[pos], self.tracks[next_pos] = self.tracks[next_pos], self.tracks[pos]

    def move_track_down(self, track):
        """轨道下移 = 在同域内向 tracks 列表的前方移动（更低 z-index）。
        跨域移动被硬性拒绝。"""
        if track not in self.tracks:
            return
        domain = getattr(track, "domain", "visual")
        indices = self._domain_indices(domain)
        pos = self.tracks.index(track)
        if pos not in indices:
            return
        idx_in_domain = indices.index(pos)
        if idx_in_domain <= 0:
            return  # 已经是域内最底
        prev_pos = indices[idx_in_domain - 1]
        self.tracks[pos], self.tracks[prev_pos] = self.tracks[prev_pos], self.tracks[pos]

    def toggle_pin(self, track):
        """置顶 / 取消置顶：pinned 的 Track 移到域内末尾。"""
        track.pinned = not track.pinned
        if track.pinned:
            self._move_to_domain_top(track)

    def _move_to_domain_top(self, track):
        """将 Track 移到其域内最顶部位置（列表中最后的域内位置）。"""
        if track not in self.tracks:
            return
        domain = getattr(track, "domain", "visual")
        indices = self._domain_indices(domain)
        if not indices:
            return
        pos = self.tracks.index(track)
        target = indices[-1]
        if pos == target:
            return
        self.tracks.remove(track)
        # 重新计算目标位置（移除后索引可能变化）
        indices2 = self._domain_indices(domain)
        if indices2:
            insert_at = indices2[-1] + 1
        else:
            insert_at = len(self.tracks)
        self.tracks.insert(insert_at, track)

    def toggle_visibility(self, track):
        """切换轨道显隐。"""
        track.visible = not track.visible

    def toggle_batch_enable(self, track):
        """切换轨道的批量操作开关。"""
        track.enable_batch = not track.enable_batch

    def normalize_orders(self):
        """向后兼容：将 track.order 同步为 tracks 列表中的位置索引。"""
        for i, t in enumerate(self.tracks):
            t.order = i

    # ---------- Domain 验证 ----------
    def validate_domain_invariant(self):
        """验证 tracks 列表是否满足 visual-before-audio 不变量。
        如果不满足则修正。返回是否需要修正。"""
        vis = [t for t in self.tracks if getattr(t, "domain", "visual") == "visual"]
        aud = [t for t in self.tracks if getattr(t, "domain", "visual") == "audio"]
        expected = vis + aud
        if self.tracks != expected:
            self.tracks[:] = expected
            return True
        return False

    def find_tracks_by_type(self, track_type):
        """根据 type 查找所有匹配的轨道。"""
        return [t for t in self.tracks if getattr(t, "type", None) == track_type]

    def find_track_by_type(self, track_type):
        """根据 type 查找第一个匹配的轨道。"""
        for t in self.tracks:
            if getattr(t, "type", None) == track_type:
                return t
        return None

# --- extracted from the T01 monolith ---
DEFAULT_TIMELINE_WIDTH = 1920

DEFAULT_TIMELINE_HEIGHT = 1080

DEFAULT_TIMELINE_FPS = 30.0

# --- 全局偏好默认值 ---
DEFAULT_TIMELINE_TEMPLATE = "1080x1920"  # 竖屏 / 横屏 = "1920x1080"

DEFAULT_VIDEO_FIT_MODE = "fill"          # "fit"=适配 | "fill"=填满 | "stretch"=拉伸
