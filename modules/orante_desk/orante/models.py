# -*- coding: utf-8 -*-
"""任务 / 段 / manifest.json（version 2）。manifest 是段状态的唯一真值。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .errors import OranteError

SEG_STATES = ("planned", "submitted", "collected", "failed", "blocked")
IMG_ROLES = ("主体", "空镜")
IMG_STATES = ("ok", "blocked", "replaced", "missing", "pending_review")
# 段上的 role：锚点模式下是 anchor/bridge；尾帧接龙是 chain；旧数据仍可能是 主体/空镜
SEG_ROLES = ("anchor", "bridge", "chain", "主体", "空镜")
MANIFEST = "manifest.json"


def sha1_text(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()


def sha1_file(p: Path, chunk=1 << 20) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_file(p: Path, chunk=1 << 20) -> str:
    """按内容认亲用。和 sha1_file 同一套读法，只换算法。"""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


@dataclass
class Image:
    p: str                       # p01
    file: str                    # images/p01.jpg （相对任务目录）
    sha1: str = ""
    role: str = "主体"
    status: str = "ok"           # ok | blocked | replaced | missing | pending_review
    desc: str = ""
    voice: str = ""
    desc_manual: bool = False    # 用户在界面手改过描述语；自动写描述语要跳过
    voice_manual: bool = False   # 同上，音色
    desc_stale: bool = False     # 图换过了但手改描述语还留着
    eye: str = "闭眼"
    scene: str = "常规"
    measure: dict = field(default_factory=dict)
    origin: str = "download"     # download | expand | replace | local
    src: str = ""                # 原始链接或原文件名
    pose: dict = field(default_factory=dict)  # 进入状态：head/eyes/hands/body/expr/text


@dataclass
class Segment:
    s: int
    text: str
    duration: int = 6
    image: str = ""              # pNN
    role: str = "主体"            # 旧：主体/空镜；锚点模式：assign_roles 写成 anchor/bridge
    shot: str = ""
    prompt_final: str = ""
    status: str = "planned"
    gen_id: str | None = None
    takes: list = field(default_factory=list)
    error: str | None = None
    bridge: dict = field(default_factory=dict)  # 仅 role=bridge 时有内容


@dataclass
class Manifest:
    version: int
    task_id: str
    name: str
    engine: str
    account: str
    type: str
    copy: dict
    images: list
    split: dict
    segments: list
    track: dict
    prompts_snapshot: dict = field(default_factory=dict)
    gapfill: dict = field(default_factory=dict)  # {report_path: "..."} 查漏补缺清单
    mode: str = "first_frame"  # first_frame | anchor_bridge | tail_chain
    chain: dict = field(default_factory=dict)  # 尾帧接龙：起头图 / 描述语策略 / 提清间隔

    # ---- 构造 / 读写 -------------------------------------------------
    @staticmethod
    def new(task_id, name, engine, account, type_, copy_text) -> "Manifest":
        return Manifest(version=2, task_id=task_id, name=name, engine=engine, account=account, type=type_,
                        copy={"text": copy_text, "sha1": sha1_text(copy_text)},
                        images=[], split={"rule": {}}, segments=[],
                        track={"pins": {}, "pools": [], "hold": 3, "jitter": 1, "unit": "seg", "seed": None})

    @staticmethod
    def load(task_dir: Path) -> "Manifest":
        p = Path(task_dir) / MANIFEST
        if not p.is_file():
            raise OranteError(f"找不到 {p.name}", f"任务目录 {task_dir} 里没有 manifest.json，先跑「准备」。")
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise OranteError("manifest.json 不是合法 JSON", str(e))
        if d.get("version") != 2:
            raise OranteError("manifest 版本不对", f"需要 version 2，实际 {d.get('version')}")
        d.setdefault("prompts_snapshot", {})
        d.setdefault("gapfill", {})
        d.setdefault("mode", "first_frame")
        d.setdefault("chain", {})
        if d.get("mode") not in ("first_frame", "anchor_bridge", "tail_chain"):
            d["mode"] = "first_frame"
        if not isinstance(d.get("chain"), dict):
            d["chain"] = {}
        return Manifest(**{k: d[k] for k in Manifest.__dataclass_fields__ if k in d})

    def save(self, task_dir: Path) -> None:
        from .filelock import manifest_lock
        with manifest_lock(task_dir):
            self._save_unlocked(task_dir)

    def _save_unlocked(self, task_dir: Path) -> None:
        """已经拿着 .manifest.lock 时用。测试也可以直接打。"""
        self.validate()
        p = Path(task_dir) / MANIFEST
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".manifest-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)

    def validate(self) -> None:
        seen = set()
        for im in self.images:
            if im["p"] in seen:
                raise OranteError(f"图片编号重复 {im['p']}")
            seen.add(im["p"])
            if im.get("role") not in IMG_ROLES:
                raise OranteError(f"图片 {im['p']} 的 role 不合法: {im.get('role')}")
            st = im.get("status") or "ok"
            if st not in IMG_STATES:
                raise OranteError(f"图片 {im['p']} 的 status 不合法: {st}")
        if self.mode not in ("first_frame", "anchor_bridge", "tail_chain"):
            raise OranteError(f"生成模式不合法: {self.mode}")
        for sg in self.segments:
            if sg.get("status") not in SEG_STATES:
                raise OranteError(f"段 {sg.get('s')} 状态不合法: {sg.get('status')}")
            r = sg.get("role")
            if r and r not in SEG_ROLES:
                raise OranteError(f"段 {sg.get('s')} 的 role 不合法: {r}")
            if sg.get("image") and sg["image"] not in seen:
                raise OranteError(f"段 {sg['s']} 指向不存在的图片 {sg['image']}")

    # ---- 便捷 ---------------------------------------------------------
    def image(self, p: str) -> dict | None:
        return next((im for im in self.images if im["p"] == p), None)

    def subject_images(self) -> list[dict]:
        return [im for im in self.images if im.get("role") == "主体" and im.get("status") == "ok"]

    def seg(self, s: int) -> dict | None:
        return next((sg for sg in self.segments if sg["s"] == s), None)

    def counts(self) -> dict:
        c = {k: 0 for k in SEG_STATES}
        for sg in self.segments:
            c[sg["status"]] = c.get(sg["status"], 0) + 1
        c["total"] = len(self.segments)
        return c

    def clip_name(self, sg: dict) -> str:
        return f"{self.task_id}-s{sg['s']:03d}-{sg.get('image') or 'p00'}.mp4"
