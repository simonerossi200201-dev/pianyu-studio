# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""
script_stitcher.py — 片段智能拼接（外挂脚本）v3 全局去重版
====================================================
由顶部菜单「脚本 → 片段智能拼接...」调用。

流程 (两阶段):
  阶段1 分析: 扫描分组 → whisperX 转录+词级时间戳 → 每段与【整段文案】全局模糊对齐
             → 按文案位置自动纠正组内顺序 (命名 b/c 写反也能排对)
             → 全局去重规划: 文案每个词在成品中只保留一次 —
               · 整段重复 / 与文案完全无关的片段 → 标记移入回收站
               · 片段内部的重复朗读/废话 (开头/中间/结尾) → 精确到秒+帧切掉,
                 多保留区间, 编码时自动重新拼接
               · 重复取舍: 质量优先, 同分保留后录的那一遍
             → 切点表保存到 _stitched/cutlist.json
  切口精修: 出/入点不按"词尾 + 固定余量"死算, 而是落在真正的静音里 ——
             词尾之后一旦有声音重新起来 (Flow 结尾常带的半秒 an/and/to 碎音,
             不管 ASR 有没有把它转出来) 就在它之前切断; 余量之内一路安静的,
             切点和老版本完全一致。接缝再做 10ms 淡入 / 30ms 淡出。
  多文件夹: 可以一次加多个素材文件夹 (一个文件夹一条视频也行)。成品、转录
             缓存、_encoded 各自写回它自己文件夹下的 _stitched, 互不覆盖;
             切点表/报告/缺失清单汇总在第一个文件夹里。
  以文件夹为一组: 不看文件名。一个文件夹里下一层的视频就是一组, 随便怎么
             命名都行。组号用文件夹名。默认关闭, 老的按文件名分组不变。
  确认汇总: 编码前弹出统计 (高置信/部分命中/低置信/回收站/内部剪切), 可选人工复核
  人工复核: 表格中可调 保留区间/拼接顺序/是否保留; 红=低置信, 橙=部分命中,
             紫=整段重复, 灰=与文案无关 (重新勾选「保留」可拯救)
  阶段2 时间线: cutlist → ClipPlacement 铺满画布、硬切、链接音频。
             不写 _stitched 中间 mp4。导入 stitched.asproj.json。

本模块不 import 主窗口 (避免循环依赖);
whisperX 模型通过 open_stitcher_dialog(...) 注入复用, 支持按语言选择对齐模型。
"""

import os
import re
import io
import csv
import time
import json
import base64

import unicodedata
import difflib
import subprocess
import threading
import traceback

import numpy as np

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QPlainTextEdit, QDoubleSpinBox, QCheckBox, QComboBox, QProgressBar,
    QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QApplication, QListWidget, QAbstractItemView
)

from cutora.core import config as _cfg
from cutora.core.config import DEVICE, FFMPEG_PATH, FFPROBE_PATH
from cutora.asr.engine import align_transcript, whisperx

# 拼接前强制体检。见 media_guard.py 顶部注释 (2026-08-23 的假 mp4 事故)。
# 这个 import 故意不写 try/except: 少了它就等于回到"坏素材静默跳过"的老样子,
# 那种情况必须炸在启动时, 而不是等两小时后发现成品少了两条。
from cutora.batch import media_guard as media_guard
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv")
# 支持两种命名: "01a"/"01b"/"01c" (字母后缀) 或 "01 (1)"/"01 (2)" (括号数字后缀)
GROUP_RE = re.compile(r"^(\d+)\s*(?:([a-zA-Z]+)|\(\s*(\d+)\s*\))?$")
# 长文案流水线的命名: Tik008-s017-p05[-b].mp4
#   Tik008 = 视频组（= 这里的组号）  s017 = 全文第几段（= 组内顺序，唯一权威）
#   p05    = 这段用的图（只做溯源，不参与排序）   -b = 同一段的第二个版本
TIK_RE = re.compile(r"^(Tik\d+)-s(\d+)(?:-p\d+)?(?:-([a-zA-Z]))?$", re.I)

# 兜底自动识别: <字母前缀><数字> <分隔符> <字母前缀><数字>
# 认得 Tik001_v01 / Ep02-03 / A01 S05 这一类"组号 + 组内序号"的通用写法。
# 只在上面三条规则都不认时才用, 所以老命名的行为一个字都不会变。
AUTO_RE = re.compile(r"^\s*([^\W\d_]*)\s*(\d+)\s*[^0-9A-Za-z]+\s*([^\W\d_]*)\s*(\d+)",
                     re.UNICODE)

# 用户在输入框里标数字位的几种常见习惯, 统统当作"这里是数字"
_PLACEHOLDER = "xX#?*0123456789nN"

def mark_to_prefix(mark):
    """'Tik00x' / 'Tik###' / 'Tik' 一律归成字面前缀 'Tik'。

    从右边一路砍掉占位符字符, 剩下的就是前缀。用户写 'v0x' 和写 'v' 结果一样 ——
    不用教他到底该怎么写占位符, 怎么顺手怎么来。
    """
    s = str(mark or "").strip()
    while s and s[-1] in _PLACEHOLDER:
        s = s[:-1]
    return s

def build_name_rule(group_mark, order_mark):
    """返回 (组正则, 序号正则, 组前缀); 两个标记都空 → None(= 不用自定义规则)。"""
    g_txt = str(group_mark or "").strip()
    o_txt = str(order_mark or "").strip()
    if not g_txt and not o_txt:
        return None
    gp = mark_to_prefix(g_txt)
    op = mark_to_prefix(o_txt)
    g_re = re.compile(re.escape(gp) + r"\s*(\d+)", re.I) if g_txt else None
    o_re = re.compile(re.escape(op) + r"\s*(\d+)", re.I) if o_txt else None
    return (g_re, o_re, gp)

def _natkey(s):
    """自然排序: 'v2' 排在 'v12' 前面, 而不是按字符串比。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]

def _tail_letter(s):
    """认 '-b' / '_b' / ' b' 这种同段第二版本的字母后缀。"""
    m = re.match(r"^[-_ ]?([a-zA-Z])(?![a-zA-Z0-9])", s)
    return m.group(1).lower() if m else ""

def _match_custom(base, rule):
    """按用户填的规则解析文件名 → (组键, 组内排序键); 不匹配返回 None。"""
    g_re, o_re, gp = rule
    if g_re is None:
        return None
    mg = g_re.search(base)
    if not mg:
        return None
    key = (gp + "%03d" % int(mg.group(1))) if gp else str(int(mg.group(1)))
    if o_re is None:
        # 只给了分组前缀 → 组内按文件名自然序
        return key, (2, 0, "", 0, _natkey(base))
    # 从组号【之后】开始找序号, 免得序号前缀正好是组前缀里的字母
    mo = o_re.search(base, mg.end()) or o_re.search(base)
    if not mo:
        return None
    return key, (2, int(mo.group(1)), _tail_letter(base[mo.end():]), 0, [])

def _match_auto(base):
    m = AUTO_RE.match(base)
    if not m:
        return None
    gp, gn, on = m.group(1) or "", m.group(2), m.group(4)
    key = (gp + "%03d" % int(gn)) if gp else str(int(gn))
    return key, (2, int(on), _tail_letter(base[m.end():]), 0, [])

# 匹配置信度阈值: 片段中命中原文案的词占比低于此值 → 视为对齐失败, 整段保留并标红
MATCH_RATIO_LOW = 0.35
# 部分命中判定: 整体低置信, 但存在 >=PARTIAL_MIN_HIT 个命中词且命中区局部密度 >=PARTIAL_MIN_DENSITY
# → 视为 "大量无关内容 + 少量正确内容", 自动剔除无关部分, 只保留命中区
PARTIAL_MIN_HIT = 4
PARTIAL_MIN_DENSITY = 0.5

# ---- 全局去重参数 (v3) ----
# 原则: 文案的每个词在成品中只允许被讲一次; 重复取舍 = 质量优先, 同分保留后录。
DEDUP_DB_TOL = 2          # 内部间隙中, 文案侧允许的未匹配词数 (ASR 错词容差)
DEDUP_MIN_CUT_WORDS = 4   # 片段侧多余词数 >= 此值才值得切一刀
DEDUP_MIN_CUT_SEC = 0.8   # 且多余时长 >= 此秒数
CUT_PAD = 0.10            # 内部切口两侧留白 (秒)
IRRELEVANT_RATIO = 0.15   # 命中率低于此且已识别出足够词 → 判为与文案完全无关
SMALL_RUN_JOIN_TOL = 2    # 孤立短 run 与大块之间隔几个未被认领的词还算挨着
QUALITY_BUCKET = 0.05     # 质量分桶粒度: 命中率差距小于一桶视为"同分" → 保留后录的

# ---- 切口精修 (0901): 在真正的静音里下刀, 不在"词尾 + 固定余量"处下刀 ----
# 现场: Tik012-s001, 文案最后一个词 year 的词尾在 4.532s, 4.63s 起还有一句
# 该剪掉的 "to sod"。老规则 出点 = 词尾 + 尾部余量(0.35) = 4.882s —— 正好落在
# "to" 的中间, 成品里就留下小半个音, 接上下一段一听就跳。Flow 渲染的片段结尾
# 经常带这种半秒的 an / and / to 之类的碎音, 固定余量必然会剐到。
#
# 新规则: 从词尾往后走, 一旦声音重新抬起来 —— 相对这段静音的最低点抬升
# EDGE_RISE dB, 并且绝对电平高过本片噪声底 EDGE_ABS_MARGIN dB —— 就在抬升之前
# 下刀; 一路安静到尾部余量为止的, 出点和老版本一个数都不差。
# 实测 Tik012 那 18 段: 只有 s001 (to sod) 和 s007 (too lord) 两段切点变了,
# 另外 16 段完全不动。抬升要"同时"满足相对和绝对两个条件, 是为了不让室内底噪
# 自己抖动 10 dB 就把切点提前 —— 那会让每段都莫名其妙变短。
EDGE_REFINE_DEFAULT = True
ENV_HOP = 0.010           # 包络步长 (秒)
ENV_WIN = 0.025           # 包络窗长 (秒)
EDGE_RISE = 9.0           # 相对本段静音最低点抬升多少 dB 算"声音回来了"
EDGE_ABS_MARGIN = 12.0    # 且必须高过本片噪声底这么多 dB
EDGE_GUARD = 0.020        # 切点离杂音起点再退这么多, 留安全距离
EDGE_MIN_TAIL = 0.030     # 词尾之后无论如何留这么久, 不许削到词尾
HEAD_PAD = 0.30           # 首段裁头时词前保留的余量 (老值, 保持不变)

# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------

def _si():
    """Windows 下隐藏子进程黑框。"""
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return si
    return None

def _run(cmd, timeout=None):
    return _no_window_run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        startupinfo=_si(), timeout=timeout
    )

def probe_media(path):
    """返回 {duration, width, height, fps, has_audio}。"""
    info = {"duration": 0.0, "width": 0, "height": 0, "fps": 30.0, "has_audio": False}
    try:
        res = _run([
            FFPROBE_PATH, "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,width,height,r_frame_rate",
            "-of", "json", path
        ])
        data = json.loads(res.stdout.decode("utf-8", errors="ignore") or "{}")
        dur = (data.get("format") or {}).get("duration")
        if dur:
            info["duration"] = float(dur)
        for st in data.get("streams") or []:
            if st.get("codec_type") == "video" and not info["width"]:
                info["width"] = int(st.get("width") or 0)
                info["height"] = int(st.get("height") or 0)
                fr = st.get("r_frame_rate") or "30/1"
                try:
                    num, den = fr.split("/")
                    if float(den) > 0:
                        info["fps"] = float(num) / float(den)
                except Exception:
                    pass
            elif st.get("codec_type") == "audio":
                info["has_audio"] = True
    except Exception:
        pass
    return info

def audio_window_cmd(path, start=None, end=None):
    """ffmpeg 命令：整段，或只解码 [start, end)。

    ``end`` 有值时一定带 ``-t``，解码在窗口结束处停下，不会把 3、4 分钟
    的成片整段送进内存。窗口长于 30 秒、或结束点晚于 60 秒，直接拒绝。
    """
    cmd = [FFMPEG_PATH, "-nostdin", "-v", "error", "-i", str(path)]
    if end is not None:
        ws = 0.0 if start is None else float(start)
        we = float(end)
        length = we - ws
        if length <= 0.05:
            raise RuntimeError("时间窗口太短")
        if length > 30.0 + 0.051:
            raise RuntimeError("时间窗口不能超过 30 秒")
        if we > 60.0 + 0.051:
            raise RuntimeError("只允许转写视频开头 60 秒以内")
        if ws > 0.05:
            cmd += ["-ss", "%.3f" % ws]
        # -vn：这几秒不要解码画面。-t 卡在输出上，读到窗口结尾就停。
        cmd += ["-vn", "-t", "%.3f" % length]
    cmd += ["-f", "f32le", "-ac", "1", "-ar", "16000", "-"]
    return cmd


def load_audio_16k(path, start=None, end=None):
    """用内置 ffmpeg 解码为 16kHz 单声道 float32 (等价 whisperx.load_audio, 不依赖 PATH)。

    ``end`` 给出时只解码那一小段。批量定位标题只用开头十几秒，
    不能把整条 3、4 分钟的片子送进显卡。
    """
    cmd = audio_window_cmd(path, start, end)
    res = _run(cmd)
    if res.returncode != 0:
        raise RuntimeError(f"音频解码失败: {os.path.basename(path)}")
    return np.frombuffer(res.stdout, dtype=np.float32).copy()

# ---- 音频包络 (切口精修用) ----
# 只要一条 10ms 一帧的 dB 曲线就够了: 用累加和算 RMS, 8 秒片段不到 1 毫秒,
# 比再跑一遍 VAD 便宜得多, 而且不引入任何新依赖。

def compute_envelope(audio, sr=16000):
    """16k 单声道 float32 → {'db': 每 10ms 一帧的 dB, 'hop':…, 'floor': 噪声底}。"""
    try:
        a = np.asarray(audio, dtype=np.float64)
        hop, win = int(sr * ENV_HOP), int(sr * ENV_WIN)
        if a.size < win + hop:
            return None
        n = 1 + (a.size - win) // hop
        c = np.concatenate(([0.0], np.cumsum(a * a)))
        st = hop * np.arange(n)
        ms = (c[st + win] - c[st]) / float(win)
        db = 10.0 * np.log10(ms + 1e-12)
        return {"db": db.astype(np.float32), "hop": ENV_HOP,
                "floor": float(np.percentile(db, 5))}
    except Exception:
        return None


INCOMPLETE_TAIL_SEC = 1.0
TAIL_SPEECH_DB = 8.0


def last_word_end(words):
    ends = []
    for w in words or []:
        try:
            ends.append(float(w[2]))
        except (TypeError, ValueError, IndexError):
            continue
    return max(ends) if ends else 0.0


def tail_has_speech(env, t0, dur):
    """True when mean envelope after t0 is well above the noise floor."""
    if not env or env.get("db") is None:
        return False
    db = np.asarray(env["db"], dtype=np.float64)
    hop = float(env.get("hop") or ENV_HOP)
    floor = float(env.get("floor") or -80.0)
    i0 = max(0, int(float(t0) / hop))
    i1 = min(len(db), int(float(dur) / hop) + 1)
    if i1 <= i0:
        return False
    return float(np.mean(db[i0:i1])) >= floor + TAIL_SPEECH_DB


def transcript_incomplete(words, dur, env=None):
    """转写结束点比音频早 1s 以上，且尾段包络仍有能量。"""
    last = last_word_end(words)
    if float(dur or 0) - last < INCOMPLETE_TAIL_SEC:
        return False
    if env is None:
        return False
    return tail_has_speech(env, last, dur)

def env_to_b64(env):
    """包络也存进转录缓存: 0.5dB 一档的 uint8, 8 秒片段约 800 字节。

    存下来是为了"文件没动就不重算"这条规矩对包络同样成立 —— 否则每次重跑
    都要把几百个 mp4 重新解码一遍音频, 白等几分钟。
    """
    if not env:
        return ""
    try:
        q = np.clip(np.rint(-np.asarray(env["db"], dtype=np.float64) * 2.0), 0, 255)
        return base64.b64encode(q.astype(np.uint8).tobytes()).decode("ascii")
    except Exception:
        return ""

def env_from_b64(s):
    try:
        if not s:
            return None
        q = np.frombuffer(base64.b64decode(s), dtype=np.uint8)
        if q.size < 8:
            return None
        db = -(q.astype(np.float32) / 2.0)
        return {"db": db, "hop": ENV_HOP, "floor": float(np.percentile(db, 5))}
    except Exception:
        return None

# ---- 数字/符号 归一化 (修复 ASR 输出 "20" 与文案 "twenty" 无法匹配的问题) ----
# 统一方向: 一律转成【英文拼写】。因为 words→digits 需要解析组合词 (twenty five→25),
# 而 digits→words 是确定性的, 不会歧义。
_NUM_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven",
             "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
             "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_NUM_TENS = ["", "", "twenty", "thirty", "forty", "fifty",
             "sixty", "seventy", "eighty", "ninety"]
_NUM_SCALE = [(1_000_000_000, "billion"), (1_000_000, "million"),
              (1000, "thousand"), (100, "hundred")]

# 序数 → 基数词干 (1st/first 与 1/one 视为不同词, 各自归一)
_ORDINAL_IRREG = {
    "first": "first", "second": "second", "third": "third", "fifth": "fifth",
    "eighth": "eighth", "ninth": "ninth", "twelfth": "twelfth",
}
_ORD_SUFFIX = {1: "st", 2: "nd", 3: "rd"}

# 符号 → 读法 (置于数字之后, 与口播顺序一致)
_SYM_AFTER = {"%": ["percent"], "°": ["degrees"]}
_SYM_BEFORE_CUR = {"$": ["dollars"], "￥": ["yuan"], "¥": ["yuan"],
                   "€": ["euros"], "£": ["pounds"]}
_SYM_WORD = {"&": ["and"], "+": ["plus"], "=": ["equals"], "@": ["at"]}

def _int_to_words(n):
    """非负整数 → 英文词列表。0 ≤ n < 10^12。"""
    if n < 20:
        return [_NUM_ONES[n]]
    if n < 100:
        t, r = divmod(n, 10)
        return [_NUM_TENS[t]] + ([_NUM_ONES[r]] if r else [])
    for val, name in _NUM_SCALE:
        if n >= val:
            head, rest = divmod(n, val)
            out = _int_to_words(head) + [name]
            if rest:
                out += _int_to_words(rest)
            return out
    return [_NUM_ONES[0]]

