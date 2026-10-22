"""MemoryManager - how Cazung avoids forgetting.

Layers (from most to least verbatim):

1. Short-term window  - the newest messages of the conversation, sent verbatim and
                        fitted to a token budget computed every turn.
2. Rolling summary    - when the window grows past `compact_trigger`, the oldest messages
                        are folded into a running summary of the conversation (kept in the
                        prompt), and marked as summarized. Nothing is ever deleted.
3. Episodic memory    - each compacted chunk also becomes a short "episode" with an
                        embedding, so details from weeks ago can be recalled by meaning.
4. Semantic memory    - durable facts / preferences / instructions about the user, extracted
                        automatically after every turn (with dedupe + contradiction updates).
                        The most important ones are always in the prompt ("core memory").
5. Archive search     - full-text search over every message ever exchanged, injected as
                        snippets when keywords match and exposed as the `search_history` tool.
6. Procedural memory  - lessons and skills learned from feedback and self-reflection.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import defaultdict

import numpy as np

from ..utils import collapse_repeats, date_str, now_str, parse_json_loose, split_emotion, truncate_middle
from .embeddings import Embedder, from_blob, to_blob, tokenize
from .store import Store, est_tokens

log = logging.getLogger("cazung.memory")

CORE_KINDS = ["instruction", "fact", "preference"]
RECALL_KINDS = ["fact", "preference", "instruction", "event", "episode", "note"]
ALL_KINDS = ["instruction", "fact", "preference", "event", "episode", "lesson", "skill", "note"]

SUMMARY_PROMPT = """You maintain the running memory of a long conversation between {user} (the human) and {ai} (the AI assistant).
Merge the EXISTING SUMMARY and the NEW MESSAGES into ONE updated summary.
Rules:
- Keep every important detail: names, numbers, dates, decisions, preferences, promises, tasks in progress and their status, open questions, files/links/code mentioned, what {user} asked {ai} to do.
- Drop greetings, small talk and repetition. When information conflicts, keep the newer one.
- Write compact bullet points in {language}, in chronological order, at most {max_words} words in total.
- Output ONLY the summary, no preamble.

EXISTING SUMMARY:
{summary}

NEW MESSAGES:
{transcript}"""

EPISODE_PROMPT = """Summarize this conversation excerpt from {date} in 1-3 sentences in {language}.
Focus on concrete facts: what {user} said/asked, what was done or decided, results. No preamble.

EXCERPT:
{transcript}"""

EXTRACT_PROMPT = """You are the long-term memory module of {ai}, the personal AI assistant of {user}.
Read the LATEST EXCHANGE and extract NEW durable information worth remembering for future conversations:
- facts about {user}: real name (ALWAYS save it if stated), age, job, location, family, friends, pets, possessions, health, schedule, projects...
- {user}'s preferences, likes/dislikes, habits, opinions, goals, plans
- instructions about how {ai} should behave, speak or answer (kind "instruction")
- important events, appointments, deadlines, commitments (kind "event", include the date if known; today is {today})
Do NOT extract: small talk, general knowledge, temporary context, things {ai} said about itself, or anything already in KNOWN MEMORIES (unless it changed).
If a known memory is outdated or contradicted, output the corrected version with "replaces" set to its id.
Each memory must be ONE self-contained sentence in {language}, about {user} in third person (e.g. "{user} thích uống trà sữa ít đường").
importance: 0.9-1.0 identity/critical instructions, 0.6-0.8 stable preferences & facts, 0.3-0.5 minor details.

Return JSON only: {{"memories": [{{"content": "...", "kind": "fact|preference|instruction|event", "importance": 0.7, "replaces": null}}]}}
Return {{"memories": []}} if nothing is worth saving.

KNOWN MEMORIES:
{known}

LATEST EXCHANGE:
{user}: {user_msg}
{ai}: {ai_msg}"""

REFLECT_PROMPT = """You are the self-improvement module of {ai}, the personal AI assistant of {user}.
Review the RECENT INTERACTIONS and FEEDBACK. Derive concrete, reusable lessons that will make {ai} serve {user} better:
mistakes to avoid, what {user} liked/disliked about answers (style, length, tone, language), better ways to use tools, recurring needs.
If a multi-step task was completed successfully with tools, also describe it as a reusable skill (name, when to use it, steps).
Only include specific, genuinely useful items that are not already in EXISTING LESSONS. Write in {language}.

