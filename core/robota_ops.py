# -*- coding: utf-8 -*-
"""
片语工作室 · Robota 全域操作工具箱 (CLI & Python API)
======================================================
供 Gemini 4 Argon (`agy.exe`) 及本地 Agent 大脑直接调用，毫秒级完成：
1. `status [day_or_path]`         : 深度扫描工作区目录、识别无后缀文件、统计视频/图片/Orante/CUTORA状态
2. `fix-organize <dir>`           : 调用右键表格同源能力：魔数嗅探补齐 .mp4/.jpg/.png 后缀、剥离中文伪后缀、归入视频/图片文件夹
3. `open-folder <target> [day]`   : 打开指定工作区文件夹（视频/图片/orante视频/下载/日期目录）
4. `launch <app> [dir]`           : 启动 studio 三大核心软件 (`table_tool` / `orante` / `cutora`)
5. `orante <subcmd> [task_id]`    : 操控 Orante 2.0 制片台 (`health` / `batches` / `check` / `worklist` / `replenish` / `reset`)
6. `cutora <subcmd> ...`          : 检查或操作 CUTORA 工程 (`inspect` / `create-link` / `api` / `launch_stitch` / `launch_edit`)
7. `remember <rule_text>`         : 将新学到的用户习惯或操作步骤写入长期记忆 (`sop.md` / `habits.md` / `episodes.jsonl`)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STUDIO_ROOT = Path(r"D:\0_LTX")
if str(STUDIO_ROOT) not in sys.path:
    sys.path.insert(0, str(STUDIO_ROOT))

from core.robota_guide import DailyWorkflowConductor, RobotaMemoryEvolutionEngine


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Robota Studio Operations CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stat = sub.add_parser("status", help="Inspect workspace and active day directory")
    p_stat.add_argument("target", nargs="?", default="", help="Day string (e.g. 2026-10-15 or 15号) or path")

    p_fix = sub.add_parser("fix-organize", help="Fix missing .mp4/.jpg extensions and organize into folders")
    p_fix.add_argument("target", nargs="?", default="", help="Target folder or day (e.g. 2026-10-15)")
    p_fix.add_argument("--video-dir", default="视频", help="Subfolder name for videos (default: 视频, or '' to keep in place)")
    p_fix.add_argument("--image-dir", default="", help="Subfolder name for images (optional)")
    p_fix.add_argument("--no-move-videos", action="store_true", help="Only fix extensions without moving videos")

    p_open = sub.add_parser("open-folder", help="Open a workspace folder in Explorer")
    p_open.add_argument("target", nargs="?", default="视频", help="Target hint (视频/图片/orante视频/今日目录 or path)")
    p_open.add_argument("--day", default="", help="Optional day hint (e.g. 2026-10-15)")

    p_launch = sub.add_parser("launch", help="Launch a studio application")
    p_launch.add_argument("app", choices=["table_tool", "orante", "cutora"], help="Application to launch")
    p_launch.add_argument("--dir", default="", help="Target directory, .cutora project path, or room")

    p_orante = sub.add_parser("orante", help="Control or query Orante 2.0")
    p_orante.add_argument("action", choices=["health", "batches", "check", "worklist", "replenish", "reset"])
    p_orante.add_argument("--task-id", default="", help="Task ID (e.g. T0127)")

    p_cutora = sub.add_parser("cutora", help="Inspect, create-link, add-credit, split, auto-subtitle, stitch-sync, or control CUTORA project")
    p_cutora.add_argument(
        "action",
        choices=["inspect", "create-link", "add-credit", "split", "auto-subtitle", "stitch-sync", "api", "launch_stitch", "launch_edit"],
    )
    p_cutora.add_argument("--path", default="", help="Path to .cutora file or day hint")
    p_cutora.add_argument("--main-dir", default="", help="Main videos folder (e.g. D:\\1_PROJECT\\2026-10-16\\1016视频)")
    p_cutora.add_argument("--clips-dir", default="", help="External clips folder for 1:1 batch link-import")
    p_cutora.add_argument("--split", default="top_bottom", help="Split layout (default: top_bottom)")
    p_cutora.add_argument("--force-auto-link", action="store_true", help="Force auto-link even if counts differ")
    p_cutora.add_argument("--preset", default="credit.default", help="Text preset ID for add-credit (default: credit.default)")
    p_cutora.add_argument("--content", default="", help="Optional custom text content for add-credit")
    p_cutora.add_argument("--chunk", type=int, default=40, help="Tasks per split project (default: 40)")
    p_cutora.add_argument("--outdir", default="", help="Output directory for split projects")
    p_cutora.add_argument("--cmd-name", default="project.info", help="CUTORA API command name (for action=api)")
    p_cutora.add_argument("--params", default="{}", help="JSON params for CUTORA API command")

    p_lc = sub.add_parser("lifecycle", help="Inspect or update active day, batch, and upload completion state")
    p_lc.add_argument("--active-day", default="", help="Set active day (e.g. 2026-10-16 or 16号)")
    p_lc.add_argument("--batch", default="", help="Set active batch (A / B / C)")
    p_lc.add_argument("--mark-done", default="", help="Mark day as 100% uploaded & completed (e.g. 2026-10-15)")
    p_lc.add_argument("--recipe", default="", help="Set active recipe (A or B)")
    p_lc.add_argument("--note", default="", help="Optional note")

    p_rag = sub.add_parser("rag", help="Search studio local RAG knowledge base")
    p_rag.add_argument("query", help="Search query")

    p_mem = sub.add_parser("remember", help="Record a workflow rule or lesson into long-term memory")
    p_mem.add_argument("rule", help="Workflow rule or habit to remember")
    p_mem.add_argument("--category", choices=["sop", "habit"], default="sop")

    args = parser.parse_args()
    conductor = DailyWorkflowConductor()

    if args.cmd == "status":
        res = conductor.inspect_workspace_detail(args.target)
    elif args.cmd == "lifecycle":
        if any([args.active_day, args.batch, args.mark_done, args.recipe, args.note]):
            res = conductor.update_project_lifecycle_state(
                active_day=args.active_day,
                active_batch=args.batch,
                mark_day_completed=args.mark_done,
                recipe_type=args.recipe,
                note=args.note,
            )
        else:
            stats = conductor.get_live_workspace_stats()
            res = {
                "ok": True,
                "active_day": stats["day_str"],
                "active_batch": stats.get("active_batch", "A"),
                "stage_label": stats.get("stage_label", ""),
                "prev_completed_day": stats.get("prev_completed_day", ""),
                "prev_completed_summary": stats.get("prev_completed_summary", {}),
                "lifecycle": stats.get("lifecycle", {}),
            }
    elif args.cmd == "fix-organize":
        res = conductor.fix_extensions_and_organize(
            target_dir_or_day=args.target,
            video_folder_name="" if args.no_move_videos else args.video_dir,
            image_folder_name=args.image_dir,
            clean_pseudo_suffix=True,
        )
    elif args.cmd == "open-folder":
        hint = f"{args.day} {args.target}".strip()
        res = conductor.open_folder_smart(hint)
    elif args.cmd == "launch":
        if args.app == "table_tool":
            tdir = Path(args.dir) if args.dir else None
            ok = conductor.launch_table_tool_in_dir(tdir)
            res = {"ok": ok, "app": "table_tool", "dir": str(tdir or conductor.get_live_workspace_stats()["day_dir"])}
        elif args.app == "orante":
            ok = conductor.launch_orante_room()
            res = {"ok": ok, "app": "orante"}
        else:
            if args.dir and args.dir.lower().endswith(".cutora"):
                ok = conductor.launch_cutora_room(room="", project_path=args.dir)
                res = {"ok": ok, "app": "cutora", "project_path": args.dir}
            else:
                room = args.dir if args.dir in ("stitch", "edit", "ai", "batch", "monitor") else "stitch"
                ok = conductor.launch_cutora_room(room=room)
                res = {"ok": ok, "app": "cutora", "room": room}
    elif args.cmd == "orante":
        res = conductor.control_orante_api(args.action, args.task_id)
    elif args.cmd == "cutora":
        if args.action == "inspect":
            res = conductor.inspect_cutora_project(args.path)
        elif args.action == "create-link":
            res = conductor.cutora_create_and_link_project(
                main_videos_dir=args.main_dir,
                external_clips_dir=args.clips_dir,
                output_project_path=args.path,
                split_layout=args.split,
                loop=True,
                match_duration=True,
                mute=True,
                open_in_cutora=True,
                force_auto_link=bool(args.force_auto_link),
            )
        elif args.action == "add-credit":
            res = conductor.cutora_add_ai_credit(
                project_path=args.path,
                preset=args.preset,
                content=args.content,
            )
        elif args.action == "split":
            res = conductor.cutora_split_project(
                project_path=args.path,
                chunk_size=args.chunk,
                out_dir=args.outdir,
            )
        elif args.action == "auto-subtitle":
            res = conductor.cutora_auto_subtitle(project_path=args.path)
        elif args.action == "stitch-sync":
            res = conductor.cutora_smart_stitch_sync(batch_dir=args.path)
        elif args.action == "api":
            try:
                p_obj = json.loads(args.params or "{}")
            except Exception:
                p_obj = {}
            res = conductor.call_cutora_api(args.cmd_name, p_obj)
        elif args.action == "launch_stitch":
            res = {"ok": conductor.launch_cutora_room("stitch", project_path=args.path)}
        else:
            res = {"ok": conductor.launch_cutora_room("edit", project_path=args.path)}
    elif args.cmd == "rag":
        res = conductor.search_studio_knowledge(args.query)
    elif args.cmd == "remember":
        mem = RobotaMemoryEvolutionEngine()
        res = mem.save_explicit_rule(args.rule, category=args.category)
    else:
        res = {"ok": False, "error": f"Unknown command: {args.cmd}"}

    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

