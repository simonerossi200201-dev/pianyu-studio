"""Orante 2.0 制片台工具集 (D:\\0_LTX\\modules\\orante_desk, http://127.0.0.1:9400)。

全面升级支持 Orante 2.0 协议（自动完成 `GET /` Session Cookie 握手），同时兼容 v1：
  orante_health()               GET /api/ping + /api/state  – 检查制片台在线状态与任务队列概况
  orante_batches()              GET /api/tasks              – 获取全部任务与批次列表
  orante_batch(pid)             GET /api/tasks/{tid}/review – 获取单个任务的分镜、收片与校验详情
  orante_check(pid, sound)      POST /api/tasks/{tid}/check – 执行 MP4 落盘与音轨响度体检
  orante_worklist(pid)          GET /api/tasks/{tid}/gapfill– 读取 CUTORA 智能拼接回流的缺片清单
  orante_jobs()                 GET /api/jobs               – 查看制片台后台运行/排队任务
  orante_auto_replenish(pid)    零 Token L0 闭环：根据 CUTORA 缺片报告自动触发 Orante 补生缺片分镜
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ORANTE_PORT = int(os.environ.get("ORANTE_PORT") or 9400)
ORANTE = f"http://127.0.0.1:{ORANTE_PORT}"
_HINT = "请通过 D:\\0_LTX\\片语工作室.bat 启动制片台 (端口 9400)。"


def _orante_session_request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> Any:
    """自动获取 Orante 2.0 的 `orante_session` HttpOnly Cookie 并发送请求。"""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    headers = {
        "Host": f"127.0.0.1:{ORANTE_PORT}",
        "Origin": ORANTE,
    }
    if path != "/api/ping":
        req_init = urllib.request.Request(f"{ORANTE}/", headers=headers, method="GET")
        opener.open(req_init, timeout=3.0).read()

    url = f"{ORANTE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def _wrap(fn):
    try:
        return fn()
    except urllib.error.URLError as exc:
        return {"ok": False, "running": False, "error": f"Orante 未启动 ({exc}). {_HINT}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def orante_health() -> dict[str, Any]:
    """检查 Orante 2.0 制片台在线状态与任务统计。"""
    def f():
        ping = _orante_session_request("GET", "/api/ping", timeout=3.0)
        state = _orante_session_request("GET", "/api/state", timeout=5.0)
        tasks = state.get("tasks", []) if isinstance(state, dict) else []
        jobs = state.get("jobs", []) if isinstance(state, dict) else []
        return {
            "ok": True,
            "running": True,
            "version": ping.get("version", "2.0"),
            "port": ping.get("port", ORANTE_PORT),
            "primary": ping.get("primary", True),
            "task_count": len(tasks),
            "active_jobs": sum(1 for j in jobs if j.get("status") in ("queued", "running")),
        }
    return _wrap(f)


def orante_batches() -> dict[str, Any]:
    """列出 Orante 2.0 全部任务及所属批次 (batch_date / batch_no)。"""
    def f():
        res = _orante_session_request("GET", "/api/tasks", timeout=10.0)
        tasks = res.get("tasks", res) if isinstance(res, dict) else res
        compact = [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "batch_date": t.get("batch_date"),
                "batch_no": t.get("batch_no"),
                "engine": t.get("engine"),
                "status": t.get("status"),
                "dir": t.get("dir"),
            }
            for t in (tasks if isinstance(tasks, list) else [])
        ]
        return {"ok": True, "count": len(compact), "tasks": compact}
    return _wrap(f)


def orante_batch(pid: str) -> dict[str, Any]:
    """读取指定任务 (如 T0125) 的收片与分镜状态。"""
    def f():
        rev = _orante_session_request("GET", f"/api/tasks/{pid}/review", timeout=15.0)
        return {"ok": True, "task_id": pid, "review": rev}
    return _wrap(f)


def orante_check(pid: str, sound: bool = True) -> dict[str, Any]:
    """对指定任务执行 MP4 容器与响度体检。"""
    def f():
        res = _orante_session_request("POST", f"/api/tasks/{pid}/check", json_body={"sound": sound}, timeout=120.0)
        return {"ok": True, "task_id": pid, "check": res}
    return _wrap(f)


def orante_worklist(pid: str) -> dict[str, Any]:
    """读取 CUTORA 智能拼接回流到 Orante 的缺片诊断报告 (`gapfill`)。"""
    def f():
        res = _orante_session_request("GET", f"/api/tasks/{pid}/gapfill", timeout=15.0)
        return {"ok": True, "task_id": pid, "gapfill": res}
    return _wrap(f)


def orante_jobs() -> dict[str, Any]:
    """列出 Orante 后台作业队列。"""
    def f():
        res = _orante_session_request("GET", "/api/jobs", timeout=10.0)
        return {"ok": True, **(res if isinstance(res, dict) else {"jobs": res})}
    return _wrap(f)


def orante_auto_replenish(pid: str) -> dict[str, Any]:
    """【零 Token L0 自动化工具】读取 CUTORA 缺片报告并自动在 Orante 中提交缺失分镜重生。"""
    def f():
        gap = _orante_session_request("GET", f"/api/tasks/{pid}/gapfill", timeout=15.0)
        segs: list[int] = []
        for item in (gap.get("items") or []) if isinstance(gap, dict) else []:
            if item.get("kind") in ("real", "mismatch") and item.get("segments"):
                segs.extend(int(s) for s in item["segments"])
        segs = sorted(set(segs))
        if not segs:
            return {"ok": True, "task_id": pid, "action": "none_needed", "segments": []}
        regen = _orante_session_request(
            "POST",
            f"/api/tasks/{pid}/regenerate",
            json_body={"segments": segs},
            timeout=15.0,
        )
        return {"ok": True, "task_id": pid, "action": "regenerating", "segments": segs, "job": regen}
    return _wrap(f)
