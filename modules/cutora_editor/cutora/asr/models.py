# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""ASR / align / VAD model catalog and disk status."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from cutora.core.licenses import ALLOWED_MODEL_LICENSES, is_license_allowed
from cutora.core.log import get_logger
from cutora.core.paths import MODELS, RESOURCES, models_root

logger = get_logger("cutora.asr.models")

HF_OFFICIAL = "https://huggingface.co"
ALLOWED_LICENSES = ALLOWED_MODEL_LICENSES

_CATALOG: list[dict[str, Any]] | None = None


def models_json_path() -> str:
    return os.path.join(RESOURCES, "data", "models.json")


def align_json_path() -> str:
    return os.path.join(RESOURCES, "data", "align_models.json")


def reset_catalog() -> None:
    global _CATALOG
    _CATALOG = None


def models_dir() -> str:
    return models_root()


def dir_is_writable(path: str) -> bool:
    """True when ``path`` exists as a directory and we can create a temp file."""
    if not path or not os.path.isdir(path):
        return False
    probe = os.path.join(path, ".cutora_write_probe")
    try:
        with open(probe, "wb") as fh:
            fh.write(b"ok")
        os.remove(probe)
        return True
    except OSError:
        try:
            if os.path.isfile(probe):
                os.remove(probe)
        except OSError:
            pass
        return False


def validate_models_dir(path: str) -> str:
    """Create the directory if needed and require it to be writable."""
    raw = os.path.abspath((path or "").strip())
    if not raw:
        raise ValueError("模型目录不能为空")
    if os.path.isfile(raw):
        raise ValueError("模型目录不能是文件: %s" % raw)
    try:
        os.makedirs(raw, exist_ok=True)
    except OSError as exc:
        raise ValueError("无法创建模型目录: %s" % exc) from exc
    if not dir_is_writable(raw):
        raise ValueError("模型目录不可写: %s" % raw)
    return raw


def apply_models_dir(path: str) -> str:
    """Validate a writable models directory and persist ``asr.models_dir``.

    Does not copy existing weights. Returns the absolute path.
    """
    raw = validate_models_dir(path)
    from cutora.core.history import _load_app_settings, _save_app_settings
    settings = _load_app_settings() or {}
    if os.path.abspath(raw) == os.path.abspath(MODELS):
        settings.pop("asr.models_dir", None)
    else:
        settings["asr.models_dir"] = raw
    _save_app_settings(settings)
    return raw


def _normalize_catalog_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    # Gated weights stay off. License no longer forces enabled=false
    # (internal NC use is allowed; quality decides which model ships).
    if item.get("gated"):
        item["enabled"] = False
    if item.get("kind") == "align" and item.get("language"):
        from cutora.asr.languages import get_language
        lang = get_language(str(item.get("language")))
        if not lang or lang.get("line_break") != "space":
            item["enabled"] = False
    return item


