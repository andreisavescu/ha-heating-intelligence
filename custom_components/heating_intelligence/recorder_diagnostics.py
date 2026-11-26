"""Small, safe diagnostics helpers for Recorder history failures."""

from __future__ import annotations

SUCCESS_CACHE_TTL_SECONDS = 6 * 60 * 60
FAILURE_RETRY_TTL_SECONDS = 5 * 60
RECORDER_HISTORY_ANALYSIS_VERSION = 4


def recorder_history_cache_ttl(summary: dict | None) -> int:
    """Retry failed Recorder/history passes quickly; cache successful analysis longer."""
    if (summary or {}).get("analysis_schema_version") != RECORDER_HISTORY_ANALYSIS_VERSION:
        return 0
    status = (summary or {}).get("status")
    if status in {"recorder_query_failed", "history_processing_failed"}:
        return FAILURE_RETRY_TTL_SECONDS
    return SUCCESS_CACHE_TTL_SECONDS


def build_recorder_failure_summary(
    *,
    failed_stage: str,
    error: Exception,
    entity_count: int,
    history_days: int,
    attempted_at: str,
) -> dict:
    """Return actionable failure metadata without retaining Recorder payloads."""
    status = (
        "recorder_query_failed"
        if failed_stage == "recorder_query"
        else "history_processing_failed"
    )
    return {
        "status": status,
        "source": "home_assistant_recorder",
        "failed_stage": failed_stage,
        "exception_type": type(error).__name__,
        "exception_message": str(error)[:500],
        "attempted_at": attempted_at,
        "requested_history_days": history_days,
        "entity_count": entity_count,
        "entities_with_history": 0,
        "total_samples": 0,
        "entities": {},
        "raw_history_retained": False,
        "retry_after_seconds": FAILURE_RETRY_TTL_SECONDS,
        "note": (
            "Recorder query failed; live operation is unaffected."
            if failed_stage == "recorder_query"
            else "Recorder returned history, but thermal history processing failed; live operation is unaffected."
        ),
    }
