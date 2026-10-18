# -*- coding: utf-8 -*-
"""规格 100（2026-10-06，T0124）：
1. 首帧校验：均值哈希判「对不上」时用画面结构复核，对的片子不再被当错件作废；
   两张配图本来就几乎一样时，像哪张都算对；真拿错的照样作废。
2. Grok 提交：点了以后页面晚一步跳到 post 页 = 提交成功，不再判失败（以前丢了 11 条、白花额度）；
   上一轮已经这样丢掉的，开跑时按会话在标签页里找回来。
3. 下载时网络断一下（ECONNRESET）不算失败，过一会儿再下。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-100-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter  # noqa: E402

from orante import db  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import generate as genmod, verify as vmod  # noqa: E402

db.init()
FF = vmod.find_ffmpeg()
need_ff = pytest.mark.skipif(not FF, reason="没有 ffmpeg")


# ---------------------------------------------------------------- 造图 / 造片

def _person(path: Path, seed: int, size=(1376, 768), bg=(40, 170, 70), pose=0, vignette=True):
    """绿幕前一个「人」：头、身体、手的位置随 seed / pose 变。"""
    import random
    rnd = random.Random(seed)
    w, h = size
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    cx = w // 2 + rnd.randint(-w // 6, w // 6)
    hr = int(min(w, h) * (0.10 + rnd.random() * 0.05))
    d.ellipse((cx - hr, int(h * 0.12), cx + hr, int(h * 0.12) + 2 * hr), fill=(120, 80, 60))
    d.rectangle((cx - int(hr * 1.6), int(h * 0.12) + 2 * hr, cx + int(hr * 1.6), h), fill=(230, 225, 210))
    for k in range(4):
        y = int(h * 0.55) + k * int(h * 0.08)
        d.rectangle((cx - int(hr * 1.6), y, cx + int(hr * 1.6), y + int(h * 0.025)), fill=(60, 40, 40))
    hx = cx + (pose - 1) * int(hr * 0.9) + rnd.randint(-10, 10)
    d.ellipse((hx - int(hr * 0.45), int(h * 0.42), hx + int(hr * 0.45), int(h * 0.42) + int(hr * 0.9)),
              fill=(110, 70, 50))
    gx = cx + rnd.randint(-hr, hr)
    d.rectangle((gx - 8, int(h * 0.08), gx + 8, int(h * 0.95)), fill=(30 + seed % 50, 120, 60))
    if vignette:
        mask = Image.new("L", size, 0)
        md = ImageDraw.Draw(mask)
        md.ellipse((-w // 3, -h // 3, w + w // 3, h + h // 3), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(min(w, h) // 6))
        dark = ImageEnhance.Brightness(im).enhance(0.55)
        im = Image.composite(im, dark, mask)
    im.save(path)
    return path


def _grokify(src: Path, dst_png: Path, out_size=(1280, 704)):
    """模仿 Grok 的首帧：去掉暗角、提亮、稍微裁边、换个分辨率——均值哈希会差很远，结构不变。"""
    im = Image.open(src).convert("RGB")
    w, h = im.size
    im = im.crop((int(w * 0.01), int(h * 0.015), int(w * 0.99), int(h * 0.985)))
    im = ImageEnhance.Brightness(im).enhance(1.35)
    im = ImageEnhance.Contrast(im).enhance(0.8)
    im = ImageEnhance.Color(im).enhance(1.3)
    im = im.resize(out_size)
    im.save(dst_png)
    return dst_png


def _video_from(png: Path, out: Path, seconds=1):
    subprocess.run([FF, "-loglevel", "error", "-y", "-loop", "1", "-i", str(png), "-t", str(seconds),
                    "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", str(out)], check=True)
    return out


def _task(n=4, similar_pair=False):
    d = Path(tempfile.mkdtemp(prefix="orante-100-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    m = Manifest.new("T1001", "规格100", "grok", "G1", "long", "")
    m.mode = "first_frame"
    m.images = []
    for i in range(1, n + 1):
        p = f"p{i:02d}"
        if similar_pair and i == 2:
            # p02 = p01 几乎同一张（同一场景、手稍微动了一点）
            _person(d / "images" / f"{p}.jpg", 1, pose=1)
            im = Image.open(d / "images" / f"{p}.jpg")
            im = im.transform(im.size, Image.AFFINE, (1, 0, 4, 0, 1, 2))
            im.save(d / "images" / f"{p}.jpg")
        else:
            _person(d / "images" / f"{p}.jpg", i, pose=i % 3)
        m.images.append({"p": p, "file": f"images/{p}.jpg", "role": "主体", "status": "ok"})
    m.segments = [{"s": i, "text": "", "duration": 6, "image": f"p{i:02d}", "role": "anchor",
                   "status": "submitted", "gen_id": f"g{i}", "takes": []} for i in range(1, n + 1)]
    m.save(d)
    return d, m


# ---------------------------------------------------------------- 1. 结构复核

def test_struct_sim_basic(tmp_path):
    a = _person(tmp_path / "a.jpg", 1)
    b = _person(tmp_path / "b.jpg", 2)
    g = _grokify(a, tmp_path / "ag.png")
    A, B, G = (vmod._small_image(x) for x in (a, b, g))
    assert vmod.struct_sim(G, A) > 0.9
    assert vmod.struct_sim(G, B) < 0.6
    # 画幅不同（正方形图 → 横屏片）也能对齐
    sq = _person(tmp_path / "sq.jpg", 3, size=(1024, 1024))
    gq = _grokify(sq, tmp_path / "sqg.png", out_size=(960, 960))
    assert vmod.struct_sim(vmod._small_image(gq), vmod._small_image(sq)) > 0.9


@need_ff
def test_after_download_rescues_grok_tone_shift():
    """T0124 s009 那种：画面就是配图，只是 Grok 调了色调 / 去了暗角 → 均值哈希距 33，结构对得上 → 收下。"""
    d, m = _task()
    png = _grokify(d / "images" / "p02.jpg", d / "f2.png")
    clip = _video_from(png, d / "clips" / "T1001-s002-p02.mp4")
    seg = m.seg(2)
    lines = []
    far = (1 << 256) - 1
    with patch.object(vmod, "ahash_video", return_value=far ^ (vmod.ahash_image(d / "images" / "p02.jpg") or 0)):
        r = vmod.after_download(m, d, seg, clip, gen_id="g2", log=lines.append, apply=True)
    assert r["verdict"] == "ok" and not r.get("voided"), (r, lines)
    assert r.get("frame_rescued") is True and r["take"]["frame_sim"] >= vmod.STRUCT_OK
    assert clip.is_file() and not (d / "clips" / "_错件").exists()
    assert any("收下" in ln for ln in lines)
    assert not (seg.get("check") or {}).get("issues")


@need_ff
def test_after_download_still_voids_real_wrong_clip():
    """真拿错（片子是 p03 的）照样作废，日志说清楚更像哪张。"""
    d, m = _task()
    png = _grokify(d / "images" / "p03.jpg", d / "f3.png")
    clip = _video_from(png, d / "clips" / "T1001-s002-p02.mp4")
    seg = m.seg(2)
    lines = []
    r = vmod.after_download(m, d, seg, clip, gen_id="g2", log=lines.append, apply=True)
    assert r["verdict"] == "wrong_clip" and r.get("voided")
    assert (d / "clips" / "_错件").is_dir()
    assert any("更像 p03" in ln for ln in lines), lines


@need_ff
def test_near_identical_images_either_is_fine():
    """两张配图本来就几乎一样（p01≈p02）：片子像哪张都算对，保持现状。"""
    d, m = _task(similar_pair=True)
    png = _grokify(d / "images" / "p01.jpg", d / "f1.png")
    clip = _video_from(png, d / "clips" / "T1001-s002-p02.mp4")
    rc = vmod.struct_recheck(m, d, m.seg(2), clip, d / "images" / "p02.jpg", "image", FF)
    assert rc and rc["ok"], rc


@need_ff
def test_verify_task_keeps_rescued_take():
    """复查（verify_task apply=True）不会把结构复核过的片子再挪去错件。"""
    d, m = _task()
    png = _grokify(d / "images" / "p01.jpg", d / "f1.png")
    clip = _video_from(png, d / "clips" / "T1001-s001-p01.mp4")
    sg = m.seg(1)
    sg["status"] = "collected"
    sg["takes"] = [{"file": "clips/T1001-s001-p01.mp4", "gen_id": "g1"}]
    m.save(d)
    far = (1 << 256) - 1
    with patch.object(vmod, "ahash_video", return_value=far ^ (vmod.ahash_image(d / "images" / "p01.jpg") or 0)):
        res = vmod.verify_task(m, d, apply=True)
    r1 = next(r for r in res if r["s"] == 1)
    assert r1["verdict"] == "ok" and not r1.get("voided")
    assert m.seg(1)["takes"][-1].get("frame_rescued") is True
    assert clip.is_file()


def test_bench_does_not_mark_rescued_bridge_red():
    import inspect
    from orante.services import bench
    assert 'tks[-1].get("frame_rescued")' in inspect.getsource(bench)


# ---------------------------------------------------------------- 2. Grok 提交晚一步跳页

class _Loc:
    def __init__(self, page, ok=True):
        self.page, self.ok = page, ok

    def count(self):
        return 1 if self.ok else 0

    @property
    def first(self):
        return self

    def is_enabled(self, timeout=0):
        return True

    def click(self, timeout=0):
        self.page.clicks += 1
        self.page.nav_at = time.time() + self.page.delay      # 过一会儿才跳 post 页


class _Kb:
    def __init__(self, page):
        self.page = page

    def press(self, k):
        self.page.presses += 1


class FakeGrokPage:
    """点提交以后 delay 秒才跳到 /imagine/post/…（T0124 的样子：比 1.5 秒还慢）。"""

    def __init__(self, delay=2.5):
        self.delay = delay
        self.nav_at = None
        self.clicks = 0
        self.presses = 0
        self.keyboard = _Kb(self)

    @property
    def url(self):
        if self.nav_at and time.time() >= self.nav_at:
            return ("https://grok.com/imagine/post/1fd04e97-0dae-432e-8f75-3bf88c304ed1"
                    "?conversation=e89729fe-4868-4af3-9b9d-36e29806f633")
        return "https://grok.com/imagine"

    def bring_to_front(self):
        pass

    def wait_for_timeout(self, ms):
        time.sleep(ms / 1000.0 * 0.2)       # 测试里时间压缩一下

    def evaluate(self, js):
        return False

    def get_by_role(self, *a, **k):
        return _Loc(self, ok=True)

    def get_by_text(self, *a, **k):
        return _Loc(self, ok=False)

    def locator(self, *a, **k):
        return _Loc(self, ok=False)


def _grok_pipe():
    from orante import paths
    paths.add_legacy_to_sys_path()
    import grok_pipe
    return grok_pipe


def test_click_submit_late_navigation_is_success():
    gp = _grok_pipe()
    page = FakeGrokPage(delay=0.9)        # 压缩后约等于真实的 4.5 秒才跳

    def guard():
        if gp.POST_RE.search(page.url):
            raise Exception("这一页已经跳到 post 页（…），不是能提交的铺页")

    how = gp.click_submit(page, timeout=20, guard=guard)
    assert "button:Submit" in how
    assert page.clicks == 1 and page.presses == 0, "跳页之前不能再点 / 按第二次（会多交一条）"
    post, conv = gp.capture_anchor(page, timeout=2)
    assert post.startswith("1fd04e97") and conv.startswith("e89729fe")


def test_click_submit_guard_still_blocks_before_any_click():
    """还没点过就已经在 post 页（页面自己跳的）：照旧报错，不当成提交成功。"""
    gp = _grok_pipe()
    page = FakeGrokPage()
    page.nav_at = time.time() - 1

    def guard():
        if gp.POST_RE.search(page.url):
            raise Exception("这一页已经跳到 post 页（…），不是能提交的铺页")

    with pytest.raises(Exception) as ei:
        gp.click_submit(page, timeout=3, guard=guard)
    assert "跳到 post 页" in str(ei.value)
    assert page.clicks == 0


def test_orphan_conv_of():
    msg = ("Grok 提交失败：这一页已经跳到 post 页（…n=e89729fe-4868-4af3-9b9d-36e29806f633），"
           "不是能提交的铺页")
    assert genmod.orphan_conv_of(msg) == "e89729fe-4868-4af3-9b9d-36e29806f633"
    assert genmod.orphan_conv_of({"error": msg}) == "e89729fe-4868-4af3-9b9d-36e29806f633"
    assert genmod.orphan_conv_of({"orphan_conv": "abc"}) == "abc"
    assert genmod.orphan_conv_of("首帧一直对不上配图（距 33）") == ""


class _Pg:
    def __init__(self, url):
        self.url = url

    def is_closed(self):
        return False

    def on(self, *a, **k):
        pass


class _Ctx:
    def __init__(self, pages):
        self.pages = pages


def test_grok_find_orphan_in_open_tabs():
    from orante.engines import grok as grokmod
    eng = grokmod.GrokEngine.__new__(grokmod.GrokEngine)
    eng._pages, eng._conv, eng._rows = {}, {}, {}
    eng._attach_netvid = lambda pg, post: None
    eng._ctx = _Ctx([_Pg("https://grok.com/imagine"),
                     _Pg("https://grok.com/imagine/post/aaaaaaaa-1111-2222-3333-444444444444"
                         "?conversation=E89729FE-4868-4af3-9b9d-36e29806f633")])
    post = eng.find_orphan("e89729fe-4868-4af3-9b9d-36e29806f633")
    assert post == "aaaaaaaa-1111-2222-3333-444444444444"
    assert eng._conv[post] == "e89729fe-4868-4af3-9b9d-36e29806f633"
    assert eng.find_orphan("ffffffff-0000-0000-0000-000000000000") == ""


# ---------------------------------------------------------------- 2b/3. generate 接线

class _Job:
    def __init__(self, tid, acc):
        self.task_id, self.account_id = tid, acc
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "J100"

    def log(self, msg):
        self.lines.append(msg)

    def check(self):
        pass

    def sleep(self, sec):
        time.sleep(0.005)


class GrokLike:
    """假 Grok：s001 上轮丢成孤儿（标签页还开着）；s002 第一次下载 ECONNRESET。"""
    inst = None

    def __init__(self, acc, log):
        self.name = "grok"
        self.can_gen_images = False
        self.subs = []
        self.dl_calls = {}
        GrokLike.inst = self

    def connect(self):
        pass

    def close(self):
        pass

    def capacity(self):
        return 4

    def find_orphan(self, conv):
        return "orph-1" if conv == "e89729fe-4868-4af3-9b9d-36e29806f633" else ""

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        self.subs.append(seg["s"])
        return "g%d" % seg["s"]

    def poll(self, ids):
        return {g: {"state": "done", "url": "x"} for g in ids}

    def download(self, gid, dest):
        n = self.dl_calls[gid] = self.dl_calls.get(gid, 0) + 1
        if gid == "g2" and n == 1:
            from orante.errors import OranteError
            raise OranteError("APIRequestContext.get: read ECONNRESET")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * (300 + len(gid)))
        return dest

    def forget_url(self, gid, url=""):
        pass


def _gen_task(tid):
    from PIL import Image as _I
    if not db.one("SELECT 1 FROM accounts WHERE id='G100'"):
        db.run("INSERT INTO accounts(id,name,engine,port,profile,project_url,model,aspect,max_concurrent,enabled,"
               "extra,created) VALUES('G100','G100','grok',9399,'G100','','','9:16',4,1,'{}',?)", (db.now(),))
    d = Path(tempfile.mkdtemp(prefix="orante-100g-"))
    (d / "images").mkdir()
    (d / "clips").mkdir()
    m = Manifest.new(tid, "规格100", "grok", "G100", "long", "")
    m.mode = "first_frame"
    m.images = []
    for i in (1, 2):
        _I.new("RGB", (64, 64), (40 * i, 90, 90)).save(d / "images" / f"p0{i}.jpg")
        m.images.append({"p": f"p0{i}", "file": f"images/p0{i}.jpg", "role": "主体", "status": "ok",
                         "desc": f"一个人站着 {i}"})
    err = ("Grok 提交失败：这一页已经跳到 post 页（…n=e89729fe-4868-4af3-9b9d-36e29806f633），"
           "不是能提交的铺页")
    m.segments = [
        {"s": 1, "text": "", "duration": 6, "image": "p01", "role": "anchor", "status": "failed",
         "error": err, "gen_id": None, "takes": [], "prompt_final": "【镜头】固定机位", "prompt_locked": True},
        {"s": 2, "text": "", "duration": 6, "image": "p02", "role": "anchor", "status": "planned",
         "gen_id": None, "takes": [], "prompt_final": "【镜头】固定机位", "prompt_locked": True},
    ]
    m.save(d)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status='arranged', engine='grok', account_id='G100' WHERE id=?", (str(d), tid))
    else:
        db.run("INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,duration_pref,status,dir,"
               "batch_id,row_order,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (tid, "规格100", "grok", "G100", "long", "", str(d / "images"), "", "arranged", str(d), "", 0,
                db.now(), db.now()))
    return tid, d


def test_generate_recovers_orphan_and_retries_reset_download():
    tid, d = _gen_task("T1002")
    job = _Job(tid, "G100")
    with patch("orante.services.generate.make_engine", side_effect=lambda a, l: GrokLike(a, l)), \
         patch("orante.services.generate.packable_manifest", return_value=False), \
         patch("orante.services.generate.POLL_EVERY", 0), \
         patch("orante.services.verify.after_download",
               side_effect=lambda m, d, seg, dest, **k: {"verdict": "ok", "frame_dist": 1, "voided": False,
                                                         "take": {"file": "clips/" + Path(dest).name}}), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        genmod.generate(job)
    m2 = Manifest.load(d)
    eng = GrokLike.inst
    assert 1 not in eng.subs, "孤儿找回来了，不能再交一次（白花额度）"
    assert m2.seg(1)["status"] == "collected" and m2.seg(2)["status"] == "collected", job.lines
    assert eng.dl_calls.get("g2") == 2
    txt = "\n".join(job.lines)
    assert "上次其实已经交上去了" in txt and "网络断了一下" in txt
    assert "dl_retry" not in m2.seg(2)


def test_transient_dl_error_detection():
    assert genmod.is_transient_dl_error("APIRequestContext.get: read ECONNRESET\nCall log:")
    assert genmod.is_transient_dl_error("net::ERR_CONNECTION_CLOSED")
    assert not genmod.is_transient_dl_error("成片还没有下载地址")


# ---------------------------------------------------------------- 4. 开跑前把误判作废的片子找回来

@need_ff
def test_restore_voided_brings_back_right_clip_only():
    d, m = _task()
    junk = d / "clips" / "_错件"
    junk.mkdir(parents=True)
    # s002：错件里那条其实是对的（画面就是 p02，只是色调变了）
    _video_from(_grokify(d / "images" / "p02.jpg", d / "g2.png"), junk / "1791285523-T1001-s002-p02.mp4")
    # s003：错件里那条真是别的图（p04）的
    _video_from(_grokify(d / "images" / "p04.jpg", d / "g4.png"), junk / "1791285524-T1001-s003-p03.mp4")
    # s004：错件里那条是旧配图 p01 时代的（文件名图号对不上现在的配图），不碰
    _video_from(_grokify(d / "images" / "p01.jpg", d / "g1.png"), junk / "1791285525-T1001-s004-p01.mp4")
    for sn, fn in ((2, "1791285523-T1001-s002-p02.mp4"), (3, "1791285524-T1001-s003-p03.mp4"),
                   (4, "1791285525-T1001-s004-p01.mp4")):
        # 校验器作废时会把 sha 记进 voided_sha——只有这种才找回
        m.seg(sn).update(status="failed", gen_id=None, error="首帧一直对不上配图（距 33）",
                         wrongclip_count=4, voided_sha=[vmod.sha256_file(junk / fn)])
    lines = []
    back = vmod.restore_voided(m, d, list(m.segments), log=lines.append)
    assert back == [2], lines
    s2 = m.seg(2)
    assert s2["status"] == "collected" and s2["takes"][0]["frame_rescued"] is True
    assert (d / s2["takes"][0]["file"]).is_file() and s2["takes"][0]["file"] == "clips/T1001-s002-p02.mp4"
    assert "wrongclip_count" not in s2 and s2["error"] is None
    assert m.seg(3)["status"] == "failed" and m.seg(4)["status"] == "failed"
    assert (junk / "1791285524-T1001-s003-p03.mp4").is_file()
    assert any("找回" in ln for ln in lines)
    # 再跑一次不会重复找回
    assert vmod.restore_voided(m, d, list(m.segments)) == []


def test_generate_calls_restore_before_queue():
    import inspect
    src = inspect.getsource(genmod.generate)
    assert src.index("restore_voided") < src.index("queue = list(todo)")
    assert src.index("find_orphan") < src.index("queue = list(todo)")


# ---------------------------------------------------------------- 5. 整台电脑断网不算段失败

class NetDownGrok(GrokLike):
    downs = 0

    def find_orphan(self, conv):
        return ""

    def submit(self, seg, image, prompt, duration, last_image=None, **kw):
        if NetDownGrok.downs < 3:
            NetDownGrok.downs += 1
            from orante.errors import OranteError
            raise OranteError("Grok 铺页失败：Page.goto: net::ERR_INTERNET_DISCONNECTED at https://grok.com/imagine")
        return super().submit(seg, image, prompt, duration, last_image=last_image, **kw)

    def download(self, gid, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * (300 + len(gid)))
        return dest


def test_network_down_pauses_instead_of_failing():
    tid, d = _gen_task("T1003")
    m = Manifest.load(d)
    m.seg(1).update(status="planned", error=None)
    m.save(d)
    NetDownGrok.downs = 0
    job = _Job(tid, "G100")
    with patch("orante.services.generate.make_engine", side_effect=lambda a, l: NetDownGrok(a, l)), \
         patch("orante.services.generate.packable_manifest", return_value=False), \
         patch("orante.services.generate.POLL_EVERY", 0), \
         patch("orante.services.generate.NET_DOWN_WAIT", 0), \
         patch("orante.services.verify.after_download",
               side_effect=lambda m, d, seg, dest, **k: {"verdict": "ok", "frame_dist": 1, "voided": False,
                                                         "take": {"file": "clips/" + Path(dest).name}}), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        genmod.generate(job)
    m2 = Manifest.load(d)
    assert [m2.seg(i)["status"] for i in (1, 2)] == ["collected", "collected"], job.lines
    txt = "\n".join(job.lines)
    assert "网断了" in txt and "网恢复了" in txt and "提交失败" not in txt


# ---------------------------------------------------------------- 6. 重生信号百分百生效（2026-10-06 事故）

@need_ff
def test_restore_never_touches_user_regen_discards():
    """用户点重生挪进 _弃用 的旧片（不在 voided_sha 里 / 在 user_discarded 里）绝不找回。"""
    d, m = _task()
    junk = d / "clips" / "_弃用"
    junk.mkdir(parents=True)
    f2 = _video_from(_grokify(d / "images" / "p02.jpg", d / "g2.png"), junk / "1791290917-T1001-s002-p02.mp4")
    f3 = _video_from(_grokify(d / "images" / "p03.jpg", d / "g3.png"), junk / "1791290918-T1001-s003-p03.mp4")
    m.seg(2).update(status="planned", gen_id=None)                                  # 没有 voided_sha
    sha3 = vmod.sha256_file(f3)
    m.seg(3).update(status="planned", gen_id=None, voided_sha=[sha3], user_discarded=[sha3])
    assert vmod.restore_voided(m, d, list(m.segments)) == []
    assert f2.is_file() and f3.is_file()
    assert m.seg(2)["status"] == "planned" and m.seg(3)["status"] == "planned"


class RegenGrok(GrokLike):
    def find_orphan(self, conv):
        return ""

    def download(self, gid, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x01" * (900 + len(gid)))
        return dest


@need_ff
def test_regenerate_really_regenerates_marked_segments():
    """标了重生 → 点重生：旧片挪走、真的重新提交、收新片；旧片不会被「错件找回」拉回来。"""
    tid, d = _gen_task("T1004")
    m = Manifest.load(d)
    # s001、s002 都已收片，且旧片画面就是自己的配图（结构复核一定过——正是事故里被拉回来的那种）
    for sn in (1, 2):
        png = d / f"old{sn}.png"
        Image.open(d / "images" / f"p0{sn}.jpg").convert("RGB").resize((256, 256)).save(png)
        clip = _video_from(png, d / "clips" / f"T1004-s00{sn}-p0{sn}.mp4")
        m.seg(sn).update(status="collected", error=None, gen_id=f"old{sn}", mark=True,
                         takes=[{"file": f"clips/{clip.name}", "sha256": vmod.sha256_file(clip), "verdict": "ok"}])
        m.seg(sn).pop("orphan_conv", None)
    m.save(d)
    job = _Job(tid, "G100")
    with patch("orante.services.generate.make_engine", side_effect=lambda a, l: RegenGrok(a, l)), \
         patch("orante.services.generate.packable_manifest", return_value=False), \
         patch("orante.services.generate.POLL_EVERY", 0), \
         patch("orante.services.verify.after_download",
               side_effect=lambda m, d, seg, dest, **k: {"verdict": "ok", "frame_dist": 1, "voided": False,
                                                         "take": {"file": "clips/" + Path(dest).name}}), \
         patch("orante.services.tails.after_collect", return_value=None), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]):
        genmod.regenerate(job, [1, 2])
    eng = GrokLike.inst
    assert sorted(eng.subs) == [1, 2], job.lines           # 两段都真的重新提交了
    m2 = Manifest.load(d)
    for sn in (1, 2):
        sg = m2.seg(sn)
        assert sg["status"] == "collected" and sg["gen_id"] == f"g{sn}"
        assert not sg["takes"][-1].get("restored_from")
        assert sg.get("user_discarded")
    assert "找回" not in "\n".join(job.lines)
    assert len(list((d / "clips" / "_弃用").glob("*.mp4"))) == 2

    # 之后再点一次普通的「开始生成」：用户扔掉的旧片也不会被找回
    for sn in (1, 2):
        m2.seg(sn).update(status="failed", takes=[], gen_id=None)
    m2.save(d)
    assert vmod.restore_voided(m2, d, list(m2.segments)) == []


def test_generate_skips_restore_for_regen_and_redo():
    import inspect
    src = inspect.getsource(genmod.generate)
    assert "if (mark_fresh or redo)" in src

