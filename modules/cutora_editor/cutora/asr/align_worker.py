# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 859-874, 877-1033, 2478-2543, 2849-3064

import os
import numbers
import math
from cutora.asr.engine import (
    torch,
    whisperx,
    get_align_model,
    get_whisperx_model,
    trim_gpu_cache,
    DEVICE,
    COMPUTE_TYPE,
    LANGUAGE,
)
from PySide6.QtCore import (
    QThread,
    Signal,
)
from cutora.core.utils import clean_text
from cutora.subtitle.text_utils import resegment_words_by_style
from cutora.render.readers import (
    probe_bitrates,
    probe_duration_sec,
)
from cutora.asr.engine import align_transcript, get_align_model, get_whisperx_model, has_local_align_model, torch, trim_gpu_cache, whisperx
from cutora.core.paths import MODELS
from cutora.render.readers import probe_bitrates, probe_duration_sec
from cutora.subtitle.text_utils import resegment_words_by_style

# ================================================

from cutora.asr.long_audio import WHISPER_CHUNK_S, transcribe_long_audio  # noqa: F401

def apply_time_offset(segments, offset_sec=0.2, min_dur=0.25):
    updated = []
    for seg in segments:
        start = seg.get('start')
        end = seg.get('end')
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            updated.append(seg)
            continue
        new_start = max(0, start - offset_sec)
        new_end = max(new_start + min_dur, end - offset_sec)
        seg['start'] = new_start
        seg['end'] = new_end
        updated.append(seg)
    return updated

