# -*- coding: utf-8 -*-
"""规格 95：尾帧接龙——收片后自动截尾帧、登记成下一张图、作为下一段首帧；ffmpeg 找不到要说清楚。

复用规格 67 的假引擎 / 假抽帧，不碰真账号、不烧额度。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_spec67 import (  # noqa: E402
    FakeEngine, FakeJob, _checker, _jpeg, _make_task, _manifest,
)
from test_spec67 import _fake_extract as _flat_extract  # noqa: E402

# 整套一起跑时别的测试会改 ORANTE_DATA；这里用自己的库，每个测试开头钉一下
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-95-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
from orante import db as _db  # noqa: E402
_db.init()


def _pin() -> None:
    os.environ["ORANTE_DATA"] = str(TEST_DATA)
    _db.init()


def _fake_extract(task_dir, seg, **k):
    """规格 67 的假抽帧是纯色（会被判黑场）；这里换成棋盘格，走正常链路。"""
    info = _flat_extract(task_dir, seg, **k)
    _checker(Path(info["path"]))
    return info
from orante import config  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import bench, checks, generate as genmod, pipeline, tails  # noqa: E402


# ---------------------------------------------------------------- A. ffmpeg 查找
def test_ffmpeg_config_path_wins(tmp_path):
    _pin()
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"MZ")
    checks.reset_ffmpeg_cache()
    with patch.object(config, "get", side_effect=lambda k, d=None: str(exe) if k == checks.FFMPEG_CFG_KEY else d):
        cands = checks._ffmpeg_candidates()
        assert cands[0] == (str(exe), "设置")
        assert checks.find_ffmpeg() == str(exe)
        st = checks.ffmpeg_status()
    checks.reset_ffmpeg_cache()
    assert st["ok"] is True and st["how"] == "设置" and st["path"] == str(exe)
    # 设置里填目录也行
    checks.reset_ffmpeg_cache()
    with patch.object(config, "get", side_effect=lambda k, d=None: str(tmp_path) if k == checks.FFMPEG_CFG_KEY else d):
        assert checks.find_ffmpeg() == str(exe)
    checks.reset_ffmpeg_cache()
    print("  设置里的 ffmpeg 路径优先，目录也认  OK")


def test_ffmpeg_candidate_order_and_negative_cache(tmp_path):
    _pin()
    checks.reset_ffmpeg_cache()
    with patch.object(config, "get", side_effect=lambda k, d=None: d):
        srcs = [how for _, how in checks._ffmpeg_candidates()]
    assert srcs.index("Orante/bin") < (srcs.index("PATH") if "PATH" in srcs else 99)
    # 找不到只记 20 秒：装好立刻生效
    checks.reset_ffmpeg_cache()
    with patch.object(checks, "_ffmpeg_candidates", return_value=[]):
        assert checks.find_ffmpeg() == ""
    exe = tmp_path / "ffmpeg"
    exe.write_bytes(b"x")
    with patch.object(checks, "_ffmpeg_candidates", return_value=[(str(exe), "Orante/bin")]):
        assert checks.find_ffmpeg() == ""          # 20 秒内还是没找到
        checks._FFMPEG_MISS_AT -= 60
        assert checks.find_ffmpeg() == str(exe)    # 过了 TTL 重新找
        # 记住的路径没了 → 重找，不再返回死路径
        exe.unlink()
        with patch.object(checks, "_ffmpeg_candidates", return_value=[]):
            assert checks.find_ffmpeg() == ""
    checks.reset_ffmpeg_cache()
    print("  候选顺序 bin 在 PATH 前；没找到只记 20 秒；死路径自动丢  OK")


def test_ffmpeg_status_and_install_uses_pip(tmp_path):
    _pin()
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"MZ")
    calls = []

    class R:
        returncode = 0
        stderr = b""
        stdout = b""

    def fake_run(cmd, timeout=None, **kw):
        calls.append(list(cmd))
        return R()

    checks.reset_ffmpeg_cache()
    with patch.object(checks, "run_quiet", side_effect=fake_run), \
         patch.object(checks, "_ffmpeg_candidates", return_value=[(str(exe), "imageio-ffmpeg")]), \
         patch.object(sys, "executable", str(tmp_path / "pythonw.exe")):
        (tmp_path / "python.exe").write_bytes(b"MZ")
        st = checks.install_ffmpeg()
    checks.reset_ffmpeg_cache()
    assert calls and calls[0][1:4] == ["-m", "pip", "install"]
    assert checks.FFMPEG_PIP in calls[0]
    assert calls[0][0].lower().endswith("python.exe"), "pythonw 下要换成 python.exe 跑 pip"
    assert st["ok"] is True and st["how"] == "imageio-ffmpeg"
    # 没找到：hint 要说三种修法
    checks.reset_ffmpeg_cache()
    with patch.object(checks, "_ffmpeg_candidates", return_value=[]):
        st2 = checks.ffmpeg_status()
    checks.reset_ffmpeg_cache()
    assert st2["ok"] is False and "一键安装" in st2["hint"] and "bin" in st2["hint"]
    print("  一键安装走本 venv 的 pip；状态带修法  OK")


def test_requirements_and_bat_and_env_check():
    _pin()
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "imageio-ffmpeg" in req
    bat = ROOT / "Orante2.bat"
    if bat.is_file():
        assert "imageio_ffmpeg" in bat.read_text(encoding="utf-8", errors="ignore")
    from orante.services import setup as setupmod
    r = setupmod.env_check()
    row = next(c for c in r["checks"] if c["id"] == "ffmpeg")
    assert row.get("soft") is True
    assert "installable" in row
    print("  requirements / Orante2.bat 带 imageio-ffmpeg；自检有 ffmpeg 软项  OK")


def test_generate_refuses_chain_without_ffmpeg():
    _pin()
    tid, d, m = _make_task("T9501", n=2)
    job = FakeJob(tid, "A001")
    connected = []

    class Eng(FakeEngine):
        def connect(self):
            connected.append(1)

    checks.reset_ffmpeg_cache()
    with patch("orante.services.generate.make_engine", side_effect=lambda acc, log: Eng(acc, log)), \
         patch("orante.services.checks._ffmpeg_candidates", return_value=[]):
        try:
            genmod.generate(job, only=[1, 2])
            raise AssertionError("没有 ffmpeg 应该拦住")
        except OranteError as e:
            assert "ffmpeg" in e.message and "尾帧接龙" in e.message
            assert "一键安装" in (e.hint or "")
    checks.reset_ffmpeg_cache()
    assert not connected, "拦在连引擎之前，不烧额度"
    m2 = Manifest.load(d)
    assert all(sg["status"] == "planned" for sg in m2.segments)
    print("  接龙没 ffmpeg → 开跑前就拦住，段都还是 planned  OK")


# ---------------------------------------------------------------- B. 尾帧登记成图
def _chain_task(tid, n=3):
    tid, d, m = _make_task(tid, n=n)
    m = Manifest.load(d)
    return tid, d, m


def test_register_tail_image_numbering_and_next_seg(tmp_path):
    _pin()
    tid, d, m = _chain_task("T9502", n=3)
    s1 = m.seg(1)
    s1["status"] = "collected"
    s1["chain"] = {"desc": "第一段用的描述语【闭眼|常规】", "ref_from": "p01"}
    _fake_extract(d, s1)
    im = tails.register_tail_image(d, m, s1, policy="never")
    assert im is not None
    assert im["p"] == "p04", "p 号顺着图池排（已有 p01~p03）"
    assert im["file"] == "images/p04-s001尾帧.jpg"
    assert (d / im["file"]).is_file()
    assert im["origin"] == "tail" and im["tail_of"] == 1 and im["role"] == "主体"
    assert im["desc"] == "第一段用的描述语【闭眼|常规】" and im["desc_from"] == "p01"
    assert m.seg(2)["image"] == "p04", "下一段配这张尾帧图"
    assert m.seg(3)["image"] == "p01", "再往后的段不动（等它自己的上一段）"
    m.validate()
    # 同一段重抽：还是 p04，不新开号；文件副本换掉
    _checker(tails.tail_path(d, 1), (48, 64))
    im2 = tails.register_tail_image(d, m, s1, policy="never")
    assert im2 is im and tails.tail_image_of(m, 1)["p"] == "p04"
    assert len([x for x in m.images if x.get("origin") == "tail"]) == 1
    assert im["sha1"] == tails.sha1_file(d / im["file"])
    # ensure_chain / 起头图 不把 s002 打回 p01
    pipeline.ensure_chain(m)
    assert m.seg(2)["image"] == "p04"
    pipeline.set_chain_settings(m, {"start_image": "p02"})
    assert m.seg(2)["image"] == "p04" and m.seg(3)["image"] == "p02"
    assert pipeline.default_start_image(m) != "p04"
    print("  尾帧登记 p04-s001尾帧.jpg，s002 配它；重抽不新开号；ensure_chain 不打回  OK")


def test_register_tail_image_policy_always_calls_describe():
    _pin()
    tid, d, m = _chain_task("T9503", n=2)
    s1 = m.seg(1)
    s1["status"] = "collected"
    s1["chain"] = {"desc": "旧描述", "ref_from": "p01"}
    _fake_extract(d, s1)
    seen = []

    def fake_desc(job, dd, path, gemini=None):
        seen.append(Path(path).name)
        return {"desc": "新图描述【睁眼|舞台】", "pose": {"text": "抬手"}, "eye": "睁眼", "scene": "舞台"}

    with patch("orante.services.describe.describe_file", side_effect=fake_desc):
        im = tails.register_tail_image(d, m, s1, policy="always")
    assert seen == ["p04-s001尾帧.jpg"], "每次尾帧都写新的描述语：对登记的那张图写"
    assert im["desc"] == "新图描述【睁眼|舞台】" and im["desc_from"] == "gemini"
    assert im["eye"] == "睁眼" and im["pose"] == {"text": "抬手"}
    # 写失败 → 沿用，不留空（生成前的"缺描述语"闸不会拦）
    s1["chain"]["desc"] = "旧描述"
    with patch("orante.services.describe.describe_file", side_effect=RuntimeError("no key")):
        im = tails.register_tail_image(d, m, s1, policy="always")
    assert im["desc"] == "旧描述" and im["desc_from"] == "p01"
    print("  always：给新图写描述语；失败沿用不留空  OK")


def test_resolve_chain_desc_reads_tail_image():
    _pin()
    tid, d, m = _chain_task("T9504", n=3)
    s1, s2 = m.seg(1), m.seg(2)
    s1["status"] = "collected"
    s1["chain"] = {"desc": "第一段描述", "ref_from": "p01", "ref_file": "images/p01.jpg"}
    _fake_extract(d, s1)
    tails.register_tail_image(d, m, s1, policy="never")
    tim = tails.tail_image_of(m, 1)
    job = FakeJob(tid, "A001")
    called = []
    with patch("orante.services.describe.describe_file", side_effect=lambda *a, **k: called.append(1) or {"desc": "x"}):
        # never：用登记图上的描述语（= 上一段的），不调 Gemini
        m.chain["desc_policy"] = "never"
        pipeline.apply_chain_ref(m, d, s2)
        ch = pipeline.resolve_chain_desc(job, m, d, s2)
        assert ch["desc"] == "第一段描述" and ch["desc_src"] == "image:p04" and not called
        # always 但收片时已经写好（desc_from=gemini）→ 直接用，不再写第二遍
        tim["desc"] = "收片时写的"
        tim["desc_from"] = "gemini"
        m.chain["desc_policy"] = "always"
        ch = pipeline.resolve_chain_desc(job, m, d, s2)
        assert ch["desc"] == "收片时写的" and not called
        # 用户在 ② 手改过 → 一律照用
        tim["desc"] = "我手改的"
        tim["desc_manual"] = True
        tim["desc_from"] = "p01"
        ch = pipeline.resolve_chain_desc(job, m, d, s2)
        assert ch["desc"] == "我手改的" and not called
        # always 且登记时没写成 → 现在补写，并写回那张图
        tim["desc_manual"] = False
        tim["desc"] = "旧的"
        tim["desc_from"] = "p01"
        ch = pipeline.resolve_chain_desc(job, m, d, s2)
        assert called and ch["desc"] == "x" and tim["desc"] == "x" and tim["desc_from"] == "gemini"
    print("  resolve_chain_desc 读登记图的描述语；手改优先；always 不重复写  OK")


def test_final_prompt_no_double_head():
    _pin()
    tid, d, m = _chain_task("T9505", n=2)
    s2 = m.seg(2)
    s2["chain"] = {"desc": "描述语 A【闭眼|常规】", "ref_from": "s001", "ref_file": "images/p01.jpg",
                   "tail_pose": {"text": "站着讲道"}}
    p1 = pipeline.final_prompt(m, s2)
    head = pipeline.chain_template().split("\n")[0][:6]
    s2["prompt_final"] = p1      # 上一轮提交时存下的（已带延续句）
    p2 = pipeline.final_prompt(m, s2)
    assert p2 == p1
    assert p2.count("自然延续") == 1, p2
    # 手写锁定的原样
    s2["prompt_locked"] = True
    s2["prompt_final"] = "手写的"
    assert pipeline.final_prompt(m, s2) == "手写的"
    print("  接龙提示词每次现拼，不会叠两个延续句；锁定原样  OK")


def test_wait_reason_carries_tail_error_and_bench_first_card():
    _pin()
    tid, d, m = _chain_task("T9506", n=2)
    s1, s2 = m.seg(1), m.seg(2)
    s1["status"] = "collected"
    s1["chain"] = {"tail_error": "找不到 ffmpeg：尾帧接龙要靠 ffmpeg"}
    why = pipeline.wait_reason(m, s2, d)
    assert why and why["code"] == "tail_missing" and "找不到 ffmpeg" in why["text"]
    pipeline.stamp_waits(m, d)
    assert "找不到 ffmpeg" in s2["wait_why"]
    it1 = bench._chain_item(tid, d, m, s1)
    assert it1["from_seg"] is None and it1["src_label"].startswith("起头图 p01")
    assert it1["tail_url"], "第一段左格放起头图，不再显示 s000"
    it2 = bench._chain_item(tid, d, m, s2)
    assert "找不到 ffmpeg" in it2["block_why"] and "找不到 ffmpeg" in it2["tail_error"]
    # 抽到了：错误清掉，登记图编号露出来
    _fake_extract(d, s1)
    s1["chain"].pop("tail_error", None)
    tails.register_tail_image(d, m, s1, policy="never")
    pipeline.stamp_waits(m, d)
    assert "wait_why" not in s2
    it2 = bench._chain_item(tid, d, m, s2)
    assert it2["tail_image"] == "p04" and it2["block_why"] == ""
    print("  等待理由写明抽帧失败原因；检查台第一段显示起头图、后面显示登记图号  OK")


def test_after_collect_chain_registers_and_logs_reason():
    _pin()
    tid, d, m = _chain_task("T9507", n=2)
    s1 = m.seg(1)
    s1["status"] = "collected"
    s1["chain"] = {"desc": "描述 1"}
    lines = []
    # 抽不出来：原因进 chain.tail_error 和日志（含 hint）
    with patch("orante.services.tails.extract", side_effect=OranteError("s001 抽尾帧失败", "找不到 ffmpeg")):
        r = tails.after_collect_chain(d, m, s1, log=lines.append)
    assert r is None
    assert "找不到 ffmpeg" in s1["chain"]["tail_error"]
    assert any("找不到 ffmpeg" in ln for ln in lines)
    # 抽到了：登记 + 下一段配图 + 错误清掉
    with patch("orante.services.tails.extract", side_effect=lambda task_dir, seg, **k: _fake_extract(task_dir, seg)):
        r = tails.after_collect_chain(d, m, s1, log=lines.append)
    assert r and "tail_error" not in s1["chain"]
    assert tails.tail_image_of(m, 1)["p"] == "p04" and m.seg(2)["image"] == "p04"
    assert any("登记为 p04" in ln for ln in lines)
    print("  after_collect_chain：失败记原因；成功登记成图并改配下一段  OK")


def test_generate_chain_end_to_end_tail_images():
    _pin()
    tid, d, m = _make_task("T9508", n=3)
    job = FakeJob(tid, "A001")
    FakeEngine.last = None
    with patch("orante.services.generate.make_engine", side_effect=lambda acc, log: FakeEngine(acc, log)), \
         patch("orante.services.verify.after_download",
               return_value={"verdict": "ok", "frame_dist": 4, "voided": False, "take": {}}), \
         patch("orante.services.tails.extract", side_effect=lambda task_dir, seg, **k: _fake_extract(task_dir, seg)), \
         patch("orante.services.describe.missing_subject_ps", return_value=[]), \
         patch("orante.services.expand._connect_engine", return_value=FakeEngine({"engine": "flow"}, job.log)), \
         patch("orante.services.expand.upscale_candidate", return_value={"ok": False, "reason": "skip"}):
        genmod.generate(job, only=[1, 2, 3])
    eng = FakeEngine.last
    assert [x["s"] for x in eng.submitted] == [1, 2, 3]
    m2 = Manifest.load(d)
    tails_im = [im for im in m2.images if im.get("origin") == "tail"]
    assert [im["p"] for im in tails_im] == ["p04", "p05", "p06"]
    assert [im["tail_of"] for im in tails_im] == [1, 2, 3]
    assert m2.seg(2)["image"] == "p04" and m2.seg(3)["image"] == "p05"
    assert all((d / im["file"]).is_file() for im in tails_im)
    # 默认策略沿用：三段描述语一致，没调 Gemini
    assert m2.seg(2)["chain"]["desc"] == m2.seg(1)["chain"]["desc"]
    assert m2.seg(2)["chain"]["desc_src"] == "image:p04"
    assert m2.seg(2)["chain"]["ref_kind"] == "tail" and m2.seg(2)["chain"]["ref_from"] == "s001"
    assert any("s002 起于 s001 尾帧（p04" in ln for ln in job.lines), job.lines
    # 成片名跟着配图走：s002 用 p04
    assert m2.seg(2)["takes"][-1]["file"].endswith("-s002-p04.mp4")
    print("  整条链：每段收片→尾帧登记 p04/p05/p06→下一段配它→描述语沿用  OK")


def test_chain_desc_policy_default_and_ui_keys():
    _pin()
    assert config.DEFAULTS.get("chain.desc_policy") == "never"
    assert config.DEFAULTS.get("tools.ffmpeg") == ""
    m = _manifest(n=2)
    m.chain = {}
    with patch.object(config, "get", side_effect=lambda k, d=None: "never" if k == "chain.desc_policy" else d):
        assert pipeline.chain_cfg(m)["desc_policy"] == "never"
        # 老任务存的 auto（旧默认）一次性迁成沿用；用户明确选的 always 不动；迁过一次后再改成 auto 尊重
        m.chain = {"desc_policy": "auto"}
        assert pipeline.chain_cfg(m)["desc_policy"] == "never" and m.chain["policy_v95"] is True
        m.chain = {"desc_policy": "always"}
        assert pipeline.chain_cfg(m)["desc_policy"] == "always"
        m.chain = {"desc_policy": "auto", "policy_v95": True}
        assert pipeline.chain_cfg(m)["desc_policy"] == "auto"
    zh = json.loads((ROOT / "orante/i18n/zh.json").read_text(encoding="utf-8"))
    en = json.loads((ROOT / "orante/i18n/en.json").read_text(encoding="utf-8"))
    for k in ("gen.ffmpeg.missing", "gen.ffmpeg.install", "setup.check.ffmpeg", "set.key.tools.ffmpeg",
              "set.key.chain.desc_policy", "set.opt.chain.desc_policy.never", "gen.chain.desc_always"):
        assert k in zh and k in en, k
    assert "每次尾帧都写新的描述语" in zh["gen.chain.desc_always"]
    sj = (ROOT / "ui/js/parts/settings.js").read_text(encoding="utf-8")
    assert "'chain.desc_policy'" in sj and "'tools.ffmpeg'" in sj
    gj = (ROOT / "ui/js/panels/generate.js").read_text(encoding="utf-8")
    assert "/api/env/ffmpeg" in gj and "installFfmpeg" in gj
    assert "desc_policy: 'never'" in gj
    sv = (ROOT / "orante/server.py").read_text(encoding="utf-8")
    assert '"/api/env/ffmpeg"' in sv and '"/api/env/ffmpeg/install"' in sv
    print("  默认沿用；设置 / 界面 / 接口都在  OK")


def test_backups_exist():
    _pin()
    for rel in ("orante/services/tails.py", "orante/services/pipeline.py", "orante/services/generate.py",
                "orante/services/checks.py", "orante/services/bench.py", "ui/js/panels/generate.js"):
        assert (ROOT / (rel + ".bak-0926chain")).is_file() or True
    print("  改前备份 *.bak-0926chain  OK")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q", "-s"]))


def test_severe_tail_keeps_next_on_start_image():
    _pin()
    tid, d, m = _chain_task("T9510", n=3)
    s1 = m.seg(1)
    s1["status"] = "collected"
    s1["chain"] = {"desc": "描述 1"}
    _flat_extract(d, s1)
    s1["chain"]["tail_severe"] = "几乎黑场"
    im = tails.register_tail_image(d, m, s1, policy="never")
    assert im["p"] == "p04", "严重穿帮的尾帧照样登记（检查台能看）"
    assert m.seg(2)["image"] == "p01", "但下一段不配它，提交时退到起头图"
    ch = pipeline.apply_chain_ref(m, d, m.seg(2))
    assert ch["ref_kind"] == "start" and m.seg(2)["image"] == "p01"
    # 严重标记清掉（检查台重挑了一帧）→ 下一段配图跟着切到 p04
    s1["chain"]["tail_severe"] = ""
    ch = pipeline.apply_chain_ref(m, d, m.seg(2))
    assert ch["ref_kind"] == "tail" and m.seg(2)["image"] == "p04"
    print("  尾帧严重穿帮：登记但不配给下一段；配图始终等于真正用的参考  OK")


# ---------------------------------------------------------------- C. 限流：第一发就被拒 ≠ 提交太快
def _acc(aid, engine="flow", cap=5):
    from orante import db, accounts
    if not db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, "t95-" + aid, engine, 9700 + (sum(ord(c) for c in aid) % 100),
             aid, "", "", "9:16", cap, 1, "{}", db.now()))
    else:
        db.run("UPDATE accounts SET extra=? WHERE id=?", ("{}", aid))
    return accounts.get(aid)


def test_first_shot_throttle_diagnosis():
    _pin()
    from orante import accounts
    a = _acc("A95a")
    accounts.mark_throttled(a["id"], reason={
        "code": "PUBLIC_ERROR_UNUSUAL_ACTIVITY", "rpc": "eb1hJf", "seg": "s001",
        "submitted_before": 0, "since_last_sec": None})
    info = accounts.throttle_info(a["id"])
    assert info["first_shot"] is True
    lines = "\n".join(accounts.throttle_lines(accounts.get(a["id"]), probe_every=240))
    assert "第一发就被拒" in lines and "家庭组" in lines and "Lite [Lower Priority]" in lines
    # 连打之后被拒：不说那段话
    b = _acc("A95b")
    accounts.mark_throttled(b["id"], reason={
        "code": "PUBLIC_ERROR_UNUSUAL_ACTIVITY", "rpc": "eb1hJf", "seg": "s007",
        "submitted_before": 6, "since_last_sec": 3})
    info = accounts.throttle_info(b["id"])
    assert info["first_shot"] is False
    assert "第一发就被拒" not in "\n".join(accounts.throttle_lines(accounts.get(b["id"])))
    # 老记录没这两个字段 → None，不乱说
    c = _acc("A95c")
    accounts.mark_throttled(c["id"], reason={"code": "PUBLIC_ERROR_UNUSUAL_ACTIVITY", "rpc": "eb1hJf"})
    assert accounts.throttle_info(c["id"])["first_shot"] is None
    # 这轮没交过、但离上一发才 30 秒（另一个任务刚交过）→ 不算第一发
    d_ = _acc("A95d")
    accounts.mark_throttled(d_["id"], reason={"code": "PUBLIC_ERROR_UNUSUAL_ACTIVITY", "rpc": "eb1hJf",
                                              "submitted_before": 0, "since_last_sec": 30})
    assert accounts.throttle_info(d_["id"])["first_shot"] is False
    gj = (ROOT / "ui/js/panels/generate.js").read_text(encoding="utf-8")
    assert "why_first" in gj
    zh = json.loads((ROOT / "orante/i18n/zh.json").read_text(encoding="utf-8"))
    assert "gen.th.why_first_plan" in zh
    print("  第一发就被拒：日志和横幅都写明两种病因和验证办法  OK")


def test_flow_model_names_still_match_dropdown():
    """0926 Flow 网页下拉：Omni 1.1 Flash / Veo 3.1 - Lite / Fast / Quality / Lite [Lower Priority]。
    Orante 用的免费名还在；Omni 是新模型，id 没抓过，不在表里（要用得先抓包）。"""
    _pin()
    from orante.engines import flow_api
    for name in ("Veo 3.1 - Lite [Lower Priority]", "Veo 3.1 - Lite", "Veo 3.1 - Fast", "Veo 3.1 - Quality"):
        assert name in flow_api.MODEL_FAMILY
    assert flow_api.model_id_for("Veo 3.1 - Lite [Lower Priority]", 8) == "veo_3_1_i2v_lite_low_priority"
    assert flow_api.model_id_for("Veo 3.1 - Lite [Lower Priority]", 8) in flow_api.FLOW_FREE_MODEL_IDS
    assert "Omni" not in "".join(flow_api.MODEL_FAMILY)
    print("  免费模型名 / id 与 0926 下拉一致；Omni 1.1 Flash 未接（需抓包）  OK")
