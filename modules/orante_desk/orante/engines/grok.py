# -*- coding: utf-8 -*-
"""Grok（grok.com/imagine）引擎：把老 grok_pipe 的浏览器操作包成 Engine 接口。

调度器只认 base.Engine，不认 grok_pipe。老脚本一个字节不改，通过
paths.add_legacy_to_sys_path() 后 import 来用它的函数。

端口一律从 self.account['port'] 来，不用 grok_pipe 里那个 CDP_URL 全局。
状态只写 Orante 的 manifest；老台账 Grok台账.json 碰都不碰。
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import time
from pathlib import Path

from .. import paths
from ..errors import OranteError, NeedRedownload
from . import chrome
from .base import Engine, BlockedImage, EngineUnavailable

paths.add_legacy_to_sys_path()
import grok_pipe  # type: ignore  # noqa: E402
import grok_batch  # type: ignore  # noqa: E402

IMAGINE_URL = "https://grok.com/imagine"

_MIME_UP = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
            ".bmp": "image/bmp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# 传图被拒：老 probe 的 IS_FAILED_JS 认 moderat；prepare 本身没有独立分支，
# 所以失败文案和页面正文两边都扫。
# 真正的「被拒」长什么样：要有一个**动作被拒绝**的说法，光出现 policy / minor
# 这种词不算——Grok 页脚就有「Content Policy」链接，页面里也可能出现别的文案。
# 0907 是 Cookie 条里的 "Cookie Policy" 误判，0908 是系统提示词里的 "minor" 误判，
# 同一个坑踩两次，所以这次收紧成「拒绝动词 + 违规名词」的组合。
_BLOCK_VERB = (r"(?:can(?:'|’)?t|cannot|unable to|won(?:'|’)?t|not able to|"
               r"refus\w*|declin\w*|rejected|denied|blocked|removed|"
               r"violat\w*|not allowed|isn(?:'|’)?t allowed|"
               r"无法|不能|已被拒|被拒绝|不符合|违反|不予)")
_BLOCK_NOUN = (r"(?:polic\w+|guideline|moderat\w*|content|image|photo|upload|request|"
               r"generate|规范|政策|准则|内容|图片|上传|生成|要求)")
_BLOCK_RE = re.compile(
    # ① 拒绝动词和违规名词离得很近（60 字以内）——两种语序都认
    rf"{_BLOCK_VERB}[^\n]{{0,60}}?{_BLOCK_NOUN}|{_BLOCK_NOUN}[^\n]{{0,60}}?{_BLOCK_VERB}|"
    # ② 几个不会有歧义的固定说法
    r"违规内容|内容违规|图片违规|涉嫌违规|content\s+moderation|"
    r"flagged\s+as|moderation\s+(?:failed|error)",
    re.I,
)

# 系统提示词、法务条款那类「本来就长这样」的文本，命中了也不算被拒。
# 它们是页面自带的，不是对这一次请求的答复。
_BLOCK_FALSE = re.compile(
    r"you are grok|- you are|system prompt|roleplay as a minor|"
    r"terms of service|privacy policy|cookie polic|"
    r"用户协议|隐私政策|服务条款",
    re.I,
)

# 登录墙：URL 或可见按钮。accounts.x.com 是 Grok 走 X 登录时会被踢到的地方。
_LOGIN_URL_RE = re.compile(
    r"accounts\.x\.com|twitter\.com/i/flow/login|x\.com/i/flow/login|"
    r"grok\.com/(sign-in|login)",
    re.I,
)
_LOGIN_BTN_JS = r"""() => {
  const out = [];
  for (const b of document.querySelectorAll('a, button, [role=button]')) {
    const t = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || ''))
                .replace(/\s+/g, ' ').trim();
    if (!/^(sign in|log in|登录|登入|sign up|accedi|iniciar sesión|se connecter)$/i.test(t)
        && !/sign in to|log in to grok|sign in with/i.test(t)) continue;
    const r = b.getBoundingClientRect();
    if (r.width > 8 && r.height > 8) out.push(t.slice(0, 40));
  }
  return out;
}"""

# 刚提交的头 20 秒不探测：老 _run_wave 的规矩。Grok 出片要一两分钟，
# 这会儿页面上只可能是宣传片 / 上一条残留，探了会误判。这不是新加的保险等待，
# 是跳过 probe，不 sleep。
_PROBE_GRACE = 20

# 详情页打得开、0 个 video、也不在生成：连续这么多轮就认 post 已死。
# POLL_EVERY=6 → 20 轮大约 2 分钟，远短于 GEN_TIMEOUT 的 15 分钟。
EMPTY_STOP = 20

# 网络层 / performance 筛视频地址。content-type 以 video/ 开头，
# 或 URL 含 assets.grok.com + /generated/。宣传片 data.x.ai 一律丢掉。
# 53：带体积。transferSize / encodedBodySize 给体积闸；旧测试仍可返回纯 URL 字符串。
_PERF_VID_JS = r"""() => (performance.getEntriesByType('resource') || [])
  .map(e => ({url: e.name || '', size: (e.transferSize || e.encodedBodySize || 0)}))
  .filter(x => x.url)"""

# 15 秒片子不可能小于这个。99 KB 是海报/预览。
NET_MIN_BYTES = 300 * 1024

# 下载按钮：aria-label / title 含 download|scarica|下载。
_DL_BTN_JS = r"""() => {
  const re = /download|scarica|下载/i;
  const mark = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    el.setAttribute('data-orante-dl', '1');
    return true;
  };
  for (const el of document.querySelectorAll('button, a, [role=button], [aria-label], [title]')) {
    const lab = ((el.getAttribute('aria-label') || '') + ' '
                 + (el.getAttribute('title') || '') + ' '
                 + (el.innerText || '')).trim();
    if (re.test(lab) && mark(el)) return 'label';
  }
  return '';
}"""
_DL_BTN_SEL = "[data-orante-dl='1']"


def dur_label(duration) -> str:
    """把 Engine 的 duration（秒，或 '6s'）收成 grok_batch.set_params 要的 '6s'/'10s'/'15s'。"""
    s = str(duration or "").strip().lower()
    if s.endswith("s") and s[:-1].isdigit():
        n = int(s[:-1])
    else:
        try:
            n = int(duration)
        except (TypeError, ValueError):
            n = 6
    if n in (6, 10, 15):
        return f"{n}s"
    # 4/8 是 Flow 档。收到就靠最近的 Grok 档，不发明新档位。
    if n <= 6:
        return "6s"
    if n <= 10:
        return "10s"
    return "15s"


def _is_login_url(url: str) -> bool:
    return bool(_LOGIN_URL_RE.search(url or ""))


# 页面正文（去掉 Cookie 同意条那一块）。OneTrust 的横幅里有
# "Cookie Policy / Privacy Policy / Terms of Service"，_BLOCK_RE 的 polic 会命中，
# 于是「没出图」被误判成「这张图被审了」。0907 T0013 p04 就是这么被标 blocked 的。
# 页面上「人真的看得见」的文字。
#
# 0908 血的教训：这里原来是 body.cloneNode(true) 再取 clone.innerText。
# 克隆出来的节点没有被渲染，浏览器就把 innerText 退化成 textContent——
# 于是 <script> 里的 JSON 全被读了进来，其中包含 Grok 自己的系统提示词
# （里面有 "decline any requests to roleplay as a minor…"）。
# _BLOCK_RE 命中 minor，每一次铺页都被判成「传图被拒」，视频一条都没提交过，
# 还连累好图被自动换掉。所以这里改成老老实实走文本节点，
# 跳过 script/style/模板、跳过隐藏元素、跳过 Cookie 条。
_PAGE_TEXT_JS = r"""() => {
  const body = document.body;
  if (!body) return '';
  const skipSel = ['#onetrust-consent-sdk', '[id*="onetrust"]', '[class*="onetrust"]',
                   '.ot-sdk-container', '.optanon-alert-box-wrapper', '#CybotCookiebotDialog',
                   '[id*="cookie"]', '[class*="cookie-banner"]'];
  const skip = [];
  for (const sel of skipSel) {
    try { document.querySelectorAll(sel).forEach(el => skip.push(el)); } catch (e) {}
  }
  const BAD = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'IFRAME',
                       'HEAD', 'META', 'LINK', 'CANVAS', 'OBJECT']);
  const out = [];
  const walk = (el, depth) => {
    if (depth > 60 || out.length > 4000) return;
    for (const node of el.childNodes) {
      if (node.nodeType === 3) {
        const t = (node.nodeValue || '').trim();
        if (t) out.push(t);
        continue;
      }
      if (node.nodeType !== 1) continue;
      if (BAD.has(node.tagName)) continue;
      if (node.getAttribute && node.getAttribute('aria-hidden') === 'true') continue;
      let hidden = false;
      try {
        const st = getComputedStyle(node);
        hidden = st && (st.display === 'none' || st.visibility === 'hidden');
      } catch (e) {}
      if (hidden) continue;
      if (skip.some(sk => sk === node || sk.contains(node))) continue;
      walk(node, depth + 1);
    }
  };
  walk(body, 0);
  return out.join('\n');
}"""

# Cookie 同意条：按最保守的选项点（拒绝非必要），点不到就算了
_CONSENT_JS = r"""() => {
  const sels = ['#onetrust-reject-all-handler', '.ot-pc-refuse-all-handler',
                '.onetrust-close-btn-handler'];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (el && el.offsetParent !== null) { el.click(); return s; }
  }
  const RE = /^(全部拒绝|拒绝全部|拒绝所有|Reject all|Refuse all|Rifiuta tutto|Rifiuta)$/i;
  for (const b of document.querySelectorAll('button, a[role=button], [role=button]')) {
    const t = ((b.innerText || '') + '').trim();
    if (RE.test(t) && b.offsetParent !== null) { b.click(); return t; }
  }
  return '';
}"""


def _hit(text: str, pad_before: int = 0) -> str:
    """在一段文字里找「被拒」的证据。找到返回那一小段，没有返回空。

    命中处前后各留一点上下文，再拿 _BLOCK_FALSE 复核一遍：如果这一段其实是
    系统提示词 / 服务条款那类页面自带文本，就当没看见。
    """
    if not text:
        return ""
    m = _BLOCK_RE.search(text)
    if not m:
        return ""
    i = m.start()
    seg = text[max(0, i - pad_before): i + 120]
    if _BLOCK_FALSE.search(seg):
        return ""
    return grok_pipe.norm_text(seg)[:120]


def _blocked_reason(msg: str, page=None) -> str:
    """异常文案或页面**可见正文**里有没有拒图信号。有就返回截出来的原因。"""
    why = _hit(msg or "")
    if why:
        return why
    if page is None:
        return ""
    try:
        t = page.evaluate(_PAGE_TEXT_JS) or ""
    except Exception:
        return ""
    return _hit(t, pad_before=40)


class GrokEngine(Engine):
    name = "grok"
    can_gen_images = True

    def __init__(self, account, log):
        super().__init__(account, log)
        self.port = int(account["port"])
        self.max_conc = int(account.get("max_concurrent") or 4)
        self._br = None
        self._ctx = None
        self.page = None                 # 登录探测用的 imagine 页，不承担出片
        self._pages: dict[str, object] = {}   # postId → playwright page
        self._conv: dict[str, str] = {}       # postId → conversation
        self._urls: dict[str, str] = {}       # postId → 视频 URL
        self._src: dict[str, str] = {}        # postId → 视频来源（post页/标签页）
        self._unique: dict[str, bool] = {}    # postId → 闸1 唯一 strong
        self._rejected: dict[str, set] = {}   # postId → 已作废的 URL（落盘自证打回）
        self._rows: dict[str, dict] = {}      # postId → 给 download_one 的 row
        self._active: set[str] = set()
        self._submitted_at: dict[str, float] = {}
        self._reloaded: set[str] = set()
        self._bounced: dict[str, int] = {}    # postId → 连续被踢回首页的次数
        self._empty_rounds: dict[str, int] = {}  # postId → 详情页连续空（0 video）轮数
        self._first_bad: dict[str, str] = {}  # postId → 第一次不对劲的 ISO 时间
        self._health_seg: dict[str, dict] = {}  # postId → generate 那边的 seg 引用，写 gen_health
        self._hold_nav: set[str] = set()      # 这一轮被踢过，下一轮不许 goto
        self._nav_logged: set[str] = set()    # 第一次导航的完整地址只打一次
        self._offpage_logged: set[str] = set()  # 「不在详情页」每段只打一次
        self._netvid: dict[str, list] = {}    # postId → 网络层看到的视频 URL（去重保序）
        self._netsize: dict[str, dict] = {}   # postId → url → 字节（Content-Length / transferSize）
        self._size_skip_logged: set = set()   # (postId, url) 体积闸跳过只打一次
        self._dlfile: dict[str, str] = {}     # postId → 下载按钮落到的临时文件
        self._gate_n: dict[str, int] = {}     # postId → 连续「有 video 过不了闸」的轮数
        self._alias: dict[str, str] = {}      # 提交时 postId → Grok 改址后的视频 postId（同一会话）

    # ---------------- 连接 ----------------
    def connect(self) -> None:
        from .. import accounts
        prof = accounts._profile_dir(self.account)
        try:
            chrome.launch(self.port, prof, IMAGINE_URL)
        except OranteError:
            raise
        except Exception as e:  # noqa: BLE001
            raise EngineUnavailable(f"Grok 的 Chrome 没起来（端口 {self.port}）：{e}") from e
        try:
            self._br = chrome.Browser(self.port).__enter__()
        except ModuleNotFoundError as e:
            raise OranteError(
                "这个 Python 环境没装 playwright，连不上 Chrome",
                "用装过 playwright 的环境跑（流水线 .venv），或在本 venv 里 pip install playwright。",
            ) from e
        except Exception as e:  # noqa: BLE001
            raise EngineUnavailable(
                f"连不上 Grok 的 Chrome（端口 {self.port}）：{e}"
            ) from e
        self._ctx = self._br.ctx
        self.page = self._ensure_imagine()
        grok_batch.pass_tos_gate(self.page)
        self._dismiss_consent(self.page)     # 新 profile 第一次会弹 Cookie 条，盖住 composer
        # 老 prepare_tab 等这两个选择器；已就绪会立刻返回，不是新加的保险等待
        try:
            u = self.page.url or ""
        except Exception:
            u = ""
        if "/imagine/post/" not in u and not _is_login_url(u):
            try:
                self.page.wait_for_selector("input[type=file]", state="attached", timeout=30000)
                self.page.wait_for_selector("div.tiptap", state="visible", timeout=30000)
            except Exception:
                pass
        ok, url, note = self._login_state(self.page)
        if not ok:
            raise EngineUnavailable(f"Grok 还没登录：{note}。到弹出的 Chrome 里用 X 账号登录一次。")
        chrome.Browser.keep_awake(self.page)
        self.log(f"Grok 已连上（{self.account['name']}，端口 {self.port}，{url[:80]}）")

    def close(self) -> None:
        # 只断 Playwright，不关用户的 Chrome。出片标签页在 download 里关。
        if self._br:
            try:
                self._br.__exit__(None, None, None)
            except Exception:
                pass
            self._br = None
            self._ctx = None
            self.page = None

    def health(self) -> dict:
        try:
            page = self._ensure_imagine()
            ok, url, note = self._login_state(page)
            return {"ok": ok, "logged_in": ok, "url": url, "note": note}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "logged_in": False, "url": "", "note": str(e)}

    def _ensure_imagine(self):
        """找到一个 grok.com 标签；没有就新开 imagine。"""
        try:
            if self.page and not self.page.is_closed():
                u = self.page.url or ""
                if "grok.com" in u and not _is_login_url(u):
                    return self.page
        except Exception:
            pass
        best = None
        for pg in list(self._ctx.pages):
            try:
                u = pg.url or ""
            except Exception:
                continue
            if _is_login_url(u):
                continue
            if "grok.com/imagine" in u:
                best = pg
                if "/imagine" in u and "post/" not in u:
                    break
            elif "grok.com" in u and best is None:
                best = pg
        if best is None:
            best = self._ctx.new_page()
            best.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
            grok_batch.pass_tos_gate(best)
            # 老 prepare_tab 等编辑器，这里同样等，不加额外保险秒数
            try:
                best.wait_for_selector("div.tiptap", state="visible", timeout=30000)
            except Exception:
                pass
        self.page = best
        chrome.Browser.keep_awake(best)
        return best

    def _login_state(self, page) -> tuple[bool, str, str]:
        """(logged_in, url, note)。参考 grok_batch.pass_tos_gate / verify_tab 的页面信号，
        加上 accounts.x.com 和登录按钮。"""
        try:
            for pg in list(self._ctx.pages):
                try:
                    u = pg.url or ""
                except Exception:
                    continue
                if "accounts.x.com" in u:
                    return False, u, "跳到了 accounts.x.com"
                if _is_login_url(u):
                    return False, u, "停在登录页"
        except Exception:
            pass
        url = ""
        try:
            url = page.url or ""
        except Exception as e:  # noqa: BLE001
            return False, "", f"读不到页面：{e}"
        if "accounts.x.com" in url or _is_login_url(url):
            return False, url, "停在登录页"
        # 编辑器在 = 已登录能铺页（verify_tab 认的就是 div.tiptap）
        try:
            if page.query_selector("div.tiptap") or page.query_selector("input[type=file]"):
                return True, url, "composer"
        except Exception:
            pass
        try:
            btns = page.evaluate(_LOGIN_BTN_JS) or []
        except Exception:
            btns = []
        if btns:
            return False, url, f"页面上有登录按钮（{btns[0]}）"
        if "grok.com" in url:
            return True, url, "grok.com"
        return False, url, "不在 grok.com"

    # ---------------- 出片 ----------------
    def capacity(self) -> int:
        return max(0, self.max_conc - len(self._active))

    def _make_row(self, seg: dict, image: Path, prompt: str, duration) -> dict:
        """现拼 grok_pipe.prepare / download_one 要的 row。键对齐 cmd_ledger。"""
        image = Path(image)
        s = int((seg or {}).get("s") or 1)
        label = (seg or {}).get("image") or f"s{s:03d}"
        return {
            "编号": label,
            "第几段": s,
            "共几段": 1,
            "图片": str(image.resolve()),
            "分段文案": grok_pipe.norm_text((seg or {}).get("text") or ""),
            "完整提示词": prompt,
            "时长": dur_label(duration),
            "postId": "",
            "会话Id": "",
            "视频URL": "",
            "视频来源": "",
        }

    def submit(self, seg: dict, image: Path, prompt: str, duration: int,
               last_image: Path | None = None) -> str:
        if self._ctx is None:
            raise EngineUnavailable("Grok 还没 connect")
        image = Path(image)
        if not image.is_file():
            raise OranteError(f"找不到起始图 {image}", "先到「图片」那一步把图下好。")
        row = self._make_row(seg, image, prompt, duration)
        if last_image:
            last_p = Path(last_image)
            if not last_p.is_file():
                raise OranteError(f"找不到尾帧图 {last_p}", "接片的目标锚点图要在盘上。")
            row["尾帧"] = str(last_p.resolve())
            # 探针 40：顺序写在 pipeline 常量里，grok_pipe.prepare 按这个挂图。
            try:
                from ..services.pipeline import GROK_BRIDGE_UPLOAD
                order = GROK_BRIDGE_UPLOAD
            except Exception:
                order = "start_then_end"
            row["接片上传顺序"] = order
            if isinstance(seg, dict) and str(seg.get("bridge_engine") or "") != "grok-single":
                seg["bridge_engine"] = "grok"
            self.log("  · 接片双图上传：%s" % (
                "终→起" if order == "end_then_start" else "起→终"))
        # 时长由 row['时长'] 带进 prepare → grok_batch.set_params，这里不再另点一次
        page = None
        try:
            page = grok_pipe.prepare(self._ctx, row, folder=str(image.parent))
        except Exception as e:  # noqa: BLE001
            why = _blocked_reason(str(e), page)
            if why:
                self.log(f"  ✗ 传图被拒：{why}")
                raise BlockedImage(why) from e
            self._raise_submit(e, "铺页")
        why = _blocked_reason("", page)
        if why:
            self.log(f"  ✗ 传图被拒：{why}")
            raise BlockedImage(why)
        chrome.Browser.keep_awake(page)
        try:
            how = grok_pipe.click_submit(
                page, timeout=100, guard=lambda: grok_pipe.ensure_intact(page, row))
        except Exception as e:  # noqa: BLE001
            why = _blocked_reason(str(e), page)
            if why:
                self.log(f"  ✗ 提交被拒：{why}")
                raise BlockedImage(why) from e
            self._raise_submit(e, "提交")
        post, conv = grok_pipe.capture_anchor(page)
        if not post:
            raise OranteError(
                "提交了但没抓到 postId",
                "看那个 Grok 标签页地址栏是不是变成了 /imagine/post/…",
            )
        row["postId"] = post
        row["会话Id"] = conv or ""
        self._pages[post] = page
        self._conv[post] = conv or ""
        self._rows[post] = row
        self._active.add(post)
        self._submitted_at[post] = time.time()
        self._attach_netvid(page, post)
        # 新 post：旧的被踢 / 空页证据作废
        self._clear_health(post)
        if isinstance(seg, dict):
            seg.pop("gen_health", None)
            prev = seg.setdefault("prev_posts", [])
            if isinstance(prev, list) and post not in prev:
                prev.append(post)
            self.attach_seg(post, seg)
        tag = f"s{(seg or {}).get('s', 0):03d}" if isinstance(seg, dict) else ""
        self.log(f"  ✓ 已提交 {tag} → post {post[:8]}…  {row['时长']}  方式 {how}")
        self.log(f"  · 抓到 postId {post}" + (f"  会话 {conv[:8]}…" if conv else "  （会话还没出现，poll 时再补）"))
        return post

    def find_orphan(self, conv: str) -> str:
        """找回「提交其实成功了、却被记成失败」的那条（2026-10-06 T0124）：
        在这个 Chrome 里找地址带 conversation=<conv> 的 post 标签页，返回 postId；找不到返回 ""。
        找到就把那一页记成这条的出片页（跟 submit 成功时一样），之后照常 poll / 下载。"""
        conv = str(conv or "").strip().lower()
        if not conv or self._ctx is None:
            return ""
        for pg in list(self._ctx.pages):
            try:
                u = pg.url or ""
            except Exception:  # noqa: BLE001
                continue
            if f"conversation={conv}" not in u.lower():
                continue
            m = grok_pipe.POST_RE.search(u)
            if not m:
                continue
            post = m.group(1)
            self._pages[post] = pg
            self._conv[post] = conv
            row = dict(self._rows.get(post) or {})
            row["postId"] = post
            row["会话Id"] = conv
            self._rows[post] = row
            self._attach_netvid(pg, post)
            return post
        return ""

    def _raise_submit(self, e: Exception, step: str) -> None:
        msg = str(e)
        if type(e).__name__ == "DurationError" or "时长" in msg:
            raise OranteError(f"{step}失败：时长没切过去（{msg}）",
                              "刷新那个 Grok 标签，确认 6s/10s/15s 按钮在。") from e
        raise OranteError(f"Grok {step}失败：{msg[:180]}",
                          "看弹出的 Chrome 里那一页现在长什么样。") from e

    def conv_of(self, gid: str) -> str:
        """submit 抓到的 conversation，给 generate.py 写进 seg['gen_conv']。"""
        return str(self._conv.get(gid) or "")

    def adopt(self, gid: str, seg: dict | None = None) -> None:
        """续跑接手：把 manifest 里的会话 / 图路径塞回内存，等待时间从现在起算。

        软件重启后 _pages/_conv 是空的。没有会话 Id 就导不回详情页。
        _submitted_at 必须是接手这一刻，不然 age = 现在 - 0 会立刻触发 3 分钟刷新。
        被踢 / 空页计数不能清零：证据在 seg['gen_health'] 里，跨 Job 还在。
        """
        seg = seg or {}
        conv = str(seg.get("gen_conv") or "")
        if conv:
            self._conv[gid] = conv
        row = dict(self._rows.get(gid) or {})
        row["postId"] = gid
        if conv:
            row["会话Id"] = conv
        s = seg.get("s")
        if s:
            try:
                row["第几段"] = int(s)
            except (TypeError, ValueError):
                row["第几段"] = s
        img = seg.get("image") or ""
        if img:
            row["图片"] = img
        tail = ""
        br = seg.get("bridge")
        if isinstance(br, dict):
            tail = br.get("tail_frame") or ""
        tail = tail or seg.get("尾帧") or ""
        if tail:
            row["尾帧"] = tail
        self._rows[gid] = row
        self._submitted_at[gid] = time.time()
        self._active.add(gid)
        gh = seg.get("gen_health") if isinstance(seg.get("gen_health"), dict) else {}
        self._bounced[gid] = int((gh or {}).get("bounced") or 0)
        self._empty_rounds[gid] = int((gh or {}).get("empty_rounds") or 0)
        first = str((gh or {}).get("first_bad_at") or "")
        if first:
            self._first_bad[gid] = first
        else:
            self._first_bad.pop(gid, None)
        self.attach_seg(gid, seg)
        # 过闸轮数 / 导航日志按这次 Job 重来；被踢计数不重来
        self._hold_nav.discard(gid)
        self._nav_logged.discard(gid)
        self._offpage_logged.discard(gid)
        self._gate_n[gid] = 0
        self._reloaded.discard(gid)
        bounced_n = self._bounced.get(gid, 0)
        empty_n = self._empty_rounds.get(gid, 0)
        if bounced_n or empty_n:
            bits = []
            if bounced_n:
                bits.append(f"之前已被踢 {bounced_n} 次")
            if empty_n:
                bits.append(f"详情页已空 {empty_n} 轮")
            self.log(f"  · 接手 {self._seg_tag(gid)}，{'，'.join(bits)}")

    def poll(self, gen_ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for gid in gen_ids:
            out[gid] = self._poll_one(gid)
        return out

    def _seg_tag(self, gid: str) -> str:
        row = self._rows.get(gid) or {}
        s = row.get("第几段")
        if s:
            try:
                return f"s{int(s):03d}"
            except (TypeError, ValueError):
                pass
        return gid[:8]

    def attach_seg(self, gid: str, seg: dict | None) -> None:
        """记住 generate 那边的 seg 引用，计数变化时写回 gen_health。"""
        if gid and isinstance(seg, dict):
            self._health_seg[gid] = seg

    def health_of(self, gid: str) -> dict | None:
        """当前 post 有多不对劲。全 0 则返回 None。"""
        b = int(self._bounced.get(gid) or 0)
        e = int(self._empty_rounds.get(gid) or 0)
        first = self._first_bad.get(gid) or ""
        if not b and not e and not first:
            return None
        h: dict = {}
        if b:
            h["bounced"] = b
        if e:
            h["empty_rounds"] = e
        if first:
            h["first_bad_at"] = first
        return h

    def _write_health(self, gid: str) -> None:
        """把内存计数写回 attach 过的 seg。自己不存盘，generate.save() 会带走。"""
        h = self.health_of(gid)
        seg = self._health_seg.get(gid)
        if not isinstance(seg, dict):
            return
        if h:
            seg["gen_health"] = h
        else:
            seg.pop("gen_health", None)

    def _clear_health(self, gid: str) -> None:
        """收到片子 / 换了新 post：整份 gen_health 丢掉。"""
        self._bounced.pop(gid, None)
        self._empty_rounds.pop(gid, None)
        self._first_bad.pop(gid, None)
        self._write_health(gid)

    def _touch_bad(self, gid: str) -> None:
        if not self._first_bad.get(gid):
            self._first_bad[gid] = time.strftime("%Y-%m-%dT%H:%M:%S")

    def _stamp(self, gid: str, result: dict) -> dict:
        # 键一定带上：None 表示已经恢复，generate 才敢把 seg 里的 gen_health 删掉。
        # 别的引擎不带这个键，generate 就不会误删 manifest 里续跑带过来的证据。
        result["gen_health"] = self.health_of(gid)
        return result

    def _note_bounce(self, gid: str, page=None, toast: str = "") -> int:
        """一次被踢 = 计数 +1，不管是 _page_of 还是 _poll_one 触发的导航。"""
        n = self._bounced.get(gid, 0) + 1
        self._bounced[gid] = n
        self._touch_bad(gid)
        self._write_health(gid)
        tag = self._seg_tag(gid)
        try:
            land = (page.url if page is not None else "") or ""
        except Exception:
            land = ""
        m_post = grok_pipe.POST_RE.search(land)
        if m_post:
            b_pid = m_post.group(1)
            seg = self._health_seg.get(gid)
            if isinstance(seg, dict) and b_pid:
                prev = seg.setdefault("prev_posts", [])
                if isinstance(prev, list) and b_pid not in prev:
                    prev.append(b_pid)
        why = toast or getattr(grok_pipe.goto_own_post, "last_toast", "") or "Post not found"
        self.log(f"  ↩ {tag} 被 Grok 踢回首页（第 {n} 次）：{why}，地址 {land}")
        return n

    def _bounce_fail(self, gid: str, toast: str = "") -> dict:
        self._active.discard(gid)
        self._close_tab(gid)
        return self._stamp(gid, {
            "state": "failed",
            "error": "Grok 找不到这个 post（分页可能关错了，片子没下到）",
            "raw": toast,
        })

    def _note_alias(self, gid: str, new_id: str) -> None:
        """Grok 把地址改成视频自己的 postId（同一会话）：记下来，之后按新 id 认页、收片。"""
        if not gid or not new_id or new_id.lower() == gid.lower():
            return
        if (self._alias.get(gid) or "").lower() == new_id.lower():
            return
        self._alias[gid] = new_id
        row = self._rows.get(gid)
        if row is not None:
            row["视频postId"] = new_id
        seg = self._health_seg.get(gid)
        if isinstance(seg, dict):
            seg["alias_id"] = new_id
            prev = seg.setdefault("prev_posts", [])
            if isinstance(prev, list) and new_id not in prev:
                prev.append(new_id)
        self.log(f"  · {self._seg_tag(gid)} Grok 把地址换成了视频自己的 post {new_id[:8]}…（同一会话，不是被踢），按新地址收片")

    def _poll_one(self, gid: str) -> dict:
        age = time.time() - self._submitted_at.get(gid, 0)
        if 0 < age < _PROBE_GRACE:
            return self._stamp(gid, {"state": "pending", "raw": f"grace {int(age)}s"})
        page = self._page_of(gid)
        # _page_of 自己导航被踢也计进 _bounced；满 2 次就按 50 号判失败
        if self._bounced.get(gid, 0) >= 2:
            toast = getattr(grok_pipe.goto_own_post, "last_toast", "") or ""
            return self._bounce_fail(gid, toast)
        if page is None:
            self._active.discard(gid)
            err = "标签页丢了，又没有会话Id，导不回详情页"
            self.log(f"  ✗ {gid[:8]} {err}")
            return {"state": "failed", "error": err}
        try:
            page.bring_to_front()
            page.wait_for_timeout(500)
        except Exception:
            pass
        tag = self._seg_tag(gid)
        conv = self._conv.get(gid, "")
        # 锚点自愈：提交时 conversation 经常还没出现，从当前地址补。
        # 只许在「地址里已经是本段 postId」时抠，免得首页/别人的 post 把会话改乱。
        if gid and not conv:
            try:
                u2 = page.url or ""
            except Exception:
                u2 = ""
            if gid.lower() in u2.lower():
                mc = re.search(r"conversation=([0-9a-fA-F\-]+)", u2)
                if mc:
                    conv = mc.group(1)
                    self._conv[gid] = conv
                    row = self._rows.get(gid)
                    if row is not None:
                        row["会话Id"] = conv
                    self.log(f"  + {tag} 补到会话Id {conv[:8]}…")
        hold = gid in self._hold_nav
        if hold:
            self._hold_nav.discard(gid)
        on_post = False
        if not hold:
            # 第 1 次被踢之后、第 2 次导航之前：会话对不上就改回提交时那个
            if self._bounced.get(gid, 0) >= 1:
                orig = str((self._rows.get(gid) or {}).get("会话Id") or "")
                cur = str(self._conv.get(gid) or conv or "")
                if orig and orig != cur:
                    self.log(
                        f"  ↩ {tag} 会话对不上：提交时 {orig} / 自愈后 {cur}，"
                        f"改回提交时的再试")
                    conv = orig
                    self._conv[gid] = orig
            if gid not in self._nav_logged:
                self._nav_logged.add(gid)
                self.log(f"  → {tag} 导航 {grok_pipe.post_url(gid, conv)}")
            grok_pipe.goto_own_post.last_toast = ""
            nav = grok_pipe.goto_own_post(page, gid, conv)
            toast = getattr(grok_pipe.goto_own_post, "last_toast", "") or ""
            if nav == "on_post":
                self._note_alias(gid, getattr(grok_pipe.goto_own_post, "redirected", "") or "")
                self._bounced[gid] = 0
                if not self._empty_rounds.get(gid):
                    self._first_bad.pop(gid, None)
                self._write_health(gid)
                on_post = True
            elif nav == "bounced":
                n = self._note_bounce(gid, page, toast)
                if n >= 2:
                    return self._bounce_fail(gid, toast)
                # 下一轮原地不动，再下一轮才允许重新导航
                self._hold_nav.add(gid)
                on_post = False
            else:
                on_post = False
        if on_post:
            grok_pipe.nudge_to_video(page)
        seen: list[str] = []
        probe_id = self._alias.get(gid) or gid
        try:
            probed = grok_pipe.probe(page, probe_id, seen, on_post, logfn=self.log)
            st, info = probed[0], probed[1]
            detail = probed[2] if len(probed) > 2 else {}
        except Exception as e:  # noqa: BLE001
            return self._stamp(gid, {"state": "pending", "raw": f"probe:{e}"[:120]})
        if not isinstance(detail, dict):
            detail = {}
        if st == "不在详情页":
            if gid not in self._offpage_logged:
                self._offpage_logged.add(gid)
                try:
                    u = page.url or ""
                except Exception:
                    u = ""
                self.log(f"  · {tag} 不在详情页（{u}），本轮不数首页视频")
            return self._stamp(gid, {"state": "pending", "raw": "不在详情页"})
        if st == grok_pipe.S_READY:
            got = self._mark_ready(gid, info or "", "post页" if on_post else "标签页")
            if got.get("state") == "done":
                return got
            # 落盘自证作废过这个地址：不能静默 pending，下面当「没过闸」走
            st = grok_pipe.S_SUBMITTED
        if st == grok_pipe.S_FAILED:
            err = info or "页面报失败"
            self._active.discard(gid)
            self.log(f"  ✗ {gid[:8]} 生成失败：{err}")
            return self._stamp(gid, {"state": "failed", "error": err, "raw": err})

        # strong 为空或全是 blob: / 已作废 → 网络层 / performance 兜底
        rejected = self._rejected.get(gid) or set()
        strong = [u for u in (detail.get("strong") or []) if u]
        strong_http = [u for u in strong
                       if not str(u).lower().startswith("blob:") and u not in rejected]
        net_cands = self._net_candidates(gid, page, tag)
        if not strong_http and net_cands:
            picked = self._pick_net_cand(gid, net_cands, tag)
            if picked:
                if picked in rejected:
                    return {"state": "pending", "raw": "rejected-url"}
                return self._mark_ready(gid, picked, "网络层")

        videos = list(detail.get("videos") or [])
        n_vid = len(videos)
        generating = False
        try:
            generating = bool(page.evaluate(grok_pipe.IS_GENERATING_JS))
        except Exception:
            generating = detail.get("why") == "生成中"
        got_url = bool(self._urls.get(gid))
        # 洞 1：详情页开着、0 个 video、也不在生成 → 不能静默等到 15 分钟
        if (on_post and st not in (grok_pipe.S_READY, grok_pipe.S_FAILED)
                and n_vid == 0 and not generating and not got_url):
            n_empty = self._empty_rounds.get(gid, 0) + 1
            self._empty_rounds[gid] = n_empty
            self._touch_bad(gid)
            self._write_health(gid)
            if n_empty % 5 == 0 and n_empty < EMPTY_STOP:
                self.log(f"  · {tag} 详情页开着，0 个 video，不在生成（第 {n_empty} 轮）")
            if n_empty >= EMPTY_STOP:
                err = "post 打得开但里面没有视频（片子没下到）"
                self._active.discard(gid)
                self._close_tab(gid)
                return self._stamp(gid, {"state": "failed", "error": err})
        elif n_vid or generating or got_url:
            self._empty_rounds[gid] = 0
            if not self._bounced.get(gid):
                self._first_bad.pop(gid, None)
            self._write_health(gid)

        n_gate = n_vid or len(strong)
        if n_gate:
            n = self._gate_n.get(gid, 0) + 1
            self._gate_n[gid] = n
            # 不许静默：每段每 5 轮打一条（0 个 video 走上面那条空页日志）
            if n % 5 == 0:
                bits = self._src_bits(videos, strong)
                self.log(f"  · {tag} 看见 {n_gate} 个 video，但没有一个过闸：{' / '.join(bits)}")
            # 下载按钮兜底：可播放的 <video> 连续 5 轮还拿不到地址
            playable = [v for v in videos
                        if int(v.get("readyState") or 0) >= 2
                        and float(v.get("duration") or 0) > 0]
            if n >= 5 and n % 5 == 0 and playable and not strong_http:
                if self._try_download_btn(page, gid, tag):
                    self._clear_health(gid)
                    return {"state": "done", "url": "", "raw": "下载按钮",
                            "unique_strong": True}
        else:
            self._gate_n[gid] = 0

        # 已提交 / 未知 = 还在生成。3 分钟刷一次（老 _run_wave 的自救，每段一次）
        if age > 180 and gid not in self._reloaded:
            self._reloaded.add(gid)
            self.log(f"  ↻ {gid[:8]} 等太久，刷新一次页面再看")
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
        return self._stamp(gid, {"state": "pending", "raw": st or "未知"})

    def _mark_ready(self, gid: str, url: str, src: str) -> dict:
        """闸1 / 网络层拿到地址 → done。落盘自证作废过的地址继续等。"""
        if url and url in (self._rejected.get(gid) or ()):
            return {"state": "pending", "raw": "rejected-url"}
        self._urls[gid] = url
        self._src[gid] = src
        self._unique[gid] = True
        self._gate_n[gid] = 0
        self._clear_health(gid)
        row = self._rows.get(gid)
        if row is not None:
            row["视频URL"] = url
            row["视频来源"] = src
            row["unique_strong"] = True
        self._active.discard(gid)
        self.log(f"  ✓ 出片 {gid[:8]}…  {src}  唯一strong")
        return {"state": "done", "url": url, "raw": src, "unique_strong": True}

    @staticmethod
    def _is_net_video(url: str, content_type: str = "") -> bool:
        """网络层候选：video/* 或 assets.grok.com + /generated/。宣传片不要。"""
        u = (url or "").lower()
        if not u.startswith("http"):
            return False
        if "data.x.ai" in u:
            return False
        ct = (content_type or "").lower()
        if ct.startswith("video/"):
            return True
        if "assets.grok.com" in u and "/generated/" in u:
            return True
        return False

    def _attach_netvid(self, page, post_id: str) -> None:
        """在页上挂 response 监听，把视频地址记到 self._netvid[postId]。"""
        if page is None or not post_id:
            return
        if getattr(page, "_orante_netvid_post", None) == post_id:
            return
        try:
            page._orante_netvid_post = post_id
        except Exception:
            pass

        def on_resp(resp):
            try:
                u = getattr(resp, "url", "") or ""
                ct = ""
                size = 0
                try:
                    hdrs = getattr(resp, "headers", None) or {}
                    if callable(hdrs):
                        hdrs = hdrs() or {}
                    h = {}
                    try:
                        for k, v in (hdrs.items() if hasattr(hdrs, "items") else []):
                            h[str(k).lower()] = v
                    except Exception:
                        h = dict(hdrs) if isinstance(hdrs, dict) else {}
                    ct = h.get("content-type") or ""
                    cl = h.get("content-length") or ""
                    if cl:
                        size = int(str(cl).split(",")[0].strip() or 0)
                except Exception:
                    ct = ct or ""
                if self._is_net_video(u, ct):
                    lst = self._netvid.setdefault(post_id, [])
                    if u not in lst:
                        lst.append(u)
                    if size > 0:
                        self._netsize.setdefault(post_id, {})[u] = size
            except Exception:
                pass

        try:
            page.on("response", on_resp)
        except Exception:
            pass

    def _collect_net_cands(self, gid: str, page) -> list[str]:
        """网络层监听 + 页内 performance，去重保序。顺手记下体积。"""
        out: list[str] = []
        seen: set[str] = set()
        for u in list(self._netvid.get(gid) or []):
            if u and u not in seen and self._is_net_video(u):
                seen.add(u)
                out.append(u)
        try:
            perf = page.evaluate(_PERF_VID_JS) or []
        except Exception:
            perf = []
        for item in perf:
            u, sz = "", 0
            if isinstance(item, dict):
                u = item.get("url") or item.get("name") or ""
                try:
                    sz = int(item.get("size") or item.get("transferSize")
                             or item.get("encodedBodySize") or 0)
                except (TypeError, ValueError):
                    sz = 0
            else:
                u = str(item or "")
            if u and sz > 0:
                self._netsize.setdefault(gid, {})[u] = sz
            if u and u not in seen and self._is_net_video(u):
                seen.add(u)
                out.append(u)
        return out

    def _net_candidates(self, gid: str, page, tag: str = "") -> list[str]:
        """过体积闸的网络层候选。小于 300 KB 当海报/预览，不当候选。"""
        rejected = self._rejected.get(gid) or set()
        tag = tag or self._seg_tag(gid)
        out: list[str] = []
        for u in self._collect_net_cands(gid, page):
            if not u or u in rejected:
                continue
            sz = int((self._netsize.get(gid) or {}).get(u) or 0)
            if 0 < sz < NET_MIN_BYTES:
                key = (gid, u)
                if key not in self._size_skip_logged:
                    self._size_skip_logged.add(key)
                    kb = max(1, int(round(sz / 1024)))
                    self.log(f"  · {tag} 网络层候选 {u[:60]} 只有 {kb} KB，跳过")
                continue
            out.append(u)
        return out

    def _pick_net_cand(self, gid: str, cands: list[str], tag: str) -> str:
        """恰好 1 个 → 当 unique_strong；多个用 url_matches_post 筛。已作废的丢掉。"""
        rejected = self._rejected.get(gid) or set()
        cands = [u for u in cands if u and u not in rejected]
        if not cands:
            return ""
        if len(cands) == 1:
            return cands[0]
        alias = self._alias.get(gid) or ""
        matched = [u for u in cands if grok_pipe.url_matches_post(u, gid)
                   or (alias and grok_pipe.url_matches_post(u, alias))]
        if len(matched) == 1:
            return matched[0]
        self.log(f"  · {tag} 网络层有 {len(cands)} 个候选，判不出哪个是本段")
        return ""

    @staticmethod
    def _src_bits(videos: list, strong: list) -> list[str]:
        """日志里每个 src 前 80 字 / blob: / 空。"""
        srcs = []
        for v in videos or []:
            if isinstance(v, dict):
                srcs.append(v.get("src") or v.get("currentSrc") or "")
            else:
                srcs.append(str(v or ""))
        if not srcs:
            srcs = list(strong or [])
        bits = []
        for s in srcs:
            if not s:
                bits.append("空")
            elif str(s).startswith("blob:"):
                bits.append("blob:")
            else:
                bits.append(str(s)[:80])
        return bits or ["空"]

    def _find_download_btn(self, page):
        """aria-label / title 含 download|scarica|下载。找不到返回 None。"""
        try:
            hit = page.evaluate(_DL_BTN_JS) or ""
        except Exception:
            hit = ""
        if hit:
            try:
                loc = page.locator(_DL_BTN_SEL)
                if loc.count() > 0:
                    return loc.first
            except Exception:
                pass
        # Playwright 选择器再扫一遍（FakePage 也走这条）
        try:
            loc = page.locator(
                "[aria-label*='ownload' i], [aria-label*='Scarica' i], "
                "[aria-label*='scarica' i], [aria-label*='下载'], "
                "[title*='ownload' i], [title*='Scarica' i], "
                "[title*='scarica' i], [title*='下载']")
            if loc.count() > 0:
                return loc.first
        except Exception:
            pass
        return None

    def _try_download_btn(self, page, gid: str, tag: str) -> bool:
        """点详情页右侧下载图标，文件落到临时路径。失败返回 False。"""
        btn = self._find_download_btn(page)
        if btn is None:
            self.log(f"  · {tag} 找不到下载按钮，放弃这路")
            return False
        tmp_dir = paths.DATA / "tmp"
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        dest = str(tmp_dir / f"grok-dl-{gid[:8]}.mp4")
        try:
            with page.expect_download(timeout=60000) as di:
                btn.click()
            dl = di.value
            dl.save_as(dest)
        except Exception as e:  # noqa: BLE001
            self.log(f"  · {tag} 点下载按钮失败：{str(e)[:120]}")
            return False
        if not dest or not os.path.isfile(dest) or os.path.getsize(dest) < 100:
            self.log(f"  · {tag} 下载按钮没落下文件")
            return False
        self._dlfile[gid] = dest
        self._src[gid] = "下载按钮"
        self._unique[gid] = True
        self._gate_n[gid] = 0
        row = self._rows.get(gid)
        if row is not None:
            row["视频URL"] = ""
            row["视频来源"] = "下载按钮"
            row["unique_strong"] = True
        self._active.discard(gid)
        self.log(f"  ✓ 出片 {gid[:8]}…  下载按钮")
        return True

    def _page_of(self, post: str):
        """手里的标签页还在就用；丢了就新开一页 goto_own_post。"""
        pg = self._pages.get(post)
        try:
            if pg is not None and not pg.is_closed():
                self._attach_netvid(pg, post)
                return pg
        except Exception:
            pg = None
        conv = self._conv.get(post, "")
        if self._ctx is None:
            return None
        if not conv:
            return None
        try:
            pg = self._ctx.new_page()
            # 先挂监听再 goto，深链加载时的视频响应才收得到
            self._attach_netvid(pg, post)
            nav = grok_pipe.goto_own_post(pg, post, conv)
        except Exception:
            return None
        # 被踢也把页留着，别当场扔掉再重开死循环。这次被踢必须计进 _bounced，
        # 不能再让 _poll_one 以为「还没被踢过」。
        if nav != "on_post" and nav != "bounced":
            try:
                pg.close()
            except Exception:
                pass
            return None
        self._pages[post] = pg
        chrome.Browser.keep_awake(pg)
        self.log(f"  · {post[:8]} 标签页丢了，已按会话重开")
        if nav == "bounced":
            toast = getattr(grok_pipe.goto_own_post, "last_toast", "") or ""
            n = self._note_bounce(post, pg, toast)
            self._hold_nav.add(post)
            if n >= 2:
                self._close_tab(post)
                return None
        elif nav == "on_post":
            self._note_alias(post, getattr(grok_pipe.goto_own_post, "redirected", "") or "")
            self._bounced[post] = 0
            if not self._empty_rounds.get(post):
                self._first_bad.pop(post, None)
            self._write_health(post)
        return pg

    def download(self, gen_id: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        row = dict(self._rows.get(gen_id) or {})
        row["postId"] = gen_id
        row["视频URL"] = self._urls.get(gen_id) or row.get("视频URL") or ""
        row["视频来源"] = self._src.get(gen_id) or row.get("视频来源") or ""
        unique = bool(self._unique.get(gen_id) or row.get("unique_strong"))
        row["unique_strong"] = unique
        local = self._dlfile.get(gen_id) or ""
        if (row.get("视频来源") == "下载按钮" or (local and not row.get("视频URL"))) \
                and local and os.path.isfile(local):
            shutil.copy2(local, dest)
            self.log(f"  ⬇ {dest.name}  下载按钮  {dest.stat().st_size // 1024} KB")
            self._close_tab(gen_id)
            return dest
        if not row.get("视频URL"):
            raise OranteError("成片还没有下载地址", "先等 poll 返回 done。")
        if self._ctx is None:
            raise EngineUnavailable("Grok 还没 connect")
        try:
            n = grok_pipe.download_one(
                self._ctx, row, str(dest), unique_strong=unique)
        except grok_pipe.NeedRedownload as e:
            url = row.get("视频URL") or self._urls.get(gen_id) or ""
            self._urls.pop(gen_id, None)
            self._unique.pop(gen_id, None)
            self._active.add(gen_id)
            # is_real_mp4 不过：这个地址作废，继续等真的。别当成本段失败。
            if url and "不是合法 mp4" in str(e):
                self._rejected.setdefault(gen_id, set()).add(url)
            raise NeedRedownload(
                str(e), "地址过不了闸，会重新取，不算失败。") from e
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "不是合法 mp4" in msg:
                url = row.get("视频URL") or self._urls.get(gen_id) or ""
                if url:
                    self._rejected.setdefault(gen_id, set()).add(url)
                self._urls.pop(gen_id, None)
                self._unique.pop(gen_id, None)
                self._active.add(gen_id)
                raise NeedRedownload(
                    msg, "地址过不了闸，会重新取，不算失败。") from e
            # 前缀留给 generate._clip_dl_error，这里不再加「下载失败：」
            raise OranteError(msg, "看那个 Grok 标签页是不是还开着。") from e
        self.log(f"  ⬇ {dest.name}  {int(n) // 1024} KB")
        self._close_tab(gen_id)
        return dest

    def download_url(self, url: str, dest: Path, post_id: str = "") -> bool:
        """从已有 asset_url 直接补下缺片（在专用线程里调用 ctx.request.get）。"""
        if not url or self._ctx is None:
            return False
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        row = {"视频URL": url, "postId": post_id or "", "unique_strong": True}
        try:
            n = grok_pipe.download_one(self._ctx, row, str(dest), unique_strong=True)
            self.log(f"  ⬇ 补回已生成片子 {dest.name}  {int(n) // 1024} KB")
            return True
        except Exception as e:  # noqa: BLE001
            self.log(f"  · 旧地址无法直接取回 {dest.name}（{str(e)[:80]}），将重新提交生成")
            return False

    def recover_from_library(self, m, task_dir: Path, segs: list[dict], rounds: int = 4) -> list[int]:
        """从 https://grok.com/library 找回已生成好的视频（先按 postId/prev_posts 精确匹配，再按首帧比对）。"""
        if not segs or self._ctx is None:
            return []
        from ..services import verify as verifymod
        task_dir = Path(task_dir)
        self.log("  · 扫描 Grok 仓库 (https://grok.com/library) 找回已生成的视频…")
        try:
            gal = grok_pipe.scan_gallery(self._ctx, rounds=rounds, log=self.log) or {}
        except Exception as e:  # noqa: BLE001
            self.log(f"  · 扫描 Grok 仓库失败（不影响后续生成）：{str(e)[:80]}")
            return []
        if not gal:
            return []

        taken_pids: set[str] = set()
        taken_shas: set[str] = set()
        for s in (m.segments or []):
            if s.get("status") == "collected" and s.get("gen_id"):
                taken_pids.add(str(s["gen_id"]).lower())
            for tk in (s.get("takes") or []):
                if tk.get("gen_id"):
                    taken_pids.add(str(tk["gen_id"]).lower())
                if tk.get("sha256"):
                    taken_shas.add(str(tk["sha256"]).lower())

        recovered: list[int] = []

        def _commit_recovered(sg: dict, dest: Path, url: str, pid: str) -> bool:
            vr = verifymod.after_download(
                m, task_dir, sg, dest,
                asset_url=url,
                gen_id=pid,
                log=self.log,
                apply=True,
            )
            if vr.get("voided") or vr.get("verdict") in verifymod.BAD_VERDICTS or not dest.is_file():
                return False
            take = vr.get("take") or {
                "file": str(dest.relative_to(task_dir)).replace("\\", "/"),
                "gen_id": pid,
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "asset_url": url,
            }
            take["engine"] = "grok"
            take["account_id"] = self.account.get("id") or ""
            take["duration"] = int(sg.get("duration") or 10)
            sg.setdefault("takes", []).append(take)
            verifymod._merge_issues(sg, verifymod.issues_from_result(vr))
            sg["status"] = "collected"
            sg["gen_id"] = pid
            sg["error"] = None
            for k in ("gen_health", "orphan_conv", "wrongclip_count", "voided_sha", "dl_retry"):
                sg.pop(k, None)
            verifymod.clear_void_streak(sg)
            taken_pids.add(pid.lower())
            if take.get("sha256"):
                taken_shas.add(str(take["sha256"]).lower())
            self._close_tab(pid)
            if sg.get("gen_id"):
                self._close_tab(str(sg.get("gen_id")))
            sn = int(sg.get("s") or 0)
            recovered.append(sn)
            self.log(f"  ✓ s{sn:03d} 从 Grok 仓库找回已生成视频 → {dest.name}（post {pid[:8]}…）")
            return True

        # 1) 第一轮：按已知 postId / alias_id / prev_posts 精确匹配
        remaining: list[dict] = []
        for sg in segs:
            cand_ids = []
            for k in ("alias_id", "gen_id"):
                v = str(sg.get(k) or "").strip()
                if v and v not in cand_ids:
                    cand_ids.append(v)
            for v in (sg.get("prev_posts") or []):
                v = str(v or "").strip()
                if v and v not in cand_ids:
                    cand_ids.append(v)
            hit_ok = False
            for pid in cand_ids:
                g = gal.get(pid)
                vids = (g or {}).get("vids") or []
                if not vids:
                    continue
                url = vids[0]
                dest = task_dir / "clips" / m.clip_name(sg)
                if self.download_url(url, dest, pid) and _commit_recovered(sg, dest, url, pid):
                    hit_ok = True
                    break
            if not hit_ok:
                remaining.append(sg)

        if not remaining:
            return recovered

        # 2) 第二轮：对还没领走的仓库视频（Grok 改了 postId 或关了标签页），按首帧跟段配图比对认领
        ffmpeg = verifymod.find_ffmpeg()
        if not ffmpeg:
            return recovered
        seg_refs: list[tuple[dict, Path, int]] = []
        for sg in remaining:
            ref, kind, _lab = verifymod._first_frame_ref(task_dir, m, sg)
            if kind != "image" or ref is None or not ref.is_file():
                continue
            ih = verifymod.ahash_image(ref, ffmpeg)
            if ih is not None:
                seg_refs.append((sg, ref, ih))
        if not seg_refs:
            return recovered

        tmp_dir = paths.DATA / "tmp" / "grok_library_cache"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        unclaimed = [(pid, info) for pid, info in gal.items()
                     if pid.lower() not in taken_pids and (info.get("vids") or [])][:25]
        for pid, info in unclaimed:
            if not seg_refs:
                break
            url = info["vids"][0]
            tmp_mp4 = tmp_dir / f"lib-{pid}.mp4"
            if not (tmp_mp4.is_file() and tmp_mp4.stat().st_size > 100_000):
                row = {"视频URL": url, "postId": pid, "unique_strong": True}
                try:
                    grok_pipe.download_one(self._ctx, row, str(tmp_mp4), unique_strong=True)
                except Exception:
                    continue
            if not (tmp_mp4.is_file() and tmp_mp4.stat().st_size > 100_000):
                continue
            try:
                digest = verifymod.sha256_file(tmp_mp4).lower()
            except Exception:
                continue
            if digest in taken_shas:
                continue
            vh = verifymod.ahash_video(tmp_mp4, ffmpeg)
            if vh is None:
                continue
            # 找首帧距离最小的未收片段
            best_idx = -1
            best_d = 999
            for idx, (sg, ref, ih) in enumerate(seg_refs):
                d = verifymod.hamming(vh, ih)
                ic = verifymod.ahash_image_cropped(ref)
                if ic is not None:
                    d = min(d, verifymod.hamming(vh, ic))
                if d < best_d:
                    best_d = d
                    best_idx = idx
            if best_idx < 0:
                continue
            sg, ref, _ih = seg_refs[best_idx]
            _ok_th, soft_th = verifymod._thresholds_for(sg)
            matched = best_d <= soft_th
            if not matched and best_d <= 45:
                rc = verifymod.struct_recheck(m, task_dir, sg, tmp_mp4, ref, "image", ffmpeg)
                matched = bool(rc and rc.get("ok"))
            if not matched:
                continue
            dest = task_dir / "clips" / m.clip_name(sg)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tmp_mp4, dest)
            if _commit_recovered(sg, dest, url, pid):
                seg_refs.pop(best_idx)

        return recovered

    def forget_url(self, gen_id: str, url: str = "") -> None:
        """落盘自证作废后：丢掉这个地址，把段重新算进在飞，下次 poll 再取。

        下载按钮那路 url 是空的，要把网络层见过的地址一并作废，
        不然下一轮又会「✓ 出片 网络层」。
        """
        old = self._urls.pop(gen_id, None)
        self._src.pop(gen_id, None)
        self._unique.pop(gen_id, None)
        rej = self._rejected.setdefault(gen_id, set())
        if url:
            rej.add(url)
        if old:
            rej.add(old)
        for u in list(self._netvid.get(gen_id) or []):
            if u:
                rej.add(u)
        row = self._rows.get(gen_id)
        if row is not None:
            row["视频URL"] = ""
            row["unique_strong"] = False
        self._active.add(gen_id)

    def _close_tab(self, gid: str) -> None:
        pg = self._pages.pop(gid, None)
        self._active.discard(gid)
        try:
            if pg is not None and not pg.is_closed():
                pg.close()
        except Exception:
            pass

    # ---------------- 生图（0907 抓包定型，见 specs\18） ----------------
    #
    #   ① 传图   POST /http/upload-file-v2/direct   multipart，字段名 file
    #            → {"uploadId":…, "fileMetadata":{"fileMetadataId":<资产Id>, …}}
    #   ② 生图   POST /rest/app-chat/conversations/new   （NDJSON 流，一行一条）
    #            body 见 _gen_body()；inputAssets 放的就是上面那个 fileMetadataId
    #            → 某一行 streamingImageGenerationResponse.progress==100 时带
    #              imageUrl（相对 https://assets.grok.com/）、imageId、moderated
    #   ③ 取图   GET https://assets.grok.com/<imageUrl>   要带 cookie，所以也在页面里 fetch
    #
    # 全部请求都由 page.evaluate 在已登录的 grok.com 页面里发出，cookie / 头都是页面自己的。

    ASSETS = "https://assets.grok.com/"

    UPLOAD_JS = r"""
    async ([b64, name, type]) => {
      const bin = atob(b64); const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      const fd = new FormData();
      fd.append('file', new File([arr], name, {type}));
      const r = await fetch('/http/upload-file-v2/direct', {method: 'POST', credentials: 'include', body: fd});
      const t = await r.text();
      let j = null; try { j = JSON.parse(t); } catch (e) {}
      return {status: r.status, json: j, raw: t.slice(0, 500)};
    }
    """

    GEN_JS = r"""
    async ([body]) => {
      const hdr = {'Content-Type': 'application/json'};
      try { hdr['x-xai-request-id'] = crypto.randomUUID(); } catch (e) {}
      const r = await fetch('/rest/app-chat/conversations/new', {
        method: 'POST', credentials: 'include', headers: hdr, body: JSON.stringify(body)});
      const t = await r.text();
      const out = {status: r.status, images: [], moderated: false, errors: [], raw: t.slice(0, 600)};
      for (const line of t.split('\n')) {
        if (!line.trim()) continue;
        let o; try { o = JSON.parse(line); } catch (e) { continue; }
        if (o.error) out.errors.push(JSON.stringify(o.error).slice(0, 300));
        const resp = (o.result && o.result.response) || {};
        if (resp.streamErrors && resp.streamErrors.length)
          out.errors.push(JSON.stringify(resp.streamErrors).slice(0, 300));
        const s = resp.streamingImageGenerationResponse;
        if (!s) continue;
        if (s.moderated) out.moderated = true;
        if (s.progress === 100 && s.imageUrl)
          out.images.push({url: s.imageUrl, id: s.imageId || s.assetId,
                           moderated: !!s.moderated, rRated: !!s.rRated});
      }
      return out;
    }
    """

    FETCH_BLOB_JS = r"""
    async (url) => {
      const r = await fetch(url, {credentials: 'include'});
      if (!r.ok) return {error: 'http ' + r.status};
      const b = await r.blob();
      const buf = new Uint8Array(await b.arrayBuffer());
      let s = ''; const CH = 0x8000;
      for (let i = 0; i < buf.length; i += CH) s += String.fromCharCode.apply(null, buf.subarray(i, i + CH));
      return {b64: btoa(s), size: buf.length, type: b.type};
    }
    """

    def _gen_body(self, prompt: str, asset_id: str) -> dict:
        return {
            "modelName": "imagine-image-edit",
            "message": prompt,
            "enableImageStreaming": True,
            "enableSideBySide": True,
            "sendFinalMetadata": True,
            "responseMetadata": {"modelConfigOverride": {"modelMap": {"imageEditModel": "imagine"}}},
            "mediaGenInput": {"imageToImage": {"prompt": prompt, "inputAssets": [asset_id]}},
            "kind": "CONVERSATION_KIND_IMAGINE",
        }

    def _gen_page(self):
        """生图用的 grok.com 标签页。用 imagine 那个就行（同源才带得上 cookie）。"""
        page = self._ensure_imagine()
        u = page.url or ""
        if "grok.com" not in u:
            page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
        return page

    def upload_image(self, img: Path) -> str:
        """传一张图，返回 Grok 的资产 Id（fileMetadataId）。同一账号同一张图只传一次。

        注意：这条路本身是通的（上传不要反爬头），但后面那步生图要 x-statsig-id，
        自己发会 403，所以生图现在走界面（见 _generate_via_ui）。这两个方法留着，
        哪天摸清 statsig 就能直接切回接口，快很多。
        """
        img = Path(img)
        if not img.is_file():
            raise OranteError(f"找不到图片 {img}")
        key = f"{self.account['id']}:{img.stat().st_size}:{img.name}"
        cached = getattr(self, "_upload_cache", None)
        if cached is None:
            cached = self._upload_cache = {}
        if key in cached:
            return cached[key]
        mime = _MIME_UP.get(img.suffix.lower(), "image/jpeg")
        b64 = base64.b64encode(img.read_bytes()).decode("ascii")
        page = self._gen_page()
        try:
            res = page.evaluate(self.UPLOAD_JS, [b64, img.name, mime])
        except Exception as e:  # noqa: BLE001 —— 标签被关 / 正在导航
            raise EngineUnavailable(f"Grok 传图时页面被打断：{str(e)[:120]}") from e
        j = (res or {}).get("json") or {}
        aid = ((j.get("fileMetadata") or {}).get("fileMetadataId") or "").strip()
        if res.get("status") != 200 or not aid:
            raw = str(res.get("raw"))[:200]
            why = _blocked_reason(raw, page)
            if why:
                raise BlockedImage(f"Grok 拒收了 {img.name}：{why}")
            raise OranteError("Grok 传图失败", f"status={res.get('status')} {raw}")
        cached[key] = aid
        self.log(f"  ↑ 已上传 {img.name} → {aid[:8]}…")
        return aid

    # 生图走界面（composer）而不是直接打接口：/rest/app-chat/conversations/new
    # 要一个 x-statsig-id 反爬头，是他们的 JS 每次现算的，我们自己发就 403
    # 「This page is out of date. Reload to continue.」（0907 实测）。
    # 所以照人手那条路走：切图片模式 → 把图拖进输入框 → 贴提示词 → 回车，
    # 再用 CDP 把那次 conversations/new 的流原样读回来，拿 imageUrl 和 moderated。

    MODE_WORDS = ("Immagine", "Image", "图片", "图像")

    def _dismiss_consent(self, page) -> str:
        """新 profile 第一次开 grok.com 会弹 Cookie 同意条，盖住 composer。
        按最保守的那个（拒绝非必要）点掉；点不到就算了，不影响后面。"""
        for _ in range(2):
            try:
                hit = page.evaluate(_CONSENT_JS) or ""
            except Exception:
                return ""
            if hit:
                self.log(f"  · 关掉 Cookie 同意条（{hit}）")
                page.wait_for_timeout(1200)
                return hit
            page.wait_for_timeout(600)
        return ""

    def _pick_image_mode(self, page) -> str:
        for w in self.MODE_WORDS:
            try:
                loc = page.get_by_role("radio", name=w, exact=False).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=5000)
                    return w
            except Exception:
                continue
        try:
            radios = page.get_by_role("radio")
            if radios.count():
                radios.nth(0).click(timeout=5000)   # 顺序是 图片 / 视频 / 代理
                return "第1个"
        except Exception:
            pass
        return ""

    @staticmethod
    def _parse_stream(text: str) -> dict:
        """conversations/new 的 NDJSON 流 → {images:[{url,id}], moderated, errors}。"""
        out = {"images": [], "moderated": False, "errors": []}
        for line in (text or "").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if isinstance(o, dict) and o.get("error"):
                out["errors"].append(json.dumps(o["error"], ensure_ascii=False)[:300])
            resp = ((o.get("result") or {}).get("response") or {}) if isinstance(o, dict) else {}
            for e in (resp.get("streamErrors") or []):
                out["errors"].append(json.dumps(e, ensure_ascii=False)[:300])
            st = resp.get("streamingImageGenerationResponse")
            if not st:
                continue
            if st.get("moderated"):
                out["moderated"] = True
            if st.get("progress") == 100 and st.get("imageUrl"):
                out["images"].append({"url": st["imageUrl"],
                                      "id": st.get("imageId") or st.get("assetId") or "",
                                      "moderated": bool(st.get("moderated"))})
        return out

    DOM_IMG_JS = r"""() => {
      const out = [];
      for (const im of document.querySelectorAll('img')) {
        const s = im.getAttribute('src') || '';
        if (s.includes('/generated/') && s.includes('/image.') && !s.includes('-part-')) out.push(s);
      }
      return out;
    }"""

    def _generate_via_ui(self, page, prompt: str, img: Path, timeout: float = 180) -> dict:
        """在 composer 里做一次图生图。返回 _parse_stream 的结果（DOM 兜底也填这个形状）。"""
        page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
        grok_batch.pass_tos_gate(page)
        self._dismiss_consent(page)
        try:
            page.wait_for_selector("div.tiptap", state="visible", timeout=30000)
        except Exception:
            pass
        mode = self._pick_image_mode(page)
        self.log(f"  · 切到图片模式（{mode or '没找到模式按钮，按当前模式来'}）")
        page.wait_for_timeout(800)
        grok_pipe.attach_by_drop(page, str(img))     # 老脚本那条走 drop 的路，不跳页
        grok_pipe.wait_quiet(page, quiet=2.0, timeout=45, tag="传图")
        grok_pipe.type_prompt(page, prompt)

        seen: dict[str, str] = {}
        done: set[str] = set()
        cdp = None
        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable", {})
            cdp.on("Network.responseReceived",
                   lambda ev: seen.__setitem__(ev["requestId"], ev["response"]["url"])
                   if "conversations/new" in (ev.get("response") or {}).get("url", "") else None)
            cdp.on("Network.loadingFinished", lambda ev: done.add(ev["requestId"]))
        except Exception as e:  # noqa: BLE001
            self.log(f"  · 挂 CDP 失败（{str(e)[:80]}），只能靠页面上的图兜底")

        # 提交前先记下页面上已经有的图（首页会铺过往作品），只认新冒出来的那张
        try:
            before = set(page.evaluate(self.DOM_IMG_JS) or [])
        except Exception:
            before = set()
        page.keyboard.press("Enter")
        t0 = time.time()
        body = ""
        dom_hit = ""
        dom_at = 0.0
        retried = False
        while time.time() - t0 < timeout:
            page.wait_for_timeout(400)
            # 回车没送出去（Cookie 条盖着、焦点跑了）时，页面根本不会发请求。
            # 35 秒还一点动静都没有就再敲一次；75 秒还没有就认了，别干等满 timeout。
            if not seen and not dom_hit:
                el = time.time() - t0
                if el > 35 and not retried:
                    retried = True
                    self._dismiss_consent(page)
                    self.log("  · 提交后 35 秒没有任何请求，再敲一次回车")
                    try:
                        page.locator("div.tiptap").first.click(timeout=5000)
                    except Exception:
                        pass
                    page.keyboard.press("Enter")
                elif el > 75:
                    self.log("  · 75 秒还是没反应，这一张放弃（不算被拒）")
                    break
            hit = [rid for rid in seen if rid in done]
            if hit and cdp is not None:
                try:
                    b = cdp.send("Network.getResponseBody", {"requestId": hit[0]})
                    body = b.get("body") or ""
                    if b.get("base64Encoded"):
                        body = base64.b64decode(body).decode("utf-8", "replace")
                except Exception as e:  # noqa: BLE001
                    self.log(f"  · 读不到接口原文（{str(e)[:60]}），改看页面上的图")
                break
            if not dom_hit:
                try:
                    cur = page.evaluate(self.DOM_IMG_JS) or []
                except Exception:
                    cur = []
                fresh = [u for u in cur if u not in before]
                if fresh:
                    dom_hit, dom_at = fresh[-1], time.time()
            elif time.time() - dom_at > 12:
                break        # 图已经在页面上了，流还读不回来就用页面那张
        if body:
            r = self._parse_stream(body)
            r["from"] = "stream"
            if r["images"] or r["moderated"] or r["errors"]:
                return r
        if not dom_hit:
            try:
                cur = page.evaluate(self.DOM_IMG_JS) or []
            except Exception:
                cur = []
            fresh = [u for u in cur if u not in before]
            dom_hit = fresh[-1] if fresh else ""
        if dom_hit:
            return {"images": [{"url": dom_hit, "id": ""}], "moderated": False,
                    "errors": [], "from": "dom"}
        why = _blocked_reason("", page)
        return {"images": [], "moderated": bool(why), "errors": [why] if why else [],
                "from": "none", "waited": round(time.time() - t0, 1)}

    def gen_images(self, prompt: str, ref: Path | None, n: int = 4, aspect: str = "9:16",
                   dest_dir: Path | None = None, model: str = "") -> list[Path]:
        """和 Flow 那个同语义：生 n 张、落盘、返回路径。被拒抛 BlockedImage。

        aspect / model 用不上：Grok 的图生图跟着参考图走。
        """
        if ref is None:
            raise OranteError("Grok 生图要有参考图",
                              "扩图/补图都是从一张主体图出发的；纯文生图这条路还没接。")
        img = Path(ref)
        if not img.is_file():
            raise OranteError(f"找不到参考图 {img}")
        dest_dir = Path(dest_dir or (paths.DATA / "tmp"))
        dest_dir.mkdir(parents=True, exist_ok=True)
        page = self._gen_page()
        out: list[Path] = []
        moderated = 0          # 真的被审掉了
        empty = 0              # 只是没出来（超时 / 页面没反应），不算被审
        for i in range(max(1, int(n))):
            try:
                r = self._generate_via_ui(page, prompt, img)
            except Exception as e:  # noqa: BLE001
                why = _blocked_reason(str(e), page)
                if why:
                    self.log(f"  · 第 {i + 1} 张被拒：{why}")
                    moderated += 1
                    continue
                raise OranteError("Grok 生图没走通", str(e)[:200]) from e
            good = [x for x in r["images"] if not x.get("moderated")]
            if not good:
                why = "；".join(r.get("errors") or [])[:160]
                if r.get("moderated") or why:
                    moderated += 1
                    self.log(f"  · 第 {i + 1} 张被拒{('：' + why) if why else ''}")
                else:
                    empty += 1
                    self.log(f"  · 第 {i + 1} 张等了 {int(r.get('waited') or 0)} 秒没出图"
                             "（不算被拒，参考图不动）")
                continue
            url = good[-1]["url"]
            if not url.startswith("http"):
                url = self.ASSETS + url.lstrip("/")
            try:
                blob = page.evaluate(self.FETCH_BLOB_JS, url)
            except Exception as e:  # noqa: BLE001
                self.log(f"  · 第 {i + 1} 张取图被打断：{str(e)[:80]}")
                continue
            if blob.get("error"):
                self.log(f"  · 第 {i + 1} 张取图失败：{blob['error']}")
                continue
            data = base64.b64decode(blob["b64"])
            if len(data) < 5_000:
                self.log(f"  · 第 {i + 1} 张只有 {len(data)} 字节，不像图，丢掉")
                continue
            stem = (good[-1].get("id") or "")[:8] or f"{int(time.time())}-{i}"
            fp = dest_dir / f"grok-{stem}.jpg"
            fp.write_bytes(data)
            out.append(fp)
            self.log(f"  ✎ 生图 {i + 1}/{n} → {fp.name}（{len(data) // 1024} KB，来源 {r.get('from')}）")
        if not out and moderated and not empty:
            raise BlockedImage(f"Grok 连着 {moderated} 次判这张参考图违规")
        if not out:
            self.log(f"  · 这一轮一张都没出来（被拒 {moderated}、没反应 {empty}）")
        return out