def _rows_from_json(path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = []
    for row in list((data or {}).get("models") or []):
        if isinstance(row, dict) and row.get("id"):
            rows.append(_normalize_catalog_row(row))
    return rows


def catalog() -> list[dict[str, Any]]:
    global _CATALOG
    if _CATALOG is not None:
        return list(_CATALOG)
    rows = []
    seen: set[str] = set()
    for path in (models_json_path(), align_json_path()):
        for item in _rows_from_json(path):
            mid = str(item.get("id"))
            if mid in seen:
                continue
            seen.add(mid)
            rows.append(item)
    _CATALOG = rows
    return list(_CATALOG)


def get_model(model_id: str) -> dict[str, Any] | None:
    want = (model_id or "").strip()
    for row in catalog():
        if row.get("id") == want:
            return dict(row)
    return None


def hf_base_url() -> str:
    raw = (os.environ.get("HF_ENDPOINT") or "").strip().rstrip("/")
    if raw and raw.lower() not in (HF_OFFICIAL, HF_OFFICIAL + "/"):
        logger.warning("忽略非官方 HF_ENDPOINT=%s，仍使用 huggingface.co", raw)
        return HF_OFFICIAL
    return HF_OFFICIAL


def hf_file_url(repo: str, filename: str, revision: str = "main") -> str:
    rev = revision or "main"
    return "%s/%s/resolve/%s/%s" % (hf_base_url(), repo, rev, filename)


def weight_path(row: dict[str, Any]) -> str:
    folder = os.path.join(models_dir(), str(row.get("dirname") or row["id"]))
    name = str(row.get("weight_file") or "model.bin")
    return os.path.join(folder, name)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


_SHA_CACHE: dict[tuple, str] = {}
_SHA_DISK = {"loaded": False, "rows": {}}


def _sha_disk_path() -> str:
    from cutora.core.paths import user_cache_dir
    return os.path.join(user_cache_dir(), "model_sha256.json")


def _sha_disk_key(key: tuple) -> str:
    return "%s|%d|%d" % key


def _sha_disk_get(key: tuple) -> str | None:
    if not _SHA_DISK["loaded"]:
        _SHA_DISK["loaded"] = True
        try:
            import json
            with open(_sha_disk_path(), encoding="utf-8") as fh:
                rows = json.load(fh)
            if isinstance(rows, dict):
                _SHA_DISK["rows"] = {str(k): str(v) for k, v in rows.items()}
        except Exception:
            pass
    return _SHA_DISK["rows"].get(_sha_disk_key(key))


def _sha_disk_put(key: tuple, digest: str) -> None:
    try:
        import json
        _SHA_DISK["rows"][_sha_disk_key(key)] = digest
        path = _sha_disk_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_SHA_DISK["rows"], fh, ensure_ascii=False, indent=0)
        os.replace(tmp, path)
    except Exception:
        pass


def _sha256_cached(path: str) -> str:
    """同一个文件（路径 + 大小 + 修改时间都没变）只算一次 sha256。

    Q152-D/E：检查器每次刷新都会查模型状态，几个 GB 的权重每次重算要好几秒。
    Q172：以前只记在内存里 —— 每次开软件后第一次打开「批量」对话框都要把几个 GB 的权重重算一遍，
    界面线程卡 14 秒（Luca 的日志）。现在记到磁盘缓存，文件不变就永远不重算；大小或修改时间变了才重算。"""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    hit = _SHA_CACHE.get(key)
    if hit is None:
        hit = _sha_disk_get(key)
        if hit is None:
            hit = sha256_file(path)
            _sha_disk_put(key, hit)
        _SHA_CACHE[key] = hit
    return hit


