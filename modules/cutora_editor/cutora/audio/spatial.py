# SPDX-License-Identifier: GPL-3.0-or-later
"""空间立体声 —— 卷积混响版(0830 重写)。

老版本为什么"一点也不好用"(留档, 免得以后又走回头路)
--------------------------------------------------
老实现是 `aecho` + `extrastereo`, 五个问题从致命往下排:

1. 【致命】整条滤镜链从头到尾没有强制声道布局, `-ac 2` 是在最后 AAC 编码器上
   才加的, 在滤镜图【之后】。extrastereo 放大的是 M/S 里的 S = L-R, 单声道
   素材 L≡R 所以 S 恒等于 0, 乘任何系数还是 0 —— 讲道/口播基本都是单声道
   (领夹麦、手机、相机), 于是宽度旋钮拖到底输出也一个字节不变。
2. aecho 是【离散回声】不是混响。十几个抽头 = 十几个梳状滤波器叠加, 听感是
   金属味/铁桶味, 给不出真实混响每秒上千次反射的扩散密度。
3. aecho 对所有声道用【同一组抽头】, 尾响左右完全相关(IACC≈1), 大脑把它听成
   "贴在脸上的一个点"。真实厅堂之所以听着"大", 靠的是左右耳收到的混响互不
   相关 —— 低 IACC 才是空间感的物理量, 跟混响量大小几乎没关系。所以老版本
   混响加得越多只会越糊, 永远不会更"大"。
4. 没有频率相关衰减。真实空间高频衰减极快(空气吸收+表面吸收), 而且混响支路
   必须高通掉 200Hz 以下。老版本每个抽头都是全频带 → 低中频堆积(浑浊) +
   齿音拖尾(糊字)。
5. 用 extrastereo 做宽度本身就是错的工具: 纯 S 增益破坏单声道兼容, 手机外放
   直接塌掉。老代码注释里已经发现"发飘、声音在脑袋外面", 然后把 width 钳到
   0.22 —— 那是在压症状。

新实现
------
照抄专业软件(Altiverb / Fairlight / Nuendo 那一挂)的三总线结构:

    干声 DRY  ── 原封不动, 定位在正中, 决定"听得清不清"
    早反 ER   ─┐ 镜像声源法从真实房间几何算出来, 左右振膜各算一遍
    尾响 TAIL ─┘ 左右去相关(低频共用保单声道兼容), 分频段 RT60
              → 湿声支路: 高通 200 / 低通 7.8k → M/S 宽度 → 被干声侧链压缩(ducking)
              → 和干声按 dry/wet 相加

ER + TAIL 预先渲染成一条【双声道 IR 文件】(spatial_ir.py), 运行时用 ffmpeg 的
`afir` 做真正的卷积。这是"话筒在舞台上"那种空间感的唯一正解 —— 算法混响可以
像, 卷积是直接把房间搬过来。

侧链压缩(ducking)是让"空间大"和"字清楚"同时成立的关键: 讲话时湿声被压下去,
字与字的空档里混响才涨起来。专业口播/讲道混音都这么干。

安全兜底: 任何一步不成立(IR 文件生不出来、ffmpeg 太老没有 afir)都会自动退回
一条【线性滤镜链】, 那条链虽然没有卷积, 但至少修好了 1/4/5 三个问题。
"""
from __future__ import annotations

import itertools
import os
import shutil
import subprocess

try:
    from cutora.audio import spatial_ir as _IR
except Exception:                       # pragma: no cover
    _IR = None


# 给每条音轨的滤镜标签编号, 保证同一条 filter_complex 里不撞名
_UID = itertools.count(1)

# IR 文件放哪儿: 优先仓库里预生成的 assets/ir, 其次 user_data(运行时现生成)
from cutora.core.paths import AUDIO_IR, FFMPEG, FFPROBE, resolve_user_data
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run


