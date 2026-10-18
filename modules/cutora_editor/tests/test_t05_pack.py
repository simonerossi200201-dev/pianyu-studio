# -*- coding: utf-8 -*-
"""T05 pack: catalog is the model list; portable venv rewrite; no silent HF."""
from __future__ import annotations

import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def test_packable_rows_come_from_catalog_not_hardcoded_ids():
    from cutora.asr.models import (
        PACK_FIXTURE_DIRNAMES,
        is_pack_fixture,
        is_venv_bundled,
        packable_model_rows,
    )
    src = open(os.path.join(REPO, "tools", "pack_internal.py"), encoding="utf-8").read()
    assert "packable_model_rows" in src
    assert "pack_model_directories" in src
    rows = packable_model_rows()
    ids = [r["id"] for r in rows]
    assert "asr-fake" not in ids
    assert all(not is_pack_fixture(r) for r in rows)
    assert all(not is_venv_bundled(r) for r in rows)
    dirnames = {str(r.get("dirname") or "") for r in rows}
    assert not (dirnames & PACK_FIXTURE_DIRNAMES)
    assert "asr-small" in ids
    assert "asr-large-v3-turbo" in ids
    assert "align-en" in ids
    assert "align-it" in ids
    assert "align-fr" in ids
    assert "align-es" in ids
    assert "align-de" in ids
    assert "align-pt" in ids
    assert "vad-silero" not in ids
    assert "vad-segmentation" not in ids


def test_required_dirnames_follow_model_paths():
    from cutora.asr.models import required_pack_dirnames
    leaves = required_pack_dirnames()
    assert "asr_small" in leaves
    assert "align_wav2vec2" in leaves
    assert "asr_fake" not in leaves


def test_align_not_bundled_note_is_the_user_facing_string():
    from cutora.asr.models import ALIGN_NOT_BUNDLED_NOTE
    assert ALIGN_NOT_BUNDLED_NOTE == "该语种对齐模型未内置"
    tier = open(os.path.join(REPO, "cutora", "asr", "tier.py"),
                encoding="utf-8").read()
    assert "ALIGN_NOT_BUNDLED_NOTE" in tier
    worker = open(os.path.join(REPO, "cutora", "asr", "align_worker.py"),
                  encoding="utf-8").read()
    assert "choose_word_timing" in worker


def test_missing_align_model_sets_not_bundled_note():
    from cutora.asr.align_worker import AlignmentWorker
    from cutora.asr.engine import has_local_align_model
    from cutora.asr.models import ALIGN_NOT_BUNDLED_NOTE

    src = open(os.path.join(REPO, "cutora", "asr", "align_worker.py"),
               encoding="utf-8").read()
    assert "choose_word_timing" in src
    assert "has_local_align_model(lang)" in src
    if has_local_align_model("fr"):
        pytest.skip("align-fr present on this machine")

    w = AlignmentWorker.__new__(AlignmentWorker)
    w.align_note = ALIGN_NOT_BUNDLED_NOTE
    w.approximate_words = False
    w._is_running = True
    w.video_path = "x"
    calls = {}

    class _Pipe:
        model = None

        def transcribe(self, audio, **kwargs):
            calls["t"] = True
            return {"segments": [{"start": 0, "end": 1, "text": "x"}]}

    w.progress = type("P", (), {"emit": staticmethod(lambda *_a, **_k: None)})()
    w.smart_segmentation = lambda segs, dur: segs
    out = AlignmentWorker._transcribe_without_align(w, _Pipe(), [0], 1.0, "fr")
    assert w.align_note == ALIGN_NOT_BUNDLED_NOTE
    assert w.approximate_words is True
    assert out
    assert calls.get("t") is True


def test_rewrite_pyvenv_cfg_is_idempotent(tmp_path):
    from cutora.core.portable import read_pyvenv_home, rewrite_pyvenv_cfg
    root = tmp_path
    py_dir = root / "runtime" / "python"
    venv = root / "runtime" / "venv"
    py_dir.mkdir(parents=True)
    venv.mkdir(parents=True)
    (py_dir / "python.exe").write_bytes(b"fake")
    cfg = venv / "pyvenv.cfg"
    cfg.write_text(
        "home = C:\\Program Files\\Python310\n"
        "include-system-site-packages = false\n"
        "version = 3.10.11\n",
        encoding="utf-8",
    )
    home = rewrite_pyvenv_cfg(str(root))
    assert os.path.normcase(home) == os.path.normcase(str(py_dir))
    assert os.path.normcase(read_pyvenv_home(str(cfg))) == os.path.normcase(home)
    home2 = rewrite_pyvenv_cfg(str(root))
    assert os.path.normcase(home2) == os.path.normcase(home)
    text = cfg.read_text(encoding="utf-8")
    assert text.count("home =") == 1
    assert "Program Files\\Python310" not in text


def test_cpu_path_when_cuda_hidden():
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "import os\n"
        "assert os.environ.get('CUDA_VISIBLE_DEVICES') == ''\n"
        "from cutora.core.config import detect_gpu_config, DEVICE\n"
        "d, ct, reason = detect_gpu_config(verbose=False)\n"
        "assert d == 'cpu', (d, ct, reason)\n"
        "assert ct == 'float32'\n"
        "print('ok', reason)\n"
    )
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO, capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_in_app_download_ignores_hf_hub_offline(monkeypatch, tmp_path):
    """HF_HUB_OFFLINE blocks whisperX/transformers, not asr.models.download."""
    from cutora.asr import download as D
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    src = open(os.path.join(REPO, "cutora", "asr", "download.py"), encoding="utf-8").read()
    assert "HF_HUB_OFFLINE" not in src
    assert "download_to_file" in src
    called = {}

    def fake(url, dest, **kwargs):
        called["url"] = url
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(b"ok")
        return {"bytes": 2, "resumed": False}

    monkeypatch.setattr(D, "download_to_file", fake)
    dest = tmp_path / "config.json"
    D.download_hf_file(
        "Systran/faster-whisper-small", "config.json", str(dest),
        revision="deadbeef")
    assert called["url"].startswith("https://huggingface.co/")
    assert dest.is_file()


def test_complete_layout_align_plan_names_missing_models():
    from cutora.asr.models import (
        ALIGN_NOT_BUNDLED_NOTE,
        align_plan_for_complete_languages,
        align_is_on_disk,
    )
    plan = align_plan_for_complete_languages()
    by = {p["code"]: p for p in plan}
    assert "en" in by and by["en"]["bundled"] is True
    assert "it" in by and by["it"]["bundled"] is True
    assert by["it"]["align_id"] == "align-it"
    for code in ("fr", "es", "de", "pt"):
        assert code in by
        assert by[code]["bundled"] is True
        assert align_is_on_disk(code)
    assert ALIGN_NOT_BUNDLED_NOTE


def test_pack_refuses_unpinned_extras():
    src = open(os.path.join(REPO, "tools", "pack_internal.py"), encoding="utf-8").read()
    assert "unpinned extra files" in src
    assert "Zero unpinned" in src
    assert "computed-unpinned" in src


def test_pack_excludes_tools_diag():
    src = open(os.path.join(REPO, "tools", "pack_internal.py"), encoding="utf-8").read()
    assert '"_diag"' in src
    assert "_diag leaked" in src
