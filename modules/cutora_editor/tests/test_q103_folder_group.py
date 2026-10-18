# -*- coding: utf-8 -*-
"""Q103: 以文件夹为一组。文件名对不上规则的视频不再被丢掉。"""
import json
import os

import pytest
from PySide6.QtWidgets import QApplication

from cutora.batch import script_stitcher as ss


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application


def _touch(folder, name):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as fh:
        fh.write(b"\x00")
    return path


def _base(paths):
    return [os.path.basename(p) for p in paths]


def test_random_names_are_one_group_only_when_folder_mode(tmp_path):
    """四个对不上命名规则的视频。关着是 0 组，开着是 1 组且按自然序。"""
    folder = tmp_path / "乱命名"
    for name in ("随便起的名字.mp4", "另一个.mp4", "a10.mp4", "a2.mp4",
                 "note.txt", "cover.jpg"):
        _touch(str(folder), name)

    off, fo, bo, notes = ss.scan_groups_multi([str(folder)], group_by_folder=False)
    assert off == {}
    assert fo == {}
    assert bo == {}
    assert notes == []

    on, fo, bo, notes = ss.scan_groups_multi([str(folder)], group_by_folder=True)
    assert list(on) == ["乱命名"]
    assert _base(on["乱命名"]) == ["a2.mp4", "a10.mp4", "另一个.mp4", "随便起的名字.mp4"]
    assert fo["乱命名"] == os.path.normpath(str(folder))
    assert bo["乱命名"] == "乱命名"
    assert notes == []
    assert _base(ss.group_by_folder_scan(str(folder))) == _base(on["乱命名"])


def test_two_folders_keep_their_own_names(tmp_path):
    """两个文件夹就是两组。叫「片段」的那个组号仍是文件夹名，不是上级目录。"""
    clips = tmp_path / "Tik012" / "片段"
    seats = tmp_path / "场次" / "观众席素材"
    _touch(str(clips), "a10.mp4")
    _touch(str(clips), "a2.mp4")
    _touch(str(seats), "b.mp4")

    groups, folder_of, base_of, notes = ss.scan_groups_multi(
        [str(clips), str(seats)], group_by_folder=True)
    assert list(groups) == ["片段", "观众席素材"]
    assert _base(groups["片段"]) == ["a2.mp4", "a10.mp4"]
    assert _base(groups["观众席素材"]) == ["b.mp4"]
    assert base_of["片段"] == "片段"
    assert base_of["观众席素材"] == "观众席素材"
    assert os.path.basename(folder_of["片段"]) == "片段"
    assert os.path.basename(folder_of["观众席素材"]) == "观众席素材"
    assert "Tik012" not in groups
    assert notes == []


def test_same_folder_name_does_not_collide(tmp_path):
    """同名文件夹在不同上级目录下：表里的键错开，base_of 仍是原文件夹名。"""
    left = tmp_path / "左" / "同名"
    right = tmp_path / "右" / "同名"
    _touch(str(left), "左片.mp4")
    _touch(str(right), "右片.mp4")

    groups, folder_of, base_of, notes = ss.scan_groups_multi(
        [str(left), str(right)], group_by_folder=True)
    assert list(groups) == ["同名", "同名(右)"]
    assert _base(groups["同名"]) == ["左片.mp4"]
    assert _base(groups["同名(右)"]) == ["右片.mp4"]
    assert base_of["同名"] == "同名"
    assert base_of["同名(右)"] == "同名"
    assert os.path.normpath(folder_of["同名"]) == os.path.normpath(str(left))
    assert os.path.normpath(folder_of["同名(右)"]) == os.path.normpath(str(right))
    assert notes == [
        "组名 同名 在多个文件夹里都有, 表里改叫 同名(右) "
        "(成品仍叫 同名.mp4, 写回它自己的文件夹)"
    ]


