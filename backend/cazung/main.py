"""FastAPI server: WebSocket chat + REST API + static frontend."""
from __future__ import annotations

import asyncio
import logging
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import tools as tools_pkg
from .agent import Agent
from .config import DATA_DIR, FRONTEND_DIR, PLUGINS_DIR, load_config
from .llm import LLM
from .memory import Embedder, MemoryManager, Store
from . import mcp
from .tools import builtin, code, email_tools, system  # noqa: F401  (registers built-in tools)
from .tools import registry
from .utils import split_emotion, visible_text
from .worker import Worker

cfg = load_config()
DATA_DIR.mkdir(parents=True, exist_ok=True)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(DATA_DIR / "cazung.log", encoding="utf-8")],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("cazung")
RENDERER_DIR = FRONTEND_DIR / "renderer"
PORT = int(cfg.server.port)
ALLOWED_ORIGINS = {"null", "file://", f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

S = SimpleNamespace()  # app state


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def send(self, ws: WebSocket, event: dict):
        try:
            await ws.send_json(event)
        except Exception:  # noqa: BLE001
            self.clients.discard(ws)

    async def broadcast(self, event: dict):
        for ws in list(self.clients):
            await self.send(ws, event)


async def reminder_loop():
    while True:
        await asyncio.sleep(10)
        if not S.hub.clients:
            continue  # keep reminders until Cazung's window is connected
        for r in S.store.due_reminders():
            S.store.mark_reminder(r["id"])
            S.store.add_message(S.store.current_conversation(), "assistant",
                                f"[surprised] ⏰ Nhắc {cfg.persona.user_title}: {r['text']}")
            await S.hub.broadcast({"type": "reminder", "id": r["id"], "text": r["text"]})


async def maintenance_loop():
    await asyncio.sleep(20)
    while True:
        S.worker.submit("maintenance", S.memory.maintenance)
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.store = Store(DATA_DIR / "cazung.db")
    S.llm = LLM(cfg)
    S.embedder = Embedder(cfg)
    S.memory = MemoryManager(cfg, S.store, S.llm, S.embedder)
    S.worker = Worker()
    S.agent = Agent(cfg, S.store, S.llm, S.memory, S.worker)
    S.worker.is_busy = lambda: S.agent.busy
    S.hub = Hub()
    plugins = tools_pkg.load_plugins(PLUGINS_DIR)
    if not cfg.agent.enable_shell:
        registry.disabled.add("run_shell")
    if not cfg.agent.enable_python:
        registry.disabled.add("run_python")
    if not (cfg.email.enabled and cfg.email.username):
        registry.disabled.update({"read_emails", "send_email"})
    cfg.agent.workspace_path.mkdir(parents=True, exist_ok=True)
    S.token = secrets.token_urlsafe(24)
    (DATA_DIR / ".token").write_text(S.token, encoding="utf-8")
    S.mcp = []

    async def start_mcp():
        S.mcp = await mcp.start_all(cfg.raw.get("mcp", []))

    tasks = [asyncio.create_task(S.worker.run()), asyncio.create_task(reminder_loop()),
             asyncio.create_task(maintenance_loop()), asyncio.create_task(start_mcp())]
    await S.embedder.embed(["ping"])  # detect early whether the embedding model is installed
    health = await S.llm.health()
    log.info("Cazung ready on http://127.0.0.1:%s | model=%s (%s) | tools=%d | plugins=%s",
             PORT, cfg.llm.model, "OK" if health.get("model_available") else "MISSING",
             len(registry.available()), plugins)
    yield
    for t in tasks:
        t.cancel()
    for server in S.mcp:
        await server.stop()
    await S.llm.close()
    await S.embedder.close()


app = FastAPI(title="Cazung", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=sorted(ALLOWED_ORIGINS), allow_methods=["*"],
                   allow_headers=["*"])


def require_token(request: Request):
    token = request.headers.get("x-cazung-token") or request.query_params.get("token")
    if not token or not secrets.compare_digest(token, S.token):
        raise HTTPException(401, "bad token")


# ------------------------------------------------------------------ REST
@app.get("/api/health")
async def health():
    llm = await S.llm.health()
    return {"ok": True, "name": cfg.persona.name, "model": cfg.llm.model, "llm": llm,
            "embedding": S.embedder.active_id, "busy": S.agent.busy}


@app.get("/api/config", dependencies=[Depends(require_token)])
async def get_config():
    return {"name": cfg.persona.name, "user_title": cfg.persona.user_title,
            "self_pronoun": cfg.persona.self_pronoun, "pet": cfg.raw["pet"], "model": cfg.llm.model}


@app.get("/api/history", dependencies=[Depends(require_token)])
async def history(limit: int = 80):
    out = []
    for m in S.store.recent_messages(S.store.current_conversation(), limit * 2):
        if m["role"] == "user":
            out.append({"id": m["id"], "role": "user", "text": m["content"], "ts": m["created_at"]})
        elif m["role"] == "assistant":
            emo, _ = split_emotion(m["content"])
            text = visible_text(m["content"])
            if text:
                out.append({"id": m["id"], "role": "assistant", "text": text, "emotion": emo, "ts": m["created_at"]})
    return out[-limit:]


@app.get("/api/memories", dependencies=[Depends(require_token)])
async def memories(kind: str | None = None):
    kinds = kind.split(",") if kind else None
    return S.store.list_memories(kinds)


class MemoryIn(BaseModel):
    content: str
    kind: str = "fact"
    importance: float = 0.8


@app.post("/api/memories", dependencies=[Depends(require_token)])
async def add_memory(m: MemoryIn):
    mid, action = await S.memory.remember(m.content, m.kind, m.importance, source="user")
    return {"id": mid, "action": action}


@app.delete("/api/memories/{mid}", dependencies=[Depends(require_token)])
async def delete_memory(mid: int):
    return {"ok": S.memory.forget(mid)}


@app.get("/api/stats", dependencies=[Depends(require_token)])
async def stats():
    cid = S.store.current_conversation()
    conv = S.store.get_conversation(cid)
    return {"conversation": cid, "messages_total": S.store.count_messages(),
            "unsummarized": len(S.store.unsummarized(cid)), "summary": conv["summary"] if conv else "",
            "memories": S.store.memory_counts(), "context": S.memory.last_context_info,
            "embedding": S.embedder.active_id, "background_jobs": S.worker.pending,
            "background_current": S.worker.current, "turns": S.store.kv_get("turns", 0),
            "last_reflection": S.store.kv_get("last_reflection")}


@app.post("/api/session/new", dependencies=[Depends(require_token)])
async def new_session():
    old = S.store.current_conversation()
    cid = S.store.new_conversation()
    S.worker.submit("compact-old", S.memory.maybe_compact, old, True)
    return {"conversation": cid}


# ------------------------------------------------------------- WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    origin = ws.headers.get("origin")
    token = ws.query_params.get("token", "")
    if (origin and origin not in ALLOWED_ORIGINS) or not secrets.compare_digest(token, S.token):
        await ws.close(code=4403)
        return
    await ws.accept()
    S.hub.clients.add(ws)
    pending: dict[str, asyncio.Future] = {}
    task: asyncio.Task | None = None

    async def emit(event: dict):
        await S.hub.send(ws, event)

    async def confirm(name: str, args: dict) -> bool:
        cid = uuid.uuid4().hex[:10]
        fut = asyncio.get_running_loop().create_future()
        pending[cid] = fut
        await emit({"type": "confirm_request", "id": cid, "name": name, "args": args})
        try:
            return await asyncio.wait_for(fut, 300)
        except asyncio.TimeoutError:
            return False
        finally:
            pending.pop(cid, None)

    await emit({"type": "hello", "name": cfg.persona.name, "model": cfg.llm.model, "time": time.time()})
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "chat":
                if task and not task.done():
                    await emit({"type": "error", "message": "Cazung đang trả lời, đợi chút hoặc bấm dừng nha."})
                    continue
                task = asyncio.create_task(S.agent.chat(str(msg.get("text", "")), emit, confirm))
            elif kind == "confirm":
                fut = pending.get(msg.get("id"))
                if fut and not fut.done():
                    fut.set_result(bool(msg.get("approved")))
            elif kind == "stop":
                if task and not task.done():
                    task.cancel()
            elif kind == "feedback":
                mid = msg.get("message_id")
                if mid:
                    rating = 1 if msg.get("rating", 0) > 0 else -1
                    S.store.add_feedback(int(mid), rating, str(msg.get("comment", ""))[:1000])
                    if rating < 0:
                        S.worker.submit("reflect", S.memory.reflect)
            elif kind == "ping":
                await emit({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        S.hub.clients.discard(ws)
        for fut in pending.values():
            if not fut.done():
                fut.set_result(False)


# ------------------------------------------------------- static frontend
@app.get("/", response_class=HTMLResponse)
async def index():
    html = (RENDERER_DIR / "index.html").read_text(encoding="utf-8")
    meta = f'<meta name="cazung-token" content="{S.token}">'
    return html.replace("<!--TOKEN-->", meta)


app.mount("/", StaticFiles(directory=str(RENDERER_DIR)), name="static")
