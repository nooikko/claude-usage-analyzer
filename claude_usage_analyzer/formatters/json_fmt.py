"""JSON export formatter."""

from __future__ import annotations

import json
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


def _make_serializable(obj):
    if isinstance(obj, defaultdict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    return obj


def write_report(stats: dict, output: str | None, top_n: int = 20):
    """Write the full stats dict as pretty-printed JSON."""
    path = output or "claude_usage.json"
    with open(path, "w") as f:
        json.dump(_make_serializable(stats), f, indent=2)
    logger.info("JSON exported to %s", path)
    print(f"JSON exported to {path}")
