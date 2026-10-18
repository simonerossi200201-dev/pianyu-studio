"""Plain-script tests for robota tools + robota-mcp (no pytest needed).

    runtime\\venv\\Scripts\\python.exe tests\\test_tools.py

Does not start Studio / ComfyUI / Orante / the console; only checks that they
report a clear 'not running' result when down.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from robota import config, memory  # noqa: E402
from robota.tools import gpu, orante, projects, queue_api, rclone, shell, studio, watch  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def case(fn):
    name = fn.__name__
    try:
        info = fn() or ""
        RESULTS.append((name, True, str(info)))
        print(f"PASS  {name}  {info}")
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((name, False, repr(exc)))
        print(f"FAIL  {name}  {exc!r}")
        traceback.print_exc()
    return fn


def _down_ok(res: dict, svc: str) -> str:
    assert isinstance(res, dict), res
    if res.get("running") is False:
        assert res["ok"] is False and "not running" in res["error"], res
        return f"{svc} down -> clear error"
    assert res.get("ok"), res
    return f"{svc} running"


@case
def test_safety_blocks_remove_item():
    r = shell.run_in_project("studio", "Remove-Item x")
    assert r["ok"] is False and r.get("blocked"), r
    r2 = shell.run_in_project("studio", "git push origin main")
    assert r2.get("blocked"), r2
    return r["error"]


@case
def test_run_git_version_ok():
    r = shell.run_in_project("studio", "git --version", timeout_s=60)
    assert r["ok"] and r["exit"] == 0 and "git version" in r["stdout_tail"], r
    log = config.DATA / "usage" / "commands.jsonl"
    assert log.is_file() and "git --version" in log.read_text(encoding="utf-8")
    return r["stdout_tail"].strip()


@case
def test_unknown_project():
    r = shell.run_in_project("nope", "git --version")
    assert r["ok"] is False and "unknown project" in r["error"]


@case
def test_gpu_status():
    s = gpu.gpu_status()
    assert s["ok"], s
    assert s["gpus"] and s["gpus"][0]["mem_total_mb"] > 0, s
    assert "models" in s["ollama"]
    g = s["gpus"][0]
    return f"{g['name']} {g['mem_used_mb']}/{g['mem_total_mb']} MB, ollama loaded={len(s['ollama']['models'])}"


@case
def test_memory_roundtrip():
    topic = "robota-selftest"
    p = memory.MEM_DIR / f"{topic}.md"
    p.unlink(missing_ok=True)
    try:
        assert memory.memory_append(topic, "hello 记忆 123")["ok"]
        r = memory.memory_read(topic)
        assert r["exists"] and "hello 记忆 123" in r["text"], r
        assert topic in memory.memory_topics()
        assert memory._topic("../../etc/passwd") == "etc_passwd"
        assert memory.memory_read("")["ok"] is False
    finally:
        p.unlink(missing_ok=True)


@case
def test_list_projects():
    ps = projects.list_projects()
    ids = sorted(p["id"] for p in ps)
    assert len(ps) == 4, ids
    assert projects.project_info("studio")["ok"]
    assert projects.project_info("zzz")["ok"] is False
    return ids


@case
def test_watch_file():
    d = Path(tempfile.mkdtemp(prefix="robota_watch_"))
    f = d / "log.txt"
    r0 = watch.watch_file(str(f), timeout_s=0.5, poll_s=0.2)
    assert r0["ok"] is False and r0["timed_out"], r0

    def writer():
        time.sleep(0.6)
        f.write_text("starting\n", encoding="utf-8")
        time.sleep(0.6)
        with f.open("a", encoding="utf-8") as fh:
            fh.write("step 1\nDONE: exported 12 clips\n")
    threading.Thread(target=writer, daemon=True).start()
    r1 = watch.watch_file(str(f), timeout_s=10, poll_s=0.2)
    assert r1["ok"] and r1["line"] is None, r1
    r2 = watch.watch_file(str(f), pattern=r"DONE: .*clips", timeout_s=10, poll_s=0.2)
    assert r2["ok"] and r2["line"] == "DONE: exported 12 clips", r2
    f.unlink()
    d.rmdir()
    return f"waited {r2['waited_s']}s"


@case
def test_rclone_ls_top():
    assert rclone.find_rclone(), "rclone not found"
    r = rclone.rclone_ls("gdrive:")
    assert r["ok"], {k: v for k, v in r.items() if k != "items"}
    return f"gdrive: top-level count={r['count']}"  # names deliberately not printed


@case
def test_rclone_refusals():
    assert rclone.rclone_ls("C:\\")["ok"] is False
    assert rclone.rclone_copy("gdrive:a", "gdrive:b")["ok"] is False
    assert rclone.rclone_copy("--delete-before", "gdrive:x")["ok"] is False
    assert rclone.rclone_copy("other:x", "D:\\tmp")["ok"] is False


@case
def test_studio_and_comfy():
    return "; ".join([_down_ok(studio.studio_health(), "studio"), _down_ok(studio.comfy_queue(), "comfy")])


@case
def test_orante_health():
    return _down_ok(orante.orante_health(), "orante")


@case
def test_queue_console():
    r = queue_api.queue_list()
    if r.get("running") is False:
        assert "not running" in r["error"]
        return "console down -> clear error"
    return "console running"


@case
def test_queue_add_validation():
    assert queue_api.queue_add("t", kind="agent")["ok"] is False
    assert queue_api.queue_add("t", command="x", kind="bogus")["ok"] is False


# ------------------------------------------------------------------ MCP end-to-end (stdio)

@case
def test_mcp_stdio_e2e():
    import json

    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
        params = StdioServerParameters(command=config.PYTHON, args=["-m", "robota.mcp_server"],
                                       cwd=str(ROOT), env=env)
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            names = sorted(t.name for t in tools)
            for need in ("gpu_status", "list_projects", "run_in_project", "memory_read", "watch_file",
                         "rclone_ls", "studio_submit", "orante_check", "queue_add", "notify"):
                assert need in names, (need, names)

            def payload(res):
                is_err = getattr(res, "is_error", None)
                if is_err is None:
                    is_err = getattr(res, "isError", False)
                assert not is_err, res
                sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
                if sc:
                    return sc.get("result", sc)
                return json.loads(res.content[0].text)
            g = payload(await s.call_tool("gpu_status", {}))
            assert g["ok"] and g["gpus"], g
            lp = payload(await s.call_tool("list_projects", {}))
            assert len(lp) == 4, lp
            blk = payload(await s.call_tool("run_in_project", {"project": "studio", "command": "Remove-Item x"}))
            assert blk.get("blocked"), blk
            return f"{len(names)} tools; list_projects={sorted(p['id'] for p in lp)}"
    return anyio.run(go)


if __name__ == "__main__":
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    sys.exit(1 if bad else 0)
