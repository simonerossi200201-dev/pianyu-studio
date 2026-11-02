"""Nov 2 Orante Upgrade Tests: Professional History Undo/Redo & Dynamic Speech Variable Slot Preservation."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


class TestNov2HistoryAndVariableSlots(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp())
        self.data_dir = self.tmp_path / "orante_data"
        self.ws_dir = self.tmp_path / "orante_ws"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ws_dir.mkdir(parents=True, exist_ok=True)
        os.environ["ORANTE_DATA"] = str(self.data_dir)
        os.environ["ORANTE_WORKSPACE"] = str(self.ws_dir)

        from orante import config, db, paths, tasks
        from orante.services import history
        paths.reset()
        db.init()
        config.set("workspace", str(self.ws_dir))
        tasks.clear_counts_cache()
        history.clear_history()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_path, ignore_errors=True)

    def _create_sample_task(self, tid: str = "T0199"):
        from orante import db, models
        d = self.ws_dir / tid
        (d / "images").mkdir(parents=True, exist_ok=True)
        (d / "images" / "p01.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (d / "images" / "p07.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        m = models.Manifest.new(tid, "1101-v01", "flow", "acc1", "short", "Sample copy")
        m.images = [
            {"p": "p01", "file": "images/p01.png", "role": "主体", "status": "ok"},
            {"p": "p07", "file": "images/p07.png", "role": "主体", "status": "ok"},
        ]
        m.segments = [
            {
                "s": 1,
                "status": "planned",
                "image": "p01",
                "duration": 8,
                "text": "IS JESUS THE SON OF GOD, OR IS HE GOD HIMSELF?",
                "preset_id": "preset_speech_en",
                "custom_prompt": "",
            },
            {
                "s": 4,
                "status": "planned",
                "image": "p07",
                "duration": 8,
                "text": "the Bible emphasizes one thing from beginning to end: There is only one God. Deuteronomy 6:4:",
                "preset_id": "",
                "custom_prompt": "保持原素材人物、服装、舞台和人群不变。人物富有强烈感情,铿锵有力的用英语说:\nThe One who became flesh was Jesus Christ. In other words,\n人物始终保持激情、震撼、有气势、充满力量的宣告状态。",
            },
            {
                "s": 12,
                "status": "planned",
                "image": "p07",
                "duration": 8,
                "text": "The One who became flesh was Jesus Christ. In other words,",
                "preset_id": "",
                "custom_prompt": "保持原素材人物、服装、舞台和人群不变。人物富有强烈感情,铿锵有力的用英语说:\nThe One who became flesh was Jesus Christ. In other words,\n人物始终保持激情、震撼、有气势、充满力量的宣告状态。",
            },
            {
                "s": 24,
                "status": "planned",
                "image": "p07",
                "duration": 8,
                "text": "then when people worshiped Him, He should have immediately stopped them.",
                "preset_id": "",
                "custom_prompt": "保持原素材人物、服装、舞台和人群不变。人物富有强烈感情,铿锵有力的用英语说:\nThe One who became flesh was Jesus Christ. In other words,\n人物始终保持激情、震撼、有气势、充满力量的宣告状态。",
            },
        ]
        m.track = {"pool": ["p01", "p07"], "pins": {}, "zones": []}
        m.save(d)
        with db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO tasks (id, name, dir, status, engine, flow_mode, preset_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tid, "1101-v01", str(d), "arranged", "flow", "ingredients", "preset_speech_en"),
            )
        return d

    def test_strip_baked_speech_and_preserve_variable_slot(self):
        from orante.services import arrange, prompt_presets

        prompt_presets.save_preset({
            "id": "preset_speech_en",
            "name": "演讲通用-有感情",
            "category": "默认分组",
            "role": "main",
            "template": "保持原素材人物、服装、舞台和人群不变。人物全程紧握麦克风，人物富有强烈感情,铿锵有力的用英语说:\n\n\n人物始终保持激情、震撼、有气势、充满力量的宣告状态。",
        })
        self._create_sample_task("T0199")

        # 1. Verify get_all_tasks_segments_table automatically strips baked speech and keeps the variable slot (\n\n\n)
        res = arrange.get_all_tasks_segments_table()
        t_row = next(t for t in res["tasks"] if t["id"] == "T0199")
        segs = {sg["s"]: sg for sg in t_row["segments"]}

        # s001 uses preset -> prompt_preview MUST be pure template with \n\n\n variable slot, NOT baked with s001's text!
        self.assertIn("\n\n\n", segs[1]["prompt_preview"])
        self.assertNotIn("IS JESUS THE SON OF GOD", segs[1]["prompt_preview"])
        # Meanwhile prompt_final (used only for 4 生成 Flow & 5 收片) DOES merge s001's own text
        self.assertIn("IS JESUS THE SON OF GOD, OR IS HE GOD HIMSELF?", segs[1]["prompt_final"])

        # s004, s012, s024 had s012's English line baked into custom_prompt -> now sanitized back to \n\n\n variable slot!
        for s_num, own_text in [
            (4, "the Bible emphasizes one thing from beginning to end: There is only one God. Deuteronomy 6:4:"),
            (12, "The One who became flesh was Jesus Christ. In other words,"),
            (24, "then when people worshiped Him, He should have immediately stopped them."),
        ]:
            self.assertIn("\n\n\n", segs[s_num]["custom_prompt"])
            self.assertNotIn("The One who became flesh", segs[s_num]["custom_prompt"])
            self.assertIn("\n\n\n", segs[s_num]["prompt_preview"])
            # And each segment's prompt_final has its OWN speech merged!
            self.assertIn(own_text, segs[s_num]["prompt_final"])

    def test_history_undo_redo_across_segments_and_presets(self):
        from orante.services import arrange, history, prompt_presets

        prompt_presets.save_preset({
            "id": "preset_speech_en",
            "name": "演讲通用-有感情",
            "category": "默认分组",
            "role": "main",
            "template": "保持原素材人物不变，铿锵有力地说:\n\n\n保持眼神坚定。",
        })
        self._create_sample_task("T0199")
        # Sanitize initial state and clear history before user actions
        arrange.get_all_tasks_segments_table()
        history.clear_history()

        # Action 1: User batch-updates s001 custom_prompt in 2 排镜头
        new_tpl = "全新镜头运镜前缀:\n\n\n全新镜头运镜后缀。"
        arrange.batch_update_segments(
            [{"task_id": "T0199", "s": 1, "preset_id": "", "custom_prompt": new_tpl}],
            action_label="修改 s001 提示词覆盖",
        )
        st1 = history.get_status()
        self.assertTrue(st1["can_undo"])
        self.assertEqual(st1["undo_count"], 1)

        # Verify s001 was updated
        tr_after = arrange.get_all_tasks_segments_table()
        s1_after = next(sg for sg in tr_after["tasks"][0]["segments"] if sg["s"] == 1)
        self.assertEqual(s1_after["custom_prompt"], new_tpl)
        self.assertEqual(s1_after["preset_id"], "")

        # Undo Action 1 -> s001 should revert to preset_speech_en and empty custom_prompt!
        undo_res = history.undo()
        self.assertTrue(undo_res["applied"])
        self.assertTrue(undo_res["history"]["can_redo"])

        tr_undone = arrange.get_all_tasks_segments_table()
        s1_undone = next(sg for sg in tr_undone["tasks"][0]["segments"] if sg["s"] == 1)
        self.assertEqual(s1_undone["preset_id"], "preset_speech_en")
        self.assertEqual(s1_undone["custom_prompt"], "")

        # Redo Action 1 -> s001 should have new_tpl again!
        redo_res = history.redo()
        self.assertTrue(redo_res["applied"])
        tr_redone = arrange.get_all_tasks_segments_table()
        s1_redone = next(sg for sg in tr_redone["tasks"][0]["segments"] if sg["s"] == 1)
        self.assertEqual(s1_redone["custom_prompt"], new_tpl)
        self.assertEqual(s1_redone["preset_id"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
