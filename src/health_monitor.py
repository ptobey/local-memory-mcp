"""
Health monitoring for memory chunks.
Flags oversized chunks and unresolved conflicts.
"""

from typing import Any, Dict, List

from .reconciliation import ReconciliationEngine
from .vector_store import VectorStore


class HealthMonitor:
    def __init__(self, vector_store: VectorStore, reconciler: ReconciliationEngine):
        self.vector_store = vector_store
        self.reconciler = reconciler
        self.IDEAL_MIN = 50
        self.IDEAL_MAX = 300
        self.WARNING_SIZE = 500
        self.CRITICAL_SIZE = 1000

    def check_health(self) -> Dict[str, Any]:
        issues: List[Dict[str, Any]] = []
        size_issues = self._check_chunk_sizes()
        issues.extend(size_issues)
        conflict_issues = self._check_conflicts()
        issues.extend(conflict_issues)

        result = self.vector_store.collection.get(
            where={"deprecated": False},
            include=[],
        )
        total_chunks = len(result.get("ids", []))
        breakdown = {
            "oversized_chunks": len([i for i in issues if i.get("type") == "oversized_chunk"]),
            "similarity_conflicts": len([i for i in issues if i.get("type") == "similarity_conflict"]),
        }
        return {
            "total_chunks": total_chunks,
            "issues": issues,
            "issue_count": len(issues),
            "breakdown": breakdown,
            "healthy": len(issues) == 0,
        }

    def _check_chunk_sizes(self) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        result = self.vector_store.collection.get(
            where={"deprecated": False},
            include=["documents", "metadatas"],
        )
        for idx, chunk_id in enumerate(result.get("ids", [])):
            text = result.get("documents", [])[idx]
            metadata = result.get("metadatas", [])[idx] or {}
            word_count = int(metadata.get("word_count", len(text.split())))
            if word_count > self.WARNING_SIZE:
                severity = "high" if word_count > self.CRITICAL_SIZE else "medium"
                issues.append(
                    {
                        "type": "oversized_chunk",
                        "chunk_id": chunk_id,
                        "word_count": word_count,
                        "recommended_max": self.IDEAL_MAX,
                        "severity": severity,
                        "text_preview": (text[:200] + "...") if len(text) > 200 else text,
                        "action_needed": "Break this into smaller logical chunks (150-300 words each)",
                    }
                )
        return issues

    def _check_conflicts(self) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        conflicts = self.vector_store.get_conflicts()
        for conflict in conflicts.get("conflicts", []):
            issues.append(
                {
                    "type": "similarity_conflict",
                    "conflict_id": conflict.get("conflict_id"),
                    "timestamp": conflict.get("timestamp"),
                    "reasoning": conflict.get("reasoning"),
                    "confidence": conflict.get("confidence"),
                    "chunks": conflict.get("chunks", []),
                    "action_needed": "Review and decide: merge, keep both, or update one",
                }
            )
        return issues