def _sha_known(path: str) -> str | None:
    """不算，只查：内存 / 磁盘缓存里有没有这个文件（路径 + 大小 + 修改时间）的 sha256。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    hit = _SHA_CACHE.get(key)
    if hit is None:
        hit = _sha_disk_get(key)
        if hit is not None:
            _SHA_CACHE[key] = hit
    return hit


_WARM = {"thread": None, "paths": [], "lock": None}


def warm_sha_async(path: str) -> None:
    """后台把这个文件的 sha256 算好记进缓存（一条线程依次算，不并发读盘）。"""
    import threading
    if _WARM["lock"] is None:
        _WARM["lock"] = threading.Lock()
    with _WARM["lock"]:
        if path not in _WARM["paths"]:
            _WARM["paths"].append(path)
        th = _WARM["thread"]
        if th is not None and th.is_alive():
            return

        def _run():
            while True:
                with _WARM["lock"]:
                    if not _WARM["paths"]:
                        _WARM["thread"] = None
                        return
                    nxt = _WARM["paths"].pop(0)
                try:
                    _sha256_cached(nxt)
                except Exception:
                    pass

        th = threading.Thread(target=_run, name="cutora-model-sha", daemon=True)
        _WARM["thread"] = th
        th.start()


def verify_weight(path: str, expected: str | None, fast: bool = False) -> str:
    """fast=True（界面列表用）：缓存里没有 sha256 就先按「在」算，后台去算；不在界面线程读几个 GB。
    真正要用模型（转写 / 对齐 / 打包）时走 fast=False，照常严格校验。"""
    if not path or not os.path.isfile(path):
        return "missing"
    want = (expected or "").strip()
    if not want:
        return "ready"
    if fast:
        got = _sha_known(path)
        if got is None:
            warm_sha_async(path)
            return "ready"
    else:
        got = _sha256_cached(path)
    if got.lower() != want.lower():
        return "checksum_failed"
    return "ready"


def iter_files(row: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in list(row.get("files") or []):
        if isinstance(item, dict) and item.get("filename"):
            out.append(dict(item))
        elif item:
            out.append({"filename": str(item)})
    if not out:
        weight = str(row.get("weight_file") or "model.bin")
        out.append({"filename": weight, "sha256": row.get("sha256")})
    return out


def model_folder(row: dict[str, Any]) -> str:
    return os.path.join(models_dir(), str(row.get("dirname") or row["id"]))


def verify_model(row: dict[str, Any], fast: bool = False) -> str:
    if row.get("bundled"):
        return "ready"
    folder = model_folder(row)
    last = "missing"
    for item in iter_files(row):
        path = os.path.join(folder, str(item["filename"]))
        last = verify_weight(path, item.get("sha256") or None, fast=fast)
        if last != "ready":
            return last
    return last


def align_row_for_language(code: str | None) -> dict[str, Any] | None:
    want = (code or "").strip().lower()
    if not want:
        return None
    for row in catalog():
        if row.get("kind") == "align" and str(row.get("language") or "").lower() == want:
            return dict(row)
    return None


def status_row(row: dict[str, Any], fast: bool = False) -> dict[str, Any]:
    out = dict(row)
    out["path"] = weight_path(row)
    out["status"] = verify_model(row, fast=fast)
    out["license_ok"] = is_license_allowed(row.get("license"))
    return out


def list_status(fast: bool = False) -> list[dict[str, Any]]:
    return [status_row(r, fast=fast) for r in catalog()]


def recommend_asr(vram_gb: float | None) -> str:
    return "asr-small"


_ASR_ALIASES = {
    "small": "asr-small",
    "turbo": "asr-large-v3-turbo",
    "large-v3-turbo": "asr-large-v3-turbo",
}


def resolve_asr_model_id(raw) -> str | None:
    """Map subtitle.auto model=small / asr-small / … to a catalog id."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in _ASR_ALIASES:
        return _ASR_ALIASES[text]
    if text.startswith("asr-"):
        return text
    return "asr-" + text


# Shown when a language has no local aligner. Must stay this exact string —
# testers (and the UI) look for it. Never silent-download from Hugging Face.
ALIGN_NOT_BUNDLED_NOTE = "该语种对齐模型未内置"
ALIGN_IT_SENTENCE_NOTE = "意大利语暂只做句级时间戳，不做词级对齐"

# Test fixture dir; never ships. Catalog may not even list it.
PACK_FIXTURE_DIRNAMES = frozenset({"asr_fake"})
# Local torchaudio pair lives in one folder; listed in models.json.
FROZEN_ALIGN_IDS = ("align-en", "align-it")
COMPLETE_LAYOUT_SCRIPTS = frozenset({"latin", "cyrillic", "greek"})


def is_pack_fixture(row: dict[str, Any]) -> bool:
    dirname = str(row.get("dirname") or "")
    mid = str(row.get("id") or "")
    if dirname in PACK_FIXTURE_DIRNAMES:
        return True
    if mid in ("asr-fake", "asr_fake"):
        return True
    return False


def is_venv_bundled(row: dict[str, Any]) -> bool:
    """Weights live inside a wheel (Silero VAD), not ``runtime/models``."""
    return bool(row.get("bundled"))


