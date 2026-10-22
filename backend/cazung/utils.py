from __future__ import annotations

import ast
import json
import re
import time

EMOTIONS = ("happy", "neutral", "sad", "surprised", "angry", "shy", "love", "thinking", "sleepy", "smug")
_EMO_RE = re.compile(r"^\s*\[([a-zA-Z_]{2,12})\]\s*")
_TOOL_RE = re.compile(r"<tool>.*?(</tool>|$)", re.S)

WEEKDAYS_VI = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def now_str(ts: float | None = None) -> str:
    t = time.localtime(ts or time.time())
    return f"{WEEKDAYS_VI[t.tm_wday]}, {time.strftime('%Y-%m-%d %H:%M', t)}"


def date_str(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def split_emotion(text: str) -> tuple[str | None, str]:
    m = _EMO_RE.match(text)
    if m and m.group(1).lower() in EMOTIONS:
        return m.group(1).lower(), text[m.end():]
    return None, text


def visible_text(assistant_raw: str) -> str:
    """What the user should see from a stored assistant message."""
    _, text = split_emotion(assistant_raw)
    return _TOOL_RE.sub("", text).strip()


def parse_json_loose(text: str):
    """Best-effort JSON extraction from LLM output."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I | re.M).strip()
    # first complete JSON value, ignoring trailing junk such as an extra "}"; also repair missing closers
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if starts:
        body = t[min(starts):]
        decoder = json.JSONDecoder()
        for suffix in ("", "}", "}}", "]}", "}]}", "\"}", "\"}}"):
            try:
                return decoder.raw_decode(body + suffix)[0]
            except ValueError:
                pass
    candidates = [t]
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = t.find(open_c), t.rfind(close_c)
        if a != -1 and b > a:
            candidates.append(t[a : b + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except Exception:  # noqa: BLE001
            pass
        fixed = re.sub(r",\s*([}\]])", r"\1", c)
        try:
            return json.loads(fixed)
        except Exception:  # noqa: BLE001
            pass
        try:
            return ast.literal_eval(
                fixed.replace("null", "None").replace("true", "True").replace("false", "False")
            )
        except Exception:  # noqa: BLE001
            pass
    return None


_REPEAT = re.compile(r"(.{1,16}?)\1{6,}", re.S)


def is_looping(text: str, window: int = 160) -> bool:
    """True when the tail of a generation is one short pattern repeated (degenerate loop)."""
    if len(text) < window + 40:
        return False
    tail = text[-window:]
    for p in range(1, 17):
        unit = tail[-p:]
        if unit.strip() and (unit * (window // p + 2))[-window:] == tail:
            return True
    return False


def collapse_repeats(text: str) -> str:
    return _REPEAT.sub(lambda m: m.group(1) * 3 + "…", text)


def truncate_middle(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n…[đã lược {len(text) - max_chars} ký tự]…\n" + text[-half:]
