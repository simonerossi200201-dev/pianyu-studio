# -*- coding: utf-8 -*-
"""HTTP 层：只转发，不写业务。所有 /api 返回 {ok:true,...} 或 {ok:false,error,hint}。"""
from __future__ import annotations

import hmac
import json
import mimetypes
import re
import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, paths, db, config, accounts, tasks, events, workspace, locks
from .errors import OranteError
from .scheduler import scheduler

app = FastAPI(title="Orante", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
# 每次启动换一把。只通过首页 Set-Cookie 交给本机界面，不出现在页面源码里。
_SESSION = secrets.token_urlsafe(32)


def _loopback_host(value: str) -> bool:
    host = (value or "").strip().lower()
    if host.startswith("["):
        return False
    return host.split(":")[0] in {"127.0.0.1", "localhost"}


def _origin_ok(origin: str, host: str) -> bool:
    """没有 Origin 的本机程序放行。浏览器跨站请求会带 Origin，必须和这次请求的 Host 完全一致。"""
    if not origin:
        return True
    raw = origin.strip().lower()
    if raw == "null":
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme != "http" or (parsed.hostname or "") not in {"127.0.0.1", "localhost"}:
        return False
    return (parsed.netloc or "") == (host or "").strip().lower()


def _session_ok(got: str) -> bool:
    if not got:
        return False
    try:
        return hmac.compare_digest(got, _SESSION)
    except Exception:
        return False


@app.middleware("http")
async def _local_session(request: Request, call_next):
    """本机网页不能靠「端口开着」就改账号、导出预设或读成片。"""
    if not _loopback_host(request.headers.get("host") or ""):
        return JSONResponse({"ok": False, "error": "拒绝访问", "hint": ""}, status_code=403)
    path = request.url.path
    if path == "/api/ping":
        # 启动器等后端就绪、多开时认「这个端口上是不是 Orante」用的。它们是本机程序、拿不到会话 Cookie，
        # 所以这一个不验 Cookie；同时不回传通配 CORS 头，防止外部恶意网页跨站探测本地端口指纹。
        if not _origin_ok(request.headers.get("origin") or "", request.headers.get("host") or ""):
            return JSONResponse({"ok": False, "error": "拒绝访问", "hint": ""}, status_code=403)
        return await call_next(request)

    guarded = path.startswith("/api/") or path.startswith("/media/")
    if guarded:
        if not _origin_ok(request.headers.get("origin") or "", request.headers.get("host") or ""):
            return JSONResponse({"ok": False, "error": "拒绝访问", "hint": ""}, status_code=403)
        if not _session_ok(request.cookies.get("orante_session") or ""):
            return JSONResponse({"ok": False, "error": "拒绝访问", "hint": ""}, status_code=403)
    response = await call_next(request)
    if path == "/":
        response.set_cookie(
            "orante_session", _SESSION,
            httponly=True, samesite="strict", path="/",
        )
    return response


class _NoCacheStatic(StaticFiles):
    """界面文件不给浏览器缓存：改完 js/css 重启就一定是新的，不会拿到旧版本。"""

    def file_response(self, *args, **kw):
        resp = super().file_response(*args, **kw)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp


app.mount("/ui", _NoCacheStatic(directory=str(paths.UI)), name="ui")
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
CHUNK = 1 << 20


@app.exception_handler(OranteError)
async def _oe(_: Request, e: OranteError):
    return JSONResponse(e.to_dict())


@app.exception_handler(Exception)
async def _ee(_: Request, e: Exception):
    try:
        logf = paths.LOGS / "orante.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        with logf.open("a", encoding="utf-8") as f:
            f.write("[api] %s: %s\n" % (type(e).__name__, e))
    except Exception:
        pass
    return JSONResponse({"ok": False, "error": "内部错误，详情已写入日志", "hint": ""})


@app.get("/api/ping")
def ping():
    """不验 Cookie 的那一个：只说「我是 Orante」。启动器 / 多开探测用。"""
    return {"ok": True, "app": "orante", "version": __version__}


def ok(**kw):
    kw["ok"] = True
    return kw


async def body(req: Request) -> dict:
    """读 JSON body。sendBeacon 有时带 application/json，有时被 WebView 写成 text/plain，
    所以不看 Content-Type，直接把原始字节当 UTF-8 JSON 解析。"""
    try:
        raw = await req.body()
        if not raw:
            return {}
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return {}
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ---------------- 壳 ----------------
@app.get("/")
def index():
    return FileResponse(paths.UI / "index.html")


@app.get("/api/state")
def state(probe: int = 1):
    """probe=0：浅层刷新用，不探 Chrome 端口。"""
    from . import instance
    from .services import setup as setupmod
    accs = accounts.list_all(probe=bool(probe))
    locks.annotate_accounts(accs)
    nt = accounts.network_throttle_state()
    studio = accounts.studio_public(probe=True)
    accs.append(studio)               # 本地模型也是一个「账号」（虚拟的，排在最后）
    import os
    return ok(version=__version__, accounts=accs,
              studio=studio,
              tasks=tasks.list_all(),
              settings=config.all(), jobs=scheduler.list(), root=str(paths.ROOT),
              flow_models=accounts.flow_video_models(),
              legacy_available=setupmod.legacy_available(),
              port=int(instance.PORT or 0),
              primary=bool(instance.PRIMARY),
              start_node=(os.environ.get("ORANTE_START_NODE") or "").strip(),
              network_throttled=bool(nt.get("on")),
              flow_throttle=nt)


@app.post("/api/refresh")
async def api_refresh(req: Request):
    """全局刷新。body {deep, tid}。浅层重扫文件；深度再清缓存、探端口。"""
    from .services import refresh as refreshmod
    d = await body(req) or {}
    tid = d.get("tid")
    tasks.clear_counts_cache()
    res = refreshmod.run(deep=bool(d.get("deep")), tid=tid)
    if tid:
        tasks.emit_seg_changed(str(tid), force=True)
    events.emit("tasks.changed")
    return ok(**res)


@app.get("/api/i18n/{lang}")
def i18n(lang: str):
    p = paths.I18N / f"{re.sub(r'[^a-z_-]', '', lang) or 'zh'}.json"
    if not p.is_file():
        p = paths.I18N / "zh.json"
    return FileResponse(p, media_type="application/json")


@app.get("/api/events")
def sse():
    return StreamingResponse(events.sse_iter(events.subscribe()), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------- 设置 ----------------
@app.get("/api/health")
def api_health():
    return ok(status="ok", version=__version__)


@app.get("/api/config")
def api_config():
    """冒烟 / 向导用。键和 settings 表同一套，缺的用 DEFAULTS。"""
    cfg = config.all()
    return ok(settings=cfg, **cfg)


@app.get("/api/settings")
def settings_get():
    return ok(settings=config.all(), defaults=config.DEFAULTS)


@app.post("/api/settings")
async def settings_set(req: Request):
    d = await body(req)
    for k, v in (d.get("set") or {}).items():
        config.set(k, v)
    for k in d.get("reset") or []:
        config.reset(k)
    return ok(settings=config.all())


@app.post("/api/system/open-logs")
def api_open_logs():
    """打开 data/logs。起动错误页「打开日志文件夹」按钮走这里。"""
    import os
    import subprocess
    import sys
    p = paths.LOGS
    p.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(p)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])
    return ok()


@app.post("/api/system/new-window")
def api_new_window():
    """顶栏「＋ 新窗口」：另起进程，端口由新进程自己挑。"""
    from . import main as mainmod
    mainmod.spawn_new_window()
    return ok(port=0)


@app.post("/api/system/install-shortcuts")
def install_shortcuts():
    """跑 tools/Install-Shortcuts.bat：桌面 + 开始菜单快捷方式。"""
    import os
    from .services.procutil import run_quiet
    bat = paths.ROOT / "tools" / "Install-Shortcuts.bat"
    if not bat.is_file():
        return JSONResponse({"ok": False, "error": "找不到 Install-Shortcuts.bat", "hint": ""})
    r = run_quiet(
        ["cmd.exe", "/c", str(bat)] if os.name == "nt" else ["bash", str(bat)],
        cwd=str(paths.ROOT),
        capture_output=True,
        text=True,
        timeout=90,
    )
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "安装快捷方式失败").strip()
        return JSONResponse({"ok": False, "error": msg, "hint": ""})
    return ok()


# ---------------- 账号 ----------------
@app.get("/api/accounts")
def acc_list():
    accs = accounts.list_all()
    locks.annotate_accounts(accs)
    studio = accounts.studio_public(probe=True)
    accs.append(studio)
    return ok(accounts=accs, flow_models=accounts.flow_video_models(), studio=studio)


