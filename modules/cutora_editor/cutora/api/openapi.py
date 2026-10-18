# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenAPI 3.1 document generated from the command registry."""
from __future__ import annotations

from typing import Any

from cutora.api import DEFAULT_PORT
from cutora.core.commands import get_registry


def build_openapi(port: int = DEFAULT_PORT) -> dict[str, Any]:
    registry = get_registry()
    paths: dict[str, Any] = {
        "/v1/commands": {
            "get": {
                "operationId": "listCommands",
                "summary": "列出全部命令",
                "security": [{"bearerAuth": []}],
                "responses": {"200": {"description": "命令列表"}},
            }
        },
        "/v1/undo": {
            "post": {
                "operationId": "undo",
                "summary": "撤销上一步",
                "security": [{"bearerAuth": []}],
                "responses": {"200": {"description": "已撤销"}},
            }
        },
        "/v1/redo": {
            "post": {
                "operationId": "redo",
                "summary": "重做",
                "security": [{"bearerAuth": []}],
                "responses": {"200": {"description": "已重做"}},
            }
        },
        "/v1/state": {
            "get": {
                "operationId": "getState",
                "summary": "当前工程摘要",
                "security": [{"bearerAuth": []}],
                "responses": {"200": {"description": "状态"}},
            }
        },
        "/v1/events": {
            "get": {
                "operationId": "events",
                "summary": "SSE 事件流",
                "security": [{"bearerAuth": []}],
                "responses": {"200": {"description": "text/event-stream"}},
            }
        },
    }
    for spec in registry.list():
        path = "/v1/commands/" + spec.name
        paths[path] = {
            "post": {
                "operationId": spec.name.replace(".", "_"),
                "summary": spec.title_zh,
                "description": spec.doc_zh,
                "security": [{"bearerAuth": []}],
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {"schema": spec.params_schema},
                    },
                },
                "responses": {
                    "200": {
                        "description": "成功",
                        "content": {
                            "application/json": {"schema": spec.returns_schema},
                        },
                    },
                    "400": {"description": "参数或 NeedsGui"},
                    "401": {"description": "未授权"},
                    "409": {"description": "危险命令未 confirm"},
                    "504": {"description": "主线程超时"},
                },
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "片语 Studio 本地 API",
            "version": "7.0.0",
            "description": "仅绑定 127.0.0.1。Authorization: Bearer <token>。",
        },
        "servers": [{"url": "http://127.0.0.1:%d" % int(port)}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                }
            }
        },
    }


_DOCS_CSS = (
    "body{font-family:sans-serif;background:#141210;color:#EFE7DA;margin:24px;}"
    "h1,h2{color:#C8A24A;} h3{color:#E0BE68;margin-bottom:8px;}"
    "article{background:#1C1815;border:1px solid #3A302A;padding:16px;"
    "margin:12px 0;border-radius:6px;}"
    "code,pre{background:#262019;padding:8px;display:block;white-space:pre-wrap;}"
    "a{color:#C8A24A;} table{border-collapse:collapse;width:100%;margin:8px 0;}"
    "th,td{border:1px solid #3A302A;padding:6px 8px;text-align:left;}"
    "th{color:#C8A24A;} .badge{display:inline-block;margin-right:6px;"
    "padding:2px 8px;border:1px solid #C8A24A;color:#C8A24A;border-radius:4px;"
    "font-size:12px;} .muted{color:#7A6F63;}"
)

# Inline renderer for /v1/commands. Keep this block ≤ 80 lines.
_DOCS_JS = """
(function(){
  var data = window.CUTORA_DOCS || {};
  var cmds = data.commands || [];
  var port = data.port || 47321;
  var groups = {};
  cmds.forEach(function(c){
    var ns = String(c.name || "").split(".")[0] || "other";
    (groups[ns] = groups[ns] || []).push(c);
  });
  var root = document.getElementById("root");
  function esc(s){
    return String(s == null ? "" : s).replace(/[&<>]/g, function(ch){
      return {"&":"&amp;","<":"&lt;",">":"&gt;"}[ch];
    });
  }
  function badge(on, label){
    return on ? '<span class="badge">'+label+'</span>' : "";
  }
  function paramsTable(schema){
    var props = (schema && schema.properties) || {};
    var req = (schema && schema.required) || [];
    var names = Object.keys(props);
    if (!names.length) return '<p class="muted">无参数</p>';
    var html = "<table><tr><th>名</th><th>类型</th><th>必填</th><th>说明</th></tr>";
    names.forEach(function(n){
      var p = props[n] || {};
      var t = p.type; if (Array.isArray(t)) t = t.join("|");
      html += "<tr><td><code>"+esc(n)+"</code></td><td>"+esc(t||"")+"</td><td>"
        +(req.indexOf(n)>=0?"是":"否")+"</td><td>"+esc(p.description||"")
        +"</td></tr>";
    });
    return html+"</table>";
  }
  function curlFor(c){
    var body = {};
    var props = (c.params_schema && c.params_schema.properties) || {};
    Object.keys(props).forEach(function(n){ body[n] = ""; });
    return "curl -sS -H \\"Authorization: Bearer $TOKEN\\" "
      +"-H \\"Content-Type: application/json\\" -d '"
      +JSON.stringify(body)+"' http://127.0.0.1:"+port+"/v1/commands/"+c.name;
  }
  function renderCmd(c){
    var el = document.createElement("article");
    el.innerHTML = "<h3><code>"+esc(c.name)+"</code> "+esc(c.title_zh)+"</h3>"
      +"<p>"+esc(c.doc_zh)+"</p><p>"
      +badge(c.undoable,"可撤销")+badge(c.dangerous,"危险")
      +badge(c.async_job,"异步")+badge(c.headless,"无头")+"</p>"
      +paramsTable(c.params_schema)
      +"<pre>"+esc(curlFor(c))+"</pre>";
    return el;
  }
  Object.keys(groups).sort().forEach(function(ns){
    var h = document.createElement("h2");
    h.textContent = ns;
    root.appendChild(h);
    groups[ns].forEach(function(c){ root.appendChild(renderCmd(c)); });
  });
})();
""".strip()


def docs_html(port: int = DEFAULT_PORT) -> str:
    """Offline docs page. Renders /v1/commands. No CDN."""
    import json
    payload = json.dumps(
        {"commands": get_registry().public_list(), "port": int(port)},
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    js_lines = [ln for ln in _DOCS_JS.splitlines() if ln.strip()]
    if len(js_lines) > 80:
        raise RuntimeError("/docs 内联 JS 超过 80 行: %d" % len(js_lines))
    return (
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        "<title>片语 Studio API</title><style>" + _DOCS_CSS + "</style></head><body>"
        "<h1>片语 Studio 本地 API</h1>"
        "<p>仅 127.0.0.1。请求头 <code>Authorization: Bearer &lt;token&gt;</code>。"
        "带 Origin 的请求一律 403。</p>"
        "<p><a href='/openapi.json'>openapi.json</a> · "
        "<a href='/v1/commands'>/v1/commands</a></p>"
        "<div id='root'></div>"
        "<script>window.CUTORA_DOCS = " + payload + ";</script>"
        "<script>" + _DOCS_JS + "</script></body></html>"
    )
