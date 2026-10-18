# -*- coding: utf-8 -*-
"""从 Chrome profile 抽出登录态（cookie + Local Storage），再写进另一台机器的 profile。

Chrome 的 cookie 值用 DPAPI 绑当前 Windows 用户。导出必须先解成明文，
导入时用新机器的 DPAPI 重新加密。解不开就标「需重登」，不要塞乱码假装成功。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import struct
import tempfile
from pathlib import Path

from ..errors import OranteError
from ..engines import chrome

# 规格点名的域 + Grok 实际登录用的 x.ai。host 用后缀匹配（.grok.com 也算）。
_HOST_SUFFIXES = (
    "grok.com",
    "google.com",
    "flow.google.com",
    "labs.google",
    "labs.google.com",
    "x.ai",
)

# Local Storage 的 origin 里同样这几个（含 scheme）。
_LS_NEEDLES = (
    "grok.com",
    "google.com",
    "flow.google.com",
    "labs.google",
    "x.ai",
)

_COOKIES_REL = (
    Path("Default") / "Network" / "Cookies",
    Path("Default") / "Cookies",
)
_LS_REL = Path("Default") / "Local Storage" / "leveldb"

_COOKIE_COLS = (
    "creation_utc", "host_key", "top_frame_site_key", "name", "value",
    "encrypted_value", "path", "expires_utc", "is_secure", "is_httponly",
    "last_access_utc", "has_expires", "is_persistent", "priority", "samesite",
    "source_scheme", "source_port", "last_update_utc", "source_type",
    "has_cross_site_ancestor",
)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii") if isinstance(s, str) else s)


def host_ok(host: str) -> bool:
    h = (host or "").lower().lstrip(".")
    if not h:
        return False
    for d in _HOST_SUFFIXES:
        if h == d or h.endswith("." + d):
            return True
        if d == "labs.google" and h.startswith("labs.google"):
            return True
    return False


def ls_key_ok(key: bytes) -> bool:
    try:
        s = key.decode("utf-8", "replace").lower()
    except Exception:
        s = ""
    if any(n in s for n in _LS_NEEDLES):
        return True
    try:
        s16 = key.decode("utf-16le", "replace").lower()
    except Exception:
        s16 = ""
    return any(n in s16 for n in _LS_NEEDLES)


def _copy_sqlite(src: Path) -> Path:
    """Chrome 开着时 Cookies 被独占锁，复制一份再读。连 -wal 一起拷。"""
    tmp = Path(tempfile.mkdtemp(prefix="orante-ck-"))
    dst = tmp / "Cookies"
    shutil.copy2(src, dst)
    for suf in ("-wal", "-journal", "-shm"):
        p = Path(str(src) + suf)
        if p.is_file():
            try:
                shutil.copy2(p, Path(str(dst) + suf))
            except OSError:
                pass
    return dst


def cookies_path(profile: Path) -> Path | None:
    for rel in _COOKIES_REL:
        p = profile / rel
        if p.is_file():
            return p
    return None


def _chrome_aes_key(profile: Path) -> bytes:
    """Local State 里 os_crypt.encrypted_key → DPAPI 解开 → 32 字节 AES 密钥。"""
    ls = profile / "Local State"
    if not ls.is_file():
        raise OranteError("这个 Chrome profile 没有 Local State", str(ls))
    try:
        data = json.loads(ls.read_text(encoding="utf-8"))
    except Exception as e:
        raise OranteError("Chrome Local State 读不出来", str(e))
    blob_b64 = ((data.get("os_crypt") or {}).get("encrypted_key") or "")
    if not blob_b64:
        raise OranteError("Chrome Local State 里没有加密密钥")
    try:
        blob = base64.b64decode(blob_b64)
    except Exception:
        raise OranteError("Chrome 加密密钥不是合法的 base64")
    if blob.startswith(b"DPAPI"):
        blob = blob[5:]
    return chrome.dpapi_unprotect(blob)


def ensure_chrome_key(profile: Path) -> bytes:
    """已有 Local State 就用它的密钥；没有就新建一把，用这台机器的 DPAPI 包好。"""
    ls = profile / "Local State"
    if ls.is_file():
        return _chrome_aes_key(profile)
    profile.mkdir(parents=True, exist_ok=True)
    aes_key = os.urandom(32)
    wrapped = b"DPAPI" + chrome.dpapi_protect(aes_key)
    payload = {"os_crypt": {"encrypted_key": _b64(wrapped)}}
    # 已有的 Local State 不能覆盖；这里是新建。
    ls.write_text(json.dumps(payload), encoding="utf-8")
    return aes_key


def decrypt_cookie_value(enc: bytes, key: bytes) -> bytes:
    if not enc:
        return b""
    if enc.startswith((b"v10", b"v11")):
        nonce, ct = enc[3:15], enc[15:]
        if len(nonce) != 12 or len(ct) < 16:
            raise ValueError("cookie 密文太短")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(nonce, ct, None)
    if enc.startswith(b"v20"):
        raise ValueError("v20 应用绑定加密，这台机器解不开")
    # 极老的明文
    return enc


def encrypt_cookie_value(plain: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return b"v10" + nonce + AESGCM(key).encrypt(nonce, plain, None)


# ---------------- LevelDB（Local Storage） ----------------
_LDB_BLOCK = 32768
_LDB_HEADER = 7
_LDB_MAGIC = 0xDB4775248B80FB57


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if b < 0x80:
            return n, i
        shift += 7
        if shift > 63:
            break
    raise ValueError("varint")


def _put_varint(n: int) -> bytes:
    out = bytearray()
    n = int(n)
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _iter_log_records(data: bytes):
    i, n = 0, len(data)
    buf = b""
    while i + _LDB_HEADER <= n:
        off = i % _LDB_BLOCK
        if off + _LDB_HEADER > _LDB_BLOCK:
            i += _LDB_BLOCK - off
            continue
        length = int.from_bytes(data[i + 4:i + 6], "little")
        typ = data[i + 6]
        i += _LDB_HEADER
        if length == 0 and typ == 0:
            if i % _LDB_BLOCK:
                i += _LDB_BLOCK - (i % _LDB_BLOCK)
            continue
        rec = data[i:i + length]
        i += length
        if typ == 1:
            yield rec
        elif typ == 2:
            buf = rec
        elif typ == 3:
            buf += rec
        elif typ == 4:
            yield buf + rec
            buf = b""


def _parse_batch(rec: bytes):
    if len(rec) < 12:
        return
    count = int.from_bytes(rec[8:12], "little")
    i = 12
    for _ in range(count):
        if i >= len(rec):
            return
        tag = rec[i]
        i += 1
        try:
            klen, i = _varint(rec, i)
        except ValueError:
            return
        key = rec[i:i + klen]
        i += klen
        if tag == 1:
            try:
                vlen, i = _varint(rec, i)
            except ValueError:
                return
            val = rec[i:i + vlen]
            i += vlen
            yield "put", key, val
        elif tag in (0, 2):
            yield "del", key, None
        else:
            return


_CRC32C_TAB: list[int] | None = None


def _crc32c_castagnoli(data: bytes) -> int:
    global _CRC32C_TAB
    if _CRC32C_TAB is None:
        tab = []
        for i in range(256):
            crc = i
            for _ in range(8):
                crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
            tab.append(crc)
        _CRC32C_TAB = tab
    crc = 0xFFFFFFFF
    tab = _CRC32C_TAB
    for b in data:
        crc = tab[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def _mask_crc(crc: int) -> int:
    # leveldb/util/crc32c.h：Rotate right 15 bits, add 0xa282ead8
    crc &= 0xFFFFFFFF
    return (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def _write_log_record(typ: int, rec: bytes, pos: int) -> tuple[bytes, int]:
    """写一条 log 记录，必要时按 32KB 块切开。返回 (bytes, 新 pos)。"""
    out = bytearray()
    left = rec
    first = True
    while True:
        leftover = _LDB_BLOCK - (pos % _LDB_BLOCK)
        if leftover < _LDB_HEADER:
            out.extend(b"\x00" * leftover)
            pos += leftover
            leftover = _LDB_BLOCK
        avail = leftover - _LDB_HEADER
        chunk, left = left[:avail], left[avail:]
        if first and not left:
            t = 1  # FULL
        elif first:
            t = 2  # FIRST
        elif not left:
            t = 4  # LAST
        else:
            t = 3  # MIDDLE
        first = False
        header_wo_crc = struct.pack("<HB", len(chunk), t)
        crc = _mask_crc(_crc32c_castagnoli(bytes([t]) + chunk))
        out.extend(struct.pack("<I", crc))
        out.extend(header_wo_crc)
        out.extend(chunk)
        pos += _LDB_HEADER + len(chunk)
        if not left:
            return bytes(out), pos


def _write_batch(puts: list[tuple[bytes, bytes]], seq0: int = 1) -> bytes:
    body = bytearray()
    body.extend(struct.pack("<Q", seq0))
    body.extend(struct.pack("<I", len(puts)))
    for k, v in puts:
        body.append(1)  # kTypeValue
        body.extend(_put_varint(len(k)))
        body.extend(k)
        body.extend(_put_varint(len(v)))
        body.extend(v)
    rec, _pos = _write_log_record(1, bytes(body), 0)
    return rec


def _write_version_edit(log_number: int, next_file: int, last_seq: int) -> bytes:
    # tag 1 comparator string, 2 log number, 3 next file, 4 last sequence
    payload = bytearray()
    payload.append(1)
    name = b"leveldb.BytewiseComparator"
    payload.extend(_put_varint(len(name)))
    payload.extend(name)
    payload.append(2)
    payload.extend(_put_varint(log_number))
    payload.append(3)
    payload.extend(_put_varint(next_file))
    payload.append(4)
    payload.extend(_put_varint(last_seq))
    rec, _ = _write_log_record(1, bytes(payload), 0)
    return rec


def write_leveldb(dir_path: Path, records: list[tuple[bytes, bytes]]) -> None:
    """写一个最小可被 Chrome 打开的 LevelDB（一个 log + MANIFEST）。"""
    if dir_path.is_dir():
        shutil.rmtree(dir_path, ignore_errors=True)
    dir_path.mkdir(parents=True, exist_ok=True)
    log_no, next_no, seq = 2, 3, max(1, len(records))
    (dir_path / "CURRENT").write_bytes(b"MANIFEST-000001\n")
    (dir_path / "MANIFEST-000001").write_bytes(
        _write_version_edit(log_no, next_no, seq))
    (dir_path / "000002.log").write_bytes(_write_batch(records, 1))
    (dir_path / "LOCK").write_bytes(b"")
    (dir_path / "LOG").write_bytes(b"")


def read_leveldb(dir_path: Path) -> list[tuple[bytes, bytes]]:
    """读 .log；.ldb 只在未压缩时尽量读。后写覆盖先写。"""
    latest: dict[bytes, bytes | None] = {}
    if not dir_path.is_dir():
        return []
    files = [f for f in dir_path.iterdir() if f.is_file()]
    ldbs = sorted(f for f in files if f.suffix in (".ldb", ".sst"))
    logs = sorted(f for f in files if f.suffix == ".log")
    # log 是最新的写入，放最后，避免误解析的 ldb 把记录盖掉
    for f in ldbs + logs:
        if f.suffix == ".log":
            try:
                data = f.read_bytes()
            except OSError:
                continue
            for rec in _iter_log_records(data):
                for op, k, v in _parse_batch(rec):
                    latest[k] = v if op == "put" else None
        else:
            try:
                _read_ldb_into(f, latest)
            except Exception:
                pass
    return [(k, v) for k, v in latest.items() if v is not None]


def _read_ldb_into(path: Path, latest: dict) -> None:
    """尽力解析未压缩的 table。压缩块（snappy）直接跳过。"""
    data = path.read_bytes()
    if len(data) < 48:
        return
    magic = int.from_bytes(data[-8:], "little")
    if magic != _LDB_MAGIC:
        return
    # footer 前 40 字节是两个 block handle。从文件头扫 data block 不稳，
    # 改走 index：footer 里 index handle 在 metaindex 后面。
    i = len(data) - 48
    try:
        _moff, i2 = _varint(data, i)
        _msz, i2 = _varint(data, i2)
        ioff, i2 = _varint(data, i2)
        isz, _ = _varint(data, i2)
    except ValueError:
        return
    index = _read_block(data, ioff, isz)
    if not index:
        return
    # index block：key + handle；我们只要每个 data block 的 handle
    j = 0
    restart_off = int.from_bytes(index[-4:], "little") if len(index) >= 4 else 0
    body = index[:-4 - 4 * restart_off - 4] if restart_off and len(index) > 8 else index
    # 简化：在整块里找 varint 对当作 handle 不可靠。改成按 restart 解析。
    _parse_index_blocks(data, index, latest)


def _parse_index_blocks(data: bytes, index: bytes, latest: dict) -> None:
    if len(index) < 8:
        return
    nre = int.from_bytes(index[-4:], "little")
    if nre <= 0 or nre > 100000:
        return
    restart_off = len(index) - 4 - 4 * nre
    if restart_off < 0:
        return
    body = index[:restart_off]
    i = 0
    key = b""
    while i < len(body):
        try:
            shared, i = _varint(body, i)
            unsh, i = _varint(body, i)
            vlen, i = _varint(body, i)
        except ValueError:
            return
        key = key[:shared] + body[i:i + unsh]
        i += unsh
        handle = body[i:i + vlen]
        i += vlen
        try:
            off, h = _varint(handle, 0)
            sz, _ = _varint(handle, h)
        except ValueError:
            continue
        blk = _read_block(data, off, sz)
        if blk:
            _parse_data_block(blk, latest)


def _snappy_decode(block: bytes) -> bytes | None:
    """LevelDB 的 snappy 块。有 cramjam 就用，没有就放弃这一块。"""
    try:
        import cramjam
        return bytes(cramjam.snappy.decompress_raw(block))
    except Exception:
        pass
    try:
        import snappy  # type: ignore
        return snappy.decompress(block)
    except Exception:
        return None


def _read_block(data: bytes, off: int, sz: int) -> bytes | None:
    # trailer 5 字节：type(1) + crc(4)
    end = off + sz + 5
    if off < 0 or end > len(data):
        return None
    block, trailer = data[off:off + sz], data[off + sz:end]
    if len(trailer) < 5:
        return None
    kind = trailer[0]
    if kind == 0:
        return block
    if kind == 1:  # snappy
        return _snappy_decode(block)
    return None


def _parse_data_block(block: bytes, latest: dict) -> None:
    if len(block) < 8:
        return
    nre = int.from_bytes(block[-4:], "little")
    if nre <= 0 or nre > 1_000_000:
        return
    restart_off = len(block) - 4 - 4 * nre
    if restart_off < 0:
        return
    body = block[:restart_off]
    i = 0
    key = b""
    while i < len(body):
        try:
            shared, i = _varint(body, i)
            unsh, i = _varint(body, i)
            vlen, i = _varint(body, i)
        except ValueError:
            return
        key = key[:shared] + body[i:i + unsh]
        i += unsh
        val = body[i:i + vlen]
        i += vlen
        # internal key：末尾 8 字节 = seq(7)+type(1)
        if len(key) < 8:
            continue
        ukey, typ = key[:-8], key[-1]
        if typ == 1:
            latest[ukey] = val
        elif typ == 0:
            latest[ukey] = None


# ---------------- 抽出 / 写入 ----------------

def extract_account(profile: Path) -> dict:
    """从 profile 抽出明文 cookie + 过滤后的 Local Storage。

    返回 {ok, cookies, local_storage, error, n_skip}。
    解不开密钥或一个明文都拿不到 → ok=False，cookies 为空。
    """
    out = {"ok": False, "cookies": [], "local_storage": [], "error": None, "n_skip": 0}
    ck = cookies_path(profile)
    if ck is None:
        out["error"] = "这个账号还没有 Cookies 文件（没登录过）"
        return out
    try:
        key = _chrome_aes_key(profile)
    except OranteError as e:
        out["error"] = e.message
        return out
    except Exception as e:
        out["error"] = f"拿不到 Chrome 密钥：{e}"
        return out

    tmp = None
    try:
        tmp = _copy_sqlite(ck)
        con = sqlite3.connect(str(tmp))
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute("SELECT * FROM cookies").fetchall()
        finally:
            con.close()
    except Exception as e:
        out["error"] = f"Cookies 数据库读不出来：{e}"
        return out
    finally:
        if tmp is not None:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    got, skip = [], 0
    for r in rows:
        host = r["host_key"] if "host_key" in r.keys() else ""
        if not host_ok(host):
            continue
        enc = r["encrypted_value"] if "encrypted_value" in r.keys() else b""
        if isinstance(enc, str):
            enc = enc.encode("latin-1")
        enc = enc or b""
        plain = b""
        if enc:
            try:
                plain = decrypt_cookie_value(enc, key)
            except Exception:
                skip += 1
                continue
        else:
            val = r["value"] if "value" in r.keys() else ""
            plain = (val or "").encode("utf-8") if isinstance(val, str) else (val or b"")
        rec = {c: r[c] for c in r.keys() if c not in ("encrypted_value", "value")}
        rec["plain"] = _b64(plain)
        got.append(rec)

    out["n_skip"] = skip
    out["cookies"] = got

    ls_dir = profile / _LS_REL
    recs = []
    raw_files = {}
    if ls_dir.is_dir():
        parsed = read_leveldb(ls_dir)
        for k, v in parsed:
            if ls_key_ok(k):
                recs.append({"k": _b64(k), "v": _b64(v)})
        total = 0
        try:
            total = sum(f.stat().st_size for f in ls_dir.iterdir() if f.is_file())
        except OSError:
            total = 0
        # ldb 经常是 snappy，按域解析会漏。账号 profile 是专用的，整包也就 MB 级。
        if total >= 50_000 or not recs:
            for f in ls_dir.iterdir():
                if not f.is_file() or f.name in ("LOCK", "LOG", "LOG.old"):
                    continue
                try:
                    raw_files[f.name] = _b64(f.read_bytes())
                except OSError:
                    pass

    out["local_storage"] = recs
    if raw_files:
        out["ls_raw"] = raw_files
    if got:
        out["ok"] = True
        if skip:
            out["error"] = f"有 {skip} 条 cookie 解不开，已跳过"
    else:
        if skip:
            out["error"] = f"这个账号的 cookie 全部解不开（跳过 {skip} 条），不要把密文塞进预设"
        elif not got:
            out["error"] = out["error"] or "没有属于 grok/google/flow 的 cookie"
    return out


def inject_account(profile: Path, bundle: dict, *, chrome_alive: bool) -> dict:
    """把明文 cookie 写进新 profile。Chrome 开着就别写。

    返回 {ok, error}。失败不抛，让上层标「需重登」。
    """
    if chrome_alive:
        return {"ok": False, "error": "这个账号的 Chrome 还在跑，关掉再导入登录态"}
    cookies = bundle.get("cookies") or []
    if not cookies and not bundle.get("local_storage") and not bundle.get("ls_raw"):
        return {"ok": False, "error": "预设里没有这个账号的登录态"}
    try:
        profile.mkdir(parents=True, exist_ok=True)
        key = ensure_chrome_key(profile)
    except OranteError as e:
        return {"ok": False, "error": e.message}
    except Exception as e:
        return {"ok": False, "error": f"准备 profile 失败：{e}"}

    ck_path = profile / _COOKIES_REL[0]
    try:
        if ck_path.is_file():
            try:
                with open(ck_path, "r+b"):
                    pass
            except OSError:
                return {"ok": False, "error": "Cookies 文件被锁着，先关掉这个账号的 Chrome"}
        ck_path.parent.mkdir(parents=True, exist_ok=True)
        _write_cookies_db(ck_path, cookies, key)
    except OranteError as e:
        return {"ok": False, "error": e.message}
    except Exception as e:
        return {"ok": False, "error": f"写 cookie 失败：{e}"}

    ls_dir = profile / _LS_REL
    recs = bundle.get("local_storage") or []
    raw = bundle.get("ls_raw") or {}
    try:
        if raw:
            # 整包更完整（snappy 块按域解析会漏），优先
            if ls_dir.is_dir():
                shutil.rmtree(ls_dir, ignore_errors=True)
            ls_dir.mkdir(parents=True, exist_ok=True)
            for name, blob in raw.items():
                if "/" in name or "\\" in name or name in (".", ".."):
                    continue
                (ls_dir / name).write_bytes(_b64d(blob))
        elif recs:
            pairs = [(_b64d(r["k"]), _b64d(r["v"])) for r in recs if r.get("k") is not None]
            write_leveldb(ls_dir, pairs)
    except Exception as e:
        return {"ok": False, "error": f"cookie 写上了，Local Storage 没写上：{e}"}
    return {"ok": True, "error": None}


def _write_cookies_db(path: Path, cookies: list[dict], key: bytes) -> None:
    if path.is_file():
        try:
            path.unlink()
        except OSError as e:
            raise OranteError("删不掉旧的 Cookies 文件", str(e))
        for suf in ("-wal", "-journal", "-shm"):
            p = Path(str(path) + suf)
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE cookies("
            "creation_utc INTEGER NOT NULL,"
            "host_key TEXT NOT NULL,"
            "top_frame_site_key TEXT NOT NULL,"
            "name TEXT NOT NULL,"
            "value TEXT NOT NULL,"
            "encrypted_value BLOB NOT NULL,"
            "path TEXT NOT NULL,"
            "expires_utc INTEGER NOT NULL,"
            "is_secure INTEGER NOT NULL,"
            "is_httponly INTEGER NOT NULL,"
            "last_access_utc INTEGER NOT NULL,"
            "has_expires INTEGER NOT NULL,"
            "is_persistent INTEGER NOT NULL,"
            "priority INTEGER NOT NULL,"
            "samesite INTEGER NOT NULL,"
            "source_scheme INTEGER NOT NULL,"
            "source_port INTEGER NOT NULL,"
            "last_update_utc INTEGER NOT NULL,"
            "source_type INTEGER NOT NULL,"
            "has_cross_site_ancestor INTEGER NOT NULL)"
        )
        con.execute(
            "CREATE UNIQUE INDEX cookies_unique ON cookies("
            "host_key, top_frame_site_key, name, path, source_scheme, source_port)"
        )
        con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO meta(key,value) VALUES('mmap_status','-1')")
        con.execute("INSERT INTO meta(key,value) VALUES('version','24')")
        sql = ("INSERT OR REPLACE INTO cookies(" + ",".join(_COOKIE_COLS) +
               ") VALUES(" + ",".join("?" * len(_COOKIE_COLS)) + ")")
        for c in cookies:
            try:
                plain = _b64d(c.get("plain") or "")
            except Exception:
                continue
            enc = encrypt_cookie_value(plain, key)
            row = []
            for col in _COOKIE_COLS:
                if col == "encrypted_value":
                    row.append(enc)
                elif col == "value":
                    row.append("")
                else:
                    v = c.get(col)
                    if v is None:
                        v = 0 if col not in ("host_key", "name", "path",
                                             "top_frame_site_key") else ""
                    row.append(v)
            con.execute(sql, row)
        con.commit()
    finally:
        con.close()
