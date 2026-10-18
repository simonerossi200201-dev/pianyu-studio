# -*- coding: utf-8 -*-
"""规格 51：重生/续跑后 Grok 页上有视频却不收。

FakePage，不碰真账号。先设 ORANTE_DATA。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-51-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante.engines.grok import GrokEngine  # noqa: E402
from orante import paths as _paths  # noqa: E402

_paths.add_legacy_to_sys_path()
import grok_pipe  # noqa: E402

ACCT = {
    "id": "A000", "name": "fake-grok", "engine": "grok",
    "port": 9999, "max_concurrent": 4, "profile": "",
}
POST = "b8901485-aaaa-bbbb-cccc-111111111111"
CONV = "7efea7c6-dddd-eeee-ffff-222222222222"
OWN = f"https://grok.com/imagine/post/{POST}?conversation={CONV}"
NET = "https://assets.grok.com/users/u/generated/abc"
PROMO = "https://data.x.ai/xxx.mp4"
BLOB = "blob:https://grok.com/abc-def"


class FakeDownload:
    def __init__(self, page):
        self.page = page

    def save_as(self, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)
        self.page.downloads.append(str(dest))


class _ExpectDL:
    def __init__(self, page):
        self.page = page
        self.value = FakeDownload(page)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeLocator:
    def __init__(self, page, sel=""):
        self.page = page
        self.sel = sel
        self.first = self

    def count(self):
        return 1 if self.page.has_dl_btn else 0

    def click(self, **kw):
        self.page.clicks.append(self.sel or "download")


class FakeCtx:
    def __init__(self, page):
        self.page = page
        self.created = 0

    def new_page(self):
        self.created += 1
        return self.page


class FakePage:
    """详情页停在自己的 post 上；goto 落到请求的地址（不踢回首页）。"""

    def __init__(self, url="", videos=None, video_infos=None, perf=None,
                 has_dl_btn=False, inner_text=""):
        self.url = url
        self.videos = list(videos or [])
        self.video_infos = video_infos  # None = 按 videos 现拼
        self.perf = list(perf or [])
        self.has_dl_btn = has_dl_btn
        self.inner_text = inner_text
        self.gotos: list[str] = []
        self.clicks: list[str] = []
        self.downloads: list[str] = []
        self.closed = False
        self._on = []

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        self.url = url
        return None

    def evaluate(self, js):
        s = js if isinstance(js, str) else str(js)
        if "getEntriesByType" in s:
            return list(self.perf)
        if "download|scarica|下载" in s:
            return "label" if self.has_dl_btn else ""
        if "readyState" in s and "querySelectorAll('video')" in s:
            if self.video_infos is not None:
                return list(self.video_infos)
            return [{"src": u, "currentSrc": u, "sources": [],
                     "readyState": 0, "duration": 0, "visible": True}
                    for u in self.videos]
        if "looksLikePost" in s or ("startsWith('blob:')" in s and "uniq" in s):
            strong = [u for u in self.videos
                      if u and not str(u).startswith("blob:")]
            return {"strong": strong, "weak": []}
        if "querySelectorAll('video')" in s or "currentSrc" in s:
            strong = [u for u in self.videos
                      if u and not str(u).startswith("blob:")]
            return {"strong": strong, "weak": []}
        if "generating" in s.lower() or "正在生成" in s:
            return False
        if "失败" in s or "failed" in s.lower():
            return ""
        if "innerText" in s:
            return self.inner_text
        return self.inner_text

    def locator(self, sel):
        return FakeLocator(self, sel)

    def expect_download(self, timeout=60000):
        return _ExpectDL(self)

    def on(self, event, handler):
        self._on.append((event, handler))

    def wait_for_timeout(self, ms):
        return None

    def bring_to_front(self):
        return None

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def reload(self, **kwargs):
        return None


def _eng(page, conv=CONV, gid=POST, s=10):
    logs: list[str] = []
    eng = GrokEngine(ACCT, logs.append)
    eng._pages[gid] = page
    eng._conv[gid] = conv
    eng._rows[gid] = {
        "第几段": s,
        "会话Id": conv,
        "postId": gid,
    }
    eng._active.add(gid)
    eng._submitted_at[gid] = time.time() - 60
    return eng, logs


def test_is_own_video_no_ext_and_promo():
    assert grok_pipe.is_own_video(NET, POST, True, True) is True
    assert grok_pipe.is_own_video(PROMO, POST, True, True) is False
    assert grok_pipe.is_own_video("blob:https://x", POST, True, True) is False


def test_blob_plus_netvid_ready():
    """<video src=blob> + 网络层 1 个无扩展名地址 → done，url 是那个地址。"""
    page = FakePage(
        url=OWN,
        videos=[BLOB],
        video_infos=[{"src": BLOB, "currentSrc": BLOB, "sources": [],
                      "readyState": 4, "duration": 15, "visible": True}],
    )
    eng, logs = _eng(page)
    eng._netvid[POST] = [NET]
    r = eng._poll_one(POST)
    assert r["state"] == "done"
    assert r["url"] == NET
    assert r.get("raw") == "网络层"


def test_strong_no_ext_ready():
    """1 个 <video> src 是 assets.grok.com/…/generated/abc（无扩展名）→ READY。"""
    page = FakePage(url=OWN, videos=[NET])
    eng, _ = _eng(page)
    r = eng._poll_one(POST)
    assert r["state"] == "done"
    assert r["url"] == NET


def test_promo_still_rejected():
    page = FakePage(url=OWN, videos=[PROMO])
    eng, _ = _eng(page)
    r = eng._poll_one(POST)
    assert r["state"] == "pending"


def test_download_button_after_five_playable_rounds():
    """0 个可用地址、可播放 video 连续 5 轮 → 点下载按钮。"""
    infos = [{"src": BLOB, "currentSrc": BLOB, "sources": [],
              "readyState": 4, "duration": 15, "visible": True}]
    page = FakePage(url=OWN, videos=[BLOB], video_infos=infos, has_dl_btn=True)
    eng, logs = _eng(page)
    for i in range(4):
        r = eng._poll_one(POST)
        assert r["state"] == "pending", f"第 {i + 1} 轮就好了：{r}"
        assert not page.clicks
    r = eng._poll_one(POST)
    assert r["state"] == "done"
    assert r.get("raw") == "下载按钮"
    assert page.clicks, "应该点了下载按钮"
    assert page.downloads, "应该有假 download.save_as"


def test_gate_log_once_per_five_rounds():
    """连续 5 轮 pending 且页面有 video → 过闸日志恰好 1 次。"""
    page = FakePage(url=OWN, videos=[PROMO])
    eng, logs = _eng(page)
    for _ in range(5):
        r = eng._poll_one(POST)
        assert r["state"] == "pending"
    joined = "\n".join(logs)
    assert joined.count("看见") == 1
    assert "没有一个过闸" in joined


def test_adopt_restores_conv_rows_and_page():
    page = FakePage(url="https://grok.com/imagine")
    logs: list[str] = []
    eng = GrokEngine(ACCT, logs.append)
    eng._ctx = FakeCtx(page)
    seg = {
        "gen_id": POST,
        "gen_conv": CONV,
        "s": 10,
        "image": "p01.jpg",
        "bridge": {"tail_frame": "tail.jpg"},
    }
    before = time.time()
    eng.adopt(POST, seg)
    assert eng._conv[POST] == CONV
    assert eng._rows[POST]["会话Id"] == CONV
    assert eng._rows[POST]["postId"] == POST
    assert eng._rows[POST]["图片"] == "p01.jpg"
    assert eng._rows[POST]["尾帧"] == "tail.jpg"
    assert eng._submitted_at[POST] >= before
    assert POST in eng._active
    pg = eng._page_of(POST)
    assert pg is not None
    assert pg is page
    assert CONV in (page.gotos[-1] if page.gotos else page.url)


def test_rejected_url_not_silent():
    """落盘自证作废过的地址不能再当 READY，5 轮后要打过闸日志。"""
    page = FakePage(url=OWN, videos=[NET])
    eng, logs = _eng(page)
    eng._rejected[POST] = {NET}
    for _ in range(5):
        r = eng._poll_one(POST)
        assert r["state"] == "pending"
    assert any("没有一个过闸" in x for x in logs)


def test_probe_detail_third_item():
    page = FakePage(url=OWN, videos=[PROMO])
    st, info, detail = grok_pipe.probe(page, post=POST)
    assert st == grok_pipe.S_SUBMITTED
    assert info == ""
    assert isinstance(detail, dict)
    assert "videos" in detail and "strong" in detail and "why" in detail
    assert detail["strong"] == [PROMO]
    assert detail["why"] == "strong不过闸"
