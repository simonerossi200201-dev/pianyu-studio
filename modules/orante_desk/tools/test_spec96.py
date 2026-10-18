# -*- coding: utf-8 -*-
"""规格 96 验收：Flow 人操作式提交（原「冷启动暖场」，2026-09-27 起改为常态）。

背景：用户反馈「Flow 里手动点击完全能生成，Orante 自动化提交却被
PUBLIC_ERROR_UNUSUAL_ACTIVITY 拒了」。真机日志显示账号本身有无限模型资格
（Flow1/Stefano，ULTRA 套餐，模型下拉里有 Veo 3.1 - Lite [Lower Priority]），
排除了账号资格问题；同一台机器上账号一旦"热"过，纯接口直连提交几十段
完全没事——问题出在"这个标签页从没被真人操作过就被拿去发第一个生成请求"。

修法（见 orante/engines/flow_api.py FlowEngine._submit_via_ui / submit）：
这个引擎实例（=这个标签页）真点设置、真点画幅/模型/时长、真点起止帧上传框
走真实文件选择器、真打字打提示词、真点「开始生成」——不再用 page.evaluate
直接拼 f.req 发请求。响应照样用 page.expect_response 被动收，解析规则和
原来 RPC_JS 一致（_parse_batchexecute）。

2026-09-27 二次修改：原设计是"只暖场一次，热了就切回接口直连图快"；但两次
真机验证（Flow2 账号）都在人操作式那条路上翻车（先是选择器语言不对，后是
漏了一步弹窗确认，见下面两条补丁），用户当场明确要求——接口直连既然会被
Google 判「异常」，就不该留着当"热了以后的快路"，应该每一段都走真人操作。
现在的设计：只要 `flow.human_warmup` 开着（默认开），**每一段**都先走
_submit_via_ui；这一段本身结构性失败（选择器对不上等）才落回接口直连
兜底，且不影响下一段继续尝试 UI（不再有"失败一次就永久放弃 UI"）；真的被
Flow 拒了（限流/审核/额度）原样抛出，不回退重交。`_ui_warmed`/
`_ui_warmup_failed` 现在只是诊断用的标志位，不再决定要不要走 UI。

这里验证：
1. _parse_batchexecute 对真实抓包响应体解析正确（gen_id / 剩余额度）。
2. _parse_batchexecute 对构造的限流响应体能配合 classify_flow_error 认出
   PUBLIC_ERROR_UNUSUAL_ACTIVITY。
3. submit() 的控制流：**每一段**都先走 _submit_via_ui；
   - 成功 → 直接返回，不落回接口直连。
   - 抛结构性异常（选择器没对上等）→ 落回接口直连，这一段照样能提交成功；
     下一段照旧先试 UI（不会因为这次没成就放弃）。
   - 抛 FlowThrottled / BlockedImage / CreditHalted → 原样往外抛，不落回
    （这是真答案，不是自动化没走通）。
   - 配置关掉 `flow.human_warmup` 就完全不碰 UI 路径，从第一段起就走接口
     直连。
4. _submit_via_ui 用假 Page（模拟 Playwright 的 get_by_role/get_by_text/
   get_by_placeholder/mouse/keyboard/expect_file_chooser/expect_response）
   走一遍真实点击序列：设置 → 起止帧上传（含确认按钮）→ 打字 → 点生成，
   序列里该出现的动作都出现了，且能从假响应里正确解出 gen_id；限流响应能
   正确抛 FlowThrottled。

2026-09-27 补丁一：真机第一次跑（Flow2 账号）卡在
`Locator.click: Timeout 30000ms exceeded ... waiting for
get_by_role("button", name="Settings trigger")`——排查发现 Flow 网页界面
语言由 URL 的 `hl` 参数决定（跟账号自己保存的语言设置无关，实测给一个平时
显示英文的账号手动加 `?hl=it` 整页立刻变意大利文），Luca 人在罗马，这几个
专属自动化账号的 Flow 页大概率被渲染成意大利文，导致所有英文无障碍角色
选择器全部对不上号。修法：`FlowEngine._open_own_page()` 打开自己专属标签页
时，通过新增的 `diagnose_project_page(..., hl="en")` 强制给每一次导航的
链接都加上 `?hl=en`，同时不碰账号真实的语言设置（`force_hl()` 只改这一次
`.goto()` 的 URL，不写回任何账号配置）；`probe_account_project_page`
（健康检查探测页，不点按钮）不传 `hl`，保持原样不受影响。新增：
5. force_hl() 正确加/换/保留其它查询参数和 fragment；空链接原样返回。
6. diagnose_project_page(hl="en") 真的把 hl=en 拼进每一次 pg.goto() 的
   链接；不传 hl（探测页那条路径）完全不改链接。

2026-09-27 补丁二：语言问题修好后（用户截图能看到地址栏已经是
`?hl=en`、界面也确实是英文），真机又卡在
`waiting for get_by_placeholder("What do you want to create?")`。用户截图
显示：点了起帧格子的「Upload media」、真传完文件后，Flow 并不会直接把图放
进这个格子——还停在同一个「Select a frame image」资产库弹窗里，新传的图
被自动选中、右边出预览和一个「Add to prompt」按钮，要真点一下这个按钮
弹窗才会关、图才真的被放进去。原来的 `_ui_attach_frame` 漏了这一步，弹窗
一直不关，后面找提示词输入框自然找不到、卡满 30 秒超时。修法：
`_ui_attach_frame` 在 `set_files()` 之后新增等「Add to prompt」按钮出现、
点一下再继续。新增：
7. _submit_via_ui 的完整点击序列里能看到 "Add to prompt" 这一次点击
   （起帧、尾帧各点一次）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-96-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
from orante import db as _db  # noqa: E402

import pytest  # noqa: E402

from orante import config  # noqa: E402
from orante.engines import flow_api as fa  # noqa: E402
from orante.engines.base import BlockedImage  # noqa: E402
from orante.errors import CreditHalted, FlowThrottled, OranteError  # noqa: E402


def _pin():
    """和其他 test_specNN.py 一样：防止别的测试模块中途改了 ORANTE_DATA。"""
    os.environ["ORANTE_DATA"] = str(TEST_DATA)
    _db.init()


# ---------------- 1) _parse_batchexecute 对真抓包解析正确 ----------------

CAPTURE_BODY_OK = (
    ")]}'\n\n55\n"
    '[["wrb.fr","eb1hJf","[null,55,[[\\"gid-abc\\",\\"coll\\",null,'
    '[\\"a prompt\\",[1,2],null,null,\\"media-1\\",\\"F9\\",[1,2]],\\"proj\\"]],'
    '[[\\"66a5d214-6d3c-4020-8d65-63c85152c2b4\\",\\"proj\\",\\"gid-abc\\",\\"CAE\\"]]]",null,"generic"]]\n'
)


def test_parse_batchexecute_success():
    _pin()
    out = fa._parse_batchexecute(CAPTURE_BODY_OK, "eb1hJf")
    assert out["err"] is None
    recs = fa._g(out["payload"], 3, default=[]) or []
    gen_id = fa._g(recs, 0, 0)
    assert gen_id == "66a5d214-6d3c-4020-8d65-63c85152c2b4"
    slot1 = fa._g(out["payload"], 1)
    assert slot1 == 55


def test_parse_batchexecute_real_capture_0922():
    """直接用规格 95/96 都引用的那份真抓包（0922）验证，不是凑出来的假数据。"""
    _pin()
    cap = (Path(__file__).resolve().parents[1] / "data" / "capture"
           / "flow_capture_0922-012853.jsonl")
    if not cap.is_file():
        pytest.skip("这台机器上没有 0922 抓包文件，跳过")
    import json
    text = None
    with cap.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            d = json.loads(line)
            if "rpcids=eb1hJf" in d.get("url", ""):
                text = d.get("body")
                break
    assert text, "抓包里应该有至少一条 eb1hJf 记录"
    out = fa._parse_batchexecute(text, "eb1hJf")
    assert out["err"] is None
    recs = fa._g(out["payload"], 3, default=[]) or []
    gen_id = fa._g(recs, 0, 0)
    assert isinstance(gen_id, str) and len(gen_id) > 10


# ---------------- 0b) force_hl / diagnose_project_page 强制英文渲染 ----------------

def test_force_hl_adds_param_to_bare_url():
    _pin()
    out = fa.force_hl("https://flow.google.com/project/abc", "en")
    assert out == "https://flow.google.com/project/abc?hl=en"


def test_force_hl_replaces_existing_hl_keeps_other_params():
    _pin()
    out = fa.force_hl("https://flow.google.com/project/abc?hl=it&x=1", "en")
    assert "hl=en" in out
    assert "hl=it" not in out
    assert "x=1" in out


def test_force_hl_keeps_fragment():
    _pin()
    out = fa.force_hl("https://flow.google.com/project/abc?x=1#frag", "en")
    assert "x=1" in out and "hl=en" in out
    assert out.endswith("#frag")


def test_force_hl_empty_url_passthrough():
    _pin()
    assert fa.force_hl("", "en") == ""


class _FakeNavPage:
    """给 diagnose_project_page / _open_own_page 用的假页面：记下每次 goto
    的链接，落地地址（.url）固定不 404，走最短路径。"""

    def __init__(self, landing_url):
        self.log = []
        self._landing = landing_url

    def goto(self, url, wait_until="domcontentloaded", timeout=60000):
        self.log.append(url)

    def evaluate(self, *a, **k):
        return None

    @property
    def url(self):
        return self._landing


_PID = "a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12
_PURL = f"https://flow.google.com/project/{_PID}"


def test_diagnose_project_page_forces_hl_en_on_navigation():
    _pin()
    pg = _FakeNavPage(_PURL)  # 落地地址不带 /404，一次 goto 就完事
    rec = fa.diagnose_project_page(
        pg, project_url=_PURL, project_id=_PID, collection_id=None,
        wiz_of=lambda p: {"SNlM0e": "x"}, hl="en")
    assert rec["ok"] is True
    assert pg.log == [f"{_PURL}?hl=en"]


def test_diagnose_project_page_without_hl_leaves_url_untouched():
    """健康检查探测页（probe_account_project_page）不传 hl，链接原样不改。"""
    _pin()
    pg = _FakeNavPage(_PURL)
    rec = fa.diagnose_project_page(
        pg, project_url=_PURL, project_id=_PID, collection_id=None,
        wiz_of=lambda p: {"SNlM0e": "x"})
    assert rec["ok"] is True
    assert pg.log == [_PURL], "不传 hl 就不该改链接"


def test_open_own_page_forces_hl_en(monkeypatch, tmp_path):
    """FlowEngine 自己专属标签页要求走 hl=en，探测页那条路径不受影响。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = _FakeNavPage(eng.project_url)

    class _FakeCdp:
        def send(self, *a, **k):
            pass

    class _FakeCtx:
        def new_page(self):
            return page

        def new_cdp_session(self, pg):
            return _FakeCdp()

    class _FakeBr:
        ctx = _FakeCtx()

    eng._br = _FakeBr()

    captured = {}
    orig = fa.diagnose_project_page

    def spy(*a, **k):
        captured.update(k)
        return orig(*a, **k)

    monkeypatch.setattr(fa, "diagnose_project_page", spy)

    got = eng._open_own_page()
    assert got is page
    assert captured.get("hl") == "en"
    assert page.log == [f"{eng.project_url}?hl=en"]


