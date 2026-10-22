"""Built-in tools: basics, web, code execution, memory, reminders. (files/source code: code.py)"""
from __future__ import annotations

import ast
import asyncio
import math
import operator
import os
import re
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from ..utils import date_str, now_str, truncate_middle
from . import ToolContext, tool

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")


def _ws(ctx: ToolContext) -> Path:
    p = Path(ctx.cfg.agent.workspace_path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_path(ctx: ToolContext, rel: str) -> Path:
    root = _ws(ctx).resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise PermissionError(f"Chỉ được thao tác trong thư mục workspace ({root}).")
    return target


# ------------------------------------------------------------------ basics
@tool("Xem ngày giờ hiện tại.", group="cơ bản")
def get_time() -> str:
    return now_str()


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")} | {"abs": abs, "round": round,
                                                                             "min": min, "max": max}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        if isinstance(node.op, ast.Pow) and abs(_eval(node.right)) > 1000:
            raise ValueError("số mũ quá lớn")
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    if isinstance(node, ast.Name) and node.id in ("pi", "e", "tau"):
        return getattr(math, node.id)
    raise ValueError("biểu thức không hỗ trợ")


@tool("Tính toán biểu thức toán học chính xác (hỗ trợ + - * / ** % và hàm math như sqrt, sin, log).",
      {"expression": "str: biểu thức, vd '12.5*3 + sqrt(16)'"}, group="cơ bản")
def calculator(expression: str) -> str:
    value = _eval(ast.parse(expression.replace("^", "**"), mode="eval"))
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return f"{expression} = {value}"


# --------------------------------------------------------------------- web
@tool("Tìm kiếm trên Internet (DuckDuckGo). Dùng khi cần thông tin mới/thời sự hoặc không chắc chắn.",
      {"query": "str: từ khóa tìm kiếm", "?max_results": "int: số kết quả, mặc định 5"}, group="web")
async def web_search(query: str, max_results: int = 5) -> str:
    max_results = max(1, min(int(max_results), 10))
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as c:
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "vn-vi"})
        soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for res in soup.select("div.result"):
        if "result--ad" in (res.get("class") or []):
            continue
        a = res.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href", "")
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        snippet = res.select_one(".result__snippet")
        results.append(f"{len(results) + 1}. {a.get_text(strip=True)}\n   {href}\n   "
                       f"{snippet.get_text(' ', strip=True) if snippet else ''}")
        if len(results) >= max_results:
            break
    if not results:
        return "Không tìm thấy kết quả (hoặc bị chặn tạm thời). Thử từ khóa khác."
    return "\n".join(results)


@tool("Đọc nội dung chữ của một trang web (URL).",
      {"url": "str: địa chỉ http(s)", "?max_chars": "int: giới hạn ký tự, mặc định 6000"}, group="web")
async def fetch_url(url: str, max_chars: int = 6000) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": UA}, follow_redirects=True) as c:
        r = await c.get(url)
    ctype = r.headers.get("content-type", "")
    if "html" in ctype:
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "svg", "form"]):
            tag.decompose()
        title = soup.title.get_text(strip=True) if soup.title else ""
        text = re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n"))
        text = re.sub(r"[ \t]+", " ", text).strip()
        text = f"# {title}\n{text}" if title else text
    else:
        text = r.text
    return f"[HTTP {r.status_code}] " + truncate_middle(text, int(max_chars))


@tool("Mở một trang web trên trình duyệt của chủ nhân.", {"url": "str: địa chỉ http(s)"}, group="web")
def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "Chỉ mở được link http(s)."
    webbrowser.open(url)
    return f"Đã mở {url}"


# -------------------------------------------------------------------- code
async def _run(cmd: list[str], cwd: Path, timeout: float, stdin: bytes | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), stdin=asyncio.subprocess.PIPE if stdin else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return f"[timeout sau {timeout}s]"
    text = out.decode("utf-8", errors="replace")
    return f"[exit {proc.returncode}]\n" + truncate_middle(text, 6000)


@tool("Chạy code Python (thư mục làm việc = workspace, có sẵn numpy/httpx/bs4). In kết quả bằng print().",
      {"code": "str: mã Python", "?timeout": "int: giây, mặc định 60"}, dangerous=True, group="code")
async def run_python(code: str, timeout: int = 60, ctx: ToolContext = None) -> str:
    ws = _ws(ctx)
    script = ws / f"_cazung_run_{int(time.time() * 1000)}.py"
    script.write_text(code, encoding="utf-8")
    try:
        return await _run([sys.executable, str(script)], ws, min(int(timeout), 600))
    finally:
        script.unlink(missing_ok=True)


@tool("Chạy lệnh shell trên máy chủ nhân (PowerShell trên Windows). Thư mục làm việc = workspace.",
      {"command": "str: lệnh", "?timeout": "int: giây, mặc định 60"}, dangerous=True, group="code")