@app.post("/api/accounts")
async def acc_create(req: Request):
    d = await body(req)
    a = accounts.create(d.get("name", ""), d.get("engine", ""), d.get("project_url", ""), d.get("model", ""))
    events.emit("accounts.changed")
    return ok(account=a)


@app.post("/api/accounts/{aid}")
async def acc_update(aid: str, req: Request):
    a = accounts.update(aid, **(await body(req)))
    events.emit("accounts.changed")
    return ok(account=a)


@app.delete("/api/accounts/{aid}")
def acc_delete(aid: str):
    accounts.delete(aid)
    events.emit("accounts.changed")
    return ok()


@app.post("/api/accounts/{aid}/login")
def acc_login(aid: str):
    return accounts.open_login(aid)


@app.get("/api/accounts/{aid}/health")
def acc_health(aid: str):
    return ok(**accounts.health(aid))


@app.post("/api/accounts/{aid}/use-project-url")
def acc_use_project_url(aid: str):
    """账号抽屉「改成项目地址」：去掉 /collection/<uuid>。没点不改。"""
    a = accounts.use_project_url(aid)
    events.emit("accounts.changed")
    return ok(account=a)


@app.post("/api/accounts/import-legacy")
def acc_import():
    added = accounts.import_legacy_lanes()
    events.emit("accounts.changed")
    return ok(added=added)


# ---------------- 第一次运行向导 ----------------
@app.get("/api/setup/state")
def setup_state():
    from .services import setup as setupmod
    return ok(**setupmod.state())


@app.get("/api/setup/check")
def setup_check(port: int | None = None, workspace: str | None = None):
    from .services import setup as setupmod
    return ok(**setupmod.env_check(port=port, workspace=workspace))


@app.get("/api/env/ffmpeg")
def env_ffmpeg():
    """规格 95：ffmpeg 在哪、怎么找到的；没有就给修法。"""
    from .services import checks
    st = checks.ffmpeg_status()
    st["found"] = bool(st.pop("ok", False))
    return ok(**st)


@app.post("/api/env/ffmpeg/install")
def env_ffmpeg_install():
    """规格 95：一键装 imageio-ffmpeg（本 venv 的 pip）。装完重新找。"""
    from .services import checks
    lines: list[str] = []
    st = checks.install_ffmpeg(log=lines.append)
    st["found"] = bool(st.pop("ok", False))
    return ok(**st, log=lines)


@app.post("/api/setup/check")
async def setup_check_post(req: Request):
    from .services import setup as setupmod
    d = await body(req) or {}
    return ok(**setupmod.env_check(port=d.get("port"), workspace=d.get("workspace")))


@app.post("/api/setup/complete")
async def setup_complete(req: Request):
    from .services import setup as setupmod
    d = await body(req) or {}
    st = setupmod.complete(
        workspace=d.get("workspace"),
        port=d.get("port"),
        gemini_key=d.get("gemini_key") if "gemini_key" in d else None,
    )
    events.emit("accounts.changed")
    return ok(**st)


@app.post("/api/setup/gemini-test")
async def setup_gemini_test(req: Request):
    from .services import setup as setupmod
    d = await body(req) or {}
    return ok(**setupmod.test_gemini(d.get("api_key") or "", d.get("model")))


# ---------------- 任务表 ----------------
@app.get("/api/tasks")
def task_list(all: int = 0):  # noqa: A002
    return ok(tasks=tasks.list_all(include_finished=bool(all)))


@app.post("/api/tasks/rows")
async def task_rows(req: Request):
    d = await body(req)
    res = tasks.upsert_rows(d.get("rows") or [])
    events.emit("tasks.changed")
    return ok(**res, tasks=tasks.list_all())


@app.get("/api/tasks/draft")
def task_draft_get():
    """崩溃快照。必须写在 {tid} 前面，不然 FastAPI 把 draft 当成 id。"""
    info = tasks.draft_info()
    # 库里都有了就顺手清掉，免得下次还弹横幅
    if info.get("exists") and not info.get("missing_n"):
        tasks.clear_draft()
        return ok(exists=False, rows=[], missing=[], missing_n=0, at=0, hash="")
    return ok(**info)


@app.post("/api/tasks/draft")
async def task_draft_set(req: Request):
    """原样写 data/draft-tasks.json。不校验、不建任务、不发号。"""
    d = await body(req)
    meta = tasks.write_draft(d.get("rows") or [])
    return ok(**meta)


@app.delete("/api/tasks/draft")
def task_draft_del():
    tasks.clear_draft()
    return ok()


@app.post("/api/tasks/parse")
async def task_parse(req: Request):
    d = await body(req)
    return ok(rows=tasks.parse_tsv(d.get("text", "")))


@app.post("/api/tasks/reorder")
async def task_reorder(req: Request):
    # 必须写在 {tid} 前面，不然 FastAPI 把 "reorder" 当成 id
    d = await body(req)
    ids = tasks.reorder(d.get("ids") or [])
    events.emit("tasks.changed")
    return ok(ids=ids)


@app.post("/api/tasks/batch-delete")
async def task_batch_delete(req: Request):
    d = await body(req)
    res = tasks.batch_delete(d.get("hard") or [], d.get("trash") or [])
    events.emit("tasks.changed")
    return ok(**res)


@app.post("/api/tasks/undelete")
async def task_undelete(req: Request):
    d = await body(req)
    res = tasks.undelete(d.get("hard") or [], d.get("trash") or [])
    events.emit("tasks.changed")
    return ok(**res)



@app.post("/api/tasks/migrate-copy-to-desc")
async def tasks_migrate_copy_to_desc(req: Request):
    """规格 86 H：把选中任务的文案挪到每张主体图描述语。必须 confirm=true。"""
    from .services import simple86
    d = await body(req)
    res = simple86.migrate_copy_to_desc(
        list(d.get("ids") or []),
        confirm=bool(d.get("confirm")),
    )
    events.emit("tasks.changed")
    return ok(**res)


@app.post("/api/tasks/{tid}/use-current-track")
async def tasks_use_current_track(tid: str, req: Request):
    """规格 86 D：每段都有图时推到 arranged，可选立刻开跑。"""
    from .services import simple86
    d = await body(req)
    start = d.get("start", d.get("start_generate", True))
    res = simple86.use_current_track(tid, start_generate=bool(start))
    events.emit("tasks.changed")
    return ok(**res)


@app.post("/api/tasks/finish-preview")
async def task_finish_preview(req: Request):
    """批量完成前体检。每个 id 跑现有 finish_check，不改状态。"""
    from .services import review
    d = await body(req)
    return ok(**review.finish_preview(d.get("ids") or []))


@app.post("/api/tasks/finish-batch")
async def task_finish_batch(req: Request):
    """串行完成选中的任务。一个失败不影响后面的。"""
    from .services import review
    d = await body(req)
    return ok(**review.finish_batch(d.get("ids") or []))


@app.post("/api/tasks/{tid}/meta")
async def task_meta(tid: str, req: Request):
    """按行覆盖图片来源递归/复制档。只合那几个键。"""
    d = await body(req)
    return ok(meta=tasks.patch_meta(tid, d))


@app.post("/api/tasks/{tid}/status")
async def task_status(tid: str, req: Request):
    d = await body(req)
    new = d.get("status", "")
    cur = (tasks.get(tid) or {}).get("status") or "draft"
    force = False
    if new in tasks.STATUS_ORDER and cur in tasks.STATUS_ORDER:
        # 面板点的回收/恢复/过时/打回：回退必须 force，否则单调性会吞掉。
        # trash 本身不受单调性限制，不必 force。
        if new != "trash" and (cur == "trash" or
                               tasks.STATUS_ORDER.index(new) < tasks.STATUS_ORDER.index(cur)):
            force = True
    st = tasks.set_status(tid, new, force=force)
    events.emit("tasks.changed")
    return ok(status=st)


@app.post("/api/tasks/{tid}/priority")
async def task_priority(tid: str, req: Request):
    """插旗子。{priority: 0|1..999} 或 {top: true} 置顶。

    0 = 自动。序号越小越优先，可以重复：只有同一个账号里才比大小，
    不同账号各排各的队，用同一个号互不影响。
    """
    d = await body(req)
    if d.get("top"):
        n = tasks.bump_top(tid)
    else:
        n = tasks.set_priority(tid, d.get("priority", 0))
    events.emit("tasks.changed")
    return ok(priority=n)


