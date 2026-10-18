# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
# CUTORA: 合并自 tapnet/tapnextpp/votsp2026/model.py + utils.py；
#   输入改成 RGB（CUTORA 的帧本来就是 RGB，省一次翻转）；加载强制 weights_only；
#   去掉 compile / warmup（内部工具不需要）。算法、预处理、坐标换算与上游一致。

"""TAPNextPP — high-level wrapper for TAPNext++ inference."""

from __future__ import annotations

import os
import pathlib
from typing import Tuple

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn

from cutora.third_party.tapnext import tapnext_torch


def preprocess_frame(frame_rgb: np.ndarray, device: torch.device, model_size: int = 256) -> torch.Tensor:
  """[H, W, 3] uint8 RGB → [1, 1, S, S, 3] float in [-1, 1]（双线性缩放）。"""
  t = torch.from_numpy(np.ascontiguousarray(frame_rgb)).to(device, non_blocking=True)
  t = t.permute(2, 0, 1).unsqueeze(0).float()
  t = F.interpolate(t, size=(model_size, model_size), mode='bilinear', align_corners=False)
  t = t.div_(127.5).sub_(1.0)
  return t.permute(0, 2, 3, 1).unsqueeze(0)


def display_to_model(points_xy: np.ndarray, disp_h: int, disp_w: int, model_size: int = 256) -> np.ndarray:
  scale = np.array([model_size / disp_w, model_size / disp_h], dtype=np.float32)
  return (points_xy * scale).astype(np.float32)


def model_to_display(points_xy: np.ndarray, disp_h: int, disp_w: int, model_size: int = 256) -> np.ndarray:
  scale = np.array([disp_w / model_size, disp_h / model_size], dtype=np.float32)
  return (points_xy * scale).astype(np.float32)


def make_query_tensor(model_pts_xy: np.ndarray, device: torch.device, query_timestep: int = 0) -> torch.Tensor:
  """[1, Q, 3]，布局 [t, y, x]。"""
  q = len(model_pts_xy)
  query = np.zeros((q, 3), dtype=np.float32)
  query[:, 0] = query_timestep
  query[:, 1] = model_pts_xy[:, 1]
  query[:, 2] = model_pts_xy[:, 0]
  return torch.from_numpy(query).to(device).unsqueeze(0)


class _Opaque:
  """训练检查点里夹带的非张量对象（超参数、回调状态等）的占位：只收参数，什么都不执行。"""

  def __init__(self, *args, **kwargs):
    self._args = args

  def __setstate__(self, state):
    self._state = state

  def __call__(self, *args, **kwargs):
    return _Opaque(*args, **kwargs)


def load_state_dict_safely(path) -> dict:
  """只按「张量字典」反序列化（weights_only=True），不让下载来的文件跑任意代码。

  * 运行时为别的模型设了全局 TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD，这里调用期间临时摘掉；
  * Lightning 训练检查点常夹带超参数对象（argparse.Namespace 之类），weights_only 会拒绝：
    先用 get_unsafe_globals_in_checkpoint 列出这些名字（只读不执行），全部换成 _Opaque 占位再读。
    我们只用里面的 state_dict。"""
  import pickle
  saved = os.environ.pop("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", None)
  try:
    try:
      ckpt = torch.load(str(path), map_location="cpu", weights_only=True)
    except pickle.UnpicklingError:
      ser = torch.serialization
      names = list(ser.get_unsafe_globals_in_checkpoint(str(path)))
      # 按「模块.名字」造同名的占位类（torch 按这个全名匹配白名单；各版本都认）
      stand_ins = []
      for full in names:
        mod, _, qual = str(full).rpartition(".")
        stand_ins.append(type(qual or "Opaque", (_Opaque,), {"__module__": mod, "__qualname__": qual}))
      with ser.safe_globals(stand_ins):
        ckpt = torch.load(str(path), map_location="cpu", weights_only=True)
  finally:
    if saved is not None:
      os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = saved
  state_dict = ckpt.get("state_dict", ckpt) if isinstance(ckpt, dict) else ckpt
  return {k.removeprefix("tapnext."): v for k, v in state_dict.items()}