Return JSON only: {{"lessons": [{{"content": "...", "importance": 0.6}}], "skills": [{{"name": "...", "description": "...", "steps": "..."}}]}}

EXISTING LESSONS:
{lessons}

FEEDBACK FROM {user}:
{feedback}

RECENT INTERACTIONS:
{transcript}"""


class MemoryManager:
    def __init__(self, cfg, store: Store, llm, embedder: Embedder):
        self.cfg = cfg
        self.m = cfg.memory
        self.store = store
        self.llm = llm
        self.embedder = embedder
        self.user = cfg.persona.user_title
        self.ai = cfg.persona.name
        self.language = cfg.persona.language
        self._index_cache = None
        self._compact_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.history_budget = (llm.num_ctx - llm.max_response_tokens) // 2
        self.last_context_info: dict = {}

    # ================================================================ index
    def _invalidate(self):
        self._index_cache = None

    def _index(self, model_id: str):
        c = self._index_cache
        if c and c[0] == model_id:
            return c
        rows = [r for r in self.store.list_memories(with_embedding=True)
                if r["emb_model"] == model_id and r["embedding"]]
        ids = np.array([r["id"] for r in rows], dtype=np.int64)
        mat = np.stack([from_blob(r["embedding"]) for r in rows]) if rows else np.zeros((0, 1), np.float32)
        meta = {r["id"]: {k: v for k, v in r.items() if k != "embedding"} for r in rows}
        self._index_cache = (model_id, ids, mat, meta)
        return self._index_cache

    def _search(self, qvec: np.ndarray, model_id: str, query: str, k: int,
                kinds: list[str] | None = None, exclude: set[int] | None = None,
                min_score: float | None = None) -> list[tuple[float, dict]]:
        _, ids, mat, meta = self._index(model_id)
        if not len(ids) or mat.shape[1] != qvec.shape[0]:
            return []
        lexical = model_id.startswith("hash")
        if min_score is None:
            min_score = 0.12 if lexical else float(self.m.recall_min_score)
        sims = mat @ qvec
        qwords = set(tokenize(query))
        now = time.time()
        out = []
        for idx in np.argsort(-sims)[: max(k * 6, 20)]:
            sim = float(sims[idx])
            if sim < min_score:
                break
            row = meta[int(ids[idx])]
            if kinds and row["kind"] not in kinds:
                continue
            if exclude and row["id"] in exclude:
                continue
            recency = math.exp(-(now - row["updated_at"]) / 86400 / 45)
            overlap = len(qwords & set(tokenize(row["content"]))) / max(1, len(qwords))
            score = sim + 0.12 * row["importance"] + 0.05 * recency + 0.1 * overlap
            out.append((score, row))
        out.sort(key=lambda x: -x[0])
        return out[:k]

    # ============================================================ long-term
    async def remember(self, content: str, kind: str = "fact", importance: float = 0.6,
                       source: str = "", conv_id: str = "", replaces: int | None = None) -> tuple[int | None, str]:
        content = (content or "").strip()
        if len(content) < 3:
            return None, "skipped"
        kind = kind if kind in ALL_KINDS else "fact"
        importance = max(0.05, min(1.0, float(importance)))
        vecs, model_id = await self.embedder.embed([content])
        vec = vecs[0]
        if replaces:
            old = self.store.get_memory(int(replaces))
            if old and old["active"]:
                self.store.update_memory(
                    old["id"], content=content, kind=kind, importance=max(importance, old["importance"]),
                    embedding=to_blob(vec), emb_model=model_id, source=source or old["source"],
                )
                self._invalidate()
                log.info("memory #%s updated: %s", old["id"], content)
                return old["id"], "updated"
        _, ids, mat, meta = self._index(model_id)
        if len(ids) and mat.shape[1] == vec.shape[0]:
            sims = mat @ vec
            threshold = 0.8 if model_id.startswith("hash") else float(self.m.dedupe_threshold)
            for idx in np.argsort(-sims)[:5]:
                if sims[idx] < threshold:
                    break
                row = meta[int(ids[idx])]
                same_group = (row["kind"] == "episode") == (kind == "episode")
                if not same_group:
                    continue
                self.store.update_memory(
                    row["id"], content=content, importance=max(importance, row["importance"]),
                    embedding=to_blob(vec), emb_model=model_id,
                )
                self._invalidate()
                return row["id"], "merged"
        mid = self.store.add_memory(kind, content, importance, to_blob(vec), model_id, source, conv_id)
        self._invalidate()
        log.info("memory #%s added (%s, %.2f): %s", mid, kind, importance, content)
        return mid, "added"

    async def recall(self, query: str, k: int = 6, kinds: list[str] | None = None) -> list[dict]:
        vecs, model_id = await self.embedder.embed([query])
        hits = self._search(vecs[0], model_id, query, k, kinds=kinds)
        self.store.touch_memories([r["id"] for _, r in hits])
        return [dict(r, score=round(s, 3)) for s, r in hits]

    def forget(self, memory_id: int) -> bool:
        row = self.store.get_memory(memory_id)
        if not row or not row["active"]:
            return False
        self.store.deactivate_memory(memory_id)
        self._invalidate()
        return True

    # ======================================================== prompt context
    async def build_memory_block(self, conv_id: str, query: str) -> str:
        """Everything Cazung should 'remember' for this turn, as one text block."""
        sections: list[str] = []
        info: dict = {}

        # core memory: who the user is + standing instructions (always present)
        core = self.store.list_memories(CORE_KINDS)
        core.sort(key=lambda r: (r["kind"] != "instruction", -r["importance"], -r["updated_at"]))
        core_lines, core_ids, used = [], set(), 0
        for r in core[: int(self.m.core_max_items)]:
            line = f"- {r['content']}"
            used += est_tokens(line)
            if used > 1100:
                break
            core_lines.append(line)
            core_ids.add(r["id"])
        if core_lines:
            sections.append(f"## What you know about {self.user}\n" + "\n".join(core_lines))

        # relevant long-term memories for this message
        vecs, model_id = await self.embedder.embed([query])
        qvec = vecs[0]
        hits = self._search(qvec, model_id, query, int(self.m.recall_top_k), kinds=RECALL_KINDS, exclude=core_ids)
        if hits:
            lines = [f"- ({date_str(r['created_at'])}) {r['content']}" for _, r in hits]
            sections.append("## Related memories\n" + "\n".join(lines))

        # procedural memory: lessons + skills
        lesson_hits = self._search(qvec, model_id, query, 3, kinds=["lesson"])
        lesson_ids = {r["id"] for _, r in lesson_hits}
        top_lessons = [r for r in self.store.list_memories(["lesson"]) if r["importance"] >= 0.75
                       and r["id"] not in lesson_ids][:3]
        lessons = [r for _, r in lesson_hits] + top_lessons
        if lessons:
            sections.append("## Lessons you learned (follow them)\n" + "\n".join(f"- {r['content']}" for r in lessons))
        skill_hits = self._search(qvec, model_id, query, 2, kinds=["skill"],
                                  min_score=None if not model_id.startswith("hash") else 0.2)
        if skill_hits:
            sections.append("## Skills you saved that may help\n" + "\n".join(f"- {r['content']}" for _, r in skill_hits))
        self.store.touch_memories([r["id"] for _, r in hits + lesson_hits + skill_hits])

        # archive snippets: verbatim old messages matching the keywords
        unsum = self.store.unsummarized(conv_id)
        first_window_id = unsum[0]["id"] if unsum else 10**12
        n_snip = int(self.m.history_snippets)
        if n_snip > 0:
            snippets = self.store.search_messages(query, limit=n_snip, exclude_conv_recent=(conv_id, first_window_id))
            if snippets:
                lines = []
                for s in snippets:
                    who = self.user if s["role"] == "user" else self.ai
                    _, txt = split_emotion(s["content"])
                    lines.append(f"- [{time.strftime('%Y-%m-%d %H:%M', time.localtime(s['created_at']))}] "
                                 f"{who}: {truncate_middle(txt.strip(), 300)}")
                sections.append("## Old conversation snippets (verbatim)\n" + "\n".join(lines))

        reminders = self.store.pending_reminders()[:5]
        if reminders:
            sections.append("## Pending reminders\n" + "\n".join(
                f"- {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['due_at']))}: {r['text']}" for r in reminders))

        conv = self.store.get_conversation(conv_id)
        if conv and conv["summary"]:
            sections.append("## Summary of this conversation so far (older messages)\n" + conv["summary"])

        info.update(core=len(core_lines), recalled=len(hits), lessons=len(lessons), skills=len(skill_hits),
                    embed=model_id)
        self.last_context_info = info
        header = (f"# MEMORY\nPrivate memory of {self.ai}. Use it naturally as your own memory; "
                  f"never mention this section or 'database'.\n")
        return header + "\n\n".join(sections) + f"\n\n## Now\n{now_str()}"

    def build_messages(self, conv_id: str, system_text: str) -> list[dict]:
        """System prompt + as many recent messages as fit the token budget."""
        msgs = self.store.unsummarized(conv_id)
        budget = self.llm.num_ctx - self.llm.max_response_tokens - est_tokens(system_text) - 64
        self.history_budget = max(600, budget)
        last_user = max((i for i, m in enumerate(msgs) if m["role"] == "user"), default=0)
        picked: list[dict] = []
        used = 0
        for i in range(len(msgs) - 1, -1, -1):
            m = msgs[i]
            content = m["content"]
            if m["role"] == "tool" and i < last_user:
                content = truncate_middle(content, 700)  # old tool output: keep only the gist
            t = est_tokens(content)
            if used + t > self.history_budget:
                if picked:
                    break
                content = truncate_middle(content, int(self.history_budget * 2.8))  # one giant message
                t = est_tokens(content)
            used += t
            picked.append({"role": m["role"], "content": content})
        picked.reverse()
        # never start the window with a dangling tool result / assistant tool call
        while len(picked) > 1 and picked[0]["role"] != "user":
            picked.pop(0)
        out = [{"role": "system", "content": system_text}]
        for i, m in enumerate(picked):
            if m["role"] == "tool":
                content = m["content"]
                if i == len(picked) - 1:  # fresh result: tell the model what to do next
                    content += (f"\n\n[SYSTEM] Use this result to continue: answer {self.user} now "
                                f"(start with an emotion tag), or call another tool if really needed.")
                out.append({"role": "user", "content": content})
            else:
                out.append(m)
        self.last_context_info.update(window_messages=len(picked), window_tokens=used,
                                      total_unsummarized=len(msgs), history_budget=self.history_budget)
        return out

    # =========================================================== compaction
    def _transcript(self, msgs: list[dict], max_chars_each: int = 1500) -> str:
        lines = []
        for m in msgs:
            ts = time.strftime("%m-%d %H:%M", time.localtime(m["created_at"]))
            if m["role"] == "user":
                lines.append(f"[{ts}] {self.user}: {truncate_middle(m['content'], max_chars_each)}")
            elif m["role"] == "assistant":
                _, txt = split_emotion(m["content"])
                txt = txt.replace("<tool>", "(dùng công cụ: ").replace("</tool>", ")")
                lines.append(f"[{ts}] {self.ai}: {truncate_middle(txt.strip(), max_chars_each)}")
            elif m["role"] == "tool":
                lines.append(f"[{ts}] (kết quả công cụ) {truncate_middle(m['content'], 400)}")
        return collapse_repeats("\n".join(lines))

    async def maybe_compact(self, conv_id: str, force: bool = False) -> bool:
        lock = self._compact_locks[conv_id]
        if lock.locked():
            return False
        async with lock:
            msgs = self.store.unsummarized(conv_id)
            if not msgs:
                return False
            total = sum(m["tokens"] for m in msgs)
            keep_n = int(self.m.recent_keep_messages)
            if not force and (total <= self.history_budget * float(self.m.compact_trigger) or len(msgs) <= keep_n):
                return False
            if force:
                keep_from = len(msgs)
            else:
                target = self.history_budget * 0.45
                keep_from, kept = len(msgs), 0
                for i in range(len(msgs) - 1, -1, -1):
                    if (len(msgs) - i) > keep_n and kept + msgs[i]["tokens"] > target:
                        break
                    kept += msgs[i]["tokens"]
                    keep_from = i
                while 0 < keep_from < len(msgs) and msgs[keep_from]["role"] != "user":
                    keep_from -= 1  # don't split a turn; keep the whole turn verbatim
            chunk = msgs[:keep_from]
            if not chunk:
                return False

            conv = self.store.get_conversation(conv_id)
            summary = conv["summary"] if conv else ""
            # fold in pieces small enough for the model's context
            piece_budget = max(1200, int(self.llm.num_ctx * 0.45))
            pieces, cur, cur_t = [], [], 0
            for m in chunk:
                t = min(m["tokens"], 600)
                if cur and cur_t + t > piece_budget:
                    pieces.append(cur)
                    cur, cur_t = [], 0
                cur.append(m)
                cur_t += t
            if cur:
                pieces.append(cur)

            for piece in pieces:
                transcript = self._transcript(piece)
                new_summary = (await self.llm.complete(SUMMARY_PROMPT.format(
                    user=self.user, ai=self.ai, language=self.language,
                    max_words=int(self.m.summary_max_words), summary=summary or "(empty)",
                    transcript=transcript), temperature=0.2)).strip()
                if len(new_summary) < 20:
                    raise RuntimeError("summary generation failed")
                summary = new_summary
                episode = (await self.llm.complete(EPISODE_PROMPT.format(
                    date=date_str(piece[0]["created_at"]), language=self.language, user=self.user,
                    transcript=transcript), temperature=0.2, max_tokens=200)).strip()
                if episode:
                    await self.remember(f"[{date_str(piece[0]['created_at'])}] {episode}", "episode", 0.45,
                                        source="compaction", conv_id=conv_id)
                # persist progress after every piece so a crash never loses work
                self.store.set_summary(conv_id, summary)
                self.store.mark_summarized([m["id"] for m in piece])
            log.info("compacted %d messages of %s", len(chunk), conv_id)
            return True

    # =========================================================== extraction
    async def extract(self, conv_id: str, user_msg: str, ai_msg: str) -> list[tuple]:
        if len(user_msg.strip()) < 4:
            return []
        vecs, model_id = await self.embedder.embed([user_msg])
        related = self._search(vecs[0], model_id, user_msg, 10,
                               kinds=["fact", "preference", "instruction", "event"], min_score=0.0)
        known = "\n".join(f"[{r['id']}] ({r['kind']}) {r['content']}" for _, r in related) or "(none)"
        _, ai_visible = split_emotion(ai_msg)
        raw = await self.llm.complete(EXTRACT_PROMPT.format(
            ai=self.ai, user=self.user, language=self.language, today=now_str(), known=known,
            user_msg=truncate_middle(user_msg, 3000), ai_msg=truncate_middle(ai_visible, 1500)),
            json_mode=True, temperature=0.1, max_tokens=600)
        data = parse_json_loose(raw)
        items = data.get("memories", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        related_ids = {r["id"] for _, r in related}
        results = []
        for it in items[:6]:
            if not isinstance(it, dict):
                continue
            content = str(it.get("content", "")).strip()
            if len(content) < 5:
                continue
            try:
                importance = float(it.get("importance", 0.6))
            except (TypeError, ValueError):
                importance = 0.6
            replaces = it.get("replaces")
            replaces = int(replaces) if isinstance(replaces, (int, float, str)) and str(replaces).isdigit() \
                and int(replaces) in related_ids else None
            results.append(await self.remember(content, str(it.get("kind", "fact")), importance,
                                               source="auto", conv_id=conv_id, replaces=replaces))
        return results

    # =========================================================== reflection
    async def reflect(self) -> dict:
        msgs = self.store.last_messages_any(30)
        feedback = self.store.unprocessed_feedback()
        if not msgs and not feedback:
            return {"lessons": 0, "skills": 0}
        fb_lines = []
        for f in feedback:
            verdict = "👍 liked" if f["rating"] > 0 else "👎 disliked"
            fb_lines.append(f"- {verdict} this answer: \"{truncate_middle(f['message'] or '', 400)}\""
                            + (f" — comment: {f['comment']}" if f["comment"] else ""))
        existing = "\n".join(f"- {r['content']}" for r in self.store.list_memories(["lesson"])[:20]) or "(none)"
        raw = await self.llm.complete(REFLECT_PROMPT.format(
            ai=self.ai, user=self.user, language=self.language, lessons=existing,
            feedback="\n".join(fb_lines) or "(none)", transcript=self._transcript(msgs, 700)),
            json_mode=True, temperature=0.3, max_tokens=900)
        data = parse_json_loose(raw) or {}
        n_lessons = n_skills = 0
        for l in (data.get("lessons") or [])[:5] if isinstance(data, dict) else []:
            content = l.get("content") if isinstance(l, dict) else str(l)
            if content and len(content) > 10:
                imp = l.get("importance", 0.6) if isinstance(l, dict) else 0.6
                await self.remember(content, "lesson", float(imp) if isinstance(imp, (int, float)) else 0.6,
                                    source="reflection")
                n_lessons += 1
        for s in (data.get("skills") or [])[:3] if isinstance(data, dict) else []:
            if isinstance(s, dict) and s.get("name") and s.get("steps"):
                await self.save_skill(str(s["name"]), str(s.get("description", "")), str(s["steps"]))
                n_skills += 1
        self.store.mark_feedback_processed([f["id"] for f in feedback])
        self.store.kv_set("last_reflection", time.time())
        log.info("reflection: +%d lessons, +%d skills", n_lessons, n_skills)
        return {"lessons": n_lessons, "skills": n_skills}

    async def save_skill(self, name: str, description: str, steps: str):
        content = f"Kỹ năng «{name.strip()}»: {description.strip()}\nCách làm: {steps.strip()}"
        return await self.remember(content, "skill", 0.65, source="skill")

    # ========================================================== maintenance
    async def maintenance(self) -> dict:
        """Re-embed with the real model when available, decay stale episodes, merge duplicates."""
        stats = {"reembedded": 0, "merged": 0, "decayed": 0}
        if self.embedder.provider != "hash":
            rows = [r for r in self.store.list_memories(with_embedding=True)
                    if r["emb_model"] != self.embedder.remote_id]
            for i in range(0, len(rows), 32):
                batch = rows[i:i + 32]
                vecs, model_id = await self.embedder.embed([r["content"] for r in batch])
                if model_id != self.embedder.remote_id:
                    break
                for r, v in zip(batch, vecs):
                    self.store.update_memory(r["id"], embedding=to_blob(v), emb_model=model_id)
                    stats["reembedded"] += 1
            self._invalidate()

        now = time.time()
        for r in self.store.list_memories(["episode"]):
            if r["access_count"] == 0 and now - r["created_at"] > 60 * 86400 and r["importance"] > 0.15:
                self.store.update_memory(r["id"], importance=round(r["importance"] * 0.85, 3))
                stats["decayed"] += 1

        model_id = self.embedder.active_id
        _, ids, mat, meta = self._index(model_id)
        if len(ids) > 1:
            sims = mat @ mat.T
            np.fill_diagonal(sims, 0)
            dead: set[int] = set()
            for a, b in zip(*np.where(np.triu(sims) >= 0.96)):
                ra, rb = meta[int(ids[a])], meta[int(ids[b])]
                if ra["id"] in dead or rb["id"] in dead or ra["kind"] != rb["kind"]:
                    continue
                loser = ra if (ra["importance"], ra["updated_at"]) < (rb["importance"], rb["updated_at"]) else rb
                self.store.deactivate_memory(loser["id"])
                dead.add(loser["id"])
            stats["merged"] = len(dead)
            self._invalidate()
        self.store.kv_set("last_maintenance", now)
        return stats
