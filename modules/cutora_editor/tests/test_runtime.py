# -*- coding: utf-8 -*-
"""init_torch_runtime: shared by app.main and tests; render child must skip torch."""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def test_init_torch_runtime_render_child_does_not_import_torch():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["CUTORA_RENDER_CHILD"] = "1"
    code = (
        "import sys\n"
        "assert 'torch' not in sys.modules\n"
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=True)\n"
        "assert 'torch' not in sys.modules, sorted(sys.modules)\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")


def test_init_torch_runtime_sets_offline_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "import os\n"
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "assert os.environ.get('TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD') == '1'\n"
        "assert os.environ.get('HF_HUB_OFFLINE') == '1'\n"
        "assert os.environ.get('TRANSFORMERS_OFFLINE') == '1'\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert proc.stdout.strip().endswith("ok")


def test_app_and_asr_smoke_call_shared_init():
    """产品与测试必须调同一个函数；测试不得再手写 CUDA 预热。"""
    app_py = os.path.join(REPO, "cutora", "app.py")
    smoke_py = os.path.join(REPO, "tests", "test_asr_smoke.py")
    runtime_py = os.path.join(REPO, "cutora", "core", "runtime.py")
    with open(app_py, encoding="utf-8") as f:
        app_src = f.read()
    with open(smoke_py, encoding="utf-8") as f:
        smoke_src = f.read()
    with open(runtime_py, encoding="utf-8") as f:
        runtime_src = f.read()
    assert "init_torch_runtime(" in app_src
    assert "init_torch_runtime(" in smoke_src
    assert "zeros(1, device=" in runtime_src
    assert "_warmup_cuda" in runtime_src
    assert "torch.zeros" not in smoke_src
    assert "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD" not in smoke_src
    assert "from cutora.asr.engine import torch" not in app_src
