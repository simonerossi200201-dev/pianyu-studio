# -*- coding: utf-8 -*-
"""引擎抽象。调度器只认这个接口，不认 Flow / Grok。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable


class Engine:
    name = "base"
    can_gen_images = False

    def __init__(self, account: dict, log: Callable[[str], None]):
        self.account = account
        self.log = log

    # 生命周期
    def connect(self) -> None:            # 拉起/连上这个账号的 Chrome，确认已登录
        raise NotImplementedError

    def close(self) -> None:
        pass

    def health(self) -> dict:             # {ok, logged_in, url, note}
        raise NotImplementedError

    # 出片
    def capacity(self) -> int:            # 还能同时挂几条
        return 1

    def submit(self, seg: dict, image: Path, prompt: str, duration: int,
               last_image: Path | None = None) -> str:
        """提交一段，返回 gen_id。抛 BlockedImage 表示图被拒。

        last_image 有值时走首尾帧：image 是起始画面，last_image 是结束画面。
        """
        raise NotImplementedError

    def poll(self, gen_ids: list[str]) -> dict[str, dict]:
        """gen_id -> {state: pending|done|failed|blocked, url?, error?}"""
        raise NotImplementedError

    def download(self, gen_id: str, dest: Path) -> Path:
        raise NotImplementedError

    def forget_url(self, gen_id: str, url: str = "") -> None:
        """落盘自证作废某个下载地址。调用方直接 eng.forget_url(...)，不再 getattr 探测。

        子类应把 url 记进作废名单，poll / 取地址再拿到同一个就当没出片、继续等。
        基类空操作：新引擎如果不覆盖，作废信息会被丢掉——那是实现遗漏，请覆盖本方法。
        """
        return

    # 出图（只有 Flow）
    def gen_images(self, prompt: str, ref: Path | None, n: int = 4, aspect: str = "9:16") -> list[Path]:
        raise NotImplementedError(f"{self.name} 不支持生图")


class BlockedImage(Exception):
    """平台拒了这张图（知名人士 / 违规 / 未成年等）。"""


class EngineUnavailable(Exception):
    """没登录 / Chrome 没起来 / 额度用完。"""


def can_gen_images(engine_name: str) -> bool:
    """这个引擎自己能不能生图。调度/路由用，不用真去连浏览器。"""
    if engine_name == "flow":
        from . import flow_api
        return bool(flow_api.FlowEngine.can_gen_images)
    if engine_name == "grok":
        from . import grok
        return bool(grok.GrokEngine.can_gen_images)
    if engine_name == "studio":
        from . import studio
        return bool(studio.StudioEngine.can_gen_images)
    return False


def make_engine(account: dict, log) -> Engine:
    eng = account["engine"]
    if eng == "flow":
        from . import flow_api
        return flow_api.FlowEngine(account, log)
    if eng == "grok":
        from . import grok
        return grok.GrokEngine(account, log)
    if eng == "studio":
        from . import studio
        return studio.StudioEngine(account, log)
    raise ValueError(f"未知引擎 {eng}")
