"""Tests for Recorder-safe diagnostic payload serialization."""

import importlib.util
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "heating_intelligence"
    / "recorder_payloads.py"
)
SPEC = importlib.util.spec_from_file_location("recorder_payloads_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_payload_chunks_are_bounded_and_reassemble_exactly():
    payload = {
        "rooms": {f"room_{index}": {"name": "Bucătărie", "capacity": index} for index in range(500)},
        "forecast": [{"temperature": 20 + index / 10} for index in range(500)],
    }
    chunks, total_chars = MODULE.serialize_payload_chunks(payload, chunk_size=9000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 9000 for chunk in chunks)
    assert sum(map(len, chunks)) == total_chars

    import json
    restored = json.loads("".join(chunks))
    assert restored == payload


def test_empty_payload_still_has_one_reconstructable_chunk():
    chunks, total_chars = MODULE.serialize_payload_chunks({}, chunk_size=10)
    assert chunks == ["{}"]
    assert total_chars == 2


def test_non_positive_chunk_size_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        MODULE.serialize_payload_chunks({"x": 1}, chunk_size=0)
