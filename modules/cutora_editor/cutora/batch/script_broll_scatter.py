# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""
script_broll_scatter.py — 片段散落（切开短视频，循环铺到工程 Video 轨道）
================================================================
由顶部菜单「脚本 → 片段散落...」调用，也给无头
`--do scatter_broll` 用。

本模块不 import 主窗口（避免循环依赖）。
不改渲染 / 分析 / 导出逻辑。
"""
from __future__ import annotations

import os
import re
import string
import subprocess

from cutora.core.config import FFMPEG_PATH, FFPROBE_PATH
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")
SCATTER_MARK = "_scatter_broll"

def _si():
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

def _natural_key(s):
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", os.path.basename(s or ""))]

def letter_suffix(index):
    """0->a, 25->z, 26->aa。"""
    n = int(index) + 1
    out = []
    while n > 0:
        n, r = divmod(n - 1, 26)
        out.append(string.ascii_lowercase[r])
    return "".join(reversed(out))

def probe_duration(path):
    try:
        res = _run([
            FFPROBE_PATH, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ])
        val = (res.stdout or b"").decode("utf-8", errors="ignore").strip()
        if not val:
            return 0.0
        d = float(val)
        return d if d > 0 else 0.0
    except Exception:
        return 0.0

def list_source_videos(folder):
    """只收目录下一层视频，跳过 _scatter 子目录和已切好的散落片。"""
    if not folder or not os.path.isdir(folder):
        return []
    names = []
    for n in os.listdir(folder):
        p = os.path.join(folder, n)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(n)[1].lower()
        if ext not in VIDEO_EXTS:
            continue
        names.append(n)
    names.sort(key=_natural_key)
    return [os.path.join(folder, n) for n in names]

def is_video_file(path):
    return (bool(path) and os.path.isfile(path)
            and os.path.splitext(path)[1].lower() in VIDEO_EXTS)


def resolve_sources(entries):
    """把「文件夹 / 单个视频」混着的一串条目摊平成真正的视频文件列表。

    Q96：以前只能给一个目录。现在整个文件夹、单独几条视频、或者两者
    混着都行。目录只收下一层（`_scatter` 是子目录，天然不会被收进来），
    重复路径按第一次出现的位置去重。
    """
    out = []
    seen = set()

    def _add(path):
        if not path:
            return
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        out.append(path)

    for raw in (entries or []):
        item = str(raw or "").strip().strip('"')
        if not item:
            continue
        if os.path.isdir(item):
            for v in list_source_videos(item):
                _add(v)
        elif is_video_file(item):
            _add(item)
    return out


def default_out_dir(entries):
    """切片落在哪：给了一个文件夹就用它自己的 `_scatter`，否则用第一条
    素材所在目录的 `_scatter`。"""
    for raw in (entries or []):
        item = str(raw or "").strip().strip('"')
        if not item:
            continue
        if os.path.isdir(item):
            return os.path.join(item, "_scatter")
        if is_video_file(item):
            return os.path.join(os.path.dirname(os.path.abspath(item)),
                                "_scatter")
    return ""


def piece_stems(srcs):
    """每条源视频对应的切片名前缀。不同文件夹里的同名视频会被加上
    所在文件夹名，免得切片互相覆盖。"""
    stems = {}
    used = {}
    for src in srcs:
        base = os.path.splitext(os.path.basename(src))[0]
        used[base] = used.get(base, 0) + 1
    taken = set()
    for src in srcs:
        base = os.path.splitext(os.path.basename(src))[0]
        stem = base
        if used.get(base, 0) > 1:
            parent = os.path.basename(os.path.dirname(os.path.abspath(src)))
            stem = ("%s-%s" % (parent, base)) if parent else base
        cand, n = stem, 2
        while cand in taken:
            cand = "%s-%d" % (stem, n)
            n += 1
        taken.add(cand)
        stems[src] = cand
    return stems


def plan_cuts(duration, clip_sec):
    """[(start, dur), ...] 最后一段不足 clip_sec 也保留。"""
    clip_sec = float(clip_sec)
    duration = float(duration)
    if clip_sec <= 0.05 or duration <= 0.05:
        return []
    out = []
    t = 0.0
    while t < duration - 0.04:
        left = duration - t
        dur = clip_sec if left >= clip_sec - 0.04 else left
        if dur < 0.08:
            break
        out.append((round(t, 3), round(dur, 3)))
        t += clip_sec
    return out

def cut_one(src, start, dur, dest, mute, log=None):
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if os.path.isfile(dest) and os.path.getsize(dest) > 10000:
        got = probe_duration(dest)
        if abs(got - dur) <= 0.20:
            if log:
                log("  − 已有 %s" % os.path.basename(dest))
            return dest
    cmd = [
        FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "%.3f" % start, "-i", src, "-t", "%.3f" % dur,
    ]
    if mute:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        dest,
    ]
    res = _run(cmd)
    if res.returncode != 0 or not os.path.isfile(dest):
        err = (res.stderr or b"").decode("utf-8", errors="ignore")[-240:]
        raise RuntimeError("切片失败 %s: %s" % (os.path.basename(src), err))
    if log:
        log("  ✓ %s  (%.2fs @ %.2fs)" % (os.path.basename(dest), dur, start))
    return dest

def cut_sources(srcs, clip_sec=4.0, mute=True, out_dir=None, log=None):
    """把给定的源视频切成 clip_sec 一段，返回切片列表（按切的顺序）。"""
    srcs = [s for s in (srcs or []) if s]
    if not srcs:
        raise ValueError("没有可用的源视频")
    if not out_dir:
        out_dir = os.path.join(
            os.path.dirname(os.path.abspath(srcs[0])), "_scatter")
    os.makedirs(out_dir, exist_ok=True)
    stems = piece_stems(srcs)
    pieces = []
    if log:
        log("切开 %d 条源视频 → %.2fs 一段，输出 %s" % (
            len(srcs), clip_sec, out_dir))
    for src in srcs:
        dur = probe_duration(src)
        if dur <= 0:
            raise ValueError("读不到时长：%s" % src)
        stem = stems.get(src) or os.path.splitext(os.path.basename(src))[0]
        ext = ".mp4"
        cuts = plan_cuts(dur, clip_sec)
        if not cuts:
            continue
        for i, (st, cd) in enumerate(cuts):
            name = "%s%s%s" % (stem, letter_suffix(i), ext)
            dest = os.path.join(out_dir, name)
            cut_one(src, st, cd, dest, mute=mute, log=log)
            pieces.append({
                "path": dest,
                "duration": probe_duration(dest) or cd,
                "source": src,
                "label": os.path.splitext(name)[0],
            })
    if not pieces:
        raise ValueError("没有切出任何片段")
    if log:
        log("共切出 %d 段" % len(pieces))
    return pieces, out_dir


def cut_folder(clips_dir, clip_sec=4.0, mute=True, out_dir=None, log=None):
    """只给一个目录的老入口（CLI 仍在用）。"""
    srcs = list_source_videos(clips_dir)
    if not srcs:
        raise ValueError("目录里没有视频：%s" % clips_dir)
    return cut_sources(
        srcs, clip_sec=clip_sec, mute=mute,
        out_dir=out_dir or os.path.join(clips_dir, "_scatter"), log=log)


def _src_path(src):
    if isinstance(src, dict):
        return src.get("path") or ""
    return getattr(src, "path", None) or ""

def _src_id(src):
    if isinstance(src, dict):
        return src.get("id")
    return getattr(src, "id", None)

def video_type_tracks(tl):
    tracks = getattr(tl, "tracks", None) or []
    return [t for t in tracks if getattr(t, "type", "") == "video"]

def ensure_video_track(tl, track_no, host=None):
    """track_no 从 1 起：1=Video 1（主视频），2=Video 2。"""
    from cutora.model.timeline_model import Track
    n = max(1, int(track_no))
    vts = video_type_tracks(tl)
    while len(vts) < n:
        tr = Track("video")
        tr.is_main_video = False
        tl.add_track(tr)
        vts = video_type_tracks(tl)
    tr = vts[n - 1]
    if n == 1 and getattr(tr, "is_main_video", False):
        return tr
    return tr

def task_duration(task, host=None):
    try:
        d = float(task.get("duration") or task.get("trim_end_sec") or 0)
        if d > 0:
            return d
    except Exception:
        pass
    tl = _timeline_of(task, host)
    if tl is not None:
        for tr in video_type_tracks(tl):
            if getattr(tr, "is_main_video", False):
                for c in getattr(tr, "clips", []) or []:
                    try:
                        return float(getattr(c, "out_t", 0) or 0) or float(
                            getattr(c, "effective_duration", 0) or 0)
                    except Exception:
                        pass
    video = task.get("video") or task.get("path") or ""
    if host is not None and hasattr(host, "probe_duration_sec") and video:
        try:
            p = host.probe_duration_sec(video)
            if p:
                return float(p)
        except Exception:
            pass
    if video:
        return probe_duration(video)
    return 0.0

def _timeline_of(task, host=None):
    if host is not None:
        fn = getattr(host, "_tl_of", None)
        if callable(fn):
            return fn(task)
    try:
        from cutora.ui.main_window import _tl_of
        return _tl_of(task)
    except Exception:
        return (task or {}).get("timeline")

def _add_source(tl, path, duration, has_audio):
    from cutora.model.timeline_model import MediaSource
    # 已有同一路径则复用
    for sid, src in list((getattr(tl, "sources", {}) or {}).items()):
        if os.path.normcase(os.path.abspath(_src_path(src) or "")) == \
                os.path.normcase(os.path.abspath(path)):
            return sid if sid else _src_id(src)
    src = MediaSource(path)
    src.has_video = True
    src.has_audio = bool(has_audio)
    if src.duration is None or not src.duration:
        src.duration = float(duration or 0)
    tl.sources[src.id] = src
    return src.id

def _clear_track_clips(track):
    track.clips = []

def _prune_audio_from_paths(tl, path_set, dir_set=None):
    """从音频轨拿掉散落素材的音轨（主音轨 is_main 不动）。"""
    norm = {os.path.normcase(os.path.abspath(p)) for p in (path_set or []) if p}
    roots = []
    for d in dir_set or []:
        if d:
            roots.append(os.path.normcase(os.path.abspath(d)))
    drop_ids = set()
    for sid, src in list((getattr(tl, "sources", {}) or {}).items()):
        p = _src_path(src)
        if not p:
            continue
        ap = os.path.normcase(os.path.abspath(p))
        hit = ap in norm
        if not hit:
            for r in roots:
                if ap == r or ap.startswith(r + os.sep):
                    hit = True
                    break
        if hit:
            drop_ids.add(sid)
            rid = _src_id(src)
            if rid:
                drop_ids.add(rid)
    if not drop_ids:
        return
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", "") != "audio":
            continue
        kept = []
        for c in getattr(tr, "clips", []) or []:
            if getattr(c, "is_main", False):
                kept.append(c)
                continue
            if getattr(c, "source_id", None) in drop_ids:
                continue
            kept.append(c)
        tr.clips = kept

def scatter_into_tasks(
        tasks,
        pieces,
        interval_sec=20.0,
        start_sec=None,
        video_track=2,
        mute=True,
        replace=True,
        host=None,
        log=None,
        clips_dir=None,
        out_dir=None,
):
    """把切片循环铺进每条任务的指定 Video 轨道。返回统计。"""
    from cutora.model.timeline_model import Clip

    if not tasks:
        raise ValueError("工程里没有视频任务")
    if not pieces:
        raise ValueError("没有可撒播的切片")
    interval_sec = float(interval_sec)
    if interval_sec <= 0:
        raise ValueError("间隔必须大于 0")
    if start_sec is None or start_sec == "":
        start_sec = interval_sec
    start_sec = float(start_sec)
    track_no = int(video_track)
    if track_no < 1:
        raise ValueError("轨道编号从 1 起（Video 1 / Video 2）")
    if track_no == 1:
        raise ValueError("Video 1 是主视频轨道，散落请用 Video 2 或更高")

    piece_paths = [p["path"] for p in pieces]
    clip_i = 0
    placed = 0
    per_task = []

    for task in tasks:
        tl = _timeline_of(task, host)
        if tl is None:
            raise ValueError("任务没有时间线：%s" % (
                os.path.basename(task.get("video") or "?")))
        dur = task_duration(task, host)
        if dur <= 0:
            raise ValueError("读不到任务时长：%s" % (
                os.path.basename(task.get("video") or "?")))
        tr = ensure_video_track(tl, track_no, host)
        if getattr(tr, "is_main_video", False):
            raise ValueError("选中的轨道是主视频，拒绝写入")
        if replace:
            _clear_track_clips(tr)
            _prune_audio_from_paths(
                tl, piece_paths, dir_set=[clips_dir, out_dir])

        t = start_sec
        n_here = 0
        while t < dur - 0.08:
            piece = pieces[clip_i % len(pieces)]
            clip_dur = float(piece.get("duration") or 0) or 0.0
            if clip_dur <= 0.05:
                clip_dur = probe_duration(piece["path"])
            remain = dur - t
            use = min(clip_dur, remain)
            if use < 0.08:
                break
            sid = _add_source(tl, piece["path"], use, has_audio=not mute)
            clip = Clip(sid, 0.0, float(use), float(t))
            clip.fit_mode = "fill"
            clip.blend_mode = "normal"
            clip.x = 0.0
            clip.y = 0.0
            clip.scale = 1.0
            clip.rotation = 0.0
            clip.speed = 1.0
            clip.loop = False
            clip.match_duration = False
            clip.is_main = False
            tr.clips.append(clip)
            clip_i += 1
            placed += 1
            n_here += 1
            t += interval_sec
        name = os.path.basename(task.get("video") or "?")
        per_task.append({"video": name, "count": n_here, "duration": dur})
        if log:
            log("  %s  Video%d  铺了 %d 段  (时长 %.1fs)" % (
                name, track_no, n_here, dur))

    return {
        "placed": placed,
        "tasks": len(tasks),
        "pieces": len(pieces),
        "per_task": per_task,
        "clip_cursor": clip_i,
    }

def run_scatter(params, host=None, log=None):
    """CLI / GUI 共用入口。params 见 OPS_SPEC scatter_broll。"""
    def _log(msg=""):
        if log:
            log(msg)

    entries = params.get("clips") or params.get("sources") or []
    if isinstance(entries, str):
        entries = [entries]
    entries = [str(e) for e in entries if str(e or "").strip()]
    clips_dir = params.get("clips_dir") or params.get("dir") or ""
    if clips_dir:
        entries = [clips_dir] + entries
    if not entries:
        raise ValueError("需要素材：给一个片段目录 clips_dir，或一串 clips")
    srcs = resolve_sources(entries)
    if not srcs:
        raise ValueError(
            "这些素材里一个视频都没有（只认 %s）：%s"
            % ("/".join(e.lstrip(".") for e in VIDEO_EXTS),
               "；".join(entries[:4])))
    clip_sec = float(params.get("clip_sec") if params.get("clip_sec") is not None else 4)
    interval_sec = float(params.get("interval_sec") if params.get("interval_sec") is not None else 20)
    start_sec = params.get("start_sec")
    if start_sec is None or start_sec == "":
        start_sec = interval_sec
    else:
        start_sec = float(start_sec)
    mute = params.get("mute")
    if mute is None:
        mute = True
    mute = bool(mute)
    video_track = int(params.get("video_track") if params.get("video_track") is not None else 2)
    replace = params.get("replace")
    if replace is None:
        replace = True
    replace = bool(replace)
    out_dir = params.get("out_dir") or default_out_dir(entries)

    pieces, out_dir = cut_sources(
        srcs, clip_sec=clip_sec, mute=mute, out_dir=out_dir, log=_log)
    return {
        "pieces": pieces,
        "out_dir": out_dir,
        "clip_sec": clip_sec,
        "interval_sec": interval_sec,
        "start_sec": start_sec,
        "mute": mute,
        "video_track": video_track,
        "replace": replace,
        "sources": srcs,
    }

# ---------------------------------------------------------------------------
#  上次用过的素材 / 参数
# ---------------------------------------------------------------------------

def _cfg_path():
    from cutora.core.paths import settings_file
    return settings_file("scatter_broll.json")


def load_cfg():
    import json
    try:
        with open(_cfg_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_cfg(d):
    import json
    try:
        path = _cfg_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_last_sources():
    """上次用过的素材，开窗就摆好；已经不存在的静默丢掉。"""
    out = []
    for p in (load_cfg().get("sources") or []):
        if not isinstance(p, str):
            continue
        if (os.path.isdir(p) or is_video_file(p)) and p not in out:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
#  GUI
# ---------------------------------------------------------------------------

def build_dialog(parent, picker=None):
    """造好散落面板但不 exec —— 测试用这个入口。

    picker=None（test_q96 / 无房间回退）内部自建勾选列表。
    批量加载最后一个 tab 传入房间已有 TaskPicker，不再造第二份。
    「关闭」改成「完成」，不关页面栏房间。
    """
    from PySide6.QtWidgets import (
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QDoubleSpinBox,
        QSpinBox,
        QCheckBox,
        QPlainTextEdit,
        QFileDialog,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QFormLayout,
        QSplitter,
        QWidget,
    )
    from PySide6.QtCore import Qt
    from cutora.batch.script_ui import TaskPicker
    from cutora.ui.theme.tokens import (
        INSPECTOR_PAIR_GAP,
        INSPECTOR_ROW_GAP,
        INSPECTOR_SECTION_GAP,
        SPACE_2,
    )

    cfg = load_cfg()
    own_picker = picker is None
    if own_picker:
        dlg = QDialog(parent)
        dlg.setWindowTitle("片段散落")
        dlg.resize(940, 700)
        root = QVBoxLayout(dlg)
        root.setSpacing(INSPECTOR_SECTION_GAP)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        picker = TaskPicker(list(getattr(parent, "tasks", None) or []), left)
        left_l.addWidget(picker)
        split.addWidget(left)
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(INSPECTOR_ROW_GAP)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 2)
    else:
        dlg = QWidget(parent)
        dlg.setObjectName("batchScatterTab")
        dlg.setWindowFlags(Qt.WindowType.Widget)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(0, SPACE_2, 0, 0)
        root.setSpacing(INSPECTOR_SECTION_GAP)
        right_l = root

    lbl_src_head = QLabel("散落素材（整个文件夹 或 单个视频，都可以，能混着加）")
    lbl_src_head.setObjectName("section")
    right_l.addWidget(lbl_src_head)

    lst_src = QListWidget()
    lst_src.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
    lst_src.setMinimumHeight(120)
    right_l.addWidget(lst_src, 1)

    row_src = QHBoxLayout()
    btn_add_dir = QPushButton("添加文件夹…")
    btn_add_file = QPushButton("添加视频…")
    btn_del_src = QPushButton("移除选中")
    btn_clr_src = QPushButton("清空")
    for b in (btn_del_src, btn_clr_src):
        b.setObjectName("btn_ghost")
    for b in (btn_add_dir, btn_add_file, btn_del_src, btn_clr_src):
        row_src.addWidget(b)
    row_src.addStretch(1)
    right_l.addLayout(row_src)

    lbl_src_sum = QLabel("")
    lbl_src_sum.setObjectName("hint")
    lbl_src_sum.setWordWrap(True)
    right_l.addWidget(lbl_src_sum)

    form_host = QWidget()
    form = QFormLayout(form_host)
    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(INSPECTOR_PAIR_GAP)
    form.setVerticalSpacing(INSPECTOR_ROW_GAP)
    right_l.addWidget(form_host)

    def _entries():
        return [lst_src.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(lst_src.count())]

    def _caption(path):
        if os.path.isdir(path):
            n = len(list_source_videos(path))
            return "📁  %s      (%d 个视频)" % (os.path.basename(
                path.rstrip("\\/")) or path, n)
        return "🎬  %s" % os.path.basename(path)

    def _refresh_src():
        entries = _entries()
        srcs = resolve_sources(entries)
        if not entries:
            lbl_src_sum.setText("还没有素材。点「添加文件夹…」加一整个文件夹，"
                                "或者「添加视频…」单挑几条。")
        elif not srcs:
            lbl_src_sum.setText("⚠ 这些条目里一个视频都没有（只认 %s）。"
                                % "/".join(e.lstrip(".") for e in VIDEO_EXTS))
        else:
            lbl_src_sum.setText("共 %d 个视频，切片会写到：%s"
                                % (len(srcs), default_out_dir(entries)))
        btn_run.setEnabled(bool(srcs))

    def _add_paths(paths):
        have = {os.path.normcase(os.path.abspath(p)) for p in _entries()}
        added = 0
        for p in paths:
            p = str(p or "").strip()
            if not p:
                continue
            key = os.path.normcase(os.path.abspath(p))
            if key in have:
                continue
            have.add(key)
            it = QListWidgetItem(_caption(p))
            it.setData(Qt.ItemDataRole.UserRole, p)
            it.setToolTip(p)
            lst_src.addItem(it)
            added += 1
        _refresh_src()
        return added

    def _add_dir():
        seed = ""
        for p in reversed(_entries()):
            seed = p if os.path.isdir(p) else os.path.dirname(p)
            break
        d = QFileDialog.getExistingDirectory(dlg, "选择片段文件夹", seed)
        if d:
            if not list_source_videos(d):
                QMessageBox.information(
                    dlg, "这个文件夹里没有视频",
                    "%s\n\n下一层里没有找到 %s。\n"
                    "（只看这一层，不会往子文件夹里翻。）"
                    % (d, "/".join(e.lstrip(".") for e in VIDEO_EXTS)))
                return
            _add_paths([d])

    def _add_files():
        seed = ""
        for p in reversed(_entries()):
            seed = p if os.path.isdir(p) else os.path.dirname(p)
            break
        pat = "视频 (%s);;所有文件 (*)" % " ".join("*" + e for e in VIDEO_EXTS)
        files, _ = QFileDialog.getOpenFileNames(dlg, "选择视频", seed, pat)
        if files:
            _add_paths(files)

    def _del_src():
        for it in lst_src.selectedItems():
            lst_src.takeItem(lst_src.row(it))
        _refresh_src()

    def _clr_src():
        lst_src.clear()
        _refresh_src()

    sp_clip = QDoubleSpinBox()
    sp_clip.setRange(0.2, 60.0)
    sp_clip.setDecimals(2)
    sp_clip.setValue(float(cfg.get("clip_sec") or 4.0))
    sp_clip.setSuffix(" 秒")
    form.addRow("裁剪撒播时长", sp_clip)

    sp_gap = QDoubleSpinBox()
    sp_gap.setRange(0.5, 600.0)
    sp_gap.setDecimals(2)
    sp_gap.setValue(float(cfg.get("interval_sec") or 20.0))
    sp_gap.setSuffix(" 秒")
    form.addRow("工程内间隔", sp_gap)

    sp_start = QDoubleSpinBox()
    sp_start.setRange(0.0, 600.0)
    sp_start.setDecimals(2)
    sp_start.setValue(float(cfg.get("start_sec") if cfg.get("start_sec")
                            is not None else 20.0))
    sp_start.setSuffix(" 秒")
    form.addRow("首次落点（开头不放）", sp_start)

    sp_track = QSpinBox()
    sp_track.setRange(2, 8)
    sp_track.setValue(int(cfg.get("video_track") or 2))
    sp_track.setPrefix("Video ")
    sp_track.setToolTip("Video 1 是主视频轨道，散落请用 Video 2 或更高")
    form.addRow("目标轨道", sp_track)

    ck_mute = QCheckBox("撒播片段静音（去掉音频，不进音轨）")
    ck_mute.setChecked(bool(cfg.get("mute", True)))
    form.addRow("", ck_mute)

    ck_replace = QCheckBox("清空目标轨道上已有片段再铺")
    ck_replace.setChecked(bool(cfg.get("replace", True)))
    form.addRow("", ck_replace)

    ck_loop = QCheckBox("按每条长视频时长循环铺满（切片用尽后从头接着用）")
    ck_loop.setChecked(True)
    ck_loop.setEnabled(False)
    form.addRow("", ck_loop)

    log_box = QPlainTextEdit()
    log_box.setReadOnly(True)
    log_box.setPlaceholderText("运行日志…")
    root.addWidget(log_box, 1)
    row_btn = QHBoxLayout()
    row_btn.setSpacing(INSPECTOR_PAIR_GAP)
    btn_run = QPushButton("切开并撒播到所选视频")
    btn_run.setObjectName("btn_primary")
    btn_close = QPushButton("完成")
    row_btn.addStretch(1)
    row_btn.addWidget(btn_run)
    row_btn.addWidget(btn_close)
    root.addLayout(row_btn)

    def _log(msg=""):
        log_box.appendPlainText(str(msg))
        log_box.verticalScrollBar().setValue(
            log_box.verticalScrollBar().maximum())

    def _remember(entries):
        d = load_cfg()
        d["sources"] = [str(p) for p in entries]
        d["clip_sec"] = float(sp_clip.value())
        d["interval_sec"] = float(sp_gap.value())
        d["start_sec"] = float(sp_start.value())
        d["video_track"] = int(sp_track.value())
        d["mute"] = bool(ck_mute.isChecked())
        d["replace"] = bool(ck_replace.isChecked())
        save_cfg(d)

    def _run():
        entries = _entries()
        srcs = resolve_sources(entries)
        if not srcs:
            QMessageBox.warning(
                dlg, "还没有素材",
                "先加素材：「添加文件夹…」整个文件夹都要，"
                "或者「添加视频…」只挑其中几条。")
            return
        tasks = picker.selected_tasks()
        if not tasks:
            QMessageBox.warning(
                dlg, "没有勾选视频",
                "左边至少勾一条要散落进去的长视频。\n"
                "（点整行就能勾 / 取消勾，上面有「全选」。）")
            return
        _remember(entries)
        params = {
            "clips": entries,
            "clip_sec": float(sp_clip.value()),
            "interval_sec": float(sp_gap.value()),
            "start_sec": float(sp_start.value()),
            "mute": bool(ck_mute.isChecked()),
            "video_track": int(sp_track.value()),
            "replace": bool(ck_replace.isChecked()),
        }
        btn_run.setEnabled(False)
        try:
            _log("==== 开始切开 ====")
            prep = run_scatter(params, host=None, log=_log)
            _log("==== 开始撒播 ====")
            stats = scatter_into_tasks(
                tasks,
                prep["pieces"],
                interval_sec=prep["interval_sec"],
                start_sec=prep["start_sec"],
                video_track=prep["video_track"],
                mute=prep["mute"],
                replace=prep["replace"],
                host=parent,
                log=_log,
                clips_dir=os.path.dirname(prep["out_dir"]),
                out_dir=prep["out_dir"],
            )
            if hasattr(parent, "refresh_ui") and getattr(parent, "curr_idx", -1) >= 0:
                parent.refresh_ui()
            if hasattr(parent, "video_canvas"):
                parent.video_canvas.update()
            if hasattr(parent, "timeline"):
                parent.timeline.update()
            _log("完成：铺了 %d 段到 %d 条长视频。" % (
                stats["placed"], stats["tasks"]))
            QMessageBox.information(
                dlg, "散落完成",
                "切出 %d 段，铺进 %d 条长视频，共 %d 个落点。\n切片目录：%s"
                % (len(prep["pieces"]), stats["tasks"], stats["placed"],
                   prep["out_dir"]))
        except Exception as e:
            _log("✘ %s" % e)
            QMessageBox.warning(dlg, "失败", str(e))
        finally:
            btn_run.setEnabled(True)
            _refresh_src()

    btn_add_dir.clicked.connect(_add_dir)
    btn_add_file.clicked.connect(_add_files)
    btn_del_src.clicked.connect(_del_src)
    btn_clr_src.clicked.connect(_clr_src)
    btn_run.clicked.connect(_run)
    if own_picker:
        btn_close.clicked.connect(dlg.reject)

    seeded = load_last_sources()
    if not seeded:
        _legacy = r"D:\0_Dev\下载图片\0-长文案\数据\观众席"
        if os.path.isdir(_legacy):
            seeded = [_legacy]
    if seeded:
        _add_paths(seeded)
    _refresh_src()

    # 给测试用的把手（不影响界面）
    dlg.picker = picker
    dlg.lst_src = lst_src
    dlg.lbl_src_sum = lbl_src_sum
    dlg.btn_run = btn_run
    dlg.btn_close = btn_close
    dlg.add_paths = _add_paths
    dlg.entries = _entries
    dlg.clear_sources = _clr_src
    dlg.run = _run
    return dlg


def open_dialog(parent):
    build_dialog(parent).exec()