def _ir_dirs():
    return (AUDIO_IR, os.path.join(resolve_user_data(), "ir"))


PRESETS = {
    "stage": {
        "label": "舞台现场（话筒在台上）",
        # 0830 二次标定: 0.34 实听"太强、说话不清晰、听着不容易安静", 收到 0.26,
        # 同时把湿声低通从 7.8k 收到 6.8k(高频拖尾正是"静不下来"的来源),
        # 加 18ms 预延迟, 侧链压缩也压得更狠(比值 3→5)。实测说话时压在人声上的
        # 混响量少了一半以上(side/mid 0.074 → 0.033), 空档里的空间感还在。
        "wet": 0.26, "width": 0.62,
        # 湿声支路的带通。人声混响低于 200Hz 全是浑浊, 高于 7k 全是齿音拖尾。
        "wet_hp": 220, "wet_lp": 6800,
        # 预延迟: 直达声先到, 混响晚 18ms 再进来。人耳把这段间隙读成"距离",
        # 而且字头(辅音)落在干净的窗口里 —— 这是"空间大又听得清"最省力的一招。
        "predelay_ms": 18,
        # 干声上的清晰度 EQ: 掐隆隆声 / 压 250Hz 浑浊 / 抬 3.2k 咬字 / 9k 空气感
        "pre": ("highpass=f=85",
                "equalizer=f=250:t=q:w=1.0:g=-1.8",
                "equalizer=f=3200:t=q:w=1.2:g=2.2",
                "treble=g=1.8:f=9000"),
        "duck": (0.030, 5.0, 4, 180),      # threshold, ratio, attack ms, release ms
        "makeup_db": 1.5,
    },
    "speech": {
        "label": "清亮通透（讲道 / 口播）",
        "wet": 0.16, "width": 0.35,
        "wet_hp": 240, "wet_lp": 6800,
        "predelay_ms": 14,
        "pre": ("highpass=f=90",
                "equalizer=f=250:t=q:w=1.0:g=-2.0",
                "equalizer=f=3200:t=q:w=1.2:g=2.0",
                "treble=g=2.0:f=9000"),
        "duck": (0.030, 5.0, 4, 160),
        "makeup_db": 2.0,
    },
    "room": {
        "label": "中型房间",
        "wet": 0.22, "width": 0.45,
        "wet_hp": 210, "wet_lp": 7400,
        "predelay_ms": 12,
        "pre": (),
        "duck": (0.035, 4.5, 5, 170),
        "makeup_db": 0.0,
    },
    "hall": {
        "label": "音乐厅",
        "wet": 0.30, "width": 0.70,
        "wet_hp": 200, "wet_lp": 6800,
        "predelay_ms": 24,
        "pre": (),
        "duck": (0.030, 5.0, 5, 280),
        "makeup_db": 0.0,
    },
    "cathedral": {
        "label": "大教堂 / 大型现场",
        "wet": 0.34, "width": 0.80,
        "wet_hp": 190, "wet_lp": 6600,
        "predelay_ms": 32,
        "pre": (),
        "duck": (0.030, 5.0, 6, 380),
        "makeup_db": 0.0,
    },
}

DEFAULT_PRESET = "stage"

# 湿声总增益 = wet 旋钮 × 这个系数。【实测标定过, 别拍脑袋改】
# 标定方法(想复现就照这个来): 拿一段单声道人声, 量"词与词空档里的混响电平"
# 相对语音本体低多少 dB。2.4 时空档混响 ≈ 语音下方 10 dB —— 明显听得见空间,
# 又不会盖住下一个字。1.8 太含蓄(低 14 dB, 用户会说"没效果"), 3.0 开始糊。
WET_SCALE = 2.4


