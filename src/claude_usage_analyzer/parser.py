"""Low-level JSONL record parsing and field extraction."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_jsonl_file(filepath: Path):
    """Yield parsed dicts from a JSONL file, skipping bad lines."""
    with open(filepath, "r") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSON at %s:%d", filepath.name, lineno)


def extract_tool_calls(content: list[dict]) -> list[dict]:
    """Pull tool_use blocks out of an assistant message's content array."""
    tools = []
    if not isinstance(content, list):
        return tools
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "unknown")
        inp = block.get("input", {})
        info = {"name": name, "id": block.get("id", "")}

        if name == "Agent":
            info["subagent_type"] = inp.get("subagent_type", "") or "general-purpose"
            info["description"] = inp.get("description", "")
            info["agent_model"] = inp.get("model", "")
        elif name == "Skill":
            info["skill"] = inp.get("skill", "")
        elif name == "Bash":
            cmd = inp.get("command", "")
            base_cmd = cmd.strip().split()[0].split("/")[-1] if cmd.strip() else ""
            info["base_command"] = base_cmd

        tools.append(info)
    return tools
