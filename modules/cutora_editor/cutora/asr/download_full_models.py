# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import json

# === 依赖检查 ===
try:
    from huggingface_hub import snapshot_download
except ImportError:
    print("❌ [错误] 缺少 huggingface_hub 库")
    print("👉 请在命令行运行: pip install huggingface_hub")
    sys.exit(1)

# === 强制在线模式 ===
# 清除可能存在的离线环境变量，确保能联网
if "HF_HUB_OFFLINE" in os.environ: del os.environ["HF_HUB_OFFLINE"]
if "TRANSFORMERS_OFFLINE" in os.environ: del os.environ["TRANSFORMERS_OFFLINE"]

# === 路径配置 ===
from cutora.core.paths import MODELS as MODELS_ROOT

# 定义模型保存的具体路径 (使用简单易读的文件夹名，避免哈希乱码)
PATHS = {
    "asr": os.path.join(MODELS_ROOT, "asr_small"),
    "align": os.path.join(MODELS_ROOT, "align_wav2vec2"),
    "vad": os.path.join(MODELS_ROOT, "vad_segmentation")
}

print("="*60)
print("CUTORA 全量模型下载器")
print(f"📂 模型根目录: {MODELS_ROOT}")
print("="*60)

def download_safely(repo_id, local_dir, patterns, name):
    print(f"\n⬇️  正在下载 [{name}]...")
    print(f"   源地址: {repo_id}")
    print(f"   目标径: {local_dir}")
    
    if not os.path.exists(local_dir):
        os.makedirs(local_dir)

    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=local_dir,
            local_dir_use_symlinks=False, # 关键：禁用软链接，防止 Windows 权限报错
            allow_patterns=patterns,
            resume_download=True
        )
        print(f"✅ [{name}] 下载成功！")
        return True
    except Exception as e:
        error_str = str(e)
        if "401" in error_str or "403" in error_str:
            print(f"⚠️ [{name}] 需要权限/Token (通常是 VAD 模型)，已跳过。")
            print("   (这不影响基础字幕生成，只影响说话人区分)")
            return "SKIPPED"
        else:
            print(f"❌ [{name}] 下载失败: {e}")
            return False

def main():
    if not os.path.exists(MODELS_ROOT):
        os.makedirs(MODELS_ROOT)

    results = {}

    # 1. ASR 核心模型 (Whisper Small)
    # 这是生成字幕必须的
    res_asr = download_safely(
        "guillaumekln/faster-whisper-small",
        PATHS["asr"],
        ["config.json", "model.bin", "vocabulary.txt", "tokenizer.json", "preprocessor_config.json"],
        "Whisper ASR"
    )
    results["asr"] = res_asr

    # 2. 对齐模型 (Wav2Vec2)
    # 这是时间轴对齐必须的
    res_align = download_safely(
        "facebook/wav2vec2-large-xlsr-53",
        PATHS["align"],
        ["config.json", "pytorch_model.bin", "preprocessor_config.json", "vocab.json"],
        "对齐模型"
    )
    results["align"] = res_align

    # 3. VAD 模型 (说话人分离)
    # 尝试下载，如果没权限就跳过
    res_vad = download_safely(
        "pyannote/segmentation-3.0",
        PATHS["vad"],
        ["config.yaml", "pytorch_model.bin"],
        "VAD 模型"
    )
    results["vad"] = res_vad

    # === 生成路径配置文件 ===
    # 主程序将读取这个文件来定位模型，彻底解决路径不一致问题
    config_data = {
        "asr_path": PATHS["asr"] if results["asr"] is True else None,
        "align_path": PATHS["align"] if results["align"] is True else None,
        "vad_path": PATHS["vad"] if results["vad"] is True else None
    }
    
    config_path = os.path.join(MODELS_ROOT, "model_paths.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4)

    print("\n" + "="*60)
    if results["asr"] is True and results["align"] is True:
        print("🎉 核心模型下载成功！")
        print(f"📄 路径配置已保存至: {config_path}")
        print("👉 模型路径已配置完毕，主程序会自动读取。")
    else:
        print("❌ 核心模型下载失败，请检查网络 (需要能访问 Hugging Face)。")
    print("="*60)

if __name__ == "__main__":
    main()
    input("按回车键退出...")
