"""Nov 1 Orante Backend Upgrade Tests (Requirements 1, 3, 4, 5, 6 & macOS Compatibility)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate ORANTE_DATA and ORANTE_WORKSPACE into tmp_path for every test."""
    data_dir = tmp_path / "orante_data"
    ws_dir = tmp_path / "orante_ws"
    data_dir.mkdir(parents=True, exist_ok=True)
    ws_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORANTE_DATA", str(data_dir))
    monkeypatch.setenv("ORANTE_WORKSPACE", str(ws_dir))

    from orante import config, db, paths, tasks
    paths.reset()
    db.init()
    config.set("workspace", str(ws_dir))
    tasks.clear_counts_cache()
    yield {"data": data_dir, "ws": ws_dir, "tmp": tmp_path}


# ==============================================================================
# Test Suite 1: crypto_vault & multi_accounts
# ==============================================================================

def test_crypto_vault_encrypt_decrypt_and_totp(isolated_env):
    from orante import crypto_vault

    # 1. Encrypt & decrypt round-trip
    secret_plain = "MySuperSecretPassword!@#2026"
    enc = crypto_vault.encrypt_secret(secret_plain)
    assert enc.startswith("v1:")
    assert secret_plain not in enc
    dec = crypto_vault.decrypt_secret(enc)
    assert dec == secret_plain

    # Idempotent encrypt on already-encrypted token
    assert crypto_vault.encrypt_secret(enc) == enc
    assert crypto_vault.decrypt_secret("") == ""

    # 2. RFC 6238 / standard base32 TOTP verification
    # Base32 encoding of "12345678901234567890" is "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    b32_secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    # At timestamp 59 (counter = 1), compute expected HOTP(key, 1)
    key = base64.b32decode(b32_secret)
    digest = hmac.new(key, struct.pack(">Q", 1), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    expected_code = f"{((struct.unpack('>I', digest[offset:offset+4])[0] & 0x7FFFFFFF) % 1000000):06d}"

    res = crypto_vault.generate_totp(b32_secret, ts=59)
    assert res["code"] == expected_code
    assert len(res["code"]) == 6
    assert res["remaining_seconds"] == 1

    # Also test spaced lowercase secret format from hedra-config-backup
    spaced = "5764 c27i x4ca c2ce x7ee r32y s32x xj37"
    res2 = crypto_vault.generate_totp(spaced, ts=1700000000)
    assert len(res2["code"]) == 6
    assert res2["code"].isdigit()
    assert 1 <= res2["remaining_seconds"] <= 30


def test_hedra_config_import_export_roundtrip(isolated_env):
    from orante import multi_accounts

    sample_backup = {
        "version": "1.0",
        "exportTime": "2026-04-16T08:29:18.000Z",
        "data": {
            "accounts": [
                "user1@example.com\tPassUser1!\t5764 c27i x4ca c2ce x7ee r32y s32x xj37\tbackup1@example.com",
                "user2@example.com\tPassUser2!\tjbsw y3dp ehpk 3pxp\tbackup2@example.com",
            ],
            "accountMeta": {
                "user1@example.com": {"group": "G1", "favorite": True},
                "user2@example.com": {"group": "G1", "favorite": False},
            },
            "localStorage": {
                "__GROUP_TAGS__": json.dumps({"user1@example.com": "G1", "user2@example.com": "G1"}),
                "__GROUP_TAGS_DETAIL__": json.dumps([
                    {"id": "G1", "name": "Group Alpha", "color": "#0969da", "createdAt": 1700000000000}
                ]),
            },
        },
    }

    imp = multi_accounts.import_hedra_config(sample_backup, overwrite=True)
    assert imp["imported_accounts"] == 2
    assert imp["imported_groups"] == 1

    # Verify list_accounts masks secrets by default
    listed = multi_accounts.list_accounts(include_secrets=False)
    assert len(listed) == 2
    assert listed[0]["has_password"] is True
    assert listed[0]["has_totp"] is True
    assert "password" not in listed[0]

    # Verify reveal_account_secrets decrypts properly
    sec1 = multi_accounts.reveal_account_secrets("user1@example.com")
    assert sec1["password"] == "PassUser1!"
    assert sec1["totp_secret"] == "5764 c27i x4ca c2ce x7ee r32y s32x xj37"

    # Verify export_hedra_config round-trips losslessly
    exported = multi_accounts.export_hedra_config()
    exp_data = exported["data"]
    assert len(exp_data["accounts"]) == 2
    assert exp_data["accounts"][0] == "user1@example.com\tPassUser1!\t5764 c27i x4ca c2ce x7ee r32y s32x xj37\tbackup1@example.com"
    assert exp_data["accountMeta"]["user1@example.com"]["group"] == "G1"
    assert exp_data["accountMeta"]["user1@example.com"]["favorite"] is True

    # If real hedra-config-backup file exists on disk, test importing it too
    real_backup = Path(r"D:\0_LTX\hedra-config-backup-2026-04-16T08-29-18.json")
    if real_backup.is_file():
        raw = json.loads(real_backup.read_text(encoding="utf-8"))
        imp_real = multi_accounts.import_hedra_config(raw, overwrite=True)
        expected_n = len((raw.get("data") or raw).get("accounts", []))
        assert imp_real["imported_accounts"] == expected_n


def test_clean_profile_caches_preserves_cookies_and_storage(isolated_env):
    from orante import multi_accounts

    prof = isolated_env["tmp"] / "chrome_profile_M1"
    # Create cache dirs to be cleaned
    (prof / "Default" / "Cache").mkdir(parents=True)
    (prof / "Default" / "Cache" / "data_0").write_bytes(b"x" * 2048)
    (prof / "Default" / "Code Cache" / "js").mkdir(parents=True)
    (prof / "Default" / "Code Cache" / "js" / "index").write_bytes(b"y" * 1024)
    (prof / "ShaderCache").mkdir(parents=True)
    (prof / "ShaderCache" / "shader.bin").write_bytes(b"z" * 512)

    # Create critical login/session files that MUST be preserved
    cookies_file = prof / "Default" / "Network" / "Cookies"
    cookies_file.parent.mkdir(parents=True)
    cookies_file.write_text("GOOGLE_AUTH_COOKIE=1", encoding="utf-8")

    local_storage = prof / "Default" / "Local Storage" / "leveldb" / "000001.log"
    local_storage.parent.mkdir(parents=True)
    local_storage.write_text("FLOW_SESSION_STATE=ok", encoding="utf-8")

    login_data = prof / "Default" / "Login Data"
    login_data.write_text("SAVED_CREDENTIALS", encoding="utf-8")

    res = multi_accounts.clean_profile_caches(prof)
    assert res["freed_bytes"] >= 3584

    # Caches deleted
    assert not (prof / "Default" / "Cache").exists()
    assert not (prof / "Default" / "Code Cache").exists()
    assert not (prof / "ShaderCache").exists()

    # Cookies, Local Storage, Login Data untouched
    assert cookies_file.is_file()
    assert cookies_file.read_text(encoding="utf-8") == "GOOGLE_AUTH_COOKIE=1"
    assert local_storage.is_file()
    assert login_data.is_file()


def test_multi_account_group_runner_rotation_and_exhaustion(isolated_env):
    from orante import events, multi_accounts

    # Create 4 accounts:
    # M1: 0 credits (should be skipped initially)
    # M2: 20 credits (will drop to 0 after 1st submission)
    # M3: 50 credits (will drop to 0 after 2nd submission)
    # M4: 50 credits (will be rotated in when M2 closes, and drop to 0)
    for idx, (aid, creds) in enumerate([("M1", 0), ("M2", 20), ("M3", 50), ("M4", 50)], start=1):
        multi_accounts.create_account({
            "id": aid,
            "name": aid,
            "idx": idx,
            "login_account": f"{aid.lower()}@example.com",
            "credits": creds,
            "group_id": "G_ROT",
        })
    multi_accounts.save_group({
        "id": "G_ROT",
        "name": "Rotation Group",
        "account_ids": ["M1", "M2", "M3", "M4"],
    })

    opened = []
    closed = []

    def fake_open(aid, auto_login=True, probe_credits=True, enforce_cap=True):
        opened.append(aid)
        acc = multi_accounts.get_account(aid)
        return {"ok": True, "account": acc, "credits": acc["flow_credits"]}

    def fake_close(aid, clean_cache=True, auto_clean=True):
        closed.append((aid, bool(clean_cache)))
        return {"ok": True, "account_id": aid}

    emitted_events = []
    orig_emit = events.emit

    def spy_emit(event_type, **payload):
        emitted_events.append((event_type, payload))
        return orig_emit(event_type, **payload)

    with patch.object(multi_accounts, "open_multi_account_flow", side_effect=fake_open), \
         patch.object(multi_accounts, "close_multi_account_flow", side_effect=fake_close), \
         patch.object(events, "emit", side_effect=spy_emit):

        runner = multi_accounts.MultiAccountGroupRunner("G_ROT", max_concurrent=2)

        # Acquire 1st account: M1 (0 credits) must be skipped; M2 and M3 opened (up to max_concurrent=2)
        a1 = runner.acquire_account()
        assert a1 is not None
        assert a1["id"] == "M2"
        assert "M1" not in runner.active_ids
        assert runner.active_ids == ["M2", "M3"]

        # Simulate M2 exhausting its credits to 0 -> closes & cleans M2, rotates M4 in
        runner.report_credits("M2", 0)
        assert ("M2", True) in closed
        assert "M2" not in runner.active_ids
        assert "M4" in runner.active_ids

        # Next acquire gets M3 or M4
        a2 = runner.acquire_account()
        assert a2["id"] in ("M3", "M4")

        # Exhaust both M3 and M4
        runner.report_credits("M3", 0)
        runner.report_credits("M4", 0)

        # Now all 4 accounts are exhausted -> acquire_account returns None and emits group.quota_exhausted
        a_none = runner.acquire_account()
        assert a_none is None
        exhausted_evts = [e for e in emitted_events if e[0] == "group.quota_exhausted"]
        assert len(exhausted_evts) >= 1
        assert exhausted_evts[-1][1]["group_id"] == "G_ROT"


# ==============================================================================
# Test Suite 2: prompt_presets & Maximum-Newline-Gap Insertion
# ==============================================================================

def test_prompt_presets_seeded_and_max_newline_insertion(isolated_env):
    from orante.services import prompt_presets

    info_all = prompt_presets.list_presets()
    presets = info_all["presets"]
    sheet_presets = [p for p in presets if p["role"] == "main"]
    orante_presets = [p for p in presets if p["role"] != "main"]

    # All 33 valid Google Sheet presets + 7 Orante presets = 40 seeded
    assert len(sheet_presets) == 33
    assert len(orante_presets) >= 7

    # Case A: Template with \n\n after line 1 and \n\n\n after line 2 -> speech inserted in \n\n\n gap
    tpl_a = "First block camera setup.\n\nSecond block character action.\n\n\nThird block ending rule."
    out_a = prompt_presets.insert_speech_at_max_newlines(tpl_a, "Hello world speech")
    assert out_a == "First block camera setup.\n\nSecond block character action.\nHello world speech\nThird block ending rule."

    # Case B: Template with only \n\n -> speech inserted in \n\n gap
    tpl_b = "Top visual prompt.\n\nBottom constraint prompt."
    out_b = prompt_presets.insert_speech_at_max_newlines(tpl_b, "My spoken line")
    assert out_b == "Top visual prompt.\nMy spoken line\nBottom constraint prompt."

    # Case C: Single-block template -> speech appended cleanly
    tpl_c = "Single line prompt without newline gaps."
    out_c = prompt_presets.insert_speech_at_max_newlines(tpl_c, "Appended line")
    assert out_c == "Single line prompt without newline gaps.\nAppended line"

    # Inspect template metadata
    info = prompt_presets.inspect_preset_template("Test Preset", tpl_a, sample_speech="SAMPLE_SPEECH")
    assert info["max_newlines"] == 3
    assert "Second block character action.\nSAMPLE_SPEECH\nThird block ending rule." in info["preview_with_speech"]


def test_prompts_priority_and_batch_apply_preset_with_gemini_dedup(isolated_env):
    from orante import accounts, models, tasks
    from orante.services import arrange, prompts

    acc = accounts.create("FlowAcc1", "flow", "https://labs.google/fx/tools/flow/project/p1", "Omni 1.1 Flash")
    ws = isolated_env["ws"]
    img_dir = ws / "src_imgs"
    img_dir.mkdir(parents=True, exist_ok=True)
    # Create two dummy images
    (img_dir / "01.jpg").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9")
    (img_dir / "02.jpg").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9")

    tasks.upsert_rows([{
        "id": "1",
        "account": acc["name"],
        "copy": "First segment line. Second segment line.",
        "image_dir": str(img_dir),
    }])
    tdir = isolated_env["ws"] / "tasks" / "1"
    tdir.mkdir(parents=True, exist_ok=True)
    tasks.set_dir("1", tdir)

    # Copy images into task dir and build manifest where Seg 1 and Seg 2 share P01
    (tdir / "images").mkdir(exist_ok=True)
    (tdir / "images" / "P01.jpg").write_bytes((img_dir / "01.jpg").read_bytes())
    m = models.Manifest.new("1", "Task 1", "flow", acc["name"], "long", "Line One. Line Two.")
    m.images = [{"p": "P01", "file": "images/P01.jpg", "desc": "Image P01 description", "role": "主体"}]
    m.segments = [
        {"s": 1, "duration": 8, "text": "Line One", "image": "P01", "role": "主体", "status": "planned"},
        {"s": 2, "duration": 8, "text": "Line Two", "image": "P01", "role": "主体", "status": "planned"},
    ]
    m.save(tdir)

    # Test Priority Resolution:
    # Priority 3: Image desc fallback
    p3 = prompts.build_final_prompt(m, m.seg(1), task=tasks.get("1"))
    assert "Image P01 description" in p3
    assert "Line One" in p3

    # Priority 2: Task-level preset_id overrides Image desc
    tasks.set_preset_id("1", "PR_SHEET_01")
    p2 = prompts.build_final_prompt(m, m.seg(1), task=tasks.get("1"))
    assert "Line One" in p2
    assert "Image P01 description" not in p2

    # Priority 1: Segment-level custom_prompt overrides Task preset_id
    m.seg(1)["custom_prompt"] = "Custom Top Block\n\n\nCustom Bottom Block"
    p1 = prompts.build_final_prompt(m, m.seg(1), task=tasks.get("1"))
    assert "Custom Top Block\nLine One\nCustom Bottom Block" in p1

    # Test batch_apply_preset with gemini_template deduplicating (task_id, image_p)
    gemini_calls = []

    def fake_ai_describer(tid, img_p, tpl_text):
        gemini_calls.append((tid, img_p, tpl_text))
        return "Gemini Visual Top\n\n\nGemini Visual Bottom"

    res = arrange.batch_apply_preset(
        targets=[{"task_id": "1", "s": 1}, {"task_id": "1", "s": 2}],
        mode="gemini_template",
        gemini_template_id="PR_GEM_01",
        flow_mode="ingredients",
        ai_describer=fake_ai_describer,
    )
    assert res["updated_segments"] == 2
    # Both segments share P01 -> Gemini called ONLY ONCE!
    assert res["gemini_calls"] == 1
    assert len(gemini_calls) == 1

    m_reloaded = models.Manifest.load(tdir)
    assert m_reloaded.seg(1)["flow_mode"] == "ingredients"
    assert "Line One" in m_reloaded.seg(1)["prompt_final"]
    assert "Line Two" in m_reloaded.seg(2)["prompt_final"]


# ==============================================================================
# Test Suite 3: Flow Dual Modes (frames vs ingredients) & Omni 1.1 Flash + 10s
# ==============================================================================

def test_flow_dual_modes_and_omni_10s(isolated_env):
    from orante import accounts, caps, tasks
    from orante.engines import flow_api

    # 1. Omni 1.1 Flash & 10s duration support
    assert 10 in caps.DURATIONS["flow"]
    assert "Omni 1.1 Flash" in flow_api.FLOW_FREE_VIDEO_NAMES
    assert flow_api.model_id_for("Omni 1.1 Flash", 10) == "omni_1_1_flash_10s"
    assert flow_api.model_id_for("Omni 1.1 Flash", 8) == "omni_1_1_flash"
    assert flow_api.model_id_for("Veo 3.1 - Fast", 8) == "veo_3_1_i2v_fast"

    # 2. Flow mode priority resolution: Segment > Task > Account
    acc = accounts.create(
        "FlowAccFrames",
        "flow",
        "https://labs.google/fx/tools/flow/project/11111111-2222-3333-4444-555555555555",
        "Omni 1.1 Flash",
        flow_mode="frames",
    )
    assert acc["flow_mode"] == "frames"

    tasks.upsert_rows([{"id": "10", "account": acc["name"], "copy": "Test", "image_dir": str(isolated_env["ws"])}])
    t = tasks.get("10")
    assert t["flow_mode"] == "auto"

    tasks.set_flow_mode("10", "ingredients")
    assert tasks.get("10")["flow_mode"] == "ingredients"

    # 3. Test FlowEngine._ui_set_mode with mocked Playwright page
    eng = flow_api.FlowEngine(acc, print)
    mock_page = MagicMock()
    radio_calls = []

    def fake_ui_radio(page, *names, fallback_pos=None):
        elems = names[0] if (len(names) == 1 and isinstance(names[0], (tuple, list))) else names
        radio_calls.append((tuple(elems), fallback_pos))
        return True

    with patch.object(eng, "_ui_radio", side_effect=fake_ui_radio):
        eng._ui_set_mode(mock_page, "9:16", "frames")
        assert (flow_api.FRAMES_RADIO_NAMES, 0) in radio_calls

        radio_calls.clear()
        eng._ui_set_mode(mock_page, "9:16", "ingredients")
        assert (flow_api.INGREDIENTS_RADIO_NAMES, 1) in radio_calls


# ==============================================================================
# Test Suite 4: player_mode & Clean Finished Task Archiving
# ==============================================================================

def test_player_mode_import_isolation_and_finished_archiving(isolated_env):
    from orante import tasks
    from orante.services import player_mode, project

    # Create 2 external folders with dummy .mp4 files
    ext_root = isolated_env["tmp"] / "external_videos"
    f1 = ext_root / "Folder_Alpha"
    f2 = ext_root / "Folder_Beta"
    f1.mkdir(parents=True)
    f2.mkdir(parents=True)
    (f1 / "clip_01.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")
    (f1 / "clip_02.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")
    (f2 / "scene_a.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")

    res = player_mode.import_folders([str(f1), str(f2)])
    assert res["count"] == 2
    ext_tids = [t["id"] for t in res["imported"]]

    for tid in ext_tids:
        t = tasks.get(tid)
        assert t["source_mode"] == "external_player"
        assert t["player_saved"] is False

    # Normal mode tasks.list_all() MUST NOT return external_player tasks
    normal_list = tasks.list_all(include_finished=True, player_mode=False)
    assert all(t["id"] not in ext_tids for t in normal_list)

    # Player mode tasks.list_all(player_mode=True) MUST return them
    player_list = tasks.list_all(player_mode=True)
    assert {t["id"] for t in player_list} == set(ext_tids)

    # Save first external folder as normal Orante project
    saved_res = player_mode.save_as_project([ext_tids[0]])
    assert saved_res["count"] == 1
    assert (f1 / "project.orproj.json").is_file()

    # Now ext_tids[0] is visible in normal mode
    normal_after_save = tasks.list_all(include_finished=True, player_mode=False)
    assert any(t["id"] == ext_tids[0] for t in normal_after_save)

    # Test finished task auto-archiving for a normal task
    ws_task_dir = isolated_env["ws"] / "tasks" / "99"
    ws_task_dir.mkdir(parents=True)
    tasks.upsert_rows([{"id": "99", "copy": "Normal task copy", "image_dir": str(ws_task_dir)}])
    tasks.set_dir("99", ws_task_dir)
    from orante.models import Manifest
    m99 = Manifest.new("99", "Task 99", "flow", "", "long", "Normal task copy")
    m99.segments = [{"s": 1, "duration": 8, "text": "Hi", "role": "主体", "status": "planned"}]
    m99.save(ws_task_dir)

    # Before finishing: visible in active tasks.list_all()
    active_before = tasks.list_all(include_finished=False)
    assert any(t["id"] == "99" for t in active_before)

    # Transition to finished -> automatically writes <task_dir>/project.orproj.json and hides from active list
    tasks.set_status("99", "finished", force=True)
    orproj_path = ws_task_dir / "project.orproj.json"
    assert orproj_path.is_file()

    active_after = tasks.list_all(include_finished=False)
    assert all(t["id"] != "99" for t in active_after)

    # Re-importing the in-place project.orproj.json after hard delete restores it cleanly
    tasks.delete("99", hard=True)
    imp_res = project.import_file(orproj_path)
    assert imp_res["imported"] == 1
    assert tasks.get("99")["id"] == "99"


# ==============================================================================
# Test Suite 5: FastAPI Endpoints Integration Test
# ==============================================================================

def test_fastapi_endpoints_integration(isolated_env):
    from fastapi.testclient import TestClient
    from orante import server

    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    # Obtain local session cookie via GET /
    r_home = client.get("/")
    assert r_home.status_code == 200

    # 1. /api/prompt-presets
    r = client.get("/api/prompt-presets")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert len(data["presets"]) >= 40

    # 2. /api/prompt-presets/inspect
    r = client.post("/api/prompt-presets/inspect", json={
        "name": "Test",
        "template": "Block 1\n\n\nBlock 2",
        "sample_speech": "My Speech",
    })
    assert r.status_code == 200
    assert r.json()["max_newlines"] == 3

    # 3. /api/multi-accounts/import-tsv
    r = client.post("/api/multi-accounts/import-tsv", json={
        "text": "acc1@gmail.com\tPass123\tJBSWY3DPEHPK3PXP\thttps://labs.google/fx/it/tools/flow\n"
                "acc2@gmail.com\tPass456\tJBSWY3DPEHPK3PXP\thttps://labs.google/fx/it/tools/flow",
        "flow_mode": "ingredients",
    })
    assert r.status_code == 200
    assert r.json()["count"] == 2

    # 4. /api/multi-accounts/{aid}/totp
    r = client.get("/api/multi-accounts/acc1@gmail.com/totp")
    assert r.status_code == 200
    assert len(r.json()["code"]) == 6

    # 5. /api/tasks/all-segments-table
    r = client.get("/api/tasks/all-segments-table")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "tasks" in r.json()
    assert "presets" in r.json()
