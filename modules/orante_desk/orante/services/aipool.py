# -*- coding: utf-8 -*-
"""多模态接口池的调度：借一条 key → 用 → 还。跨进程（四开窗口共用一份账）。

为什么要跨进程：名册里那几条 key 是**全局**资源，四个 Orante 窗口共用同一份配额。
只在进程内数并发，四开时照样把 Gemini 打成 429（0908 就是这么炸的）。

三张表（都在 data/orante.db）：
  ai_providers  名册，见 aikeys.py
  ai_leases     谁正在用哪条 key（一行 = 一个在飞的请求）。心跳过期自动回收
  ai_state      每条 key 的冷却时间和当天用量

挑谁的规矩（用户 0908 定的）：
  ① 先按 priority 分档，1 档最优先；高档只要还有能用的，就轮不到低档
  ② 同档之间「谁手上活最少给谁」，一样少就给最久没用过的
  ③ 冷却中（刚被 429）、当天额度用完、在飞数满了的，跳过
  ④ 一条都挑不出来就等，等到有人还回来或者冷却到期
"""
from __future__ import annotations

import os
import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

from .. import db
from ..errors import OranteError
from . import aikeys

LEASE_STALE = 600.0    # 一个请求最多认它在飞这么久（Gemini 超时 120s，留足余量）
POLL = 0.4             # 没位置时多久看一次
BEAT = 60.0            # 在飞心跳：长请求要续命，否则会被当成僵尸回收

_tx = threading.Lock()


def _now() -> float:
    return time.time()


def _token(tag: str = "") -> str:
    return f"{socket.gethostname()}#{os.getpid()}#{threading.get_ident()}#{tag}"


