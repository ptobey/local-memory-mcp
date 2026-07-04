from __future__ import annotations

import sys
import time
import contextvars
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import os

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .health_monitor import HealthMonitor
from .reconciliation import ReconciliationEngine
from .vector_store import VectorStore, _load_config


class _StaticTokenVerifier(TokenVerifier):
    def __init__(self, token: str, scopes: list[str] | None, resource: str | None):
        self._token = token
        self._scopes = scopes or []
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        if token != self._token:
            return None
        return AccessToken(token=token, client_id="static_token", scopes=self._scopes, resource=self._resource)


_config = _load_config()
_allowed_hosts = _config.get("MCP_ALLOWED_HOSTS") or []
_allowed_origins = _config.get("MCP_ALLOWED_ORIGINS") or []
_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=bool(_config.get("MCP_ENABLE_DNS_REBINDING_PROTECTION", True)),
    allowed_hosts=_allowed_hosts,
    allowed_origins=_allowed_origins,
)
_auth_mode = str(_config.get("MCP_AUTH_MODE", "none")).lower()
_token = os.environ.get("MCP_BEARER_TOKEN") or _config.get("MCP_BEARER_TOKEN")
_issuer_url = _config.get("MCP_ISSUER_URL") or "http://localhost:8000"
_resource_url = _config.get("MCP_RESOURCE_SERVER_URL") or _issuer_url
_required_scopes = _config.get("MCP_REQUIRED_SCOPES") or []
_default_oauth_token_ttl = 60 * 60 * 24 * 30


def _normalize_host_pattern(value: Any) -> str:
    host = str(value or "").strip().lower()
    if host.endswith(":*"):
        host = host[:-2]
    return host


def _is_local_host_pattern(value: Any) -> bool:
    host = _normalize_host_pattern(value)
    if not host:
        return True
    if host in {"localhost", "127.0.0.1", "::1", "[::1]"}:
        return True
    if host.startswith("127."):
        return True
    return False


def _is_local_origin(value: Any) -> bool:
    origin = str(value or "").strip().lower()
    if not origin:
        return True
    if origin.startswith("http://127.") or origin.startswith("https://127."):
        return True
    if origin.startswith("http://localhost") or origin.startswith("https://localhost"):
        return True
    if origin.startswith("http://[::1]") or origin.startswith("https://[::1]"):
        return True
    return False


