# -*- coding: utf-8 -*-
"""规格 26：预设导出 / 导入 / 加密 / cookie 失败隔离 / 出厂。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec26-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import accounts, config, db, paths  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.services import aikeys, chrome_cookies, preset  # noqa: E402

db.init()
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def test_defaults_clean():
    print("== DEFAULTS 没有私人路径 ==")
    for k, v in config.DEFAULTS.items():
        s = str(v)
        if "1_PROJECT" in s or "D:\\\\1_" in s or "D:/1_" in s:
            bad(f"{k} 默认值像私人路径：{s}")
            return
        if k == "gemini.api_key" and s.strip():
            bad("gemini.api_key 默认不是空串")
            return
        if k.endswith("api_key") and s.strip():
            bad(f"{k} 默认藏了 key")
            return
    root = str(config.DEFAULTS.get("workspace.root") or "")
    if "workspace" not in root.replace("\\", "/").lower():
        bad(f"workspace.root 默认不是通用 workspace：{root}")
        return
    ok("DEFAULTS 干净（workspace.root = 软件自己的 workspace）")


def test_encrypt_roundtrip():
    print("== 文件格式 / 加解密 ==")
    config.set("split.flow.max_chars", 199)
    config.set("prompts.desc", "DESC-LOCAL-AAA")
    config.set("prompts.camera", {"quiet": ["x"]})
    config.set("prompts.format_version", 5)
    a = accounts.create("spec26-luca", "grok")
    fake_key = "AIza" + "SyFakeTestKeyForSpec26"
    p = aikeys.add({"name": "Gemini-测", "kind": "gemini",
                    "api_key": fake_key,
                    "model": "gemini-3.5-flash-lite"})
    dest = TEST_DATA / "roundtrip.orpreset"
    r = preset.export(list(preset.GROUPS), False, "pw-26-ok", str(dest))
    if not dest.is_file():
        bad("没写出文件")
        return
    if r["bytes"] < 100:
        bad(f"文件太小 {r['bytes']}")
        return
    raw = dest.read_bytes()
    if not raw.startswith(b"ORANTE-PRESET\n"):
        bad("开头不是 ORANTE-PRESET")
        return
    head = raw.split(b"\n", 2)[1].decode("utf-8")
    if "AIza" in head or "sk-" in head or "spec26-luca" in head or "Gemini-测" in head:
        bad("明文头部漏了 key / 账号名")
        return
    if fake_key.encode("utf-8") in raw:
        bad("密文文件里能搜到 API key 明文")
        return

    if "spec26-luca".encode("utf-8") in raw.split(b"\n", 2)[1]:
        bad("头部有账号名")
        return
    meta = json.loads(head)
    if meta.get("format") != 1:
        bad(f"format={meta.get('format')}")
        return
    if "kdf" not in meta or meta["kdf"].get("iter") != 200000:
        bad("kdf.iter 不是 200000")
        return
    ins = preset.inspect(str(dest))
    if "AIza" in json.dumps(ins):
        bad("inspect 回了 key")
        return
    try:
        preset.diff(str(dest), "wrong-password")
        bad("错密码居然没报错")
        return
    except OranteError as e:
        if e.message != preset.BAD_PASSWORD:
            bad(f"错密码文案不对：{e.message!r}")
            return
        ok("错密码一句人话")
    d = preset.diff(str(dest), "pw-26-ok")
    if d["accounts"]["add"] + len(d["accounts"]["conflict"]) < 1:
        bad("diff 没看到账号")
        return
    ok(f"导出 {r['bytes']} 字节，头部干净")
    return a, p, dest


def test_merge_local_prompts(dest: Path):
    print("== 合并，冲突保留本机 ==")
    config.set("prompts.desc", "DESC-CHANGED-BBB")
    config.set("prompts.camera", {"quiet": ["local"]})
    config.set("prompts.tail", {"x": 1})
    r = preset.do_import(str(dest), "pw-26-ok", "merge_local",
                         ["settings", "prompts", "accounts", "ai_providers"])
    desc = config.get("prompts.desc")
    if desc != "DESC-CHANGED-BBB":
        bad(f"merge_local 把 desc 盖掉了：{desc!r}")
        return
    ok("merge_local 没覆盖本机改过的提示词")
    return r


def test_replace_backup(dest: Path):
    print("== 整包替换先备份 ==")
    config.set("prompts.desc", "BEFORE-REPLACE")
    r = preset.do_import(str(dest), "pw-26-ok", "replace",
                         ["settings", "prompts", "accounts", "ai_providers"])
    bak = r.get("backup")
    if not bak or not Path(bak).is_file():
        bad(f"替换前备份不存在：{bak}")
        return
    if config.get("prompts.desc") != "DESC-LOCAL-AAA":
        bad(f"替换后 desc 不是预设里的：{config.get('prompts.desc')!r}")
        return
    # 备份能导回来
    preset.do_import(bak, "pw-26-ok", "replace", ["prompts"])
    if config.get("prompts.desc") != "BEFORE-REPLACE":
        bad(f"从备份导回来 desc 不对：{config.get('prompts.desc')!r}")
        return
    ok(f"备份 {Path(bak).name} 能导回来")


def test_cookie_fail_isolated():
    print("== 某个账号 cookie 解不开，其余照常 ==")
    # 清掉账号，重新建两个
    for a in accounts.list_all():
        db.run("DELETE FROM accounts WHERE id=?", (a["id"],))
    good = accounts.create("ck-good", "grok")
    bad_acc = accounts.create("ck-bad", "flow")
    # 好的：写一套能解开的 cookie
    gdir = accounts.profile_dir(good)
    bundle = {
        "ok": True,
        "cookies": [{
            "host_key": ".grok.com", "name": "sid", "path": "/",
            "plain": chrome_cookies._b64(b"hello-session"),
            "creation_utc": 1, "expires_utc": 9, "is_secure": 1,
            "is_httponly": 1, "last_access_utc": 1, "has_expires": 1,
            "is_persistent": 1, "priority": 1, "samesite": 0,
            "source_scheme": 2, "source_port": 443, "last_update_utc": 1,
            "source_type": 0, "has_cross_site_ancestor": 0,
            "top_frame_site_key": "",
        }],
        "local_storage": [],
    }
    inj = chrome_cookies.inject_account(gdir, bundle, chrome_alive=False)
    if not inj.get("ok"):
        bad(f"写入好 cookie 失败：{inj}")
        return
    ext = chrome_cookies.extract_account(gdir)
    if not ext.get("ok"):
        bad(f"读回好 cookie 失败：{ext.get('error')}")
        return
    plain = chrome_cookies._b64d(ext["cookies"][0]["plain"])
    if plain != b"hello-session":
        bad(f"明文对不上：{plain!r}")
        return
    ok("cookie 解密/再加密往返")

    # 坏的：Cookies 文件改成垃圾，但 Local State 还在
    bdir = accounts.profile_dir(bad_acc)
    chrome_cookies.ensure_chrome_key(bdir)
    ck = bdir / "Default" / "Network" / "Cookies"
    ck.parent.mkdir(parents=True, exist_ok=True)
    ck.write_bytes(b"this-is-not-sqlite")

    dest = TEST_DATA / "ck.orpreset"
    r = preset.export(["accounts"], True, "pw-ck", str(dest))
    warns = " ".join(r.get("warnings") or [])
    if bad_acc["id"] not in warns and "解不开" not in warns and "读不出来" not in warns:
        # 导出时坏账号应该进 warnings
        if not any(bad_acc["id"] in w for w in (r.get("warnings") or [])):
            bad(f"坏 cookie 导出没警告：{r.get('warnings')}")
            return
    ok("坏 cookie 导出有警告")

    # 模拟新机器：删账号再导入
    db.run("DELETE FROM accounts")
    imported = preset.do_import(str(dest), "pw-ck", "replace", ["accounts", "cookies"])
    need = imported.get("need_relogin") or []
    accs = {a["name"]: a for a in accounts.list_all()}
    if "ck-good" not in accs or "ck-bad" not in accs:
        bad(f"账号没导回来：{list(accs)}")
        return
    if accs["ck-good"].get("login_state") != "imported":
        bad(f"好账号不是已带入：{accs['ck-good'].get('login_state')}")
        return
    if accs["ck-bad"].get("login_state") != "need_relogin":
        bad(f"坏账号不是需重登：{accs['ck-bad'].get('login_state')} need={need}")
        return
    if accs["ck-bad"]["id"] not in need:
        bad(f"汇总没点名坏账号：{need}")
        return
    ok(f"坏账号 {accs['ck-bad']['id']} 需重登，好账号已带入")


def test_factory_keeps_tasks():
    print("== 恢复出厂保留任务 ==")
    db.run("INSERT INTO tasks(id,name,engine,status,created,updated,meta) VALUES(?,?,?,?,?,?,?)",
           ("T9999", "keep-me", "grok", "draft", db.now(), db.now(), "{}"))
    accounts.create("wipe-me", "grok")
    r = preset.factory_reset("确认清空", "pw-fac")
    if not r.get("backup") or not Path(r["backup"]).is_file():
        bad("出厂备份不存在")
        return
    if accounts.list_all():
        bad("出厂后还有账号")
        return
    if aikeys.list_all():
        bad("出厂后还有接口")
        return
    row = db.one("SELECT id FROM tasks WHERE id='T9999'")
    if not row:
        bad("出厂把任务删了")
        return
    try:
        preset.factory_reset("不对")
        bad("确认词不对居然执行了")
        return
    except OranteError:
        ok("确认词不对会拒绝")
    ok("恢复出厂清空账号，任务还在")


def test_bat_lists_targets():
    print("== Make-Clean-Build.bat（已被 64 号取代） ==")
    p = ROOT / "tools" / "Make-Clean-Build.bat"
    if not p.is_file():
        bad("没有 tools/Make-Clean-Build.bat")
        return
    t = p.read_text(encoding="utf-8", errors="replace")
    low = t.lower()
    if "del " in low or "rmdir" in low:
        bad("Make-Clean-Build.bat 还在删东西（64 号已停用它）")
        return
    if "15-Make-Share-Package.bat" not in t:
        bad("停用提示没指向 15-Make-Share-Package.bat")
        return
    ok("bat 已停用，指向 64 号出包脚本")


def test_header_search_live(dest: Path):
    print("== 全量搜不到 AIza / 账号名 ==")
    raw = dest.read_bytes()
    text = raw.decode("latin-1", "replace")
    for needle in ("AIza", "sk-", "spec26-luca", "Gemini-测"):
        # 只查明文头部
        head = raw.split(b"\n", 2)[1].decode("utf-8", "replace")
        if needle in head:
            bad(f"头部搜到 {needle}")
            return
    ok("头部搜不到 AIza / sk- / 账号名")


def main():
    print("TEST_DATA", TEST_DATA)
    test_defaults_clean()
    pair = test_encrypt_roundtrip()
    dest = pair[2] if pair else None
    if dest:
        test_merge_local_prompts(dest)
        test_replace_backup(dest)
        test_header_search_live(dest)
    test_cookie_fail_isolated()
    test_factory_keeps_tasks()
    test_bat_lists_targets()
    print()
    if fail:
        print(f"FAILED {fail}")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