@app.get("/api/queue")
def queue():
    """跨窗口的真实队形：每个账号谁在跑、谁在排队（已按旗子排好）。

    前端 store.jobs 只有本窗口的 Job，四开 Orante 时不够看，所以要问后端。
    stuck：running 超过 10 分钟没新日志的僵尸 Job，生成面板账号行要点停。
    """
    q = locks.queue_view()
    stuck = scheduler.stuck_jobs()
    by: dict[str, list] = {}
    for s in stuck:
        aid = s.get("account_id") or ""
        if not aid:
            continue
        by.setdefault(aid, []).append(s)
        q.setdefault(aid, {"running": None, "waiting": []})
        q[aid]["stuck"] = by[aid]
    # 规格 87：本窗口里同账号一起在跑的任务（拼单），账号行和排队文案要用
    try:
        for aid, pv in scheduler.pack_view().items():
            q.setdefault(aid, {"running": None, "waiting": []})
            q[aid]["pack"] = pv
    except Exception:
        pass
    return ok(queue=q, stuck=stuck)


@app.delete("/api/tasks/{tid}")
def task_delete(tid: str, hard: int = 0):
    tasks.delete(tid, hard=bool(hard))
    events.emit("tasks.changed")
    return ok()


@app.post("/api/tasks/{tid}/open-folder")
def task_open_folder(tid: str):
    import subprocess
    t = tasks.get(tid)
    if not t.get("dir") or not Path(t["dir"]).is_dir():
        raise OranteError("这个任务还没有文件夹", "先「准备」。")
    if paths.IS_WIN:
        subprocess.Popen(["explorer", str(Path(t["dir"]))])
    elif paths.IS_MAC:
        subprocess.Popen(["open", t["dir"]])
    else:
        subprocess.Popen(["xdg-open", t["dir"]])
    return ok()


@app.get("/api/tasks/{tid}/counts")
def task_counts(tid: str):
    """轻量：只给段计数。生成面板折叠行 / Job 收尾兜底用，不读整份 manifest 明细。"""
    t = tasks.get(tid)
    return ok(counts=tasks.counts_of(t))


@app.get("/api/tasks/{tid}/manifest")
def task_manifest(tid: str):
    t = tasks.get(tid)
    d = Path(t["dir"]) if t.get("dir") else None
    if not d or not d.is_dir():
        from .services import project
        return ok(manifest=project.stub_manifest(t), sync_log=[], missing_dir=True)
    from .services import broll, images as imgmod, expand
    try:
        expand.ensure_default_expand_ref(tid)
    except Exception:
        pass
    d, m, sync_log = broll.load_synced(tid)
    blob = dict(m.__dict__)
    # mtime 只给前端破缓存，不写进 manifest.json
    blob["images"] = imgmod.annotate_images(d, m.images)
    from .services import duration_edit as _duredit
    blob["segments"] = _duredit.enrich_segments(d, m)
    return ok(manifest=blob, sync_log=sync_log)


@app.post("/api/split/preview")
async def split_preview(req: Request):
    from .services import split
    d = await body(req)
    return ok(segments=split.preview(d.get("text", ""), d.get("engine", "flow"), d.get("rule")))


# ---------------- 提示词 / Gemini / 描述语（规格 05，只加路由） ----------------
@app.post("/api/gemini/test")
async def gemini_test(req: Request):
    from .services.gemini import Gemini
    d = await body(req)
    key = d.get("api_key")
    model = d.get("model")
    g = Gemini(api_key=key if key is not None else None,
               model=model if model else None)
    return ok(models=g.list_models(), model=g.model)


@app.get("/api/prompts")
def prompts_get():
    from .services import prompts
    return ok(**prompts.all_blocks())


@app.post("/api/prompts")
async def prompts_set(req: Request):
    from .services import prompts
    d = await body(req)
    prompts.set_block(d.get("name", ""), d.get("value"))
    return ok(**prompts.all_blocks())


# ---- 规格 92：按语种改提示词 ----------------------------------------------

@app.get("/api/lang/state")
def lang_state():
    from .services import langpack
    return ok(**langpack.state())


@app.get("/api/lang/export")
def lang_export(lang: str = "", scope: str = "core"):
    """把要翻的提示词整理成一份清单，用户拿去丢给 ChatGPT。"""
    from .services import langpack
    return ok(**langpack.export_text(lang, scope=scope))


@app.post("/api/lang/import")
async def lang_import(req: Request):
    """ChatGPT 翻完粘回来。只应用认得出的条目，占位符丢了的那条会被拒掉。"""
    from .services import langpack
    d = await body(req) or {}
    text = d.get("text") or ""
    if not str(text).strip():
        raise OranteError("没有粘贴内容", "把 ChatGPT 返回的整份清单贴进来。")
    out = langpack.apply_parsed(langpack.parse(text), lang=(d.get("lang") or "").strip())
    return ok(**out)


@app.post("/api/lang/restore")
def lang_restore():
    """翻坏了：回到改语种之前那一份。"""
    from .services import langpack
    out = langpack.restore()
    return ok(**out)


@app.post("/api/prompts/reset")
async def prompts_reset(req: Request):
    from .services import prompts
    d = await body(req)
    prompts.reset_block(d.get("name", ""))
    return ok(**prompts.all_blocks())


@app.post("/api/prompts/emotion-preset")
async def prompts_emotion_preset(req: Request):
    """切固定结语的情绪预设：温暖（默认）/ 激昂（改动前那套）。"""
    from .services import prompts
    d = await body(req) or {}
    prompts.apply_emotion_preset(d.get("name") or "")
    return ok(**prompts.all_blocks())


@app.post("/api/prompts/migrate/ack")
async def prompts_migrate_ack(req: Request):
    """关掉迁移横幅。body {kind, name} 都可空 = 全清。"""
    from .services import prompts
    d = await body(req) or {}
    prompts.ack_notice(d.get("kind") or None, d.get("name") or None)
    return ok(**prompts.all_blocks())


@app.post("/api/prompts/migrate/restore")
async def prompts_migrate_restore(req: Request):
    """把格式升级备份写回 prompts.expand。"""
    from .services import prompts
    d = await body(req) or {}
    prompts.restore_backup(d.get("name") or "expand")
    return ok(**prompts.all_blocks())


@app.get("/api/queue/describe")
def describe_queue_get():
    from .services import descqueue
    return ok(**descqueue.snapshot())


@app.post("/api/queue/describe/start")
async def describe_queue_start(req: Request):
    from .services import descqueue
    d = await body(req) or {}
    only = d.get("only")
    if only is not None and not isinstance(only, list):
        only = None
    return ok(**descqueue.start(only))


@app.post("/api/queue/describe/pause")
async def describe_queue_pause(req: Request):
    from .services import descqueue
    d = await body(req) or {}
    on = d.get("on")
    if on is None:
        on = True
    return ok(**descqueue.pause(bool(on)))


@app.post("/api/queue/describe/skip")
def describe_queue_skip():
    from .services import descqueue
    return ok(**descqueue.skip())


@app.post("/api/queue/describe/stop")
def describe_queue_stop():
    from .services import descqueue
    return ok(**descqueue.stop())


@app.post("/api/tasks/{tid}/image/{p}")
async def task_image_update(tid: str, p: str, req: Request):
    tasks.ensure_writable(tid)
    from .services import describe, expand
    d = await body(req) or {}
    im = None
    if "expand_ref" in d:
        # 扩图参考勾选：白名单在 expand.set_expand_ref，不走描述语那三个字段
        im = expand.set_expand_ref(tid, p, d.get("expand_ref"))
        leftover = {k: d[k] for k in ("desc", "voice", "role", "by", "desc_manual", "voice_manual") if k in d}
        if leftover:
            im = describe.update_image(tid, p, leftover)
        return ok(image=im)
    im = describe.update_image(tid, p, d)
    return ok(image=im)


@app.post("/api/tasks/{tid}/images/copy-desc")
async def task_images_copy_desc(tid: str, req: Request):
    """把一张图的描述语/音色覆盖到勾选的其他图。body.restore 是撤销。"""
    tasks.ensure_writable(tid)
    from .services import images as imgmod
    d = await body(req) or {}
    rest = d.get("restore")
    if isinstance(rest, list):
        return ok(**imgmod.restore_desc(tid, rest))
    return ok(**imgmod.copy_desc(tid, d.get("from_p") or "", d.get("to_ps") or [], d.get("fields")))


@app.get("/api/images/batch-desc-index")
def images_batch_desc_index():
    """每个已准备任务自己的图片。批量填描述语打开时用，不跟当前任务走。"""
    from .services import images as imgmod
    return ok(tasks=imgmod.batch_image_index(), nodes=imgmod.get_prompt_nodes())


@app.get("/api/prompt-nodes")
def prompt_nodes_get():
    """获取带编号的文字描述语节点列表（如 D01~D08）。"""
    from .services import images as imgmod
    return ok(nodes=imgmod.get_prompt_nodes())


