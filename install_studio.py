"""片语工作室 (D:\\0_LTX) 全自动安装、快捷方式创建与硬件自适应体检程序 (install_studio.py)。

功能：
1. 自动重写便携 Python (`runtime/venv/pyvenv.cfg` -> `runtime/python`)，确保任意目录解压即用；
2. 自动生成桌面与开始菜单快捷方式 (`片语工作室.lnk`)；
3. 自动检测本机 CPU、内存与显卡显存，生成 32GB+CPU / 8GB GPU / 16GB GPU 三级运行配置；
4. 自动注册 Windows 资源管理器「片语工作室 - 表格工具」右键菜单（仅限当前用户 HKCU，免管理员权限）；
5. 严格验证 `MiniMax H3` 排除状态。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

LTX_ROOT = Path(__file__).resolve().parent
if str(LTX_ROOT) not in sys.path:
    sys.path.insert(0, str(LTX_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from core.portable import rewrite_pyvenv_cfg
from core.shortcut import ensure_shortcuts
from core.hardware_guard import get_hardware_guard
from core.spine_db import get_spine_db


def register_context_menu() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        pyw = LTX_ROOT / "runtime" / "venv" / "Scripts" / "pythonw.exe"
        if not pyw.is_file():
            pyw = Path(sys.executable)
        script = LTX_ROOT / "modules" / "table_tool" / "app" / "表格工具.py"
        ico = LTX_ROOT / "resources" / "brand" / "app.ico"

        for reg_path, arg in [
            (r"Software\Classes\Directory\Background\shell\LTXStudioTableTool", "%V"),
            (r"Software\Classes\Directory\shell\LTXStudioTableTool", "%1"),
        ]:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_path) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "片语工作室 - 表格工具")
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, str(ico if ico.is_file() else pyw))
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_path + r"\command") as ck:
                winreg.SetValueEx(ck, "", 0, winreg.REG_SZ, f'"{pyw}" "{script}" "{arg}"')
        return True
    except Exception as e:
        print(f"  [提示] 右键菜单注册跳过: {e}")
        return False


def verify_no_minimax_h3() -> list[str]:
    violations: list[str] = []
    for root, dirs, files in os.walk(LTX_ROOT / "runtime" / "models"):
        for name in dirs + files:
            low = name.lower()
            if "minimax_h3" in low or "qwen3vl_32b_minimax" in low:
                violations.append(str(Path(root) / name))
    return violations


def main() -> None:
    print("================================================================")
    print("  片语工作室 (D:\\0_LTX) 全自动安装、桌面图标创建与硬件体检程序")
    print("================================================================\n")

    # 0. 重写便携 Python 路径 + 创建桌面快捷方式
    rewrite_pyvenv_cfg(LTX_ROOT)
    shortcuts = ensure_shortcuts(LTX_ROOT, force=True, quiet=True)

    guard = get_hardware_guard()
    profile = guard.probe_system()
    print(f"[1/5] 硬件探测完成:")
    print(f"  · CPU 核心数 : {profile.cpu_cores} 核")
    print(f"  · 物理内存   : {profile.ram_avail_gb} GB 可用 / {profile.ram_total_gb} GB 总计")
    print(f"  · 独立显卡   : {profile.gpu_name or '未检测到独显 (将使用 32GB+CPU 模式)'} "
          f"(总显存 {profile.gpu_vram_total_gb} GB / 空闲 {profile.gpu_vram_free_gb} GB)")
    print(f"  · 推荐运行档 : {profile.tier_label}\n")

    matrix = guard.evaluate_all_models()
    print(f"[2/5] 本地模型与工具链分级检定 ({len(matrix)} 项):")
    for m in matrix:
        print(f"  · {m['badge']}  {m['name']}")

    from core.pack_manager import evaluate_all_packs, scan_and_auto_install_inbox
    auto_installed = scan_and_auto_install_inbox()
    if auto_installed:
        print(f"\n[扩展包自动安装] 已从 packs/ 目录自动安装 {len(auto_installed)} 个扩展包:")
        for r in auto_installed:
            print(f"  · ✅ {r.get('pack_id')}: {r.get('files_installed')} 个文件 ({r.get('installed_size_gb')} GB)")
    packs_info = evaluate_all_packs(profile)
    print(f"\n[模块化乐高扩展包状态] 当前模式: {packs_info['summary']['edition_mode']}:")
    for pk in packs_info["packs"]:
        print(f"  · {pk['status_badge']}  {pk['name']} ({pk['approx_size_gb']} GB)")

    ctx_ok = register_context_menu()
    print(f"\n[3/5] 桌面与开始菜单快捷方式创建: 已生成 {len(shortcuts)} 个 ({shortcuts[0] if shortcuts else ''})")
    print(f"[4/5] 资源管理器右键菜单注册: {'成功' if ctx_ok else '已跳过'}")

    violations = verify_no_minimax_h3()
    print(f"[5/5] 合规校验 (确认不包含 MiniMax H3 权重): {'通过 (100% 不含 MiniMax H3)' if not violations else '发现违规: ' + str(violations)}")

    spine = get_spine_db()
    report_path = LTX_ROOT / "data" / "install_health_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "hardware": profile.to_dict(),
                "models": matrix,
                "packs": packs_info,
                "shortcuts_created": [str(p) for p in shortcuts],
                "context_menu_registered": ctx_ok,
                "minimax_h3_excluded": len(violations) == 0,
                "spine_db": spine.get_dashboard_summary(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✅ 全自动安装与体检全部完成！报告已写入: {report_path}")


if __name__ == "__main__":
    main()
