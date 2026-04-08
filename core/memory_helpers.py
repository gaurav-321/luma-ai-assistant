from __future__ import annotations

import asyncio
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import requests
from agent_framework import BaseContextProvider
from qdrant_client import QdrantClient, models

from core.utils.config import config

warnings.filterwarnings(
    "ignore",
    message="Pydantic serializer warnings",
    category=UserWarning,
)


def _normalize_role(role: str | None) -> str:
    r = (role or "").lower()
    if r in ("user", "assistant", "system"):
        return r
    if r == "human":
        return "user"
    if r in ("ai", "bot"):
        return "assistant"
    return "assistant"


def _message_text(message: Any) -> str:
    text = getattr(message, "text", "")
    if isinstance(text, str):
        return text
    return ""


class QdrantSemanticProvider(BaseContextProvider):
    def __init__(
            self,
            *,
            chat_id_memory: str,
            source_id: str = "qdrant-semantic",
            qdrant_client: QdrantClient | None = None,
    ) -> None:
        super().__init__(source_id)

        self.chat_id_memory = str(chat_id_memory)

        vector_cfg = config["vector_store"]["config"]
        embedder_cfg = config["embedder"]["config"]

        self.collection_name = vector_cfg["collection_name"]
        self.vector_size = int(vector_cfg["embedding_model_dims"])
        self.embedding_model = embedder_cfg["model"]
        self.embedding_base_url = embedder_cfg["ollama_base_url"].rstrip("/")

        self.client = qdrant_client or self._build_client()
        self._collection_ready = False

    def _semantic_filter(self, mode: str = "24h") -> models.Filter:
        must: list[models.Condition] = [
            models.FieldCondition(
                key="chat_id_memory",
                match=models.MatchValue(value=self.chat_id_memory),
            )
        ]

        now_utc = datetime.now(timezone.utc)
        now_local = datetime.now()

        if mode == "hourly":
            cutoff = int((now_utc - timedelta(hours=1)).timestamp())
            must.append(models.FieldCondition(
                key="memory_ts_epoch",
                range=models.Range(gte=cutoff),
            ))

        elif mode == "24h":
            cutoff = int((now_utc - timedelta(hours=24)).timestamp())
            must.append(models.FieldCondition(
                key="memory_ts_epoch",
                range=models.Range(gte=cutoff),
            ))

        elif mode == "weekly":
            cutoff = int((now_utc - timedelta(days=7)).timestamp())
            must.append(models.FieldCondition(
                key="memory_ts_epoch",
                range=models.Range(gte=cutoff),
            ))

        elif mode == "today":
            today_str = now_local.strftime("%Y-%m-%d")
            must.append(models.FieldCondition(
                key="memory_date",
                match=models.MatchValue(value=today_str),
            ))

        return models.Filter(must=must)

    def _build_client(self) -> QdrantClient:
        vector_cfg = config["vector_store"]["config"]
        return QdrantClient(
            host=vector_cfg["host"],
            port=int(vector_cfg["port"]),
            timeout=10,
        )

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        resp = requests.post(
            f"{self.embedding_base_url}/api/embed",
            json={
                "model": self.embedding_model,
                "input": texts,
            },
            timeout=60,
        )
        resp.raise_for_status()

        data = resp.json()
        embeddings = data.get("embeddings")
        if not embeddings:
            raise ValueError(f"No embeddings returned from Ollama: {data}")

        for i, emb in enumerate(embeddings):
            if len(emb) != self.vector_size:
                raise ValueError(
                    f"Embedding dimension mismatch at index {i}: "
                    f"expected {self.vector_size}, got {len(emb)}"
                )

        return embeddings

    def _ensure_collection(self) -> None:
        if self._collection_ready:
            return

        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.vector_size,
                    distance=models.Distance.COSINE,
                ),
            )

        index_specs = [
            ("chat_id_memory", models.PayloadSchemaType.KEYWORD),
            ("role", models.PayloadSchemaType.KEYWORD),
            ("memory_date", models.PayloadSchemaType.KEYWORD),
            ("memory_ts_epoch", models.PayloadSchemaType.INTEGER),
        ]

        for field_name, schema in index_specs:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=schema,
                    wait=True,
                )
            except Exception:
                pass

        self._collection_ready = True

    def _chat_filter(self) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="chat_id_memory",
                    match=models.MatchValue(value=self.chat_id_memory),
                )
            ]
        )

    def _record_to_memory(self, item: Any) -> dict[str, Any]:
        payload = getattr(item, "payload", None) or {}

        text = (
                payload.get("document")
                or payload.get("memory")
                or payload.get("content")
                or ""
        )

        return {
            "id": str(getattr(item, "id", payload.get("id", ""))),
            "memory": text,
            "metadata": payload.get("metadata") or {},
            "created_at": payload.get("memory_timestamp") or "",
            "updated_at": None,
            "user_id": payload.get("chat_id_memory") or "",
            "role": payload.get("role") or "assistant",
            "score": getattr(item, "score", None),
        }

    def _parse_dt(self, m: dict[str, Any]) -> datetime:
        meta = m.get("metadata") or {}
        epoch_val = meta.get("memory_ts_epoch")

        if epoch_val is None:
            epoch_val = m.get("memory_ts_epoch")

        if epoch_val is not None:
            try:
                return datetime.fromtimestamp(int(epoch_val), tz=timezone.utc)
            except Exception:
                pass

        raw = (
                (meta.get("memory_timestamp") or "").strip()
                or (m.get("created_at") or "").strip()
        )
        if not raw:
            return datetime.min.replace(tzinfo=timezone.utc)

        try:
            return datetime.fromisoformat(raw)
        except Exception:
            try:
                return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except Exception:
                try:
                    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except Exception:
                    return datetime.min.replace(tzinfo=timezone.utc)

    def _parse_ts(self, m: dict[str, Any]) -> str:
        meta = m.get("metadata") or {}
        raw = (meta.get("memory_timestamp") or "").strip() or (m.get("created_at") or "").strip()
        if raw:
            return raw[:19].replace("T", " ")

        dt = self._parse_dt(m)
        if dt == datetime.min.replace(tzinfo=timezone.utc):
            return "unknown-time"
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def _normalize_text(self, m: dict[str, Any]) -> str:
        return " ".join((m.get("memory") or "").strip().split())

    def _valid_chat_memory(self, m: dict[str, Any]) -> bool:
        return (
                _normalize_role(m.get("role")) in ("user", "assistant")
                and bool((m.get("memory") or "").strip())
        )

    def _fmt_line(self, m: dict[str, Any]) -> str | None:
        role = _normalize_role(m.get("role"))
        body = m.get("memory", "")
        if not body:
            return None

        role_label = "User" if role == "user" else "Assistant"
        ts = self._parse_ts(m)
        return f"[{ts}] {role_label}: {body}"

    async def get_memory_chats(
            self,
            current_query: str,
            mode: str = "24h",  # hourly | 24h | today | weekly | last_n
            limit: int = 10,
    ) -> str:
        self._ensure_collection()

        max_chars = 6000

        # ---------------- SEMANTIC ----------------
        semantic_results: list[dict[str, Any]] = []
        try:
            query_vector = self._embed_texts([current_query])[0]

            query_response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=self._semantic_filter(mode=mode),
                limit=int(limit / 2),
                with_payload=True,
                with_vectors=False,
            )
            semantic_results = [self._record_to_memory(p) for p in (query_response.points or [])]
        except Exception as e:
            print(f"[QDRANT SEMANTIC ERROR] {e}")

        # ---------------- RECENT ----------------
        recent_results: list[dict[str, Any]] = []
        try:
            records, _ = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=self._chat_filter(),
                order_by=models.OrderBy(key="memory_ts_epoch", direction="desc"),
                limit=max(limit * 8, 50),
                with_payload=True,
                with_vectors=False,
            )
            recent_results = [self._record_to_memory(r) for r in records]
        except Exception as e:
            print(f"[QDRANT RECENT ERROR] {e}")

        # ---------------- SEMANTIC CLEAN ----------------
        semantic_chat = [m for m in semantic_results if self._valid_chat_memory(m)]
        semantic_chat.sort(key=self._parse_dt)

        seen_semantic: set[str] = set()
        relevant_memories: list[dict[str, Any]] = []
        for m in semantic_chat:
            key = self._normalize_text(m)
            if not key or key in seen_semantic:
                continue
            seen_semantic.add(key)
            relevant_memories.append(m)

        # ---------------- RECENT CLEAN ----------------
        recent_chat = [m for m in recent_results if self._valid_chat_memory(m)]

        deduped = {}
        for m in recent_chat:
            key = self._normalize_text(m)
            if key and key not in deduped:
                deduped[key] = m

        deduped_list = list(deduped.values())
        deduped_list.sort(key=self._parse_dt)

        # 🔥 MODE: last_n override
        if mode == "last_n":
            recent_tail = deduped_list[-limit:]
        else:
            recent_tail = deduped_list[-limit:]

        shown_keys = {self._normalize_text(m) for m in relevant_memories}

        # ---------------- FORMAT ----------------
        relevant_lines: list[str] = []
        recent_lines: list[str] = []
        total_chars = 0

        for m in relevant_memories[:3]:
            line = self._fmt_line(m)
            if not line:
                continue
            if total_chars + len(line) > max_chars:
                break
            relevant_lines.append(line.replace("\n", ", "))
            total_chars += len(line)

        for m in recent_tail:
            key = self._normalize_text(m)
            if key in shown_keys:
                continue

            line = self._fmt_line(m)
            if not line:
                continue
            if total_chars + len(line) > max_chars:
                break

            recent_lines.append(line.replace("\n", ", "))
            total_chars += len(line)

        sections: list[str] = []

        # 🔥 FIXED HEADER LOGIC
        if relevant_lines:
            if mode == "today":
                header = "### Most Relevant Memories From Today"
            elif mode == "24h":
                header = "### Most Relevant Memories From Last 24 Hours"
            elif mode == "hourly":
                header = "### Most Relevant Memories From Last Hour"
            elif mode == "weekly":
                header = "### Most Relevant Memories From Last 7 Days"
            else:
                header = "### Most Relevant Memories"

            sections.append(header + "\n" + "\n".join(relevant_lines))

        if recent_lines:
            sections.append("### Recent Conversation\n" + "\n".join(recent_lines))

        return "\n\n".join(sections).strip()

    def add_memory(self, messages_to_store: list[dict[str, Any]]) -> None:
        self._ensure_collection()

        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)

        now_ts = now_local.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now_local.strftime("%Y-%m-%d")
        epoch_ts = int(now_utc.timestamp())

        texts: list[str] = []
        payloads: list[dict[str, Any]] = []
        ids: list[str] = []

        for msg in messages_to_store:
            item = dict(msg)
            role = _normalize_role(item.get("role"))
            body = str(item.get("content") or item.get("memory") or "").strip()

            if not body:
                continue

            item_metadata = dict(item.get("metadata") or {})
            item_metadata.setdefault("memory_timestamp", now_ts)
            item_metadata.setdefault("memory_date", today_str)
            item_metadata.setdefault("memory_ts_epoch", epoch_ts)

            payload = {
                "document": body,
                "role": role,
                "chat_id_memory": self.chat_id_memory,
                "memory_timestamp": item_metadata["memory_timestamp"],
                "memory_date": item_metadata["memory_date"],
                "memory_ts_epoch": item_metadata["memory_ts_epoch"],
                "metadata": item_metadata,
            }

            texts.append(body)
            payloads.append(payload)
            ids.append(uuid4().hex)

        if not texts:
            return

        vectors = self._embed_texts(texts)

        points = [
            models.PointStruct(id=pid, vector=vec, payload=payload)
            for pid, vec, payload in zip(ids, vectors, payloads, strict=True)
        ]

        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )


Mem0SemanticProvider = QdrantSemanticProvider


async def _main() -> None:
    provider = QdrantSemanticProvider(
        chat_id_memory="test_user_123_general",
    )

    provider.add_memory(
        [
            {"role": "user", "content": "I had oats and mi121lk for breakfast."},
            {"role": "assistant", "content": "Logged yo2121ur breakfast."},
            {"role": "user", "content": "I had an ene212rgy drink of 90 calories."},
            {"role": "assistant", "content": "Logged 1212the 90 calorie energy drink."},
            {"role": "user", "content": "Send today e121xercises."},
            {"role": "assistant", "content": "Here 212are your exercises for today."},
        ]
    )

    result = await provider.get_memory_chats(
        current_query="What food did I log today?",
        mode="24h"
    )

    print("\n========== FINAL MEMORY BLOCK ==========\n")
    print(result)


if __name__ == "__main__":
    asyncio.run(_main())
