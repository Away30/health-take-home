"""Canonical JSON and SHA-256 helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """Return deterministic JSON for hashing and storage."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(payload: Any) -> str:
    """Return the SHA-256 hex digest for a canonical JSON payload."""

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
