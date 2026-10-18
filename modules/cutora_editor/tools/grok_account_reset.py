#!/usr/bin/env python
"""彻底换掉 grok 账号：清干净所有可能压过新登录的东西，然后登一次。

顺序是有讲究的 —— 前面不清干净，后面登了也会被旧的盖回去：

  1. 停掉还在跑的 grok（leader 进程占着 ~/.grok，不停就改不动）
  2. grok logout（清缓存凭据）
  3. 删掉 XAI_API_KEY / GROK_API_KEY 环境变量
     —— 环境变量的优先级高于登录账号，logout 清不掉它，这是最常见的元凶
  4. 只挪走账号状态那几个文件（auth.json / agent_id / 各种 cache）
     —— grok.exe 就装在 ~/.grok\\bin 里，整个目录改名会把程序自己搬走
  5. grok login（你在这一步登新账号）
  6. grok models 验证通没通

全程不删任何东西，只改名。脚本不会打印你的 key 值。
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
GROK_DIR = HOME / ".grok"
ENV_VARS = ("XAI_API_KEY", "GROK_API_KEY", "GROK_TOKEN", "XAI_TOKEN")


def say(msg: str = "") -> None:
    print(msg, flush=True)


def rule(title: str) -> None:
    say("")
    say("=" * 64)
    say("  " + title)
    say("=" * 64)


def run(cmd: list[str] | str, *, capture: bool = True, check: bool = False):
    """跑一条命令。capture=False 时让它直接用控制台（登录要交互）。"""
    try:
        if capture:
            return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")
        return subprocess.run(cmd, shell=isinstance(cmd, str))
    except FileNotFoundError:
        return None


def ask(prompt: str) -> bool:
    try:
        return input(prompt + " [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ------------------------------------------------------------------ 1. 停进程

def stop_grok() -> None:
    rule("1 / 6  停掉还在跑的 grok")
    r = run("grok leader list")
    if r is not None and r.returncode == 0 and (r.stdout or "").strip():
        say("  发现 leader 进程：")
        for line in (r.stdout or "").strip().splitlines():
            say("    " + line)
        killed = run("grok leader kill --all")
        if killed is None or killed.returncode != 0:
            killed = run("grok leader kill")
        if killed is not None and killed.returncode == 0:
            say("  已让它们退出。")
        else:
            say("  grok leader kill 没能收工 —— 下面的 taskkill 会兜底。")
    else:
        say("  没有在跑的 leader。")
    # 兜底：残留进程会锁住 ~/.grok，改名会失败
    for image in ("grok.exe", "grok-tui.exe"):
        run(f'taskkill /F /IM {image}')
    say("  残留进程已清理（没有的话这一步什么也不会发生）。")


# ------------------------------------------------------------------ 2. logout

def logout() -> None:
    rule("2 / 6  grok logout（清缓存凭据）")
    r = run("grok logout")
    if r is None:
        say("  找不到 grok 命令 —— 它不在 PATH 里。先确认 grok 装在哪。")
        return
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    say("  " + (out.replace("\n", "\n  ") if out else "(没有输出，通常表示已经是登出状态)"))


# ------------------------------------------------------- 3. 环境变量（关键一步）

def _reg_has(var: str, root: str) -> bool:
    key = r"HKCU\Environment" if root == "user" else \
          r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    r = run(f'reg query "{key}" /v {var}')
    return bool(r and r.returncode == 0)


def clear_env() -> None:
    rule("3 / 6  删掉会压过登录账号的环境变量")
    say("  （环境变量的优先级高于你登录的账号，logout 清不掉它 —— 这是最常见的元凶）")
    say("")
    found_any = False
    for var in ENV_VARS:
        in_proc = bool(os.environ.get(var))
        in_user = _reg_has(var, "user")
        in_sys = _reg_has(var, "system")
        if not (in_proc or in_user or in_sys):
            say(f"  {var:<14} 没有设置")
            continue
        found_any = True
        where = ", ".join(w for w, ok in
                          (("当前进程", in_proc), ("用户级", in_user), ("系统级", in_sys)) if ok)
        say(f"  {var:<14} **有值** —— 位置：{where}")
        if in_user:
            r = run(f'reg delete "HKCU\\Environment" /F /V {var}')
            say("                 用户级已删除。" if r and r.returncode == 0
                else "                 用户级删除失败，需要手动处理。")
        if in_sys:
            say("                 系统级需要管理员权限，这个脚本不碰。")
            say("                 用管理员身份开 cmd，跑：")
            say(f'                   reg delete "HKLM\\SYSTEM\\CurrentControlSet\\Control'
                f'\\Session Manager\\Environment" /F /V {var}')
        os.environ.pop(var, None)
    if not found_any:
        say("")
        say("  四个都没有设置 —— 那问题不在环境变量上，继续往下走。")
    else:
        say("")
        say("  注意：已经开着的命令行窗口读不到这个改动。这个脚本跑完之后，")
        say("  把所有 cmd / PowerShell 窗口关掉重开一次。")


# ------------------------------------------------ 4. 只挪凭据，绝不动整个目录

# 2026-09-19 的教训：grok.exe 就装在 ~/.grok\bin 里面。第一版脚本把整个目录
# 改了名，等于把程序自己搬走了，`grok` 立刻 not recognized。
# 所以这里只挪「账号状态」这几个文件，程序、插件、技能、会话一律不碰。
ACCOUNT_FILES = (
    "auth.json", "auth.json.lock",
    "agent_id",
    "settings_cache.json",
    "models_cache.json",
    "active_sessions.json", "active_sessions.lock",
)
# 这些一旦挪走 grok 就废了 / 或者是与账号无关的用户资产，永远不碰
NEVER_TOUCH = (
    "bin", "vendor", "bundled", "completions", "docs", "downloads",
    "installed-plugins", "marketplace-cache", "skills", "sessions",
    "memory", "memory-v2", "logs", "worktrees.db", "relocations",
)


def grok_exe_path() -> str:
    r = run("where grok")
    if r and r.returncode == 0 and (r.stdout or "").strip():
        return (r.stdout or "").strip().splitlines()[0]
    return ""


def clean_account_state(already_clean: bool) -> None:
    rule("4 / 6  挪走账号状态文件（程序本体不动）")
    exe = grok_exe_path()
    if exe:
        say(f"  grok.exe 在：{exe}")
        if str(GROK_DIR).lower() in exe.lower():
            say("  —— 它就装在 ~/.grok 里面，所以**绝对不能给整个目录改名**。")
    if not GROK_DIR.exists():
        say(f"  {GROK_DIR} 不存在，跳过。")
        return
    if already_clean:
        say("")
        say("  上一步已经确认是未登录状态（grok logout 生效了），")
        say("  账号残留已经清干净，这一步没必要做。")
        if not ask("  还是要挪一遍吗？"):
            say("  跳过。")
            return
    present = [n for n in ACCOUNT_FILES if (GROK_DIR / n).exists()]
    if not present:
        say("")
        say("  这几个账号状态文件一个都不在，已经是干净的：")
        say("    " + ", ".join(ACCOUNT_FILES))
        return
    say("")
    say("  要挪走的（只有这些）：")
    for n in present:
        say(f"    {n}")
    say("")
    say("  不碰的：" + ", ".join(NEVER_TOUCH))
    say("  历史会话、跨会话记忆、插件、技能都留着。")
    say("")
    if not ask("  挪吗？（只是移到子目录，随时能挪回来）"):
        say("  跳过。")
        return
    dst = GROK_DIR / ("_account_backup_" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    dst.mkdir(parents=True, exist_ok=True)
    moved = 0
    for n in present:
        try:
            shutil.move(str(GROK_DIR / n), str(dst / n))
            moved += 1
        except OSError as exc:
            say(f"  {n} 挪不动：{exc}")
    say(f"  挪了 {moved} 个，放在：{dst}")
    say("  想还原就把里面的文件移回上一层。")


# -------------------------------------------------------------------- 5. login

def login() -> None:
    rule("5 / 6  登录新账号")
    say("  下面会打开浏览器让你授权。")
    say("")
    say("  **先在浏览器里退出旧账号**，或者用无痕窗口 ——")
    say("  浏览器要是还挂着旧账号，它会一路点过去，把旧的又授权回来。")
    say("")
    try:
        input("  准备好了按回车继续……")
    except (EOFError, KeyboardInterrupt):
        return
    r = run("grok login", capture=False)
    if r is None:
        say("  找不到 grok 命令。")
        return
    if r.returncode != 0:
        say("")
        say(f"  grok login 退出码 {r.returncode}。")
        say("  如果它默认走的不是 OAuth，试试： grok login --oauth")


# ------------------------------------------------------------------- 6. 验证

def check_auth() -> bool:
    """跑一次 grok models，返回 True 表示「已经是未登录状态」。"""
    r = run("grok models")
    if r is None:
        return False
    out = ((r.stdout or "") + (r.stderr or "")).lower()
    return "not authenticated" in out or "未认证" in out or "not logged in" in out


def verify() -> None:
    rule("6 / 6  验证")
    say("  跑 grok models（只列模型，几乎不花额度）……")
    say("")
    r = run("grok models")
    if r is None:
        say("  找不到 grok 命令。")
        return
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    for line in out.splitlines()[:30]:
        say("    " + line)
    say("")
    low = out.lower()
    if r.returncode == 0 and "grok-4" in low:
        say("  通了。新账号可用。")
        say("")
        say("  接下来：关掉所有命令行窗口重开一次（让环境变量的改动生效），")
        say("  然后点 D:\\0_Dev\\CUTORA\\RUN_GROK_QUEUE2.bat 开始跑队列。")
    elif "limit" in low or "quota" in low or "402" in low or "payment" in low:
        say("  还是被限。到这一步说明清理是干净的，限制来自账号本身：")
        say("    · 新账号的套餐真的有周/月上限，等重置或升级")
        say("    · 或者登的还是旧账号 —— 回到第 5 步，用无痕窗口重登一次")
    else:
        say(f"  退出码 {r.returncode}。把上面的输出发给 Claude 看。")


def main() -> int:
    say("")
    say("  彻底换掉 grok 账号")
    say("  ------------------")
    say("  会做六件事：停进程 → logout → 清环境变量 → 挪账号状态 → 登录 → 验证")
    say("  全程只改名不删除，随时可以还原。")
    say("")
    if not ask("  开始吗？"):
        say("  取消了。")
        return 1
    stop_grok()
    logout()
    clear_env()
    clean_account_state(already_clean=check_auth())
    login()
    verify()
    say("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
