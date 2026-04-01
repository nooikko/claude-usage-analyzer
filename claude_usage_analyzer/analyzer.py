"""Core analysis engine — walks project directories and aggregates stats."""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

from .parser import parse_jsonl_file
from .utils import (
    categorize_reminder,
    extract_system_reminders,
    file_could_be_in_range,
    get_project_name,
    measure_content_size,
    record_in_range,
)

logger = logging.getLogger(__name__)


def _new_stats() -> dict:
    """Return a fresh, empty stats dict with all expected keys."""
    return {
        "by_model": defaultdict(lambda: {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read": 0, "cache_create": 0, "requests": 0,
        }),
        "by_project": defaultdict(lambda: {
            "input_tokens": 0, "output_tokens": 0, "requests": 0,
            "cache_read": 0, "cache_create": 0,
            "sessions": 0, "tool_calls": 0,
        }),
        "by_project_model": defaultdict(lambda: {
            "input_tokens": 0, "output_tokens": 0, "requests": 0,
            "cache_read": 0, "cache_create": 0,
        }),
        "cache_by_date": defaultdict(lambda: {
            "input_tokens": 0, "cache_read": 0, "cache_create": 0, "requests": 0,
        }),
        # Keyed by "YYYY-MM-DD|project"
        "daily_by_project": defaultdict(lambda: {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read": 0, "cache_create": 0,
            "requests": 0, "tool_calls": 0, "sessions": set(),
        }),
        "tool_result_cost": defaultdict(lambda: {
            "total_chars": 0, "count": 0, "by_project": defaultdict(int),
            "max_single": 0,
        }),
        "hook_injection_cost": defaultdict(lambda: {
            "total_chars": 0, "count": 0, "by_project": defaultdict(int),
        }),
        "subagent_cost": defaultdict(lambda: {
            "input_tokens": 0, "output_tokens": 0, "cache_read": 0,
            "cache_create": 0, "requests": 0, "agent_count": 0,
            "by_project": defaultdict(lambda: {
                "input": 0, "output": 0, "cache_read": 0, "cache_create": 0,
            }),
            "descriptions": [],
        }),
        "skill_cost": defaultdict(lambda: {
            "total_chars": 0, "count": 0, "by_project": defaultdict(int),
        }),
        "by_tool": defaultdict(lambda: {
            "count": 0, "by_model": defaultdict(int), "by_project": defaultdict(int),
        }),
        "by_skill": defaultdict(lambda: {
            "count": 0, "by_project": defaultdict(int),
        }),
        "by_bash_command": defaultdict(lambda: {
            "count": 0, "by_project": defaultdict(int),
        }),
        "by_subagent_type": defaultdict(lambda: {
            "count": 0, "descriptions": [], "by_project": defaultdict(int),
        }),
        "sessions": defaultdict(lambda: {
            "project": "", "start": None, "end": None,
            "input_tokens": 0, "output_tokens": 0,
            "cache_read": 0, "cache_create": 0,
            "tool_calls": 0, "models": set(), "is_sidechain": False,
        }),
        "total_sessions": 0,
        "total_requests": 0,
        "total_tool_calls": 0,
    }


