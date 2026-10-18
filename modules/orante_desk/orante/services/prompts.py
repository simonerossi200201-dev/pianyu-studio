# -*- coding: utf-8 -*-
"""四块提示词：默认值 + 用户覆盖 + 成品提示词拼装。

默认值优先读 orante/legacy/prompt_defaults.json（不依赖老目录）。
用户改动存 settings 的 prompts.*，reset_block 即可恢复默认。
成品提示词走 legacy.flow_shots.build_prompt，卡库用当前 camera/tail 覆盖后再传进去。
"""
from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path

from .. import paths
from ..errors import OranteError
from ..models import Manifest
from . import coverage as covlib

paths.add_legacy_to_sys_path()
import flow_shots  # type: ignore  # noqa: E402

READABLE = ("desc", "camera", "tail", "problem", "tail_notes", "versions",
            "expand", "refill_clause", "bridge", "bridge_single", "upscale",
            "chain", "migrate_notice",
            "silent")
EDITABLE = ("desc", "camera", "tail", "problem", "expand", "refill_clause",
            "bridge", "bridge_single", "upscale", "chain",
            "silent")

# 63：情绪预设。live 键是 flow_shots 真正拼进成品词的那几条；
# 「温暖 / 激昂」两套原文放在 tail.情绪预设 里，切换时整组覆盖 live。
EMOTION_LIVE_KEYS = (
    "情绪", "情绪_闭眼", "情绪_睁眼",
    "情绪基调_闭眼", "情绪基调_睁眼",
    "情绪推进", "情绪推进_睁眼",
)
EMOTION_PRESET_NAMES = ("温暖", "激昂")
EMOTION_PRESET_DEFAULT = "温暖"
_PAUSE_HOLD = "⟦ORANTE_PAUSE⟧"
_WS = re.compile(r"\s+")
_MISSING = object()
_DEFAULTS_CACHE: dict | None = None
_MIGRATE_LOCK = threading.Lock()

# 补位时插在 head 之后的那一句。清空则和普通扩图一样。
REFILL_CLAUSE_DEFAULT = "并且构图必须和上传图像明显不同，"
# 提升画质：拿这张图自己当参考，原样重绘一遍。不走覆盖库。
UPSCALE_DEFAULT = (
    "以上传的这张图为唯一参考，原样重绘一遍：同一个人物、同一张脸、同一个姿态和手势、"
    "同一个构图和取景、同一个背景和光线、同一套衣着。不要增减任何元素，不要改变镜头角度，"
    "不要重新构图。只把清晰度和细节做到最高：皮肤、发丝、布料纹理、背景细节都要锐利干净。\n"
    "Photorealistic, maximum detail and sharpness, identical composition to the reference."
)
# 提示词格式版本：v6 = expand 从 24 行字符串改成机位×动作 JSON
PROMPT_FORMAT_VERSION = 6

_SCAN_SYSTEM = "你只输出 JSON 数组，不要输出任何解释、markdown 或代码围栏。"
_SCAN_PROMPT = (
    "下面这段文案会作为视频台词交给视频生成模型。"
    "模型有时会把要求观众/人物停顿、等待、静默、输入某词、评论某词、点赞、分享"
    "等句子当成拍摄指令，而不是念出来的台词。\n\n"
    "请从文案里找出所有这类原句（语言不限）。只输出一个 JSON 数组，"
    "数组元素是原句字符串，必须一字不改。没有就输出 []。\n\n"
    "文案：\n"
)


def _cfg_get(key: str, default=None):
    try:
        from .. import config
        return config.get(key, default)
    except Exception:
        return default


def _cfg_set(key: str, value) -> None:
    from .. import config
    config.set(key, value)
    bump_rev(key)


def _cfg_reset(key: str) -> None:
    from .. import config
    config.reset(key)
    bump_rev(key)


# --- 规格 93：提示词世代 ---------------------------------------------------
# 提示词（积木开关 / 极简模式 / 各个可编辑块 / 套装 / 语种包）一改，
# 已经拼好但还没出片的段必须重拼。段上记的是"我是按第几代拼的"，
# 和开跑那一刻的世代对不上就作废重拼——见 services/generate.py::_final_prompt。
REV_KEY = "prompts.rev"


