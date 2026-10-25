# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 106-106, 107-107, 109-110, 111-111, 113-115, 117-124, 126-166, 169-207, 210-234, 237-260, 263-280, 338-358

import os
from cutora.ui.widgets.lazy import _LazyModule
from cutora.core.config import (
    DEVICE,
    COMPUTE_TYPE,
    LANGUAGE,
)
from cutora.core.paths import get_model_paths, models_root
from cutora.ui.widgets.lazy import _LazyModule

torch = _LazyModule("torch")

whisperx = _LazyModule("whisperx")

# === WhisperX Global Model Cache (FIX for 20% freeze) ===
_WHISPERX_MODEL = None

_WHISPERX_DEVICE = None

_WHISPERX_DIR = None

# === WhisperX Alignment Model Cache (多语种版) ===
# 按 (语言, 设备) 缓存, 支持运行时切换语言而无需重启
_ALIGN_CACHE = {}

# 语言 → 对齐模型 显式映射
#   "en"  = LARGE_LV60K（Apache-2.0，本地 torchaudio）
#   "it"  = whisperX 默认 VOXPOPULI_ASR_BASE_10K_IT（CC-BY-NC，内部非盈利用）
ALIGN_MODEL_BY_LANG = {
    "en": "WAV2VEC2_ASR_LARGE_LV60K_960H",
    "it": None,
}


def has_local_align_model(language=None):
    """True for frozen en/it, a ready dedicated aligner, or MMS plus uroman."""
    lang = (language or "").strip().lower()
    if lang in ALIGN_MODEL_BY_LANG:
        return True
    from cutora.asr.models import align_row_for_language, generic_aligner_available, status_row
    row = align_row_for_language(lang)
    if row and row.get("enabled"):
        return status_row(row).get("status") == "ready"
    if not generic_aligner_available():
        return False
    from cutora.asr.languages import catalog_by_code
    return lang in catalog_by_code()


def _refuse_align(lang):
    """The human-readable stop Q108 already shows when nothing can time words."""
    from cutora.asr.stitch_language import StitchLanguageError, unstitchable_message
    raise StitchLanguageError(unstitchable_message(lang))


_MMS_MODEL = None
_MMS_META_BASE = None


def _load_mms_align_model(lang, key):
    """Load align-mms once. Romanization happens later, in ``align_transcript``."""
    global _MMS_MODEL, _MMS_META_BASE
    from cutora.asr.models import get_model, model_folder
    print("[WhisperX] 对齐走通用模型 align-mms（语言=%s，先罗马化再对齐）" % lang)
    if _MMS_MODEL is None:
        row = get_model("align-mms")
        if not row:
            _refuse_align(lang)
        folder = model_folder(row)
        if not os.path.isdir(folder):
            _refuse_align(lang)
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        # language_code "en" makes whisperx split the romanized text on spaces.
        # ja/zh would otherwise become one character per "word".
        model, metadata = whisperx.load_align_model(
            language_code="en",
            device=DEVICE,
            model_name=folder,
            model_dir=folder,
        )
        metadata = dict(metadata)
        dictionary = dict(metadata.get("dictionary") or {})
        # whisperx treats the vocab key "<pad>" as the CTC blank. This model's
        # pad_token_id is 0 ("<blank>"); the key "<pad>" is id 1. Leaving that
        # key in collapses every alignment onto the last token.
        dictionary.pop("<pad>", None)
        dictionary.pop("[pad]", None)
        metadata["dictionary"] = dictionary
        _MMS_MODEL = model
        _MMS_META_BASE = metadata
    metadata = dict(_MMS_META_BASE)
    metadata["cutora_mms"] = True
    metadata["cutora_source_lang"] = lang
    _ALIGN_CACHE[key] = (_MMS_MODEL, metadata)
    return _MMS_MODEL, metadata


def _load_catalog_or_mms(lang, key):
    from cutora.asr.models import align_row_for_language, generic_aligner_available
    row = align_row_for_language(lang)
    if row and row.get("enabled"):
        return _load_hf_align_model(lang, key)
    if generic_aligner_available():
        return _load_mms_align_model(lang, key)
    _refuse_align(lang)


