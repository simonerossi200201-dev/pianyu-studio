# SPDX-License-Identifier: GPL-3.0-or-later
import uuid

def probe_duration_sec(media_path):
    from cutora.media.probe_cache import probe_duration_sec as _cached
    return _cached(media_path)

_DURATION_UNSET = object()


class MediaSource:
    def __init__(self, path, duration=_DURATION_UNSET, *, probe=True):
        self.id = uuid.uuid4().hex
        self.path = path
        self.has_video = True
        self.has_audio = True
        if duration is not _DURATION_UNSET:
            self.duration = duration
        elif probe:
            self.duration = probe_duration_sec(path)
        else:
            self.duration = None
        self.alpha = False
        self.preview_path = None
        self.alpha_mode = "straight"  # "straight" | "premultiplied"

class Clip:
    def __init__(self, source_id, in_t, out_t, start_t):
        self.source_id = source_id
        self.in_t = in_t
        self.out_t = out_t
        self.start_t = start_t
        self.link_group_id = None       # 音视频编组 ID
        self.type = "media"             # 类型：'media' 或 'transition' (独立转场块)

        # === 视频属性（video clip 使用）===
        self.fit_mode = "fill"          # 适配模式: fit / fill / stretch
        # Q117 背景填充（模糊/镜像/平铺/拉伸）。出厂就带上，↺ 回默认才有据可依
        from cutora.model.project_schema import BG_FILL_DEFAULTS
        self.bg_fill = dict(BG_FILL_DEFAULTS)
        self.blend_mode = "normal"      # 混合模式（元素级）
        self.x = 0.0                    # 画布偏移 X
        self.y = 0.0                    # 画布偏移 Y
        self.scale = 1.0                # 缩放
        self.scale_y = 1.0              # 高度相对缩放；1 = 不变形
        self.lock_aspect = True         # 拖动手柄时保持当前宽高比
        self.rotation = 0.0             # 旋转（度）
        self.flip_x = False             # 水平翻转
        self.flip_y = False             # 垂直翻转
        self.speed = 1.0                # 播放速率（0.1~10.0），影响时长
        self.size_mode = "project"      # T21: "project" 铺满画布 / "source" 源像素
        self.loop = False               # 循环播放（填满时间段）
        self.alpha = False              # 源带 alpha（ProRes 4444 / GIF / VP9）
        self.alpha_mode = "straight"    # "straight" | "premultiplied"
        self.match_duration = False     # 自动匹配时间线时长
        self._match_target_dur = 0.0    # match_duration 目标时长（由 UI 设置）
        self.transition_preset = "none" # 转场预设（如 fade_in / pop_both / sway_out）
        self.transition_duration = 0.35 # 转场默认时长（秒）
        self.fade_top = 0.0
        self.fade_bottom = 0.0
        self.fade_left = 0.0
        self.fade_right = 0.0
        # Q152-G 图层面板：显隐 / 锁定 / 颜色标签 / 自定义名（默认值 = 老工程的样子）
        self.hidden = False
        self.locked = False
        self.label_color = ""
        self.name = ""
        # Q152-B/C 形状遮罩 / 蓝绿幕抠像（空 = 没有，渲染零改动）
        self.masks = []
        self.keyer = {}
        self.ai_matte = {}            # Q152-D：AI 抠像（会话 id + 提示点 + 输出设置）
        self.tracking = {}            # Q152-E：AI 点跟踪（会话 id + 跟踪点 + 绑定）
        self.fx_graph = {}            # Q152-F：效果节点图（空 = 老流水线）
        self.uid = uuid.uuid4().hex   # Q154：稳定 id（撤销深拷贝后不变；预合成的重复份靠它找来源）
        self.group = {}               # Q154：预合成组（空 = 不在组里），见 model/clip_groups.py
        self.matte_for = ""           # 空 = 普通视频。有值 = 只做这条轨道的亮度蒙版，不单独画一层
        self.matte_invert = False
        self.ai_cards = []            # 每个片段绑定的 AI 卡牌列表（记录参数、提示词、素材路径）

        # === 音频属性（audio clip 使用）===
        self.gain_db = 0.0              # 音量增益 (dB)，0 = 原始音量
        self.pitch_semitones = 0.0      # 音高调整（半音）
        self.fade_in_dur = 0.0          # 淡入时长（秒）
        self.fade_out_dur = 0.0         # 淡出时长（秒）
        self.spatial_enabled = False    # 这条音频吃不吃空间立体声
        self.spatial_amount = 0.0       # 空间化强度（湿声 0~1）
        self.is_main = False            # 是否为视频原始音频
        self.audio_role = ""            # "", "main", "bgm", "sfx"
        self.audio_proc = {}            # Q206：{} = 无降噪。不改 MediaSource.path
        self.speed_curve = []           # Q208：[] = 只用 clip.speed。不进关键帧
        self.seam_xfade = False         # 新配乐才打开：循环接缝交叉淡化。老工程保持 aloop

    def clone(self):
        """深拷贝——不共享对象引用。"""
        import copy
        return copy.deepcopy(self)

    @property
    def duration(self):
        """Clip 源素材时长（秒）。"""
        return max(0.0, self.out_t - self.in_t)

    @property
    def effective_duration(self):
        """考虑变速 / 循环+匹配时长 后的时间线上实际时长（秒）。
        当 loop=True 且 match_duration=True 且 _match_target_dur>0 时，
        时长延伸到 _match_target_dur（从 start_t 起算的剩余时间），
        素材在源范围内循环播放。
        """
        base_dur = max(0.0, (self.out_t - self.in_t) / max(0.01, self.speed))
        curve = getattr(self, "speed_curve", None) or []
        if curve:
            from cutora.audio.speed_curve import integrated_out_dur
            base_dur = integrated_out_dur(max(0.0, self.out_t - self.in_t), self.speed, curve)
        if self.match_duration and self._match_target_dur > 0:
            return max(base_dur, self._match_target_dur)
        return base_dur

    def split_at(self, timeline_time):
        """在 timeline_time 处切割，返回右半 Clip（self 变为左半）。
        如果 timeline_time 不在 clip 范围内，返回 None。"""
        clip_start = self.start_t
        clip_end = self.start_t + self.effective_duration
        if timeline_time <= clip_start or timeline_time >= clip_end:
            return None
        # 计算 source 中的切割点（考虑变速）
        offset_in_source = (timeline_time - self.start_t) * self.speed + self.in_t
        cut_local = float(timeline_time) - float(clip_start)
        # 右半 Clip
        right = self.clone()
        right.uid = uuid.uuid4().hex     # Q154：右半是新片段（同组照旧，id 不能撞）
        right.in_t = offset_in_source
        right.start_t = timeline_time
        # 左半（修改 self）
        self.out_t = offset_in_source
        from cutora.render.keyframe import has_keyframes, split_keyframes
        if has_keyframes(self):
            split_keyframes(
                self, right, cut_local,
                left_dur=float(self.effective_duration),
                right_dur=float(right.effective_duration),
            )
        return right


