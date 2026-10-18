# -*- coding: utf-8 -*-
"""规格 35：舞台坐标系机位库、关掉正脸、现场硬校验、不合格重生。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec35-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import config, db  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import aipool, coverage, expand, prompts  # noqa: E402

db.init()
load_all_services()
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def _wipe_prompts():
    for k in (
        "prompts.expand", "prompts.refill", "prompts.refill_clause",
        "prompts.refill_merged", "prompts.migrate_notice",
        "prompts.format_version", "prompts.cov_v2_migrated",
        "prompts.expand.bak.v1lib",
    ):
        try:
            config.reset(k)
        except Exception:
            pass


class FakeJob:
    def __init__(self, task_id="T35"):
        self.task_id = task_id
        self.lines = []
        self.id = "J35"

    def log(self, msg: str) -> None:
        print(f"  {msg}")
        self.lines.append(msg)

    def check(self) -> None:
        pass

    def set_progress(self, **kw) -> None:
        pass


class StageGemini:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def ask_image(self, prompt, image_path, json_mode=False, **kw):
        self.calls.append(str(prompt))
        if self.answers:
            ans = self.answers.pop(0)
        else:
            ans = {"env": True, "facing": "听众"}
        if isinstance(ans, str):
            return ans
        return json.dumps(ans, ensure_ascii=False)


class SeqEngine:
    def __init__(self, files):
        self.files = list(files)
        self.n = 0

    def gen_images(self, prompt, ref=None, n=1, dest_dir=None, **kw):
        dest = Path(dest_dir or tempfile.mkdtemp())
        dest.mkdir(parents=True, exist_ok=True)
        if not self.files:
            return []
        src = Path(self.files.pop(0))
        self.n += 1
        out = dest / f"gen-{self.n:04d}{src.suffix or '.jpg'}"
        out.write_bytes(src.read_bytes())
        return [out]


def _jpeg(path: Path, color=(40, 80, 60), size=(320, 560)) -> Path:
    from PIL import Image as PILImage
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", size, color).save(path, "JPEG", quality=80)
    return path


def test_v2_library():
    print("== v2 库：frontal / rank / 74+62 / 中文句子 ==")
    lib = coverage.default_lib()
    cams = {c["id"]: c for c in lib["cameras"]}
    if set(c["id"] for c in lib["cameras"]) != {f"{i:02d}" for i in range(1, 13)}:
        bad("机位 id 不是 01–12")
        return
    frontal = [c["id"] for c in lib["cameras"] if c.get("frontal")]
    if frontal != ["01", "02"]:
        bad(f"frontal 机位 {frontal}，应为 01 02")
        return
    if cams["03"].get("frontal") or cams["06"].get("frontal"):
        bad("03/06 环境镜头不该标 frontal")
        return
    ranks = {}
    for c in lib["cameras"]:
        ranks.setdefault(c["rank"], 0)
        ranks[c["rank"]] += 1
    if ranks != {1: 2, 2: 2, 3: 5, 4: 3}:
        bad(f"rank 分布 {ranks}，应为 1:2 2:2 3:5 4:3")
        return
    head = lib.get("head") or ""
    if "从不看镜头" not in head or "绝不转身背对听众" not in head:
        bad("head 没锁面向听众")
        return
    if any("看向镜头" in (a.get("text") or "") for a in lib["actions"]):
        bad("动作库还在写看向镜头")
        return
    if "恳切" not in (next(a for a in lib["actions"] if a["id"] == "08").get("text") or ""):
        bad("动作 08 不是恳切")
        return
    n = coverage.legal_count(lib)
    n2 = coverage.legal_count(lib, no_frontal=True)
    if n != 74 or n2 != 62:
        bad(f"合法 {n}/{n2}，应为 74/62")
        return
    ok(f"frontal=01,02；合法 {n} / 关掉正脸 {n2}")


def test_no_frontal_pick():
    print("== 关掉正脸：挑 12 条不含 01/02；关掉开关则出现 ==")
    lib = coverage.default_lib()
    on = coverage.pick(lib, 12, no_frontal=True)
    cams_on = [p["cov"]["camera"] for p in on]
    if "01" in cams_on or "02" in cams_on:
        bad(f"关掉正脸还出了正面机位：{cams_on}")
        return
    if len(set(cams_on)) < 8:
        bad(f"机位覆盖太窄：{cams_on}")
        return
    if any(p["cov"].get("frontal") for p in on):
        bad("cov.frontal 还是 true")
        return
    off = coverage.pick(lib, 12, no_frontal=False)
    cams_off = [p["cov"]["camera"] for p in off]
    if "01" not in cams_off and "02" not in cams_off:
        bad(f"打开正脸后 01/02 都没出现：{cams_off}")
        return
    keys = [p["key"] for p in on]
    if len(set(keys)) != len(keys):
        bad(f"同轮二元组重复：{keys}")
        return
    ok(f"开：{cams_on}；关：{cams_off}")


def test_no_frontal_default():
    print("== expand.no_frontal 默认开 ==")
    if config.DEFAULTS.get("expand.no_frontal") is not True:
        bad(f"默认 {config.DEFAULTS.get('expand.no_frontal')}")
        return
    if expand._no_frontal() is not True:
        bad("_no_frontal() 不是 True")
        return
    config.set("expand.no_frontal", False)
    if expand._no_frontal() is not False:
        bad("设 False 之后还是 True")
        config.reset("expand.no_frontal")
        return
    config.reset("expand.no_frontal")
    ok("默认 True，可关掉")


def test_parse_stage():
    print("== 现场校验 JSON 解析 + 判据 ==")
    v = expand.parse_stage('{"env":true,"facing":"听众"}')
    if v != {"env": True, "facing": "听众"}:
        bad(f"标准 JSON 解析坏了：{v}")
        return
    v = expand.parse_stage('```json\n{"env": false, "facing": "镜头"}\n```')
    if v["env"] is not False or v["facing"] != "镜头":
        bad(f"围栏 JSON 解析坏了：{v}")
        return
    v = expand.parse_stage('{"env":"是","facing":"面向听众讲话"}')
    if not v["env"] or v["facing"] != "听众":
        bad(f"中文值没收下：{v}")
        return
    cov_side = {"camera": "07", "camera_name": "舞台左侧·正侧面中景", "frontal": False}
    cov_front = {"camera": "01", "camera_name": "观众席正中·中景", "frontal": True}
    cov_hand = {"camera": "11", "camera_name": "手部特写", "frontal": False}
    if expand.stage_fail_reason({"env": False, "facing": "听众"}, cov_side) != "看不到听众席":
        bad("env=false 没判看不到听众席")
        return
    if expand.stage_fail_reason({"env": True, "facing": "镜头"}, cov_side) != "直视镜头":
        bad("facing=镜头 且 frontal=false 没判")
        return
    if expand.stage_fail_reason({"env": True, "facing": "镜头"}, cov_front) is not None:
        bad("frontal=true 看镜头不该判不合格")
        return
    if expand.stage_fail_reason({"env": False, "facing": "听众"}, cov_hand) is not None:
        bad("手部特写不该查听众席")
        return
    if not expand.skip_env_check(cov_hand) or expand.skip_env_check(cov_side):
        bad("skip_env_check 机位判断不对")
        return
    ok("解析和判据对")


def test_sanitize_frontal():
    print("== sanitize 读 frontal，旧 front 也能认 ==")
    lib = coverage.sanitize({
        "head": "h", "tail": "t",
        "cameras": [{"id": "01", "name": "a", "class": "A", "front": True, "rank": 3, "text": "x"}],
        "actions": [{"id": "01", "name": "b", "phase": "hold", "rank": 3, "text": "y"}],
    })
    if lib["cameras"][0].get("frontal") is not True:
        bad("旧 front 没映射成 frontal")
        return
    if "front" in lib["cameras"][0]:
        bad("sanitize 还留着 front")
        return
    ok("front → frontal")


def test_v1_migrate():
    print("== 用户存的 v1 库自动换成 v2 ==")
    _wipe_prompts()
    v1 = {
        "head": "保持上传图像中的人物长相不变",
        "tail": "。",
        "cameras": [{"id": "01", "name": "正面", "class": "A", "front": True, "rank": 3, "text": "人物正面朝向镜头"}],
        "actions": [{"id": "01", "name": "看镜头", "phase": "hold", "rank": 1, "text": "眼神看向镜头"}],
    }
    config.set("prompts.expand", v1)
    config.set("prompts.format_version", 6)
    got = prompts.get_block("expand")
    if "从不看镜头" not in str(got.get("head") or ""):
        bad("没换成 v2 head")
        return
    bak = config.get("prompts.expand.bak.v1lib")
    if not isinstance(bak, dict) or bak.get("head") != v1["head"]:
        bad(f"v1 没备份：{bak!r}")
        return
    notices = prompts.migrate_notices()
    if not any(n.get("bak_key") == "prompts.expand.bak.v1lib" for n in notices):
        bad(f"没有迁移横幅：{notices!r}")
        return
    _wipe_prompts()
    ok("v1 → bak.v1lib，expand 换成 v2")


def test_cheap_rank():
    print("== 现场校验走便宜模型 ==")
    if aipool._cheap_rank({"model": "gemini-3.5-flash-lite"}) >= aipool._cheap_rank({"model": "gemini-3-pro"}):
        bad("lite 没有排在 pro 前面")
        return
    if aipool._cheap_rank({"model": "gpt-4o-mini"}) >= aipool._cheap_rank({"model": "gpt-4o"}):
        bad("mini 没有排在前面")
        return
    ok("lite/mini 比 pro 便宜")


def test_reject_regen():
    print("== 看不到听众席 → 挪进 _不合格/ 并换组合重生 ==")
    lib = coverage.default_lib()
    picks = coverage.pick(lib, 3, no_frontal=True)
    if len(picks) < 2:
        bad("挑不出组合")
        return
    d = Path(tempfile.mkdtemp(prefix="orante-t35-"))
    (d / "images").mkdir()
    ref = _jpeg(d / "images" / "p01.jpg", (20, 30, 40))
    a = _jpeg(d / "plain-a.jpg", (200, 10, 10))
    b = _jpeg(d / "plain-b.jpg", (10, 200, 10))
    m = Manifest.new("T35", "t", "flow", "A001", "long", "hello")
    m.images = [{"p": "p01", "file": "images/p01.jpg", "role": "主体", "status": "ok"}]
    job = FakeJob()
    gem = StageGemini([
        {"env": False, "facing": "听众"},
        {"env": True, "facing": "听众"},
    ])
    eng = SeqEngine([a, b])
    pool = []
    avoid = set()
    fp, last, dup_warn, stage_warn = expand._gen_one_cov(
        job, eng, ref, d / "images" / "_候选", picks[0], pool, m, d,
        False, avoid, gemini=gem)
    if fp is None:
        bad("重生之后没出图")
        return
    rejected = list((d / expand.REJECT_DIR).glob("*")) if (d / expand.REJECT_DIR).is_dir() else []
    if not rejected:
        bad("_不合格/ 是空的，不合格图被删了或没挪")
        return
    logs = "\n".join(job.lines)
    if "看不到听众席" not in logs or "重生" not in logs:
        bad(f"日志不对：{logs}")
        return
    if "换 " not in logs or "×" not in logs:
        bad(f"没写换组合：{logs}")
        return
    if last["key"] == picks[0]["key"]:
        bad("还是原来那个组合")
        return
    if dup_warn or stage_warn:
        bad("合格图被标黄了")
        return
    if len(gem.calls) < 1:
        bad("没问多模态")
        return
    if "env" not in gem.calls[0] or "facing" not in gem.calls[0]:
        bad("一次调用没把两个问题合在一起")
        return
    ok(f"不合格进 {rejected[0].name}，换 {last['cov']['camera']}×{last['cov']['action']}")


def test_facing_camera():
    print("== 非正脸机位直视镜头 → 不合格重生 ==")
    lib = coverage.default_lib()
    side = next(p for p in coverage.pick(lib, 12, no_frontal=True)
                if not p["cov"].get("frontal") and p["cov"]["camera"] != "11")
    d = Path(tempfile.mkdtemp(prefix="orante-t35f-"))
    (d / "images").mkdir()
    ref = _jpeg(d / "images" / "p01.jpg", (20, 30, 40))
    a = _jpeg(d / "a.jpg", (180, 20, 20))
    b = _jpeg(d / "b.jpg", (20, 180, 20))
    m = Manifest.new("T35f", "t", "flow", "A001", "long", "hello")
    m.images = [{"p": "p01", "file": "images/p01.jpg", "role": "主体", "status": "ok"}]
    job = FakeJob()
    gem = StageGemini([
        {"env": True, "facing": "镜头"},
        {"env": True, "facing": "听众"},
    ])
    eng = SeqEngine([a, b])
    fp, last, dup_warn, stage_warn = expand._gen_one_cov(
        job, eng, ref, d / "images" / "_候选", side, [], m, d,
        False, set(), gemini=gem)
    if fp is None:
        bad("没出图")
        return
    logs = "\n".join(job.lines)
    if "直视镜头" not in logs:
        bad(f"没写直视镜头：{logs}")
        return
    rejected = list((d / expand.REJECT_DIR).glob("*")) if (d / expand.REJECT_DIR).is_dir() else []
    if not rejected:
        bad("直视镜头的图没挪进 _不合格/")
        return
    ok("直视镜头 → 重生")


def test_hand_skip_env():
    print("== 手部特写看不到听众席也算合格 ==")
    lib = coverage.default_lib()
    hand = next(p for p in coverage.pick(lib, 20, no_frontal=False)
                if p["cov"]["camera"] == "11")
    d = Path(tempfile.mkdtemp(prefix="orante-t35h-"))
    (d / "images").mkdir()
    ref = _jpeg(d / "images" / "p01.jpg", (20, 30, 40))
    a = _jpeg(d / "a.jpg", (90, 90, 20))
    m = Manifest.new("T35h", "t", "flow", "A001", "long", "hello")
    m.images = [{"p": "p01", "file": "images/p01.jpg", "role": "主体", "status": "ok"}]
    job = FakeJob()
    gem = StageGemini([{"env": False, "facing": "听众"}])
    eng = SeqEngine([a])
    fp, last, dup_warn, stage_warn = expand._gen_one_cov(
        job, eng, ref, d / "images" / "_候选", hand, [], m, d,
        False, set(), gemini=gem)
    if fp is None:
        bad("手部特写被拒了")
        return
    if (d / expand.REJECT_DIR).is_dir() and list((d / expand.REJECT_DIR).glob("*")):
        bad("手部特写不该进 _不合格/")
        return
    if "看不到听众席" in "\n".join(job.lines):
        bad("手部特写不该查听众席")
        return
    ok("11 手部特写跳过 env")


def test_round_robin_still():
    print("== 32 号均匀轮转还在 ==")
    lib = coverage.default_lib()
    picks = coverage.pick(lib, 10, no_frontal=True)
    keys = [p["key"] for p in picks]
    if len(set(keys)) != 10:
        bad(f"二元组重复：{keys}")
        return
    more = coverage.pick(lib, 10, no_frontal=True,
                         used_across=set(keys), avoid=set(keys))
    overlap = set(p["key"] for p in more) & set(keys)
    if overlap:
        bad(f"跨轮次还拿了用过的：{overlap}")
        return
    meta = picks[0]["cov"]
    for k in ("camera", "camera_name", "class", "frontal", "action", "action_name", "phase"):
        if k not in meta:
            bad(f"cov 缺 {k}")
            return
    ok("轮转 / 不重复 / cov 打标还在")


def test_ui_strings():
    print("== 界面文案和开关字段 ==")
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in ("gemini.cov.no_frontal", "gemini.cov.no_frontal_hint",
              "gemini.cov.frontal", "gemini.cov.stage_fail"):
        if k not in zh:
            bad(f"缺文案 {k}")
            return
    js = (ROOT / "ui" / "js" / "panels" / "gemini.js").read_text(encoding="utf-8")
    if "row.frontal" not in js or "gemini.cov.no_frontal" not in js:
        bad("gemini.js 没接 frontal / 关掉正脸")
        return
    if "expand.no_frontal" not in js:
        bad("开关没写 settings")
        return
    ok("文案和开关都在")


def main() -> int:
    print(f"ORANTE_DATA = {TEST_DATA}")
    test_v2_library()
    test_no_frontal_pick()
    test_no_frontal_default()
    test_parse_stage()
    test_sanitize_frontal()
    test_v1_migrate()
    test_cheap_rank()
    test_reject_regen()
    test_facing_camera()
    test_hand_skip_env()
    test_round_robin_still()
    test_ui_strings()
    print()
    if fail:
        print(f"失败 {fail} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