@app.post("/api/prompt-nodes")
async def prompt_nodes_save(req: Request):
    """保存或重置带编号的文字描述语节点列表。"""
    from .services import images as imgmod
    d = await body(req) or {}
    return ok(nodes=imgmod.save_prompt_nodes(d.get("nodes")))


@app.post("/api/images/batch-desc")
async def images_batch_desc(req: Request):
    """跨任务批量填描述语（规格 85 + 节点连线模式支持）。body.restore=changed 列表则撤销。"""
    from .services import images as imgmod
    d = await body(req) or {}
    rest = d.get("restore")
    if isinstance(rest, list):
        return ok(**imgmod.restore_batch_desc(rest))
    return ok(**imgmod.batch_desc(
        d.get("items") or [],
        d.get("desc") or "",
        voice=d["voice"] if "voice" in d else None,
        mode=d.get("mode") or "fill_empty",
        node_id=d.get("node_id"),
        node_name=d.get("node_name"),
        connections=d.get("connections"),
    ))


@app.post("/api/tasks/{tid}/image/{p}/open")
def task_image_open(tid: str, p: str):
    """系统默认看图程序打开。路径必须在任务目录内。"""
    from .services import images as imgmod
    return ok(**imgmod.open_image(tid, p))


@app.post("/api/tasks/{tid}/image/{p}/reveal")
def task_image_reveal(tid: str, p: str):
    """资源管理器里选中这个文件。路径必须在任务目录内。"""
    from .services import images as imgmod
    return ok(**imgmod.reveal_image(tid, p))


@app.get("/api/tasks/{tid}/rescan")
def task_rescan_preview(tid: str):
    """按磁盘重建图库：先看后改，这一步不写盘。"""
    from .services import rescan
    return ok(**rescan.preview(tid))


@app.post("/api/tasks/{tid}/rescan")
def task_rescan_apply(tid: str):
    """确认后落盘：先备份 manifest.bak-rescan-<时间>.json。"""
    tasks.ensure_writable(tid)
    from .services import rescan
    return ok(**rescan.apply(tid))


@app.post("/api/tasks/{tid}/image/{p}/adopt")
async def task_image_adopt(tid: str, p: str, req: Request):
    """文件不在：从图库挑一张拷到这条的位置。"""
    tasks.ensure_writable(tid)
    from .services import rescan
    d = await body(req) or {}
    from_p = d.get("from") or d.get("from_p") or d.get("p") or ""
    return ok(image=rescan.adopt_image(tid, p, from_p))


@app.get("/api/tasks/{tid}/prompt/{s}")
def task_prompt(tid: str, s: str):
    from .services import describe
    return ok(prompt=describe.preview_prompt(tid, s))


# ---------------- 配图轨 / 扩图（规格 07，只加路由） ----------------
@app.get("/api/tasks/{tid}/track")
def task_track(tid: str):
    from .services import arrange, expand
    try:
        expand.ensure_default_expand_ref(tid)
    except OranteError:
        pass
    return ok(**arrange.get_track(tid))


@app.post("/api/tasks/{tid}/track/preview")
async def task_track_preview(tid: str, req: Request):
    from .services import arrange
    d = await body(req)
    track = d.get("track") if isinstance(d.get("track"), dict) else d
    return ok(**arrange.preview(tid, track))


@app.post("/api/tasks/{tid}/review/image")
async def task_review_set_image(tid: str, req: Request):
    """收片面板换图：把某段的配图换成现有的另一张，并钉进配图轨。"""
    from .services import review
    d = await body(req)
    return ok(**review.set_image(tid, d.get("s"), d.get("p")))


@app.post("/api/tasks/{tid}/review/prompt")
async def task_review_set_prompt(tid: str, req: Request):
    """收片面板改成品提示词：{s, text} 保存并锁定；{s, reset:true} 恢复默认。"""
    from .services import review
    d = await body(req)
    return ok(**review.set_prompt(tid, d.get("s"), text=d.get("text"), reset=bool(d.get("reset"))))


@app.post("/api/tasks/{tid}/review/clear_fresh")
async def task_review_clear_fresh(tid: str, req: Request):
    """清掉「新」标：body 里给 s 就清一段，不给就清整个任务。"""
    from .services import review
    d = await body(req)
    return ok(**review.clear_fresh(tid, d.get("s")))


@app.post("/api/tasks/{tid}/track/settings")
async def task_track_settings(tid: str, req: Request):
    """只存钉住 / 限定图池 / 参数，不算轨、不改配图。"""
    tasks.ensure_writable(tid)
    from .services import arrange
    d = await body(req)
    track = d.get("track") if isinstance(d.get("track"), dict) else d
    return ok(track=arrange.save_track(tid, track))


@app.post("/api/tasks/{tid}/mode")
async def task_mode(tid: str, req: Request):
    """任务级生成方式：first_frame | anchor_bridge | tail_chain。preview=true 只算 hint 不改。"""
    d = await body(req) or {}
    r = tasks.apply_gen_mode(tid, d.get("mode") or "", preview=bool(d.get("preview")))
    return ok(**r)


@app.post("/api/tasks/{tid}/rtc")
async def task_rtc(tid: str, req: Request):
    """④ 卡片「跑到全部出片」开关。默认开。"""
    d = await body(req) or {}
    on = d.get("on")
    if on is None:
        on = d.get("run_to_completion")
    v = tasks.set_rtc(tid, bool(on) if on is not None else True)
    return ok(run_to_completion=v)


@app.get("/api/tasks/{tid}/blocked")
def task_blocked(tid: str):
    """④ 徽章点开：被拒段列表。"""
    t = tasks.get(tid)
    d = t.get("dir")
    if not d:
        return ok(rows=[])
    from .models import Manifest
    from .services import generate as genmod
    m = Manifest.load(Path(d))
    return ok(rows=genmod.blocked_rows(m))


@app.post("/api/tasks/{tid}/blocked/retry")
async def task_blocked_retry(tid: str, req: Request):
    d = await body(req) or {}
    from .services import generate as genmod
    return ok(**genmod.retry_blocked(tid, int(d.get("s") or 0), account_id=d.get("account_id") or None))


@app.post("/api/tasks/{tid}/studio-handoff")
def task_studio_handoff(tid: str):
    """规格 98：「现在交给本地模型」——排给本地模型的段立刻转交（不用等 ticker）。"""
    from .services import studio_handoff
    r = studio_handoff.kick(tid, manual=True)
    if r.get("skip") == "disabled":
        raise OranteError("本地模型没启用", "到 设置 → 本地模型 里打开。")
    if r.get("skip") == "busy":
        raise OranteError("这个任务还在出片", "等这一轮跑完会自动转交给本地模型。")
    return ok(**r)


@app.post("/api/tasks/{tid}/blocked/borrow")
async def task_blocked_borrow(tid: str, req: Request):
    d = await body(req) or {}
    from .services import generate as genmod
    run = d.get("run")
    if run is None:
        run = True
    return ok(**genmod.borrow_seg(tid, int(d.get("s") or 0), run=bool(run)))


@app.post("/api/tasks/{tid}/role")
async def task_role(tid: str, req: Request):
    """点链条上某一段切换单图首帧 / 首尾帧接片。走段级一键（旧片作废重拍）。"""
    tasks.ensure_writable(tid)
    d = await body(req) or {}
    from .services import pipeline, broll, bench
    tdir, m, _sync = broll.load_synced(tid)
    if not pipeline.is_anchor_mode(m):
        raise OranteError(
            "这个任务是逐段首帧，每段都单独起头",
            "段级切换只在「锚点+接片」任务里可用。")
    sg = m.seg(int(d.get("s") or 0))
    if not sg:
        from .services.pipeline import _lab
        raise OranteError("没有 %s" % _lab(d.get("s")))
    cur = "bridge" if sg.get("role") == "bridge" else "single"
    want = "single" if cur == "bridge" else "bridge"
    return ok(**bench.set_seg_mode(tid, d.get("s"), want))


@app.post("/api/tasks/{tid}/seg-mode")
async def task_seg_mode(tid: str, req: Request):
    """段级生成方式：mode=single|bridge|chain。幂等。"""
    from .services import bench
    d = await body(req) or {}
    return ok(**bench.set_seg_mode(tid, d.get("s"), d.get("mode")))


@app.post("/api/tasks/{tid}/seg-duration")
async def task_seg_duration(tid: str, req: Request):
    """段时长：{s, duration} 或 {items:[{s,duration}], skip_collected?}。手动例外。"""
    from .services import duration_edit
    d = await body(req) or {}
    items = d.get("items")
    if not items:
        items = [{"s": d.get("s"), "duration": d.get("duration")}]
    return ok(**duration_edit.set_seg_durations(
        tid, items, skip_collected=bool(d.get("skip_collected"))))