def active_clips_at(track, t):
    """Clips on ``track`` covering ``t``, in list order (bottom → top).

    Z-order between overlapping clips is the track stack, not start_t.
    """
    t = float(t)
    active = []
    for clip in getattr(track, "clips", []) or []:
        if getattr(clip, "hidden", False):      # Q152-G：图层面板里关掉的层
            continue
        try:
            start_t = float(getattr(clip, "start_t", 0.0))
            eff_dur = float(getattr(clip, "effective_duration", 0.0))
            if eff_dur <= 0:
                in_t = float(getattr(clip, "in_t", 0.0))
                out_t = float(getattr(clip, "out_t", in_t))
                eff_dur = max(0.0, out_t - in_t)
        except Exception:
            continue
        if start_t <= t < start_t + eff_dur:
            active.append(clip)
    return active


class TransitionItem:
    """Standalone Transition Entity.
    Acts as an independent NLE rendering director over a defined timeline duration,
    overriding the intrinsic frames of any underlying clips.
    """
    def __init__(self, start_t, duration, preset,
                 zoom_scale_start=0.7, motion_blur=0.4, domain="visual",
                 intensity=1.0, easing="ease_in_out", direction=None,
                 angle=0.0, feather=0.0):
        self.id = uuid.uuid4().hex
        self.start_t = start_t
        self.duration = duration
        self.preset = preset
        self.zoom_scale_start = float(zoom_scale_start)  # 进场缩放起始倍率 (0.5–1.0)
        self.motion_blur = float(motion_blur)            # 运动模糊强度 (0.0–1.0)
        self.domain = "audio" if str(domain or "visual") == "audio" else "visual"
        self.intensity = float(intensity)
        self.easing = str(easing or "ease_in_out")
        self.direction = direction
        try:
            self.angle = float(angle or 0.0)
        except (TypeError, ValueError):
            self.angle = 0.0
        try:
            self.feather = float(feather or 0.0)
        except (TypeError, ValueError):
            self.feather = 0.0

    @property
    def is_visual(self):
        return str(getattr(self, "domain", "visual") or "visual") != "audio"

    @property
    def end_t(self):
        return self.start_t + self.duration

    def clone(self):
        import copy
        return copy.deepcopy(self)

