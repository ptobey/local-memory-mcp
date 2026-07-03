import json
import math
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

# HuggingFace offline flags MUST be set before huggingface_hub is imported
# (via sentence_transformers below) — otherwise the offline constant is baked
# in as False and every model load makes network cache-validation calls.
# setdefault (not hard-set) so a caller can still opt into online mode by
# exporting HF_HUB_OFFLINE=0 before import (e.g. to download a new reranker).
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

try:
    import chromadb  # type: ignore
except Exception as exc:
    chromadb = None
    _CHROMADB_IMPORT_ERROR = exc
else:
    _CHROMADB_IMPORT_ERROR = None
from sentence_transformers import SentenceTransformer


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chromadb_version_str() -> str:
    if chromadb is None:
        return "not installed"
    return str(getattr(chromadb, "__version__", "unknown"))


def _parse_iso_dt(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _compute_recency_score(last_modified: str) -> float:
    """1.0 = today, decays linearly to 0.0 at 365 days old, clamped at 0.0."""
    dt = _parse_iso_dt(last_modified)
    if not dt:
        return 0.0
    days_old = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    return round(max(0.0, 1.0 - days_old / 365), 4)


def _tokenize_for_ranking(text: str) -> List[str]:
    stop_words = {
        "the",
        "and",
        "to",
        "of",
        "in",
        "a",
        "is",
        "for",
        "on",
        "with",
        "as",
        "at",
        "by",
        "an",
        "be",
        "this",
        "that",
        "it",
        "are",
        "was",
        "were",
        "or",
        "if",
        "now",
    }
    tokens: List[str] = []
    for raw in (text or "").lower().split():
        token = "".join(ch for ch in raw if ch.isalnum())
        if not token:
            continue
        if token in stop_words:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        if token.isdigit() and len(token) < 4:
            continue
        if len(token) <= 2 and not token.isdigit():
            continue
        if token:
            tokens.append(token)
    return tokens


def _is_time_sensitive_query(query: str) -> bool:
    lowered = (query or "").lower()
    markers = (
        "current",
        "latest",
        "recent",
        "now",
        "as of",
        "status",
        "decision",
        "hierarchy",
        "update",
        "updated",
        "correction",
        "corrected",
        "deprecated",
    )
    if any(marker in lowered for marker in markers):
        return True
    month_names = (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    )
    has_month = any(month in lowered for month in month_names)
    has_year = any(str(year) in lowered for year in range(2000, 2101))
    return has_month and has_year


def _is_update_style_text(text: str) -> bool:
    lowered = (text or "").lower()
    markers = (
        "update",
        "clarification",
        "correction",
        "corrected",
        "revised",
        "deprecated",
        "eliminated",
        "no longer",
        "remains",
        "superseded",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_actionable_soft_duplicate(text_a: str, text_b: str) -> bool:
    tokens_a = set(_tokenize_for_ranking(text_a))
    tokens_b = set(_tokenize_for_ranking(text_b))
    if not tokens_a or not tokens_b:
        return False
    intersection = tokens_a & tokens_b
    jaccard = len(intersection) / float(len(tokens_a | tokens_b))
    overlap_a = len(intersection) / float(len(tokens_a))
    overlap_b = len(intersection) / float(len(tokens_b))
    novel_ratio_a = len(tokens_a - tokens_b) / float(len(tokens_a))
    novel_ratio_b = len(tokens_b - tokens_a) / float(len(tokens_b))
    return (
        jaccard >= 0.55
        and max(overlap_a, overlap_b) >= 0.70
        and min(novel_ratio_a, novel_ratio_b) <= 0.35
    )


def _load_config() -> Dict[str, Any]:
    # Resolve chroma_db path relative to project root (parent of src/)
    project_root = Path(__file__).resolve().parents[1]
    default_chroma_path = str(project_root / "chroma_db")

    default = {
        "CHROMADB_PERSIST_DIR": default_chroma_path,
        "EMBEDDING_MODEL": "all-MiniLM-L6-v2",
        # Two-stage retrieval: bi-encoder narrows a wide pool, cross-encoder
        # reorders it. RERANK_ENABLED turns the second pass on for AI-facing
        # search; RERANK_OVERFETCH is the pool multiplier (pool = top_k * this).
        "RERANK_ENABLED": True,
        "RERANKER_MODEL": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "RERANK_OVERFETCH": 5,
        "RECONCILIATION_THRESHOLD": 0.85,
        "AUTO_MERGE_THRESHOLD": 0.95,
        "LLM_PROVIDER": "anthropic",
        "LLM_MODEL": "claude-haiku-4-5",
        "ENABLE_ACCESS_TRACKING": True,
        "ALLOW_RANDOMNESS": False,
        "ALLOW_EXTERNAL_LLM": False,
        "ALLOW_WEB_REQUESTS": False,
        "MCP_AUTH_MODE": "none",
        "MCP_BEARER_TOKEN": "",
        "MCP_OAUTH_CLIENT_ID": "change-me",
        "MCP_OAUTH_CLIENT_SECRET": "change-me",
        "MCP_OAUTH_REDIRECT_ALLOWLIST": [],
        "MCP_OAUTH_TOKEN_TTL_SECONDS": 2592000,
        "MCP_ISSUER_URL": "http://localhost:8000",
        "MCP_RESOURCE_SERVER_URL": "http://localhost:8000",
        "MCP_REQUIRED_SCOPES": [],
        "MCP_ENABLE_DNS_REBINDING_PROTECTION": True,
        "MCP_ALLOWED_HOSTS": ["127.0.0.1:*", "localhost:*", "[::1]:*"],
        "MCP_ALLOWED_ORIGINS": ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    }
    config_path = Path(__file__).resolve().parents[1] / "config.json"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            settings = data.get("settings", {})
            # If config has relative path, convert it to absolute
            if "CHROMADB_PERSIST_DIR" in settings:
                config_db_path = settings["CHROMADB_PERSIST_DIR"]
                if not Path(config_db_path).is_absolute():
                    settings["CHROMADB_PERSIST_DIR"] = str(project_root / config_db_path)
            default.update(settings)
        except json.JSONDecodeError:
            pass
    return default


def _normalize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            normalized[key] = ""
        elif isinstance(value, bool):
            normalized[key] = value
        elif isinstance(value, (int, float, str)):
            normalized[key] = value
        else:
            normalized[key] = json.dumps(value)
    return normalized


class _DisabledEmbeddingFunction:
    @staticmethod
    def name() -> str:
        return "disabled"

    @staticmethod
    def get_config() -> Dict[str, Any]:
        return {}

    @staticmethod
    def build_from_config(_config: Dict[str, Any]) -> "_DisabledEmbeddingFunction":
        return _DisabledEmbeddingFunction()

    def __call__(self, input: List[str]) -> List[List[float]]:
        raise RuntimeError("Embedding function is disabled; provide embeddings explicitly.")


@dataclass
class ChunkRecord:
    chunk_id: str
    text: str
    metadata: Dict[str, Any]
    embedding: Optional[List[float]] = None


class EmbeddingManager:
    def __init__(self, model_name: Optional[str] = None):
        # HF offline flags are set at module import (top of this file), which is
        # early enough to actually take effect. Setting them here would be too late.
        config = _load_config()
        self.model_name = model_name or config["EMBEDDING_MODEL"]
        self.model = SentenceTransformer(self.model_name)

    def embed_text(self, text: str) -> List[float]:
        return self.model.encode([text])[0].tolist()


# --- Cross-encoder reranker: loaded once per process, kept warm. -------------
# The MCP server is long-lived, so we cache the model on first use and reuse it
# for every subsequent search. If a model can't be loaded (offline, not yet
# downloaded, bad name), we remember the failure and fall back to plain
# bi-encoder ordering so search never hard-fails.
_RERANKER_CACHE: Dict[str, Any] = {}
_RERANKER_FAILED: set = set()


def _get_reranker(model_name: str):
    """Return a warm CrossEncoder for `model_name`, or None if unavailable."""
    if not model_name or model_name in _RERANKER_FAILED:
        return None
    cached = _RERANKER_CACHE.get(model_name)
    if cached is not None:
        return cached
    try:
        from sentence_transformers import CrossEncoder
        model = CrossEncoder(model_name)
        _RERANKER_CACHE[model_name] = model
        print(f"[reranker] loaded {model_name!r} (warm for process lifetime)", file=sys.stderr)
        return model
    except Exception as exc:
        print(
            f"[reranker] could not load {model_name!r}: {exc}. "
            "Falling back to bi-encoder ordering. "
            "(Pre-download the model once if the store runs offline.)",
            file=sys.stderr,
        )
        _RERANKER_FAILED.add(model_name)
        return None


def _sigmoid(x: float) -> float:
    """Map a cross-encoder logit to a readable 0..1 relevance score.
    Monotonic, so it never changes ordering — purely for legibility."""
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


class VectorStore:
    def __init__(
        self,
        persist_dir: Optional[str] = None,
        embedding_manager: Optional[EmbeddingManager] = None,
        collection_name: str = "master_memory",
        log_collection_name: str = "reconciliation_log",
    ):
        if chromadb is None:
            raise RuntimeError(
                "chromadb failed to import. Ensure compatible dependencies "
                "(pydantic v1 or chromadb version that supports pydantic v2). "
                f"Original error: {_CHROMADB_IMPORT_ERROR}"
            )
        config = _load_config()
        self.persist_dir = persist_dir or config["CHROMADB_PERSIST_DIR"]
        self.enable_access_tracking = bool(config["ENABLE_ACCESS_TRACKING"])
        self.rerank_enabled = bool(config.get("RERANK_ENABLED", True))
        self.reranker_model = str(config.get("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"))
        try:
            self.rerank_overfetch = max(1, int(config.get("RERANK_OVERFETCH", 5)))
        except (TypeError, ValueError):
            self.rerank_overfetch = 5
        self.client = self._build_client(self.persist_dir)
        disabled_embedding = _DisabledEmbeddingFunction()
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=disabled_embedding,
        )
        self.log_collection = self.client.get_or_create_collection(
            name=log_collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=disabled_embedding,
        )
        self.embedding_manager = embedding_manager or EmbeddingManager()

    def _build_client(self, persist_dir: str):
        if not hasattr(chromadb, "PersistentClient"):
            sqlite_path = Path(persist_dir) / "chroma.sqlite3"
            version = _chromadb_version_str()
            if sqlite_path.exists():
                raise RuntimeError(
                    "Unsupported chromadb client version for this persisted database. "
                    f"Installed chromadb={version}. This project DB uses a newer Chroma format. "
                    "Use the project virtualenv (.venv) or install chromadb==1.4.1."
                )
            raise RuntimeError(
                "Unsupported chromadb client version. "
                f"Installed chromadb={version}. Install chromadb==1.4.1."
            )
        return chromadb.PersistentClient(path=persist_dir)

    def _collection_count(self) -> int:
        try:
            return int(self.collection.count())
        except Exception:
            return len(self.collection.get(include=[]).get("ids", []))

    def _base_metadata(
        self,
        confidence: float,
        source_type: str,
        supersedes: Optional[str] = None,
        timestamp: Optional[str] = None,
        word_count: int = 0,
    ) -> Dict[str, Any]:
        created_at = timestamp or _now_iso()
        return {
            "timestamp": created_at,
            "last_modified": created_at,
            "confidence": float(confidence),
            "deprecated": False,
            "supersedes": supersedes or "",
            "source_type": source_type,
            "word_count": int(word_count),
            "access_count": 0,
            "last_accessed": "",
        }

    def add_chunk(
        self,
        text: str,
        confidence: float = 1.0,
        source_type: str = "user_statement",
        supersedes: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> str:
        chunk_id = str(uuid4())
        embedding = self.embedding_manager.embed_text(text)
        word_count = len(text.split())
        metadata = _normalize_metadata(
            self._base_metadata(
                confidence=confidence,
                source_type=source_type,
                supersedes=supersedes,
                timestamp=timestamp,
                word_count=word_count,
            )
        )
        self.collection.add(
            ids=[chunk_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata],
        )
        return chunk_id

    def get_chunk(self, chunk_id: str) -> Optional[ChunkRecord]:
        result = self.collection.get(
            ids=[chunk_id],
            include=["documents", "metadatas", "embeddings"],
        )
        if not result["ids"]:
            return None
        metadata = result["metadatas"][0] or {}
        return ChunkRecord(
            chunk_id=result["ids"][0],
            text=result["documents"][0],
            metadata=metadata,
            embedding=result["embeddings"][0],
        )

    def _update_chunk_metadata(self, chunk_id: str, updates: Dict[str, Any]) -> None:
        existing = self.get_chunk(chunk_id)
        if not existing:
            return
        metadata = dict(existing.metadata)
        metadata.update(updates)
        metadata["last_modified"] = _now_iso()
        self.collection.update(
            ids=[chunk_id],
            metadatas=[_normalize_metadata(metadata)],
        )

    def update_metadata(self, chunk_id: str, updates: Dict[str, Any]) -> None:
        self._update_chunk_metadata(chunk_id, updates)

    def update_chunk(
        self,
        chunk_id: str,
        new_text: str,
        strategy: str = "version",
    ) -> Optional[str]:
        existing = self.get_chunk(chunk_id)
        if not existing:
            return None
        if strategy == "replace":
            embedding = self.embedding_manager.embed_text(new_text)
            metadata = dict(existing.metadata)
            metadata["last_modified"] = _now_iso()
            metadata["word_count"] = len(new_text.split())
            self.collection.update(
                ids=[chunk_id],
                documents=[new_text],
                embeddings=[embedding],
                metadatas=[_normalize_metadata(metadata)],
            )
            return chunk_id
        new_id = self.add_chunk(
            text=new_text,
            confidence=float(existing.metadata.get("confidence", 1.0)),
            source_type=existing.metadata.get("source_type", "user_statement"),
            supersedes=chunk_id,
        )
        self._update_chunk_metadata(chunk_id, {"deprecated": True})
        return new_id

    def delete_chunk(self, chunk_id: str, hard_delete: bool = False) -> bool:
        existing = self.get_chunk(chunk_id)
        if not existing:
            return False
        if hard_delete:
            self.collection.delete(ids=[chunk_id])
        else:
            self._update_chunk_metadata(chunk_id, {"deprecated": True})
        return True

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_confidence: float = 0.0,
        include_deprecated: bool = False,
        use_reranker: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        # Two-stage retrieval:
        #   stage 1 (bi-encoder) — overfetch a WIDE pool of top_k * overfetch
        #     candidates cheaply, so the reranker has enough to work with.
        #   stage 2 (cross-encoder) — reorder that pool precisely, keep top_k.
        # use_reranker=None -> follow config (RERANK_ENABLED). Internal callers
        # (duplicate detection) pass use_reranker=False to keep the original
        # bi-encoder score semantics their thresholds depend on.
        do_rerank = self.rerank_enabled if use_reranker is None else bool(use_reranker)

        embedding = self.embedding_manager.embed_text(query)
        query_tokens = set(_tokenize_for_ranking(query))
        time_sensitive = _is_time_sensitive_query(query)
        where: Dict[str, Any] = {}
        if not include_deprecated:
            where["deprecated"] = False
        total = self._collection_count()
        if total == 0:
            return []
        # Overfetch the pool when reranking; otherwise just top_k as before.
        pool_size = top_k * self.rerank_overfetch if do_rerank else top_k
        n_results = min(pool_size, total)
        query_kwargs = {"query_embeddings": [embedding], "n_results": n_results}
        if where:
            query_kwargs["where"] = where
        results = self.collection.query(**query_kwargs)
        output: List[Dict[str, Any]] = []
        for idx, chunk_id in enumerate(results.get("ids", [[]])[0]):
            metadata = results.get("metadatas", [[]])[0][idx] or {}
            text = results.get("documents", [[]])[0][idx]
            distance = results.get("distances", [[]])[0][idx]
            similarity = 1.0 - float(distance)
            confidence = float(metadata.get("confidence", 1.0))
            if confidence < min_confidence:
                continue
            timestamp = str(metadata.get("timestamp", "") or "")
            last_modified = str(metadata.get("last_modified", "") or "")
            source_type = str(metadata.get("source_type", "") or "")
            supersedes = str(metadata.get("supersedes", "") or "")
            try:
                word_count = int(metadata.get("word_count", 0) or 0)
            except (TypeError, ValueError):
                word_count = 0
            text_tokens = set(_tokenize_for_ranking(text))
            lexical_overlap = (
                (len(query_tokens & text_tokens) / float(len(query_tokens)))
                if query_tokens
                else 0.0
            )
            output.append(
                {
                    "id": chunk_id,
                    "chunk_id": chunk_id,
                    "text": text,
                    "score": similarity,
                    "confidence": confidence,
                    "deprecated": bool(metadata.get("deprecated", False)),
                    "timestamp": timestamp,
                    "last_modified": last_modified,
                    "source_type": source_type,
                    "supersedes": supersedes,
                    "word_count": word_count,
                    "query_overlap": lexical_overlap,
                }
            )
        # --- Stage 2: cross-encoder rerank (rerank score ALONE decides order) ---
        # We deliberately do NOT blend in the bi-encoder score, lexical overlap,
        # or recency here. recency_score is still attached below as an
        # informational field the AI can read, but it does not reorder results.
        reranker = _get_reranker(self.reranker_model) if do_rerank else None
        if reranker is not None and output:
            pairs = [(query, item["text"]) for item in output]
            try:
                raw_scores = reranker.predict(pairs, show_progress_bar=False)
            except Exception as exc:
                print(f"[reranker] predict failed: {exc}; using bi-encoder order", file=sys.stderr)
                raw_scores = None
            if raw_scores is not None:
                for item, raw in zip(output, raw_scores):
                    rerank_score = _sigmoid(float(raw))
                    item["rerank_score"] = rerank_score
                    # rank_score always means "the score that set final order".
                    item["rank_score"] = rerank_score
                    item["reranked"] = True
                    item["recency_score"] = _compute_recency_score(
                        item.get("last_modified") or item.get("timestamp") or ""
                    )
                output.sort(
                    key=lambda item: (item["rerank_score"], item["score"]),
                    reverse=True,
                )
                output = output[:top_k]
                if self.enable_access_tracking and output:
                    self._bump_access(output)
                return output
            # raw_scores is None -> fall through to bi-encoder ranking below.

        recency_range: Optional[tuple[datetime, datetime]] = None
        if time_sensitive and output:
            parsed_times = []
            for item in output:
                parsed = _parse_iso_dt(item.get("last_modified") or item.get("timestamp") or "")
                if parsed:
                    parsed_times.append(parsed)
            if parsed_times:
                recency_range = (min(parsed_times), max(parsed_times))

        for item in output:
            lexical_bonus = 0.06 * float(item.get("query_overlap", 0.0))
            recency_bonus = 0.0
            update_style_bonus = 0.0
            if recency_range is not None:
                oldest, newest = recency_range
                parsed = _parse_iso_dt(item.get("last_modified") or item.get("timestamp") or "")
                if parsed:
                    span = (newest - oldest).total_seconds()
                    if span > 0:
                        recency_norm = (parsed - oldest).total_seconds() / span
                    else:
                        recency_norm = 1.0
                    recency_bonus = 0.08 * recency_norm
            if time_sensitive and _is_update_style_text(item.get("text", "")):
                # Favor explicit update/correction/clarification chunks over baseline summaries.
                update_style_bonus = 0.04
            item["rank_score"] = (
                float(item["score"]) + lexical_bonus + recency_bonus + update_style_bonus
            )
            item["recency_score"] = _compute_recency_score(
                item.get("last_modified") or item.get("timestamp") or ""
            )

        output.sort(
            key=lambda item: (
                float(item.get("rank_score", item["score"])),
                float(item["score"]),
                item.get("last_modified") or item.get("timestamp") or "",
            ),
            reverse=True,
        )
        output = output[:top_k]
        if self.enable_access_tracking and output:
            self._bump_access(output)
        return output

    def _bump_access(self, results: List[Dict[str, Any]]) -> None:
        ids = []
        metadatas = []
        for item in results:
            record = self.get_chunk(item["chunk_id"])
            if not record:
                continue
            metadata = dict(record.metadata)
            metadata["access_count"] = int(metadata.get("access_count", 0)) + 1
            metadata["last_accessed"] = _now_iso()
            ids.append(record.chunk_id)
            metadatas.append(_normalize_metadata(metadata))
        if ids:
            self.collection.update(ids=ids, metadatas=metadatas)

    def get_recent(
        self,
        n: int = 10,
        include_deprecated: bool = False,
    ) -> Dict[str, Any]:
        result = self.collection.get(include=["documents", "metadatas"])
        ids = result.get("ids") or []
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        chunks = []
        for chunk_id, text, metadata in zip(ids, documents, metadatas):
            metadata = metadata or {}
            if not include_deprecated and metadata.get("deprecated", False):
                continue
            last_modified = str(metadata.get("last_modified", "") or "")
            timestamp = str(metadata.get("timestamp", "") or "")
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "text": text,
                    "last_modified": last_modified,
                    "timestamp": timestamp,
                    "recency_score": _compute_recency_score(last_modified or timestamp),
                    "deprecated": bool(metadata.get("deprecated", False)),
                    "supersedes": str(metadata.get("supersedes", "") or ""),
                    "source_type": str(metadata.get("source_type", "") or ""),
                    "word_count": int(metadata.get("word_count", 0) or 0),
                    "confidence": float(metadata.get("confidence", 1.0)),
                }
            )
        chunks.sort(
            key=lambda x: x.get("last_modified") or x.get("timestamp") or "",
            reverse=True,
        )
        return {
            "as_of": _now_iso(),
            "total_returned": min(n, len(chunks)),
            "total_active": len(chunks),
            "chunks": chunks[:n],
        }

    def get_evolution_chain(self, chunk_id: str) -> List[Dict[str, Any]]:
        result = self.collection.get(include=["documents", "metadatas"])
        records = []
        for idx, current_id in enumerate(result.get("ids", [])):
            metadata = result.get("metadatas", [])[idx] or {}
            records.append(
                {
                    "chunk_id": current_id,
                    "text": result.get("documents", [])[idx],
                    "metadata": metadata,
                }
            )
        by_id = {record["chunk_id"]: record for record in records}
        chain = []
        current = by_id.get(chunk_id)
        while current:
            chain.append(current)
            supersedes = (current["metadata"] or {}).get("supersedes") or ""
            if not supersedes or supersedes not in by_id:
                break
            current = by_id.get(supersedes)
        return chain

    def create_backup(self, label: Optional[str] = None) -> Dict[str, Any]:
        backup_root = Path(__file__).resolve().parents[1] / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        backup_id = f"{timestamp}_{label}" if label else timestamp
        dest = backup_root / backup_id
        if hasattr(self.client, "persist"):
            self.client.persist()
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(self.persist_dir, dest)
        total_chunks = len(self.collection.get(include=[]).get("ids", []))
        return {"backup_id": backup_id, "timestamp": timestamp, "chunk_count": total_chunks}

    def restore_backup(self, backup_id: str) -> Dict[str, Any]:
        backup_root = Path(__file__).resolve().parents[1] / "backups"
        source = backup_root / backup_id
        if not source.exists():
            raise FileNotFoundError(f"Backup not found: {backup_id}")
        if Path(self.persist_dir).exists():
            shutil.rmtree(self.persist_dir)
        shutil.copytree(source, self.persist_dir)
        return {"backup_id": backup_id, "restored_at": _now_iso()}

    def self_check(self) -> Dict[str, Any]:
        result = self.collection.get(include=["metadatas", "embeddings"])
        all_ids = result.get("ids", [])
        metadatas = result.get("metadatas", [])
        embeddings = result.get("embeddings", [])
        total_chunks = len(all_ids)
        deprecated_chunks = sum(
            1 for metadata in metadatas if bool((metadata or {}).get("deprecated", False))
        )
        active_chunks = total_chunks - deprecated_chunks
        active_id_embeddings: List[tuple[str, List[float]]] = []
        invalid_embedding_records = 0
        for idx, metadata in enumerate(metadatas):
            if bool((metadata or {}).get("deprecated", False)):
                continue
            if idx >= len(all_ids) or idx >= len(embeddings):
                invalid_embedding_records += 1
                continue
            raw_embedding = embeddings[idx]
            coerced_embedding = _coerce_embedding(raw_embedding)
            if coerced_embedding is None:
                invalid_embedding_records += 1
                continue
            active_id_embeddings.append((all_ids[idx], coerced_embedding))
        unresolved = int(self.get_conflicts().get("unresolved_count", 0))
        potential_duplicates, duplicate_scan_errors = _count_duplicates(
            self.collection,
            active_id_embeddings,
            where={"deprecated": False},
        )
        health_score = 1.0
        if total_chunks:
            health_score = max(
                0.0,
                1.0 - min(1.0, unresolved / float(total_chunks)),
            )
        recommendations = []
        if potential_duplicates:
            recommendations.append(
                "Review potential duplicate chunks (AI/user decides whether they are true duplicates or intentional overlap)."
            )
        if unresolved:
            recommendations.append(
                "Review unresolved reconciliation conflicts and resolve the ones that matter."
            )
        if invalid_embedding_records:
            recommendations.append(
                "Some active chunks had missing/invalid embeddings during self-check; run update/store maintenance as needed."
            )
        if duplicate_scan_errors:
            recommendations.append(
                "Duplicate scan skipped some records due to query/index errors; consider a backup+restore cycle if this persists."
            )
        return {
            "total_chunks": total_chunks,
            "active_chunks": active_chunks,
            "deprecated_chunks": deprecated_chunks,
            "unresolved_conflicts": unresolved,
            "potential_duplicates": potential_duplicates,
            "duplicate_scan_errors": duplicate_scan_errors,
            "invalid_embedding_records": invalid_embedding_records,
            "health_score": health_score,
            "recommendations": recommendations,
        }

    def log_reconciliation(
        self,
        action_type: str,
        chunk_ids: List[str],
        reasoning: str,
        auto_resolved: bool,
        confidence: float,
    ) -> str:
        log_id = str(uuid4())
        metadata = _normalize_metadata(
            {
                "log_id": log_id,
                "timestamp": _now_iso(),
                "action_type": action_type,
                "chunk_ids_affected": json.dumps(chunk_ids),
                "chunks_affected": len(chunk_ids),
                "confidence": float(confidence),
                "auto_resolved": bool(auto_resolved),
                "human_reviewed": False,
            }
        )
        embedding = self.embedding_manager.embed_text(reasoning)
        self.log_collection.add(
            ids=[log_id],
            embeddings=[embedding],
            documents=[reasoning],
            metadatas=[metadata],
        )
        return log_id

    def get_conflicts(self) -> Dict[str, Any]:
        """Get unresolved conflicts with full chunk details for AI/user review."""
        log_result = self.log_collection.get(include=["metadatas", "documents"])
        log_ids = log_result.get("ids", [])
        log_metas = log_result.get("metadatas", [])
        log_docs = log_result.get("documents", [])

        unresolved = []
        for log_id, meta, doc in zip(log_ids, log_metas, log_docs):
            if meta and not bool(meta.get("auto_resolved", True)):
                # Parse chunk IDs from metadata
                chunk_ids_raw = meta.get("chunk_ids_affected", "[]")
                if isinstance(chunk_ids_raw, str):
                    try:
                        chunk_ids = json.loads(chunk_ids_raw)
                    except json.JSONDecodeError:
                        chunk_ids = []
                elif isinstance(chunk_ids_raw, list):
                    chunk_ids = chunk_ids_raw
                else:
                    chunk_ids = []

                # Fetch the actual chunks
                chunks = []
                for chunk_id in chunk_ids:
                    chunk = self.get_chunk(chunk_id)
                    if chunk:
                        chunks.append({
                            "chunk_id": chunk_id,
                            "text": chunk.text,
                            "confidence": chunk.metadata.get("confidence"),
                            "timestamp": chunk.metadata.get("timestamp"),
                            "deprecated": chunk.metadata.get("deprecated", False),
                            "source_type": chunk.metadata.get("source_type"),
                        })

                # If all referenced chunks are already deprecated, this conflict is stale.
                if chunks and all(bool(c.get("deprecated", False)) for c in chunks):
                    continue

                action_type = str(meta.get("action_type") or "")
                # If any side of a soft-duplicate pair is already deprecated, the conflict
                # is usually no longer worth surfacing for current-state cleanup review.
                if (
                    action_type == "soft_duplicate"
                    and chunks
                    and any(bool(c.get("deprecated", False)) for c in chunks)
                ):
                    continue
                if (
                    action_type == "soft_duplicate"
                    and len(chunks) == 2
                    and not _looks_like_actionable_soft_duplicate(
                        chunks[0].get("text", "") or "",
                        chunks[1].get("text", "") or "",
                    )
                ):
                    continue

                unresolved.append({
                    "conflict_id": log_id,
                    "timestamp": meta.get("timestamp"),
                    "action_type": action_type,
                    "reasoning": doc,
                    "confidence": meta.get("confidence"),
                    "chunks": chunks,
                })

        return {
            "unresolved_count": len(unresolved),
            "conflicts": unresolved,
            "message": f"Found {len(unresolved)} unresolved conflict(s)"
        }

    def resolve_conflicts(self, conflict_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Mark conflicts as resolved. If no IDs provided, resolves all unresolved conflicts."""
        log_result = self.log_collection.get(include=["metadatas"])
        log_ids = log_result.get("ids", [])
        log_metas = log_result.get("metadatas", [])
        id_to_index = {log_id: idx for idx, log_id in enumerate(log_ids)}

        if conflict_ids is None:
            # Resolve all unresolved conflicts
            to_resolve = [
                log_id for log_id, meta in zip(log_ids, log_metas)
                if meta and not bool(meta.get("auto_resolved", True))
            ]
        else:
            to_resolve = [cid for cid in conflict_ids if cid in log_ids]

        if not to_resolve:
            return {"resolved_count": 0, "message": "No conflicts to resolve"}

        # Update metadata to mark as resolved
        for conflict_id in to_resolve:
            idx = id_to_index[conflict_id]
            meta = log_metas[idx] or {}
            meta["auto_resolved"] = True
            meta["resolved_at"] = _now_iso()
            meta["resolved_by"] = "manual_resolution"

            self.log_collection.update(
                ids=[conflict_id],
                metadatas=[_normalize_metadata(meta)]
            )

        return {
            "resolved_count": len(to_resolve),
            "resolved_ids": to_resolve,
            "message": f"Resolved {len(to_resolve)} conflict(s)"
        }

def _coerce_embedding(value: Any) -> Optional[List[float]]:
    if value is None:
        return None
    try:
        items = list(value)
    except TypeError:
        return None
    if not items:
        return None
    coerced: List[float] = []
    for item in items:
        try:
            coerced.append(float(item))
        except (TypeError, ValueError):
            return None
    return coerced


def _count_duplicates(
    collection: Any,
    id_embeddings: List[tuple[str, List[float]]],
    where: Optional[Dict[str, Any]] = None,
) -> tuple[int, int]:
    duplicate_pairs: set[tuple[str, str]] = set()
    scan_errors = 0
    for anchor_id, embedding in id_embeddings:
        coerced_embedding = _coerce_embedding(embedding)
        if coerced_embedding is None:
            scan_errors += 1
            continue
        query_kwargs: Dict[str, Any] = {
            "query_embeddings": [coerced_embedding],
            "n_results": 4,
        }
        if where:
            query_kwargs["where"] = where
        try:
            results = collection.query(**query_kwargs)
        except Exception:
            scan_errors += 1
            continue
        raw_ids = results.get("ids", [[]])
        raw_distances = results.get("distances", [[]])
        if not raw_ids or not raw_distances:
            continue
        ids = raw_ids[0] or []
        distances = raw_distances[0] or []
        for candidate_id, distance in zip(ids, distances):
            if candidate_id == anchor_id:
                continue
            similarity = 1.0 - float(distance)
            if similarity > 0.95:
                duplicate_pairs.add(tuple(sorted((anchor_id, candidate_id))))
    return len(duplicate_pairs), scan_errors
