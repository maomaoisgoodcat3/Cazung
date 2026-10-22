"""The agent loop: think -> (tool -> observe)* -> answer, with streaming events."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Awaitable, Callable

import httpx

from .llm import LLMError
from .persona import build_system_prompt
from .tools import ToolContext, registry
from .utils import EMOTIONS, collapse_repeats, is_looping, parse_json_loose, truncate_middle

log = logging.getLogger("cazung.agent")

Emit = Callable[[dict], Awaitable[None]]
Confirm = Callable[[str, dict], Awaitable[bool]]

TOOL_OPEN, TOOL_CLOSE = "<tool>", "</tool>"
STOP = [TOOL_CLOSE, "[TOOL RESULT]", "<tool_result"]
_LEAD_TAG = re.compile(r"^\s*\[([^\[\]\n]{1,30})\](?!\()\s*")
_EMO_ANY = re.compile(r"\[(" + "|".join(EMOTIONS) + r")\]\s?", re.I)


class ThinkFilter:
    """Routes an inline <think>...</think> block (reasoning models) to 'thinking', the rest to 'content'."""
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf = ""
        self.state = "start"
        self.after_think = False

    def feed(self, s: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self.buf += s
        while self.buf:
            if self.state == "start":
                st = self.buf.lstrip()
                if not st or (len(st) < len(self.OPEN) and self.OPEN.startswith(st)):
                    return out
                if st.startswith(self.OPEN):
                    self.buf, self.state = st[len(self.OPEN):], "think"
                else:
                    self.state = "content"
            elif self.state == "think":
                i = self.buf.find(self.CLOSE)
                if i != -1:
                    if i:
                        out.append(("thinking", self.buf[:i]))
                    self.buf, self.state = self.buf[i + len(self.CLOSE):], "content"
                    self.after_think = True
                    continue
                hold = next((k for k in range(min(len(self.CLOSE) - 1, len(self.buf)), 0, -1)
                             if self.CLOSE.startswith(self.buf[-k:])), 0)
                if len(self.buf) > hold:
                    out.append(("thinking", self.buf[: len(self.buf) - hold]))
                self.buf = self.buf[len(self.buf) - hold:]
                return out
            else:
                if self.after_think:  # drop the newline(s) right after </think>, even across chunks
                    self.buf = self.buf.lstrip()
                    if not self.buf:
                        return out
                    self.after_think = False
                out.append(("content", self.buf))
                self.buf = ""
        return out

    def finish(self) -> list[tuple[str, str]]:
        if not self.buf:
            return []
        kind = "thinking" if self.state == "think" else "content"
        out, self.buf = [(kind, self.buf)], ""
        return out


class StreamParser:
    """Splits the model stream into an emotion tag, visible text and an optional tool call."""

    def __init__(self):
        self.raw = ""
        self.buf = ""
        self.started = False
        self.emotion: str | None = None
        self.visible = ""
        self.in_tool = False
        self.tool_buf = ""
        self._eat_space = False

    def _emit_text(self, s: str, events: list):
        if self._eat_space and s[:1] == " ":
            s = s[1:]
        self._eat_space = False
        original = s

        def emo(m):
            self.emotion = m.group(1).lower()
            events.append(("emotion", self.emotion))
            if m.end() == len(original):
                self._eat_space = True  # the space after the tag may arrive in the next chunk
            return ""
        s = _EMO_ANY.sub(emo, s)  # emotion tags in the middle of a reply
        if s:
            self.visible += s
            events.append(("text", s))

    def _strip_leading_tags(self, events: list, final: bool) -> bool:
        """Consume leading [tags]. Returns False if we must wait for more text."""
        while True:
            stripped = self.buf.lstrip()
            if not stripped:
                return final
            if not stripped.startswith("["):
                return True
            if "]" not in stripped:
                return final or len(stripped) >= 32
            m = _LEAD_TAG.match(stripped)
            if not m:
                return True
            if not final and m.end() == len(stripped):
                return False  # could still become a markdown link "[text](url)"
            tag = m.group(1).strip().lower()
            if tag in EMOTIONS:
                self.emotion = tag
                events.append(("emotion", tag))
            self.buf = stripped[m.end():]  # small models sometimes emit junk like "[Nhìn thấy]"

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self.raw += chunk
        events: list[tuple[str, str]] = []
        if self.in_tool:
            self.tool_buf += chunk
            return events
        self.buf += chunk
        if not self.started:
            if not self._strip_leading_tags(events, final=False):
                return events
            self.started = True
        idx = self.buf.find(TOOL_OPEN)
        if idx != -1:
            self._emit_text(self.buf[:idx], events)
            self.tool_buf = self.buf[idx + len(TOOL_OPEN):]
            self.buf, self.in_tool = "", True
            return events
        hold = 0
        for k in range(min(len(TOOL_OPEN) - 1, len(self.buf)), 0, -1):
            if TOOL_OPEN.startswith(self.buf[-k:]):
                hold = k
                break
        bracket = self.buf.rfind("[")
        if bracket != -1 and "]" not in self.buf[bracket:] and len(self.buf) - bracket <= 12:
            hold = max(hold, len(self.buf) - bracket)  # maybe an emotion tag still arriving
        self._emit_text(self.buf[: len(self.buf) - hold], events)
        self.buf = self.buf[len(self.buf) - hold:]
        return events

    def finish(self) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        if not self.in_tool and self.buf:
            if not self.started:
                self._strip_leading_tags(events, final=True)
            self._emit_text(self.buf, events)
            self.buf = ""
        return events

    def tool_call(self) -> dict | None:
        """Return {"name", "args"} / {"invalid": raw} / None. Also detects bare-JSON tool calls."""
        retract = False
        if self.in_tool:
            body = self.tool_buf.split(TOOL_CLOSE)[0]
        else:
            text = self.visible.strip()
            if not (text.startswith("{") or text.startswith("```")):
                return None
            body, retract = text, True
        data = parse_json_loose(body)
        call = _normalize_call(data)
        if call is None:
            return None if retract else {"invalid": body.strip()[:300]}
        if retract and not registry.get(call["name"]):
            return None
        call["retract"] = retract
        return call


def _normalize_call(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    name = data.get("name") or data.get("tool") or data.get("function")
    args = data.get("args", data.get("arguments", data.get("parameters", data.get("input"))))
    if not name and len(data) == 1:
        name, args = next(iter(data.items()))
    if not isinstance(name, str) or not name:
        return None
    if isinstance(args, str):
        args = parse_json_loose(args) or {"input": args}
    return {"name": name.strip(), "args": args if isinstance(args, dict) else {}}


class Agent:
    def __init__(self, cfg, store, llm, memory, worker):
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.memory = memory
        self.worker = worker
        self.lock = asyncio.Lock()
        self.busy = False

    @property
    def name(self):
        return self.cfg.persona.name

    # ---------------------------------------------------------------- turn
    async def chat(self, text: str, emit: Emit, confirm: Confirm) -> None:
        text = (text or "").strip()
        if not text:
            return
        async with self.lock:
            self.busy = True
            cid = self.store.current_conversation()
            parts: list[str] = []
            emotion = "neutral"
            last_mid = None
            parser = None
            try:
                if text.startswith("/") and await self.command(text, emit, cid):
                    return
                self.store.add_message(cid, "user", text)
                await emit({"type": "start"})
                memory_block = await self.memory.build_memory_block(cid, text)
                system_text = build_system_prompt(self.cfg, registry.describe_all()) + "\n\n" + memory_block
                max_steps = int(self.cfg.agent.max_steps)
                retried_empty = nudge = False
                step = 0
                while step <= max_steps:
                    messages = self.memory.build_messages(cid, system_text)
                    if step == max_steps:
                        messages.append({"role": "user", "content":
                                         "[SYSTEM] Tool limit reached. Give your final answer now, no more tools."})
                    elif nudge:
                        messages.append({"role": "user", "content":
                                         f"[SYSTEM] Your last reply was empty. Reply to {self.cfg.persona.user_title} now."})
                    nudge = False
                    parser = StreamParser()
                    thinker = ThinkFilter()
                    stream = self.llm.stream_chat(messages, stop=STOP)
                    try:
                        async for src, chunk in stream:
                            pieces = [("thinking", chunk)] if src == "thinking" else thinker.feed(chunk)
                            for kind, text in pieces:
                                if kind == "thinking":
                                    await emit({"type": "thinking", "text": text})
                                    continue
                                for k2, val in parser.feed(text):
                                    await self._relay(k2, val, emit)
                            if is_looping(parser.visible):
                                log.warning("degenerate repetition detected, stopping generation")
                                parser.visible = collapse_repeats(parser.visible)
                                await emit({"type": "retract"})
                                await emit({"type": "token", "text": parser.visible})
                                break
                    finally:
                        await stream.aclose()
                    for kind, text in thinker.finish():
                        if kind == "thinking":
                            await emit({"type": "thinking", "text": text})
                        else:
                            for k2, val in parser.feed(text):
                                await self._relay(k2, val, emit)
                    for kind, val in parser.finish():
                        await self._relay(kind, val, emit)
                    emotion = parser.emotion or emotion
                    call = parser.tool_call() if step < max_steps else None

                    if call is None and not parser.visible.strip() and not retried_empty:
                        retried_empty = nudge = True  # small models sometimes stop right after a tool result
                        continue
                    step += 1

                    if call is None:
                        clean = f"[{emotion}] {parser.visible.strip() or '...'}"
                        last_mid = self.store.add_message(cid, "assistant", clean)
                        parts.append(parser.visible)
                        parser = None
                        break

                    if call.get("retract"):
                        await emit({"type": "retract"})
                        parser.visible = ""
                    parts.append(parser.visible)
                    if "invalid" in call:
                        self.store.add_message(cid, "assistant", f"[{emotion}] {parser.visible}<tool>{call['invalid']}</tool>")
                        self.store.add_message(cid, "tool", '[TOOL RESULT] ERROR: invalid tool call. Use exactly '
                                               '<tool>{"name": "...", "args": {...}}</tool> with valid JSON.')
                        parser = None
                        continue
                    payload = json.dumps({"name": call["name"], "args": call["args"]}, ensure_ascii=False)
                    self.store.add_message(cid, "assistant", f"[{emotion}] {parser.visible.strip()} <tool>{payload}</tool>",
                                           meta={"tool_call": call["name"]})
                    parser = None
                    result = await self.run_tool(call["name"], call["args"], emit, confirm, cid)
                    self.store.add_message(cid, "tool", f"[TOOL RESULT] {call['name']}:\n{result}",
                                           meta={"tool": call["name"]})

                final = "".join(parts).strip()
                await emit({"type": "done", "message_id": last_mid, "text": final, "emotion": emotion})
                self._after_turn(cid, text, final)
            except asyncio.CancelledError:
                if parser and parser.visible.strip():
                    last_mid = self.store.add_message(cid, "assistant", f"[{emotion}] {parser.visible.strip()} (bị ngắt)")
                await emit({"type": "done", "message_id": last_mid, "text": "".join(parts) +
                            (parser.visible if parser else ""), "emotion": emotion, "stopped": True})
                raise
            except (LLMError, httpx.HTTPError) as e:
                log.exception("LLM error")
                hint = ""
                if isinstance(e, httpx.ConnectError):
                    hint = " Hình như Ollama chưa chạy. Mở Ollama lên giúp em nha!"
                elif "not found" in str(e).lower():
                    hint = f" Model '{self.llm.model}' chưa được tải. Chạy: ollama pull {self.llm.model}"
                await emit({"type": "error", "message": f"Lỗi kết nối model: {e}.{hint}"})
            except Exception as e:  # noqa: BLE001
                log.exception("turn failed")
                await emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            finally:
                self.busy = False

    async def _relay(self, kind: str, val: str, emit: Emit):
        if kind == "emotion":
            await emit({"type": "emotion", "value": val})
        else:
            await emit({"type": "token", "text": val})

    async def run_tool(self, name: str, args: dict, emit: Emit, confirm: Confirm, cid: str) -> str:
        tid = uuid.uuid4().hex[:8]
        t = registry.get(name)
        await emit({"type": "tool_call", "id": tid, "name": name, "args": args})
        if not t:
            result = f"ERROR: không có công cụ '{name}'. Hãy dùng một công cụ trong danh sách."
            await emit({"type": "tool_result", "id": tid, "name": name, "ok": False, "preview": result})
            return result
        ctx = ToolContext(cfg=self.cfg, store=self.store, memory=self.memory, llm=self.llm, conv_id=cid)
        if self.cfg.agent.confirm_dangerous and t.needs_confirmation(args, ctx):
            if not await confirm(name, args):
                result = f"{self.cfg.persona.user_title} đã TỪ CHỐI cho chạy công cụ này. Đừng thử lại, hãy hỏi lại hoặc làm cách khác."
                await emit({"type": "tool_result", "id": tid, "name": name, "ok": False, "preview": "Đã bị từ chối"})
                return result
        ok = True
        started = time.time()
        try:
            result = await registry.call(name, args, ctx, float(self.cfg.agent.tool_timeout))
        except asyncio.TimeoutError:
            ok, result = False, f"ERROR: quá thời gian {self.cfg.agent.tool_timeout}s"
        except Exception as e:  # noqa: BLE001
            ok, result = False, f"ERROR: {type(e).__name__}: {e}"
        result = truncate_middle(result or "(không có kết quả)", 6000)
        log.info("tool %s(%s) -> %s in %.1fs", name, args, "ok" if ok else "error", time.time() - started)
        await emit({"type": "tool_result", "id": tid, "name": name, "ok": ok, "preview": result[:600]})
        return result

    def _after_turn(self, cid: str, user_text: str, final: str):
        self.worker.submit("extract", self.memory.extract, cid, user_text, final)
        self.worker.submit("compact", self.memory.maybe_compact, cid)
        turns = int(self.store.kv_get("turns", 0)) + 1
        self.store.kv_set("turns", turns)
        if turns % max(1, int(self.cfg.memory.reflect_every_turns)) == 0:
            self.worker.submit("reflect", self.memory.reflect)

    # ------------------------------------------------------------ commands
    async def command(self, text: str, emit: Emit, cid: str) -> bool:
        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()

        async def say(msg: str):
            await emit({"type": "system", "text": msg})

        if cmd in ("/new", "/moi"):
            old = cid
            self.store.new_conversation()
            self.worker.submit("compact-old", self.memory.maybe_compact, old, True)
            await say("Đã mở cuộc trò chuyện mới. Cuộc cũ được tóm tắt vào trí nhớ dài hạn ✨")
        elif cmd in ("/remember", "/nho") and arg:
            mid, action = await self.memory.remember(arg, "fact", 0.85, source="user", conv_id=cid)
            await say(f"Đã ghi nhớ (#{mid}, {action}).")
        elif cmd in ("/forget", "/quen") and arg.isdigit():
            await say("Đã quên." if self.memory.forget(int(arg)) else "Không thấy ký ức đó.")
        elif cmd in ("/memory", "/mem"):
            rows = self.store.list_memories(["instruction", "fact", "preference", "lesson"])[:40]
            await say("\n".join(f"#{r['id']} [{r['kind']}] {r['content']}" for r in rows) or "Chưa có ký ức nào.")
        elif cmd == "/compact":
            await say("Đang nén cuộc trò chuyện vào bộ nhớ...")
            done = await self.memory.maybe_compact(cid, force=True)
            await say("Xong!" if done else "Không có gì để nén.")
        elif cmd == "/reflect":
            await say("Cazung đang tự suy ngẫm...")
            r = await self.memory.reflect()
            await say(f"Rút ra {r['lessons']} bài học, {r['skills']} kỹ năng mới.")
        elif cmd == "/maintain":
            r = await self.memory.maintenance()
            await say(f"Bảo trì bộ nhớ: {r}")
        elif cmd == "/help":
            await say("/new – cuộc trò chuyện mới · /remember <câu> – ghi nhớ · /forget <id> – quên · "
                      "/memory – xem ký ức · /compact – nén hội thoại · /reflect – tự suy ngẫm · /maintain – dọn bộ nhớ")
        else:
            return False
        return True
