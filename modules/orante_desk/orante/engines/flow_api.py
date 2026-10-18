# -*- coding: utf-8 -*-
r"""Flow（flow.google.com，0905 新版 Angular 前端）—— 走它自己的 batchexecute 接口，不找按钮。

协议（0906 抓包 data\capture\flow_capture_0906-031010.jsonl 定型）：

  POST https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute
       ?rpcids=<rpc>&source-path=/project/<pid>/collection/<cid>&bl=<build>&f.sid=<sid>&hl=zh-CN&_reqid=<n>&rt=c
  body: f.req=[[["<rpc>", "<inner JSON 字符串>", null, "generic"]]] & at=<xsrf>
  resp: ")]}'\n\n<len>\n[["wrb.fr","<rpc>","<JSON 字符串>",...]]"

  rpc     作用            inner
  maseQ   上传图片        [[null,22,null,null,null,pid,null,cid,null,null,[recaptcha,1]], base64, mime, 1, null×4, 文件名, null, uuid, uuid]
  as29s   查媒体          ["mediaId"]                       → 含 CDN 地址（图 [5][9] / 视频 [7][0][8]）
  eb1hJf  图生视频        见 _gen_inner()                    → [null, 额度(与 nzlxg 相同), [[workflow...]], [[生成记录]]]
  jwpduf  轮询生成        [null,null,[["genId"],...]]         → 记录 [5][10] 状态：6 排队/生成中，3 完成
  WuwhI   前端埋点        不需要

XSRF（at）、构建号（bl）、会话（f.sid）都从页面的 WIZ_global_data 拿；reCAPTCHA token 在页面里调
grecaptcha.enterprise.execute 现拿。全部请求都由 page.evaluate 在已登录的 Flow 页面里发出，
cookie / 头一律是页面自己的——我们不伪造任何鉴权。

  ogiZ0b  生图（同步）    见 gen_image_once()               → [[mediaId,...,[[...,url@13,...]]]]
  nzlxg   查余额          []                                → 第一个数字；解析不出返回 None
  pGCYOe  归档/删除媒体   [[[mediaId,null,null,[null,null,1],pid]],[["metadata.archived"]]]
  reCAPTCHA 动作：UPLOAD_IMAGE / VIDEO_GENERATION / IMAGE_GENERATION；时长藏在模型 id 里（见 model_id_for）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import random
import re
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from datetime import datetime

from .. import paths, config, db
from ..errors import CreditHalted, FlowThrottled, NeedRedownload, OranteError
from . import chrome
from .base import Engine, BlockedImage, EngineUnavailable

FLOW_HOME = "https://flow.google.com/"

RECAPTCHA_SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
# reCAPTCHA 动作名（0906 第二轮抓包，hook grecaptcha.enterprise.execute 抓到）
RC_ACTION_UPLOAD = "UPLOAD_IMAGE"
RC_ACTION_GENERATE = "VIDEO_GENERATION"
RC_ACTION_IMAGE = "IMAGE_GENERATION"

# 时长藏在模型 id 里（0906 第二轮抓包）：
#   8s（默认）veo_3_1_i2v_lite_low_priority   4s veo_3_1_i2v_s_lite_4s_low_priority   6s veo_3_1_i2v_s_lite_6s_low_priority
MODEL_FAMILY = {
    "Veo 3.1 - Lite [Lower Priority]": ("lite", "_low_priority"),
    "Veo 3.1 - Lite": ("lite", ""),
    "Veo 3.1 - Fast": ("fast", ""),
    "Veo 3.1 - Quality": ("", ""),
    "Omni 1.1 Flash": ("omni", ""),
}
# 生图模型 key（0907 抓包核对）：GEM_PIX_2 = Nano Banana Pro（默认）、NARWHAL = Nano Banana 2。
# 两个在 Flow 里 UI 都写 0 crediti。Nano Banana 2 Lite 的 id 等 C 节抓包后再加白名单。
IMAGE_MODELS = {"pro": "GEM_PIX_2", "nb2": "NARWHAL",
                "nano": "GEM_PIX_2", "imagen": "NARWHAL"}

# 免费模型白名单：不在这里的 id 一律拒绝提交（flow.allow_paid=true 才放行）。
FLOW_FREE_MODEL_IDS = frozenset({
    "veo_3_1_i2v_lite_low_priority",
    "veo_3_1_i2v_s_lite_4s_low_priority",
    "veo_3_1_i2v_s_lite_6s_low_priority",
    "GEM_PIX_2",
    "NARWHAL",
})
FLOW_FREE_VIDEO_NAMES = frozenset({
    "Veo 3.1 - Lite [Lower Priority]",
})
# 生图 inner 的数量位。永远 ×1；UI 里 ×1 才显示 0 crediti，多张计价没核过。
IMAGE_ONCE_COUNT = 1


# 0922 抓包（flow_capture_0922-012853）：eb1hJf / MZZa6b 请求最外层最后一项 [uuid, N]。
# 网页设置「返回无声视频」开 → N=1，关 → N=2。账号上的开关本身走 DA4VGb
# （[[None×9, 1|0], [["should_return_silent_videos"]]]），我们不去改它，只按任务在请求里带。
AUDIO_SILENT = 1
AUDIO_WITH_SOUND = 2

# 规格 96：冷启动暖场——真点真打字真提交，给这个标签页留一点 reCAPTCHA
# 认得出的"人在用"痕迹（见文件末尾 FlowEngine._submit_via_ui）。
UI_STEP_TIMEOUT = 20_000  # ms
KNOWN_MODEL_LABELS = (
    "Veo 3.1 - Lite [Lower Priority]",
    "Veo 3.1 - Lite",
    "Veo 3.1 - Fast",
    "Veo 3.1 - Quality",
    "Omni 1.1 Flash",
)

# 2026-09-27 实测（flow.google.com，hl=en）：提示词框是 ProseMirror 的
# contenteditable div，没有 placeholder 属性（占位字是 CSS 画的），所以
# get_by_placeholder 永远找不到它。旧版 0-长文案/core/flow_batch.py 早就用的是
# 这组选择器；role=textbox 那个是老 labs 界面的写法，一起留着。
EDITOR_SEL = ('div.ProseMirror[contenteditable="true"], '
              'div[role="textbox"][contenteditable="true"]')
PROMPT_PLACEHOLDERS = ("What do you want to create?",)
_ZW_RE = re.compile("[﻿​‌‍]")
UI_CLICK_TIMEOUT = 8_000    # ms：已经确认在屏幕上的按钮/选项，点不中最多等这么久（原来默认 30 秒）
UI_UPLOAD_WAIT = 90         # 秒：传图后等它真的挂进作曲区的最长时间（大图传云端慢）
UI_FRAME_MAX = 2560      # 首帧/尾帧长边上限（视频只有 720p/1080p），超过就先缩再传
UI_IMAGE_ASPECTS = ("16:9", "4:3", "1:1", "3:4", "9:16")   # 图片模式面板里的画幅（09-27 实测）
# 生图模型 key → Flow 网页下拉里的名字（下拉里前面带个 🍌，比对时去掉）。
# key 与名字的对应：0907 抓包（GEM_PIX_2 = Nano Banana Pro、NARWHAL = Nano Banana 2）。
IMAGE_MODEL_LABELS = {"GEM_PIX_2": "Nano Banana Pro", "NARWHAL": "Nano Banana 2"}

# 作曲区里挂着几张图（起止帧缩略图 / 参考图）。从提示词框往上找到同时装着
# 「Start generation」的那一层就是作曲区，只数这一层里的图，绝不数整页。
COMPOSER_IMG_JS = r"""() => {
  const ed = document.querySelector('div.ProseMirror[contenteditable="true"], div[role="textbox"][contenteditable="true"]');
  if (!ed) return 0;
  let box = ed;
  for (let i = 0; i < 14 && box; i++) {
    if (box.querySelector && box.querySelector('button[aria-label="Start generation"]')) break;
    box = box.parentElement;
  }
  if (!box || box === document.body || box === document.documentElement) return 0;
  return [...box.querySelectorAll('img')].filter(i => {
    const r = i.getBoundingClientRect();
    return r.width >= 16 && r.height >= 16;
  }).length;
}"""

# 资产库弹窗里某个文件名的那一项：有没有、传完没、是不是被选中，以及现在选中的是谁。
# 09-27 实测结构：列表是 [role=listbox] 里的 button[role=option].asset-item，选中的那项
# aria-selected="true"（class 带 asset-item-active），文件名在 span.asset-title 里；
# 还在传的那项文字里带 "Uploading"。
ASSET_STATE_JS = r"""(name) => {
  const panes = [...document.querySelectorAll('.cdk-overlay-pane,[role="dialog"]')]
      .filter(d => { const r = d.getBoundingClientRect(); return r.width > 100 && r.height > 100; });
  if (!panes.length) return {pane: false};
  const pane = panes[panes.length - 1];
  const opts = [...pane.querySelectorAll('[role="option"]')];
  const title = o => { const t = o.querySelector('.asset-title'); return t ? t.textContent.trim() : ''; };
  const mine = opts.filter(o => title(o) === name);
  const busy = o => /Uploading/i.test(o.innerText || '');
  const imgOk = o => [...o.querySelectorAll('img')].some(i => i.complete && i.naturalWidth > 0);
  return {
    pane: true, n: opts.length, mine: mine.length,
    uploading: mine.some(busy),
    // 09-28 实测：刚传完的图在弹窗里只显示一个 image 图标，缩略图要重开弹窗才出来——
    // 所以"传完"只看不再显示 Uploading，不等缩略图（原来等缩略图，白等 90 秒）
    ready: mine.filter(o => !busy(o)).length,
    thumb: mine.filter(o => imgOk(o)).length,
    selected: mine.some(o => o.getAttribute('aria-selected') === 'true'),
    selected_names: opts.filter(o => o.getAttribute('aria-selected') === 'true').map(title)
  };
}"""

# 传图被 Flow 当场拒收时弹窗 / 提示条里的话（照旧版 0905c 的 UPLOAD_FAIL_JS）。
# 只看弹窗和提示条，绝不扫整页——整页里可能躺着别的失败卡片，会把好图误判成被拒。
UPLOAD_FAIL_JS = r"""() => {
  const scopes = [
    ...document.querySelectorAll('.cdk-overlay-pane,[role="dialog"]'),
    ...document.querySelectorAll('[role="alert"],[role="status"],.mat-mdc-snack-bar-container,' +
                                 'mat-snack-bar-container,snack-bar-container')
  ].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  const sent = [
    /[^。.!?\n]*(禁止上传|不允许上传|无法上传|上传失败|请尝试使用其他图片)[^。.!?\n]*[。.!?]?/,
    /[^.!?\n]*(Failed to upload|couldn't upload|could not upload|unable to upload)[^.!?\n]*[.!?]?/i,
    /[^.!?\n]*do not allow uploads?[^.!?\n]*[.!?]?/i,
    /[^.!?\n]*uploads? of (minors|public figures|people)[^.!?\n]*[.!?]?/i,
    /[^.!?\n]*try a different image[^.!?\n]*[.!?]?/i
  ];
  for (const sc of scopes) {
    const t = sc.innerText || '';
    for (const p of sent) {
      const m = t.match(p);
      if (m && m[0] && m[0].trim().length > 3) return m[0].trim().replace(/\s+/g, ' ').slice(0, 120);
    }
  }
  return '';
}"""
UI_MAX_CONCURRENT = 4       # 真人操作模式下同时在生成的段数上限（Flow 网页一次最多 4 条，用户确认）
GEN_BTN_ENABLE_WAIT = 15.0  # 秒：填好图和字之后「开始生成」由灰变亮的最长等待


def _norm_prompt(s: str) -> str:
    """比对提示词用：去零宽字符、去占位字、空白压成一个空格。"""
    s = _ZW_RE.sub("", s or "")
    for ph in PROMPT_PLACEHOLDERS:
        s = s.replace(ph, "")
    return re.sub(r"\s+", " ", s).strip()


def _short_err(e: BaseException, n: int = 160) -> str:
    """Playwright 的报错后面跟一大段 Call log，日志里只要「在等哪个控件」那一句。"""
    msg = getattr(e, "message", None) or str(e) or e.__class__.__name__
    lines = [ln.strip() for ln in str(msg).splitlines() if ln.strip()]
    head = lines[0] if lines else ""
    wait = next((ln.lstrip("- ").strip() for ln in lines if ln.lstrip("- ").startswith("waiting for")), "")
    out = head if not wait or wait in head else f"{head} {wait}"
    return out[:n]


def _freq_strings(post_data: str) -> list[str] | None:
    """把 batchexecute 请求体（f.req=...）里所有字符串叶子取出来。
    外层 JSON 里第二项又是一段 JSON 字符串，要再解一层。解不开返回 None。"""
    from urllib.parse import parse_qs
    try:
        raw = parse_qs(post_data or "", keep_blank_values=True).get("f.req", [""])[0]
        root = json.loads(raw)
    except Exception:
        return None
    out: list[str] = []

    def walk(x, depth=0):
        if depth > 40:
            return
        if isinstance(x, str):
            t = x.strip()
            if t[:1] in "[{" and len(t) > 1:
                try:
                    walk(json.loads(t), depth + 1)
                    return
                except Exception:
                    pass
            out.append(x)
        elif isinstance(x, list):
            for y in x:
                walk(y, depth + 1)
        elif isinstance(x, dict):
            for y in x.values():
                walk(y, depth + 1)

    walk(root)
    return out


def request_carries_prompt(post_data: str, prompt: str) -> bool | None:
    """点「生成」后收到的那条 eb1hJf，它的请求里是不是正好带着这一段的提示词。
    True=对上了；False=请求解得开但里面没有这段提示词（绝不能认这个 genId）；
    None=请求体拿不到/解不开（没法核对）。"""
    leaves = _freq_strings(post_data)
    if leaves is None:
        return None
    want = _norm_prompt(prompt)
    if not want:
        return None
    return any(_norm_prompt(x) == want for x in leaves)


def request_audio_flag(post_data: str) -> int | None:
    """eb1hJf 请求最外层最后一项是 [uuid, N]：1 = 无声（网页「Return silent videos」开着），
    2 = 有声（0921/0922 抓包）。解不开返回 None。"""
    from urllib.parse import parse_qs
    try:
        root = json.loads(parse_qs(post_data or "").get("f.req", [""])[0])
        inner = json.loads(root[0][0][1])
        last = inner[-1]
        if isinstance(last, list) and len(last) == 2 and isinstance(last[1], int):
            return int(last[1])
    except Exception:
        pass
    return None


def _model_label(s: str) -> str:
    """模型名比对用：去掉下拉箭头的图标字、前面的 🍌 之类符号、多余空白。"""
    s = _norm_prompt((s or "").replace("arrow_drop_down", ""))
    s = re.sub(r"^(?:volume_up|videocam|image|check)\b\s*", "", s, flags=re.I)
    return re.sub(r"^[^\w\[]+", "", s).strip()


def _model_base(name: str) -> str:
    """"Veo 3.1 - Lite [Lower Priority]" → "veo 3.1 - lite"，读回核对用。"""
    return re.sub(r"\s*[\[（(].*$", "", name or "").strip().lower()


def _parse_batchexecute(text: str, rpc: str) -> dict:
    """真点「开始生成」之后用 page.expect_response 拿到的原始响应体，
    按 RPC_JS 里同一套规则解析（])}'\\n\\n<len>\\n[[...]] 这种格式）。"""
    out = {"payload": None, "err": None, "raw": (text or "")[:400]}
    for line in (text or "").split("\n"):
        if not line.startswith("[["):
            continue
        try:
            parsed_line = json.loads(line)
        except Exception:
            continue
        for it in parsed_line:
            if not isinstance(it, list) or not it:
                continue
            if it[0] == "wrb.fr" and len(it) > 1 and it[1] == rpc:
                try:
                    out["payload"] = json.loads(it[2]) if it[2] else None
                except Exception:
                    out["payload"] = None
                if len(it) > 5 and it[5]:
                    out["err"] = it[5]
            elif it[0] == "er":
                out["err"] = it
    return out


