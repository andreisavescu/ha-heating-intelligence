"""Recorder-safe serialization helpers for detailed diagnostic payloads."""

import json


def serialize_payload_chunks(payload, chunk_size: int = 9000) -> tuple[list[str], int]:
    """Serialize a payload as deterministic ASCII JSON and split it into safe chunks."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    chunks = [
        serialized[index:index + chunk_size]
        for index in range(0, len(serialized), chunk_size)
    ] or ["{}"]
    return chunks, len(serialized)
