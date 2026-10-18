# -*- coding: utf-8 -*-
"""统一的多模态入口。调用方不再自己 new Gemini()，而是 ai.client()。

对外的方法名和参数跟老的 Gemini 完全一样（ask_text / ask_image / ask_images），
所以各处的调用点只要把 `Gemini()` 换成 `ai.client()` 就行，别的一个字不用改。

内部干的事：
  ① 向 aipool 借一条当前最闲的 key（按优先级分档，同档均衡）
  ② 用这条 key 对应的协议发请求（gemini / openai 兼容 / ollama）
  ③ 成功就还回去记一次用量；被 429 就把这条冷却起来，**换下一条重发**，
     而不是像以前那样死等退避、最后整个任务失败
  ④ 一轮把所有 key 都试过还不成，才真的报错，并把每条为什么不行说清楚

加 key 就等于同时加了吞吐和额度——这是 0908 定的设计。
"""
from __future__ import annotations

import base64
import json
import re
import threading
from pathlib import Path

import requests

from ..errors import OranteError
from . import aipool
from .gemini import Gemini, _shrink, _MIME

TIMEOUT = 180

# 本进程多模态成功次数。描述语顺带写 pose 的验收靠这个看「次数没有增加」。
_CALLS = 0
_CALLS_LOCK = threading.Lock()


def calls() -> int:
    return _CALLS


def reset_calls() -> None:
    global _CALLS
    with _CALLS_LOCK:
        _CALLS = 0


# ---------------------------------------------------------------- 工具
def _img_b64(path: Path) -> tuple[str, str]:
    """读图 + 压到长边 1280（复用 gemini 那套，省得两份逻辑跑偏）。"""
    path = Path(path)
    mime = _MIME.get(path.suffix.lower(), "image/jpeg")
    data, mime = _shrink(path.read_bytes(), mime)
    return base64.b64encode(data).decode("ascii"), mime


def _retry_after(r: requests.Response) -> float:
    v = r.headers.get("Retry-After") or ""
    try:
        return float(v)
    except Exception:
        pass
    m = re.search(r'"?retry[_-]?(?:after|delay)"?\s*[:=]\s*"?([\d.]+)', r.text or "", re.I)
    return float(m.group(1)) if m else 0.0


def _http_err(name: str, r: requests.Response) -> OranteError:
    body = (r.text or "")[:300]
    try:
        j = r.json()
        body = str((j.get("error") or {}).get("message") or body)[:300]
    except Exception:
        pass
    e = OranteError(f"{name} 返回 HTTP {r.status_code}", body)
    e.http_status = r.status_code
    if r.status_code == 429:
        e.quota = {"scope": "", "value": "", "id": "", "retry": _retry_after(r)}
    return e


# ---------------------------------------------------------------- 三种协议
def _ask_gemini(p: dict, prompt, images, system, temperature, json_mode) -> str:
    g = Gemini(api_key=p["api_key"], model=p["model"], retries=0)   # 不内部重试，交给池子换 key
    if not images:
        return g.ask_text(prompt, system=system, temperature=temperature, json_mode=json_mode)
    if len(images) == 1:
        return g.ask_image(prompt, images[0], system=system, temperature=temperature,
                           json_mode=json_mode)
    return g.ask_images(prompt, images, system=system, temperature=temperature,
                        json_mode=json_mode)