TRACK_HEIGHT_COMPACT = 24
TRACK_HEIGHT_STANDARD = 40
TRACK_HEIGHT_HIGH = 64
TRACK_HEIGHT_MAX = 160
TRACK_HEIGHT_CYCLE = (
    TRACK_HEIGHT_COMPACT, TRACK_HEIGHT_STANDARD, TRACK_HEIGHT_HIGH,
)


def clamp_track_height(height) -> float:
    try:
        h = float(height)
    except (TypeError, ValueError):
        h = float(TRACK_HEIGHT_STANDARD)
    if h != h:
        h = float(TRACK_HEIGHT_STANDARD)
    return max(float(TRACK_HEIGHT_COMPACT), min(float(TRACK_HEIGHT_MAX), h))


class Track:
    # Domain 分类规则（与 timeline.py 中的 domain_of() 保持一致）
    _AUDIO_TYPES = frozenset(("audio", "bgm", "sfx"))

    def __init__(self, track_type):
        self.id = uuid.uuid4().hex
        self.type = track_type
        self.clips = []
        self.transitions = []  # List of TransitionItem objects
        self.enable_batch = False
        self.is_main_video = False

        # === 轨道域（强约束：一经创建不可更改）===
        self.domain = "audio" if track_type in self._AUDIO_TYPES else "visual"

        # === 轨道显示 & 排序 ===
        self.visible = True
        self.locked = False
        self.mute = False

        # === 轨道顺序（用户操作顺序，越大越靠上）===
        self.order = 0
        self.pinned = False  # T33 图钉：会话状态，不存工程（同 solo）

        # === 显示名（空则 UI 用类型标签）===
        self.name = ""

        # === 轨道变换 / 不透明 / 混合（默认恒等）===
        self.props = {
            "x": 0.0,
            "y": 0.0,
            "scale": 1.0,
            "rotation": 0.0,
            "flip_x": False,
            "flip_y": False,
            "opacity": 1.0,
            "blend_mode": "normal",
        }

        # T34 分屏：None = 不参与（全屏叠最上）；"A"/"B"/… = 填到该格子
        self.split_cell = None

        # Q155 整幅背景：""= 普通轨；"backdrop" = 外部视频背景（铺满、循环，不进分屏格子）
        self.bg_role = ""

        # Q197：音频角色轨。"" / "main" / "bgm" / "sfx"。Track.type 仍是 "audio"。
        self.audio_role = ""

        # Q33: 轨道高度（像素），紧凑 / 标准 / 高；可拖、可记忆
        self.height = TRACK_HEIGHT_STANDARD

        # === 混合模式（渲染属性，与 props.blend_mode 同步）===
        self.blend_mode = "normal"

        # === 未知字段保留（向前兼容）===
        self._extra = {}
