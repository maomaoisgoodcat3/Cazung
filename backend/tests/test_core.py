"""Offline tests (no Ollama needed): python -m unittest discover -s tests -v   (run from backend/)"""
import asyncio
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("CAZUNG_DATA", tempfile.mkdtemp(prefix="cazung-test-"))

from cazung.agent import StreamParser, ThinkFilter  # noqa: E402
from cazung.config import load_config  # noqa: E402
from cazung.memory import MemoryManager, Store  # noqa: E402
from cazung.memory.embeddings import Embedder  # noqa: E402
from cazung.tools import builtin  # noqa: E402,F401
from cazung.utils import collapse_repeats, is_looping, parse_json_loose  # noqa: E402


def parse(text, n):
    p = StreamParser()
    ev = []
    for i in range(0, len(text), n):
        ev += p.feed(text[i:i + n])
    ev += p.finish()
    return ([v for k, v in ev if k == "emotion"], "".join(v for k, v in ev if k == "text").strip(), p.tool_call())


class ParserTests(unittest.TestCase):
    CASES = {
        "[Nhìn thấy mình] [happy] Dạ chào chủ nhân!": (["happy"], "Dạ chào chủ nhân!", None),
        "ok [sad] giữa câu": (["sad"], "ok giữa câu", None),
        "[Google](https://g.com) link": ([], "[Google](https://g.com) link", None),
        "[love] a[0] và a[1]": (["love"], "a[0] và a[1]", None),
    }

    def test_text_cases_any_chunking(self):
        for text, expected in self.CASES.items():
            for n in (1, 2, 3, 7, 100):
                self.assertEqual(parse(text, n), expected, f"{text!r} chunk={n}")

    def test_tool_call(self):
        for n in (1, 4, 100):
            emos, text, call = parse('[thinking] Để em tra~ <tool>{"name": "web_search", "args": {"query": "x"}}', n)
            self.assertEqual(emos, ["thinking"])
            self.assertEqual(text, "Để em tra~")
            self.assertEqual(call["name"], "web_search")
            self.assertEqual(call["args"], {"query": "x"})

    def test_bare_json_tool_call_is_retracted(self):
        _, _, call = parse('{"name": "calculator", "args": {"expression": "2+2"}}', 5)
        self.assertTrue(call["retract"])

    def test_invalid_tool_json(self):
        _, _, call = parse("<tool>not json at all</tool>", 3)
        self.assertIn("invalid", call)

    def test_think_filter(self):
        text = "<think>Chủ nhân hỏi giờ… cần get_time</think>\n[happy] Dạ để em xem!"
        for n in (1, 2, 5, 100):
            f = ThinkFilter()
            out = []
            for i in range(0, len(text), n):
                out += f.feed(text[i:i + n])
            out += f.finish()
            think = "".join(t for k, t in out if k == "thinking")
            content = "".join(t for k, t in out if k == "content")
            self.assertEqual(think, "Chủ nhân hỏi giờ… cần get_time", f"chunk={n}")
            self.assertEqual(content, "[happy] Dạ để em xem!", f"chunk={n}")
        f = ThinkFilter()
        self.assertEqual(f.feed("[neutral] không suy nghĩ") + f.finish(), [("content", "[neutral] không suy nghĩ")])

    def test_loose_json(self):
        self.assertEqual(parse_json_loose('```json\n{"a": [1, 2,],}\n```'), {"a": [1, 2]})
        self.assertEqual(parse_json_loose("Sure! {'memories': []} hope it helps"), {"memories": []})
        extra = '{"name": "web_search", "args": {"query": "x"}}}'  # small models add a stray brace
        self.assertEqual(parse_json_loose(extra)["args"], {"query": "x"})
        missing = '{"name": "get_time", "args": {}'
        self.assertEqual(parse_json_loose(missing), {"name": "get_time", "args": {}})

    def test_repetition_guard(self):
        looping = "kết quả là 7.006.664" + ".000" * 80
        self.assertTrue(is_looping(looping))
        self.assertFalse(is_looping("Một câu trả lời bình thường, không lặp lại gì cả. " * 3 + "Hết."))
        self.assertLess(len(collapse_repeats(looping)), 40)