@app.post("/api/tasks/{tid}/seg-duration/restore")
async def task_seg_duration_restore(tid: str, req: Request):
    """恢复自动时长：{s} 或 {items:[s,…]}。"""
    from .services import duration_edit
    d = await body(req) or {}
    items = d.get("items")
    if items is None:
        items = [d.get("s")]
    return ok(**duration_edit.restore_seg_durations(tid, items))


@app.post("/api/tasks/duration-pref")
async def tasks_duration_pref(req: Request):
    """批量设任务 duration_pref（只作用于没文案的行）。
    body: {ids?: [...], duration?: N, by_engine?: {flow:N, grok:N, studio:N}}
    """
    from .services import duration_edit
    d = await body(req) or {}
    by = d.get("by_engine") if isinstance(d.get("by_engine"), dict) else {}
    if not by and d.get("duration") is not None:
        # 单一时长：对涉及引擎都设（调用方通常已按引擎拆）
        n = int(d.get("duration"))
        by = {"flow": n, "grok": n, "studio": n}
    ids = d.get("ids")
    return ok(**duration_edit.set_tasks_duration_pref(ids, by))


@app.get("/api/tasks/{tid}/chain")
def task_chain_get(tid: str):
    """④ 生成接龙设置 + 起头图列表。"""
    from .services import pipeline, broll
    tdir, m, _sync = broll.load_synced(tid)
    cfg = pipeline.chain_cfg(m)
    images = []
    for im in m.images or []:
        if (im.get("status") or "ok") != "ok":
            continue
        if im.get("role") == "空镜":
            continue
        rel = (im.get("file") or "").replace("\\", "/")
        images.append({
            "p": im.get("p") or "",
            "file": rel,
            "desc": (im.get("desc") or "")[:80],
            "expand_ref": bool(im.get("expand_ref")),
        })
    return ok(chain=cfg, images=images, mode=pipeline.mode_of(m))


@app.post("/api/tasks/{tid}/chain")
async def task_chain_set(tid: str, req: Request):
    tasks.ensure_writable(tid)
    from .services import pipeline, broll
    tdir, m, _sync = broll.load_synced(tid)
    d = await body(req) or {}
    cfg = pipeline.set_chain_settings(m, d)
    m.save(tdir)
    from . import events
    events.emit("tasks.changed")
    return ok(chain=cfg, mode=pipeline.mode_of(m))


@app.get("/api/tasks/{tid}/tails")
def task_tails(tid: str):
    """⑤ 尾帧一览。"""
    from .services import tails
    return ok(**tails.list_tails(tid))


@app.post("/api/tasks/{tid}/chain/cascade")
async def task_chain_cascade(tid: str, req: Request):
    """检查台「从这里往后重跑」。"""
    from .services import bench
    d = await body(req) or {}
    return ok(**bench.cascade_chain_from(tid, d.get("s")))


@app.get("/api/tasks/{tid}/bench")
def task_bench(tid: str):
    """尾帧检查台：接片卡 + 图池。跨度分悬停再算。"""
    from .services import bench
    return ok(**bench.snapshot(tid))


@app.post("/api/tasks/{tid}/bench/swap")
async def task_bench_swap(tid: str, req: Request):
    from .services import bench
    d = await body(req) or {}
    return ok(**bench.auto_swap(tid, d.get("s")))


@app.post("/api/tasks/{tid}/bench/swap-red")
async def task_bench_swap_red(tid: str, req: Request):
    from .services import bench
    return ok(**bench.swap_all_red(tid))


@app.post("/api/tasks/{tid}/bench/pick")
async def task_bench_pick(tid: str, req: Request):
    from .services import bench
    d = await body(req) or {}
    return ok(**bench.pick_anchor(tid, d.get("s"), d.get("p")))


@app.get("/api/tasks/{tid}/bench/score")
def task_bench_score(tid: str, s: int, p: str):
    from .services import bench
    return ok(**bench.score_one(tid, s, p))


@app.post("/api/tasks/{tid}/bench/pass")
async def task_bench_pass(tid: str, req: Request):
    from .services import bench
    d = await body(req) or {}
    force = bool(d.get("force") or d.get("ignore"))
    return ok(**bench.pass_one(tid, d.get("s"), force=force))


@app.post("/api/tasks/{tid}/bench/pass-yellow")
async def task_bench_pass_yellow(tid: str, req: Request):
    from .services import bench
    return ok(**bench.pass_all_yellow(tid))


@app.post("/api/tasks/{tid}/bench/pass-all")
async def task_bench_pass_all(tid: str, req: Request):
    """全部放行（含红）。"""
    from .services import bench
    return ok(**bench.pass_all(tid))


@app.get("/api/tasks/{tid}/bench/tails")
def task_bench_tails(tid: str, s: int):
    from .services import bench
    return ok(**bench.preview_tails(tid, s))


@app.post("/api/tasks/{tid}/bench/tail")
async def task_bench_tail(tid: str, req: Request):
    from .services import bench
    d = await body(req) or {}
    return ok(**bench.set_tail(tid, d.get("s"), d.get("k")))


@app.post("/api/tasks/{tid}/bench/acted")
async def task_bench_acted(tid: str, req: Request):
    """改提示词 / 点重生时先记下 acted，红色项随后能通过。"""
    from .services import bench
    d = await body(req) or {}
    why = (d.get("why") or "").strip()
    if why == "regen":
        return ok(**bench.after_regen_src(tid, d.get("s")))
    return ok(**bench.after_prompt_edit(tid, d.get("s")))


@app.post("/api/tasks/{tid}/bench/retry")
async def task_bench_retry(tid: str, req: Request):
    """检查台「重试接片」：清对不上/超时账，打回 planned。"""
    from .services import bench
    d = await body(req) or {}
    return ok(**bench.retry_bridge(tid, d.get("s")))


@app.post("/api/tasks/{tid}/track/apply")
async def task_track_apply(tid: str, req: Request):
    from . import jobs as jobmod
    d = await body(req)
    track = d.get("track") if isinstance(d.get("track"), dict) else d
    job = jobmod.start("arrange", tid, track=track)
    return ok(job=job.to_dict())


@app.post("/api/tasks/{tid}/image/{p}/keep")
def task_image_keep(tid: str, p: str):
    tasks.ensure_writable(tid)
    from .services import expand
    return ok(image=expand.keep_image(tid, p))


@app.post("/api/tasks/{tid}/image/{p}/discard")
def task_image_discard(tid: str, p: str):
    tasks.ensure_writable(tid)
    from .services import expand
    return ok(image=expand.discard_image(tid, p))


# ---------------- 换图描述卡片 ----------------
@app.get("/api/desc-cards")
def desc_cards_list():
    from .services import desc_cards
    return ok(cards=desc_cards.list_all())


@app.post("/api/desc-cards")
async def desc_cards_add(req: Request):
    from .services import desc_cards
    d = await body(req)
    return ok(card=desc_cards.create(d.get("name") or "", d.get("prompt") or ""))


@app.post("/api/desc-cards/reorder")
async def desc_cards_reorder(req: Request):
    from .services import desc_cards
    d = await body(req)
    if d.get("id") and d.get("delta") is not None:
        return ok(cards=desc_cards.move(d.get("id"), d.get("delta")))
    return ok(cards=desc_cards.reorder(d.get("ids") or []))


@app.post("/api/desc-cards/restore")
def desc_cards_restore():
    from .services import desc_cards
    return ok(cards=desc_cards.restore_factory())


@app.post("/api/desc-cards/{cid}/copy")
def desc_cards_copy(cid: str):
    from .services import desc_cards
    return ok(card=desc_cards.copy(cid))


@app.post("/api/desc-cards/{cid}")
async def desc_cards_update(cid: str, req: Request):
    from .services import desc_cards
    d = await body(req)
    return ok(card=desc_cards.update(cid, name=d.get("name"), prompt=d.get("prompt")))


@app.delete("/api/desc-cards/{cid}")
def desc_cards_delete(cid: str):
    from .services import desc_cards
    return ok(card=desc_cards.delete(cid))


# ---------------- 图片编辑（规格 15，只加路由） ----------------
@app.get("/api/tasks/{tid}/image/{p}/info")
def task_image_info(tid: str, p: str):
    from .services import imgedit
    return ok(**imgedit.image_info(tid, p))


@app.post("/api/tasks/{tid}/image/{p}/duplicate")
def task_image_duplicate(tid: str, p: str):
    tasks.ensure_writable(tid)
    from .services import imgedit
    return ok(**imgedit.duplicate(tid, p))


@app.post("/api/tasks/{tid}/image/{p}/flip")
def task_image_flip(tid: str, p: str):
    tasks.ensure_writable(tid)
    from .services import imgedit
    return ok(**imgedit.flip(tid, p))


