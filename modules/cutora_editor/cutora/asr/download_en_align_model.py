# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""
下载英文对齐大模型 WAV2VEC2_ASR_LARGE_LV60K_960H (~1.2GB)
下载到 runtime/models/align_wav2vec2/ , 与意大利模型共存。
用法: 双击根目录的 4_Download_EN_Align_Model.bat
"""
import os
import sys

from cutora.core.paths import MODELS
TARGET = os.path.join(MODELS, "align_wav2vec2")

def main():
    os.makedirs(TARGET, exist_ok=True)
    # 确保在线 (主程序是强制离线的, 这里要反过来)
    for v in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ.pop(v, None)

    print("=" * 60)
    print("英文对齐大模型下载器")
    print("模型: WAV2VEC2_ASR_LARGE_LV60K_960H (torchaudio, ~1.2GB)")
    print(f"目标: {TARGET}")
    print("=" * 60)

    try:
        import torchaudio
    except ImportError:
        print("❌ 缺少 torchaudio, 请先运行 1_Install.bat")
        sys.exit(1)

    bundle = torchaudio.pipelines.WAV2VEC2_ASR_LARGE_LV60K_960H
    print("\n⬇️  正在下载/校验 (已存在则跳过, 请耐心等待) ...")
    try:
        bundle.get_model(dl_kwargs={"model_dir": TARGET})
    except Exception as e:
        print(f"\n❌ 下载失败: {e}")
        print("请检查网络后重试 (需要能访问 download.pytorch.org)")
        sys.exit(1)

    print("\n✅ 英文对齐大模型就绪！")
    print("   软件内「脚本 → 对齐/识别语言」选 English 即可使用 (默认已是英语)。")
    print("   意大利模型不受影响, 可随时切回。")

if __name__ == "__main__":
    main()
    input("\n按回车键退出...")