def packable_model_rows() -> list[dict[str, Any]]:
    """Catalog rows whose hashed files exist on disk and must ship.

    Source of truth is ``catalog()`` (models.json + align_models.json).
    Never hardcode ids. Skip test fixtures, venv-bundled rows (Silero),
    gated/empty rows, and catalog entries whose files are not on disk
    (large-v3, align-fr, …). Those last ones are optional downloads;
    the app must say ALIGN_NOT_BUNDLED_NOTE instead of fetching them.
    """
    rows = []
    seen: set[str] = set()
    for row in catalog():
        if row.get("enabled") is False:
            continue
        if is_pack_fixture(row):
            continue
        if is_venv_bundled(row):
            continue
        hashed = [
            item for item in iter_files(row)
            if str(item.get("sha256") or "").strip()
        ]
        if not hashed:
            continue
        if verify_model(row) != "ready":
            continue
        mid = str(row.get("id"))
        if mid in seen:
            continue
        seen.add(mid)
        rows.append(dict(row))
    return rows


def required_pack_dirnames() -> list[str]:
    """Dirnames ``model_paths.json`` points at. Pack fails if any is missing."""
    from cutora.core.paths import get_model_paths
    paths = get_model_paths()
    out: list[str] = []
    for key in ("asr_path", "align_path"):
        raw = paths.get(key)
        if not raw:
            continue
        leaf = os.path.basename(str(raw).rstrip("\\/"))
        if leaf and leaf not in out:
            out.append(leaf)
    return out


def pack_model_directories() -> list[str]:
    """Unique ``runtime/models/<dirname>`` folders to copy, in stable order."""
    seen: set[str] = set()
    out: list[str] = []
    for row in packable_model_rows():
        leaf = str(row.get("dirname") or row.get("id") or "").strip()
        if not leaf or leaf in PACK_FIXTURE_DIRNAMES or leaf in seen:
            continue
        seen.add(leaf)
        out.append(leaf)
    for leaf in required_pack_dirnames():
        if leaf not in seen:
            out.append(leaf)
            seen.add(leaf)
    return out


def complete_layout_languages() -> list[dict[str, Any]]:
    """Languages T04L L4 would mark ``subtitle_tier=complete``.

    ``subtitle_tier`` is not in languages.json yet. Rule matches L4:
    space line-break + latin/cyrillic/greek, not experimental.
    """
    from cutora.asr.languages import load_catalog
    out = []
    for row in load_catalog():
        if row.get("line_break") != "space":
            continue
        if row.get("script") not in COMPLETE_LAYOUT_SCRIPTS:
            continue
        if row.get("experimental"):
            continue
        out.append(dict(row))
    return out


_MMS_ON_DISK: bool | None = None


def mms_aligner_on_disk() -> bool:
    """True when the enabled ``align-mms`` weight file is present.

    The language table calls this once per row. Hashing the 1.2 GB weight
    here would freeze the window, so this checks the file is there and its
    size matches the catalog. ``verify_model`` still does the full checksum
    on the model page.
    """
    global _MMS_ON_DISK
    if _MMS_ON_DISK is not None:
        return _MMS_ON_DISK
    row = get_model("align-mms")
    if not row or not row.get("enabled"):
        _MMS_ON_DISK = False
        return False
    weight_name = str(row.get("weight_file") or "model.safetensors")
    path = os.path.join(model_folder(row), weight_name)
    expect = None
    for item in row.get("files") or []:
        if isinstance(item, dict) and item.get("filename") == weight_name:
            expect = item.get("size")
            break
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    ok = size > 0 and (expect is None or int(size) == int(expect))
    _MMS_ON_DISK = bool(ok)
    return _MMS_ON_DISK


def generic_aligner_available() -> bool:
    """Whether the MMS aligner can time words that have no dedicated model.

    True only when ``align-mms`` is on disk and ``uroman`` imports. Callers
    must not copy the check.
    """
    if not mms_aligner_on_disk():
        return False
    from cutora.asr.romanize import uroman_available
    return uroman_available()