# ---------------- 2) 限流响应能被认出来 ----------------

def test_classify_unusual_activity_from_constructed_err():
    _pin()
    res = {"status": 200, "err": ["PUBLIC_ERROR_UNUSUAL_ACTIVITY", None, None, None, None, None, 7],
           "payload": None, "raw": ""}
    info = fa.classify_flow_error("eb1hJf", res)
    assert info["action"] == "throttle"
    assert info["code"] == "PUBLIC_ERROR_UNUSUAL_ACTIVITY"
    assert info["err_n"] == 7


# ---------------- 假 Playwright Page，供 3)/4) 用 ----------------

class _Loc:
    """假 Locator。key 形如 role:button:Start / text:xxx / css:<selector>。"""

    def __init__(self, page, key, matched=True):
        self.page = page
        self.log = page.log
        self.key = key
        self.matched = matched

    def click(self, timeout=None):
        if not self.matched:
            raise TimeoutError(f"Locator.click: Timeout 30000ms exceeded.\nCall log:\n  - waiting for {self.key}")
        self.log.append(("click", self.key))
        self.page._on_click(self.key)

    def wait_for(self, state="visible", timeout=0):
        if state == "visible" and not self.matched:
            raise TimeoutError(f"Locator.wait_for: Timeout exceeded.\nCall log:\n  - waiting for {self.key}")
        self.log.append(("wait_for", self.key, state))

    def count(self):
        return 1 if self.matched else 0

    def is_enabled(self):
        return True

    def is_visible(self):
        return self.matched

    def get_attribute(self, name):
        if "Return silent videos toggle" in self.key:
            return "true" if self.page.silent_on else "false"
        return "false"

    def inner_text(self, timeout=0):
        if "Settings trigger" in self.key:
            return self.page.trigger_text()
        if "Select model family" in self.key:
            return self.page.model_now() + " arrow_drop_down"
        if self.key.startswith("css:") and "ProseMirror" in self.key:
            return self.page.editor_text or "\n"
        return ""

    @property
    def last(self):
        return self

    @property
    def first(self):
        return self


class _Keyboard:
    def __init__(self, page):
        self.page = page
        self.log = page.log
        self._selected_all = False

    def press(self, key):
        self.log.append(("key", key))
        if key == "Control+A":
            self._selected_all = True
        elif key in ("Backspace", "Delete") and self._selected_all:
            self.page.editor_text = ""
            self._selected_all = False

    def type(self, ch, delay=0):
        self.log.append(("type", ch))
        self.page.editor_text += ch

    def insert_text(self, text):
        self.log.append(("insert_text", text))
        if self.page.drop_paste:
            return  # 模拟"粘贴没进框"
        self.page.editor_text += text


class _Mouse:
    def __init__(self, log):
        self.log = log

    def move(self, x, y, steps=1):
        self.log.append(("move",))


class _FileChooserCtx:
    def __init__(self, page):
        self.page = page
        self.log = page.log
        self.value = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def set_files(self, path):
        self.log.append(("set_files", str(path)))
        self.page.uploaded = True
        # 真实行为（09-27 实测）：新传的图排到最上面、先显示 Uploading，传完才有缩略图，
        # 而且**不会自动选中**——选中的还是上一次用过的那张
        self.page.assets.insert(0, {"name": Path(path).name, "polls": 2,
                                    "selected": False, "reject": bool(self.page.upload_reject)})
        self.page.n_upload += 1
        if self.page.upload_reject:
            self.page.snackbar = self.page.upload_reject


class _Request:
    def __init__(self, post_data):
        self.post_data = post_data


class _Response:
    def __init__(self, status, text, post_data=None):
        self.status = status
        self._text = text
        self.request = _Request(post_data)

    def text(self):
        return self._text


class _ResponseCtx:
    def __init__(self, log, response):
        self.log = log
        self.value = response

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


IMAGE_BODY_OK = (
    ")]}'\n\n100\n"
    '[["wrb.fr","ogiZ0b","[[[\\"img-media-1\\",null,\\"x\\",null,null,null,[[null,1,null,null,null,null,1,'
    '\\"p\\",25,null,null,\\"x\\",null,\\"https://example.invalid/img.jpg\\"]]]]]",null,null,null,"generic"]]\n'
)

_IMG_KEY = {"Nano Banana Pro": "GEM_PIX_2", "Nano Banana 2": "NARWHAL", "Nano Banana 2 Lite": "X_LITE"}