def fix_segment_durations(segments, min_dur=0.6, next_gap_pad=0.05, rebuild_words_first_only=True):
    """修复字幕段 end 过早导致“字幕一闪而过”的问题。
    规则（兜底优先级）：
    - end 至少覆盖：原 seg.end、该段最后一个 word.end
    - 若仍偏短：保证最小可读时长 min_dur
    - 若下一段存在且不重叠：end 至少延伸到 next.start - next_gap_pad
    注意：此函数应在所有后处理之后最后执行，覆盖 seg['end']。
    """
    if not isinstance(segments, list) or not segments:
        return segments

    def _is_num(v):
        if not (isinstance(v, numbers.Real) and not isinstance(v, bool)):
            return False
        try:
            return math.isfinite(float(v))
        except Exception:
            return False

    segs = [s for s in segments if isinstance(s, dict)]
    if not segs:
        return segments

    # 按 start 排序，保证 next.start 语义正确（不改原列表对象引用）
    segs.sort(key=lambda s: float(s.get("start", 0.0)) if _is_num(s.get("start")) else 0.0)

    for i, seg in enumerate(segs):
        start = seg.get("start", 0.0)
        end = seg.get("end", start)
        if not _is_num(start):
            start = 0.0
        start = max(0.0, float(start))
        if not _is_num(end):
            end = start
        end = float(end)

        # 兜底 1：word-level end
        words = seg.get("words", [])
        if isinstance(words, list) and words:
            last_word_end = None
            for w in words:
                if not isinstance(w, dict):
                    continue
                we = w.get("end")
                if _is_num(we):
                    ve = float(we)
                    last_word_end = ve if last_word_end is None else max(last_word_end, ve)
            if last_word_end is not None:
                end = max(end, last_word_end)

        # 兜底 2：最小可读时长
        if end - start < float(min_dur):
            end = start + float(min_dur)

        # 兜底 3：对齐下一句（不制造重叠）
        if i + 1 < len(segs):
            ns = segs[i + 1].get("start")
            if _is_num(ns):
                next_start = float(ns)
                if next_start > start + 1e-4:
                    target = next_start - float(next_gap_pad)
                    if target > end:
                        end = target
                    # 若 end 已经超过 next_start（重叠），则轻微回退避免覆盖下一句
                    if end > next_start - 0.01:
                        end = max(start + 0.25, next_start - 0.01)

        seg["start"] = start
        seg["end"] = max(start, end)

        # === 弹力色/逐词高亮：只允许“首句”在必要时修复 words 时间轴 ===
        # 用户需求：后续句子必须严格按 WhisperX word-level 对齐实时高亮，
        # 不能因为 segment 拉长而影响单词对齐。
        if rebuild_words_first_only and i == 0:
            words = seg.get("words", [])
            if isinstance(words, list) and words:
                # 计算当前 words 覆盖
                w_starts = []
                w_ends = []
                for w in words:
                    if not isinstance(w, dict):
                        continue
                    ws = w.get("start")
                    we = w.get("end")
                    if _is_num(ws):
                        w_starts.append(float(ws))
                    if _is_num(we):
                        w_ends.append(float(we))
                seg_span = max(0.0, end - start)
                cov_span = 0.0
                cov_s = cov_e = None
                if seg_span > 0.2 and w_starts and w_ends:
                    cov_s = min(w_starts)
                    cov_e = max(w_ends)
                    cov_span = max(0.0, cov_e - cov_s)

                # 仅当首句 words 覆盖明显不足（导致高亮“闪一下”）才修复
                need_rebuild = False
                if seg_span > 0.8:
                    if cov_span < seg_span * 0.50:
                        need_rebuild = True
                    if w_ends and (max(w_ends) < end - 0.50):
                        need_rebuild = True

                if need_rebuild:
                    # 优先：保留 WhisperX 相对节奏 → 线性拉伸到 [start, end]
                    if cov_s is not None and cov_e is not None and cov_e > cov_s + 1e-3:
                        span0 = cov_e - cov_s
                        cursor = start
                        for wi, w in enumerate(words):
                            if not isinstance(w, dict):
                                continue
                            ws = w.get("start")
                            we = w.get("end")
                            if _is_num(ws) and _is_num(we):
                                n0 = (float(ws) - cov_s) / span0
                                n1 = (float(we) - cov_s) / span0
                                w_start = start + n0 * seg_span
                                w_end = start + n1 * seg_span
                            else:
                                # 缺失时间戳的词：按游标顺序给一个最小宽度
                                w_start = cursor
                                w_end = min(end, w_start + 0.06)
                            if wi == len(words) - 1:
                                w_end = end
                            w_start = max(start, min(end, float(w_start)))
                            w_end = max(w_start + 0.01, min(end, float(w_end)))
                            # 保证单调递增
                            w_start = max(w_start, cursor)
                            w_end = max(w_end, w_start + 0.01)
                            w["start"] = w_start
                            w["end"] = w_end
                            cursor = w_end
                    else:
                        # 退化：没有可拉伸的原时间轴 → 按 token 长度分配
                        tokens = []
                        weights = []
                        for w in words:
                            if not isinstance(w, dict):
                                continue
                            tok = str(w.get("word", "") or "").strip()
                            tokens.append(w)
                            weights.append(max(1, len(tok)))
                        total_w = float(sum(weights)) if weights else 1.0
                        cursor = start
                        for wi, w in enumerate(tokens):
                            frac = weights[wi] / total_w
                            w_dur = max(0.06, seg_span * frac)
                            w_start = cursor
                            w_end = min(end, w_start + w_dur)
                            if wi == len(tokens) - 1:
                                w_end = end
                            w["start"] = w_start
                            w["end"] = max(w_start + 0.01, w_end)
                            cursor = w["end"]

    return segs

