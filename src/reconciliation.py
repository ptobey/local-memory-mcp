from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .vector_store import VectorStore, _load_config


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _tokenize(text: str) -> List[str]:
    return [
        "".join(char for char in token.lower() if char.isalnum())
        for token in text.split()
        if token
    ]


def _jaccard(a: List[str], b: List[str]) -> float:
    set_a = set(a)
    set_b = set(b)
    if not set_a and not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


@dataclass
class Relationship:
    relationship: str
    confidence: float
    reasoning: str


class ConflictDetector:
    NEGATION_WORDS = {"not", "never", "no", "none", "cannot", "can't", "won't", "doesn't"}

    def classify(self, new_text: str, old_text: str) -> Relationship:
        tokens_new = _tokenize(new_text)
        tokens_old = _tokenize(old_text)
        similarity = _jaccard(tokens_new, tokens_old)
        has_negation = bool(self.NEGATION_WORDS & set(tokens_new + tokens_old))
        if similarity >= 0.65:
            return Relationship("OVERLAP", 0.8, "High token overlap.")
        if similarity >= 0.50 and has_negation:
            return Relationship("CONTRADICT", 0.7, "High overlap with negation terms.")
        return Relationship("COMPLEMENT", 0.6, "Default complement classification.")


class ReconciliationEngine:
    def __init__(self, store: VectorStore):
        self.store = store
        self.config = _load_config()
        self.conflict_detector = ConflictDetector()
        self.debug = bool(self.config.get("RECONCILIATION_DEBUG", False))

    def _debug(self, message: str) -> None:
        if self.debug:
            print(message, file=sys.stderr)

    @staticmethod
    def _is_likely_soft_duplicate(
        similarity: float,
        token_overlap: float,
        novel_tokens: int,
        total_new_tokens: int,
    ) -> bool:
        if total_new_tokens <= 0:
            return False
        novel_ratio = novel_tokens / float(total_new_tokens)
        # Be conservative: only log unresolved soft duplicates when the texts are
        # very similar and the new chunk contributes little new information.
        return (
            similarity >= 0.88
            and token_overlap >= 0.60
            and novel_ratio <= 0.40
        )

    def on_write_reconcile(self, chunk_id: str) -> List[Dict[str, Any]]:
        self._debug(f"[RECONCILIATION] Starting for chunk_id: {chunk_id}")
        new_record = self.store.get_chunk(chunk_id)
        if not new_record:
            return []
        self._debug(f"[RECONCILIATION] Chunk text: {new_record.text[:100]}...")

        total = self.store._collection_count()
        if total <= 1:
            self._debug("[RECONCILIATION] Found 0 similar chunks (only one chunk present)")
            return []
        threshold = float(self.config["RECONCILIATION_THRESHOLD"])
        n_results = min(20, total)
        query_results = self.store.collection.query(
            query_embeddings=[new_record.embedding],
            n_results=n_results,
            where={"deprecated": False},
        )
        actions: List[Dict[str, Any]] = []
        candidates = query_results.get("ids", [[]])[0]
        self._debug(f"[RECONCILIATION] Found {len(candidates)} similar chunks")
        for idx, candidate_id in enumerate(candidates):
            if candidate_id == chunk_id:
                continue
            candidate_metadata = query_results.get("metadatas", [[]])[0][idx] or {}
            candidate_text = query_results.get("documents", [[]])[0][idx]

            distance = query_results.get("distances", [[]])[0][idx]
            similarity = 1.0 - float(distance)
            if similarity < threshold:
                continue
            self._debug(
                f"[RECONCILIATION] Comparing with chunk {candidate_id}, similarity: {similarity:.3f}"
            )
            self._debug(f"[RECONCILIATION] Comparing with: {candidate_text[:100]}...")
            self._debug(f"[RECONCILIATION] Similarity score: {similarity:.3f}")
            relationship = self.conflict_detector.classify(new_record.text, candidate_text)
            self._debug(f"[RECONCILIATION] Relationship: {relationship.relationship}")
            if relationship.confidence < 0.7:
                # Calculate additional metrics for AI decision-making
                tokens_new = set(_tokenize(new_record.text))
                tokens_old = set(_tokenize(candidate_text))
                token_overlap = len(tokens_new & tokens_old) / len(tokens_new) if tokens_new else 0
                novel_tokens = len(tokens_new - tokens_old)
                total_new_tokens = len(tokens_new)

                # Direct version-chain comparisons are expected and should not create
                # unresolved soft-duplicate work.
                new_supersedes = str(new_record.metadata.get("supersedes", "") or "")
                candidate_supersedes = str(candidate_metadata.get("supersedes", "") or "")
                if new_supersedes == candidate_id or candidate_supersedes == new_record.chunk_id:
                    self.store.log_reconciliation(
                        "no_action",
                        [candidate_id, new_record.chunk_id],
                        "Version-related comparison; no action required.",
                        auto_resolved=True,
                        confidence=relationship.confidence,
                    )
                    continue

                if not self._is_likely_soft_duplicate(
                    similarity=similarity,
                    token_overlap=token_overlap,
                    novel_tokens=novel_tokens,
                    total_new_tokens=total_new_tokens,
                ):
                    self.store.log_reconciliation(
                        "no_action",
                        [candidate_id, new_record.chunk_id],
                        "Related but distinct chunks; no action required.",
                        auto_resolved=True,
                        confidence=relationship.confidence,
                    )
                    continue

                self.store.log_reconciliation(
                    "soft_duplicate",
                    [candidate_id, new_record.chunk_id],
                    "Potential soft duplicate detected; requires AI resolution.",
                    auto_resolved=False,
                    confidence=relationship.confidence,
                )
                actions.append(
                    {
                        "action_type": "soft_duplicate",
                        "chunk_ids_affected": [candidate_id, new_record.chunk_id],
                        "reasoning": "Potential soft duplicate detected; requires AI resolution.",
                        "auto_resolved": False,
                        "similarity": similarity,
                        "token_overlap": token_overlap,
                        "novel_tokens": novel_tokens,
                        "candidate_text": candidate_text,
                        "new_text": new_record.text,
                    }
                )
                continue
            action = self._resolve_relationship(
                relationship,
                new_record,
                candidate_id,
                candidate_metadata,
                candidate_text,
            )
            if action:
                self._debug(f"[RECONCILIATION] Action: {action['action_type']}")
                actions.append(action)
            else:
                self.store.log_reconciliation(
                    "no_action",
                    [candidate_id, new_record.chunk_id],
                    "Complementary information; no action required.",
                    auto_resolved=True,
                    confidence=relationship.confidence,
                )
        self._debug(f"[RECONCILIATION] Completed with {len(actions)} actions")
        return actions

    def queue_for_reconciliation(self, chunk_id: str) -> None:
        self.on_write_reconcile(chunk_id)

    def _resolve_relationship(
        self,
        relationship: Relationship,
        new_record,
        candidate_id: str,
        candidate_metadata: Dict[str, Any],
        candidate_text: str,
    ) -> Optional[Dict[str, Any]]:
        if relationship.relationship == "CONTRADICT":
            return self._resolve_contradiction(new_record, candidate_id, candidate_metadata, relationship)
        if relationship.relationship == "OVERLAP":
            return self._resolve_overlap(new_record, candidate_id, candidate_metadata, candidate_text)
        return None

    def _resolve_contradiction(
        self,
        new_record,
        old_id: str,
        old_metadata: Dict[str, Any],
        relationship: Relationship,
    ) -> Dict[str, Any]:
        new_conf = float(new_record.metadata.get("confidence", 1.0))
        old_conf = float(old_metadata.get("confidence", 1.0))
        new_ts = _parse_iso(new_record.metadata.get("timestamp", ""))
        old_ts = _parse_iso(old_metadata.get("timestamp", ""))
        newer = False
        if new_ts and old_ts and new_ts - old_ts > timedelta(days=7):
            newer = True
        if new_conf > old_conf or newer:
            self.store.update_metadata(old_id, {"deprecated": True})
            if not new_record.metadata.get("supersedes"):
                self.store.update_metadata(new_record.chunk_id, {"supersedes": old_id})
            self.store.log_reconciliation(
                "deprecated",
                [old_id, new_record.chunk_id],
                relationship.reasoning,
                auto_resolved=True,
                confidence=relationship.confidence,
            )
            return {
                "action_type": "deprecated",
                "chunk_ids_affected": [old_id, new_record.chunk_id],
                "reasoning": relationship.reasoning,
                "auto_resolved": True,
            }
        self.store.log_reconciliation(
            "conflict",
            [old_id, new_record.chunk_id],
            relationship.reasoning,
            auto_resolved=False,
            confidence=relationship.confidence,
        )
        return {
            "action_type": "conflict",
            "chunk_ids_affected": [old_id, new_record.chunk_id],
            "reasoning": relationship.reasoning,
            "auto_resolved": False,
        }

    def _resolve_overlap(
        self,
        new_record,
        old_id: str,
        old_metadata: Dict[str, Any],
        old_text: str,
    ) -> Dict[str, Any]:
        merged_text = _merge_texts(new_record.text, old_text)
        merged_confidence = max(
            float(new_record.metadata.get("confidence", 1.0)),
            float(old_metadata.get("confidence", 1.0)),
        )
        merged_id = self.store.add_chunk(
            merged_text,
            confidence=merged_confidence,
            source_type="ai_inference",
        )
        self.store.update_metadata(old_id, {"deprecated": True})
        self.store.update_metadata(new_record.chunk_id, {"deprecated": True})
        self.store.log_reconciliation(
            "merged",
            [old_id, new_record.chunk_id, merged_id],
            "Merged overlapping chunks into a single record.",
            auto_resolved=True,
            confidence=0.9,
        )
        return {
            "action_type": "merged",
            "chunk_ids_affected": [old_id, new_record.chunk_id, merged_id],
            "reasoning": "Merged overlapping chunks into a single record.",
            "auto_resolved": True,
        }


def _merge_texts(text_a: str, text_b: str) -> str:
    sentences = []
    seen = set()
    for text in (text_a, text_b):
        for sentence in [line.strip() for line in text.splitlines() if line.strip()]:
            if sentence not in seen:
                seen.add(sentence)
                sentences.append(sentence)
    return "\n".join(sentences)