class FakePage:
    """够 _submit_via_ui / _gen_image_via_ui 用的假页面，按 2026-09-27 实测的 flow.google.com：
      · 提示词框是 ProseMirror div（没有 placeholder 属性）
      · 设置按钮是个开关：点一下开、再点一下关，按 Esc 关不掉
      · 起止帧空格子是 button.empty-chip（字是 Start / End），挂上图就变缩略图
      · 图片模式挂参考图走「Add ingredients to the prompt box」，同一个资产库弹窗
      · 作曲区有东西时右上角才有 ×（Clear prompt），点了整个清空
      · 交完之后上一段的图还留在作曲区（逼代码自己先点 × 清场）"""

    def __init__(self, response_status=200, response_text=CAPTURE_BODY_OK, missing_model_family=False,
                 model_shown="Veo 3.1 - Lite [Lower Priority]", composer_dirty=False,
                 drop_paste=False, missing=(), image_model="Nano Banana 2", upload_reject="",
                 add_to_prompt_works=True, assets=None, option_click_attaches=True,
                 silent_on=False, late_attach_polls=None):
        self.log = []
        # 09-28 T0122 s002：点中以后先显示「选中」，隔一会儿 Flow 才自己挂进格子、关弹窗
        self.late_attach_polls = late_attach_polls
        self._late = None
        self.keyboard = _Keyboard(self)
        self.mouse = _Mouse(self.log)
        self.viewport_size = {"width": 1280, "height": 800}
        self._status = response_status
        self._text = response_text
        self._missing_model_family = missing_model_family
        self.models = {"video": model_shown, "image": image_model}
        self.composer_dirty = composer_dirty
        self.editor_text = ""
        self.drop_paste = drop_paste
        self.missing = set(missing)     # 这些 key 的控件"找不到"
        self.gotos = []
        self.panel_open = False
        self.mode = "video"
        self.duration = "8s"
        self.aspect = "16:9"
        self.count = "x2"               # 账号上次手动用过 x2
        self.attached = set()           # 已挂上的起止帧格子
        self.ingredients = 0
        self._picking = None
        self.uploaded = False
        self.upload_reject = upload_reject
        self.add_to_prompt_works = add_to_prompt_works
        # 资产库弹窗里的列表。默认里面已经有别的任务传过的同名 p01，而且正被选中——
        # 这正是 09-27 挂错图的现场
        self.assets = list(assets) if assets is not None else [
            {"name": "p01.jpeg", "polls": 0, "selected": True, "reject": False}]
        self.attached_names = {}        # 格子 → 挂上去的是哪个文件名
        self.snackbar = ""              # 右上角"Failed to upload …"提示条
        # 09-28 真机：在弹窗里点一下已传好的那一项，Flow 直接把它放进格子、关掉弹窗
        self.option_click_attaches = option_click_attaches
        self.silent_on = silent_on      # 网页「Return silent videos」开关
        self.grid_menu = False          # 「Tile grid settings」菜单开着没有
        self.n_silent_click = 0
        self.n_upload = 0
        self.ingredient_names = []
        self.request_prompt = None      # None=按作曲区里当时的字生成请求体
        self.n_generate = 0
        self.last_rpc = None

    # ---- 状态 ----
    def model_now(self):
        m = self.models[self.mode]
        return ("🍌 " + m) if self.mode == "image" else m

    def trigger_text(self):
        if self.mode == "image":
            return f"🍌 {self.models['image']} crop_{self.aspect.replace(':', '_')} {self.count}"
        return f"Video · 720p · {self.duration} crop_{self.aspect.replace(':', '_')} {self.count}"

    def _dirty(self):
        return bool(self.composer_dirty or self.attached or self.ingredients or self.editor_text)

    def _on_click(self, key):
        if key == "role:button:Settings trigger":
            self.panel_open = not self.panel_open
        elif key == "role:button:Clear prompt":
            self.composer_dirty = False
            self.editor_text = ""
            self.attached.clear()
            self.ingredients = 0
        elif key in ("role:button:Start", "role:button:End"):
            self._picking = key.rsplit(":", 1)[1]
            self.uploaded = False
        elif key == "role:button:Add ingredients to the prompt box":
            self._picking = "ing"
            self.uploaded = False
        elif key == "role:button:Add to prompt":
            sel = [a["name"] for a in self.assets if a["selected"]]
            if self._picking and sel and self.add_to_prompt_works:
                if self._picking == "ing":
                    self.ingredients += 1
                    self.ingredient_names.append(sel[0])
                else:
                    self.attached.add(self._picking)
                    self.attached_names[self._picking] = sel[0]
                self._picking = None
        elif key == "role:button:Dismiss":
            self.snackbar = ""
        elif key.startswith("option:"):
            want = key[len("option:"):]
            for a in self.assets:
                a["selected"] = (a["name"] == want)
            if self.late_attach_polls is not None and self._picking:
                self._late = [self.late_attach_polls, self._picking, want]
            elif self.option_click_attaches and self._picking:
                if self._picking == "ing":
                    self.ingredients += 1
                    self.ingredient_names.append(want)
                else:
                    self.attached.add(self._picking)
                    self.attached_names[self._picking] = want
                self._picking = None
        elif key == "role:button:Tile grid settings":
            self.grid_menu = not self.grid_menu
        elif key == "role:switch:Return silent videos toggle":
            self.silent_on = not self.silent_on
            self.n_silent_click += 1
        elif key == "css:.cdk-overlay-backdrop":
            self.grid_menu = False
        elif key == "role:button:Start generation":
            self.n_generate += 1
            self.editor_text = ""       # 字被吃掉，图留在作曲区
        elif key.startswith("role:radio:") and self.panel_open:
            v = key.split(":", 2)[2]
            if v == "Image":
                self.mode = "image"
            elif v == "Video":
                self.mode = "video"
            elif v.endswith("s") and v[:-1].isdigit():
                self.duration = v
            elif v in ("9:16", "16:9", "1:1", "4:3", "3:4"):
                self.aspect = v
            elif v.startswith("x") and v[1:].isdigit():
                self.count = v
        elif key.startswith("text:"):
            lab = key[5:]
            if lab in fa.KNOWN_MODEL_LABELS:
                self.models["video"] = lab
            elif lab in _IMG_KEY:
                self.models["image"] = lab

    def _loc(self, key, matched=True):
        return _Loc(self, key, matched=matched and key not in self.missing)

    # ---- Playwright 接口 ----
    def get_by_role(self, role, name=None, exact=False):
        key = f"role:{role}:{name}"
        if role == "button" and name == "Select model family" and self._missing_model_family:
            return self._loc(key, matched=False)
        if role == "option":
            return self._loc(key, matched=False)  # 逼真实代码走 get_by_text 兜底
        if role == "button" and name == "Clear prompt":
            return self._loc(key, matched=self._dirty())
        if role == "button" and name in ("Start", "End"):
            return self._loc(key, matched=(self.mode == "video" and name not in self.attached))
        if role == "button" and name == "Add ingredients to the prompt box":
            return self._loc(key, matched=(self.mode == "image"))
        if role == "button" and name == "Add to prompt":
            # 晚一步自动挂上的那种：这时「Add to prompt」点不中（真机截图里它已经没了）
            return self._loc(key, matched=self._picking is not None and not self._late)
        if role == "button" and name == "Dismiss":
            return self._loc(key, matched=bool(self.snackbar))
        if role == "button" and name == "Upload media":
            return self._loc(key, matched=self._picking is not None)
        if role == "radio":
            return self._loc(key, matched=self.panel_open)
        if role == "switch":
            return self._loc(key, matched=self.grid_menu)
        return self._loc(key)

    def get_by_text(self, text, exact=False):
        if hasattr(text, "search"):
            for lab in list(fa.KNOWN_MODEL_LABELS) + list(_IMG_KEY):
                if text.search(lab) or text.search("🍌 " + lab):
                    return self._loc(f"text:{lab}", matched=f"text:{lab}" not in self.missing)
            return self._loc(f"text:{text.pattern}", matched=False)
        return self._loc(f"text:{text}")

    def get_by_placeholder(self, text):
        # 真页面上这个永远找不到（ProseMirror 没有 placeholder 属性）
        return self._loc(f"placeholder:{text}", matched=False)

    def locator(self, sel, has_text=None, has=None):
        if sel == ".asset-title":
            return ("asset-title", has_text)
        if sel == '[role="option"]' and has is not None:
            rx = has[1]
            hit = next((a for a in self.assets if rx.search(a["name"]) and a["polls"] <= 0
                        and not a["reject"]), None)
            return self._loc("option:" + (hit["name"] if hit else "?"), matched=hit is not None)
        if sel == "button.empty-chip" and has_text is not None:
            slot = "Start" if has_text.search("Start") else ("End" if has_text.search("End") else "")
            return self._loc(f"css:{sel}:{slot}",
                             matched=(self.mode == "video" and slot and slot not in self.attached))
        return self._loc(f"css:{sel}")

    def goto(self, url, **k):
        self.gotos.append(url)
        self.log.append(("goto", url))
        # 刷新：设置面板收起，作曲区清空；设置（模式/时长/画幅/条数/模型）Flow 自己记着
        self.panel_open = False
        self.editor_text = ""
        self.attached.clear()
        self.ingredients = 0
        self.composer_dirty = False

    def screenshot(self, path=None, **k):
        self.log.append(("screenshot", path))
        if path:
            Path(path).write_bytes(b"png")

    def evaluate(self, js, *a, **k):
        if js is fa.COMPOSER_IMG_JS:
            if self._late:
                self._late[0] -= 1
                if self._late[0] <= 0:
                    _, slot, want = self._late
                    self._late = None
                    if slot == "ing":
                        self.ingredients += 1
                        self.ingredient_names.append(want)
                    else:
                        self.attached.add(slot)
                        self.attached_names[slot] = want
                    self._picking = None      # 弹窗关了，「Add to prompt」也就没了
            return len(self.attached) + self.ingredients
        if js is fa.UPLOAD_FAIL_JS:
            return self.snackbar
        if js is fa.ASSET_STATE_JS:
            name = a[0]
            for x in self.assets:              # 每问一次，还在传的往前走一步
                if x["polls"] > 0:
                    x["polls"] -= 1
            self.assets = [x for x in self.assets if not (x["reject"] and x["polls"] <= 0)]
            mine = [x for x in self.assets if x["name"] == name]
            return {"pane": True, "n": len(self.assets), "mine": len(mine),
                    "uploading": any(x["polls"] > 0 for x in mine),
                    "ready": len([x for x in mine if x["polls"] <= 0]),
                    "selected": any(x["selected"] for x in mine),
                    "selected_names": [x["name"] for x in self.assets if x["selected"]]}
        return {}

    def expect_file_chooser(self, timeout=0):
        return _FileChooserCtx(self)

    def expect_response(self, predicate, timeout=0):
        import json as _json
        from urllib.parse import quote
        txt = self.editor_text if self.request_prompt is None else self.request_prompt
        if self.mode == "image":
            self.last_rpc = "ogiZ0b"
            key = _IMG_KEY.get(self.models["image"], "?")
            inner = _json.dumps([None, [[None, None, None, 1, 2, key, None, None, [[[txt]]]]], 1])
        else:
            self.last_rpc = "eb1hJf"
            inner = _json.dumps([[[[None, None, [[[txt]]]]], "veo_3_1_i2v_lite_low_priority"],
                                 None, ["U-U-I-D", 1 if self.silent_on else 2]])
        body = "f.req=" + quote(_json.dumps([[[self.last_rpc, inner, None, "generic"]]]))
        return _ResponseCtx(self.log, _Response(self._status, self._text, post_data=body))


