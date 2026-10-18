# -*- coding: utf-8 -*-
"""规格 89 的界面冒烟：真的把悬浮图标跑起来，盯住「它还在屏幕上吗」。

0924 修的那个 bug 就是这里该拦住的：图标放大之后贴边收起，只剩一条几乎全透明的缝，
看不见也点不着。所以这支测试对每个尺寸、每条边都量一次窗口几何，
要求任何时候至少有 24×24 像素落在屏幕里。

没有 tkinter 或者没有显示器（纯命令行的 CI）就跳过。Windows 上一定跑得起来。
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tk = pytest.importorskip("tkinter", reason="这套 Python 没带 tkinter")
Image = pytest.importorskip("PIL.Image", reason="没装 Pillow")

MIN_VISIBLE = 24


def _has_display() -> bool:
    if os.name == "nt":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


pytestmark = pytest.mark.skipif(not _has_display(), reason="没有显示器，跳过界面冒烟")


@contextmanager
def open_dock(tmp_path, monkeypatch):
    """把图标真的跑起来（demo 模式，一直显示），用完关掉。

    故意不写成 pytest fixture：tools/conftest.py 只认内置 fixture 名，
    自定义 fixture 的用例会被整条丢掉（8 个"deselected"就是这么来的）。
    """
    monkeypatch.setenv("ORANTE_DATA", str(tmp_path))
    from orante import paths, floaticon as fi
    paths.reset()
    png = paths.ROOT / "Orante me.png"
    made = False
    if not png.is_file():
        from PIL import Image as I
        img = I.new("RGBA", (982, 1076), (0, 0, 0, 0))
        img.paste(I.new("RGBA", (700, 700), (243, 178, 15, 255)), (140, 180))
        img.save(png)
        made = True
    d = fi.Dock(demo=True)
    d.sw, d.sh = 1920, 1080        # 固定一块虚拟屏幕，换机器也一样
    d.show()
    try:
        yield d
    finally:
        try:
            d.quit()
        finally:
            if made:
                png.unlink(missing_ok=True)


def _geometry(d):
    d.root.update_idletasks()
    g = d.root.geometry()                      # "WxH+X+Y"
    wh, x, y = g.split("+")[0], int(g.split("+")[1]), int(g.split("+")[2])
    w, h = (int(v) for v in wh.split("x"))
    return w, h, x, y


def _visible(d) -> tuple[int, int]:
    w, h, x, y = _geometry(d)
    return min(x + w, d.sw) - max(x, 0), min(y + h, d.sh) - max(y, 0)


def _assert_visible(d, tag: str):
    vw, vh = _visible(d)
    assert vw >= MIN_VISIBLE and vh >= MIN_VISIBLE, \
        f"{tag}：窗口 {_geometry(d)} 在屏幕上只剩 {vw}×{vh}，用户会以为图标没了"


class Wheel:
    def __init__(self, up=True):
        self.delta = 120 if up else -120


def test_visible_at_every_size_on_every_edge(tmp_path, monkeypatch):
  with open_dock(tmp_path, monkeypatch) as d:
        _assert_visible(d, "刚出现")
        for side, (x, y) in (("left", (2, 400)), ("right", (d.sw - 40, 700)),
                             ("top", (700, 2)), ("bottom", (700, d.sh - 60))):
            for size in (40, 96, 160, 256):
                d.set_size(size)
                d.x, d.y = x, y
                d._sync_side(save=False)
                assert d.side == side, (side, size, d.side)
                d.peeking = False          # 鼠标离开 = 收起来
                d._paint()
                _assert_visible(d, f"{side} 收起 size={size}")
                d.peeking = True           # 鼠标碰上来 = 整张图
                d._paint()
                _assert_visible(d, f"{side} 展开 size={size}")


def test_wheel_to_max_then_collapse_stays_visible(tmp_path, monkeypatch):
    """用户踩的那条路：滚轮一路放大，然后鼠标移开。"""
    with open_dock(tmp_path, monkeypatch) as d:
        d.x, d.y = d.sw - 120, 500
        d._sync_side(save=False)
        for _ in range(40):
            d._on_wheel(Wheel(up=True))
        from orante import floaticon as fi
        assert d.size == fi.MAX_SIZE
        _assert_visible(d, "放到最大")
        d.peeking = False
        d._paint()
        _assert_visible(d, "最大之后收起")
        # 再滚回最小也得在
        for _ in range(60):
            d._on_wheel(Wheel(up=False))
        assert d.size == fi.MIN_SIZE
        d.peeking = False
        d._paint()
        _assert_visible(d, "缩到最小收起")


def test_drag_to_corner_stays_visible(tmp_path, monkeypatch):
    """拖到四个角上松手。"""
    with open_dock(tmp_path, monkeypatch) as d:
        for x, y in ((-500, -500), (d.sw + 500, -500), (-500, d.sh + 500),
                     (d.sw + 500, d.sh + 500)):
            d.x, d.y = fi_fit(d, x, y)
            d._sync_side(save=False)
            d.peeking = False
            d._paint()
            _assert_visible(d, f"角落 {x},{y}")


def fi_fit(d, x, y):
    from orante import floaticon as fi
    return fi.fit_on_screen(x, y, d.size, d.sw, d.sh)


def test_size_and_pos_are_remembered(tmp_path, monkeypatch):
    from orante import config, db, floaticon as fi
    monkeypatch.setenv("ORANTE_DATA", str(tmp_path))
    db.init()                      # 记大小 / 位置要写 settings 表
    with open_dock(tmp_path, monkeypatch) as d:
        d.set_size(128)
        d.x, d.y = 600, 300
        d._sync_side(save=True)
        assert int(config.get("ui.float_icon.size")) == 128
        assert fi.parse_pos(config.get("ui.float_icon.pos")) == (600, 300)


def test_stays_collapsed_while_ticking(tmp_path, monkeypatch):
    """0924 的「贴边不收起」：每 2 秒跟一次设置，那条路以前会把图标重新撑开。

    这里强制每一轮都走「跟随设置」分支，鼠标按在别处，图标必须一直是收起的。
    """
    from orante import db
    monkeypatch.setenv("ORANTE_DATA", str(tmp_path))
    db.init()
    with open_dock(tmp_path, monkeypatch) as d:
        d.set_size(248)
        d.x, d.y = d.sw - 140, 600
        d._sync_side(save=False)
        assert d.side == "right"
        d._pointer_over = lambda: False       # 鼠标不在图标上
        d._sync_peek()
        w0 = _geometry(d)[0]
        assert w0 < 248, f"松手后应该收成徽标，现在还是 {w0}"
        for _ in range(5):
            d._last_cfg = 0                   # 强制走跟随设置那一段
            d._tick_once()
            assert _geometry(d)[0] == w0, f"第 {_} 轮被重新撑开了：{_geometry(d)}"
        _assert_visible(d, "一直收着")


def test_pointer_back_expands_again(tmp_path, monkeypatch):
    """鼠标回到徽标上要能恢复整张图，不靠 Enter 事件也行。"""
    from orante import db
    monkeypatch.setenv("ORANTE_DATA", str(tmp_path))
    db.init()
    with open_dock(tmp_path, monkeypatch) as d:
        d.set_size(160)
        d.x, d.y = 4, 500
        d._sync_side(save=False)
        d._pointer_over = lambda: False
        d._sync_peek()
        assert _geometry(d)[0] < 160
        d._pointer_over = lambda: True
        d._sync_peek()
        assert _geometry(d)[0] == 160
        _assert_visible(d, "碰上来展开")