class TAPNextPP(nn.Module):
  """逐帧在线点跟踪。第一帧给查询点，之后每帧带上 state。"""

  MODEL_SIZE: int = 256

  def __init__(self, inner: tapnext_torch.TAPNext, device: torch.device, input_resolution: int = MODEL_SIZE) -> None:
    super().__init__()
    self._model = inner
    self.device = device
    self.input_resolution = input_resolution

  @classmethod
  def build(cls, device="cpu", input_resolution: int = MODEL_SIZE, **arch) -> "TAPNextPP":
    """不加载权重的空模型（测试 / 结构检查用）。arch 可改 width / depth 等做小模型。"""
    if isinstance(device, str):
      device = torch.device(device)
    inner = tapnext_torch.TAPNext(image_size=(cls.MODEL_SIZE, cls.MODEL_SIZE), **arch)
    if input_resolution % inner.patch_size[0] != 0:
      raise ValueError("input_resolution 必须是 patch 尺寸的整数倍")
    inner = inner.to(device).eval()
    return cls(inner, device, input_resolution=input_resolution)

  @classmethod
  def from_checkpoint(cls, path: str | pathlib.Path, device="cuda", half_precision: bool = False,
                      input_resolution: int = MODEL_SIZE) -> "TAPNextPP":
    if path is None:
      raise ValueError("path must be provided explicitly.")
    if isinstance(device, str):
      device = torch.device(device)
    inner = tapnext_torch.TAPNext(image_size=(cls.MODEL_SIZE, cls.MODEL_SIZE))
    if input_resolution % inner.patch_size[0] != 0:
      raise ValueError("input_resolution 必须是 patch 尺寸的整数倍")
    inner.load_state_dict(load_state_dict_safely(path))
    inner = inner.to(device)
    inner.eval()
    if half_precision:
      inner = inner.half()
    return cls(inner, device, input_resolution=input_resolution)

  @torch.no_grad()
  def track_frame(self, frame_rgb: np.ndarray, query_points_xy: np.ndarray | None = None,
                  state: tapnext_torch.TAPNextTrackingState | None = None,
                  autocast: bool = True) -> Tuple[np.ndarray, np.ndarray, np.ndarray, tapnext_torch.TAPNextTrackingState]:
    """返回 (positions_xy [Q, 2] 显示像素, visible [Q] bool, visible_prob [Q] 0–1, state)。"""
    if query_points_xy is None and state is None:
      raise ValueError("Either query_points_xy or state must be provided.")
    h, w = frame_rgb.shape[:2]
    frame_t = preprocess_frame(frame_rgb, self.device, self.input_resolution)
    q_t = None
    if query_points_xy is not None:
      model_pts = display_to_model(np.asarray(query_points_xy, dtype=np.float32), h, w, self.MODEL_SIZE)
      q_t = make_query_tensor(model_pts, self.device)
    dtype = torch.float16 if self.device.type == "cuda" else torch.float32
    ctx = (torch.amp.autocast("cuda", dtype=dtype) if (autocast and self.device.type == "cuda")
           else torch.amp.autocast("cpu", enabled=False))
    with ctx:
      tracks, _, vis_logits, new_state = self._model(video=frame_t, query_points=q_t, state=state)
    tracks_xy = tracks[0, 0].cpu().float().numpy()[:, ::-1].copy()
    positions_xy = model_to_display(tracks_xy, h, w, self.MODEL_SIZE)
    logits = vis_logits[0, 0, :, 0].float()
    visible = (logits > 0).cpu().numpy()
    prob = torch.sigmoid(logits).cpu().numpy().astype(np.float32)
    return positions_xy, visible, prob, new_state
