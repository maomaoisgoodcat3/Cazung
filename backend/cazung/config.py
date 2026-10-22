"""Load config.toml (project root) on top of built-in defaults."""
from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
DATA_DIR = Path(os.environ.get("CAZUNG_DATA") or ROOT / "data")
PLUGINS_DIR = BACKEND_DIR / "plugins"
CONFIG_PATH = Path(os.environ.get("CAZUNG_CONFIG") or ROOT / "config.toml")

DEFAULTS: dict = {
    "server": {"host": "127.0.0.1", "port": 8765},
    "llm": {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model": "dolphin3",
        "api_key": "",
        "temperature": 0.8,
        "num_ctx": 8192,
        "max_response_tokens": 1024,
        "repeat_penalty": 1.1,
        "think": False,
        "think_max_tokens": 2048,
        "keep_alive": "30m",
        "utility_model": "",
    },
    "embedding": {"provider": "ollama", "base_url": "", "model": "nomic-embed-text", "api_key": ""},
    "persona": {
        "name": "Cazung",
        "user_title": "chủ nhân",
        "self_pronoun": "em",
        "language": "Vietnamese",
        "extra": "",
    },
    "memory": {
        "recent_keep_messages": 8,
        "compact_trigger": 0.75,
        "summary_max_words": 450,
        "core_max_items": 25,
        "recall_top_k": 6,
        "recall_min_score": 0.35,
        "history_snippets": 3,
        "reflect_every_turns": 10,
        "dedupe_threshold": 0.9,
    },
    "agent": {
        "max_steps": 6,
        "confirm_dangerous": True,
        "tool_timeout": 90,
        "workspace": "data/workspace",
        "enable_shell": True,
        "enable_python": True,
    },
    "email": {
        "enabled": False, "smtp_host": "smtp.gmail.com", "smtp_port": 587, "imap_host": "imap.gmail.com",
        "imap_port": 993, "username": "", "password": "", "from_name": "",
    },
    "mcp": [],
    "pet": {"scale": 4, "wander": True, "idle_chatter": True, "sleep_after_minutes": 5},
}


def _merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _ns(d: dict) -> SimpleNamespace:
    return SimpleNamespace(**{k: _ns(v) if isinstance(v, dict) else v for k, v in d.items()})


def load_config() -> SimpleNamespace:
    raw = DEFAULTS
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            raw = _merge(DEFAULTS, tomllib.load(f))
    cfg = _ns(raw)
    cfg.raw = raw
    ws = Path(cfg.agent.workspace)
    cfg.agent.workspace_path = ws if ws.is_absolute() else ROOT / ws
    return cfg
