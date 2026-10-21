# -*- coding: utf-8 -*-
"""规格 98：本地模型 Studio Zero（MiniMax H3，本机 ComfyUI）当第三个出片引擎。

Orante 这边只跟 Studio Zero 的本机接口说话（studio_api.py，默认 127.0.0.1:7870），
不碰 ComfyUI 节点。它挂在虚拟账号 STUDIO 上（accounts.studio_account），不进数据库。

和 Flow / Grok 的分工：
  · 只用来重生（手动选「本地模型」，或 Flow 拒图后自动转交），第一版不能当任务账号；
  · 没有拒图：永远不抛 BlockedImage，失败了用原图再交一次（见 generate._keeps_image）；
  · 出好的片子统一成和 Flow 一样的规格：画幅跟着图走（竖 720×1280 / 横、正方形 1280×720）、
    24fps、时长正好等于交的秒数、AAC 48k 立体声。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import config
from ..errors import OranteError
from . import caps
from .base import Engine, EngineUnavailable

GID_PREFIX = "sz:"
HTTP_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 300
LAUNCH_WAIT = 90
_LTX_ROOT = Path(__file__).resolve().parents[4]
# 启动脚本没填时的默认位置（设置里填 off 就不自动启动）
DEFAULT_LAUNCHER = str(_LTX_ROOT / "启动本地算力网关.bat")

# 【镜头】行里出现这些词就是运镜：不锁尾帧（锁了最后一帧会跳回首帧），用运镜档
CAMERA_WORDS = ("平移", "推进", "推近", "拉远", "拉开", "环绕", "摇", "跟拍", "跟随", "升降", "运镜", "移动")
_CAMERA_RE = re.compile("|".join(CAMERA_WORDS))
_NEG_RE = re.compile(r"(不|别|禁止|避免|无)[^，,。；;！!？?\n]{0,6}?(" + "|".join(CAMERA_WORDS) + ")")
# Veo 专用、对 H3 没用而且可能被念出来的句子
_DROP_SENT = ("一个字都不要念出来", "嘴型对齐", "一字不差")


def base_url() -> str:
    return str(config.get("engine.studio.url", "http://127.0.0.1:7870") or "http://127.0.0.1:7870").rstrip("/")


def _opener():
    # 只连本机：不走系统代理
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(method: str, path: str, payload: dict | None = None, timeout: float = HTTP_TIMEOUT) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with _opener().open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8") or "{}").get("error") or ""
        except Exception:
            msg = ""
        err = StudioHTTPError(e.code, msg or f"HTTP {e.code}")
        raise err from e
    try:
        return json.loads(body)
    except ValueError:
        return {}


class StudioHTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def health(timeout: float = 2.0) -> dict:
    """{ok, comfy, busy, queued, ...}；连不上返回 {ok: False}。"""
    try:
        r = http_json("GET", "/v1/health", timeout=timeout)
        r["ok"] = bool(r.get("ok"))
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "comfy": False, "error": str(e)[:160]}


# ---------------------------------------------------------------- 提示词 / 运镜（规格 98 §5）

def camera_line(prompt: str) -> str:
    for ln in (prompt or "").splitlines():
        if ln.strip().startswith("【镜头】"):
            return ln.strip()
    return ""


def has_camera_move(prompt: str) -> bool:
    """只看【镜头】那一行（"人物保持原位不移动"里的"移动"说的是人，不是镜头）。
    前面 6 个字以内有"不 / 别 / 禁止 / 避免 / 无"的不算（"不要推进""禁止摇镜"）。"""
    line = camera_line(prompt)
    if not line:
        return False
    stripped = _NEG_RE.sub("", line)
    return bool(_CAMERA_RE.search(stripped))


# 本地模型的出片模式（账号抽屉 / 设置里选）。速度是 RTX 5060 Ti 16GB 实测（15 秒片），8 秒片大约一半多。
QUALITIES = ("fast", "balanced", "high", "best")
# 每出 1 秒片子大约要跑多少秒（超时按它的 2 倍再加 3 分钟算，最少 15 分钟）
WALL_PER_SEC = {"fast": 15, "balanced": 18, "camera": 20, "high": 28, "best": 120}


def current_quality() -> str:
    q = str(config.get("engine.studio.quality", "balanced") or "balanced").strip().lower()
    return q if q in QUALITIES else "balanced"


def pick_mode(prompt: str) -> tuple[str, bool]:
    """(quality, lock_last)。有运镜 → 不锁尾帧（锁了最后一帧会跳回首帧）；极速 / 均衡遇到运镜升到运镜档，
    高质 / 精品本来就够细，保持原档。没有运镜 → 设置里的模式 + 锁尾帧（防止人物走样）。"""
    q = current_quality()
    if has_camera_move(prompt):
        quality, lock = ("camera" if q in ("fast", "balanced") else q), False
    else:
        quality, lock = q, True
    pol = str(config.get("engine.studio.lock_last", "auto") or "auto").strip().lower()
    if pol == "always":
        lock = True
    elif pol == "never":
        lock = False
    return quality, lock


def studio_direction(prompt: str, line: str = "") -> str:
    """将 Orante 成品提示词整理为给本地 LTX-2.5 (Gemma-4 多模态导演) 的清晰剧本说明（台词另外传）。

    保留中英文静音/表情/连续动作说明，仅剥离末尾给 Veo 专用的冗长警告句。"""
    s = str(prompt or "")
    i = s.rfind("注意：上文所有内容")
    if i >= 0:
        s = s[:i]
    else:
        j = s.rfind("说话的内容：")
        if j >= 0:
            s = s[:j]
    line = (line or "").strip()
    if line and s.rstrip().endswith(line):
        s = s.rstrip()[: -len(line)]
    if line:
        # 1) 静态首帧识图常把照片里的闭嘴状态写成“双唇微抿/紧闭双唇/保持动作不变”，
        #    同时原提示词里的“强烈的面部微表情/悲恸/颤抖”在本地 LTX-2.5 蒸馏模型上会导致五官夸张扭曲与张大嘴咆哮；
        #    统一替换为克制、自然、真实的说话口型描述。
        for closed_kw in ("双唇微抿", "紧闭双唇", "嘴唇紧闭", "双唇紧闭", "嘴唇闭合", "嘴巴闭合"):
            s = s.replace(closed_kw, "双唇随温和说话自然轻柔开合")
        s = s.replace("保持动作不变", "双手与身体姿势保持端庄稳定，双唇随说话自然轻柔开合")
        s = s.replace("强烈的面部微表情", "自然平和的真实面部神态")
        s = s.replace("神情悲恸而虔诚", "神情平静温和而虔诚")
        s = s.replace("悲恸", "平和虔诚")
        s = s.replace("下颌绷紧，", "")
        s = s.replace("颤抖仍在，", "")
        s = s.replace(
            "只有祷告时自然的轻微起伏和手部的细微动作",
            "画面正中央的主角全程以平静自然的口型温和说话，双唇随台词音节自然轻柔开合（绝不夸张张大嘴、绝非画外音旁白）",
        )
    if "人物始终留在画面中间" in s:
        # 2) LTX-2.5 遇到“平移”会把主角甩出画外，遇到“微推”会直接推成怼脸大特写；
        #    改为稳固保持起始图原画幅景别（中景/全景），不推近特写、不横移、不切镜头。
        for pan_kw in (
            "镜头轻微明显地向画面左侧平移。",
            "镜头轻微明显地向画面右侧平移。",
            "镜头向画面左侧平移。",
            "镜头向画面右侧平移。",
            "镜头始终留在人群外围，",
        ):
            s = s.replace(pan_kw, "")
        s = s.replace(
            "运动要轻微柔和、明显，不要完全不动的固定镜头。人物始终留在画面中间。",
            "镜头全程平稳固定在起始图的原景别构图，保持主角稳居画面正中央，绝不推近成面部特写、绝不切镜头、绝不把主角移出画面外。",
        )
    kept_lines = []
    for ln in s.splitlines():
        parts = re.split(r"(?<=[。！？!?])", ln)
        parts = [p for p in parts if p and not any(k in p for k in _DROP_SENT)]
        kept_lines.append("".join(parts))
    s = "\n".join(kept_lines)
    s = re.sub(r"【[^【】|｜]*[|｜][^【】]*】", "", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{2,}", "\n", s).strip()
    if len(s) > 1500:
        s = s[:1500].rstrip()
    return s


# ---------------------------------------------------------------- 规格化

def target_size(aspect: str) -> tuple[int, int]:
    return (720, 1280) if aspect == "9:16" else (1280, 720)


def normalize_cmd(ffmpeg: str, raw: Path, dest: Path, seconds: float, aspect: str,
                  match_spec: bool = True) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw), "-t", f"{float(seconds):.3f}"]
    if match_spec:
        w, h = target_size(aspect)
        cmd += ["-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos+accurate_rnd+full_chroma_int,"
                       f"crop={w}:{h},unsharp=5:5:0.48:3:3:0.18,setsar=1,fps=24"]
    else:
        cmd += ["-vf", "unsharp=5:5:0.45:3:3:0.15"]
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "15", "-pix_fmt", "yuv420p"]
    cmd += ["-af", "adeclip,highpass=f=70,lowpass=f=15500,afftdn=nf=-25,equalizer=f=3200:t=q:w=1.2:g=1.8,alimiter=limit=0.92:level=disabled"]
    if match_spec:
        cmd += ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", str(dest)]
    return cmd


# ---------------------------------------------------------------- 引擎

class StudioEngine(Engine):
    name = "studio"
    can_gen_images = True

    def __init__(self, account: dict, log):
        super().__init__(account, log)
        self._active: set[str] = set()
        self._meta: dict[str, dict] = {}
        self._void: set[str] = set()
        self._done: set[str] = set()

    # ---- 生命周期
    def connect(self) -> None:
        if not bool(config.get("engine.studio.enabled", False)):
            config.set("engine.studio.enabled", True)
        h = health()
        if not h.get("ok"):
            # 优先直接拉起本地算力网关进程（无窗口、2 秒就绪），再兜底启动脚本
            ltx_root = _LTX_ROOT
            ltx_py = ltx_root / "runtime" / "venv" / "Scripts" / "python.exe"
            gw_script = ltx_root / "modules" / "studio_gateway" / "server.py"
            launched = False
            if ltx_py.is_file() and gw_script.is_file():
                self.log("  · 正在唤醒本地视频模型网关 (127.0.0.1:7870)…")
                try:
                    kw = {}
                    if sys.platform == "win32":
                        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    env = os.environ.copy()
                    env["PYTHONIOENCODING"] = "utf-8"
                    env["PYTHONUTF8"] = "1"
                    subprocess.Popen(
                        [str(ltx_py), str(gw_script)],
                        cwd=str(ltx_root),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        **kw,
                    )
                    launched = True
                except Exception as e:  # noqa: BLE001
                    self.log(f"  · 直接拉起网关失败：{e}")
            if not launched:
                launcher = str(config.get("engine.studio.launcher", "") or "").strip()
                if launcher.lower() in ("off", "none", "no", "0"):
                    launcher = ""
                elif not launcher:
                    launcher = DEFAULT_LAUNCHER
                if launcher and Path(launcher).is_file():
                    self.log(f"  · 本地模型没开，正在启动：{launcher}")
                    try:
                        kw = {}
                        if sys.platform == "win32":
                            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                            subprocess.Popen(["cmd", "/c", launcher], cwd=str(Path(launcher).parent),
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, **kw)
                        else:
                            subprocess.Popen([launcher], cwd=str(Path(launcher).parent))
                        launched = True
                    except Exception as e:  # noqa: BLE001
                        self.log(f"  · 启动失败：{e}")
            if launched:
                t0 = time.time()
                while time.time() - t0 < LAUNCH_WAIT:
                    time.sleep(2)
                    h = health()
                    if h.get("ok"):
                        break
        if h.get("ok"):
            if not h.get("comfy_reachable", h.get("comfy")):
                self.log("  · 正在唤醒本地视频模型 LTX 推理引擎 (ComfyUI 127.0.0.1:8188)…")
                try:
                    http_json("POST", "/v1/warmup", payload={}, timeout=95.0)
                except Exception:  # noqa: BLE001
                    pass
                h = health()
            if h.get("ok") and h.get("comfy"):
                self.log("  · 本地视频模型 LTX 已就绪")
                return
        if h.get("ok") and not h.get("comfy"):
            raise EngineUnavailable("本地模型的接口开着，但 ComfyUI 没开（127.0.0.1:8188）：双击 D:\\0_LTX\\启动本地算力网关.bat")
        raise EngineUnavailable(f"本地模型（Studio Zero）没开（{base_url()} 连不上）：双击 D:\\0_LTX\\启动本地算力网关.bat")

    def close(self) -> None:
        """Job 结束 / 取消：Studio 那边还没出完的就取消掉，免得显卡白跑。"""
        for gid in list(self._active):
            if gid not in self._done:
                self.cancel(gid)
        self._active.clear()

    def health(self) -> dict:
        return health()

    def capacity(self) -> int:
        try:
            n = int(config.get("engine.studio.max_inflight", 2) or 2)
        except (TypeError, ValueError):
            n = 2
        return max(0, max(1, n) - len(self._active))

    # ---- 出片
    def submit(self, seg: dict, image: Path, prompt: str, duration: int,
               last_image: Path | None = None, silent: bool = False) -> str:
        image = Path(image)
        if not image.is_file():
            raise OranteError(f"找不到起始图 {image}", "先把图下好。")
        signals = dict((seg or {}).get("_orante_signals") or {}) if isinstance(seg, dict) else {}
        if not signals.get("suite_name"):
            try:
                from ..services import suites as _suites_mod
                _sid = _suites_mod.current_id()
                if _sid:
                    signals["suite_id"] = _sid
                    signals["suite_name"] = str((_suites_mod.get(_sid) or {}).get("name") or "")
            except Exception:  # noqa: BLE001
                pass
        suite_name = str(signals.get("suite_name") or "")
        is_silent = bool(silent or signals.get("silent") or ("静音" in suite_name and not str((seg or {}).get("text") or "").strip()))
        line = "" if is_silent else str((seg or {}).get("text") or "").strip()
        if not line:
            is_silent = True
        signals["silent"] = is_silent
        voice_tag = str(signals.get("voice") or "").strip()
        direction = studio_direction(prompt, line)
        if not is_silent and voice_tag and voice_tag not in direction:
            direction = f"{voice_tag}\n{direction}".strip()
        aspect = caps.aspect_for_image(image, "flow")   # 和 Flow 同一条规则，不读账号
        quality, lock_last = pick_mode(prompt)
        body = {
            "image_path": str(image.resolve()),
            "end_image_path": str(Path(last_image).resolve()) if last_image else None,
            "direction": direction,
            "line": line,
            "silent": is_silent,
            "voice": voice_tag,
            "signals": signals,
            "seconds": int(duration),
            "aspect": aspect,
            "quality": quality,
            "lock_last": bool(lock_last),
            "seed": None,
            "client_ref": f"s{int((seg or {}).get('s') or 0):03d}",
        }
        if not direction and not line:
            raise OranteError("给本地模型的提示词是空的", "这一段既没有画面描述也没有台词。")
        try:
            r = http_json("POST", "/v1/videos", body)
        except StudioHTTPError as e:
            raise OranteError(f"本地模型没收这一段：{e.message}", "看 D:\\0_LLM\\logs\\studio_api.log。") from e
        except Exception as e:  # noqa: BLE001
            raise OranteError(f"连不上本地模型：{e}", "确认 Studio API 开着（D:\\0_LLM\\启动Studio API.bat）。") from e
        sid = str(r.get("id") or "")
        if not sid:
            raise OranteError("本地模型没有返回任务号", str(r)[:200])
        gid = GID_PREFIX + sid
        meta = {"id": sid, "seconds": int(duration), "aspect": aspect, "quality": quality, "lock_last": lock_last}
        self._meta[gid] = meta
        self._active.add(gid)
        if isinstance(seg, dict):
            seg["studio_job"] = dict(meta)
        mode = {"camera": "运镜档 704×1280 高清+音质增强", "balanced": "均衡 704×1280 高清+音质增强", "fast": "极速 576p",
                "high": "高质 768p+音质增强", "best": "精品 768p（8 步）+音质增强"}.get(quality, quality)
        self.log(f"  · 本地模型：{aspect} · {int(duration)} 秒 · {mode} · {'锁尾帧' if lock_last else '不锁尾帧'}")
        return gid

    def timeout_for(self, seg: dict | None, gid: str | None = None) -> float:
        """这一条最多等多久（从真正开始跑算，排队不算）。精品模式一条 8 秒片要十几分钟，固定 15 分钟会误判超时。"""
        meta = self._meta.get(gid or "") or ((seg or {}).get("studio_job") or {})
        q = meta.get("quality") or current_quality()
        sec = float(meta.get("seconds") or (seg or {}).get("duration") or 8)
        return max(15 * 60.0, sec * WALL_PER_SEC.get(q, 30) * 2 + 180)

    def adopt(self, gid: str, seg: dict) -> None:
        """Orante 重启后接着轮询：任务号、秒数、画幅从段上读回来。"""
        if not str(gid).startswith(GID_PREFIX):
            return
        sj = (seg or {}).get("studio_job") if isinstance((seg or {}).get("studio_job"), dict) else {}
        self._meta[gid] = {"id": gid[len(GID_PREFIX):],
                           "seconds": int(sj.get("seconds") or (seg or {}).get("duration") or 8),
                           "aspect": sj.get("aspect") or "9:16",
                           "quality": sj.get("quality") or current_quality()}
        self._active.add(gid)

    def _sid(self, gid: str) -> str:
        m = self._meta.get(gid)
        return m["id"] if m else str(gid)[len(GID_PREFIX):]

    def poll(self, gen_ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for gid in gen_ids:
            if gid in self._void:
                self._active.discard(gid)
                out[gid] = {"state": "failed", "error": "本地模型这条片子作废了，用原图重交"}
                continue
            try:
                r = http_json("GET", f"/v1/videos/{self._sid(gid)}")
            except StudioHTTPError as e:
                if e.status == 404:
                    self._active.discard(gid)
                    out[gid] = {"state": "failed", "error": "本地模型那边找不到这条（Studio API 可能重启过）"}
                else:
                    out[gid] = {"state": "pending", "queued": False}
                continue
            except Exception:  # noqa: BLE001  连不上：先当还在做，超时会兜住
                out[gid] = {"state": "pending", "queued": False}
                continue
            st = r.get("state")
            if st in ("queued", "running"):
                out[gid] = {"state": "pending", "queued": st == "queued",
                            "progress": r.get("progress")}
            elif st == "done":
                out[gid] = {"state": "done", "url": f"{base_url()}/v1/videos/{self._sid(gid)}/file"}
            else:
                self._active.discard(gid)
                why = r.get("error") or ("已取消" if st == "cancelled" else "本地模型出片失败")
                out[gid] = {"state": "failed", "error": f"本地模型：{why}"[:400]}
        return out

    def download(self, gen_id: str, dest: Path) -> Path:
        from ..services import checks
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        raw = dest.with_suffix(".raw.mp4")
        meta = self._meta.get(gen_id) or {}
        req = urllib.request.Request(f"{base_url()}/v1/videos/{self._sid(gen_id)}/file")
        try:
            with _opener().open(req, timeout=DOWNLOAD_TIMEOUT) as resp, open(raw, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
        except Exception as e:  # noqa: BLE001
            try:
                raw.unlink()
            except OSError:
                pass
            raise OranteError(f"从本地模型下载片子失败：{e}") from e
        ffmpeg = checks.find_ffmpeg()
        if not ffmpeg:
            raise OranteError("找不到 ffmpeg，本地模型的片子没法统一成 Flow 规格", checks.FFMPEG_FIX_HINT)
        cmd = normalize_cmd(ffmpeg, raw, dest, float(meta.get("seconds") or 8), meta.get("aspect") or "9:16",
                            bool(config.get("engine.studio.match_flow_spec", True)))
        try:
            from ..services.procutil import run_quiet
            r = run_quiet(cmd, timeout=300)
            if r.returncode != 0 or not dest.is_file() or dest.stat().st_size < 1024:
                err = (r.stderr or b"").decode("utf-8", "replace")[-300:]
                raise OranteError(f"本地模型的片子转格式失败：{err or 'ffmpeg 没出文件'}")
        finally:
            try:
                raw.unlink()
            except OSError:
                pass
        self._done.add(gen_id)
        self._active.discard(gen_id)
        return dest

    def cancel(self, gen_id: str) -> None:
        """取消 Studio 那边的这一条（排队中直接移出，正在跑就中断）。尽力而为。"""
        try:
            http_json("DELETE", f"/v1/videos/{self._sid(gen_id)}", timeout=10)
        except Exception:  # noqa: BLE001
            pass
        self._active.discard(gen_id)

    def forget_url(self, gen_id: str, url: str = "") -> None:
        """片子作废（首帧对不上等）：这一条不再要，下一次轮询报失败，generate 会用原图重交一次。"""
        self._void.add(gen_id)
        self.cancel(gen_id)

    def gen_images(self, prompt: str, ref: Path | None, n: int = 4, aspect: str = "9:16") -> list[Path]:
        """调用本地 FLUX.2 [klein] 4B 生图或改图（用户手动选择本地 AI 扩图/补图时触发）。"""
        from .. import paths
        tmp_dir = paths.DATA / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        w, h = (1088, 1920) if aspect == "9:16" else (1920, 1088)
        out_paths: list[Path] = []
        count = max(1, min(int(n or 1), 4))
        for i in range(count):
            payload = {
                "prompt": str(prompt or ""),
                "width": w,
                "height": h,
                "steps": 4,
                "seed": int(time.time() * 1000) % 1000000 + i,
                "edit_image": str(Path(ref).resolve()) if ref and Path(ref).is_file() else None,
            }
            r = http_json("POST", "/v1/images", payload, timeout=30)
            jid = str(r.get("id") or "")
            if not jid:
                raise OranteError("本地 FLUX.2 生图未返回任务号", str(r)[:200])
            self.log(f"  · 本地 FLUX.2 [klein] 4B 生图中（第 {i + 1}/{count} 张，任务 {jid}）…")
            t0 = time.time()
            while time.time() - t0 < 180:
                time.sleep(2.5)
                st_res = http_json("GET", f"/v1/jobs/{jid}", timeout=15)
                st = st_res.get("state")
                if st == "done":
                    dest = tmp_dir / f"flux2_{jid}_{i}.png"
                    req = urllib.request.Request(f"{base_url()}/v1/jobs/{jid}/file")
                    with _opener().open(req, timeout=60) as resp, open(dest, "wb") as f:
                        f.write(resp.read())
                    if dest.is_file() and dest.stat().st_size > 1024:
                        out_paths.append(dest)
                    break
                if st == "failed":
                    raise OranteError(f"本地 FLUX.2 生图失败：{st_res.get('error') or '未知错误'}")
        return out_paths

