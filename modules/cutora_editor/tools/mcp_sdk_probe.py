# -*- coding: utf-8 -*-
"""Run official MCP SDK client against cutora.api.mcp_server (product python).

Invoked by tests/test_mcp_stdio.py using build/mcp-sdk-venv, not runtime/venv.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys


def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    product = os.environ.get("CUTORA_PRODUCT_PY") or os.path.join(
        repo, "runtime", "venv", "Scripts", "python.exe")
    env = dict(os.environ)
    env["PYTHONPATH"] = repo + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    params = StdioServerParameters(
        command=product,
        args=["-m", "cutora.api.mcp_server"],
        env=env,
        cwd=repo,
    )

    async def _run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                ver = getattr(init, "protocol_version", None) or getattr(
                    init, "protocolVersion", None)
                assert str(ver) == "2025-06-18", ver
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                assert "app_version" in names, names[:12]
                called = await session.call_tool("app_version", arguments={})
                is_err = getattr(called, "is_error", None)
                if is_err is None:
                    is_err = getattr(called, "isError", False)
                assert not is_err, called
                text = called.content[0].text
                body = json.loads(text)
                assert body.get("ok") is True, body
                assert body["result"]["version"]
                res = await session.read_resource("cutora://docs/commands")
                blob = res.contents[0]
                text2 = getattr(blob, "text", None) or ""
                assert "app.version" in text2

    asyncio.run(_run())
    sys.stdout.write("sdk-ok\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
