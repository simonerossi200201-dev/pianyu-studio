# -*- coding: utf-8 -*-
"""规格 21 验收：落盘自证回归 + 假重复 + 查漏补缺解析 + 闸 1 / 闸 3 单测。

默认跑第 1、2、3、4、5 条。第 6 条真实出片要 --live。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

T0013 = Path(r"D:\1_PROJECT\2026-09-07\B\T0013-李良v04")
LIST = Path(r"D:\1_PROJECT\2026-09-07\B\T0010-李良v01\clips\_stitched\缺失文案清单.txt")

WANT_WRONG = {4, 6, 8, 10, 11}
WANT_OK = {2, 3}
WANT_GAP_SEGS = {4, 5, 8, 9, 11}


class Fail(Exception):
    pass


def _ok(name, detail=""):
    print(f"  ✓ {name}" + (f"  {detail}" if detail else ""))


def _bad(name, detail=""):
    print(f"  ✗ {name}" + (f"  {detail}" if detail else ""))
    raise Fail(name)


def test_1_t0013_wrong_clips():
    from orante.services import verify
    from orante.models import Manifest
    if not T0013.is_dir():
        _bad("回归复现", f"找不到 {T0013}")
    m = Manifest.load(T0013)
    results = verify.verify_task(m, T0013, apply=False)
    by = {int(r["s"]): r for r in results}
    missing = [s for s in WANT_WRONG if s not in by]
    if missing:
        _bad("回归复现", f"这几段没有结果：{missing}  有={sorted(by)}")
    failed = []
    for s in WANT_WRONG:
        r = by[s]
        if r.get("code") != verify.E_WRONG_CLIP and r.get("verdict") != "wrong_clip":
            failed.append(f"s{s:03d}={r.get('code') or r.get('verdict')} dist={r.get('frame_dist')}")
    if failed:
        _bad("回归复现 拿错片漏报", "；".join(failed))
    for s in WANT_OK:
        r = by.get(s)
        if not r:
            _bad("回归复现", f"s{s:03d} 没有结果")
        if r.get("code") in (verify.E_WRONG_CLIP, verify.E_DUP_ASSET):
            _bad("回归复现 误伤", f"s{s:03d} 不该红旗 {r.get('code')} dist={r.get('frame_dist')}")
        if r.get("verdict") not in ("ok", "soft"):
            _bad("回归复现 误伤", f"s{s:03d} verdict={r.get('verdict')} dist={r.get('frame_dist')}")
    dist = {s: by[s].get("frame_dist") for s in sorted(by)}
    _ok("回归复现", f"错段 {sorted(WANT_WRONG)} 全红；s002/s003 通过  dist={dist}")


def test_2_fake_dup():
    from orante.services import verify
    from orante.models import Manifest
    src = T0013 / "clips" / "T0013-s002-p04.mp4"
    dst = T0013 / "clips" / "T0013-s003-p17.mp4"
    bak = T0013 / "clips" / "T0013-s003-p17.mp4.bak-verify"
    if not src.is_file() or not dst.is_file():
        _bad("假重复", f"缺文件 {src.name if not src.is_file() else dst.name}")
    shutil.copy2(dst, bak)
    try:
        shutil.copy2(src, dst)
        m = Manifest.load(T0013)
        results = verify.verify_task(m, T0013, apply=False)
        hit = next((r for r in results if int(r["s"]) == 3), None)
        if not hit:
            _bad("假重复", "s003 没有结果")
        if hit.get("code") != verify.E_DUP_ASSET and hit.get("verdict") != "dup":
            _bad("假重复", f"s003 应得 E_DUP_ASSET，实际 {hit.get('code')}/{hit.get('verdict')}")
        other = hit.get("dup_with")
        if other not in (2, 1, 3):
            # 必须指出和哪一段撞。s002 是源。
            if other != 2:
                _bad("假重复", f"dup_with={other}，期望 2")
        if other != 2:
            _bad("假重复", f"dup_with={other}，期望和 s002 撞")
        _ok("假重复", f"s003 与 s{int(other):03d} 撞车")
    finally:
        if bak.is_file():
            shutil.copy2(bak, dst)
            bak.unlink()


def test_3_gapfill():
    from orante.services import gapfill
    from orante.models import Manifest
    if not LIST.is_file():
        _bad("查漏补缺", f"找不到 {LIST}")
    m = Manifest.load(T0013)
    text = LIST.read_text(encoding="utf-8")
    groups = gapfill.parse_report(text)
    g = gapfill.pick_group(groups, "T0013")
    if not g:
        _bad("查漏补缺", f"没取到【组 T013】  groups={[x.get('group') for x in groups]}")
    if g.get("group", "").upper() not in ("T013", "T0013"):
        _bad("查漏补缺", f"组名 {g.get('group')}")
    if len(g.get("gaps") or []) != 4:
        _bad("查漏补缺", f"期望 4 处缺失，实际 {len(g.get('gaps') or [])}")
    mapped = gapfill.map_gaps(m, g["gaps"])
    segs = set()
    for item in mapped:
        segs.update(item.get("segs") or [])
    if segs != WANT_GAP_SEGS:
        _bad("查漏补缺 映射", f"得到 {sorted(segs)} 期望 {sorted(WANT_GAP_SEGS)}  mapped={mapped}")
    _ok("查漏补缺", f"组 {g['group']} · 4 处缺失 → s" +
        "/s".join(f"{x:03d}" for x in sorted(segs)))


class FakePage:
    def __init__(self, find):
        self._find = find
        self.url = "https://grok.com/imagine/post/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def evaluate(self, js):
        import grok_pipe
        if js is grok_pipe.FIND_VIDEO_JS:
            return self._find
        if isinstance(js, str) and "strong" in js and "weak" in js and "querySelectorAll('video')" in js:
            return self._find
        if "generating" in js.lower() or "正在生成" in js:
            return False
        if "失败" in js or "failed" in js.lower():
            return ""
        return None


def _find_from_html(html: str) -> dict:
    import re
    strong = re.findall(r"<video[^>]*\ssrc=['\"]([^'\"]+)", html, re.I)
    strong += re.findall(r"<source[^>]*\ssrc=['\"]([^'\"]+)", html, re.I)
    weak = re.findall(r"https?://[^\"'\\s]+\.mp4[^\"'\\s]*", html)
    su, seen = [], set()
    for u in strong:
        if u not in seen:
            seen.add(u)
            su.append(u)
    wu = [u for u in weak if u not in seen]
    return {"strong": su, "weak": wu}


def test_4_probe_three_videos():
    os.environ.setdefault("PYTHONPATH", str(ROOT / "orante" / "legacy"))
    from orante import paths as _paths
    _paths.add_legacy_to_sys_path()
    import grok_pipe
    html = """<!doctype html><html><body>
      <video src="https://assets.grok.com/users/u/generated/aaa/one.mp4"></video>
      <video src="https://assets.grok.com/users/u/generated/bbb/two.mp4"></video>
      <video src="https://assets.grok.com/users/u/generated/ccc/three.mp4"></video>
      <script>window.__pre="https://assets.grok.com/users/u/generated/ddd/prefetch.mp4"</script>
    </body></html>"""
    found = _find_from_html(html)
    if len(found["strong"]) != 3:
        _bad("闸1 假页面", f"strong={found['strong']}")
    page = FakePage(found)
    st, info, *_ = grok_pipe.probe(page, post="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    if st == grok_pipe.S_READY:
        _bad("闸1 probe", f"3 个 strong 却返回已生成，取了 {info}")
    if st != grok_pipe.S_SUBMITTED:
        _bad("闸1 probe", f"期望 已提交，实际 {st!r} info={info!r}")
    _ok("闸1 probe", "3 个 mp4 的假页面 → 已提交（不取第一个）")


def test_5_download_no_bypass():
    from orante import paths as _paths
    _paths.add_legacy_to_sys_path()
    import grok_pipe
    row = {
        "视频URL": "https://assets.grok.com/users/u/generated/ffff0000deadbeef/x.mp4",
        "postId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "视频来源": "post页",
    }
    try:
        grok_pipe.download_one(None, row, str(ROOT / "data" / "tmp" / "gate3-no.mp4"))
    except grok_pipe.NeedRedownload:
        _ok("闸3 免检已删", "视频来源=post页 且 URL 不含 postId → 拒绝")
        return
    except Exception as e:
        if type(e).__name__ == "NeedRedownload" or "拒绝下载" in str(e) or "unique_strong" in str(e):
            _ok("闸3 免检已删", f"{type(e).__name__}: {e}")
            return
        _bad("闸3", f"抛了别的异常 {type(e).__name__}: {e}")
    _bad("闸3", "视频来源=post页 被免检放行了")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    tests = [
        ("1 回归复现", test_1_t0013_wrong_clips),
        ("2 假重复", test_2_fake_dup),
        ("3 查漏补缺", test_3_gapfill),
        ("4 闸1 probe", test_4_probe_three_videos),
        ("5 闸3 免检", test_5_download_no_bypass),
    ]
    print("规格 21 验收")
    n_ok = n_fail = 0
    for name, fn in tests:
        print(f"\n[{name}]")
        try:
            fn()
            n_ok += 1
        except Fail:
            n_fail += 1
        except Exception as e:
            n_fail += 1
            print(f"  ✗ 异常 {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n合计 {n_ok} 过 / {n_fail} 不过")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
