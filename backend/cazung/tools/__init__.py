"""Tool registry.

Add a tool anywhere (built-ins here, or a .py file in backend/plugins/) with:

    from cazung.tools import tool

    @tool("Mô tả ngắn cho model", params={"city": "str: tên thành phố"})
    async def get_weather(city: str, ctx=None) -> str:
        ...

Param spec: "type: description"; prefix the name with "?" to make it optional
({"?limit": "int: ..."}). A function may accept `ctx` (the ToolContext) as a keyword.
`dangerous=True` makes Cazung ask for confirmation before running it.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("cazung.tools")


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, str]
    func: Callable
    dangerous: bool = False
    wants_ctx: bool = False
    confirm_if: Callable | None = None  # (args, ctx) -> bool : ask only in some cases
    group: str = "khác"

    def needs_confirmation(self, args: dict, ctx) -> bool:
        if self.dangerous:
            return True
        if self.confirm_if:
            try:
                return bool(self.confirm_if(args or {}, ctx))
            except Exception:  # noqa: BLE001
                return True
        return False

    def signature(self) -> str:
        parts = []
        for raw, spec in self.params.items():
            optional = raw.startswith("?")
            name = raw.lstrip("?")
            typ, _, desc = spec.partition(":")
            parts.append(f"{name}{'?' if optional else ''}: {typ.strip()}")
        return f"{self.name}({', '.join(parts)})"

    def describe(self) -> str:
        details = "; ".join(
            f"{raw.lstrip('?')} = {spec.partition(':')[2].strip()}" for raw, spec in self.params.items()
            if spec.partition(":")[2].strip()
        )
        flag = " [cần chủ nhân xác nhận]" if self.dangerous else (
            " [ngoài workspace cần xác nhận]" if self.confirm_if else "")
        return f"- {self.signature()} — {self.description}{flag}" + (f" ({details})" if details else "")


@dataclass
class ToolContext:
    cfg: Any = None
    store: Any = None
    memory: Any = None
    llm: Any = None
    conv_id: str = ""
    extra: dict = field(default_factory=dict)


class Registry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}
        self.disabled: set[str] = set()

    def register(self, t: Tool):
        self.tools[t.name] = t

    def available(self) -> list[Tool]:
        return [t for n, t in self.tools.items() if n not in self.disabled]

    def get(self, name: str) -> Tool | None:
        return None if name in self.disabled else self.tools.get(name)

    def describe_all(self) -> str:
        groups: dict[str, list[str]] = {}
        for t in self.available():
            groups.setdefault(t.group, []).append(t.describe())
        return "\n".join(f"[{g}]\n" + "\n".join(lines) for g, lines in groups.items())

    async def call(self, name: str, args: dict, ctx: ToolContext, timeout: float) -> str:
        t = self.get(name)
        if not t:
            raise KeyError(f"Không có công cụ tên '{name}'. Các công cụ hiện có: {', '.join(self.tools)}")
        accepted = {p.lstrip("?") for p in t.params}
        kwargs = {k: v for k, v in (args or {}).items() if k in accepted}
        missing = [p for p in t.params if not p.startswith("?") and p not in kwargs]
        if missing:
            raise ValueError(f"Thiếu tham số: {', '.join(missing)}. Cú pháp: {t.signature()}")
        if t.wants_ctx:
            kwargs["ctx"] = ctx
        if inspect.iscoroutinefunction(t.func):
            result = await asyncio.wait_for(t.func(**kwargs), timeout)
        else:
            result = await asyncio.wait_for(asyncio.to_thread(t.func, **kwargs), timeout)
        return result if isinstance(result, str) else repr(result)


registry = Registry()


def tool(description: str, params: dict[str, str] | None = None, name: str | None = None,
         dangerous: bool = False, confirm_if: Callable | None = None, group: str = "khác"):
    def deco(fn):
        wants_ctx = "ctx" in inspect.signature(fn).parameters
        registry.register(Tool(name or fn.__name__, description, params or {}, fn, dangerous, wants_ctx,
                               confirm_if, group))
        return fn
    return deco


def load_plugins(folder: Path) -> list[str]:
    loaded = []
    if not folder.exists():
        return loaded
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"cazung_plugin_{path.stem}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            loaded.append(path.stem)
        except Exception as e:  # noqa: BLE001
            log.error("plugin %s failed to load: %s", path.name, e)
    return loaded
