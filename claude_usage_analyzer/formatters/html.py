"""HTML report formatter — self-contained single-file report."""

from __future__ import annotations

import html as html_mod
import logging

from ..utils import cache_hit_rate, format_tokens

logger = logging.getLogger(__name__)

_CSS = """\
:root { --bg: #0d1117; --fg: #c9d1d9; --accent: #58a6ff;
        --border: #30363d; --row-alt: #161b22; --header-bg: #21262d; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
       font-size: 13px; background: var(--bg); color: var(--fg);
       padding: 2rem; max-width: 1400px; margin: auto; }
h1 { color: var(--accent); margin-bottom: .5rem; font-size: 1.3rem; }
h2 { color: var(--accent); margin: 2rem 0 .5rem; font-size: 1.05rem;
     border-bottom: 1px solid var(--border); padding-bottom: .3rem; }
.summary { margin-bottom: 1.5rem; line-height: 1.8; }
.summary span { display: inline-block; min-width: 160px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 1rem; }
th { background: var(--header-bg); text-align: left; padding: 6px 10px;
     border-bottom: 2px solid var(--border); white-space: nowrap; }
td { padding: 5px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
tr:nth-child(even) { background: var(--row-alt); }
tr:hover { background: #1c2128; }
.right { text-align: right; }
.muted { color: #8b949e; }
"""


def _esc(val) -> str:
    return html_mod.escape(str(val))


def _html_table(headers, rows, right_cols=None):
    """Return an HTML <table> string."""
    right_cols = right_cols or set()
    lines = ["<table>", "<thead><tr>"]
    for i, h in enumerate(headers):
        cls = ' class="right"' if i in right_cols else ""
        lines.append(f"  <th{cls}>{_esc(h)}</th>")
    lines.append("</tr></thead><tbody>")
    for row in rows:
        lines.append("<tr>")
        for i, cell in enumerate(row):
            cls = ' class="right"' if i in right_cols else ""
            lines.append(f"  <td{cls}>{_esc(cell)}</td>")
        lines.append("</tr>")
    lines.append("</tbody></table>")
    return "\n".join(lines)


