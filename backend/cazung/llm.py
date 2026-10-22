"""LLM providers: Ollama native API and any OpenAI-compatible server.

Tool calling is done with a text protocol (see agent.py), so any chat model works,
including uncensored fine-tunes such as Dolphin that have no native tool support.
"""
from __future__ import annotations

import json
import logging
import re
from typing import AsyncIterator

import httpx

log = logging.getLogger("cazung.llm")

TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)
_THINK_BLOCK = re.compile(r"<think>.*?(</think>|$)", re.S)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg):
        c = cfg.llm
        self.provider = c.provider.lower()
        self.base_url = c.base_url.rstrip("/")
        self.model = c.model
        self.utility_model = c.utility_model or c.model
        self.api_key = c.api_key
        self.temperature = c.temperature
        self.num_ctx = int(c.num_ctx)
        self.max_response_tokens = int(c.max_response_tokens)
        self.keep_alive = c.keep_alive
        self.repeat_penalty = float(getattr(c, "repeat_penalty", 1.1))
        self.think = bool(getattr(c, "think", False))
        self.think_max_tokens = int(getattr(c, "think_max_tokens", 2048))
        self.client = httpx.AsyncClient(timeout=TIMEOUT)

    async def close(self):
        await self.client.aclose()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    # ------------------------------------------------------------------ chat
    async def stream_chat(
        self,
        messages: list[dict],
        *,
        stop: list[str] | None = None,
        temperature: float | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[tuple[str, str]]:
        """Yields ("content", text) and, for reasoning models, ("thinking", text)."""
        model = model or self.model
        temperature = self.temperature if temperature is None else temperature
        max_tokens = max_tokens or self.max_response_tokens
        if self.provider == "ollama":
            think = self.think
            payload = {
                "model": model,
                "messages": messages,
                "stream": True,
                "keep_alive": self.keep_alive,
                "options": {
                    "temperature": temperature,
                    "num_ctx": self.num_ctx,
                    "num_predict": max_tokens + (self.think_max_tokens if think else 0),
                    "repeat_penalty": self.repeat_penalty,
                    "stop": stop or [],
                },
            }
            if think:
                payload["think"] = True
            async with self.client.stream("POST", f"{self.base_url}/api/chat", json=payload) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode("utf-8", "replace")
                    if think and "thinking" in body.lower():
                        log.warning("model %s does not support thinking mode; disabling it", model)
                        self.think = False
                        async for item in self.stream_chat(messages, stop=stop, temperature=temperature,
                                                           model=model, max_tokens=max_tokens):
                            yield item
                        return
                    raise LLMError(f"Ollama {r.status_code}: {body[:300]}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise LLMError(data["error"])
                    msg = data.get("message") or {}
                    if msg.get("thinking"):
                        yield "thinking", msg["thinking"]
                    if msg.get("content"):
                        yield "content", msg["content"]
                    if data.get("done"):
                        break
        else:
            payload = {
                "model": model,
                "messages": messages,
                "stream": True,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if stop:
                payload["stop"] = stop[:4]
            url = f"{self.base_url}/v1/chat/completions"
            async with self.client.stream("POST", url, json=payload, headers=self._headers()) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode("utf-8", "replace")
                    raise LLMError(f"LLM server {r.status_code}: {body[:300]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    data = json.loads(chunk)
                    choices = data.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                        if reasoning:
                            yield "thinking", reasoning
                        if delta.get("content"):
                            yield "content", delta["content"]

    async def complete(
        self,
        messages: list[dict] | str,
        *,
        json_mode: bool = False,
        temperature: float = 0.3,
        utility: bool = True,
        max_tokens: int | None = None,
    ) -> str:
        """Non-streaming completion for internal tasks (summaries, memory extraction...)."""
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        model = self.utility_model if utility else self.model
        if self.provider == "ollama":
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "keep_alive": self.keep_alive,
                "options": {
                    "temperature": temperature,
                    "num_ctx": self.num_ctx,
                    "num_predict": max_tokens or self.max_response_tokens,
                },
            }
            if json_mode:
                payload["format"] = "json"
            if self.think and model == self.model:
                payload["think"] = False  # background jobs don't need visible reasoning
            r = await self.client.post(f"{self.base_url}/api/chat", json=payload)
            if r.status_code != 200:
                raise LLMError(f"Ollama {r.status_code}: {r.text[:300]}")
            return _THINK_BLOCK.sub("", r.json()["message"]["content"]).strip()
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens or self.max_response_tokens,
        }
        r = await self.client.post(
            f"{self.base_url}/v1/chat/completions", json=payload, headers=self._headers()
        )
        if r.status_code != 200:
            raise LLMError(f"LLM server {r.status_code}: {r.text[:300]}")
        return _THINK_BLOCK.sub("", r.json()["choices"][0]["message"]["content"] or "").strip()

    # ---------------------------------------------------------------- status
    async def health(self) -> dict:
        try:
            if self.provider == "ollama":
                r = await self.client.get(f"{self.base_url}/api/tags", timeout=5)
                names = [m["name"] for m in r.json().get("models", [])]
                has = any(n == self.model or n.split(":")[0] == self.model.split(":")[0] for n in names)
                return {"ok": True, "model_available": has, "models": names}
            r = await self.client.get(f"{self.base_url}/v1/models", headers=self._headers(), timeout=5)
            names = [m.get("id") for m in r.json().get("data", [])]
            return {"ok": True, "model_available": self.model in names or not names, "models": names}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "model_available": False, "models": []}
