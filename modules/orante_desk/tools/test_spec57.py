# -*- coding: utf-8 -*-
"""规格 57：Flow 额度审计。先设 ORANTE_DATA，全部用 FakeRPC，不碰真账号。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-57-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from orante import config, db, paths  # noqa: E402
from orante.engines import flow_api  # noqa: E402
from orante.errors import CreditHalted, OranteError  # noqa: E402
from orante.scheduler import Scheduler  # noqa: E402

db.init()
paths.ensure_dirs()

ACC = {
    "id": "A057", "name": "t-flow-57", "engine": "flow", "port": 9457,
    "project_url": (
        "https://flow.google.com/project/"
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/collection/"
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ),
    "model": "Veo 3.1 - Lite [Lower Priority]", "aspect": "9:16",
    "max_concurrent": 5,
}


class FakeJob:
    def __init__(self):
        self.id = "J00057"
        self.task_id = "T0057"
        self.kind = "generate"
        self.status = "running"
        self.lines = []
        self.credit_continue = False
        self.credit_halt = None
        self.credit_b0 = None
        self.error = None
        self.cancelled = threading.Event()

    def log(self, msg: str) -> None:
        self.lines.append(msg)

    def check(self) -> None:
        if self.cancelled.is_set():
            from orante.scheduler import JobCancelled
            raise JobCancelled()

    def sleep(self, sec: float) -> None:
        if self.credit_continue:
            return
        # 测试里不真等：第一次 sleep 就当用户点了继续，避免死循环
        self.credit_continue = True

    def to_dict(self) -> dict:
        return {
            "id": self.id, "task_id": self.task_id, "kind": self.kind,
            "status": self.status, "error": self.error,
            "credit_halt": self.credit_halt, "prio": 0,
        }


def _jpeg() -> Path:
    p = TEST_DATA / "ref.jpg"
    if not p.is_file():
        p.write_bytes(
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01"
            b"\x00\x00\xff\xd9"
        )
    return p


def _ogi_payload():
    rec = [None] * 7
    rec[0] = "mediaid" + "x" * 29
    rec[6] = [[None] * 14]
    rec[6][0][13] = "https://flow-content.google/image/x"
    return [[rec]]


def _eb_payload(slot1=55, gid="genid1234567890abcdef"):
    return [None, slot1, None, [[gid]]]


class FakeRPC:
    def __init__(self, nzlxg=None):
        self.nzlxg_q = list(nzlxg if nzlxg is not None else [[[8611]]])
        self.calls = []
        self.eb = _eb_payload()
        self.ogi = _ogi_payload()

    def __call__(self, rpc, inner, retries=2, raise_=True):
        if callable(inner) and not isinstance(inner, (list, dict, tuple)):
            inner = inner()
        self.calls.append(rpc)
        if rpc == "nzlxg":
            if self.nzlxg_q:
                return self.nzlxg_q.pop(0)
            return [[0]]
        if rpc == "eb1hJf":
            return self.eb
        if rpc == "ogiZ0b":
            return self.ogi
        return None


def _eng(logs=None, rpc=None):
    lines = logs if logs is not None else []
    eng = flow_api.FlowEngine(ACC, lines.append)
    eng._rpc = rpc or FakeRPC()
    eng._recaptcha = lambda action: "token"
    eng.upload_image = lambda p: "M" * 36
    eng._ensure_page = lambda: None
    return eng, lines


@pytest.fixture(autouse=True)
def _reset_cfg():
    for k in ("flow.allow_paid", "flow.credit_guard", "flow.image_model_key"):
        config.reset(k)
    # 规格 96 定稿后 submit() 默认每段都走网页真人操作；这个文件测的是接口直连
    # 那条路上的余额守卫 / 付费模型闸，所以显式关掉真人操作（= 设置里关掉那个开关）。
    config.set("flow.human_warmup", False)
    yield
    for k in ("flow.allow_paid", "flow.credit_guard", "flow.image_model_key", "flow.human_warmup"):
        config.reset(k)


def test_credits_parses_nested():
    eng, _ = _eng(rpc=FakeRPC(nzlxg=[[[8611, "x"]]]))
    assert eng.credits() == 8611


def test_credits_garbage_logs_once():
    lines = []
    eng, _ = _eng(logs=lines, rpc=FakeRPC())
    eng._rpc = lambda *a, **k: {"foo": "bar"}
    assert eng.credits() is None
    assert sum(1 for x in lines if "解析不出" in x) == 1
    assert eng.credits() is None
    assert sum(1 for x in lines if "解析不出" in x) == 1


def test_credits_error_does_not_raise():
    lines = []
    eng, _ = _eng(logs=lines)

    def boom(*a, **k):
        raise OranteError("nzlxg 挂了", "x")

    eng._rpc = boom
    assert eng.credits() is None
    assert any("查余额失败" in x for x in lines)


def test_paid_video_blocked():
    eng, _ = _eng()
    eng.model_name = "Veo 3.1 - Fast"
    with pytest.raises(OranteError) as ei:
        eng.submit({"s": 1}, _jpeg(), "hi", 8)
    assert "这个模型会扣额度" in ei.value.message


def test_paid_video_allowed_when_flag():
    config.set("flow.allow_paid", True)
    rpc = FakeRPC(nzlxg=[[[100]]])
    eng, lines = _eng(rpc=rpc)
    eng.model_name = "Veo 3.1 - Fast"
    gid = eng.submit({"s": 1}, _jpeg(), "hi", 8)
    assert gid
    assert any("剩余额度" in x for x in lines)


def test_paid_image_blocked():
    config.set("flow.image_model_key", "IMAGEN_3")
    eng, _ = _eng()
    with pytest.raises(OranteError) as ei:
        eng.gen_image_once("hi", "R" * 36)
    assert "这个模型会扣额度" in ei.value.message


def test_credit_guard_halts_submit():
    rpc = FakeRPC(nzlxg=[[[100]], [[90]]])
    eng, _ = _eng(rpc=rpc)
    job = FakeJob()
    config.set("flow.credit_guard", True)
    eng.begin_credits(job)
    eng._credits_at = 0
    with pytest.raises(CreditHalted) as ei:
        eng.submit({"s": 1}, _jpeg(), "hi", 8)
    assert ei.value.b0 == 100
    assert ei.value.b == 90
    assert "已停止提交" in ei.value.message


def test_credit_guard_continue_skips():
    rpc = FakeRPC(nzlxg=[[[100]], [[90]]])
    eng, _ = _eng(rpc=rpc)
    job = FakeJob()
    job.credit_continue = True
    eng.begin_credits(job)
    eng._credits_at = 0
    gid = eng.submit({"s": 1}, _jpeg(), "hi", 8)
    assert gid


def test_job_status_halted_not_failed():
    sch = Scheduler()

    def fn(job):
        raise CreditHalted(100, 90)

    job = sch.submit("credit-halt-test", fn)
    deadline = time.time() + 8
    while job.status in ("queued", "running") and time.time() < deadline:
        time.sleep(0.05)
    assert job.status == "halted", job.status
    assert job.status != "failed"
    assert job.error and "57 号报告" in job.error


def test_image_count_assert_one():
    flow_api.assert_image_once_count([None, [[]], 1, None, []])
    with pytest.raises(AssertionError):
        flow_api.assert_image_once_count([None, [[]], 2, None, []])


def test_gen_image_once_count_is_1():
    captured = []
    rpc = FakeRPC(nzlxg=[[[100]]])

    def wrap(rpc_name, inner, retries=2, raise_=True):
        if callable(inner) and not isinstance(inner, (list, dict, tuple)):
            inner = inner()
        if rpc_name == "ogiZ0b":
            captured.append(inner)
            return rpc.ogi
        return rpc(rpc_name, inner, retries=retries, raise_=raise_)

    eng, _ = _eng()
    eng._rpc = wrap
    eng.gen_image_once("hi", "R" * 36)
    assert captured, "没截到 inner"
    assert captured[0][2] == 1


def test_submit_log_says_credits_after_probe():
    """B 节定性：eb1hJf[1] 与 nzlxg 相同，日志改回「剩余额度」。"""
    rpc = FakeRPC(nzlxg=[[[100]]])
    eng, lines = _eng(rpc=rpc)
    eng.submit({"s": 1}, _jpeg(), "hi", 8)
    blob = "\n".join(lines)
    assert "剩余额度" in blob
    assert "响应[1]=" not in blob


def test_begin_end_logs_balance():
    rpc = FakeRPC(nzlxg=[[[100]], [[100]]])
    eng, lines = _eng(rpc=rpc)
    job = FakeJob()
    eng.begin_credits(job)
    eng.end_credits()
    n = sum(1 for x in lines if "账户余额" in x and "nzlxg" in x)
    assert n >= 2, lines


def test_account_model_reject():
    from orante import accounts
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A057",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A057", "t57", "flow", 9457, "A057", ACC["project_url"],
             "Veo 3.1 - Lite [Lower Priority]", "9:16", 5, 1, "{}", db.now()),
        )
    with pytest.raises(OranteError) as ei:
        accounts.update("A057", model="Veo 3.1 - Fast")
    assert "扣额度" in ei.value.message
    config.set("flow.allow_paid", True)
    a = accounts.update("A057", model="Veo 3.1 - Fast")
    assert a["model"] == "Veo 3.1 - Fast"


def test_model_id_for_fast():
    assert flow_api.model_id_for("Veo 3.1 - Fast", 8) == "veo_3_1_i2v_fast"
    assert "veo_3_1_i2v_fast" not in flow_api.FLOW_FREE_MODEL_IDS


def test_guard_off_only_logs():
    config.set("flow.credit_guard", False)
    rpc = FakeRPC(nzlxg=[[[100]], [[90]]])
    eng, lines = _eng(rpc=rpc)
    job = FakeJob()
    eng.begin_credits(job)
    eng._credits_at = 0
    gid = eng.submit({"s": 1}, _jpeg(), "hi", 8)
    assert gid
    assert any("只记录不拦" in x for x in lines)
