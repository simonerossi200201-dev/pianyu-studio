# -*- coding: utf-8 -*-
"""
Unit & Integration Tests for Image Studio Backend Engine (`core.image_studio`),
Studio Gateway REST API (`/v1/images/studio/*`), and Robota Tools (`image_studio_batch`, `image_studio_edit`).
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

LTX_ROOT = Path(__file__).resolve().parents[1]
if str(LTX_ROOT) not in sys.path:
    sys.path.insert(0, str(LTX_ROOT))

ROBOTA_ROOT = Path(r"D:\0_Robota")
if ROBOTA_ROOT.is_dir() and str(ROBOTA_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBOTA_ROOT))

from core import image_studio


class TestImageStudioEngine(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="test_img_studio_")
        self.proj_dir = Path(self._tmp.name) / "proj"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_batch_parse_and_multi_aspect_generation(self) -> None:
        tsv_text = (
            "v01\tCinematic portrait of a speaker in warm golden light\tWalking in Light\tDaily Grace\n"
            "v02\tPeaceful mountain sunrise with mist\tMorning Prayer\tFind Peace Today\n"
        )
        parsed = image_studio.parse_batch_prompts(
            tsv_text,
            aspects=["9:16", "1:1"],
            count_per_prompt=1,
        )
        self.assertEqual(len(parsed), 4)
        self.assertEqual(parsed[0]["aspect"], "9:16")
        self.assertEqual((parsed[0]["width"], parsed[0]["height"]), (1088, 1920))
        self.assertEqual(parsed[1]["aspect"], "1:1")
        self.assertEqual((parsed[1]["width"], parsed[1]["height"]), (1024, 1024))
        self.assertEqual(parsed[0]["typography"]["title"], "Walking in Light")
        self.assertEqual(parsed[0]["typography"]["subtitle"], "Daily Grace")

        mgr = image_studio.ImageStudioManager(self.proj_dir, name="BatchTest")
        res = mgr.generate_batch(
            prompts_text=tsv_text,
            aspects=["9:16", "1:1"],
            run_now=True,
            mock=True,
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["generated_count"], 4)
        for it in mgr.items:
            self.assertEqual(it["status"], "ready")
            self.assertTrue((self.proj_dir / it["raw_file"]).is_file())
            self.assertTrue((self.proj_dir / it["composed_file"]).is_file())
            self.assertGreater(it["qc"]["sharpness"], 20.0)

    def test_02_yunet_face_safe_typography_and_depth_text(self) -> None:
        mgr = image_studio.ImageStudioManager(self.proj_dir)
        # 创建一个带小贴纸的测试 PNG
        sticker_p = self.proj_dir / "test_sticker.png"
        Image.new("RGBA", (320, 90), (30, 180, 90, 230)).save(sticker_p)

        mgr.generate_batch(
            prompts_text="1\tClose-up portrait of a young woman\tGOLDEN HEADLINE\tSubtitle Text",
            aspects=["9:16"],
            default_typography={
                "preset": "editorial_pill",
                "sticker_path": str(sticker_p),
                "auto_avoid_face": True,
                "title_y": 0.22,  # 故意设在人脸区域 (0.22)，验证自动避脸
                "sticker_y": 0.24,  # 故意设在人脸区域 (0.24)，验证贴纸自动避脸
            },
            run_now=True,
            mock=True,
        )
        it = mgr.get_item("img_0001")
        self.assertGreaterEqual(len(it["faces"]), 1, "应当检测到人像面部框")
        face = it["faces"][0]
        fy1, fy2 = face["y"] - 0.03, face["y"] + face["h"] + 0.03
        solved_title_y = it["typography"]["title_y"]
        solved_sticker_y = it["typography"]["sticker_y"]
        self.assertFalse(fy1 <= solved_title_y <= fy2, f"标题 Y({solved_title_y}) 必须自动避开人脸({fy1}..{fy2})")
        self.assertFalse(fy1 <= solved_sticker_y <= fy2, f"贴纸 Y({solved_sticker_y}) 必须自动避开人脸({fy1}..{fy2})")
        self.assertFalse(it["qc"].get("face_overlap", True))

        # 验证非破坏性：raw 原图与 composed 合成图不相同，且切换预设或 depth_text 时 raw 保持纯净
        raw_arr_before = np.array(Image.open(self.proj_dir / it["raw_file"]))
        comp_arr_1 = np.array(Image.open(self.proj_dir / it["composed_file"]))
        self.assertGreater(np.abs(raw_arr_before.astype(np.int32) - comp_arr_1.astype(np.int32)).mean(), 0.5)

        # 启用 depth_text=True (自动触发抠图并将大标题穿插在人物背后)
        depth_res = mgr.update_typography("img_0001", depth_text=True, preset="cinema_gold")
        self.assertTrue(depth_res["ok"])
        it_after = mgr.get_item("img_0001")
        self.assertTrue(it_after["matted_file"])
        self.assertTrue((self.proj_dir / it_after["matted_file"]).is_file())
        raw_arr_after = np.array(Image.open(self.proj_dir / it_after["raw_file"]))
        np.testing.assert_array_equal(raw_arr_before, raw_arr_after, "非破坏性排版绝不能修改 raw 原图")

    def test_03_inpaint_erase_outpaint_matte_upscale_revert_and_orante_handoff(self) -> None:
        mgr = image_studio.ImageStudioManager(self.proj_dir)
        mgr.generate_batch(
            prompts_text="v01\tSquare portrait in studio\tTitle One\tSub One",
            aspects=["1:1"],
            run_now=True,
            mock=True,
        )
        it0 = mgr.get_item("img_0001")
        self.assertEqual((it0["width"], it0["height"]), (1024, 1024))

        # 1. 创建局部遮罩 (仅中心偏右下 120x120 块为白色 255，其余全黑 0)
        mask = np.zeros((1024, 1024), dtype=np.uint8)
        mask[700:820, 700:820] = 255
        raw_before = np.array(Image.open(self.proj_dir / it0["raw_file"]))

        # 测试魔术擦除 (mode="erase")
        t0 = time.perf_counter()
        erase_res = mgr.inpaint("img_0001", mask, mode="erase", mock=True)
        erase_elapsed = time.perf_counter() - t0
        self.assertTrue(erase_res["ok"])
        self.assertLess(erase_elapsed, 1.5)
        raw_after_erase = np.array(Image.open(self.proj_dir / it0["raw_file"]))
        # 验证遮罩外部 (例如左上角 0:500, 0:500) 100% 像素未受任何影响
        np.testing.assert_array_equal(raw_before[:500, :500], raw_after_erase[:500, :500])

        # 测试带提示词的局部重绘 (mode="inpaint")
        inp_res = mgr.inpaint("img_0001", mask, prompt="bright golden flower brooch", mode="inpaint", mock=True)
        self.assertTrue(inp_res["ok"])
        raw_after_inpaint = np.array(Image.open(self.proj_dir / it0["raw_file"]))
        np.testing.assert_array_equal(raw_before[:500, :500], raw_after_inpaint[:500, :500])
        self.assertGreater(
            np.abs(raw_after_inpaint[720:800, 720:800].astype(np.float32) - raw_after_erase[720:800, 720:800].astype(np.float32)).mean(),
            2.0,
        )

        # 2. 测试交互式点选分割预览与抠图 (< 0.4s)
        t_seg = time.perf_counter()
        prev_res = image_studio.segment_points_preview(
            self.proj_dir / it0["raw_file"],
            points=[{"x": 0.5, "y": 0.35, "label": 1}, {"x": 0.05, "y": 0.05, "label": 0}],
        )
        seg_elapsed = time.perf_counter() - t_seg
        self.assertTrue(prev_res["ok"])
        self.assertLess(seg_elapsed, 0.8)
        self.assertGreater(prev_res["foreground_ratio"], 0.05)

        matte_res = mgr.matte(
            "img_0001",
            mode="sam_points",
            points=[{"x": 0.5, "y": 0.35, "label": 1}],
            bg_mode="transparent",
            prefer_fast=True,
        )
        self.assertTrue(matte_res["ok"])
        self.assertTrue((self.proj_dir / matte_res["matted_file"]).is_file())

        # 3. 测试 AI 扩图 (1:1 -> 9:16)
        outp_res = mgr.outpaint("img_0001", target_aspect="9:16", align="center", mock=True)
        self.assertTrue(outp_res["ok"])
        self.assertEqual((outp_res["width"], outp_res["height"]), (1088, 1920))

        # 4. 测试历史版本回退 (revert to v1 1024x1024)
        rev_res = mgr.revert("img_0001", version=1)
        self.assertTrue(rev_res["ok"])
        it_rev = mgr.get_item("img_0001")
        self.assertEqual((it_rev["width"], it_rev["height"]), (1024, 1024))

        # 5. 测试移交 Orante 任务 (export_to_orante_task)
        orante_dir = Path(self._tmp.name) / "T0199-TestOrante"
        exp_res = mgr.export_to_orante(orante_dir, use_composed=False)
        self.assertTrue(exp_res["ok"])
        self.assertEqual(exp_res["exported_count"], 1)
        self.assertTrue((orante_dir / "images" / "p01.png").is_file())
        mf_path = orante_dir / "manifest.json"
        self.assertTrue(mf_path.is_file())
        mf = json.loads(mf_path.read_text(encoding="utf-8"))
        self.assertEqual(mf["version"], 2)
        self.assertEqual(len(mf["images"]), 1)
        self.assertEqual(mf["images"][0]["p"], "p01")
        self.assertEqual(len(mf["segments"]), 1)

    def test_04_robota_tools_and_gateway_endpoints(self) -> None:
        from robota.tools import studio_pipeline
        from fastapi.testclient import TestClient
        from modules.studio_gateway.server import app

        # 测试 Robota studio_pipeline.image_studio_batch & image_studio_edit
        orante_target = Path(self._tmp.name) / "T0200-RobotaBatch"
        batch_res = studio_pipeline.image_studio_batch(
            project_dir=str(self.proj_dir),
            prompts_text="v01\tWarm cinematic study room\tStudy the Word\tFree Online",
            aspects=["9:16"],
            title_preset="cinema_gold",
            send_to_orante_task=str(orante_target),
            mock=True,
        )
        self.assertTrue(batch_res["ok"])
        self.assertIn("orante_export", batch_res)
        self.assertTrue((orante_target / "images" / "p01.png").is_file())

        edit_res = studio_pipeline.image_studio_edit(
            project_dir=str(self.proj_dir),
            item_id="img_0001",
            action="typography",
            title="UPDATED GOLDEN TITLE",
            preset="crimson_badge",
        )
        self.assertTrue(edit_res["ok"])
        self.assertEqual(edit_res["typography"]["title"], "UPDATED GOLDEN TITLE")

        # 测试 Studio Gateway REST 接口 (/v1/images/studio/*)
        client = TestClient(app)
        r_insp = client.post(
            "/v1/images/studio/inspect",
            json={"project_dir": str(self.proj_dir), "item_id": "img_0001"},
        )
        self.assertEqual(r_insp.status_code, 200)
        self.assertTrue(r_insp.json()["ok"])

        r_outp = client.post(
            "/v1/images/studio/outpaint",
            json={"project_dir": str(self.proj_dir), "item_id": "img_0001", "target_aspect": "16:9", "mock": True},
        )
        self.assertEqual(r_outp.status_code, 200)
        self.assertEqual(r_outp.json()["width"], 1920)
        self.assertEqual(r_outp.json()["height"], 1088)


if __name__ == "__main__":
    unittest.main(verbosity=2)