def merge_short_segments(segments, short_dur=0.35, max_gap=0.25, min_dur=0.25):
    segs = sorted(segments, key=lambda s: s.get('start', 0))
    i = 0
    while i < len(segs):
        s = segs[i].get('start')
        e = segs[i].get('end')
        if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
            i += 1
            continue
        dur = e - s
        if dur >= short_dur:
            i += 1
            continue
        left_idx = i - 1 if i - 1 >= 0 else None
        right_idx = i + 1 if i + 1 < len(segs) else None
        best_idx = None
        best_gap = None
        if right_idx is not None:
            rs = segs[right_idx].get('start')
            if isinstance(rs, (int, float)):
                gap_r = rs - e
                if gap_r <= max_gap:
                    best_idx = right_idx
                    best_gap = gap_r
        if left_idx is not None:
            le = segs[left_idx].get('end')
            if isinstance(le, (int, float)):
                gap_l = s - le
                if gap_l <= max_gap and (best_idx is None or gap_l < best_gap):
                    best_idx = left_idx
                    best_gap = gap_l
        if best_idx is None:
            i += 1
            continue
        target = segs[best_idx]
        if best_idx < i:
            target['start'] = min(target.get('start', s), s)
            target['end'] = max(target.get('end', e), e)
            if target.get('text') and segs[i].get('text'):
                target['text'] = f"{target['text']} {segs[i]['text']}"
            else:
                target['text'] = target.get('text', '') or segs[i].get('text', '')
            if 'words' in target and 'words' in segs[i]:
                target['words'].extend(segs[i]['words'])
        else:
            target['start'] = min(target.get('start', s), s)
            target['end'] = max(target.get('end', e), e)
            if segs[i].get('text') and target.get('text'):
                target['text'] = f"{segs[i]['text']} {target['text']}"
            else:
                target['text'] = segs[i].get('text', '') or target.get('text', '')
            if 'words' in target and 'words' in segs[i]:
                target['words'] = segs[i]['words'] + target['words']
        del segs[i]
        if best_idx < i:
            i = max(0, best_idx - 1)
        else:
            i = max(0, i - 1)
    segs.sort(key=lambda s: s.get('start', 0))
    for seg in segs:
        s = seg.get('start')
        e = seg.get('end')
        if isinstance(s, (int, float)) and isinstance(e, (int, float)):
            if e < s + min_dur:
                seg['end'] = s + min_dur
    return segs

def _faster_whisper_segments(fw_model, audio, lang, initial_prompt=None, hotwords=None):
    """word_timestamps=True via faster-whisper; scores left None (approximate)."""
    from cutora.asr.decode_opts import asr_decode_options
    kw = dict(language=lang, word_timestamps=True, vad_filter=True)
    kw.update(asr_decode_options())
    if initial_prompt:
        kw["initial_prompt"] = initial_prompt
    if hotwords:
        kw["hotwords"] = hotwords
    segments_iter, _info = fw_model.transcribe(audio, **kw)
    out = []
    for seg in segments_iter:
        words = []
        for w in list(getattr(seg, "words", None) or []):
            token = getattr(w, "word", None)
            if not token:
                continue
            words.append({
                "word": token,
                "start": getattr(w, "start", None),
                "end": getattr(w, "end", None),
                "score": None,
            })
        out.append({
            "start": getattr(seg, "start", None),
            "end": getattr(seg, "end", None),
            "text": getattr(seg, "text", "") or "",
            "words": words,
        })
    return _ensure_segment_words(out)


def _ensure_segment_words(segments):
    """If ASR returned no word timings, keep one word spanning the segment."""
    out = []
    for seg in list(segments or []):
        if not isinstance(seg, dict):
            out.append(seg)
            continue
        if seg.get("words"):
            out.append(seg)
            continue
        copied = dict(seg)
        text = (seg.get("text") or "").strip()
        if text:
            copied["words"] = [{
                "word": text,
                "start": seg.get("start"),
                "end": seg.get("end"),
            }]
        out.append(copied)
    return out