def _coerce_int(value: Any, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback

_auth_settings = None
_token_verifier = None
_auth_provider = None
if _auth_mode == "none":
    external_hosts = [
        str(host)
        for host in _allowed_hosts
        if str(host).strip() and str(host).strip() != "*" and not _is_local_host_pattern(host)
    ]
    external_origins = [
        str(origin)
        for origin in _allowed_origins
        if str(origin).strip() and not _is_local_origin(origin)
    ]
    if external_hosts or external_origins:
        raise RuntimeError(
            "Refusing insecure config: MCP_AUTH_MODE='none' with non-local hosts/origins. "
            "Switch to MCP_AUTH_MODE='bearer' or 'oauth' before exposing this server."
        )
if _auth_mode == "bearer":
    if not _token:
        raise RuntimeError("MCP auth is enabled but MCP_BEARER_TOKEN is not set.")
    _auth_settings = AuthSettings(
        issuer_url=_issuer_url,
        resource_server_url=_resource_url,
        required_scopes=_required_scopes,
    )
    _token_verifier = _StaticTokenVerifier(_token, _required_scopes, _resource_url)
elif _auth_mode == "oauth":
    from .oauth_provider import InMemoryOAuthProvider, OAuthClientAnyRedirect

    client_id = os.environ.get("MCP_OAUTH_CLIENT_ID") or _config.get("MCP_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("MCP_OAUTH_CLIENT_SECRET") or _config.get("MCP_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("OAuth is enabled but MCP_OAUTH_CLIENT_ID/SECRET are not set.")
    redirect_allowlist = _config.get("MCP_OAUTH_REDIRECT_ALLOWLIST") or []
    _oauth_token_ttl = _coerce_int(
        os.environ.get("MCP_OAUTH_TOKEN_TTL_SECONDS")
        or _config.get("MCP_OAUTH_TOKEN_TTL_SECONDS"),
        _default_oauth_token_ttl,
    )
    if _oauth_token_ttl <= 0:
        _oauth_token_ttl = _default_oauth_token_ttl
    scope_str = " ".join(_required_scopes) if _required_scopes else None
    client = OAuthClientAnyRedirect(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uris=["https://example.com/oauth/callback"],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code"],
        response_types=["code"],
        scope=scope_str,
        redirect_allowlist=redirect_allowlist,
    )
    _auth_provider = InMemoryOAuthProvider(client=client, token_ttl_seconds=_oauth_token_ttl)
    _auth_settings = AuthSettings(
        issuer_url=_issuer_url,
        resource_server_url=_resource_url,
        required_scopes=_required_scopes,
    )

mcp = FastMCP(
    "LocalMemoryMCPV1",
    transport_security=_transport_security,
    auth=_auth_settings,
    token_verifier=_token_verifier,
    auth_server_provider=_auth_provider,
)
_store = None
_reconciler = None
_health_monitor = None
_recent_search_receipts: Dict[str, Dict[str, Any]] = {}
_request_scope_hint: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_request_scope_hint", default="default"
)
_SEARCH_RECEIPT_TTL_SECONDS = 300


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_request_scope_hint(scope_key: str):
    """Set a request-local scope key used for search receipts (SSE middleware use)."""
    return _request_scope_hint.set(str(scope_key or "default"))


def reset_request_scope_hint(token) -> None:
    _request_scope_hint.reset(token)


def _current_receipt_scope_key() -> str:
    try:
        key = _request_scope_hint.get()
    except Exception:
        key = "default"
    return str(key or "default")


def _record_search_receipt(
    query: str,
    results: List[Dict[str, Any]],
    include_deprecated: bool,
) -> None:
    """Store a short-lived proof that search() was called (scoped per request/client)."""
    scope_key = _current_receipt_scope_key()
    _recent_search_receipts[scope_key] = {
        "recorded_at": _now_iso(),
        "recorded_at_epoch": time.time(),
        "query": query,
        "include_deprecated": bool(include_deprecated),
        "top_results": [
            {
                "chunk_id": item.get("chunk_id"),
                "rank_score": item.get("rank_score"),
                "score": item.get("score"),
                "deprecated": bool(item.get("deprecated", False)),
                "last_modified": item.get("last_modified") or item.get("timestamp"),
            }
            for item in results[:5]
        ],
    }


def _get_recent_search_receipt(max_age_seconds: int = _SEARCH_RECEIPT_TTL_SECONDS) -> Optional[Dict[str, Any]]:
    scope_key = _current_receipt_scope_key()
    raw_receipt = _recent_search_receipts.get(scope_key)
    if not raw_receipt:
        return None
    recorded_at_epoch = raw_receipt.get("recorded_at_epoch")
    if not isinstance(recorded_at_epoch, (int, float)):
        return None
    age_seconds = max(0.0, time.time() - float(recorded_at_epoch))
    if age_seconds > max_age_seconds:
        _recent_search_receipts.pop(scope_key, None)
        return None
    top_results = raw_receipt.get("top_results") or []
    return {
        "found": True,
        "age_seconds": round(age_seconds, 2),
        "max_age_seconds": int(max_age_seconds),
        "recorded_at": raw_receipt.get("recorded_at"),
        "query": raw_receipt.get("query"),
        "include_deprecated": bool(raw_receipt.get("include_deprecated", False)),
        "scope_key": scope_key,
        "top_chunk_ids": [str(item.get("chunk_id")) for item in top_results if item.get("chunk_id")],
    }


def _looks_like_state_update(text: str) -> bool:
    lowered = (text or "").lower()
    stripped = lowered.strip()
    lead = stripped[:120]

    # Strong cues: almost always indicate an update/correction to existing state.
    strong_prefix_cues = (
        "update:",
        "decision update",
        "correction:",
        "correction to",
        "small correction",
        "bigger update",
        "new hierarchy",
    )
    if any(cue in lead for cue in strong_prefix_cues):
        return True

    strong_anywhere_cues = (
        " no longer ",
        " now preferred",
        " supersedes ",
        " superseded ",
        " replaced by ",
        " moved from ",
        " changed from ",
        " was overestimate",
        " overestimate",
        " corrected from ",
    )
    if any(cue in lowered for cue in strong_anywhere_cues):
        return True

    # "Clarification" is common in both updates and net-new baseline framing.
    # Treat it as an update only when paired with a state-change/restatement cue.
    clarification_present = ("clarification:" in lead) or (" clarification " in lowered)
    clarification_update_cues = (
        " remains ",
        " still ",
        " not an active ",
        " eliminated",
        " deprecated",
        " supersedes ",
        " current hierarchy",
        " current live ",
        " canonical ",
        " reaffirm",
    )
    if clarification_present and any(cue in lowered for cue in clarification_update_cues):
        return True

    # Temporal/state framing alone ("as of", "current", "snapshot") is too noisy.
    # Only treat it as update-like when paired with clear restatement/change language.
    temporal_state_cues = (
        "as of ",
        "state snapshot",
        "canonical snapshot",
        "current authoritative",
        "current live",
        "locked snapshot",
    )
    temporal_restatement_cues = (
        " remains ",
        " not a co-equal",
        " fallback",
        " comparison-only",
        " active hierarchy",
        " no change",
        " reaffirm",
        " preserve history",
    )
    if any(cue in lowered for cue in temporal_state_cues) and any(
        cue in lowered for cue in temporal_restatement_cues
    ):
        return True

    return False


def _is_strong_update_candidate(candidate: Dict[str, Any]) -> bool:
    """Conservative threshold to reduce false positives for store() duplicate/update warnings."""
    try:
        score = float(candidate.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        rank_score = float(candidate.get("rank_score") or 0.0)
    except (TypeError, ValueError):
        rank_score = 0.0
    try:
        query_overlap = float(candidate.get("query_overlap") or 0.0)
    except (TypeError, ValueError):
        query_overlap = 0.0
    # Require both some lexical overlap and strong semantic similarity.
    if query_overlap < 0.20:
        return False
    return (score >= 0.60) or (rank_score >= 0.72 and query_overlap >= 0.30)


def _normalize_for_equivalence(text: str) -> str:
    normalized = (text or "").lower()
    normalized = normalized.replace("\u2019", "'").replace("\u2018", "'")
    normalized = normalized.replace("\u201c", '"').replace("\u201d", '"')
    normalized = normalized.replace("\u2013", "-").replace("\u2014", "-")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _tokenize_equivalence(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", _normalize_for_equivalence(text))


def _has_explicit_change_cues(text: str) -> bool:
    lowered = _normalize_for_equivalence(text)
    cues = (
        "update:",
        "correction:",
        "small correction",
        "changed",
        "changed from",
        "moved from",
        "no longer",
        "now ",
        "corrected",
        "revised",
        "clarified roles",
        "clarification",
    )
    return any(cue in lowered for cue in cues)


def _has_reaffirmation_cues(text: str) -> bool:
    lowered = _normalize_for_equivalence(text)
    cues = (
        "reaffirm",
        "revalidated",
        "revalidate",
        "relock",
        "clean relock",
        "locked baseline",
        "clean snapshot",
        "no change to",
        "no state change",
        "same current status",
        "same current state",
        "same operational state",
        "without modifying",
        "revalidated baseline",
        "reaffirmed",
    )
    return any(cue in lowered for cue in cues)


def _assess_reaffirmation_duplicate_risk(old_text: str, new_text: str) -> Dict[str, Any]:
    old_norm = _normalize_for_equivalence(old_text)
    new_norm = _normalize_for_equivalence(new_text)
    if old_norm == new_norm and old_norm:
        return {
            "suspected": True,
            "reason": "normalized_exact_match",
            "hints": {
                "normalized_exact_match": True,
                "lexical_overlap_old": 1.0,
                "lexical_overlap_new": 1.0,
                "jaccard": 1.0,
                "length_delta_ratio": 0.0,
            },
        }

    old_tokens = _tokenize_equivalence(old_text)
    new_tokens = _tokenize_equivalence(new_text)
    if not old_tokens or not new_tokens:
        return {"suspected": False}

    old_set = set(old_tokens)
    new_set = set(new_tokens)
    inter = old_set & new_set
    union = old_set | new_set
    jaccard = len(inter) / float(len(union)) if union else 0.0
    overlap_old = len(inter) / float(len(old_set)) if old_set else 0.0
    overlap_new = len(inter) / float(len(new_set)) if new_set else 0.0
    length_delta_ratio = abs(len(new_tokens) - len(old_tokens)) / float(max(len(old_tokens), 1))
    explicit_change_cues = _has_explicit_change_cues(new_text)
    reaffirmation_cues = _has_reaffirmation_cues(new_text)

    suspected = (
        (
            (not explicit_change_cues)
            and jaccard >= 0.72
            and min(overlap_old, overlap_new) >= 0.82
            and length_delta_ratio <= 0.25
        )
        or (
            reaffirmation_cues
            and jaccard >= 0.62
            and min(overlap_old, overlap_new) >= 0.74
            and length_delta_ratio <= 0.40
        )
    )
    if not suspected:
        return {"suspected": False}

    reason = "high_lexical_equivalence_possible_reaffirmation"
    if reaffirmation_cues and explicit_change_cues:
        reason = "reaffirmation_language_with_version_boilerplate"
    elif reaffirmation_cues:
        reason = "reaffirmation_language_possible_no_state_change"

    return {
        "suspected": True,
        "reason": reason,
        "hints": {
            "normalized_exact_match": False,
            "lexical_overlap_old": round(overlap_old, 3),
            "lexical_overlap_new": round(overlap_new, 3),
            "jaccard": round(jaccard, 3),
            "length_delta_ratio": round(length_delta_ratio, 3),
            "explicit_change_cues_detected": explicit_change_cues,
            "reaffirmation_cues_detected": reaffirmation_cues,
        },
    }


def _candidate_from_search_result(item: Dict[str, Any]) -> Dict[str, Any]:
    candidate = {
        "chunk_id": item.get("chunk_id"),
        "score": item.get("score"),
        "rank_score": item.get("rank_score"),
        "query_overlap": item.get("query_overlap"),
        "last_modified": item.get("last_modified") or item.get("timestamp"),
        "supersedes": item.get("supersedes") or "",
        "deprecated": bool(item.get("deprecated", False)),
        "text_preview": " ".join(str(item.get("text", "")).split())[:140],
    }
    candidate["strong_match"] = _is_strong_update_candidate(candidate)
    return candidate


def _add_warning(
    result: Dict[str, Any],
    *,
    code: str,
    severity: str,
    message: str,
    recommended_next_action: Optional[str] = None,
    **extra: Any,
) -> None:
    warnings = result.setdefault("warnings", [])
    severity_value = str(severity or "info").lower()
    if severity_value not in {"high", "medium", "low", "info"}:
        severity_value = "info"

    reason_value = str(extra.get("reason") or "")
    candidate_ids_value = extra.get("candidate_chunk_ids")
    compare_chunk_id_value = str(extra.get("compare_chunk_id") or "")

    # Prevent duplicate warning entries in future code paths (same issue repeated).
    for existing in warnings:
        if (
            str(existing.get("code") or "") == str(code)
            and str(existing.get("reason") or "") == reason_value
            and str(existing.get("compare_chunk_id") or "") == compare_chunk_id_value
        ):
            existing_candidate_ids = existing.get("candidate_chunk_ids")
            if existing_candidate_ids == candidate_ids_value:
                return

    entry: Dict[str, Any] = {
        "code": str(code),
        "severity": severity_value,
        "message": str(message),
    }
    if recommended_next_action:
        entry["recommended_next_action"] = str(recommended_next_action)
    for key, value in extra.items():
        if value is not None:
            entry[key] = value
    warnings.append(entry)


def _warning_severity_rank(severity: Any) -> int:
    value = str(severity or "").lower()
    ranks = {
        "high": 3,
        "medium": 2,
        "low": 1,
        "info": 0,
    }
    return ranks.get(value, 0)


_WARNING_PAYLOAD_SCHEMA_VERSION = 1
_SELF_HEAL_PROTOCOL_VERSION = 1


def _attach_self_heal_contract(result: Dict[str, Any]) -> None:
    warnings = result.get("warnings")
    has_warnings = bool(isinstance(warnings, list) and warnings)

    result["self_heal_protocol_version"] = _SELF_HEAL_PROTOCOL_VERSION
    result["self_heal_required"] = has_warnings
    result["self_heal_status"] = "required" if has_warnings else "clean"
    result["self_heal_reason_codes"] = list(result.get("warning_codes") or [])

    # Stable playbook for AI remediation loops.
    result["self_heal_steps"] = [
        "1) Review warnings and warning_context",
        "2) Inspect candidate IDs with get_chunk/search",
        "3) Apply update(strategy='version') or delete(hard_delete=False) as needed",
        "4) Verify with search() that canonical current state is clean",
    ]

    if has_warnings:
        result["self_heal_instruction"] = (
            "Self-heal required before finalizing the user-facing response. "
            "Resolve warning-driven stale parallels and duplicate/reaffirmation issues, "
            "preserve history, then verify with search()."
        )
    else:
        result["self_heal_instruction"] = (
            "No self-heal action required for this operation."
        )


def _finalize_warning_payload(result: Dict[str, Any]) -> None:
    warnings = result.get("warnings")
    if not isinstance(warnings, list) or not warnings:
        _attach_self_heal_contract(result)
        return

    warnings.sort(
        key=lambda item: (
            -_warning_severity_rank(item.get("severity")),
            str(item.get("code") or ""),
        )
    )

    warning_codes: List[str] = []
    seen_codes: set[str] = set()
    for item in warnings:
        code = str(item.get("code") or "")
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        warning_codes.append(code)

    primary = warnings[0]
    max_severity = str(primary.get("severity") or "info")

    candidate_chunk_ids: List[str] = []
    seen_candidate_ids: set[str] = set()
    compare_chunk_ids: List[str] = []
    seen_compare_ids: set[str] = set()
    warning_reasons: List[str] = []
    seen_reasons: set[str] = set()

    for item in warnings:
        reason = str(item.get("reason") or "").strip()
        if reason and reason not in seen_reasons:
            seen_reasons.add(reason)
            warning_reasons.append(reason)

        compare_id = str(item.get("compare_chunk_id") or "").strip()
        if compare_id and compare_id not in seen_compare_ids:
            seen_compare_ids.add(compare_id)
            compare_chunk_ids.append(compare_id)

        raw_candidate_ids = item.get("candidate_chunk_ids")
        if isinstance(raw_candidate_ids, list):
            for cid in raw_candidate_ids:
                cid_str = str(cid or "").strip()
                if not cid_str or cid_str in seen_candidate_ids:
                    continue
                seen_candidate_ids.add(cid_str)
                candidate_chunk_ids.append(cid_str)

    result["has_warnings"] = True
    result["review_recommended"] = True
    result["warning_schema_version"] = _WARNING_PAYLOAD_SCHEMA_VERSION
    result["warning_count"] = len(warnings)
    result["warning_codes"] = warning_codes
    result["max_warning_severity"] = max_severity
    result["primary_warning"] = {
        "code": str(primary.get("code") or ""),
        "severity": max_severity,
        "message": str(primary.get("message") or ""),
        "recommended_next_action": str(primary.get("recommended_next_action") or ""),
    }
    result["warning_summary"] = {
        "schema_version": _WARNING_PAYLOAD_SCHEMA_VERSION,
        "count": len(warnings),
        "max_severity": max_severity,
        "codes": warning_codes,
        "primary_code": str(primary.get("code") or ""),
        "review_recommended": True,
    }
    result["warning_context"] = {
        "schema_version": _WARNING_PAYLOAD_SCHEMA_VERSION,
        "candidate_chunk_ids": candidate_chunk_ids,
        "candidate_chunk_count": len(candidate_chunk_ids),
        "compare_chunk_ids": compare_chunk_ids,
        "compare_chunk_count": len(compare_chunk_ids),
        "reasons": warning_reasons,
        "primary_reason": str(primary.get("reason") or ""),
    }
    _attach_self_heal_contract(result)


def _find_update_candidates(
    store_instance: VectorStore,
    text: str,
    top_k: int = 5,
    include_deprecated: bool = False,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    # Duplicate/update detection relies on raw bi-encoder score + rank_score
    # thresholds, so keep this path off the reranker (which rewrites rank_score).
    similar = store_instance.search(
        query=text,
        top_k=top_k,
        include_deprecated=include_deprecated,
        use_reranker=False,
    )
    for item in similar:
        candidates.append(_candidate_from_search_result(item))
    return candidates


def _evolution_chain_ids(store_instance: VectorStore, chunk_id: str) -> set[str]:
    chain_ids: set[str] = set()
    try:
        for node in store_instance.get_evolution_chain(chunk_id):
            cid = str(node.get("chunk_id") or "")
            if cid:
                chain_ids.add(cid)
    except Exception:
        pass
    return chain_ids


def _find_stale_parallel_candidates(
    store_instance: VectorStore,
    anchor_chunk_id: str,
    new_text: str,
    top_k: int = 6,
) -> List[Dict[str, Any]]:
    chain_ids = _evolution_chain_ids(store_instance, anchor_chunk_id)
    candidates = _find_update_candidates(store_instance, text=new_text, top_k=top_k, include_deprecated=False)
    filtered: List[Dict[str, Any]] = []
    for c in candidates:
        cid = str(c.get("chunk_id") or "")
        if not cid:
            continue
        if cid in chain_ids:
            continue
        if not bool(c.get("strong_match", False)):
            continue
        filtered.append(c)
    return filtered


def _ensure_ready():
    global _store, _reconciler, _health_monitor
    if _store is None:
        _store = VectorStore()
    if _reconciler is None:
        _reconciler = ReconciliationEngine(_store)
    if _health_monitor is None:
        _health_monitor = HealthMonitor(_store, _reconciler)
    return _store, _reconciler, _health_monitor


@mcp.tool()
def store(text: str, author: Optional[str] = None) -> Dict[str, Any]:
    """
    Store net-new information in memory.

    Recommended workflow for decision/state/preference updates:
    1) search() first (2-4 targeted queries)
    2) If this updates existing memory, use update(strategy="version")
    3) Soft-delete stale conflicting active chunks with delete(hard_delete=False)
    4) Run one verification search()

    Use store() only when the information is truly new and not a correction/refinement
    of an existing chunk.

    If the input looks like an update/correction/clarification, search first — it
    probably belongs in update(), not store().
    If the response comes back with warnings (self_heal_required=true), resolve them
    before finishing: review the flagged candidate chunks, then update(strategy="version")
    the right one and soft-delete any stale parallel summaries.

    You decide how to chunk this information.
    Aim for 150-300 words per chunk for best retrieval.
    Chunks over 500 words will be flagged during health checks.

    Pass author to record who is making this write (e.g. "Patrick" or "Khaleel").
    It is stored on the chunk and shown by get_audit(); omit it if genuinely unknown.

    IMPORTANT — fidelity over brevity: Write concisely, but never sacrifice meaning to
    save tokens. Preserve nuance, reasoning, qualifiers, caveats, specific details, and
    any emotional or subjective framing — those are the point of a memory and must
    survive so the chunk is still useful when recalled later. Trim only genuine filler
    (redundant restatements, empty transitions), never substance. Use natural language;
    lists are fine where they aid recall. Keep names, numbers, and distinctions exact —
    don't abbreviate away precision. When unsure whether a detail matters, keep it.
    """
    _log(f"[STORE] Adding text: {text[:100]}...")
    store_instance, reconciler, _ = _ensure_ready()
    looks_like_update = _looks_like_state_update(text)
    candidate_update_targets: List[Dict[str, Any]] = []
    recent_search_receipt = _get_recent_search_receipt()
    if looks_like_update:
        _log("[STORE] Advisory: input looks like a state update/correction; search/update workflow preferred.")
        try:
            candidate_update_targets = _find_update_candidates(
                store_instance,
                text=text,
                top_k=3,
                include_deprecated=False,
            )
            if candidate_update_targets:
                _log(
                    "[STORE] Advisory candidates: "
                    + ", ".join(str(c.get("chunk_id")) for c in candidate_update_targets)
                )
        except Exception as exc:
            _log(f"[STORE] Advisory candidate lookup failed: {exc}")
    recent_ids = set((recent_search_receipt or {}).get("top_chunk_ids") or [])
    candidate_ids = {str(item.get("chunk_id")) for item in candidate_update_targets if item.get("chunk_id")}
    strong_candidate_ids = {
        str(item.get("chunk_id"))
        for item in candidate_update_targets
        if item.get("chunk_id") and bool(item.get("strong_match", False))
    }
    receipt_matches_candidates = bool(recent_ids & candidate_ids) if recent_ids and candidate_ids else False
    receipt_matches_strong_candidates = (
        bool(recent_ids & strong_candidate_ids) if recent_ids and strong_candidate_ids else False
    )
    if looks_like_update and strong_candidate_ids and not recent_search_receipt:
        write_risk = "high"
    elif looks_like_update and strong_candidate_ids and not receipt_matches_strong_candidates:
        write_risk = "medium"
    else:
        write_risk = "low"

    advisory_text = (
        "This text looks like an update/correction. Preferred workflow: search -> "
        "update(strategy='version') -> delete stale conflicting chunks -> search verify."
    )

    chunk_id = store_instance.add_chunk(text=text, confidence=1.0, source_type="user_statement", author=author)
    reconciler.queue_for_reconciliation(chunk_id)
    result: Dict[str, Any] = {"chunk_id": chunk_id, "word_count": len(text.split()), "stored": True}
    if looks_like_update:
        result["advisory"] = advisory_text
        result["update_like"] = True
        result["write_risk"] = write_risk
        result["search_receipt"] = recent_search_receipt or {
            "found": False,
            "max_age_seconds": _SEARCH_RECEIPT_TTL_SECONDS,
        }
        if recent_search_receipt:
            result["search_receipt"]["matches_candidate_update_targets"] = receipt_matches_candidates
            result["search_receipt"]["matches_strong_candidate_targets"] = receipt_matches_strong_candidates
        if candidate_update_targets:
            result["candidate_update_targets"] = candidate_update_targets
        if strong_candidate_ids:
            if recent_search_receipt and receipt_matches_strong_candidates:
                warning_reason = "strong_candidate_match_after_relevant_search"
            elif recent_search_receipt and not receipt_matches_strong_candidates:
                warning_reason = "recent_search_not_relevant_to_strong_candidates"
            else:
                warning_reason = "no_recent_search_for_strong_candidates"
            recommended_action = (
                "This store() looks like a state update and strong active matches exist. "
                "Inspect candidate_update_targets and decide how to resolve it. Preferred path: "
                "use search() if needed, then update(strategy='version') on the correct chunk, "
                "and delete stale parallel summaries if needed."
            )
            _add_warning(
                result,
                code="possible_update_like_store_with_strong_matches",
                severity=write_risk,
                message=(
                    "This store() looks like an update/refinement to existing active memory and strong "
                    "candidate matches were found."
                ),
                recommended_next_action=recommended_action,
                reason=warning_reason,
                candidate_chunk_ids=[str(cid) for cid in sorted(strong_candidate_ids)],
            )
            _log(
                f"[STORE] Duplicate warning risk={write_risk} strong_candidates={len(strong_candidate_ids)} "
                f"recent_search={'yes' if recent_search_receipt else 'no'}"
            )
        elif write_risk in {"high", "medium"}:
            result["soft_nudge"] = (
                "This likely updates existing memory. Review candidate_update_targets and prefer "
                "update(strategy='version') plus delete() for stale parallel summaries."
            )
            _add_warning(
                result,
                code="possible_update_like_store",
                severity=write_risk,
                message="This store() looks update-like and may create parallel state summaries if left as-is.",
                recommended_next_action=result["soft_nudge"],
                candidate_chunk_ids=[
                    str(item.get("chunk_id"))
                    for item in candidate_update_targets
                    if item.get("chunk_id")
                ] or None,
            )
            _log(
                f"[STORE] Soft nudge risk={write_risk} recent_search="
                f"{'yes' if recent_search_receipt else 'no'}"
            )
    _finalize_warning_payload(result)
    return result


@mcp.tool()
def search(
    query: str,
    top_k: int = 5,
    min_confidence: float = 0.0,
    include_deprecated: bool = False,
) -> List[Dict[str, Any]]:
    """
    Semantic search for existing memory.

    Pass a natural-language query and roughly how many results you want (top_k) —
    you don't tune anything else. Treat top_k as a target, not a hard limit: you'll
    get top_k, or a few more when several extra chunks are strongly relevant, so
    nothing useful is cut off.

    Use this before store()/update() when handling:
    - decisions and decision hierarchy changes
    - preference changes
    - corrections
    - status updates ("now", "as of", "no longer", "eliminated", etc.)

    Each result carries fields for picking the canonical/current chunk when several
    conflict on one topic: timestamp, last_modified, source_type, supersedes,
    deprecated, and recency_score (1.0 = today, decaying to 0.0 at 365 days). Use
    recency_score to spot the most recent source of truth; it's informational and
    does not change the ranking.
    """
    store_instance, _, _ = _ensure_ready()
    results = store_instance.search(
        query=query,
        top_k=top_k,
        min_confidence=min_confidence,
        include_deprecated=include_deprecated,
    )
    normalized_results: List[Dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        canonical_id = str(row.get("id") or row.get("chunk_id") or "").strip()
        if canonical_id:
            row["id"] = canonical_id
            row["chunk_id"] = canonical_id
        normalized_results.append(row)
    results = normalized_results
    _record_search_receipt(query=query, results=results, include_deprecated=include_deprecated)
    _log(f"[SEARCH] query='{query}' results={len(results)}")
    return results


def update_chunk(
    chunk_id: str,
    new_text: str,
    strategy: str = "version",
    author: Optional[str] = None,
) -> Dict[str, Any]:
    store_instance, reconciler, _ = _ensure_ready()
    existing = store_instance.get_chunk(chunk_id)
    if not existing:
        return {"chunk_id": None, "error": "Chunk not found"}
    duplicate_risk = None
    if strategy == "version":
        try:
            duplicate_risk = _assess_reaffirmation_duplicate_risk(existing.text, new_text)
        except Exception:
            duplicate_risk = None
    updated_id = store_instance.update_chunk(chunk_id=chunk_id, new_text=new_text, strategy=strategy, author=author)
    if not updated_id:
        return {"chunk_id": None, "error": "Chunk not found"}
    reconciler.queue_for_reconciliation(updated_id)
    result: Dict[str, Any] = {"chunk_id": updated_id, "updated_from_chunk_id": chunk_id, "strategy": strategy}
    if strategy == "version" and isinstance(duplicate_risk, dict) and duplicate_risk.get("suspected"):
        duplicate_warning_reason = str(duplicate_risk.get("reason") or "possible_reaffirmation")
        duplicate_warning_hints = duplicate_risk.get("hints") or {}
        recommended_action = (
            "This version looks highly similar to the superseded chunk (possible reaffirmation/no-state-change version). "
            "AI should decide whether to keep it as an intentional freeze point or deprecate/delete the redundant version."
        )
        _add_warning(
            result,
            code="possible_reaffirmation_version",
            severity="medium",
            message=(
                "This version looks highly similar to the superseded chunk and may be a reaffirmation/no-state-change version."
            ),
            recommended_next_action=recommended_action,
            compare_chunk_id=chunk_id,
            hints=duplicate_warning_hints,
            reason=duplicate_warning_reason,
        )
    if strategy == "version":
        try:
            stale_parallel_candidates = _find_stale_parallel_candidates(
                store_instance=store_instance,
                anchor_chunk_id=updated_id,
                new_text=new_text,
                top_k=6,
            )
            if stale_parallel_candidates:
                result["candidate_stale_parallel_chunks"] = stale_parallel_candidates
                result["cleanup_advisory"] = (
                    "Review candidate_stale_parallel_chunks and soft-delete stale parallel summaries "
                    "if they are no longer the desired canonical current state (delete(hard_delete=False)). "
                    "AI decides which candidates are truly stale."
                )
                _add_warning(
                    result,
                    code="candidate_stale_parallel_summaries",
                    severity="medium",
                    message=(
                        "Strongly similar active chunks outside the supersedes chain were found. "
                        "Some may be stale parallel summaries."
                    ),
                    recommended_next_action=result["cleanup_advisory"],
                    candidate_chunk_ids=[
                        str(item.get("chunk_id"))
                        for item in stale_parallel_candidates
                        if item.get("chunk_id")
                    ] or None,
                    candidate_count=len(stale_parallel_candidates),
                )
        except Exception as exc:
            result["cleanup_advisory_error"] = str(exc)
    _finalize_warning_payload(result)
    return result


@mcp.tool()
def update(chunk_id: str, new_text: str, strategy: str = "version", author: Optional[str] = None) -> Dict[str, Any]:
    """
    Update existing memory.

    Pass author to record who is making this edit (e.g. "Patrick"); it's added to
    the chunk's audit trail (see get_audit()).

    Prefer strategy="version" for evolving state so history is preserved and the old chunk
    is deprecated. Use strategy="replace" only for direct corrections to the same record.

    After versioning a state chunk, search again for older parallel summaries outside
    this supersedes chain that may now be stale — soft-delete the ones that are.
    If the response comes back with warnings (self_heal_required=true), resolve them
    before finishing.

    IMPORTANT — fidelity over brevity: Write new_text concisely, but never sacrifice
    meaning to save tokens. Preserve nuance, reasoning, qualifiers, caveats, specific
    details, and any emotional or subjective framing — those are the point of a memory
    and must survive so the chunk is still useful when recalled later. Trim only genuine
    filler, never substance. Keep names, numbers, and distinctions exact — don't
    abbreviate away precision. When unsure whether a detail matters, keep it.
    """
    return update_chunk(chunk_id=chunk_id, new_text=new_text, strategy=strategy, author=author)


def delete_chunk(chunk_id: str, hard_delete: bool = False) -> Dict[str, Any]:
    store_instance, _, _ = _ensure_ready()
    result = store_instance.delete_chunk(chunk_id, hard_delete=hard_delete)
    return {"chunk_id": chunk_id, "deleted": result, "hard_delete": hard_delete}


@mcp.tool()
def delete(chunk_id: str, hard_delete: bool = False) -> Dict[str, Any]:
    """
    Delete or deprecate a chunk.

    Default behavior is soft delete (deprecate). Use this after versioning a chunk when
    stale conflicting chunks should no longer remain active in retrieval.
    Prefer soft delete for historical context retention.
    """
    return delete_chunk(chunk_id=chunk_id, hard_delete=hard_delete)


@mcp.tool()
def get_chunk(chunk_id: str) -> Dict[str, Any]:
    """Fetch one chunk's full text and metadata by chunk_id."""
    store_instance, _, _ = _ensure_ready()
    record = store_instance.get_chunk(chunk_id)
    if not record:
        return {}
    return {
        "chunk_id": record.chunk_id,
        "text": record.text,
        "metadata": record.metadata,
    }


@mcp.tool()
def get_evolution_chain(chunk_id: str) -> List[Dict[str, Any]]:
    """A chunk's version history: this chunk plus everything it supersedes, newest first."""
    store_instance, _, _ = _ensure_ready()
    return store_instance.get_evolution_chain(chunk_id)


@mcp.tool()
def get_audit(chunk_id: str) -> Dict[str, Any]:
    """Audit trail for a chunk: who created it and when, and who updated it and when.

    Returns created_by / created_at, current_author, last_modified, edit_count, and a
    chronological history[] of {action, author, at} entries spanning the version chain.
    Chunks written before authorship tracking show author "unknown".
    """
    store_instance, _, _ = _ensure_ready()
    return store_instance.get_audit_history(chunk_id)


@mcp.tool()
def self_check() -> Dict[str, Any]:
    """Health check: chunk counts, potential duplicates, unresolved conflicts, embedding integrity."""
    store_instance, _, _ = _ensure_ready()
    return store_instance.self_check()


@mcp.tool()
def create_backup(label: Optional[str] = None) -> Dict[str, Any]:
    """Snapshot the full memory store to disk (optionally labeled). Use before risky bulk edits."""
    store_instance, _, _ = _ensure_ready()
    return store_instance.create_backup(label=label)


@mcp.tool()
def restore_backup(backup_id: str) -> Dict[str, Any]:
    """Restore the memory store from a backup_id (overwrites current active state)."""
    store_instance, _, _ = _ensure_ready()
    return store_instance.restore_backup(backup_id=backup_id)


@mcp.tool()
def resolve_soft_duplicate(
    chunk_id_new: str,
    chunk_id_old: str,
    resolution: str,
) -> Dict[str, Any]:
    """
    Resolve a soft duplicate conflict detected during reconciliation.

    Args:
        chunk_id_new: The newly added chunk ID
        chunk_id_old: The existing chunk ID that's similar
        resolution: One of: "merge", "refine", "keep_both", "keep_new", "keep_old"

    Returns:
        Dict with resolution details
    """
    store_instance, _, _ = _ensure_ready()

    if resolution not in ["merge", "refine", "keep_both", "keep_new", "keep_old"]:
        return {"error": f"Invalid resolution: {resolution}"}

    # Get both chunks
    new_chunk = store_instance.get_chunk(chunk_id_new)
    old_chunk = store_instance.get_chunk(chunk_id_old)

    if not new_chunk or not old_chunk:
        return {"error": "One or both chunks not found"}

    result = {"resolution": resolution, "chunks_affected": [chunk_id_old, chunk_id_new]}

    if resolution == "merge":
        # Merge texts and update old chunk
        from .reconciliation import _merge_texts
        merged_text = _merge_texts(new_chunk.text, old_chunk.text)
        store_instance.update_chunk(chunk_id_old, merged_text, strategy="version")
        store_instance.update_metadata(chunk_id_new, {"deprecated": True})
        store_instance.log_reconciliation(
            "merged",
            [chunk_id_old, chunk_id_new],
            "Merged by AI decision via resolve_soft_duplicate",
            auto_resolved=True,
            confidence=0.9,
        )
        result["action"] = "merged"
        result["kept_chunk"] = chunk_id_old

    elif resolution == "refine":
        # New text replaces old (with versioning)
        store_instance.update_chunk(chunk_id_old, new_chunk.text, strategy="version")
        store_instance.update_metadata(chunk_id_new, {"deprecated": True})
        store_instance.log_reconciliation(
            "refined",
            [chunk_id_old, chunk_id_new],
            "Refined by AI decision via resolve_soft_duplicate",
            auto_resolved=True,
            confidence=0.9,
        )
        result["action"] = "refined"
        result["kept_chunk"] = chunk_id_old

    elif resolution == "keep_both":
        # Just mark the conflict as resolved, keep both active
        store_instance.log_reconciliation(
            "kept_both",
            [chunk_id_old, chunk_id_new],
            "AI decided to keep both chunks as distinct",
            auto_resolved=True,
            confidence=0.8,
        )
        result["action"] = "kept_both"
        result["kept_chunks"] = [chunk_id_old, chunk_id_new]

    elif resolution == "keep_new":
        # Deprecate old, keep new
        store_instance.update_metadata(chunk_id_old, {"deprecated": True})
        store_instance.log_reconciliation(
            "kept_new",
            [chunk_id_old, chunk_id_new],
            "AI decided to keep new chunk only",
            auto_resolved=True,
            confidence=0.85,
        )
        result["action"] = "kept_new"
        result["kept_chunk"] = chunk_id_new

    elif resolution == "keep_old":
        # Deprecate new, keep old
        store_instance.update_metadata(chunk_id_new, {"deprecated": True})
        store_instance.log_reconciliation(
            "kept_old",
            [chunk_id_old, chunk_id_new],
            "AI decided to keep old chunk only",
            auto_resolved=True,
            confidence=0.85,
        )
        result["action"] = "kept_old"
        result["kept_chunk"] = chunk_id_old

    return result


@mcp.tool()
def get_conflicts() -> Dict[str, Any]:
    """
    Get all unresolved conflicts with full chunk details.
    Returns conflicts flagged during reconciliation that require AI resolution.
    """
    store_instance, _, _ = _ensure_ready()
    return store_instance.get_conflicts()


@mcp.tool()
def resolve_conflicts(conflict_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Mark conflicts as resolved.
    If no IDs provided, resolves all unresolved conflicts.

    Args:
        conflict_ids: Optional list of conflict IDs to resolve. If None, resolves all.

    Returns:
        Dict with count of resolved conflicts
    """
    store_instance, _, _ = _ensure_ready()
    return store_instance.resolve_conflicts(conflict_ids=conflict_ids)


@mcp.tool()
def get_recent(
    n: int = 10,
    include_deprecated: bool = False,
) -> Dict[str, Any]:
    """
    Get the N most recently added or modified chunks, sorted by last_modified descending.

    Call this at session start to orient to recent state without needing to know
    what to search for. Each chunk includes recency_score (1.0 = today, 0.0 = 1 year ago).

    When multiple chunks conflict on the same topic, the chunk with the highest
    recency_score is the source of truth.
    """
    store_instance, _, _ = _ensure_ready()
    return store_instance.get_recent(n=n, include_deprecated=include_deprecated)


@mcp.tool()
def get_issues() -> Dict[str, Any]:
    """
    Get all memory system issues.

    Returns oversized chunks and unresolved conflicts.
    """
    _, _, health_monitor = _ensure_ready()
    return health_monitor.check_health()


if __name__ == "__main__":
    mcp.run()
