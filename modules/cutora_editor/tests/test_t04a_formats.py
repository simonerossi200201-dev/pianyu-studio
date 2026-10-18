# -*- coding: utf-8 -*-
"""T04a: five-format roundtrip + malformed samples must not crash."""
from __future__ import annotations

import pytest

from cutora.subtitle.formats import load_subtitle, save_subtitle
from cutora.subtitle.formats.ass import dump_ass, load_ass
from cutora.subtitle.formats.native_json import dump_json, load_json
from cutora.subtitle.formats.srt import dump_srt, load_srt
from cutora.subtitle.formats.txt import load_txt
from cutora.subtitle.formats.vtt import dump_vtt, load_vtt
from cutora.subtitle.model import Segment, Word, segment_from_dict


def _segs():
    return [
        Segment(
            id="a", start=0.0, end=1.5, text="Hello friends.",
            words=[
                Word(text="Hello", start=0.0, end=0.7),
                Word(text="friends.", start=0.8, end=1.5),
            ],
            speaker="host", lang="en", locked=False,
        ),
        Segment(
            id="b", start=2.0, end=3.25, text="Welcome.",
            words=[Word(text="Welcome.", start=2.0, end=3.25, score=0.8)],
            lang="en",
        ),
    ]


def _keys(segments, prec=3):
    return [
        (round(s.start, prec), round(s.end, prec), (s.text or "").strip())
        for s in segments
    ]


def test_timecode_comma_dot_and_optional_hours():
    from cutora.subtitle.formats.timecode import parse_timecode
    assert parse_timecode("00:00:01,500") == pytest.approx(1.5)
    assert parse_timecode("00:00:01.500") == pytest.approx(1.5)
    assert parse_timecode("00:01.500") == pytest.approx(1.5)
    assert parse_timecode("1:02:03.4") == pytest.approx(3723.4)
    assert parse_timecode("nope") is None


def test_srt_roundtrip(tmp_path):
    src = _segs()
    path = tmp_path / "a.srt"
    save_subtitle(str(path), src, fmt="srt")
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    got = load_subtitle(str(path))
    assert _keys(got) == _keys(src)


def test_srt_reads_bom_and_dot_separator(tmp_path):
    path = tmp_path / "bom.srt"
    body = "1\n00:00:00.000 --> 00:01.250\nHi there\n"
    path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    got = load_subtitle(str(path), fmt="srt")
    assert len(got) == 1
    assert got[0].text == "Hi there"
    assert got[0].end == pytest.approx(1.25)


def test_srt_optional_bom_write(tmp_path):
    path = tmp_path / "bom.srt"
    save_subtitle(str(path), _segs(), fmt="srt", bom=True)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_vtt_roundtrip_and_line_setting(tmp_path):
    src = _segs()
    path = tmp_path / "a.vtt"
    save_subtitle(
        str(path), src, fmt="vtt",
        style={"pos_y": 864}, play_h=1080,
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT")
    assert "line:80%" in text
    got = load_subtitle(str(path))
    assert _keys(got) == _keys(src)


def test_vtt_skips_style_and_region():
    raw = (
        "WEBVTT\n\nSTYLE\n::cue { color: red; }\n\n"
        "REGION\nid:r\n\nNOTE ignore me\n\n"
        "00:00:00.000 --> 00:00:01.000\nKeep\n"
    )
    got = load_vtt(raw)
    assert [s.text for s in got] == ["Keep"]


def test_ass_roundtrip_times_and_text():
    src = _segs()
    text = dump_ass(src, style={
        "font_family": "Arimo", "fontsize": 48,
        "color_text": "#FFFFFF", "color_outline": "#000000",
        "use_stroke": True, "border_width": 2,
        "shadow_enabled": True, "pos_y": 900, "bold": True,
    }, play_w=1920, play_h=1080, karaoke=True)
    assert "Arimo" in text
    assert r"\k" in text
    got = load_ass(text)
    assert _keys(got, prec=2) == _keys(src, prec=2)


def test_ass_read_strips_tags_and_ignores_style():
    raw = (
        "[V4+ Styles]\nStyle: Default,Arial,20,&H00FFFFFF,&H000000FF,"
        "&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n"
        "[Events]\n"
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,"
        r"{\k50}Hello{\k40} world" "\n"
        "Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,skip\n"
    )
    got = load_ass(raw)
    assert len(got) == 1
    assert got[0].text == "Hello world"


def test_json_roundtrip_words_speaker_lang(tmp_path):
    src = _segs()
    path = tmp_path / "a.json"
    save_subtitle(str(path), src, fmt="json")
    got = load_subtitle(str(path))
    assert got[0].speaker == "host"
    assert got[0].lang == "en"
    assert got[0].words[0].text == "Hello"
    assert got[1].words[0].score == pytest.approx(0.8)
    again = load_json(dump_json(got))
    assert _keys(again) == _keys(src)


def test_txt_sentence_split_and_stitcher_roundtrip(tmp_path):
    raw = "Hello friends. Welcome to the studio.\n"
    got = load_txt(raw)
    assert [s.text for s in got] == [
        "Hello friends.", "Welcome to the studio.",
    ]
    path = tmp_path / "a.txt"
    save_subtitle(str(path), got, fmt="txt")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("01|")
    back = load_subtitle(str(path))
    assert [s.text for s in back] == [s.text for s in got]


def test_txt_reads_numbered_blocks():
    raw = "01|Peace be with you\n\n02|Let us pray\n"
    got = load_txt(raw)
    assert [s.text for s in got] == ["Peace be with you", "Let us pray"]


MALFORMED = [
    ("srt", b""),
    ("srt", b"not a subtitle at all"),
    ("srt", b"1\nthis has no arrow\nHi\n"),
    ("srt", b"1\n00:00:05,000 --> 00:00:01,000\nbackwards\n"),
    ("srt", b"1\n00:00:00,000 --> 00:00:01,000\n\x00null\n"),
    ("srt", b"999999\n"),
    ("vtt", b"STYLE\n::cue { color: red }\n"),
    ("vtt", b"NOTE only\n\njunk identifier\nstill no times\n"),
    ("vtt", b"00:zz:00.000 --> 00:00:01.000\nnope\n"),
    ("ass", b"[Script Info]\nTitle: empty\n"),
    ("ass", b"Dialogue: 0,oops\n"),
    ("ass", b"random binary \xff\xfe stuff"),
    ("json", b"not json"),
    ("json", b"null"),
    ("json", b'{"segments": "nope"}'),
    ("json", b'{"segments": [1, 2, {"text": "x"}]}'),
    ("txt", b"   \n\n"),
    ("txt", b"\xff\xfe\x00only-invalid-utf8"),
    ("ssa", b"Dialogue: Marked=0"),
    ("vtt", b"WEBVTT\n\nREGION\nid:r\n\n"),
]


@pytest.mark.parametrize("fmt,payload", MALFORMED)
def test_malformed_samples_do_not_crash(fmt, payload, tmp_path):
    path = tmp_path / ("bad." + ("ass" if fmt == "ssa" else fmt))
    path.write_bytes(payload)
    got = load_subtitle(str(path), fmt=fmt)
    assert isinstance(got, list)


def test_dump_srt_matches_loader_for_plain_cues():
    segs = [segment_from_dict({
        "start": 0, "end": 1.234, "text": "Hello friends.", "words": [],
    })]
    got = load_srt(dump_srt(segs))
    assert _keys(got) == _keys(segs)


def test_dump_vtt_empty_ok():
    assert "WEBVTT" in dump_vtt([])
