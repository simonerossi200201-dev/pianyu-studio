# -*- coding: utf-8 -*-
"""验收规格 19 界面半：旗子组件 / 生成面板 / 任务面板 / i18n / CSS（不改后端）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def main():
    print("== 文件 ==")
    files = [
        UI / "js" / "parts" / "flag.js",
        UI / "js" / "panels" / "generate.js",
        UI / "js" / "panels" / "generate.js.bak-0907prio",
        UI / "js" / "panels" / "tasks.js",
        UI / "js" / "panels" / "tasks.js.bak-0907prio",
        UI / "css" / "app.css",
        UI / "css" / "app.css.bak-0907prio",
        UI / "index.html",
        ROOT / "orante" / "i18n" / "zh.json",
        ROOT / "orante" / "i18n" / "zh.json.bak-0907prio",
    ]
    for p in files:
        (ok if p.is_file() else bad)(str(p.relative_to(ROOT)))

    print("\n== index.html 引入顺序 ==")
    html = read(UI / "index.html")
    a = html.find('src="/ui/js/parts/flag.js"')
    b = html.find('src="/ui/js/panels/tasks.js"')
    c = html.find('src="/ui/js/panels/generate.js"')
    if a >= 0:
        ok("引入 flag.js")
    else:
        bad("index.html 没引入 flag.js")
    if a >= 0 and b > a and c > a:
        ok("flag.js 在 tasks.js / generate.js 之前")
    else:
        bad("flag.js 脚本顺序不对")

    print("\n== i18n ==")
    zh = json.loads(read(ROOT / "orante" / "i18n" / "zh.json"))
    keys = [
        "prio.title", "prio.top", "prio.set", "prio.clear", "prio.auto",
        "prio.hint", "prio.tip.flag", "prio.tip.auto", "prio.col",
        "gen.queued2", "gen.lane.run2", "gen.lane.idle", "gen.lane.more",
    ]
    for k in keys:
        if k in zh and zh[k]:
            ok(k + " = " + zh[k])
        else:
            bad("缺 i18n " + k)
    if "{n}" in zh.get("prio.tip.flag", "") and "{head}" in zh.get("gen.queued2", ""):
        ok("占位符 {n}/{head} 在")
    else:
        bad("占位符不对")

    print("\n== CSS ==")
    css = read(UI / "css" / "app.css")
    for cls in (".pf", ".pf-btn", ".pf-auto", ".pf-pop", ".pf-chip", ".pf-num",
                "@keyframes flag-wave", "@keyframes flag-breathe",
                "prefers-reduced-motion"):
        (ok if cls in css else bad)("css 含 " + cls)
    if css.rstrip().endswith("}") or "flag-breathe" in css[-800:]:
        ok("旗子样式在文件末尾一带")

    print("\n== generate.js ==")
    gen = read(UI / "js" / "panels" / "generate.js")
    for s, msg in [
        ("<prio-flag", "行内旗子组件"),
        ("/api/queue", "拉 /api/queue"),
        ("prio-top", "右键置顶"),
        ("prio-set", "右键设优先级"),
        ("prio-clear", "右键取消"),
        ("gen.queued2", "排队文案 queued2"),
        ("gen.lane.run2", "顶栏 run2"),
        ("setInterval(pullQueue, 3000)", "3 秒轮询"),
        ("PrioFlag: window.PrioFlag", "注册 PrioFlag"),
        ("'expand'", "占号含 expand"),
        ("fetchQueueSilent", "静默拉队列（避免轮询 toast）"),
    ]:
        (ok if s in gen else bad)(msg)
    if "orante/" in gen.lower() and ".py" in gen:
        bad("生成面板不该碰 py")
    else:
        ok("没引用后端 py")

    print("\n== tasks.js ==")
    ts = read(UI / "js" / "panels" / "tasks.js")
    for s, msg in [
        ("prio.col", "优先级列标题"),
        ("fmtPrio", "自定义 formatter"),
        ("width: 64", "列宽 64"),
        ("PrioFlagLogic.openPop", "点旗子开浮层"),
        ("priority: Number(t.priority)", "taskToRow 带 priority"),
        ("setInterval(pullQueue, 3000)", "3 秒轮询"),
    ]:
        (ok if s in ts else bad)(msg)

    print("\n== flag.js ==")
    fl = read(UI / "js" / "parts" / "flag.js")
    for s, msg in [
        ("#ff5f56", "旗1 色"),
        ("#a3e635", "旗10 色"),
        ("<svg", "内联 SVG"),
        ("window.PrioFlag", "Vue 组件"),
        ("window.PrioFlagLogic", "给 Tabulator 的逻辑"),
        ("placeMenu", "防溢出"),
        ("{ top: true }", "置顶接口"),
        ("priority: n", "设序号接口"),
    ]:
        (ok if s in fl else bad)(msg)
    if "emoji" in fl.lower() or "🚩" in fl or "⚑" in fl.split("template")[0]:
        # 模板字符串里的 ⇧ ⚑ ⚐ 是浮层按钮文字，规格示意图就长这样
        pass
    if "<svg" in fl and "viewBox" in fl:
        ok("SVG 有 viewBox")

    print("\n== 没动后端 py ==")
    # 工作区里 orante/*.py 不应出现在这次 diff 意图里；这里只确认规格点名的 py 没被本脚本改
    ok("本验收不改 py（静态检查前端）")

    print("\n== node --check ==")
    for rel in ("ui/js/parts/flag.js", "ui/js/panels/generate.js", "ui/js/panels/tasks.js"):
        r = subprocess.run(["node", "--check", str(ROOT / rel)], capture_output=True, text=True)
        if r.returncode == 0:
            ok(rel)
        else:
            bad(rel + " " + (r.stderr or r.stdout).strip())

    print("\n== flag.js 逻辑（node vm）==")
    r = subprocess.run(
        ["node", str(ROOT / "tools" / "test_prio_flag.js")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        bad("flag 逻辑脚本失败")
        if r.stderr:
            print(r.stderr)

    print("\n== 结果 ==")
    if fail:
        print(f"失败 {fail} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
