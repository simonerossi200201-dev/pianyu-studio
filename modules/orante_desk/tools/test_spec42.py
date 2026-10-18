# -*- coding: utf-8 -*-
"""规格 42：任务表自动保存 / 关窗落盘 / 崩溃快照。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec42-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from starlette.requests import Request  # noqa: E402

from orante import db  # noqa: E402
from orante import tasks as taskmod  # noqa: E402
from orante.server import body, task_draft_del, task_draft_get, task_draft_set  # noqa: E402

db.init()
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def _acc():
    if db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A001", "t-flow", "flow", 9398, "A001", "", "", "9:16", 5, 1, "{}", db.now()),
    )


def _await(coro):
    """pytest 里没有默认 event loop，get_event_loop() 会炸。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _request(raw: bytes, content_type: str = "application/json") -> Request:
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"content-type", content_type.encode("ascii"))],
        "client": ("127.0.0.1", 123),
        "server": ("127.0.0.1", 80),
    }
    return Request(scope, receive)


def test_draft_raw_write():
    print("== 快照原样落盘，不校验 ==")
    _acc()
    rows = [
        {"name": "草稿甲", "copy": "", "image_source": "", "type": "", "account": ""},
        {"name": "", "copy": "一段文案", "image_source": "http://x", "type": "short", "account": "t-flow"},
        {"_uid": "u-local-1", "name": "没 id"},
    ]
    meta = taskmod.write_draft(rows)
    p = taskmod.draft_path()
    if p.is_file():
        ok("wrote " + p.name)
    else:
        bad("draft file missing")
        return
    if meta.get("n") == 3 and meta.get("hash"):
        ok("hash=%s n=3" % meta["hash"])
    else:
        bad("meta " + str(meta))
    info = taskmod.draft_info()
    if info["exists"] and info["missing_n"] == 3:
        ok("missing_n=3（库里都没有）")
    else:
        bad("info " + json.dumps(info, ensure_ascii=False)[:200])
    taskmod.write_draft([{"name": "第二份"}])
    info2 = taskmod.draft_info()
    if info2["missing_n"] == 1 and info2["rows"][0].get("name") == "第二份":
        ok("覆盖成最新一份")
    else:
        bad("not overwritten " + str(info2.get("rows")))
    if info2["hash"] != meta["hash"]:
        ok("hash 随内容变")
    else:
        bad("hash unchanged")


def test_draft_http_handlers():
    print("== /api/tasks/draft 处理函数 ==")
    taskmod.clear_draft()
    d = task_draft_get()
    if d.get("ok") and not d.get("exists"):
        ok("GET 无快照 exists=false")
    else:
        bad("GET empty " + str(d))

    payload = json.dumps({"rows": [
        {"name": "crash-1", "copy": "文案1", "_uid": "u1"},
        {"name": "crash-2", "copy": "文案2", "_uid": "u2"},
    ]}, ensure_ascii=False).encode("utf-8")
    req = _request(payload)
    d = _await(task_draft_set(req))
    if d.get("ok") and d.get("n") == 2:
        ok("POST draft n=2")
    else:
        bad("POST draft " + str(d))
    d = task_draft_get()
    if d.get("exists") and d.get("missing_n") == 2:
        ok("GET missing_n=2")
    else:
        bad("GET after post " + str(d))
    d = task_draft_del()
    if d.get("ok"):
        ok("DELETE draft")
    else:
        bad("DELETE " + str(d))
    d = task_draft_get()
    if d.get("exists") is False:
        ok("DELETE 之后不再提示")
    else:
        bad("still exists " + str(d))


def test_body_plain_and_json():
    print("== sendBeacon 也能吃 JSON（text/plain 也行）==")
    payload = json.dumps({"rows": [{"name": "beacon-row", "copy": "一段够长的文案"}]}, ensure_ascii=False).encode("utf-8")
    req = _request(payload, "text/plain;charset=UTF-8")
    d = _await(body(req))
    if d.get("rows") and d["rows"][0]["name"] == "beacon-row":
        ok("text/plain JSON 解析成功")
    else:
        bad("plain body " + str(d))
    req = _request(payload, "application/json")
    d = _await(body(req))
    if d.get("rows"):
        ok("application/json 照旧")
    else:
        bad("json body " + str(d))


def test_draft_vs_db():
    print("== 快照里已在库的行不算 missing ==")
    _acc()
    res = taskmod.upsert_rows([{
        "name": "已存",
        "copy": "库里有",
        "image_source": "http://x",
        "type": "short",
        "account": "t-flow",
    }])
    tid = (res.get("saved") or [None])[0]
    if not tid:
        bad("upsert failed " + str(res))
        return
    taskmod.write_draft([
        {"id": tid, "name": "已存", "copy": "库里有"},
        {"name": "没存的", "copy": "ghost", "_uid": "u-ghost"},
    ])
    info = taskmod.draft_info()
    if info["missing_n"] == 1 and info["missing"][0].get("name") == "没存的":
        ok("只把库里没有的算没保存")
    else:
        bad("missing " + str(info.get("missing")))
    d = task_draft_get()
    if d.get("exists") and d.get("missing_n") == 1:
        ok("GET 保留有 missing 的快照")
    else:
        bad("GET kept " + str(d))
    taskmod.write_draft([{"id": tid, "name": "已存"}])
    d = task_draft_get()
    if d.get("exists") is False:
        ok("missing_n=0 时 GET 清掉快照")
    else:
        bad("should auto-clear " + str(d))


