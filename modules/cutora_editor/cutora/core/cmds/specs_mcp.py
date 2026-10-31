# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Agent / MCP gap-fill command specs.

Each entry wraps an existing UI action or CLI helper that previously had no
command-bus entry, so AI agents (studio MCP server, local API, cutora_cli)
can drive it without dialogs. Handlers live in ``cutora.core.cmds.mcp_ops``.
"""
from __future__ import annotations

from cutora.core.cmds.specutil import s, spec

H = "cutora.core.cmds.mcp_ops"


def build() -> list:
    return [
        spec(
            "export.cancel", "停止导出",
            "停止当前正在进行的单条导出或批量导出，等价于导出面板上的停止按钮。"
            "已经写完的成品保留，正在写的那一条会被中断。没有导出时返回 stopped=false 而不是报错。",
            H + ":cmd_export_cancel",
        ),
        spec(
            "app.release_vram", "释放显存",
            "卸载已缓存的 ASR 识别与对齐模型并归还显存（菜单 性能 → 释放显存），不弹提示框。"
            "AI 字幕任务运行中会拒绝执行。本进程从未加载过 torch 时直接返回 0，不会为了释放而导入 torch。",
            H + ":cmd_app_release_vram",
            headless=True,
        ),
        spec(
            "project.recent.list", "列出最近工程",
            "读取最近打开过的工程列表（文件 → 打开最近项目），返回路径、时间戳与文件是否仍存在。"
            "只读，不打开工程、不改设置。配合 project.open 让机器人重新打开上次的工程。",
            H + ":cmd_project_recent_list",
            params={"limit": s("integer", "最多返回几条，默认 20，上限 50")},
            headless=True,
        ),
        spec(
            "project.canvas.set", "设置工程画布尺寸",
            "把工程画布改为 width×height（视图 → 工程画布尺寸，含自定义尺寸），所有视频条目一起改。"
            "写入一步撤销。不弹输入框。失败常见原因：尺寸超出 16~8192。",
            H + ":cmd_project_canvas_set",
            params={
                "width": s("integer", "画布宽，例如 1080"),
                "height": s("integer", "画布高，例如 1920"),
            },
            required=["width", "height"],
        ),
        spec(
            "view.fit_mode.set", "设置视频适配模式",
            "切换预览与新素材的视频适配模式（视图 → 视频适配模式）：fit 适配、fill 填满、stretch 拉伸。"
            "只改界面状态，不改已有片段的变换。失败常见原因：mode 不是三者之一。",
            H + ":cmd_view_fit_mode_set",
            params={"mode": s("string", "fit / fill / stretch", enum=["fit", "fill", "stretch"])},
            required=["mode"],
        ),
        spec(
            "edit.undo", "撤销",
            "撤销最近 steps 步编辑（编辑 → 撤销 / Ctrl+Z），等价于本地 API 的 /v1/undo。"
            "返回撤销后的历史位置与总步数。没有可撤销的步骤时不报错。",
            H + ":cmd_edit_undo",
            params={"steps": s("integer", "撤销几步，默认 1，上限 50")},
        ),
        spec(
            "edit.redo", "重做",
            "重做最近被撤销的 steps 步（编辑 → 重做 / Ctrl+Y），等价于本地 API 的 /v1/redo。"
            "返回重做后的历史位置与总步数。没有可重做的步骤时不报错。",
            H + ":cmd_edit_redo",
            params={"steps": s("integer", "重做几步，默认 1，上限 50")},
        ),
        spec(
            "edit.history", "操作历史",
            "列出撤销栈里每一步的描述、时间、来源命令与来源（ui/api/mcp），以及当前所在位置"
            "（编辑 → 操作历史）。只读，不改任何状态。",
            H + ":cmd_edit_history",
        ),
        spec(
            "job.list", "列出后台作业",
            "列出 USER_DATA/jobs 下最近的后台作业（--async 导出、配方、API 异步命令），"
            "按修改时间倒序，含 job_id、op、状态、pid、日志路径。只读。",
            H + ":cmd_job_list",
            params={"limit": s("integer", "最多返回几条，默认 20")},
            headless=True,
        ),
        spec(
            "job.status", "查询后台作业",
            "查询一个后台作业的进度：运行中与否、已完成/总数、最新输出、失败数、日志末尾若干行，"
            "以及工程队列摘要。等价于 cutora --status。job_id 省略时返回当前导出状态。",
            H + ":cmd_job_status",
            params={
                "job_id": s("string", "作业 id；省略则看当前导出"),
                "log_tail": s("integer", "附带日志末尾几行，默认 20"),
            },
            headless=True,
        ),
        spec(
            "job.cancel", "取消后台作业",
            "取消一个后台作业：先写导出停止标记让渲染优雅收尾，再结束作业进程树，并把作业标成 cancelled。"
            "不删除任何已输出的文件。失败常见原因：job_id 不存在。",
            H + ":cmd_job_cancel",
            params={"job_id": s("string", "作业 id")},
            required=["job_id"],
            headless=True,
        ),
        spec(
            "batch.queue.status", "工程队列状态",
            "返回工程队列摘要：是否激活、是否暂停、当前第几条、每条工程的状态与计数"
            "（批量 → 工程队列…）。只读，不启动队列。",
            H + ":cmd_batch_queue_status",
            headless=True,
        ),
        spec(
            "batch.queue.pause", "暂停工程队列",
            "把工程队列标记为暂停：当前这条导出完成后不再自动接下一条，重启后也不自动续跑。"
            "不删除队列，不改工程文件。没有队列时返回空摘要。",
            H + ":cmd_batch_queue_pause",
            params={"reason": s("string", "暂停原因，写进队列文件便于排查")},
            headless=True,
        ),
        spec(
            "batch.queue.resume", "继续工程队列",
            "取消工程队列的暂停标记，之后由 batch.queue_run 或主窗口继续按顺序处理剩余工程。"
            "本命令只改标记，不立即启动导出。",
            H + ":cmd_batch_queue_resume",
            headless=True,
        ),
        spec(
            "batch.queue.deactivate", "停用工程队列",
            "把工程队列停用（active=false），之后不会再自动续跑；队列文件与每条工程的状态记录保留，"
            "可用 batch.queue.add 重新建队列。不删除任何工程文件或成品。",
            H + ":cmd_batch_queue_deactivate",
            headless=True,
        ),
        spec(
            "batch.face_safe_verify", "人脸防遮挡校准",
            "对已保存的工程执行「人脸与画面零穿帮」自动校准：检测每条主视频人脸，调整分屏副画面与标题位置，"
            "可选输出实景抽帧复核图。结果写到新的工程文件（默认 原名_facesafe），绝不覆盖源工程。耗时较长，异步运行。",
            H + ":cmd_batch_face_safe_verify",
            params={
                "project": s("string", "源工程文件 .cutora / .asproj.json"),
                "out": s("string", "输出工程路径；省略为 原名_facesafe"),
                "force_top_main": s("boolean", "必要时强制主视频置顶布局"),
                "contact_sheet_dir": s("string", "抽帧复核图输出目录（可选）"),
            },
            required=["project"],
            headless=True,
            async_job=True,
        ),
        spec(
            "batch.auto_project", "无头全自动建项",
            "按批次目录、主视频目录、配菜 manifest 与任务表格自动建出分批工程：ASR 定位口播标题、"
            "插入电话贴纸与 AI 标识、分配分屏样式并做人脸防遮挡校准，拆成 parts 个工程。"
            "等价于 python -m cutora.batch.auto_project。只新建工程文件，不删除素材。耗时较长，异步运行。",
            H + ":cmd_batch_auto_project",
            params={
                "batch_dir": s("string", "批次根目录"),
                "videos_dir": s("string", "主视频目录"),
                "clips_manifest": s("string", "配菜 manifest.json 路径或目录"),
                "table": s("string", "任务表格文本路径（含 vNN 与标题）"),
                "phone_img": s("string", "电话贴纸 PNG"),
                "project_name": s("string", "输出工程名前缀"),
                "export_dest": s("string", "成品输出目录"),
                "parts": s("integer", "拆成几个分批工程，默认 2"),
                "ref_project": s("string", "参考样式工程（可选）"),
            },
            required=["batch_dir", "videos_dir", "clips_manifest"],
            headless=True,
            async_job=True,
        ),
        spec(
            "silence.detect", "检测剪气口停顿",
            "对当前视频或指定工程任务执行三路融合停顿检测（ASR 词时间戳 + Silero VAD + 10ms 自适应能量包络），"
            "返回可裁剪的停顿列表、防误伤保护提示与预计缩短时长。只读，不改动工程。",
            H + ":cmd_silence_detect",
            params={
                "project": s("string", "工程文件路径（无头模式必填；有界面时省略则用当前打开工程）"),
                "task_index": s("integer", "任务下标，省略或 -1 表示全部任务"),
                "threshold": s("number", "触发裁剪的最小停顿时长，默认 1.0"),
                "unit": s("string", "停顿时长单位：frames / ms / s / min，默认 s", enum=["frames", "ms", "s", "min"]),
                "keep": s("number", "裁剪后保留的自然间隔，默认 0.25"),
                "keep_unit": s("string", "保留间隔单位：frames / ms / s / min，默认 s", enum=["frames", "ms", "s", "min"]),
                "head_frames": s("integer", "前词词尾保护帧数，默认 2"),
                "tail_frames": s("integer", "后词词首保护帧数，默认 2"),
                "keep_breaths": s("boolean", "保留自然呼吸/气声防误伤，默认 true"),
                "use_vad": s("boolean", "启用 Silero VAD 语音核验，默认 true"),
                "trim_edges": s("boolean", "同时检测片头片尾静音，默认 true"),
            },
            headless=True,
        ),
        spec(
            "silence.apply", "执行剪气口裁剪与自检",
            "对当前打开工程的指定任务（或全部任务）执行剪气口无损涟漪裁剪，自动同步主视频、人声、字幕、"
            "词级卡拉OK时间戳与叠加层，并返回词级与包络无损自检报告。写入一步撤销。",
            H + ":cmd_silence_apply",
            params={
                "task_index": s("integer", "任务下标；省略或 -1 表示全部任务"),
                "threshold": s("number", "触发裁剪的最小停顿时长，默认 1.0"),
                "unit": s("string", "停顿时长单位：frames / ms / s / min，默认 s", enum=["frames", "ms", "s", "min"]),
                "keep": s("number", "裁剪后保留的自然间隔，默认 0.25"),
                "keep_unit": s("string", "保留间隔单位：frames / ms / s / min，默认 s", enum=["frames", "ms", "s", "min"]),
                "head_frames": s("integer", "前词词尾保护帧数，默认 2"),
                "tail_frames": s("integer", "后词词首保护帧数，默认 2"),
                "keep_breaths": s("boolean", "保留自然呼吸/气声防误伤，默认 true"),
                "use_vad": s("boolean", "启用 Silero VAD 语音核验，默认 true"),
                "trim_edges": s("boolean", "同时裁剪片头片尾静音，默认 true"),
            },
        ),
        spec(
            "silence.batch", "无头批量剪气口",
            "对已保存的工程文件执行全工程批量剪气口与无损自检，结果写入新的工程文件（默认 原名_debreath.cutora），"
            "绝不覆盖源工程。",
            H + ":cmd_silence_batch",
            params={
                "project": s("string", "源工程文件 .cutora / .asproj.json"),
                "out": s("string", "输出工程路径；省略为 原名_debreath.cutora"),
                "threshold": s("number", "触发裁剪的最小停顿时长，默认 1.0"),
                "unit": s("string", "停顿时长单位：frames / ms / s / min，默认 s", enum=["frames", "ms", "s", "min"]),
                "keep": s("number", "裁剪后保留的自然间隔，默认 0.25"),
                "keep_unit": s("string", "保留间隔单位：frames / ms / s / min，默认 s", enum=["frames", "ms", "s", "min"]),
                "head_frames": s("integer", "前词词尾保护帧数，默认 2"),
                "tail_frames": s("integer", "后词词首保护帧数，默认 2"),
                "keep_breaths": s("boolean", "保留自然呼吸/气声防误伤，默认 true"),
                "use_vad": s("boolean", "启用 Silero VAD 语音核验，默认 true"),
                "trim_edges": s("boolean", "同时裁剪片头片尾静音，默认 true"),
            },
            required=["project"],
            headless=True,
        ),
        spec(
            "stitch.scan_groups", "智能扫描片段分组",
            "对一个或多个素材文件夹执行智能分组推断（auto 自动识别前缀与序号 / folder 按文件夹分组），"
            "返回每组片段排序、推断依据与置信度。只读，不改动文件。",
            H + ":cmd_stitch_scan_groups",
            params={
                "folders": s("array", "素材文件夹路径列表"),
                "mode": s("string", "auto 自动识别 / folder 按文件夹分组，默认 auto", enum=["auto", "folder"]),
                "group_mark": s("string", "可选自定义分组前缀"),
                "order_mark": s("string", "可选自定义序号前缀"),
            },
            required=["folders"],
            headless=True,
        ),
        spec(
            "stitch.precheck", "拼接前分组与文案体检",
            "检查素材文件夹分组结果与文案列表是否一一对应、是否存在组内序号跳号、缺号或低置信度分组，并输出详细体检报告。只读，不改动文件。",
            H + ":cmd_stitch_precheck",
            params={
                "folders": s("array", "素材文件夹路径列表"),
                "scripts": s("object", "组名到文案文本的映射 {组键: 文案}"),
                "mode": s("string", "auto / folder，默认 auto", enum=["auto", "folder"]),
            },
            required=["folders"],
            headless=True,
        ),
        spec(
            "batch.title_align", "识别并校准口播标题时长",
            "对工程文件或当前打开工程中的任务执行「标题 ↔ 口播」按词对齐与时长校准，返回每条标题的精确起止时间、匹配状态与差异详情。支持无头模式。",
            H + ":cmd_batch_title_align",
            params={
                "project": s("string", "可选工程文件路径；无头模式必填，有界面时省略则操作当前工程"),
                "task_index": s("integer", "任务下标；省略或 -1 表示全部任务"),
                "apply": s("boolean", "是否把识别出的起止时长直接写回标题叠加层，默认 false（只读预览）"),
                "out": s("string", "当提供 project 且 apply=true 时，另存为新工程的路径（可选）"),
            },
            headless=True,
        ),
        spec(
            "studio.overview", "查询工作室全局状态",
            "查询片语 Studio 全套工作台（CUTORA 视频剪辑、Orante 生产工坊、TableTool 表格中枢、Robota 智能体）的模块状态、数据库路径、GPU/NVENC 配置与多语种支持概况。只读。",
            H + ":cmd_studio_overview",
            headless=True,
        ),
        spec(
            "orante.list_tasks", "查询 Orante 生产任务",
            "从 Orante 生产工坊数据库或工作区读取任务列表（含任务编号、口播文案、标题、音频与视频素材就绪状态），支持按任务范围（如 T0137-T0160）筛选。只读。",
            H + ":cmd_orante_list_tasks",
            params={
                "task_range": s("string", "可选任务范围，如 T0137-T0160，留空返回最近任务"),
                "limit": s("integer", "最多返回条数，默认 50，上限 500"),
            },
            headless=True,
        ),
        spec(
            "table.inspect", "读取并检查任务表格",
            "读取并解析片语 Studio 批量任务表格（支持 .tsv / .csv / .txt / .json），返回表头字段、总行数及前 N 行记录，方便 AI 自动提取文案与标题。",
            H + ":cmd_table_inspect",
            params={
                "path": s("string", "表格文件绝对路径"),
                "limit": s("integer", "预览前几行，默认 20，上限 200"),
            },
            required=["path"],
            headless=True,
        ),
        spec(
            "blackbox.status", "查询黑匣子性能建议与事故诊断",
            "运行片语 Studio 黑匣子系统（Flight Recorder & Optimization Advisor），实时检测当前设备硬件瓶颈、自适应运行参数、给开发者的针对性升级建议，以及上次会话是否发生过崩溃/卡死/强制断电的根源定责分析。只读，支持无头模式。",
            H + ":cmd_blackbox_status",
            params={
                "include_win_events": s("boolean", "是否扫描 Windows 系统事件日志（TDR/Kernel-Power 41/OOM），默认 true"),
            },
            headless=True,
        ),
        spec(
            "blackbox.export", "一键导出黑匣子诊断包",
            "生成包含《00_【必看】黑匣子总览与根源诊断报告.md》、硬件自适应与开发者调优建议、断电前未闭合操作轨迹、界面卡顿调用栈与日志的完整黑匣子 ZIP 压缩包。支持无头模式。",
            H + ":cmd_blackbox_export",
            params={
                "dest_dir": s("string", "输出目录路径；省略则默认导出到桌面"),
                "include_win_events": s("boolean", "是否包含 Windows 事件日志分析，默认 true"),
            },
            headless=True,
        ),
    ]
