"""Content-addressed cache that invalidates automatically on artifact or approval changes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Dict, Optional

from .model_client import ModelConfig
from .schemas import ReviewBundle, ReviewReport


def compute_bundle_cache_key(
    bundle: ReviewBundle,
    model_config: ModelConfig,
    reviewer_mode: str,
) -> str:
    payload = {
        "bundle": asdict(bundle),
        "model_config": asdict(model_config),
        "reviewer_mode": reviewer_mode,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ReviewCache:
    """In-memory content-addressed cache keyed by full bundle + approvals + config hash."""

    def __init__(self) -> None:
        self._store: Dict[str, ReviewReport] = {}

    def get(self, key: str) -> Optional[ReviewReport]:
        return self._store.get(key)

    def put(self, key: str, report: ReviewReport) -> None:
        self._store[key] = report

    def __len__(self) -> int:
        return len(self._store)