class FakeLLM:
    """Deterministic stand-in for the model used by the memory manager."""
    num_ctx = 1500
    max_response_tokens = 300

    def __init__(self):
        self.calls = []

    async def complete(self, prompt, **kw):
        text = prompt if isinstance(prompt, str) else prompt[-1]["content"]
        self.calls.append(text[:40])
        if "running memory" in text:
            body = text.split("NEW MESSAGES:")[1]
            facts = [line.split(": ", 1)[1] for line in body.strip().splitlines() if "chủ nhân: " in line]
            old = text.split("EXISTING SUMMARY:")[1].split("NEW MESSAGES:")[0].strip()
            old = "" if old == "(empty)" else old
            return (old + "\n" + "\n".join(f"- {f}" for f in facts)).strip()
        if "Summarize this conversation excerpt" in text:
            return "Hai bên nói về " + text.split("EXCERPT:")[1].strip().splitlines()[0][:60]
        if "long-term memory module" in text:
            user_msg = text.split("LATEST EXCHANGE:")[1].strip().splitlines()[0]
            if "tên" in user_msg:
                return json.dumps({"memories": [{"content": "chủ nhân tên là Minh", "kind": "fact", "importance": 0.95}]})
            return '{"memories": []}'
        return "{}"


def make_manager(tmp):
    cfg = load_config()
    cfg.embedding.provider = "hash"
    store = Store(Path(tmp) / "t.db")
    llm = FakeLLM()
    return MemoryManager(cfg, store, llm, Embedder(cfg)), store, llm


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mm, self.store, self.llm = make_manager(self.tmp)

    def tearDown(self):
        self.store.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_long_chat_does_not_forget(self):
        """150 turns into a tiny 1.5k-token context: early facts must survive via summary/episodes/archive."""
        cid = self.store.current_conversation()
        self.store.add_message(cid, "user", "Mật khẩu wifi nhà mình là hoa-sen-2024 nhé")
        self.store.add_message(cid, "assistant", "[happy] Dạ em nhớ rồi!")
        for i in range(150):
            self.store.add_message(cid, "user", f"Câu hỏi số {i} về chuyện linh tinh " + "bla " * 20)
            self.store.add_message(cid, "assistant", f"[neutral] Trả lời số {i} " + "ok " * 20)
            self.mm.build_messages(cid, "system prompt")
            self.run_async(self.mm.maybe_compact(cid))
        msgs = self.mm.build_messages(cid, "system prompt")
        window_tokens = self.mm.last_context_info["window_tokens"]
        self.assertLessEqual(window_tokens, self.mm.history_budget)
        self.assertNotIn("hoa-sen-2024", json.dumps(msgs, ensure_ascii=False))  # long gone from the window...
        summary = self.store.get_conversation(cid)["summary"]
        self.assertIn("hoa-sen-2024", summary)                                  # ...but kept in the summary
        block = self.run_async(self.mm.build_memory_block(cid, "mật khẩu wifi là gì nhỉ"))
        self.assertIn("hoa-sen-2024", block)                                    # and recalled verbatim
        self.assertEqual(self.store.count_messages(), 302)                      # nothing deleted

    def test_extract_and_dedupe(self):
        cid = self.store.current_conversation()
        self.run_async(self.mm.extract(cid, "Mình tên là Minh nha", "[happy] Chào Minh!"))
        self.run_async(self.mm.extract(cid, "Nhắc lại, tên mình là Minh", "[happy] Dạ nhớ rồi"))
        facts = self.store.list_memories(["fact"])
        self.assertEqual(len(facts), 1)
        block = self.run_async(self.mm.build_memory_block(cid, "xin chào"))
        self.assertIn("chủ nhân tên là Minh", block)

    def test_replace_and_forget(self):
        mid, _ = self.run_async(self.mm.remember("chủ nhân sống ở Hà Nội", "fact", 0.8))
        mid2, action = self.run_async(self.mm.remember("chủ nhân đã chuyển vào Sài Gòn", "fact", 0.8, replaces=mid))
        self.assertEqual((mid2, action), (mid, "updated"))
        self.assertTrue(self.mm.forget(mid))
        self.assertEqual(self.store.list_memories(["fact"]), [])

    def test_archive_search_is_accent_insensitive(self):
        cid = self.store.current_conversation()
        self.store.add_message(cid, "user", "Tuần sau mình đi Đà Nẵng công tác")
        rows = self.store.search_messages("da nang")
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