def model_id_for(model_name: str, duration: int) -> str:
    """按时长拼 Veo 模型 id。规格 84：不在 caps.DURATIONS['flow'] 里的时长直接抛错。"""
    from . import caps
    fam, suffix = MODEL_FAMILY.get(model_name or "", ("lite", "_low_priority"))
    d = int(duration or 0)
    if d not in caps.DURATIONS["flow"]:
        raise ValueError(
            f"Flow 不支持 {d} 秒（只能是 {', '.join(map(str, caps.DURATIONS['flow']))} 秒）"
        )
    if d == 8:
        return f"veo_3_1_i2v{('_' + fam) if fam else ''}{suffix}"
    return f"veo_3_1_i2v_s{('_' + fam) if fam else ''}_{d}s{suffix}"
STATUS_PENDING = {1, 2, 6}
STATUS_DONE = {3}
STATUS_FAILED = {4, 5, 7}       # 猜测值；遇到会在日志里打出原文，待校正

RPC_JS = r"""
async ([rpc, innerJson, srcPath]) => {
  const w = window.WIZ_global_data || {};
  const at = w.SNlM0e, bl = w.cfb2h, sid = w.FdrFJe, hl = w.hl || 'en';
  if (!at) return {error: 'no_xsrf'};
  const reqid = Math.floor(Math.random() * 900000) + 100000;
  const url = '/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=' + rpc +
              '&source-path=' + encodeURIComponent(srcPath) + '&bl=' + encodeURIComponent(bl) +
              '&f.sid=' + encodeURIComponent(sid) + '&hl=' + hl + '&_reqid=' + reqid + '&rt=c';
  const body = 'f.req=' + encodeURIComponent(JSON.stringify([[[rpc, innerJson, null, 'generic']]])) +
               '&at=' + encodeURIComponent(at) + '&';
  const res = await fetch(url, {method: 'POST', credentials: 'include',
     headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8', 'X-Same-Domain': '1'}, body});
  const txt = await res.text();
  const out = {status: res.status, payload: null, err: null, raw: txt.slice(0, 400)};
  for (const line of txt.split('\n')) {
    if (!line.startsWith('[[')) continue;
    try {
      for (const it of JSON.parse(line)) {
        if (it[0] === 'wrb.fr' && it[1] === rpc) { out.payload = it[2] ? JSON.parse(it[2]) : null; if (it[5]) out.err = it[5]; }
        if (it[0] === 'er') out.err = it;
      }
    } catch (e) {}
  }
  return out;
}
"""
RC_JS = r"""
async ([key, action]) => {
  const ready = () => window.grecaptcha && window.grecaptcha.enterprise && window.grecaptcha.enterprise.execute;
  if (!ready()) {
    // Flow 是按需加载 reCAPTCHA 的；没加载就自己把官方脚本挂上（同一个 site key）
    if (!document.querySelector('script[data-orante-rc]')) {
      const sc = document.createElement('script');
      sc.src = 'https://www.google.com/recaptcha/enterprise.js?render=' + key;
      sc.setAttribute('data-orante-rc', '1'); document.head.appendChild(sc);
    }
    for (let i = 0; i < 60 && !ready(); i++) await new Promise(r => setTimeout(r, 500));
    if (!ready()) return {error: 'no_grecaptcha (waited 30s)'};
    await new Promise(r => { try { window.grecaptcha.enterprise.ready(r); } catch (e) { r(); } });
  }
  try { return {token: await window.grecaptcha.enterprise.execute(key, {action})}; }
  catch (e) { return {error: 'execute: ' + String(e)}; }
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
  return {b64: btoa(s), type: b.type, size: buf.length};
}
"""


def _g(o, *path, default=None):
    """安全取嵌套下标：_g(rec, 5, 10, 0)。"""
    for p in path:
        try:
            o = o[p]
        except (IndexError, KeyError, TypeError):
            return default
        if o is None:
            return default
    return o


def allow_paid_models() -> bool:
    try:
        return bool(config.get("flow.allow_paid", False))
    except Exception:
        return False


def credit_guard_enabled() -> bool:
    """余额守卫：默认开。探针证明 nzlxg 不是额度后把 flow.credit_guard 改 false，只记不拦。"""
    try:
        v = config.get("flow.credit_guard", True)
        if v is None:
            return True
        return bool(v)
    except Exception:
        return True


def _first_int(obj):
    """从 nzlxg 返回里抠第一个整数。[[8611, …]] / [8611, …] / 8611 都能认。"""
    if isinstance(obj, bool):
        return None
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, list):
        for x in obj:
            n = _first_int(x)
            if n is not None:
                return n
    return None


def assert_image_once_count(inner) -> None:
    """生图 inner 的数量位必须是 1。UI 里 ×1 才显示 0 crediti。"""
    n = _g(inner, 2)
    if n != IMAGE_ONCE_COUNT:
        raise AssertionError(f"生图数量位必须是 1，实际 {n}（多张计价没核过）")


def reject_paid_model(model_id: str) -> None:
    if not model_id:
        raise OranteError("这个模型会扣额度，已拦下", "模型 id 为空。")
    if allow_paid_models():
        return
    if model_id in FLOW_FREE_MODEL_IDS:
        return
    raise OranteError(
        "这个模型会扣额度，已拦下",
        f"模型 id={model_id} 不在免费白名单。设置 flow.allow_paid=true 才放行。",
    )


def notify_credit_halt(job, eng, exc) -> None:
    """顶部红条 + 日志近 5 次请求。段状态由调用方保证不动。"""
    from .. import events
    rec = list(getattr(eng, "_recent_rpc", []) or [])[-5:]
    payload = {
        "b0": getattr(exc, "b0", None),
        "b": getattr(exc, "b", None),
        "message": getattr(exc, "message", str(exc)),
        "recent": rec,
    }
    if job is not None:
        job.credit_halt = payload
        job.status = "halted"
        job.error = payload["message"]
        to_dict = getattr(job, "to_dict", None)
        events.emit("job.state", job=to_dict() if callable(to_dict) else {
            "id": getattr(job, "id", ""), "status": "halted",
            "credit_halt": payload, "error": payload["message"],
        })
    events.emit(
        "flow.credit_halt",
        job_id=getattr(job, "id", None),
        task_id=getattr(job, "task_id", None),
        **payload,
    )
    log = getattr(eng, "log", None) or (getattr(job, "log", None) if job else None)
    if callable(log):
        log(f"  ■ {payload['message']}")
        for item in rec:
            log(f"  · 近次 rpc={item.get('rpc')} 模型 {item.get('model')} 响应[1]={item.get('slot1')}")


def wait_credit_continue(job, eng) -> None:
    """等到用户点「我知道，继续」或取消。"""
    if job is None:
        return
    while not getattr(job, "credit_continue", False):
        # 0922 拼单：同组别的任务上已经点过「我知道，继续」（共用引擎上已放行），这里也一起放行
        if eng is not None and getattr(eng, "_credit_continue", False):
            break
        job.check()
        job.sleep(0.4)
    if eng is not None:
        eng._credit_continue = True
    job.status = "running"
    job.credit_halt = None
    job.error = None
    from .. import events
    to_dict = getattr(job, "to_dict", None)
    if callable(to_dict):
        events.emit("job.state", job=to_dict())


def _urls(obj, out=None) -> dict:
    """递归把记录里的 CDN 地址捞出来：{'video': [...], 'image': [...]}。结构变了也不怕。"""
    out = out if out is not None else {"video": [], "image": []}
    if isinstance(obj, str):
        if obj.startswith("https://flow-content.google/video/"):
            out["video"].append(obj)
        elif obj.startswith("https://flow-content.google/image/"):
            out["image"].append(obj)
    elif isinstance(obj, list):
        for x in obj:
            _urls(x, out)
    return out


def _status_of(rec) -> int | None:
    """生成记录的状态码在 rec[5][8][0]：6 刚提交，2 生成中，3 完成（0906 实测）。"""
    st = _g(rec, 5, 8)
    if isinstance(st, list) and st and isinstance(st[0], int):
        return st[0]
    return None


# 上传被拒的 err 码：第一次常见 [3]，token 复用后常见 [13]（修了重试换 token 后 [13] 应少见）
_REJECT_ERR = {3, 13}
_REJECT_HINT = re.compile(
    r"prominent|policy|violat|unsafe|celebrit|blocked|SAFETY|RAI", re.I)

# 规格 82：Flow 错误码 → 人话 + 处理。散落的 _REJECT_HINT / 重试判断往这里收。
# action: throttle 不重试并冷却；reject 换图；retry 现有退避；
#         busy 30s→60s→120s；unknown 现有重试，第一行人话。
FLOW_ERRORS = {
    "PUBLIC_ERROR_UNUSUAL_ACTIVITY": {
        "plain": "这个 Flow 账号被 Google 暂时限流了（短时间内提交太多）",
        "action": "throttle",
    },
    "PUBLIC_ERROR_UNSAFE_GENERATION": {
        "plain": "画面被 Flow 审核拦下了",
        "action": "reject",
    },
    "PUBLIC_ERROR_AUDIO_FILTERED": {
        # 0922：Veo 连声音一起生成，声音被审核过滤就整条不给。不说话的镜头打开「返回无声视频」可避开
        "plain": "Flow 生成的声音被审核过滤了，片子没给出来",
        "action": "retry",
    },
    "PUBLIC_ERROR_VIDEO_GENERATION_TIMED_OUT": {
        "plain": "Flow 服务器那边生成超时了",
        "action": "retry",
    },
    "RESOURCE_EXHAUSTED": {
        "plain": "Flow 服务现在很忙",
        "action": "busy",
    },
    "UNAVAILABLE": {
        "plain": "Flow 服务现在很忙",
        "action": "busy",
    },
}
BUSY_BACKOFF = (30, 60, 120)
_PUBLIC_ERR_RE = re.compile(r"PUBLIC_ERROR_[A-Z0-9_]+")
_NAMED_ERR_RE = re.compile(
    r"(PUBLIC_ERROR_[A-Z0-9_]+|RESOURCE_EXHAUSTED|UNAVAILABLE)")
