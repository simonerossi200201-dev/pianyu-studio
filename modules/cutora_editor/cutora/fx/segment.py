# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-D · 提示式分割后端（本地推理，不碰网络）。

两个后端，同一个接口：

* :class:`Sam2Backend` —— Meta SAM 2.1（Apache 2.0），走 transformers 自带的 ``Sam2VideoModel`` /
  ``Sam2VideoProcessor``（不需要 sam2 / hydra / iopath 包），权重从「模型管理」下载到
  ``runtime/models/matte_sam21_*/``，``HF_HUB_OFFLINE=1`` 下只读本地目录。
* :class:`FakeBackend` —— 颜色相似度泛洪填充，确定性、零依赖，给测试和 ``CUTORA_MATTE_BACKEND=fake`` 用。

提示（prompt）的坐标是**源帧像素**（没裁切 / 没翻转的那张）：
    {"obj": 1, "x": 312.0, "y": 240.0, "label": 1}      正点 label=1 / 负点 label=0
    {"obj": 1, "box": [x0, y0, x1, y1]}

接口::

    backend.segment(rgb_u8, prompts) -> {obj_id: (alpha float32 (h, w) 0–1, score 0–1)}
    backend.propagate(frames, prompts_by_offset, on_frame, cancel=None)
        frames: 可迭代 (src_frame_idx, rgb_u8)，按喂给模型的顺序（倒着传播就倒着喂 —— 对模型只是另一段视频）
        prompts_by_offset: {喂入序号: prompts}；序号 0 必须有提示
        on_frame(src_frame_idx, {obj_id: (alpha, score)}) -> False 可提前停