def rev() -> int:
    try:
        return int(_cfg_get(REV_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0


def bump_rev(hint: str = "") -> int:
    """提示词变了就 +1。hint 是哪个键引起的（rev 自己不算）。"""
    if hint == REV_KEY:
        return rev()
    n = rev() + 1
    try:
        from .. import config
        config.set(REV_KEY, n, touch_rev=False)
    except Exception:  # noqa: BLE001
        return n
    return n


def _cfg_has(key: str) -> bool:
    try:
        from .. import db
        return db.one("SELECT value FROM settings WHERE key=?", (key,)) is not None
    except Exception:
        return False


def _nospace(s: str) -> str:
    return _WS.sub("", s or "")


def defaults() -> dict:
    """四块默认值。优先读打包进来的 prompt_defaults.json。"""
    global _DEFAULTS_CACHE
    if _DEFAULTS_CACHE is None:
        p = paths.LEGACY / "prompt_defaults.json"
        if not p.is_file():
            raise OranteError(
                "找不到默认提示词",
                f"没有 {p.name}，在开发机上跑 tools/extract_prompt_defaults.py。")
        try:
            _DEFAULTS_CACHE = json.loads(p.read_text(encoding="utf-8"))
            if "silent" not in _DEFAULTS_CACHE:
                from . import simple86 as _s86
                _DEFAULTS_CACHE["silent"] = _s86.SILENT_DEFAULT
        except Exception as e:
            raise OranteError("默认提示词文件坏了", str(e))
        # 扩图块改读 coverage_library.json（机位 × 动作），不再用 24 行字符串
        _DEFAULTS_CACHE["expand"] = covlib.default_lib()
        _DEFAULTS_CACHE["refill_clause"] = REFILL_CLAUSE_DEFAULT
        _DEFAULTS_CACHE["upscale"] = UPSCALE_DEFAULT
        if not _DEFAULTS_CACHE.get("bridge"):
            from .pipeline import BRIDGE_PROMPT_DEFAULT
            _DEFAULTS_CACHE["bridge"] = BRIDGE_PROMPT_DEFAULT
        if not _DEFAULTS_CACHE.get("bridge_single"):
            from .pipeline import BRIDGE_PROMPT_SINGLE
            _DEFAULTS_CACHE["bridge_single"] = BRIDGE_PROMPT_SINGLE
        if not _DEFAULTS_CACHE.get("chain"):
            from .pipeline import CHAIN_PROMPT_DEFAULT
            _DEFAULTS_CACHE["chain"] = CHAIN_PROMPT_DEFAULT
    return _DEFAULTS_CACHE


def _check_name(name: str, write: bool = False) -> None:
    ok = EDITABLE if write else READABLE
    if name not in ok:
        raise OranteError(
            f"没有叫 {name} 的提示词块",
            f"{'可改' if write else '可读'}的块：{', '.join(ok)}")


def _notice_list() -> list:
    raw = _cfg_get("prompts.migrate_notice", _MISSING)
    if raw is _MISSING or raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _set_notices(items: list) -> None:
    if items:
        _cfg_set("prompts.migrate_notice", items)
    else:
        _cfg_reset("prompts.migrate_notice")


def _push_notice(item: dict) -> None:
    notices = _notice_list()
    key = (item.get("kind"), item.get("name"), item.get("bak_key"))
    notices = [n for n in notices if (n.get("kind"), n.get("name"), n.get("bak_key")) != key]
    notices.append(item)
    _set_notices(notices)


def _migrate_format() -> None:
    """格式版本升级：先把用户的值抄到 bak.v<旧版本>，再重置，并写横幅 notice。"""
    try:
        cur = int(_cfg_get("prompts.format_version", 0) or 0)
    except (TypeError, ValueError):
        cur = 0
    if cur >= PROMPT_FORMAT_VERSION:
        return
    labels = {"expand": "扩图提示词", "refill": "补槽位提示词"}
    for name in ("expand", "refill"):
        key = f"prompts.{name}"
        try:
            if not _cfg_has(key):
                continue
            old = _cfg_get(key, _MISSING)
            if old is _MISSING:
                continue
            bak_key = f"prompts.{name}.bak.v{cur}"
            _cfg_set(bak_key, old)
            _cfg_reset(key)
            # 补槽位已经并进扩图，横幅只提示扩图；refill 备份仍留着
            if name == "expand":
                _push_notice({
                    "kind": "format_reset",
                    "name": name,
                    "label": labels.get(name, name),
                    "from_version": cur,
                    "bak_key": bak_key,
                })
        except Exception:
            pass
    try:
        _cfg_set("prompts.format_version", PROMPT_FORMAT_VERSION)
    except Exception:
        pass


def _strip_refill_clause(text: str, clause: str) -> str:
    """逐行去掉补位那一句；注释行不动。"""
    if not clause or not isinstance(text, str):
        return text if isinstance(text, str) else ""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s and not s.startswith("#") and clause in ln:
            ln = ln.replace(clause, "")
        out.append(ln)
    result = "\n".join(out)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def _merge_refill() -> None:
    """补槽位提示词并进扩图：只做一次，不许把用户已有的东西弄丢。"""
    if _cfg_get("prompts.refill_merged"):
        return
    has_expand = _cfg_has("prompts.expand")
    has_refill = _cfg_has("prompts.refill")
    try:
        if has_refill and not has_expand:
            raw = _cfg_get("prompts.refill")
            if isinstance(raw, str) and raw.strip():
                clause = REFILL_CLAUSE_DEFAULT
                moved = _strip_refill_clause(raw, clause)
                _cfg_set("prompts.expand", moved)
                if clause in raw:
                    _cfg_set("prompts.refill_clause", clause)
            _cfg_reset("prompts.refill")
        elif has_refill and has_expand:
            raw = _cfg_get("prompts.refill")
            _cfg_set("prompts.refill.bak", raw)
            _cfg_reset("prompts.refill")
            _push_notice({
                "kind": "refill_merged",
                "name": "refill",
                "label": "补槽位提示词",
                "bak_key": "prompts.refill.bak",
            })
        elif has_refill:
            _cfg_reset("prompts.refill")
        _cfg_set("prompts.refill_merged", True)
    except Exception:
        pass


def _looks_like_v1_lib(obj) -> bool:
    """v1 机位库：还在用画面坐标系，head 没锁「从不看镜头」。"""
    if not covlib.is_lib(obj):
        return False
    head = str(obj.get("head") or "")
    return "从不看镜头" not in head


def _migrate_stage_lib() -> None:
    """v1 覆盖库换成舞台坐标系 v2。已经是 v2 或用户自己改过 v2 的不动。"""
    if _cfg_get("prompts.cov_v2_migrated"):
        return
    try:
        if not _cfg_has("prompts.expand"):
            _cfg_set("prompts.cov_v2_migrated", True)
            return
        old = _cfg_get("prompts.expand", _MISSING)
        if old is _MISSING or not _looks_like_v1_lib(old):
            _cfg_set("prompts.cov_v2_migrated", True)
            return
        _cfg_set("prompts.expand.bak.v1lib", old)
        _cfg_reset("prompts.expand")
        _push_notice({
            "kind": "format_reset",
            "name": "expand",
            "label": "扩图覆盖库",
            "from_version": "v1lib",
            "bak_key": "prompts.expand.bak.v1lib",
        })
        _cfg_set("prompts.cov_v2_migrated", True)
    except Exception:
        pass


def _packed_emotion_presets() -> dict:
    """打包 JSON 里的两套情绪原文。defaults() 已缓存，这里只读。"""
    d = defaults().get("tail") or {}
    p = d.get("情绪预设")
    return p if isinstance(p, dict) else {}


def _guess_emotion_preset(tail: dict) -> str:
    """看 live 睁眼句像哪一套。对不上就当温暖（新默认）。"""
    eye = str(tail.get("情绪_睁眼") or "")
    if "直视镜头" in eye or "powerful voice" in eye or "emphatic gestures" in eye:
        return "激昂"
    return EMOTION_PRESET_DEFAULT


def _ensure_emotion_presets(tail: dict) -> dict:
    """补齐 情绪预设 结构；已有的槽不覆盖。"""
    packed = _packed_emotion_presets()
    presets = tail.get("情绪预设")
    if not isinstance(presets, dict):
        presets = {}
    out = dict(presets)
    for name in EMOTION_PRESET_NAMES:
        slot = out.get(name)
        if not isinstance(slot, dict) or not slot:
            src = packed.get(name)
            out[name] = copy.deepcopy(src) if isinstance(src, dict) else {}
        else:
            # 槽在但缺某条 live 键：用打包的补上，用户写过的留下
            packed_slot = packed.get(name) if isinstance(packed.get(name), dict) else {}
            filled = dict(slot)
            for k in EMOTION_LIVE_KEYS:
                if k not in filled and k in packed_slot:
                    filled[k] = copy.deepcopy(packed_slot[k])
            out[name] = filled
    cur = out.get("当前")
    if cur not in EMOTION_PRESET_NAMES:
        out["当前"] = _guess_emotion_preset(tail)
    tail["情绪预设"] = out
    return tail


def _mirror_live_into_current(tail: dict) -> dict:
    """把手改过的 live 键写回当前预设，切走再切回来不会丢。"""
    _ensure_emotion_presets(tail)
    cur = tail["情绪预设"].get("当前") or EMOTION_PRESET_DEFAULT
    if cur not in EMOTION_PRESET_NAMES:
        cur = EMOTION_PRESET_DEFAULT
        tail["情绪预设"]["当前"] = cur
    slot = dict(tail["情绪预设"].get(cur) or {})
    for k in EMOTION_LIVE_KEYS:
        if k in tail:
            slot[k] = copy.deepcopy(tail[k])
    tail["情绪预设"][cur] = slot
    return tail


def _apply_slot_to_live(tail: dict, name: str) -> dict:
    _ensure_emotion_presets(tail)
    slot = tail["情绪预设"].get(name) or {}
    if not isinstance(slot, dict):
        slot = {}
    for k in EMOTION_LIVE_KEYS:
        if k in slot:
            tail[k] = copy.deepcopy(slot[k])
    tail["情绪预设"]["当前"] = name
    return tail


def _stamp_emotion_choice(name: str) -> None:
    """单独记一份，26 号导出 prompts.* 时一定带上当前选的是哪套。"""
    if name in EMOTION_PRESET_NAMES:
        try:
            _cfg_set("prompts.emotion_preset", name)
        except Exception:
            pass


def _prepare_tail_for_save(value: dict) -> dict:
    tail = copy.deepcopy(value)
    _ensure_emotion_presets(tail)
    _mirror_live_into_current(tail)
    cur = (tail.get("情绪预设") or {}).get("当前")
    if cur in EMOTION_PRESET_NAMES:
        _stamp_emotion_choice(cur)
    return tail


def _looks_like_old_fiery(tail: dict) -> bool:
    eye = str(tail.get("情绪_睁眼") or "")
    return ("直视镜头" in eye) or ("下颌用力" in eye and "目光炯炯" in eye)


def _migrate_emotion_warm() -> None:
    """用户 settings 里还是改动前那套严厉结语时：原样存进「激昂」，live 换成「温暖」。"""
    if _cfg_get("prompts.emotion_warm_migrated"):
        return
    try:
        if not _cfg_has("prompts.tail"):
            _cfg_set("prompts.emotion_warm_migrated", True)
            return
        raw = _cfg_get("prompts.tail", _MISSING)
        if raw is _MISSING or not isinstance(raw, dict):
            _cfg_set("prompts.emotion_warm_migrated", True)
            return
        tail = copy.deepcopy(raw)
        _ensure_emotion_presets(tail)
        if _looks_like_old_fiery(tail) and "从不看镜头" not in str(tail.get("情绪_睁眼") or ""):
            old_slot = {k: copy.deepcopy(tail[k]) for k in EMOTION_LIVE_KEYS if k in tail}
            if old_slot:
                tail["情绪预设"]["激昂"] = old_slot
            _apply_slot_to_live(tail, EMOTION_PRESET_DEFAULT)
        else:
            cur = _guess_emotion_preset(tail)
            tail["情绪预设"]["当前"] = cur
            _mirror_live_into_current(tail)
        _cfg_set("prompts.tail", tail)
        _cfg_set("prompts.emotion_warm_migrated", True)
    except Exception:
        pass


def apply_emotion_preset(name: str) -> dict:
    """切到「温暖」或「激昂」。先把当前手改写回旧槽，再把目标槽铺到 live 键。"""
    name = (name or "").strip()
    if name not in EMOTION_PRESET_NAMES:
        raise OranteError(
            f"没有叫 {name} 的情绪预设",
            "可选：温暖（默认）/ 激昂（改动前那套）。")
    tail = get_block("tail")
    if not isinstance(tail, dict):
        raise OranteError("固定结语不是对象")
    _ensure_emotion_presets(tail)
    _mirror_live_into_current(tail)
    _apply_slot_to_live(tail, name)
    set_block("tail", tail)
    return get_block("tail")


def emotion_preset_name() -> str:
    tail = get_block("tail")
    if not isinstance(tail, dict):
        return EMOTION_PRESET_DEFAULT
    cur = ((tail.get("情绪预设") or {}) if isinstance(tail.get("情绪预设"), dict) else {}).get("当前")
    return cur if cur in EMOTION_PRESET_NAMES else _guess_emotion_preset(tail)


def _migrate_once() -> None:
    """格式升级备份 + 补槽位并进扩图 + v1 机位库换成舞台坐标系 + 63 情绪预设。"""
    with _MIGRATE_LOCK:
        _migrate_format()
        _merge_refill()
        _migrate_stage_lib()
        _migrate_emotion_warm()


def _expand_head() -> str:
    """当前覆盖库的 head；没有就用打包那份。"""
    try:
        lib = get_block("expand")
        if covlib.is_lib(lib) and lib.get("head"):
            return str(lib.get("head") or "")
    except Exception:
        pass
    try:
        return str(covlib.default_lib().get("head") or "")
    except Exception:
        return ""


def insert_refill_clause(block: str, clause: str) -> str:
    """列表模式：非注释行把 clause 插到 head 之后。

    覆盖库拼装走 coverage.compose；这里留给旧的字符串扩图行。
    用户改过的行不一定还含默认 head：以第一句「保持…，」的逗号为同类位置。
    """
    if not clause or not isinstance(block, str):
        return block if isinstance(block, str) else ""
    head = _expand_head()
    out = []
    for ln in block.splitlines():
        s = ln.lstrip()
        if not s or s.startswith("#"):
            out.append(ln)
            continue
        if head and head in ln:
            idx = ln.find(head)
            out.append(ln[:idx] + head + clause + ln[idx + len(head):])
            continue
        lead = ln[:len(ln) - len(s)]
        if s.startswith("保持"):
            comma = s.find("，")
            if comma >= 0:
                out.append(lead + s[:comma + 1] + clause + s[comma + 1:])
                continue
        out.append(lead + clause + s)
    result = "\n".join(out)
    if block.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def with_refill_clause(block: str) -> str:
    """取当前 refill_clause 插进 expand 正文。空 clause = 原样。"""
    clause = get_block("refill_clause")
    if not isinstance(clause, str):
        clause = REFILL_CLAUSE_DEFAULT
    clause = clause.strip()
    if not clause:
        return block
    return insert_refill_clause(block, clause)


def migrate_notices() -> list:
    """给前端横幅：带上备份正文，方便「查看」。"""
    _migrate_once()
    out = []
    for n in _notice_list():
        item = dict(n)
        bak_key = item.get("bak_key") or ""
        bak = _cfg_get(bak_key, _MISSING) if bak_key else _MISSING
        if bak is _MISSING or bak is None:
            item["bak_text"] = ""
        elif isinstance(bak, str):
            item["bak_text"] = bak
        else:
            try:
                item["bak_text"] = json.dumps(bak, ensure_ascii=False, indent=2)
            except Exception:
                item["bak_text"] = str(bak)
        out.append(item)
    return out


def ack_notice(kind: str | None = None, name: str | None = None) -> None:
    """点「知道了」：清掉对应横幅。都不传就全清。"""
    if not kind and not name:
        _cfg_reset("prompts.migrate_notice")
        return
    left = []
    for n in _notice_list():
        match_kind = (not kind) or n.get("kind") == kind
        match_name = (not name) or n.get("name") == name
        if match_kind and match_name:
            continue
        left.append(n)
    _set_notices(left)


def restore_backup(name: str) -> None:
    """把格式升级备份写回对应块。expand / refill（refill 写回 expand，因为已经合并）。"""
    name = (name or "").strip()
    hit = None
    for n in _notice_list():
        if n.get("name") == name or (name == "expand" and n.get("kind") == "format_reset"
                                     and n.get("name") == "expand"):
            hit = n
            break
    if hit is None:
        raise OranteError("没有可恢复的备份", "横幅已经关掉或备份不在了。")
    bak_key = hit.get("bak_key") or ""
    bak = _cfg_get(bak_key, _MISSING) if bak_key else _MISSING
    if bak is _MISSING or bak is None:
        raise OranteError("备份不在了", bak_key or name)
    target = "expand" if hit.get("name") in ("expand", "refill") else hit.get("name")
    if target not in EDITABLE:
        raise OranteError(f"不能恢复到 {target}")
    if target in ("desc", "refill_clause") and not isinstance(bak, str):
        raise OranteError("这段备份不是文字")
    if target == "expand" and not isinstance(bak, (str, dict)):
        raise OranteError("这段备份不是覆盖库也不是文字")
    _cfg_set(f"prompts.{target}", bak)


def get_block(name: str):
    """用户改过的，没改过就默认。"""
    _check_name(name)
    _migrate_once()
    if name == "migrate_notice":
        return migrate_notices()
    if name in ("tail_notes", "versions"):
        return copy.deepcopy(defaults().get(name) or ({} if name == "tail_notes" else {}))
    if name == "refill_clause":
        v = _cfg_get("prompts.refill_clause", _MISSING)
        if v is _MISSING or v is None:
            return REFILL_CLAUSE_DEFAULT
        return v if isinstance(v, str) else REFILL_CLAUSE_DEFAULT
    if name == "upscale":
        v = _cfg_get("prompts.upscale", _MISSING)
        if v is _MISSING or v is None:
            return UPSCALE_DEFAULT
        return v if isinstance(v, str) else UPSCALE_DEFAULT
    if name == "chain":
        v = _cfg_get("prompts.chain", _MISSING)
        if v is _MISSING or v is None:
            from .pipeline import CHAIN_PROMPT_DEFAULT
            return CHAIN_PROMPT_DEFAULT
        return v if isinstance(v, str) else CHAIN_PROMPT_DEFAULT
    v = _cfg_get(f"prompts.{name}", _MISSING)
    if v is _MISSING or v is None:
        v = copy.deepcopy(defaults()[name])
    else:
        v = copy.deepcopy(v)
    if name == "expand" and isinstance(v, dict):
        return covlib.sanitize(v)
    if name == "tail" and isinstance(v, dict):
        _ensure_emotion_presets(v)
    return v


def get_coverage() -> dict:
    """当前扩图覆盖库。旧格式字符串或坏数据都退回打包默认。"""
    v = get_block("expand")
    if covlib.is_lib(v):
        return covlib.sanitize(v)
    return covlib.default_lib()


def set_block(name: str, value) -> None:
    if name == "switches":
        set_switches(value)
        return
    if name == "minimal":
        apply_minimal(bool(value))
        return
    _check_name(name, write=True)
    if name in ("desc", "refill_clause", "bridge", "bridge_single", "upscale", "chain") and not isinstance(value, str):
        raise OranteError("这段提示词必须是一段文字")
    if name == "expand":
        if isinstance(value, dict):
            value = covlib.sanitize(value)
        elif not isinstance(value, str):
            raise OranteError("扩图覆盖库必须是对象，或者旧格式的一段文字")
    if name in ("camera", "tail", "problem") and not isinstance(value, dict):
        raise OranteError(f"{name} 必须是 JSON 对象")
    if name == "tail":
        value = _prepare_tail_for_save(value)
    _cfg_set(f"prompts.{name}", value)


def reset_block(name: str) -> None:
    _check_name(name, write=True)
    _cfg_reset(f"prompts.{name}")
    if name == "tail":
        try:
            _cfg_reset("prompts.emotion_preset")
        except Exception:
            pass


def all_blocks() -> dict:
    _migrate_once()
    d = defaults()
    versions = dict(d.get("versions") or {})
    versions["overridden"] = {n: _cfg_has(f"prompts.{n}") for n in EDITABLE}
    return {
        "desc": get_block("desc"),
        "camera": get_block("camera"),
        "tail": get_block("tail"),
        "problem": get_block("problem"),
        "expand": get_block("expand"),
        "refill_clause": get_block("refill_clause"),
        "bridge": get_block("bridge"),
        "bridge_single": get_block("bridge_single"),
        "upscale": get_block("upscale"),
        "chain": get_block("chain"),
        "migrate_notice": migrate_notices(),
        "tail_notes": copy.deepcopy(d.get("tail_notes") or {}),
        "versions": versions,
        "switches": get_switches(),
    }


def _match_pattern(sentence: str, pat: dict) -> bool:
    kind = (pat.get("kind") or "phrase").strip().lower()
    value = pat.get("value") or ""
    if not value:
        return False
    if kind == "regex":
        try:
            return re.search(value, sentence, re.I) is not None
        except re.error:
            return False
    hay = _nospace(sentence).lower()
    needle = _nospace(value).lower()
    return bool(needle) and needle in hay


def find_problem_sentences(text: str) -> list[str]:
    """按 flow_shots._sentences 切句，命中问题库的原句（去重保序）。"""
    block = get_block("problem") or {}
    patterns = block.get("patterns") or []
    hits: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines() or [text or ""]:
        for s in flow_shots._sentences((line or "").strip()):
            if s in seen:
                continue
            if any(_match_pattern(s, p) for p in patterns if isinstance(p, dict)):
                hits.append(s)
                seen.add(s)
    return hits


def gemini_scan_problems(text: str) -> list[str]:
    """让 Gemini 扫文案，把新句子以 phrase 加进问题库（不重复）。返回模型列出的原句。"""
    from . import ai
    raw = ai.client().ask_text(_SCAN_PROMPT + (text or ""), system=_SCAN_SYSTEM, json_mode=True)
    try:
        arr = json.loads(raw)
    except Exception:
        raise OranteError("Gemini 没有返回合法 JSON", (raw or "")[:200])
    if not isinstance(arr, list):
        raise OranteError("Gemini 没有返回 JSON 数组", (raw or "")[:200])

    block = get_block("problem") or {}
    patterns = list(block.get("patterns") or [])
    existing = {_nospace(p.get("value") or "").lower() for p in patterns if isinstance(p, dict)}
    found: list[str] = []
    seen_found: set[str] = set()
    changed = False
    for item in arr:
        s = item.strip() if isinstance(item, str) else str(item or "").strip()
        if not s or s in seen_found:
            continue
        seen_found.add(s)
        found.append(s)
        key = _nospace(s).lower()
        if not key or key in existing:
            continue
        patterns.append({
            "kind": "phrase",
            "value": s,
            "lang": "*",
            "note": "Gemini 扫文案自动补充",
            "source": "gemini",
        })
        existing.add(key)
        changed = True
    if changed:
        out = dict(block)
        out["patterns"] = patterns
        set_block("problem", out)
    return found


def _replace_in(obj, old: str, new: str):
    if isinstance(obj, str):
        return obj.replace(old, new)
    if isinstance(obj, dict):
        return {k: _replace_in(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_in(v, old, new) for v in obj]
    return obj


def _load_base_shots() -> dict:
    try:
        return copy.deepcopy(flow_shots.load_shots())
    except Exception:
        pass
    for p in (
        paths.LEGACY / "flow_shots.json",
        *( [paths.find_legacy_root() / "core" / "flow_shots.json"]
           if paths.find_legacy_root() else [] ),
    ):
        if p and Path(p).is_file():
            return json.loads(Path(p).read_text(encoding="utf-8"))
    raise OranteError(
        "找不到运镜卡库",
        "orante/legacy/flow_shots.json 缺失，跑 tools/extract_prompt_defaults.py。")


def _effective_shots() -> dict:
    """完整卡库 + 用户当前的 camera / tail。"""
    shots = _load_base_shots()
    cam = get_block("camera")
    if isinstance(cam, dict):
        shots.update(cam)
    tail = get_block("tail")
    if isinstance(tail, dict):
        shots["固定结语"] = copy.deepcopy(tail)
    return _replace_in(shots, "{停顿强调}", _PAUSE_HOLD)


def _fill_pause(prompt: str, seg_text: str) -> str:
    if _PAUSE_HOLD not in prompt and "{停顿强调}" not in prompt:
        return prompt
    hits = find_problem_sentences(seg_text)
    tpl = (get_block("problem") or {}).get("template") or "包括{原话}这句话也要读"
    ins = "".join(str(tpl).replace("{原话}", h) for h in hits)
    return prompt.replace(_PAUSE_HOLD, ins).replace("{停顿强调}", ins)


def _as_map(obj) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    from dataclasses import asdict, is_dataclass
    if is_dataclass(obj):
        return asdict(obj)
    return {}



# --- 规格 85：prompts.switches ---------------------------------------------
def default_switches() -> dict:
    import flow_shots as fs
    return fs.default_switches()


def get_switches() -> dict:
    """读 prompts.switches；缺省全开。"""
    import flow_shots as fs
    raw = _cfg_get("prompts.switches", None)
    return fs.normalize_switches(raw if isinstance(raw, dict) else None)


def set_switches(switches) -> dict:
    """写入 prompts.switches，返回归一化后的字典。"""
    import flow_shots as fs
    if switches is None:
        _cfg_reset("prompts.switches")
        return fs.default_switches()
    if not isinstance(switches, dict):
        raise OranteError("switches 必须是对象")
    norm = fs.normalize_switches(switches)
    _cfg_set("prompts.switches", norm)
    return norm


def apply_minimal(on: bool) -> dict:
    """极简模式：开=关掉除静音提示外所有积木并记住先前状态；关=恢复。"""
    import flow_shots as fs
    cur = get_switches()
    if on:
        prev = _cfg_get("prompts.switches_before_minimal", None)
        if not isinstance(prev, dict):
            # 记住当前（极简打开前）
            _cfg_set("prompts.switches_before_minimal", dict(cur))
        off = fs.default_switches()
        for k in fs.SWITCH_BRICK_KEYS + ("描述语整理",):
            off[k] = False
        # 静音提示保持当前值
        off["静音提示"] = bool(cur.get("静音提示", True))
        return set_switches(off)
    # 关极简：恢复记忆，没有记忆则全开
    prev = _cfg_get("prompts.switches_before_minimal", None)
    _cfg_reset("prompts.switches_before_minimal")
    if isinstance(prev, dict):
        return set_switches(prev)
    return set_switches(fs.default_switches())



def build_final_prompt(m: Manifest, seg: dict, desc_override=None) -> str:
    """成品提示词：身份锁 + 运镜卡 + 结语 + 台词。{停顿强调} 用问题句子库填。"""
    seg = _as_map(seg)
    img = m.image(seg.get("image") or "") if hasattr(m, "image") else None
    if isinstance(img, dict):
        pass
    elif img is None and isinstance(getattr(m, "images", None), list):
        img = next((im for im in m.images if _as_map(im).get("p") == seg.get("image")), None)
    img = _as_map(img)
    if not img:
        raise OranteError(
            f"段 {seg.get('s')} 没有对应的图片",
            "先配图轨，或检查 segment.image 是不是 pNN。")
    desc = desc_override if desc_override is not None else (img.get("desc") or "")
    if not str(desc).strip():
        raise OranteError(
            f"图片 {img.get('p')} 还没有描述语",
            "先跑「写描述语」。")

    parsed_eye, parsed_scene, _ = flow_shots.parse_tag(desc)
    eye = img.get("eye") or parsed_eye or "闭眼"
    scene = img.get("scene") or parsed_scene or "常规"
    voice = img.get("voice") or ""
    seg_text = seg.get("text") or ""
    try:
        seg_idx = int(seg.get("s") or 1)
    except (TypeError, ValueError):
        seg_idx = 1
    n_segs = len(m.segments) or 1

    shots = _effective_shots()
    card = (seg.get("shot") or "").strip()
    if not card:
        label = img.get("p") or "p01"
        seated = flow_shots.is_seated(desc)
        cards = flow_shots.shot_cards(label, n_segs, eye, scene, shots, seated=seated)
        if cards:
            i = min(max(seg_idx - 1, 0), len(cards) - 1)
            card = cards[i]

    # 规格 86：任务级无文案才传 no_copy=True；静音文案来自 prompts.silent 可编辑块
    no_copy = False
    try:
        from . import simple86 as _s86
        no_copy = not _s86.has_copy(m)
    except Exception:
        c = getattr(m, "copy", None)
        if isinstance(c, dict):
            no_copy = not bool((c.get("text") or "").strip())
        else:
            no_copy = not bool((c or "").strip())
    if no_copy:
        tip = _cfg_get("prompts.silent", None)
        if isinstance(tip, str) and tip.strip():
            shots = dict(shots or {})
            shots["静音提示"] = tip.strip()
            shots["silent"] = tip.strip()
    prompt = flow_shots.build_prompt(
        desc, card, eye, scene, seg_text, shots,
        seg_idx=seg_idx, n_segs=n_segs, voice_lock=voice,
        switches=get_switches(), no_copy=no_copy)
    return _fill_pause(prompt, seg_text)


def get_silent() -> str:
    """规格 86：静音提示可编辑块（prompts.silent）。"""
    from .simple86 import SILENT_DEFAULT
    v = _cfg_get("prompts.silent", None)
    if isinstance(v, str) and v.strip():
        return v
    return SILENT_DEFAULT


def set_silent(text: str) -> str:
    text = (text or "").strip()
    if not text:
        from .simple86 import SILENT_DEFAULT
        text = SILENT_DEFAULT
    _cfg_set("prompts.silent", text)
    return text
