# 模型许可证白名单

判据：**允许商用且可再分发**。全仓库只此一份常量：`cutora.core.licenses.ALLOWED_MODEL_LICENSES`（MIT / Apache-2.0 / BSD-2-Clause / BSD-3-Clause / ISC / CC0-1.0 / CC-BY-4.0 / Unlicense）。以后加模型对照 HuggingFace 卡片，不在表内、为空、为 `other`、或含 `NC`/`ND` 的默认不启用。

## Demucs htdemucs（Q209）

人声 / 伴奏分离使用 Demucs 的 `htdemucs` 权重，许可证 MIT（在白名单内）。权重不入库，放在 `runtime/models/demucs/`。文件名以打包脚本锁定的为准，不写死某一个检查点文件名。哈希在权重打进离线包时再钉。界面进程不加载这个模型，只由 `cutora.audio.stem_worker` 子进程调用。界面文案是「只留说话」「只留伴奏」。