def test_upsert_still_validates():
    print("== 正式保存仍校验 ==")
    res = taskmod.upsert_rows([{
        "name": "缺图",
        "copy": "有文案",
        "image_source": "",
        "type": "short",
        "account": "t-flow",
    }])
    if res.get("errors"):
        ok("缺图仍报错：" + res["errors"][0]["error"])
    else:
        bad("should error " + str(res))
    res = taskmod.upsert_rows([{"name": "", "copy": "", "image_source": ""}])
    if res.get("saved") == [] and res.get("errors") == []:
        ok("空行跳过、不建任务号")
    else:
        bad("empty row " + str(res))


def test_js_hooks():
    print("== 前端关键钩子还在 ==")
    tasks_js = (ROOT / "ui" / "js" / "panels" / "tasks.js").read_text(encoding="utf-8")
    store_js = (ROOT / "ui" / "js" / "store.js").read_text(encoding="utf-8")
    app_js = (ROOT / "ui" / "js" / "app.js").read_text(encoding="utf-8")
    main_py = (ROOT / "orante" / "main.py").read_text(encoding="utf-8")
    checks = [
        ("SAVE_MS = 1500", tasks_js, "1.5 秒去抖"),
        ("DRAFT_MS = 800", tasks_js, "800ms 快照"),
        ("isBusyEditing", tasks_js, "编辑中不触发"),
        ("sendBeacon", tasks_js, "beforeunload beacon"),
        ("hasDraftRows", tasks_js, "草稿行保护"),
        ("const freeze = dirty.value || hasDraftRows()", tasks_js, "有草稿不许整表覆盖"),
        ("tg-savestat", tasks_js, "保存状态点"),
        ("tg-draft-banner", tasks_js, "恢复横幅"),
        ("window.__flushAll", store_js, "__flushAll"),
        ("window.flushAll", store_js, "flushAll await"),
        ("registerFlush", store_js, "registerFlush"),
        ("window.__beaconFlush", store_js, "__beaconFlush"),
        ("goNode", app_js, "切面板 flush"),
        ("beforeunload", app_js, "app beforeunload"),
        ("__beaconFlush", app_js, "beforeunload 只 beacon"),
        ("_bind_closing", main_py, "pywebview closing"),
        ("window.__flushAll", main_py, "evaluate_js flush"),
        ("time.time() + 2.0", main_py, "2 秒超时"),
        ("window.destroy()", main_py, "flush 完再 destroy"),
        ("clearTimeout(draftTimer)", tasks_js, "成功保存取消快照 timer"),
    ]
    for needle, src, label in checks:
        if needle in src:
            ok(label)
        else:
            bad("missing " + label + " (" + needle + ")")
    flush_fn = store_js.split("window.flushAll = async")[1].split("window.__flushAll")[0]
    if "__beaconFlush" in flush_fn:
        bad("flushAll 仍顺手 beacon，会把没 id 的草稿建成两份")
    else:
        ok("flushAll 只 fetch、不 beacon")
    unload = app_js.split("function onAppUnload")[1].split("function ")[0]
    if "__flushAll" in unload:
        bad("beforeunload 走了 __flushAll（fetch 不可靠）")
    else:
        ok("beforeunload 不走 fetch")


def test_closing_timeout_shape():
    print("== 关窗超时形状 ==")
    src = (ROOT / "orante" / "main.py").read_text(encoding="utf-8")
    chunk = src.split("def _bind_closing")[1].split("def _bind_win_icon")[0]
    if "return True" in chunk:
        ok("on_closing 第二次放行 return True")
    else:
        bad("on_closing 没有放行")
    if "return False" in chunk:
        ok("第一次 closing 先拦住")
    else:
        bad("第一次 closing 没拦住")
    if "time.time() + 2.0" in chunk:
        ok("2 秒超时")
    else:
        bad("没有 2 秒超时")


def test_i18n_json():
    print("== i18n ==")
    p = ROOT / "orante" / "i18n" / "zh.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for k in ("tasks.save.dirty", "tasks.save.saving", "tasks.save.ok", "tasks.save.fail",
              "tasks.draft.banner", "tasks.draft.restore", "tasks.draft.discard"):
        if k in data:
            ok(k)
        else:
            bad("missing i18n " + k)


def main():
    print("ORANTE_DATA =", TEST_DATA)
    test_draft_raw_write()
    test_draft_http_handlers()
    test_body_plain_and_json()
    test_draft_vs_db()
    test_upsert_still_validates()
    test_js_hooks()
    test_closing_timeout_shape()
    test_i18n_json()
    print()
    if fail:
        print("FAILED", fail)
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
