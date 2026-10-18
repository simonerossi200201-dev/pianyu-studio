# -*- coding: utf-8 -*-
"""规格 89：悬浮图标。

界面部分（置顶、拖拽、右键菜单）只有 Windows 上才看得见，这里测的是能离开界面验的那一半：
窗口登记表、「是不是全都最小化了」的判断、贴边几何、大小钳制、单实例锁、图片处理。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-89-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import config, floaticon as fi, paths, winlist  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("ORANTE_DATA", str(tmp_path))
    paths.reset()
    yield


# ---------- 窗口登记表 ----------

def test_winlist_write_read_remove():
    winlist.write(9400, pid=111, hwnd=222, title="Orante 制片台")
    winlist.write(9401, pid=333, hwnd=444, title="Orante 制片台 · :9401")
    live = winlist.live()
    assert [r["port"] for r in live] == [9400, 9401]
    assert live[0]["hwnd"] == 222 and live[1]["title"].endswith(":9401")
    winlist.remove(9400)
    assert [r["port"] for r in winlist.live()] == [9401]


def test_winlist_stale_dropped():
    winlist.write(9400, hwnd=1)
    old = winlist.path_for(9400)
    import json
    rec = json.loads(old.read_text(encoding="utf-8"))
    rec["beat"] = time.time() - 120
    old.write_text(json.dumps(rec), encoding="utf-8")
    assert winlist.live() == []
    # 自定义过期时间也认
    assert len(winlist.live(stale=300)) == 1


def test_winlist_bad_file_ignored():
    winlist.write(9400, hwnd=1)
    (winlist.dir_() / "9999.json").write_text("{坏掉的 json", encoding="utf-8")
    assert [r["port"] for r in winlist.live()] == [9400]


# ---------- 什么时候浮出来 ----------

def test_should_show_rules():
    assert fi.should_show([]) is False
    assert fi.should_show([{"hwnd": 1, "minimized": True}]) is True
    assert fi.should_show([{"hwnd": 1, "minimized": True},
                           {"hwnd": 2, "minimized": True}]) is True
    # 只要还有一个开着就不出来
    assert fi.should_show([{"hwnd": 1, "minimized": True},
                           {"hwnd": 2, "minimized": False}]) is False
    # 句柄还不知道（浏览器模式）按开着算
    assert fi.should_show([{"hwnd": 0, "minimized": True}]) is False


# ---------- 大小 / 位置 ----------

def test_clamp_size():
    assert fi.clamp_size(96) == 96
    assert fi.clamp_size(5) == fi.MIN_SIZE
    assert fi.clamp_size(9999) == fi.MAX_SIZE
    assert fi.clamp_size("上上下下") == fi.DEFAULT_SIZE
    assert fi.clamp_size(None) == fi.DEFAULT_SIZE


def test_parse_pos():
    assert fi.parse_pos("120,340") == (120, 340)
    assert fi.parse_pos("") is None
    assert fi.parse_pos("x,y") is None
    assert fi.parse_pos(None) is None


def test_fit_on_screen_keeps_it_reachable():
    sw, sh, size = 1920, 1080, 96
    # 跑到屏幕外也要留半个身子在屏幕里
    x, y = fi.fit_on_screen(5000, 5000, size, sw, sh)
    assert x <= sw - size // 2 and y <= sh - size // 2
    x, y = fi.fit_on_screen(-500, -500, size, sw, sh)
    assert x >= -size // 2 and y >= 0
    # 本来就在屏幕里的不动
    assert fi.fit_on_screen(800, 400, size, sw, sh) == (800, 400)


# ---------- 贴边 ----------

def test_dock_side_picks_nearest_edge():
    sw, sh, size = 1920, 1080, 96
    assert fi.dock_side(4, 500, size, sw, sh) == "left"
    assert fi.dock_side(sw - size - 3, 500, size, sw, sh) == "right"
    assert fi.dock_side(800, 2, size, sw, sh) == "top"
    assert fi.dock_side(800, sh - size - 1, size, sw, sh) == "bottom"
    assert fi.dock_side(800, 500, size, sw, sh) == ""


def test_badge_is_always_visible_and_clickable():
    """0924 的坑：原来贴边只留 12 像素，圆图标那一条几乎全透明——

    分层窗口透明处鼠标穿透，于是既看不见也点不着，图越大越明显。
    现在贴边是缩成一个整块都在屏幕里的小徽标。
    """
    sw, sh = 1920, 1080
    for size in (40, 96, 160, 256):
        badge = fi.badge_size(size)
        assert badge >= fi.BADGE_MIN
        assert badge <= size
        for side in ("left", "right", "top", "bottom"):
            bx, by = fi.badge_pos(300, 500, size, badge, sw, sh, side)
            # 整块都在屏幕里 = 一定看得见、点得着
            assert 0 <= bx <= sw - badge, (size, side, bx)
            assert 0 <= by <= sh - badge, (size, side, by)
        # 贴哪边就紧贴哪边
        assert fi.badge_pos(300, 500, size, badge, sw, sh, "left")[0] == 0
        assert fi.badge_pos(300, 500, size, badge, sw, sh, "right")[0] == sw - badge
        assert fi.badge_pos(300, 500, size, badge, sw, sh, "top")[1] == 0
        assert fi.badge_pos(300, 500, size, badge, sw, sh, "bottom")[1] == sh - badge


def test_badge_follows_the_icon_centre():
    sw, sh, size = 1920, 1080, 96
    badge = fi.badge_size(size)
    top = fi.badge_pos(300, 100, size, badge, sw, sh, "right")[1]
    bottom = fi.badge_pos(300, 900, size, badge, sw, sh, "right")[1]
    assert top < bottom                      # 原来在上面，收起来也在上面


def test_pull_in_shows_the_whole_icon():
    sw, sh, size = 1920, 1080, 160
    assert fi.pull_in(-50, -50, size, sw, sh) == (0, 0)
    assert fi.pull_in(sw, sh, size, sw, sh) == (sw - size, sh - size)
    assert fi.pull_in(500, 500, size, sw, sh) == (500, 500)


def test_no_sliver_logic_left():
    src = (ROOT / "orante" / "floaticon.py").read_text(encoding="utf-8")
    assert "SLIVER" not in src               # 只留一条缝的做法已经废了
    assert "docked_pos" not in src


def test_tick_and_callbacks_are_guarded():
    """循环断了图标就再也不出现——所以每一轮、每个回调都得兜住异常。"""
    src = (ROOT / "orante" / "floaticon.py").read_text(encoding="utf-8")
    assert "def _tick_once" in src and "self.root.after(POLL_MS, self.tick)" in src
    assert src.count("@_guard") >= 8
    main = (ROOT / "orante" / "main.py").read_text(encoding="utf-8")
    assert "floaticon.running()" in main     # 挂了要能自己回来
    assert "floaticon.spawn()" in main


def test_dock_then_expand_returns_to_home():
    """收起来是徽标，鼠标碰上来是整张图，位置还是原来那个家。"""
    sw, sh, size = 1920, 1080, 96
    x, y = sw - size - 5, 500                 # 丢在右边上
    assert fi.dock_side(x, y, size, sw, sh) == "right"
    badge = fi.badge_size(size)
    bx, by = fi.badge_pos(x, y, size, badge, sw, sh, "right")
    assert bx == sw - badge
    # 展开回去仍然整张在屏幕里，而且还算贴着右边
    ux, uy = fi.pull_in(x, y, size, sw, sh)
    assert ux == x and 0 <= ux <= sw - size      # 本来就放得下，不用挪
    assert fi.dock_side(ux, uy, size, sw, sh) == "right"
    assert fi.pull_in(sw - 10, 500, size, sw, sh)[0] == sw - size   # 超出去的才拉回来


def test_no_dock_when_far():
    sw, sh, size = 1920, 1080, 96
    assert fi.dock_side(800, 500, size, sw, sh) == ""
    assert fi.badge_pos(800, 500, size, fi.badge_size(size), sw, sh, "") == (800, 500)


# ---------- 单实例锁 ----------

def test_lock_beat_and_running():
    assert fi.running() is False
    fi.lock_beat()
    # 自己写的锁不算「别人在跑」
    assert fi.running() is False
    import json
    fi.lock_file().write_text(json.dumps({"pid": os.getpid() + 1, "beat": time.time()}),
                              encoding="utf-8")
    assert fi.running() is True
    fi.lock_file().write_text(json.dumps({"pid": os.getpid() + 1, "beat": time.time() - 600}),
                              encoding="utf-8")
    assert fi.running() is False          # 心跳过期 = 那个进程没了


def test_lock_clear_only_mine():
    import json
    fi.lock_file().parent.mkdir(parents=True, exist_ok=True)
    fi.lock_file().write_text(json.dumps({"pid": os.getpid() + 1, "beat": time.time()}),
                              encoding="utf-8")
    fi.lock_clear()
    assert fi.lock_file().is_file()       # 不是我的锁，不许删
    fi.lock_beat()
    fi.lock_clear()
    assert not fi.lock_file().is_file()


# ---------- 图 ----------

def test_load_icon_keeps_aspect_and_alpha(tmp_path, monkeypatch):
    src = tmp_path / "Orante me.png"
    Image.new("RGBA", (982, 1076), (10, 120, 200, 255)).save(src)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    assert fi.icon_file() == src
    img = fi.load_icon(96)
    assert img.size == (96, 96) and img.mode == "RGBA"
    # 竖图放进方块里，左右会留透明边
    assert img.getpixel((1, 48))[3] == 0
    assert img.getpixel((48, 48))[3] == 255


def test_flatten_on_key_is_opaque(tmp_path, monkeypatch):
    src = tmp_path / "Orante me.png"
    Image.new("RGBA", (100, 100), (0, 0, 0, 0)).save(src)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    flat = fi.flatten_on_key(fi.load_icon(64))
    assert flat.mode == "RGB" and flat.size == (64, 64)
    assert flat.getpixel((0, 0)) == (255, 0, 254)     # 全透明的地方填抠除色


def test_icon_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "UI", tmp_path / "ui")
    (tmp_path / "ui" / "assets" / "png").mkdir(parents=True)
    fb = tmp_path / "ui" / "assets" / "png" / "icon-256.png"
    Image.new("RGBA", (256, 256)).save(fb)
    assert fi.icon_file() == fb


# ---------- 接线 ----------

def test_defaults_registered():
    for k, v in (("ui.float_icon", True), ("ui.float_icon.size", 96),
                 ("ui.float_icon.edge_hide", True)):
        assert config.DEFAULTS[k] == v
    assert "ui.float_icon.pos" in config.HIDDEN_KEYS      # 位置不进设置页


def test_spawn_is_windows_only(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert fi.spawn() is False


def test_main_py_registers_window():
    src = (ROOT / "orante" / "main.py").read_text(encoding="utf-8")
    assert "_bind_float_icon" in src
    assert "floaticon.spawn" in src
    assert "winlist.remove" in src        # 关窗注销


def test_demo_mode_flag():
    src = (ROOT / "orante" / "floaticon.py").read_text(encoding="utf-8")
    assert "--demo" in src        # 自测脚本靠它在 Windows 上直接看效果


def test_no_new_dependency():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for bad in ("pyqt", "pystray", "pyinstaller", "pywin32"):
        assert bad not in req, f"悬浮图标不许引入 {bad}"


def test_settings_panel_shows_the_three_keys():
    js = (ROOT / "ui" / "js" / "parts" / "settings.js").read_text(encoding="utf-8")
    for k in ("'ui.float_icon'", "'ui.float_icon.size'", "'ui.float_icon.edge_hide'"):
        assert k in js, k
    assert "'ui.float_icon.pos'" in js        # 位置不给人调
    assert "'ui.float_icon.size': [40, 256]" in js


def test_i18n_has_labels():
    import json
    for name in ("zh", "en"):
        d = json.loads((ROOT / "orante" / "i18n" / f"{name}.json").read_text(encoding="utf-8"))
        for k in ("set.key.ui.float_icon", "set.hint.ui.float_icon",
                  "set.key.ui.float_icon.size", "set.key.ui.float_icon.edge_hide",
                  "set.unit.ui.float_icon.size"):
            assert d.get(k), (name, k)


def test_log_goes_to_a_file(tmp_path, monkeypatch):
    """图标是没有控制台的独立进程，出事必须能在日志里找到。"""
    monkeypatch.setenv("ORANTE_DATA", str(tmp_path))
    paths.reset()
    fi._log("测试一行")
    assert "测试一行" in fi.log_path().read_text(encoding="utf-8")


def test_user_can_switch_it_off():
    """设置里能关、右键菜单里也能关；默认必须是开着的。"""
    assert config.DEFAULTS["ui.float_icon"] is True
    src = (ROOT / "orante" / "floaticon.py").read_text(encoding="utf-8")
    assert "def turn_off" in src
    assert '"ui.float_icon", False' in src          # 菜单里关 = 写总开关
    assert 'cfg("ui.float_icon", True)' in src      # 关了之后图标自己退出
    main = (ROOT / "orante" / "main.py").read_text(encoding="utf-8")
    assert 'config.get("ui.float_icon", True)' in main   # 关了就别再拉起来
    js = (ROOT / "ui" / "js" / "parts" / "settings.js").read_text(encoding="utf-8")
    assert "'ui.float_icon'" in js                  # 设置页看得见这个勾


def test_config_sync_never_forces_expand():
    """跟随设置那条路不许碰 peeking（0924「贴边不收起」的根因）。"""
    src = (ROOT / "orante" / "floaticon.py").read_text(encoding="utf-8")
    assert "at_pointer=False" in src
    assert "_size_saved" in src