def analyze_projects(
    projects_dir: str,
    project_filter: str | None = None,
    since_dt=None,
    until_dt=None,
) -> dict:
    """Scan *projects_dir* and return an aggregated stats dict."""
    stats = _new_stats()

    projects_path = Path(projects_dir)
    if not projects_path.exists():
        logger.error("Projects directory does not exist: %s", projects_dir)
        sys.exit(1)

    project_dirs = sorted(projects_path.iterdir())
    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue

        project_name = get_project_name(project_dir.name)
        if project_filter and project_filter.lower() not in project_name.lower():
            continue

        jsonl_files = sorted(project_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue

        logger.debug("Processing project %s (%d files)", project_name, len(jsonl_files))
        project_sessions: set[str] = set()

        # --- Pass 1: agent-*.jsonl files (subagent token usage) ------------
        _process_agent_files(
            sorted(project_dir.glob("agent-*.jsonl")),
            project_name, stats, since_dt, until_dt,
        )

        # --- Pass 2: regular session files ---------------------------------
        regular_files = [f for f in jsonl_files if not f.name.startswith("agent-")]
        for jsonl_file in regular_files:
            if not file_could_be_in_range(jsonl_file, since_dt, until_dt):
                continue
            session_id = jsonl_file.stem
            project_sessions.add(session_id)

            records = [
                r for r in parse_jsonl_file(jsonl_file)
                if record_in_range(r, since_dt, until_dt)
            ]
            _process_session_records(records, jsonl_file.stem, project_name, stats)

        stats["by_project"][project_name]["sessions"] = len(project_sessions)

    stats["total_sessions"] = len(stats["sessions"])
    return stats


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _process_agent_files(agent_files, project_name, stats, since_dt, until_dt):
    for agent_file in agent_files:
        if not file_could_be_in_range(agent_file, since_dt, until_dt):
            continue
        subagent_type = "unknown"
        sa_key = None
        for record in parse_jsonl_file(agent_file):
            if not record_in_range(record, since_dt, until_dt):
                continue
            if record.get("type") == "assistant":
                msg = record.get("message", {})
                usage = msg.get("usage", {})
                model = msg.get("model", "unknown")
                sa_key = f"{model}|{subagent_type}"

                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                cr = usage.get("cache_read_input_tokens", 0)
                cc = usage.get("cache_creation_input_tokens", 0)

                bucket = stats["subagent_cost"][sa_key]
                bucket["input_tokens"] += inp
                bucket["output_tokens"] += out
                bucket["cache_read"] += cr
                bucket["cache_create"] += cc
                bucket["requests"] += 1
                proj = bucket["by_project"][project_name]
                proj["input"] += inp
                proj["output"] += out
                proj["cache_read"] += cr
                proj["cache_create"] += cc

        if sa_key is not None:
            stats["subagent_cost"][sa_key]["agent_count"] += 1


def _process_session_records(records, file_stem, project_name, stats):
    tool_use_map: dict[str, dict] = {}

    for record in records:
        rec_type = record.get("type")
        session_id = record.get("sessionId", file_stem)
        is_sidechain = record.get("isSidechain", False)
        timestamp = record.get("timestamp")

        sess = stats["sessions"][session_id]
        sess["project"] = project_name
        if is_sidechain:
            sess["is_sidechain"] = True
        if timestamp:
            if sess["start"] is None or timestamp < sess["start"]:
                sess["start"] = timestamp
            if sess["end"] is None or timestamp > sess["end"]:
                sess["end"] = timestamp

        if rec_type == "assistant":
            _handle_assistant(record, project_name, session_id, stats, tool_use_map)
        elif rec_type == "user":
            _handle_user(record, project_name, stats, tool_use_map)


def _handle_assistant(record, project_name, session_id, stats, tool_use_map):
    msg = record.get("message", {})
    model = msg.get("model", "unknown")
    usage = msg.get("usage", {})
    content = msg.get("content", [])

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_create = usage.get("cache_creation_input_tokens", 0)

    # Aggregate into all the relevant buckets
    for bucket in (
        stats["by_model"][model],
        stats["by_project"][project_name],
        stats["by_project_model"][f"{project_name}|{model}"],
    ):
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["cache_read"] += cache_read
        bucket["cache_create"] += cache_create
        bucket["requests"] += 1

    # Cache-by-date
    ts_str = record.get("timestamp", "")
    date_key = ts_str[:10] if ts_str else "unknown"
    dm = stats["cache_by_date"][f"{date_key}|{model}"]
    dm["input_tokens"] += input_tokens
    dm["cache_read"] += cache_read
    dm["cache_create"] += cache_create
    dm["requests"] += 1

    # Daily-by-project
    dp = stats["daily_by_project"][f"{date_key}|{project_name}"]
    dp["input_tokens"] += input_tokens
    dp["output_tokens"] += output_tokens
    dp["cache_read"] += cache_read
    dp["cache_create"] += cache_create
    dp["requests"] += 1
    dp["sessions"].add(session_id)

    sess = stats["sessions"][session_id]
    sess["input_tokens"] += input_tokens
    sess["output_tokens"] += output_tokens
    sess["cache_read"] += cache_read
    sess["cache_create"] += cache_create
    sess["models"].add(model)
    stats["total_requests"] += 1

    # Tool calls
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_name = block.get("name", "unknown")
        tool_id = block.get("id", "")
        inp = block.get("input", {})

        tool_info = {"name": tool_name, "model": model}

        if tool_name == "Agent":
            sa_type = inp.get("subagent_type", "") or "general-purpose"
            tool_info["subagent_type"] = sa_type
            tool_info["description"] = inp.get("description", "")
            stats["by_subagent_type"][sa_type]["count"] += 1
            stats["by_subagent_type"][sa_type]["by_project"][project_name] += 1
            desc = inp.get("description", "")
            if desc:
                stats["by_subagent_type"][sa_type]["descriptions"].append(desc)
        elif tool_name == "Skill":
            tool_info["skill"] = inp.get("skill", "")
            skill = inp.get("skill", "unknown")
            stats["by_skill"][skill]["count"] += 1
            stats["by_skill"][skill]["by_project"][project_name] += 1
        elif tool_name == "Bash":
            cmd = inp.get("command", "")
            base_cmd = cmd.strip().split()[0].split("/")[-1] if cmd.strip() else ""
            tool_info["base_command"] = base_cmd
            if base_cmd:
                stats["by_bash_command"][base_cmd]["count"] += 1
                stats["by_bash_command"][base_cmd]["by_project"][project_name] += 1

        tool_use_map[tool_id] = tool_info
        stats["by_tool"][tool_name]["count"] += 1
        stats["by_tool"][tool_name]["by_model"][model] += 1
        stats["by_tool"][tool_name]["by_project"][project_name] += 1
        stats["total_tool_calls"] += 1
        sess["tool_calls"] += 1
        stats["by_project"][project_name]["tool_calls"] += 1
        stats["daily_by_project"][f"{date_key}|{project_name}"]["tool_calls"] += 1


def _handle_user(record, project_name, stats, tool_use_map):
    content = record.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, dict):
            continue

        if block.get("type") == "tool_result":
            _attribute_tool_result(block, project_name, stats, tool_use_map)
        elif block.get("type") == "text":
            text = block.get("text", "")
            _, reminders = extract_system_reminders(text)
            for rem in reminders:
                cat = categorize_reminder(rem)
                stats["hook_injection_cost"][cat]["total_chars"] += len(rem)
                stats["hook_injection_cost"][cat]["count"] += 1
                stats["hook_injection_cost"][cat]["by_project"][project_name] += len(rem)


