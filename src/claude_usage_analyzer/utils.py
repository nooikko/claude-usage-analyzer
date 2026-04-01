"""Shared formatting helpers and utility functions."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token / number formatting
# ---------------------------------------------------------------------------

def format_tokens(n: int | float) -> str:
    """Format a token count with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def cache_hit_rate(cache_read: int, cache_create: int, input_tokens: int) -> str:
    """Return cache-read as a percentage of total input context."""
    total = cache_read + cache_create + input_tokens
    if total == 0:
        return "-"
    return f"{cache_read / total * 100:.1f}%"


def cache_hit_rate_float(cache_read: int, cache_create: int, input_tokens: int) -> float:
    """Return cache-read ratio (0.0-1.0) for numeric use."""
    total = cache_read + cache_create + input_tokens
    if total == 0:
        return 0.0
    return cache_read / total


# ---------------------------------------------------------------------------
# Project name extraction
# ---------------------------------------------------------------------------

def get_project_name(dir_name: str) -> str:
    """Convert a Claude project directory name to a human-readable label.

    Example: ``-Users-jane-dev-myproject`` → ``myproject``
    """
    parts = dir_name.replace("-", "/").strip("/").split("/")
    skip = {"Users", "home", "dev"}
    meaningful = [p for p in parts if p not in skip and len(p) > 1]
    if meaningful:
        return "/".join(meaningful[-2:]) if len(meaningful) > 1 else meaningful[-1]
    return dir_name


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def parse_timestamp(ts_str: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def file_could_be_in_range(filepath, since_dt, until_dt) -> bool:
    """Quick pre-filter using file mtime so we can skip stale files."""
    if not since_dt and not until_dt:
        return True
    try:
        mtime = datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc)
        if since_dt and mtime < since_dt:
            return False
    except OSError:
        pass
    return True


def record_in_range(record: dict, since_dt, until_dt) -> bool:
    """Return True if the record's timestamp falls within [since, until]."""
    ts_str = record.get("timestamp")
    if not ts_str:
        return True
    ts = parse_timestamp(ts_str)
    if ts is None:
        return True
    if since_dt and ts < since_dt:
        return False
    if until_dt and ts > until_dt:
        return False
    return True


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------

def measure_content_size(content) -> int:
    """Return the character-length of tool result content."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(json.dumps(x)) for x in content)
    return len(json.dumps(content)) if content else 0


_REMINDER_RE = re.compile(r"<system-reminder>(.*?)</system-reminder>", re.DOTALL)


def extract_system_reminders(text: str) -> tuple[str, list[str]]:
    """Split *text* into (cleaned, [reminder_bodies])."""
    if not isinstance(text, str):
        return text, []
    reminders = _REMINDER_RE.findall(text)
    cleaned = _REMINDER_RE.sub("", text)
    return cleaned, reminders


def categorize_reminder(text: str) -> str:
    """Bucket a system-reminder body into a human-friendly category."""
    t = text[:500].lower()
    if "pretooluse" in t or "pre_tool_use" in t:
        return "hook:PreToolUse"
    if "posttooluse" in t or "post_tool_use" in t:
        return "hook:PostToolUse"
    if "sessionstart" in t or "session_start" in t:
        return "hook:SessionStart"
    if "todowrite" in t and "hasn't been used" in t:
        return "hook:TodoReminder"
    if "deferred tool" in t:
        return "system:DeferredTools"
    if "skill" in t and "available" in t:
        return "system:SkillList"
    if "context_guidance" in t or "context_window_protection" in t:
        return "hook:ContextGuidance"
    if "hook" in t:
        return "hook:Other"
    return "system:Other"