def quota_day() -> str:
    """配额日。Gemini 的免费额度按太平洋时间零点重置，就统一按 PT 算。

    不依赖 tzdata（Windows 上不一定装了）：PT = UTC-8/-7，用 -8 偏保守，
    最多让额度晚一小时才「翻篇」，不会提前把额度当成新的一天用。
    """
    return (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%d")


def _state(pid: str) -> dict:
    r = db.one("SELECT * FROM ai_state WHERE provider_id=?", (pid,))
    if not r:
        return {"provider_id": pid, "cool_until": 0.0, "cool_why": "", "day": quota_day(),
                "calls": 0, "fails": 0, "last_error": "", "last_ts": 0.0}
    r = dict(r)
    if r.get("day") != quota_day():      # 换天了，用量清零
        r["day"], r["calls"], r["fails"] = quota_day(), 0, 0
    return r


def _put_state(s: dict) -> None:
    db.run("INSERT OR REPLACE INTO ai_state"
           "(provider_id,cool_until,cool_why,day,calls,fails,last_error,last_ts)"
           " VALUES(?,?,?,?,?,?,?,?)",
           (s["provider_id"], float(s.get("cool_until") or 0), s.get("cool_why") or "",
            s.get("day") or quota_day(), int(s.get("calls") or 0), int(s.get("fails") or 0),
            (s.get("last_error") or "")[:300], float(s.get("last_ts") or 0)))


def _inflight(c, now: float) -> dict:
    out: dict[str, int] = {}
    for r in c.execute("SELECT provider_id, COUNT(*) AS n FROM ai_leases WHERE ts>? GROUP BY provider_id",
                       (now - LEASE_STALE,)).fetchall():
        out[r["provider_id"]] = int(r["n"])
    return out


def _why_skip(p: dict, st: dict, inflight: int, now: float) -> str:
    if not p.get("enabled"):
        return "已停用"
    if st.get("cool_until", 0) > now:
        left = int(st["cool_until"] - now)
        return f"冷却中还剩 {left} 秒（{st.get('cool_why') or ''}）".strip()
    lim = int(p.get("daily_limit") or 0)
    if lim and int(st.get("calls") or 0) >= lim:
        return f"今天的 {lim} 次用完了"
    if inflight >= int(p.get("max_inflight") or 1):
        return "手上活满了"
    return ""


class Lease:
    """借到的一条 key。用完必须 done()/failed()，或者用 with。"""

    def __init__(self, provider: dict, key: str):
        self.provider = provider
        self.key = key
        self.t0 = _now()
        self._stop = threading.Event()
        self._beat = threading.Thread(target=self._beat_loop, daemon=True)
        self._beat.start()

    def _beat_loop(self) -> None:
        while not self._stop.wait(BEAT):
            try:
                db.run("UPDATE ai_leases SET ts=? WHERE key=?", (_now(), self.key))
            except Exception:
                pass

    def _release(self) -> None:
        self._stop.set()
        try:
            db.run("DELETE FROM ai_leases WHERE key=?", (self.key,))
        except Exception:
            pass

    def done(self) -> None:
        """成功。记一次用量。"""
        s = _state(self.provider["id"])
        s["calls"] = int(s.get("calls") or 0) + 1
        s["last_ts"] = _now()
        _put_state(s)
        self._release()

    def failed(self, error: str = "", cool: float = 0.0, why: str = "") -> None:
        """失败。cool>0 时把这条 key 冷却起来，池子会自动绕开它。"""
        s = _state(self.provider["id"])
        s["calls"] = int(s.get("calls") or 0) + 1
        s["fails"] = int(s.get("fails") or 0) + 1
        s["last_error"] = error
        s["last_ts"] = _now()
        if cool > 0:
            s["cool_until"] = max(float(s.get("cool_until") or 0), _now() + cool)
            s["cool_why"] = why or error
        _put_state(s)
        self._release()

    def exhausted_today(self, why: str = "今天的额度用完了") -> None:
        """当天额度用完：冷却到下一个配额日（PT 零点）。"""
        nxt = (datetime.now(timezone.utc) - timedelta(hours=8)).replace(
            hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        secs = max(60.0, (nxt - (datetime.now(timezone.utc) - timedelta(hours=8))).total_seconds())
        self.failed(why, cool=secs, why=why)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *a):
        if not self._stop.is_set():
            self.failed(f"{exc_type.__name__}" if exc_type else "") if exc_type else self.done()
        return False


def _cheap_rank(p: dict) -> int:
    """模型名越便宜数字越小。现场校验走最便宜那档，别浪费 Pro。

    按 token 切，避免 gemini 这个词本身含 mini。
    """
    name = str((p or {}).get("model") or "").lower()
    tokens = {t for t in re.split(r"[^a-z0-9]+", name) if t}
    if tokens & {"lite", "nano", "mini"} or "flashlite" in name.replace("-", "").replace("_", ""):
        return 0
    if "flash" in tokens or "flash" in name:
        return 1
    if tokens & {"pro", "ultra", "thinking"}:
        return 3
    return 2


def _try_lease(vision: bool, skip: set[str], tag: str,
               cheap: bool = False) -> tuple[Lease | None, list[str]]:
    """抢一次。抢到返回 (Lease, [])；没抢到返回 (None, [为什么每条都不行])。"""
    c = db.conn()
    now = _now()
    key = _token(tag) + f"#{now:.4f}"
    with _tx:
        if c.in_transaction:
            c.commit()
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("DELETE FROM ai_leases WHERE ts<?", (now - LEASE_STALE,))
            inflight = _inflight(c, now)
            cands, whys = [], []
            for p in aikeys.list_all():
                if p["id"] in skip:
                    whys.append(f"{p['name']}：这一轮已经试过了")
                    continue
                if vision and not p.get("vision"):
                    whys.append(f"{p['name']}：不支持看图")
                    continue
                st = _state(p["id"])
                n = int(inflight.get(p["id"], 0))
                bad = _why_skip(p, st, n, now)
                if bad:
                    whys.append(f"{p['name']}：{bad}")
                    continue
                cands.append((int(p["priority"]), n, float(st.get("last_ts") or 0), p))
            if not cands:
                c.commit()
                return None, whys
            if cheap:
                # 最便宜那档：lite/flash 先，同价再按手上活少、最久没用
                cands.sort(key=lambda x: (_cheap_rank(x[3]), x[1], x[2], x[0]))
            else:
                # ① 优先级小的先 ② 手上活少的先 ③ 最久没用的先
                cands.sort(key=lambda x: (x[0], x[1], x[2]))
            p = cands[0][3]
            c.execute("INSERT OR REPLACE INTO ai_leases(key,provider_id,owner,job_id,ts)"
                      " VALUES(?,?,?,?,?)", (key, p["id"], _token(), tag, now))
            c.commit()
            return Lease(p, key), []
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass
            raise


def lease(vision: bool = True, skip: set[str] | None = None, timeout: float = 300.0,
          tag: str = "", check=None, cheap: bool = False) -> Lease:
    """借一条能干活的 key。全忙就等；等不到就把「每条为什么不行」原样报出来。

    cheap=True：优先 lite/flash 那档（扩图现场校验这种便宜活）。
    """
    t0 = _now()
    skip = set(skip or ())
    while True:
        if not aikeys.list_all(enabled_only=True):
            raise OranteError("还没有可用的多模态接口",
                              "到「设置 → AI 接口」里加一条（Gemini / OpenAI 兼容 / 本地 Ollama 都行）。")
        lz, whys = _try_lease(vision, skip, tag, cheap=cheap)
        if lz is not None:
            return lz
        if callable(check):
            check()                       # 排队时也能被「停止」打断
        if _now() - t0 > timeout:
            raise OranteError("所有多模态接口都用不了",
                              "；".join(whys[:6]) or "全都在忙或在冷却")
        time.sleep(POLL)


def capacity() -> int:
    """全池同时能干多少活 = 各条 max_inflight 相加。给「开几个线程」用。"""
    return sum(int(p["max_inflight"]) for p in aikeys.list_all(enabled_only=True)) or 1


def workers(cap: int = 16) -> int:
    """建议的并发线程数：跟着 key 数量走，但卡一个总上限，别把本地机器打爆。"""
    return max(1, min(int(cap or 16), capacity()))


def status() -> list[dict]:
    """给设置面板看的：每条 key 现在在干嘛、今天用了多少。"""
    now = _now()
    c = db.conn()
    inflight = _inflight(c, now)
    out = []
    for p in aikeys.list_all():
        st = _state(p["id"])
        n = int(inflight.get(p["id"], 0))
        cool = max(0.0, float(st.get("cool_until") or 0) - now)
        out.append({
            "id": p["id"], "name": p["name"], "kind": p["kind"], "model": p["model"],
            "enabled": p["enabled"], "priority": p["priority"],
            "inflight": n, "max_inflight": p["max_inflight"],
            "calls": int(st.get("calls") or 0), "fails": int(st.get("fails") or 0),
            "daily_limit": p["daily_limit"], "day": st.get("day"),
            "cool_left": int(cool), "cool_why": st.get("cool_why") or "",
            "last_error": st.get("last_error") or "",
            "state": ("停用" if not p["enabled"] else
                      f"冷却 {int(cool)}s" if cool > 0 else
                      f"在飞 {n}" if n else "空闲"),
        })
    return out


def clear_cooldown(pid: str | None = None) -> None:
    """手动解冻（用户换了付费计划、或者自己知道额度回来了）。"""
    if pid:
        db.run("UPDATE ai_state SET cool_until=0, cool_why='' WHERE provider_id=?", (pid,))
    else:
        db.run("UPDATE ai_state SET cool_until=0, cool_why=''")
