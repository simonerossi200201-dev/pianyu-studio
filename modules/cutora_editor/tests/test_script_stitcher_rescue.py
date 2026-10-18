# -*- coding: utf-8 -*-
"""Tik009 低置信误报: 选词表看命中, 复核要查没对上的段。不需要 GPU。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cutora.batch import script_stitcher as ss


class TranscriptBetterTests(unittest.TestCase):
    def test_more_hits_wins(self):
        self.assertTrue(ss._transcript_better(
            {"hit": 9, "ratio": 1.0}, {"hit": 3, "ratio": 1.0}))

    def test_same_hits_higher_ratio_wins(self):
        # 9 个正确词从 30 个废话里洗出来
        self.assertTrue(ss._transcript_better(
            {"hit": 9, "ratio": 1.0}, {"hit": 9, "ratio": 0.31}))

    def test_same_hits_lower_ratio_loses(self):
        self.assertFalse(ss._transcript_better(
            {"hit": 9, "ratio": 0.31}, {"hit": 9, "ratio": 1.0}))

    def test_equal_not_better(self):
        self.assertFalse(ss._transcript_better(
            {"hit": 9, "ratio": 1.0}, {"hit": 9, "ratio": 1.0}))

    def test_fewer_hits_loses_even_if_ratio_higher(self):
        self.assertFalse(ss._transcript_better(
            {"hit": 2, "ratio": 1.0}, {"hit": 9, "ratio": 0.4}))


class AdoptTests(unittest.TestCase):
    def test_adopt_cleaner_same_hits(self):
        clip = {"m": {"hit": 9, "ratio": 0.31, "matched": False}, "words": []}
        junk = (["fame", "and", "wealth", "can", "draw", "you", "away",
                 "from", "god"]
                + ["mm", "yeah"] * 10)
        script = ss.tokenize(
            "Fame and wealth can draw you away from God. "
            "Pride can cause you to resist God.")
        dirty = [(t, 0, 0) for t in junk]
        clip["m"] = ss.match_clip_global(dirty, script)
        self.assertLess(clip["m"]["ratio"], ss.MATCH_RATIO_LOW)
        clean = [(t, 0, 0) for t in
                 ["fame", "and", "wealth", "can", "draw",
                  "you", "away", "from", "god"]]
        self.assertTrue(ss._adopt_if_better(clip, clean, script, "test"))
        self.assertGreaterEqual(clip["m"]["ratio"], ss.MATCH_RATIO_LOW)
        self.assertTrue(clip["m"]["matched"])


class SuspectClipsTests(unittest.TestCase):
    def test_unmatched_in_hole_comes_first(self):
        clips = [
            {"name": "s001.mp4",
             "m": {"matched": True, "partial": False, "hit": 10}},
            {"name": "s002.mp4",
             "m": {"matched": False, "partial": False, "hit": 0}},
            {"name": "s003.mp4",
             "m": {"matched": True, "partial": False, "hit": 10}},
        ]
        items = [
            {"name": "s001.mp4", "keep": True, "s_runs": [[0, 10]]},
            {"name": "s002.mp4", "keep": True, "s_runs": []},
            {"name": "s003.mp4", "keep": True, "s_runs": [[20, 30]]},
        ]
        holes = [{"s": 10, "e": 20, "text": "missing"}]
        got = ss._suspect_clips(holes, clips, items, max_n=4)
        self.assertEqual(got[0], 1, "洞里的低置信段必须排在邻居前面")
        self.assertIn(0, got)
        self.assertIn(2, got)

    def test_all_matched_only_neighbors(self):
        clips = [
            {"name": "a.mp4", "m": {"matched": True, "partial": False}},
            {"name": "b.mp4", "m": {"matched": True, "partial": False}},
        ]
        items = [
            {"name": "a.mp4", "keep": True, "s_runs": [[0, 10]]},
            {"name": "b.mp4", "keep": True, "s_runs": [[20, 30]]},
        ]
        holes = [{"s": 10, "e": 20, "text": "gap"}]
        got = ss._suspect_clips(holes, clips, items, max_n=4)
        self.assertEqual(got, [0, 1])

    def test_no_unmatched_no_neighbors(self):
        clips = [{"name": "a.mp4", "m": {"matched": True, "partial": False}}]
        items = [{"name": "a.mp4", "keep": True, "s_runs": [[0, 50]]}]
        self.assertEqual(ss._suspect_clips([], clips, items), [])


class DilutionMatchTests(unittest.TestCase):
    def test_junk_dilution_drops_below_threshold(self):
        script = ss.tokenize(
            "Fame and wealth can draw you away from God.")
        good = [(t, 0, 0) for t in ss.tokenize(
            "Fame and wealth can draw you away from God.")]
        m0 = ss.match_clip_global(good, script)
        self.assertTrue(m0["matched"])
        dirty = good + [("mm", 0, 0)] * 20
        m1 = ss.match_clip_global(dirty, script)
        self.assertFalse(m1["matched"])
        self.assertEqual(m1["hit"], m0["hit"])


if __name__ == "__main__":
    unittest.main()