"""
from __future__ import annotations

import os
import threading
import time

import numpy as np

from cutora.core.log import get_logger

_LOG = get_logger("cutora.fx.segment")
_MODEL_LOCK = threading.RLock()
_LOADED: dict = {}        # model_id -> (model, processor, device, dtype)

LOW_CONFIDENCE = 0.5      # 低于这个分数的帧在时间线上画成黄色


def backend_name() -> str:
    """环境变量 CUTORA_MATTE_BACKEND=fake 走假后端（测试 / 没有模型时的演示）。"""
    return str(os.environ.get("CUTORA_MATTE_BACKEND") or "sam2").strip().lower()


def _group_prompts(prompts):
    by_obj: dict = {}
    for p in prompts or []:
        if not isinstance(p, dict):
            continue
        try:
            oid = int(p.get("obj", 1))
        except (TypeError, ValueError):
            continue
        by_obj.setdefault(oid, {"points": [], "labels": [], "box": None})
        if "box" in p and p["box"] is not None:
            b = [float(v) for v in p["box"]][:4]
            if len(b) == 4:
                x0, x1 = sorted((b[0], b[2]))
                y0, y1 = sorted((b[1], b[3]))
                by_obj[oid]["box"] = [x0, y0, x1, y1]
        elif "x" in p and "y" in p:
            by_obj[oid]["points"].append([float(p["x"]), float(p["y"])])
            by_obj[oid]["labels"].append(1 if int(p.get("label", 1)) else 0)
    return {k: v for k, v in by_obj.items() if v["points"] or v["box"] is not None}


# ───────────────────────────── 设备 ─────────────────────────────

def pick_device():
    """(device_str, dtype_str, note_zh)。没有 CUDA 就用 CPU（慢 10–20×，UI 要明示）。"""
    try:
        import torch
    except Exception:
        return "cpu", "float32", "未安装 PyTorch"
    try:
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            major, _minor = torch.cuda.get_device_capability(0)
            dtype = "bfloat16" if major >= 8 else "float16"
            return "cuda", dtype, name
    except Exception:
        pass
    return "cpu", "float32", "无 NVIDIA 显卡，用 CPU"


def vram_free_gb() -> float | None:
    try:
        import torch
        if torch.cuda.is_available():
            free, _total = torch.cuda.mem_get_info()
            return free / (1024 ** 3)
    except Exception:
        return None
    return None


# ───────────────────────────── SAM 2.1 ─────────────────────────────

class Sam2Backend:
    """transformers 的 Sam2VideoModel 流式推理（一帧一帧喂，不把整段视频放进显存）。

    不用 Sam2VideoProcessor：transformers 5 的图像处理器要 torchvision，运行时没装。
    预处理（1024×1024 双线性 + ImageNet 均值方差）、提示点缩放（×1024/宽高）、
    后处理（双线性放回原尺寸 + sigmoid）都在这里做，和处理器的算法一致。"""

    name = "sam2"
    IMG = 1024
    MEAN = (0.485, 0.456, 0.406)
    STD = (0.229, 0.224, 0.225)

    def __init__(self, model_id: str = "matte-sam21-large", model_dir: str | None = None,
                 device: str | None = None, dtype: str | None = None):
        self.model_id = model_id
        self.model_dir = model_dir
        self.device_pref = device
        self.dtype_pref = dtype
        self.model = None
        self.device = "cpu"
        self.dtype = None
        self.load_seconds = 0.0

    # ---- 加载 ----
    def _resolve_dir(self) -> str:
        if self.model_dir:
            return self.model_dir
        from cutora.asr.models import get_model, model_folder, verify_model
        row = get_model(self.model_id)
        if row is None:
            raise RuntimeError("未登记的模型 %s" % self.model_id)
        if verify_model(row) != "ready":
            raise RuntimeError("模型 %s 未下载或校验失败，请到「设置 → 模型」下载" % self.model_id)
        return model_folder(row)

    def ensure_loaded(self):
        with _MODEL_LOCK:
            hit = _LOADED.get(self.model_id)
            if hit is not None:
                self.model, self.device, self.dtype = hit
                return
            t0 = time.perf_counter()
            import torch
            from transformers import Sam2VideoModel
            folder = self._resolve_dir()
            dev, dt, _note = pick_device()
            dev = self.device_pref or dev
            dt = self.dtype_pref or dt
            dtype = getattr(torch, dt, torch.float32)
            if dev == "cpu":
                dtype = torch.float32
            model = Sam2VideoModel.from_pretrained(folder, dtype=dtype, local_files_only=True)
            model = model.to(dev).eval()
            self.model, self.device, self.dtype = model, dev, dtype
            _LOADED[self.model_id] = (model, dev, dtype)
            self.load_seconds = time.perf_counter() - t0
            _LOG.info("SAM 2.1 已加载 %s device=%s dtype=%s 用时 %.1fs", self.model_id, dev, dt, self.load_seconds)

    # ---- 内部 ----
    def _new_session(self, h: int, w: int):
        from transformers import Sam2VideoInferenceSession
        return Sam2VideoInferenceSession(
            video=None, video_height=int(h), video_width=int(w),
            inference_device=self.device, inference_state_device=self.device,
            video_storage_device="cpu", dtype=self.dtype, max_vision_features_cache_size=1)

    def _prep(self, rgb):
        """(h, w, 3) uint8 RGB → (3, 1024, 1024) 归一化张量（在推理设备上）。"""
        import torch
        import torch.nn.functional as F
        t = torch.from_numpy(np.ascontiguousarray(rgb)).to(self.device)
        t = t.permute(2, 0, 1).unsqueeze(0).float()
        t = F.interpolate(t, size=(self.IMG, self.IMG), mode="bilinear", align_corners=False, antialias=True)
        t = t / 255.0
        mean = torch.tensor(self.MEAN, device=self.device).view(1, 3, 1, 1)
        std = torch.tensor(self.STD, device=self.device).view(1, 3, 1, 1)
        t = (t - mean) / std
        return t[0].to(self.dtype)

    def _add_prompts(self, session, feed_idx: int, prompts, h: int, w: int):
        import torch
        groups = _group_prompts(prompts)
        if not groups:
            return []
        sx, sy = float(self.IMG) / float(w), float(self.IMG) / float(h)
        obj_ids = sorted(groups)
        for oid in obj_ids:
            g = groups[oid]
            coords, labels = [], []
            if g["box"] is not None:                      # 框 = 两个角点，标签 2 / 3（SAM 约定），必须排在点前面
                x0, y0, x1, y1 = g["box"]
                coords += [[x0 * sx, y0 * sy], [x1 * sx, y1 * sy]]
                labels += [2, 3]
            for (x, y), lab in zip(g["points"], g["labels"]):
                coords.append([x * sx, y * sy])
                labels.append(1 if lab else 0)
            obj_idx = session.obj_id_to_idx(int(oid))
            session.add_point_inputs(obj_idx, feed_idx, {
                "point_coords": torch.tensor(coords, dtype=torch.float32).view(1, 1, -1, 2),
                "point_labels": torch.tensor(labels, dtype=torch.int32).view(1, 1, -1),
            })
            session.remove_mask_inputs(obj_idx, feed_idx)
        session.obj_with_new_inputs = [int(o) for o in obj_ids]
        return obj_ids

    def _masks_from_output(self, out, h: int, w: int):
        """放大到缓存尺寸（最长边 ≤ matte_store.MASK_MAX_EDGE），不是源尺寸：4K 素材省掉一大半放大和写盘。"""
        import torch
        import torch.nn.functional as F
        from cutora.fx.matte_store import mask_dims
        h, w = mask_dims(int(h), int(w))
        logits = out.pred_masks.float()
        if logits.dim() == 3:
            logits = logits.unsqueeze(1)
        up = F.interpolate(logits, size=(int(h), int(w)), mode="bilinear", align_corners=False)
        alpha = torch.sigmoid(up).squeeze(1).clamp_(0.0, 1.0).to("cpu").numpy()   # (n, H, W)
        scores = torch.sigmoid(out.object_score_logits.float().reshape(-1)).to("cpu").numpy()
        result = {}
        for i, oid in enumerate(list(out.object_ids or [])):
            s = float(scores[i]) if i < len(scores) else 1.0
            result[int(oid)] = (np.ascontiguousarray(alpha[i], dtype=np.float32), s)
        return result

    @staticmethod
    def _prune(session, feed_idx: int, keep_frames: set, window: int = 48):
        """把早于 window 的像素帧和非条件输出扔掉，显存 / 内存不随长度线性涨。"""
        try:
            pf = session.processed_frames
            if pf:
                for k in [k for k in pf if k < feed_idx - 2 and k not in keep_frames]:
                    pf.pop(k, None)
            for od in session.output_dict_per_obj.values():
                nc = od.get("non_cond_frame_outputs") or {}
                for k in [k for k in nc if k < feed_idx - window]:
                    nc.pop(k, None)
        except Exception:
            pass

    # ---- 接口 ----
    def segment(self, rgb, prompts):
        self.ensure_loaded()
        import torch
        h, w = rgb.shape[:2]
        with torch.inference_mode():
            session = self._new_session(h, w)
            px = self._prep(rgb)
            obj_ids = self._add_prompts(session, 0, prompts, h, w)
            if not obj_ids:
                return {}
            out = self.model(inference_session=session, frame=px, frame_idx=0)
            return self._masks_from_output(out, h, w)

    def propagate(self, frames, prompts_by_offset, on_frame, cancel=None):
        self.ensure_loaded()
        import torch
        session = None
        keep = set(int(k) for k in prompts_by_offset)
        n = 0
        with torch.inference_mode():
            for feed_idx, (src_idx, rgb) in enumerate(frames):
                if cancel is not None and cancel.is_set():
                    break
                h, w = rgb.shape[:2]
                if session is None:
                    session = self._new_session(h, w)
                px = self._prep(rgb)
                prompts = prompts_by_offset.get(feed_idx)
                if prompts:
                    self._add_prompts(session, feed_idx, prompts, h, w)
                elif feed_idx == 0:
                    raise RuntimeError("第一帧没有提示点，无法传播")
                # frame_idx 显式给：_prune 会把旧像素帧弹掉，不能靠 len(processed_frames) 自增
                out = self.model(inference_session=session, frame=px, frame_idx=feed_idx)
                masks = self._masks_from_output(out, h, w)
                self._prune(session, feed_idx, keep)
                n += 1
                if on_frame(int(src_idx), masks) is False:
                    break
        return n

    def release(self):
        release_models()


def release_models() -> None:
    """卸载已加载的 SAM 模型并归还显存（和 ASR 的 release_gpu_models 一起被「释放显存」调用）。"""
    with _MODEL_LOCK:
        _LOADED.clear()
    try:
        import gc
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def is_loaded(model_id: str | None = None) -> bool:
    with _MODEL_LOCK:
        return bool(_LOADED) if model_id is None else model_id in _LOADED


# ───────────────────────────── 假后端 ─────────────────────────────

class FakeBackend:
    """颜色相似度泛洪：正点所在的同色连通区 = 对象；负点所在的连通区挖掉；框 = 只保留框内。

    传播 = 用上一帧 mask 的质心当正点再做一次（够测试「动起来的色块」）。确定性，无随机。"""

    name = "fake"

    def __init__(self, tol: int = 40):
        self.tol = int(tol)

    def ensure_loaded(self):
        return None

    def _flood(self, rgb, x, y):
        import cv2
        h, w = rgb.shape[:2]
        x = int(min(max(0, round(x)), w - 1))
        y = int(min(max(0, round(y)), h - 1))
        mask = np.zeros((h + 2, w + 2), np.uint8)
        img = np.ascontiguousarray(rgb)
        flags = 4 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE
        cv2.floodFill(img, mask, (x, y), 0, (self.tol,) * 3, (self.tol,) * 3, flags)
        return mask[1:-1, 1:-1]

    def _segment_obj(self, rgb, g):
        h, w = rgb.shape[:2]
        acc = np.zeros((h, w), np.uint8)
        for (x, y), lab in zip(g["points"], g["labels"]):
            if lab:
                acc = np.maximum(acc, self._flood(rgb, x, y))
        if g["box"] is not None and not any(g["labels"]):
            x0, y0, x1, y1 = [int(round(v)) for v in g["box"]]
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            acc = np.maximum(acc, self._flood(rgb, cx, cy))
        for (x, y), lab in zip(g["points"], g["labels"]):
            if not lab:
                acc[self._flood(rgb, x, y) > 0] = 0
        if g["box"] is not None:
            x0, y0, x1, y1 = [int(round(v)) for v in g["box"]]
            box = np.zeros_like(acc)
            box[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = 1
            acc = acc * box
        area = float((acc > 0).sum()) / float(max(1, h * w))
        score = 1.0 if area > 0.002 else 0.2
        return acc.astype(np.float32) / 255.0, score

    def segment(self, rgb, prompts):
        groups = _group_prompts(prompts)
        return {oid: self._segment_obj(rgb, g) for oid, g in groups.items()}

    def propagate(self, frames, prompts_by_offset, on_frame, cancel=None):
        last: dict = {}
        n = 0
        for feed_idx, (src_idx, rgb) in enumerate(frames):
            if cancel is not None and cancel.is_set():
                break
            prompts = prompts_by_offset.get(feed_idx)
            if prompts:
                groups = _group_prompts(prompts)
            else:
                groups = {}
                for oid, (m, _s) in last.items():
                    ys, xs = np.nonzero(m > 0.5)
                    if len(xs) == 0:
                        groups[oid] = {"points": [], "labels": [], "box": None}
                        continue
                    groups[oid] = {"points": [[float(xs.mean()), float(ys.mean())]], "labels": [1], "box": None}
            out = {}
            for oid, g in groups.items():
                if g["points"] or g["box"] is not None:
                    out[oid] = self._segment_obj(rgb, g)
                else:
                    out[oid] = (np.zeros(rgb.shape[:2], np.float32), 0.0)
            last = out
            n += 1
            if on_frame(int(src_idx), out) is False:
                break
        return n

    def release(self):
        return None


def make_backend(model_id: str = "matte-sam21-large"):
    if backend_name() == "fake":
        return FakeBackend()
    return Sam2Backend(model_id)
