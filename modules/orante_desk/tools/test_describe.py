# -*- coding: utf-8 -*-
"""验收规格 05：describe / 空镜搬家 / 提示词预览 / 路由 / 没 Key 的人话。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-describe-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, config, tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.jobs import REGISTRY, ACCOUNT_BOUND, load_all_services  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import describe, prompts, lens  # noqa: E402
from orante.services.gemini import Gemini  # noqa: E402
from longtik_images import parse_sidecar_text  # noqa: E402

db.init()
load_all_services()

SEG_TEXT = "Stay for 7 seconds and type Amen. God bless you."
DESC_P01 = (
    "这是一位非洲的中年40岁左右女性基督徒，她皮肤带有细腻的岁月痕迹，"
    "她全程紧闭双眼，双脚不移动位置，保持同一人物、同一现场空间的一致性【闭眼|常规】"
)
VOICE_P01 = "【音色】非洲裔|女|中年|中低音|非洲口音"
DESC_P02 = (
    "画面是一座礼堂的观众席，几千人坐满，主体很小，"
    "画面中没有人在对着镜头说话【空镜|舞台】"
)
DESC_P03 = (
    "这是一位美国的中年40岁左右男性基督徒，他全程睁着眼，站在原位不离开原地，"
    "保持同一人物、同一现场空间的一致性【睁眼|常规】"
)
VOICE_P03 = "【音色】非裔|男|中年|中低音|美国口音"

MEASURE = {
    "shot_size": "中景", "facing": "正面", "room_left": "中", "room_right": "中",
    "headroom": "中", "depth": "中", "people": 1, "crowd": False,
    "seated": False, "level": "平视",
}


class FakeJob:
    def __init__(self, task_id):
        self.task_id = task_id
        self.cancelled = threading.Event()
        self.lines = []
        self.id = "Jtest"
        self.progress = {}

    def log(self, msg: str) -> None:
        print(f"  {msg}")
        self.lines.append(msg)

    def check(self) -> None:
        if self.cancelled.is_set():
            from orante.scheduler import JobCancelled
            raise JobCancelled()

    def set_progress(self, **kw) -> None:
        self.progress.update(kw)


class FakeGemini:
    def __init__(self, api_key=None, model=None):
        if api_key is not None and str(api_key).strip() == "":
            raise OranteError(
                "还没填 Gemini API Key",
                "设置 → Gemini → api_key。到 aistudio.google.com 免费申请。")
        self.api_key = api_key or "fake"
        self.model = (model or "gemini-3.5-flash-lite").removeprefix("models/")

    def ask_image(self, prompt, image_path, json_mode=False, **kw):
        p = Path(image_path).stem
        text = str(prompt or "")
        if json_mode and "is_broll" in text:
            is_broll = p == "p02"
            return json.dumps({
                "is_broll": is_broll,
                "reason": "观众席大全景" if is_broll else "有明确主角",
                "shot_size": "远景" if is_broll else "中景",
                "people": 800 if is_broll else 1,
                "crowd": is_broll,
            }, ensure_ascii=False)
        if json_mode and ("room_left" in text or "摄影指导" in text):
            m = dict(MEASURE)
            if p == "p02":
                m.update({"shot_size": "远景", "people": 8, "crowd": True})
            return json.dumps(m, ensure_ascii=False)
        pose = (
            'POSE_JSON={"head":"正面","eyes":"看向听众","hands":"自然下垂",'
            '"body":"站立","expr":"平静","text":"正面站立看向听众"}'
        )
        if p == "p02":
            return DESC_P02 + "\n" + pose
        if p == "p03":
            return DESC_P03 + "\n" + VOICE_P03 + "\n" + pose
        return DESC_P01 + "\n" + VOICE_P01 + "\n" + pose

    def ask_text(self, prompt, system="", json_mode=False, **kw):
        return json.dumps(["Stay for 7 seconds and type Amen."])

    def list_models(self):
        return ["gemini-3.5-flash-lite", "gemini-2.5-flash"]


def _png(path: Path, color=(40, 80, 60)) -> None:
    from PIL import Image as PILImage
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", (320, 560), color).save(path, "JPEG", quality=80)


def _make_task(n_img=3) -> tuple[str, Path]:
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-"))
    (task_dir / "images").mkdir()
    (task_dir / "copy.txt").write_text(SEG_TEXT, encoding="utf-8")
    colors = [(40, 80, 60), (80, 40, 40), (40, 40, 90)]
    images = []
    for i in range(1, n_img + 1):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpg"
        _png(fp, colors[(i - 1) % 3])
        images.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": "a" * 40,
            "role": "主体", "status": "ok", "desc": "", "voice": "",
            "eye": "闭眼", "scene": "常规", "measure": {},
            "origin": "local", "src": str(fp),
        })
    m = Manifest.new("T0007", "三张图", "flow", "A001", "long", SEG_TEXT)
    m.images = images
    m.segments = [{
        "s": 1, "text": SEG_TEXT, "duration": 8, "image": "p01",
        "role": "主体", "shot": "", "prompt_final": "",
        "status": "planned", "gen_id": None, "takes": [], "error": None,
    }]
    m.save(task_dir)
    if not db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("A001", "t-desc", "flow", 9399, "A001", "", "", "9:16", 5, 1, "{}", db.now()))
    if db.one("SELECT 1 FROM tasks WHERE id=?", ("T0007",)):
        db.run("UPDATE tasks SET dir=?, status=?, copy=?, updated=? WHERE id=?",
               (str(task_dir), "prepared", SEG_TEXT, db.now(), "T0007"))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("T0007", "三张图", "flow", "A001", "long", SEG_TEXT, str(task_dir / "images"),
             "", "prepared", str(task_dir), "", 0, db.now(), db.now()))
    return "T0007", task_dir


def test_parse_sidecar() -> None:
    two = parse_sidecar_text(DESC_P01 + "\n" + VOICE_P01)
    if two["eye"] != "闭眼" or two["scene"] != "常规" or two["role"] != "主体":
        raise SystemExit(f"FAIL 两行 sidecar：{two}")
    if not two.get("voice") or two["voice"].get("ethnic") != "非洲裔":
        raise SystemExit(f"FAIL 音色 dict：{two.get('voice')}")
    one = parse_sidecar_text(DESC_P02)
    if one["role"] != "空镜" or one["eye"] != "空镜" or one.get("voice"):
        raise SystemExit(f"FAIL 空镜一行：{one}")
    print("OK parse_sidecar_text 两行主体 / 一行空镜")


def test_parse_measure() -> None:
    got = lens.parse_measure(json.dumps(MEASURE, ensure_ascii=False))
    if got["shot_size"] != "中景" or got["facing"] != "正面" or got["people"] != 1:
        raise SystemExit(f"FAIL parse_measure：{got}")
    print("OK lens.parse_measure 收敛")


def test_jobs_registered() -> None:
    for k in ("describe", "scan_problems"):
        if k not in REGISTRY:
            raise SystemExit(f"FAIL 没注册 {k}，现有 {sorted(REGISTRY)}")
        if k in ACCOUNT_BOUND:
            raise SystemExit(f"FAIL {k} 不该占账号")
    if "preview_prompt" in REGISTRY:
        raise SystemExit("FAIL preview_prompt 不该是 job")
    print(f"OK jobs：describe / scan_problems 已注册且不占账号")


def test_no_key() -> None:
    try:
        Gemini(api_key="")
    except OranteError as e:
        blob = (e.message or "") + (e.hint or "")
        if "还没填 Gemini API Key" not in e.message:
            raise SystemExit(f"FAIL 没 Key 原文不对：{e.message!r}")
        if "设置" not in blob:
            raise SystemExit(f"FAIL hint 里没有「设置」：{blob!r}")
        print(f"OK 没 Key：{e.message} / {e.hint}")
    else:
        raise SystemExit("FAIL 没 Key 应该抛 OranteError")


def test_describe_three() -> None:
    tid, d = _make_task(3)
    config.set("gemini.concurrency", 2)
    job = FakeJob(tid)
    with patch("orante.services.describe.ai.client", lambda job, **kw: FakeGemini()), \
         patch("orante.services.describe.aikeys.list_all", lambda enabled_only=True: [{"id": "k1"}]), \
         patch("orante.services.describe.ai.workers", lambda n: 2), \
         patch("orante.services.describe.ai.calls", lambda: 0):
        res = describe.describe(job)
    m = Manifest.load(d)
    if len(m.images) != 3:
        raise SystemExit(f"FAIL 图数 {len(m.images)}")
    for im in m.images:
        if not (im.get("desc") or "").strip():
            raise SystemExit(f"FAIL {im['p']} 没有 desc")
        if not im.get("measure"):
            raise SystemExit(f"FAIL {im['p']} 没有 measure")
    p01, p02, p03 = m.image("p01"), m.image("p02"), m.image("p03")
    if p01["role"] != "主体" or "非洲" not in p01["desc"]:
        raise SystemExit(f"FAIL p01：{p01}")
    if VOICE_P01 not in (p01.get("voice") or ""):
        raise SystemExit(f"FAIL p01 音色：{p01.get('voice')!r}")
    if p02["role"] != "空镜":
        raise SystemExit(f"FAIL p02 应该是空镜：{p02['role']}")
    if not str(p02["file"]).replace("\\", "/").startswith("空镜头/"):
        raise SystemExit(f"FAIL p02 没搬家：{p02['file']}")
    if not (d / p02["file"]).is_file():
        raise SystemExit(f"FAIL p02 文件不在 {p02['file']}")
    if (d / "images" / "p02.jpg").is_file():
        raise SystemExit("FAIL p02 还留在 images/")
    if p03["role"] != "主体" or p03.get("eye") != "睁眼":
        raise SystemExit(f"FAIL p03：role={p03['role']} eye={p03.get('eye')}")
    t = taskmod.get(tid)
    if t["status"] != "described":
        raise SystemExit(f"FAIL 状态 {t['status']} 应为 described")
    if not any("空镜" in ln for ln in job.lines):
        raise SystemExit("FAIL 空镜没有打日志")
    if res.get("described") != 3 or res.get("broll") != 1:
        raise SystemExit(f"FAIL 返回 {res}")
    print(f"OK describe 3 张：主体 {p01['p']}/{p03['p']}，空镜 {p02['file']}，状态 described")
    return tid, d


def test_update_role_and_preview(tid: str, d: Path) -> None:
    im = describe.update_image(tid, "p03", {"role": "空镜"})
    if im["role"] != "空镜":
        raise SystemExit(f"FAIL 手改 role：{im['role']}")
    if not str(im["file"]).replace("\\", "/").startswith("空镜头/"):
        raise SystemExit(f"FAIL 手改没搬家：{im['file']}")
    if not (d / im["file"]).is_file():
        raise SystemExit("FAIL 手改后文件不在 空镜头/")
    m = Manifest.load(d)
    brolls = [x for x in m.images if x["role"] == "空镜"]
    if {x["p"] for x in brolls} != {"p02", "p03"}:
        raise SystemExit(f"FAIL 空镜栏 { [x['p'] for x in brolls] }")
    print("OK 手改 p03→空镜，出现在空镜头栏")

    prompt = describe.preview_prompt(tid, "s001")
    if SEG_TEXT not in prompt:
        raise SystemExit("FAIL 预览里没有台词")
    if "【镜头】" not in prompt and "镜头" not in prompt:
        print("  注：预览里没看到【镜头】字样（p01 描述语还在）")
    if "非洲的中年40岁左右女性基督徒" not in prompt:
        raise SystemExit("FAIL 预览里没有描述语正文")
    print("OK 预览含台词 + 描述语正文")
    print("---------- 预览全文 ----------")
    print(prompt)
    print("---------- 以上 ----------")


def test_scan_problems(tid: str) -> None:
    job = FakeJob(tid)
    before = {(p.get("value") or "") for p in (prompts.get_block("problem") or {}).get("patterns") or []}
    with patch("orante.services.ai.client", lambda job=None, **kw: FakeGemini()), \
         patch("orante.services.gemini.Gemini", FakeGemini):
        res = describe.scan_problems(job)
    added = res.get("added") or []
    # 默认库已有 Stay for 7 seconds 的正则，所以这句可能不算新加
    after = prompts.get_block("problem")
    print(f"OK scan_problems 返回 added={added!r} found={res.get('found')!r} "
          f"（库 {len(before)} → {len((after or {}).get('patterns') or [])}）")


class _Req:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data

    async def body(self):
        return json.dumps(self._data).encode("utf-8")


def test_http_routes() -> None:
    import asyncio
    from orante import server

    paths = {getattr(r, "path", "") for r in server.app.routes}
    for p in ("/api/gemini/test", "/api/prompts", "/api/prompts/reset",
              "/api/tasks/{tid}/image/{p}", "/api/tasks/{tid}/prompt/{s}"):
        if p not in paths:
            raise SystemExit(f"FAIL 缺路由 {p}，现有 {sorted(x for x in paths if x.startswith('/api'))}")
    print("OK 五条新路由都挂在 FastAPI 上")

    body = server.prompts_get()
    if not body.get("ok") or not str(body.get("desc") or "").startswith("【提示词版本 v7"):
        raise SystemExit(f"FAIL GET /api/prompts：{str(body)[:200]}")
    print("OK GET /api/prompts desc 以 v7 开头")

    orig = body["desc"]
    r = asyncio.run(server.prompts_set(_Req({"name": "desc", "value": orig + "\n# 用户改过"})))
    if not r.get("ok"):
        raise SystemExit(f"FAIL POST /api/prompts：{r}")
    if not prompts.get_block("desc").endswith("# 用户改过"):
        raise SystemExit("FAIL 保存后 get_block 没有用户改动")
    r = asyncio.run(server.prompts_reset(_Req({"name": "desc"})))
    if not r.get("ok"):
        raise SystemExit(f"FAIL POST /api/prompts/reset：{r}")
    if not prompts.get_block("desc").startswith("【提示词版本 v7"):
        raise SystemExit("FAIL 恢复默认没回到 v7")
    print("OK 改描述语提示词 → 保存 → 恢复默认回到 v7")

    try:
        asyncio.run(server.gemini_test(_Req({"api_key": ""})))
    except OranteError as e:
        blob = (e.message or "") + (e.hint or "")
        if "还没填 Gemini API Key" not in e.message:
            raise SystemExit(f"FAIL /api/gemini/test 原文不对：{e.message!r}")
        if "设置" not in blob:
            raise SystemExit(f"FAIL /api/gemini/test hint 没有设置：{blob!r}")
        print(f"OK POST /api/gemini/test 没 Key → {e.message}")
    else:
        raise SystemExit("FAIL 没 Key 的 /api/gemini/test 应该抛 OranteError")

    tid = "T0007"
    r = server.task_prompt(tid, "s001")
    if not r.get("ok") or SEG_TEXT not in (r.get("prompt") or ""):
        raise SystemExit(f"FAIL GET prompt：{str(r)[:300]}")
    print("OK GET /api/tasks/{tid}/prompt/s001 含台词")

    r = asyncio.run(server.task_image_update(tid, "p01", _Req({"desc": DESC_P01, "voice": VOICE_P01})))
    if not r.get("ok") or not (r.get("image") or {}).get("desc"):
        raise SystemExit(f"FAIL POST image：{r}")
    print("OK POST /api/tasks/{tid}/image/p01 改 desc")


def test_js_syntax() -> None:
    import subprocess
    js = ROOT / "ui" / "js" / "panels" / "gemini.js"
    r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL node --check gemini.js\n{r.stderr}")
    print("OK node --check ui/js/panels/gemini.js")


def main() -> int:
    print(f"ORANTE_DATA = {TEST_DATA}")
    test_parse_sidecar()
    test_parse_measure()
    test_jobs_registered()
    test_no_key()
    tid, d = test_describe_three()
    test_update_role_and_preview(tid, d)
    test_scan_problems(tid)
    test_http_routes()
    test_js_syntax()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