def _ask_openai(p: dict, prompt, images, system, temperature, json_mode) -> str:
    """任何 OpenAI 兼容的 /chat/completions（官方、中转、vLLM、LM Studio…）。"""
    content = [{"type": "text", "text": prompt}]
    for im in images:
        b64, mime = _img_b64(im)
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"}})
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": content}]
    body = {"model": p["model"], "messages": msgs, "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    url = (p.get("base_url") or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if p.get("api_key"):
        headers["Authorization"] = "Bearer " + p["api_key"]
    r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise _http_err(p["name"], r)
    try:
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        raise OranteError(f"{p['name']} 的响应看不懂", (r.text or "")[:200])


def _ask_ollama(p: dict, prompt, images, system, temperature, json_mode) -> str:
    """本地 Ollama 的原生接口 /api/chat（图片走 images 数组，不是 data URL）。"""
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = [_img_b64(im)[0] for im in images]
    msgs = ([{"role": "system", "content": system}] if system else []) + [msg]
    body = {"model": p["model"], "messages": msgs, "stream": False,
            "options": {"temperature": temperature}}
    if json_mode:
        body["format"] = "json"
    url = (p.get("base_url") or "http://127.0.0.1:11434").rstrip("/") + "/api/chat"
    r = requests.post(url, json=body, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise _http_err(p["name"], r)
    try:
        return ((r.json().get("message") or {}).get("content") or "").strip()
    except Exception:
        raise OranteError(f"{p['name']} 的响应看不懂", (r.text or "")[:200])


ADAPTERS = {"gemini": _ask_gemini, "openai": _ask_openai, "ollama": _ask_ollama}


# ---------------------------------------------------------------- 池化的客户端
class PooledClient:
    """接口和老的 Gemini 一样，但每次调用都从池子里挑一条 key。

    job 传进来（可选）只为两件事：把换 key 的过程写进任务日志，
    以及排队等 key 时还能响应「停止」。
    """

    def __init__(self, job=None, tag: str = "", cheap: bool = False):
        self.job = job
        self.tag = tag or getattr(job, "id", "") or ""
        self.cheap = bool(cheap)

    # 老接口，原样保留 -------------------------------------------------
    def ask_text(self, prompt: str, system: str = "", temperature: float = 0.4,
                 json_mode: bool = False) -> str:
        return self._call([], prompt, system, temperature, json_mode, vision=False)

    def ask_image(self, prompt: str, image_path, system: str = "",
                  temperature: float = 0.4, json_mode: bool = False) -> str:
        return self._call([Path(image_path)], prompt, system, temperature, json_mode)

    def ask_images(self, prompt: str, image_paths: list, system: str = "",
                   temperature: float = 0.4, json_mode: bool = False) -> str:
        return self._call([Path(x) for x in image_paths], prompt, system, temperature, json_mode)

    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        fn = getattr(self.job, "log", None)
        if callable(fn):
            try:
                fn(msg)
            except Exception:
                pass

    def _call(self, images, prompt, system, temperature, json_mode, vision=True) -> str:
        tried: set[str] = set()
        last: Exception | None = None
        check = getattr(self.job, "check", None)
        # 最多把每条 key 都试一遍；每失败一条就换下一条，不在同一条上死磕
        for _ in range(max(1, len(aipool.aikeys.list_all(enabled_only=True))) + 1):
            live = [x["id"] for x in aipool.aikeys.list_all(enabled_only=True)]
            if tried and live and all(i in tried for i in live):
                break            # 这一轮每条都试过了，别再去 lease 里干等
            lz = aipool.lease(vision=vision, skip=tried, tag=self.tag, check=check,
                              cheap=self.cheap)
            p = lz.provider
            tried.add(p["id"])
            fn = ADAPTERS.get(p["kind"])
            if fn is None:
                lz.failed(f"不认识的接口类型 {p['kind']}", cool=3600, why="类型不对")
                last = OranteError(f"不认识的接口类型 {p['kind']}")
                continue
            try:
                out = fn(p, prompt, images, system, temperature, json_mode)
            except OranteError as e:
                last = e
                q = getattr(e, "quota", None)
                status = getattr(e, "http_status", 0)
                if q is not None or status == 429:
                    if (q or {}).get("scope") == "每天":
                        lz.exhausted_today(e.message)
                        self._log(f"  · {p['name']} 今天的额度用完了，换下一条")
                    else:
                        cool = float((q or {}).get("retry") or 0) or 30.0
                        lz.failed(e.message, cool=cool, why="配额限流")
                        self._log(f"  · {p['name']} 被限流（歇 {int(cool)} 秒），换下一条")
                else:
                    lz.failed(e.message, cool=20.0, why="报错")
                    self._log(f"  · {p['name']} 出错：{e.message}，换下一条")
                continue
            except Exception as e:                       # 网络等意外
                last = e
                lz.failed(f"{type(e).__name__}: {e}", cool=20.0, why="网络异常")
                self._log(f"  · {p['name']} 连不上，换下一条")
                continue
            lz.done()
            global _CALLS
            with _CALLS_LOCK:
                _CALLS += 1
                n = _CALLS
            nimg = len(images or [])
            self._log(f"  · 多模态 #{n} {p['name']}（{'图×'+str(nimg) if nimg else '纯文本'}）")
            return out
        if isinstance(last, OranteError):
            raise OranteError("所有多模态接口都没能完成这次请求",
                              f"最后一条的错是：{last.message}｜{last.hint}"[:300])
        raise OranteError("所有多模态接口都没能完成这次请求", str(last or "")[:300])


def client(job=None, tag: str = "", cheap: bool = False) -> PooledClient:
    return PooledClient(job=job, tag=tag, cheap=cheap)


def workers(cap: int = 16) -> int:
    """建议开几个线程：跟着 key 数量走，卡一个总上限。"""
    return aipool.workers(cap)


def probe(p: dict) -> dict:
    """加 key 时的自检：先问一句纯文字，再拿一张 2×2 的红图问颜色，
    确认这条接口真的能看图（用户的要求是「只要支持多模态输入就行」）。"""
    fn = ADAPTERS.get(p.get("kind"))
    if fn is None:
        raise OranteError(f"不认识的接口类型 {p.get('kind')}")
    out = {"text": "", "vision": "", "ok_text": False, "ok_vision": False}
    out["text"] = (fn(p, "只回答两个字：可以", [], "", 0.0, False) or "").strip()[:40]
    out["ok_text"] = bool(out["text"])
    # 2×2 纯红 PNG（内嵌，不用联网也不用读盘）
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFUlEQVR4nGP8z8Dwn4"
        "GBgYmBgYGBAQAoAwMBz2K5xgAAAABJRU5ErkJggg==")
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png)
        tmp = Path(f.name)
    try:
        ans = fn(p, "这张图是什么颜色？只回答颜色名。", [tmp], "", 0.0, False)
        out["vision"] = (ans or "").strip()[:40]
        out["ok_vision"] = bool(out["vision"])
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    return out
