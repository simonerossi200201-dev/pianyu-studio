# -*- coding: utf-8 -*-
"""验收规格 16：统一任务选择器的分组 / 跳转 / 接入钩子。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
PANELS = UI / "js" / "panels"
PARTS = UI / "js" / "parts"
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
    for p in (
        PARTS / "taskpick.js",
        PANELS / "gemini.js",
        PANELS / "track.js",
        PANELS / "review.js",
        PANELS / "gemini.js.bak-0906c",
        PANELS / "track.js.bak-0906c",
        PANELS / "review.js.bak-0906c",
        UI / "css" / "app.css",
        UI / "index.html",
        ROOT / "orante" / "i18n" / "zh.json",
    ):
        (ok if p.is_file() else bad)(str(p.relative_to(ROOT)))

    html = read(UI / "index.html")
    if 'src="/ui/js/parts/taskpick.js"' in html:
        ok("index.html 引入 taskpick.js")
    else:
        bad("index.html 没有 taskpick.js")
    idx = html.find("/ui/js/parts/taskpick.js")
    gidx = html.find("/ui/js/panels/gemini.js")
    if idx >= 0 and gidx >= 0 and idx < gidx:
        ok("taskpick.js 在三个面板之前加载")
    else:
        bad("script 顺序不对，TaskPick 还没定义")

    print("== i18n ==")
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    keys = [
        "taskpick.placeholder", "taskpick.ready.prepared", "taskpick.ready.described",
        "taskpick.ready.generating", "taskpick.notyet", "taskpick.count",
        "taskpick.empty.prepared", "taskpick.empty.described", "taskpick.empty.generating",
        "taskpick.hint.draft", "taskpick.hint.prepared", "taskpick.hint.described",
        "taskpick.hint.arranged", "taskpick.search", "taskpick.nomatch", "taskpick.go",
        "tasks.status.draft", "tasks.status.prepared", "tasks.status.finished",
    ]
    for k in keys:
        (ok if k in zh else bad)(k)
    if zh.get("taskpick.count") == "可用 {a} / 共 {b}":
        ok("计数文案")
    else:
        bad("计数文案不是「可用 {a} / 共 {b}」")
    if "先去多模态写描述语" in zh.get("taskpick.hint.prepared", ""):
        ok("prepared 提示")
    else:
        bad("prepared 提示文案")

    print("== 面板接入 ==")
    gem = read(PANELS / "gemini.js")
    trk = read(PANELS / "track.js")
    rev = read(PANELS / "review.js")
    if "<task-pick" in gem and 'min-status="prepared"' in gem:
        ok("gemini 用 task-pick prepared")
    else:
        bad("gemini 没换成 task-pick prepared")
    if "<select v-model=\"tid\">" in gem:
        bad("gemini 还留着任务 select")
    else:
        ok("gemini 任务 select 已撤")
    if "readyTasks" in gem and "takePendingTid(tid, 'gemini')" in gem:
        ok("gemini 保留 readyTasks + 接 pendingTid")
    else:
        bad("gemini readyTasks / pendingTid")

    if "<task-pick" in trk and 'min-status="prepared"' in trk:
        ok("track 用 task-pick prepared")
    else:
        bad("track 没换成 task-pick prepared")
    if "<select v-model=\"tid\">" in trk:
        bad("track 还留着任务 select")
    else:
        ok("track 任务 select 已撤")
    if "readyTasks" in trk and "takePendingTid(tid, 'track')" in trk:
        ok("track 保留 readyTasks + 接 pendingTid")
    else:
        bad("track readyTasks / pendingTid")

    if "<task-pick" in rev and 'min-status="generating"' in rev:
        ok("review 用 task-pick generating")
    else:
        bad("review 没换成 task-pick generating")
    if "<select v-model=\"tid\">" in rev:
        bad("review 还留着任务 select")
    else:
        ok("review 任务 select 已撤")
    if "readyTasks" in rev and "takePendingTid(tid, 'review')" in rev and "pickerTasks" in rev:
        ok("review 保留 readyTasks + extra 合并进 pickerTasks")
    else:
        bad("review readyTasks / pendingTid / pickerTasks")
    if "pickDefaultTid" in rev:
        ok("review 默认选未完成的逻辑还在")
    else:
        bad("review pickDefaultTid 丢了")

    print("== node --check ==")
    for p in (PARTS / "taskpick.js", PANELS / "gemini.js", PANELS / "track.js", PANELS / "review.js"):
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        if r.returncode == 0:
            ok(p.name)
        else:
            bad(p.name + " " + (r.stderr or r.stdout).strip())

    print("== 分组逻辑 ==")
    logic = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.env.ORANTE_TASKPICK, 'utf8');
const sandbox = {
  window: {},
  Vue: { ref(v){return {value:v}}, reactive(v){return v}, computed(fn){return {get value(){return fn()}}},
         watch(){}, onMounted(){}, onUnmounted(){}, nextTick(fn){return Promise.resolve().then(fn)} },
  document: { addEventListener(){}, removeEventListener(){} },
};
sandbox.window = Object.assign(sandbox, sandbox.window);
sandbox.window.Vue = sandbox.Vue;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const L = sandbox.window.TaskPickLogic;
if (!L) { console.log('NO_LOGIC'); process.exit(2); }

const tasks = [
  {id:'T0001', name:'甲', engine:'flow', status:'described'},
  {id:'T0002', name:'乙', engine:'flow', status:'described'},
  {id:'T0003', name:'丙', engine:'grok', status:'prepared'},
  {id:'T0004', name:'丁', engine:'flow', status:'prepared'},
  {id:'T0005', name:'废', engine:'flow', status:'trash'},
  {id:'T0006', name:'完', engine:'flow', status:'finished'},
  {id:'T0007', name:'草', engine:'flow', status:'draft'},
];

function ids(arr){ return arr.map(x => x.id).join(','); }

const track = L.splitTasks(tasks, 'described');
if (track.nTotal !== 6) { console.log('total', track.nTotal); process.exit(3); }
if (ids(track.waiting) !== 'T0003,T0004,T0007') { console.log('wait', ids(track.waiting)); process.exit(4); }
if (ids(track.ready) !== 'T0001,T0002,T0006') { console.log('ready', ids(track.ready)); process.exit(5); }
if (track.nReady !== 3) { console.log('nReady', track.nReady); process.exit(6); }
if (track.ready[track.ready.length-1].id !== 'T0006') { console.log('finished not last'); process.exit(7); }

const gem = L.splitTasks(tasks, 'prepared');
if (ids(gem.waiting) !== 'T0007') { console.log('gem wait', ids(gem.waiting)); process.exit(8); }
if (!gem.ready.find(x => x.id==='T0003') || gem.waiting.find(x => x.id==='T0003')) {
  console.log('prepared should be ready on gemini'); process.exit(9);
}

const rev = L.splitTasks(tasks, 'generating');
if (ids(rev.ready) !== 'T0006') { console.log('rev ready', ids(rev.ready)); process.exit(10); }
if (!rev.waiting.find(x => x.id==='T0001')) { console.log('described should wait on review'); process.exit(11); }

if (L.jumpOf('draft').node !== 'tasks') process.exit(12);
if (L.jumpOf('prepared').node !== 'gemini') process.exit(13);
if (L.jumpOf('described').node !== 'track') process.exit(14);
if (L.jumpOf('arranged').node !== 'generate') process.exit(15);
if (L.emptyFrom('described') !== 'prepared') process.exit(16);
if (L.emptyFrom('prepared') !== 'draft') process.exit(17);
if (L.emptyFrom('generating') !== 'arranged') process.exit(18);

if (L.matches({id:'T0003', name:'曾佳慧02-v09'}, 't0003') === false) process.exit(19);
if (L.matches({id:'T0003', name:'曾佳慧02-v09'}, '佳慧') === false) process.exit(20);
if (L.matches({id:'T0003', name:'曾佳慧02-v09'}, 'xyz') === true) process.exit(21);

const four = [
  {id:'T0001', name:'A', engine:'flow', status:'described'},
  {id:'T0002', name:'B', engine:'flow', status:'described'},
  {id:'T0003', name:'C', engine:'flow', status:'prepared'},
  {id:'T0004', name:'D', engine:'flow', status:'prepared'},
];
const acc = L.splitTasks(four, 'described');
if (acc.nReady !== 2 || acc.nTotal !== 4) { console.log('acc', acc.nReady, acc.nTotal); process.exit(22); }
if (ids(acc.waiting) !== 'T0003,T0004') { console.log('acc wait', ids(acc.waiting)); process.exit(23); }

const lab = L.triggerText({id:'T0005', name:'曾佳慧02-v09', engine:'flow'});
if (lab !== 'T0005 · 曾佳慧02-v09 · flow') { console.log('lab', lab); process.exit(24); }

sandbox.window.ui = { node: 'gemini', pendingTid: 'T0003' };
const tid = { value: '' };
if (!L.takePendingTid(tid, 'gemini') || tid.value !== 'T0003' || sandbox.window.ui.pendingTid) {
  console.log('take dest'); process.exit(25);
}
sandbox.window.ui = { node: 'gemini', pendingTid: 'T0003' };
tid.value = '';
if (L.takePendingTid(tid, 'track') || tid.value || sandbox.window.ui.pendingTid !== 'T0003') {
  console.log('take source stole'); process.exit(26);
}

console.log('LOGIC_OK');
"""
    r = subprocess.run(
        ["node", "-e", logic],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**__import__("os").environ, "ORANTE_TASKPICK": str(PARTS / "taskpick.js")},
    )
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and "LOGIC_OK" in out:
        ok("trash 不列、finished 排可用组末尾")
        ok("编排 可用 2 / 共 4，T0003 T0004 置灰")
        ok("多模态 draft 置灰；收片 finished 进可用组")
        ok("跳转 draft→tasks / prepared→gemini / described→track / arranged→generate")
        ok("搜索按 id / 任务名")
        ok("触发器文案 T0005 · 名 · flow")
    else:
        bad("分组逻辑 " + out.strip() + f" code={r.returncode}")

    print("== CSS ==")
    css = read(UI / "css" / "app.css")
    for sel in (".tp-trig", ".tp-pop", ".tp-item.wait", ".tp-count", ".st-prepared"):
        (ok if sel in css else bad)(sel)

    if fail:
        print(f"\n{fail} 项失败")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