def _make_engine(monkeypatch):
    account = {
        "id": "acc-test-flow1", "name": "Flow1", "port": 9300,
        "project_url": "https://flow.google.com/project/" + "a" * 8 + "-" + "b" * 4 + "-" + "c" * 4
                        + "-" + "d" * 4 + "-" + "e" * 12,
        "model": "Veo 3.1 - Lite [Lower Priority]",
    }
    logs = []
    eng = fa.FlowEngine(account, logs.append)
    monkeypatch.setattr(eng, "_note_rpc", lambda *a, **k: None)
    monkeypatch.setattr(eng, "_ui_pace", lambda *a, **k: None)   # 测试里不真的停顿
    monkeypatch.setattr("orante.accounts.note_flow_success", lambda *a, **k: None, raising=False)
    return eng, logs


# ---------------- 3) submit() 控制流 ----------------

def test_submit_uses_ui_path_for_every_segment(monkeypatch, tmp_path):
    """2026-09-27 起：只要配置开着，每一段都先走 UI，成功了就不落回接口
    直连——不再是"热一次就切回快路"。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")

    calls = {"ui": 0, "direct": 0}

    def fake_ui(seg, image, prompt, duration, *, last_image, aspect, silent=False):
        calls["ui"] += 1
        return f"gid-from-ui-{calls['ui']}"

    def fake_direct_rpc(rpc, inner, retries=2, raise_=True):
        if rpc != "eb1hJf":
            return None  # tick_credits() 之类顺路的 nzlxg 查询，不算一次提交
        calls["direct"] += 1
        return [[["gid-from-direct", "proj"]], 99, None, [["gid-from-direct", "proj"]]]

    monkeypatch.setattr(eng, "_submit_via_ui", fake_ui)
    monkeypatch.setattr(eng, "upload_image", lambda p: "media-" + Path(p).name)
    monkeypatch.setattr(eng, "_rpc", fake_direct_rpc)

    gid1 = eng.submit({"s": 1}, img, "prompt one", 8)
    assert gid1 == "gid-from-ui-1"
    assert calls["ui"] == 1 and calls["direct"] == 0
    assert eng._ui_warmed is True

    gid2 = eng.submit({"s": 2}, img, "prompt two", 8)
    assert gid2 == "gid-from-ui-2"
    assert calls["ui"] == 2, "每一段都该走 UI，不该只走第一段"
    assert calls["direct"] == 0, "UI 每次都成功就不该碰接口直连"


def test_submit_never_falls_back_to_direct_on_structural_ui_failure(monkeypatch, tmp_path):
    """2026-09-27 定稿：界面上某一步对不上 → 这一段直接报错（说清卡在哪一步），
    绝不改走接口直连（冷标签页走接口直连必被判异常、冷却翻倍）；下一段照旧先走界面。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")

    calls = {"ui": 0, "direct": 0}

    def fake_ui(seg, image, prompt, duration, *, last_image, aspect, silent=False):
        calls["ui"] += 1
        eng._ui_stage = "粘贴提示词"
        raise TimeoutError("Locator.click: Timeout 30000ms exceeded.\nCall log:\n"
                           "  - waiting for locator('div.ProseMirror')")

    def fake_direct_rpc(rpc, inner, retries=2, raise_=True):
        if rpc != "eb1hJf":
            return None
        calls["direct"] += 1
        return [[["gid-from-direct", "proj"]], 99, None, [["gid-from-direct", "proj"]]]

    monkeypatch.setattr(eng, "_submit_via_ui", fake_ui)
    monkeypatch.setattr(eng, "upload_image", lambda p: "media-" + Path(p).name)
    monkeypatch.setattr(eng, "_rpc", fake_direct_rpc)

    with pytest.raises(OranteError) as ei:
        eng.submit({"s": 1}, img, "prompt one", 8)
    assert "粘贴提示词" in ei.value.message, "报错要说清卡在哪一步"
    assert "waiting for" in ei.value.message, "要带上在等哪个控件"
    assert "没有改走接口直连" in ei.value.hint
    assert eng._ui_warmup_failed is True

    with pytest.raises(OranteError):
        eng.submit({"s": 2}, img, "prompt two", 8)
    assert calls["ui"] == 2, "上一段没走通，下一段也照旧先走界面"
    assert calls["direct"] == 0, "开着真人操作就绝不走接口直连"


def test_submit_error_after_generate_click_warns_may_be_submitted(monkeypatch, tmp_path):
    """点了「生成」之后才出的问题：提示用户这段可能已经在生成了，别急着重交。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")

    def fake_ui(seg, image, prompt, duration, *, last_image, aspect, silent=False):
        eng._ui_stage = "等回包"
        raise TimeoutError("Timeout 45000ms exceeded while waiting for event \"response\"")

    monkeypatch.setattr(eng, "_submit_via_ui", fake_ui)
    monkeypatch.setattr(eng, "_rpc", lambda *a, **k: None)
    with pytest.raises(OranteError) as ei:
        eng.submit({"s": 1}, img, "prompt", 8)
    assert "可能已经在 Flow 里生成了" in ei.value.hint


def _assert_real_answer_not_swallowed(monkeypatch, tmp_path, exc):
    """真人操作式提交如果也被 Flow 真的拒了（限流/审核/额度），这是真答案，
    不该被当成"自动化没走通"而悄悄回退到接口直连再交一次。

    注：不用 @pytest.mark.parametrize——tools/conftest.py 的收集期过滤只认
    pytest 内置 fixture 名，会把参数化的 `exc` 误判成"缺 fixture"整条测试
    丢掉（能在 -v 输出的 "deselected" 里看到），所以这里拆成三个独立函数调用
    同一段断言逻辑。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")

    def fake_ui(seg, image, prompt, duration, *, last_image, aspect, silent=False):
        raise exc

    direct_called = []

    def fake_rpc(rpc, inner, retries=2, raise_=True):
        if rpc == "eb1hJf":
            direct_called.append(1)
        return None

    monkeypatch.setattr(eng, "_submit_via_ui", fake_ui)
    monkeypatch.setattr(eng, "_rpc", fake_rpc)

    with pytest.raises(type(exc)):
        eng.submit({"s": 1}, img, "prompt", 8)
    assert not direct_called, "被真的拒了不该再落回接口直连重交一次"
    assert eng._ui_warmed is False
    assert eng._ui_warmup_failed is False