def _load_hf_align_model(lang, key):
    """Load a catalogued HF wav2vec2 aligner from the local models dir."""
    from cutora.asr.models import align_row_for_language, model_folder
    row = align_row_for_language(lang)
    if not row or not row.get("enabled"):
        raise FileNotFoundError("No enabled align model for %s" % lang)
    folder = model_folder(row)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    model, metadata = whisperx.load_align_model(
        language_code=lang,
        device=DEVICE,
        model_name=folder,
        model_dir=folder,
    )
    _ALIGN_CACHE[key] = (model, metadata)
    return model, metadata

def get_align_model(language=None):
    """加载指定语言的对齐模型 (默认 config.LANGUAGE)。带缓存, 可多语言共存。"""
    from cutora.core import config as _cfg
    lang = (language or getattr(_cfg, "LANGUAGE", "en") or "en").lower()
    key = (lang, DEVICE)
    if key in _ALIGN_CACHE:
        return _ALIGN_CACHE[key]

    if lang not in ALIGN_MODEL_BY_LANG:
        return _load_catalog_or_mms(lang, key)

    print(f"[WhisperX] 正在加载对齐模型 (语言={lang}, 本地模式)...")

    # 1. 读取路径配置
    paths = get_model_paths()
    align_dir = paths.get("align_path")

    # 2. 验证路径
    if not align_dir or not os.path.exists(align_dir):
        # 尝试默认回退路径
        align_dir = os.path.join(models_root(), "align_wav2vec2")

    if not os.path.exists(align_dir):
        print(f"❌ 错误: 找不到对齐模型！预期路径: {align_dir}")
        raise FileNotFoundError("Align model missing. Please run download_full_models.py")

    try:
        # HF 强制离线; torchaudio 模型若本地缺失会尝试联网下载到 align_dir
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

        model, metadata = whisperx.load_align_model(
            language_code=lang,
            device=DEVICE,
            model_name=ALIGN_MODEL_BY_LANG.get(lang),  # None → whisperX 默认
            model_dir=align_dir  # 关键：直接传入本地文件夹路径
        )
        _ALIGN_CACHE[key] = (model, metadata)
        return model, metadata
    except Exception as e:
        print(f"[Error] 对齐模型加载失败 (语言={lang}): {e}")
        if lang == "en":
            print("👉 英文大模型缺失时请双击运行 4_Download_EN_Align_Model.bat 下载")
        raise


def align_transcript(segments, model, metadata, audio, device, **kwargs):
    """``whisperx.align``, romanizing only on the MMS fallback.

    Returned segment text and word strings stay in the original script.
    If romanization does not stay one token per original token, this raises
    the same human-readable error as a missing aligner. It does not guess.
    """
    if not (metadata or {}).get("cutora_mms"):
        return whisperx.align(segments, model, metadata, audio, device, **kwargs)
    from cutora.asr.romanize import RomanizeError, romanize_tokens, speech_tokens
    lang = str((metadata or {}).get("cutora_source_lang") or "")
    rewritten = []
    per_segment = []
    for seg in segments or []:
        text = "" if seg is None else str(seg.get("text") or "")
        tokens = speech_tokens(text, lang)
        try:
            roman = romanize_tokens(tokens, lang) if tokens else []
        except (RomanizeError, ImportError):
            _refuse_align(lang)
        if len(roman) != len(tokens):
            _refuse_align(lang)
        copied = dict(seg)
        copied["text"] = " ".join(roman) if roman else text
        rewritten.append(copied)
        per_segment.append(tokens)
    aligned = whisperx.align(rewritten, model, metadata, audio, device, **kwargs)
    flat = [token for group in per_segment for token in group]
    words = []
    for seg in aligned.get("segments") or []:
        for word in seg.get("words") or []:
            if str(word.get("word") or "").strip():
                words.append(word)
    if flat and len(words) != len(flat):
        _refuse_align(lang)
    cursor = 0
    char_script = False
    if lang:
        from cutora.asr.languages import catalog_by_code
        row = catalog_by_code().get(lang) or {}
        char_script = row.get("line_break") == "fallback"
    for seg in aligned.get("segments") or []:
        owned = [w for w in (seg.get("words") or []) if str(w.get("word") or "").strip()]
        chunk = flat[cursor:cursor + len(owned)]
        cursor += len(owned)
        for word, src in zip(owned, chunk):
            word["word"] = src
        if chunk:
            seg["text"] = "".join(chunk) if char_script else " ".join(chunk)
    aligned["word_segments"] = []
    for seg in aligned.get("segments") or []:
        aligned["word_segments"].extend(seg.get("words") or [])
    return aligned