_PROJECT_404_RE = re.compile(r"/404(?:\?|$)|reason=project", re.I)
_COLLECTION_RE = re.compile(
    r"/collection/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I)


def parse_project_url(url: str) -> tuple[str | None, str | None]:
    """从 Flow 链接里抠 project_id、collection_id。"""
    m = re.search(
        r"/project/([0-9a-f-]{36})(?:/collection/([0-9a-f-]{36}))?",
        url or "", re.I)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def strip_collection_url(url: str) -> str:
    """去掉 /collection/<uuid>，只留 /project/<pid>。"""
    pid, _cid = parse_project_url(url or "")
    if not pid:
        return url or ""
    return f"{FLOW_HOME.rstrip('/')}/project/{pid}"


def is_project_404(url: str) -> bool:
    return bool(_PROJECT_404_RE.search(url or ""))


def force_hl(url: str, hl: str = "en") -> str:
    """给链接强制加/换 ``hl=<hl>`` 查询参数。

    规格 96 补丁（2026-09-27）：Flow 网页界面语言由这个 URL 参数决定，跟账号
    自己保存的语言设置无关——账号本来显示英文，URL 加了 ``?hl=it`` 照样整页
    变意大利文（实测验证过）。Orante 自己专用的标签页只用来点按钮识别控件、
    从不代表用户操作账号设置，所以在这里强制成英文只影响这一次导航渲染出来
    的文字，不会改到账号真实的语言偏好；账号别处打开 Flow 还是原来的语言。
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() != "hl"]
        q.append(("hl", hl))
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                            urlencode(q), parts.fragment))
    except Exception:  # noqa: BLE001
        return url


def project_page_plain(why: str, account_name: str = "") -> str:
    """项目页打不开时给人看的原因 + 处理办法。"""
    if why == "collection":
        return ("链接里的合集那一段失效了，已自动改用项目地址。"
                "生成接口正常，不影响出片。")
    name = account_name or "这个账号"
    return (f"这个项目在 {name} 登录的 Google 账号里看不到。"
            f"浏览器里没法看进度，要修好就在 {name} 那个 Chrome 里打开 Flow、"
            "新建或打开一个项目，把地址栏复制到账号设置里。")


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def wait_xsrf(pg, wiz_of, tries: int = 60, interval: float = 0.5) -> bool:
    """等到页面上的 WIZ_global_data.SNlM0e。"""
    for _ in range(max(1, int(tries))):
        try:
            if (wiz_of(pg) or {}).get("SNlM0e"):
                return True
        except Exception:
            pass
        time.sleep(interval)
    try:
        return bool((wiz_of(pg) or {}).get("SNlM0e"))
    except Exception:
        return False


def diagnose_project_page(pg, *, project_url: str, project_id: str,
                          collection_id: str | None, wiz_of,
                          log=None, account_name: str = "",
                          hl: str | None = None) -> dict:
    """打开项目链接，看落地地址。404 就试去掉合集，再不行去首页。

    不改提交用的 src_path。返回 {ok, why?, at, url}。

    ``hl``：规格 96 补丁，非空时给每一次导航的链接都强制加上
    ``?hl=<hl>``（只影响这个标签页渲染出来的语言，不改账号真实设置，见
    :func:`force_hl`）。给 Orante 自己专用的标签页用，让暖场时点按钮用的
    无障碍角色名称（英文）能稳定对上；健康检查探测页（不点按钮，只看落地
    地址）不传这个参数，保持原样。
    """
    def _log(msg: str) -> None:
        if callable(log):
            log(msg)

    def _url(u: str) -> str:
        return force_hl(u, hl) if hl else u

    def _goto(url: str) -> None:
        pg.goto(_url(url), wait_until="domcontentloaded", timeout=60000)

    rec = {"ok": True, "at": _iso_now(), "url": ""}
    try:
        _goto(project_url)
    except Exception as e:  # noqa: BLE001
        _log(f"  · 打开 Flow 项目页出错：{str(e)[:100]}")
    wait_xsrf(pg, wiz_of)
    landing = ""
    try:
        landing = pg.url or ""
    except Exception:
        landing = ""
    rec["url"] = landing
    if not is_project_404(landing):
        return rec

    why = "not_visible"
    if collection_id and project_id:
        alt = f"{FLOW_HOME.rstrip('/')}/project/{project_id}"
        try:
            _goto(alt)
        except Exception as e:  # noqa: BLE001
            _log(f"  · 改用项目地址时出错：{str(e)[:100]}")
        wait_xsrf(pg, wiz_of)
        try:
            landing = pg.url or ""
        except Exception:
            landing = ""
        rec["url"] = landing
        if not is_project_404(landing):
            why = "collection"
            rec.update({"ok": False, "why": why, "at": _iso_now()})
            return rec

    rec.update({"ok": False, "why": why, "at": _iso_now()})
    try:
        _goto(FLOW_HOME)
    except Exception as e:  # noqa: BLE001
        _log(f"  · 打开 Flow 首页出错：{str(e)[:100]}")
    wait_xsrf(pg, wiz_of)
    try:
        rec["url"] = pg.url or FLOW_HOME
    except Exception:
        rec["url"] = FLOW_HOME
    return rec


def probe_account_project_page(account: dict, log=None) -> dict:
    """给 accounts.health 用：临时开一个标签诊断项目页，用完关掉。"""
    from .. import accounts as accmod
    port = int(account.get("port") or 0)
    url = account.get("project_url") or ""
    pid, cid = parse_project_url(url)
    if not pid:
        return {"ok": False, "why": "no_url", "at": _iso_now()}
    br = chrome.Browser(port).__enter__()
    pg = None
    try:
        pg = br.ctx.new_page()
        try:
            cdp = br.ctx.new_cdp_session(pg)
            cdp.send("Page.setBypassCSP", {"enabled": True})
        except Exception:
            pass
        rec = diagnose_project_page(
            pg, project_url=url, project_id=pid, collection_id=cid,
            wiz_of=lambda p: _wiz_eval(p), log=log,
            account_name=account.get("name") or "")
        try:
            accmod.patch_extra(account["id"], project_page=rec)
        except Exception:
            pass
        return rec
    finally:
        if pg is not None:
            try:
                if not pg.is_closed():
                    pg.close()
            except Exception:
                pass
        try:
            br.__exit__(None, None, None)
        except Exception:
            pass


def _wiz_eval(page) -> dict:
    try:
        return page.evaluate(
            "() => { const w = window.WIZ_global_data||{}; "
            "return {SNlM0e: w.SNlM0e, cfb2h: w.cfb2h, FdrFJe: w.FdrFJe}; }"
        ) or {}
    except Exception:
        return {}


def _walk_strs(obj, out=None) -> list:
    out = out if out is not None else []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _walk_strs(x, out)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_strs(v, out)
    return out


def _first_err_int(err) -> int | None:
    if isinstance(err, int):
        return err
    if isinstance(err, (list, tuple)):
        for x in err:
            if isinstance(x, int):
                return x
            n = _first_err_int(x)
            if n is not None:
                return n
    return None


def classify_flow_error(rpc: str, res: dict | None) -> dict:
    """从 batchexecute 的 status/err/raw 里认出错误码，给出人话和怎么处理。"""
    res = res if isinstance(res, dict) else {}
    status = res.get("status")
    err = res.get("err")
    blob_parts = _walk_strs(err) + _walk_strs(res.get("raw")) + _walk_strs(res.get("payload"))
    blob = " ".join(blob_parts)
    codes = []
    for s in blob_parts:
        codes.extend(_NAMED_ERR_RE.findall(s or ""))
    codes.extend(_NAMED_ERR_RE.findall(blob))
    # 去重保序
    seen, uniq = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    code = uniq[0] if uniq else ""
    if not code and status == 429:
        code = "HTTP_429"
    low = blob.lower()
    info = FLOW_ERRORS.get(code) if code else None
    if info is None and code.startswith("PUBLIC_ERROR_") and _REJECT_HINT.search(code):
        info = {"plain": "画面被 Flow 审核拦下了", "action": "reject"}
    if info is None and (status == 429 or "high demand" in low or "resource_exhausted" in low):
        info = FLOW_ERRORS["RESOURCE_EXHAUSTED"]
        code = code or "RESOURCE_EXHAUSTED"
    if info is None and code:
        info = {
            "plain": f"Flow 返回了一个没见过的错误（{code}）",
            "action": "unknown",
        }
    if info is None:
        info = {"plain": "Flow 这次没跑成", "action": "unknown"}
        code = code or "UNKNOWN"
    return {
        "code": code,
        "plain": info["plain"],
        "action": info["action"],
        "err_n": _first_err_int(err),
        "rpc": rpc or "",
        "status": status,
    }


def format_flow_raw(info: dict) -> str:
    """日志第二行：缩进的原始码，给排查用。"""
    bits = [info.get("code") or "UNKNOWN"]
    if info.get("rpc"):
        bits.append(str(info["rpc"]))
    if info.get("err_n") is not None:
        bits.append(f"err={info['err_n']}")
    elif info.get("status") not in (None, 200):
        bits.append(f"http={info.get('status')}")
    return "        [" + " · ".join(bits) + "]"


def _is_content_reject(res: dict) -> bool:
    """HTTP 200、payload 空、err 是 [3] 或 [13] → 服务端按内容拒图，不是断网。"""
    if not isinstance(res, dict):
        return False
    if res.get("status") != 200:
        return False
    if res.get("payload") not in (None, [], {}):
        return False
    err = res.get("err")
    if not isinstance(err, list) or not err:
        return False
    ints = [x for x in err if isinstance(x, int)]
    return bool(ints) and set(ints) <= _REJECT_ERR and len(ints) == len(err)


def _reject_snippet(rec) -> str | None:
    """从失败记录里抠审核理由。优先独立的大写错误码，其次含关键词的片段。"""
    try:
        blob = json.dumps(rec, ensure_ascii=False)
    except Exception:
        blob = str(rec)
    codes = []
    for m in re.finditer(r'"((?:PUBLIC_ERROR_)?[A-Z][A-Z0-9_]{6,})"', blob):
        if _REJECT_HINT.search(m.group(1)):
            codes.append(m.group(1))
    if codes:
        # 去重且保序
        seen = []
        for c in codes:
            if c not in seen:
                seen.append(c)
        return " / ".join(seen)
    m = _REJECT_HINT.search(blob)
    if not m:
        return None
    a = max(0, m.start() - 80)
    b = min(len(blob), m.end() + 120)
    return blob[a:b]


def _crop_box(img_path: Path, aspect=None):
    """微调到精确 9:16 / 16:9。返回 [上, 左, 下, 右]；0 用 None（与 Flow 网页一致）。
    规格 84：方向永远跟图片一致，绝不跨方向裁。"""
    from . import caps
    try:
        from PIL import Image
        with Image.open(img_path) as im:
            w, h = im.size
    except Exception:
        return [None, None, 1, 1]
    img_aspect = caps.aspect_for_image(img_path, "flow")
    if aspect in caps.FLOW_ASPECT_CODE:
        aw, ah = (int(x) for x in aspect.split(":"))
        if w != h and ((aw >= ah) != (w >= h)):
            aspect = img_aspect
    else:
        aspect = img_aspect
    a, b = (int(x) for x in aspect.split(":"))
    target = a / b
    ratio = (w / h) if h else target
    if abs(ratio - target) < 1e-9:
        return [None, None, 1, 1]
    if ratio > target:
        x0 = (w - h * target) / 2.0 / w
        left = None if abs(x0) < 1e-12 else x0
        right = 1.0 if left is None else (1.0 - x0)
        return [None, left, 1, right]
    y0 = (h - w / target) / 2.0 / h
    top = None if abs(y0) < 1e-12 else y0
    bottom = 1.0 if top is None else (1.0 - y0)
    return [top, None, bottom, 1]

class FlowEngine(Engine):
    name = "flow"
    can_gen_images = True

    def __init__(self, account, log):
        super().__init__(account, log)
        self.port = int(account["port"])
        self.project_url = account.get("project_url") or ""
        m = re.search(r"/project/([0-9a-f-]{36})(?:/collection/([0-9a-f-]{36}))?", self.project_url)
        if not m:
            raise OranteError(f"账号 {account['name']} 没填 Flow 项目链接",
                              "登录后到项目/合集页，把地址栏复制到账号设置里。")
        self.project_id, self.collection_id = m.group(1), m.group(2)
        self.src_path = f"/project/{self.project_id}" + (f"/collection/{self.collection_id}" if self.collection_id else "")
        self.model_name = account.get("model") or "Veo 3.1 - Lite [Lower Priority]"
        self.aspect = None  # 规格 84：画幅跟图片走，不读账号
        self.max_conc = int(account.get("max_concurrent") or 5)
        self._br = None
        self.page = None
        # 这个引擎实例自己开的标签页。只用自己的，绝不去动 Chrome 里别的标签，
        # 也就绝不会有「我在 evaluate、别人在刷新」的上下文被销毁。
        self._tag = f"orante-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._own: list = []
        self._active: set[str] = set()
        self._rejected: dict[str, set] = {}   # gen_id → 已作废的 URL（落盘自证打回）
        self._cache_file = paths.DATA / "flow_media_cache.json"
        self._cache = self._load_cache()
        self._credit_b0 = None
        self._credit_continue = False
        self._credit_job = None
        self._credits_at = 0.0
        self._credits_last = None
        self._credits_unparsed_logged = False
        self._recent_rpc: deque = deque(maxlen=5)
        # 规格 96：这个引擎实例（=这个标签页）有没有真人操作式地提交成功过一次；
        # 成功过一次就当"热"了，后面照旧走快的接口直连。失败过一次就不再重试，
        # 免得每段都白白多花几秒钟走一遍注定失败的 UI 步骤。
        self._ui_warmed = False
        self._ui_warmup_failed = False
        self._ui_stage = ""   # 真人操作走到哪一步了（报错、落盘现场时用）
        self._ui_model_ok = False
        self._ui_silent_state = None   # 网页「Return silent videos」开关现在拨在哪（None=还没看过）   # 这个标签页里模型读回来核对过没有（核对过就不用每段开设置面板）

    # ---------------- 连接 ----------------
    def connect(self) -> None:
        from .. import accounts
        prof = accounts._profile_dir(self.account)
        # 已经在跑就不再往里塞标签：下面 _ensure_page() 会开一个只属于本次任务的
        chrome.launch(self.port, prof, self.project_url, tab_if_alive=False)
        self._br = chrome.Browser(self.port).__enter__()
        self.page = None
        self._ensure_page()
        for _ in range(60):
            if self._wiz().get("SNlM0e"):
                break
            time.sleep(1)
        w = self._wiz()
        if not w.get("SNlM0e"):
            if any("accounts.google.com" in (p.url or "") for p in self._br.ctx.pages):
                raise EngineUnavailable("Flow 还没登录：请到弹出的 Chrome 里登录一次")
            raise EngineUnavailable("Flow 页面没就绪（拿不到 XSRF），刷新那个 Chrome 标签再试")
        chrome.Browser.keep_awake(self.page)
        self.log(f"Flow 已连上（{self.account['name']}，端口 {self.port}，构建 {w.get('cfb2h')}）")
        self._log_balance()

    def close(self) -> None:
        for pg in list(self._own):
            try:
                if not pg.is_closed():
                    pg.close()
            except Exception:
                pass
        self._own.clear()
        self.page = None
        if self._br:
            self._br.__exit__(None, None, None)
            self._br = None

    def health(self) -> dict:
        try:
            w = self._wiz()
            return {"ok": bool(w.get("SNlM0e")), "logged_in": bool(w.get("SNlM0e")), "url": self.page.url}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "logged_in": False, "note": str(e)}

    def _wiz(self) -> dict:
        return self._wiz_of(self.page) if self.page else {}

    # ---------------- 底层 ----------------
    def _ensure_page(self):
        """保证有一个「只属于本次任务」的 Flow 标签页。

        0907 之前是随便挑一个 flow.google.com 标签来用，还会 reload 它去关 CSP。
        多开 Orante 时两个进程挑到同一个标签，一个刷新、另一个正在 evaluate，
        就报 Execution context was destroyed。现在一律自己开、自己关，
        用完 close() 收掉，绝不碰别人的标签。
        """
        try:
            if self.page and not self.page.is_closed() and "flow.google.com" in (self.page.url or ""):
                return self.page
        except Exception:
            pass
        self.page = self._open_own_page()
        return self.page

    def _open_own_page(self):
        """新开一个标签：先用 CDP 关掉 CSP，再导航——这样不用 reload。

        落地如果是「找不到专案」404，就诊断合集失效 / 项目不可见，
        标签页改停在能看见的地方。提交用的 src_path 不动。
        """
        pg = self._br.ctx.new_page()
        try:
            cdp = self._br.ctx.new_cdp_session(pg)
            cdp.send("Page.setBypassCSP", {"enabled": True})
        except Exception as e:  # noqa: BLE001
            self.log(f"  · 关 CSP 失败（{str(e)[:80]}），继续尝试")
        rec = diagnose_project_page(
            pg, project_url=self.project_url, project_id=self.project_id,
            collection_id=self.collection_id, wiz_of=self._wiz_of,
            log=self.log, account_name=self.account.get("name") or "",
            hl="en")
        self._remember_project_page(rec)
        try:
            pg.evaluate("(t) => { window.name = t; }", self._tag)
        except Exception:
            pass
        self._own.append(pg)
        chrome.Browser.keep_awake(pg)
        self.log(f"  · 已开好本次任务专用的 Flow 标签页（{self._tag}）")
        return pg

    def _remember_project_page(self, rec: dict) -> None:
        """把诊断写入账号 extra；404 只在第一次发现时打人话日志。"""
        rec = dict(rec or {})
        prev = ((self.account.get("extra") or {}).get("project_page") or {})
        changed = (prev.get("ok") != rec.get("ok")
                   or prev.get("why") != rec.get("why"))
        extra = dict(self.account.get("extra") or {})
        extra["project_page"] = rec
        self.account["extra"] = extra
        try:
            from .. import accounts as accmod
            aid = self.account.get("id")
            if aid:
                accmod.patch_extra(aid, project_page=rec)
        except Exception:
            pass
        if rec.get("ok") or not changed:
            return
        why = rec.get("why") or "not_visible"
        name = self.account.get("name") or "这个账号"
        if why == "collection":
            self.log("  · Flow 项目页打不开（找不到专案），但生成接口正常，不影响出片。")
            self.log("    原因：链接里的合集那一段失效了，已自动改用项目地址。")
        else:
            self.log("  · Flow 项目页打不开，但生成接口正常，不影响出片。")
            self.log(f"    原因：这个项目在 {name} 登录的 Google 账号里看不到。"
                     f"浏览器里没法看进度，要修好就在 {name} 那个 Chrome 里打开 Flow、"
                     "新建或打开一个项目，把地址栏复制到账号设置里。")

    def _wiz_of(self, page) -> dict:
        try:
            return page.evaluate("() => { const w = window.WIZ_global_data||{}; return {SNlM0e: w.SNlM0e, cfb2h: w.cfb2h, FdrFJe: w.FdrFJe}; }") or {}
        except Exception:
            return {}

    def _log_balance(self) -> int | None:
        n = self.credits()
        if n is not None:
            self.log(f"  · 账户余额 {n}（nzlxg）")
        return n

    def credits(self) -> int | None:
        """走 nzlxg 读余额。解析不出返回 None，原文只打一次日志。抛错不往外传。"""
        try:
            payload = self._rpc("nzlxg", [], retries=1)
        except Exception as e:  # noqa: BLE001
            self.log(f"  · nzlxg 查余额失败：{str(e)[:160]}")
            return None
        n = _first_int(payload)
        if n is None:
            if not self._credits_unparsed_logged:
                self._credits_unparsed_logged = True
                try:
                    snip = json.dumps(payload, ensure_ascii=False)[:300]
                except Exception:
                    snip = str(payload)[:300]
                self.log(f"  · nzlxg 返回解析不出数字：{snip}")
            return None
        self._credits_last = n
        self._credits_at = time.time()
        return n

    def credits_throttled(self, interval: float = 60.0) -> int | None:
        now = time.time()
        if self._credits_last is not None and (now - self._credits_at) < interval:
            return self._credits_last
        return self.credits()

    def begin_credits(self, job=None) -> int | None:
        """Job 开始：记下 B0，打一行账户余额。"""
        self._credit_job = job
        self._credit_continue = bool(getattr(job, "credit_continue", False)) if job else False
        n = self._log_balance()
        self._credit_b0 = n
        if job is not None:
            job.credit_b0 = n
        return n

    def end_credits(self) -> int | None:
        """Job 结束再查一次。"""
        return self._log_balance()

    def tick_credits(self) -> int | None:
        """Job 中每 60 秒最多再查一次，并走余额守卫。"""
        n = self.credits_throttled(60.0)
        self._check_credit_guard(n)
        return n

    def acknowledge_credit_drop(self) -> None:
        """「我知道，继续」：本 Job 不再拦。"""
        self._credit_continue = True

    def _note_rpc(self, rpc: str, model_id: str, slot1) -> None:
        self._recent_rpc.append({"rpc": rpc, "model": model_id, "slot1": slot1})

    def _check_credit_guard(self, n: int | None) -> None:
        if getattr(self, "_credit_continue", False):
            return
        job = self._credit_job
        if job is not None and getattr(job, "credit_continue", False):
            self._credit_continue = True
            return
        b0 = self._credit_b0
        if b0 is None or n is None:
            return
        if n >= b0:
            return
        rec = list(self._recent_rpc)
        if not credit_guard_enabled():
            self.log(f"  · nzlxg 从 {b0} 降到 {n}（credit_guard 关闭，只记录不拦）")
            return
        raise CreditHalted(b0, n, rec)

    def _log_flow_err(self, info: dict, extra: str = "") -> None:
        """两行：第一行人话（不含 rpc 名），第二行缩进的原始码。"""
        plain = (info or {}).get("plain") or "Flow 这次没跑成"
        self.log(f"  · {plain}{extra}")
        self.log(format_flow_raw(info or {}))

    def _rpc(self, rpc: str, inner, retries: int = 2, raise_: bool = True):
        """inner 可以是列表，也可以是无参函数（每次重试重新拼，用来换新 token）。

        raise_=True（默认）：成功返回 payload，失败抛 OranteError（标题不带原始字典）。
        raise_=False：返回 {ok, payload, status, err, raw}，失败不抛。
        限流（UNUSUAL_ACTIVITY）无论 raise_ 都抛 FlowThrottled，不许重试。
        """
        last = None
        last_info = None
        for attempt in range(retries + 1):
            self._ensure_page()
            try:
                # inner() 里会去页面拿 reCAPTCHA token，一样可能被导航打断，
                # 所以必须和 evaluate 一起包在 try 里，否则异常会穿过重试。
                if callable(inner) and not isinstance(inner, (list, dict, tuple)):
                    if attempt > 0:
                        self.log("  · 重新取 token")
                    payload_inner = inner()
                else:
                    payload_inner = inner
                res = self.page.evaluate(
                    RPC_JS,
                    [rpc, json.dumps(payload_inner, ensure_ascii=False, separators=(",", ":")), self.src_path])
            except FlowThrottled:
                raise
            except Exception as e:  # noqa: BLE001 —— 标签被关/正在导航
                self.log(f"  · 页面被打断了，换个标签再试：{str(e)[:120]}")
                self.page = None
                time.sleep(2)
                continue
            if res.get("error") == "no_xsrf":
                raise EngineUnavailable("Flow 页面丢了登录态（无 XSRF）")
            if res.get("status") == 200 and res.get("err") is None:
                if raise_:
                    return res.get("payload")
                return {"ok": True, "payload": res.get("payload"),
                        "status": 200, "err": None, "raw": res.get("raw")}
            last = res
            info = classify_flow_error(rpc, res)
            last_info = info
            if info.get("action") == "throttle":
                raise FlowThrottled(
                    self.account.get("name") or "",
                    code=info.get("code") or "PUBLIC_ERROR_UNUSUAL_ACTIVITY",
                    err_n=info.get("err_n"),
                    raw=str(res.get("err"))[:300],
                )
            if info.get("action") == "reject" and rpc == "eb1hJf":
                raise BlockedImage(info.get("plain") or "画面被 Flow 审核拦下了")
            if attempt < retries:
                if info.get("action") == "busy":
                    delay = BUSY_BACKOFF[min(attempt, len(BUSY_BACKOFF) - 1)]
                else:
                    delay = 2 + attempt * 3
                extra = f"，重试 {attempt + 1}/{retries}"
                self._log_flow_err(info, extra)
                time.sleep(delay)
            else:
                self._log_flow_err(info)
        if not raise_:
            last = last or {}
            return {"ok": False, "payload": last.get("payload"),
                    "status": last.get("status"), "err": last.get("err"),
                    "raw": last.get("raw")}
        info = last_info or classify_flow_error(rpc, last or {})
        raise OranteError(info.get("plain") or "Flow 这次没跑成",
                          format_flow_raw(info).strip())

    def _recaptcha(self, action: str, retries: int = 2) -> str:
        """拿一个 reCAPTCHA token。标签被导航打断就换页重来。

        这里以前没有任何重试：页面一被刷新，异常直接穿过 _rpc（inner() 在 try
        之外调用）把整个 Job 打成失败——0907 T0011 就是这么挂的。
        """
        key = config.get("engine.flow.recaptcha_key") or RECAPTCHA_SITE_KEY
        last = ""
        for attempt in range(retries + 1):
            self._ensure_page()
            try:
                res = self.page.evaluate(RC_JS, [key, action])
            except Exception as e:  # noqa: BLE001 —— 标签被关 / 正在导航
                last = str(e)[:150]
                self.log(f"  · 取 reCAPTCHA 被打断：{last}，换个标签重来")
                self.page = None
                time.sleep(2)
                continue
            if not res.get("error"):
                return res["token"]
            last = str(res.get("error"))[:150]
            self.log(f"  · reCAPTCHA 失败：{last}，重试 {attempt + 1}/{retries}")
            time.sleep(2 + attempt * 2)
        raise OranteError(f"拿不到 reCAPTCHA token：{last}", "刷新那个 Flow 标签页再试")

    # ---------------- 上传 ----------------
    def _load_cache(self) -> dict:
        try:
            d = json.loads(self._cache_file.read_text(encoding="utf-8"))
            return {k: v for k, v in d.items() if isinstance(v, str) and len(v) >= 30}
        except Exception:
            return {}

    def _save_cache(self) -> None:
        """原子写 + 合并：几个 Orante 窗口同时写，谁也别把别人的条目冲掉。"""
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        merged = self._load_cache()
        merged.update(self._cache)
        self._cache = merged
        tmp = self._cache_file.with_suffix(f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=0), encoding="utf-8")
            os.replace(tmp, self._cache_file)
        except Exception as e:  # noqa: BLE001
            self.log(f"  · 上传缓存没写成（忽略）：{str(e)[:80]}")
            try:
                tmp.unlink()
            except OSError:
                pass

    def upload_image(self, img: Path) -> str:
        """上传一张图，返回 Flow 的 mediaId。同一账号同一张图（sha1）只传一次。

        被服务端按内容拒掉（err=[3]/[13]、HTTP 200、payload 空）时，会缩到宽 512
        再传一次探测图：探测成功 → 抛 BlockedImage；探测也失败 → 抛「上传接口不可用」。
        """
        return self._upload_file(Path(img), cache=True, probe_on_reject=True)

    def _upload_file(self, img: Path, *, cache: bool, probe_on_reject: bool,
                     retries: int = 2) -> str:
        img = Path(img)
        sha = hashlib.sha1(img.read_bytes()).hexdigest()
        ck = f"{self.account['id']}:{sha}"
        if cache and ck in self._cache:
            return self._cache[ck]
        mime = mimetypes.guess_type(str(img))[0] or "image/jpeg"
        b64 = base64.b64encode(img.read_bytes()).decode()
        fname = img.name

        def make_inner():
            token = self._recaptcha(RC_ACTION_UPLOAD)
            return [[None, 22, None, None, None, self.project_id, None, self.collection_id,
                     None, None, [token, 1]],
                    b64, mime, 1, None, None, None, None, fname, None,
                    str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()]

        try:
            res = self._rpc("maseQ", make_inner, retries=retries, raise_=False)
        except FlowThrottled:
            raise
        except OranteError as e:
            # 拿不到 token / 页面丢了：不要说成「图被拒」
            raise OranteError(
                "Flow 上传接口不可用",
                (e.message or "") + (("；" + e.hint) if e.hint else "")) from e
        if res.get("ok"):
            payload = res.get("payload")
            media_id = _g(payload, 0, 0)
            if not isinstance(media_id, str) or len(media_id) < 30:
                raise OranteError("上传图片后没拿到 mediaId",
                                  json.dumps(payload, ensure_ascii=False)[:300])
            if cache:
                self._cache[ck] = media_id
                self._save_cache()
            self.log(f"  ↑ 已上传 {img.name} → {media_id[:8]}…")
            return media_id

        if probe_on_reject and _is_content_reject(res):
            return self._probe_then_decide(img, sha, res)

        raw = str({k: res.get(k) for k in ("status", "err", "payload")})[:200]
        if not probe_on_reject:
            raise OranteError(
                "Flow 上传接口不可用",
                "缩小的探测图也传不上去；刷新那个 Flow 标签页、确认没被登出，再试。原始错误：" + raw)
        raise OranteError("Flow 上传没成功", "原始错误：" + raw)

    def _make_probe(self, img: Path, sha: str, width: int = 512) -> Path:
        """原图等比缩到指定宽、JPEG 质量 85，落到 data/tmp/probe-<sha1前8>.jpg。

        规格写宽 512；T0008 p03 在 512 上仍 err=[3]，用户事后验证 327×600 能传，
        所以探测失败后再试 320。
        """
        from PIL import Image
        dest = paths.DATA / "tmp" / f"probe-{sha[:8]}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(img) as im:
            im = im.convert("RGB")
            w, h = im.size
            tw = max(1, int(width))
            if w > tw:
                nh = max(1, round(h * tw / w))
                resample = getattr(Image, "LANCZOS", getattr(Image, "BICUBIC", 3))
                im = im.resize((tw, nh), resample)
            im.save(dest, format="JPEG", quality=85)
        return dest

    def _archive_media(self, media_id: str) -> None:
        try:
            self._rpc("pGCYOe", [[[media_id, None, None, [None, None, 1], self.project_id]],
                                 [["metadata.archived"]]])
        except Exception as e:  # noqa: BLE001
            self.log(f"  · 探测图归档失败（忽略）：{str(e)[:80]}")

    def _neutral_upload_ok(self) -> bool:
        """传一张 320×568 的合成渐变图（没有人、没有内容可拒），判断上传接口本身通不通。
        成功就把探测图归档。60 秒内不重复探。"""
        now = time.time()
        last = getattr(self, "_neutral_at", 0.0)
        if now - last < 60:
            return bool(getattr(self, "_neutral_ok", False))
        self._neutral_at = now
        try:
            from PIL import Image
            dest = paths.DATA / "tmp" / "probe-neutral.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            w, h = 320, 568
            im = Image.new("RGB", (w, h))
            px = im.load()
            for y in range(h):
                for x in range(w):
                    px[x, y] = (40 + x * 120 // w, 60 + y * 100 // h, 90)
            im.save(dest, format="JPEG", quality=85)
            self.log("  · 探测：传一张中性合成图看接口通不通")
            mid = self._upload_file(dest, cache=False, probe_on_reject=False, retries=0)
            try:
                self._archive_media(mid)
            except Exception:
                pass
            self._neutral_ok = True
        except EngineUnavailable:
            raise
        except FlowThrottled:
            raise
        except Exception as e:  # noqa: BLE001
            self.log(f"  · 中性图也传不上去：{str(e)[:80]}")
            self._neutral_ok = False
        return bool(self._neutral_ok)

    def _probe_then_decide(self, img: Path, sha: str, orig_res: dict) -> str:
        """原图被拒后传缩图：能传说明接口通、是这张图的内容问题。

        先宽 512（规格），再 320（T0008 p03 实测 512 仍被拒、约 327 能过）。探测图不写 cache。
        """
        last_err: Exception | None = None
        for width in (512, 320):
            probe = self._make_probe(img, sha, width=width)
            try:
                mid = self._upload_file(
                    probe, cache=False, probe_on_reject=False, retries=0)
            except EngineUnavailable:
                raise
            except FlowThrottled:
                raise
            except OranteError as e:
                last_err = e
                self.log(f"  · 探测图宽 {width} 没传上去，再试更小的" if width == 512 else
                         f"  · 探测图宽 {width} 也失败")
                continue
            self.log(f"  ✗ {img.name} 被 Flow 拒收（缩图可传、原图不可）→ 换一张图")
            try:
                self._archive_media(mid)
            except Exception:
                pass
            raise BlockedImage(
                f"Flow 拒收了 {img.name}（缩小后能传，全尺寸不能，多半是人脸/名人识别）")
        # 缩图也被拒：还不能断定接口坏了——T0007 p21 实测 512/320 全被拒，
        # 几秒后 p22 却正常上传。用一张中性合成图再探一次：能传 → 接口没问题，是这张图被拒。
        if self._neutral_upload_ok():
            self.log(f"  ✗ {img.name} 被 Flow 拒收（中性图可传、这张图任何尺寸都不可）→ 换一张图")
            raise BlockedImage(f"Flow 拒收了 {img.name}（缩小后仍被拒，中性图能传，是这张图的内容问题）")
        self.log("  ✗ 上传接口不可用（探测图、中性图都失败）")
        raw = str({k: orig_res.get(k) for k in ("status", "err", "payload")})[:200]
        hint = "缩小的探测图也传不上去；刷新那个 Flow 标签页、确认没被登出，再试。原始错误：" + raw
        if last_err and getattr(last_err, "hint", ""):
            hint += "；探测：" + str(last_err.hint)[:120]
        raise OranteError("Flow 上传接口不可用", hint)

    # ---------------- 生成 ----------------
    def _gen_inner(self, prompt: str, media_id: str, crop, duration: int, token: str,
                   aspect_code: int = 1,
                   last_media_id: str | None = None, silent: bool = False):
        # 图片槽：[None, 首帧 media_id, 尾帧 media_id, None, None, crop]
        # 规格 36 探针 A′：尾帧塞下标 2，无额外标志位，模型 id 不用改。
        last = last_media_id or None
        job = [[None, None, [[[prompt]]]], model_id_for(self.model_name, duration), aspect_code, None,
               [None, media_id, last, None, None, crop],
               [None, None, self.collection_id, None, str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()]]
        return [[job], [None, 22, None, None, None, self.project_id, None, None, None, None, [token, 1]],
                [str(uuid.uuid4()).upper(), AUDIO_SILENT if silent else AUDIO_WITH_SOUND]]

    def capacity(self) -> int:
        cap = self.max_conc
        if config.get("flow.human_warmup", True):
            # 网页上一次最多 4 条在生成（用户 09-27 确认）。超了第 5 条会卡在网页上，
            # 所以真人操作模式下封顶 4，由 generate 那边照旧"满了就先收片再提交"。
            cap = min(cap, UI_MAX_CONCURRENT)
        return max(0, cap - len(self._active))

    def submit(self, seg: dict, image: Path, prompt: str, duration: int,
               last_image: Path | None = None, silent: bool = False) -> str:
        """silent=True：这一段要无声视频（0922 抓包：网页「返回无声视频」开着时，
        eb1hJf 最后一项是 [uuid, 1]；关着是 [uuid, 2]）。只影响这一次请求，不改账号设置。"""
        mid = model_id_for(self.model_name, duration)
        reject_paid_model(mid)
        self.tick_credits()
        from . import caps
        aspect = caps.aspect_for_image(image, "flow")
        if last_image:
            la = caps.aspect_for_image(last_image, "flow")
            if la != aspect:
                raise OranteError(
                    ("首帧是横图、尾帧是竖图，Flow 不支持"
                     if aspect == "16:9" else
                     "首帧是竖图、尾帧是横图，Flow 不支持"),
                    "接龙的首帧和尾帧必须同一方向（都横或都竖）。",
                )

        # 规格 96（2026-09-27 定稿）：用户明确要求——每一段都在网页界面上真点、
        # 真粘贴、真点生成，跟人手动操作一模一样；**不再**落回接口直连。
        # 冷标签页走接口直连的结果已经在真机上看了三次：必被 Google 判
        # UNUSUAL_ACTIVITY，还把冷却从 20 分钟翻成 40 分钟。所以界面上哪一步
        # 对不上，这一段就直接报错（截图 + 页面结构落盘），不交给 Google、
        # 不耗额度、不触发限流；下一段照旧先走界面。
        # 设置里把 flow.human_warmup 关掉，才走原来的接口直连（留给极少数
        # "这个环境反而信任接口直连"的情况）。
        if config.get("flow.human_warmup", True):
            try:
                gid = self._submit_via_ui(
                    seg, Path(image), prompt, int(duration),
                    last_image=Path(last_image) if last_image else None, aspect=aspect,
                    silent=bool(silent))
            except (FlowThrottled, BlockedImage, CreditHalted):
                raise  # 真人操作也被拒——这是 Flow 的真答案
            except Exception as e:  # noqa: BLE001 —— 界面上某一步对不上
                raise self._ui_wrap_error(e, "段") from e
            else:
                self._ui_warmed = True
                if isinstance(seg, dict) and last_image:
                    seg["bridge_engine"] = "flow"
                try:
                    from .. import accounts as accmod
                    accmod.note_flow_success(self.account.get("id") or "")
                except Exception:
                    pass
                return gid

        # ---------------- 接口直连（只有设置里关掉「每段都走真人操作」才会走到这里） ----------------
        # 尾帧走同一套上传 / 拒图 / 缓存；有就先传它，再传首帧。
        last_media_id = None
        if last_image:
            last_media_id = self.upload_image(Path(last_image))
            if isinstance(seg, dict):
                seg["bridge_engine"] = "flow"
        media_id = self.upload_image(image)
        crop = _crop_box(image, aspect)
        aspect_code = caps.flow_aspect_code(aspect)

        def make_inner():
            token = self._recaptcha(RC_ACTION_GENERATE)
            return self._gen_inner(prompt, media_id, crop, duration, token,
                                  aspect_code=aspect_code, last_media_id=last_media_id,
                                  silent=silent)

        payload = self._rpc("eb1hJf", make_inner)
        recs = _g(payload, 3, default=[]) or []
        gen_id = _g(recs, 0, 0)
        if not gen_id:
            txt = json.dumps(payload, ensure_ascii=False)[:400]
            if re.search(r"prominent|policy|违反|不允许|unsafe|minor|celebrit", txt, re.I):
                raise BlockedImage(txt)
            raise OranteError("提交生成后没拿到 genId", txt)
        slot1 = _g(payload, 1)
        self._note_rpc("eb1hJf", mid, slot1)
        self._active.add(gen_id)
        try:
            from .. import accounts as accmod
            accmod.note_flow_success(self.account.get("id") or "")
        except Exception:
            pass
        self.log(f"  ✓ 已提交 s{seg.get('s', 0):03d} → {gen_id[:8]}…  {duration}s  剩余额度 {slot1}")
        return gen_id

    # ---------------- 人操作式提交（规格 96：所有生成都在网页上真点真粘贴真点生成） ----------------
    #
    # 两个入口，Orante 里所有"发给 Flow 生成"的功能都走这两个口子：
    #   · 视频：submit()      —— 生成、重生、接片、接龙都从这里交
    #   · 图片：gen_images()  —— 扩图、高清修复原图、尾帧提清、描述卡生图都从这里交
    # 流程照着 Luca 手动操作、也照着旧版 0-长文案（flow_batch.py / longtik_flow_newui.py）
    # 在网页上点的那一套，选择器按 2026-09 的 flow.google.com（hl=en）实测：
    #   0. 清场：作曲区有上一次留下的图/字就点右上角 ×（Clear prompt）；Agent 开着就关掉
    #   1. 设置面板（Settings trigger 是个开关，看状态再点）：
    #        视频 = Video / Frames / 画幅 / 模型 / 时长 / x1；图片 = Image / 画幅 / 模型 / x1
    #      读回模型核对，对不上不交
    #   2. 挂图：视频点「Start」「End」格子，图片点「Add ingredients」→「Upload media」→
    #      接住文件选择器传图 →「Add to prompt」→ 核对图真的挂上了（格子变缩略图 / 多了一张图）
    #      Flow 拒收这张图（未成年 / 名人等）→ 抛 BlockedImage，跟原来直连时一样走换图流程
    #   3. 提示词框（ProseMirror）点进去 → 全选删掉 → 一次性粘贴 → 读回逐字核对
    #   4. 等「Start generation」变亮 → 点 → 被动收 Flow 自己发出去的回包（视频 eb1hJf / 图片
    #      ogiZ0b），再核对回包对应的请求里带的正是这一次的提示词，才认这个 genId / 这张图
    # 点生成之前哪一步对不上：重新打开一次项目页从头再走；还不行就这一次报错（截图 + 页面结构
    # 落盘），**不**改走接口直连。点了生成之后绝不再点第二次。

    def _ui_pace(self, lo: float, hi: float) -> None:
        time.sleep(random.uniform(lo, hi))

    def _ui_step(self, name: str) -> None:
        self._ui_stage = name

    def _ui_wander_mouse(self, page) -> None:
        """开场先让鼠标在页面上晃两下，比标签页一开就直接发请求更像人在看页面。"""
        try:
            vp = page.viewport_size or {"width": 1280, "height": 800}
            w, h = vp["width"], vp["height"]
            for _ in range(random.randint(2, 4)):
                page.mouse.move(random.uniform(w * 0.2, w * 0.8),
                                 random.uniform(h * 0.2, h * 0.8),
                                 steps=random.randint(8, 20))
                self._ui_pace(0.1, 0.35)
        except Exception:
            pass

    def _ui_reload(self, page) -> None:
        page.goto(force_hl(self.project_url, "en"), wait_until="domcontentloaded", timeout=60000)
        page.locator(EDITOR_SEL).first.wait_for(state="visible", timeout=45000)
        self._ui_pace(0.8, 1.5)

    def _ui_clear_composer(self, page) -> None:
        """上一次交完，作曲区里可能还挂着上一张图 / 上一段字（Flow 交完不一定清）。
        点作曲区右上角那个 ×（aria-label = Clear prompt）整个清空——人也是这么做的，
        不刷新页面。Agent 模式开着作曲区是另一套按钮，顺手关掉。"""
        try:
            agent = page.locator("button.agent-mode-chip").first
            if agent.count() > 0 and agent.get_attribute("aria-pressed") == "true":
                agent.click(timeout=UI_CLICK_TIMEOUT)
                self._ui_pace(0.3, 0.6)
        except Exception:
            pass
        clear = page.get_by_role("button", name="Clear prompt", exact=True)
        if clear.count() > 0:
            clear.first.click(timeout=UI_CLICK_TIMEOUT)
            try:
                clear.first.wait_for(state="hidden", timeout=5000)
            except Exception:
                pass
            self._ui_pace(0.3, 0.6)

    # ---- 设置面板：Settings trigger 是个开关（09-27 实测：点一下开、再点一下关；
    #      按 Esc、点页面空白处都关不掉它）。原来每段都"点一下 + 按 Esc"，Esc 其实没关上，
    #      下一段再点一下反而把面板关了 → 找不到 Video → 卡 30 秒 → 刷新页面。
    #      这就是"每生成一段就刷新一次"的真正原因。现在看状态再点，绝不盲点。
    def _ui_set_silent(self, page, silent: bool) -> None:
        """网页右上角「Tile grid settings」菜单里的「Return silent videos」开关（09-28 实测：
        button[role=switch] name="return-silent-videos"）。不说话的任务要无声视频——原来走接口
        直连时是在请求里单独带一个标记、不动网页设置；网页上没有"只这一次"的办法，只能拨这个
        开关，所以每段交之前按这段要不要声音拨到对的位置（这个标签页里记着当前状态，没变就不去点）。
        点完读回来核对，菜单用它自己的透明遮罩关掉（Esc 关不掉）。"""
        want = bool(silent)
        if getattr(self, "_ui_silent_state", None) is want:
            return
        page.locator('button[aria-label="Settings"], button[aria-label="Tile grid settings"]').first.click(timeout=UI_CLICK_TIMEOUT)
        sw = page.get_by_role("switch", name="Return silent videos toggle").first
        sw.wait_for(state="visible", timeout=UI_STEP_TIMEOUT)
        self._ui_pace(0.2, 0.5)
        for _ in range(2):
            now = (sw.get_attribute("aria-checked") == "true")
            if now == want:
                break
            sw.click(timeout=UI_CLICK_TIMEOUT)
            self._ui_pace(0.3, 0.6)
        now = (sw.get_attribute("aria-checked") == "true")
        try:
            page.locator(".cdk-overlay-backdrop").last.click(timeout=3000)
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        self._ui_pace(0.2, 0.5)
        if now != want:
            raise OranteError(
                f"网页上的「Return silent videos」开关拨不到{'开' if want else '关'}",
                "这一段没交。到这个账号的 Flow 页面右上角网格设置里手动看一下这个开关。")
        self._ui_silent_state = want
        self.log(f"  · 网页「Return silent videos」已{'打开（这段出无声视频）' if want else '关上（这段出有声视频）'}")

    def _ui_panel_open(self, page) -> bool:
        try:
            return bool(page.get_by_role("radio", name="Video", exact=True).first.is_visible())
        except Exception:
            return False

    def _ui_open_settings(self, page) -> None:
        trig = page.get_by_role("button", name="Settings trigger").first
        for _ in range(3):
            if self._ui_panel_open(page):
                return
            trig.click(timeout=UI_CLICK_TIMEOUT)
            try:
                page.get_by_role("radio", name="Video", exact=True).first.wait_for(
                    state="visible", timeout=4000)
                return
            except Exception:
                self._ui_pace(0.3, 0.6)
        raise OranteError("Flow 的设置面板打不开", "点了三次「Settings trigger」都没看到 Image / Video 选项。")

    def _ui_close_settings(self, page) -> None:
        for _ in range(2):
            if not self._ui_panel_open(page):
                return
            page.get_by_role("button", name="Settings trigger").first.click(timeout=UI_CLICK_TIMEOUT)
            self._ui_pace(0.3, 0.6)

    def _ui_radio(self, page, name: str) -> None:
        page.get_by_role("radio", name=name, exact=True).click(timeout=UI_CLICK_TIMEOUT)
        self._ui_pace(0.15, 0.4)

    def _ui_set_mode(self, page, aspect: str) -> None:
        self._ui_radio(page, "Video")
        self._ui_radio(page, "Frames")
        self._ui_radio(page, aspect)

    def _ui_pick_model(self, page, model_name: str) -> None:
        btn = page.get_by_role("button", name="Select model family")
        try:
            shown = btn.first.inner_text(timeout=5000)
        except Exception:
            shown = ""
        # 已经是这个模型就别去点——多点一下下拉会展开，挡住面板上别的选项
        if _model_label(shown).lower() == _model_label(model_name).lower():
            return
        btn.first.click(timeout=UI_CLICK_TIMEOUT)
        self._ui_pace(0.2, 0.5)
        # 图片模型的选项前面带个 🍌，按"去掉前面的符号后完全相等"来认，
        # 免得「Nano Banana 2」认成「Nano Banana 2 Lite」
        pat = re.compile(r"^(?:volume_up\s+|image\s+|videocam\s+|\W*)*" + re.escape(_model_label(model_name)) + r"\s*$", re.I)
        opt = page.get_by_role("menuitem", name=pat)
        if opt.count() == 0:
            opt = page.get_by_role("option", name=pat)
        if opt.count() == 0:
            opt = page.get_by_text(pat)
        opt.first.click(timeout=UI_CLICK_TIMEOUT)
        self._ui_pace(0.15, 0.4)

    def _ui_check_model(self, page, model_name: str, *, exact: bool = False) -> None:
        """读回来核对模型。选错成不吃图片的模型（比如 Omni），每段都会"提交成功"
        然后出一张灰卡、白烧额度——旧版 0905 那批就是这么来的，所以宁可不交。"""
        shown = _model_label(
            page.get_by_role("button", name="Select model family").first.inner_text(timeout=5000))
        want = _model_label(model_name)
        ok = (shown.lower() == want.lower()) if exact else shown.lower().startswith(_model_base(model_name))
        if not ok:
            raise OranteError(
                f"Flow 模型没切成「{want}」，现在是「{shown or '读不出来'}」",
                "这一次没交。去这个账号的 Flow 页面设置里手动选一次这个模型再试。")

    def _ui_settings_already_ok(self, page, aspect: str, duration: int) -> bool:
        """设置按钮上写着「Video · 720p · 8s crop_9_16 x1」。模型在这个标签页里核对过、
        按钮上的模式/时长/画幅/条数又都对，就不用打开面板——人也不会每段都去点一遍。"""
        if not self._ui_model_ok:
            return False
        try:
            t = page.get_by_role("button", name="Settings trigger").first.inner_text(timeout=3000)
        except Exception:
            return False
        toks = [x for x in re.split(r"[\s·]+", t or "") if x]
        return ("Video" in toks and f"{int(duration)}s" in toks and "x1" in toks
                and ("crop_" + aspect.replace(":", "_")) in toks)

    # ---- 挂图：视频的起止帧格子、图片的参考图，都是同一个资产库弹窗 ----
    def _ui_upload_reject_text(self, page) -> str:
        try:
            return page.evaluate(UPLOAD_FAIL_JS) or ""
        except Exception:
            return ""

    def _ui_unique_copy(self, img: Path) -> tuple[str, Path]:
        """传给 Flow 的图换一个"按内容定"的唯一文件名：<原名>-<内容 sha1 前 10 位><后缀>。

        为什么：你每个任务的首图都叫 p01，Flow 资产库里一排同名 p01，分不清谁是谁。
        按内容取名以后，名字一样 ⇔ 图一模一样——在弹窗里按名字点中的，一定是这一张图。"""
        img = Path(img)
        h = hashlib.sha1(img.read_bytes()).hexdigest()[:10]
        name = f"{img.stem}-{h}{img.suffix.lower() or '.jpg'}"
        d = paths.DATA / "tmp" / "flow_upload"
        d.mkdir(parents=True, exist_ok=True)
        dst = d / name
        if not dst.is_file() or dst.stat().st_size != img.stat().st_size:
            dst.write_bytes(img.read_bytes())
        return name, dst

    def _ui_frame_copy(self, img: Path, aspect: str | None) -> tuple[str, Path]:
        """首帧 / 尾帧专用：先按 _crop_box 同一条规则居中裁成**精确**的 16:9 / 9:16 再传。

        为什么（T0122，09-28 真机）：以前走接口时，Orante 把裁切框跟着请求一起交给 Flow，
        正方形图（1200×1200、4096×4096）出来的片子是满画面。改成网页上传以后，原图
        原样交上去，Flow 就把正方形图塞进 16:9、左右补黑边——片子带黑边，首帧校验也被
        黑边带偏（距 105~136），把对的片当错件作废。现在网页上传前自己裁好，
        跟接口时出来的片子一模一样。长边超过 UI_FRAME_MAX 的顺手缩一下（视频只有
        720p/1080p，4096 的原图白白多传几兆，上传慢）。
        已经是精确画幅、又不大的图，原样传（名字规则同 _ui_unique_copy）。"""
        img = Path(img)
        box = _crop_box(img, aspect) if aspect else [None, None, 1, 1]
        try:
            from PIL import Image
            with Image.open(img) as im:
                w, h = im.size
        except Exception:
            return self._ui_unique_copy(img)
        need_crop = list(box) != [None, None, 1, 1]
        need_shrink = max(w, h) > UI_FRAME_MAX
        if not need_crop and not need_shrink:
            return self._ui_unique_copy(img)
        src_h = hashlib.sha1(img.read_bytes()).hexdigest()[:10]
        tag = (aspect or "src").replace(":", "x")
        name = f"{img.stem}-{src_h}-{tag}.jpg"
        d = paths.DATA / "tmp" / "flow_upload"
        d.mkdir(parents=True, exist_ok=True)
        dst = d / name
        if dst.is_file() and dst.stat().st_size > 0:
            return name, dst
        top, left, bottom, right = box
        top = float(top or 0.0)
        left = float(left or 0.0)
        bottom = float(bottom if bottom is not None else 1.0)
        right = float(right if right is not None else 1.0)
        with Image.open(img) as im:
            im = im.convert("RGB")
            cut = (int(round(left * w)), int(round(top * h)),
                   int(round(right * w)), int(round(bottom * h)))
            if need_crop:
                im = im.crop(cut)
            cw, ch = im.size
            if max(cw, ch) > UI_FRAME_MAX:
                k = UI_FRAME_MAX / float(max(cw, ch))
                im = im.resize((max(1, int(round(cw * k))), max(1, int(round(ch * k)))),
                               Image.LANCZOS)
            tmp = dst.with_suffix(".part")
            im.save(tmp, "JPEG", quality=95, subsampling=0, optimize=True)
            os.replace(tmp, dst)
        if need_crop:
            self.log(f"  · {img.name} 是 {w}×{h}，按 {aspect} 居中裁好再传（跟接口时一样，片子不带黑边）")
        return name, dst

    def _ui_asset_state(self, page, name: str) -> dict:
        try:
            return page.evaluate(ASSET_STATE_JS, name) or {}
        except Exception:
            return {}

    def _ui_upload_in_picker(self, page, img: Path, attached, aspect: str | None = None) -> None:
        """资产库弹窗已经打开：挂**这一张**图，绝不挂错。

        09-27 实测的坑：点「Upload media」传完新图，弹窗里**选中的还是上一次用过的那张**
        （新传的图排在最上面，但不会自动选中），这时点「Add to prompt」挂上去的是旧图。
        T0113~T0116 的片子开头全是 T0112 那张图，就是这么来的。所以现在：
          1. 图换成按内容取的唯一名字（见 _ui_unique_copy）
          2. 库里已经有这个名字（=同一张图）就直接用，不重复上传；没有才上传，并等它
             「Uploading」结束、缩略图出来
          3. 点中**这个名字**的那一项，确认它是弹窗里唯一被选中的那一项
          4. 才点「Add to prompt」，再核对作曲区里真的挂上了
        任何一步对不上就报错，不点生成。Flow 当场拒收这张图 → BlockedImage（上层换图）。"""
        name, up = (self._ui_frame_copy(img, aspect) if aspect else self._ui_unique_copy(img))
        page.get_by_role("button", name="Upload media").first.wait_for(
            state="visible", timeout=UI_STEP_TIMEOUT)
        st = self._ui_asset_state(page, name)
        if not st.get("ready"):
            failed = ""
            for attempt in (1, 2):
                failed = self._ui_upload_once(page, name, up)
                if not failed:
                    break
                if attempt == 1:
                    # Flow 偶尔会有一次性的传图失败；再传一次，还不行才算它不收这张图
                    self.log(f"  · Flow 没收 {Path(img).name}（{failed}），再传一次")
                    self._ui_dismiss_snackbar(page)
                    self._ui_pace(1.0, 2.0)
            if failed:
                self._ui_dismiss_snackbar(page)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                # 跟原来走接口上传被内容拒掉时一样：抛 BlockedImage，上层按"图被拒"处理
                raise BlockedImage(f"Flow 拒收了 {Path(img).name}：{failed}（连传 2 次都没收）")
        # 点中这一张
        opt = page.locator('[role="option"]', has=page.locator(
            ".asset-title", has_text=re.compile(r"^\s*" + re.escape(name) + r"\s*$"))).first
        opt.click(timeout=UI_CLICK_TIMEOUT)
        # 09-28 真机实测：在「Select a frame image」里点一下已经传好的那一项，Flow 会**直接**
        # 把它放进格子、关掉弹窗（不用再点「Add to prompt」）；也可能只是选中它、等你点
        # 「Add to prompt」。两种都认——点的是按名字认准的这一项，挂进去的一定是这张图。
        t0 = time.time()
        direct = False
        while True:
            if attached():
                direct = True
                break
            st = self._ui_asset_state(page, name)
            if st.get("selected") and set(st.get("selected_names") or []) == {name}:
                break
            if time.time() - t0 > 6:
                raise OranteError(
                    f"弹窗里选不中刚传的 {Path(img).name}",
                    f"现在选中的是：{'、'.join(st.get('selected_names') or []) or '没有'}。这一次没交。")
            time.sleep(0.3)
        if not direct:
            # T0122 s002（09-28）：先看到「选中了」，紧接着 Flow 自己就把图挂进格子、关了弹窗，
            # 这时再去点「Add to prompt」就点空（按钮已经没了），8 秒超时后整段作废。
            # 所以：先等一小会儿看它会不会自己挂上；点之前、点不中之后都再核对一次。
            t1 = time.time()
            while time.time() - t1 < 1.5:
                if attached():
                    direct = True
                    break
                time.sleep(0.25)
        if not direct:
            self._ui_pace(0.2, 0.5)
            if not attached():
                try:
                    page.get_by_role("button", name="Add to prompt").first.click(timeout=UI_CLICK_TIMEOUT)
                except Exception:
                    if not attached():
                        raise
        t0 = time.time()
        while not attached():
            if time.time() - t0 > 20:
                raise OranteError(f"点了「Add to prompt」，{Path(img).name} 还是没挂进作曲区", "这一次没交。")
            time.sleep(0.4)
        try:
            page.get_by_text("Select a frame image").first.wait_for(state="hidden", timeout=5000)
        except Exception:
            pass
        self._ui_pace(0.3, 0.8)

    def _ui_dismiss_snackbar(self, page) -> None:
        try:
            d = page.get_by_role("button", name="Dismiss")
            if d.count() > 0:
                d.first.click(timeout=3000)
        except Exception:
            pass

    def _ui_upload_once(self, page, name: str, up: Path) -> str:
        """传一次。传完返回 ""；Flow 没收就返回原因（它提示条里的原话，没有就说明现象）。
        网络慢到 UI_UPLOAD_WAIT 秒还没传完，抛 OranteError（这不是"图被拒"）。"""
        base_reject = self._ui_upload_reject_text(page)
        with page.expect_file_chooser(timeout=UI_STEP_TIMEOUT) as fc_info:
            page.get_by_role("button", name="Upload media").first.click(timeout=UI_CLICK_TIMEOUT)
        fc_info.value.set_files(str(up))
        deadline = time.time() + UI_UPLOAD_WAIT
        seen = False
        while True:
            st = self._ui_asset_state(page, name)
            if st.get("mine"):
                seen = True
            if st.get("ready"):
                return ""
            why = self._ui_upload_reject_text(page)
            if why and why != base_reject:
                return why
            if seen and not st.get("mine") and not st.get("uploading"):
                # 传上去又消失了：Flow 的"Failed to upload …"提示条可能晚一两秒才出来，等一下原话
                t1 = time.time()
                while time.time() - t1 < 4:
                    why = self._ui_upload_reject_text(page)
                    if why and why != base_reject:
                        return why
                    time.sleep(0.4)
                return "传上去以后又从资产库里消失了"
            if time.time() > deadline:
                raise OranteError(f"{name} 传了 {UI_UPLOAD_WAIT} 秒还没传完",
                                  "网络慢或者 Flow 没反应。这一次没交。")
            time.sleep(0.5)

    def _ui_slot_empty(self, page, slot: str) -> bool:
        """这个格子是不是空的（空格子是一个写着 Start / End 的按钮，09-27 实测）。"""
        try:
            return page.get_by_role("button", name=slot, exact=True).count() > 0
        except Exception:
            return False

    def _ui_empty_chip(self, page, slot: str) -> bool:
        """更严格的"空格子"判断：class 就叫 empty-chip、字正好是 Start / End。
        挂上图以后它一定会消失——用来核对图真的进了这个格子。"""
        try:
            return page.locator("button.empty-chip",
                                has_text=re.compile(r"^\s*" + slot + r"\s*$")).count() > 0
        except Exception:
            return False

    def _ui_attach_frame(self, page, img: Path, slot: str, aspect: str | None = None) -> None:
        """挂起帧 / 尾帧。核对两件事才算挂上：作曲区里多了一张缩略图，而且这个格子的
        空按钮不见了——没首帧的视频是纯文生视频，全错，所以宁可不交。"""
        n0 = self._ui_composer_images(page)
        page.get_by_role("button", name=slot, exact=True).first.click(timeout=UI_CLICK_TIMEOUT)
        self._ui_pace(0.3, 0.8)
        self._ui_upload_in_picker(
            page, img,
            lambda: self._ui_composer_images(page) > n0 and not self._ui_empty_chip(page, slot),
            aspect=aspect)

    def _ui_composer_images(self, page) -> int:
        try:
            return int(page.evaluate(COMPOSER_IMG_JS) or 0)
        except Exception:
            return 0

    def _ui_attach_ingredient(self, page, img: Path) -> None:
        """图片模式挂参考图：点「Add ingredients to the prompt box」（那个 +）走同一个资产库弹窗；
        挂上以后作曲区里会多一张缩略图，以此核对。"""
        n0 = self._ui_composer_images(page)
        page.get_by_role("button", name="Add ingredients to the prompt box").first.click(
            timeout=UI_CLICK_TIMEOUT)
        self._ui_pace(0.3, 0.8)
        self._ui_upload_in_picker(page, img, lambda: self._ui_composer_images(page) > n0)

    def _ui_type_prompt(self, page, text: str) -> None:
        """点提示词框 → 全选删掉 → 一次性粘贴 → 读回来逐字核对。

        用 keyboard.insert_text（相当于人按 Ctrl+V 粘贴，一次输入事件进框），
        旧版 flow_batch.fill_editor 在 flow.google.com 上就是这么填的；
        不用一个字一个字敲——几百字的文案敲一遍要半分钟，也不像人。"""
        want = _norm_prompt(text)
        ed = page.locator(EDITOR_SEL).first
        ed.wait_for(state="visible", timeout=UI_STEP_TIMEOUT)
        got = ""
        for _ in range(2):
            ed.click(timeout=UI_CLICK_TIMEOUT)
            self._ui_pace(0.15, 0.4)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            self._ui_pace(0.1, 0.3)
            page.keyboard.insert_text(text)
            self._ui_pace(0.3, 0.7)
            got = _norm_prompt(ed.inner_text(timeout=5000))
            if got == want:
                return
        raise OranteError("提示词没粘进 Flow 的输入框",
                          f"框里读回来 {len(got)} 字，原文 {len(want)} 字")

    def _ui_click_generate(self, page, rpc: str = "eb1hJf", timeout: int = 45_000,
                           prompt: str | None = None) -> dict:
        btn = page.get_by_role("button", name="Start generation", exact=True)
        btn.wait_for(state="visible", timeout=UI_STEP_TIMEOUT)
        t0 = time.time()
        while not btn.is_enabled():
            if time.time() - t0 > GEN_BTN_ENABLE_WAIT:
                raise OranteError("「Start generation」按钮一直是灰的",
                                  "图或提示词可能没真的放进作曲区。")
            time.sleep(0.3)
        self._ui_step("点生成")
        with page.expect_response(lambda r: f"rpcids={rpc}" in r.url, timeout=timeout) as resp_info:
            btn.click(timeout=UI_CLICK_TIMEOUT)
        self._ui_step("等回包")
        resp = resp_info.value
        out = _parse_batchexecute(resp.text(), rpc)
        out["status"] = resp.status
        out["prompt_ok"] = None
        out["req_strings"] = None
        out["audio"] = None
        if prompt is not None:
            try:
                post = resp.request.post_data or ""
                out["prompt_ok"] = request_carries_prompt(post, prompt)
                out["req_strings"] = _freq_strings(post)
                out["audio"] = request_audio_flag(post)
            except Exception:
                out["prompt_ok"] = None
        return out

    def _ui_dump_scene(self, page, why: str) -> str:
        """对不上的时候把现场存下来：一张截图 + 页面上所有按钮/单选/输入框。
        下次卡在哪一步、页面长什么样一眼就知道，不用再猜。返回截图路径。"""
        stamp = datetime.now().strftime("%m%d-%H%M%S")
        d = paths.DATA / "logs" / "flow_ui"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            return ""
        png = d / f"{stamp}.png"
        try:
            page.screenshot(path=str(png), full_page=False)
        except Exception:
            png = None
        try:
            scene = page.evaluate(r"""() => {
              const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
              const t = e => (e.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60);
              return {
                url: location.href, lang: document.documentElement.lang || '',
                editors: [...document.querySelectorAll('[contenteditable="true"]')].map(t),
                radios: [...document.querySelectorAll('[role="radio"]')].filter(vis)
                          .map(x => t(x) + '|' + x.getAttribute('aria-checked')),
                buttons: [...document.querySelectorAll('button,[role="button"]')].filter(vis)
                          .map(b => t(b) + ' @@ ' + (b.getAttribute('aria-label') || '')),
                dialogs: [...document.querySelectorAll('.cdk-overlay-pane,[role="dialog"]')]
                          .filter(vis).map(t)
              };
            }""")
        except Exception as e:  # noqa: BLE001
            scene = {"error": str(e)[:200]}
        try:
            (d / f"{stamp}.json").write_text(
                json.dumps({"why": why, "stage": getattr(self, "_ui_stage", ""), "scene": scene},
                           ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
        return str(png) if png else str(d / f"{stamp}.json")

    def _ui_attempt(self, page, fill_and_click):
        """点「生成」之前的任何一步出岔子（控件没出来、弹窗没关等），重新打开一次
        项目页、从头再走一遍；两次都不行才往外抛。点了「生成」之后就绝不重来——
        那一下已经可能交出去了，再点一次就是重复提交、白烧一次额度。"""
        for attempt in (1, 2):
            self._ui_stage = ""
            try:
                return fill_and_click()
            except (FlowThrottled, BlockedImage, CreditHalted):
                raise
            except Exception as e:  # noqa: BLE001
                if attempt == 2 or self._ui_stage in ("点生成", "等回包"):
                    raise
                self.log(f"  · 真人操作卡在「{self._ui_stage}」（{_short_err(e)}），"
                         "重新打开项目页再走一遍")
                try:
                    shot = self._ui_dump_scene(page, f"第 1 次 {self._ui_stage}: {_short_err(e, 300)}")
                    if shot:
                        self.log(f"    现场：{shot}")
                except Exception:
                    pass
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                self._ui_reload(page)

    # ---------------- 视频 ----------------
    def _ui_fill_and_generate(self, page, image: Path, prompt: str, duration: int, *,
                              last_image: Path | None, aspect: str, silent: bool = False) -> dict:
        self._ui_step("清场")
        self._ui_clear_composer(page)
        self._ui_step("设无声开关")
        self._ui_set_silent(page, silent)
        self._ui_wander_mouse(page)
        if self._ui_settings_already_ok(page, aspect, duration):
            self._ui_close_settings(page)
        else:
            self._ui_step("打开设置")
            self._ui_open_settings(page)
            self._ui_pace(0.3, 0.7)
            self._ui_step("选 Video/Frames/画幅")
            self._ui_set_mode(page, aspect)
            self._ui_step("选模型")
            self._ui_pick_model(page, self.model_name)
            self._ui_step(f"选时长 {int(duration)}s")
            self._ui_radio(page, f"{int(duration)}s")
            # 每次只要 1 条。Flow 账号上次手动用过 x2 的话会一直记着，一次出两条，
            # Orante 只认第一条，第二条白白烧额度（Flow2 截图里就是 x2）。
            self._ui_step("选 x1")
            self._ui_radio(page, "x1")
            self._ui_step("核对模型")
            self._ui_check_model(page, self.model_name)
            self._ui_model_ok = True
            self._ui_step("关设置")
            self._ui_close_settings(page)
        self._ui_pace(0.2, 0.5)
        self._ui_step("找空的首帧格子")
        if not self._ui_slot_empty(page, "Start") or (last_image and not self._ui_slot_empty(page, "End")):
            # × 没把上一段的图清掉（极少见）：重新打开一次项目页，设置 Flow 自己记着
            self.log("  · 作曲区里还挂着上一段的图，重新打开一次项目页")
            self._ui_reload(page)
            page.get_by_role("button", name="Start", exact=True).first.wait_for(
                state="visible", timeout=UI_STEP_TIMEOUT)
        self._ui_step("挂首帧")
        self._ui_attach_frame(page, image, "Start", aspect=aspect)
        if last_image:
            self._ui_step("挂尾帧")
            self._ui_attach_frame(page, last_image, "End", aspect=aspect)
        self._ui_step("粘贴提示词")
        self._ui_type_prompt(page, prompt)
        self._ui_pace(0.4, 1.3)
        self._ui_step("核对首帧还在")
        need = 2 if last_image else 1
        if (self._ui_composer_images(page) < need or self._ui_empty_chip(page, "Start")
                or (last_image and self._ui_empty_chip(page, "End"))):
            raise OranteError("填完提示词再看，首帧/尾帧格子又空了", "这一次没交。")
        self._ui_step("等生成按钮变亮")
        return self._ui_click_generate(page, prompt=prompt)

    def _submit_via_ui(self, seg: dict, image: Path, prompt: str, duration: int, *,
                       last_image: Path | None, aspect: str, silent: bool = False) -> str:
        """真点上传框、真粘贴、真点「开始生成」——不走 page.evaluate 拼接口，
        让 Flow 自己的前端代码去发那个请求，reCAPTCHA 看到的是一次真操作。"""
        page = self._ensure_page()
        res = self._ui_attempt(page, lambda: self._ui_fill_and_generate(
            page, image, prompt, duration, last_image=last_image, aspect=aspect, silent=silent))
        self._ui_step("看回包")
        if res.get("status") == 200 and not res.get("err"):
            payload = res.get("payload")
            recs = _g(payload, 3, default=[]) or []
            gen_id = _g(recs, 0, 0)
            if not gen_id:
                raise OranteError("真人操作提交没拿到 genId",
                                  json.dumps(payload, ensure_ascii=False)[:300])
            if res.get("prompt_ok") is False:
                # 收到的这条生成回包，请求里带的不是这一段的提示词——对号绝不能乱认，
                # 宁可这一段报错（不记 genId、不下载、不改名），也不能把别的片子记到这段头上。
                raise OranteError(
                    "生成回包和这一段对不上（请求里的提示词不是这一段的），这个 genId 不认",
                    f"genId {gen_id[:8]}… 没记到 s{seg.get('s', 0):03d} 头上，也不会下载改名。"
                    "已经点了「生成」，这一段可能已经在 Flow 里生成了——先去 Flow 页面看一眼，别急着重交。")
            mid = model_id_for(self.model_name, duration)
            leaves = res.get("req_strings") or []
            if leaves and mid not in leaves:
                got = next((x for x in leaves if x.startswith("veo_")), "?")
                self.log(f"  ⚠ s{seg.get('s', 0):03d} 请求里的模型是 {got}，不是预期的 {mid}")
            want_audio = AUDIO_SILENT if silent else AUDIO_WITH_SOUND
            got_audio = res.get("audio")
            if got_audio is not None and got_audio != want_audio:
                self.log(f"  ⚠ s{seg.get('s', 0):03d} 请求里的声音设置是"
                         f"{'无声' if got_audio == AUDIO_SILENT else '有声'}，跟这段要的"
                         f"{'无声' if silent else '有声'}对不上（网页「Return silent videos」开关）")
            slot1 = _g(payload, 1)
            self._note_rpc("eb1hJf", mid, slot1)
            self._active.add(gen_id)
            self.log(f"  ✓ 已提交 s{seg.get('s', 0):03d}（真人操作）→ {gen_id[:8]}…  "
                     f"{duration}s  剩余额度 {slot1}")
            return gen_id
        info = classify_flow_error("eb1hJf", res)
        if info.get("action") == "throttle":
            raise FlowThrottled(
                self.account.get("name") or "",
                code=info.get("code") or "PUBLIC_ERROR_UNUSUAL_ACTIVITY",
                err_n=info.get("err_n"), raw=str(res.get("err"))[:300])
        if info.get("action") == "reject":
            raise BlockedImage(info.get("plain") or "画面被 Flow 审核拦下了")
        raise OranteError(info.get("plain") or "真人操作提交没成功", format_flow_raw(info).strip())

    # ---------------- 图片（扩图 / 高清修复原图 / 尾帧提清 / 描述卡） ----------------
    def _ui_image_fill_and_generate(self, page, ref: Path | None, prompt: str,
                                    aspect: str, label: str) -> dict:
        self._ui_step("清场")
        self._ui_clear_composer(page)
        self._ui_wander_mouse(page)
        self._ui_step("打开设置")
        self._ui_open_settings(page)
        self._ui_pace(0.3, 0.7)
        self._ui_step("选 Image/画幅")
        self._ui_radio(page, "Image")
        self._ui_radio(page, aspect)
        self._ui_step("选生图模型")
        self._ui_pick_model(page, label)
        self._ui_step("选 x1")
        self._ui_radio(page, "x1")
        self._ui_step("核对生图模型")
        self._ui_check_model(page, label, exact=True)
        self._ui_step("关设置")
        self._ui_close_settings(page)
        self._ui_pace(0.2, 0.5)
        if ref is not None:
            self._ui_step("挂参考图")
            self._ui_attach_ingredient(page, ref)
        self._ui_step("粘贴提示词")
        self._ui_type_prompt(page, prompt)
        self._ui_pace(0.4, 1.0)
        if ref is not None and self._ui_composer_images(page) < 1:
            raise OranteError("填完提示词再看，参考图不在作曲区里了", "这一次没交。")
        self._ui_step("等生成按钮变亮")
        return self._ui_click_generate(page, rpc="ogiZ0b", timeout=180_000, prompt=prompt)

    def _gen_image_via_ui(self, ref: Path | None, prompt: str, aspect: str, model: str) -> dict:
        key = self._image_model_key(model)
        reject_paid_model(key)
        label = IMAGE_MODEL_LABELS.get(key)
        if not label:
            raise OranteError(f"生图模型 {key} 在 Flow 网页上对应哪个选项还没核对过",
                              "设置里「Flow 生图模型」换回默认（Nano Banana Pro）或 NARWHAL（Nano Banana 2）。")
        if aspect not in UI_IMAGE_ASPECTS:
            aspect = "9:16"
        self.tick_credits()
        page = self._ensure_page()
        res = self._ui_attempt(page, lambda: self._ui_image_fill_and_generate(
            page, ref, prompt, aspect, label))
        self._ui_step("看回包")
        if res.get("status") == 200 and not res.get("err"):
            payload = res.get("payload")
            media_id = _g(payload, 0, 0, 0)
            url = _g(payload, 0, 0, 6, 0, 13)
            if not media_id or not url:
                txt = json.dumps(payload, ensure_ascii=False)[:400]
                if re.search(r"prominent|policy|违反|unsafe|minor|celebrit|blocked", txt, re.I):
                    raise BlockedImage(txt)
                raise OranteError("生图没有返回图片", txt)
            if res.get("prompt_ok") is False:
                raise OranteError("生图回包和这一次对不上（请求里的提示词不是这次的），这张图不认",
                                  "不会存成这一次的结果。")
            leaves = res.get("req_strings") or []
            if leaves and key not in leaves:
                got = next((x for x in leaves if x in IMAGE_MODEL_LABELS), "?")
                self.log(f"  ⚠ 生图请求里的模型是 {got}，不是预期的 {key}")
            self._note_rpc("ogiZ0b", key, _g(payload, 1))
            return {"media_id": media_id, "url": url, "seed": None}
        info = classify_flow_error("ogiZ0b", res)
        if info.get("action") == "throttle":
            raise FlowThrottled(
                self.account.get("name") or "",
                code=info.get("code") or "PUBLIC_ERROR_UNUSUAL_ACTIVITY",
                err_n=info.get("err_n"), raw=str(res.get("err"))[:300])
        if info.get("action") == "reject":
            raise BlockedImage(info.get("plain") or "这张图被 Flow 审核拦下了")
        raise OranteError(info.get("plain") or "真人操作生图没成功", format_flow_raw(info).strip())

    def _ui_wrap_error(self, e: BaseException, what: str) -> OranteError:
        """界面上某一步对不上 → 统一成给人看的 OranteError（带卡在哪一步、现场截图）。"""
        self._ui_warmup_failed = True
        stage = getattr(self, "_ui_stage", "") or "开始"
        shot = ""
        try:
            shot = self._ui_dump_scene(self.page, f"{stage}: {_short_err(e, 300)}")
        except Exception:
            pass
        clicked = stage in ("点生成", "等回包", "看回包")
        detail = e.message if isinstance(e, OranteError) else _short_err(e)
        hint = ((f"已经点了「生成」，这一{what}可能已经在 Flow 里生成了——先去 Flow 页面看一眼，"
                 "别急着重交。" if clicked else
                 f"这一{what}没有交给 Google（不耗额度、不会触发限流），也没有改走接口直连。")
                + (f" 现场截图：{shot}" if shot else ""))
        return OranteError(f"真人操作卡在「{stage}」：{detail}", hint)

    def poll(self, gen_ids: list[str]) -> dict[str, dict]:
        if not gen_ids:
            return {}
        payload = self._rpc("jwpduf", [None, None, [[g] for g in gen_ids]])
        out = {}
        for rec in _g(payload, 2, default=[]) or []:
            gid = _g(rec, 0)
            st = _status_of(rec)
            all_vids = _urls(rec)["video"] or []
            rejected = self._rejected.get(gid) or set()
            vids = [u for u in all_vids if u and u not in rejected]
            if vids:
                out[gid] = {"state": "done", "url": vids[0], "raw": st}
                self._active.discard(gid)
            elif all_vids and not vids:
                # 有地址但全作废过：当没出片，继续等（可能换地址）
                out[gid] = {"state": "pending", "raw": "rejected-url"}
                self._active.add(gid)
            elif st in STATUS_DONE:
                # 状态完成、记录里还没地址：照旧让 download 走 as29s
                out[gid] = {"state": "done", "url": None, "raw": st}
                self._active.discard(gid)
            elif st in STATUS_FAILED:
                try:
                    blob = json.dumps(rec, ensure_ascii=False)
                except Exception:
                    blob = str(rec)
                dump = paths.LOGS / f"flow-failed-{str(gid)[:8]}.json"
                try:
                    dump.parent.mkdir(parents=True, exist_ok=True)
                    dump.write_text(blob, encoding="utf-8")
                    self.log(f"  · {str(gid)[:8]} 失败已落盘 {dump.name}（{len(blob)} 字）")
                except Exception as e:  # noqa: BLE001
                    self.log(f"  · 失败记录落盘失败：{str(e)[:80]}")
                snippet = _reject_snippet(rec)
                if snippet:
                    error = "被拒：" + snippet
                else:
                    # 0922：有错误码就说人话（别写"被拒"，那会把图标成 blocked）
                    mcode = _PUBLIC_ERR_RE.search(blob)
                    if mcode:
                        code = mcode.group(0)
                        info = FLOW_ERRORS.get(code) or {}
                        error = f"{info.get('plain') or '生成失败'}（{code}）"
                    else:
                        error = f"status={st}"
                out[gid] = {"state": "failed", "error": error, "raw": blob[:300]}
                self._active.discard(gid)
            else:
                if st not in STATUS_PENDING and st is not None:
                    self.log(f"  · {gid[:8]} 未知状态码 {st}，先按生成中处理")
                out[gid] = {"state": "pending", "raw": st}
        for g in gen_ids:
            out.setdefault(g, {"state": "pending", "raw": "not-in-response"})
        return out

    def media_urls(self, media_id: str) -> dict:
        rec = self._rpc("as29s", [media_id])
        u = _urls(rec)
        return {"image": u["image"][0] if u["image"] else None, "video": u["video"][0] if u["video"] else None,
                "status": _status_of(rec)}

    def download(self, gen_id: str, dest: Path) -> Path:
        urls = self.media_urls(gen_id)
        url = urls.get("video")
        if not url:
            raise OranteError("成片还没有下载地址", gen_id)
        if url in (self._rejected.get(gen_id) or ()):
            raise NeedRedownload("地址已作废", "落盘自证作废过，会重新取。")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            import requests
            r = requests.get(url, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            data = r.content
        except Exception as e:  # noqa: BLE001 —— 直连失败就借页面下
            self.log(f"  · 直连下载失败（{e}），改由页面下载")
            self._ensure_page()
            res = self.page.evaluate(FETCH_BLOB_JS, url)
            if res.get("error"):
                raise OranteError("下载失败", res["error"])
            data = base64.b64decode(res["b64"])
        if len(data) < 50_000:
            raise OranteError("下载的文件太小，不像视频", f"{len(data)} bytes")
        dest.write_bytes(data)
        self.log(f"  ⬇ {dest.name}  {len(data) // 1024} KB")
        return dest

    def forget_url(self, gen_id: str, url: str = "") -> None:
        """落盘自证作废后：丢掉这个地址，把段重新算进在飞，下次 poll 再取。"""
        rej = self._rejected.setdefault(gen_id, set())
        if url:
            rej.add(url)
        self._active.add(gen_id)

    # ---------------- 生图（ogiZ0b，同步返回；前端一次点 4 张就是并发 4 个不同种子） ----------------
    def _image_model_key(self, model: str = "nano") -> str:
        """生图用的 Flow 模型 key。默认 GEM_PIX_2（Nano Banana Pro）；
        设置 flow.image_model_key 可整体覆盖（NARWHAL = Nano Banana 2）。"""
        try:
            k = str(config.get("flow.image_model_key", "") or "").strip()
        except Exception:
            k = ""
        return k or IMAGE_MODELS.get(model, "GEM_PIX_2")

    def gen_image_once(self, prompt: str, ref_media_id: str | None, seed: int | None = None,
                       model: str = "nano") -> dict:
        key = self._image_model_key(model)
        reject_paid_model(key)
        self.tick_credits()
        seed = seed if seed is not None else random.randint(1, 2**31 - 1)

        def make_inner():
            token = self._recaptcha(RC_ACTION_IMAGE)
            ctx = [None, 22, None, None, None, self.project_id, None, self.collection_id,
                   None, None, [token, 1]]
            refs = [[ref_media_id, None, None, None, 1]] if ref_media_id else None
            inner = [None, [[None, None, refs, seed, 2, key,
                            None, ctx, [[[prompt]]], None, None, None,
                            str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()]],
                     IMAGE_ONCE_COUNT, ctx, [str(uuid.uuid4()).upper()]]
            assert_image_once_count(inner)
            return inner

        payload = self._rpc("ogiZ0b", make_inner)
        media_id = _g(payload, 0, 0, 0)
        url = _g(payload, 0, 0, 6, 0, 13)
        if not media_id or not url:
            txt = json.dumps(payload, ensure_ascii=False)[:400]
            if re.search(r"prominent|policy|违反|unsafe|minor|celebrit|blocked", txt, re.I):
                raise BlockedImage(txt)
            raise OranteError("生图没有返回图片", txt)
        self._note_rpc("ogiZ0b", key, _g(payload, 1))
        return {"media_id": media_id, "url": url, "seed": seed}

    def gen_images(self, prompt: str, ref: Path | None, n: int = 4, aspect: str | None = None,
                   dest_dir: Path | None = None, model: str = "nano") -> list[Path]:
        # 规格 84：有参考图时画幅跟参考图走，不再默认账号/写死竖屏
        from . import caps
        if aspect is None:
            aspect = caps.aspect_for_image(ref, "flow") if ref else "9:16"
        dest_dir = Path(dest_dir or (paths.DATA / "tmp"))
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 规格 96：扩图 / 高清修复原图 / 尾帧提清 / 描述卡生图都走到这里。开着「每次都走
        # 真人操作」就在网页上切到 Image、挂参考图、粘贴提示词、点生成，不再走接口直连
        # （参考图也是网页上传，不再用 maseQ 直传）。每张图单独点一次生成（x1），跟原来
        # 一次请求一张一致；拿到图以后下载、命名、返回，后面的流程一个字没改。
        human = bool(config.get("flow.human_warmup", True))
        ref_id = None if (human or not ref) else self.upload_image(ref)
        out = []
        for i in range(n):
            try:
                if human:
                    try:
                        r = self._gen_image_via_ui(Path(ref) if ref else None, prompt, aspect, model)
                    except (FlowThrottled, BlockedImage, CreditHalted):
                        raise
                    except Exception as e:  # noqa: BLE001
                        raise self._ui_wrap_error(e, "张图") from e
                else:
                    r = self.gen_image_once(prompt, ref_id, model=model)
            except BlockedImage as e:
                self.log(f"  · 第 {i + 1} 张被拒：{str(e)[:120]}")
                if human and "拒收了" in str(e):
                    raise   # 参考图本身传不上去——再生几张也一样，交给上层按"图被拒"处理
                continue
            import requests
            resp = requests.get(r["url"], timeout=120, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            p = dest_dir / f"gen-{r['media_id'][:8]}.jpg"
            p.write_bytes(resp.content)
            out.append(p)
            self.log(f"  ✎ 生图 {i + 1}/{n} → {p.name}（模型 {self._image_model_key(model)}）")
        return out