def test_submit_propagates_flow_throttled_without_fallback(monkeypatch, tmp_path):
    _assert_real_answer_not_swallowed(
        monkeypatch, tmp_path,
        FlowThrottled("Flow1", code="PUBLIC_ERROR_UNUSUAL_ACTIVITY", err_n=7, raw="x"))


def test_submit_propagates_blocked_image_without_fallback(monkeypatch, tmp_path):
    _assert_real_answer_not_swallowed(monkeypatch, tmp_path, BlockedImage("画面被拒"))


def test_submit_propagates_credit_halted_without_fallback(monkeypatch, tmp_path):
    _assert_real_answer_not_swallowed(monkeypatch, tmp_path, CreditHalted(100, 50, {}))


def test_submit_skips_ui_path_when_config_off(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")
    monkeypatch.setattr(config, "get",
                         lambda k, d=None: False if k == "flow.human_warmup" else d)

    calls = {"ui": 0}
    monkeypatch.setattr(eng, "_submit_via_ui", lambda *a, **k: calls.__setitem__("ui", calls["ui"] + 1))
    monkeypatch.setattr(eng, "upload_image", lambda p: "media-" + Path(p).name)
    monkeypatch.setattr(eng, "_rpc", lambda *a, **k: [[["gid", "proj"]], 1, None, [["gid", "proj"]]])

    gid = eng.submit({"s": 1}, img, "prompt", 8)
    assert gid == "gid"
    assert calls["ui"] == 0, "配置关掉暖场就完全不该碰 UI 路径"


# ---------------- 4) _submit_via_ui 真正跑一遍点击序列 ----------------

def test_submit_via_ui_click_sequence_success(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")
    page = FakePage(response_status=200, response_text=CAPTURE_BODY_OK, model_shown="Omni 1.1 Flash",
                    option_click_attaches=False)   # 走「选中 → Add to prompt」那种弹窗行为
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)

    prompt = "画面里有一个人，\n慢慢抬头微笑。"
    gid = eng._submit_via_ui({"s": 1}, img, prompt, 8, last_image=None, aspect="9:16")
    assert gid == "66a5d214-6d3c-4020-8d65-63c85152c2b4"

    clicked = [e[1] for e in page.log if e[0] == "click"]
    order = [c for c in clicked if c in (
        "role:button:Settings trigger", "role:button:Start", "role:button:Add to prompt",
        "css:" + fa.EDITOR_SEL, "role:button:Start generation")]
    # 设置按钮点两下：一下打开面板、一下关上（它是开关，Esc 关不掉）
    assert order == ["role:button:Settings trigger", "role:button:Settings trigger",
                     "role:button:Start", "role:button:Add to prompt",
                     "css:" + fa.EDITOR_SEL, "role:button:Start generation"], order
    assert page.panel_open is False, "交之前设置面板必须已经关上"
    assert "role:radio:Video" in clicked and "role:radio:Frames" in clicked
    assert "role:radio:9:16" in clicked and "role:radio:8s" in clicked
    assert "role:radio:x1" in clicked, "每次只要 1 条，别让账号上次的 x2 白烧额度"
    assert "role:button:Select model family" in clicked
    assert "text:Veo 3.1 - Lite [Lower Priority]" in clicked
    assert "role:button:End" not in clicked           # 没给尾帧就不该点 End
    assert "role:button:Clear prompt" not in clicked  # 作曲区本来就是空的，不用清
    # 提示词是一次性粘贴进 ProseMirror 框的，而且读回来跟原文一致
    pasted = [e[1] for e in page.log if e[0] == "insert_text"]
    assert pasted == [prompt]
    assert page.n_generate == 1, "生成只点一次"
    assert page.attached == {"Start"} and page.count == "x1" and page.aspect == "9:16"
    assert not any(e[0] == "type" for e in page.log), "不再一个字一个字敲"


def test_submit_via_ui_clears_leftover_composer_first(monkeypatch, tmp_path):
    """上一段交完作曲区里还挂着图：先点 ×（Clear prompt）清空，再挂这一段的图。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p02.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")
    page = FakePage(composer_dirty=True)
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    eng._submit_via_ui({"s": 2}, img, "第二段", 8, last_image=None, aspect="9:16")
    clicked = [e[1] for e in page.log if e[0] == "click"]
    assert clicked.index("role:button:Clear prompt") < clicked.index("role:button:Start")


def test_submit_via_ui_wrong_model_never_submits(monkeypatch, tmp_path):
    """模型读回来不对（比如停在不吃图片的 Omni）：这一段不交，也不挂图、不点生成。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")
    page = FakePage(model_shown="Omni 1.1 Flash", missing={"text:Veo 3.1 - Lite [Lower Priority]"})
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    with pytest.raises(Exception):
        eng._submit_via_ui({"s": 1}, img, "p", 8, last_image=None, aspect="9:16")
    clicked = [e[1] for e in page.log if e[0] == "click"]
    assert "role:button:Start generation" not in clicked
    assert not any(e[0] == "set_files" for e in page.log)
    assert len(page.gotos) == 1, "点生成之前出岔子：重新打开一次项目页、再走一遍"


def test_submit_via_ui_model_readback_mismatch_raises(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage(model_shown="Omni 1.1 Flash")
    with pytest.raises(OranteError) as ei:
        eng._ui_check_model(page, "Veo 3.1 - Lite [Lower Priority]")
    assert "Omni 1.1 Flash" in ei.value.message
    eng._ui_check_model(FakePage(model_shown="Veo 3.1 - Lite [Lower Priority]"),
                        "Veo 3.1 - Lite [Lower Priority]")


def test_submit_via_ui_paste_not_landing_never_submits(monkeypatch, tmp_path):
    """粘贴没进框（读回来跟原文对不上）：绝不点生成。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")
    page = FakePage(drop_paste=True)
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    with pytest.raises(OranteError) as ei:
        eng._submit_via_ui({"s": 1}, img, "一段文案", 8, last_image=None, aspect="9:16")
    assert "提示词没粘进" in ei.value.message
    clicked = [e[1] for e in page.log if e[0] == "click"]
    assert "role:button:Start generation" not in clicked


def test_placeholder_selector_is_not_used(monkeypatch, tmp_path):
    """真页面上的提示词框没有 placeholder 属性——get_by_placeholder 永远找不到，
    这正是 09-27 真机卡住的原因。确认代码不再依赖它。"""
    import inspect
    src = inspect.getsource(fa.FlowEngine)
    assert "get_by_placeholder" not in src
    assert "ProseMirror" in fa.EDITOR_SEL


def test_short_err_keeps_waiting_for_line():
    e = TimeoutError("Locator.click: Timeout 30000ms exceeded.\nCall log:\n"
                     "  - waiting for get_by_role(\"button\", name=\"Add to prompt\")\n  - more")
    s = fa._short_err(e)
    assert "Timeout 30000ms" in s and "Add to prompt" in s and "more" not in s


def test_submit_via_ui_attaches_end_frame_when_given(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    tail = tmp_path / "tail.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")
    tail.write_bytes(b"\xff\xd8\xff\xe0fake2")
    page = FakePage(response_status=200, response_text=CAPTURE_BODY_OK, option_click_attaches=False)
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)

    gid = eng._submit_via_ui({"s": 2}, img, "接着上一段", 8, last_image=tail, aspect="16:9")
    assert gid == "66a5d214-6d3c-4020-8d65-63c85152c2b4"
    clicked = [e[1] for e in page.log if e[0] == "click"]
    assert clicked.count("role:button:Start") == 1
    assert clicked.count("role:button:End") == 1
    assert clicked.count("role:button:Add to prompt") == 2, "起帧尾帧各要确认一次"
    # 起帧格子里挂的是起帧那张图、尾帧格子里是尾帧那张（按内容取的唯一名字核对）
    assert page.attached_names == {"Start": eng._ui_unique_copy(img)[0],
                                   "End": eng._ui_unique_copy(tail)[0]}


def test_submit_via_ui_raises_throttled_on_real_rejection(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")
    body = (")]}'\n\n7\n"
            '[["wrb.fr","eb1hJf",null,null,null,'
            '["PUBLIC_ERROR_UNUSUAL_ACTIVITY",null,null,null,null,null,7]]]\n')
    page = FakePage(response_status=200, response_text=body)
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)

    with pytest.raises(FlowThrottled) as ei:
        eng._submit_via_ui({"s": 1}, img, "prompt", 8, last_image=None, aspect="9:16")
    assert ei.value.flow_code == "PUBLIC_ERROR_UNUSUAL_ACTIVITY"



# ---------------- 5) 09-27 下午：一次交 4 段、不刷新页面、不认错号 ----------------

def _img(tmp_path, name="p01.jpg"):
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0fake")
    return p


def test_consecutive_segments_no_page_refresh(monkeypatch, tmp_path):
    """连着交 4 段：每段先点 × 清掉上一段留下的图，接着交下一段；全程不刷新页面。
    （原来每段都"点一下设置 + 按 Esc"，Esc 关不掉面板，下一段再点反而把面板关了，
    卡 30 秒后刷新——就是用户看到的"每生成一段就刷新一次"。）"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage(model_shown="Omni 1.1 Flash")
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    durs = [8, 8, 4, 6]
    for i, d in enumerate(durs, 1):
        gid = eng._submit_via_ui({"s": i}, _img(tmp_path, f"p{i:02d}.jpg"), f"第{i}段文案", d,
                                 last_image=None, aspect="9:16")
        assert gid == "66a5d214-6d3c-4020-8d65-63c85152c2b4"
        assert page.panel_open is False
    assert page.gotos == [], "连续交几段都不该刷新页面"
    assert page.n_generate == 4
    clicked = [e[1] for e in page.log if e[0] == "click"]
    assert clicked.count("role:button:Clear prompt") == 3, "第 2~4 段先点 × 清掉上一段的图"
    # 第 2 段时长/画幅/条数/模型都没变 → 不用再开设置面板；第 3、4 段时长变了才开
    assert clicked.count("role:button:Settings trigger") == 2 * 3
    assert not any("重新打开" in ln for ln in logs)


def test_capacity_capped_at_4_in_human_mode(monkeypatch):
    _pin()
    eng, _ = _make_engine(monkeypatch)
    eng.max_conc = 5
    assert eng.capacity() == 4, "网页上最多 4 条同时生成"
    eng._active.update({"a", "b", "c"})
    assert eng.capacity() == 1
    eng._active.add("d")
    assert eng.capacity() == 0
    monkeypatch.setattr(config, "get", lambda k, d=None: False if k == "flow.human_warmup" else d)
    assert eng.capacity() == 1, "关掉真人操作才回到账号自己的并发上限"


def test_response_for_other_prompt_is_never_accepted(monkeypatch, tmp_path):
    """回包对应的请求里带的不是这一段的提示词 → 这个 genId 绝不记到这段头上。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage()
    page.request_prompt = "别的段的文案"
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    with pytest.raises(OranteError) as ei:
        eng._submit_via_ui({"s": 3}, _img(tmp_path), "第三段的文案", 8, last_image=None, aspect="9:16")
    assert "对不上" in ei.value.message
    assert not eng._active, "不认的 genId 不能进在飞列表（不会下载、不会改名）"
    assert page.n_generate == 1, "点了生成之后绝不再点第二次"


def test_request_carries_prompt_on_real_capture():
    """用 9/22 真抓包（网页真点出来的 eb1hJf 请求）核对：能从请求体里认出这一段的提示词。"""
    cap = (Path(__file__).resolve().parents[1] / "data" / "capture" / "flow_capture_0922-012853.jsonl")
    if not cap.is_file():
        pytest.skip("这台机器上没有 0922 抓包文件，跳过")
    import json
    post = None
    with cap.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            d = json.loads(line)
            if "rpcids=eb1hJf" in d.get("url", "") and d.get("postData"):
                post = d["postData"]
                break
    assert post
    leaves = fa._freq_strings(post)
    assert leaves and any(x.startswith("veo_") for x in leaves)
    prompt = next(x for x in leaves if len(x) > 40 and " " in x)
    assert fa.request_carries_prompt(post, prompt) is True
    assert fa.request_carries_prompt(post, prompt + " 多一句") is False
    assert fa.request_carries_prompt("garbage", prompt) is None


def test_upload_rejected_raises_blocked_image(monkeypatch, tmp_path):
    """Flow 当场拒收这张图 → BlockedImage（跟原来走接口上传被拒一样，上层去换图），不点生成。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage(upload_reject="Failed to upload image. We do not allow uploads of minors.")
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    with pytest.raises(BlockedImage) as ei:
        eng._submit_via_ui({"s": 1}, _img(tmp_path), "p", 8, last_image=None, aspect="9:16")
    assert page.n_generate == 0
    assert page.n_upload == 2 and "minors" in str(ei.value)
    assert page.snackbar == "", "提示条要点掉"


def test_transient_upload_failure_retries_and_succeeds(monkeypatch, tmp_path):
    """第一次 Failed to upload、第二次传成功：照常挂自己的图、照常交。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage(upload_reject="Failed to upload p01-xxxx.jpg")
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    orig_set = _FileChooserCtx.set_files

    def flaky(self, path):
        orig_set(self, path)
        if self.page.n_upload >= 2:
            self.page.assets[0]["reject"] = False
            self.page.upload_reject = ""
            self.page.snackbar = ""
    monkeypatch.setattr(_FileChooserCtx, "set_files", flaky)
    img = _img(tmp_path)
    gid = eng._submit_via_ui({"s": 1}, img, "p", 8, last_image=None, aspect="9:16")
    assert gid and page.n_upload == 2
    assert page.attached_names["Start"] == eng._ui_unique_copy(img)[0]
    assert any("再传一次" in ln for ln in logs)


def test_frame_not_really_attached_never_submits(monkeypatch, tmp_path):
    """点了「Add to prompt」但格子没变（图没挂上）→ 绝不点生成（没首帧就是纯文生视频）。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    monkeypatch.setattr(fa, "UI_UPLOAD_WAIT", 1)
    page = FakePage(add_to_prompt_works=False, option_click_attaches=False)
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    with pytest.raises(OranteError) as ei:
        eng._submit_via_ui({"s": 1}, _img(tmp_path), "p", 8, last_image=None, aspect="9:16")
    assert "没挂进作曲区" in ei.value.message
    assert page.n_generate == 0


def test_leftover_frame_that_x_cannot_clear_reloads_once(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage(missing={"role:button:Clear prompt"})
    page.attached.add("Start")                      # 上一段的图还在，× 又找不到
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    gid = eng._submit_via_ui({"s": 2}, _img(tmp_path), "p", 8, last_image=None, aspect="9:16")
    assert gid and len(page.gotos) == 1
    assert any("还挂着上一段的图" in ln for ln in logs)


# ---------------- 6) 生图（扩图 / 高清修复原图 / 尾帧提清 / 描述卡）也走网页 ----------------

class _FakeHTTP:
    def __init__(self):
        self.content = b"\xff\xd8\xff\xe0generated"

    def raise_for_status(self):
        pass


def _image_engine(monkeypatch, page, key=""):
    eng, logs = _make_engine(monkeypatch)
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    monkeypatch.setattr(eng, "tick_credits", lambda: None)

    def no_direct(*a, **k):
        raise AssertionError("开着真人操作，生图不许再走接口直连（maseQ / ogiZ0b）")

    monkeypatch.setattr(eng, "upload_image", no_direct)
    monkeypatch.setattr(eng, "gen_image_once", no_direct)
    monkeypatch.setattr(eng, "_rpc", no_direct)
    real_get = config.get
    monkeypatch.setattr(config, "get",
                        lambda k, d=None: key if k == "flow.image_model_key" else real_get(k, d))
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeHTTP())
    return eng, logs


def test_gen_images_goes_through_web_ui(monkeypatch, tmp_path):
    _pin()
    page = FakePage(response_text=IMAGE_BODY_OK, image_model="Nano Banana 2 Lite")
    eng, logs = _image_engine(monkeypatch, page, key="NARWHAL")
    ref = _img(tmp_path, "ref.jpg")
    out = eng.gen_images("换个角度拍同一个人", ref=ref, n=2, aspect="9:16", dest_dir=tmp_path / "out")
    assert len(out) == 2 and all(p.is_file() for p in out)
    clicked = [e[1] for e in page.log if e[0] == "click"]
    assert "role:radio:Image" in clicked and "role:radio:9:16" in clicked and "role:radio:x1" in clicked
    assert clicked.count("role:button:Add ingredients to the prompt box") == 2, "每张图都挂一次参考图"
    assert page.models["image"] == "Nano Banana 2", "NARWHAL = Nano Banana 2，不能认成 2 Lite"
    assert page.n_generate == 2 and page.last_rpc == "ogiZ0b"
    uname = eng._ui_unique_copy(ref)[0]
    assert page.ingredient_names == [uname, uname], "两张图挂的都是这张参考图"
    assert len([e for e in page.log if e[0] == "set_files"]) == 1, "同一张参考图只传一次，第二次直接用库里的"
    assert page.gotos == []


def test_gen_images_default_model_is_nano_banana_pro(monkeypatch, tmp_path):
    _pin()
    page = FakePage(response_text=IMAGE_BODY_OK, image_model="Nano Banana 2")
    eng, logs = _image_engine(monkeypatch, page, key="")
    out = eng.gen_images("高清修复", ref=_img(tmp_path), n=1, dest_dir=tmp_path / "o")
    assert len(out) == 1
    assert page.models["image"] == "Nano Banana Pro"


def test_gen_images_ref_rejected_propagates(monkeypatch, tmp_path):
    """参考图本身被拒：跟原来直连时 upload_image 抛 BlockedImage 一样往上抛。"""
    _pin()
    page = FakePage(response_text=IMAGE_BODY_OK, upload_reject="We do not allow uploads of public figures.")
    eng, logs = _image_engine(monkeypatch, page, key="NARWHAL")
    with pytest.raises(BlockedImage):
        eng.gen_images("p", ref=_img(tmp_path), n=2, dest_dir=tmp_path / "o")
    assert page.n_generate == 0


def test_gen_images_structural_failure_is_clear_error(monkeypatch, tmp_path):
    _pin()
    page = FakePage(response_text=IMAGE_BODY_OK, missing={"role:radio:Image"})
    eng, logs = _image_engine(monkeypatch, page, key="NARWHAL")
    with pytest.raises(OranteError) as ei:
        eng.gen_images("p", ref=_img(tmp_path), n=1, dest_dir=tmp_path / "o")
    assert "真人操作卡在" in ei.value.message and "没有改走接口直连" in ei.value.hint


def test_gen_images_direct_only_when_switch_off(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    monkeypatch.setattr(config, "get", lambda k, d=None: False if k == "flow.human_warmup" else d)
    calls = []
    monkeypatch.setattr(eng, "upload_image", lambda p: calls.append("up") or "M" * 36)
    monkeypatch.setattr(eng, "gen_image_once",
                        lambda *a, **k: calls.append("gen") or {"media_id": "abcdefgh1", "url": "u", "seed": 1})
    monkeypatch.setattr(eng, "_gen_image_via_ui", lambda *a, **k: (_ for _ in ()).throw(AssertionError("UI")))
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeHTTP())
    out = eng.gen_images("p", ref=_img(tmp_path), n=1, aspect="9:16", dest_dir=tmp_path / "o")
    assert len(out) == 1 and calls == ["up", "gen"]


def test_video_after_image_switches_back_to_video(monkeypatch, tmp_path):
    """同一个标签页先生了图（切到了 Image），再交视频：会打开面板切回 Video/Frames。"""
    _pin()
    page = FakePage(response_text=IMAGE_BODY_OK)
    eng, logs = _image_engine(monkeypatch, page, key="NARWHAL")
    eng.gen_images("p", ref=_img(tmp_path, "r.jpg"), n=1, dest_dir=tmp_path / "o")
    assert page.mode == "image"
    page._text = CAPTURE_BODY_OK
    gid = eng._submit_via_ui({"s": 1}, _img(tmp_path), "视频文案", 8, last_image=None, aspect="9:16")
    assert gid and page.mode == "video" and page.attached == {"Start"} and page.ingredients == 0


def test_model_label_strips_banana_and_arrow():
    assert fa._model_label("🍌 Nano Banana 2 arrow_drop_down") == "Nano Banana 2"
    assert fa._model_label("Veo 3.1 - Lite [Lower Priority]") == "Veo 3.1 - Lite [Lower Priority]"
    assert set(fa.IMAGE_MODEL_LABELS) >= {"GEM_PIX_2", "NARWHAL"}


# ---------------- 7) 09-27 晚：绝不挂错图（T0113~T0116 全挂成了 T0112 的图） ----------------

def test_never_attaches_previously_selected_asset(monkeypatch, tmp_path):
    """资产库里别的任务的 p01 正被选中、新传的图不会自动选中（真实行为）——
    必须点中自己这张再挂，挂进格子的一定是这一段自己的图。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage()          # 默认：库里有一张别人的 p01.jpeg，正被选中
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0baseball")
    eng._submit_via_ui({"s": 1}, img, "文案", 8, last_image=None, aspect="9:16")
    mine = eng._ui_unique_copy(img)[0]
    assert page.attached_names["Start"] == mine
    assert page.attached_names["Start"] != "p01.jpeg"
    assert mine.startswith("p01-") and mine.endswith(".jpg")


def test_same_name_different_content_never_mixed(monkeypatch, tmp_path):
    """两个任务的首图都叫 p01、内容不同：各挂各的，绝不串。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage()
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    a = tmp_path / "A" / "p01.jpg"
    b = tmp_path / "B" / "p01.jpg"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_bytes(b"\xff\xd8\xff\xe0pastor")
    b.write_bytes(b"\xff\xd8\xff\xe0baseball")
    eng._submit_via_ui({"s": 1}, a, "A 段", 8, last_image=None, aspect="9:16")
    got_a = page.attached_names["Start"]
    eng._submit_via_ui({"s": 1}, b, "B 段", 8, last_image=None, aspect="9:16")
    got_b = page.attached_names["Start"]
    assert got_a == eng._ui_unique_copy(a)[0]
    assert got_b == eng._ui_unique_copy(b)[0]
    assert got_a != got_b


def test_reuses_existing_same_content_asset_without_upload(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0same")
    name = eng._ui_unique_copy(img)[0]
    page = FakePage(assets=[{"name": "p01.jpeg", "polls": 0, "selected": True, "reject": False},
                            {"name": name, "polls": 0, "selected": False, "reject": False}])
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    eng._submit_via_ui({"s": 1}, img, "文案", 8, last_image=None, aspect="9:16")
    assert not any(e[0] == "set_files" for e in page.log), "库里已有同一张图就不重复上传"
    assert page.attached_names["Start"] == name


def test_waits_for_upload_to_finish_before_add(monkeypatch, tmp_path):
    """还在 Uploading 的时候绝不点 Add to prompt（那时点下去挂的是旧图）。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage()
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    img = tmp_path / "p01.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0slow")
    orig_set = _FileChooserCtx.set_files

    def slow_set(self, path):
        orig_set(self, path)
        self.page.assets[0]["polls"] = 6       # 传得慢
    monkeypatch.setattr(_FileChooserCtx, "set_files", slow_set)
    eng._submit_via_ui({"s": 1}, img, "文案", 8, last_image=None, aspect="9:16")
    assert page.attached_names["Start"] == eng._ui_unique_copy(img)[0]


def test_upload_vanished_is_error_not_wrong_attach(monkeypatch, tmp_path):
    """传上去又消失（被拒但没看到原话）：报错，绝不退回去挂别的图。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage()
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    orig_set = _FileChooserCtx.set_files

    def vanish(self, path):
        orig_set(self, path)
        self.page.assets[0]["reject"] = True
    monkeypatch.setattr(_FileChooserCtx, "set_files", vanish)
    with pytest.raises(BlockedImage) as ei:
        eng._submit_via_ui({"s": 1}, _img(tmp_path), "p", 8, last_image=None, aspect="9:16")
    assert "消失" in str(ei.value) and "连传 2 次" in str(ei.value)
    assert page.n_upload == 2, "Flow 没收先再传一次，还不收才算被拒"
    assert page.n_generate == 0 and not page.attached_names


# ---------------- 8) 09-28：新图不等缩略图；点一下直接挂上；不说话的任务出无声视频 ----------------

def test_option_click_attaches_directly_no_add_to_prompt(monkeypatch, tmp_path):
    """真机行为：弹窗里点一下已传好的那一项就直接挂进格子、弹窗关掉——认这种，不再报"选不中"。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage()     # option_click_attaches=True（真机行为）
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    img = _img(tmp_path, "p01.png")
    gid = eng._submit_via_ui({"s": 1}, img, "文案", 8, last_image=None, aspect="16:9")
    assert gid
    clicked = [e[1] for e in page.log if e[0] == "click"]
    assert "role:button:Add to prompt" not in clicked
    assert page.attached_names["Start"] == eng._ui_unique_copy(img)[0]
    assert page.gotos == []


def test_ready_does_not_wait_for_thumbnail():
    """ASSET_STATE_JS 里"传完"只看有没有 Uploading，不再要求缩略图（真机上新图只显示图标）。"""
    js = fa.ASSET_STATE_JS
    assert "ready: mine.filter(o => !busy(o)).length" in js


def test_silent_task_turns_on_return_silent_videos(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage(silent_on=False)
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    eng._submit_via_ui({"s": 1}, _img(tmp_path), "文案", 8, last_image=None, aspect="9:16", silent=True)
    assert page.silent_on is True and page.grid_menu is False, "开关拨开、菜单关上"
    assert not any("声音设置" in ln for ln in logs), "请求里是无声，跟要的一致"
    # 第二段还是无声：不再去点开关
    eng._submit_via_ui({"s": 2}, _img(tmp_path, "p02.jpg"), "文案2", 8, last_image=None, aspect="9:16", silent=True)
    assert page.n_silent_click == 1


def test_sound_task_turns_off_return_silent_videos(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage(silent_on=True)     # 上次留下的是无声
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    eng._submit_via_ui({"s": 1}, _img(tmp_path), "文案", 8, last_image=None, aspect="9:16", silent=False)
    assert page.silent_on is False
    assert not any("声音设置" in ln for ln in logs)


def test_submit_passes_silent_through_to_ui(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    got = {}

    def fake_ui(seg, image, prompt, duration, *, last_image, aspect, silent=False):
        got["silent"] = silent
        return "gid"
    monkeypatch.setattr(eng, "_submit_via_ui", fake_ui)
    eng.submit({"s": 1}, _img(tmp_path), "p", 8, silent=True)
    assert got["silent"] is True


def test_request_audio_flag_parses_real_capture():
    cap = (Path(__file__).resolve().parents[1] / "data" / "capture" / "flow_capture_0922-012853.jsonl")
    if not cap.is_file():
        pytest.skip("这台机器上没有 0922 抓包文件，跳过")
    import json
    flags = []
    with cap.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            d = json.loads(line)
            if "rpcids=eb1hJf" in d.get("url", "") and d.get("postData"):
                flags.append(fa.request_audio_flag(d["postData"]))
    assert set(flags) <= {1, 2} and flags


# ---------------- 09-28 T0122：正方形图、晚一步自己挂上、停止不记失败 ----------------

def _real_img(tmp_path, name, w, h, color=(200, 60, 60)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), color)
    dr = ImageDraw.Draw(im)
    dr.rectangle([w // 4, h // 3, w // 2, h * 2 // 3], fill=(20, 20, 220))
    dr.ellipse([w // 2, h // 5, w * 4 // 5, h // 2], fill=(250, 250, 30))
    p = tmp_path / name
    im.save(p)
    return p


def test_square_frame_is_center_cropped_to_16_9_before_upload(monkeypatch, tmp_path):
    """正方形图网页上传前先居中裁成精确 16:9（接口时代由 crop 框做的事），否则 Flow 补黑边。"""
    _pin()
    from PIL import Image
    eng, logs = _make_engine(monkeypatch)
    page = FakePage()
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    img = _real_img(tmp_path, "p01.png", 1200, 1200)
    gid = eng._submit_via_ui({"s": 1}, img, "文案", 8, last_image=None, aspect="16:9")
    assert gid
    up = [e[1] for e in page.log if e[0] == "set_files"]
    assert len(up) == 1
    with Image.open(up[0]) as im:
        w, h = im.size
    assert abs(w / h - 16 / 9) < 0.01 and w == 1200
    name = Path(up[0]).name
    assert page.attached_names["Start"] == name
    assert name.startswith("p01-") and name.endswith("-16x9.jpg")
    # 同一张图第二次：同名、不重传
    name2, _ = eng._ui_frame_copy(img, "16:9")
    assert name2 == name


def test_exact_aspect_small_frame_uploaded_as_is(monkeypatch, tmp_path):
    _pin()
    eng, logs = _make_engine(monkeypatch)
    img = _real_img(tmp_path, "p05.png", 1280, 720)
    assert eng._ui_frame_copy(img, "16:9") == eng._ui_unique_copy(img)


def test_big_frame_is_shrunk(monkeypatch, tmp_path):
    _pin()
    from PIL import Image
    eng, logs = _make_engine(monkeypatch)
    img = _real_img(tmp_path, "p06.png", 3840, 2160)
    name, up = eng._ui_frame_copy(img, "16:9")
    with Image.open(up) as im:
        assert im.size == (2560, 1440)


def test_portrait_frame_cropped_to_9_16(monkeypatch, tmp_path):
    _pin()
    from PIL import Image
    eng, logs = _make_engine(monkeypatch)
    img = _real_img(tmp_path, "p07.png", 1000, 1500)
    name, up = eng._ui_frame_copy(img, "9:16")
    with Image.open(up) as im:
        w, h = im.size
    assert abs(w / h - 9 / 16) < 0.01 and h == 1500


def test_ingredient_refs_are_never_cropped(monkeypatch, tmp_path):
    """扩图 / 高清修复的参考图原样传——只有视频首尾帧才裁。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage(image_model="Nano Banana Pro")
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    ref = _real_img(tmp_path, "p02.png", 1200, 1200)
    try:
        eng._gen_image_via_ui(ref, "扩成横图", "16:9", "GEM_PIX_2")
    except Exception:
        pass
    up = [Path(e[1]).name for e in page.log if e[0] == "set_files"]
    assert up and up[0] == eng._ui_unique_copy(ref)[0]


def test_late_direct_attach_does_not_click_vanished_add_to_prompt(monkeypatch, tmp_path):
    """T0122 s002：先看到选中、Flow 晚一步自己挂上并关弹窗——不能再去点已经没了的「Add to prompt」。"""
    _pin()
    eng, logs = _make_engine(monkeypatch)
    page = FakePage(late_attach_polls=3)
    monkeypatch.setattr(eng, "_ensure_page", lambda: page)
    img = _img(tmp_path, "p02.png")
    gid = eng._submit_via_ui({"s": 2}, img, "文案", 8, last_image=None, aspect="16:9")
    assert gid
    assert page.attached_names["Start"] == eng._ui_unique_copy(img)[0]
    assert page.gotos == []


def test_verify_square_image_cropped_video_matches(tmp_path):
    """首帧校验：正方形配图，片子首帧是居中裁出的 16:9 → 算对上；换一张图 → 仍然对不上。"""
    from PIL import Image
    from orante.services import verify as vf
    a = _real_img(tmp_path, "a.png", 1200, 1200)
    b = _real_img(tmp_path, "b.png", 1200, 1200, color=(30, 160, 60)).rename(tmp_path / "b0.png")
    with Image.open(b) as im:
        im.transpose(Image.ROTATE_90).save(tmp_path / "b.png")   # 构图不一样的另一张图
    b = tmp_path / "b.png"
    with Image.open(a) as im:
        fr = im.crop((0, (1200 - 675) // 2, 1200, (1200 - 675) // 2 + 675))
        fr = fr.resize((1280, 720))
        fp = tmp_path / "frame.png"
        fr.save(fp)
    vh = vf._pillow_gray16(fp)
    d_full = vf.hamming(vh, vf.ahash_image(a))
    d_crop = vf.hamming(vh, vf.ahash_image_cropped(a))
    assert d_crop <= vf.DIST_OK < d_full or d_crop <= vf.DIST_OK
    d_other = min(vf.hamming(vh, vf.ahash_image(b)), vf.hamming(vh, vf.ahash_image_cropped(b)))
    assert d_other > vf.DIST_SOFT
    # 已经是精确 16:9 的图没有"裁过的"版本
    c = _real_img(tmp_path, "c.png", 1280, 720)
    assert vf.ahash_image_cropped(c) is None

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
