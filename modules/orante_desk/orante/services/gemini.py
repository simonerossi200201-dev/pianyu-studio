# -*- coding: utf-8 -*-
"""Gemini REST 客户端。普通 Key 直连 generateContent；page 模式这一版不实现。

并发由调用方管。429 / 503 指数退避重试 5 次（1,2,4,8,16 秒）。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from pathlib import Path

import requests

from ..errors import OranteError

log = logging.getLogger("orante.gemini")


def _redact_secret(text: str, secret: str) -> str:
    out = text or ""
    if secret:
        out = out.replace(secret, "***")
    return re.sub(r"([?&]key=)[^&\s\"']+", r"\1***", out)

API = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_INLINE = 4 * 1024 * 1024
LONG_SIDE = 1280
RETRY_DELAYS = (1, 2, 4, 8, 16)
_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def _cfg(key: str, default=None):
    try:
        from .. import config
        return config.get(key, default)
    except Exception:
        from ..config import DEFAULTS
        return DEFAULTS.get(key, default)


class Gemini:
    def __init__(self, api_key: str | None = None, model: str | None = None,
                 retries: int | None = None):
        # retries=0：不在内部退避重试，429 直接抛出去——接口池要靠这个立刻换下一条 key。
        # 不传就是老行为（1,2,4,8,16 秒退避五次）。
        self.retries = len(RETRY_DELAYS) if retries is None else max(0, int(retries))
        mode = _cfg("gemini.mode") or "direct"
        if mode == "page":
            raise OranteError(
                "page 模式尚未实现",
                "把 Key 换成不限制 referrer 的普通 Key")
        if api_key is None:
            api_key = _cfg("gemini.api_key") or ""
        self.api_key = str(api_key or "").strip()
        if not self.api_key:
            raise OranteError(
                "还没填 Gemini API Key",
                "设置 → Gemini → api_key。到 aistudio.google.com 免费申请。")
        if model is None:
            model = _cfg("gemini.model") or "gemini-3.5-flash-lite"
        self.model = str(model or "gemini-3.5-flash-lite").strip().removeprefix("models/")

    def ask_text(self, prompt: str, system: str = "", temperature: float = 0.4,
                 json_mode: bool = False) -> str:
        parts = [{"text": prompt or ""}]
        return self._generate(parts, system=system, temperature=temperature, json_mode=json_mode)

    def ask_image(self, prompt: str, image_path: Path, system: str = "",
                  json_mode: bool = False, temperature: float = 0.4) -> str:
        return self.ask_images(prompt, [image_path], system=system,
                               json_mode=json_mode, temperature=temperature)

    def ask_images(self, prompt: str, image_paths: list[Path], system: str = "",
                   json_mode: bool = False, temperature: float = 0.4) -> str:
        parts = []
        for p in image_paths or []:
            parts.append(self._image_part(Path(p)))
        parts.append({"text": prompt or ""})
        return self._generate(parts, system=system, temperature=temperature, json_mode=json_mode)

    def list_models(self) -> list[str]:
        data = self._request("GET", API)
        names = []
        for m in data.get("models") or []:
            n = (m.get("name") or "").removeprefix("models/")
            if n:
                names.append(n)
        return names

    # ---- 内部 ----------------------------------------------------------
    def _generate(self, parts: list, system: str, temperature: float, json_mode: bool) -> str:
        body: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        url = f"{API}/{self.model}:generateContent"
        data = self._request("POST", url, body)
        text = _text_from_response(data)
        if json_mode:
            try:
                json.loads(text)
            except Exception:
                raise OranteError("Gemini 没有返回合法 JSON", (text or "")[:200])
        return text

    def _request(self, method: str, url: str, body: dict | None = None) -> dict:
        last = ""
        for i in range(self.retries + 1):
            try:
                r = requests.request(
                    method, url,
                    headers={"x-goog-api-key": self.api_key},
                    json=body, timeout=120)
            except requests.RequestException as e:
                last = _redact_secret(str(e), self.api_key)
                if i < self.retries:
                    d = RETRY_DELAYS[min(i, len(RETRY_DELAYS) - 1)]
                    log.warning("Gemini 网络错误，%s 秒后重试：%s", d, last)
                    time.sleep(d)
                    continue
                raise OranteError("Gemini 请求失败", last)
            if r.status_code in (429, 503) and i < self.retries:
                d = RETRY_DELAYS[min(i, len(RETRY_DELAYS) - 1)]
                if r.status_code == 429:
                    q = _quota_info(r)
                    if q["scope"] == "每天":
                        break            # 今天的额度没了，退避多少次都没用
                    if q["retry"]:       # Google 自己说了等多久，就听它的
                        d = min(60.0, max(d, q["retry"] + 0.5))
                log.warning("Gemini HTTP %s，%s 秒后重试（第 %s 次）", r.status_code, d, i + 1)
                time.sleep(d)
                last = f"HTTP {r.status_code}"
                continue
            if r.status_code >= 400:
                err = _http_error(r)
                err.message = _redact_secret(err.message, self.api_key)
                err.hint = _redact_secret(err.hint, self.api_key)
                raise err
            try:
                return r.json()
            except Exception:
                raise OranteError(
                    "Gemini 响应不是 JSON",
                    _redact_secret((r.text or "")[:200], self.api_key),
                )
        raise OranteError("Gemini 多次重试仍失败", last)

    def _image_part(self, path: Path) -> dict:
        p = Path(path)
        if not p.is_file():
            raise OranteError(f"找不到图片 {p.name}", str(p))
        data = p.read_bytes()
        mime = _MIME.get(p.suffix.lower(), "image/jpeg")
        if len(data) > MAX_INLINE:
            data, mime = _shrink(data, mime)
        b64 = base64.b64encode(data).decode("ascii")
        return {"inlineData": {"mimeType": mime, "data": b64}}


def _shrink(data: bytes, mime: str) -> tuple[bytes, str]:
    try:
        from PIL import Image
    except ImportError:
        raise OranteError(
            "图片超过 4MB，需要 Pillow 才能缩小",
            "pip install pillow")
    im = Image.open(io.BytesIO(data))
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    w, h = im.size
    long_side = max(w, h)
    if long_side > LONG_SIDE:
        scale = LONG_SIDE / float(long_side)
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85, optimize=True)
    out = buf.getvalue()
    if len(out) > MAX_INLINE:
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=60, optimize=True)
        out = buf.getvalue()
    return out, "image/jpeg"


def _text_from_response(data: dict) -> str:
    err = (data.get("error") or {}).get("message")
    if err:
        raise OranteError("Gemini 报错", str(err)[:200])
    cands = data.get("candidates") or []
    if not cands:
        fb = data.get("promptFeedback") or {}
        reason = fb.get("blockReason") or "空响应"
        raise OranteError("Gemini 没有返回内容", str(reason)[:200])
    parts = ((cands[0].get("content") or {}).get("parts") or [])
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
    out = "".join(texts).strip()
    if not out:
        fr = cands[0].get("finishReason") or ""
        raise OranteError(
            "Gemini 返回空文字",
            f"finishReason={fr}" if fr else "candidates 里没有 text")
    return out


def _quota_info(r: requests.Response) -> dict:
    """从 429 的响应体里挖出「到底是哪条限额」和「Google 说等多久」。

    Google 把这些放在 error.details 里：
      QuotaFailure.violations[].quotaMetric / quotaId / quotaValue
      RetryInfo.retryDelay  例如 "27s"
    返回 {"scope": "每分钟"|"每天"|"", "value": "1000", "id": "...", "retry": 27.0}
    """
    out = {"scope": "", "value": "", "id": "", "retry": 0.0}
    try:
        details = ((r.json().get("error") or {}).get("details") or [])
    except Exception:
        return out
    for d in details:
        typ = str(d.get("@type") or "")
        if typ.endswith("QuotaFailure"):
            v = (d.get("violations") or [{}])[0]
            out["id"] = str(v.get("quotaId") or v.get("quotaMetric") or "")
            out["value"] = str(v.get("quotaValue") or "")
            low = out["id"].lower()
            if "perday" in low or "per_day" in low:
                out["scope"] = "每天"
            elif "perminute" in low or "per_minute" in low:
                out["scope"] = "每分钟"
        elif typ.endswith("RetryInfo"):
            m = re.match(r"([\d.]+)s", str(d.get("retryDelay") or ""))
            if m:
                try:
                    out["retry"] = float(m.group(1))
                except Exception:
                    pass
    return out


def _http_error(r: requests.Response) -> OranteError:
    try:
        msg = (r.json().get("error") or {}).get("message") or r.text
    except Exception:
        msg = r.text
    msg = (msg or "")[:200]
    if r.status_code in (401, 403):
        return OranteError("Gemini API Key 无效或没权限", msg)
    if r.status_code == 429:
        # 光说「配额用完了」没用——要说清是每分钟还是每天，不然不知道该等
        # 一分钟还是等到明天。这些信息 Google 放在 error.details 里。
        q = _quota_info(r)
        head = "Gemini 配额用完"
        if q["scope"]:
            head += f"（{q['scope']}上限"
            if q["value"]:
                head += f" {q['value']} 次"
            head += "）"
        hint = []
        if q["id"]:
            hint.append(q["id"])
        if q["retry"]:
            hint.append(f"Google 说 {int(q['retry'])} 秒后可以再试")
        elif q["scope"] == "每天":
            hint.append("每天的额度要等太平洋时间零点才重置")
        hint.append(msg)
        e = OranteError(head, "；".join(x for x in hint if x)[:300])
        e.quota = q          # 接口池按这个决定：冷却几秒，还是歇到明天
        e.http_status = 429
        return e
    return OranteError(f"Gemini 返回 HTTP {r.status_code}", msg)