def get_whisperx_model(asr_dir=None, log=None):
    """Load ASR. ``model_paths.json`` is the ceiling; T12f caps to what this machine can run."""
    global _WHISPERX_MODEL, _WHISPERX_DEVICE, _WHISPERX_DIR
    from cutora.asr.tier import (
        asr_dirname, is_oom_error, probe_hardware, resolve_runtime_tier,
        handle_oom,
    )
    from cutora.core import config as _cfg

    def _say(msg):
        if callable(log):
            log(msg)
        else:
            print(msg)

    if _WHISPERX_MODEL is not None and _WHISPERX_DIR:
        if asr_dir is None:
            return _WHISPERX_MODEL
        want = os.path.normcase(os.path.abspath(asr_dir))
        have = os.path.normcase(os.path.abspath(_WHISPERX_DIR))
        if want == have:
            return _WHISPERX_MODEL

    wanted = asr_dir
    if not wanted:
        paths = get_model_paths()
        wanted = paths.get("asr_path")
    probe = probe_hardware()
    if str(getattr(_cfg, "DEVICE", "") or "").lower() != "cuda":
        from cutora.asr.tier import HardwareProbe
        probe = HardwareProbe(
            cuda_count=0,
            vram_total_gb=0.0,
            vram_free_gb=0.0,
            ram_total_gb=probe.ram_total_gb,
            ram_free_gb=probe.ram_free_gb,
            cpu_count=probe.cpu_count,
            models_on_disk=probe.models_on_disk,
        )
    picked, drop_msg = resolve_runtime_tier(wanted, probe, log=_say)
    asr_path = os.path.join(models_root(), asr_dirname(picked["asr_id"]))
    device = str(picked["device"])
    from cutora.asr.tier import gpu_compute
    compute = gpu_compute(str(picked["compute"])) if device == "cuda" else str(picked["compute"])
    if drop_msg:
        _say(drop_msg)
    if not os.path.isdir(asr_path):
        asr_path = wanted if wanted and os.path.isdir(wanted) else os.path.join(
            models_root(), "asr_small")
        device = DEVICE
        compute = COMPUTE_TYPE
    cache_key = (asr_path, device, compute)
    if (_WHISPERX_MODEL is not None
            and _WHISPERX_DEVICE == device
            and _WHISPERX_DIR == asr_path
            and getattr(_WHISPERX_MODEL, "_cutora_compute", None) == compute):
        return _WHISPERX_MODEL

    _cfg.DEVICE = device
    _cfg.COMPUTE_TYPE = compute
    _sync_device_globals()

    _say("[WhisperX] 正在加载 ASR 模型...")
    _say("   路径: %s" % asr_path)
    _say("   设备: %s" % device)
    _say("   精度: %s" % compute)
    _say("   档位: %s" % picked["id"])

    if not os.path.exists(asr_path):
        _say("❌ 严重错误: 找不到 ASR 模型文件夹: %s" % asr_path)
        raise FileNotFoundError("ASR model missing. Please run download_full_models.py")

    last_err = None
    cur = picked
    for _attempt in range(6):
        try:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            from cutora.asr.languages import whisper_language_arg
            from cutora.asr.decode_opts import asr_decode_options
            model = whisperx.load_model(
                asr_path,
                device,
                compute_type=compute,
                language=whisper_language_arg(LANGUAGE),
                vad_model=whisperx_vad_model(),
                asr_options=asr_decode_options(),
            )
            try:
                model._cutora_compute = compute
                model._cutora_tier = cur["id"]
            except Exception:
                pass
            _WHISPERX_MODEL = model
            _WHISPERX_DEVICE = device
            _WHISPERX_DIR = asr_path
            return _WHISPERX_MODEL
        except Exception as e:
            last_err = e
            if not is_oom_error(e):
                _say("FATAL ERROR: 模型加载失败: %s" % e)
                raise
            nxt = handle_oom(cur["id"], log=_say)
            if not nxt:
                _say("FATAL ERROR: 模型加载 OOM，已在最低档: %s" % e)
                raise
            from cutora.asr.tier import asr_tier
            release_gpu_models()
            cur = asr_tier(nxt)
            asr_path = os.path.join(models_root(), asr_dirname(cur["asr_id"]))
            device = str(cur["device"])
            compute = gpu_compute(str(cur["compute"])) if device == "cuda" else str(cur["compute"])
            _cfg.DEVICE = device
            _cfg.COMPUTE_TYPE = compute
            _sync_device_globals()
    _say("FATAL ERROR: 模型加载失败: %s" % last_err)
    raise last_err