def test_output_subdirs_are_not_members(tmp_path):
    """_stitched / _scatter / _invalid 和再下一层的视频都不进组。"""
    box = tmp_path / "盒子"
    _touch(str(box), "a10.mp4")
    _touch(str(box), "a2.mp4")
    _touch(str(box / "_stitched"), "done.mp4")
    _touch(str(box / "_scatter"), "piece.mp4")
    _touch(str(box / "_invalid"), "bad.mp4")
    _touch(str(box / "extra"), "nested.mp4")

    groups, _fo, _bo, notes = ss.scan_groups_multi([str(box)], group_by_folder=True)
    assert list(groups) == ["盒子"]
    assert _base(groups["盒子"]) == ["a2.mp4", "a10.mp4"]
    assert notes == []

    found = [os.path.basename(p) for p in ss.find_material_dirs(str(box))]
    assert sorted(found) == ["extra", "盒子"]


def test_empty_folder_is_skipped_with_a_note(tmp_path):
    """没有视频就不产生空组，notes 里写明为什么没出现。"""
    empty = tmp_path / "空的"
    empty.mkdir()
    texty = tmp_path / "只有说明"
    texty.mkdir()
    _touch(str(texty), "readme.txt")

    groups, folder_of, base_of, notes = ss.scan_groups_multi(
        [str(empty), str(texty)], group_by_folder=True)
    assert groups == {}
    assert folder_of == {}
    assert base_of == {}
    assert notes == [
        "%s 里没有视频，跳过" % os.path.normpath(str(empty)),
        "%s 里没有视频，跳过" % os.path.normpath(str(texty)),
    ]


def test_folder_mode_ignores_prefix_marks(tmp_path):
    """传了前缀规则也得和没传完全一样。关掉时这条规则会把「随便」丢掉。"""
    folder = tmp_path / "带前缀规则"
    for name in ("Tik009_v02.mp4", "随便.mp4", "Tik009_v01.mp4"):
        _touch(str(folder), name)

    off, _, _, _ = ss.scan_groups_multi(
        [str(folder)], "Tik00x", "v0x", group_by_folder=False)
    assert list(off) == ["Tik009"]
    assert _base(off["Tik009"]) == ["Tik009_v01.mp4", "Tik009_v02.mp4"]

    with_marks = ss.scan_groups_multi(
        [str(folder)], "Tik00x", "v0x", group_by_folder=True)
    no_marks = ss.scan_groups_multi([str(folder)], group_by_folder=True)
    assert with_marks == no_marks
    groups = with_marks[0]
    assert list(groups) == ["带前缀规则"]
    assert _base(groups["带前缀规则"]) == [
        "Tik009_v01.mp4", "Tik009_v02.mp4", "随便.mp4"]


def test_filename_mode_matches_legacy_scan(tmp_path):
    """group_by_folder 不传或显式 False：老命名的分组、顺序、重名改名与改前一致。"""
    folder_a = tmp_path / "A"
    folder_b = tmp_path / "B"
    for name in ("01b.mp4", "01a.mp4", "Tik001-s002-p03.mp4",
                 "Tik001-s001-p01.mp4", "Tik001_v02.mp4", "Tik001_v01.mp4"):
        _touch(str(folder_a), name)
    for name in ("01b.mp4", "01a.mp4"):
        _touch(str(folder_b), name)
    folders = [str(folder_a), str(folder_b)]

    def check(result):
        groups, folder_of, base_of, notes = result
        assert list(groups) == ["01", "01(B)", "Tik001"]
        assert _base(groups["01"]) == ["01a.mp4", "01b.mp4"]
        assert _base(groups["01(B)"]) == ["01a.mp4", "01b.mp4"]
        assert _base(groups["Tik001"]) == [
            "Tik001-s001-p01.mp4",
            "Tik001-s002-p03.mp4",
            "Tik001_v01.mp4",
            "Tik001_v02.mp4",
        ]
        assert os.path.basename(folder_of["01"]) == "A"
        assert os.path.basename(folder_of["01(B)"]) == "B"
        assert os.path.basename(folder_of["Tik001"]) == "A"
        assert all(os.path.dirname(p) == folder_of["01"] for p in groups["01"])
        assert all(os.path.dirname(p) == folder_of["Tik001"] for p in groups["Tik001"])
        assert base_of == {"01": "01", "01(B)": "01", "Tik001": "Tik001"}
        assert notes == [
            "组名 01 在多个文件夹里都有, 表里改叫 01(B) "
            "(成品仍叫 01.mp4, 写回它自己的文件夹)"
        ]

    check(ss.scan_groups_multi(folders))
    check(ss.scan_groups_multi(folders, group_by_folder=False))


