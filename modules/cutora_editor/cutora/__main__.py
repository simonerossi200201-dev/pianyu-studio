# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""python -m cutora

Early CLI dispatch (--describe / --status / --do / --run) runs before the
GUI is imported, so --describe never loads torch.
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _early_dispatch() -> None:
    if "--render-job" in sys.argv:
        return
    from cutora.core.command_cli import is_commands_argv
    if not (
        "--describe" in sys.argv
        or "--status" in sys.argv
        or "--do" in sys.argv
        or "--run" in sys.argv
        or is_commands_argv(sys.argv)
    ):
        return
    try:
        from cutora.batch import cli as _batch_cli
        _early = _batch_cli.early_dispatch(sys.argv)
        if _early is not None:
            raise SystemExit(_early)
    except SystemExit:
        raise
    except Exception as _cli_early_err:
        try:
            sys.stderr.write("[CLI] 早期分派失败: %s\n" % _cli_early_err)
        except Exception:
            pass
        raise SystemExit(1)


def _check_runtime() -> int:
    """init_torch_runtime + import PySide6, print mapped MSVCP140 path, exit."""
    import PySide6.QtCore  # noqa: F401
    from cutora.core.runtime import loaded_msvc_runtime_path

    path = loaded_msvc_runtime_path() or ""
    sys.stdout.write(path + "\n")
    return 0


def _guard_null_stdio() -> None:
    """pythonw leaves stdout/stderr as None. Install the logging stand-in first."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from cutora.core.log import setup_logging
    setup_logging()


if __name__ == "__main__":
    _guard_null_stdio()
    if "--render-job" in sys.argv:
        from cutora.core.flags import mark_render_child
        mark_render_child()
        from cutora.app import run_render_job_entry
        raise SystemExit(run_render_job_entry())
    if "--gpu-fx-server" in sys.argv:
        # Q181：GPU 像素运算服务进程（只 import torch + 运算，不碰 Qt 界面）
        _i = sys.argv.index("--gpu-fx-server")
        _addr_file = sys.argv[_i + 1]
        _parent = int(sys.argv[_i + 2]) if _i + 2 < len(sys.argv) else None
        from cutora.core.runtime import pin_cv2_num_threads
        pin_cv2_num_threads()
        from cutora.fx.gpu import serve
        raise SystemExit(serve(_addr_file, _parent))
    _early_dispatch()
    if "--doctor" in sys.argv:
        from cutora.core.doctor import run_cli
        raise SystemExit(run_cli())
    from cutora.core.runtime import init_torch_runtime  # 此时 sys.modules 里不得有任何 PySide6.*
    init_torch_runtime(render_child=False)
    if "--check-runtime" in sys.argv:
        raise SystemExit(_check_runtime())
    if "--selftest" in sys.argv:
        from cutora.core.selftest import run_selftest
        raise SystemExit(run_selftest())
    from cutora.core.command_cli import dispatch_late, is_commands_argv
    if is_commands_argv(sys.argv):
        raise SystemExit(dispatch_late(sys.argv))
    from cutora.app import main
    main()

