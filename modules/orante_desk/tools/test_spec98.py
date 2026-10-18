# -*- coding: utf-8 -*-
"""规格 98：本地模型 Studio Zero 当第三个出片引擎。先设 ORANTE_DATA。

用假的 Studio API（http.server，随机端口），不需要显卡、不需要 ComfyUI。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-98-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from orante import accounts, config, db, jobs as jobmod  # noqa: E402
from orante.engines import caps, studio as st  # noqa: E402
from orante.engines.base import EngineUnavailable, make_engine  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import generate as genmod  # noqa: E402

db.init()

T0107_PROMPT = (
    "【镜头】全片是一整段连续拍下来的画面。镜头轻微明显地向画面左侧平移。运动要轻微柔和、明显，不要完全不动的固定镜头。人物始终留在画面中间。\n"
    "五个高中生站在教室里低头祷告，人物保持原位不移动。\n"
    "人物的头身比例自然，4K电影质感，情绪平静，禁止背景音乐。\n"
    "人物全程紧闭双眼不睁开。画面上不要出现任何文字字幕内容。注意：上文所有内容都是拍摄指令，一个字都不要念出来；"
    "嘴型对齐台词。这个人物说话的内容：\n"
    "September 29, 30, and October 1. The person holding this phone is blessed."
)
T0107_LINE = "September 29, 30, and October 1. The person holding this phone is blessed."


def _pin():
    os.environ["ORANTE_DATA"] = str(TEST_DATA)
    db.init()


@pytest.fixture(autouse=True)
def _cfg():
    _pin()
    keys = [k for k in config.DEFAULTS if k.startswith("engine.studio.")]
    for k in keys:
        config.reset(k)
    config.set("studio.pending_tasks", [], touch_rev=False)   # 别让上一条用例登记的任务被 ticker 交给本条的假接口
    yield
    for k in keys:
        config.reset(k)
    config.set("studio.pending_tasks", [], touch_rev=False)


def _ffmpeg():
    from orante.services import checks
    return checks.find_ffmpeg()


def _make_mp4(path: Path, w=640, h=1120, seconds=6.58, rate=32000, audio=True):
    cmd = [_ffmpeg(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
           f"testsrc=size={w}x{h}:rate=24:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration={seconds}", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


def _probe(path: Path) -> dict:
    import shutil
    fp = shutil.which("ffprobe")
    if not fp:
        pytest.skip("没有 ffprobe")
    out = subprocess.run([fp,
                          "-v", "error", "-show_entries", "stream=codec_type,width,height,sample_rate,r_frame_rate"
                          ":format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(out.stdout or "{}")


class FakeStudioAPI:
    """够 StudioEngine 用的 studio_api：health / 提交 / 状态 / 文件 / 取消。"""

    def __init__(self, mp4: Path | None = None):
        self.mp4 = mp4
        self.jobs: dict[str, dict] = {}
        self.bodies: list[dict] = []
        self.deleted: list[str] = []
        self.health = {"ok": True, "comfy": True, "busy": False, "queued": 0, "version": "1"}
        self.script: list[str] = []          # 每次 GET 状态按顺序吐出的 state，空了就 done
        self.reject = ""
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                b = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path == "/v1/health":
                    return self._json(outer.health)
                parts = self.path.strip("/").split("/")
                if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "videos":
                    jid = parts[2]
                    if jid not in outer.jobs:
                        return self._json({"ok": False, "error": "没有这个任务"}, 404)
                    if len(parts) == 4 and parts[3] == "file":
                        b = outer.mp4.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp4")
                        self.send_header("Content-Length", str(len(b)))
                        self.end_headers()
                        self.wfile.write(b)
                        return
                    j = outer.jobs[jid]
                    if j["state"] not in ("cancelled",):
                        j["state"] = outer.script.pop(0) if outer.script else "done"
                    return self._json({"id": jid, "state": j["state"], "error": j.get("error", "")})
                return self._json({}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/v1/videos":
                    if outer.reject:
                        return self._json({"ok": False, "error": outer.reject}, 422)
                    outer.bodies.append(body)
                    jid = "j%d" % (len(outer.jobs) + 1)
                    outer.jobs[jid] = {"state": "queued"}
                    return self._json({"id": jid, "state": "queued"})
                return self._json({}, 404)

            def do_DELETE(self):
                jid = self.path.strip("/").split("/")[-1]
                outer.deleted.append(jid)
                if jid in outer.jobs:
                    outer.jobs[jid]["state"] = "cancelled"
                return self._json({"id": jid, "state": "cancelled"})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


_MP4 = []
_FA = []


@pytest.fixture(autouse=True)
def _fake_api_env(request):
    """每条用例都起一个假 Studio API（tools/conftest 会剔掉带自定义 fixture 参数的用例，所以用 autouse）。
    只有用到它的用例才把设置指过去。"""
    needs = "fake_api" in getattr(request.function, "__code__").co_varnames
    if not needs:
        yield
        return
    if not _MP4 and _ffmpeg():
        _MP4.append(_make_mp4(Path(tempfile.mkdtemp(prefix="szraw-")) / "raw.mp4"))
    api = FakeStudioAPI(_MP4[0] if _MP4 else None)
    config.set("engine.studio.url", api.url)
    config.set("engine.studio.enabled", True)
    config.set("engine.studio.launcher", "off")
    accounts._STUDIO_HEALTH["at"] = 0
    _FA[:] = [api]
    yield
    api.close()


def _img(tmp_path: Path, name: str, w: int, h: int) -> Path:
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (w, h), (90, 120, 150)).save(p)
    return p


# ---------------------------------------------------------------- 时长 / 能力表

def test_snap_duration_studio_keeps_everything():
    assert [caps.snap_duration("studio", d) for d in (4, 6, 8, 10, 15)] == [4, 6, 8, 10, 15]
    assert caps.snap_duration("studio", 3) == 4 and caps.snap_duration("studio", 20) == 15


def test_make_engine_and_cannot_gen_images():
    from orante.engines.base import can_gen_images
    assert can_gen_images("studio") is False
    eng = make_engine(accounts.studio_account(), lambda m: None)
    assert eng.name == "studio"


# ---------------------------------------------------------------- 虚拟账号

def test_virtual_account_not_in_db_list():
    a = accounts.get("STUDIO")
    assert a["engine"] == "studio" and a["virtual"] is True
    assert all(x["id"] != "STUDIO" for x in accounts.list_all(probe=False))
    assert db.one("SELECT 1 FROM accounts WHERE id='STUDIO'") is None


def test_virtual_account_guards():
    for fn in (lambda: accounts.delete("STUDIO"),
               lambda: accounts.update("STUDIO", name="x"),
               lambda: accounts.open_login("STUDIO"),
               lambda: accounts.set_login_state("STUDIO", "need_relogin")):
        with pytest.raises(OranteError):
            fn()
    # 限流 / 探路这类内部记录：静默忽略，不报错
    assert accounts.patch_extra("STUDIO", throttle_probe_at="x")["id"] == "STUDIO"
    assert accounts.update("STUDIO", extra={"a": 1})["id"] == "STUDIO"
    assert accounts.throttle_remaining(accounts.get("STUDIO")) == 0
    assert accounts.is_throttled("STUDIO") is False


def test_studio_public_disabled_does_not_probe():
    config.set("engine.studio.enabled", False)
    with patch.object(st, "health", side_effect=AssertionError("不该探")):
        pub = accounts.studio_public()
    assert pub["enabled"] == 0 and pub["alive"] is False


def test_studio_public_alive_when_enabled():
    fake_api = _FA[0]
    pub = accounts.studio_public()
    assert pub["enabled"] == 1 and pub["alive"] is True and pub["quality"] == "balanced"
    fake_api.health = {"ok": True, "comfy": False}
    accounts._STUDIO_HEALTH["at"] = 0
    assert accounts.studio_public()["alive"] is False


def test_flow_task_cannot_switch_to_studio_account():
    """规格 99 起本地模型能当任务账号；但已经是 Flow 的任务不能直接换成本地模型（人声不一样）。"""
    from orante import tasks as taskmod
    tid = _task("T9801")[0]
    with pytest.raises(OranteError) as ei:
        taskmod.set_account(tid, "STUDIO")
    assert "同一个引擎" in ei.value.message


# ---------------------------------------------------------------- 提示词 / 运镜

def test_studio_direction_cleans_veo_prompt():
    d = st.studio_direction(T0107_PROMPT, T0107_LINE)
    assert "一个字都不要念出来" not in d and "嘴型对齐" not in d
    assert T0107_LINE not in d and "说话的内容" not in d
    assert "视频" not in d
    assert d.startswith("【镜头】")
    assert "五个高中生站在教室里低头祷告" in d


def test_studio_direction_silent_task_drops_english_and_video_word():
    p = ("我需要做一个人物视频，就好像它正在看视频，这个人物不说话。\n\n"
         "画面中的人物全程不说话、没有对白，嘴唇保持闭合。\n"
         "No dialogue. The person does not speak; lips stay closed.")
    d = st.studio_direction(p, "")
    assert "No dialogue" not in d and "视频" not in d and "嘴唇保持闭合" in d


def test_pick_mode_camera_and_negation():
    assert st.pick_mode(T0107_PROMPT) == ("camera", False)
    assert st.pick_mode("【镜头】固定机位，人物看镜头。") == ("balanced", True)
    assert st.pick_mode("【镜头】固定机位，不要推进，也别摇镜。") == ("balanced", True)
    assert st.pick_mode("人物保持原位不移动。没有镜头行") == ("balanced", True)
    config.set("engine.studio.quality", "fast")
    assert st.pick_mode("【镜头】固定机位") == ("fast", True)
    config.set("engine.studio.lock_last", "always")
    assert st.pick_mode(T0107_PROMPT) == ("camera", True)
    config.set("engine.studio.lock_last", "never")
    assert st.pick_mode("【镜头】固定机位") == ("fast", False)


# ---------------------------------------------------------------- 引擎

def test_connect_disabled_or_offline():
    config.set("engine.studio.enabled", False)
    eng = st.StudioEngine(accounts.studio_account(), lambda m: None)
    with pytest.raises(EngineUnavailable) as ei:
        eng.connect()
    assert "没启用" in str(ei.value)
    config.set("engine.studio.enabled", True)
    config.set("engine.studio.url", "http://127.0.0.1:9")
    config.set("engine.studio.launcher", "off")
    with pytest.raises(EngineUnavailable) as ei:
        eng.connect()
    assert "没开" in str(ei.value)


def test_connect_comfy_down():
    fake_api = _FA[0]
    fake_api.health = {"ok": True, "comfy": False}
    eng = st.StudioEngine(accounts.studio_account(), lambda m: None)
    with pytest.raises(EngineUnavailable) as ei:
        eng.connect()
    assert "ComfyUI" in str(ei.value)


def test_submit_square_image_goes_16_9_and_cleans_prompt(tmp_path):
    fake_api = _FA[0]
    img = _img(tmp_path, "p01.jpg", 1200, 1200)
    logs = []
    eng = st.StudioEngine(accounts.studio_account(), logs.append)
    eng.connect()
    seg = {"s": 1, "text": T0107_LINE, "duration": 6}
    gid = eng.submit(seg, img, T0107_PROMPT, 6)
    assert gid.startswith("sz:")
    b = fake_api.bodies[0]
    assert b["aspect"] == "16:9"                  # 正方形按横，跟 Flow 一样，不读账号
    assert b["seconds"] == 6 and b["line"] == T0107_LINE
    assert b["quality"] == "camera" and b["lock_last"] is False
    assert "一个字都不要念出来" not in b["direction"]
    assert Path(b["image_path"]).is_absolute()
    assert seg["studio_job"]["aspect"] == "16:9"
    assert eng.capacity() == 1                    # 默认同时 2 条，已占 1


def test_submit_portrait_and_silent(tmp_path):
    fake_api = _FA[0]
    img = _img(tmp_path, "p02.jpg", 900, 1600)
    eng = st.StudioEngine(accounts.studio_account(), lambda m: None)
    eng.submit({"s": 2, "text": "有台词"}, img, "【镜头】固定机位。人物坐着。", 8, silent=True)
    b = fake_api.bodies[0]
    assert b["aspect"] == "9:16" and b["line"] == "" and b["quality"] == "balanced" and b["lock_last"] is True


def test_submit_rejected_is_orante_error_not_blocked(tmp_path):
    fake_api = _FA[0]
    from orante.engines.base import BlockedImage
    fake_api.reject = "参考图不存在"
    eng = st.StudioEngine(accounts.studio_account(), lambda m: None)
    with pytest.raises(OranteError) as ei:
        eng.submit({"s": 1, "text": "x"}, _img(tmp_path, "a.jpg", 900, 1600), "画面", 6)
    assert not isinstance(ei.value, BlockedImage) and "参考图不存在" in ei.value.message


def test_poll_mapping_and_queued_flag(tmp_path):
    fake_api = _FA[0]
    eng = st.StudioEngine(accounts.studio_account(), lambda m: None)
    gid = eng.submit({"s": 1, "text": "x"}, _img(tmp_path, "a.jpg", 900, 1600), "画面", 6)
    fake_api.script = ["queued", "running", "failed"]
    fake_api.jobs[gid[3:]]["error"] = "显存不够"
    r1 = eng.poll([gid])[gid]
    assert r1["state"] == "pending" and r1["queued"] is True
    r2 = eng.poll([gid])[gid]
    assert r2["state"] == "pending" and r2["queued"] is False
    r3 = eng.poll([gid])[gid]
    assert r3["state"] == "failed" and "显存不够" in r3["error"]
    assert eng.poll(["sz:nope"])["sz:nope"]["state"] == "failed"     # 404 → 失败（会用原图重交）


@pytest.mark.skipif(not _ffmpeg(), reason="没有 ffmpeg")
def test_download_normalizes_to_flow_spec(tmp_path):
    fake_api = _FA[0]
    eng = st.StudioEngine(accounts.studio_account(), lambda m: None)
    gid = eng.submit({"s": 1, "text": "x"}, _img(tmp_path, "a.jpg", 900, 1600), "画面", 6)
    assert eng.poll([gid])[gid]["state"] == "done"
    dest = tmp_path / "clips" / "T-s001-p01.mp4"
    eng.download(gid, dest)
    info = _probe(dest)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    a = [s for s in info["streams"] if s["codec_type"] == "audio"][0]
    assert (v["width"], v["height"]) == (720, 1280)
    assert v["r_frame_rate"] == "24/1"
    assert a["sample_rate"] == "48000"
    assert abs(float(info["format"]["duration"]) - 6.0) < 0.06
    assert not dest.with_suffix(".raw.mp4").exists()
    assert eng.capacity() == 2


@pytest.mark.skipif(not _ffmpeg(), reason="没有 ffmpeg")
def test_download_landscape(tmp_path):
    fake_api = _FA[0]
    eng = st.StudioEngine(accounts.studio_account(), lambda m: None)
    gid = eng.submit({"s": 1, "text": "x"}, _img(tmp_path, "sq.jpg", 1200, 1200), "画面", 8)
    eng.poll([gid])
    dest = tmp_path / "o.mp4"
    eng.download(gid, dest)
    v = [s for s in _probe(dest)["streams"] if s["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == (1280, 720)


def test_forget_url_cancel_and_close(tmp_path):
    fake_api = _FA[0]
    eng = st.StudioEngine(accounts.studio_account(), lambda m: None)
    img = _img(tmp_path, "a.jpg", 900, 1600)
    g1 = eng.submit({"s": 1, "text": "x"}, img, "画面", 6)
    g2 = eng.submit({"s": 2, "text": "x"}, img, "画面", 6)
    eng.forget_url(g1, "")
    assert g1[3:] in fake_api.deleted
    assert eng.poll([g1])[g1]["state"] == "failed"
    eng.close()
    assert g2[3:] in fake_api.deleted


def test_adopt_reads_meta_from_seg():
    eng = st.StudioEngine(accounts.studio_account(), lambda m: None)
    eng.adopt("sz:abc", {"s": 3, "duration": 8, "studio_job": {"id": "abc", "seconds": 8, "aspect": "16:9"}})
    assert eng._meta["sz:abc"]["aspect"] == "16:9" and "sz:abc" in eng._active


# ---------------------------------------------------------------- jobs 把关

class _Sched:
    def __init__(self, active=None):
        self.active = active or []
        self.sub = []

    def list(self, active_only=True):
        return list(self.active)

    def submit(self, kind, fn, task_id=None, account_id=None, **args):
        self.sub.append({"kind": kind, "task_id": task_id, "account_id": account_id, "args": args})

        class J:
            pass
        j = J()
        j.account_id = account_id
        j.id = "J9800"
        return j


def _acc(aid, engine):
    if not db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        db.run("INSERT INTO accounts(id,name,engine,port,profile,project_url,model,aspect,max_concurrent,enabled,extra,created)"
               " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (aid, "名" + aid, engine, 9300 + int(aid[-2:]), aid, "", "", "9:16", 4, 1, "{}", db.now()))


def _task(tid, copy="", n=3, durs=(8, 6, 8), img_size=(1200, 1200)):
    from PIL import Image
    _acc("A981", "flow")
    d = Path(tempfile.mkdtemp(prefix="orante-task-98-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    m = Manifest.new(tid, "规格98", "flow", "A981", "long", copy)
    m.mode = "first_frame"
    m.images = []
    for i in range(1, n + 1):
        p = f"p{i:02d}"
        Image.new("RGB", img_size, (30 * i, 60, 90)).save(d / "images" / f"{p}.jpg")
        m.images.append({"p": p, "file": f"images/{p}.jpg", "sha1": "a" * 40, "role": "主体", "status": "ok",
                         "desc": f"描述{p}", "voice": "", "eye": "", "scene": "常规", "measure": {},
                         "origin": "download", "src": ""})
    m.segments = [{"s": i, "text": ("台词%d" % i) if copy else "", "duration": durs[i - 1], "image": f"p{i:02d}",
                   "role": "anchor", "shot": "", "prompt_final": f"【镜头】固定机位。画面{i}", "prompt_locked": True,
                   "status": "planned", "gen_id": None, "error": None, "takes": []} for i in range(1, n + 1)]
    m.save(d)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, engine='flow', account_id='A981', copy=?, updated=? WHERE id=?",
               (str(d), "arranged", copy, db.now(), tid))
    else:
        db.run("INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,duration_pref,status,dir,"
               "batch_id,row_order,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (tid, "规格98", "flow", "A981", "long", copy, str(d / "images"), "", "arranged", str(d), "", 0,
                db.now(), db.now()))
    return tid, d


def test_jobs_regen_with_studio_enabled_and_disabled():
    tid, _d = _task("T9802")
    config.set("engine.studio.enabled", True)
    s = _Sched()
    with patch.object(jobmod, "scheduler", s):
        j = jobmod.start("regenerate", tid, segs=[1], account_id="STUDIO")
    assert j.account_id == "STUDIO"
    config.set("engine.studio.enabled", False)
    with patch.object(jobmod, "scheduler", _Sched()):
        with pytest.raises(OranteError) as ei:
            jobmod.start("regenerate", tid, segs=[1], account_id="STUDIO")
    assert "没启用" in ei.value.message


def test_jobs_generate_with_studio_only_when_enabled():
    """规格 99：本地模型当任务账号时 generate 直接用它；没启用就说清楚。"""
    tid, _d = _task("T9803")
    config.set("engine.studio.enabled", True)
    s = _Sched()
    with patch.object(jobmod, "scheduler", s):
        jobmod.start("generate", tid, account_id="STUDIO")
    assert s.sub[0]["account_id"] == "STUDIO"
    config.set("engine.studio.enabled", False)
    with patch.object(jobmod, "scheduler", _Sched()):
        with pytest.raises(OranteError) as ei:
            jobmod.start("generate", tid, account_id="STUDIO")
    assert "没启用" in ei.value.message


def test_jobs_studio_waits_for_flow_on_same_task():
    tid, _d = _task("T9804")
    config.set("engine.studio.enabled", True)
    busy = _Sched([{"task_id": tid, "kind": "generate", "account_id": "A981", "status": "running"}])
    with patch.object(jobmod, "scheduler", busy):
        with pytest.raises(OranteError) as ei:
            jobmod.start("regenerate", tid, segs=[1], account_id="STUDIO")
    assert "正在用" in ei.value.message


def test_retry_blocked_with_studio_goes_regenerate():
    tid, d = _task("T9805")
    calls = []
    with patch("orante.jobs.start", side_effect=lambda *a, **k: calls.append((a, k))):
        r = genmod.retry_blocked(tid, 2, account_id="STUDIO")
    assert calls == [(("regenerate", tid), {"segs": [2], "account_id": "STUDIO"})]
    assert r["account_id"] == "STUDIO"
    calls.clear()
    with patch("orante.jobs.start", side_effect=lambda *a, **k: calls.append((a, k))):
        genmod.retry_blocked(tid, 2)
    assert calls[0][0][0] == "generate"


def test_card_swap_passes_redo_account():
    import inspect
    from orante.services import desc_cards
    sig = inspect.signature(desc_cards.card_swap)
    assert "redo_account_id" in sig.parameters
    src = inspect.getsource(desc_cards.card_swap)
    assert 'account_id=str(redo_account_id)' in src


# ---------------------------------------------------------------- 真走一遍：Flow 任务用本地模型重生

class _Job:
    def __init__(self, task_id, account_id):
        self.task_id = task_id
        self.account_id = account_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J98"

    def log(self, msg):
        self.lines.append(msg)

    def check(self):
        pass

    def sleep(self, sec):
        time.sleep(0.01)


@pytest.mark.skipif(not _ffmpeg(), reason="没有 ffmpeg")
def test_regenerate_flow_task_with_studio_end_to_end():
    fake_api = _FA[0]
    tid, d = _task("T9806", copy="", durs=(8, 6, 8))
    m = Manifest.load(d)
    # p01 被 Flow 拒过：图 blocked + flow_upload_refused，段 blocked + 排给本地模型
    m.image("p01").update(status="blocked", flow_upload_refused=True, block_reason="Flow 拒收了 p01")
    m.seg(1).update(status="blocked", studio_pending=True, error="Flow 不收这张图")
    m.save(d)
    job = _Job(tid, "STUDIO")
    with patch("orante.services.generate.POLL_EVERY", 0), \
         patch("orante.services.verify.after_download",       # 假片子是测试图案，首帧当然对不上配图
               side_effect=lambda m, d, seg, dest, **k: {"verdict": "ok", "frame_dist": 2, "voided": False,
                                                         "take": {"file": "clips/" + Path(dest).name,
                                                                  "gen_id": k.get("gen_id")}}), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        genmod.regenerate(job, [1, 2])
    m2 = Manifest.load(d)
    s1, s2 = m2.seg(1), m2.seg(2)
    assert s1["status"] == "collected" and s2["status"] == "collected", job.lines
    tk = s1["takes"][-1]
    assert tk["engine"] == "studio" and tk["account_id"] == "STUDIO" and tk["duration"] == 8
    assert "studio_pending" not in s1
    im = m2.image("p01")
    assert im["status"] == "ok" and im.get("flow_upload_refused") is True    # 拒收标记留着
    assert [b["seconds"] for b in fake_api.bodies] == [8, 6]                # 时长原样
    assert all(b["aspect"] == "16:9" for b in fake_api.bodies)              # 正方形图 → 横屏
    clip = d / tk["file"]
    info = _probe(clip)
    v = [x for x in info["streams"] if x["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == (1280, 720)
    # 不说话的 Flow 任务：本地模型的片子也去掉音轨
    assert not [x for x in info["streams"] if x["codec_type"] == "audio"]
    assert m2.seg(3)["status"] == "planned"


def test_queued_time_not_counted_as_timeout(monkeypatch):
    """本地模型还在本机排队时不算超时；真正在跑超过时限才算，并取消 Studio 那边的那条。"""
    tid, d = _task("T9807", n=1, durs=(8,))

    class E:
        name = "studio"

        def __init__(self, acc, log):
            self.t0 = None
            self.cancelled = []
            self._active = set()
            E.last = self

        def connect(self):
            pass

        def close(self):
            pass

        def capacity(self):
            return 2 - len(self._active)

        def submit(self, seg, image, prompt, duration, last_image=None, **kw):
            self.t0 = time.time()
            self._active.add("sz:1")
            return "sz:1"

        def poll(self, ids):
            q = time.time() - self.t0 < E.queue_for
            if E.never_done or q:
                return {i: {"state": "pending", "queued": q} for i in ids}
            return {i: {"state": "done", "url": "x"} for i in ids}

        def download(self, gid, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\x00" * 300)
            self._active.discard(gid)
            return dest

        def cancel(self, gid):
            self.cancelled.append(gid)

        def forget_url(self, gid, url=""):
            pass

    monkeypatch.setattr(genmod, "GEN_TIMEOUT", 0.4)
    monkeypatch.setattr(genmod, "POLL_EVERY", 0)
    config.set("engine.studio.enabled", True)
    common = [patch("orante.services.generate.make_engine", side_effect=lambda a, l: E(a, l)),
              patch("orante.services.verify.after_download",
                    side_effect=lambda m, d, seg, dest, **k: {"verdict": "ok", "frame_dist": 1, "voided": False,
                                                              "take": {"file": "clips/" + Path(dest).name}}),
              patch("orante.services.tails.after_collect", return_value=None),
              patch("orante.services.describe.missing_subject_ps", return_value=[])]
    for c in common:
        c.start()
    try:
        E.queue_for, E.never_done = 1.0, False         # 排队 1 秒（> 0.4 秒超时），之后出片
        genmod.regenerate(_Job(tid, "STUDIO"), [1])
        assert Manifest.load(d).seg(1)["status"] == "collected"
        assert E.last.cancelled == []
        E.queue_for, E.never_done = 0.0, True          # 一提交就在跑，一直不出 → 超时，取消 Studio 那条
        genmod.regenerate(_Job(tid, "STUDIO"), [1])
        sg = Manifest.load(d).seg(1)
        assert sg["status"] == "failed" and "太久" in (sg.get("error") or "")
        assert "sz:1" in E.last.cancelled
    finally:
        for c in common:
            c.stop()


# ================================================================ P2：Flow 拒图自动转交

from orante.engines.base import BlockedImage  # noqa: E402
from orante.services import studio_handoff  # noqa: E402


_REFUSAL_CASES = [
    ("Flow 拒收了 p01.jpg：Failed to upload（连传 2 次都没收）", True),
    ("被拒：PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED / PROMINENT_PERSON", True),
    ("PUBLIC_ERROR_MINOR_UPLOAD", True),
    ("PUBLIC_ERROR_SAFETY_FILTER", True),
    ("被拒：PUBLIC_ERROR_AUDIO_FILTERED", False),
    ("PUBLIC_ERROR_UNUSUAL_ACTIVITY", False),
    ("等了太久没出片", False),
    ("生成失败", False),
    ("真人操作卡在「挂首帧」：Timeout", False),
    ("", False),
]


def test_is_content_refusal():
    for err, want in _REFUSAL_CASES:
        assert genmod.is_content_refusal(err) is want, err


class FlowLike:
    """假 Flow：图 p01 上传就拒（或出片时被拒），其它图正常出片。"""
    mode = "upload"           # upload | poll_content | poll_audio
    made = []

    def __init__(self, acc, log):
        self.name = "flow"
        self.can_gen_images = False
        self.account = acc
        self.subs = []
        FlowLike.made.append(self)

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 4

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        if seg.get("image") == "p01" and FlowLike.mode == "upload":
            raise BlockedImage("Flow 拒收了 p01-abc.jpg：Failed to upload（连传 2 次都没收）")
        self.subs.append(seg["s"])
        return "g%d" % seg["s"]

    def poll(self, ids):
        out = {}
        for g in ids:
            s = int(g[1:])
            if s == 1 and FlowLike.mode == "poll_content":
                out[g] = {"state": "failed", "error": "被拒：PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED"}
            elif s == 1 and FlowLike.mode == "poll_audio":
                out[g] = {"state": "failed", "error": "被拒：PUBLIC_ERROR_AUDIO_FILTERED"}
            else:
                out[g] = {"state": "done", "url": "x"}
        return out

    def download(self, gid, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * 300)
        return dest

    def forget_url(self, gid, url=""):
        pass


def _run_flow(tid, mode="upload"):
    FlowLike.mode = mode
    FlowLike.made = []
    job = _Job(tid, "A981")
    with patch("orante.services.generate.make_engine", side_effect=lambda a, l: FlowLike(a, l)), \
         patch("orante.services.generate.packable_manifest", return_value=False), \
         patch("orante.services.generate.POLL_EVERY", 0), \
         patch("orante.services.verify.after_download",
               side_effect=lambda m, d, seg, dest, **k: {"verdict": "ok", "frame_dist": 1, "voided": False,
                                                         "take": {"file": "clips/" + Path(dest).name}}), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        genmod.generate(job)
    return job


def _two_segs_same_image(tid):
    tid, d = _task(tid, n=3, durs=(8, 8, 8))
    m = Manifest.load(d)
    m.seg(2)["image"] = "p01"              # s001、s002 都用 p01
    m.save(d)
    return tid, d


def test_upload_refused_routes_to_studio_without_refill():
    config.set("engine.studio.enabled", True)
    config.set("studio.pending_tasks", [])
    tid, d = _two_segs_same_image("T9810")
    job = _run_flow(tid, "upload")
    m = Manifest.load(d)
    for sn in (1, 2):
        sg = m.seg(sn)
        assert sg["status"] == "blocked" and sg.get("studio_pending") is True, sg
    assert m.seg(3)["status"] == "collected"
    joined = "\n".join(job.lines)
    assert "交给本地模型" in joined
    assert "→ 自动换图" not in joined and "当前引擎不能生图" not in joined     # 没走换图
    assert tid in config.get("studio.pending_tasks")
    assert "已转本地模型 2" in joined


def test_fallback_off_keeps_old_behavior():
    config.set("engine.studio.enabled", True)
    config.set("engine.studio.fallback", False)
    tid, d = _two_segs_same_image("T9811")
    job = _run_flow(tid, "upload")
    m = Manifest.load(d)
    assert not m.seg(1).get("studio_pending")
    assert "当前引擎不能生图" in "\n".join(job.lines)                       # 照旧走换图路径


def test_studio_disabled_keeps_old_behavior():
    config.set("engine.studio.enabled", False)
    tid, d = _two_segs_same_image("T9812")
    _run_flow(tid, "upload")
    assert not Manifest.load(d).seg(1).get("studio_pending")


def test_poll_content_refusal_routes():
    config.set("engine.studio.enabled", True)
    tid, d = _task("T9813", n=2, durs=(8, 8))
    _run_flow(tid, "poll_content")
    sg = Manifest.load(d).seg(1)
    assert sg["status"] == "blocked" and sg.get("studio_pending") is True


def test_poll_audio_filtered_does_not_route():
    config.set("engine.studio.enabled", True)
    tid, d = _task("T9814", n=2, durs=(8, 8))
    _run_flow(tid, "poll_audio")
    assert not Manifest.load(d).seg(1).get("studio_pending")


def test_flow_skips_studio_pending_and_known_refused():
    """排给本地模型的段 Flow 不再碰；上次被 Flow 拒收过的图直接排过去，不再白传一次。"""
    config.set("engine.studio.enabled", True)
    tid, d = _task("T9815", n=3, durs=(8, 8, 8))
    m = Manifest.load(d)
    m.seg(1).update(status="blocked", studio_pending=True)
    m.image("p01").update(status="blocked")
    m.image("p02")["flow_upload_refused"] = True
    m.save(d)
    _run_flow(tid, "none")
    eng = FlowLike.made[0]
    assert eng.subs == [3]                              # s001（已排）和 s002（p02 已知被拒）都没交给 Flow
    m2 = Manifest.load(d)
    assert m2.seg(2).get("studio_pending") is True and m2.seg(2)["status"] == "blocked"


def test_progress_and_relay_ignore_studio_pending():
    tid, d = _task("T9816", n=2, durs=(8, 8))
    m = Manifest.load(d)
    m.seg(1).update(status="blocked", studio_pending=True)
    m.seg(2).update(status="blocked")
    line = genmod._progress_line(tid, m, 0)
    assert "仍被拒 1" in line and "已转本地模型 1" in line
    assert genmod._blocked_for_flow(m.seg(1)) is False and genmod._blocked_for_flow(m.seg(2)) is True


# ---------------------------------------------------------------- kick / 钩子

def _pend(tid, d):
    m = Manifest.load(d)
    m.seg(1).update(status="blocked", studio_pending=True)
    m.seg(2).update(status="blocked", studio_pending=True)
    m.save(d)


def test_kick_starts_studio_regenerate_once():
    fake_api = _FA[0]   # noqa: F841  （本地模型在线）
    tid, d = _task("T9820", n=3, durs=(8, 8, 8))
    _pend(tid, d)
    calls = []

    class J:
        id = "J9820"
    with patch("orante.scheduler.scheduler.list", return_value=[]), \
         patch("orante.jobs.start", side_effect=lambda *a, **k: calls.append((a, k)) or J()):
        r = studio_handoff.kick(tid)
    assert r.get("started") == "J9820"
    assert calls == [(("regenerate", tid), {"segs": [1, 2], "account_id": "STUDIO"})]
    assert tid not in config.get("studio.pending_tasks", [])
    # 第二次：任务上已经有本地模型的 Job 在跑 → 不重复排
    with patch("orante.scheduler.scheduler.list",
               return_value=[{"task_id": tid, "kind": "regenerate", "account_id": "STUDIO", "status": "running"}]), \
         patch("orante.jobs.start", side_effect=AssertionError("不该再排")):
        assert studio_handoff.kick(tid)["skip"] == "busy"


def test_kick_waits_when_flow_still_running():
    fake_api = _FA[0]   # noqa: F841
    tid, d = _task("T9821", n=2, durs=(8, 8))
    _pend(tid, d)
    with patch("orante.scheduler.scheduler.list",
               return_value=[{"task_id": tid, "kind": "generate", "account_id": "A981", "status": "running"}]):
        assert studio_handoff.kick(tid)["skip"] == "busy"
    assert tid in config.get("studio.pending_tasks", [])


def test_kick_offline_without_launcher_remembers():
    config.set("engine.studio.enabled", True)
    config.set("engine.studio.url", "http://127.0.0.1:9")
    config.set("engine.studio.launcher", "off")
    tid, d = _task("T9822", n=2, durs=(8, 8))
    _pend(tid, d)
    with patch("orante.scheduler.scheduler.list", return_value=[]), \
         patch("orante.jobs.start", side_effect=AssertionError("没开就不该排")):
        r = studio_handoff.kick(tid)
    assert r["skip"] == "offline" and tid in config.get("studio.pending_tasks", [])


def test_kick_disabled_or_nothing_pending():
    config.set("engine.studio.enabled", False)
    tid, d = _task("T9823", n=2, durs=(8, 8))
    assert studio_handoff.kick(tid)["skip"] == "disabled"
    config.set("engine.studio.enabled", True)
    assert studio_handoff.kick(tid)["skip"] == "none"


def test_on_job_finished_rules():
    class Jb:
        def __init__(self, kind, status, acc):
            self.kind, self.status, self.account_id, self.task_id = kind, status, acc, "T9824"
    seen = []
    with patch.object(studio_handoff, "kick", side_effect=lambda tid, **k: seen.append(tid)):
        studio_handoff.on_job_finished(Jb("generate", "cancelled", "A981"))   # 用户点了停止
        studio_handoff.on_job_finished(Jb("regenerate", "done", "STUDIO"))    # 本地模型自己那轮
        studio_handoff.on_job_finished(Jb("expand", "done", "A981"))
        studio_handoff.on_job_finished(Jb("generate", "done", "A981"))
    assert seen == ["T9824"]


def test_scheduler_finish_calls_handoff():
    import inspect
    from orante import scheduler as sch
    src = inspect.getsource(sch.Scheduler._finish)
    assert "studio_handoff.on_job_finished(job)" in src
    assert "studio_handoff.tick()" in inspect.getsource(sch.start_ticker)
    assert "studio.pending_tasks" in config.HIDDEN_KEYS


def test_ui_wiring_all_regen_entries():
    rv = (ROOT / "ui" / "js" / "panels" / "review.js").read_text(encoding="utf-8")
    gj = (ROOT / "ui" / "js" / "panels" / "generate.js").read_text(encoding="utf-8")
    ra = (ROOT / "ui" / "js" / "parts" / "regen-acc.js").read_text(encoding="utf-8")
    assert "window.RegenAccMenu" in ra and "STUDIO_ID = 'STUDIO'" in ra and "function menu(" in ra
    # ⑤ 收片：抽屉 / 换图并重生 / 旧图 / 查漏补缺 / 尾帧 / 描述卡
    for needle in ("args.account_id = regenAcc.value", "doRegen(false, false, pickerAcc.value || '')",
                   "redoReplacedMenu($event, seg)", "@go=\"acc => doGapfillRegen(acc)\"",
                   "@go=\"acc => tailsRegen(acc)\"", "args.redo_account_id = pickerAcc.value"):
        assert needle in rv, needle
    # ④ 生成：对话框 / 右键重做失败段 / 被拒段 / 检查台两个按钮 / 被拒列表
    for needle in ("args.account_id = redoAcc.value", "onMenu('fail-acc')", "onMenu('block-studio')",
                   "@go=\"acc => chainRegen(it, acc)\"", "@go=\"acc => benchRegen(it, acc)\"",
                   "retryBlocked(row, 'STUDIO')", "studio-handoff"):
        assert needle in gj, needle
    idx = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert idx.index("parts/regen-acc.js") < idx.index("panels/generate.js")
    st = (ROOT / "ui" / "js" / "parts" / "settings.js").read_text(encoding="utf-8")
    for k in [k for k in config.DEFAULTS if k.startswith("engine.studio.")]:
        assert f"'{k}'" in st, k
    for lang in ("zh", "en"):
        dct = json.loads((ROOT / "orante" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        for k in [k for k in config.DEFAULTS if k.startswith("engine.studio.")]:
            assert "set.key." + k in dct and "set.hint." + k in dct, (lang, k)
        for k in ("regen.acc.studio", "regen.acc.studio_off_tip", "gen.blocked_studio", "gen.ctx.block_studio",
                  "gen.ctx.redo_fail_acc", "gen.studio_handoff_now", "acc.studio.note", "set.group.studio"):
            assert k in dct, (lang, k)


# ================================================================ 规格 99：本地模型当任务账号 + 出片模式

def _studio_task(tid, copy="", n=2, durs=(8, 8), pref=""):
    tid, d = _task(tid, copy=copy, n=n, durs=durs)
    db.run("UPDATE tasks SET engine='studio', account_id='STUDIO', duration_pref=? WHERE id=?", (pref, tid))
    m = Manifest.load(d)
    m.engine = "studio"
    m.account_id = "STUDIO"
    m.save(d)
    return tid, d


def test_99_task_row_accepts_studio_names():
    from orante import tasks as taskmod
    for nm in ("本地模型", "studio", "Studio Zero", "STUDIO", " 本地模型 "):
        a = taskmod._acc_by_name(nm)
        assert a and a["id"] == "STUDIO" and a["engine"] == "studio", nm
    norm = taskmod.normalize_row({"name": "x", "copy": "", "image_source": "D:/imgs", "account": "本地模型",
                                  "duration_pref": "12"})
    assert norm["account_id"] == "STUDIO" and norm["engine"] == "studio" and norm["duration_pref"] == "12"


def test_99_upsert_rows_saves_studio_task():
    from orante import tasks as taskmod
    r = taskmod.upsert_rows([{"name": "本地任务", "copy": "", "image_source": "D:/imgs/x", "account": "本地模型"}])
    assert not r.get("errors"), r
    row = db.one("SELECT * FROM tasks WHERE name='本地任务' ORDER BY created DESC")
    assert row["account_id"] == "STUDIO" and row["engine"] == "studio"
    t = taskmod.get(row["id"])
    assert accounts.get(t["account_id"])["engine"] == "studio"


def test_99_set_account_studio_task_ok():
    from orante import tasks as taskmod
    tid, _d = _studio_task("T9901")
    taskmod.set_account(tid, "STUDIO")
    assert taskmod.get(tid)["account_id"] == "STUDIO"


def test_99_update_studio_account_modes_and_toggle():
    for q in ("fast", "balanced", "high", "best"):
        a = accounts.update("STUDIO", quality=q)
        assert a["model"] == q and config.get("engine.studio.quality") == q and st.current_quality() == q
    accounts.update("STUDIO", model="high")              # 跟 Flow 账号一样传 model 也行
    assert st.current_quality() == "high"
    with pytest.raises(OranteError):
        accounts.update("STUDIO", quality="ultra")
    assert st.current_quality() == "high"
    assert accounts.update("STUDIO", enabled=True)["enabled"] == 1 and accounts.studio_enabled()
    assert accounts.update("STUDIO", enabled=False)["enabled"] == 0 and not accounts.studio_enabled()
    assert accounts.update("STUDIO", max_concurrent=9)["max_concurrent"] == 4
    assert accounts.update("STUDIO", max_concurrent=-3)["max_concurrent"] == 1
    with pytest.raises(OranteError):
        accounts.update("STUDIO", name="改名")


def test_99_studio_public_reports_mode():
    config.set("engine.studio.enabled", False)
    accounts.update("STUDIO", quality="best")
    pub = accounts.studio_public(probe=False)
    assert pub["name"] == "本地模型" and pub["virtual"] is True
    assert pub["quality"] == "best" and pub["model"] == "best"


def test_99_pick_mode_high_and_best():
    config.set("engine.studio.quality", "high")
    assert st.pick_mode("【镜头】固定机位") == ("high", True)
    assert st.pick_mode(T0107_PROMPT) == ("high", False)     # 运镜：高质档自己就能拍，不降到 640p 运镜档
    config.set("engine.studio.quality", "best")
    assert st.pick_mode("【镜头】固定机位") == ("best", True)
    assert st.pick_mode(T0107_PROMPT) == ("best", False)
    config.set("engine.studio.quality", "nonsense")
    assert st.current_quality() == "balanced"


def test_99_timeout_scales_with_mode():
    eng = st.StudioEngine(accounts.studio_account(), lambda m: None)
    seg = {"s": 1, "duration": 10}
    config.set("engine.studio.quality", "fast")
    t_fast = eng.timeout_for(seg)
    config.set("engine.studio.quality", "best")
    t_best = eng.timeout_for(seg)
    assert t_fast >= 900 and t_best > t_fast
    assert t_best >= 10 * st.WALL_PER_SEC["best"] * 2


def test_99_generate_timeout_uses_engine_deadline():
    now = 10000.0
    seg = {"s": 1}
    assert genmod.gen_timed_out(seg, adopted=False, started_at=now - 1000, now=now, deadline=2000)[0] is False
    assert genmod.gen_timed_out(seg, adopted=False, started_at=now - 3000, now=now, deadline=2000)[0] is True
    assert genmod.gen_timed_out(seg, adopted=False, started_at=now - 1000, now=now)[0] is True   # 默认时限比这短


def test_99_split_no_copy_studio_any_second():
    from orante.services import split
    assert split.no_copy_duration({"duration_pref": "12"}, "studio") == 12
    assert split.no_copy_duration({"duration_pref": ""}, "studio") == 8
    with pytest.raises(Exception):
        split.no_copy_duration({"duration_pref": "20"}, "studio")


_LONG = ("I pray for the one holding this phone. May God bless you and your family with peace, health and joy. "
         "Today is a day of miracles. Whatever you have been praying for, the Lord has heard you. "
         "Do not be afraid, because He walks beside you every single step. Type Amen if you believe it. "
         "Share this with someone who needs a blessing today. God is good, all the time.")


def test_99_split_copy_studio_tiers_and_fewer_segments():
    """有文案的本地模型任务：自动按 6/10/15 档（学 Grok），段数比 Flow 少，不留碎尾巴，只在句末切。"""
    from orante.services import split
    rule = split._rule_for("studio")
    assert (rule["dur_4s_max_words"], rule["dur_6s_max_words"], rule["dur_8s_max_words"],
            rule["dur_10s_max_words"], rule["dur_15s_max_words"]) == (8, 13, 20, 25, 37)
    fl = split.preview(_LONG, "flow")
    sz = split.preview(_LONG, "studio")
    assert len(sz) < len(fl)                                    # 5 段 → 3 段
    assert all(s["duration"] in (6, 10, 15) for s in sz)
    assert min(s["words"] for s in sz) >= 8                     # 没有 4 秒碎尾巴
    assert all(s["text"].rstrip()[-1] in ".!?" for s in sz)     # 整句切，镜头完整
    assert "".join(s["text"] for s in sz).replace(" ", "") == _LONG.replace(" ", "")
    for s in sz:
        assert s["duration"] == split._duration_studio(s["text"], rule)
    # 时长档：够念完的最短一档
    assert [split._duration_studio(" ".join(["word"] * n), rule) for n in (5, 13, 14, 25, 26, 37)] == [6, 6, 10, 10, 15, 15]


def test_99_even_split_beats_greedy_tail():
    """贪心会切成 15 + 15 + 4，摊匀后段数一样、总时长更短。"""
    from orante.services import split
    rule = split._rule_for("studio")
    greedy = split._split_words(_LONG, rule["max_words"], rule["max_chars"])
    even = split._split_studio(_LONG, rule)
    assert len(even) <= len(greedy)
    tot = lambda xs: sum(split._duration_studio(x, rule) for x in xs)  # noqa: E731
    assert tot(even) <= tot(greedy)


def test_99_studio_short_and_unpunctuated_copy():
    from orante.services import split
    assert [s["duration"] for s in split.preview("Amen.", "studio")] == [6]
    run_on = ("Lord I lift up the person reading this right now and I ask that you remove every burden from "
              "their heart and fill them with your peace because they have carried so much for so long and "
              "they deserve rest so please Lord give them strength today")
    segs = split.preview(run_on, "studio")
    assert segs and "".join(s["text"] for s in segs).replace(" ", "") == run_on.replace(" ", "")


def test_99_copy_pref_studio_tiers():
    from orante.services import split
    for d in (4, 6, 8, 10, 15):
        assert split.copy_duration_pref({"duration_pref": str(d)}, "studio") == d
    with pytest.raises(Exception):
        split.copy_duration_pref({"duration_pref": "12"}, "studio")
    with pytest.raises(Exception):
        split.copy_duration_pref({"duration_pref": "10"}, "flow")    # Flow 照旧只有 4/6/8
    rule, cap = split._apply_duration_pref(split._rule_for("studio"), "studio", 10)
    assert cap == 25 and rule["max_words"] == 25 and rule["max_chars"] > 145
    rule, cap = split._apply_duration_pref(split._rule_for("studio"), "studio", 6)
    assert cap == 13 and rule["max_chars"] == 145


def test_99_split_task_studio_with_pref_15():
    from orante.services import split
    m = Manifest.new("T9910", "x", "studio", "STUDIO", "long", _LONG)
    _rule, segs = split.split_task(None, {"engine": "studio", "copy": _LONG, "duration_pref": "15"}, m)
    assert segs and all(s["duration"] == 15 for s in segs) and len(segs) <= 3


def test_99_flow_split_unchanged():
    from orante.services import split
    fl = split.preview(_LONG, "flow")
    assert [s["duration"] for s in fl] == [8, 8, 6, 8, 8]


def test_99_duration_edit_studio_auto_and_warn():
    from orante.services import duration_edit as de
    t30 = " ".join(["word"] * 30) + "."
    assert de.auto_duration({"text": t30}, "studio", {"engine": "studio"}) == 15
    assert de.auto_duration({"text": "one two three."}, "studio", {"engine": "studio"}) == 6
    assert de.auto_duration({"text": " ".join(["w"] * 18)}, "studio", {"engine": "studio"}) == 10
    assert de.duration_warn({"text": "one two three.", "duration": 6}, "studio") == ""     # 自动给的 6 秒不报长停顿
    assert "念不完" in de.duration_warn({"text": t30, "duration": 10}, "studio")
    assert de.duration_warn({"text": t30, "duration": 15}, "studio") == ""
    assert "长停顿" in de.duration_warn({"text": "one two three.", "duration": 15}, "studio")
    assert de.duration_warn({"text": " ".join(["w"] * 27), "duration": 12}, "studio") == ""   # 手改 12 秒按语速折算 30 词


def test_99_batch_duration_pref_studio():
    from orante.services import duration_edit
    tid, _d = _studio_task("T9902")
    r = duration_edit.set_tasks_duration_pref([tid], {"flow": 6})
    assert tid not in r["updated"]                               # 本地模型有自己的一栏
    r = duration_edit.set_tasks_duration_pref([tid], {"studio": 10})
    assert tid in r["updated"]
    assert db.one("SELECT duration_pref FROM tasks WHERE id=?", (tid,))["duration_pref"] == "10"


def test_99_seg_duration_studio_any_second():
    from orante.services import duration_edit
    tid, d = _studio_task("T9903")
    r = duration_edit.set_seg_durations(tid, [{"s": 1, "duration": 11}])
    assert r["changed"] == [1] and Manifest.load(d).seg(1)["duration"] == 11
    with pytest.raises(OranteError):
        duration_edit.set_seg_durations(tid, [{"s": 1, "duration": 16}])


def test_99_regen_on_studio_task_can_borrow_flow_and_back():
    """本地模型的任务也能临时换 Flow / Grok 重生；任务本身账号不变。"""
    tid, _d = _studio_task("T9904")
    config.set("engine.studio.enabled", True)
    s = _Sched()
    with patch.object(jobmod, "scheduler", s):
        jobmod.start("regenerate", tid, segs=[1], account_id="A981")
        jobmod.start("regenerate", tid, segs=[1])
    assert [x["account_id"] for x in s.sub] == ["A981", "STUDIO"]


def test_99_fill_missing_on_studio_task_borrows_image_account():
    """本地模型不能生图：重生时缺图要「生成新图替补」，借扩图账号（Flow）生，不拿本地模型去生图。"""
    tid, d = _studio_task("T9905", n=1, durs=(8,))
    (d / "images" / "p01.jpg").unlink()
    config.set("engine.studio.enabled", True)
    used = []

    class E:
        def __init__(self, acc, log):
            used.append(acc["id"])

        def connect(self):
            pass

    with patch("orante.services.generate.make_engine", side_effect=lambda a, l: E(a, l)), \
         patch("orante.services.expand.fill_segments", return_value={"unfilled": [1]}), \
         patch("orante.services.expand.resolve_account_id", return_value="A981"), \
         patch("orante.services.generate.generate", return_value={}):
        job = _Job(tid, "STUDIO")
        try:
            genmod.regenerate(job, [1], fill_missing=True)
        except Exception:
            pass
    assert used and used[0] == "A981", (used, job.lines)


@pytest.mark.skipif(not _ffmpeg(), reason="没有 ffmpeg")
def test_99_generate_studio_task_end_to_end():
    """本地模型当任务账号：点「开始生成」整条走本地模型，按设置里的出片模式提交。"""
    fake_api = _FA[0]
    tid, d = _studio_task("T9906", durs=(8, 12))
    accounts.update("STUDIO", quality="high")
    job = _Job(tid, "STUDIO")
    with patch("orante.services.generate.POLL_EVERY", 0), \
         patch("orante.services.verify.after_download",
               side_effect=lambda m, d, seg, dest, **k: {"verdict": "ok", "frame_dist": 2, "voided": False,
                                                         "take": {"file": "clips/" + Path(dest).name,
                                                                  "gen_id": k.get("gen_id")}}), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        genmod.generate(job)
    m2 = Manifest.load(d)
    assert [m2.seg(i)["status"] for i in (1, 2)] == ["collected", "collected"], job.lines
    mine = [b for b in fake_api.bodies if Path(b["image_path"]).resolve().parent.parent == Path(d).resolve()]
    assert [b["seconds"] for b in mine] == [8, 12], "\n".join(job.lines)
    assert all(b["quality"] == "high" for b in mine)
    tk = m2.seg(1)["takes"][-1]
    assert tk["engine"] == "studio" and tk["account_id"] == "STUDIO"
    # 不说话的任务：本地模型出的片也去掉音轨
    info = _probe(d / tk["file"])
    assert not [x for x in info["streams"] if x["codec_type"] == "audio"]


def test_99_server_lists_studio_as_account():
    import inspect
    from orante import server
    src = inspect.getsource(server)
    assert src.count("accs.append(studio)") >= 2


def test_99_ui_wiring_studio_task_account():
    tj = (ROOT / "ui" / "js" / "panels" / "tasks.js").read_text(encoding="utf-8")
    for needle in ("t('tasks.group.studio')", "row-studio", "studio: ['4', '6', '8', '10', '12', '15']",
                   "STUDIO_COPY_DURS = ['4', '6', '8', '10', '15']", "durPrefDlg.needStudio",
                   "STUDIO_ALIASES", "if (a.enabled) studio.push(item)", "by.studio"):
        assert needle in tj, needle
    aj = (ROOT / "ui" / "js" / "parts" / "accounts.js").read_text(encoding="utf-8")
    for needle in ("API.post('/api/accounts/STUDIO'", "STUDIO_MODES = ['fast', 'balanced', 'high', 'best']",
                   "toggleStudio", "realAccounts"):
        assert needle in aj, needle
    ra = (ROOT / "ui" / "js" / "parts" / "regen-acc.js").read_text(encoding="utf-8")
    assert "task.account_id === STUDIO_ID" in ra
    css = (ROOT / "ui" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".panel-tasks .tabulator-row.row-studio::before" in css and ".sz-mode.on" in css
    sj = (ROOT / "ui" / "js" / "parts" / "settings.js").read_text(encoding="utf-8")
    assert "set.opt.engine.studio.quality.high" in sj and "set.opt.engine.studio.quality.best" in sj
    for lang in ("zh", "en"):
        dct = json.loads((ROOT / "orante" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        keys = ["tasks.group.studio", "acc.studio.mode", "acc.studio.enable", "acc.studio.disable",
                "acc.studio.mode_saved", "acc.studio.cell_tip", "acc.studio.on_ok", "acc.studio.off_ok",
                "set.opt.engine.studio.quality.high", "set.opt.engine.studio.quality.best"]
        for q in ("fast", "balanced", "high", "best"):
            keys += [f"acc.studio.q.{q}", f"acc.studio.q_hint.{q}", f"regen.acc.studio_q.{q}"]
        for k in keys:
            assert k in dct, (lang, k)