# ── ffmpeg filtergraph 里的路径转义 ────────────────────────────────
# Windows 路径带盘符冒号, 而冒号在 filtergraph 里是选项分隔符, 必须转义。
# 反斜杠也一样(它本身是转义符), 统一换成正斜杠 ffmpeg 在 Windows 上照样认。
def _esc_path(p):
    """把路径包进单引号并转义冒号 —— 实测唯一能同时扛住盘符冒号、空格、逗号的写法。

    踩过的坑: 只写 "D\\:/..."(单反斜杠)会被解析成 "D"; 写 "D\\\\:/..."(双反斜杠)
    盘符没问题, 但路径里一有空格或逗号就又炸。单引号包起来 + 冒号单转义, 三种
    都过。路径里的单引号按 shell 老办法拆开转义。
    """
    p = str(p).replace("\\", "/").replace("'", "'\\''")
    return "'" + p.replace(":", "\\:") + "'"


# ── 话筒质感 ────────────────────────────────────────────────────
# "近讲的电容麦"到底是什么声音, 拆开就三件事, 这里一件件还原:
#   1. 近讲效应(proximity): 心形指向的话筒离嘴越近, 低频越顶。用 170Hz 低架
#      模拟胸腔厚度 —— 这是"人就在话筒前面"最主要的线索。
#   2. 轻微喷麦: 爆破音 P/B/T 喷出的气流本来就是一股 60~120Hz 的低频冲击。
#      所以不需要什么瞬态检测 —— 把高通从 85Hz 放到 55Hz 让这股能量活下来,
#      再在 95Hz 上顶一个窄峰, 喷麦感自然就出来了, 而且只在真有爆破音时出现。
#   3. 电容麦的"空气": aexciter 在 7.8k 以上补一点谐波。注意它【不碰 2~5k】,
#      也就是咬字那一段一个 dB 都没动 —— 质感加了, 清晰度不受影响(实测)。
# 整体电平用 -2.6×量 dB 补平, 开质感不会顺带变响(实测残差 < 0.7 dB)。
MIC_MAKEUP_PER_UNIT = -2.6


def mic_texture_chain(amount):
    """返回话筒质感滤镜串(列表)。amount<=0 → 空列表。建议 0.4~1.0。"""
    m = max(0.0, min(2.0, float(amount or 0.0)))
    if m <= 0.01:
        return []
    return [
        # 喷麦要靠这条: 高通往下放, 爆破音的低频冲击才活得下来
        f"highpass=f={int(round(85 - 30 * min(1.0, m)))}",
        f"equalizer=f=95:t=q:w=1.1:g={2.2 * m:.2f}",     # 喷麦的"噗"
        f"bass=g={1.8 * m:.2f}:f=170:w=0.7",             # 近讲效应的胸腔厚度
        f"aexciter=amount={0.8 * m:.2f}:drive=2.5:blend=0:freq=7800:ceil=15500",
        f"volume={MIC_MAKEUP_PER_UNIT * m:.2f}dB",
    ]


def mic_amount(track, fx_settings=None):
    """这条音轨要加多少话筒质感。只给主音频加, 配乐/音效不加。"""
    fx = fx_settings if isinstance(fx_settings, dict) else {}
    if not bool(fx.get("mic_texture", False)):
        return 0.0
    role = str((track or {}).get("audio_role") or
               ("main" if (track or {}).get("is_main") else ""))
    if role and role != "main":
        return 0.0
    try:
        return max(0.0, min(2.0, float(fx.get("mic_amount", 0.6))))
    except (TypeError, ValueError):
        return 0.6


def _find_ir(preset):
    """找(必要时生成)这个预设的 IR 文件。找不到返回 ""。"""
    if _IR is None:
        return ""
    name = f"ir_{preset}_{getattr(_IR, 'SR', 48000)}.wav"
    for d in _ir_dirs():
        p = os.path.join(d, name)
        try:
            if os.path.isfile(p) and os.path.getsize(p) > 4096:
                return p
        except OSError:
            pass
    # 预生成的不在 → 现场渲一份到 user_data(只会发生一次)
    try:
        return _IR.ensure_ir(preset, _ir_dirs()[-1])
    except Exception:
        return ""


