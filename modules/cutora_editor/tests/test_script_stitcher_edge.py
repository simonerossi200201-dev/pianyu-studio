# -*- coding: utf-8 -*-
"""切口精修 + 多文件夹扫描的回归测试。不需要 GPU, 也不碰真实素材。

看住的是 0901 那个毛病: 文案最后一个词讲完之后, Flow 渲染的片段常常还带
半秒该剪掉的碎音 (to / and / an ...)。老规则「词尾 + 固定尾部余量」下刀会
正好剐在这半个音上, 拼起来一听就跳。
"""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cutora.batch import script_stitcher as ss


def mk_env(segments, dur, floor=-50.0, hop=ss.ENV_HOP):
    """造一条包络: segments = [(起, 止, dB), ...], 其余是噪声底。"""
    n = int(round(dur / hop))
    db = np.full(n, floor, dtype=np.float32)
    for a, b, lv in segments:
        db[int(round(a / hop)):int(round(b / hop))] = lv
    return {"db": db, "hop": hop, "floor": floor}


class RefineTailTests(unittest.TestCase):
    def test_cuts_before_untranscribed_noise(self):
        # 词尾 1.00s, 1.20s 起有一段 ASR 没转出来的碎音 → 必须切在 1.20 之前
        env = mk_env([(0.0, 1.00, -20.0), (1.20, 1.60, -22.0)], 2.0)
        t = ss.refine_tail(env, 1.00, 1.35, None, 2.0)
        self.assertLess(t, 1.20)
        self.assertGreater(t, 1.00)

    def test_cuts_before_next_word(self):
        # ASR 认出了后面那个该剪掉的词 → 切点不许越过它的起点
        env = mk_env([(0.0, 1.00, -20.0), (1.10, 1.60, -22.0)], 2.0)
        t = ss.refine_tail(env, 1.00, 1.35, 1.10, 2.0)
        self.assertLessEqual(t, 1.10)

    def test_clean_tail_keeps_old_behaviour(self):
        # 词尾之后一路安静 → 出点必须和老版本一模一样 (词尾 + 尾部余量)
        env = mk_env([(0.0, 1.00, -20.0)], 3.0)
        self.assertAlmostEqual(ss.refine_tail(env, 1.00, 1.35, None, 3.0), 1.35, 3)

    def test_no_env_still_respects_next_word(self):
        # 拿不到音频包络时也不许切进下一个词
        t = ss.refine_tail(None, 1.00, 1.35, 1.10, 3.0)
        self.assertLessEqual(t, 1.10)
        self.assertGreater(t, 1.00)

    def test_never_exceeds_default_or_duration(self):
        env = mk_env([(0.0, 1.00, -20.0)], 1.2)
        t = ss.refine_tail(env, 1.00, 1.20, None, 1.2)
        self.assertLessEqual(t, 1.20)

    def test_room_tone_wobble_does_not_shorten(self):
        # 静音里底噪自己抖 8dB 不算"声音回来了" (否则每段都会莫名变短)
        env = mk_env([(0.0, 1.00, -20.0), (1.15, 1.20, -42.0)], 3.0)
        self.assertAlmostEqual(ss.refine_tail(env, 1.00, 1.35, None, 3.0), 1.35, 3)


class RefineHeadTests(unittest.TestCase):
    def test_cuts_after_leading_noise(self):
        # 词头 1.00s, 0.60~0.80 有前一句的尾音 → 入点必须落在 0.80 之后
        env = mk_env([(0.60, 0.80, -20.0), (1.00, 1.60, -20.0)], 2.0)
        a = ss.refine_head(env, 1.00, 0.70, None)
        self.assertGreaterEqual(a, 0.80)
        self.assertLess(a, 1.00)

    def test_clean_head_keeps_old_behaviour(self):
        env = mk_env([(1.00, 1.60, -20.0)], 2.0)
        self.assertAlmostEqual(ss.refine_head(env, 1.00, 0.70, None), 0.70, 3)


class EnvelopeTests(unittest.TestCase):
    def test_roundtrip_precision(self):
        rng = np.random.default_rng(0)
        audio = (rng.standard_normal(16000) * 0.05).astype(np.float32)
        env = ss.compute_envelope(audio)
        self.assertIsNotNone(env)
        back = ss.env_from_b64(ss.env_to_b64(env))
        self.assertEqual(len(back["db"]), len(env["db"]))
        self.assertLessEqual(float(np.max(np.abs(back["db"] - env["db"]))), 0.3)

    def test_too_short_returns_none(self):
        self.assertIsNone(ss.compute_envelope(np.zeros(10, dtype=np.float32)))

    def test_bad_b64_returns_none(self):
        self.assertIsNone(ss.env_from_b64("not base64 至少不会炸"))
        self.assertIsNone(ss.env_from_b64(""))


class MultiFolderTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="stitch_multi_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _touch(self, *parts):
        p = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
        return p

    def test_collision_keeps_output_name_and_folder(self):
        self._touch("A", "01a.mp4")
        self._touch("A", "01b.mp4")
        self._touch("B", "01a.mp4")
        g, folder_of, base_of, notes = ss.scan_groups_multi(
            [os.path.join(self.root, "A"), os.path.join(self.root, "B")])
        self.assertEqual(len(g), 2)                 # 两组都在, 谁也没被吞掉
        self.assertEqual(sorted(base_of.values()), ["01", "01"])
        self.assertEqual(len(set(folder_of.values())), 2)
        self.assertTrue(notes)

    def test_single_folder_matches_scan_groups(self):
        self._touch("A", "01a.mp4")
        self._touch("A", "02a.mp4")
        one = ss.scan_groups(os.path.join(self.root, "A"))
        g, _, base_of, notes = ss.scan_groups_multi([os.path.join(self.root, "A")])
        self.assertEqual(g, one)                    # 单文件夹行为一个字不变
        self.assertFalse(notes)

    def test_find_material_dirs_skips_outputs(self):
        self._touch("Tik012", "片段", "Tik012-s001-p01.mp4")
        self._touch("Tik012", "片段", "_stitched", "Tik012.mp4")
        self._touch("Tik013", "片段", "Tik013-s001-p01.mp4")
        found = ss.find_material_dirs(self.root)
        self.assertEqual(len(found), 2)
        self.assertFalse(any("_stitched" in d for d in found))


class ShortRunTests(unittest.TestCase):
    """0901 事故: 开头落单的一个词被剪枝扔掉, 成品开头整句没了。

    现场是 04a: 文案 "WHAT SINS SHOULD CHRISTIANS…", whisperX 把 sins 听成
    sin, what 于是成了一个 1 词的孤立 run。老代码的 >=2 剪枝把它扔掉,
    保留区间从 should 才开始 —— 而覆盖表用的是剪枝前的数据, 所以缺失检测
    还一个字都不报。
    """

    SCRIPT = ("what sins should christians be most watchful of "
              "the bible repeatedly warns us about five dangerous sins today")

    def _clip(self, spoken, name="04a.mp4", dur=15.0):
        toks = ss.tokenize(self.SCRIPT)
        words, t = [], 0.03
        for w in spoken:
            words.append((w, round(t, 3), round(t + 0.30, 3)))
            t += 0.40
        return toks, {"path": name, "name": name, "dur": dur, "fps": 30.0,
                      "words": words, "env": None,
                      "m": ss.match_clip_global(words, toks)}

    def test_leading_word_survives_asr_error(self):
        # sins → sin: what 落单, 但它紧挨着大块 → 必须留在成品里
        spoken = ["what", "sin"] + self.SCRIPT.split()[2:]
        toks, clip = self._clip(spoken)
        items = ss.plan_group_dedup([clip], [0], toks, 0.35, True, pinned=True)
        self.assertEqual(items[0]["segs"][0][0], 0.0)

    def test_far_isolated_word_still_pruned(self):
        # 只在文案最末尾蹭中一个词, 中间隔着几十个词 → 仍然按老规矩剪掉,
        # 不许为了一个孤词把一整段废话拖进成品
        toks = ss.tokenize(self.SCRIPT)
        spoken = ["what", "sins", "should", "christians"] + \
                 ["blah"] * 8 + ["today"]
        words = [(w, round(0.03 + i * 0.4, 3), round(0.33 + i * 0.4, 3))
                 for i, w in enumerate(spoken)]
        clip = {"path": "x.mp4", "name": "x.mp4", "dur": 15.0, "fps": 30.0,
                "words": words, "env": None,
                "m": ss.match_clip_global(words, toks)}
        items = ss.plan_group_dedup([clip], [0], toks, 0.35, True, pinned=True)
        # 末尾那个孤词 today 前面隔着 8 个废话词 → 出点必须停在它之前
        self.assertLess(items[0]["segs"][-1][1], words[-1][1])

    def test_first_segment_head_never_moves_later(self):
        # 首段的入点绝不许因为精修而往后挪 (那等于剪掉开头的正词)
        env = mk_env([(0.0, 0.90, -20.0), (1.00, 2.00, -20.0)], 3.0)
        words = [("junk", 0.03, 0.90)] + [
            (w, 1.00 + i * 0.4, 1.30 + i * 0.4)
            for i, w in enumerate(ss.tokenize(self.SCRIPT))]
        toks = ss.tokenize(self.SCRIPT)
        clip = {"path": "y.mp4", "name": "y.mp4", "dur": 12.0, "fps": 30.0,
                "words": words, "env": env,
                "m": ss.match_clip_global(words, toks)}
        items = ss.plan_group_dedup([clip], [0], toks, 0.35, True, pinned=True,
                                    edge_refine=True)
        t0 = words[1][1]
        self.assertLessEqual(items[0]["segs"][0][0], max(0.0, t0 - ss.HEAD_PAD) + 1e-6)


def test_audio_window_cmd_decodes_only_the_slice():
    """标题定位只解码用户圈的那几秒，整段命令里不能出现 -t。"""
    short = ss.audio_window_cmd("a.mp4", 0, 15)
    assert "-vn" in short
    assert short[short.index("-t") + 1] == "15.000"
    assert "-ss" not in short
    later = ss.audio_window_cmd("a.mp4", 2, 14)
    assert later[later.index("-ss") + 1] == "2.000"
    assert later[later.index("-t") + 1] == "12.000"
    whole = ss.audio_window_cmd("a.mp4")
    assert "-t" not in whole
    assert "-vn" not in whole
    try:
        ss.audio_window_cmd("a.mp4", 0, 240)
    except RuntimeError:
        return
    raise AssertionError("超过 30 秒的窗口必须拒绝")


if __name__ == "__main__":
    unittest.main(verbosity=2)
