# -*- coding: utf-8 -*-
"""
diagnose_packaging.py — PyInstaller 打包前置诊断
================================================
目的：在浪费时间打包之前，先查清 PyInstaller 会漏掉什么。

背景：AutoSub 用了两类 PyInstaller 静态分析看不见的东西：
  1. 字符串形式的动态导入
       _LazyModule -> importlib.import_module("torch") / ("whisperx")
       self_check_dependencies() -> __import__("yaml") 等
     PyInstaller 扫 AST 找 `import xxx` 语句，字符串它读不到，
     结果就是 torch / whisperx 根本不进包，运行时 ModuleNotFoundError。
  2. 运行时才读的非 Python 文件
       ctranslate2 / onnxruntime 的 DLL
       speechbrain / pyannote 的 hyperparams.yaml
       faster-whisper 的 tokenizer 资源
       torch\\lib\\*.dll（CUDA，数 GB）

用法（必须在 venv 里跑）：
    cd core
    call venv_autosub\\Scripts\\activate
    python ..\\packaging\\diagnose_packaging.py

输出：
    packaging\\diagnose_report.txt       人读的报告
    packaging\\generated_hiddenimports.py  可直接 import 进 .spec 的清单
"""

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
CORE = PROJECT / "core"

# 要扫描的源文件（项目自己的代码，不含 _archive / _backup / venv）
SOURCE_FILES = [
    "AutoSub_debug.py",
    "script_stitcher.py",
    "anim_engine.py",
    "davinci_panels.py",
    "preset_manager.py",
    "config.py",
    "timeline.py",
    "timeline_model.py",
    "waveform.py",
    "audio_spectrum.py",
    "storage.py",
    "utils.py",
    "debug_print.py",
]

# 已知需要完整收集的包。value = 为什么
CRITICAL_PACKAGES = {
    "torch":                "CUDA DLL 在 torch/lib/，PyInstaller 常漏；且本项目经 _LazyModule 动态导入",
    "torchaudio":           "对齐模型 wav2vec2 由 torchaudio 提供，含 C 扩展",
    "torchvision":          "torch 生态依赖",
    "whisperx":             "经 _LazyModule 动态导入，PyInstaller 完全看不见",
    "faster_whisper":       "含 tokenizer 资源与 assets",
    "ctranslate2":          "纯 C++ 后端，一堆 DLL",
    "speechbrain":          "运行时读 hyperparams.yaml / 预训练接口",
    "pyannote":             "VAD/分离模型，运行时读 yaml 配置",
    "onnxruntime":          "VAD provider DLL",
    "transformers":         "被 __import__ 字符串导入；含大量数据文件",
    "tokenizers":           "被 __import__ 字符串导入；Rust 扩展",
    "sentencepiece":        "C 扩展",
    "lightning_fabric":     "speechbrain/pyannote 依赖链，常漏 version.info",
    "pytorch_lightning":    "同上",
    "asteroid_filterbanks": "pyannote 依赖",
    "antlr4":               "omegaconf/hydra 依赖链",
    "av":                   "PyAV，含 FFmpeg 动态库",
    "soundfile":            "libsndfile DLL",
    "cv2":                  "OpenCV，含大量 DLL",
    "yaml":                 "被 __import__ 字符串导入",
    "pandas":               "被 __import__ 字符串导入",
    "numpy":                "科学计算基础",
    "scipy":                "含 Fortran/C 扩展",
    "nltk":                 "whisperx 可能间接依赖",
    "huggingface_hub":      "模型加载路径解析",
}

# 运行时才读的数据文件后缀（PyInstaller 不会自动带）
DATA_SUFFIXES = {".yaml", ".yml", ".json", ".txt", ".bin", ".model",
                 ".onnx", ".cfg", ".ini", ".info", ".pt", ".pth"}
BINARY_SUFFIXES = {".dll", ".so", ".pyd", ".dylib"}