def align_tier(code: str) -> str:
    """``exact``, ``fallback``, or ``none``.

    Unknown codes return ``none``. They are not in the transcribe catalog
    and have no aligner, and the language table must keep working.

    ``exact`` — a dedicated aligner is enabled (frozen en/it, or an enabled
    row in align_models.json).
    ``fallback`` — the code is in the transcribe catalog, it has no dedicated
    aligner, and ``generic_aligner_available()`` is true.
    ``none`` — words can be transcribed, but nothing can time them, so
    stitch cannot run.
    """
    raw = (code or "").strip().lower()
    if _dedicated_aligner_enabled(raw):
        return "exact"
    if not raw or raw == "auto":
        return "none"
    from cutora.asr.languages import catalog_by_code
    if raw not in catalog_by_code():
        return "none"
    if generic_aligner_available():
        return "fallback"
    return "none"


def _dedicated_aligner_enabled(code: str) -> bool:
    if not code or code == "auto":
        return False
    from cutora.asr.engine import ALIGN_MODEL_BY_LANG
    if code in ALIGN_MODEL_BY_LANG:
        return True
    row = align_row_for_language(code)
    return bool(row and row.get("enabled"))


def align_model_label(code: str) -> str:
    """Human log label for the dedicated aligner, or ``无``."""
    raw = (code or "").strip().lower()
    from cutora.asr.engine import ALIGN_MODEL_BY_LANG
    if raw in ALIGN_MODEL_BY_LANG:
        frozen = ALIGN_MODEL_BY_LANG.get(raw)
        if raw == "en":
            return "align-en / %s" % (frozen or "WAV2VEC2_ASR_LARGE_LV60K_960H")
        if raw == "it":
            name = frozen or "VOXPOPULI"
            return "align-it / %s" % name
    row = align_row_for_language(raw)
    if row and row.get("enabled"):
        return "%s / %s" % (row.get("id") or raw, row.get("repo") or "")
    if align_tier(raw) == "fallback":
        mms = get_model("align-mms") or {}
        return "align-mms / %s" % (mms.get("repo") or "mms-300m-1130-forced-aligner")
    return "无"


def _script_has_covering_font(script: str) -> bool:
    key = (script or "").strip().lower()
    if not key:
        return False
    from cutora.subtitle.font_coverage import load_coverage
    cov = load_coverage().get("coverage") or {}
    for row in cov.values():
        if isinstance(row, dict) and row.get(key):
            return True
    return False


def language_capability(code: str) -> dict:
    """What this language can do. The only source for UI, reports, and tests.

    ``transcribe`` is true only for a languages.json row.
    ``font_ok`` is true when font_script_coverage.json marks at least one
    family as covering that language's script.
    """
    raw = (code or "").strip().lower()
    from cutora.asr.languages import catalog_by_code
    row = catalog_by_code().get(raw) if raw and raw != "auto" else None
    script = str((row or {}).get("script") or "")
    return {
        "code": raw,
        "name_zh": str((row or {}).get("name_zh") or raw),
        "transcribe": row is not None,
        "align_tier": align_tier(raw),
        "subtitle_tier": str((row or {}).get("subtitle_tier") or ""),
        "font_ok": _script_has_covering_font(script) if row else False,
    }


def align_is_on_disk(code: str) -> bool:
    """True when this language's aligner is present locally (no HF fetch)."""
    want = (code or "").strip().lower()
    if want in ("en", "it"):
        row = get_model("align-%s" % want)
        return bool(row) and verify_model(row) == "ready"
    row = align_row_for_language(want)
    if not row or not row.get("enabled"):
        return False
    return verify_model(row) == "ready"


def align_plan_for_complete_languages() -> list[dict[str, Any]]:
    """Per complete-layout language: which aligner, whether it ships."""
    plan = []
    for lang in complete_layout_languages():
        code = lang["code"]
        row = get_model("align-%s" % code) or align_row_for_language(code)
        bundled = align_is_on_disk(code)
        plan.append({
            "code": code,
            "name_zh": lang.get("name_zh"),
            "script": lang.get("script"),
            "align_id": (row or {}).get("id"),
            "repo": (row or {}).get("repo"),
            "dirname": (row or {}).get("dirname"),
            "bundled": bundled,
        })
    return plan