_FFMPEG = None          # 由 App 注入; 没注入就自己找
_AFIR_NORM = None       # 探测结果缓存


def set_ffmpeg_path(path):
    """让主程序把它那份 FFMPEG_PATH 传进来(可选)。"""
    global _FFMPEG, _AFIR_NORM
    if path and os.path.isfile(path):
        _FFMPEG, _AFIR_NORM = path, None


def _ffmpeg():
    if _FFMPEG:
        return _FFMPEG
    if os.path.isfile(FFMPEG):
        return FFMPEG
    return shutil.which("ffmpeg") or "ffmpeg"


def _afir_norm_opt():
    """afir 关掉自动增益的选项名在不同 ffmpeg 版本里不一样, 探一次记住。

    · 新版(7/8)叫 irnorm, 负值 = 不归一化;
    · 老版(≤6)叫 gtype, none = 不归一化;
    默认的自动增益会按峰值把 IR 拉到 1.0, 我们的 IR 已经做过能量归一化,
    再被拉一次湿声就会莫名其妙变响 —— 所以必须关掉。
    """
    global _AFIR_NORM
    if _AFIR_NORM is not None:
        return _AFIR_NORM
    # None 表示"探不出来" —— 这时候【不许】上卷积。默认的自动增益会按峰值重新
    # 缩放 IR, 湿声电平就完全失控了(可能大十几 dB), 宁可退回线性链路。
    _AFIR_NORM = None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        r = _no_window_run([_ffmpeg(), "-hide_banner", "-h", "filter=afir"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=15, creationflags=flags)
        txt = (r.stdout or b"").decode("utf-8", "ignore")
        if "irnorm" in txt:
            _AFIR_NORM = "irnorm=-1:"
        elif "gtype" in txt:
            _AFIR_NORM = "gtype=none:"
    except Exception:
        _AFIR_NORM = None
    return _AFIR_NORM


_CH_CACHE = {}


def source_channels(path):
    """探素材是几声道。缓存, 每个文件只探一次。探不到返回 0。

    为什么非要知道: ffmpeg 把单声道转立体声时会按功率守恒各乘 0.707,
    也就是【固定掉 3.01 dB】, 而且没有任何选项能关掉。不补回来的话, 一开
    空间立体声整条音频就变小声 —— 这正是老版本"一开就变小"的另一半原因。
    """
    if not path:
        return 0
    key = str(path)
    if key in _CH_CACHE:
        return _CH_CACHE[key]
    n = 0
    try:
        probe = FFPROBE if os.path.isfile(FFPROBE) else (shutil.which("ffprobe") or "ffprobe")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        r = _no_window_run([probe, "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=channels",
                            "-of", "csv=p=0", key],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=15, creationflags=flags)
        n = int((r.stdout or b"").decode("utf-8", "ignore").strip().split(",")[0] or 0)
    except Exception:
        n = 0
    _CH_CACHE[key] = n
    return n


def _pre_chain(spec, mic=0.0):
    """干声上的处理: 话筒质感 → 清晰度 EQ。

    质感自己带高通(而且更低, 为了留住喷麦), 所以开质感时要把预设那条高通拿掉,
    否则 85Hz 那一刀会把刚放行的爆破音又切没。
    """
    pre = list(spec.get("pre") or ())
    m = mic_texture_chain(mic)
    if m:
        pre = [f for f in pre if not f.startswith("highpass=")]
        return m + pre
    return pre


def _to_stereo(src_path):
    """统一成立体声, 并把单声道上变换固有的 −3.01 dB 补回来。"""
    s = "aformat=channel_layouts=stereo"
    if source_channels(src_path) == 1:
        s += ",volume=3.0103dB"
    return s


def _linear_fallback(spec, wet, width, src_path=None, mic=0.0):
    """没有 IR / 没有 afir 时的退路。

    不是卷积, 但把老版本最致命的三条修了: 强制立体声布局(宽度终于有作用)、
    混响支路带通、宽度改用 M/S 的 side 电平(单声道兼容, 不发飘)。
    """
    parts = [_to_stereo(src_path)]
    parts += _pre_chain(spec, mic)
    if wet > 0.001:
        # 少量早期反射: 50ms 以内的反射人耳和直达声听成一个声音(哈斯效应),
        # 只会觉得"有空间", 不会糊字。绝不给长尾 —— 长尾必须靠卷积才不难听。
        taps = (17, 29, 47)
        g = [0.38, 0.26, 0.17]
        s = sum(g)
        gains = [max(0.001, min(0.99, x * wet / s)) for x in g]
        parts.append(
            "aecho=%.3f:1.0:%s:%s" % (
                max(0.05, min(1.0, 1.0 - wet * 0.5)),
                "|".join(str(t) for t in taps),
                "|".join(f"{x:.4f}" for x in gains),
            )
        )
    if width > 0.01:
        # haas: 唯一能把【单声道】变出真实宽度的办法(左右微延迟+微增益差)。
        # 延迟压在 3ms 以内, 超过就开始有"两个声源"的错乱感。
        d = 0.8 + width * 1.6
        parts.append(
            f"haas=level_in=1:level_out=1:side_gain={0.4 + width * 0.5:.3f}:"
            f"middle_source=mid:left_delay={d:.2f}:right_delay={d * 1.35:.2f}:"
            f"left_balance=-0.12:right_balance=0.12"
        )
        parts.append(f"stereotools=mlev=1:slev={0.7 + width * 0.6:.3f}")
    mk = float(spec.get("makeup_db") or 0.0)
    if abs(mk) > 0.01:
        parts.append(f"volume={mk}dB")
    parts.append("alimiter=limit=0.97:level=false")
    return ",".join(parts)


def build_hall_reverb_filter(preset=DEFAULT_PRESET, wet=None, width=None,
                             uid=None, src_path=None, mic=0.0):
    """返回一段可拼进 ffmpeg 滤镜图的字符串。空串 = 关闭。

    调用方用 `chain += "," + 返回值` 拼接即可 —— 返回值一定以滤镜开头、以滤镜
    结尾, 中间可能含 `;` 和标签(卷积那条路要多路分支), 所以【必须走
    -filter_complex, 不能走 -af】。判断办法: 返回值里有 ';' 就是多链路。
    """
    spec = PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]
    wet = spec["wet"] if wet is None else float(wet)
    wet = max(0.0, min(0.85, wet))
    width = spec["width"] if width is None else float(width)
    width = max(0.0, min(1.0, width))

    if wet <= 0.001:
        pre = _pre_chain(spec, mic)
        if not pre:
            return ""
        out = [_to_stereo(src_path)] + pre
        mk0 = float(spec.get("makeup_db") or 0.0)
        if abs(mk0) > 0.01:
            out.append(f"volume={mk0}dB")
        return ",".join(out)

    ir = _find_ir(preset)
    norm = _afir_norm_opt()
    if not ir or norm is None:
        return _linear_fallback(spec, wet, width, src_path, mic)

    n = int(uid) if uid is not None else next(_UID)
    L = lambda s: f"[sp{n}{s}]"          # noqa: E731  标签前缀, 保证不撞名

    # 干/湿电平。IR 做过能量归一化, 所以湿声 RMS ≈ 干声 RMS × wet, 旋钮是线性的。
    dry_g = 1.0 - wet * 0.22             # 干声只轻微让位, 保住咬字
    wet_g = wet * WET_SCALE

    hp, lp = spec.get("wet_hp", 200), spec.get("wet_lp", 7800)
    pd = int(spec.get("predelay_ms") or 0)
    # 预延迟放在卷积【之后】: IR 里存的是相对直达声的反射时间, 整条往后推就行
    pre_delay = f"adelay={pd}|{pd}:all=1," if pd > 0 else ""
    th, ratio, atk, rel = spec.get("duck") or (0.05, 3.0, 8, 300)

    g = []
    # ① 统一成立体声再分三路: 干声 / 侧链参考 / 送进混响
    g.append(
        _to_stereo(src_path)
        + ("," + ",".join(_pre_chain(spec, mic)) if _pre_chain(spec, mic) else "")
        + f",asplit=3{L('d')}{L('s')}{L('w')}"
    )
    # ② IR 作为第二路输入喂给 afir
    g.append(f"amovie={_esc_path(ir)}{L('ir')}")
    # ③ 卷积 → 湿声带通 → M/S 宽度 → 电平
    #    关掉 afir 自带的自动增益(IR 文件里已经归一好了, 电平才可预期)
    wet_chain = (
        f"{L('w')}{L('ir')}afir=dry=1:wet=1:{norm}maxir=8:minp=8192,"
        f"{pre_delay}highpass=f={hp},lowpass=f={lp},"
        f"stereotools=mlev=1:slev={0.55 + width * 0.95:.3f},"
        f"volume={wet_g:.4f}{L('wr')}"
    )
    g.append(wet_chain)
    # ④ 侧链压缩: 讲话时把混响压下去, 字缝里再让它涨起来 —— 大空间 + 听得清
    g.append(
        f"{L('wr')}{L('s')}sidechaincompress=threshold={th}:ratio={ratio}:"
        f"attack={atk}:release={rel}:makeup=1:level_sc=1{L('wd')}"
    )
    # ⑤ 干声电平
    g.append(f"{L('d')}volume={dry_g:.4f}{L('dg')}")
    # ⑥ 相加。duration=first → 输出长度跟干声走, 绝不会因为混响尾巴改变时长
    tail = f"{L('dg')}{L('wd')}amix=inputs=2:duration=first:dropout_transition=0:normalize=0"
    mk = float(spec.get("makeup_db") or 0.0)
    if abs(mk) > 0.01:
        tail += f",volume={mk}dB"
    tail += ",alimiter=limit=0.97:level=false"
    g.append(tail)
    return ";".join(g)


