# -*- coding: utf-8 -*-
"""从老流水线抽出四块默认提示词，写成 orante/legacy/prompt_defaults.json。

分发版只带这份 JSON，不再依赖「下载图片」目录。
开发机上可以再跑一遍：  python tools/extract_prompt_defaults.py
会从 paths.find_legacy_root() 重新抽，并顺带把 flow_shots.py / flow_shots.json
原样拷进 orante/legacy/（build_prompt 还要用卡库里没被抽出来的键）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante import paths  # noqa: E402

CAMERA_KEYS = [
    "安静运镜_多样",
    "安静运镜_多样_睁眼",
    "人物坐姿_安静",
    "人物原地_安静",
    "镜头禁令_点名版",
    "运动强制_安静",
    "人群保留_安静",
    "空镜卡池",
    "画外音指令",
]

# 四条正则旁边的源码注释（第三条源码没单独写注释，按规则自己定）
PAUSE_NOTES = [
    "「停顿→几秒」和「几秒→停顿」两种语序都收，但限制在同一句之内（中间不许跨过句号或换行），免得把隔壁句子的秒数算到这句头上。",
    "「停顿→几秒」和「几秒→停顿」两种语序都收，但限制在同一句之内（中间不许跨过句号或换行），免得把隔壁句子的秒数算到这句头上。",
    "中文：停顿/停留/等待/暂停/静默/沉默后紧跟秒或分钟。（源码这条没有独立注释）",
    '"Pause for a moment" —— 实际文案里和"pause for seven seconds"一样常见，模型照样会真的停住。但必须卡死【动词 + for】这个形状：文案里还有大量 "If you have a moment, share this message"，那是"有空的话"，不是让人停下来，误配进去就会凭空多出一句台词。',
]
PAUSE_LANGS = ["en", "en", "zh", "en"]


def _sha1(p: Path) -> str:
    h = hashlib.sha1()
    h.update(p.read_bytes())
    return h.hexdigest()


def _load_module(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    if spec is None or spec.loader is None:
        raise SystemExit(f"FAIL：没法加载 {file}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _extract_default_prompt(html: str) -> str:
    m = re.search(r"const DEFAULT_PROMPT = `([\s\S]*?)`;", html)
    if not m:
        raise SystemExit("FAIL：描述语生成器.html 里找不到 const DEFAULT_PROMPT = `...`")
    return m.group(1)


def _copy_if_needed(src: Path, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and _sha1(src) == _sha1(dest):
        return f"已是最新 {dest.name}  sha1={_sha1(dest)[:12]}"
    shutil.copy2(src, dest)
    return f"已拷贝 {src.name} → {dest}  sha1={_sha1(dest)[:12]}"


def extract(pipe: Path) -> dict:
    html_path = pipe / "描述语生成器.html"
    shots_json = pipe / "core" / "flow_shots.json"
    shots_py = pipe / "core" / "flow_shots.py"
    for p in (html_path, shots_json, shots_py):
        if not p.is_file():
            raise SystemExit(f"FAIL：找不到 {p}")

    desc = _extract_default_prompt(html_path.read_text(encoding="utf-8"))
    if not desc.startswith("【提示词版本 v7"):
        raise SystemExit(f"FAIL：DEFAULT_PROMPT 不是 v7，开头是 {desc[:40]!r}")

    shots = json.loads(shots_json.read_text(encoding="utf-8"))
    camera = {}
    for k in CAMERA_KEYS:
        if k not in shots:
            raise SystemExit(f"FAIL：flow_shots.json 没有键 {k}")
        camera[k] = shots[k]

    raw_tail = shots.get("固定结语")
    if not isinstance(raw_tail, dict):
        raise SystemExit("FAIL：flow_shots.json 没有「固定结语」对象")
    tail = {k: v for k, v in raw_tail.items() if not str(k).startswith("_")}
    tail_notes = {k: v for k, v in raw_tail.items() if str(k).startswith("_")}

    fs = _load_module("_orante_flow_shots_src", shots_py)
    regs = getattr(fs, "_PAUSE_RES", None)
    if not regs or len(regs) != 4:
        raise SystemExit(f"FAIL：_PAUSE_RES 不是 4 条，实际 {0 if not regs else len(regs)}")
    patterns = []
    for i, rgx in enumerate(regs):
        patterns.append({
            "kind": "regex",
            "value": rgx.pattern,
            "lang": PAUSE_LANGS[i],
            "note": PAUSE_NOTES[i],
            "source": "default",
        })
    template = tail.get("停顿强调") or "包括{原话}这句话也要读"

    vm = re.search(r"【提示词版本\s*([^】]+)】", desc)
    versions = {
        "desc": vm.group(1).strip() if vm else "v7",
        "camera": "flow_shots",
        "tail": "flow_shots",
        "problem": "pause-res-4",
    }
    return {
        "desc": desc,
        "camera": camera,
        "tail": tail,
        "tail_notes": tail_notes,
        "problem": {"patterns": patterns, "template": template},
        "versions": versions,
    }


def main() -> int:
    pipe = paths.find_legacy_root()
    if not pipe:
        raise SystemExit("FAIL：找不到流水线目录（下载图片\\），没法抽取")
    print(f"流水线：{pipe}")

    shots_py = pipe / "core" / "flow_shots.py"
    shots_json = pipe / "core" / "flow_shots.json"
    print(_copy_if_needed(shots_py, paths.LEGACY / "flow_shots.py"))
    print(_copy_if_needed(shots_json, paths.LEGACY / "flow_shots.json"))

    data = extract(pipe)
    out = paths.LEGACY / "prompt_defaults.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写 {out}")
    print(f"  desc     {len(data['desc'])} 字，版本 {data['versions']['desc']}")
    print(f"  camera   {len(data['camera'])} 键：{', '.join(data['camera'])}")
    print(f"  tail     {len(data['tail'])} 键（说明 {len(data['tail_notes'])} 条）")
    print(f"  problem  {len(data['problem']['patterns'])} 条正则")
    print(f"  开头     {data['desc'][:24]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