def _attribute_tool_result(block, project_name, stats, tool_use_map):
    tool_id = block.get("tool_use_id", "")
    raw_content = block.get("content", "")

    total_size = measure_content_size(raw_content)
    text_content = raw_content if isinstance(raw_content, str) else json.dumps(raw_content)
    _, reminders = extract_system_reminders(text_content)
    reminder_size = sum(len(r) for r in reminders)
    tool_content_size = total_size - reminder_size

    tool_info = tool_use_map.get(tool_id, {})
    tool_name = tool_info.get("name", "unknown")

    # Tool result cost
    trc = stats["tool_result_cost"][tool_name]
    trc["total_chars"] += tool_content_size
    trc["count"] += 1
    trc["by_project"][project_name] += tool_content_size
    if tool_content_size > trc["max_single"]:
        trc["max_single"] = tool_content_size

    # Hook / system-reminder cost
    for rem in reminders:
        cat = categorize_reminder(rem)
        stats["hook_injection_cost"][cat]["total_chars"] += len(rem)
        stats["hook_injection_cost"][cat]["count"] += 1
        stats["hook_injection_cost"][cat]["by_project"][project_name] += len(rem)

    # Skill content
    if tool_name == "Skill":
        skill_name = tool_info.get("skill", "unknown")
        stats["skill_cost"][skill_name]["total_chars"] += tool_content_size
        stats["skill_cost"][skill_name]["count"] += 1
        stats["skill_cost"][skill_name]["by_project"][project_name] += tool_content_size

    # Agent result cost
    if tool_name == "Agent":
        sa_type = tool_info.get("subagent_type", "general-purpose")
        sa_key = f"result|{sa_type}"
        stats["subagent_cost"][sa_key]["input_tokens"] += tool_content_size // 4
        stats["subagent_cost"][sa_key]["requests"] += 1
        if tool_info.get("description"):
            stats["subagent_cost"][sa_key]["descriptions"].append(
                tool_info["description"][:60]
            )
