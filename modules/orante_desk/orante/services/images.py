# -*- coding: utf-8 -*-
"""图片来源：本地文件夹/单文件、Google Drive 文件夹/单文件、http(s) 直链。

把 task['image_source'] 里的图落到 task_dir/images/pNN.<ext>，返回 Manifest.images 列表。
Drive 优先走 OAuth（data/google/ 里的 token）；没有 token 就走公开链接。
老模块 google_drive_downloader.py 是 GUI，__init__ 会弹窗，这里只复用它的公开直下方法。
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import unquote

import requests

from .. import paths
from ..errors import OranteError
from ..models import Image, Manifest, sha1_file, sha256_file

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MIN_SIDE = 300
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
DRIVE_HINT = "把文件夹设为『任何知道链接的人可查看』"
_USED_PREFIX_RE = re.compile(
    r"^(【已使用】|【已再次使用】|【已使用过】|【已第\d+次使用】)+"
)
_EXT_IN_NAME = re.compile(r"\.(jpe?g|png|webp)\b", re.I)

USED_FILE = lambda: paths.DATA / "used_images.json"  # noqa: E731
GOOGLE_DIR = lambda: paths.DATA / "google"  # noqa: E731


# ---- 小工具 ----------------------------------------------------------------

def _cfg(key, default=None):
    try:
        from .. import config
        return config.get(key, default)
    except Exception:
        from ..config import DEFAULTS
        return DEFAULTS.get(key, default)


def _nat_key(name: str):
    """p1,p2,p10 而不是 p1,p10,p2。"""
    return [int(x) if x.isdigit() else x.lower()
            for x in re.split(r"(\d+)", name)]


def file_mtime_ms(path: Path | None) -> int:
    """给 <img>/<video> src 加 ?v= 用的毫秒 mtime。文件不在就 0。

    必须用 mtime，不能用随机数：随机数会让每次刷新都重下几百 MB。
    毫秒比秒更稳 —— 同一秒内换文件也能破缓存。
    """
    if not path:
        return 0
    try:
        st = Path(path).stat()
    except OSError:
        return 0
    ns = getattr(st, "st_mtime_ns", None)
    if ns is not None:
        return int(ns // 1_000_000)
    return int(st.st_mtime * 1000)


def annotate_images(task_dir: Path, images: list) -> list[dict]:
    """给前端用：exists / mtime（毫秒）/ bytes。不改原 dict，不写盘。"""
    task_dir = Path(task_dir)
    out = []
    for im in images or []:
        x = dict(im)
        rel = (im.get("file") or "").replace("\\", "/")
        fp = (task_dir / rel) if rel else None
        exists = bool(fp) and fp.is_file()
        x["exists"] = exists
        if exists:
            try:
                st = fp.stat()
                x["mtime"] = file_mtime_ms(fp)
                x["bytes"] = st.st_size
            except OSError:
                x["mtime"] = 0
                x["bytes"] = 0
                x["exists"] = False
        else:
            x["mtime"] = 0
        out.append(x)
    return out


# 跳过 images/ 下这些子目录：备份、候选、弃用，以及空镜头
_SKIP_DIR_NAMES = {"空镜头"}
_SHA_CACHE_LOCK = None  # 延迟建，避免 import 时抢锁
_SHA_CACHE: dict | None = None


def _sha_cache_path() -> Path:
    return paths.DATA / "sha-cache.json"


def _sha_cache() -> dict:
    """路径+大小+mtime → {sha1, sha256}。换内容才重算。"""
    global _SHA_CACHE, _SHA_CACHE_LOCK
    if _SHA_CACHE_LOCK is None:
        import threading
        _SHA_CACHE_LOCK = threading.Lock()
    with _SHA_CACHE_LOCK:
        if _SHA_CACHE is not None:
            return _SHA_CACHE
        p = _sha_cache_path()
        data = {}
        if p.is_file():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except Exception:
                data = {}
        _SHA_CACHE = data
        return _SHA_CACHE


def _save_sha_cache() -> None:
    p = _sha_cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _sha_cache()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".sha-cache-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, p)


def hash_file_cached(path: Path) -> dict:
    """算 sha1 + sha256，按 路径+大小+mtime 缓存。浅层刷新不要走这里。"""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return {"sha1": "", "sha256": "", "size": 0, "mtime": 0}
    ns = getattr(st, "st_mtime_ns", None)
    key = "%s|%s|%s" % (str(path.resolve()), st.st_size, ns if ns is not None else int(st.st_mtime))
    cache = _sha_cache()
    hit = cache.get(key)
    if isinstance(hit, dict) and hit.get("sha256") and hit.get("sha1"):
        return {
            "sha1": hit["sha1"], "sha256": hit["sha256"],
            "size": st.st_size, "mtime": file_mtime_ms(path),
        }
    rec = {
        "sha1": sha1_file(path),
        "sha256": sha256_file(path),
        "size": st.st_size,
        "mtime": file_mtime_ms(path),
    }
    cache[key] = {"sha1": rec["sha1"], "sha256": rec["sha256"]}
    try:
        _save_sha_cache()
    except OSError:
        pass
    return rec


def list_library_files(img_dir: Path) -> list[Path]:
    """扫 images/ 下的图片。跳过下划线开头的子目录和 空镜头/。"""
    img_dir = Path(img_dir)
    if not img_dir.is_dir():
        return []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(img_dir):
        dirnames[:] = [
            n for n in dirnames
            if n and not n.startswith("_") and not n.startswith(".")
            and n not in _SKIP_DIR_NAMES
        ]
        for name in filenames:
            if not name or name.startswith("."):
                continue
            p = Path(dirpath) / name
            if p.suffix.lower() in IMG_EXTS:
                out.append(p)
    out.sort(key=lambda p: _nat_key(p.name))
    return out


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"})
    return s


def _sniff_ext(path: Path) -> str | None:
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def _ext_from_name(name: str) -> str | None:
    if not name:
        return None
    m = _EXT_IN_NAME.search(name)
    if not m:
        return None
    ext = "." + m.group(1).lower()
    return ".jpg" if ext == ".jpeg" and name.lower().endswith(".jpg") else ext


def _looks_image_name(name: str) -> bool:
    return bool(_EXT_IN_NAME.search(name or ""))


# ---- 已使用记录 --------------------------------------------------------------

class _FileLock:
    """跨进程锁 used_images.json。Windows 走 msvcrt，别处用独占创建。"""

    def __init__(self, path: Path, timeout=20):
        self.path = Path(path)
        self.timeout = timeout
        self.fp = None
        self._sentinel = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout
        if msvcrt is not None:
            self.fp = open(self.path, "a+b")
            if self.fp.tell() == 0:
                self.fp.write(b"\0")
                self.fp.flush()
            while True:
                try:
                    self.fp.seek(0)
                    msvcrt.locking(self.fp.fileno(), msvcrt.LK_NBLCK, 1)
                    return self
                except OSError:
                    if time.time() > deadline:
                        raise OranteError("已使用记录正被占用", "稍后再试")
                    time.sleep(0.05)
        sentinel = self.path.with_suffix(self.path.suffix + ".lock")
        while True:
            try:
                fd = os.open(str(sentinel), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                self._sentinel = (fd, sentinel)
                return self
            except FileExistsError:
                if time.time() > deadline:
                    raise OranteError("已使用记录正被占用", "稍后再试")
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.fp is not None:
            try:
                self.fp.seek(0)
                if msvcrt is not None:
                    msvcrt.locking(self.fp.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self.fp.close()
                self.fp = None
        if self._sentinel is not None:
            fd, sentinel = self._sentinel
            os.close(fd)
            try:
                sentinel.unlink()
            except OSError:
                pass


def _load_used() -> dict:
    p = USED_FILE()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_used(data: dict) -> None:
    p = USED_FILE()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".used-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _used_count(key: str) -> int:
    rec = _load_used().get(key) or {}
    if isinstance(rec, dict):
        return int(rec.get("count") or 0)
    return 0


def _mark_used(key: str) -> int:
    """记一次使用，返回记完后的次数。"""
    lock_path = USED_FILE().with_suffix(".json.lock")
    with _FileLock(lock_path):
        data = _load_used()
        rec = data.get(key) if isinstance(data.get(key), dict) else {}
        n = int(rec.get("count") or 0) + 1
        data[key] = {"count": n, "last": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_used(data)
        return n


# ---- OAuth（静默刷新；不弹浏览器）------------------------------------------

def _bootstrap_google_dir() -> Path:
    """OAuth 文件只放 data/google/。开发机上若空，从流水线 core/ 拷一份过来。"""
    d = GOOGLE_DIR()
    d.mkdir(parents=True, exist_ok=True)
    token = d / "token.json"
    secret = d / "client_secret.json"
    if token.is_file() and secret.is_file():
        return d
    root = paths.find_legacy_root()
    if root is None:
        return d
    core = root / "core"
    if not token.is_file():
        src = core / "token.json"
        if src.is_file():
            shutil.copy2(src, token)
    if not secret.is_file():
        cands = sorted(core.glob("client_secret*.json"))
        if cands:
            shutil.copy2(cands[0], secret)
        else:
            cfg = core / "config.json"
            if cfg.is_file():
                try:
                    data = json.loads(cfg.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                cid = (data.get("oauth_client_id") or "").strip()
                sec = (data.get("oauth_client_secret") or "").strip()
                if cid and sec:
                    secret.write_text(json.dumps(
                        {"installed": {"client_id": cid, "client_secret": sec}},
                        ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def _read_oauth_creds() -> tuple[str, str, str] | None:
    """返回 (client_id, client_secret, refresh_token)；缺一样就 None。"""
    d = _bootstrap_google_dir()
    cid = sec = refresh = ""
    secret_p = d / "client_secret.json"
    if secret_p.is_file():
        try:
            blob = json.loads(secret_p.read_text(encoding="utf-8"))
        except Exception:
            blob = {}
        inst = blob.get("installed") or blob.get("web") or blob
        cid = (inst.get("client_id") or "").strip()
        sec = (inst.get("client_secret") or "").strip()
    token_p = d / "token.json"
    if token_p.is_file():
        try:
            tok = json.loads(token_p.read_text(encoding="utf-8"))
        except Exception:
            tok = {}
        refresh = (tok.get("refresh_token") or "").strip()
        cid = cid or (tok.get("client_id") or "").strip()
    if cid and sec and refresh:
        return cid, sec, refresh
    return None


def get_access_token(log=None) -> str | None:
    """静默用 refresh_token 换 access_token。失败或没有凭证 → None（走公开链接）。

    不弹浏览器：任务跑到一半不能等人点授权。首次授权把 token.json 放到 data/google/ 即可。
    """
    creds = _read_oauth_creds()
    if not creds:
        return None
    cid, sec, refresh = creds
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": cid, "client_secret": sec,
            "refresh_token": refresh, "grant_type": "refresh_token",
        }, timeout=30)
        if r.status_code == 200:
            return r.json().get("access_token")
        if log:
            log(f"Drive token 刷新失败 HTTP {r.status_code}，改走公开链接")
    except Exception as e:
        if log:
            log(f"Drive token 刷新异常：{e}，改走公开链接")
    return None


# ---- 来源识别 ----------------------------------------------------------------

def _drive_folder_id(url: str) -> str | None:
    m = re.search(r"/folders/([A-Za-z0-9_-]{20,})", url)
    if m:
        return m.group(1)
    # 规格：文件夹链接也包括 ?id=（排除已经能看出来是单文件的）
    if "drive.google.com" in url and "/file/" not in url \
            and "open?" not in url and re.search(r"(?:^|[?&/])uc(?:\?|$)", url) is None:
        m = re.search(r"[?&]id=([A-Za-z0-9_-]{20,})", url)
        if m:
            return m.group(1)
    return None


def _drive_file_id(url: str) -> str | None:
    for p in (r"/file/d/([A-Za-z0-9_-]{20,})",
              r"[?&]id=([A-Za-z0-9_-]{20,})",
              r"/uc\?id=([A-Za-z0-9_-]{20,})"):
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _is_drive(url: str) -> bool:
    u = url.lower()
    return "drive.google.com" in u or "docs.google.com" in u or "drive.usercontent.google.com" in u


def classify(src: str):
    """按规格顺序识别来源。返回 (kind, payload)。"""
    raw = (src or "").strip().strip('"').strip("'")
    if not raw:
        raise OranteError("没有图片来源", "任务表的「图片来源」列是空的。")
    p = Path(raw)
    try:
        if p.is_dir():
            return "local_dir", p
        if p.is_file():
            return "local_file", p
    except OSError:
        pass
    if raw.startswith("http://") or raw.startswith("https://"):
        if _is_drive(raw):
            if "/folders/" in raw:
                fid = _drive_folder_id(raw)
                if fid:
                    return "drive_folder", fid
            if "/file/d/" in raw or "open?" in raw or "uc?" in raw or "/uc?" in raw:
                fid = _drive_file_id(raw)
                if fid:
                    return "drive_file", fid
            folder_id = _drive_folder_id(raw)
            if folder_id:
                return "drive_folder", folder_id
            file_id = _drive_file_id(raw)
            if file_id:
                return "drive_file", file_id
        return "http", raw
    raise OranteError(
        f"认不出图片来源：{raw[:80]}",
        "填本地文件夹/文件路径，或 Google Drive / 直链。")


# ---- 本地 --------------------------------------------------------------------

_LOCAL_MAX_DEPTH = 3  # 递归扫子文件夹的深度上限（根是 0）
_LOCAL_MODES = ("copy", "copy_mark", "move")


def _looks_like_local_path(raw: str) -> bool:
    """Windows 盘符、UNC、带反斜杠/正斜杠的路径。classify 认不出时 probe 用来给人话。"""
    s = (raw or "").strip()
    if not s:
        return False
    if re.match(r"^[A-Za-z]:[\\/]", s):
        return True
    if s.startswith("\\\\") or s.startswith("//"):
        return True
    if s.startswith("/") or "\\" in s:
        return True
    return False


def _scan_local_dir(folder: Path, recursive: bool = False,
                    max_depth: int = _LOCAL_MAX_DEPTH) -> tuple[list[Path], int]:
    """列出本地图片。recursive=False 只扫顶层（现在的行为）。

    递归时复用 list_library_files 那套 os.walk：跳过 _ 和 . 开头的目录，
    深度上限 3 层，按文件名自然序。返回 (图片列表, 跳过的非图片/隐藏文件数)。
    """
    folder = Path(folder)
    if not folder.is_dir():
        return [], 0
    images: list[Path] = []
    skipped = 0
    if not recursive:
        try:
            entries = list(folder.iterdir())
        except OSError:
            return [], 0
        for p in entries:
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            if (not p.name) or p.name.startswith(".") or p.suffix.lower() not in IMG_EXTS:
                skipped += 1
                continue
            images.append(p)
        images.sort(key=lambda p: _nat_key(p.name))
        return images, skipped

    root = str(folder)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirnames[:] = [
            n for n in dirnames
            if n and not n.startswith("_") and not n.startswith(".")
        ]
        if depth >= max_depth:
            dirnames[:] = []
        for name in filenames:
            if not name or name.startswith("."):
                skipped += 1
                continue
            p = Path(dirpath) / name
            if p.suffix.lower() in IMG_EXTS:
                images.append(p)
            else:
                skipped += 1
    images.sort(key=lambda p: _nat_key(p.name))
    return images, skipped


def _list_local_dir(folder: Path, recursive: bool = False) -> list[Path]:
    files, _skipped = _scan_local_dir(folder, recursive=recursive)
    return files


def _local_mode_of(task: dict) -> str:
    """行上 meta.image_src_mode 覆盖设置 images.local_mode；非法值当复制。"""
    meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
    raw = (meta.get("image_src_mode") if isinstance(meta, dict) else None) or _cfg(
        "images.local_mode", "copy")
    s = str(raw or "copy").strip()
    return s if s in _LOCAL_MODES else "copy"


def _local_mode_label(mode: str, recursive: bool = False) -> str:
    if mode == "move":
        core = "移动，原文件已搬走"
    elif mode == "copy_mark":
        core = "复制并标记"
    else:
        core = "复制，原文件保留"
    if recursive:
        return core + "，包含子文件夹"
    return core


def _mark_local_used(src_file: Path) -> Path:
    """源文件改名加【已使用】前缀。已有前缀不再叠。"""
    src_file = Path(src_file)
    base = _strip_used_prefix(src_file.name)
    dest = src_file.with_name("【已使用】" + base)
    if dest.resolve() == src_file.resolve():
        return src_file
    if dest.exists():
        stem, ext = Path(base).stem, Path(base).suffix
        i = 2
        while True:
            cand = src_file.with_name(f"【已使用】{stem}-{i}{ext}")
            if not cand.exists():
                dest = cand
                break
            i += 1
    src_file.rename(dest)
    return dest


def _apply_local_mode(src_file: Path, dest: Path, mode: str, log) -> str:
    """拷进任务目录之后处理源文件。移动/标记失败就降级成复制，准备不失败。"""
    src_file = Path(src_file)
    if mode not in ("copy_mark", "move"):
        return "copy"
    if not src_file.exists():
        if log:
            log(f"  ↳ 源文件已经不在了，当复制：{src_file.name}")
        return "copy"
    if mode == "copy_mark":
        try:
            _mark_local_used(src_file)
            return "copy_mark"
        except Exception as e:
            if log:
                log(f"  ↳ 标记失败，降级成复制：{e}")
            return "copy"
    # 移动：dest 已经是拷好的图，不能 shutil.move 到 dest（Windows 上目标存在会失败）。
    # 把源挪到旁边再删；跨盘 shutil.move 自己会退化成拷贝+删除。
    try:
        parked = src_file.with_name(src_file.name + ".orante-moved")
        shutil.move(str(src_file), str(parked))
        try:
            parked.unlink()
        except OSError:
            pass
        return "move"
    except Exception as e:
        if log:
            log(f"  ↳ 移动失败，降级成复制：{e}")
        return "copy"


# 规格 81：probe-source 同路径 60 秒缓存，别每敲一个字就扫一次磁盘
_PROBE_TTL = 60.0
_PROBE_CACHE: dict[tuple, tuple[float, dict]] = {}
_PROBE_LOCK = threading.Lock()


def clear_probe_cache() -> None:
    with _PROBE_LOCK:
        _PROBE_CACHE.clear()


def probe_source(src: str, recursive: bool = False) -> dict:
    """只数不拷。网盘/直链只判别类型，不发网络请求。"""
    raw = (src or "").strip().strip('"').strip("'")
    rec = bool(recursive)
    key = (raw, rec)
    now = time.time()
    with _PROBE_LOCK:
        hit = _PROBE_CACHE.get(key)
        if hit and now - hit[0] < _PROBE_TTL:
            return dict(hit[1])
    out = _probe_source_uncached(raw, rec)
    with _PROBE_LOCK:
        _PROBE_CACHE[key] = (now, out)
        if len(_PROBE_CACHE) > 256:
            dead = [k for k, v in _PROBE_CACHE.items() if now - v[0] >= _PROBE_TTL]
            for k in dead:
                _PROBE_CACHE.pop(k, None)
    return dict(out)


def _probe_source_uncached(raw: str, recursive: bool) -> dict:
    empty = {
        "kind": "", "ok": False, "n": 0, "n_deep": 0,
        "note": "", "sample": [], "recursive": recursive, "path": "",
    }
    if not raw:
        empty["note"] = "没有图片来源"
        return empty
    try:
        kind, payload = classify(raw)
    except OranteError as e:
        out = dict(empty)
        if _looks_like_local_path(raw):
            out["kind"] = "local_missing"
            out["note"] = "找不到这个文件夹"
            out["path"] = raw
        else:
            out["note"] = e.message or "认不出图片来源"
        return out

    if kind == "local_dir":
        folder = Path(payload)
        top, _ = _scan_local_dir(folder, recursive=False)
        deep, _ = _scan_local_dir(folder, recursive=True)
        n, n_deep = len(top), len(deep)
        used = deep if recursive else top
        sample_from = used if used else deep
        sample = [p.name for p in sample_from[:4]]
        ok = len(used) > 0
        note = ""
        if n == 0 and n_deep == 0:
            note = "文件夹里 0 张图片（顶层和子文件夹都没有）"
            ok = False
        elif n == 0 and n_deep > 0 and not recursive:
            note = f"顶层 0 张，子文件夹里有 {n_deep} 张"
            ok = False
        return {
            "kind": "local_dir", "ok": ok, "n": n, "n_deep": n_deep,
            "note": note, "sample": sample, "recursive": recursive,
            "path": str(folder),
        }
    if kind == "local_file":
        p = Path(payload)
        is_img = p.suffix.lower() in IMG_EXTS
        return {
            "kind": "local_file", "ok": is_img,
            "n": 1 if is_img else 0, "n_deep": 1 if is_img else 0,
            "note": "" if is_img else "不是图片文件",
            "sample": [p.name] if is_img else [],
            "recursive": recursive, "path": str(p),
        }
    if kind == "drive_folder":
        return {
            "kind": "drive_folder", "ok": True, "n": 0, "n_deep": 0,
            "note": "", "sample": [], "recursive": recursive, "path": "",
        }
    if kind == "drive_file":
        return {
            "kind": "drive_file", "ok": True, "n": 0, "n_deep": 0,
            "note": "", "sample": [], "recursive": recursive, "path": "",
        }
    if kind == "http":
        return {
            "kind": "http", "ok": True, "n": 0, "n_deep": 0,
            "note": "", "sample": [], "recursive": recursive, "path": "",
        }
    out = dict(empty)
    out["note"] = "认不出图片来源"
    return out


# ---- Drive：列目录 ------------------------------------------------------------

def _list_folder_api(folder_id: str, token: str | None = None, api_key: str | None = None) -> list[tuple[str, str]]:
    files, page = [], None
    headers = {"User-Agent": UA}
    if token:
        headers["Authorization"] = "Bearer " + token
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken,files(id,name,mimeType)",
            "pageSize": 1000, "orderBy": "name",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if api_key and not token:
            params["key"] = api_key
        if page:
            params["pageToken"] = page
        if api_key and not token:
            headers["Referer"] = f"https://drive.google.com/drive/folders/{folder_id}"
        r = requests.get("https://www.googleapis.com/drive/v3/files",
                         params=params, headers=headers, timeout=30)
        if r.status_code != 200:
            msg = ""
            try:
                msg = r.json().get("error", {}).get("message", "")[:80]
            except Exception:
                msg = r.text[:80]
            raise RuntimeError(f"API 列文件夹失败 HTTP {r.status_code} {msg}")
        data = r.json()
        for f in data.get("files") or []:
            mime = f.get("mimeType") or ""
            name = f.get("name") or ""
            if mime.startswith("image/") or _looks_image_name(name):
                files.append((f["id"], name))
        page = data.get("nextPageToken")
        if not page:
            return files


def _list_folder_scrape(folder_id: str) -> list[tuple[str, str]]:
    """公开文件夹：优先嵌入页（只含这一夹的条目），再退回普通页。"""
    s = _session()
    found: dict[str, str] = {}
    keys: list[str] = []
    last_err = None

    def _eat(html: str, allow_bare_id: bool):
        if not html:
            return
        for fid, name in re.findall(
                r'/file/d/([A-Za-z0-9_-]{20,})[^"]*"[^>]*>.*?flip-entry-title">([^<]+)<',
                html, re.S):
            found[fid] = unquote(name).strip()
        for fid, name in re.findall(
                r'"id"\s*:\s*"([A-Za-z0-9_-]{20,})"[^}]{0,400}?"name"\s*:\s*"([^"]+)"',
                html):
            found.setdefault(fid, name)
        for name, fid in re.findall(
                r'"name"\s*:\s*"([^"]+)"[^}]{0,400}?"id"\s*:\s*"([A-Za-z0-9_-]{20,})"',
                html):
            found.setdefault(fid, name)
        if allow_bare_id and not found:
            for fid in re.findall(r"/file/d/([A-Za-z0-9_-]{20,})", html):
                found.setdefault(fid, "")
        for k in re.findall(r"(AI" r"zaSy[0-9A-Za-z\-_]{20,})", html):
            if k not in keys:
                keys.append(k)

    urls = [
        (f"https://drive.google.com/embeddedfolderview?id={folder_id}#list", True),
        (f"https://drive.google.com/embeddedfolderview?id={folder_id}#grid", True),
        (f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing", False),
    ]
    for url, allow_bare in urls:
        try:
            r = s.get(url, timeout=30)
            if r.status_code == 200:
                _eat(r.text, allow_bare_id=allow_bare)
                if any(_looks_image_name(n) for n in found.values()):
                    break
            else:
                last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)

    if not found:
        for k in keys:
            try:
                for fid, name in _list_folder_api(folder_id, api_key=k):
                    found[fid] = name
                if found:
                    break
            except Exception as e:
                last_err = str(e)

    if not found:
        raise RuntimeError(last_err or "解析不到文件")

    files = []
    for fid, name in found.items():
        if fid == folder_id:
            continue
        if name and not _looks_image_name(name):
            continue
        files.append((fid, name))
    if not files:
        # 全是没名字的 id：留给下载后再验是不是图
        files = [(fid, name) for fid, name in found.items() if fid != folder_id]
    files.sort(key=lambda x: _nat_key(x[1] or x[0]))
    return files


def list_drive_folder(folder_id: str, log, token: str | None) -> list[tuple[str, str]]:
    errors = []
    if token:
        try:
            files = _list_folder_api(folder_id, token=token)
            log(f"Drive 文件夹（OAuth）{len(files)} 项")
            return files
        except Exception as e:
            errors.append(f"OAuth：{e}")
            log(f"OAuth 列文件夹失败（{e}），改试公开链接")
    try:
        files = _list_folder_scrape(folder_id)
        log(f"Drive 文件夹（公开链接）{len(files)} 项")
        return files
    except Exception as e:
        errors.append(f"公开链接：{e}")
    raise OranteError(
        "列不出 Drive 文件夹里的图",
        f"{DRIVE_HINT}。详情：{' / '.join(errors)}")


# ---- Drive：下载单文件 --------------------------------------------------------

def _legacy_gdd():
    """拿老模块的类（不要实例化，一实例化就弹 Tk 窗）。"""
    try:
        paths.add_legacy_to_sys_path()
        import google_drive_downloader as m  # type: ignore
        return m.GoogleDriveDownloader
    except Exception:
        return None


def _filename_from_response(response) -> str:
    cd = response.headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*=(?:UTF-8|utf-8)''(.+?)(?:;|$)", cd)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename="?([^";]+)"?', cd)
    if m:
        return unquote(m.group(1).strip())
    return ""


def _download_uc(file_id: str, dest: Path) -> tuple[Path, str]:
    """公开直下：uc?export=download，大文件病毒扫描页也能过。"""
    s = _session()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urls = [
        ("https://drive.google.com/uc",
         {"export": "download", "id": file_id, "confirm": "t"}),
        ("https://drive.usercontent.google.com/download",
         {"id": file_id, "export": "download", "confirm": "t"}),
    ]
    last_err = "未知错误"
    try:
        for base, params in urls:
            r = s.get(base, params=params, stream=True, timeout=120)
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                html = r.text
                peek = html[:2000]
                if "ServiceLogin" in peek or "accounts.google.com" in peek:
                    last_err = "需要登录才能访问"
                    continue
                confirm = None
                for pat in (r'href="(/uc\?export=download[^"]*)"',
                            r'id="download-form"[^>]*action="([^"]*)"',
                            r'action="([^"]*)"[^>]*id="download-form"',
                            r"confirm=([a-zA-Z0-9_-]+)"):
                    m = re.search(pat, html)
                    if m:
                        confirm = m.group(1).replace("&amp;", "&")
                        break
                for k, v in r.cookies.items():
                    if k.startswith("download_warning"):
                        params = dict(params)
                        params["confirm"] = v
                        confirm = confirm or base
                        break
                if confirm:
                    if confirm.startswith("/"):
                        r = s.get("https://drive.google.com" + confirm, stream=True, timeout=120)
                    elif confirm.startswith("http"):
                        r = s.get(confirm, stream=True, timeout=120)
                    else:
                        params = dict(params)
                        params["confirm"] = confirm
                        r = s.get(base, params=params, stream=True, timeout=120)
                    r.raise_for_status()
                    ctype = (r.headers.get("Content-Type") or "").lower()
                    if "text/html" in ctype:
                        peek = r.text[:1500]
                        if "accounts.google.com" in peek:
                            last_err = "需要登录才能访问"
                            continue
                        last_err = "返回的是网页而不是文件"
                        continue
            orig = _filename_from_response(r)
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(32768):
                    if chunk:
                        f.write(chunk)
            if tmp.stat().st_size == 0:
                tmp.unlink(missing_ok=True)
                last_err = "下载的文件大小为 0"
                continue
            _move(tmp, dest)
            return dest, orig
        raise RuntimeError(last_err)
    finally:
        s.close()
        if tmp.exists() and not dest.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _download_oauth(file_id: str, dest: Path, token: str) -> tuple[Path, str]:
    headers = {"Authorization": "Bearer " + token, "User-Agent": UA}
    orig = ""
    try:
        meta = requests.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"fields": "name,mimeType", "supportsAllDrives": "true"},
            headers=headers, timeout=30)
        if meta.status_code == 200:
            orig = (meta.json() or {}).get("name") or ""
    except Exception:
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    r = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        params={"alt": "media", "supportsAllDrives": "true"},
        headers=headers, stream=True, timeout=120)
    if r.status_code != 200:
        msg = ""
        try:
            msg = r.json().get("error", {}).get("message", "")[:80]
        except Exception:
            msg = (r.text or "")[:80]
        raise RuntimeError(f"Drive API 下载失败 HTTP {r.status_code} {msg}")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(32768):
            if chunk:
                f.write(chunk)
    if tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("下载的文件大小为 0")
    _move(tmp, dest)
    return dest, orig


def download_drive_file(file_id: str, dest: Path, log, token: str | None) -> tuple[Path, str]:
    """有 token 走 Drive API；失败或没 token 走公开直下（含老模块）。"""
    if token:
        try:
            return _download_oauth(file_id, dest, token)
        except Exception as e:
            log(f"  登录下载失败（{e}），改试公开直链")
    gdd = _legacy_gdd()
    if gdd is not None:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".legacy")
            # 不要调 __init__（会弹 Tk 窗）；__new__ 就够调实例方法
            inst = gdd.__new__(gdd)
            ok, orig = inst._download_from_gdrive(file_id, str(tmp))
            if ok and tmp.exists() and tmp.stat().st_size > 0:
                _move(tmp, dest)
                return dest, orig or ""
            if tmp.exists():
                tmp.unlink()
        except Exception as e:
            log(f"  老模块直下失败（{e}），改用内置公开直链")
            try:
                dest.with_suffix(".legacy").unlink(missing_ok=True)
            except OSError:
                pass
    try:
        return _download_uc(file_id, dest)
    except Exception as e:
        raise OranteError(
            f"Drive 文件下不下来（{file_id[:12]}…）",
            f"{DRIVE_HINT}。原因：{e}") from e


# ---- Drive 改名 --------------------------------------------------------------

def _strip_used_prefix(name: str) -> str:
    return _USED_PREFIX_RE.sub("", name or "")


def _used_prefix(count: int) -> str:
    if count <= 1:
        return "【已使用】"
    if count == 2:
        return "【已再次使用】"
    return f"【已第{count}次使用】"


def _drive_file_name(file_id: str, token: str) -> str:
    r = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        params={"fields": "name", "supportsAllDrives": "true"},
        headers={"Authorization": "Bearer " + token, "User-Agent": UA},
        timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"查文件名失败 HTTP {r.status_code}")
    return r.json().get("name") or ""


def try_rename_on_drive(file_id: str, count: int, token: str, log) -> None:
    try:
        name = _drive_file_name(file_id, token)
        base = _strip_used_prefix(name)
        new = _used_prefix(count) + base
        if new == name:
            return
        r = requests.patch(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"supportsAllDrives": "true"},
            json={"name": new},
            headers={"Authorization": "Bearer " + token, "User-Agent": UA},
            timeout=30)
        if r.status_code != 200:
            msg = ""
            try:
                msg = r.json().get("error", {}).get("message", "")[:80]
            except Exception:
                pass
            raise RuntimeError(f"HTTP {r.status_code} {msg}")
        log(f"  ↳ 网盘已改名：{new}")
    except Exception as e:
        log(f"  ↳ 网盘改名失败（不影响下载）：{e}")


# ---- HTTP 直链 ----------------------------------------------------------------

def download_http(url: str, dest: Path) -> tuple[Path, str]:
    s = _session()
    try:
        r = s.get(url, stream=True, timeout=60)
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            raise OranteError(
                f"直链不是图片（Content-Type: {ctype or '空'}）",
                "换一张图片的直链，或改用 Drive / 本地文件夹。")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(32768):
                if chunk:
                    f.write(chunk)
        if tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise OranteError("直链下到的是空文件", url[:80])
        _move(tmp, dest)
        orig = _filename_from_response(r)
        return dest, orig or url
    except OranteError:
        raise
    except Exception as e:
        raise OranteError(f"直链下载失败：{e}", url[:120]) from e
    finally:
        s.close()


# ---- 落盘 + 校验 --------------------------------------------------------------

def _clear_old_pnn(img_dir: Path) -> None:
    """清掉上次准备留下的 pNN / bNN，含 空镜头/ 里的。"""
    for p in img_dir.iterdir():
        if p.is_file() and re.match(r"^p\d+\.[A-Za-z0-9]+$", p.name, re.I):
            try:
                p.unlink()
            except OSError:
                pass
    broll_dir = img_dir.parent / "空镜头"
    if broll_dir.is_dir():
        for p in broll_dir.iterdir():
            if p.is_file() and re.match(r"^[pb]\d+\.[A-Za-z0-9]+$", p.name, re.I):
                try:
                    p.unlink()
                except OSError:
                    pass


def _finalize(tmp: Path, img_dir: Path, idx: int, hinted_name: str, log) -> Path | None:
    """把临时文件验成图，编号成 pNN.ext；不是图就删掉返回 None。"""
    ext = _ext_from_name(hinted_name) or tmp.suffix.lower()
    if ext not in IMG_EXTS:
        sniffed = _sniff_ext(tmp)
        ext = sniffed or ext
    if ext == ".jpeg":
        ext = ".jpeg"
    elif ext not in IMG_EXTS:
        sniffed = _sniff_ext(tmp)
        ext = sniffed or ""
    if ext not in IMG_EXTS:
        log(f"  ! 不是图片文件，已跳过：{hinted_name or tmp.name}")
        tmp.unlink(missing_ok=True)
        return None
    if PILImage is None:
        size = None
    else:
        try:
            with PILImage.open(tmp) as im:
                im.load()
                size = im.size
        except Exception:
            log(f"  ! 打不开的图片，已跳过：{hinted_name or tmp.name}")
            tmp.unlink(missing_ok=True)
            return None
    if size is not None and min(size) < MIN_SIDE:
        log(f"  ! 图太小 {size[0]}×{size[1]}（任一边 < {MIN_SIDE}px），仍收下：{hinted_name or tmp.name}")
    dest = img_dir / f"p{idx:02d}{ext}"
    if dest.exists():
        dest.unlink()
    _move(tmp, dest)
    return dest


def _one_image(p: str, dest: Path, origin: str, src: str) -> dict:
    return asdict(Image(
        p=p, file=f"images/{dest.name}", sha1=sha1_file(dest),
        origin=origin, src=src,
    ))


# ---- 主入口 ------------------------------------------------------------------

def _move(src, dst):
    """跨磁盘也能用的移动（Path.replace 跨盘会 WinError 17）。"""
    try:
        Path(src).replace(dst)
    except OSError:
        shutil.move(str(src), str(dst))


def fetch(job, task: dict, task_dir: Path) -> list[dict]:
    """把 task['image_source'] 里的图弄到 task_dir/images/pNN.<ext>，返回 Manifest.images。"""
    src = (task.get("image_source") or "").strip()
    kind_task = (task.get("type") or "long").strip().lower()
    if kind_task not in ("long", "short"):
        kind_task = "long"
    task_dir = Path(task_dir)
    img_dir = task_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    _clear_old_pnn(img_dir)

    kind, payload = classify(src)
    job.log(f"图片来源 {kind}：{src[:120]}")
    meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
    recursive = bool(meta.get("image_src_recursive")) if isinstance(meta, dict) else False
    local_mode = _local_mode_of(task)
    token = None
    if kind.startswith("drive"):
        token = get_access_token(job.log)
        if token:
            job.log("Drive：已拿到 OAuth token")
        else:
            job.log("Drive：没有 OAuth，走公开链接")
    rename = bool(_cfg("images.rename_on_drive", False)) and bool(token)

    def _copy_local(src_file: Path):
        def _fn(tmp: Path):
            shutil.copy2(src_file, tmp)
            return tmp, src_file.name
        return _fn

    def _cands_for(kind_, payload_):
        out_c = []
        if kind_ == "local_dir":
            files, skipped = _scan_local_dir(payload_, recursive=recursive)
            if skipped:
                job.log(f"跳过 {skipped} 个非图片或隐藏文件")
            if not files and not recursive:
                deep, _ = _scan_local_dir(payload_, recursive=True)
                if deep:
                    raise OranteError(
                        f"这个文件夹顶层没有图片，子文件夹里有 {len(deep)} 张"
                        "——在「图片来源」那一格点「包含子文件夹」")
            job.log(f"本地文件夹 {len(files)} 张（{_local_mode_label(local_mode, recursive)}）")
            for f in files:
                out_c.append({
                    "origin": "local", "key": None, "src": f.name,
                    "fetch": _copy_local(f), "pre_sha1": sha1_file(f),
                    "src_path": f,
                })
        elif kind_ == "local_file":
            f = payload_
            if f.suffix.lower() not in IMG_EXTS:
                job.log(f"  ! 不是图片文件，已跳过：{f.name}")
            else:
                job.log(f"本地单文件（{_local_mode_label(local_mode)}）")
                out_c.append({
                    "origin": "local", "key": None, "src": f.name,
                    "fetch": _copy_local(f), "pre_sha1": sha1_file(f),
                    "src_path": f,
                })
        elif kind_ == "drive_folder":
            items = list_drive_folder(payload_, job.log, token)
            for fid, name in items:
                out_c.append({
                    "origin": "download", "key": fid,
                    "src": f"https://drive.google.com/file/d/{fid}/view",
                    "fetch": lambda tmp, fid=fid: download_drive_file(fid, tmp, job.log, token),
                    "drive_id": fid, "name": name,
                })
        elif kind_ == "drive_file":
            # open?id= 有时其实是文件夹：先当下文件，失败再按文件夹列
            if token:
                try:
                    meta = requests.get(
                        f"https://www.googleapis.com/drive/v3/files/{payload_}",
                        params={"fields": "mimeType,name", "supportsAllDrives": "true"},
                        headers={"Authorization": "Bearer " + token, "User-Agent": UA},
                        timeout=20)
                    if meta.status_code == 200 and \
                            (meta.json() or {}).get("mimeType") == "application/vnd.google-apps.folder":
                        job.log("open?id= 实际是文件夹，改按文件夹处理")
                        return _cands_for("drive_folder", payload_)
                except Exception:
                    pass
            out_c.append({
                "origin": "download", "key": payload_, "src": src,
                "fetch": lambda tmp, fid=payload_: download_drive_file(fid, tmp, job.log, token),
                "drive_id": payload_, "name": "",
            })
        elif kind_ == "http":
            out_c.append({
                "origin": "download", "key": None, "src": src,
                "fetch": lambda tmp, url=src: download_http(url, tmp),
            })
        return out_c

    cands = _cands_for(kind, payload)
    if not cands:
        raise OranteError("一张图都没拿到", "检查链接是否公开 / 文件夹里有没有图片")

    def _key_of(c):
        return c["key"] or c.get("pre_sha1") or ""

    if kind_task == "short":
        unused = [c for c in cands if _used_count(_key_of(c)) <= 0]
        others = [c for c in cands if c not in unused]
        if unused:
            cands = unused + others
        else:
            random.shuffle(cands)
        job.log("短文案：只取 1 张（优先未用过的；下不到就试下一张）")

    out: list[dict] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix=".orante-img-", dir=str(task_dir)))  # 同一磁盘，rename 才不会跨盘失败
    try:
        idx = 1
        for c in cands:
            job.check()
            tmp = tmp_dir / f"raw-{idx}"
            try:
                got, hinted = c["fetch"](tmp)
                got = Path(got) if got else tmp
                if got != tmp and got.exists():
                    shutil.copy2(got, tmp)
                    if str(got).startswith(str(tmp_dir)):
                        try:
                            got.unlink()
                        except OSError:
                            pass
                if not tmp.exists():
                    job.log(f"  ! 下载结果丢失，跳过：{c.get('src')}")
                    continue
            except OranteError as e:
                job.log(f"  ! {e.message}，跳过")
                if kind == "drive_file" and "文件夹" not in (e.message or ""):
                    # 单文件直下失败：可能其实是文件夹
                    try:
                        extra = _cands_for("drive_folder", payload)
                    except OranteError:
                        extra = []
                    if extra:
                        job.log("单文件下失败，改按文件夹继续")
                        cands.extend(x for x in extra if x not in cands)
                continue
            except Exception as e:
                job.log(f"  ! 取图失败，跳过：{e}")
                continue
            dest = _finalize(tmp, img_dir, idx, hinted or c.get("name") or c.get("src") or "", job.log)
            if dest is None:
                continue
            sha = sha1_file(dest)
            used_key = c.get("key") or sha
            n = _mark_used(used_key)
            if rename and c.get("drive_id"):
                try_rename_on_drive(c["drive_id"], n, token, job.log)
            rec = _one_image(f"p{idx:02d}", dest, c["origin"], c["src"])
            src_path = c.get("src_path")
            if src_path and c.get("origin") == "local":
                _apply_local_mode(Path(src_path), dest, local_mode, job.log)
            out.append(rec)
            job.log(f"  ✓ {rec['p']} ← {c['src'][:80]}")
            if kind_task == "short":
                break
            idx += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if not out:
        raise OranteError("一张图都没拿到", "检查链接是否公开 / 文件夹里有没有图片")
    job.log(f"共收下 {len(out)} 张")
    return out


# ---- 规格 75：批量改描述语 / 打开图片 --------------------------------

_P_OK = re.compile(r"^[pb]\d+$", re.I)


def _task_dir(tid: str) -> Path:
    from .. import tasks as taskmod
    t = taskmod.get(tid)
    if not t.get("dir"):
        raise OranteError("这个任务还没准备", "先在任务面板点「准备」。")
    d = Path(t["dir"])
    if not d.is_dir():
        raise OranteError("任务文件夹不在了", str(d))
    return d


def _norm_p(p: str) -> str:
    s = (p or "").strip()
    m = re.match(r"^([pb])(\d+)$", s, re.I)
    if m:
        return m.group(1).lower() + f"{int(m.group(2)):02d}"
    return s


def _find_im(m: Manifest, p: str):
    want = _norm_p(p)
    for im in m.images or []:
        if _norm_p(im.get("p") or "") == want:
            return im
    return None


def patch(im: dict, fields: dict, by: str | None = None) -> dict:
    """改一张图 dict 的 desc / voice，不存盘。copy_desc 一次事务写完再 save。

    by="user"：界面手改，置 desc_manual / voice_manual。
    Gemini / 扩图 / 普通拷卡片不要传 by，别把自动写的标成手改。
    """
    if not im or not fields:
        return im
    who = by or fields.get("by")
    if "desc" in fields:
        im["desc"] = "" if fields["desc"] is None else str(fields["desc"])
        if who == "user":
            im["desc_manual"] = True
            im.pop("desc_stale", None)
    if "voice" in fields:
        im["voice"] = "" if fields["voice"] is None else str(fields["voice"])
        if who == "user":
            im["voice_manual"] = True
    if "desc_manual" in fields:
        im["desc_manual"] = bool(fields["desc_manual"])
        if not im["desc_manual"]:
            im.pop("desc_stale", None)
    if "voice_manual" in fields:
        im["voice_manual"] = bool(fields["voice_manual"])
    return im


def copy_desc(task_id: str, from_p: str, to_ps, fields=None) -> dict:
    """把源图的 desc / voice 覆盖写到目标。源自己不改。不存在的目标进 skipped。"""
    allowed = [f for f in (fields or ["desc"]) if f in ("desc", "voice")]
    if not allowed:
        raise OranteError("fields 只能含 desc / voice")
    src_p = _norm_p(from_p)
    if not src_p:
        raise OranteError("缺少源图片")
    d = _task_dir(task_id)
    m = Manifest.load(d)
    src = _find_im(m, src_p)
    if not src:
        raise OranteError(f"没有图片 {from_p}")
    src_key = _norm_p(src.get("p") or src_p)
    changed = []
    skipped = []
    seen = set()
    for raw in to_ps or []:
        p = _norm_p(raw)
        if not p or p in seen:
            continue
        seen.add(p)
        if p == src_key:
            continue
        im = _find_im(m, p)
        if not im:
            skipped.append({"p": p, "why": "不存在"})
            continue
        if (im.get("status") or "") == "missing":
            skipped.append({"p": p, "why": "已删除"})
            continue
        rec = {
            "p": im.get("p") or p,
            "desc_old": im.get("desc") or "",
            "voice_old": im.get("voice") or "",
            "desc_manual_old": bool(im.get("desc_manual")),
            "voice_manual_old": bool(im.get("voice_manual")),
        }
        payload = {}
        if "desc" in allowed:
            payload["desc"] = src.get("desc") or ""
        if "voice" in allowed:
            payload["voice"] = src.get("voice") or ""
        patch(im, payload)
        # 75 号同步是用户明确下的指令：可以盖手改的，盖完目标也算手改
        if "desc" in allowed:
            im["desc_manual"] = True
            im.pop("desc_stale", None)
        if "voice" in allowed:
            im["voice_manual"] = True
        changed.append(rec)
    m.save(d)
    from .. import tasks as taskmod
    taskmod.publish_counts(task_id, d, m)
    return {"n": len(changed), "changed": changed, "skipped": skipped}


def restore_desc(task_id: str, items) -> dict:
    """用 copy_desc 返回的 changed 旧值写回，一次存盘。"""
    d = _task_dir(task_id)
    m = Manifest.load(d)
    n = 0
    for it in items or []:
        p = (it or {}).get("p")
        im = _find_im(m, p) if p else None
        if not im:
            continue
        payload = {}
        if "desc_old" in it:
            payload["desc"] = it.get("desc_old") or ""
        elif "desc" in it:
            payload["desc"] = it.get("desc") or ""
        if "voice_old" in it:
            payload["voice"] = it.get("voice_old") or ""
        elif "voice" in it:
            payload["voice"] = it.get("voice") or ""
        if "desc_manual_old" in it:
            payload["desc_manual"] = bool(it.get("desc_manual_old"))
        if "voice_manual_old" in it:
            payload["voice_manual"] = bool(it.get("voice_manual_old"))
        if not payload:
            continue
        patch(im, payload)
        n += 1
    m.save(d)
    from .. import tasks as taskmod
    taskmod.publish_counts(task_id, d, m)
    return {"n": n}


def _reject_p(p: str) -> str | None:
    """p 带路径穿越或不是图号 → 拒绝原因。先挡再碰磁盘。"""
    s = (p or "").strip()
    if not s:
        return "缺少图片编号"
    if ".." in s or "/" in s or "\\" in s or "\x00" in s:
        return "路径不在任务目录内"
    if not _P_OK.match(s):
        return "路径不在任务目录内"
    return None


def resolve_image_path(task_id: str, p: str) -> Path:
    """图号 → 任务目录内的真实文件。越界 / 不存在都抛 OranteError，不执行打开命令。"""
    why = _reject_p(p)
    if why:
        raise OranteError(why)
    d = _task_dir(task_id).resolve()
    m = Manifest.load(d)
    im = _find_im(m, p)
    if not im:
        raise OranteError(f"没有图片 {p}")
    rel = (im.get("file") or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise OranteError("路径不在任务目录内")
    path = (d / rel).resolve()
    if d not in path.parents:
        raise OranteError("路径不在任务目录内")
    if not path.is_file():
        raise OranteError("文件不存在")
    return path


def _start_open(path: Path) -> None:
    """系统默认程序打开。平台分支照抄 server.py /api/system/open-logs。"""
    if os.name == "nt":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _start_reveal(path: Path) -> None:
    """资源管理器里选中这个文件。平台分支照抄 /api/tasks/{tid}/reveal。"""
    if paths.IS_WIN:
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif paths.IS_MAC:
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def open_image(task_id: str, p: str) -> dict:
    path = resolve_image_path(task_id, p)
    _start_open(path)
    return {"path": str(path)}


def reveal_image(task_id: str, p: str) -> dict:
    path = resolve_image_path(task_id, p)
    _start_reveal(path)
    return {"path": str(path)}


def batch_image_index() -> list[dict]:
    """批量填描述语用：每个已准备任务自己的全部图片。

    不看多模态当前打开的是哪一个。只读 manifest，不跑空镜同步，不改盘。
    """
    from .. import db
    from ..models import Manifest

    rows = db.rows(
        "SELECT id, name, status, dir, copy FROM tasks ORDER BY row_order, created")
    out = []
    for t in rows or []:
        st = (t.get("status") or "").strip()
        if st in ("draft", "trash"):
            continue
        d = t.get("dir") or ""
        if not d or not Path(d).is_dir():
            continue
        try:
            m = Manifest.load(Path(d))
        except Exception:
            continue
        copy = t.get("copy") or ""
        if isinstance(copy, dict):
            copy = copy.get("text") or ""
        imgs = []
        for im in annotate_images(Path(d), list(m.images or [])):
            if not im.get("p"):
                continue
            if (im.get("status") or "") == "missing":
                continue
            imgs.append({
                "p": im.get("p") or "",
                "desc": im.get("desc") or "",
                "role": im.get("role") or "主体",
                "file": im.get("file") or "",
                "mtime": im.get("mtime") or 0,
                "exists": bool(im.get("exists")),
                "prompt_node_id": im.get("prompt_node_id") or "",
                "prompt_node_name": im.get("prompt_node_name") or "",
            })
        out.append({
            "tid": t.get("id") or "",
            "name": t.get("name") or "",
            "status": st,
            "copy": str(copy or ""),
            "images": imgs,
        })
    return out


DEFAULT_PROMPT_NODES: list[dict] = [
    {
        "id": "D01",
        "name": "D01 · 竖起大拇指赞同",
        "color": "#F59E0B",
        "desc": (
            "人物正专注认真地倾听视频内容，眼中流露出由衷的认同与赞许；自然地抬起一只手对着镜头竖起大拇指表示强烈赞同，同时温和地点头微笑，随后手部自然放松回落，呼吸与肩部微动真实自然。人物嘴唇全程保持自然闭合，绝不说话、绝不张嘴、不做任何说话口型。\n"
            "The person is attentively listening to a video with genuine agreement and appreciation, naturally raising one hand to give a warm thumbs-up gesture while nodding gently with a warm smile, then relaxing their hand naturally. The mouth remains calm and closed the entire time with strictly no speaking, no talking, and no lip movement."
        ),
    },
    {
        "id": "D02",
        "name": "D02 · 手捂胸口深表感动",
        "color": "#EC4899",
        "desc": (
            "人物正全神贯注地倾听视频，内心深受触动与感动；一只手自然轻放在胸口（心口）位置并轻轻抚按，配合深表共鸣的温和点头与自然眨眼，眼神温暖虔诚，肩膀与手臂随呼吸自然微动，绝不僵硬。人物嘴唇全程保持自然闭合，绝不说话、绝不张嘴、不做任何说话口型。\n"
            "The person is listening deeply and attentively, genuinely moved and touched at heart; one hand rests or gently presses over their chest/heart in heartfelt resonance while nodding warmly and blinking naturally, with relaxed breathing and subtle shoulder motion. The mouth stays calm and closed throughout with strictly no speaking and no lip movement."
        ),
    },
    {
        "id": "D03",
        "name": "D03 · 频频点头深表认同",
        "color": "#10B981",
        "desc": (
            "人物正认真专注地观看倾听视频，十分赞同所听到的内容；头部随着倾听节奏自然温和地连续点头两三次，眼神明亮专注，偶尔自然眨眼，身体微微前倾，双肩与搁放的手臂有真实自然的轻微放松动作。人物嘴唇全程保持自然闭合，绝不说话、绝不张嘴、不做任何说话口型。\n"
            "The person is watching and listening intently, nodding their head warmly and naturally two or three times in strong agreement with what they hear, with bright attentive eyes, natural blinking, a slight forward lean, and relaxed subtle arm and shoulder movement. The mouth remains completely closed and silent with strictly no speaking or lip movement."
        ),
    },
    {
        "id": "D04",
        "name": "D04 · 微笑倾听双手微动",
        "color": "#3B82F6",
        "desc": (
            "人物正专注倾听视频，脸上浮现温暖会心的微笑，表现出深深的理解与赞同；双手与胳膊在桌面或身前做自然放松的小幅姿态调整，配合轻轻点头与自然眨眼，充满真实人物视频的生动感。人物嘴唇全程保持自然闭合，绝不说话、绝不张嘴、不做任何说话口型。\n"
            "The person is listening attentively with a warm, understanding smile, showing natural lifelike presence as their hands and arms make a small, relaxed posture adjustment in front of them, accompanied by a gentle agreeing nod and natural blinking. The mouth stays calm and closed the entire time with strictly no speaking or lip movement."
        ),
    },
    {
        "id": "D05",
        "name": "D05 · 托腮/交握静思共鸣",
        "color": "#8B5CF6",
        "desc": (
            "人物保持托腮或双手交握的自然倾听姿态，目光温和专注地看着前方，随着听到的内容若有所思地轻轻点头表示赞同，手指与手腕有极其自然的轻微放松微动，呼吸平稳真实，绝不像静态照片。人物嘴唇全程保持自然闭合，绝不说话、绝不张嘴、不做任何说话口型。\n"
            "Keeping a natural listening posture with hands clasped or resting near the chin, the person gazes warmly and attentively ahead, nodding thoughtfully in quiet agreement with subtle, relaxed finger and wrist micro-movements and natural breathing. The mouth remains calm and closed throughout with strictly no speaking or lip movement."
        ),
    },
    {
        "id": "D06",
        "name": "D06 · 侧首微笑欣然赞许",
        "color": "#06B6D4",
        "desc": (
            "人物正认真倾听视频，听到精辟之处欣然微笑，头部自然微微侧倾并温和点头表示高度认可，眉毛微微舒展，胳膊与手部姿态自然放松微动，整体神态生动自然。人物嘴唇全程保持自然闭合，绝不说话、绝不张嘴、不做任何说话口型。\n"
            "The person listens attentively and smiles warmly in appreciation, tilting their head slightly and giving a gentle, approving nod with relaxed eyebrow expression and natural subtle arm movement. The mouth stays completely calm and closed with strictly no speaking or lip movement."
        ),
    },
    {
        "id": "D07",
        "name": "D07 · 车内静止专注点头",
        "color": "#F97316",
        "desc": (
            "人物坐在完全停稳的车内专注倾听视频，车窗外的所有景物、树木与车辆保持100%静止不动（车辆处于泊车静止状态）；人物神情温和专注，随着内容轻轻点头表示赞同，扶在方向盘或安全带旁的手臂自然放松微动。人物嘴唇全程保持自然闭合，绝不说话、绝不张嘴、不做任何说话口型。\n"
            "Sitting inside a completely parked, stationary car with everything outside the windows 100% still and motionless, the person listens attentively and nods gently in warm agreement while their arm and hand rest naturally with subtle lifelike relaxation. The mouth remains closed and silent throughout with strictly no speaking or lip movement."
        ),
    },
    {
        "id": "D08",
        "name": "D08 · 抬手示意后自然回落",
        "color": "#14B8A6",
        "desc": (
            "人物顺着第一帧的抬手或扶镜框姿态，自然地完成一个简短温和的认同示意（若戴眼镜则眼镜稳固戴在鼻梁上绝不变形或摘下），随后手臂顺畅自然地放松回落，同时温和点头微笑表示赞同，绝不僵硬举手定格。人物嘴唇全程保持自然闭合，绝不说话、绝不张嘴、不做任何说话口型。\n"
            "Following the initial raised-hand pose in the first frame, the person completes a brief, natural acknowledging gesture (keeping any eyeglasses solid and intact on the nose bridge without deforming or removing them) and then smoothly relaxes and lowers their hand naturally while nodding warmly in agreement. The mouth stays calm and closed with strictly no speaking or lip movement."
        ),
    },
]


def get_prompt_nodes() -> list[dict]:
    from .. import config
    saved = config.get("prompts.desc_nodes", None)
    if isinstance(saved, list) and saved:
        out = []
        for i, n in enumerate(saved):
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id") or f"D{i+1:02d}").strip()
            out.append({
                "id": nid,
                "name": str(n.get("name") or nid).strip(),
                "color": str(n.get("color") or "#F59E0B").strip(),
                "desc": str(n.get("desc") or "").strip(),
                "voice": str(n.get("voice") or "").strip(),
            })
        if out:
            return out
    return [dict(x) for x in DEFAULT_PROMPT_NODES]


def save_prompt_nodes(nodes: list[dict] | None) -> list[dict]:
    from .. import config
    if not nodes:
        config.set("prompts.desc_nodes", [dict(x) for x in DEFAULT_PROMPT_NODES])
        return get_prompt_nodes()
    palette = ["#F59E0B", "#EC4899", "#10B981", "#3B82F6", "#8B5CF6", "#06B6D4", "#F97316", "#14B8A6", "#EAB308", "#F43F5E"]
    cleaned = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or f"D{i+1:02d}").strip()
        cleaned.append({
            "id": nid,
            "name": str(n.get("name") or nid).strip(),
            "color": str(n.get("color") or palette[i % len(palette)]).strip(),
            "desc": str(n.get("desc") or "").strip(),
            "voice": str(n.get("voice") or "").strip(),
        })
    config.set("prompts.desc_nodes", cleaned)
    return cleaned


def batch_desc(items, desc: str = "", voice=None, mode: str = "fill_empty",
               *, node_id: str | None = None, node_name: str | None = None,
               connections: list[dict] | None = None) -> dict:
    """跨任务批量写入描述语（规格 85 + 节点连线模式支持）。

    items: [{"tid": "...", "ps": ["p01", ...]}, ...]
    connections: [{"node_id": "D01", "node_name": "...", "desc": "...", "voice": ..., "items": [{"tid": "...", "ps": [...]}]}]
    mode: fill_empty | overwrite
    voice: None=不动音色；字符串（含空串）=写入
    每个任务一次拿锁、一次 _save_unlocked。拿不到锁的任务进 skipped，其余照写。
    """
    from ..errors import OranteError
    from ..filelock import manifest_lock
    from ..models import Manifest
    from .. import tasks as taskmod

    if mode not in ("fill_empty", "overwrite"):
        raise OranteError("mode 只能是 fill_empty 或 overwrite")

    if isinstance(connections, list) and connections:
        groups = []
        for conn in connections:
            if not isinstance(conn, dict):
                continue
            c_desc = str(conn.get("desc") or "").strip()
            c_items = conn.get("items") or []
            if not c_desc or not c_items:
                continue
            groups.append({
                "items": c_items,
                "desc": c_desc,
                "voice": conn["voice"] if "voice" in conn else voice,
                "node_id": str(conn.get("node_id") or "").strip() or None,
                "node_name": str(conn.get("node_name") or "").strip() or None,
            })
        if not groups:
            raise OranteError("请先将图片连接到至少一个有描述语内容的节点")
    else:
        desc = "" if desc is None else str(desc)
        if not str(desc).strip():
            raise OranteError("描述语不能为空", "先在右边写好要填的描述语。")
        if not isinstance(items, list) or not items:
            raise OranteError("请至少选一个任务和图片")
        groups = [{
            "items": items,
            "desc": desc,
            "voice": voice,
            "node_id": str(node_id or "").strip() or None,
            "node_name": str(node_name or "").strip() or None,
        }]

    # 按 tid 汇总所有 (pth, desc, voice, node_id, node_name)
    by_tid: dict[str, list[dict]] = {}
    for g in groups:
        for raw in g["items"]:
            if not isinstance(raw, dict):
                continue
            tid = str(raw.get("tid") or "").strip()
            ps = list(raw.get("ps") or [])
            if not tid or not ps:
                continue
            try:
                trow = taskmod.get(tid)
            except Exception:
                raise OranteError(f"没有任务 {tid}")
            if (trow.get("status") or "draft") == "draft":
                raise OranteError(
                    f"任务 {tid} 还是草稿",
                    "草稿要先准备好才能填描述语。请从列表里去掉草稿任务。",
                )
            for pth in ps:
                by_tid.setdefault(tid, []).append({
                    "p": pth,
                    "desc": g["desc"],
                    "voice": g["voice"],
                    "node_id": g["node_id"],
                    "node_name": g["node_name"],
                })

    changed = []
    skipped = []
    touched_tasks = set()

    for tid, ops in by_tid.items():
        try:
            taskmod.ensure_writable(tid)
        except OranteError as e:
            why = getattr(e, "msg", None) or getattr(e, "args", [str(e)])[0]
            for op in ops:
                skipped.append({"tid": tid, "p": _norm_p(op["p"]) or str(op["p"]), "why": str(why)})
            continue

        d = _task_dir(tid)
        try:
            lock = manifest_lock(d, timeout=0.35)
            lock.acquire()
        except OranteError:
            for op in ops:
                skipped.append({"tid": tid, "p": _norm_p(op["p"]) or str(op["p"]), "why": "清单正被占用"})
            continue

        try:
            m = Manifest.load(d)
            wrote = False
            touched_ps = set()
            for op in ops:
                pth = _norm_p(op["p"])
                if not pth:
                    continue
                im = _find_im(m, pth)
                if not im:
                    skipped.append({"tid": tid, "p": pth, "why": "不存在"})
                    continue
                if (im.get("status") or "") == "missing":
                    skipped.append({"tid": tid, "p": pth, "why": "已删除"})
                    continue
                old_desc = im.get("desc") or ""
                old_voice = im.get("voice") or ""
                if mode == "fill_empty" and str(old_desc).strip():
                    skipped.append({"tid": tid, "p": pth, "why": "已有描述语"})
                    continue
                rec = {
                    "tid": tid,
                    "p": im.get("p") or pth,
                    "desc_old": old_desc,
                    "voice_old": old_voice,
                    "desc_manual_old": bool(im.get("desc_manual")),
                    "voice_manual_old": bool(im.get("voice_manual")),
                }
                patch(im, {"desc": op["desc"]}, by="user")
                if op["voice"] is not None:
                    patch(im, {"voice": str(op["voice"])}, by="user")
                if op.get("node_id"):
                    im["prompt_node_id"] = op["node_id"]
                if op.get("node_name"):
                    im["prompt_node_name"] = op["node_name"]
                changed.append(rec)
                touched_ps.add(im.get("p") or pth)
                wrote = True
            if wrote:
                for sg in getattr(m, "segments", None) or []:
                    if (sg.get("image") or "") in touched_ps and not sg.get("prompt_locked"):
                        sg["prompt_final"] = ""
                        sg.pop("prompt_snap", None)
                # 外层已持有 .manifest.lock，禁止再走 m.save（会再次加锁，Windows 下 PermissionError）
                m._save_unlocked(d)
                touched_tasks.add(tid)
        finally:
            try:
                lock.release()
            except Exception:
                pass

    _publish_desc_counts(touched_tasks)
    return {
        "ok": True,
        "n_tasks": len(touched_tasks),
        "n_images": len(changed),
        "changed": changed,
        "skipped": skipped,
    }


def restore_batch_desc(changed) -> dict:
    """按 batch_desc 返回的 changed 旧值跨任务回滚。"""
    from ..filelock import manifest_lock
    from ..models import Manifest
    from .. import tasks as taskmod

    if not isinstance(changed, list) or not changed:
        return {"ok": True, "n": 0}
    by_tid: dict[str, list] = {}
    for it in changed:
        if not isinstance(it, dict):
            continue
        tid = str(it.get("tid") or "").strip()
        if tid:
            by_tid.setdefault(tid, []).append(it)
    n = 0
    for tid, items in by_tid.items():
        taskmod.ensure_writable(tid)
        d = _task_dir(tid)
        with manifest_lock(d):
            m = Manifest.load(d)
            for it in items:
                pth = _norm_p(it.get("p"))
                im = _find_im(m, pth) if pth else None
                if not im:
                    continue
                fields = {}
                if "desc_old" in it:
                    fields["desc"] = it.get("desc_old") or ""
                if "voice_old" in it:
                    fields["voice"] = it.get("voice_old") or ""
                if fields:
                    patch(im, fields)
                if "desc_manual_old" in it:
                    im["desc_manual"] = bool(it.get("desc_manual_old"))
                    if not im["desc_manual"]:
                        im.pop("desc_stale", None)
                if "voice_manual_old" in it:
                    im["voice_manual"] = bool(it.get("voice_manual_old"))
                n += 1
            m._save_unlocked(d)
    _publish_desc_counts(by_tid.keys())
    return {"ok": True, "n": n}


def _publish_desc_counts(task_ids) -> None:
    """批量写完描述语后，让生成页放下「没描述语」那条。"""
    from .. import tasks as taskmod
    from ..models import Manifest
    for tid in task_ids or []:
        try:
            t = taskmod.get(str(tid))
            d = Path(t.get("dir") or "")
            if not d.is_dir():
                continue
            taskmod.publish_counts(str(tid), d, Manifest.load(d))
        except Exception:
            continue