async def run_shell(command: str, timeout: int = 60, ctx: ToolContext = None) -> str:
    ws = _ws(ctx)
    if sys.platform == "win32":
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]
    else:
        cmd = ["bash", "-lc", command]
    return await _run(cmd, ws, min(int(timeout), 600))


# ------------------------------------------------------------------ memory
@tool("Ghi nhớ lâu dài một thông tin quan trọng (về chủ nhân, yêu cầu, sự kiện...).",
      {"content": "str: câu đầy đủ cần nhớ",
       "?kind": "str: fact|preference|instruction|event|note",
       "?importance": "float: 0.1-1.0"}, group="trí nhớ")
async def remember(content: str, kind: str = "fact", importance: float = 0.75, ctx: ToolContext = None) -> str:
    mid, action = await ctx.memory.remember(content, kind, importance, source="tool", conv_id=ctx.conv_id)
    return f"Đã {'ghi nhớ' if action == 'added' else 'cập nhật'} ký ức #{mid}."


@tool("Tìm trong trí nhớ dài hạn theo ý nghĩa.", {"query": "str: điều cần nhớ lại", "?k": "int: số kết quả"}, group="trí nhớ")
async def recall(query: str, k: int = 6, ctx: ToolContext = None) -> str:
    hits = await ctx.memory.recall(query, int(k))
    if not hits:
        return "Không tìm thấy ký ức liên quan."
    return "\n".join(f"#{h['id']} [{h['kind']}, {date_str(h['created_at'])}] {h['content']}" for h in hits)


@tool("Xóa (quên) một ký ức sai hoặc lỗi thời theo id (lấy id từ recall).", {"memory_id": "int"}, group="trí nhớ")
def forget(memory_id: int, ctx: ToolContext = None) -> str:
    return "Đã quên." if ctx.memory.forget(int(memory_id)) else "Không thấy ký ức đó."


@tool("Tìm nguyên văn trong toàn bộ lịch sử trò chuyện (kể cả rất cũ) theo từ khóa.",
      {"query": "str: từ khóa", "?limit": "int: mặc định 8"}, group="trí nhớ")
def search_history(query: str, limit: int = 8, ctx: ToolContext = None) -> str:
    rows = ctx.store.search_messages(query, limit=int(limit))
    if not rows:
        return "Không thấy đoạn hội thoại nào khớp."
    name, user = ctx.cfg.persona.name, ctx.cfg.persona.user_title
    return "\n".join(
        f"[{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['created_at']))}] "
        f"{user if r['role'] == 'user' else name}: {truncate_middle(r['content'], 500)}" for r in rows)


@tool("Lưu một kỹ năng/quy trình vừa làm thành công để lần sau dùng lại.",
      {"name": "str: tên kỹ năng", "description": "str: dùng khi nào", "steps": "str: các bước thực hiện"}, group="trí nhớ")
async def save_skill(name: str, description: str, steps: str, ctx: ToolContext = None) -> str:
    mid, _ = await ctx.memory.save_skill(name, description, steps)
    return f"Đã lưu kỹ năng #{mid}."


# --------------------------------------------------------------- reminders
@tool("Đặt lời nhắc: Cazung sẽ gọi chủ nhân đúng giờ. Dùng 'minutes' (sau bao nhiêu phút) HOẶC 'at' (YYYY-MM-DD HH:MM hoặc HH:MM).",
      {"text": "str: nội dung nhắc", "?minutes": "float", "?at": "str"}, group="nhắc nhở")
def set_reminder(text: str, minutes: float | None = None, at: str | None = None, ctx: ToolContext = None) -> str:
    if minutes is not None:
        due = time.time() + float(minutes) * 60
    elif at:
        at = at.strip()
        due = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                due = datetime.strptime(at, fmt).timestamp()
                break
            except ValueError:
                pass
        if due is None:
            m = re.fullmatch(r"(\d{1,2})[:h](\d{2})?", at)
            if not m:
                return "Không hiểu thời gian. Dùng 'YYYY-MM-DD HH:MM', 'HH:MM' hoặc minutes."
            now = datetime.now()
            d = now.replace(hour=int(m.group(1)), minute=int(m.group(2) or 0), second=0, microsecond=0)
            due = d.timestamp() + (86400 if d <= now else 0)
    else:
        return "Cần 'minutes' hoặc 'at'."
    rid = ctx.store.add_reminder(text, due)
    return f"Đã đặt nhắc #{rid} lúc {time.strftime('%Y-%m-%d %H:%M', time.localtime(due))}: {text}"


@tool("Xem các lời nhắc đang chờ.", group="nhắc nhở")
def list_reminders(ctx: ToolContext = None) -> str:
    rows = ctx.store.pending_reminders()
    return "\n".join(f"#{r['id']} {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['due_at']))}: {r['text']}"
                     for r in rows) or "Không có lời nhắc nào."


@tool("Hủy lời nhắc theo id.", {"reminder_id": "int"}, group="nhắc nhở")
def cancel_reminder(reminder_id: int, ctx: ToolContext = None) -> str:
    ctx.store.mark_reminder(int(reminder_id), delivered=2)
    return "Đã hủy."