@app.post("/api/tasks/{tid}/image/{p}/crop")
async def task_image_crop(tid: str, p: str, req: Request):
    tasks.ensure_writable(tid)
    from .services import imgedit
    return ok(**imgedit.crop(tid, p, await body(req)))


@app.get("/api/tasks/{tid}/expand-account")
def task_expand_account_get(tid: str):
    from .services import expand
    return ok(**expand.expand_account_state(tid))


@app.post("/api/tasks/{tid}/expand-account")
async def task_expand_account_set(tid: str, req: Request):
    from .services import expand
    d = await body(req) or {}
    return ok(**expand.set_expand_account(tid, d.get("account_id")))


@app.post("/api/tasks/{tid}/image/{p}/reset-refs")
async def task_image_reset_refs(tid: str, p: str, req: Request):
    tasks.ensure_writable(tid)
    from .services import expand
    d = await body(req) or {}
    only_kept = bool(d.get("only_kept") or d.get("kept"))
    return ok(**expand.reset_image_refs(tid, p, only_kept=only_kept))


# ---------------- 收片 / 体检（规格 09，只加路由） ----------------
@app.get("/api/tasks/{tid}/review")
def task_review(tid: str):
    from .services import review
    return ok(**review.get_review(tid))


@app.post("/api/tasks/{tid}/marks")
async def task_marks(tid: str, req: Request):
    from .services import review
    d = await body(req)
    return ok(**review.set_mark(tid, d.get("s"), d.get("mark")))


@app.post("/api/tasks/{tid}/finish-check")
def task_finish_check(tid: str):
    from .services import review
    return ok(**review.finish_check(tid))


@app.post("/api/tasks/{tid}/verify")
async def task_verify(tid: str, req: Request):
    """手动重跑落盘自证。body {only:[4,5], apply?:false}。默认只报告不挪文件。"""
    from .services import verify
    d = await body(req)
    only = d.get("only")
    apply = bool(d.get("apply"))
    return ok(**verify.run_api(tid, only=only, apply=apply))


@app.get("/api/tasks/{tid}/gapfill")
def task_gapfill(tid: str, path: str | None = None):
    from .services import gapfill
    return ok(**gapfill.get_gapfill(tid, path))


@app.post("/api/tasks/{tid}/gapfill/path")
async def task_gapfill_path(tid: str, req: Request):
    from .services import gapfill
    d = await body(req)
    return ok(**gapfill.set_path(tid, d.get("path") or ""))


@app.post("/api/tasks/{tid}/reveal")
async def task_reveal(tid: str, req: Request):
    """V/P：在资源管理器里选中这段的视频 / 原图。平台分支沿用 open-folder。"""
    import subprocess
    from .services import review
    d = await body(req)
    path = review.reveal_path(tid, d.get("s"), d.get("what") or "video")
    if paths.IS_WIN:
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif paths.IS_MAC:
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
    return ok(path=str(path))


# ---------------- 工作区 ----------------
@app.post("/api/workspace/batch")
async def ws_batch(req: Request):
    d = await body(req)
    return ok(batch=workspace.new_batch(d.get("root")))


@app.post("/api/workspace/pick")
async def ws_pick(req: Request):
    """弹本地文件夹选择框（pywebview 窗口里才有；浏览器模式退回手填）。"""
    from . import main as _main
    d = await body(req)
    title = (d.get("title") or "").strip() or None
    folder = _main.pick_folder(title)
    return ok(folder=folder)


@app.post("/api/images/probe-source")
async def images_probe_source(req: Request):
    """图片来源当场体检：只数不拷。网盘链接不联网。

    HTTP 的 ok 一律 true（请求成功）。来源能不能用在 src_ok，避免 ok() 把
    「找不到文件夹」盖成成功。
    """
    from .services import images as imgmod
    d = await body(req)
    rec = imgmod.probe_source(d.get("src") or "", recursive=bool(d.get("recursive")))
    return ok(
        kind=rec.get("kind") or "",
        n=int(rec.get("n") or 0),
        n_deep=int(rec.get("n_deep") or 0),
        note=rec.get("note") or "",
        sample=list(rec.get("sample") or []),
        recursive=bool(rec.get("recursive")),
        path=rec.get("path") or "",
        src_ok=bool(rec.get("ok")),
    )


@app.post("/api/workspace/pick-file")
def ws_pick_file():
    """弹本地文件选择框（查漏补缺选 Cutora 清单）。"""
    from . import main as _main
    path = _main.pick_file()
    return ok(path=path)


# ---------------- 多模态接口池（AI 接口） ----------------
@app.get("/api/ai/providers")
def ai_list():
    """名册 + 每条的实时状态（在飞几个、今天用了多少、有没有在冷却）。"""
    from .services import aikeys, aipool
    return ok(providers=[aikeys.masked(p) for p in aikeys.list_all()],
              status=aipool.status(), capacity=aipool.capacity(), workers=aipool.workers())


@app.post("/api/ai/providers")
async def ai_add(req: Request):
    from .services import aikeys
    d = await body(req)
    return ok(provider=aikeys.masked(aikeys.add(d)))


@app.post("/api/ai/providers/reorder")
async def ai_reorder(req: Request):
    # 必须写在 {pid} 前面，不然 FastAPI 把 "reorder" 当成 id
    from .services import aikeys
    d = await body(req)
    aikeys.reorder(d.get("ids") or [])
    return ok()


@app.post("/api/ai/providers/{pid}")
async def ai_update(pid: str, req: Request):
    from .services import aikeys
    d = await body(req)
    return ok(provider=aikeys.masked(aikeys.update(pid, d)))


@app.delete("/api/ai/providers/{pid}")
def ai_delete(pid: str):
    from .services import aikeys
    aikeys.delete(pid)
    return ok()


@app.post("/api/ai/providers/{pid}/test")
def ai_test(pid: str):
    """自检：问一句文字，再拿一张纯色小图问颜色——确认真的能看图。"""
    from .services import aikeys, ai
    return ok(result=ai.probe(aikeys.get(pid)))


@app.post("/api/ai/test")
async def ai_test_draft(req: Request):
    """还没保存就想先试试（新建对话框里的「测一下」）。"""
    from .services import aikeys, ai
    d = await body(req)
    if d.get("id") and not str(d.get("api_key") or "").strip():
        d = {**aikeys.get(d["id"]), **{k: v for k, v in d.items() if v not in ("", None)}}
    return ok(result=ai.probe(aikeys._clean(d)))


@app.post("/api/ai/thaw")
async def ai_thaw(req: Request):
    """手动解冻：换了付费计划、或者自己知道额度回来了。"""
    from .services import aipool
    d = await body(req)
    aipool.clear_cooldown(d.get("id"))
    return ok()


# ---------------- 预设（规格 26） ----------------
@app.get("/api/preset/preview")
def preset_preview():
    from .services import preset
    return ok(**preset.preview())


@app.post("/api/preset/export")
async def preset_export(req: Request):
    from .services import preset
    d = await body(req) or {}
    r = preset.export(d.get("groups"), bool(d.get("cookies")), d.get("password") or "", d.get("path"))
    return ok(**r)


@app.post("/api/preset/inspect")
async def preset_inspect(req: Request):
    from .services import preset
    d = await body(req) or {}
    return ok(**preset.inspect(d.get("path") or ""))


@app.post("/api/preset/diff")
async def preset_diff(req: Request):
    from .services import preset
    d = await body(req) or {}
    return ok(**preset.diff(d.get("path") or "", d.get("password") or ""))


@app.post("/api/preset/import")
async def preset_import(req: Request):
    from .services import preset
    d = await body(req) or {}
    r = preset.do_import(d.get("path") or "", d.get("password") or "",
                         d.get("mode") or "merge_local", d.get("groups"))
    events.emit("accounts.changed")
    events.emit("tasks.changed")
    return ok(**r)


@app.post("/api/preset/factory_reset")
async def preset_factory(req: Request):
    from .services import preset
    d = await body(req) or {}
    r = preset.factory_reset(d.get("confirm") or "", d.get("password") or "")
    events.emit("accounts.changed")
    events.emit("tasks.changed")
    return ok(**r)


@app.post("/api/preset/pick-save")
async def preset_pick_save(req: Request):
    from . import main as _main
    d = await body(req) or {}
    path = _main.pick_save(
        title="Orante：导出预设",
        filename=d.get("filename") or "",
        directory=d.get("directory") or "",
        filetypes=[("Orante 预设", "*.orpreset"), ("全部", "*.*")])
    return ok(path=path)


