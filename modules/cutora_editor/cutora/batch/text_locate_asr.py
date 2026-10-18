# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q120 · 「只要时间戳」的识别：给批量加文本用来定位，不产生字幕。

和正常的字幕流程完全分开：
- 结果**不写进** task["segments"]，不建字幕轨，不碰任何字幕设置；
- 只存在缓存里（路径 + 修改时间 + 大小 + 时间窗口 做键），
  同一条视频、同一段窗口只跑一次，第二次直接命中；
- **只解码用户圈定的那几秒**（默认开头 15 秒，最长 30 秒）。
  成片有 3、4 分钟，整段送进 WhisperX 会把显卡打满、机器黑屏；
- 一批片子共用一个模型，严格一个接一个。显存不够就停，已经写进缓存的留下。

用途只有一个：把用户粘进来的标题在这条视频开头的时间找出来。
"""
from __future__ import annotations

import json
import os

from cutora.core.log import get_logger

_LOG = get_logger("cutora.batch.text_locate_asr")

_CACHE_NAME = "locate_asr.json"
_CACHE_VERSION = 3          # Q190：每条存 {"w": 词, "s": 带标点的原句}；v2 的纯词列表照样读
_MAX_CACHE = 400
MAX_LOCATE_WINDOW_SEC = 30.0
DEFAULT_LOCATE_END = 15.0
FULL_VIDEO = -1.0           # Q190：end 传这个 = 整条视频（分 30 s 一段串行识别，显存不超）
_CHUNK_OVERLAP_S = 1.0

_mem: dict = {}
_pref_window = [0.0, DEFAULT_LOCATE_END]
_loaded = False
_busy = False
_live_thread = None
_ctx = {"start": 0.0, "end": DEFAULT_LOCATE_END, "language": "auto"}


def _cache_path() -> str:
    from cutora.core.paths import user_cache_dir
    return os.path.join(user_cache_dir(), _CACHE_NAME)


def _fp_key(path: str) -> str | None:
    from cutora.audio.waveform import file_fingerprint, fingerprint_key
    fp = file_fingerprint(path)
    if fp is None:
        return None
    return fingerprint_key(fp)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    dest = _cache_path()
    if not os.path.isfile(dest):
        return
    try:
        data = json.loads(open(dest, encoding="utf-8").read() or "{}")
    except (OSError, ValueError):
        return
    if not isinstance(data, dict) or int(data.get("v") or 0) not in (2, _CACHE_VERSION):
        return
    window = data.get("window")
    if (isinstance(window, (list, tuple)) and len(window) == 2):
        try:
            _pref_window[0] = float(window[0])
            _pref_window[1] = float(window[1])
        except (TypeError, ValueError):
            pass
    items = data.get("items")
    if isinstance(items, dict):
        for k, rec in items.items():
            if not isinstance(k, str):
                continue
            if isinstance(rec, list):                       # v2：只有词
                _mem[k] = {"w": rec, "s": []}
            elif isinstance(rec, dict) and isinstance(rec.get("w"), list):
                _mem[k] = {"w": rec["w"], "s": list(rec.get("s") or [])}


def _flush() -> None:
    dest = _cache_path()
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    items = dict(_mem)
    if len(items) > _MAX_CACHE:
        for k in list(items.keys())[: len(items) - _MAX_CACHE]:
            items.pop(k, None)
            _mem.pop(k, None)
    tmp = dest + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({
                "v": _CACHE_VERSION,
                "window": list(_pref_window),
                "items": items,
            }, fh, ensure_ascii=False)
        os.replace(tmp, dest)
    except OSError:
        _LOG.exception("locate asr cache write failed: %s", dest)
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


def reset_cache() -> None:
    """测试用：丢掉进程内缓存。"""
    global _loaded, _busy
    _mem.clear()
    _pref_window[:] = [0.0, DEFAULT_LOCATE_END]
    _loaded = False
    _busy = False


def is_full(end) -> bool:
    try:
        return float(end) == FULL_VIDEO
    except (TypeError, ValueError):
        return False


def clamp_window(start, end) -> tuple[float, float]:
    """收成一段安全的短窗口。再长也不许超过 30 秒，结束点不许晚于 60 秒。

    ``end == FULL_VIDEO`` 原样返回 (0, FULL_VIDEO)：整条视频由 ``_transcribe_full`` 分段跑。
    """
    if is_full(end):
        return 0.0, FULL_VIDEO
    try:
        start_s = float(0 if start is None else start)
    except (TypeError, ValueError):
        start_s = 0.0
    try:
        end_s = float(DEFAULT_LOCATE_END if end is None else end)
    except (TypeError, ValueError):
        end_s = DEFAULT_LOCATE_END
    start_s = max(0.0, start_s)
    if end_s <= start_s + 0.2:
        end_s = start_s + 0.5
    if end_s - start_s > MAX_LOCATE_WINDOW_SEC:
        end_s = start_s + MAX_LOCATE_WINDOW_SEC
    if end_s > 60.0:
        end_s = 60.0
        if end_s - start_s > MAX_LOCATE_WINDOW_SEC:
            start_s = end_s - MAX_LOCATE_WINDOW_SEC
    return round(start_s, 2), round(end_s, 2)


def _item_key(path: str, start, end, language: str) -> str | None:
    fp = _fp_key(path)
    if fp is None:
        return None
    start_s, end_s = clamp_window(start, end)
    lang = str(language or "auto")
    if is_full(end_s):
        return "%s|full|%s" % (fp, lang)
    return "%s|%.2f-%.2f|%s" % (fp, start_s, end_s, lang)


def _rec(key):
    rec = _mem.get(key)
    return rec if isinstance(rec, dict) else None


def lookup(path: str, start=0.0, end=DEFAULT_LOCATE_END, language: str = "auto"):
    """这段窗口识别出来的词（每词一条 {start, end, text}）。没有就 None。"""
    key = _item_key(path, start, end, language)
    if key is None:
        return None
    _load()
    rec = _rec(key)
    return list(rec["w"]) if rec else None


def lookup_sentences(path: str, start=0.0, end=DEFAULT_LOCATE_END, language: str = "auto"):
    """这段窗口的原句（带标点、大小写，每句 {start, end, text}）。老缓存没有就是 []。"""
    key = _item_key(path, start, end, language)
    if key is None:
        return None
    _load()
    rec = _rec(key)
    return list(rec.get("s") or []) if rec else None


def _keys_of(path: str):
    fp = _fp_key(path)
    if fp is None:
        return []
    _load()
    prefix = fp + "|"
    return [k for k in _mem if k == fp or k.startswith(prefix)]


def _key_span(key: str) -> float:
    mid = key.split("|")[1] if "|" in key else ""
    if mid == "full":
        return float("inf")
    try:
        a, b = mid.split("-")
        return float(b) - float(a)
    except ValueError:
        return 0.0


def lookup_latest(path: str):
    """这条片子上任意一个已保存的窗口（最后存的那个）。改过起止、缓存对不上时还能用。"""
    keys = _keys_of(path)
    if not keys:
        return None
    rec = _rec(keys[-1])
    return list(rec["w"]) if rec else None


def lookup_best(path: str, start=0.0, end=DEFAULT_LOCATE_END, language: str = "auto"):
    """Q190：优先整条视频的结果，其次当前窗口，再其次任意窗口里最长的那个。

    返回 (words, sentences, window_label)；什么都没有返回 (None, None, "")。
    """
    full = _item_key(path, 0.0, FULL_VIDEO, language)
    if full and _rec(full):
        rec = _rec(full)
        return list(rec["w"]), list(rec.get("s") or []), "full"
    cur = _item_key(path, start, end, language)
    if cur and _rec(cur):
        rec = _rec(cur)
        return list(rec["w"]), list(rec.get("s") or []), cur.split("|")[1]
    keys = _keys_of(path)
    if not keys:
        return None, None, ""
    best = max(keys, key=_key_span)
    rec = _rec(best)
    if not rec:
        return None, None, ""
    return list(rec["w"]), list(rec.get("s") or []), best.split("|")[1]


def store(path: str, segments, start=0.0, end=DEFAULT_LOCATE_END,
          language: str = "auto", sentences=None) -> None:
    key = _item_key(path, start, end, language)
    if key is None:
        return
    _load()
    _mem[key] = {"w": [dict(s) for s in (segments or [])],
                 "s": [dict(s) for s in (sentences or [])]}
    _flush()


def saved_window() -> tuple[float, float]:
    _load()
    return clamp_window(_pref_window[0], _pref_window[1])


def remember_window(start, end) -> tuple[float, float]:
    start_s, end_s = clamp_window(start, end)
    _pref_window[0] = start_s
    _pref_window[1] = end_s
    _load()
    _flush()
    return start_s, end_s


def _slim(segments):
    """只留定位要用的三样：起、止、文字。别把 words 那一大坨也缓存下来。"""
    out = []
    for seg in segments or []:
        if isinstance(seg, dict):
            start, end, text = seg.get("start"), seg.get("end"), seg.get("text")
        else:
            start = getattr(seg, "start", None)
            end = getattr(seg, "end", None)
            text = getattr(seg, "text", "")
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError):
            continue
        text = str(text or "").strip()
        if not text:
            continue
        out.append({"start": start, "end": end, "text": text})
    return out


def words_to_segments(words) -> list[dict]:
    """一个词一条。定位时首词的起点、末词的终点就是标题的时长。"""
    out = []
    for item in words or []:
        try:
            text, start, end = item[0], item[1], item[2]
            start = float(start)
            end = float(end)
        except (TypeError, ValueError, IndexError):
            continue
        text = str(text or "").strip()
        if not text or end < start:
            continue
        out.append({"start": start, "end": end, "text": text})
    return out


def _hello_language(language: str) -> str:
    lang = str(language or "").strip().lower()
    if lang and lang != "auto":
        return lang
    try:
        from cutora.core import config as cfg
        lang = str(getattr(cfg, "LANGUAGE", "") or "").strip().lower()
    except Exception:
        lang = ""
    if lang and lang != "auto":
        return lang
    return "en"


def _is_oom(exc: BaseException) -> bool:
    text = str(exc or "").lower()
    return "oom" in text or "out of memory" in text


def sentences_from_raw(raw) -> list[dict]:
    """worker 给的 [(text, start, end)] → [{start, end, text}]。"""
    out = []
    for item in raw or []:
        try:
            text, start, end = item[0], float(item[1]), float(item[2])
        except (TypeError, ValueError, IndexError):
            continue
        text = str(text or "").strip()
        if text:
            out.append({"start": start, "end": max(end, start), "text": text})
    return out


def _transcribe_window(client, path: str, language: str, start: float, end: float):
    """一段窗口 → (词, 原句)。"""
    words, _incomplete, _tier = client.transcribe(
        path, max(0.2, float(end) - float(start)), language,
        timeout_sec=120.0, win_start=float(start), win_end=float(end))
    return words_to_segments(words), sentences_from_raw(getattr(client, "last_segments", None))


def _media_duration(path: str) -> float:
    try:
        from cutora.batch.script_stitcher import probe_media
        info = probe_media(path) or {}
        return float(info.get("duration") or 0.0)
    except Exception:
        _LOG.exception("probe for full-video locate failed: %s", path)
        return 0.0


def _transcribe_full(client, path: str, language: str):
    """整条视频：30 s 一段、重叠 1 s、串行；拼接时丢掉重叠区里重复的词。显存和单窗口一样。"""
    dur = _media_duration(path)
    if dur <= 0.0:
        raise RuntimeError("读不到视频时长，没法整条识别")
    words: list[dict] = []
    sents: list[dict] = []
    a = 0.0
    while a < dur - 0.05:
        b = min(dur, a + MAX_LOCATE_WINDOW_SEC)
        w, s = _transcribe_window(client, path, language, a, b)
        cut = words[-1]["end"] if words else -1.0
        words.extend(x for x in w if x["start"] >= cut - 0.02)     # 重叠区里上一段已有的词不再要
        scut = sents[-1]["end"] if sents else -1.0
        sents.extend(x for x in s if x["end"] > scut + 0.3)       # 有新内容的句子才要
        if b >= dur:
            break
        a = b - _CHUNK_OVERLAP_S
    return words, sents


def _transcribe_with_client(client, path: str, language: str, start: float, end: float):
    """兼容旧调用：只回词。"""
    return _transcribe_any(client, path, language, start, end)[0]


def _transcribe_any(client, path: str, language: str, start: float, end: float):
    if is_full(end):
        return _transcribe_full(client, path, language)
    return _transcribe_window(client, path, language, start, end)


def _transcribe_uncached(path: str, language: str = "auto"):
    """跑一遍识别。测试会打桩这一个函数。只解码 _ctx 里的那几秒。"""
    from cutora.asr.clip_worker import AsrClient

    start, end = clamp_window(_ctx.get("start"), _ctx.get("end"))
    lang = _hello_language(language)
    client = AsrClient()
    try:
        client.start(lang)
        return _transcribe_any(client, path, lang, start, end)
    finally:
        client.close()


def segments_for_locate(path: str, language: str = "auto",
                        start: float = 0.0, end: float = DEFAULT_LOCATE_END):
    """这条视频用来定位的时间戳。命中缓存就不跑识别。"""
    start, end = clamp_window(start, end)
    lang = str(language or "auto")
    hit = lookup(path, start, end, lang)
    if hit is not None:
        return hit
    _ctx["start"], _ctx["end"], _ctx["language"] = start, end, lang
    got = _transcribe_uncached(path, lang)
    segs, sents = (got if isinstance(got, tuple) else (got, []))
    store(path, segs, start, end, lang, sentences=sents)
    return segs


def run_locate_batch(paths, on_one, *, start=0.0, end=DEFAULT_LOCATE_END,
                     language: str = "auto", force: bool = False) -> None:
    """一个模型、一个接一个。``on_one(path, segments, err)`` 可能在工作线程里调用。

    显存不够就停。已经写进缓存的不删，剩下的路径带回「已停止」。
    """
    from cutora.asr.clip_worker import AsrClient

    start, end = clamp_window(start, end)
    lang = str(language or "auto")
    hello = _hello_language(lang)
    pending = []
    for path in paths or []:
        path = str(path or "")
        if not path:
            continue
        if not force:
            hit = lookup(path, start, end, lang)
            if hit is not None:
                if callable(on_one):
                    on_one(path, hit, "")
                continue
        pending.append(path)
    if not pending:
        return
    client = AsrClient()
    stopped = False
    try:
        try:
            client.start(hello)
        except Exception as exc:
            _LOG.exception("locate asr model failed")
            err = ("已停止：显存不够。已经识别到的时间戳已保存。"
                   if _is_oom(exc) else "模型加载失败：%s" % (str(exc)[:160] or "未知原因"))
            for path in pending:
                if callable(on_one):
                    on_one(path, [], err)
            return
        for path in pending:
            if stopped:
                if callable(on_one):
                    on_one(path, [], "已停止：显存不够。已经识别到的时间戳已保存。")
                continue
            try:
                segs, sents = _transcribe_any(client, path, hello, start, end)
                store(path, segs, start, end, lang, sentences=sents)
                _LOG.info("[Locate] %s 窗口 %s：%d 个词、%d 句",
                          os.path.basename(path), "整条" if is_full(end) else "%.0f–%.0fs" % (start, end),
                          len(segs), len(sents))
                if callable(on_one):
                    on_one(path, segs, "")
            except Exception as exc:
                _LOG.exception("locate asr failed: %s", path)
                err = "识别出错：%s" % (str(exc)[:160] or "未知原因")
                if callable(on_one):
                    on_one(path, [], err)
                if _is_oom(exc):
                    stopped = True
                    try:
                        client.close()
                    except Exception:
                        pass
    finally:
        try:
            client.close()
        except Exception:
            pass


def queue_locate_batch(paths, on_done, *, start=0.0, end=DEFAULT_LOCATE_END,
                       language: str = "auto", force: bool = False) -> int:
    """后台串行跑完这一批。每条在界面线程回调 on_done(path, segments, error)。

    已经有一批在跑时返回 0，不再开第二个模型。
    """
    global _busy, _live_thread
    from PySide6.QtCore import QObject, QThread, Signal

    paths = [str(p) for p in (paths or []) if p]
    if not paths or _busy:
        return 0
    _busy = True
    start, end = clamp_window(start, end)

    class _Sig(QObject):
        one = Signal(str, object, str)
        finished = Signal()

    class _Job(QThread):
        def run(self):
            def _one(path, segs, err):
                try:
                    sig.one.emit(str(path), segs, str(err or ""))
                except RuntimeError:
                    pass
            from cutora.core import gpu_gate
            ticket = gpu_gate.acquire("文本定位识别")       # Q152-D：和其他 AI 任务排队用显卡
            try:
                run_locate_batch(
                    paths, _one, start=start, end=end,
                    language=language, force=force)
            except Exception as exc:
                _LOG.exception("locate batch failed")
                err = str(exc) or "识别失败"
                for path in paths:
                    _one(path, [], err)
            finally:
                gpu_gate.release(ticket)
            try:
                sig.finished.emit()
            except RuntimeError:
                pass

    sig = _Sig()
    job = _Job()
    _live_thread = job

    def _one_gui(path, segs, err):
        if callable(on_done):
            on_done(path, segs, err)

    def _finished():
        global _busy, _live_thread
        _busy = False
        if _live_thread is job:
            _live_thread = None

    sig.one.connect(_one_gui)
    sig.finished.connect(_finished)
    job._sig = sig  # 线程活着时信号对象不能被收掉
    job.start()
    return len(paths)


def queue_locate_probe(path: str, on_done, language: str = "auto",
                       start: float = 0.0, end: float = DEFAULT_LOCATE_END) -> int:
    """一条也走同一条串行队列，不会和别的识别抢显卡。"""
    return queue_locate_batch(
        [path], on_done, start=start, end=end, language=language)
