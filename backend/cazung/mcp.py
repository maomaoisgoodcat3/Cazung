"""Minimal MCP (Model Context Protocol) client over stdio.

Each [[mcp]] entry in config.toml starts a server process; its tools are registered as
`<server>__<tool>` and, by default, every call needs the owner's approval."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil

from .tools import Tool, registry

log = logging.getLogger("cazung.mcp")
PROTOCOL = "2025-06-18"


class MCPServer:
    def __init__(self, spec: dict):
        self.name = re.sub(r"\W+", "_", spec["name"])
        self.command = spec["command"]
        self.args = [str(a) for a in spec.get("args", [])]
        self.env = {k: str(v) for k, v in (spec.get("env") or {}).items()}
        self.confirm = bool(spec.get("confirm", True))
        self.auto_approve = set(spec.get("auto_approve", []))
        self.proc: asyncio.subprocess.Process | None = None
        self.pending: dict[int, asyncio.Future] = {}
        self.next_id = 0
        self.tools: list[str] = []

    async def start(self):
        exe = shutil.which(self.command) or self.command
        self.proc = await asyncio.create_subprocess_exec(
            exe, *self.args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env={**os.environ, **self.env}, limit=16 * 1024 * 1024)
        asyncio.create_task(self._reader())
        await self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": "cazung", "version": "1.0"}})
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        result = await self.request("tools/list", {})
        for t in result.get("tools", []):
            self._register(t)
        log.info("MCP %s: %d tools", self.name, len(self.tools))

    def _register(self, t: dict):
        schema = t.get("inputSchema") or {}
        required = set(schema.get("required", []))
        params = {}
        for pname, p in (schema.get("properties") or {}).items():
            typ = p.get("type", "any") if isinstance(p.get("type"), str) else "any"
            key = pname if pname in required else f"?{pname}"
            params[key] = f"{typ}: {(p.get('description') or '')[:120]}"
        full = f"{self.name}__{t['name']}"
        server, tool_name = self, t["name"]

        async def call(**kwargs):
            return await server.call_tool(tool_name, kwargs)

        needs = self.confirm and t["name"] not in self.auto_approve
        registry.register(Tool(full, (t.get("description") or "")[:300], params, call, dangerous=needs,
                               group=f"MCP: {self.name}"))
        self.tools.append(full)

    async def _send(self, msg: dict):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict, timeout: float = 120):
        self.next_id += 1
        rid = self.next_id
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)

    async def _reader(self):
        while self.proc and self.proc.returncode is None:
            line = await self.proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            fut = self.pending.get(msg.get("id"))
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError(msg["error"].get("message", "MCP error")))
                else:
                    fut.set_result(msg.get("result") or {})
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(f"MCP server {self.name} stopped"))

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif item.get("type") == "resource":
                parts.append(json.dumps(item.get("resource"), ensure_ascii=False)[:2000])
            else:
                parts.append(f"[{item.get('type')}]")
        text = "\n".join(parts) or json.dumps(result.get("structuredContent", {}), ensure_ascii=False)
        return ("ERROR: " if result.get("isError") else "") + text

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()


async def start_all(specs: list[dict]) -> list[MCPServer]:
    servers = []
    for spec in specs or []:
        if not spec.get("name") or not spec.get("command") or spec.get("enabled") is False:
            continue
        s = MCPServer(spec)
        try:
            await s.start()
            servers.append(s)
        except Exception as e:  # noqa: BLE001
            log.error("MCP server %s failed to start: %s", spec.get("name"), e)
            await s.stop()
    return servers