@app.post("/api/preset/pick-open")
def preset_pick_open():
    from . import main as _main
    path = _main.pick_file(
        title="Orante：导入预设",
        filetypes=[("Orante 预设", "*.orpreset"), ("全部", "*.*")])
    return ok(path=path)


# ---------------- 模板套装（规格 76） ----------------
@app.get("/api/suites")
def suites_list():
    from .services import suites
    return ok(**suites.list_all())


@app.post("/api/suites")
async def suites_create(req: Request):
    from .services import suites
    d = await body(req) or {}
    item = suites.create(
        d.get("name") or "", d.get("note") or "",
        d.get("color") or "", d.get("groups"),
        from_=d.get("from") or "current")
    return ok(item=item, current=suites.current_id())


@app.post("/api/suites/import")
async def suites_import(req: Request):
    from .services import suites
    d = await body(req) or {}
    item = suites.import_suite(d.get("path") or "", d.get("password") or "",
                               d.get("name") or "")
    return ok(item=item)


@app.post("/api/suites/{sid}/switch")
async def suites_switch(sid: str, req: Request):
    from .services import suites
    d = await body(req) or {}
    save = d.get("save")
    if save is not None:
        save = bool(save)
    r = suites.switch(sid, save=save)
    events.emit("accounts.changed")
    return ok(**r)


@app.patch("/api/suites/{sid}")
async def suites_patch(sid: str, req: Request):
    from .services import suites
    d = await body(req) or {}
    item = suites.patch(sid, name=d.get("name"), note=d.get("note"), color=d.get("color"))
    return ok(item=item)


@app.post("/api/suites/{sid}/duplicate")
def suites_dup(sid: str):
    from .services import suites
    return ok(item=suites.duplicate(sid))


@app.post("/api/suites/{sid}/save-current")
def suites_save(sid: str):
    from .services import suites
    return ok(item=suites.save_current(sid))


@app.post("/api/suites/{sid}/restore-builtin")
def suites_restore(sid: str):
    from .services import suites
    item = suites.restore_builtin(sid)
    events.emit("accounts.changed")
    return ok(item=item, current=suites.current_id())


@app.delete("/api/suites/{sid}")
def suites_delete(sid: str):
    from .services import suites
    return ok(**suites.delete(sid))


@app.post("/api/suites/{sid}/export")
async def suites_export(sid: str, req: Request):
    from .services import suites
    d = await body(req) or {}
    r = suites.export_suite(sid, d.get("password") or "", d.get("path"))
    return ok(**r)


# ---------------- 工程文件（规格 27） ----------------
@app.post("/api/project/export")
async def project_export(req: Request):
    from .services import project
    d = await body(req) or {}
    r = project.export(
        task_ids=d.get("task_ids"),
        batch_ids=d.get("batch_ids"),
        all_finished=bool(d.get("all_finished")),
    )
    return ok(**r)


@app.post("/api/project/clear")
async def project_clear(req: Request):
    from .services import project
    d = await body(req) or {}
    r = project.clear(d.get("task_ids") or [], force=bool(d.get("force")))
    if not r.get("ok", True):
        return JSONResponse(r)
    return ok(**{k: v for k, v in r.items() if k != "ok"})


@app.post("/api/project/import")
async def project_import(req: Request):
    from .services import project
    d = await body(req) or {}
    r = project.do_import(d.get("paths") or [])
    return ok(**r)


@app.post("/api/project/unfreeze")
async def project_unfreeze(req: Request):
    from .services import project
    d = await body(req) or {}
    r = project.unfreeze(d.get("task_ids") or [])
    return ok(**{k: v for k, v in r.items() if k != "ok"})


@app.post("/api/project/pick-open")
def project_pick_open():
    from . import main as _main
    paths_ = _main.pick_files(
        title="Orante：导入工程",
        filetypes=[("Orante 工程", "*.orproj.json"), ("JSON", "*.json"), ("全部", "*.*")])
    return ok(paths=paths_ or [])


@app.post("/api/accounts/{aid}/login-state")
async def acc_login_state(aid: str, req: Request):
    d = await body(req) or {}
    a = accounts.set_login_state(aid, d.get("state") or "")
    events.emit("accounts.changed")
    return ok(account=a)


# ---------------- Job ----------------
@app.get("/api/jobs")
def jobs_list(all: int = 0):  # noqa: A002
    return ok(jobs=scheduler.list(active_only=not all))


@app.get("/api/jobs/{jid}")
def jobs_get(jid: str):
    j = scheduler.get(jid)
    if not j:
        raise OranteError("没有这个 job")
    return ok(job=j.to_dict())


@app.get("/api/jobs/{jid}/log", response_class=PlainTextResponse)
def jobs_log(jid: str):
    j = scheduler.get(jid)
    if not j or not j.log_path.is_file():
        return ""
    return j.log_path.read_text(encoding="utf-8", errors="replace")


@app.post("/api/jobs/{jid}/cancel")
def jobs_cancel(jid: str):
    scheduler.cancel(jid)
    return ok()


@app.post("/api/tasks/{tid}/account")
async def task_set_account(tid: str, req: Request):
    """④ 生成横幅「换一个 Flow 账号继续」。"""
    d = await body(req)
    aid = (d.get("account_id") or "").strip()
    if not aid:
        raise OranteError("没选账号")
    acc = accounts.get(aid)
    if acc.get("engine") != "flow":
        raise OranteError("只能换 Flow 账号", "Flow 和 Grok 的人声不一样。")
    if accounts.network_throttled():
        raise OranteError("连着两个 Flow 账号都被限流了",
                          "多半是网络被盯上了，换账号也没用。先暂停 30 分钟。")
    if accounts.is_throttled(acc):
        raise OranteError("这个账号也在限流中", "换一个没在冷却的 Flow 账号。")
    t = tasks.set_account(tid, aid)
    # 排队里旧账号那条先取消，立刻用新账号接着跑
    for j in scheduler.list():
        if (j.get("task_id") == tid
                and j.get("kind") in ("generate", "regenerate")
                and j.get("status") == "queued"):
            try:
                scheduler.cancel(j["id"])
            except Exception:
                pass
    from . import jobs as jobmod
    job = jobmod.start("generate", tid)
    events.emit("tasks.changed")
    events.emit("accounts.changed")
    return ok(task=t, job=job.to_dict(), account=acc)


@app.post("/api/flow/throttle/wait")
async def flow_throttle_wait(req: Request):
    """「等它恢复」：确保受影响任务有一条 generate 排着，冷却一到自动跑。"""
    d = await body(req) or {}
    tid = (d.get("task_id") or "").strip()
    if not tid:
        return ok()
    if accounts.network_throttled():
        return ok(queued=False, network_throttled=True)
    live = [j for j in scheduler.list()
            if j.get("task_id") == tid
            and j.get("kind") in ("generate", "regenerate")
            and j.get("status") in ("queued", "running", "halted")]
    if live:
        return ok(queued=True, job=live[0])
    from . import jobs as jobmod
    job = jobmod.start("generate", tid)
    return ok(queued=True, job=job.to_dict())


@app.post("/api/flow/throttle/clear")
async def flow_throttle_clear(req: Request):
    """「现在就试」：解除这个账号的冷却（以及全网暂停），并立刻排一条 generate。

    规格 91：冷却时长是 Orante 自己定的，用户在浏览器里确认能生成时，
    没有理由继续干等。清掉之后照常跑；真被 Google 拦了，下一次提交会重新记一笔。
    """
    d = await body(req) or {}
    aid = (d.get("account_id") or "").strip()
    tid = (d.get("task_id") or "").strip()
    net = bool(d.get("network"))
    cleared = None
    if aid:
        cleared = accounts.clear_throttle(aid, who="user")
    elif net:
        accounts.clear_network_throttle(who="user")
    if net and aid:
        accounts.clear_network_throttle(who="user")
    events.emit("accounts.changed")
    events.emit("flow.throttled", account_id=aid, account_name=(cleared or {}).get("name") or "",
                network=False, cleared=True, until="", until_ts=0, alts=[],
                message="冷却已手动解除")
    queued = None
    if tid:
        live = [j for j in scheduler.list()
                if j.get("task_id") == tid
                and j.get("kind") in ("generate", "regenerate")
                and j.get("status") in ("queued", "running", "halted")]
        if live:
            queued = live[0]
        else:
            from . import jobs as jobmod
            queued = jobmod.start("generate", tid).to_dict()
    return ok(account=cleared, network=accounts.network_throttle_state(), job=queued)