# ═══════════════════════════════════════════════════════════════════════════
# 显存管理 (v6.1 优化)
#
# 背景：whisperx.align() 对整段音频一次性推理时，PyTorch 的 caching allocator
#       会向驱动申请数 GB 的显存块。推理结束后这些块留在 allocator 内部，
#       不会归还给驱动，导致「GPU 利用率 4% 但显存占用 11GB」。
#       在 16GB 显卡上这会让第二个软件实例直接 CUDA OOM 崩溃。
#
# 策略：
#   trim_gpu_cache()    —— 轻量。只把 allocator 里的空闲块还给驱动，
#                          模型仍然常驻，下一个视频不需要重新加载。
#                          每次 AI 字幕任务结束后自动调用。
#   release_gpu_models() —— 彻底。卸载 ASR + 对齐模型并清空缓存。
#                          由用户从菜单手动触发（准备双开时使用）。
# ═══════════════════════════════════════════════════════════════════════════

def _torch_loaded() -> bool:
    """Q187：torch 没进内存就不要为了「看一眼显存」把它拉进来（主界面里那是 2～3 GB）。"""
    import sys as _sys
    return "torch" in _sys.modules


def _cuda_mem_gb():
    """返回 (已分配, 已保留) 显存，单位 GB。CUDA 不可用 / torch 未加载时返回 (0.0, 0.0)。"""
    try:
        if not _torch_loaded() or not torch.cuda.is_available():
            return (0.0, 0.0)
        return (torch.cuda.memory_allocated() / (1024 ** 3),
                torch.cuda.memory_reserved() / (1024 ** 3))
    except Exception:
        return (0.0, 0.0)

def _trim_process_memory():
    """在 Windows 下将 Python GC / mimalloc / Rust 原生缓冲池及进程工作集中的空闲页立即归还给操作系统。"""
    import gc
    try:
        gc.collect()
    except Exception:
        pass
    try:
        from cutora.core.rust_engine import rust_trim_working_set
        rust_trim_working_set()
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            lib = ctypes.WinDLL("mimalloc.dll")
            lib.mi_collect.argtypes = [ctypes.c_bool]
            lib.mi_collect.restype = None
            lib.mi_collect(True)
        except Exception:
            pass
        try:
            import ctypes
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi.EmptyWorkingSet(k32.GetCurrentProcess())
        except Exception:
            pass


def trim_gpu_cache(tag=""):
    """把 PyTorch allocator 中的空闲显存块与系统内存块归还给驱动和操作系统。模型保持加载状态。

    这是一个纯粹的资源回收操作，不改变任何模型状态或推理结果，
    因此可以安全地在任意时刻调用。
    """
    try:
        import gc
        gc.collect()
        if not _torch_loaded() or not torch.cuda.is_available():
            _trim_process_memory()
            return
        before_alloc, before_res = _cuda_mem_gb()
        torch.cuda.empty_cache()
        try:
            torch.cuda.ipc_collect()
        except Exception:
            pass
        _trim_process_memory()
        after_alloc, after_res = _cuda_mem_gb()
        freed = before_res - after_res
        if freed > 0.05:
            print(f"[VRAM]{(' ' + tag) if tag else ''} 已释放 {freed:.2f} GB "
                  f"(保留 {before_res:.2f} → {after_res:.2f} GB, 模型占用 {after_alloc:.2f} GB)")
    except Exception as e:
        print(f"[VRAM] 缓存回收失败（已忽略，不影响功能）: {e}")