# --- AI Worker (Enhanced) ---
class AlignmentWorker(QThread):
    finished = Signal(object) 
    error = Signal(str)
    progress = Signal(int)
    queued = Signal(str)        # Q152-D：显卡被别的 AI 任务占着，排队中（参数 = 占用者）
    
    def __init__(
        self, video_path, user_text, style=None, video_w=None, language=None,
        initial_prompt=None, hotwords=None, asr_dir=None,
    ):
        super().__init__()
        self.video_path = video_path
        self.user_text = user_text
        self.style = style or {}
        self.video_w = video_w
        self.language = language
        self.initial_prompt = initial_prompt or ""
        self.hotwords = hotwords or ""
        self.asr_dir = asr_dir
        self._is_running = True
        if not self.initial_prompt and not self.hotwords:
            from cutora.asr.glossary import prompt_and_hotwords
            prompt, hot = prompt_and_hotwords()
            self.initial_prompt = prompt
            self.hotwords = hot

    def stop(self): self._is_running = False

    def _apply_asr_hints(self, model):
        prompt = (self.initial_prompt or "").strip()
        hot = (self.hotwords or "").strip()
        opts = getattr(model, "options", None)
        if opts is None:
            return
        from dataclasses import replace
        from cutora.asr.decode_opts import asr_decode_options
        from cutora.core.log import get_logger
        kw = asr_decode_options()
        if prompt:
            kw["initial_prompt"] = prompt
        if hot:
            kw["hotwords"] = hot
        fields = getattr(opts, "__dataclass_fields__", None)
        if fields:
            kw = {k: v for k, v in kw.items() if k in fields}
        if not kw:
            return
        try:
            model.options = replace(opts, **kw)
        except TypeError as exc:
            get_logger("cutora.asr.align_worker").warning(
                "could not apply ASR decode options: %s", exc)

    def run(self):
        try:
            self.align_note = None
            self.approximate_words = False
            self.language_detection = None
            self.progress.emit(10)
            if not self._is_running: return
            # Q152-D：和 AI 抠像 / 跟踪排队用显卡（先到先得；排队时可取消）
            from cutora.core import gpu_gate
            self._gpu_ticket = gpu_gate.acquire(
                "AI 字幕", should_stop=lambda: not self._is_running,
                on_wait=lambda who: self.queued.emit(str(who or "")))
            if not self._gpu_ticket: return
            if torch.cuda.is_available(): torch.cuda.empty_cache()
            
            self.progress.emit(20)
            os.environ["HF_HOME"] = MODELS
            print("正在初始化模型，若本地缺失将进行约 500MB 的下载，请观察命令行")
            compute_type = COMPUTE_TYPE
            if DEVICE == "cpu":
                compute_type = "float32"
            model = get_whisperx_model(self.asr_dir)
            if not self._is_running: return
            self._apply_asr_hints(model)
            final_segments = self._run_pipeline(model)
            if self._is_running:
                self.progress.emit(100)
                self.finished.emit(final_segments)
        except Exception as e:
            # Q132：以前只把 str(e) 发给界面，日志里没有栈，WinError 2 这种
            # 「找不到文件」根本看不出是哪个文件。
            try:
                from cutora.core.log import get_logger
                get_logger("cutora.asr.align").exception(
                    "自动字幕失败 media=%s", self.video_path)
            except Exception:
                pass
            msg = str(e)
            if isinstance(e, FileNotFoundError) and getattr(e, "filename", None):
                msg = "%s（%s）" % (msg, e.filename)
            self.error.emit(msg)
        finally:
            # [v6.1] 对齐推理产生的大块中间张量在此归还驱动。
            # 模型本体保持常驻，不影响下一个视频的处理速度。
            # 无论成功、失败还是中途 return，这里都会执行。
            trim_gpu_cache("AI字幕结束")
            try:
                from cutora.core import gpu_gate
                gpu_gate.release(getattr(self, "_gpu_ticket", 0))
                self._gpu_ticket = 0
            except Exception:
                pass

    def _load_audio_bundle(self):
        from cutora.asr.audio_io import load_audio as _load_audio_nowin
        audio = _load_audio_nowin(self.video_path)   # Q133：不开黑框
        audio_dur = probe_duration_sec(self.video_path)
        if not isinstance(audio_dur, (int, float)) or audio_dur <= 0:
            try:
                audio_dur = float(len(audio)) / 16000.0
            except Exception:
                audio_dur = None
        return audio, audio_dur

    def _run_pipeline(self, model):
        from cutora.core import config as _cfg
        from cutora.asr.languages import whisper_language_arg
        requested = self.language if self.language is not None else getattr(
            _cfg, "LANGUAGE", LANGUAGE)
        lang = whisper_language_arg(requested)
        audio = None
        audio_dur = None
        if lang is None:
            self.progress.emit(50)
            audio, audio_dur = self._load_audio_bundle()
            from cutora.asr.detect import detect_from_array
            det = detect_from_array(audio, model=model)
            self.language_detection = det
            lang = det["language"]
        from cutora.asr.tier import choose_word_timing, notify_fallback
        timing = choose_word_timing(lang, has_align=has_local_align_model(lang))
        if timing.layer != "align":
            self.align_note = timing.notify
            return self._transcribe_without_align(model, audio, audio_dur, lang)
        try:
            align_model, metadata = get_align_model(lang)
        except Exception:
            self.align_note = notify_fallback("align", "word_timestamps") + "（近似词级）"
            return self._transcribe_without_align(model, audio, audio_dur, lang)
        self.progress.emit(50)
        if audio is None:
            audio, audio_dur = self._load_audio_bundle()
        return self._align_or_transcribe(
            model, align_model, metadata, audio, audio_dur, lang)

    def _transcribe_without_align(self, model, audio, audio_dur, lang):
        self.approximate_words = True
        if not self.align_note:
            self.align_note = "近似词级"
        if audio is None:
            self.progress.emit(50)
            audio, audio_dur = self._load_audio_bundle()
        fw = getattr(model, "model", None)
        if fw is not None and hasattr(fw, "transcribe"):
            segs = _faster_whisper_segments(
                fw, audio, lang,
                initial_prompt=self.initial_prompt, hotwords=self.hotwords)
        else:
            result = transcribe_long_audio(model, audio, lang, batch_size=4)
            segs = _ensure_segment_words(result.get("segments") or [])
        if not self._is_running:
            return []
        self.progress.emit(90)
        return self.smart_segmentation(segs, audio_dur)

    def _align_or_transcribe(self, model, align_model, metadata, audio, audio_dur, lang):
        self.progress.emit(60)
        aligned_user_segments, align_stats = self.force_align_user_text(
            self.user_text, align_model, metadata, audio, DEVICE, audio_dur
        )
        use_forced = False
        if aligned_user_segments:
            missing_ratio = align_stats.get("missing_ratio", 1.0)
            if missing_ratio <= 0.08:
                use_forced = True
        if use_forced:
            self.progress.emit(85)
            return self.smart_segmentation(aligned_user_segments, audio_dur)
        self.align_note = "原文与朗读不一致，已自动切换为识别字幕以保证时间准确"
        result = transcribe_long_audio(model, audio, lang, batch_size=4)
        if not self._is_running:
            return []
        self.progress.emit(75)
        aligned_result = align_transcript(
            result["segments"], align_model, metadata, audio, DEVICE,
            return_char_alignments=False)
        self.progress.emit(90)
        return self.smart_segmentation(aligned_result["segments"], audio_dur)

    def force_align_user_text(self, user_text_raw, align_model, metadata, audio, device, audio_dur):
        txt = clean_text(user_text_raw or "")
        if not txt:
            return None, {"missing_ratio": 1.0}
        dur = float(audio_dur) if isinstance(audio_dur, (int, float)) and audio_dur > 0 else 0.0
        segs = [{"start": 0.0, "end": dur, "text": txt}]
        try:
            aligned = align_transcript(segs, align_model, metadata, audio, device, return_char_alignments=False)
        except Exception as exc:
            from cutora.asr.stitch_language import StitchLanguageError
            if isinstance(exc, StitchLanguageError):
                raise
            return None, {"missing_ratio": 1.0}
        segments = aligned.get("segments", [])
        words = self._flatten_words_from_segments(segments)
        missing_ratio = self._calc_missing_ratio(words)
        return segments, {"missing_ratio": missing_ratio}

    def smart_segmentation(self, aligned_segments, audio_dur):
        all_words = self._flatten_words_from_segments(aligned_segments)
        all_words = self._interpolate_missing_word_times(all_words, audio_dur)
        video_w = self.video_w
        if not isinstance(video_w, (int, float)) or video_w <= 0:
            probed = probe_bitrates(self.video_path)
            video_w = probed.get("width") if probed else None
        if not isinstance(video_w, (int, float)) or video_w <= 0:
            video_w = 1920
        return resegment_words_by_style(all_words, self.style, video_w)

    def _flatten_words_from_segments(self, segments):
        all_words = []
        wid = 0
        for seg in segments or []:
            for w in seg.get("words", []) or []:
                token = w.get("word", "")
                if not token:
                    continue
                all_words.append({
                    "word": token,
                    "start": w.get("start"),
                    "end": w.get("end"),
                    "wid": wid
                })
                wid += 1
        return all_words

    def _calc_missing_ratio(self, words):
        if not words:
            return 1.0
        missing = 0
        for w in words:
            s = w.get("start")
            e = w.get("end")
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
                missing += 1
        return missing / max(1, len(words))

    def _interpolate_missing_word_times(self, words, audio_dur):
        if not words:
            return words
        def _valid(t):
            # whisperx 有时会返回 numpy.float32/float64 等类型；用 numbers.Real 统一判断
            if not (isinstance(t, numbers.Real) and not isinstance(t, bool)):
                return False
            try:
                return math.isfinite(float(t))
            except Exception:
                return False
        n = len(words)
        i = 0
        while i < n:
            s = words[i].get("start")
            e = words[i].get("end")
            if _valid(s) and _valid(e):
                i += 1
                continue
            run_start = i
            while i < n and (not _valid(words[i].get("start")) or not _valid(words[i].get("end"))):
                i += 1
            run_end = i - 1
            prev_end = None
            if run_start - 1 >= 0 and _valid(words[run_start - 1].get("end")):
                prev_end = words[run_start - 1].get("end")
            next_start = None
            if run_end + 1 < n and _valid(words[run_end + 1].get("start")):
                next_start = words[run_end + 1].get("start")
            if _valid(prev_end) and _valid(next_start) and next_start >= prev_end:
                span = next_start - prev_end
                step = span / (run_end - run_start + 2)
                for k, idx in enumerate(range(run_start, run_end + 1)):
                    start = prev_end + step * (k + 0)
                    end = prev_end + step * (k + 1)
                    words[idx]["start"] = start
                    words[idx]["end"] = end
            else:
                # 关键增强：处理“开头一段缺失时间戳”的情况（prev_end=None, next_start>0）
                # 原逻辑会把 start=end=next_start，导致首句字幕极短/一闪而过。
                count = (run_end - run_start + 1)
                if (not _valid(prev_end)) and _valid(next_start) and float(next_start) > 0 and count > 0:
                    ns = float(next_start)
                    # 最多回填 1.2s（够用且不会过度扭曲），同时不超过 next_start
                    span = min(ns, 1.2)
                    base = max(0.0, ns - span)
                    step = span / (count + 1)
                    for k, idx in enumerate(range(run_start, run_end + 1)):
                        start = base + step * k
                        end = base + step * (k + 1)
                        words[idx]["start"] = start
                        words[idx]["end"] = end
                else:
                    fallback = prev_end if _valid(prev_end) else next_start
                    if not _valid(fallback):
                        fallback = 0.0
                    fv = float(fallback)
                    for idx in range(run_start, run_end + 1):
                        words[idx]["start"] = fv
                        words[idx]["end"] = fv
        if _valid(audio_dur) and float(audio_dur) > 0:
            for w in words:
                s = w.get("start")
                e = w.get("end")
                if _valid(s):
                    w["start"] = max(0.0, min(float(s), float(audio_dur)))
                if _valid(e):
                    w["end"] = max(0.0, min(float(e), float(audio_dur)))
        return words

    def pack_event(self, words):
        if not words: return None
        full_text = ""
        for w in words:
            if w.get('is_break'): full_text += "\n"
            full_text += w['word'] + " "
        return {
            "start": words[0]["start"], 
            "end": words[-1]["end"], 
            "words": words,
            "text": full_text.strip()
        }