def write_report(stats: dict, output: str | None, top_n: int = 20):
    """Write a self-contained HTML report."""
    path = output or "claude_usage.html"
    parts: list[str] = []
    p = parts.append

    p("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    p("<title>Claude Code Usage Report</title>")
    p(f"<style>{_CSS}</style></head><body>")

    p("<h1>Claude Code Token Cost Analysis</h1>")
    p('<div class="summary">')
    p(f"<span>Total sessions:</span> {stats['total_sessions']}<br>")
    p(f"<span>Total API calls:</span> {stats['total_requests']}<br>")
    p(f"<span>Total tool calls:</span> {stats['total_tool_calls']}")
    p("</div>")

    # --- By Model ---
    p("<h2>Token Usage by Model</h2>")
    rows = []
    for model, d in sorted(stats["by_model"].items(),
                            key=lambda x: x[1]["cache_read"] + x[1]["cache_create"] + x[1]["input_tokens"],
                            reverse=True):
        rows.append([
            model, format_tokens(d["input_tokens"]),
            format_tokens(d["cache_read"]), format_tokens(d["cache_create"]),
            cache_hit_rate(d["cache_read"], d["cache_create"], d["input_tokens"]),
            format_tokens(d["output_tokens"]), f"{d['requests']:,}",
        ])
    p(_html_table(["Model", "Uncached", "Cache Read", "Cache Create", "Hit Rate", "Output", "Reqs"],
                  rows, {1, 2, 3, 5, 6}))

    # --- By Project ---
    p("<h2>Token Usage by Project</h2>")
    rows = []
    for proj, d in sorted(stats["by_project"].items(),
                           key=lambda x: x[1]["output_tokens"], reverse=True)[:top_n]:
        rows.append([
            proj, format_tokens(d["input_tokens"]),
            format_tokens(d["cache_read"]),
            cache_hit_rate(d["cache_read"], d["cache_create"], d["input_tokens"]),
            format_tokens(d["output_tokens"]),
            f"{d['requests']:,}", d["sessions"], d["tool_calls"],
        ])
    p(_html_table(["Project", "Uncached", "Cache Read", "Hit Rate", "Output", "Reqs", "Sessions", "Tools"],
                  rows, {1, 2, 4, 5, 6, 7}))

    # --- Daily cache ---
    p("<h2>Daily Cache Hit Rate</h2>")
    date_model_data: dict[str, dict] = {}
    for dm_key, d in stats["cache_by_date"].items():
        date, model = dm_key.split("|", 1)
        if date != "unknown":
            date_model_data.setdefault(date, {})[model] = d
    daily = {}
    for date, models in sorted(date_model_data.items()):
        daily[date] = {
            "input_tokens": sum(m["input_tokens"] for m in models.values()),
            "cache_read": sum(m["cache_read"] for m in models.values()),
            "cache_create": sum(m["cache_create"] for m in models.values()),
            "requests": sum(m["requests"] for m in models.values()),
        }
    rows = []
    for date in sorted(daily)[-top_n:]:
        d = daily[date]
        total = d["input_tokens"] + d["cache_read"] + d["cache_create"]
        miss = f"{(d['input_tokens'] + d['cache_create']) / total * 100:.1f}%" if total else "-"
        rows.append([
            date, format_tokens(d["cache_read"]),
            format_tokens(d["cache_create"]), format_tokens(d["input_tokens"]),
            cache_hit_rate(d["cache_read"], d["cache_create"], d["input_tokens"]),
            miss, f"{d['requests']:,}",
        ])
    p(_html_table(["Date", "Cache Read", "Cache Create", "Uncached", "Hit Rate", "Miss Rate", "Reqs"],
                  rows, {1, 2, 3, 6}))

    # --- Tool result cost ---
    p("<h2>Tool Result Context Cost</h2>")
    rows = []
    for tool, d in sorted(stats["tool_result_cost"].items(),
                           key=lambda x: x[1]["total_chars"], reverse=True)[:top_n]:
        avg = d["total_chars"] // d["count"] if d["count"] else 0
        top_proj = max(d["by_project"].items(), key=lambda x: x[1])[0] if d["by_project"] else "-"
        rows.append([
            tool, f"{d['count']:,}",
            format_tokens(d["total_chars"] // 4),
            format_tokens(avg // 4),
            format_tokens(d["max_single"] // 4),
            top_proj,
        ])
    p(_html_table(["Tool", "Calls", "Total Est Tok", "Avg/Call", "Max Single", "Top Project"],
                  rows, {1, 2, 3, 4}))

    # --- Subagents ---
    if stats["by_subagent_type"]:
        p("<h2>Subagent Dispatches &amp; Result Cost</h2>")
        rows = []
        for sa, d in sorted(stats["by_subagent_type"].items(),
                             key=lambda x: x[1]["count"], reverse=True)[:top_n]:
            rk = f"result|{sa}"
            rd = stats["subagent_cost"].get(rk, {})
            rt = rd.get("input_tokens", 0)
            top_proj = max(d["by_project"].items(), key=lambda x: x[1])[0] if d["by_project"] else "-"
            sample = d["descriptions"][0][:50] if d["descriptions"] else "-"
            rows.append([
                sa, d["count"],
                format_tokens(rt) if rt else "-",
                format_tokens(rt // d["count"]) if rt and d["count"] else "-",
                top_proj, sample,
            ])
        p(_html_table(["Type", "Dispatches", "Result Tok", "Avg Result", "Top Project", "Sample"],
                      rows, {1, 2, 3}))

    # --- Heaviest sessions ---
    p("<h2>Heaviest Sessions</h2>")
    items = [(sid, s, s["input_tokens"] + s["output_tokens"])
             for sid, s in stats["sessions"].items()
             if s["input_tokens"] + s["output_tokens"] > 0]
    items.sort(key=lambda x: x[2], reverse=True)
    rows = []
    for sid, sess, _ in items[:top_n]:
        models = ", ".join(sorted(sess["models"]))
        rows.append([
            sid[:24] + "...", sess["project"],
            format_tokens(sess["input_tokens"]),
            format_tokens(sess["output_tokens"]),
            cache_hit_rate(sess["cache_read"], sess["cache_create"], sess["input_tokens"]),
            sess["tool_calls"], models[:30],
        ])
    p(_html_table(["Session", "Project", "Uncached", "Output", "Cache Hit", "Tools", "Models"],
                  rows, {2, 3, 5}))

    # --- Cost summary ---
    p("<h2>Cost Summary</h2>")
    tc = sum(d["total_chars"] for d in stats["tool_result_cost"].values())
    hc = sum(d["total_chars"] for d in stats["hook_injection_cost"].values())
    sc = sum(d["total_chars"] for d in stats["skill_cost"].values())
    p(f"<p>Context from tool results: ~{format_tokens(tc // 4)} tokens</p>")
    p(f"<p>Context from hooks/system: ~{format_tokens(hc // 4)} tokens</p>")
    p(f"<p>Context from skill loads: ~{format_tokens(sc // 4)} tokens (subset of tool results)</p>")

    all_costs = []
    for tool, d in stats["tool_result_cost"].items():
        all_costs.append((f"tool:{tool}", d["total_chars"] // 4, d["count"]))
    for cat, d in stats["hook_injection_cost"].items():
        all_costs.append((cat, d["total_chars"] // 4, d["count"]))
    all_costs.sort(key=lambda x: x[1], reverse=True)
    rows = [(n, format_tokens(t), f"{c:,}") for n, t, c in all_costs[:10]]
    p(_html_table(["Source", "Est Tokens", "Calls"], rows, {1, 2}))

    p("</body></html>")

    with open(path, "w") as f:
        f.write("\n".join(parts))
    logger.info("HTML report written to %s", path)
    print(f"HTML report written to {path}")