# ────────────────────────────────────────────────────────────────
# 1. 扫源码，找 PyInstaller 看不见的动态导入
# ────────────────────────────────────────────────────────────────
def find_dynamic_imports():
    """返回 [(文件, 行号, 代码片段, 被导入的模块名或 None)]"""
    findings = []
    for name in SOURCE_FILES:
        path = CORE / name
        if not path.exists():
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src, filename=str(path))
        except SyntaxError as e:
            findings.append((name, e.lineno or 0, f"<解析失败: {e}>", None))
            continue

        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            target = None

            # importlib.import_module("xxx")
            if isinstance(func, ast.Attribute) and func.attr == "import_module":
                target = _literal_or_none(node.args[0]) if node.args else None
            # __import__("xxx")
            elif isinstance(func, ast.Name) and func.id == "__import__":
                target = _literal_or_none(node.args[0]) if node.args else None
            else:
                continue

            ln = node.lineno
            snippet = lines[ln - 1].strip() if 0 < ln <= len(lines) else ""
            findings.append((name, ln, snippet, target))
    return findings


def _literal_or_none(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None  # 变量传入，静态推不出来


# ────────────────────────────────────────────────────────────────
# 2. 扫 _LazyModule 的赋值，拿到真实模块名
# ────────────────────────────────────────────────────────────────
def find_lazy_modules():
    """找 xxx = _LazyModule("yyy") 形式，返回 {变量名: 模块名}"""
    out = {}
    for name in SOURCE_FILES:
        path = CORE / name
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            call = node.value
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "_LazyModule"
                    and call.args):
                mod = _literal_or_none(call.args[0])
                if mod and node.targets and isinstance(node.targets[0], ast.Name):
                    out[node.targets[0].id] = mod
    return out


# ────────────────────────────────────────────────────────────────
# 3. 检查每个关键包：装没装、在哪、有多少非 .py 文件
# ────────────────────────────────────────────────────────────────
def probe_packages():
    results = {}
    for pkg, reason in CRITICAL_PACKAGES.items():
        info = {"reason": reason, "installed": False, "path": None,
                "data_files": 0, "binaries": 0, "size_mb": 0.0,
                "sample_data": [], "sample_bin": []}
        try:
            spec = importlib.util.find_spec(pkg)
        except (ImportError, ValueError, ModuleNotFoundError, AttributeError):
            spec = None

        if spec is None:
            results[pkg] = info
            continue

        info["installed"] = True
        origin = spec.origin
        if spec.submodule_search_locations:
            root = Path(list(spec.submodule_search_locations)[0])
        elif origin and origin != "built-in":
            root = Path(origin).parent
        else:
            results[pkg] = info
            continue

        info["path"] = str(root)
        total = 0
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                total += p.stat().st_size
            except OSError:
                pass
            sfx = p.suffix.lower()
            if sfx in BINARY_SUFFIXES:
                info["binaries"] += 1
                if len(info["sample_bin"]) < 5:
                    info["sample_bin"].append(p.relative_to(root).as_posix())
            elif sfx in DATA_SUFFIXES:
                info["data_files"] += 1
                if len(info["sample_data"]) < 5:
                    info["sample_data"].append(p.relative_to(root).as_posix())
        info["size_mb"] = round(total / 1024 / 1024, 1)
        results[pkg] = info
    return results


