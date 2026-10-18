# -*- coding: utf-8 -*-
"""T13a: --describe output must be a superset of the T03c-done fixture."""
from __future__ import annotations

import json
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FIXTURE = os.path.join(REPO, "tests", "fixtures", "t03c_describe.json")


def _run_describe():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [VENV_PY, "-m", "cutora", "--describe"],
        cwd=REPO, capture_output=True, text=True, timeout=30, env=env,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _assert_superset(old, new, path="$"):
    if isinstance(old, dict):
        assert isinstance(new, dict), "%s: expected object" % path
        for key, val in old.items():
            assert key in new, "%s missing key %r" % (path, key)
            _assert_superset(val, new[key], path + "." + str(key))
        return
    if isinstance(old, list):
        assert isinstance(new, list), "%s: expected list" % path
        if old and isinstance(old[0], dict) and "name" in old[0]:
            new_by = {item.get("name"): item for item in new if isinstance(item, dict)}
            for item in old:
                name = item.get("name")
                assert name in new_by, "%s missing named item %r" % (path, name)
                _assert_superset(item, new_by[name], path + "[" + str(name) + "]")
            return
        assert len(new) >= len(old), "%s list shrank" % path
        for i, item in enumerate(old):
            _assert_superset(item, new[i], "%s[%d]" % (path, i))
        return
    assert old == new, "%s changed: %r -> %r" % (path, old, new)


def test_describe_is_superset_of_t03c_fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        old = json.load(fh)
    new = _run_describe()
    _assert_superset(old, new)
    assert "commands" in new
    assert isinstance(new["commands"], list)
    assert len(new["commands"]) >= 60