def release_gpu_models():
    """彻底卸载 ASR 与对齐模型并归还显存与系统内存。返回释放的 GB 数。

    下次运行 AI 字幕时会自动重新加载模型（get_whisperx_model /
    get_align_model 本身就是惰性加载），所以功能不受任何影响，
    只是下一次任务会多几秒的模型加载时间。
    """
    global _WHISPERX_MODEL, _WHISPERX_DEVICE, _WHISPERX_DIR, _ALIGN_CACHE
    global _MMS_MODEL, _MMS_META_BASE
    before = _cuda_mem_gb()[1]
    try:
        _WHISPERX_MODEL = None
        _WHISPERX_DEVICE = None
        _WHISPERX_DIR = None
        _ALIGN_CACHE.clear()
        _MMS_MODEL = None
        _MMS_META_BASE = None
    except Exception:
        pass
    try:
        from cutora.asr.clip_worker import close_all_asr_clients
        close_all_asr_clients()
    except Exception:
        pass
    try:
        # Q152-D：AI 抠像的 SAM 2.1 也一起卸
        from cutora.fx.segment import release_sam2_models
        release_sam2_models()
    except Exception:
        pass
    try:
        # Q152-E：AI 跟踪的 TAPNext++ 也一起卸
        from cutora.fx.tracking import release_models as _release_track
        _release_track()
    except Exception:
        pass
    trim_gpu_cache("模型卸载")
    after = _cuda_mem_gb()[1]
    return max(0.0, before - after)

def _sync_device_globals():
    """把 config 模块里的 DEVICE / COMPUTE_TYPE / LANGUAGE 同步到本模块。

    [v6.1 修复] 上面那行 `from config import DEVICE, COMPUTE_TYPE, ...` 是**值拷贝**。
    此前 toggle_gpu 里写 `config.DEVICE = "cpu"` 只改了 config 模块的属性，
    本模块里 get_whisperx_model()、whisperx.align(..., DEVICE, ...) 用的仍然是
    导入那一刻的旧值 —— GPU 开关对 AI 推理半失效、对导出编码器全生效，
    行为完全不可预测。

    现在所有修改 config.DEVICE 的地方都必须紧接着调用本函数。
    模型缓存本身会检查 _WHISPERX_DEVICE == DEVICE，设备变了会自动重新加载，
    所以切换是安全的。
    """
    global DEVICE, COMPUTE_TYPE, LANGUAGE
    try:
        from cutora.core import config as _cfg
        DEVICE = _cfg.DEVICE
        COMPUTE_TYPE = _cfg.COMPUTE_TYPE
        LANGUAGE = _cfg.LANGUAGE
    except Exception as e:
        from cutora.core.log import get_logger
        get_logger("cutora.asr.engine").warning(
            "[Config] 同步设备设置失败（保持原值）: %s", e)


def whisperx_vad_model():
    """faster-whisper bundled Silero VAD (MIT). Never pyannote."""
    from cutora.asr.vad_silero import make_faster_whisper_vad
    return make_faster_whisper_vad()


def runtime_model_table():
    """Models the recognition path actually loads (or explicitly does not)."""
    return [
        {
            "role": "asr",
            "repo": "Systran/faster-whisper-* (local asr_* dir)",
            "license": "MIT",
            "gated": False,
            "loaded_by_default": True,
        },
        {
            "role": "vad",
            "repo": "faster-whisper/assets/silero_vad_v6.onnx",
            "source": "snakers4/silero-vad",
            "license": "MIT",
            "gated": False,
            "loaded_by_default": True,
        },
        {
            "role": "align-en",
            "repo": "local torchaudio WAV2VEC2_ASR_LARGE_LV60K_960H",
            "license": "Apache-2.0",
            "gated": False,
            "loaded_by_default": True,
        },
        {
            "role": "align-it",
            "repo": "local torchaudio VOXPOPULI_ASR_BASE_10K_IT",
            "license": "CC-BY-NC-4.0",
            "license_url": "https://huggingface.co/facebook/wav2vec2-base-10k-voxpopuli-ft-it",
            "gated": False,
            "loaded_by_default": True,
        },
        {
            "role": "vad-pyannote",
            "repo": "pyannote/segmentation-3.0",
            "license": "other",
            "gated": True,
            "loaded_by_default": False,
        },
    ]


# ── Q133：whisperx 自己解码音频时会开 ffmpeg 黑框 ────────────────────────
# 模型一加载，whisperx.asr / alignment / … 就都已导入；这时把它们模块里
# 绑定的 load_audio 换成 cutora.asr.audio_io 的不开窗口版本（逐字节等价）。
def _no_window_after(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            try:
                from cutora.asr.audio_io import patch_whisperx
                patch_whisperx()
            except Exception:
                pass
    return wrapper


get_whisperx_model = _no_window_after(get_whisperx_model)
get_align_model = _no_window_after(get_align_model)