# ────────────────────────────────────────────────────────────────
# 4. 环境快照
# ────────────────────────────────────────────────────────────────
def env_snapshot():
    snap = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "in_venv": hasattr(sys, "real_prefix") or sys.prefix != sys.base_prefix,
        "platform": sys.platform,
    }
    try:
        import torch  # noqa
        snap["torch"] = torch.__version__
        snap["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            snap["gpu"] = torch.cuda.get_device_name(0)
            cc = torch.cuda.get_device_capability(0)
            snap["compute_capability"] = f"sm_{cc[0]}{cc[1]}"
    except Exception as e:
        snap["torch"] = f"<导入失败: {e}>"
    try:
        import PyInstaller  # noqa
        snap["pyinstaller"] = PyInstaller.__version__
    except Exception:
        snap["pyinstaller"] = "<未安装 —— pip install pyinstaller>"
    return snap


# ────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────
def main():
    HERE.mkdir(parents=True, exist_ok=True)
    lines = []
    W = lines.append

    W("=" * 72)
    W("AutoSub 打包诊断报告")
    W("=" * 72)
    W("")

    # -- 环境 --
    snap = env_snapshot()
    W("【环境】")
    for k, v in snap.items():
        W(f"  {k:20} = {v}")
    if not snap["in_venv"]:
        W("")
        W("  ⚠ 警告：当前不在 venv 里！结果不可信。")
        W("    请先 call core\\venv_autosub\\Scripts\\activate")
    W("")

    # -- 动态导入 --
    dyn = find_dynamic_imports()
    lazy = find_lazy_modules()
    W("【PyInstaller 看不见的动态导入】")
    W("  这些是最可能导致 'whisper 失效' 的元凶。")
    W("")
    if lazy:
        W("  _LazyModule 代理：")
        for var, mod in sorted(lazy.items()):
            W(f"    {var:12} -> importlib.import_module({mod!r})   ← 必须写进 hiddenimports")
        W("")
    if dyn:
        W("  字符串动态导入调用点：")
        for f, ln, snippet, target in dyn:
            tag = f"-> {target!r}" if target else "-> <变量，静态推不出>"
            W(f"    {f}:{ln}  {tag}")
            W(f"        {snippet}")
        W("")
    else:
        W("  (未发现)")
        W("")

    # -- 包探测 --
    pkgs = probe_packages()
    missing = [k for k, v in pkgs.items() if not v["installed"]]
    W("【关键包状态】")
    W("")
    W(f"  {'包名':<22}{'状态':<8}{'体积MB':>9}{'DLL':>7}{'数据':>7}")
    W("  " + "-" * 60)
    for pkg, info in pkgs.items():
        status = "已装" if info["installed"] else "未装"
        W(f"  {pkg:<22}{status:<8}{info['size_mb']:>9}{info['binaries']:>7}{info['data_files']:>7}")
    W("")

    if missing:
        W("  未安装（如果代码实际不用，可从 CRITICAL_PACKAGES 里删掉）：")
        for m in missing:
            W(f"    ✘ {m}")
        W("")

    W("【需要 collect_all 的包 —— 含 DLL 或运行时数据文件】")
    W("")
    need_collect = []
    for pkg, info in pkgs.items():
        if not info["installed"]:
            continue
        if info["binaries"] > 0 or info["data_files"] > 0:
            need_collect.append(pkg)
            W(f"  ● {pkg}   ({info['binaries']} DLL / {info['data_files']} 数据文件, {info['size_mb']} MB)")
            W(f"      原因: {info['reason']}")
            if info["sample_bin"]:
                W(f"      DLL 例: {', '.join(info['sample_bin'])}")
            if info["sample_data"]:
                W(f"      数据例: {', '.join(info['sample_data'])}")
            W("")

    total_mb = sum(v["size_mb"] for v in pkgs.values() if v["installed"])
    W(f"  依赖总体积约 {total_mb:.0f} MB（打包产物会接近这个量级，还要加 3.2GB 模型）")
    W("")

    # -- 结论 --
    W("=" * 72)
    W("【结论】")
    W("=" * 72)
    hidden = sorted(set(list(lazy.values())
                        + [t for *_, t in dyn if t]
                        + [p for p in need_collect]))
    W("")
    W(f"  hiddenimports 需要 {len(hidden)} 项")
    W(f"  collect_all   需要 {len(need_collect)} 项")
    W("")
    W("  下一步：把 generated_hiddenimports.py 交给 .spec 使用。")
    W("")

    report = "\n".join(lines)
    (HERE / "diagnose_report.txt").write_text(report, encoding="utf-8")
    print(report)

    # -- 生成给 .spec 用的清单 --
    gen = [
        "# -*- coding: utf-8 -*-",
        "# 由 diagnose_packaging.py 自动生成，不要手改。",
        "# 重新生成： python packaging/diagnose_packaging.py",
        "",
        "HIDDEN_IMPORTS = [",
    ]
    for h in hidden:
        gen.append(f"    {h!r},")
    gen += ["]", "", "COLLECT_ALL = ["]
    for c in sorted(need_collect):
        gen.append(f"    {c!r},")
    gen += ["]", ""]
    (HERE / "generated_hiddenimports.py").write_text("\n".join(gen), encoding="utf-8")

    # -- 机器可读版，方便后续脚本消费 --
    (HERE / "diagnose_report.json").write_text(
        json.dumps({"env": snap, "lazy": lazy,
                    "dynamic_imports": [
                        {"file": f, "line": ln, "code": s, "target": t}
                        for f, ln, s, t in dyn],
                    "packages": pkgs}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print()
    print(f"报告已写入: {HERE / 'diagnose_report.txt'}")
    print(f"清单已写入: {HERE / 'generated_hiddenimports.py'}")
    print(f"JSON  已写入: {HERE / 'diagnose_report.json'}")


if __name__ == "__main__":
    main()