@app.get("/api/flow/throttle")
def flow_throttle_state(account_id: str = ""):
    """冷却详情：为什么被限、谁定的时长、下一次探路什么时候。"""
    out = {"network": accounts.network_throttle_state()}
    if account_id:
        out["account"] = accounts.throttle_info(account_id)
    else:
        out["accounts"] = [accounts.throttle_info(a) for a in accounts.list_all(probe=False)
                           if (a.get("engine") or "") == "flow"
                           and accounts.throttle_remaining(a) > 0]
    return ok(**out)


@app.post("/api/jobs/{jid}/credit-continue")
def jobs_credit_continue(jid: str):
    """红条「我知道，继续」：本 Job 不再拦余额下降。"""
    j = scheduler.get(jid)
    if not j:
        raise OranteError("没有这个 job")
    j.credit_continue = True
    if j.status == "halted":
        j.status = "running"
        j.credit_halt = None
        j.error = None
        events.emit("job.state", job=j.to_dict())
    return ok(job=j.to_dict())


@app.post("/api/jobs")
async def jobs_submit(req: Request):
    """{kind, task_id?, ...args} → 由 jobs.py 的注册表决定跑什么。"""
    from . import instance, jobs as jobmod
    d = await body(req)
    kind = d.pop("kind", "")
    tid = d.pop("task_id", None)
    held = None
    if tid:
        try:
            t = tasks.get(tid)
            aid = d.get("account_id") or t.get("account_id")
            if kind in ("expand", "refill", "upscale", "upscale_tail", "card_swap"):
                from .services.expand import resolve_account_id
                aid = resolve_account_id(t)
            h = locks.holder(aid) if aid else None
            if h:
                op = int(h.get("owner_port") or 0)
                if op and op != int(instance.PORT or 0):
                    held = op
        except Exception:
            held = None
    job = jobmod.start(kind, tid, **d)
    extra = {}
    if held:
        extra["account_held_by"] = held
    return ok(job=job.to_dict(), **extra)


# ---------------- 🎨 制图工坊 (Image Studio) ----------------
@app.get("/api/imgstudio/state")
def imgstudio_state(tid: str = ""):
    from .services import imgstudio
    return ok(**imgstudio.get_state(tid or None))


@app.post("/api/imgstudio/add-prompts")
async def imgstudio_add_prompts(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.add_prompts(
        tid=d.get("tid"),
        text=d.get("text") or "",
        aspects=d.get("aspects") or ["9:16"],
        count_per_prompt=int(d.get("count_per_prompt") or 1),
        typography=d.get("typography"),
    )
    return ok(**res)


@app.post("/api/imgstudio/import-from-task")
async def imgstudio_import_from_task(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.import_from_task(tid=d.get("tid") or "", mode=d.get("mode") or "images")
    return ok(**res)


@app.post("/api/imgstudio/import-files")
async def imgstudio_import_files(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.import_local_files(tid=d.get("tid"), path_or_files=d.get("folder_or_files") or "")
    return ok(**res)


@app.post("/api/imgstudio/generate")
async def imgstudio_generate(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.run_generate_batch(
        tid=d.get("tid"),
        item_ids=d.get("item_ids") or [],
        engine=d.get("engine") or "flux2",
        steps=int(d.get("steps") or 4),
        async_run=bool(d.get("async", True)),
    )
    return ok(**res)


@app.post("/api/imgstudio/review")
async def imgstudio_review(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.update_review(
        tid=d.get("tid"),
        item_ids=d.get("item_ids") or [],
        review=d.get("review"),
        rating=d.get("rating"),
    )
    return ok(**res)


@app.post("/api/imgstudio/delete")
async def imgstudio_delete(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.delete_items(tid=d.get("tid"), item_ids=d.get("item_ids") or [])
    return ok(**res)


@app.post("/api/imgstudio/typography")
async def imgstudio_typography(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.apply_typography_batch(
        tid=d.get("tid"),
        item_ids=d.get("item_ids") or [],
        typography=d.get("typography") or {},
    )
    return ok(**res)


@app.post("/api/imgstudio/inspect")
async def imgstudio_inspect(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.run_inspect_batch(tid=d.get("tid"), item_ids=d.get("item_ids") or [])
    return ok(**res)


@app.post("/api/imgstudio/inpaint")
async def imgstudio_inpaint(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.run_inpaint(
        tid=d.get("tid"),
        item_id=d.get("item_id") or "",
        mask_data_url=d.get("mask_data_url") or "",
        prompt=d.get("prompt") or "",
        mode=d.get("mode") or "inpaint",
        denoise=float(d.get("denoise") if d.get("denoise") is not None else 0.75),
    )
    return ok(**res)


@app.post("/api/imgstudio/outpaint")
async def imgstudio_outpaint(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.run_outpaint(
        tid=d.get("tid"),
        item_ids=d.get("item_ids") or [],
        target_aspect=d.get("target_aspect") or "9:16",
        align=d.get("align") or "center",
        prompt=d.get("prompt") or "",
        pad_top=int(d.get("pad_top") or 0),
        pad_bottom=int(d.get("pad_bottom") or 0),
        pad_left=int(d.get("pad_left") or 0),
        pad_right=int(d.get("pad_right") or 0),
        use_ai=bool(d.get("use_ai", True)),
    )
    return ok(**res)


@app.post("/api/imgstudio/matte")
async def imgstudio_matte(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.run_matte_batch(
        tid=d.get("tid"),
        item_ids=d.get("item_ids") or [],
        mode=d.get("mode") or "rmbg",
        points=d.get("points") or [],
        bg_mode=d.get("bg_mode") or "transparent",
        bg_color=d.get("bg_color") or "#18181b",
    )
    return ok(**res)


@app.post("/api/imgstudio/segment-preview")
async def imgstudio_segment_preview(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.preview_point_segment(
        tid=d.get("tid"),
        item_id=d.get("item_id") or "",
        points=d.get("points") or [],
    )
    return ok(**res)


@app.post("/api/imgstudio/upscale")
async def imgstudio_upscale(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.run_upscale_batch(
        tid=d.get("tid"),
        item_ids=d.get("item_ids") or [],
        scale=int(d.get("scale") or 2),
    )
    return ok(**res)


@app.post("/api/imgstudio/revert")
async def imgstudio_revert(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.revert_item_version(
        tid=d.get("tid"),
        item_id=d.get("item_id") or "",
        ver=int(d.get("ver") or 1),
    )
    return ok(**res)


@app.post("/api/imgstudio/send-to-task")
async def imgstudio_send_to_task(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.send_to_task(
        tid=d.get("tid"),
        item_ids=d.get("item_ids") or [],
        use_composed=bool(d.get("use_composed", False)),
        mode=d.get("mode") or "append",
    )
    return ok(**res)


@app.post("/api/imgstudio/open-folder")
async def imgstudio_open_folder(req: Request):
    from .services import imgstudio
    d = await body(req) or {}
    res = imgstudio.open_project_folder(tid=d.get("tid"))
    return ok(**res)


@app.get("/media/imgstudio/{kind}/{item_id}")
def media_imgstudio(kind: str, item_id: str, request: Request, tid: str = "", ver: int | None = None):
    from .services import imgstudio
    path = imgstudio.resolve_media_file(kind=kind, item_id=item_id, tid=tid or None, ver=ver)
    ctype = mimetypes.guess_type(str(path))[0] or "image/png"
    cache = ("private, max-age=31536000, immutable"
             if request.query_params.get("v") else "no-store")
    return FileResponse(path, media_type=ctype, headers={"Cache-Control": cache})


# ---------------- 媒体（支持 Range） ----------------
@app.get("/media/{tid}/{rel:path}")
def media(tid: str, rel: str, request: Request):
    t = tasks.get(tid)
    base = Path(t["dir"]).resolve()
    path = (base / rel).resolve()
    if base not in path.parents or not path.is_file():
        raise OranteError("文件不存在")
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    size = path.stat().st_size
    # 带 ?v=mtime 的地址内容不会变，可以长缓存；没带就禁止缓存
    cache = ("private, max-age=31536000, immutable"
             if request.query_params.get("v") else "no-store")
    rng = request.headers.get("range")
    m = RANGE_RE.search(rng or "")
    if not m:
        return FileResponse(path, media_type=ctype,
                            headers={"Accept-Ranges": "bytes", "Cache-Control": cache})
    start = int(m.group(1) or 0)
    end = int(m.group(2)) if m.group(2) else size - 1
    start = max(0, min(start, size - 1))
    end = max(start, min(end, size - 1))
    length = end - start + 1

    def stream():
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                blob = f.read(min(CHUNK, left))
                if not blob:
                    break
                left -= len(blob)
                yield blob

    return StreamingResponse(stream(), status_code=206, media_type=ctype,
                             headers={"Content-Range": f"bytes {start}-{end}/{size}",
                                      "Accept-Ranges": "bytes", "Content-Length": str(length),
                                      "Cache-Control": cache})
