# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""app.* / project.* / media.* command specs."""
from __future__ import annotations

from cutora.core.cmds.specutil import arr, s, spec

H_LIGHT = "cutora.core.cmds.light"
H_GUI = "cutora.core.cmds.gui"


def build() -> list:
    return [
        spec(
            "app.version", "读取版本",
            "返回当前片语 Studio 的应用名称与版本号，不改动任何工程、文件或设置。"
            "给机器人探测「软件是否就是这一套」用。失败常见原因：安装不完整。",
            H_LIGHT + ":cmd_app_version",
            headless=True,
        ),
        spec(
            "app.gpu_info", "读取 GPU 信息",
            "调用唯一的 GPU 判断入口 detect_gpu_config，返回设备、compute_type 与判断原因。"
            "不改设置。失败常见原因：未安装 PyTorch 或驱动不可用。",
            H_LIGHT + ":cmd_app_gpu_info",
            headless=True,
        ),
        spec(
            "app.free_vram", "读取空闲显存",
            "用 nvidia-smi 读取当前显卡显存总量、已用与空闲（GB），不加载 torch，不改任何状态。"
            "失败常见原因：没有 NVIDIA 驱动或 nvidia-smi 不在 PATH。",
            H_LIGHT + ":cmd_app_free_vram",
            headless=True,
        ),
        spec(
            "app.settings.get", "读取应用设置",
            "读取 app_settings.json 的当前内容。返回前会剥离密钥字段（*_key/*_token/*_secret），"
            "密钥永远不会出现在结果里。失败常见原因：设置文件损坏。",
            H_LIGHT + ":cmd_app_settings_get",
            params={"key": s("string", "只返回这一项；省略则返回全部非密钥设置")},
            headless=True,
        ),
        spec(
            "app.settings.set", "写入应用设置",
            "把一个非密钥设置项写入 app_settings.json。密钥字段会被拒绝，必须走 keyring。"
            "失败常见原因：key 为空、试图写入 *_key/*_token/*_secret。",
            H_LIGHT + ":cmd_app_settings_set",
            params={
                "key": s("string", "设置键名"),
                "value": {"description": "任意 JSON 值"},
            },
            required=["key"],
            headless=True,
        ),
        spec(
            "app.theme.list", "列出主题",
            "列出内置调色板（roma-gold / midnight-blue / amethyst / rose / pine / graphite）"
            "的 id 与中英文名。不切换主题。失败常见原因：调色板 JSON 缺失。",
            H_LIGHT + ":cmd_app_theme_list",
            headless=True,
        ),
        spec(
            "app.theme.set", "切换主题",
            "把当前进程的调色板切到 theme_id，重建 QSS 并写回 app_settings。"
            "未知 id 会回退到 roma-gold。必须在图形界面进程里执行。",
            H_GUI + ":cmd_app_theme_set",
            params={"theme_id": s("string", "内置主题 id")},
            required=["theme_id"],
            undoable=False,
        ),
        spec(
            "project.new", "新建工程",
            "清空当前任务列表、关闭已打开工程路径，按当前画布模板开始一份空白工程。"
            "不弹窗。已有未保存改动会被丢弃，调用方应先自行保存。",
            H_GUI + ":cmd_project_new",
            undoable=True,
        ),
        spec(
            "project.open", "打开工程",
            "从给定的 .asproj.json 路径加载工程到主窗口。命令体不弹文件框。"
            "失败常见原因：路径不存在、版本不支持、JSON 损坏。",
            H_GUI + ":cmd_project_open",
            params={"path": s("string", "工程文件路径")},
            required=["path"],
            undoable=True,
        ),
        spec(
            "project.save", "保存工程",
            "把当前工程写回 project_path。若尚未指定路径则失败，应改用 project.save_as。"
            "命令体不弹成功/失败对话框。失败常见原因：未打开工程、磁盘只读。",
            H_GUI + ":cmd_project_save",
            requires_project=True,
        ),
        spec(
            "project.save_as", "另存工程",
            "把当前工程写到指定路径并记住为当前 project_path。命令体不弹文件框。"
            "失败常见原因：path 为空、目录不可写。",
            H_GUI + ":cmd_project_save_as",
            params={"path": s("string", "目标 .asproj.json 路径")},
            required=["path"],
            requires_project=True,
        ),
        spec(
            "project.close", "关闭工程",
            "清空任务列表并忘掉当前工程路径，界面回到欢迎页（若有）。"
            "不保存；未保存的改动会被丢弃。不弹确认框。",
            H_GUI + ":cmd_project_close",
            undoable=True,
        ),
        spec(
            "project.info", "工程摘要",
            "返回当前工程路径、视频条数、画布尺寸、当前选中下标，不改动任何状态。"
            "没有工程时仍返回空摘要而不是报错，方便机器人探活。",
            H_GUI + ":cmd_project_info",
        ),
        spec(
            "project.pack", "打包工程",
            "把当前工程用到的全部素材拷到 out_dir/media/，路径改成相对工程文件，写成 project.cutora。"
            "mode=copy 默认；move 会搬走原文件。含绝对路径，不进配方。"
            "失败常见原因：out_dir 为空。收不到的素材列在 missing。",
            H_GUI + ":cmd_project_pack",
            params={
                "out_dir": s("string", "打包输出目录"),
                "mode": s("string", "copy 或 move，默认 copy"),
            },
            required=["out_dir"],
            requires_project=True,
        ),
        spec(
            "project.autosave.status", "自动保存状态",
            "返回自动保存间隔、是否有残留的 .autosave.json（上次未正常退出）。不改文件。",
            H_GUI + ":cmd_project_autosave_status",
            headless=True,
        ),
        spec(
            "project.autosave.restore", "恢复自动保存",
            "把残留的自动保存加载进当前窗口，不自动覆盖工程文件。用户保存才写回。"
            "失败常见原因：没有残留文件。",
            H_GUI + ":cmd_project_autosave_restore",
            params={"path": s("string", "sidecar 路径；省略则用当前工程旁的残留")},
            undoable=True,
        ),
        spec(
            "media.probe", "检查素材",
            "用本机 ffprobe 读一条视频的时长。不是视频或时长接近 0 时失败。"
            "不改工程、不渲染。给配方在导入前过滤坏文件。失败常见原因：路径不存在、假 mp4。",
            H_LIGHT + ":cmd_media_probe",
            params={"path": s("string", "视频路径")},
            required=["path"],
            headless=True,
            portable=True,
        ),
        spec(
            "media.import", "导入媒体",
            "把一组视频路径导入当前工程（等价拖放），返回新条目的 media_ids（列表下标）。"
            "命令体不弹文件框。失败常见原因：paths 为空、文件不是可识别视频。",
            H_GUI + ":cmd_media_import",
            params={"paths": arr("string", "视频或目录路径")},
            required=["paths"],
            undoable=True,
            portable=True,
        ),
        spec(
            "media.remove", "移除媒体",
            "按 media_ids（列表下标）从当前工程删除视频条目，不可恢复，除非随后撤销。"
            "命令体不弹确认框。失败常见原因：id 越界、尚未导入视频。",
            H_GUI + ":cmd_media_remove",
            params={"ids": arr("integer", "要删除的媒体下标")},
            required=["ids"],
            undoable=True,
            dangerous=True,
            requires_project=True,
        ),
        spec(
            "media.list", "列出媒体",
            "列出当前工程里每一条视频的 id、文件名、路径与时长，不改动状态。"
            "空工程返回空数组。给机器人确认导入是否成功用。",
            H_GUI + ":cmd_media_list",
        ),
        spec(
            "media.info", "媒体详情",
            "返回指定 id 那一条视频的路径、文案长度、字幕段数、画布尺寸。"
            "失败常见原因：id 越界或工程为空。",
            H_GUI + ":cmd_media_info",
            params={"id": s("integer", "媒体下标")},
            required=["id"],
            requires_project=True,
        ),
        spec(
            "media.relink", "重链一条素材",
            "把工程里 source_id 对应的文件改到 new_path，并重新探测离线/时长。"
            "含绝对路径，不进配方。失败常见原因：新路径不是文件、id 对不上。",
            H_GUI + ":cmd_media_relink",
            params={
                "source_id": s("string", "时间线 sources 的 id，或 video"),
                "new_path": s("string", "新文件的绝对路径"),
            },
            required=["source_id", "new_path"],
            undoable=True,
            requires_project=True,
        ),
        spec(
            "media.relink_folder", "整批换素材根目录",
            "把工程里所有以 old_root 开头的素材路径改成 new_root 下的对应文件。"
            "用户把 D:\\素材\\2026 挪到 E:\\素材\\2026 时用这一条。含绝对路径，不进配方。"
            "失败常见原因：新根目录不存在。",
            H_GUI + ":cmd_media_relink_folder",
            params={
                "old_root": s("string", "原来的素材根目录"),
                "new_root": s("string", "新的素材根目录"),
            },
            required=["old_root", "new_root"],
            undoable=True,
            requires_project=True,
        ),
        spec(
            "media.select", "选中媒体",
            "把资产库选中项设为 ids 数组（多选），并让第一个 id 成为当前编辑条目。"
            "失败常见原因：id 越界。不改时间轴内容。",
            H_GUI + ":cmd_media_select",
            params={"ids": arr("integer", "要选中的媒体下标")},
            required=["ids"],
            requires_project=True,
        ),
        spec(
            "recipe.record_start", "开始录制配方",
            "开始记录来源为 ui、可撤销、且 portable=True 的命令。"
            "含下标或媒体绑定的操作不录，状态栏会提示。"
            "媒体路径会换成 {media}，输出目录换成 {out_dir}。失败常见原因：总线未初始化。",
            H_LIGHT + ":cmd_recipe_record_start",
            headless=True,
        ),
        spec(
            "recipe.record_stop", "停止录制配方",
            "结束录制并返回已记下的步骤列表，供保存为 .cutorarecipe。"
            "不写盘。失败常见原因：尚未开始录制（此时返回空列表）。",
            H_LIGHT + ":cmd_recipe_record_stop",
            headless=True,
        ),
        spec(
            "recipe.apply_folder", "套用配方到文件夹",
            "对文件夹里每个视频按配方逐步执行。subtitle.auto 串行占 GPU；"
            "失败时按该步 on_error=stop|skip 处理。写出 _cutora_batch_report.json。"
            "失败常见原因：配方不合法、目录没有视频。",
            H_LIGHT + ":cmd_recipe_apply_folder",
            params={
                "recipe": s("string", "配方文件路径"),
                "folder": s("string", "素材文件夹"),
                "out_dir": s("string", "输出目录"),
                "jobs": s("integer", "并行数，默认 min(2, cpu//4)"),
                "recursive": s("boolean", "是否递归子目录"),
            },
            required=["recipe", "folder", "out_dir"],
            headless=True,
            async_job=True,
        ),
    ]
