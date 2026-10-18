# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q262：门禁 0 按内容记账。钥匙必须在「该重跑」时变、在「内容没变」时不变。"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tools import gate0  # noqa: E402


def test_classify_tiers():
    c = gate0.classify
    assert c("cutora/ui/main_window.py") == "code"
    assert c("resources/stylepacks/a.json") == "code"
    assert c("tests/conftest.py") == "code"
    assert c("tests/qt_isolate.py") == "code"
    assert c("tests/golden/RERECORD_NOTES.md") == "code"
    assert c("resources/THIRD_PARTY_LICENSES.md") == "code"
    assert c("docs/api/commands.md") == "code"          # 产品运行时读
    assert c("AGENTS.md") == "docs"
    assert c("docs/plan/PACK_GATE.md") == "docs"
    assert c("tests/test_t03_shell.py") == "skip"       # 各自的钥匙
    assert c("tools/_diag/probe.py") == "skip"
    assert c("docs/plan/queue/_log/_gate0.log") == "skip"
    assert c("tests/ui_golden/u01c/mismatch.png") == "skip"
    assert c("x/y.autosave.json") == "skip"
    assert c("cutora/__pycache__/a.cpython-310.pyc") == "skip"


def test_tier_digest_is_order_free_and_content_sensitive():
    a = gate0.tier_digest({"a.py": "1" * 40, "b.py": "2" * 40})
    b = gate0.tier_digest({"b.py": "2" * 40, "a.py": "1" * 40})
    assert a == b
    assert a != gate0.tier_digest({"a.py": "1" * 40, "b.py": "3" * 40})
    assert a != gate0.tier_digest({"a.py": "1" * 40})


def test_key_changes_only_when_inputs_change():
    k = gate0.file_key
    base = k("code", "env", "docs", "test", False)
    assert base == k("code", "env", "docs", "test", False)
    assert base != k("code2", "env", "docs", "test", False)
    assert base != k("code", "env2", "docs", "test", False)
    assert base != k("code", "env", "docs", "test2", False)
    # 不读文档的测试：文档变了不重跑
    assert base == k("code", "env", "docs2", "test", False)
    # 读文档的测试：文档变了要重跑
    assert k("code", "env", "docs", "test", True) != k("code", "env", "docs2", "test", True)


def test_docs_sensitivity_detection():
    assert gate0.is_docs_sensitive('p = os.path.join(REPO, "docs", "plan")')
    assert gate0.is_docs_sensitive('open("README_INTERNAL.md")')
    assert gate0.is_docs_sensitive("for dp, dn, fn in os.walk(REPO):")
    assert not gate0.is_docs_sensitive("def test_x():\n    assert 1 + 1 == 2\n")


def test_repo_fingerprint_covers_every_test_file():
    fp = gate0.repo_fingerprints()
    found = set(gate0.discover())
    assert found, "no test files discovered"
    assert found <= set(fp["tests"]), sorted(found - set(fp["tests"]))[:5]
    assert "cutora/__init__.py" in fp["_code_map"]
    assert "AGENTS.md" in fp["_docs_map"]
    assert not any(p.startswith("tools/_diag/") for p in fp["_code_map"])


def test_pack_gate0_uses_content_keyed_runner():
    src = open(os.path.join(REPO, "tools", "pack_internal.py"), encoding="utf-8").read()
    assert '"gate0.py"' in src
    assert '"pytest", "tests", "-q"' not in src


def test_env_normalized_before_fingerprint(monkeypatch):
    """手动跑和 pack_internal 里跑（带 CUTORA_COMPOSE_WORKERS=1）必须是同一把钥匙。"""
    monkeypatch.delenv("CUTORA_COMPOSE_WORKERS", raising=False)
    # _normalize_env 会写这些；先交给 monkeypatch 记账，测完还原，不漏给后面的测试
    for name in ("PYTHONUTF8", "PYTHONIOENCODING"):
        monkeypatch.setenv(name, os.environ.get(name, "1" if name == "PYTHONUTF8" else "utf-8"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    gate0._normalize_env()
    plain = gate0.env_fingerprint()
    monkeypatch.setenv("CUTORA_COMPOSE_WORKERS", "1")
    gate0._normalize_env()
    assert "CUTORA_COMPOSE_WORKERS" not in os.environ
    assert gate0.env_fingerprint() == plain
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert gate0.env_fingerprint() != plain
