"""Tests for Recorder history failure diagnostics and retry policy."""

import importlib.util
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "heating_intelligence"
    / "recorder_diagnostics.py"
)
SPEC = importlib.util.spec_from_file_location("recorder_diagnostics_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_query_failure_exposes_exception_and_stage():
    error = RuntimeError("Recorder database is busy")
    result = MODULE.build_recorder_failure_summary(
        failed_stage="recorder_query",
        error=error,
        entity_count=50,
        history_days=90,
        attempted_at="2026-11-25T00:00:00+00:00",
    )
    assert result["status"] == "recorder_query_failed"
    assert result["failed_stage"] == "recorder_query"
    assert result["exception_type"] == "RuntimeError"
    assert result["exception_message"] == "Recorder database is busy"
    assert result["entity_count"] == 50
    assert result["retry_after_seconds"] == 300


def test_processing_failure_is_not_misreported_as_recorder_query_failure():
    result = MODULE.build_recorder_failure_summary(
        failed_stage="history_processing",
        error=ValueError("bad history payload"),
        entity_count=12,
        history_days=30,
        attempted_at="2026-11-25T00:00:00+00:00",
    )
    assert result["status"] == "history_processing_failed"
    assert result["failed_stage"] == "history_processing"
    assert result["exception_type"] == "ValueError"


def test_failed_history_pass_retries_after_five_minutes():
    for status in ("recorder_query_failed", "history_processing_failed"):
        summary = {
            "status": status,
            "analysis_schema_version": MODULE.RECORDER_HISTORY_ANALYSIS_VERSION,
        }
        assert MODULE.recorder_history_cache_ttl(summary) == 300


def test_legacy_successful_history_pass_is_invalidated():
    assert MODULE.recorder_history_cache_ttl({"status": "ok"}) == 0


def test_current_successful_history_pass_keeps_six_hour_cache():
    summary = {
        "status": "ok",
        "analysis_schema_version": MODULE.RECORDER_HISTORY_ANALYSIS_VERSION,
    }
    assert MODULE.recorder_history_cache_ttl(summary) == 21600


def test_current_failed_history_pass_retries_after_five_minutes():
    summary = {
        "status": "history_processing_failed",
        "analysis_schema_version": MODULE.RECORDER_HISTORY_ANALYSIS_VERSION,
    }
    assert MODULE.recorder_history_cache_ttl(summary) == 300
