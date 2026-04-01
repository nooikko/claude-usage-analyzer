"""CSV export formatter — writes multiple CSV files with a shared prefix."""

from __future__ import annotations

import csv
import logging

from ..utils import cache_hit_rate_float

logger = logging.getLogger(__name__)


def write_report(stats: dict, output: str | None, top_n: int = 20):
    """Write multiple CSV files: tool_cost, hook_cost, tokens, cache_daily."""
    base = (output or "claude_usage.csv").rsplit(".", 1)[0]

    # Tool cost
    with open(f"{base}_tool_cost.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tool", "calls", "total_result_chars", "est_tokens",
                     "avg_chars", "max_single_chars"])
        for tool, d in sorted(stats["tool_result_cost"].items(),
                               key=lambda x: x[1]["total_chars"], reverse=True):
            avg = d["total_chars"] // d["count"] if d["count"] else 0
            w.writerow([tool, d["count"], d["total_chars"],
                        d["total_chars"] // 4, avg, d["max_single"]])

    # Hook cost
    with open(f"{base}_hook_cost.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["category", "injections", "total_chars", "est_tokens", "avg_chars"])
        for cat, d in sorted(stats["hook_injection_cost"].items(),
                              key=lambda x: x[1]["total_chars"], reverse=True):
            avg = d["total_chars"] // d["count"] if d["count"] else 0
            w.writerow([cat, d["count"], d["total_chars"],
                        d["total_chars"] // 4, avg])

    # Token usage by project + model
    with open(f"{base}_tokens.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["project", "model", "input_tokens", "output_tokens",
                     "cache_read", "cache_create", "cache_hit_rate", "requests"])
        for pm_key, d in stats["by_project_model"].items():
            proj, model = pm_key.split("|", 1)
            hit = cache_hit_rate_float(d["cache_read"], d["cache_create"], d["input_tokens"])
            w.writerow([proj, model, d["input_tokens"], d["output_tokens"],
                        d["cache_read"], d["cache_create"], f"{hit:.4f}", d["requests"]])

    # Daily cache
    with open(f"{base}_cache_daily.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "model", "input_tokens", "cache_read",
                     "cache_create", "cache_hit_rate", "requests"])
        for dm_key, d in sorted(stats["cache_by_date"].items()):
            date, model = dm_key.split("|", 1)
            hit = cache_hit_rate_float(d["cache_read"], d["cache_create"], d["input_tokens"])
            w.writerow([date, model, d["input_tokens"], d["cache_read"],
                        d["cache_create"], f"{hit:.4f}", d["requests"]])

    files = [f"{base}_{s}.csv" for s in ("tool_cost", "hook_cost", "tokens", "cache_daily")]
    logger.info("CSV files written: %s", ", ".join(files))
    print(f"CSV files exported: {', '.join(files)}")
