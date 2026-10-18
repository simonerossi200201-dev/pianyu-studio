# -*- coding: utf-8 -*-
r"""所有路径的唯一来源。任何模块都不得自己拼绝对路径。

规则：
  ROOT   = Orante\           软件根（这个文件的上上级）
  DATA   = ROOT\data         程序数据（数据库、日志、抓包）；可被 ORANTE_DATA 覆盖
  UI     = ROOT\ui           前端静态文件
  LEGACY = ROOT\orante\legacy  从流水线拷进来的老模块（只读）
  默认工作区 = ROOT\workspace   用户可在设置里改
find_legacy_root()  在开发机上顺带找到旁边的流水线目录，让老模块能 import（分发包里没有它也能跑）。

DATA / DB_FILE 等模块级常量在 import 时算一次。测试改 ORANTE_DATA 之后要
调用 reset() 才会跟着走；db.conn() 开连接前会自己 reset 一次。
新代码请用 data_dir() / db_file()，每次都读环境变量。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "orante"
UI = ROOT / "ui"
LEGACY = PKG / "legacy"
I18N = PKG / "i18n"
DEFAULT_WORKSPACE = ROOT / "workspace"

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

# 下面四个由 reset() 填。保留常量是因为全项目 40+ 处 paths.DATA / paths.DB_FILE
# 做 Path 运算（mkdir、/、is_dir）；改成纯函数要动 engines/probe 一整面。
DATA: Path
LOGS: Path
DB_FILE: Path
CHROME_PROFILES: Path


def data_dir() -> Path:
    """每次读 ORANTE_DATA。没设就用 ROOT/data。"""
    env = os.environ.get("ORANTE_DATA")
    return Path(env).resolve() if env else ROOT / "data"


def logs_dir() -> Path:
    return data_dir() / "logs"


def db_file() -> Path:
    return data_dir() / "orante.db"


def chrome_profiles() -> Path:
    return data_dir() / "chrome"


def reset() -> None:
    """按当前 ORANTE_DATA 重算 DATA / LOGS / DB_FILE / CHROME_PROFILES。"""
    global DATA, LOGS, DB_FILE, CHROME_PROFILES
    DATA = data_dir()
    LOGS = logs_dir()
    DB_FILE = db_file()
    CHROME_PROFILES = chrome_profiles()


reset()


def ensure_dirs() -> None:
    reset()
    for p in (DATA, LOGS, DEFAULT_WORKSPACE, CHROME_PROFILES, LEGACY):
        p.mkdir(parents=True, exist_ok=True)


def find_legacy_root() -> Path | None:
    """开发机：Orante 旁边那个含 core\\grok_pipe.py 的流水线目录（名字可能是中文）。"""
    env = os.environ.get("ORANTE_PIPE_ROOT")
    if env and (Path(env) / "core" / "grok_pipe.py").is_file():
        return Path(env)
    for d in ROOT.parent.iterdir():
        try:
            if (d / "core" / "grok_pipe.py").is_file():
                return d
        except OSError:
            continue
    return None


def legacy_sys_path() -> list[str]:
    """老模块的搜索路径：先 ROOT\\orante\\legacy，再开发机上的流水线 core 目录。"""
    out = [str(LEGACY)]
    root = find_legacy_root()
    if root:
        out += [str(root / "core"), str(root / "0-长文案" / "core")]
    return out


def add_legacy_to_sys_path() -> None:
    for p in legacy_sys_path():
        if p not in sys.path:
            sys.path.append(p)


def rel(p: Path | str) -> str:
    """给日志/界面看的相对路径（相对 ROOT），落在外面就原样返回。"""
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def is_under_real_data(p: Path | str | None) -> bool:
    """p 是否落在 ROOT/data 下面（真实库）。比较时忽略盘符大小写。"""
    if p is None:
        return False
    try:
        child = os.path.normcase(os.path.abspath(str(Path(p).resolve())))
        parent = os.path.normcase(os.path.abspath(str((ROOT / "data").resolve())))
    except Exception:
        return False
    sep = os.sep
    if not parent.endswith(sep):
        parent_sep = parent + sep
    else:
        parent_sep = parent
    return child == parent or child.startswith(parent_sep)
