import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_mcp_v1_http_sse as http_server
from src.mcp_server_v1 import (
    _assess_reaffirmation_duplicate_risk,
    _finalize_warning_payload,
    _looks_like_state_update,
)
from src.vector_store import VectorStore, _sanitize_backup_label


class _FixedEmbeddingManager:
    def embed_text(self, text: str) -> list[float]:
        # A deterministic, non-zero vector keeps this test independent of model
        # downloads while exercising the real Chroma persistence path.
        return [float(len(text) or 1), 1.0, 0.5]


class RegressionTests(unittest.TestCase):
    def test_backup_labels_cannot_escape_backup_root(self) -> None:
        self.assertEqual(_sanitize_backup_label("../../secrets"), "secrets")
        self.assertEqual(_sanitize_backup_label("..."), "")
        self.assertEqual(_sanitize_backup_label("release 1 / blue"), "release-1---blue")

    def test_state_updates_are_detected_without_flagging_net_new_text(self) -> None:
        self.assertTrue(_looks_like_state_update("Correction: our support window is now 9-5."))
        self.assertTrue(_looks_like_state_update("The old plan was replaced by the new rollout."))
        self.assertFalse(_looks_like_state_update("Our support team works across three time zones."))

    def test_reaffirmation_duplicate_risk_is_explicit(self) -> None:
        risk = _assess_reaffirmation_duplicate_risk(
            "The current support window is Monday through Friday, 9 to 5.",
            "Reaffirmed: the current support window is Monday through Friday, 9 to 5.",
        )
        self.assertTrue(risk["suspected"])
        self.assertIn("reason", risk)

    def test_warning_payload_has_a_stable_self_heal_contract(self) -> None:
        result = {"warnings": [{"code": "oversized_chunk", "severity": "medium", "message": "Too large."}]}
        _finalize_warning_payload(result)
        self.assertEqual(result["warning_schema_version"], 1)
        self.assertEqual(result["warning_codes"], ["oversized_chunk"])
        self.assertTrue(result["self_heal_required"])
        self.assertEqual(result["self_heal_status"], "required")

    def test_unauthenticated_server_refuses_a_public_bind(self) -> None:
        with (
            patch.object(http_server, "_auth_mode", "none"),
            patch.dict("os.environ", {"MCP_BIND_HOST": "0.0.0.0"}, clear=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "unauthenticated MCP server"):
                http_server.run_server()

    def test_version_update_preserves_history_and_soft_delete_hides_old_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VectorStore(
                persist_dir=str(Path(temp_dir) / "chroma"),
                embedding_manager=_FixedEmbeddingManager(),
            )
            original_id = store.add_chunk("The focus block is 6:30 to 9:00 AM.")
            current_id = store.update_chunk(
                original_id,
                "The focus block is now 7:00 to 9:30 AM.",
                strategy="version",
            )

            self.assertIsNotNone(current_id)
            original = store.get_chunk(original_id)
            current = store.get_chunk(current_id)
            self.assertTrue(original.metadata["deprecated"])
            self.assertEqual(current.metadata["supersedes"], original_id)
            self.assertEqual(
                [item["chunk_id"] for item in store.get_evolution_chain(current_id)],
                [current_id, original_id],
            )


if __name__ == "__main__":
    unittest.main()