def test_parse_scripts_matches_chinese_folder_name(tmp_path):
    """文件夹名当组号。显式「观众席素材|文案」要对上正文，不能把整行塞回去。"""
    folder = tmp_path / "观众席素材"
    _touch(str(folder), "随便.mp4")
    groups, _fo, base_of, _notes = ss.scan_groups_multi(
        [str(folder)], group_by_folder=True)
    assert list(groups) == ["观众席素材"]
    assert base_of["观众席素材"] == "观众席素材"
    assert ss._norm_key("观众席素材") == "观众席素材"

    scripts, err = ss.parse_scripts("观众席素材|今天的口播就到这里", list(groups))
    assert err is None
    assert scripts == {"观众席素材": "今天的口播就到这里"}

    # 两段的书写顺序和组列表相反，必须按标签对上，不能按出现顺序硬套。
    keys = ["观众席素材", "片头"]
    text = "片头|先放这一段\n\n观众席素材|席位上的口播"
    scripts, err = ss.parse_scripts(text, keys)
    assert err is None
    assert scripts["片头"] == "先放这一段"
    assert scripts["观众席素材"] == "席位上的口播"

    scripts, err = ss.parse_scripts("Hello: this is the caption", ["01"])
    assert err is None
    assert scripts == {"01": "Hello: this is the caption"}


def test_parse_scripts_numbered_keys_unchanged():
    """01 / Tik001 这种老的显式编号还是只取分隔符后面的正文。"""
    text = "01|第一段\n\nTik001|第二段\n\n02|第三段"
    scripts, err = ss.parse_scripts(text, ["01", "02", "Tik001"])
    assert err is None
    assert scripts == {"01": "第一段", "02": "第三段", "Tik001": "第二段"}


# stitch 在加上 group_by_folder 之前的参数。叶子值和当时逐字相同。
_STITCH_PARAMS_BEFORE = {
    "dir": {
        "type": "path", "required": True,
        "desc": "素材目录。里面是按组命名的短视频，例如 01.mp4、01 (2).mp4、01a.mp4",
    },
    "captions": {
        "type": "text|path", "required": True,
        "desc": ("文案。三种写法：1) 直接把全文放进这个字段；"
                 "2) @文件路径 读取文本文件；3) 字符串 \"-\" 表示从 stdin 读。"
                 "文本格式二选一：每段以「01|文案」或「01:文案」开头；"
                 "或按空行分段，段数必须和素材组数一致。"),
    },
    "language": {"type": "string", "default": "en",
                 "desc": "识别/对齐语言。常用 en（英语）或 it（意大利语）"},
    "out_dir": {"type": "path", "default": "<dir>\\\\_stitched",
                "desc": "成品目录。默认是素材目录下的 _stitched"},
    "allow_blocking": {"type": "bool", "default": False,
                       "desc": "素材体检发现断号/无音轨/组内分辨率不一致时，默认停下。true 才继续"},
    "quarantine": {"type": "bool", "default": True,
                   "desc": "把根本不是视频的坏文件挪到 <dir>/_invalid/。只移动不删除"},
    "write_project": {"type": "bool", "default": True,
                      "desc": "成功后写出 stitched.asproj.json，供后续 auto_subtitle / 导出使用"},
    "allow_missing": {"type": "bool", "default": False,
                      "desc": "有缺失文案时默认 exit 3 不写时间线。true 仍写出时间线工程"},
    "analyze": {"type": "bool", "default": True,
                "desc": "false = 只跑素材体检，不调 whisper、不写时间线。给「仅检查」用"},
    "encode": {"type": "bool", "default": True,
               "desc": "false = 分析到 cutlist 为止。true 写出时间线工程（不再拼中间 mp4）"},
    "ignore_scripture_refs": {
        "type": "bool", "default": True,
        "desc": "括号里的经文出处（如 (John 8:34-35)）不列入缺失清单。配音不会念这些。默认 true"},
}


