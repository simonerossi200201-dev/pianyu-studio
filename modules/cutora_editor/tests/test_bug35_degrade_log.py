# -*- coding: utf-8 -*-
"""BUG-35: degrade log must not claim GPU already stopped."""
from __future__ import annotations

from cutora.render.export_guard import write_degrade


def test_write_degrade_does_not_lie(tmp_path, capsys):
    write_degrade(str(tmp_path), "可用内存仅剩 1.80 GB", inflight=5)
    out = capsys.readouterr().out + capsys.readouterr().err
    # _log may go to stdout or a module logger; also read the flag file.
    import os, json
    flag = os.path.join(str(tmp_path), "degrade.flag")
    data = json.loads(open(flag, encoding="utf-8").read())
    blob = json.dumps(data, ensure_ascii=False) + out
    assert "已请求降级" in blob or data.get("level") == "gpu_off"
    assert "已关闭 GPU 加速并降低编码线程" not in blob
    assert data.get("inflight") == 5