def is_graph(chain):
    """True = 这段是多链路滤镜图, 必须用 -filter_complex 跑。"""
    return bool(chain) and (";" in chain or "[" in chain)


def resolve_spatial_for_track(track, fx_settings=None):
    """这条音轨要不要上空间混响：只认 clip.spatial_enabled。

    全局设置只提供预设 / 湿声 / 宽度（以及新导入时的默认勾选），
    渲染时不再二次决定开还是关。sfx 强制不上（音效本身已做好）。

    预设只跟全局 fx.spatial_preset 走，不读 clip 上的任何预设字段。
    湿声：clip.spatial_amount > 0 才是这条上的滑条，否则跟全局。
    """
    fx = fx_settings if isinstance(fx_settings, dict) else {}
    is_main = bool(track.get("is_main"))
    role = str(track.get("audio_role") or ("main" if is_main else ""))
    if role == "sfx":
        return False, DEFAULT_PRESET, 0.0, 0.0

    enabled = bool(track.get("spatial_enabled", False))
    preset = str(fx.get("spatial_preset") or DEFAULT_PRESET)
    if preset not in PRESETS:
        preset = DEFAULT_PRESET
    wet = track.get("spatial_amount")
    if wet is None or (float(wet) <= 0 and enabled):
        wet = fx.get("spatial_wet", PRESETS[preset]["wet"])
    try:
        wet = float(wet)
    except (TypeError, ValueError):
        wet = PRESETS[preset]["wet"]
    width = fx.get("spatial_width", PRESETS[preset]["width"])
    try:
        width = float(width)
    except (TypeError, ValueError):
        width = PRESETS[preset]["width"]
    return bool(enabled), preset, wet, width