def test_describe_stitch_gains_only_group_by_folder():
    """--describe 的 stitch 参数多了 group_by_folder，其余叶子值一个字没动。"""
    from cutora.batch.cli import OPS_SPEC
    spec = next(item for item in OPS_SPEC if item["name"] == "stitch")
    params = spec["params"]
    assert set(params) == set(_STITCH_PARAMS_BEFORE) | {"group_by_folder"}
    for key, val in _STITCH_PARAMS_BEFORE.items():
        assert params[key] == val, key
    assert params["group_by_folder"] == {
        "type": "bool",
        "default": False,
        "desc": "true 时一个文件夹算一组，忽略文件名与前缀规则。文件随便命名也能拼",
    }


def test_dialog_checkbox_disables_prefixes_and_rescans(app, tmp_path):
    """勾上之后两个前缀还在，但是灰的；扫描改按文件夹，选择写进配置且不冲掉别的键。"""
    folder = tmp_path / "乱命名"
    for name in ("随便起的名字.mp4", "另一个.mp4", "a10.mp4", "a2.mp4", "note.txt"):
        _touch(str(folder), name)
    from cutora.core.paths import settings_file
    path = settings_file("stitch_naming.json")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "group_mark": "Tik00x",
            "order_mark": "v0x",
            "folders": [str(folder)],
            "keep_me": {"a": 1},
        }, fh, ensure_ascii=False, indent=2)

    dlg = ss.StitcherDialog(None, lambda: None, lambda *a: None, lambda *a: None)
    try:
        dlg.show()
        app.processEvents()
        assert dlg.chk_group_by_folder.text() == "以文件夹为一组（忽略文件名）"
        assert dlg.chk_group_by_folder.isChecked() is False
        assert dlg.ed_group_mark.isEnabled()
        assert dlg.ed_order_mark.isEnabled()
        assert dlg.ed_group_mark.isVisible()
        assert dlg.ed_group_mark.text() == "Tik00x"
        assert dlg.ed_order_mark.text() == "v0x"
        assert dlg._groups == {}

        dlg.chk_group_by_folder.setChecked(True)
        app.processEvents()
        assert dlg.ed_group_mark.isVisible()
        assert dlg.ed_order_mark.isVisible()
        assert dlg.ed_group_mark.isEnabled() is False
        assert dlg.ed_order_mark.isEnabled() is False
        assert dlg.ed_group_mark.text() == "Tik00x"
        assert list(dlg._groups) == ["乱命名"]
        assert _base(dlg._groups["乱命名"]) == [
            "a2.mp4", "a10.mp4", "另一个.mp4", "随便起的名字.mp4"]
        assert dlg._base_of["乱命名"] == "乱命名"

        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
        assert saved["group_by_folder"] is True
        assert saved["group_mark"] == "Tik00x"
        assert saved["order_mark"] == "v0x"
        assert saved["keep_me"] == {"a": 1}
        assert saved["folders"] == [str(folder)]
    finally:
        dlg.close()

    again = ss.StitcherDialog(None, lambda: None, lambda *a: None, lambda *a: None)
    try:
        assert again.chk_group_by_folder.isChecked() is True
        assert again.ed_group_mark.isEnabled() is False
        assert again.ed_order_mark.isEnabled() is False
        assert list(again._groups) == ["乱命名"]
        again.chk_group_by_folder.setChecked(False)
        app.processEvents()
        assert again.ed_group_mark.isEnabled()
        assert again.ed_order_mark.isEnabled()
        assert again._groups == {}
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
        assert saved["group_by_folder"] is False
        assert saved["group_mark"] == "Tik00x"
        assert saved["order_mark"] == "v0x"
        assert saved["keep_me"] == {"a": 1}
    finally:
        again.close()
