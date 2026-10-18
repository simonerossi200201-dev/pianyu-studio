"""Robota tool implementations.

Plain functions that return JSON-serialisable dicts/lists. They are used by
robota.mcp_server (MCP stdio) and can be imported directly by the console /
task queue. Every function is expected to *return* an error dict instead of
raising for "expected" failures (service down, bad argument, blocked command).
"""
from __future__ import annotations

__all__ = ["gpu", "notify", "projects", "shell", "queue_api", "rclone", "studio", "orante", "watch"]
