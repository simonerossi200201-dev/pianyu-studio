# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model file download via cutora.core.net (huggingface.co only)."""
from __future__ import annotations

import os
import threading

from cutora.asr.models import (
    get_model, hf_file_url, iter_files, model_folder,
    sha256_file, verify_weight, weight_path,
)
from cutora.core.commands import CommandError
from cutora.core.log import get_logger
from cutora.core.net import download_to_file

logger = get_logger("cutora.asr.download")

_LOCK = threading.Lock()


def download_hf_file(
    repo: str,
    filename: str,
    dest: str,
    *,
    revision: str = "main",
    expected_sha256: str | None = None,
    progress_cb=None,
    cancel_event=None,
) -> dict:
    url = hf_file_url(repo, filename, revision=revision)
    logger.info("download %s -> %s", url, dest)
    info = download_to_file(url, dest, resume=True, purpose="model",
                            progress_cb=progress_cb, cancel_event=cancel_event)
    if expected_sha256:
        status = verify_weight(dest, expected_sha256)
        if status != "ready":
            try:
                os.remove(dest)
            except OSError:
                pass
            raise CommandError(
                "SHA256 校验失败，已拒绝安装: %s（期望 %s）" % (
                    os.path.basename(dest), expected_sha256))
        info["sha256"] = expected_sha256
    elif os.path.isfile(dest):
        info["sha256"] = sha256_file(dest)
    return info


def _file_progress(progress_cb, base: int, total: int, name: str):
    if progress_cb is None:
        return None

    def cb(written, _tot):
        progress_cb(base + int(written), total, name)
    return cb


def download_model(model_id: str, progress_cb=None, cancel_event=None) -> dict:
    """下载一个登记过的模型（全部文件）。

    progress_cb(done_bytes, total_bytes, filename)：总字节数来自登记表里每个文件的 size；
    cancel_event：threading.Event，置位后在下一个块停下（.part 保留，可续传），抛 NetCancelled。"""
    row = get_model(model_id)
    if row is None:
        raise CommandError("未知模型 %s" % model_id)
    if row.get("local_only"):
        raise CommandError("模型 %s 只能使用本地文件，不能从网络下载" % model_id)
    if row.get("gated") or not row.get("enabled", True):
        raise CommandError("模型 %s 未启用或需授权，不能默认下载（%s）" % (
            model_id, row.get("license")))
    repo = row.get("repo")
    if not repo:
        raise CommandError("模型 %s 没有 HuggingFace 仓库" % model_id)
    folder = model_folder(row)
    os.makedirs(folder, exist_ok=True)
    files = iter_files(row)
    revision = str(row.get("revision") or "")
    if not revision:
        raise CommandError("模型 %s 没有钉死 revision，拒绝下载" % model_id)
    results = []
    if not _LOCK.acquire(blocking=False):
        raise CommandError("已有模型下载在进行（并发 1）")
    try:
        total = 0
        for item in files:
            try:
                total += int(item.get("size") or 0)
            except (TypeError, ValueError):
                pass
        done_before = 0
        for item in files:
            name = str(item["filename"])
            dest = os.path.join(folder, name)
            expect = (item.get("sha256") or None)
            if not expect:
                raise CommandError(
                    "模型 %s 文件 %s 没有钉死 sha256，拒绝下载" % (model_id, name))
            if os.path.isfile(dest) and verify_weight(dest, expect) == "ready":
                # 已经完整且校验通过的文件不重下（断点续传 / 重试）
                done_before += int(item.get("size") or 0)
                if progress_cb is not None:
                    progress_cb(done_before, total, name)
                results.append({"bytes": 0, "resumed": False, "sha256": expect, "skipped": True})
                continue
            results.append(download_hf_file(
                repo, name, dest, revision=revision, expected_sha256=expect,
                progress_cb=_file_progress(progress_cb, done_before, total, name),
                cancel_event=cancel_event))
            done_before += int(item.get("size") or 0)
    finally:
        _LOCK.release()
    return {"id": model_id, "dir": folder, "files": results}


def delete_model(model_id: str) -> dict:
    row = get_model(model_id)
    if row is None:
        raise CommandError("未知模型 %s" % model_id)
    if row.get("bundled"):
        raise CommandError("模型 %s 是内置的，不能删除" % model_id)
    path = weight_path(row)
    folder = os.path.dirname(path)
    removed = []
    if row.get("kind") == "align":
        if os.path.isfile(path):
            os.remove(path)
            removed.append(path)
        return {"id": model_id, "removed": removed}
    import shutil
    if os.path.isdir(folder):
        shutil.rmtree(folder)
        removed.append(folder)
    return {"id": model_id, "removed": removed}
