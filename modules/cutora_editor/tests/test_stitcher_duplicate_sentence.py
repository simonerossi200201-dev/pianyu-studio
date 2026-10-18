# -*- coding: utf-8 -*-
"""文案重复句把片段对到第一次出现处 → 第二处被判缺失。

夹具来自 Luca 生产批次 T0011-李良v02（只读复制）。不碰媒体文件。
"""
from __future__ import annotations

import json
import os
import unittest

from cutora.batch import script_stitcher as ss

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "fixtures", "stitcher_dup")
_CLIP_KEYS = ("s005", "s006", "s021", "s022", "s023")


def _load_copy():
    with open(os.path.join(_DIR, "copy.txt"), encoding="utf-8") as f:
        return f.read()


def _load_clip_data():
    with open(os.path.join(_DIR, "clips_words.json"), encoding="utf-8") as f:
        return json.load(f)


def _make_clips(script_tokens, clip_data=None):
    data = clip_data if clip_data is not None else _load_clip_data()
    clips = []
    for key in _CLIP_KEYS:
        d = data[key]
        words = [(w[0], float(w[1]), float(w[2])) for w in d["words"]]
        clips.append({
            "path": d["name"], "name": d["name"],
            "dur": float(d["dur"] or 8.0),
            "fps": float(d["fps"] or 30.0),
            "words": words, "env": None,
            "m": ss.match_clip_global(words, script_tokens),
        })
    return clips


def _order_by_script(clips):
    hit_slots = [i for i, c in enumerate(clips)
                 if c["m"]["matched"] or c["m"]["partial"]]
    by_script = sorted(hit_slots, key=lambda i: clips[i]["m"]["s_start"])
    order = list(range(len(clips)))
    for slot, src in zip(hit_slots, by_script):
        order[slot] = src
    return order


def _plan(clips, script_tokens, pinned=False):
    order = (list(range(len(clips))) if pinned
             else _order_by_script(clips))
    return ss.plan_group_dedup(
        clips, order, script_tokens, 0.35, True,
        pinned=pinned, edge_refine=False)


def _by_tag(items, tag):
    for it in items:
        if tag in it["name"]:
            return it
    raise AssertionError("missing clip %s" % tag)


def _compact(items):
    return [{
        "name": it["name"], "s_start": it["s_start"], "s_end": it["s_end"],
        "in_t": it["in_t"], "ratio": it["ratio"], "keep": it["keep"],
        "segs": it["segs"], "note": it["note"],
    } for it in items]


class DuplicateSentenceTests(unittest.TestCase):
    def test_s022_lands_on_second_occurrence(self):
        text = _load_copy()
        toks, raws = ss.tokenize_raw_pairs(text)
        clips = _make_clips(toks)
        n_ambig = ss.resolve_ambiguous_positions(clips, toks)
        self.assertGreaterEqual(n_ambig, 1)
        items = _plan(clips, toks, pinned=False)
        s022 = _by_tag(items, "s022")
        s006 = _by_tag(items, "s006")
        # 括号内经文 44:6 / 1:14 / 14:9 / 3:16 现在拆成章+节，第二处后移。
        self.assertEqual(s022["s_start"], 293)
        self.assertEqual(s022["s_end"], 298)
        self.assertEqual(s006["in_t"], 0.0)
        self.assertGreaterEqual(s006["ratio"], 0.73)
        self.assertNotIn("裁头", s006["note"])
        missing = ss.find_missing(items, toks, raws)
        miss_txt = " ".join(m["text"] for m in missing)
        self.assertNotIn("There is only one God", miss_txt)
        self.assertIn("⇄", s022["note"])
        names = [it["name"] for it in items if it.get("keep")]
        i21 = next(i for i, n in enumerate(names) if "s021" in n)
        i22 = next(i for i, n in enumerate(names) if "s022" in n)
        i23 = next(i for i, n in enumerate(names) if "s023" in n)
        self.assertLess(i21, i22)
        self.assertLess(i22, i23)

    def test_find_all_occurrences_of_hit(self):
        toks = ss.tokenize(_load_copy())
        hit = ["there", "is", "only", "1", "god"]
        found = ss._find_all_occurrences(toks, hit, min_ratio=0.95)
        starts = [a for a, b, r in found]
        self.assertIn(63, starts)
        self.assertIn(293, starts)


class UniqueSentenceTests(unittest.TestCase):
    def test_single_occurrence_matches_pre_fix_cutlist(self):
        text = _load_copy()
        unique = text.replace(
            "The Bible reveals:\nThere is only one God.\n",
            "The Bible reveals:\n")
        toks = ss.tokenize(unique)
        clips = _make_clips(toks)
        ss.resolve_ambiguous_positions(clips, toks)
        items = _plan(clips, toks, pinned=True)
        exp_path = os.path.join(_DIR, "unique_expected.json")
        with open(exp_path, encoding="utf-8") as f:
            expected = json.load(f)
        self.assertEqual(_compact(items), expected)
        s022 = _by_tag(items, "s022")
        self.assertEqual(s022["s_start"], 63)
        self.assertEqual(s022["s_end"], 68)
        self.assertNotIn("⇄", s022["note"])


class ScriptureRefTests(unittest.TestCase):
    def test_ignore_scripture_refs_switch(self):
        script = "(John 8:34-35)"
        toks, raws = ss.tokenize_raw_pairs(script)
        items = []
        off = ss.find_missing(
            items, toks, raws, ignore_scripture_refs=False, script_text=script)
        on = ss.find_missing(
            items, toks, raws, ignore_scripture_refs=True, script_text=script)
        self.assertTrue(any("John" in (m["text"] or "") for m in off))
        self.assertFalse(any("John" in (m["text"] or "") for m in on))
        n_ign = ss.count_ignored_scripture_refs(off, script)
        self.assertGreaterEqual(n_ign, 1)
        lines = ss.format_missing_footer(ambig_count=2, scripture_ignored=3)
        self.assertTrue(any("歧义裁决: 2 处" in x for x in lines))
        self.assertTrue(any("已忽略 3 处经文出处" in x for x in lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
