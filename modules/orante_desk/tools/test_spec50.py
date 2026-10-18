# -*- coding: utf-8 -*-
"""规格 50：Grok Post not found 被踢回首页后不再 6 秒一刷。

FakePage，不碰真账号。先设 ORANTE_DATA。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-50-"))
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
POST = "8d698021-db1a-49fc-9c70-d1ea9f257141"
ORIG = "d9be7c13-aaaa-bbbb-cccc-111111111111"
WRONG = "ffffffff-0000-0000-0000-222222222222"
TOAST = "Post non trovato. Reindirizzamento alla home di Imagine…"
HOME = "https://grok.com/imagine"


class FakePage:
    """goto 一律落到首页，innerText 带意大利语 Post not found。"""

    def __init__(self, url="", inner_text=TOAST, videos=None):
        self.url = url
        self.inner_text = inner_text
        self.videos = list(videos or [])
        self.gotos: list[str] = []
        self.video_evals = 0
        self.closed = False

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        self.url = HOME
        return None

    def evaluate(self, js):
        s = js if isinstance(js, str) else str(js)
        # ALL_VIDEO_JS（规格 51）带 readyState，不要算进 FIND_VIDEO_JS 的计数
        if "readyState" in s and "querySelectorAll('video')" in s:
            return [{"src": u, "currentSrc": u, "sources": [],
                     "readyState": 0, "duration": 0, "visible": True}
                    for u in self.videos]
        if "querySelectorAll('video')" in s or "currentSrc" in s:
            self.video_evals += 1
            return {"strong": list(self.videos), "weak": []}
        if "generating" in s.lower() or "正在生成" in s:
            return False
        if "失败" in s or "failed" in s.lower():
            return ""
        if "innerText" in s:
            return self.inner_text
        return self.inner_text

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


def _eng(page, conv=ORIG, row_conv=None, gid=POST, s=10):
    logs: list[str] = []
    eng = GrokEngine(ACCT, logs.append)
    eng._pages[gid] = page
    eng._conv[gid] = conv
    eng._rows[gid] = {
        "第几段": s,
        "会话Id": ORIG if row_conv is None else row_conv,
        "postId": gid,
    }
    eng._active.add(gid)
    eng._submitted_at[gid] = time.time() - 60
    return eng, logs


def test_goto_own_post_bounced():
    page = FakePage(url="https://grok.com/imagine/post/other")
    nav = grok_pipe.goto_own_post(page, POST, ORIG)
    assert nav == "bounced"
    assert grok_pipe.goto_own_post.last_toast
    assert "non trovato" in grok_pipe.goto_own_post.last_toast.lower()
    assert page.url == HOME
    assert len(page.gotos) == 1


def test_goto_own_post_on_post_and_stayed():
    own = f"https://grok.com/imagine/post/{POST}?conversation={ORIG}"
    page = FakePage(url=own)
    assert grok_pipe.goto_own_post(page, POST, ORIG) == "on_post"
    assert page.gotos == []
    page2 = FakePage(url=HOME)
    assert grok_pipe.goto_own_post(page2, POST, "") == "stayed"
    assert page2.gotos == []


def test_two_bounces_fail_with_backoff():
    """第 1 次被踢后下一轮不导航，再下一轮第 2 次被踢 → failed。"""
    page = FakePage(url=HOME)
    eng, logs = _eng(page)
    r1 = eng._poll_one(POST)
    assert r1["state"] == "pending"
    n1 = len(page.gotos)
    assert n1 >= 1
    assert any("被 Grok 踢回首页（第 1 次）" in x for x in logs)
    assert any("导航 https://grok.com/imagine/post/" in x for x in logs)
    r2 = eng._poll_one(POST)
    assert r2["state"] == "pending"
    assert len(page.gotos) == n1  # 这一轮原地不动
    r3 = eng._poll_one(POST)
    assert r3["state"] == "failed"
    assert "找不到这个 post" in (r3.get("error") or "")
    assert len(page.gotos) <= 2
    assert page.closed
    assert POST not in eng._active
    joined = "\n".join(logs)
    assert "第 2 次" in joined
    assert joined.count("→ s010 导航") == 1


def test_second_nav_uses_submit_conv():
    page = FakePage(url=HOME)
    eng, logs = _eng(page, conv=WRONG, row_conv=ORIG)
    eng._poll_one(POST)
    eng._poll_one(POST)  # 退避，不导航
    eng._poll_one(POST)  # 第二次导航，应改回提交时会话
    assert page.gotos, "至少导航过一次"
    assert ORIG in page.gotos[-1]
    assert WRONG in page.gotos[0]
    assert any("会话对不上" in x for x in logs)
    assert any(ORIG in x and WRONG in x for x in logs)


def test_probe_not_on_post_skips_videos():
    page = FakePage(
        url=HOME,
        videos=[
            "https://assets.grok.com/users/u/generated/aaa/one.mp4",
            "https://assets.grok.com/users/u/generated/bbb/two.mp4",
        ],
    )
    st, info, *_ = grok_pipe.probe(page, post=POST)
    assert st == "不在详情页"
    assert info == ""
    assert page.video_evals == 0


def test_heal_only_on_own_post():
    own = f"https://grok.com/imagine/post/{POST}?conversation={ORIG}"
    page = FakePage(url=own, inner_text="")
    eng, logs = _eng(page, conv="", row_conv="")
    eng._poll_one(POST)
    assert eng._conv.get(POST) == ORIG
    assert any("补到会话Id" in x for x in logs)

    page2 = FakePage(
        url=f"https://grok.com/imagine/post/deadbeef-0000-0000-0000-9999?conversation={WRONG}",
        inner_text="",
    )
    eng2, _ = _eng(page2, conv="", row_conv="")
    eng2._poll_one(POST)
    assert eng2._conv.get(POST, "") == ""


def test_failed_js_does_not_include_not_found():
    js = grok_pipe.IS_FAILED_JS.lower()
    assert "not found" not in js
    assert "non trovato" not in js
    assert grok_pipe.BOUNCE_TEXT_RE.search("Post non trovato")
    assert grok_pipe.BOUNCE_TEXT_RE.search("Post not found")
