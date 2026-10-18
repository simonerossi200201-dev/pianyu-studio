# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""asr.* command specs (T12a: detect_language; later slices add models.*)."""
from __future__ import annotations

from cutora.core.cmds.specutil import arr, s, spec

H = "cutora.core.cmds.asr_ops"


def build() -> list:
    return [
        spec(
            "asr.detect_language", "检测语言",
            "用已安装的识别模型听音频或视频的前 30 秒，返回语言代码、置信度、"
            "是否低于 0.6、是否实验性语言，以及给界面用的低置信提示文案。"
            "不写工程、不改设置。失败常见原因：media 路径无效、识别模型未安装。",
            H + ":cmd_asr_detect_language",
            params={
                "media": s("string", "音频或视频文件路径"),
                "media_id": s("integer", "当前工程媒体下标；有窗口且未给 media 时用"),
            },
            headless=True,
            async_job=True,
            portable=True,
        ),
        spec(
            "asr.models.list", "列出模型",
            "列出识别、对齐、VAD 模型的 id、仓库、许可证、大小、本地状态"
            "（missing / ready / checksum_failed）以及是否默认启用。"
            "只读，不联网。失败常见原因：models.json 缺失。",
            H + ":cmd_asr_models_list",
            headless=True,
        ),
        spec(
            "asr.models.download", "下载模型",
            "从 huggingface.co 下载指定 id 的模型文件到本地模型目录，走 net.py 白名单，"
            "SHA256 校验，断点续传，并发 1。忽略非官方 HF_ENDPOINT。"
            "gated / 未启用的默认拒绝。不静默下载。"
            "失败常见原因：id 未知、未启用、网络被拒绝。",
            H + ":cmd_asr_models_download",
            params={"id": s("string", "模型 id，如 asr-small / asr-large-v3-turbo")},
            required=["id"],
            headless=True,
            async_job=True,
        ),
        spec(
            "glossary.get", "读取术语表",
            "读取全局或工程级术语表（人名、地名、经文、专业词，一行一个）。"
            "默认 scope=global，读 USER_DATA/presets/glossary.txt，可无头。"
            "scope=project 读当前工程；无窗口时返回 needs_project。"
            "不改识别、不联网。失败常见原因：工程未打开。",
            H + ":cmd_glossary_get",
            params={
                "scope": s("string", "global 或 project，默认 global"),
            },
            headless=True,
        ),
        spec(
            "glossary.set", "写入术语表",
            "写入全局或工程级术语表。text 或 terms 均可，一行一个，空行忽略。"
            "默认 scope=global，写 USER_DATA/presets/glossary.txt，可无头。"
            "scope=project 写进当前项目；无窗口时返回 needs_project。"
            "下次自动字幕会拼进 initial_prompt 与 hotwords。失败常见原因：工程未打开。",
            H + ":cmd_glossary_set",
            params={
                "scope": s("string", "global 或 project，默认 global"),
                "text": s("string", "整段文本，按行切开"),
                "terms": arr("string", "术语数组，与 text 二选一"),
            },
            headless=True,
        ),
        spec(
            "asr.models.delete", "删除模型",
            "删除指定 id 的本地模型文件。不可恢复。API/MCP 必须带 confirm=true。"
            "对齐 en/it 只删对应权重文件，不拆公共目录里的另一份。"
            "失败常见原因：id 未知、文件正在使用。",
            H + ":cmd_asr_models_delete",
            params={"id": s("string", "模型 id")},
            required=["id"],
            headless=True,
            dangerous=True,
        ),
    ]