def _ordinal_words(n):
    """整数 → 序数英文词列表, 如 21 → ['twenty','first']。"""
    ws = _int_to_words(n)
    last = ws[-1]
    irreg = {"one": "first", "two": "second", "three": "third", "five": "fifth",
             "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
    if last in irreg:
        ws[-1] = irreg[last]
    elif last.endswith("y"):
        ws[-1] = last[:-1] + "ieth"
    else:
        ws[-1] = last + "th"
    return ws

def _numeric_to_words(s):
    """把一个含数字的裸串转成英文词列表; 无法处理则返回 None。
    支持: 20 / 1,000 / 3.5 / 1st / 2nd / 10th"""
    s = s.replace(",", "")
    m = re.fullmatch(r"(\d+)(st|nd|rd|th)", s, flags=re.I)
    if m:
        return _ordinal_words(int(m.group(1)))
    m = re.fullmatch(r"(\d+)\.(\d+)", s)
    if m:
        out = _int_to_words(int(m.group(1))) + ["point"]
        for ch in m.group(2):
            out.append(_NUM_ONES[int(ch)])
        return out
    if re.fullmatch(r"\d+", s):
        n = int(s)
        if n < 10 ** 12:
            return _int_to_words(n)
    return None

_ARABIC_NORM_MAP = str.maketrans({
    "\u0640": "",   # tatweel
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ة": "ه",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
})
_CJK_NORM_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def _fold_multilingual_token(s: str) -> str:
    """Fold Arabic variants and Latin diacritics (Malagasy ô→o, Swahili, etc.)."""
    import unicodedata
    s = s.translate(_ARABIC_NORM_MAP)
    out = []
    for ch in unicodedata.normalize("NFKD", s):
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me"):
            continue
        out.append(ch)
    return unicodedata.normalize("NFKC", "".join(out))


def norm_tokens(w):
    """归一化一个原始词 → token 列表 (可能 0 个、1 个或多个)。

    小写、去标点; 连字符/斜杠拆分; 数字与符号统一展开为英文拼写:
      "20"          → ['twenty']
      "25"          → ['twenty','five']
      "twenty-five" → ['twenty','five']
      "50%"         → ['fifty','percent']
      "$100"        → ['one','hundred','dollars']
      "1st"         → ['first']
    中日韩字符按单字切分；阿拉伯语/马尔加什语/斯瓦希里语自动归一变体与重音。
    返回 [] 表示应丢弃。
    """
    raw = _fold_multilingual_token((w or "").strip().lower())
    if not raw:
        return []
    if _CJK_NORM_RE.search(raw):
        out_cjk = []
        buf = ""
        for ch in raw:
            if _CJK_NORM_RE.match(ch):
                if buf:
                    out_cjk.extend(norm_tokens(buf))
                    buf = ""
                out_cjk.append(ch)
            else:
                buf += ch
        if buf:
            out_cjk.extend(norm_tokens(buf))
        return [t for t in out_cjk if t]
    out = []
    # 时间 "3:30" 必须在下面那步 re.sub 吃掉冒号之前处理掉。
    # 否则会被压成 "330" → three hundred thirty, 而口播念的是 "three thirty",
    # 两边差 1~2 个词, 是数字类误报的常见来源之一。
    colon_src = raw.strip("()[]{}\"'“”‘’")
    mt = re.fullmatch(r"(\d{1,2}):([0-5]\d)(?::([0-5]\d))?", colon_src)
    if mt:
        return [g.lstrip("0") or "0" for g in mt.groups() if g]
    # 经文 13:3 / 16:13-15 / 44:6)（括号会挡住上面那条）
    ms = re.fullmatch(r"(\d{1,3}):(\d{1,3})(?:-(\d{1,3}))?", colon_src)
    if ms:
        return [g.lstrip("0") or "0" for g in ms.groups() if g]
    # 连字符/斜杠先拆, 否则 "twenty-five" 会被压成 "twentyfive"
    for part in re.split(r"[-–—/]+", raw):
        if not part:
            continue
        # 货币符号: 记下来, 数字读完再补 (口播是 "one hundred dollars")
        cur = []
        for sym, words in _SYM_BEFORE_CUR.items():
            if sym in part:
                cur = list(words)
                part = part.replace(sym, "")
        tail = []
        for sym, words in _SYM_AFTER.items():
            if sym in part:
                tail += words
                part = part.replace(sym, "")
        for sym, words in _SYM_WORD.items():
            if sym in part:
                out += words
                part = part.replace(sym, "")
        core = re.sub(r"[^\w.]+", "", part, flags=re.UNICODE)
        core = core.strip(".")
        if core:
            # 年代 "1990s" / "90s": 老代码不认, 整体当一个 token, 撞上口播的
            # "nineteen nineties" 必挂。这里剥掉复数 s 交给数字折叠处理。
            md = re.fullmatch(r"(\d{2,4})s", core)
            if md:
                core = md.group(1)
            if any(ch.isdigit() for ch in core):
                nw = _numeric_to_words(core)
                if nw is not None:
                    out += nw
                else:
                    # 形如 "3d" / "h264": 数字与字母混排, 保守处理为整体 token
                    out.append(re.sub(r"[^\w]+", "", core, flags=re.UNICODE))
            else:
                out.append(re.sub(r"[^\w]+", "", core, flags=re.UNICODE))
        out += cur + tail
    return [t for t in out if t]

def norm_token(w):
    """兼容旧签名: 返回单个 token (多 token 时拼接)。新代码请用 norm_tokens。"""
    return "".join(norm_tokens(w))

# ---- 数字折叠: 把"英文数字词串"折回数字 ----
# 为什么不是把数字展开成单词 (老做法):
#   展开是【一对多】的 —— 2026 可以读成
#       two thousand twenty six / two thousand and twenty six / twenty twenty six
#   老代码只产出其中一种, 口播念了另一种就对不上, 中间留下 2~4 个词的洞,
#   跟旁边一个 ASR 错词一凑就越过"3 词以上算缺失"的报警线 → 误报。
#   代码注释里写 "digits→words 是确定性的, 不会歧义" —— 这个前提本身不成立。
# 折回数字是【多对一】的: 所有读法都归到同一个数字, 天然吃掉全部变体。
#
# 安全性来自【对称】: 文案侧和 ASR 侧跑的是同一个函数。即使某个折叠不完美
# (比如把代词 one 也折成 1), 两边折得一模一样, 对匹配没有任何影响。
_W2N_UNIT = {w: i for i, w in enumerate(_NUM_ONES)}          # zero..nineteen
_W2N_TENS = {w: i * 10 for i, w in enumerate(_NUM_TENS) if w}  # twenty..ninety
# 年代读法: "nineteen nineties" → 1990
_W2N_DECADE = {"twenties": 20, "thirties": 30, "forties": 40, "fifties": 50,
               "sixties": 60, "seventies": 70, "eighties": 80, "nineties": 90}
_W2N_SCALE_W = {"hundred": 100, "thousand": 1000,
                "million": 1_000_000, "billion": 1_000_000_000}

def _read_1_99(toks, i):
    """读 0~99: "twenty" / "twenty five" / "seven" / "nineties"。"""
    n = len(toks)
    if i >= n:
        return None, i
    t = toks[i]
    if t in _W2N_TENS:
        v = _W2N_TENS[t]
        i += 1
        # 只有 tens 后面【紧跟个位】才合并 (twenty five=25);
        # 后面若是另一个 tens (twenty twenty), 说明是年份读法, 交给外层。
        if i < n and toks[i] in _W2N_UNIT and 1 <= _W2N_UNIT[toks[i]] <= 9:
            v += _W2N_UNIT[toks[i]]
            i += 1
        return v, i
    if t in _W2N_DECADE:
        return _W2N_DECADE[t], i + 1
    if t in _W2N_UNIT:
        return _W2N_UNIT[t], i + 1
    return None, i

def _read_simple_number(toks, i):
    """读 0~999 (含 hundred), 不跨 thousand/million。"""
    n = len(toks)
    start = i
    if i < n and toks[i] in ("a", "an") and i + 1 < n and toks[i + 1] == "hundred":
        cur, i = 1, i + 1                       # "a hundred" = 100
    else:
        cur, i2 = _read_1_99(toks, i)
        if cur is None:
            if i < n and toks[i] == "hundred":
                cur = 1                         # 裸 "hundred"
            else:
                return None, start
        else:
            i = i2
    if i < n and toks[i] == "hundred":
        cur *= 100
        i += 1
        j = i
        if j < n and toks[j] == "and":          # two hundred and five
            j += 1
        v2, j2 = _read_1_99(toks, j)
        if v2 is not None:
            cur += v2
            i = j2
    return cur, i

def _read_number(toks, i):
    """读一个完整数字: 含 thousand/million, 含 "twenty twenty six" 年份读法。"""
    n = len(toks)
    total, got, j = 0, False, i
    while True:
        v, j2 = _read_simple_number(toks, j)
        if v is None:
            break
        if j2 < n and toks[j2] in ("thousand", "million", "billion"):
            total += v * _W2N_SCALE_W[toks[j2]]
            got = True
            j = j2 + 1
            if j < n and toks[j] == "and":       # two thousand and twenty six
                j += 1
            continue
        total += v
        got = True
        j = j2
        break
    if not got:
        return None, i
    # 年份读法: "twenty twenty six"=2026 / "nineteen ninety five"=1995
    # 判据: 前半是 10~99 且没有量级词参与, 后面紧跟另一个 1~99 的数。
    # "twenty five" 这种 tens+ones 已经在 _read_1_99 里被吃成 25, 不会误判。
    if 10 <= total <= 99 and j < n:
        v2, j2 = _read_number(toks, j)
        if v2 is not None and 1 <= v2 <= 99:
            return total * 100 + v2, j2
    return total, j

def fold_numbers(toks, spans=None):
    """把 token 列表里的英文数字词串折回数字字符串。

    spans 给定时 (ASR 侧), 同步合并时间戳: 折出来的数字取
    第一个词的 start 和最后一个词的 end。返回 (新 toks, 新 spans)。
    """
    out, out_spans = [], []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if (t in _W2N_UNIT or t in _W2N_TENS or t in _W2N_DECADE
                or t in _W2N_SCALE_W
                or (t in ("a", "an") and i + 1 < n
                    and toks[i + 1] in _W2N_SCALE_W)):
            val, j = _read_number(toks, i)
            if val is not None and j > i:
                out.append(str(val))
                if spans is not None:
                    out_spans.append((spans[i][0], spans[j - 1][1]))
                i = j
                continue
        out.append(t)
        if spans is not None:
            out_spans.append(spans[i])
        i += 1
    return (out, out_spans) if spans is not None else (out, None)

# ---- 0904 修: 标点后面没空格 → 两个词被粘成一个 token ----------------
# 文案里大量出现 "bless you,two"、"this.When"、"labour.”Ecclesiastes" 这种
# 标点后不带空格的写法（源文案本身就这样）。tokenize 只按空白切，norm_tokens
# 又把标点直接删掉，于是粘成 "youtwo" / "thiswhen" / "labourecclesiastes"。
# 口播那边 ASR 出来的永远是分开的两个词，对不上 → 每个句子交界处都开一个
# 1~3 词的洞 → find_missing 把它们报成"缺失文案，请重新生成"。
# 实测 Tik017 的 6 个洞里 5 个、Tik018 的 3 个洞全部都是这么来的，
# 片段本身好好的，什么都不用重生成。
# 这里在切词之前把「字母 + 标点 + 字母」的标点后面补一个空格。
# 只在两边都是字母时才补，所以 "4:9"、"22:10" 这类数字不受影响。
_GLUED_PUNCT = re.compile(
    r'(?<=[^\W\d_])([.,;:!?\u2026\u060C\u061B\u061F]+[\u201d"\u2019\')\]\u00bb]*)(?=[^\W\d_]|\u201c|"|\u2018|\'|\(|\[|\u00ab)')

def unglue_punct(text):
    """把标点后面漏掉的空格补回来。切词前调用，纯预处理，不改别的。"""
    return _GLUED_PUNCT.sub(r'\1 ', text or "")

# 经文卷名（口播 / 文案归一化用）。John 16:13 vs ASR「john 1613」。
_BOOKS = frozenset({
    "genesis", "exodus", "leviticus", "numbers", "deuteronomy",
    "joshua", "judges", "ruth", "samuel", "kings", "chronicles",
    "ezra", "nehemiah", "esther", "job", "psalm", "psalms",
    "proverbs", "ecclesiastes", "isaiah", "jeremiah", "lamentations",
    "ezekiel", "daniel", "hosea", "joel", "amos", "obadiah", "jonah",
    "micah", "nahum", "habakkuk", "zephaniah", "haggai", "zechariah",
    "malachi", "matthew", "mark", "luke", "john", "acts", "romans",
    "corinthians", "galatians", "ephesians", "philippians", "colossians",
    "thessalonians", "timothy", "titus", "philemon", "hebrews", "james",
    "peter", "jude", "revelation",
})


_BOOK_CHAPTERS = {
    "matthew": 28, "mark": 16, "luke": 24, "john": 21, "acts": 28,
    "romans": 16, "corinthians": 16, "galatians": 6, "ephesians": 6,
    "philippians": 4, "colossians": 4, "thessalonians": 5, "timothy": 6,
    "titus": 3, "philemon": 1, "hebrews": 13, "james": 5, "peter": 5,
    "jude": 1, "revelation": 22, "genesis": 50, "exodus": 40,
    "leviticus": 27, "numbers": 36, "deuteronomy": 34, "psalm": 150,
    "psalms": 150, "proverbs": 31, "isaiah": 66, "jeremiah": 52,
}
_SCRIPTURE_FILLER = frozenset({
    "chapter", "chapters", "ch", "verse", "verses", "vs", "v",
})
_ACRONYMS = frozenset({
    "kjv", "nkjv", "esv", "niv", "nlt", "nasb", "csb", "rsv", "nrsv",
    "asv", "amp",
})
_COLON_REF_RE = re.compile(r"^(\d{1,3}):(\d{1,3})(?:-(\d{1,3}))?$")
INCOMPLETE_MARK = "⚠ 转写可能不完整"


def _split_glued_cv(num, book=None):
    """'1613' after a book name → ['16', '13'] if it looks like chapter:verse."""
    s = str(num)
    if not s.isdigit() or not (3 <= len(s) <= 4):
        return None
    if len(s) == 3:
        cands = [(s[0], s[1:]), (s[:2], s[2:])]
    else:
        cands = [(s[:2], s[2:]), (s[0], s[1:])]
    max_ch = _BOOK_CHAPTERS.get(book or "", 150)
    valid = []
    for ch, vs in cands:
        ch_n = ch.lstrip("0") or "0"
        vs_n = vs.lstrip("0") or "0"
        ci, vi = int(ch_n), int(vs_n)
        if 1 <= ci <= max_ch and 1 <= vi <= 176:
            valid.append((vi, -ci, [ch_n, vs_n]))
    if not valid:
        return None
    valid.sort()
    return valid[0][2]


def fold_glued_verses(toks, spans=None):
    """john 1613 / 16:13 / sixteen thirteen / chapter 16 verse 13 → john 16 13."""
    out, out_spans = [], []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        sp = spans[i] if spans is not None else None
        if t in _SCRIPTURE_FILLER:
            nxt = toks[i + 1] if i + 1 < n else ""
            if nxt.isdigit() or nxt in _SCRIPTURE_FILLER or _COLON_REF_RE.match(nxt or ""):
                i += 1
                continue
        m = _COLON_REF_RE.match(t)
        if m:
            for g in m.groups():
                if not g:
                    continue
                out.append(g.lstrip("0") or "0")
                if spans is not None:
                    out_spans.append(sp)
            i += 1
            continue
        book = out[-1] if out else None
        if t.isdigit() and book in _BOOKS:
            split = _split_glued_cv(t, book)
            if split:
                for part in split:
                    out.append(part)
                    if spans is not None:
                        out_spans.append(sp)
                i += 1
                continue
        out.append(t)
        if spans is not None:
            out_spans.append(sp)
        i += 1
    return (out, out_spans) if spans is not None else out


def fold_acronyms(toks, spans=None):
    """K J V / kjv → kjv. Both sides, so matching stays symmetric."""
    out, out_spans = [], []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if len(t) == 1 and t.isalpha():
            letters, j = [t], i + 1
            while j < n and len(toks[j]) == 1 and toks[j].isalpha():
                letters.append(toks[j])
                j += 1
            joined = "".join(letters)
            if joined in _ACRONYMS:
                out.append(joined)
                if spans is not None:
                    out_spans.append((spans[i][0], spans[j - 1][1]))
                i = j
                continue
        out.append(t)
        if spans is not None:
            out_spans.append(spans[i])
        i += 1
    return (out, out_spans) if spans is not None else out


def _fold_for_match(toks, spans=None):
    folded, sp = fold_numbers(toks, spans=spans)
    if spans is None:
        return fold_acronyms(fold_glued_verses(folded)), None
    folded, sp = fold_glued_verses(folded, spans=sp)
    folded, sp = fold_acronyms(folded, spans=sp)
    return folded, sp


def tokenize(text):
    toks = []
    for raw in re.split(r"\s+", unglue_punct(text)):
        toks.extend(norm_tokens(raw))
    return _fold_for_match(toks)[0]

def tokenize_raw_pairs(text):
    """返回 (归一化 token 列表, 对应原始词列表) — 两者索引一一对应。
    一个原始词展开成多个 token 时, 只在【首个】token 位置放原文,
    后续位置放 ''; 渲染缺失原文时过滤空串即可, 不会出现重复词。"""
    toks, raws = [], []
    for raw in re.split(r"\s+", unglue_punct(text)):
        ts = norm_tokens(raw)
        for i, t in enumerate(ts):
            toks.append(t)
            raws.append(raw if i == 0 else "")
    # 数字折叠会把多个 token 合成一个, raws 要跟着合并:
    # 取这一段里第一个非空原文, 保证缺失清单打印出来还是人读得懂的原话。
    idx = list(range(len(toks)))
    folded, spans = _fold_for_match(toks, spans=[(k, k) for k in idx])
    new_raws = []
    for (a, b) in spans:
        chunk = [r for r in raws[a:b + 1] if r]
        new_raws.append(chunk[0] if chunk else "")
    return folded, new_raws

# ----------------------------------------------------------------------
# 素材分组 与 文案解析
# ----------------------------------------------------------------------

def _gkey(k):
    """组排序键: 纯数字组按数值升序排前面, Tik 组按名字排后面。"""
    s = str(k)
    return (0, int(s), "") if s.isdigit() else (1, 0, s.lower())

def scan_groups(folder, group_mark=None, order_mark=None):
    """扫描文件夹 → {'01': [path_a, ...]} (组升序, 组内按序号排序)。

    识别顺序(先到先得, 后面的只处理前面认不出来的文件):
      0. 用户自定义规则 —— group_mark / order_mark, 例如 'Tik00x' + 'v0x'
      1. 纯数字命名 01a / 01b, 或 01 (1) / 01 (2)          ← 老行为, 一字未动
      2. 长文案流水线 TikNNN-sNNN-pNN[-b]                   ← 老行为, 一字未动
      3. 通用兜底 AUTO_RE: <前缀><数字><分隔><前缀><数字>    ← 新增

    第 3 条是为了 Tik001_v01.mp4 这类"一眼就很规整、老代码却扫出 0 组"的命名。
    自定义规则排在最前面, 是因为用户明确说了怎么分组时, 不该让猜测抢在前面。
    """
    rule = build_name_rule(group_mark, order_mark)
    groups = {}
    for name in sorted(os.listdir(folder), key=_natkey):
        base, ext = os.path.splitext(name)
        if ext.lower() not in VIDEO_EXTS:
            continue
        base = base.strip()
        hit = _match_custom(base, rule) if rule else None
        if hit is None:
            m = GROUP_RE.match(base)
            if m:
                letters = (m.group(2) or "").lower()
                paren = m.group(3)
                # 老命名排序键保持原三元组, Normal 组内顺序与补丁前一致
                suffix = (1, "", int(paren)) if paren is not None else (0, letters, 0)
                hit = (m.group(1), suffix)
        if hit is None:
            m = TIK_RE.match(base)
            if m:
                hit = ("Tik" + f"{int(m.group(1)[3:]):03d}",
                       (1, int(m.group(2)), (m.group(3) or "").lower(), 0))
        if hit is None:
            hit = _match_auto(base)
        if hit is None:
            continue
        groups.setdefault(hit[0], []).append((hit[1], os.path.join(folder, name)))
    ordered = {}
    for num in sorted(groups.keys(), key=_gkey):
        ordered[num] = [p for _, p in sorted(groups[num], key=lambda x: x[0])]
    return ordered

def _folder_label(folder):
    """给文件夹起个人看得懂的短名。

    长文案流水线的目录长这样 …\Tik012\片段 —— 直接拿 basename 全是"片段",
    区分不了, 所以这种通用名往上取一层。
    """
    folder = os.path.normpath(folder or "")
    base = os.path.basename(folder) or folder
    if base in ("片段", "clips", "Clips", "素材", "video", "videos"):
        up = os.path.basename(os.path.dirname(folder))
        if up:
            return up
    return base

# 拼接 / 散落自己写出来的目录。往下找素材时跳过, 否则成品和切片会被当成新素材。
# find_material_dirs 和「以文件夹为一组」共用这一份, 不要在旁边再抄一套。
MATERIAL_DIR_SKIP = frozenset({
    "_stitched", "_invalid", "_scatter", "_collect", "_to_delete",
    "__pycache__", "字幕成品", "交接",
})

def group_by_folder_scan(folder):
    """一个文件夹收成一组: 只看下一层的视频, 按文件名自然序。不看命名规则。

    不进入子目录。MATERIAL_DIR_SKIP 里的自产目录即使里面有视频也不算。
    没有视频时返回空列表; 要不要记一笔由调用方决定。
    """
    folder = os.path.normpath(folder or "")
    if not folder or not os.path.isdir(folder):
        return []
    names = []
    try:
        entries = os.listdir(folder)
    except OSError:
        return []
    for name in entries:
        if name in MATERIAL_DIR_SKIP:
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
            continue
        names.append(name)
    names.sort(key=_natkey)
    return [os.path.join(folder, name) for name in names]

def scan_groups_multi(folders, group_mark=None, order_mark=None, group_by_folder=False):
    """扫多个素材文件夹 → (groups, folder_of, base_of, notes)。

    folders     [路径, ...] (只有一个时行为与 scan_groups 完全一致)
    groups      {组号: [片段路径, ...]}
    folder_of   {组号: 该组素材所在的文件夹}   —— 成品写回它自己的 _stitched
    base_of     {组号: 成品文件名用的原始组号} —— 重名改名后仍用原名出片
    notes       给日志的提示行

    group_by_folder 为 False（默认）时按文件名分组, 与改这个参数之前一个字节一样。
    为 True 时每个文件夹是一组: 组号是文件夹名, 组内是下一层全部视频的自然序,
    group_mark / order_mark 不看。没有视频的文件夹不进表, notes 里写一行原因。

    重名处理: 两个文件夹里都有 "01" 组时, 后来的那个在表里改叫 "01(Tik013)",
    只是为了字典键唯一; 出片文件名还是 01.mp4, 而且落在它自己的文件夹里,
    所以谁也不会覆盖谁。以文件夹为一组时, 重名的是文件夹名, 表里改叫
    「名字(上级目录名)」, base_of 仍是原来的文件夹名。
    """
    groups, folder_of, base_of, notes = {}, {}, {}, []
    seen = []
    for folder in folders or []:
        folder = os.path.normpath(folder)
        if not os.path.isdir(folder) or folder in seen:
            continue
        seen.append(folder)
        if group_by_folder:
            paths = group_by_folder_scan(folder)
            if not paths:
                notes.append("%s 里没有视频，跳过" % folder)
                continue
            one = {os.path.basename(folder): paths}
            label = os.path.basename(os.path.dirname(folder)) or folder
        else:
            one = scan_groups(folder, group_mark, order_mark)
            label = _folder_label(folder)
            if not one and not (group_mark or "").strip() and not (order_mark or "").strip():
                try:
                    from cutora.batch.stitch_grouping import GROUP_AUTO, scan_folders
                    sub_res = scan_folders([folder], mode=GROUP_AUTO)
                    if sub_res.groups and not sub_res.low_items:
                        one = {g.key: g.paths for g in sub_res.groups}
                except Exception:
                    pass
        for k, paths in one.items():
            key = k
            if key in groups:
                key = "%s(%s)" % (k, label)
                i = 2
                while key in groups:
                    key = "%s(%s-%d)" % (k, label, i)
                    i += 1
                notes.append("组名 %s 在多个文件夹里都有, 表里改叫 %s "
                             "(成品仍叫 %s.mp4, 写回它自己的文件夹)" % (k, key, k))
            groups[key] = paths
            folder_of[key] = folder
            base_of[key] = k
    ordered = {}
    for k in sorted(groups.keys(), key=_gkey):
        ordered[k] = groups[k]
    return ordered, folder_of, base_of, notes

# Q131：往下找素材时也不进这些目录 —— 空镜头 / B-roll 是散落用的素材，
# 成品是导出结果，下划线开头的是程序或用户自己挪开的（_弃用 / _候选 / _scatter…）。
MATERIAL_DIR_SKIP_CI = frozenset({
    "空镜头", "空镜", "broll", "b-roll", "b_roll", "成品",
})


def _skip_material_dir(name):
    if name in MATERIAL_DIR_SKIP or name.startswith(".") or name.startswith("_"):
        return True
    return name.strip().lower() in MATERIAL_DIR_SKIP_CI


def find_material_dirs(root, max_depth=3):
    """从一个总目录里挑出所有"装着视频的文件夹"。

    用途: 他的目录是 …\0901\Tik012\片段 这种一个视频一个文件夹, 选总目录
    比一个个添加省事得多。跳过 MATERIAL_DIR_SKIP 里自己产出的目录, 以及
    _skip_material_dir 认出来的空镜头 / 成品 / 下划线目录。
    """
    out = []
    root = os.path.normpath(root or "")
    if not os.path.isdir(root):
        return out
    for cur, dirs, files in os.walk(root):
        rel = os.path.relpath(cur, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = [d for d in dirs if not _skip_material_dir(d)]
        if depth >= max_depth:
            dirs[:] = []
        if any(os.path.splitext(f)[1].lower() in VIDEO_EXTS for f in files):
            out.append(cur)
    return sorted(out, key=_natkey)

# 自定义命名规则记在这儿, 免得每次开对话框都要重敲一遍
def _naming_cfg():
    from cutora.core.paths import settings_file
    return settings_file("stitch_naming.json")

NAMING_CFG = None  # resolved at use; see _naming_cfg()

def load_naming():
    try:
        import json
        with open(_naming_cfg(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return str(d.get("group_mark") or ""), str(d.get("order_mark") or "")
    except Exception:
        return "", ""

def _load_naming_cfg():
    try:
        with open(_naming_cfg(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _save_naming_cfg(d):
    try:
        path = _naming_cfg()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def save_naming(group_mark, order_mark):
    # 只改这两个键, 别把同一个文件里记着的文件夹列表冲掉
    d = _load_naming_cfg()
    d["group_mark"] = group_mark or ""
    d["order_mark"] = order_mark or ""
    _save_naming_cfg(d)

def load_group_by_folder():
    """上次是否勾了「以文件夹为一组」。老配置没有这个键, 算关。"""
    return _load_naming_cfg().get("group_by_folder") is True

def save_group_by_folder(enabled):
    """只写这一个键。分组前缀、序号前缀、文件夹列表保持原样。"""
    d = _load_naming_cfg()
    d["group_by_folder"] = bool(enabled)
    _save_naming_cfg(d)

def load_last_folders():
    """上次用过的素材文件夹 —— 开对话框时直接摆好, 不用每次重新添加。"""
    d = _load_naming_cfg()
    out = []
    for p in (d.get("folders") or []):
        if isinstance(p, str) and os.path.isdir(p) and p not in out:
            out.append(p)
    return out

def save_last_folders(folders):
    d = _load_naming_cfg()
    d["folders"] = [str(p) for p in (folders or [])]
    _save_naming_cfg(d)

def _norm_key(k):
    """显式文案编号归一: 纯数字才 lstrip('0'); Tik008 → Tik008。

    没有数字的名字（文件夹名「观众席素材」）原样返回。不要在这里剥汉字,
    否则组号和文案对不上。
    """
    s = str(k or "").strip()
    if s.isdigit():
        return s.lstrip("0") or "0"
    m = re.match(r"^([^\W\d_]+)\s*(\d+)$", s, re.UNICODE)
    if m:
        return m.group(1) + f"{int(m.group(2)):03d}"
    return s

def _split_explicit_line(paragraph, known_keys):
    """把「组号|文案」拆开。认不出返回 None。

    数字组号、Tik001 这种前缀加数字, 沿用原来的正则。
    没有数字的组号（以文件夹为一组时就是文件夹名）只有在归一化之后
    正好是本次某个组号时才认, 免得普通句子里的冒号被当成编号。
    """
    m = re.match(r"^(\d+|[^\W\d_]+\s*\d+)\s*[|:：\t]\s*(.+)$",
                 paragraph, flags=re.S | re.I | re.UNICODE)
    if m:
        return _norm_key(m.group(1)), m.group(2).strip()
    m = re.match(r"^(.+?)\s*[|:：\t]\s*(.+)$",
                 paragraph, flags=re.S | re.UNICODE)
    if not m:
        return None
    key = _norm_key(m.group(1).strip())
    if key and key in known_keys:
        return key, m.group(2).strip()
    return None

# 文案体检的报告行, 供界面/CLI 打日志用 (parse_scripts 每次会重写)
_WENAN_LAST = []

def _wenan_ai():
    """AI 兜底的回调。key 复用音效面板里那把 Gemini key; 没配就返回 None,
    只跑词表那一路, 不联网也不报错。"""
    try:
        from cutora.batch import wenan_norm as _wn
    except Exception:
        return None
    model = "gemini-2.0-flash"
    key = ""
    try:
        from cutora.core.secrets import get as _secret_get
        key = (_secret_get("gemini_api_key") or "").strip()
    except Exception:
        key = ""
    try:
        from cutora.audio import sfx_library as sfx_library
        fx = sfx_library.load_fx_settings() or {}
        model = (fx.get("gemini_model") or model).strip() or model
    except Exception:
        pass
    return _wn.gemini_ai(model=model) if key else None

def parse_scripts(text, group_keys):
    """
    解析用户粘贴的文案, 返回 ({组号: 文案}, 错误信息)。
    支持两种格式:
      A. 显式编号:  每段以 "01|文案" / "01:文案" / "01<TAB>文案" 开头
                    也认 "Tik001|文案", 以及和组号一模一样的名字
                    (以文件夹为一组时组号就是文件夹名, 例如 "观众席素材|文案")
      B. 顺序段落:  按空行分段, 依组号升序一一对应
    """
    text = (text or "").strip()
    if not text:
        return None, "文案为空"
    # 0904 文案体检: 只补空格, 绝不改字 (core/wenan_norm.py)。
    # 这是安全网 —— 正常情况下上游 (长文案 开新批) 已经规范过了, 但手工粘进来
    # 的、以前存下来的旧文案还是会带着 "you,two" / "preparingto" 这种粘连,
    # 那些会在 tokenize 之后变成 "youtwo"/"preparingto", 跟口播永远对不上,
    # 于是每个句子交界处开一个洞, 被 find_missing 报成"缺失, 请重新生成"。
    # 骨架校验没过就整篇原样退回, 一个字都不动。
    try:
        from cutora.batch import wenan_norm as _wn
        text, _wrep = _wn.normalize(text, ai=_wenan_ai())
        _WENAN_LAST.clear()
        _WENAN_LAST.extend(_wn.report_lines(_wrep))
    except Exception:
        pass
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    known = {_norm_key(k) for k in (group_keys or [])}
    explicit = {}
    all_explicit = True
    for p in paras:
        hit = _split_explicit_line(p, known)
        if hit:
            explicit[hit[0]] = hit[1]
        else:
            all_explicit = False
            break

    result = {}
    if all_explicit and explicit:
        for k in group_keys:
            kk = _norm_key(k)
            if kk in explicit:
                result[k] = explicit[kk]
        missing = [k for k in group_keys if k not in result]
        if missing:
            return None, f"以下组缺少对应文案: {', '.join(missing)}"
        return result, None

    if len(paras) != len(group_keys):
        return None, (f"文案段数({len(paras)})与素材组数({len(group_keys)})不一致。\n"
                      f"请按空行分段并保证一一对应, 或使用 '编号|文案' 显式格式。")
    for k, p in zip(group_keys, paras):
        result[k] = p
    return result, None

# ----------------------------------------------------------------------
# 词级对齐 → 切点
# ----------------------------------------------------------------------

def _seg_words_or_text(seg):
    """取一个 segment 的词列表; align 把词吞了就用原文补回来。

    ★ 这是"转录明明成功、匹配却看不到内容"的根因所在 ★
    whisperx.align() 有三条分支会把整段的 words 清成空列表, 但 text 原封不动:
        · 该段没有一个字符在 wav2vec2 字典里 (数字、纯符号)
        · 段起始时间超过音频长度
        · backtrack 对齐回溯失败
    三种情况它只打一行 warning, 然后 append 一个 words=[] 的段。
    原来的 flatten_words 只遍历 seg["words"], 于是这段话【凭空消失】——
    转录器其实转出来了, 匹配阶段却当它没说过, 那部分文案随后被判成"缺失"。
    实测 360 (1).mp4: 8 秒只剩 3 个词, 命中率还显示 100% (3/3)。

    这里改成: words 为空就拿 text 拆词, 按 segment 的起止时间均分。
    时间戳精度会差一点, 但内容一个字都不会丢 —— 对"这句话讲没讲过"
    这个判断来说, 内容完整远比时间戳精确重要。
    """
    ws = seg.get("words") or []
    if ws:
        return ws, False
    text = (seg.get("text") or "").strip()
    if not text:
        return [], False
    parts = [p for p in re.split(r"\s+", text) if p]
    if not parts:
        return [], False
    t0, t1 = seg.get("start"), seg.get("end")
    try:
        t0 = float(t0)
        t1 = float(t1)
        if not (np.isfinite(t0) and np.isfinite(t1) and t1 > t0):
            raise ValueError
    except Exception:
        t0, t1 = 0.0, max(0.5, 0.35 * len(parts))
    step = (t1 - t0) / len(parts)
    return ([{"word": p, "start": t0 + i * step, "end": t0 + (i + 1) * step}
             for i, p in enumerate(parts)], True)

def flatten_words(segments, log=None):
    """whisperx.align 结果 → [(token, start, end)], 缺失时间戳就近插值。"""
    words = []
    n_rescued = 0
    for seg in segments or []:
        seg_words, rescued = _seg_words_or_text(seg)
        if rescued:
            n_rescued += 1
        merged_seg_words = []
        for w in seg_words:
            raw_w = str(w.get("word", "") or "").strip()
            if (
                merged_seg_words
                and re.fullmatch(r",?\d{3}(?:,\d{3})*", raw_w)
                and re.fullmatch(r"\$?\d{1,3},?", str(merged_seg_words[-1].get("word", "") or "").strip())
            ):
                prev = dict(merged_seg_words[-1])
                prev["word"] = str(prev.get("word", "")).rstrip(",") + raw_w.replace(",", "")
                if w.get("end") is not None:
                    prev["end"] = w.get("end")
                merged_seg_words[-1] = prev
                continue
            merged_seg_words.append(w)
        for w in merged_seg_words:
            toks = norm_tokens(w.get("word", ""))
            if not toks:
                continue
            ws, we = w.get("start"), w.get("end")
            if len(toks) == 1:
                words.append([toks[0], ws, we])
                continue
            # 一个 ASR 词展开成多个 token (如 "25"→twenty five):
            # 按 token 数等分该词的时长, 保证词级时间戳不错位。
            ok = (isinstance(ws, (int, float)) and isinstance(we, (int, float))
                  and np.isfinite(float(ws)) and np.isfinite(float(we))
                  and float(we) > float(ws))
            if not ok:
                for t in toks:
                    words.append([t, ws, we])
                continue
            ws, we = float(ws), float(we)
            step = (we - ws) / len(toks)
            for i, t in enumerate(toks):
                words.append([t, ws + i * step,
                              we if i == len(toks) - 1 else ws + (i + 1) * step])

    def _ok(v):
        return isinstance(v, (int, float)) and np.isfinite(float(v))

    last_end = 0.0
    for i, w in enumerate(words):
        if not _ok(w[1]):
            w[1] = last_end
        if not _ok(w[2]):
            nxt = None
            for j in range(i + 1, len(words)):
                if _ok(words[j][1]):
                    nxt = words[j][1]
                    break
            w[2] = nxt if nxt is not None else w[1] + 0.30
        last_end = w[2]
    if n_rescued and callable(log):
        log(f"    ↺ 对齐丢了 {n_rescued} 段的词级时间戳, 已用转录原文补回"
            f"(内容不丢, 时间戳按段均分)")
    # 数字折叠 —— 必须和文案侧跑同一个函数, 对称才安全。
    # 折出来的数字取"第一个词的开始 ~ 最后一个词的结束"作为时间戳。
    toks = [w[0] for w in words]
    spans = [(float(w[1]), float(w[2])) for w in words]
    toks, spans = fold_numbers(toks, spans)
    return [(t, float(s), float(e)) for t, (s, e) in zip(toks, spans)]

# ---- 转录兜底: 保证"说了的话"一定进得了匹配 ----
# 期望词速 (词/秒)。正常英文朗读 2~3 词/秒; 低于这个值基本可以断定
# 转录/对齐没把内容全拿到 —— 实测正常批次中位 2.6~2.8, 最低 1.3。
#
# 0829 上调 1.20 → 2.00。1.20 这个值形同虚设:
# 实测 0829 那批 43 个片段的词速分布是 中位 2.45 / 最低 1.54 / 最高 3.19,
# 整个分布都在 1.20 以上, 这道保险从来没触发过。出事的 03b 是
# 22 词 / 14.3 秒 = 1.54 词/秒, 整整一句 "Unless you repent, you too will
# all perish." 被 whisperX 的 VAD+对齐链路吞掉, 却因为 1.54 > 1.20 判成"正常"。
# 用同一个 small 模型走绕过 VAD 的兜底路径重转, 那句话一字不差地回来了 ——
# 说明不是模型不行, 是这道闸门开得太松。
MIN_WORDS_PER_SEC = 2.00
# 片段太短时词速统计不稳, 低于这个秒数不做兜底判断
MIN_DUR_FOR_CHECK = 2.5
# 单个词允许占的最长时间 (秒)。这是比词速更硬的信号:
# 英语里没有哪个词要念 4 秒。03b 对齐之后 "what" 从 4.12 一路占到 8.25 ——
# 那 4 秒里其实有一整句话, 只是没被转出来, 对齐只好把这段无主的时间
# 摊到旁边的词上。词速会被"整体略慢"稀释掉, 这个信号不会。
MAX_WORD_SPAN_SEC = 1.5

# ---- 转录缓存 + 可复现 ----
# 为什么必须有这两样:
#   1) 每跑一次就把所有片段重转一遍。两百多个片段, 绝大多数一个字都没改,
#      纯属白烧显卡时间。
#   2) 更要命的是结果不稳定。faster-whisper 的 temperature 是一条【回退梯子】
#      [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]: 第一档贪心解码是确定的, 但音频一难
#      (背景音乐、气声、口音重) 就会往上爬, 而上面几档是【随机采样】。
#      于是同一个片段这次转出 12 个词、下次转出 9 个词 —— 认领区间跟着变,
#      覆盖表跟着变, "缺失清单"也跟着变。这就是
#      "补了 14 段, 又说缺 6 段, 再补又说缺 1 段" 的根本原因:
#      不是它算错了, 是每次拿到的转录都不一样, 相当于每次在算一批新素材。
# 对策:
#   · 转过的片段按 (文件名+大小+修改时间+语言+参数版本) 存下词表。文件没动
#     就直接复用 —— 既不重算, 结果也就永远不会变。
#   · 真需要重转的 (新片段/改过的片段), 转之前把随机源钉死, 至少保证
#     同一个文件重转两次结果一致。
ASR_CACHE_NAME = "_asr_cache.json"
# 转录参数一变旧缓存就该失效。改了 transcribe_clip / _fw_transcribe_words 里
# 任何影响结果的参数, 把这个版本号 +1, 全部缓存自动作废重转。
ASR_PARAM_VER = 4      # BUG-06: 无 VAD 补转后对齐拿真词级；均摊标 tier 3
ASR_SEED = 1234

TIMING_TIER_ALIGN = 1
TIMING_TIER_WORD_TS = 2
TIMING_TIER_EVEN = 3
EVEN_SPLIT_STD = 0.08
TIMING_ESTIMATED_MARK = "⚠ 词级时间戳为估算"


def _seed_all(seed=ASR_SEED):
    """钉死所有会影响解码的随机源, 让必须重转的片段也能复现。"""
    try:
        import random
        random.seed(seed)
    except Exception:
        pass
    try:
        np.random.seed(seed)
    except Exception:
        pass
    try:
        import ctranslate2          # faster-whisper 的解码后端
        ctranslate2.set_random_seed(seed)
    except Exception:
        pass
    try:
        import torch                # wav2vec2 对齐
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass

def clip_fingerprint(path, language):
    """片段指纹: 文件没被改动, 指纹就完全一致。

    用 大小+修改时间 而不是内容哈希 —— 几百个几十兆的 mp4 逐个哈希太慢,
    而"文件被换掉了"这件事在 Windows 上必然带动这两个值。
    """
    try:
        st = os.stat(path)
        sig = f"{st.st_size}:{int(st.st_mtime)}"
    except Exception:
        sig = "nostat"
    return f"{os.path.basename(path)}|{sig}|{language}|p{ASR_PARAM_VER}"

def load_asr_cache(out_dir):
    if not out_dir:
        return {}
    try:
        with open(os.path.join(out_dir, ASR_CACHE_NAME), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def save_asr_cache(out_dir, cache):
    """先写 .tmp 再 replace: 中途断电也不会留下半个损坏的缓存。"""
    if not out_dir:
        return
    try:
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, ASR_CACHE_NAME)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass

def load_json_safe(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, type(default)) else default
    except Exception:
        return default

def save_json_safe(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass

def _clip_name_index(clips):
    """clips[i]['name'] → i。同时登记去扩展名, 避免 _order.json 带不带 .mp4 对不上。"""
    idx = {}
    for i, c in enumerate(clips):
        n = c.get("name") or ""
        idx[n] = i
        stem = os.path.splitext(n)[0]
        if stem and stem not in idx:
            idx[stem] = i
    return idx

def pin_order_from_json(clips, folder, gnum):
    """读片段目录下的 _order.json 钉死组内顺序。

    返回 (order, clips_out, log_msgs, pinned).
    没有文件 / 名单对不上 → pinned=False, order=None, clips 原样。
    名单是 clips 的真子集 → 截断为名单内片段 (备份 take 不进成品)。
    """
    logs = []
    op = os.path.join(folder or "", "_order.json")
    if not folder or not os.path.isfile(op):
        return None, clips, logs, False
    data = load_json_safe(op, {}) or {}
    names = data.get(str(gnum))
    if names is None:
        want = str(gnum).lower()
        for k, v in data.items():
            if str(k).lower() == want:
                names = v
                break
    if not names:
        logs.append(f"  ⚠ 组{gnum}: _order.json 没有键 {gnum}，退回自动纠正")
        return None, clips, logs, False
    idx = _clip_name_index(clips)
    missing = [n for n in names
               if n not in idx and os.path.splitext(n)[0] not in idx]
    if missing:
        logs.append(
            f"  ⚠ 组{gnum}: _order.json 有 {len(missing)} 个名字对不上 clips"
            f"（例 {missing[0]}），退回自动纠正")
        return None, clips, logs, False
    order_idx = []
    for n in names:
        order_idx.append(idx[n] if n in idx else idx[os.path.splitext(n)[0]])
    if len(order_idx) != len(set(order_idx)):
        logs.append(f"  ⚠ 组{gnum}: _order.json 有重复名，退回自动纠正")
        return None, clips, logs, False
    if len(order_idx) == len(clips):
        logs.append(f"  ⛓ 组{gnum}: 已按 _order.json 钉死顺序，跳过自动纠正")
        return order_idx, clips, logs, True
    new_clips = [clips[i] for i in order_idx]
    logs.append(
        f"  ⛓ 组{gnum}: 已按 _order.json 钉死顺序，跳过自动纠正"
        f"（截断 {len(clips)}→{len(new_clips)}）")
    return list(range(len(new_clips))), new_clips, logs, True

def _fw_transcribe_words(model, audio, language, dur):
    """绕开 VAD 和 wav2vec2 对齐, 用 faster-whisper 自带的词级时间戳直接转。

    为什么需要这条后路: whisperX 的正常链路是 VAD 切段 → whisper 转录 →
    wav2vec2 强制对齐, 三个环节都可能"悄悄吃掉"内容:
      · VAD 把带背景音乐/气声的语音判成非语音, 整段不进转录
      · 对齐失败 → 词列表被清空 (已由 _seg_words_or_text 兜住)
    faster-whisper 的 word_timestamps=True 直接给词级时间戳, 且
    vad_filter=False 时一秒音频都不会被丢。精度略逊, 但绝不漏内容。
    """
    fw = getattr(model, "model", None)          # FasterWhisperPipeline.model
    if fw is None or not hasattr(fw, "transcribe"):
        return []
    kwargs = dict(
        audio=audio, language=language, beam_size=5, best_of=5,
        word_timestamps=True, vad_filter=False,
        condition_on_previous_text=False,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        no_speech_threshold=0.9,        # 放宽: 宁可多转, 不可漏转
        compression_ratio_threshold=2.8,
    )
    try:
        segs, _info = fw.transcribe(verbose=False, **kwargs)
    except TypeError:
        segs, _info = fw.transcribe(**kwargs)
    out = []
    for s in segs:
        ws = []
        for w in (getattr(s, "words", None) or []):
            ws.append({"word": w.word, "start": w.start, "end": w.end})
        out.append({"start": getattr(s, "start", 0.0),
                    "end": getattr(s, "end", dur),
                    "text": getattr(s, "text", "") or "",
                    "words": ws})
    return out

# ═══════════ 复核用模型 (0829) ═══════════
#
# 主力仍是 small: 43 个片段整体跑一遍最快, 绝大多数片段它转得完全够用。
# 但 small 有它转不动的东西 —— 0829 的 02c 把 "Hear, O Israel" 转成了
# "hero israel"(同音合并), 文案侧 hear/o 两个词没人认领, 凑够 3 个 token
# 就越过了"≥3 词算缺失"的报警线。实测同一个文件:
#     asr_small  → "... verse 4, hero Israel, the Lord our God ..."
#     asr_medium → "... verse 4. Hear, O Israel! The Lord our God ..."
# medium 对这类圣经熟语明显更准 (语言先验更强)。
#
# 但【不能因此把全量换成 medium】: 一来慢 (CPU 实测 5s → 15s), 二来 medium
# 自己也会漏词 —— 同一句它把 "chapter" 吞了。换大模型是换一种错法, 不是消灭错。
# 所以 medium 只在【复核那一趟】上场: 平时不加载, 只有某组真报出缺失时,
# 才拿它去重转那一两个可疑片段。代价接近零, 收益是把 small 的硬伤补上。
#
# 找模型的顺序: 环境变量 CUTORA_RECHECK_ASR → runtime/models 下的 asr_medium /
# asr_large*。一个都没有就退回"用主力模型走绕过 VAD 的路径"(仍能救组 03 那种)。
_RECHECK_MODEL = None          # 懒加载, 整轮拼接只加载一次
_RECHECK_TRIED = False

def _recheck_model_dir():
    from cutora.core.flags import recheck_asr_dir
    from cutora.core.paths import MODELS, get_model_paths
    env = recheck_asr_dir()
    if env and os.path.isdir(env):
        return env
    base = MODELS
    try:
        mp = get_model_paths() or {}
        ap = mp.get("asr_path")
        if ap and os.path.isdir(os.path.dirname(ap)):
            base = os.path.dirname(ap)
    except Exception:
        pass
    cur = ""
    try:
        cur = os.path.basename(str((load_json_safe(
            os.path.join(base, "model_paths.json"), {}) or {}).get("asr_path") or ""))
    except Exception:
        pass
    for name in ("asr_large-v3", "asr_large", "asr_medium"):
        if name == cur:
            continue                       # 主力已经是它了, 复核没有升级空间
        d = os.path.join(base, name)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "model.bin")):
            return d
    return None

def get_recheck_model(log=None):
    """懒加载复核模型。拿不到就返回 None —— 调用方退回主力模型的兜底路径。"""
    global _RECHECK_MODEL, _RECHECK_TRIED
    if _RECHECK_TRIED:
        return _RECHECK_MODEL
    _RECHECK_TRIED = True
    d = _recheck_model_dir()
    if not d:
        return None
    try:
        from faster_whisper import WhisperModel
        try:
            _RECHECK_MODEL = WhisperModel(
                d, device=DEVICE,
                compute_type="float16" if DEVICE == "cuda" else "int8")
        except Exception:
            _RECHECK_MODEL = WhisperModel(d, device="cpu", compute_type="int8")
        if callable(log):
            log(f"    ⤴ 复核模型已加载: {os.path.basename(d)}")
    except Exception as e:
        _RECHECK_MODEL = None
        if callable(log):
            log(f"    · 复核模型加载失败({str(e)[:50]}), 退回主力模型兜底路径")
    return _RECHECK_MODEL

def release_recheck_model():
    """一轮拼接跑完就把复核模型放掉, 不长期占显存。"""
    global _RECHECK_MODEL, _RECHECK_TRIED
    _RECHECK_MODEL = None
    _RECHECK_TRIED = False
    try:
        import gc
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

def _fw_words_raw(fw, audio, language, dur):
    """裸 faster-whisper 模型 → 段字典列表 (给 flatten_words 用)。

    参数和 _fw_transcribe_words 逐字一致: 绕开 VAD, 宁可多转不可漏转。
    """
    if fw is None or not hasattr(fw, "transcribe"):
        return []
    segs, _info = fw.transcribe(
        audio, language=language, beam_size=5, best_of=5,
        word_timestamps=True, vad_filter=False,
        condition_on_previous_text=False,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        no_speech_threshold=0.9,
        compression_ratio_threshold=2.8,
    )
    out = []
    for sg in segs:
        ws = [{"word": w.word, "start": w.start, "end": w.end}
              for w in (getattr(sg, "words", None) or [])]
        out.append({"start": getattr(sg, "start", 0.0),
                    "end": getattr(sg, "end", dur),
                    "text": getattr(sg, "text", "") or "", "words": ws})
    return out

def deep_transcribe(path, language, dur, model=None, log=None):
    """复核用的深度重转: 优先复核模型, 没有就用主力模型绕过 VAD 再来一遍。

    返回 [(token, start, end)]; 失败返回 []。
    """
    fw = get_recheck_model(log=log)
    used = "复核模型"
    if fw is None:
        fw = getattr(model, "model", None)      # 主力 (FasterWhisperPipeline)
        used = "主力模型·绕过VAD"
    if fw is None:
        return [], ""
    try:
        _seed_all()
        audio = load_audio_16k(path)
        return flatten_words(_fw_words_raw(fw, audio, language, dur),
                             log=None), used
    except Exception as e:
        if callable(log):
            log(f"    ⚠ 深度重转失败: {str(e)[:60]}")
        return [], used

def word_dur_std(words):
    durs = []
    for w in words or []:
        try:
            d = float(w[2]) - float(w[1])
        except (TypeError, ValueError, IndexError):
            continue
        if d > 0:
            durs.append(d)
    if len(durs) < 3:
        return 0.0
    return float(np.std(durs))


def is_even_split(words):
    """True when durations are ~equal — sentence-level even fill, not speech."""
    return word_dur_std(words) <= EVEN_SPLIT_STD and len(words or []) >= 3


def timing_tier_of(words):
    if is_even_split(words):
        return TIMING_TIER_EVEN
    return TIMING_TIER_WORD_TS


def _align_word_list(align_model, metadata, audio, words, dur, device, log=None):
    """Force-align the transcript text. Returns word list or None."""
    if align_model is None or not words or audio is None:
        return None
    text = " ".join(str(w[0]) for w in words)
    if not text.strip():
        return None
    segs = [{"start": 0.0, "end": float(dur or 0.0) or 1.0, "text": text}]
    aligned = align_transcript(
        segs, align_model, metadata, audio, device,
        return_char_alignments=False)
    out = flatten_words(aligned.get("segments", []), log=log)
    return out or None


def stamp_word_timings(model, align_model, metadata, audio, words, dur,
                       language, device, log=None):
    """Real word times: wav2vec2 (1) → word_timestamps (2) → even split (3).

    Even split is only the last resort and must be tagged. Never silent.
    """
    def _say(s):
        if callable(log):
            log(s)

    words = list(words or [])
    try:
        aligned = _align_word_list(
            align_model, metadata, audio, words, dur, device, log=None)
        if aligned and not is_even_split(aligned):
            return aligned, TIMING_TIER_ALIGN
    except Exception as e:
        from cutora.asr.stitch_language import StitchLanguageError
        if isinstance(e, StitchLanguageError):
            raise
        _say("    ↺ 词级对齐失败: %s" % str(e)[:50])
    if words and not is_even_split(words):
        return words, TIMING_TIER_WORD_TS
    try:
        if model is not None and audio is not None:
            fw = flatten_words(
                _fw_transcribe_words(model, audio, language, dur), log=None)
            if fw and not is_even_split(fw):
                return fw, TIMING_TIER_WORD_TS
    except Exception as e:
        _say("    ↺ word_timestamps 失败: %s" % str(e)[:50])
    if words:
        _say("    %s" % TIMING_ESTIMATED_MARK)
    return words, TIMING_TIER_EVEN


def transcribe_clip(model, align_model, metadata, audio, dur, language,
                    device, log=None, script_tokens=None, status=None):
    """转一个片段 → 词列表。内容完整性优先, 全自动, 不需要人工复核。

    链路: whisperX 正常路径 → 词速体检 → 不达标就绕过 VAD 重转一次。
    有文案时按命中词数/命中率取胜者 (废话多的那次会输);
    没有文案时仍取词多者 (旧行为, 只为把被 VAD 吞掉的内容捞回来)。
    """

    def _say(s):
        if callable(log):
            log(s)

    words = []
    try:
        from cutora.asr.long_audio import transcribe_long_audio
        result = transcribe_long_audio(model, audio, language, batch_size=4)
        raw_segs = result.get("segments", []) or []
        if status is not None:
            # Q190：带标点、带大小写的原句（批量加文本的「视频原话」要用；词列表是归一化过的）
            status["raw_segments"] = [
                {"start": float(sg.get("start") or 0.0), "end": float(sg.get("end") or 0.0),
                 "text": str(sg.get("text") or "").strip()}
                for sg in raw_segs if isinstance(sg, dict) and str(sg.get("text") or "").strip()]
        try:
            aligned = align_transcript(raw_segs, align_model, metadata, audio,
                                       device, return_char_alignments=False)
            words = flatten_words(aligned.get("segments", []), log=log)
        except Exception as e:
            from cutora.asr.stitch_language import StitchLanguageError
            if isinstance(e, StitchLanguageError):
                raise
            # 对齐整体炸了: 退回用转录原文 + 段级时间戳, 内容照样不丢
            _say(f"    ↺ 对齐失败({str(e)[:50]}), 改用转录原文的段级时间戳")
            words = flatten_words(raw_segs, log=None)
    except Exception as e:
        _say(f"    ⚠ 常规转录失败({str(e)[:60]}), 尝试兜底转录")

    # ---- 词速体检 + 超长词体检: 只要像"没转全", 就绕过 VAD 再来一遍 ----
    if dur >= MIN_DUR_FOR_CHECK:
        wps = len(words) / max(0.01, dur)
        span = max((float(w[2]) - float(w[1]) for w in words), default=0.0)
        why = ""
        if wps < MIN_WORDS_PER_SEC:
            why = (f"词速仅 {wps:.2f} 词/秒 ({len(words)}词/{dur:.1f}s)")
        elif span > MAX_WORD_SPAN_SEC:
            why = (f"有个词占了 {span:.2f} 秒 (上限 {MAX_WORD_SPAN_SEC}s), "
                   f"这段时间里多半还有没转出来的话")
        if why:
            _say(f"    ↻ {why}, 疑似没转全 → 绕过 VAD 重转")
            try:
                segs2 = _fw_transcribe_words(model, audio, language, dur)
                w2 = flatten_words(segs2, log=None)
                if script_tokens is not None:
                    m2 = match_clip_global(w2, script_tokens)
                    m1 = match_clip_global(words, script_tokens)
                    take_stamped = (
                        is_even_split(words) and not is_even_split(w2)
                        and int(m2.get("hit") or 0) >= int(m1.get("hit") or 0))
                    if _transcript_better(m2, m1) or take_stamped:
                        _say(f"    ✓ 兜底转录命中{m2['hit']}词/"
                             f"{m2['ratio'] * 100:.0f}% "
                             f"(原 {m1['hit']}词/{m1['ratio'] * 100:.0f}%), "
                             f"采用兜底结果")
                        words = w2
                        if status is not None and segs2:
                            status["raw_segments"] = [
                                {"start": float(sg.get("start") or 0.0), "end": float(sg.get("end") or 0.0),
                                 "text": str(sg.get("text") or "").strip()}
                                for sg in segs2 if isinstance(sg, dict) and str(sg.get("text") or "").strip()]
                    else:
                        _say(f"    · 兜底转录命中{m2['hit']}词/"
                             f"{m2['ratio'] * 100:.0f}%, 未超过原结果, 保持原样")
                elif len(w2) > len(words) or (
                        is_even_split(words) and not is_even_split(w2)):
                    _say(f"    ✓ 兜底转录拿到 {len(w2)} 个词 "
                         f"(原 {len(words)} 个), 采用兜底结果")
                    words = w2
                    if status is not None and segs2:
                        status["raw_segments"] = [
                            {"start": float(sg.get("start") or 0.0), "end": float(sg.get("end") or 0.0),
                             "text": str(sg.get("text") or "").strip()}
                            for sg in segs2 if isinstance(sg, dict) and str(sg.get("text") or "").strip()]
                else:
                    _say(f"    · 兜底转录 {len(w2)} 个词, 未超过原结果, 保持原样")
            except Exception as e:
                _say(f"    ⚠ 兜底转录也失败: {str(e)[:60]}")
    words, still = fill_incomplete_transcript(
        model, audio, words, dur, language, log=log)
    words, timing_tier = stamp_word_timings(
        model, align_model, metadata, audio, words, dur, language, device,
        log=log)
    if still:
        _say("    %s" % INCOMPLETE_MARK)
    if timing_tier >= TIMING_TIER_EVEN:
        _say("    %s" % TIMING_ESTIMATED_MARK)
    if isinstance(status, dict):
        status["incomplete"] = bool(still)
        status["timing_tier"] = int(timing_tier)
    return words


def fill_incomplete_transcript(model, audio, words, dur, language, log=None):
    """If ASR stopped early while the tail still has speech, transcribe the rest.

    Returns (words, still_incomplete).
    """
    def _say(s):
        if callable(log):
            log(s)

    env = compute_envelope(audio) if audio is not None else None
    if not transcript_incomplete(words, dur, env):
        return words, False
    # 整段、不带 VAD。从末词切开会把边界词切成半个；整段重转再取更长的那份。
    _say("    ↻ 转写结束点比音频早 ≥1s 且尾段有能量 → 整段不带 VAD 重转")
    w2 = []
    try:
        if audio is not None and getattr(audio, "size", 0) > 16000 * 0.4:
            segs = _fw_transcribe_words(model, audio, language, dur)
            w2 = flatten_words(segs, log=None)
    except Exception as e:
        _say("    ⚠ 整段补转失败: %s" % str(e)[:60])
        w2 = []
    if w2 and _incomplete_better(w2, words):
        _say("    ✓ 整段补转 %d 词 / 末词 %.2fs（原 %d 词 / %.2fs），采用" % (
            len(w2), last_word_end(w2), len(words or []), last_word_end(words)))
        words = w2
    else:
        _say("    · 整段补转未更长，保持原结果")
    still = transcript_incomplete(words, dur, env)
    return words, still


def _incomplete_better(new_words, old_words):
    """Prefer the transcript that covers more of the clip."""
    n_end = last_word_end(new_words)
    o_end = last_word_end(old_words)
    if n_end > o_end + 0.15:
        return True
    if abs(n_end - o_end) <= 0.15 and len(new_words or []) > len(old_words or []):
        return True
    return False

def _trim_spurious(blks):
    """
    对角线一致性过滤:
    片段里的零散废话词 (you/for/the...) 可能与文案偶然匹配, 造成切点误延。
    真实的连续朗读中, 相邻匹配块在"片段侧"与"文案侧"的间隔应大致相等 (ASR 错词只差 1~2);
    偶然匹配则两侧跳跃严重不一致 → 剔除首尾的小块 (size<=2 且间隔差>2)。
    """
    blks = list(blks)
    changed = True
    while changed and len(blks) > 1:
        changed = False
        b_prev, b_last = blks[-2], blks[-1]
        da = b_last.a - (b_prev.a + b_prev.size)
        db = b_last.b - (b_prev.b + b_prev.size)
        if b_last.size <= 2 and abs(da - db) > 2:
            blks.pop()
            changed = True
            continue
        b_first, b_next = blks[0], blks[1]
        da = b_next.a - (b_first.a + b_first.size)
        db = b_next.b - (b_first.b + b_first.size)
        if b_first.size <= 2 and abs(da - db) > 2:
            blks.pop(0)
            changed = True
    return blks

_APOS_FROM = "’‘`´ʼ"
_HYPHEN_FROM = "‐‑‒–—―"


def normalise_for_match(s):
    """NFKC + unify apostrophes/hyphens/nbsp. Matching only; never write back."""
    text = unicodedata.normalize("NFKC", s or "")
    for ch in _APOS_FROM:
        text = text.replace(ch, "'")
    for ch in _HYPHEN_FROM:
        text = text.replace(ch, "-")
    return text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2007", " ")


def match_clip_global(clip_words, script_tokens):
    """
    将片段词序列与【整段文案】做全局模糊对齐 (不依赖片段先后顺序,
    因此命名顺序写反也能定位到正确的文案位置)。
    返回 dict:
      matched   : 命中率 >= MATCH_RATIO_LOW
      partial   : 整体低置信, 但存在局部连续密集命中 (大量废话+少量正确内容)
      first_idx / last_idx : 命中区在片段词序列中的范围 (闭区间)
      s_start / s_end      : 命中区在文案 token 中的范围 [s_start, s_end)
      ratio     : 命中词数 / 片段词数
      density   : 命中词数 / 片段命中区跨度 (局部密度)
    """
    base = {"matched": False, "partial": False, "first_idx": None, "last_idx": None,
            "s_start": None, "s_end": None, "ratio": 0.0, "density": 0.0, "hit": 0,
            "blocks": []}
    n = len(clip_words)
    if n == 0 or not script_tokens:
        return base
    clip_toks = _fold_for_match(
        [normalise_for_match(w[0]).lower() for w in clip_words])[0]
    script_cmp = [normalise_for_match(t) for t in script_tokens]
    sm = difflib.SequenceMatcher(None, clip_toks, script_cmp, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
    blocks = _trim_spurious(blocks)
    if not blocks:
        return base
    hit = sum(b.size for b in blocks)
    ratio = hit / max(1, n)
    first_idx = blocks[0].a
    last_b = blocks[-1]
    last_idx = last_b.a + last_b.size - 1
    span = last_idx - first_idx + 1
    density = hit / max(1, span)
    matched = ratio >= MATCH_RATIO_LOW
    partial = (not matched) and hit >= PARTIAL_MIN_HIT and density >= PARTIAL_MIN_DENSITY
    return {"matched": matched, "partial": partial,
            "first_idx": first_idx, "last_idx": last_idx,
            "s_start": blocks[0].b, "s_end": last_b.b + last_b.size,
            "ratio": ratio, "density": density, "hit": hit,
            "blocks": [(b.a, b.b, b.size) for b in blocks]}

# 括号内经文出处。配音不念，默认不参与缺失判定。
SCRIPTURE_REF_RE = re.compile(
    r"\(\s*[1-3]?\s*[A-Za-z]+\.?\s+\d+:\d+(?:[-–]\d+)?(?:;\s*\d+:\d+)?\s*\)")


def _local_hit_ratio(query, window):
    if not query:
        return 0.0
    q = [normalise_for_match(t) for t in query]
    w = [normalise_for_match(t) for t in window]
    sm = difflib.SequenceMatcher(None, q, w, autojunk=False)
    hit = sum(b.size for b in sm.get_matching_blocks() if b.size > 0)
    return hit / float(len(query))


def _find_all_occurrences(script_tokens, clip_hit_tokens, min_ratio):
    """每个起点做局部比对，命中率 ≥ min_ratio 的都算候选。

    相邻错位窗口会得到一串重叠高分（同一处滑 1 个词），不是第二处出现。
    按命中率取非重叠的峰，只把真正分开的重复句留下。
    """
    n = len(clip_hit_tokens or [])
    if n == 0:
        return []
    raw = []
    mlen = len(script_tokens)
    for start in range(0, mlen - n + 1):
        window = script_tokens[start:start + n]
        ratio = _local_hit_ratio(clip_hit_tokens, window)
        if ratio >= min_ratio:
            raw.append((start, start + n, ratio))
    raw.sort(key=lambda t: (-t[2], t[0]))
    kept = []
    for cand in raw:
        if any(cand[0] < k[1] and k[0] < cand[1] for k in kept):
            continue
        kept.append(cand)
    kept.sort(key=lambda t: t[0])
    return kept


def _is_hit_clip(c):
    m = c.get("m") or {}
    return bool(m.get("matched") or m.get("partial")) and m.get("s_start") is not None


def _shift_match(m, new_start):
    old = m.get("s_start")
    if old is None or new_start == old:
        return
    delta = new_start - old
    span = (m.get("s_end") or old) - old
    m["s_start"] = new_start
    m["s_end"] = new_start + span
    m["blocks"] = [(a, b + delta, sz) for a, b, sz in (m.get("blocks") or [])]


def _neighbor_window(clips, idx, unambiguous):
    prev_end, next_start = None, None
    for j in range(idx - 1, -1, -1):
        if j in unambiguous:
            prev_end = clips[j]["m"]["s_end"]
            break
    for j in range(idx + 1, len(clips)):
        if j in unambiguous:
            next_start = clips[j]["m"]["s_start"]
            break
    lo = (prev_end - 3) if prev_end is not None else 0
    hi = (next_start + 3) if next_start is not None else 10 ** 9
    return lo, hi


def _corrected_index(clips, idx, cand_start):
    hit_slots = [i for i, c in enumerate(clips) if _is_hit_clip(c)]

    def _st(i):
        return cand_start if i == idx else clips[i]["m"]["s_start"]

    by_script = sorted(hit_slots, key=_st)
    order = list(range(len(clips)))
    for slot, src in zip(hit_slots, by_script):
        order[slot] = src
    try:
        return order.index(idx)
    except ValueError:
        return idx


def _who_covers(clips, idx, cs, ce):
    names = []
    for j, c in enumerate(clips):
        if j == idx or not _is_hit_clip(c):
            continue
        os, oe = c["m"]["s_start"], c["m"]["s_end"]
        if cs < oe and os < ce:
            names.append(c.get("name") or "")
    return names


def _short_clip_tag(name):
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    m = re.search(r"(s\d+)", stem, flags=re.I)
    return m.group(1) if m else (stem or name)


def _pick_candidate(clips, idx, cands, unambiguous):
    lo, hi = _neighbor_window(clips, idx, unambiguous)
    in_win = [c for c in cands if c[0] < hi and c[1] > lo]
    reason = "邻居窗口"
    pool = in_win if in_win else cands
    if not in_win:
        reason = "顺序位移最小"
    if len(pool) > 1:
        scored = [(_corrected_index(clips, idx, c[0]), c) for c in pool]
        best = min(abs(s - idx) for s, _c in scored)
        pool = [c for s, c in scored if abs(s - idx) == best]
        if not in_win:
            reason = "顺序位移最小"
    if len(pool) > 1:
        uncovered = [c for c in pool if not _who_covers(clips, idx, c[0], c[1])]
        if uncovered:
            pool = uncovered
            reason = "未被覆盖"
    return pool[0], reason


def _ambig_note(chosen, others, reason, clips, idx):
    ns, ne = chosen[0], chosen[1]
    bits = []
    for cs, ce, _r in others:
        who = _who_covers(clips, idx, cs, ce)
        loc = "%d–%d" % (cs, ce)
        if who:
            bits.append("%s 已由 %s 覆盖" % (loc, _short_clip_tag(who[0])))
        else:
            bits.append(loc)
    extra = (" (另一处 %s)" % "; ".join(bits)) if bits else ""
    return ("⇄ 文案重复句, 按%s定位到第 %d–%d 词%s"
            % (reason, ns + 1, ne, extra))


def resolve_ambiguous_positions(clips, script_tokens):
    """命中序列在文案里出现多次时，按邻居窗口 / 位移 / 未覆盖裁决。

    改 clip['m'] 的 s_start/s_end/blocks，并写 clip['ambig_note']。
    返回被搬走的片段数。
    """
    clips = clips or []
    info, unambiguous = [], set()
    for i, c in enumerate(clips):
        if not _is_hit_clip(c):
            continue
        m = c["m"]
        hit_toks = list(script_tokens[m["s_start"]:m["s_end"]])
        min_ratio = max(0.0, float(m.get("ratio") or 0.0) - 0.05)
        cands = _find_all_occurrences(script_tokens, hit_toks, min_ratio)
        if not cands:
            cands = [(m["s_start"], m["s_end"], 1.0)]
        info.append((i, cands))
        if len(cands) == 1:
            unambiguous.add(i)
    n_moved = 0
    for i, cands in info:
        if len(cands) <= 1:
            continue
        chosen, reason = _pick_candidate(clips, i, cands, unambiguous)
        old_s, old_e = clips[i]["m"]["s_start"], clips[i]["m"]["s_end"]
        if chosen[0] == old_s and chosen[1] == old_e:
            continue
        others = [x for x in cands if x[0] != chosen[0]]
        clips[i]["ambig_note"] = _ambig_note(chosen, others, reason, clips, i)
        _shift_match(clips[i]["m"], chosen[0])
        n_moved += 1
    return n_moved


def scripture_token_indices(script_text, script_tokens):
    """经文出处对应的 token 下标。"""
    used, idx = set(), set()
    for mm in SCRIPTURE_REF_RE.finditer(script_text or ""):
        toks = tokenize(mm.group(0))
        n = len(toks)
        if n == 0:
            continue
        for start in range(0, len(script_tokens) - n + 1):
            if any(j in used for j in range(start, start + n)):
                continue
            if list(script_tokens[start:start + n]) == toks:
                for j in range(start, start + n):
                    idx.add(j)
                    used.add(j)
                break
    return idx


def count_ignored_scripture_refs(missing_off, script_text):
    """开关打开后会从缺失清单拿掉的经文出处条数。"""
    n = 0
    for mm in missing_off or []:
        txt = mm.get("text") or ""
        if not SCRIPTURE_REF_RE.search(txt):
            continue
        leftover = SCRIPTURE_REF_RE.sub(" ", txt).strip()
        if len(tokenize(leftover)) < 3:
            n += 1
    return n


def format_missing_footer(ambig_count=0, scripture_ignored=0, incomplete_count=0,
                          estimated_timing=0):
    lines = []
    if ambig_count:
        lines.append("歧义裁决: %d 处" % int(ambig_count))
    if scripture_ignored:
        lines.append("已忽略 %d 处经文出处" % int(scripture_ignored))
    if incomplete_count:
        lines.append("%s: %d 处（不当缺失，请先听片段）" % (
            INCOMPLETE_MARK, int(incomplete_count)))
    if estimated_timing:
        lines.append("%s: %d 个片段（切点可能落在词中间）" % (
            TIMING_ESTIMATED_MARK, int(estimated_timing)))
    return lines


def tag_incomplete_missing(items, missing, clips):
    """Holes next to an incomplete transcript are ⚠, not 'missing copy'."""
    names = {c.get("name") for c in (clips or []) if c.get("incomplete")}
    if not names:
        return [dict(mm, kind=mm.get("kind") or "missing") for mm in missing]
    owner = {}
    for it in items or []:
        if not it.get("keep", True):
            continue
        name = it.get("name")
        for a, b in it.get("s_runs") or []:
            for t in range(int(a), int(b)):
                owner[t] = name
    out = []
    for mm in missing:
        tagged = dict(mm)
        prev = owner.get(int(mm["s"]) - 1)
        nxt = owner.get(int(mm["e"]))
        if prev in names or nxt in names:
            tagged["kind"] = "incomplete"
        else:
            tagged["kind"] = tagged.get("kind") or "missing"
        out.append(tagged)
    return out

def _transcript_better(new_m, old_m):
    """新词表是否比旧的更认得出文案。先比命中词数, 同分再比命中率。

    命中率必须参与: 9 个正确词埋在 30 个废话里 (hit 同分, ratio 31%)
    换成 9/9 (100%) 才是 Tik009 那种误报的修法。只比 hit 会把修好的结果丢掉。
    """
    nh = int((new_m or {}).get("hit") or 0)
    oh = int((old_m or {}).get("hit") or 0)
    nr = float((new_m or {}).get("ratio") or 0.0)
    oratio = float((old_m or {}).get("ratio") or 0.0)
    if nh != oh:
        return nh > oh
    return nr > oratio + 1e-9

def _write_clip_asr_cache(clip, language, asr_cache, out_dir, deep=False):
    if asr_cache is None:
        return
    try:
        key = clip_fingerprint(clip["path"], language)
        ent = asr_cache.get(key) or {}
        words = clip.get("words") or []
        ent["words"] = [[w[0], round(float(w[1]), 3),
                         round(float(w[2]), 3)] for w in words]
        ent.setdefault("dur", round(float(clip.get("dur") or 0.0), 3))
        ent.setdefault("fps", round(float(clip.get("fps") or 30.0), 3))
        if not ent.get("env") and clip.get("env") is not None:
            ent["env"] = env_to_b64(clip.get("env"))   # 重转不该弄丢音频包络
        if deep:
            ent["deep"] = 1
        asr_cache[key] = ent
        if out_dir:
            save_asr_cache(out_dir, asr_cache)
    except Exception:
        pass

def _adopt_if_better(clip, new_words, script_tokens, used, log=None):
    if not new_words:
        return False
    m_new = match_clip_global(new_words, script_tokens)
    old_m = clip.get("m") or {}
    if not _transcript_better(m_new, old_m):
        if callable(log):
            log(f"    · {used}命中{m_new['hit']}词/"
                f"{m_new['ratio'] * 100:.0f}%, 未超过原结果 "
                f"({int(old_m.get('hit') or 0)}词/"
                f"{float(old_m.get('ratio') or 0) * 100:.0f}%), 保持原样")
        return False
    if callable(log):
        log(f"    ✓ {used}命中{m_new['hit']}词/"
            f"{m_new['ratio'] * 100:.0f}% "
            f"(原 {int(old_m.get('hit') or 0)}词/"
            f"{float(old_m.get('ratio') or 0) * 100:.0f}%), "
            f"转出 {len(new_words)} 个词 → 采用")
    clip["words"] = new_words
    clip["m"] = m_new
    return True

def rescue_unmatched(clip, script_tokens, language, model=None,
                     asr_cache=None, out_dir=None, log=None):
    """初匹配失败时, 先绕过 VAD 用主力模型重转, 还不行再上复核模型。

    whisperX 的 VAD 会把三人现场的气声/串音切成一堆废话词, 命中率被稀释到
    低置信线以下, 而词速看起来「正常」, 词速体检不会触发。
    主力模型 + vad_filter=False 在 Tik009 上能把 24 段误报全部救回来。
    """
    def _say(s):
        if callable(log):
            log(s)

    m = clip.get("m") or {}
    if m.get("matched") or m.get("partial"):
        return False
    if clip.get("deep"):
        return False

    name = clip.get("name") or os.path.basename(clip.get("path") or "")
    dur = float(clip.get("dur") or 0.0)
    path = clip.get("path") or ""
    _say(f"    ↻ {name}: 低置信({float(m.get('ratio') or 0) * 100:.0f}%) "
         f"→ 绕过 VAD 重转")

    w2 = []
    try:
        if model is not None and path:
            _seed_all()
            audio = load_audio_16k(path)
            w2 = flatten_words(
                _fw_transcribe_words(model, audio, language, dur), log=None)
    except Exception as e:
        _say(f"    ⚠ 绕过 VAD 重转失败({str(e)[:50]})")

    changed = _adopt_if_better(clip, w2, script_tokens, "主力模型·绕过VAD", log)
    mm = clip.get("m") or {}
    if changed and (mm.get("matched") or mm.get("partial")):
        _write_clip_asr_cache(clip, language, asr_cache, out_dir, deep=False)
        return True

    words, used = deep_transcribe(path, language, dur, model=model, log=log)
    clip["deep"] = True
    if _adopt_if_better(clip, words, script_tokens, used, log):
        changed = True
    if changed:
        _write_clip_asr_cache(clip, language, asr_cache, out_dir, deep=True)
    return changed

# ----------------------------------------------------------------------
# v3 核心: 全局去重规划 (文案每个词只保留一次)
# ----------------------------------------------------------------------

def fmt_t(t, fps=30.0):
    """时间格式: 秒 + 帧号, 如 '3.42s/f103'。"""
    return f"{t:.2f}s/f{int(round(t * max(1.0, fps)))}"

def move_to_recycle_bin(paths):
    """将文件移入 Windows 回收站 (可撤销)。返回 (成功?, 说明)。"""
    paths = [p for p in (paths or []) if os.path.exists(p)]
    if not paths:
        return True, "无需移动"
    if os.name != "nt":
        return False, "非 Windows, 未移动"
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", wintypes.LPVOID),
                ("lpszProgressTitle", ctypes.c_wchar_p),
            ]

        FO_DELETE = 3
        FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = \
            0x0004, 0x0010, 0x0040, 0x0400
        # pFrom 需要 双\0 结尾、单\0 分隔; 必须用 buffer 保证嵌入 \0 不被截断
        src = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
        buf = ctypes.create_unicode_buffer(src, len(src) + 1)
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = ctypes.cast(buf, ctypes.c_wchar_p)
        op.pTo = None
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if res == 0 and not op.fAnyOperationsAborted:
            return True, f"已移入回收站 {len(paths)} 个文件"
        return False, f"SHFileOperation 返回码 {res}"
    except Exception as e:
        return False, str(e)

def _sub_intervals(ivals, lo, hi):
    """从区间列表 [[a,b),...] 中挖掉 [lo, hi)。"""
    out = []
    for a, b in ivals:
        if hi <= a or lo >= b:
            out.append([a, b])
            continue
        if a < lo:
            out.append([a, lo])
        if hi < b:
            out.append([hi, b])
    return [iv for iv in out if iv[1] > iv[0]]

def _intersect_blocks(blocks, allowed):
    """匹配块 (a,b,size) 与允许的文案区间求交, 必要时拆分块。"""
    out = []
    for (a, b, size) in blocks:
        for lo, hi in allowed:
            s, e = max(b, lo), min(b + size, hi)
            if e > s:
                out.append((a + (s - b), s, e - s))
    out.sort(key=lambda x: x[0])
    return out

def _env_i(env, t):
    n = len(env["db"])
    return max(0, min(int(round(float(t) / env["hop"])), n - 1))

def _scan_edge(env, i_from, i_to):
    """从 i_from 朝 i_to 走, 返回"声音重新抬起来"的那一帧; 一路安静则 None。

    判据是相对 + 绝对两条同时成立 (见 EDGE_RISE / EDGE_ABS_MARGIN 处的说明)。
    """
    db = env["db"]
    abs_thr = env["floor"] + EDGE_ABS_MARGIN
    step = 1 if i_to >= i_from else -1
    m = float(db[i_from])
    i = i_from
    while (i <= i_to) if step > 0 else (i >= i_to):
        v = float(db[i])
        if v < m:
            m = v
        elif v > m + EDGE_RISE and v > abs_thr:
            return i
        i += step
    return None

def refine_tail(env, t_word_end, b_default, next_start, dur):
    """出点精修: 在词尾之后的静音里下刀, 绝不切在下一个杂音/词的身上。

    env=None (拿不到音频) 时退化成"不越过下一个词的起点", 仍比老版本干净。
    """
    t_word_end = float(t_word_end)
    b_default = float(b_default)
    hard = float(next_start) if next_start is not None else None
    t = b_default
    if env:
        hi = b_default if hard is None else min(b_default, max(t_word_end + 0.02, hard))
        i0, i1 = _env_i(env, t_word_end), _env_i(env, hi)
        if i1 > i0:
            i_rise = _scan_edge(env, i0, i1)
            if i_rise is not None:
                t = i_rise * env["hop"] - EDGE_GUARD
    if hard is not None:
        t = min(t, hard - EDGE_GUARD)
    # 下限: 词尾之后总得留一点, 但也不能为了留白反而切进杂音里
    lo = min(t_word_end + EDGE_MIN_TAIL, b_default)
    if hard is not None:
        lo = min(lo, max(t_word_end + 0.01, hard - 0.01))
    t = max(t, lo)
    return max(0.0, min(dur, min(t, b_default)))

def refine_head(env, t_word_start, a_default, prev_end):
    """入点精修: 从词头往回走, 撞上前一句的尾音就停在它之后的静音里。"""
    t_word_start = float(t_word_start)
    a_default = float(a_default)
    hard = float(prev_end) if prev_end is not None else None
    t = a_default
    if env:
        lo = a_default if hard is None else max(a_default, min(t_word_start - 0.02, hard))
        i1, i0 = _env_i(env, t_word_start), _env_i(env, lo)
        if i1 > i0:
            i_rise = _scan_edge(env, i1, i0)      # 往回扫
            if i_rise is not None:
                t = i_rise * env["hop"] + ENV_WIN + EDGE_GUARD
    if hard is not None:
        t = max(t, hard + EDGE_GUARD)
    hi = max(t_word_start - EDGE_MIN_TAIL, a_default)
    if hard is not None:
        hi = max(hi, min(t_word_start - 0.01, hard + 0.01))
    t = min(t, hi)
    return max(0.0, max(t, a_default))

def _plan_segments(words, eff, script_tokens):
    """
    由有效匹配块规划片段内保留区间。
    返回 (seg_ranges, internal_cuts):
      seg_ranges    : [[w0, w1], ...] 保留的词索引闭区间 (按时间序)
      internal_cuts : [(cw0, cw1, 原因), ...] 剪掉的词索引闭区间
    规则: 相邻匹配块两侧间隔都小 → 连续朗读, 合并不切;
          片段侧多出一堆词而文案侧几乎无缺口 → 中间是重复朗读/废话 → 切一刀;
          重复朗读且两遍同样准确时 → 保留后录的那一遍 (改剪前一遍)。
    """
    groups = [[eff[0]]]
    for b in eff[1:]:
        pb = groups[-1][-1]
        da = b[0] - (pb[0] + pb[2])          # 片段侧间隔词数
        db = b[1] - (pb[1] + pb[2])          # 文案侧间隔词数
        gap_sec = words[b[0]][1] - words[pb[0] + pb[2] - 1][2]
        if (da - max(0, db)) >= DEDUP_MIN_CUT_WORDS and db <= DEDUP_DB_TOL \
                and gap_sec >= DEDUP_MIN_CUT_SEC:
            groups.append([b])               # 真正的内部切点
        else:
            groups[-1].append(b)             # 连续朗读, 合并

    raw_ranges = []
    for g in groups:
        raw_ranges.append([g[0][0], g[-1][0] + g[-1][2] - 1])

    seg_ranges = [raw_ranges[0]]
    internal_cuts = []
    for gi in range(1, len(raw_ranges)):
        prev, cur = seg_ranges[-1], raw_ranges[gi]
        gap_start, gap_end = prev[1] + 1, cur[0] - 1
        cut0, cut1 = gap_start, gap_end
        reason = "与文案无关的废话"
        gap_toks = [w[0] for w in words[gap_start:gap_end + 1]]
        pb = groups[gi - 1][-1]              # 前一组最后一个匹配块
        nb = groups[gi][0]                   # 后一组第一个匹配块

        def _r(win):
            return difflib.SequenceMatcher(None, gap_toks, win,
                                           autojunk=False).ratio()
        # gap 是"后录的那遍"? → 与前块尾部的文案比较
        L = min(len(gap_toks), pb[2])
        r_prev = _r(script_tokens[pb[1] + pb[2] - L: pb[1] + pb[2]]) if L >= 3 else 0.0
        # gap 是"先录的那遍"? → 与后块头部的文案比较 (SM 已自动保留了后录那遍)
        L2 = min(len(gap_toks), nb[2])
        r_next = _r(script_tokens[nb[1]: nb[1] + L2]) if L2 >= 3 else 0.0
        if max(r_prev, r_next) >= 0.6:
            if r_next >= r_prev:
                reason = "重复朗读(已保留后录的那遍)"
            else:
                reason = "重复朗读"
                if r_prev >= 0.95 and pb[2] > L:
                    # gap 是后录且两遍几乎同样准确 → 保留后录, 改剪前一遍
                    reason = "重复朗读(同分保留后录)"
                    cut0 = pb[0] + pb[2] - L     # 前一遍起点
                    cut1 = gap_start - 1         # 前一遍终点
                    prev[1] = cut0 - 1
                    cur[0] = gap_start           # 后录那遍并入当前保留段
        if cut1 >= cut0:
            internal_cuts.append((cut0, cut1, reason))
        seg_ranges.append(cur)
    return seg_ranges, internal_cuts

# ═══════════ 缺失检测 + 复核 (0829) ═══════════
#
# 老逻辑: 覆盖表上出现 ≥3 词的洞 → 直接写进"缺失文案清单", 让人去重做片段。
# 0829 那批两处缺失【全是假的】, 两种不同的成因:
#   · 组03: 03b 里 "Unless you repent, you too will all perish." 明明念了,
#           whisperX 的 VAD+对齐链路把 3.42–6.68 秒那段吞了 (对齐还把 "what"
#           拉成 4.13 秒)。8 个词的洞 → 报缺失。
#   · 组02: 02c 把 "Hear, O Israel" 转成 "hero israel"(同音合并), 文案侧
#           hear/o 没人认领, 凑够 3 个 token → 报缺失。
# 两次都让人去"重新生成片段", 而片段本身完全没问题 —— 白烧一次生成额度。
#
# 治本的判据: **报缺失之前, 先排除"是转录出错"这一种可能**。
# 一个洞如果两侧的命中词落在同一个片段里, 那这段话在物理上就在那个片段的
# 音频里 —— 要么是转录漏了, 要么是转错了词, 不可能是"没人讲过"。
# 所以先把这一两个可疑片段用更强的路径重转一遍 (复核模型 / 绕过 VAD),
# 重新匹配、重新规划, 洞还在才写进清单。
#
# 代价: 正常批次一次都不会触发 (没有洞就不复核); 真报洞时也只重转
# 那一两个片段, 几秒钟的事。

MISSING_HOLE = 3  # ≥3 tokens = 缺失. 1–2 = 疑似缺词 (T20b).

MISSING_NAME = "缺失文案清单.txt"


def missing_report_dirs(out_dir="", out_dirs=None, folders=None):
    """缺失清单可能落在哪些目录，按「最可能」排在前面。"""
    dirs = []

    def _add(d):
        d = str(d or "").strip()
        if d and d not in dirs:
            dirs.append(d)

    _add(out_dir)
    src = out_dirs or {}
    for d in (src.values() if isinstance(src, dict) else src):
        _add(d)
    for f in (folders or []):
        if f:
            _add(os.path.join(str(f), "_stitched"))
    return dirs


def _last_report_file():
    try:
        from cutora.core.paths import settings_file
        return settings_file("last_missing_report.json")
    except Exception:
        return ""


def _remember_missing_report(path):
    """记住最近一次拼接写出的缺失清单绝对路径（「打开缺失报告」优先用它）。"""
    rf = _last_report_file()
    if not rf or not path:
        return
    try:
        os.makedirs(os.path.dirname(rf), exist_ok=True)
        with open(rf, "w", encoding="utf-8") as f:
            json.dump({"path": os.path.abspath(path), "ts": time.time()}, f, ensure_ascii=False)
    except Exception:
        pass


def last_missing_report():
    rf = _last_report_file()
    try:
        with open(rf, "r", encoding="utf-8") as f:
            p = (json.load(f) or {}).get("path") or ""
        return p if p and os.path.isfile(p) else ""
    except Exception:
        return ""


def find_missing_report(dirs):
    """这些目录里最新的一份缺失清单; 没有就返回 ''。"""
    best, best_mtime = "", -1.0
    for d in dirs or []:
        path = os.path.join(str(d or ""), MISSING_NAME)
        try:
            if not os.path.isfile(path):
                continue
            m = os.path.getmtime(path)
        except OSError:
            continue
        if m > best_mtime:
            best, best_mtime = path, m
    return best


def reveal_in_explorer(path):
    """在资源管理器里选中这个文件(或打开这个目录)。

    注意：不能把 ``/select,<路径>`` 当成列表参数传给 Popen —— 路径里一有空格或括号
    （例如 ``V01-简单的小天使 (1)``），list2cmdline 会把整段包成 ``"/select,D:\\a b\\x.txt"``，
    explorer 不认这种写法，会直接打开「文档」文件夹，看起来就是「打开的位置不对」。
    正确写法是 ``explorer /select,"D:\\a b\\x.txt"``（引号只包路径）。
    """
    path = os.path.abspath(str(path or ""))
    if not path:
        return False
    try:
        if os.name == "nt":
            import subprocess as _sp
            flags = getattr(_sp, "CREATE_NO_WINDOW", 0)
            if os.path.isfile(path):
                _sp.Popen('explorer /select,"%s"' % os.path.normpath(path), creationflags=flags)
            elif os.path.isdir(path):
                os.startfile(os.path.normpath(path))
            else:
                parent = os.path.dirname(path)
                if not os.path.isdir(parent):
                    return False
                os.startfile(os.path.normpath(parent))
            return True
        target = path if os.path.isdir(path) else os.path.dirname(path)
        _no_window_popen(["xdg-open", target])
        return True
    except Exception:
        return False
GRADE_COLOR = {
    "missing": "#E24B4A",
    "suspected": "#E8A317",
    "incomplete": "#9AA0A6",
}


def _hole_context(script_raws, j, k, window=3):
    before = " ".join(x for x in (script_raws[max(0, j - window):j] or []) if x)
    after = " ".join(x for x in (script_raws[k:k + window] or []) if x)
    return before, after


def find_missing(items, script_tokens, script_raws,
                 ignore_scripture_refs=True, script_text=None,
                 suspected=None):
    """文案里没有任何保留片段讲到的部分 → [{s, e, text, grade}].

    ≥3 词 = 缺失（进返回列表）。1–2 词 = 疑似缺词，写入 suspected
    （附前后各 3 词）。不要把阈值降到 1。
    ignore_scripture_refs: 括号内经文出处不参与缺失判定。
    """
    covered = set()
    for it in items:
        if not it.get("keep", True):
            continue
        for a, b in it.get("s_runs") or []:
            covered.update(range(int(a), int(b)))
    if ignore_scripture_refs:
        text = script_text
        if text is None:
            text = " ".join(x for x in (script_raws or []) if x)
        covered.update(scripture_token_indices(text, script_tokens))
    out = []
    if suspected is not None:
        suspected.clear()
    M = len(script_tokens)
    j = 0
    while j < M:
        if j in covered:
            j += 1
            continue
        k = j
        while k < M and k not in covered:
            k += 1
        txt = " ".join(x for x in script_raws[j:k] if x)
        before, after = _hole_context(script_raws, j, k)
        rec = {
            "s": j, "e": k, "text": txt,
            "context_before": before, "context_after": after,
        }
        if k - j >= MISSING_HOLE:
            rec["grade"] = "missing"
            out.append(rec)
        elif k - j >= 1:
            rec["grade"] = "suspected"
            if suspected is not None:
                suspected.append(rec)
        j = k
    return out

def _suspect_clips(g_missing, clips, items, max_n=4):
    """洞该由谁负责: ① 没匹配上的片段 (洞里的人) ② 洞两侧已命中的邻居。

    旧实现只看邻居。Tik009 的 24 段低置信一个词都没认领, 洞夹在已经成功的
    s001 和 s003 之间, 复核去重转了成功的两条, 真正失败的 s002 从未进名单。
    没匹配上的不受 max_n 限制 (上限 40, 防极端组把复核拖死);
    邻居仍只取 max_n 个, Normal 有洞时的成本不变。
    """
    idx_by_name = {c["name"]: i for i, c in enumerate(clips)}
    owner = {}
    for it in items:
        if not it.get("keep", True):
            continue
        i = idx_by_name.get(it.get("name"))
        if i is None:
            continue
        for a, b in it.get("s_runs") or []:
            for t in range(int(a), int(b)):
                owner[t] = i
    neighbors = []
    for mm in g_missing:
        for t in (mm["s"] - 1, mm["e"]):
            i = owner.get(t)
            if i is not None and i not in neighbors:
                neighbors.append(i)
    unmatched = []
    for i, c in enumerate(clips):
        m = c.get("m") or {}
        if m.get("matched") or m.get("partial"):
            continue
        if i not in unmatched:
            unmatched.append(i)
    if len(unmatched) > 40:
        unmatched = unmatched[:40]
    neigh = [i for i in neighbors if i not in unmatched][:max_n]
    return unmatched + neigh

def recheck_missing(g_missing, clips, items, script_tokens, language,
                    model=None, asr_cache=None, out_dir=None, log=None):
    """重转可疑片段。有片段的词表变好了返回 True (调用方重算一轮)。"""
    def _say(x):
        if callable(log):
            log(x)

    changed = False
    for i in _suspect_clips(g_missing, clips, items):
        c = clips[i]
        if c.get("deep"):
            continue                      # 这一轮已经深转过, 不重复烧
        m0 = c.get("m") or {}
        why = ("低置信未命中" if not (m0.get("matched") or m0.get("partial"))
               else "洞就在它讲的范围里")
        _say(f"  🔎 复核 {c['name']}: {why}, 先排除转录出错 → 重转")
        words, used = deep_transcribe(c["path"], language, c.get("dur") or 0.0,
                                      model=model, log=log)
        c["deep"] = True
        if not words:
            _say(f"    · {used}没拿到结果, 保持原样")
            continue
        # 采纳看命中词数, 同分再看命中率 (废话稀释的那次会输)。
        if not _adopt_if_better(c, words, script_tokens, used, log):
            continue
        changed = True
        _write_clip_asr_cache(c, language, asr_cache, out_dir, deep=True)
    return changed

def plan_group_dedup(clips, order, script_tokens, tail_pad, trim_head, log=None,
                     pinned=False, edge_refine=EDGE_REFINE_DEFAULT):
    """
    组内全局去重规划 → items 列表。
    - 文案每个词在成品中只保留一次 (跨片段 + 片段内部);
    - 整段重复 / 与文案完全无关的片段 → keep=False + recycle=True (稍后移入回收站);
    - 片段内部的重复/废话 → 多保留区间 segs, 编码时分段取用重新拼接。
    """
    def _log(s):
        if callable(log):
            log(s)

    n_clips = len(clips)
    matched = [i for i in range(n_clips)
               if clips[i]["m"]["matched"] or clips[i]["m"]["partial"]]
    allowed = {i: [[clips[i]["m"]["s_start"], clips[i]["m"]["s_end"]]]
               for i in matched}
    # claimed = 剪枝【前】的认领区间, 只用来算"文案覆盖率"; allowed = 剪枝后的
    # 区间, 用来决定成品怎么剪。两者必须分开, 原因见下面 claimed[i] 赋值处。
    claimed = {}
    dup_notes = {i: [] for i in range(n_clips)}
    recycle_reason = {i: None for i in range(n_clips)}

    def _span(i):
        iv = allowed.get(i) or []
        return (iv[0][0], iv[-1][1]) if iv else None

    def _bucket(i):
        return int(round(clips[i]["m"]["ratio"] / QUALITY_BUCKET))

    # ---- 跨片段去重: 词级认领 ----
    # 文案的每个词只能被一个片段"认领"。认领优先级: 质量(命中率分桶)高者先;
    # 同分 → 组内顺序靠后的先 (保留后录); 再同 → 大块优先。
    # 只有真正讲过该词的片段才能认领, 因此不会误删只有自己讲过的句子。
    M = len(script_tokens)
    owner = [-1] * M
    order_pos = {i: order.index(i) for i in range(n_clips)}
    cands = []
    for i in matched:
        for (a, b, size) in clips[i]["m"]["blocks"]:
            cands.append((i, a, b, size))
    cands.sort(key=lambda t: (-_bucket(t[0]), -order_pos[t[0]], -t[3]))
    for i, a, b, size in cands:
        j = b
        while j < b + size:
            if owner[j] == -1:
                owner[j] = i
            j += 1

    for i in matched:
        # 本片段实际讲过的词中, 归自己的连续区间
        runs, lost = [], []
        for (a, b, size) in clips[i]["m"]["blocks"]:
            j = b
            while j < b + size:
                mine = owner[j] == i
                k = j
                while k < b + size and (owner[k] == i) == mine:
                    k += 1
                (runs if mine else lost).append([j, k])
                j = k
        # 相邻/连续 run 合并
        runs.sort()
        merged = []
        for r in runs:
            if merged and r[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], r[1])
            else:
                merged.append(list(r))
        # ---- 覆盖率账本 与 剪切计划 必须分开 ----
        # 下面那行 >=2 的剪枝, 是为了不让一个孤立的词在成品里白挨一刀 ——
        # 对"怎么剪"来说是对的, 保持不变。
        # 但【不能】拿剪枝后的结果去算"文案覆盖": 那个词明明有片段讲过, 一剪掉
        # 它就从覆盖表里凭空消失, 于是被算进"缺失"。更糟的是, 你补进新片段后
        # 词级认领会重新洗牌, 谁被切碎、碎在哪每次都不一样 —— 缺失的位置也就
        # 每次都不一样, 补完又冒出新的。这就是"缺 14 段→补→缺 6 段→补→缺 1 段"
        # 那个死循环的来源。
        # 分开记账之后, 覆盖率 = "这个词到底有没有片段讲过", 和谁抢到它无关,
        # 加片段不会再让原本覆盖住的地方重新变成窟窿。
        claimed[i] = [list(r) for r in merged]
        big = [r for r in merged if r[1] - r[0] >= 2]
        # ---- 孤立的短 run 不能一律扔掉 (0901 事故) ----
        # 现场: 文案 "WHAT SINS SHOULD CHRISTIANS…", whisperX 把 sins 听成
        # sin, 于是 what 落单成了一个 1 词的 run, 被 >=2 这行剪枝扔掉 ——
        # 保留区间从 should 才开始, 成品开头的 "what sin" 整句没了。
        # (04i 同理: revelation 落单, 开头的 "revelation 3" 没了。)
        # 更阴的是 claimed 记的是剪枝【前】的区间, 所以覆盖表认为这两个词
        # "有人讲过", 缺失检测一个字都不报 —— 白丢了还没人吭声。
        #
        # 该扔的是"离所有大块都远"的孤词 (那种确实不值得为它单独留一刀);
        # 紧挨着大块、中间只隔着没人认领的 ASR 错词的, 必须留下。留下也不会
        # 多挨一刀: _plan_segments 那边间隔小于 DEDUP_MIN_CUT_WORDS 根本不切。
        # 判据里的"没人认领"很关键: 空档里只要有词被别的片段抢走了, 那就是
        # 真去重, 合过去会让同一句话在成品里念两遍。
        if big and len(big) < len(merged):
            small = []
            for r in merged:
                if r[1] - r[0] >= 2:
                    continue
                for b in big:
                    if r[1] <= b[0]:
                        lo, hi = r[1], b[0]
                    elif b[1] <= r[0]:
                        lo, hi = b[1], r[0]
                    else:
                        lo = hi = r[0]
                    if hi - lo > SMALL_RUN_JOIN_TOL:
                        continue
                    if any(ls < hi and lo < le for ls, le in lost):
                        continue
                    small.append(r)
                    break
            if small:
                big = sorted(big + small)
        allowed[i] = big if big else merged
        # 被别的片段抢走的部分 → 生成说明
        loser_of = {}
        for (s, e) in lost:
            cnt = {}
            for j in range(s, e):
                o = owner[j]
                if o not in (-1, i):
                    cnt[o] = cnt.get(o, 0) + 1
            if not cnt:
                continue
            o = max(cnt, key=cnt.get)
            loser_of[o] = loser_of.get(o, 0) + (e - s)
            if e - s >= 2:
                dup_notes[i].append(
                    f"✂ 与 {clips[o]['name']} 重复 (文案第{s + 1}~{e}词), "
                    f"该处以 {clips[o]['name']} 为准")
        if not allowed[i]:
            top = max(loser_of, key=loser_of.get) if loser_of else None
            who = clips[top]["name"] if top is not None else "其他片段"
            recycle_reason[i] = f"整段内容已被 {who} 覆盖 (完全重复)"

    # ---- 逐片段生成保留区间 ----
    group_has_match = bool(matched)
    items = []
    for k, i in enumerate(order):
        c = clips[i]
        m, words, dur = c["m"], c["words"], c["dur"]
        fps = c.get("fps", 30.0)
        cuts_desc = list(dup_notes.get(i) or [])
        segs = [[0.0, dur]]
        in_t, out_t = 0.0, dur
        note, tail_txt = "", ""
        level, keep, recycled = "low", True, False

        if recycle_reason.get(i):
            keep, recycled, level = False, True, "dup"
            note = f"🗑 {recycle_reason[i]} → 将移入回收站"
        elif allowed.get(i):
            eff = _intersect_blocks(m["blocks"], allowed[i])
            if not eff:
                keep, recycled, level = False, True, "dup"
                note = "🗑 去重后没有剩余可用内容 → 将移入回收站"
            else:
                level = "ok" if m["matched"] else "partial"
                seg_ranges, internal_cuts = _plan_segments(
                    words, eff, script_tokens)
                # 音频包络: 有就用来把切口挪进静音里, 没有就退回老规则。
                env = c.get("env") if edge_refine else None
                n_sr = len(seg_ranges)
                segs = []
                for si, (w0, w1) in enumerate(seg_ranges):
                    if not words or w0 < 0 or w1 < 0 or w0 >= len(words) or w1 >= len(words):
                        continue
                    t0, t1 = words[w0][1], words[w1][2]
                    # 入点: 词头前的余量, 但不许把前一个(被剪掉的)词的尾音带进来
                    prev_end = words[w0 - 1][2] if w0 > 0 else None
                    if si == 0:
                        # 首段的头【一个字都不往后挪】, 和精修前完全一样。
                        # 理由: 片段开头前面那点东西没有任何"它是废话"的证据 ——
                        # 上一个词多半是被 ASR 听错了的正词 (sins→sin), 把入点
                        # 卡到它后面就是把开头的正词剪掉, 这个代价远大于留下
                        # 半秒杂音。往后挪只在【内部切口】处做, 那里的间隔是
                        # _plan_segments 判过的 (≥4 词且 ≥0.8 秒) 真废话。
                        a = 0.0
                        if (trim_head or level == "partial") and t0 > 0.8:
                            a = max(0.0, t0 - HEAD_PAD)
                    else:
                        a = refine_head(env, t0, max(0.0, t0 - CUT_PAD), prev_end)
                    # 出点: 词尾后的余量, 但不许剐到后面那个该剪掉的音
                    nxt_start = words[w1 + 1][1] if w1 + 1 < len(words) else None
                    pad = tail_pad if si == n_sr - 1 else CUT_PAD
                    b_plain = min(dur, t1 + pad)
                    b = refine_tail(env, t1, b_plain, nxt_start, dur)
                    if b_plain - b > 0.015:
                        after = " ".join(w[0] for w in words[w1 + 1:w1 + 4])
                        cuts_desc.append(
                            f"✎ 出点精修: 按余量本该到 {fmt_t(b_plain, fps)}, "
                            f"那里还有声音"
                            + (f"(“{after}”)" if after else "(未转录出的杂音)")
                            + f" → 改在 {fmt_t(b, fps)} 的静音处下刀")
                    if b > a:
                        segs.append([round(a, 3), round(b, 3)])
                if not segs:
                    segs = [[0.0, dur]]
                for (cw0, cw1, reason) in internal_cuts:
                    cs, ce = words[cw0][1], words[cw1][2]
                    txt = " ".join(w[0] for w in words[cw0:cw1 + 1][:14])
                    cuts_desc.append(
                        f"✂ 剪去 {fmt_t(cs, fps)} → {fmt_t(ce, fps)} "
                        f"({ce - cs:.1f}s, {reason}): “{txt}”")
                in_t, out_t = segs[0][0], segs[-1][1]
                last_w = seg_ranges[-1][1]
                tail_txt = " ".join(w[0] for w in words[last_w + 1:][:12])
                head_cut = f"  裁头{in_t:.1f}s" if in_t > 0.05 else ""
                tc = dur - out_t
                tail_cut = f"  裁尾{tc:.1f}s" if tc > 0.05 else ""
                mid = (f"  内部剪{len(seg_ranges) - 1}刀"
                       if len(seg_ranges) > 1 else "")
                pref = "" if m["matched"] else "◐ 部分命中, "
                note = f"{pref}命中{m['ratio'] * 100:.0f}%{head_cut}{tail_cut}{mid}"
                if sum(b - a for a, b in segs) < 0.3:
                    segs = [[0.0, dur]]
                    in_t, out_t = 0.0, dur
                    note += "  ⚠ 切后过短已还原"
        else:
            if (group_has_match and len(words) >= 5
                    and m["ratio"] < IRRELEVANT_RATIO):
                keep, recycled, level = False, True, "junk"
                spoken = " ".join(w[0] for w in words[:14])
                note = (f"🗑 与文案完全无关(命中率{m['ratio'] * 100:.0f}%) "
                        f"→ 将移入回收站  实际说话内容: “{spoken}...”")
            else:
                note = f"⚠ 低置信({m['ratio'] * 100:.0f}%), 整段保留, 请复核"

        if c.get("ambig_note"):
            note = c["ambig_note"] + (("  " + note) if note else "")
        if i != k and keep:
            note = f"↻ 顺序纠正(原第{i + 1}位→第{k + 1}位)  " + note

        # 本片段实际覆盖 (认领) 的文案区间 — 供缺失检测用
        if recycled:
            s_runs = []
        elif allowed.get(i):
            # 用剪枝前的 claimed 记覆盖率 (理由见上面 claimed[i] 赋值处的说明)
            s_runs = [[int(s), int(e)]
                      for s, e in (claimed.get(i) or allowed[i])]
        else:
            s_runs = []
            for (a, b, size) in m.get("blocks") or []:
                if s_runs and b <= s_runs[-1][1]:
                    s_runs[-1][1] = max(s_runs[-1][1], b + size)
                else:
                    s_runs.append([b, b + size])

        sspan = _span(i) or (m["s_start"], m["s_end"])
        items.append({
            "s_runs": s_runs,
            "path": c["path"], "name": c["name"], "dur": round(dur, 3),
            "fps": round(fps, 3),
            "in_t": round(in_t, 3), "out_t": round(out_t, 3),
            "segs": [[round(a, 3), round(b, 3)] for a, b in segs],
            "ratio": round(m["ratio"], 3),
            "matched": bool(m["matched"] or m["partial"]),
            "level": level, "keep": keep, "recycle": recycled, "seq": k + 1,
            "s_start": sspan[0], "s_end": sspan[1],
            "note": note, "tail": tail_txt, "cuts": cuts_desc,
        })
        if recycled:
            _log(f"  🗑 {c['name']}: {note}")
        elif len(segs) > 1:
            _log(f"  ✂ {c['name']}: 内部剪 {len(segs) - 1} 刀 "
                 + "; ".join(cuts_desc[-3:]))

    # ---- 认领后按各片段实际保留内容的文案位置重排拼接顺序 ----
    # (词级认领可能让"后录"片段拿走开头, 原顺序不再等于文案顺序)
    # 有 _order.json 钉死顺序时跳过这一步, 否则 ASR 会把钉死的顺序再打乱。
    if not pinned:
        slots = [idx for idx, it in enumerate(items)
                 if it["keep"] and it["matched"] and it["s_start"] is not None]
        by_script = sorted(slots, key=lambda idx: items[idx]["s_start"])
        if slots != by_script:
            arranged = list(items)
            for slot, src in zip(slots, by_script):
                arranged[slot] = items[src]
            items = arranged
            _log("  ↻ 去重后按文案位置重排拼接顺序: "
                 + " → ".join(it["name"] for it in items if it["keep"]))
    for k2, it in enumerate(items):
        it["seq"] = k2 + 1
    return items

# ----------------------------------------------------------------------
# 阶段1: 分析线程 (转录+对齐+切点)
# ----------------------------------------------------------------------

class AnalyzeWorker(QThread):
    progress = Signal(int)
    log = Signal(str)
    analyzed = Signal(dict)     # {组号: [cut_item, ...]}  cut_item 见下
    failed = Signal(str)
    # Language code whose aligner is missing. The UI downloads that one
    # language, then calls align_fetch_finished.
    need_align = Signal(str)

    def __init__(self, groups, scripts, get_model, get_align,
                 language=None, tail_pad=0.35, trim_head=True,
                 out_dir="", parent=None, edge_refine=EDGE_REFINE_DEFAULT,
                 out_dirs=None, ignore_scripture_refs=True):
        super().__init__(parent)
        self.groups = groups
        self.scripts = scripts
        self.get_model = get_model
        self.get_align = get_align
        self.language = language or getattr(_cfg, "LANGUAGE", "en")
        self.tail_pad = float(tail_pad)
        self.trim_head = bool(trim_head)
        self.edge_refine = bool(edge_refine)
        self.out_dir = out_dir
        # 多文件夹: {组号: 该组自己的 _stitched 目录}; 单文件夹时为空, 行为不变
        self.out_dirs = dict(out_dirs or {})
        self.ignore_scripture_refs = bool(ignore_scripture_refs)
        self.missing_map = {}   # {组号: {"clips": [...], "missing": [{s,e,text}]}}
        self.suspected_map = {}  # 1–2 词疑似缺词
        self.ambig_count = 0
        self.scripture_ignored = 0
        self.estimated_timing = 0
        self._running = True
        self._align_fetcher = None
        self._align_fetch_event = threading.Event()
        self._align_fetch_ok = False
        from cutora.asr.clip_worker import TimeoutCircuit
        self._timeout_circuit = TimeoutCircuit()

    def align_fetch_finished(self, ok: bool) -> None:
        self._align_fetch_ok = bool(ok)
        self._align_fetch_event.set()

    def _ensure_align_downloaded(self, lang: str) -> None:
        """Block until this language's aligner is on disk, or stop the batch."""
        from cutora.asr.align_fetch import fetch_plan
        from cutora.asr.stitch_language import StitchLanguageError
        if fetch_plan(lang) is None:
            return
        if not callable(self._align_fetcher):
            raise StitchLanguageError(
                "这种语言的对齐模型还没下载，这里没有界面可以下载，拼接没有开始。")
        self._align_fetch_event.clear()
        self._align_fetch_ok = False
        self.need_align.emit(str(lang))
        while self._running and not self._align_fetch_event.wait(0.2):
            pass
        if not self._running:
            raise StitchLanguageError("已取消，对齐模型没有下载，拼接没有开始。")
        if not self._align_fetch_ok:
            raise StitchLanguageError("对齐模型没有下载完成，拼接没有开始。")
        if fetch_plan(lang) is not None:
            raise StitchLanguageError(
                "对齐模型下载结束后仍然不在本地，拼接没有开始。")

    def stop(self):
        self._running = False

    def _stop_batch_circuit(self, exc):
        from cutora.asr.clip_worker import CIRCUIT_STOP_MSG
        msg = str(exc) or CIRCUIT_STOP_MSG
        try:
            from cutora.core.paths import ensure_user_layout, user_logs_dir
            ensure_user_layout()
            folder = os.path.join(user_logs_dir(), "asr")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "circuit.log"), "a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
                for p in getattr(self._timeout_circuit, "failed_paths", []) or []:
                    fh.write("  %s\n" % p)
        except Exception:
            pass
        self.log.emit("✘ " + msg)
        self.failed.emit(msg)

    def _transcribe_one(self, path, dur, script_tokens, model, align_model, metadata, client):
        """One clip. Process watchdog when ``client`` is set; else in-process + OOM drop."""
        from cutora.asr.tier import is_oom_error
        if client is not None:
            from cutora.asr.clip_worker import transcribe_with_downgrade
            return transcribe_with_downgrade(
                client, path, dur, self.language,
                log=self.log.emit, circuit=self._timeout_circuit)
        audio = load_audio_16k(path)
        _seed_all()
        asr_status = {}
        try:
            from cutora.asr import engine as asr_engine
            words = transcribe_clip(
                model, align_model, metadata, audio, dur,
                self.language, asr_engine.DEVICE, log=self.log.emit,
                script_tokens=script_tokens, status=asr_status)
            return words, bool(asr_status.get("incomplete")), int(
                asr_status.get("timing_tier") or timing_tier_of(words))
        except Exception as e:
            if is_oom_error(e):
                from cutora.asr.engine import get_whisperx_model, release_gpu_models
                from cutora.asr.tier import handle_oom
                cur = getattr(model, "_cutora_tier", "S")
                nxt = handle_oom(cur, log=self.log.emit)
                if nxt:
                    release_gpu_models()
                    os.environ["CUTORA_ASR_TIER"] = nxt
                    model = get_whisperx_model(log=self.log.emit)
                    from cutora.asr import engine as asr_engine
                    words = transcribe_clip(
                        model, align_model, metadata, audio, dur,
                        self.language, asr_engine.DEVICE, log=self.log.emit,
                        script_tokens=script_tokens, status=asr_status)
                    return words, bool(asr_status.get("incomplete")), int(
                        asr_status.get("timing_tier") or timing_tier_of(words))
            raise

    def _first_clip_path(self) -> str:
        for paths in (self.groups or {}).values():
            for path in paths or []:
                if path:
                    return str(path)
        from cutora.asr.stitch_language import StitchLanguageError
        raise StitchLanguageError("没有素材，无法检测语言，拼接没有开始。")

    def _load_align_once(self):
        """Call the injected loader once. The cache is engine._ALIGN_CACHE."""
        pair = getattr(self, "_align_pair", None)
        if pair is not None:
            return pair
        try:
            pair = self.get_align(self.language)
        except TypeError:
            pair = self.get_align()
        self._align_pair = pair
        return pair

    def _ask_confirm(self, detection):
        from PySide6.QtCore import QTimer
        from cutora.asr.stitch_language import StitchLanguageError
        from cutora.ui.detect_confirm import ask_low_confidence
        app = QApplication.instance()
        parent = self.parent()
        if app is None:
            raise StitchLanguageError(
                "检测到的语言置信度偏低，这里没有窗口可以确认。"
                "请改选一种语言后再拼。")
        if parent is None or QThread.currentThread() is app.thread():
            return ask_low_confidence(parent, detection)
        box = {}
        done = threading.Event()

        def _ui():
            try:
                box["v"] = ask_low_confidence(parent, detection)
            except Exception as exc:
                box["e"] = exc
            finally:
                done.set()

        QTimer.singleShot(0, parent, _ui)
        done.wait()
        if "e" in box:
            raise box["e"]
        return box.get("v")

    def _prepare_asr(self):
        """Lock the batch language, then load align once. None-tier stops here."""
        from cutora.asr.languages import whisper_language_arg
        from cutora.asr.models import align_tier, language_capability
        from cutora.asr.stitch_language import (
            StitchLanguageError, align_choice_log, detection_log,
            read_whisper_detection, read_whisper_info, resolve_detected_language,
            unstitchable_message,
        )
        explicit = whisper_language_arg(self.language)
        inproc = os.environ.get("CUTORA_ASR_INPROC") == "1"
        client = None
        model = None
        align_model = None
        metadata = None
        self._align_pair = None

        if explicit is not None and align_tier(explicit) == "none":
            raise StitchLanguageError(unstitchable_message(explicit))
        if explicit is not None:
            self._ensure_align_downloaded(explicit)

        if not inproc:
            try:
                from cutora.asr.clip_worker import AsrClient
                client = AsrClient(log=self.log.emit)
                if explicit is None:
                    client.start("en", defer_align=True)
                else:
                    client.start(explicit)
                self.log.emit("  ASR 跑在可杀子进程里（看门狗 %d× 时长）" % 10)
            except Exception as e:
                self.log.emit("  ⚠ ASR 子进程未启动，回退进程内: %s" % str(e)[:80])
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass
                client = None

        if explicit is None:
            try:
                path = self._first_clip_path()
                if client is not None:
                    info = client.detect(path)
                else:
                    model = self.get_model()
                    info = read_whisper_detection(model, path)
                detected, prob = read_whisper_info(info)
                self.log.emit(detection_log(detected, prob))
                locked = resolve_detected_language(info, self._ask_confirm)
                if locked != detected:
                    chosen = language_capability(locked)
                    self.log.emit("已改用%s（%s），整批都按这个语言。" % (
                        chosen.get("name_zh") or locked, locked))
                self.language = locked
                self.log.emit(align_choice_log(locked))
                self._ensure_align_downloaded(locked)
                if client is not None:
                    client.load_align(locked)
                else:
                    if model is None:
                        model = self.get_model()
                    align_model, metadata = self._load_align_once()
            except Exception:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
                raise
        else:
            self.language = explicit
            self.log.emit(align_choice_log(explicit))
            if client is None:
                model = self.get_model()
                align_model, metadata = self._load_align_once()

        if client is None and (model is None or align_model is None):
            self.failed.emit("模型加载失败, 请检查 models 目录")
            return None
        return client, model, align_model, metadata

    def run(self):
        from cutora.asr.stitch_language import StitchLanguageError
        client = None
        try:
            self.log.emit("正在准备识别（语言=%s）..." % (self.language or "auto"))
            prepared = self._prepare_asr()
            if prepared is None:
                return
            client, model, align_model, metadata = prepared

            total = sum(len(v) for v in self.groups.values())
            done = 0
            cuts_map = {}

            # 转录缓存: 文件没动的片段直接复用上次的词表 —— 不重算, 也不会变。
            # 多文件夹时每个素材文件夹各存各的缓存 (缓存跟着素材走, 以后单独
            # 重跑那一个文件夹照样命中), 所以按 out_dir 分开装。
            caches = {}
            n_reuse, n_fresh = 0, 0

            for gnum, paths in self.groups.items():
                if not self._running:
                    self.failed.emit("已取消")
                    return
                out_dir_g = self.out_dirs.get(gnum) or self.out_dir
                if out_dir_g not in caches:
                    caches[out_dir_g] = load_asr_cache(out_dir_g)
                asr_cache = caches[out_dir_g]
                cache_dirty = False
                script_tokens, script_raws = tokenize_raw_pairs(
                    self.scripts.get(gnum, ""))

                # ---- 第一步: 组内所有片段先各自转录 + 与整段文案全局匹配 ----
                clips = []
                for path in paths:
                    if not self._running:
                        self.failed.emit("已取消")
                        return
                    name = os.path.basename(path)
                    key = clip_fingerprint(path, self.language)
                    hit = asr_cache.get(key)

                    if isinstance(hit, dict) and hit.get("words") is not None:
                        # 文件一个字节都没动 → 上次的词表原样拿来用。
                        # 连 ffprobe 都省了, 而且结果必然和上次完全一致。
                        words = [(str(t), float(a), float(b))
                                 for t, a, b in hit["words"]]
                        dur = float(hit.get("dur") or 0.0)
                        fps = float(hit.get("fps") or 30.0)
                        if dur < media_guard.MIN_DURATION:
                            raise RuntimeError(
                                f"转录缓存里 {name} 的时长是 {dur:.3f}s —— "
                                f"这条缓存是上一版静默跳过坏文件时写下的脏数据。"
                                f"请删除 _stitched/{ASR_CACHE_NAME} 后重跑。")
                        n_reuse += 1
                        env = env_from_b64(hit.get("env") or "")
                        if env is None and self.edge_refine:
                            # 老缓存里没有包络 —— 只把音频解一遍补上, 不重转录
                            try:
                                env = compute_envelope(load_audio_16k(path))
                                hit["env"] = env_to_b64(env)
                                asr_cache[key] = hit
                                cache_dirty = True
                            except Exception:
                                env = None
                        incomplete = bool(hit.get("incomplete"))
                        timing_tier = int(hit.get("timing_tier") or timing_tier_of(words))
                        if transcript_incomplete(words, dur, env) or timing_tier >= TIMING_TIER_EVEN:
                            self.log.emit(
                                f"  [{gnum}] ↻ {name} 缓存转写疑似截断或均摊时间戳，整段重转并对齐")
                            try:
                                words, still, timing_tier = self._transcribe_one(
                                    path, dur, script_tokens, model,
                                    align_model, metadata, client)
                                hit["words"] = [[w[0], round(float(w[1]), 3),
                                                 round(float(w[2]), 3)]
                                                for w in words]
                                hit["incomplete"] = 1 if still else 0
                                hit["timing_tier"] = int(timing_tier)
                                incomplete = bool(still)
                                asr_cache[key] = hit
                                cache_dirty = True
                            except Exception as e:
                                from cutora.asr.clip_worker import AsrCircuitOpen
                                if isinstance(e, AsrCircuitOpen):
                                    self._stop_batch_circuit(e)
                                    return
                                self.log.emit(
                                    f"    ⚠ 补转失败({e})，沿用缓存")
                        self.log.emit(f"  [{gnum}] ♻ {name} 未改动, "
                                      f"复用上次转录 ({dur:.1f}s, {len(words)}词)")
                    else:
                        incomplete = False
                        timing_tier = TIMING_TIER_WORD_TS
                        info = probe_media(path)
                        dur = info["duration"] or 0.0
                        fps = info["fps"] or 30.0
                        # 兜底闸门。probe_media 出任何错都返回 duration=0.0,
                        # 旧代码就这么带着 0 秒往下走, 最后安静地少一条成品。
                        # 拼接前体检已经拦过一道; 能走到这里说明文件在体检之后
                        # 被换掉或损坏了 —— 那也必须炸, 不许再静默跳过。
                        if dur < media_guard.MIN_DURATION:
                            raise RuntimeError(
                                f"素材 {name} 读不出有效时长 ({dur:.3f}s)。"
                                f"文件可能已损坏或在体检后被替换 —— 已中止, "
                                f"请重新开始以触发体检。")
                        self.log.emit(f"  [{gnum}] 转录 {name} ({dur:.1f}s) ...")
                        try:
                            env = None
                            if self.edge_refine:
                                try:
                                    env = compute_envelope(load_audio_16k(path))
                                except Exception:
                                    env = None
                            words, incomplete, timing_tier = self._transcribe_one(
                                path, dur, script_tokens, model,
                                align_model, metadata, client)
                            n_fresh += 1
                            asr_cache[key] = {
                                "words": [[w[0], round(float(w[1]), 3),
                                           round(float(w[2]), 3)] for w in words],
                                "dur": round(dur, 3), "fps": round(fps, 3),
                                "env": env_to_b64(env),
                                "incomplete": 1 if incomplete else 0,
                                "timing_tier": int(timing_tier)}
                            # 逐个落盘: 跑一半被取消/断电, 已转好的也不白费
                            save_asr_cache(out_dir_g, asr_cache)
                            cache_dirty = False
                        except Exception as e:
                            from cutora.asr.clip_worker import AsrCircuitOpen
                            if isinstance(e, AsrCircuitOpen):
                                self._stop_batch_circuit(e)
                                return
                            words = []
                            env = None
                            timing_tier = TIMING_TIER_EVEN
                            self.log.emit(f"    ⚠ 转录失败({e}), 该段整段保留")

                    if int(timing_tier) >= TIMING_TIER_EVEN:
                        self.estimated_timing += 1
                        self.log.emit("    %s" % TIMING_ESTIMATED_MARK)
                    clip = {"path": path, "name": name, "dur": dur,
                            "fps": fps, "words": words, "env": env,
                            "m": match_clip_global(words, script_tokens),
                            "deep": bool(isinstance(hit, dict) and hit.get("deep")),
                            "incomplete": bool(incomplete),
                            "timing_tier": int(timing_tier)}
                    rescue_unmatched(
                        clip, script_tokens, self.language, model=model,
                        asr_cache=asr_cache, out_dir=out_dir_g,
                        log=self.log.emit)
                    clips.append(clip)
                    done += 1
                    self.progress.emit(int(done / total * 100))

                # ---- 第二步: 顺序纠正 ----
                # 命中(含部分命中)的片段按其在文案中的位置重排; 未命中的留在原位不动。
                # 新增：外部钉死顺序。片段是按已知的文案段序生成的场合（长文案流水线），
                # 顺序在生成时就是已知的，不该再靠 ASR 猜 —— 3 分钟文案里重复句太多，
                # 靠 s_start 排序会把第 3 段和第 28 段排到一起。
                folder = os.path.dirname(paths[0]) if paths else ""
                _pinned, clips, _pin_logs, _is_pinned = pin_order_from_json(
                    clips, folder, gnum)
                for _msg in _pin_logs:
                    self.log.emit(_msg)

                # 歧义裁决必须在按 s_start 重排之前, 否则重复句会先被搬到第一次出现处。
                self.ambig_count += resolve_ambiguous_positions(
                    clips, script_tokens)

                if _is_pinned:
                    order = _pinned
                else:
                    hit_slots = [i for i, c in enumerate(clips)
                                 if c["m"]["matched"] or c["m"]["partial"]]
                    by_script = sorted(
                        hit_slots, key=lambda i: clips[i]["m"]["s_start"])
                    order = list(range(len(clips)))
                    for slot, src in zip(hit_slots, by_script):
                        order[slot] = src
                    if order != list(range(len(clips))):
                        self.log.emit(
                            f"  ↻ 组{gnum}: 命名顺序与文案不符, 已按文案顺序纠正: "
                            + " → ".join(clips[i]["name"] for i in order))

                # ---- 第三步: 全局去重规划 (v3) ----
                # 文案每个词只保留一次: 整段重复/无关 → 回收站;
                # 片段内部重复/废话 → 多保留区间, 编码时切掉中间重新拼接。
                # 规划 → 缺失检测 → (报了洞就复核可疑片段) → 最多再算一轮。
                # 第一轮没有洞时, 行为和老版本一字不差: 只跑一次 plan_group_dedup。
                # 顺序 order 不重算 —— 复核只会让某个片段的词表更全,
                # 它在文案里的位置本来就是对的, 重排只会带来无谓的变数。
                items, g_missing = [], []
                g_suspected = []
                script_text = self.scripts.get(gnum, "")
                for _round in (1, 2):
                    resolve_ambiguous_positions(clips, script_tokens)
                    items = plan_group_dedup(
                        clips, order, script_tokens,
                        self.tail_pad, self.trim_head, log=self.log.emit,
                        pinned=_is_pinned, edge_refine=self.edge_refine)
                    g_suspected = []
                    g_missing = find_missing(
                        items, script_tokens, script_raws,
                        ignore_scripture_refs=self.ignore_scripture_refs,
                        script_text=script_text, suspected=g_suspected)
                    if not g_missing or _round == 2:
                        break
                    self.log.emit(
                        f"  ⏳ 组{gnum}: 初判 {len(g_missing)} 处缺失, "
                        f"先复核是不是转录自己丢的 ...")
                    if not recheck_missing(
                            g_missing, clips, items, script_tokens,
                            self.language, model=model, asr_cache=asr_cache,
                            out_dir=out_dir_g, log=self.log.emit):
                        break
                g_missing = tag_incomplete_missing(items, g_missing, clips)
                if self.ignore_scripture_refs:
                    g_off = find_missing(
                        items, script_tokens, script_raws,
                        ignore_scripture_refs=False, script_text=script_text)
                    self.scripture_ignored += count_ignored_scripture_refs(
                        g_off, script_text)
                for mm in g_missing:
                    if mm.get("kind") == "incomplete":
                        self.log.emit(
                            f"  {INCOMPLETE_MARK} 组{gnum} "
                            f"(文案第{mm['s'] + 1}~{mm['e']}词): “{mm['text']}”")
                    else:
                        self.log.emit(
                            f"  ❓ 组{gnum} 缺失 (文案第{mm['s'] + 1}~{mm['e']}词): "
                            f"“{mm['text']}”")
                if g_missing:
                    self.missing_map[gnum] = {
                        "clips": [os.path.basename(p) for p in paths],
                        "missing": g_missing}
                if g_suspected:
                    self.suspected_map[gnum] = {
                        "clips": [os.path.basename(p) for p in paths],
                        "suspected": g_suspected}
                cuts_map[gnum] = items
                if cache_dirty:
                    save_asr_cache(out_dir_g, asr_cache)

            for _d, _c in caches.items():
                save_asr_cache(_d, _c)
            release_recheck_model()     # 复核模型只在这一轮用, 用完还显存
            if n_reuse or n_fresh:
                self.log.emit(
                    f"♻ 转录复用 {n_reuse} 段(文件未改动, 结果与上次完全一致), "
                    f"新转 {n_fresh} 段")

            # ---- 缺失文案清单导出: 拿去重新生成对应片段 ----
            n_incomplete = sum(
                1 for v in self.missing_map.values()
                for mm in v.get("missing") or []
                if mm.get("kind") == "incomplete")
            footer = format_missing_footer(
                self.ambig_count, self.scripture_ignored, n_incomplete,
                self.estimated_timing)
            failed_clips = list(
                getattr(self._timeout_circuit, "failed_paths", []) or [])
            if self.out_dir and (
                    self.missing_map or self.suspected_map or footer or failed_clips):
                try:
                    os.makedirs(self.out_dir, exist_ok=True)
                    mpath = os.path.join(self.out_dir, MISSING_NAME)
                    lines = [
                        "缺失文案清单 — 以下内容现有片段都没讲到, 请重新生成后放回素材文件夹",
                        "阈值：≥3 词记「缺失」；1–2 词记「疑似缺词」（0 缺失 ≠ 一个词都没丢）。",
                        "(新片段按组命名即可, 如 157 (5).mp4, 下次分析会自动识别并拼入正确位置)",
                        f"{INCOMPLETE_MARK} 的条目不是缺失，先听片段再决定要不要重做。",
                        "=" * 60]
                    for g, info in self.missing_map.items():
                        lines.append(f"\n【组 {g}】 现有片段: {', '.join(info['clips'])}")
                        for mi, mm in enumerate(info["missing"], 1):
                            if mm.get("kind") == "incomplete":
                                lines.append(
                                    f"  {INCOMPLETE_MARK} (文案第{mm['s'] + 1}~{mm['e']}词):")
                            else:
                                lines.append(
                                    f"  缺失{mi} (文案第{mm['s'] + 1}~{mm['e']}词):")
                            lines.append(f"    {mm['text']}")
                    if self.suspected_map:
                        lines.append("")
                        lines.append("—— 疑似缺词（1–2 词，请 5 秒内对照上下文判断）——")
                        for g, info in self.suspected_map.items():
                            lines.append(f"\n【组 {g}】")
                            for si, sm in enumerate(info.get("suspected") or [], 1):
                                ctx = " ".join(
                                    x for x in (
                                        sm.get("context_before"),
                                        "【" + (sm.get("text") or "") + "】",
                                        sm.get("context_after"),
                                    ) if x)
                                lines.append(
                                    f"  疑似{si} (文案第{sm['s'] + 1}~{sm['e']}词): {ctx}")
                    if failed_clips:
                        lines.append("")
                        lines.append("—— ASR 超时失败（该片已跳过，未中断整批）——")
                        for p in failed_clips:
                            lines.append("  %s" % os.path.basename(p))
                    if footer:
                        lines.append("")
                        lines.extend(footer)
                    with open(mpath, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                    _remember_missing_report(mpath)
                    n_m = sum(len(v["missing"]) for v in self.missing_map.values())
                    if n_m:
                        self.log.emit(
                            f"❓ 共 {len(self.missing_map)} 组 {n_m} 处缺失文案, "
                            f"清单已导出: {mpath}")
                    else:
                        self.log.emit(f"清单已导出: {mpath}")
                    for _fl in footer:
                        self.log.emit("  " + _fl)
                except Exception as e:
                    self.log.emit(f"⚠ 缺失清单导出失败: {e}")
            elif self.out_dir:
                # 本次没有任何缺失：也写一份「本次无缺失」的清单覆盖旧文件，
                # 避免「打开缺失报告」打开的是上一轮的过期清单。
                try:
                    os.makedirs(self.out_dir, exist_ok=True)
                    mpath = os.path.join(self.out_dir, MISSING_NAME)
                    with open(mpath, "w", encoding="utf-8") as f:
                        f.write(
                            "缺失文案清单 — %s\n%s\n✓ 本次拼接所有文案均已被片段完整覆盖，没有缺失或疑似缺词。\n"
                            % (time.strftime("%Y-%m-%d %H:%M:%S"), "=" * 60))
                    _remember_missing_report(mpath)
                except Exception:
                    pass

            if self.out_dir and cuts_map:
                try:
                    os.makedirs(self.out_dir, exist_ok=True)
                    clp = os.path.join(self.out_dir, "cutlist.json")
                    with open(clp, "w", encoding="utf-8") as f:
                        json.dump(cuts_map, f, ensure_ascii=False, indent=2)
                    self.log.emit(f"  切点表已写入 {clp}")
                except Exception as e:
                    self.log.emit(f"⚠ cutlist.json 写入失败: {e}")

            self.analyzed.emit(cuts_map)
        except StitchLanguageError as e:
            self.log.emit("✘ " + str(e))
            self.failed.emit(str(e))
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))
        finally:
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
            try:
                from cutora.asr.engine import trim_gpu_cache
                trim_gpu_cache("拼接分析结束")
            except Exception:
                pass

# ----------------------------------------------------------------------
# 人工复核表格
# ----------------------------------------------------------------------

class ReviewDialog(QDialog):
    """切点复核: 可改 保留区间/拼接顺序, 可取消勾选「保留」弃用片段。
    红=低置信, 橙=部分命中, 紫=整段重复(将移入回收站), 灰=与文案无关(将移入回收站)。"""

    COLS = ["组", "序", "片段", "保留", "原时长", "保留区间(s)", "命中率",
            "说明 / 剪掉的内容"]
    C_SEQ, C_KEEP, C_SEGS = 1, 3, 5

    @staticmethod
    def _segs_to_text(it):
        segs = it.get("segs") or [[it["in_t"], it["out_t"]]]
        return "; ".join(f"{a:.2f}-{b:.2f}" for a, b in segs)

    @staticmethod
    def _parse_segs(txt, dur):
        """'0.00-3.10; 5.20-8.00' → [[a,b],...]; 非法返回 None。"""
        segs = []
        for part in re.split(r"[;；]+", (txt or "").strip()):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"^(\d+(?:[.,]\d+)?)\s*[-~→]+\s*(\d+(?:[.,]\d+)?)$", part)
            if not m:
                return None
            a = float(m.group(1).replace(",", "."))
            b = float(m.group(2).replace(",", "."))
            if not (0 <= a < b <= dur + 0.05):
                return None
            segs.append([round(a, 3), round(min(b, dur), 3)])
        if not segs:
            return None
        segs.sort()
        for i in range(1, len(segs)):
            if segs[i][0] < segs[i - 1][1]:
                return None
        return segs

    def __init__(self, cuts_map, parent=None):
        super().__init__(parent)
        self.cuts_map = cuts_map
        self.setWindowTitle("人工复核 — 可修改 保留区间/拼接顺序/是否保留")
        self.setMinimumSize(1080, 560)
        try:
            from cutora.batch.script_ui import apply_plugin_style
            apply_plugin_style(self)
        except Exception:
            pass
        v = QVBoxLayout(self)
        tip = QLabel("双击「序 / 保留区间」修改。保留区间格式: 0.00-3.10; 5.20-8.00 "
                     "(多段=中间部分已被剪掉, 编码时自动重新拼接)。\n"
                     "红=低置信(整段保留); 橙=部分命中; 紫=整段重复; 灰=与文案无关 — "
                     "紫/灰 已自动取消保留, 编码成功后原文件将移入回收站; "
                     "重新勾选「保留」即可拯救该片段。改完点「确认并编码」。")
        tip.setObjectName("hint")
        v.addWidget(tip)

        rows = sum(len(items) for items in cuts_map.values())
        self.table = QTableWidget(rows, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(
            len(self.COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self._row_ref = []      # row → (gnum, item)
        r = 0
        for gnum, items in cuts_map.items():
            for it in items:
                desc = it["note"]
                for cdesc in it.get("cuts") or []:
                    desc += "   " + cdesc
                if it.get("tail"):
                    desc += "   裁尾: " + it["tail"]
                vals = [gnum, str(it.get("seq", 0)), it["name"], "",
                        f"{it['dur']:.2f}", self._segs_to_text(it),
                        f"{it['ratio'] * 100:.0f}%", desc]
                level = it.get("level", "ok" if it["matched"] else "low")
                color = {"low": "#ff6666", "partial": "#ffaa44",
                         "dup": "#b388ff", "junk": "#9e9e9e"}.get(level)
                for c, val in enumerate(vals):
                    cell = QTableWidgetItem(val)
                    if c == self.C_KEEP:
                        cell.setFlags((cell.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                                      & ~Qt.ItemFlag.ItemIsEditable)
                        cell.setCheckState(Qt.CheckState.Checked if it.get("keep", True)
                                           else Qt.CheckState.Unchecked)
                    elif c not in (self.C_SEQ, self.C_SEGS):
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if color:
                        cell.setForeground(QColor(color))
                    self.table.setItem(r, c, cell)
                self._row_ref.append((gnum, it))
                r += 1
        self.table.resizeColumnsToContents()
        v.addWidget(self.table, 1)

        h = QHBoxLayout()
        h.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        h.addWidget(btn_cancel)
        btn_ok = QPushButton("确认并编码")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._confirm)
        h.addWidget(btn_ok)
        v.addLayout(h)

    def _confirm(self):
        for r, (gnum, it) in enumerate(self._row_ref):
            try:
                seq = int(float(self.table.item(r, self.C_SEQ).text().replace(",", ".")))
            except Exception:
                QMessageBox.warning(self, "数值错误", f"第 {r + 1} 行的「序」不是有效数字")
                return
            segs = self._parse_segs(self.table.item(r, self.C_SEGS).text(), it["dur"])
            if segs is None:
                QMessageBox.warning(
                    self, "区间错误",
                    f"第 {r + 1} 行「保留区间」格式无效。\n"
                    f"格式: 0.00-3.10; 5.20-8.00  "
                    f"(需满足 0 ≤ 起 < 止 ≤ {it['dur']:.2f}, 多段不重叠)")
                return
            it["seq"] = seq
            it["keep"] = (self.table.item(r, self.C_KEEP).checkState()
                          == Qt.CheckState.Checked)
            if it["keep"]:
                it["recycle"] = False   # 用户拯救的片段绝不移入回收站
            it["segs"] = segs
            it["in_t"] = segs[0][0]
            it["out_t"] = segs[-1][1]
        # 按用户调整后的「序」重排每组的拼接顺序 (稳定排序, 同序号保持原相对顺序)
        for gnum, items in self.cuts_map.items():
            items.sort(key=lambda x: x.get("seq", 0))
        # 每组至少保留一段
        empty = [g for g, items in self.cuts_map.items()
                 if not any(x.get("keep", True) for x in items)]
        if empty:
            QMessageBox.warning(self, "提示",
                                f"以下组没有保留任何片段, 将被跳过: {', '.join(empty)}")
        self.accept()

# ----------------------------------------------------------------------
# Google Sheet 剪贴板解析 与 可粘贴表格
# ----------------------------------------------------------------------

def parse_sheet_clipboard(text):
    """
    解析从 Google Sheet 复制整列后的剪贴板内容 → [每格文案, ...]。
    Sheet 规则: 行之间用换行分隔; 含换行/引号的格子整体用双引号包裹, 内部引号写成 ""。
    csv 模块 (tab 分隔) 原生支持该格式, 含换行的格子会被正确合并为一条。
    """
    cells = []
    try:
        for row in csv.reader(io.StringIO(text or ""), delimiter="\t"):
            if not row:
                cells.append("")
                continue
            # 若误复制了多列, 取最长的那一列 (文案列必然最长)
            cell = row[0] if len(row) == 1 else max(row, key=lambda c: len(c or ""))
            cells.append((cell or "").strip())
    except Exception:
        cells = [ln.strip() for ln in (text or "").splitlines()]
    while cells and not cells[-1]:
        cells.pop()
    return cells

class PasteTable(QTableWidget):
    """支持 Ctrl+V 直接粘贴 Google Sheet 多格内容的表格。"""

    def __init__(self, on_paste, *args):
        super().__init__(*args)
        self._on_paste = on_paste

    def keyPressEvent(self, ev):
        if ev.matches(QKeySequence.StandardKey.Paste):
            if callable(self._on_paste):
                self._on_paste()
            return
        super().keyPressEvent(ev)

# ----------------------------------------------------------------------
# 主对话框
# ----------------------------------------------------------------------

class PreflightWorker(QThread):
    """素材体检跑在后台线程。

    1000 个片段要逐个 ffprobe + 扫 box 表, 放主线程会把界面冻住一分多钟,
    看起来像卡死 —— 然后人就会去点叉, 体检也就白做了。
    """
    log = Signal(str)
    done = Signal(object)          # media_guard.Report

    def __init__(self, folder, groups, quarantine_bad, parent=None, folder_of=None):
        super().__init__(parent)
        self.folder = folder            # 主文件夹 (报告落在这儿)
        self.groups = groups
        self.quarantine_bad = quarantine_bad
        self.folder_of = dict(folder_of or {})   # {组号: 素材所在文件夹}

    def run(self):
        try:
            # 多文件夹: 每个文件夹各体检各的 (坏文件要隔离进它自己的 _invalid,
            # 断号也只在同一个文件夹里比才有意义), 最后把问题合成一份报告。
            by_folder = {}
            for k, v in (self.groups or {}).items():
                by_folder.setdefault(self.folder_of.get(k) or self.folder, {})[k] = v
            if not by_folder:
                by_folder = {self.folder: (self.groups or {})}
            rep = media_guard.Report(self.folder)
            for _folder, _gs in by_folder.items():
                # Tik 组名不能 int(); 断号检查只对纯数字组有意义。
                check_gaps = all(str(k).isdigit() for k in (_gs or {}))
                one = media_guard.preflight(
                    _folder, groups=_gs,
                    quarantine_bad=self.quarantine_bad,
                    check_gaps=check_gaps,
                    log=self.log.emit)
                rep.issues += one.issues
                rep.n_clips += one.n_clips
                rep.n_groups += one.n_groups
                rep.quarantined += list(one.quarantined or [])
                rep.elapsed += one.elapsed
        except Exception as e:
            traceback.print_exc()
            # 体检本身崩了也绝不放行: 造一个致命报告回去。
            rep = media_guard.Report(self.folder)
            rep.add(media_guard.Issue(
                media_guard.FATAL, "E_GUARD_CRASH", self.folder,
                "素材体检自身出错: %s" % e,
                "在体检结果未知的情况下不允许开始拼接"))
        self.done.emit(rep)

class StitcherDialog(QDialog):
    def __init__(self, parent, get_model, get_align, on_done):
        super().__init__(parent)
        self.get_model = get_model
        self.get_align = get_align
        self.on_done = on_done
        self.worker = None
        self.enc_worker = None
        self.pf_worker = None
        self._scripts = {}
        self._out_dir = ""
        self._out_dirs = {}      # {组号: 该组成品/缓存所在的 _stitched}
        self._out_names = {}     # {组号: 成品文件名(不含扩展名)}
        self._folder_of = {}     # {组号: 素材文件夹}
        self._base_of = {}
        self._groups = {}
        self._group_keys = []
        self._grouping_result = None
        self._session = None
        self._pending_groups = None
        self._pending_scripts = None
        self._auto_handoff_mode = False
        self._layout_lock = "auto"  # "auto" | "h" | "v"
        self.setWindowTitle("片段智能拼接")
        self.setProperty("cutora_sheet", "fill")
        self.setMinimumSize(320, 220)
        self.setAcceptDrops(True)
        self._build_ui()
        try:
            from cutora.batch.script_ui import apply_plugin_style
            self.btn_start.setObjectName("btn_primary")
            apply_plugin_style(self)
        except Exception:
            pass
        self._attach_session()
        if (
            os.environ.get("CUTORA_START_ROOM") == "stitch"
            or os.environ.get("CUTORA_SYNC_ORANTE") == "1"
            or (parent is not None and not self._folders())
        ):
            self._sync_from_orante(quiet=True)
        if self._folders() and not self._groups:
            self._rescan()

    def _build_ui(self):
        from PySide6.QtGui import QAction, QShortcut
        from PySide6.QtWidgets import QFrame, QMenu, QToolButton, QWidget
        from cutora.ui.i18n import tr as _tr
        from cutora.ui.silence_cut_dialog import HoverThinSplitter

        v = QVBoxLayout(self)
        v.setSpacing(6)

        # ── 顶部工具栏：标题 + 排版切换 + Orante 同步 + ⓘ 使用指南 ──
        top_bar = QHBoxLayout()
        lbl_title = QLabel("🔗 片段智能拼接")
        lbl_title.setObjectName("sectionHeader")
        top_bar.addWidget(lbl_title)

        self.btn_info = QToolButton(self)
        self.btn_info.setText("ⓘ 说明")
        self.btn_info.setCursor(Qt.CursorShape.PointingHandCursor)
        info_tip = (
            "【智能分组说明】\n"
            "• 自动识别（推荐）：添加或粘贴文件夹瞬间自动解析文件名形状与数字/字母序号\n"
            "  （支持 01a/01b、01 (1)、Tik001_v01、V01-简单的小天使 (1)-1 等任意规律）。\n"
            "• 按文件夹分组：每个素材文件夹直接算作一组，内部按文件名自然排序。\n\n"
            "【自由分界线与排版】\n"
            "• 分界线平时自动隐藏，鼠标移到两栏之间即显现极细微光线，可自由无限制拖拽任意比例。\n"
            "• 点击「⇄ 自动排版」可在横屏左右双栏 / 竖屏上下分栏之间自由切换或锁定。\n\n"
            "【原文案粘贴说明】\n"
            "• 与 Google Sheet 一一对应：一格 = 一个视频。\n"
            "• 在 Sheet 中复制整列文案后，点击右侧表格按 Ctrl+V 或点「从剪贴板批量粘贴」即可。"
        )
        self.btn_info.setToolTip(info_tip)
        self.btn_info.clicked.connect(lambda: QMessageBox.information(self, "片段智能拼接说明", info_tip))
        top_bar.addWidget(self.btn_info)

        self.btn_layout_mode = QPushButton("⇄ 自动排版")
        self.btn_layout_mode.setToolTip("点击切换：自动随窗口横竖屏切换 / 强制左右双栏 / 强制上下分栏（分界线悬停即显，可自由拖动）")
        self.btn_layout_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_layout_mode.clicked.connect(self._cycle_layout_mode)
        top_bar.addWidget(self.btn_layout_mode)

        top_bar.addStretch(1)
        self.ed_orante_range = QLineEdit()
        self.ed_orante_range.setPlaceholderText("任务范围(留空=全部，如 T0137-T0162 或 v01-v26)")
        self.ed_orante_range.setFixedWidth(230)
        top_bar.addWidget(self.ed_orante_range)
        b_orante = QPushButton("⚡ 载入 Orante 批次与文案")
        b_orante.setToolTip("直接读取 Orante 当前活跃批次（或左侧指定范围）的全部已收片文件夹与文案，自动写入顺序锁并填好文案表")
        b_orante.clicked.connect(lambda: self._sync_from_orante(quiet=False, auto_start_override=False))
        top_bar.addWidget(b_orante)
        b_miss = QPushButton("打开缺失报告")
        b_miss.clicked.connect(self._open_missing_report)
        top_bar.addWidget(b_miss)
        v.addLayout(top_bar)

        # ── 外层纵向悬停极细分割器（上方为主工作区，下方为操作与日志区，均可无限制自由拖拽）──
        self.outer_splitter = HoverThinSplitter(Qt.Orientation.Vertical, self)

        # ── 主工作区响应式双栏 HoverThinSplitter（平时隐藏分界线，悬停显现细线自由拖拽）──
        self.main_splitter = HoverThinSplitter(Qt.Orientation.Horizontal, self.outer_splitter)

        # === 左/上栏：素材文件夹与智能分组卡片 ===
        self.pane_folders = QFrame(self.main_splitter)
        self.pane_folders.setObjectName("stitchCardPane")
        self.pane_folders.setMinimumSize(40, 40)
        pf_lay = QVBoxLayout(self.pane_folders)
        pf_lay.setContentsMargins(6, 6, 6, 6)
        pf_lay.setSpacing(6)

        row_f = QHBoxLayout()
        row_f.addWidget(QLabel("📂 素材目录 (支持拖入或粘贴路径):"))
        row_f.addStretch(1)
        b_add = QPushButton("添加文件夹...")
        b_add.clicked.connect(self._add_folder)
        row_f.addWidget(b_add)
        b_tree = QPushButton("添加总目录...")
        b_tree.setToolTip("选一个总目录, 自动把它下面所有装着视频的子文件夹都加进来")
        b_tree.clicked.connect(self._add_tree)
        row_f.addWidget(b_tree)
        b_paste_dir = QPushButton("📋 粘贴路径")
        b_paste_dir.setToolTip("直接从剪贴板粘贴一个或多个文件夹路径，立即自动识别分组")
        b_paste_dir.clicked.connect(self._paste_folders)
        row_f.addWidget(b_paste_dir)
        b_del = QPushButton("移除")
        b_del.clicked.connect(self._remove_folder)
        row_f.addWidget(b_del)
        b_clr = QPushButton("清空")
        b_clr.clicked.connect(self._clear_folders)
        row_f.addWidget(b_clr)
        pf_lay.addLayout(row_f)

        self.lst_folders = QListWidget()
        self.lst_folders.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lst_folders.setMinimumHeight(36)
        self.lst_folders.setToolTip("可将文件夹或视频直接拖入此处，或选中后按 Delete 移除")
        pf_lay.addWidget(self.lst_folders, 1)

        # ── 分组依据（合并原分组前缀与序号前缀为智能自动识别 + 文件夹分组）──
        rown = QHBoxLayout()
        rown.addWidget(QLabel("分组依据:"))
        self.cmb_group_mode = QComboBox()
        self.cmb_group_mode.addItem("✨ 自动识别前缀与序号 (推荐)", "auto")
        self.cmb_group_mode.addItem("📁 用文件夹来分组 (一夹一组)", "folder")
        rown.addWidget(self.cmb_group_mode, 1)

        btn_apply = QPushButton("🔄 重新扫描")
        btn_apply.clicked.connect(self._apply_naming)
        rown.addWidget(btn_apply)
        pf_lay.addLayout(rown)

        # 保留原有隐藏控件以兼容外部脚本与旧配置读写
        self.ed_group_mark = QLineEdit(self)
        self.ed_group_mark.setPlaceholderText("留空=自动识别；例: Tik00x 或 Tik")
        self.ed_group_mark.hide()
        self.ed_order_mark = QLineEdit(self)
        self.ed_order_mark.setPlaceholderText("留空=自动识别；例: v0x 或 v")
        self.ed_order_mark.hide()
        self.chk_group_by_folder = QCheckBox("以文件夹为一组（忽略文件名）", self)
        self.chk_group_by_folder.hide()

        _gm, _om = load_naming()
        self.ed_group_mark.setText(_gm)
        self.ed_order_mark.setText(_om)
        _by_folder = load_group_by_folder()
        self.chk_group_by_folder.setChecked(_by_folder)
        self.cmb_group_mode.setCurrentIndex(1 if _by_folder else 0)
        for _p in load_last_folders():
            self.lst_folders.addItem(_p)
        self.ed_group_mark.editingFinished.connect(self._apply_naming)
        self.ed_order_mark.editingFinished.connect(self._apply_naming)
        self.chk_group_by_folder.toggled.connect(self._on_group_by_folder)
        self.cmb_group_mode.currentIndexChanged.connect(self._on_group_mode_combo)

        self.lbl_scan = QLabel("（添加或拖入文件夹后立即自动识别分组）")
        self.lbl_scan.setObjectName("okHint")
        self.lbl_scan.setWordWrap(True)
        pf_lay.addWidget(self.lbl_scan)

        # === 右/下栏：原文案与智能认领预览表卡片 ===
        self.pane_scripts = QFrame(self.main_splitter)
        self.pane_scripts.setObjectName("stitchCardPane")
        self.pane_scripts.setMinimumSize(40, 40)
        ps_lay = QVBoxLayout(self.pane_scripts)
        ps_lay.setContentsMargins(6, 6, 6, 6)
        ps_lay.setSpacing(6)

        rowp = QHBoxLayout()
        rowp.addWidget(QLabel("📝 原文案与分组匹配表 (一格一视频):"))
        self.ed_filter_group = QLineEdit()
        self.ed_filter_group.setPlaceholderText("🔎 筛选组号或文案…")
        self.ed_filter_group.setMaximumWidth(170)
        self.ed_filter_group.textChanged.connect(self._filter_script_rows)
        rowp.addWidget(self.ed_filter_group)
        rowp.addStretch(1)
        btn_paste = QPushButton("📋 从剪贴板批量粘贴")
        btn_paste.setToolTip("在 Google Sheet 中复制文案列后点此粘贴，或在表格内按 Ctrl+V")
        btn_paste.clicked.connect(self._paste_clipboard)
        rowp.addWidget(btn_paste)
        self.btn_precheck = QPushButton("🔍 拼接前自检")
        self.btn_precheck.setToolTip("检查各组片段顺序、缺号情况与文案匹配度")
        self.btn_precheck.clicked.connect(self._run_precheck_dialog)
        rowp.addWidget(self.btn_precheck)
        btn_clear = QPushButton("清空文案")
        btn_clear.clicked.connect(self._clear_scripts)
        rowp.addWidget(btn_clear)
        self.lbl_fill = QLabel("已填 0 / 0")
        self.lbl_fill.setObjectName("okHint")
        rowp.addWidget(self.lbl_fill)
        ps_lay.addLayout(rowp)

        self.tbl_script = PasteTable(self._paste_clipboard, 0, 3)
        self.tbl_script.setMinimumHeight(36)
        self.tbl_script.setHorizontalHeaderLabels(["组", "文案 (双击可单独编辑)", "智能分组与片段序"])
        self.tbl_script.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tbl_script.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl_script.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.tbl_script.setColumnWidth(0, 120)
        self.tbl_script.setColumnWidth(2, 220)
        ps_lay.addWidget(self.tbl_script, 1)

        self.main_splitter.addWidget(self.pane_folders)
        self.main_splitter.addWidget(self.pane_scripts)
        self.main_splitter.setStretchFactor(0, 2)
        self.main_splitter.setStretchFactor(1, 3)
        self.main_splitter.setSizes([460, 740])

        # ── 下半部：控制栏 + 进度条 + 可自由拉伸的实时日志框 ──
        self.pane_bottom = QFrame(self.outer_splitter)
        self.pane_bottom.setMinimumSize(40, 44)
        bot_lay = QVBoxLayout(self.pane_bottom)
        bot_lay.setContentsMargins(0, 4, 0, 0)
        bot_lay.setSpacing(6)

        opt = QHBoxLayout()
        opt.addWidget(QLabel("识别语言:"))
        self.cmb_lang = QComboBox()
        self.cmb_lang.setToolTip("默认自动识别全语种（含中文、马尔加什语、斯瓦西里语、阿拉伯语、英语等）")
        lang_items = [
            ("auto", "auto · 自动识别 (全语种)"),
            ("zh", "zh · 中文"),
            ("mg", "mg · 马尔加什语 (Malagasy)"),
            ("sw", "sw · 斯瓦西里语 (Swahili)"),
            ("ar", "ar · 阿拉伯语 (العربية)"),
            ("en", "en · 英语 (English)"),
            ("it", "it · 意大利语"),
            ("es", "es · 西班牙语"),
            ("fr", "fr · 法语"),
            ("pt", "pt · 葡萄牙语"),
            ("de", "de · 德语"),
        ]
        for code, label in lang_items:
            self.cmb_lang.addItem(label, code)
        self.cmb_lang.setCurrentIndex(0)  # 默认必须是 auto 自动识别
        opt.addWidget(self.cmb_lang)

        opt.addSpacing(10)
        opt.addWidget(QLabel("尾部余量(s):"))
        self.spin_pad = QDoubleSpinBox()
        self.spin_pad.setRange(0.0, 2.0)
        self.spin_pad.setSingleStep(0.05)
        self.spin_pad.setValue(0.35)
        opt.addWidget(self.spin_pad)

        # 保留原有 QCheckBox 属性并与「⚙ 高级设置 ▾」下拉菜单双向同步
        self.chk_head = QCheckBox("裁剪开头空白", self)
        self.chk_head.setChecked(True)
        self.chk_head.hide()
        self.chk_edge = QCheckBox("切口落在静音处", self)
        self.chk_edge.setChecked(EDGE_REFINE_DEFAULT)
        self.chk_edge.hide()
        self.chk_fade = QCheckBox("接缝淡化30ms", self)
        self.chk_fade.setChecked(True)
        self.chk_fade.hide()
        self.chk_review = QCheckBox("编码前总是人工复核 (不弹确认询问)", self)
        self.chk_review.setChecked(False)
        self.chk_review.hide()
        self.chk_auto_ai = QCheckBox("完成后自动一键全自动", self)
        self.chk_auto_ai.setChecked(True)
        self.chk_auto_ai.hide()
        self.chk_recycle = QCheckBox("编码成功后将多余片段移入回收站", self)
        self.chk_recycle.setChecked(True)
        self.chk_recycle.hide()
        self.chk_ignore_refs = QCheckBox(_tr("忽略经文出处"), self)
        self.chk_ignore_refs.setChecked(True)
        self.chk_ignore_refs.hide()

        self.btn_adv = QToolButton(self)
        self.btn_adv.setText("⚙ 高级拼接设置 ▾")
        self.btn_adv.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        adv_menu = QMenu(self.btn_adv)
        for chk, tip in (
            (self.chk_head, "自动裁掉首个词之前的空白气口"),
            (self.chk_edge, "词尾后若有碎音，自动在静音谷底切断"),
            (self.chk_fade, "每段接缝处做 30ms 音频淡化，消除底噪突变"),
            (self.chk_ignore_refs, "括号里的经文出处（如 (John 8:34-35)）配音不念时不列入缺失"),
            (self.chk_auto_ai, "生成时间线后自动启动全自动字幕与排版"),
            (self.chk_recycle, "拼接完成后将完全重复或废片移入回收站"),
            (self.chk_review, "生成时间线前总是打开人工复核表微调切点"),
        ):
            act = QAction(chk.text(), adv_menu, checkable=True)
            act.setChecked(chk.isChecked())
            act.setToolTip(tip)
            act.toggled.connect(chk.setChecked)
            chk.toggled.connect(act.setChecked)
            adv_menu.addAction(act)
        adv_menu.addSeparator()
        act_custom_prefix = QAction("自定义前缀规则覆盖…", adv_menu)
        act_custom_prefix.triggered.connect(self._open_custom_prefix_dialog)
        adv_menu.addAction(act_custom_prefix)
        self.btn_adv.setMenu(adv_menu)
        opt.addSpacing(8)
        opt.addWidget(self.btn_adv)

        opt.addStretch(1)
        self.btn_start = QPushButton("🚀 开始智能拼接")
        self.btn_start.setMinimumHeight(36)
        self.btn_start.setMinimumWidth(180)
        self.btn_start.clicked.connect(self._start)
        opt.addWidget(self.btn_start)
        bot_lay.addLayout(opt)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setMaximumHeight(14)
        bot_lay.addWidget(self.bar)

        self.ed_log = QPlainTextEdit()
        self.ed_log.setReadOnly(True)
        self.ed_log.setMinimumHeight(24)
        bot_lay.addWidget(self.ed_log, 1)

        self.outer_splitter.addWidget(self.main_splitter)
        self.outer_splitter.addWidget(self.pane_bottom)
        self.outer_splitter.setStretchFactor(0, 5)
        self.outer_splitter.setStretchFactor(1, 1)
        self.outer_splitter.setSizes([580, 140])
        v.addWidget(self.outer_splitter, 1)

        self._update_responsive_layout()

    @staticmethod
    def _read_orante_copy_for_folder(folder_path: str) -> str:
        """从 <task_dir>/clips/_order.json 或 <task_dir>/manifest.json 自动读取 Orante 原文案。"""
        if not folder_path:
            return ""
        try:
            op = os.path.join(folder_path, "_order.json")
            if os.path.isfile(op):
                d = load_json_safe(op, {}) or {}
                ct = (d.get("copy_text") or "").strip()
                if ct:
                    return ct
            mp = os.path.join(os.path.dirname(os.path.normpath(folder_path)), "manifest.json")
            if os.path.isfile(mp):
                md = load_json_safe(mp, {}) or {}
                cobj = md.get("copy") or {}
                ct = (cobj.get("text") or cobj.get("raw") or "").strip()
                if ct:
                    return ct
        except Exception:
            pass
        return ""

    def _sync_from_orante(self, quiet: bool = False, auto_start_override: bool | None = None) -> int:
        """一键读取 Orante 当前批次（或指定范围/交接单）所有已收片文件夹与文案，自动写入 _order.json 并填入表格。"""
        try:
            import sys as _sys
            from pathlib import Path as _Path
            from PySide6.QtCore import QTimer as _QTimer
            _ltx_root = str(_Path(__file__).resolve().parents[4])
            if _ltx_root not in _sys.path:
                _sys.path.insert(0, _ltx_root)
            from core.table_sync import HANDOFF_FILE, discover_orante_batch_tasks, write_order_lock_for_cutora

            range_txt = ""
            if hasattr(self, "ed_orante_range"):
                range_txt = self.ed_orante_range.text().strip()

            auto_start = False
            if auto_start_override is not None:
                auto_start = bool(auto_start_override)
            elif os.environ.get("CUTORA_START_ROOM") == "stitch" or os.environ.get("CUTORA_SYNC_ORANTE") == "1":
                auto_start = True

            clip_dirs = []
            # 1. 优先检查是否有来自 Orante / 主控台刚投递的交接单 (`cutora_stitch_handoff.json`)
            if HANDOFF_FILE.is_file() and not range_txt:
                try:
                    hdata = json.loads(HANDOFF_FILE.read_text(encoding="utf-8"))
                    if isinstance(hdata, dict) and not hdata.get("consumed"):
                        hdata["consumed"] = True
                        HANDOFF_FILE.write_text(json.dumps(hdata, ensure_ascii=False, indent=2), encoding="utf-8")
                        if auto_start_override is None:
                            auto_start = bool(hdata.get("auto_start", True))
                        if hasattr(self, "chk_auto_ai") and "auto_ai" in hdata:
                            self.chk_auto_ai.setChecked(bool(hdata.get("auto_ai", True)))
                        if hasattr(self, "ed_orante_range") and hdata.get("range_text"):
                            self.ed_orante_range.setText(str(hdata.get("range_text") or ""))
                            range_txt = str(hdata.get("range_text") or "")
                        for cdir in (hdata.get("clip_dirs") or []):
                            if cdir and os.path.isdir(cdir):
                                has_mp4 = any(
                                    os.path.splitext(fn)[1].lower() in VIDEO_EXTS
                                    and os.path.isfile(os.path.join(cdir, fn))
                                    for fn in os.listdir(cdir)
                                )
                                if has_mp4:
                                    clip_dirs.append(os.path.normpath(cdir))
                except Exception:
                    pass

            # 2. 若无未消费交接单，则直接从 Orante 数据库与任务目录扫描（支持范围过滤）
            if not clip_dirs:
                recs = discover_orante_batch_tasks(range_text=range_txt)
                for r in recs:
                    if r.task_dir and _Path(r.task_dir).is_dir():
                        write_order_lock_for_cutora(_Path(r.task_dir))
                    if r.clips_dir and os.path.isdir(r.clips_dir):
                        has_mp4 = any(
                            os.path.splitext(fn)[1].lower() in VIDEO_EXTS
                            and os.path.isfile(os.path.join(r.clips_dir, fn))
                            for fn in os.listdir(r.clips_dir)
                        )
                        if has_mp4:
                            clip_dirs.append(os.path.normpath(r.clips_dir))

            if clip_dirs:
                self._auto_handoff_mode = bool(auto_start)
                self.lst_folders.clear()
                for p in clip_dirs:
                    self.lst_folders.addItem(p)
                save_last_folders(clip_dirs)
                self._rescan()
                scope_note = f"（范围: {range_txt}）" if range_txt else "（当前批次全部任务）"
                self._log(f"⚡ 已从 Orante 自动载入 {len(clip_dirs)} 个任务文件夹{scope_note}，并自动填好全部文案与分镜顺序锁！")
                if auto_start and not self._busy():
                    self._log("🚀 已触发自动智能拼接闭环：正在自动启动体检与语音对齐分析...")
                    _QTimer.singleShot(450, self._auto_start_if_idle)
                return len(clip_dirs)
            if not quiet:
                self._log("⚠ 未在 Orante 当前批次中找到已收片的 clips 文件夹")
        except Exception as e:
            if not quiet:
                self._log(f"⚠ 从 Orante 同步批次失败: {e}")
        return 0

    def _auto_start_if_idle(self):
        if not self._busy():
            self._start()

    def _attach_session(self):
        """Dialog UI stays here. The workers live on the shared session."""
        from cutora.batch.stitch_session import StitchSession
        self._session = StitchSession(
            self.get_model, self.get_align, parent=self, stop_on_missing=False)
        self._session.log.connect(self._log)
        self._session.progress.connect(self.bar.setValue)
        self._session.preflight_ready.connect(self._on_preflight_done)
        self._session.analyzed.connect(self._on_analyzed)
        self._session.failed.connect(self._on_failed)

    def _arm_job(self, scripts, folders=None):
        """Groups that have a script, and the _stitched paths _start assigns."""
        from cutora.batch.stitch_session import assign_outputs
        if folders is None:
            folders = [f for f in self._folders() if os.path.isdir(f)]
        groups = {k: v for k, v in self._groups.items() if k in (scripts or {})}
        out_dirs, out_names, out_dir = assign_outputs(
            groups, self._folder_of, self._base_of, folders)
        return groups, out_dirs, out_names, out_dir

    # ---------- 交互 ----------
    def _cycle_layout_mode(self):
        order = ("auto", "h", "v")
        cur = getattr(self, "_layout_lock", "auto")
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "auto"
        self._layout_lock = nxt
        labels = {
            "auto": "⇄ 自动排版",
            "h": "◫ 左右双栏",
            "v": "⊟ 上下分栏",
        }
        if hasattr(self, "btn_layout_mode"):
            self.btn_layout_mode.setText(labels[nxt])
        self._update_responsive_layout()

    def _filter_script_rows(self, text: str = ""):
        q = (text or "").strip().lower()
        for r in range(self.tbl_script.rowCount()):
            if not q:
                self.tbl_script.setRowHidden(r, False)
                continue
            hit = False
            for col in range(self.tbl_script.columnCount()):
                it = self.tbl_script.item(r, col)
                if it is not None and q in (it.text() or "").lower():
                    hit = True
                    break
            self.tbl_script.setRowHidden(r, not hit)

    def _update_responsive_layout(self):
        sp = getattr(self, "main_splitter", None)
        if sp is None:
            return
        lock = getattr(self, "_layout_lock", "auto")
        if lock == "h":
            want = Qt.Orientation.Horizontal
        elif lock == "v":
            want = Qt.Orientation.Vertical
        else:
            w = max(1, self.width())
            h = max(1, self.height())
            want = Qt.Orientation.Vertical if (w < 920 or w < h) else Qt.Orientation.Horizontal
        if sp.orientation() != want:
            sp.setOrientation(want)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_layout()

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime is not None and (mime.hasUrls() or mime.hasText()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        paths = []
        if mime is not None and mime.hasUrls():
            for u in mime.urls():
                lp = u.toLocalFile()
                if lp:
                    paths.append(lp)
        elif mime is not None and mime.hasText():
            for line in (mime.text() or "").splitlines():
                s = line.strip().strip('"').strip("'")
                if s and os.path.exists(s):
                    paths.append(s)
        if paths:
            self._add_paths(paths)
            event.acceptProposedAction()

    def _paste_folders(self):
        raw = QApplication.clipboard().text() or ""
        paths = []
        for line in raw.splitlines():
            s = line.strip().strip('"').strip("'")
            if s and os.path.exists(s):
                paths.append(s)
        if not paths:
            QMessageBox.information(self, "提示", "剪贴板中没有找到有效的文件夹或视频路径。")
            return
        n = self._add_paths(paths)
        self._log(f"📋 从剪贴板识别并加入 {n} 个素材目录")

    def _folders(self):
        return [self.lst_folders.item(i).text()
                for i in range(self.lst_folders.count())]

    def _add_paths(self, paths):
        cur = self._folders()
        added = 0
        for raw_p in paths:
            p = os.path.normpath(str(raw_p or "").strip().strip('"'))
            if os.path.isfile(p) and os.path.splitext(p)[1].lower() in VIDEO_EXTS:
                p = os.path.dirname(p)
            if not os.path.isdir(p):
                continue
            # 若拖入/粘贴的是总目录（下一层没有直接的视频文件），自动往下找出所有含视频的子文件夹
            has_direct_video = False
            try:
                has_direct_video = any(
                    os.path.splitext(fn)[1].lower() in VIDEO_EXTS
                    and os.path.isfile(os.path.join(p, fn))
                    for fn in os.listdir(p)
                )
            except OSError:
                pass
            targets = [p] if has_direct_video else (find_material_dirs(p, max_depth=5) or [p])
            for tp in targets:
                tp = os.path.normpath(tp)
                if os.path.isdir(tp) and tp not in cur:
                    cur.append(tp)
                    added += 1
        if added:
            self.lst_folders.clear()
            for p in cur:
                self.lst_folders.addItem(p)
            save_last_folders(cur)
            self._rescan()
        return added

    def _add_folder(self):
        from cutora.batch.explorer_windows import list_open_explorer_folders
        open_dirs = list_open_explorer_folders()
        if open_dirs:
            picked = self._pick_open_folders(open_dirs)
            if picked is None:
                return
            if picked:
                if not self._add_paths(picked):
                    self._log("这些文件夹已经在列表里了")
                return
        d = QFileDialog.getExistingDirectory(self, "选择素材文件夹")
        if d and not self._add_paths([d]):
            self._log("这个文件夹已经在列表里了")

    def _pick_open_folders(self, paths):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QDialogButtonBox, QAbstractItemView, QLabel
        from PySide6.QtCore import Qt
        dlg = QDialog(self)
        dlg.setWindowTitle("已打开的资源管理器")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("勾选要添加的文件夹（完整路径）"))
        lst = QListWidget()
        lst.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for p in paths:
            lst.addItem(p)
        lay.addWidget(lst)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return [it.text() for it in lst.selectedItems()]

    def _add_tree(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择总目录 (自动找出里面所有装着视频的文件夹)")
        if not d:
            return
        found = find_material_dirs(d)
        if not found:
            QMessageBox.warning(self, "提示", "这个目录下面没找到装着视频的文件夹")
            return
        n = self._add_paths(found)
        self._log(f"在 {d} 下找到 {len(found)} 个素材文件夹, 新加入 {n} 个")

    def _remove_folder(self):
        for it in self.lst_folders.selectedItems():
            self.lst_folders.takeItem(self.lst_folders.row(it))
        save_last_folders(self._folders())
        self._rescan()

    def _clear_folders(self):
        self.lst_folders.clear()
        save_last_folders([])
        self._rescan()

    def _open_missing_report(self):
        sess = getattr(self, "_session", None)
        sess_dir = getattr(sess, "out_dir", "") if sess is not None else ""
        sess_dirs = getattr(sess, "out_dirs", None) if sess is not None else None
        dirs = missing_report_dirs(
            getattr(self, "_out_dir", "") or sess_dir or "",
            dict(getattr(self, "_out_dirs", None) or {}, **(sess_dirs or {})),
            self._folders() if hasattr(self, "_folders") else [])
        path = find_missing_report(dirs)
        last = last_missing_report()
        if last and (not path or os.path.getmtime(last) >= os.path.getmtime(path)):
            path = last
        if path:
            if not reveal_in_explorer(path):
                QMessageBox.information(self, "打不开", path)
            else:
                self._log(f"📂 已在资源管理器中定位缺失报告：{path}")
            return
        home = ""
        for d in dirs:
            if os.path.isdir(d):
                home = d
                break
        if home:
            QMessageBox.information(
                self, "没有缺失报告",
                "%s\n里没有「%s」。\n\n"
                "要么这趟没有缺失文案(那是好事)，要么还没跑过拼接。\n"
                "先给你打开这个目录。" % (home, MISSING_NAME))
            reveal_in_explorer(home)
            return
        QMessageBox.information(
            self, "没有缺失报告",
            "还没跑过拼接，所以还没有「%s」。\n"
            "它会写在第一个素材文件夹的 _stitched\\ 里。" % MISSING_NAME)

    def _by_folder(self):
        cmb = getattr(self, "cmb_group_mode", None)
        if cmb is not None:
            return cmb.currentData() == "folder"
        chk = getattr(self, "chk_group_by_folder", None)
        return bool(chk is not None and chk.isChecked())

    def _on_group_mode_combo(self, _idx=0):
        by_f = self._by_folder()
        chk = getattr(self, "chk_group_by_folder", None)
        if chk is not None and chk.isChecked() != by_f:
            chk.blockSignals(True)
            chk.setChecked(by_f)
            chk.blockSignals(False)
        save_group_by_folder(by_f)
        self._rescan()

    def _on_group_by_folder(self, checked):
        """勾上就不用文件名了: 同步下拉框并马上重扫。"""
        self.ed_group_mark.setEnabled(not checked)
        self.ed_order_mark.setEnabled(not checked)
        cmb = getattr(self, "cmb_group_mode", None)
        if cmb is not None:
            want_idx = 1 if checked else 0
            if cmb.currentIndex() != want_idx:
                cmb.blockSignals(True)
                cmb.setCurrentIndex(want_idx)
                cmb.blockSignals(False)
        save_group_by_folder(checked)
        self._rescan()

    def _open_custom_prefix_dialog(self):
        from PySide6.QtWidgets import QDialogButtonBox, QFormLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("自定义前缀规则覆盖 (可选)")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("默认留空即可自动识别所有规律。仅当特殊命名需要强制指定前缀时填写："))
        form = QFormLayout()
        ed_g = QLineEdit(self.ed_group_mark.text())
        ed_g.setPlaceholderText("留空=自动识别；例: Tik00x 或 Tik")
        ed_o = QLineEdit(self.ed_order_mark.text())
        ed_o.setPlaceholderText("留空=自动识别；例: v0x 或 v")
        form.addRow("分组前缀:", ed_g)
        form.addRow("序号前缀:", ed_o)
        lay.addLayout(form)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.ed_group_mark.setText(ed_g.text().strip())
            self.ed_order_mark.setText(ed_o.text().strip())
            self._apply_naming()

    def _run_precheck_dialog(self):
        from cutora.batch.stitch_grouping import GROUP_AUTO, GROUP_FOLDER, precheck, scan_folders
        folders = self._folders()
        if not folders:
            QMessageBox.information(self, "拼接前自检", "请先添加素材文件夹。")
            return
        mode = GROUP_FOLDER if self._by_folder() else GROUP_AUTO
        gm = self.ed_group_mark.text().strip() if hasattr(self, "ed_group_mark") else ""
        om = self.ed_order_mark.text().strip() if hasattr(self, "ed_order_mark") else ""
        res = self._grouping_result or scan_folders(folders, mode=mode, group_mark=gm, order_mark=om)
        scripts, _empty = self._collect_scripts()
        rep = precheck(res, scripts=scripts)
        lines = [
            f"共扫描 {len(res.groups)} 组 / {res.n_clips} 个视频片段 · 平均分组置信度 {res.mean_confidence * 100:.0f}%",
            "",
        ]
        if rep["ok"]:
            lines.append("✓ 所有分组与文案均已完整对应，无缺号或低置信问题，可直接开始拼接！")
        else:
            lines.append(f"⚠ 发现 {len(rep['problems'])} 项需要注意：")
            for p in rep["problems"][:15]:
                lines.append(f"  • {p}")
        lines.append("\n【各组详情前 12 组】")
        for g in res.groups[:12]:
            w_str = f"  ⚠ {'; '.join(g.warnings)}" if g.warnings else ""
            lines.append(
                f"  • {g.key}: {len(g.clips)} 段 ({' → '.join(c.name for c in g.clips[:4])}{'…' if len(g.clips) > 4 else ''}) [{g.basis}]{w_str}"
            )
        QMessageBox.information(self, "拼接前分组与文案自检报告", "\n".join(lines))

    def _apply_naming(self):
        """改了前缀就存下来并立刻重扫。"""
        gm = self.ed_group_mark.text().strip()
        om = self.ed_order_mark.text().strip()
        save_naming(gm, om)
        self._rescan()

    def _rescan(self, folder=None):
        from cutora.batch.stitch_grouping import GROUP_AUTO, GROUP_FOLDER, scan_folders
        gm = self.ed_group_mark.text().strip() if hasattr(self, "ed_group_mark") else ""
        om = self.ed_order_mark.text().strip() if hasattr(self, "ed_order_mark") else ""
        folders = self._folders()
        by_f = self._by_folder()
        mode = GROUP_FOLDER if by_f else GROUP_AUTO
        res = scan_folders(folders, mode=mode, group_mark=gm, order_mark=om)
        self._grouping_result = res
        self._groups, self._folder_of, self._base_of, notes = scan_groups_multi(
            folders, gm, om, group_by_folder=by_f)
        for _n in notes:
            self._log("⚠ " + _n)
        self._group_keys = list(self._groups.keys())
        n_clip = sum(len(v) for v in self._groups.values())
        if not self._groups:
            names = []
            for _f in folders[:2]:
                try:
                    names += [n for n in sorted(os.listdir(_f))
                              if os.path.splitext(n)[1].lower() in VIDEO_EXTS][:3]
                except OSError:
                    pass
            if not folders:
                tip = "还没有添加素材文件夹 —— 点上方「添加文件夹...」或直接把文件夹拖进来。"
            elif not names:
                tip = "这些目录里没有视频文件。"
            else:
                tip = ("未能分组。目录里的文件: " + " / ".join(names[:3])
                       + " → 可切换为「用文件夹来分组」")
            self.lbl_scan.setText("扫描到 0 组 / 0 个片段 —— " + tip)
            self.lbl_scan.setObjectName("warnHint")
            self.tbl_script.setRowCount(0)
            self._update_fill_label()
            return
        self.lbl_scan.setObjectName("okHint")
        conf_pct = int(round(res.mean_confidence * 100))
        low_note = f" · ⚠ {len(res.low_items)} 段建议确认" if res.low_items else f" · ✓ 识别置信度 {conf_pct}%"
        self.lbl_scan.setText(
            f"✓ {len(folders)} 个目录 → 智能识别 {len(self._groups)} 组 / {n_clip} 个片段{low_note}: "
            + ", ".join(f"{k}({len(v)})" for k, v in list(self._groups.items())[:10])
            + (" ..." if len(self._groups) > 10 else ""))
        # 重建文案表格 (保留已填内容)
        old = {}
        for r in range(self.tbl_script.rowCount()):
            k = self.tbl_script.item(r, 0)
            t = self.tbl_script.item(r, 1)
            if k and t and t.text().strip():
                old[k.text()] = t.text()
        self.tbl_script.setRowCount(len(self._group_keys))
        for r, gnum in enumerate(self._group_keys):
            g_info = res.group(gnum)
            it0 = QTableWidgetItem(gnum)
            it0.setFlags(it0.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if g_info is not None:
                it0.setToolTip(f"分组: {gnum}\n目录: {g_info.folder}\n依据: {g_info.basis}")
            self.tbl_script.setItem(r, 0, it0)
            txt_val = old.get(gnum, "")
            if not txt_val:
                txt_val = self._read_orante_copy_for_folder(self._folder_of.get(gnum, ""))
            it1 = QTableWidgetItem(txt_val)
            it1.setToolTip(txt_val)
            self.tbl_script.setItem(r, 1, it1)
            if self.tbl_script.columnCount() >= 3 and g_info is not None:
                c_names = [c.name for c in g_info.clips]
                preview_seq = " → ".join(c_names[:3]) + (" …" if len(c_names) > 3 else "")
                badge = "⚠" if g_info.low else "✓"
                it2 = QTableWidgetItem(f"{badge} {len(c_names)}段 · {preview_seq}")
                it2.setFlags(it2.flags() & ~Qt.ItemFlag.ItemIsEditable)
                tip_lines = [
                    f"分组依据: {g_info.basis}",
                    f"置信度: {g_info.confidence * 100:.0f}%",
                    "组内排序:",
                ] + [f"  {idx + 1}. {nm}" for idx, nm in enumerate(c_names)]
                if g_info.warnings:
                    tip_lines.append("提示: " + "；".join(g_info.warnings))
                it2.setToolTip("\n".join(tip_lines))
                if g_info.low:
                    it2.setForeground(QColor("#E5A93C"))
                else:
                    it2.setForeground(QColor("#56D364"))
                self.tbl_script.setItem(r, 2, it2)
        self._update_fill_label()

    def _paste_clipboard(self):
        if not self._group_keys:
            QMessageBox.warning(self, "提示", "请先选择素材文件夹 (需要先知道有哪些组)")
            return
        text = QApplication.clipboard().text()
        cells = parse_sheet_clipboard(text)
        if not cells:
            QMessageBox.warning(self, "提示", "剪贴板是空的。请先在 Google Sheet 中复制文案列")
            return
        # 从当前选中行开始粘贴 (未选中则从第一行开始)
        start = self.tbl_script.currentRow()
        if start < 0:
            start = 0
        filled = 0
        for i, cell in enumerate(cells):
            r = start + i
            if r >= self.tbl_script.rowCount():
                break
            item = self.tbl_script.item(r, 1)
            if item is None:
                item = QTableWidgetItem("")
                self.tbl_script.setItem(r, 1, item)
            item.setText(cell)
            item.setToolTip(cell)
            filled += 1
        extra = len(cells) - filled
        msg = f"已粘贴 {filled} 条 (从组 {self._group_keys[start]} 开始)"
        if extra > 0:
            msg += f", 超出表格的 {extra} 条已忽略"
        self._log(msg)
        self._update_fill_label()

    def _clear_scripts(self):
        for r in range(self.tbl_script.rowCount()):
            item = self.tbl_script.item(r, 1)
            if item:
                item.setText("")
                item.setToolTip("")
        self._update_fill_label()

    def _update_fill_label(self):
        n = self.tbl_script.rowCount()
        filled = sum(1 for r in range(n)
                     if self.tbl_script.item(r, 1) and self.tbl_script.item(r, 1).text().strip())
        self.lbl_fill.setText(f"已填 {filled} / {n}")

    def _collect_scripts(self):
        """从表格收集 {组号: 文案}; 返回 (scripts, 空缺组列表)。"""
        scripts, empty = {}, []
        for r, gnum in enumerate(self._group_keys):
            item = self.tbl_script.item(r, 1)
            txt = (item.text() if item else "").strip()
            if txt:
                scripts[gnum] = txt
            else:
                empty.append(gnum)
        return scripts, empty

    def _log(self, s):
        self.ed_log.appendPlainText(s)
        try:
            from pathlib import Path as _Path
            _log_p = _Path(__file__).resolve().parents[4] / "data" / "cutora_stitch_live.log"
            _log_p.parent.mkdir(parents=True, exist_ok=True)
            with open(_log_p, "a", encoding="utf-8") as _lf:
                _lf.write(f"[{time.strftime('%H:%M:%S')}] {s}\n")
        except Exception:
            pass

    def _busy(self):
        sess = getattr(self, "_session", None)
        if sess is not None and sess.busy():
            return True
        return bool(
            (getattr(self, "worker", None) and self.worker.isRunning())
            or (getattr(self, "enc_worker", None) and self.enc_worker.isRunning())
            or (getattr(self, "pf_worker", None) and self.pf_worker.isRunning())
        )

    def _start(self):
        if self._busy():
            if self._session is not None:
                self._session.stop()
            if self.worker and self.worker.isRunning():
                self.worker.stop()
            if self.enc_worker and self.enc_worker.isRunning():
                self.enc_worker.stop()
            self.btn_start.setText("正在停止...")
            return
        from cutora.ui.gpu_notice import confirm as _gpu_ok
        if not _gpu_ok(self, "stitch"):            # Q193：没有 N 卡先说清楚
            return
        folders = [f for f in self._folders() if os.path.isdir(f)]
        gone = [f for f in self._folders() if not os.path.isdir(f)]
        if gone:
            self._log("⚠ 这些文件夹不在了, 已跳过: " + " / ".join(gone))
        if not folders:
            QMessageBox.warning(self, "提示", "请先添加素材文件夹")
            return
        if not self._group_keys:
            self._rescan()
        if not self._groups:
            if self._by_folder():
                QMessageBox.warning(self, "提示", "这些文件夹里没有可以直接拼接的视频")
            else:
                QMessageBox.warning(self, "提示", "文件夹中没有符合 01a/01b 或 01 (1)/01 (2) 命名的视频")
            return
        scripts, empty = self._collect_scripts()
        if not scripts:
            QMessageBox.warning(self, "提示", "文案表格是空的。请从 Google Sheet 复制文案列后粘贴")
            return
        if empty:
            reply = QMessageBox.question(
                self, "有组缺少文案",
                f"以下 {len(empty)} 组没有文案, 将被跳过不处理:\n"
                f"{', '.join(empty[:20])}{' ...' if len(empty) > 20 else ''}\n\n继续吗?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._scripts = scripts
        # 成品/缓存各回各家: 每组写进它自己素材文件夹下的 _stitched。
        # 只有汇总性的东西 (切点表/报告/缺失清单) 放在第一个文件夹里。
        groups, self._out_dirs, self._out_names, self._out_dir = self._arm_job(
            scripts, folders)
        self._pending_groups = groups
        self._pending_scripts = scripts
        self._session.set_job(
            groups, scripts, self._folder_of,
            self._out_dirs, self._out_names, self._out_dir, folders)
        save_last_folders(folders)

        # ---- 强制体检闸门 ----------------------------------------------
        # 在这里挡住, 而不是等分析阶段"跳过坏片段"。
        # 2026-08-23: 两个 JPG 被当成 mp4 收进来, 分析阶段静默跳过,
        # 最后 _stitched/ 里少了 53 和 91 两条成品, 界面上一个字都没提。
        self.btn_start.setEnabled(False)
        self.btn_start.setText("体检中...")
        self.bar.setRange(0, 0)                      # 不确定进度的滚动条
        self._log("拼接前体检: 逐个验证 %d 个文件夹 / %d 组 / %d 个素材的文件头与可读性 ..."
                  % (len(folders), len(groups),
                     sum(len(v) for v in groups.values())))
        self.pf_worker = self._session.start_preflight(
            folders[0], groups, self._folder_of, quarantine_bad=True)

    def _on_preflight_done(self, rep):
        """体检结果裁决。致命 → 硬停; 仅需确认 → 明确弹窗, 默认不继续。"""
        from cutora.batch.stitch_session import preflight_continue
        groups = self._pending_groups
        scripts = self._pending_scripts
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.btn_start.setEnabled(True)
        self.btn_start.setText("开始拼接")
        self.pf_worker = None
        self._log(rep.text())
        if not preflight_continue(self, rep, self._log):
            return
        self._launch_analyze(groups, scripts)

    def _selected_language(self) -> str:
        if hasattr(self, "cmb_lang") and self.cmb_lang is not None:
            d = self.cmb_lang.currentData()
            if d:
                return str(d).strip()
            txt = (self.cmb_lang.currentText() or "").strip()
            if txt:
                return txt.split()[0].strip()
        return "auto"

    def _launch_analyze(self, groups, scripts):
        self._session.groups = groups
        self._session.scripts = scripts
        self._session.out_dir = self._out_dir
        self._session.out_dirs = dict(self._out_dirs)
        self.btn_start.setText("停止")
        self._log(f"阶段1/2 分析 {len(groups)} 组素材 ...")
        self.worker = self._session.launch_analyze({
            "language": self._selected_language(),
            "tail_pad": self.spin_pad.value(),
            "trim_head": self.chk_head.isChecked(),
            "edge_refine": self.chk_edge.isChecked(),
            "ignore_scripture_refs": self.chk_ignore_refs.isChecked(),
        })

    def _on_analyzed(self, cuts_map):
        # 切点表存档 (复跑/追溯用)
        try:
            os.makedirs(self._out_dir, exist_ok=True)
            with open(os.path.join(self._out_dir, "cutlist.json"), "w", encoding="utf-8") as f:
                json.dump(cuts_map, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # ---- 编码前置检测汇总: 让用户心里有数, 再决定是否人工介入 ----
        n_all = sum(len(items) for items in cuts_map.values())
        n_low = sum(1 for items in cuts_map.values()
                    for it in items if it.get("level") == "low")
        n_partial = sum(1 for items in cuts_map.values()
                        for it in items if it.get("level") == "partial")
        n_recycle = sum(1 for items in cuts_map.values()
                        for it in items if it.get("recycle"))
        n_midcut = sum(max(0, len(it.get("segs") or []) - 1)
                       for items in cuts_map.values() for it in items)
        n_reorder = sum(1 for items in cuts_map.values()
                        if any("顺序纠正" in it.get("note", "") for it in items))
        missing_map = getattr(self.worker, "missing_map", None) or {}
        n_miss = sum(len(v["missing"]) for v in missing_map.values())
        miss_line = (f"  ❓ 缺失文案: {len(missing_map)} 组共 {n_miss} 处 "
                     f"(清单已导出: 缺失文案清单.txt, 请重新生成后补录)\n"
                     if missing_map else "  ✔ 文案覆盖完整, 无缺失\n")
        summary = (f"分析完成, 共 {n_all} 段:\n"
                   f"  ✔ 高置信: {n_all - n_low - n_partial - n_recycle} 段\n"
                   f"  ◐ 部分命中 (已自动剔除无关内容): {n_partial} 段\n"
                   f"  ⚠ 低置信 (整段保留, 建议复核): {n_low} 段\n"
                   f"  🗑 整段多余 (重复/与文案无关, 编码后移入回收站): {n_recycle} 段\n"
                   f"  ✂ 片段内部剪切 (重复朗读/废话): {n_midcut} 刀\n"
                   + miss_line +
                   f"  ↻ 顺序已按文案自动纠正: {n_reorder} 组")
        self._log(summary)

        need_review = self.chk_review.isChecked()
        is_auto_handoff = bool(getattr(self, "_auto_handoff_mode", False))
        if not need_review:
            if not missing_map and is_auto_handoff:
                self._log("🚀 全部片段文案覆盖完整、零缺失！正在自动为每个任务创建时间线并启动全自动加字幕...")
            else:
                extra_hint = (
                    "\n\n⚡ 缺词片段已自动回传 Orante 并精准标记（mark=True）！\n"
                    "建议点「取消」后直接回到 Orante「⑤ 收片 -> 全部任务 -> 已标记」一键批量重生补全；\n"
                    "或点「生成时间线」先用现有片段创建时间线。"
                    if missing_map
                    else "\n\n是否在生成时间线前人工复核/调整切点与顺序?"
                )
                box = QMessageBox(self)
                box.setWindowTitle("分析结果确认")
                box.setText(summary + extra_hint)
                b_rev = box.addButton("人工复核", QMessageBox.ButtonRole.ActionRole)
                b_go = box.addButton("生成时间线", QMessageBox.ButtonRole.AcceptRole)
                b_cancel = box.addButton("回 Orante 补全 / 取消" if missing_map else "取消", QMessageBox.ButtonRole.RejectRole)
                box.setDefaultButton(b_cancel if missing_map else (b_rev if (n_low or n_recycle) else b_go))
                box.exec()
                clicked = box.clickedButton()
                if clicked is b_cancel:
                    self.btn_start.setText("开始拼接")
                    self._log("已暂停生成时间线 —— 请在 Orante「全部任务 -> 已标记」中重生缺片后再来拼接" if missing_map else "已取消 (确认阶段)")
                    return
                need_review = clicked is b_rev

        if need_review:
            dlg = ReviewDialog(cuts_map, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                self.btn_start.setText("开始拼接")
                self._log("已取消 (复核阶段)")
                return
            self._log("复核完成, 使用调整后的切点/顺序")

        self._finish_as_timeline(cuts_map)

    def _project_canvas_wh(self):
        """BUG-38: the canvas the user picked on the welcome page / 画布 menu.

        Read from the host window's ``_timeline_template``. None when the
        host has none (headless) → timeline_stitch falls back to source size.
        """
        host = self.parent()
        tpl = getattr(host, "_timeline_template", None) if host is not None else None
        if not isinstance(tpl, dict):
            return None
        try:
            w, h = int(tpl.get("width") or 0), int(tpl.get("height") or 0)
        except (TypeError, ValueError):
            return None
        return (w, h) if w > 0 and h > 0 else None

    def _finish_as_timeline(self, cuts_map):
        """T20a: cutlist → timeline project. No _stitched mp4."""
        canvas_wh = self._project_canvas_wh()
        try:
            payload, n_places, asproj, canvas_wh = build_timeline_result(
                cuts_map, self._out_dir, self._scripts or {}, canvas_wh)
        except Exception as e:
            self._on_failed("时间线生成失败: %s" % e)
            return
        self.btn_start.setText("开始拼接")
        self.bar.setValue(100)
        self._log("时间线工程 %d 个片段（未生成中间 mp4，画布 %s）: %s" % (
            n_places,
            ("%dx%d" % canvas_wh) if canvas_wh else "按素材尺寸", asproj))
        try:
            if callable(self.on_done):
                self.on_done(payload, self.chk_auto_ai.isChecked())
        except Exception as e:
            QMessageBox.warning(self, "导入工程失败", str(e))
            return
        if not bool(getattr(self, "_auto_handoff_mode", False)):
            QMessageBox.information(
                self, "完成",
                "已生成时间线工程（%d 个独立片段，铺满画布，硬切）。\n"
                "没有写出 _stitched 中间视频。\n%s" % (n_places, asproj))
        self.accept()

    def _on_failed(self, msg):
        self.btn_start.setText("开始拼接")
        self._log(f"✘ {msg}")
        if msg != "已取消":
            QMessageBox.warning(self, "处理失败", msg)


def build_timeline_result(cuts_map, out_dir, scripts, canvas_wh):
    """Cutlist → timeline projects. Shared by the dialog and StitchSession.

    Raises on failure. Returns (payload, placement_count, asproj, canvas_wh).
    """
    from cutora.batch.timeline_stitch import (
        placements_by_group, placements_from_cutlist, project_from_placements,
        projects_from_cutlist, write_placements,
    )
    scripts = scripts or {}
    grouped = placements_by_group(cuts_map)
    if len(grouped) > 1:
        asprojs = projects_from_cutlist(
            cuts_map, out_dir, text_by_group=scripts,
            canvas_wh=canvas_wh)
        placements = [p for plist in grouped.values() for p in plist]
        asproj = asprojs[0] if asprojs else ""
    else:
        placements = placements_from_cutlist(cuts_map)
        write_placements(out_dir, placements)
        text = "\n\n".join(str(scripts.get(g, "") or "") for g in cuts_map)
        asproj = os.path.join(out_dir, "stitched.asproj.json")
        project_from_placements(placements, asproj, text=text,
                                canvas_wh=canvas_wh)
        asprojs = [asproj] if asproj else []
    payload = {
        "kind": "timeline", "asproj": asproj, "asprojs": asprojs,
        "n": len(placements),
    }
    return payload, len(placements), asproj, canvas_wh


def open_stitcher_dialog(parent, get_model, get_align, on_done):
    from cutora.ui.widgets.busy_bar import finish, report, start_busy
    start_busy(parent, "正在打开智能拼接")
    try:
        report(0.4, "建界面")
        dlg = StitcherDialog(parent, get_model, get_align, on_done)
    finally:
        finish()
    dlg.exec()
