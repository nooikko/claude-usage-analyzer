"""ASCII table formatter — prints to stdout."""

from __future__ import annotations

import sys

from ..utils import (
    cache_hit_rate, estimate_cost, estimate_cost_no_cache,
    format_cost, format_tokens, get_pricing,
)


def _table(headers, rows, title="", file=sys.stdout):
    if title:
        print(f"\n{'=' * 70}", file=file)
        print(f"  {title}", file=file)
        print(f"{'=' * 70}", file=file)
    if not rows:
        print("  (no data)", file=file)
        return

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    hdr = "  ".join(str(h).ljust(col_widths[i]) for i, h in enumerate(headers))
    print(f"\n  {hdr}", file=file)
    print(f"  {'  '.join('-' * w for w in col_widths)}", file=file)
    for row in rows:
        line = "  ".join(str(c).ljust(col_widths[i]) for i, c in enumerate(row))
        print(f"  {line}", file=file)


# ---- Section builders -----------------------------------------------------

def _section_models(stats, top_n, f):
    rows = []
    for model, d in sorted(stats["by_model"].items(),
                            key=lambda x: x[1]["cache_read"] + x[1]["cache_create"] + x[1]["input_tokens"],
                            reverse=True):
        cost = estimate_cost(model, d["input_tokens"], d["output_tokens"], d["cache_read"], d["cache_create"])
        no_cache_cost = estimate_cost_no_cache(model, d["input_tokens"], d["output_tokens"], d["cache_read"], d["cache_create"])
        savings = (no_cache_cost - cost) if (cost is not None and no_cache_cost is not None) else None
        rows.append([
            model,
            format_tokens(d["input_tokens"]),
            format_tokens(d["cache_read"]),
            format_tokens(d["cache_create"]),
            cache_hit_rate(d["cache_read"], d["cache_create"], d["input_tokens"]),
            format_tokens(d["output_tokens"]),
            format_cost(cost),
            format_cost(savings),
            d["requests"],
        ])
    _table(["Model", "Uncached", "Cache Read", "Cache Create", "Hit Rate", "Output", "Est Cost", "Cache Savings", "Reqs"],
           rows, "TOKEN USAGE BY MODEL", f)


def _project_costs(stats) -> dict[str, tuple[float, float]]:
    """Return {project: (actual_cost, no_cache_cost)} summed across models."""
    costs: dict[str, list] = {}
    for pm_key, d in stats["by_project_model"].items():
        proj, model = pm_key.split("|", 1)
        cost = estimate_cost(model, d["input_tokens"], d["output_tokens"], d["cache_read"], d["cache_create"])
        no_cache = estimate_cost_no_cache(model, d["input_tokens"], d["output_tokens"], d["cache_read"], d["cache_create"])
        if proj not in costs:
            costs[proj] = [0.0, 0.0]
        if cost is not None:
            costs[proj][0] += cost
        if no_cache is not None:
            costs[proj][1] += no_cache
    return {p: (v[0], v[1]) for p, v in costs.items()}


def _section_projects(stats, top_n, f):
    proj_costs = _project_costs(stats)
    rows = []
    for proj, d in sorted(stats["by_project"].items(),
                           key=lambda x: x[1]["output_tokens"], reverse=True)[:top_n]:
        cost, no_cache = proj_costs.get(proj, (None, None))
        savings = (no_cache - cost) if (cost is not None and no_cache is not None) else None
        rows.append([
            proj[:35],
            format_tokens(d["input_tokens"]),
            format_tokens(d["cache_read"]),
            cache_hit_rate(d["cache_read"], d["cache_create"], d["input_tokens"]),
            format_tokens(d["output_tokens"]),
            format_cost(cost),
            format_cost(savings),
            d["requests"], d["sessions"], d["tool_calls"],
        ])
    _table(["Project", "Uncached", "Cache Read", "Hit Rate", "Output", "Est Cost", "Cache Savings", "Reqs", "Sessions", "Tools"],
           rows, f"TOKEN USAGE BY PROJECT (top {top_n})", f)


def _section_cache_daily(stats, top_n, f):
    date_model_data: dict[str, dict] = {}
    for dm_key, d in stats["cache_by_date"].items():
        date, model = dm_key.split("|", 1)
        if date == "unknown":
            continue
        date_model_data.setdefault(date, {})[model] = d

    daily = {}
    for date, models in sorted(date_model_data.items()):
        daily[date] = {
            "input_tokens": sum(m["input_tokens"] for m in models.values()),
            "cache_read": sum(m["cache_read"] for m in models.values()),
            "cache_create": sum(m["cache_create"] for m in models.values()),
            "requests": sum(m["requests"] for m in models.values()),
        }

    # Pre-compute cost per date by summing across models
    date_cost: dict[str, float] = {}
    for date, models in date_model_data.items():
        c = sum(
            estimate_cost(m, d["input_tokens"], d.get("output_tokens", 0), d["cache_read"], d["cache_create"]) or 0.0
            for m, d in models.items()
        )
        date_cost[date] = c

    rows = []
    for date in sorted(daily)[-top_n:]:
        d = daily[date]
        total = d["input_tokens"] + d["cache_read"] + d["cache_create"]
        miss = f"{(d['input_tokens'] + d['cache_create']) / total * 100:.1f}%" if total else "-"
        rows.append([
            date,
            format_tokens(d["cache_read"]),
            format_tokens(d["cache_create"]),
            format_tokens(d["input_tokens"]),
            cache_hit_rate(d["cache_read"], d["cache_create"], d["input_tokens"]),
            miss, d["requests"],
            format_cost(date_cost.get(date)),
        ])
    _table(["Date", "Cache Read", "Cache Create", "Uncached", "Hit Rate", "Miss Rate", "Reqs", "Est Cost"],
           rows, f"DAILY CACHE HIT RATE (last {top_n} days)\n  Hit = cache_read / total_input_context", f)

    # Per-model daily
    sig = [m for m, d in stats["by_model"].items() if d["requests"] > 100 and m != "<synthetic>"]
    for model in sig[:4]:
        rows = []
        for date in sorted(date_model_data)[-top_n:]:
            if model not in date_model_data[date]:
                continue
            d = date_model_data[date][model]
            c = estimate_cost(model, d["input_tokens"], d.get("output_tokens", 0), d["cache_read"], d["cache_create"])
            rows.append([
                date,
                format_tokens(d["cache_read"]),
                format_tokens(d["cache_create"]),
                format_tokens(d["input_tokens"]),
                cache_hit_rate(d["cache_read"], d["cache_create"], d["input_tokens"]),
                d["requests"],
                format_cost(c),
            ])
        if rows:
            short = (model.replace("claude-", "")
                     .replace("-20251001", "").replace("-20251101", "")
                     .replace("-20250929", "").replace("-20250805", "")
                     .replace("-20250514", ""))
            _table(["Date", "Cache Read", "Cache Create", "Uncached", "Hit Rate", "Reqs", "Est Cost"],
                   rows, f"DAILY CACHE: {short}", f)


def _section_cache_projects(stats, top_n, f):
    rows = []
    for proj, d in sorted(stats["by_project"].items(),
                           key=lambda x: x[1]["cache_read"] + x[1]["cache_create"] + x[1]["input_tokens"],
                           reverse=True)[:top_n]:
        total = d["input_tokens"] + d["cache_read"] + d["cache_create"]
        if total == 0:
            continue
        rows.append([
            proj[:35],
            format_tokens(d["cache_read"]),
            format_tokens(d["cache_create"]),
            format_tokens(d["input_tokens"]),
            cache_hit_rate(d["cache_read"], d["cache_create"], d["input_tokens"]),
            f"{d['input_tokens'] / total * 100:.1f}%",
            d["requests"],
        ])
    _table(["Project", "Cache Read", "Cache Create", "Uncached", "Hit Rate", "Uncached %", "Reqs"],
           rows, f"CACHE HIT RATE BY PROJECT (top {top_n})", f)


def _session_cost(sess) -> float | None:
    """Estimate cost for a session using its primary model."""
    models = sess.get("models") or set()
    model = sorted(models)[0] if models else None
    if not model or model == "<synthetic>":
        return None
    return estimate_cost(model, sess["input_tokens"], sess["output_tokens"],
                         sess["cache_read"], sess["cache_create"])


def _section_worst_cache_sessions(stats, top_n, f):
    items = []
    for sid, sess in stats["sessions"].items():
        total = sess["input_tokens"] + sess["cache_read"] + sess["cache_create"]
        if total >= 100_000:
            items.append((sid, sess, sess["cache_read"] / total, total))
    items.sort(key=lambda x: x[2])

    rows = []
    for sid, sess, _, _ in items[:top_n]:
        rows.append([
            sid[:20] + "...",
            sess["project"][:20],
            format_tokens(sess["cache_read"]),
            format_tokens(sess["cache_create"]),
            format_tokens(sess["input_tokens"]),
            cache_hit_rate(sess["cache_read"], sess["cache_create"], sess["input_tokens"]),
            format_cost(_session_cost(sess)),
            sess.get("start", "")[:10] if sess.get("start") else "-",
        ])
    if rows:
        _table(["Session", "Project", "Cache Read", "Cache Create", "Uncached", "Hit Rate", "Est Cost", "Date"],
               rows, "SESSIONS WITH WORST CACHE HIT RATE (min 100K context)", f)


def _blended_input_rate(stats) -> float:
    """Compute blended cost per input token across all models ($/token)."""
    total_cost = 0.0
    total_tokens = 0
    for model, d in stats["by_model"].items():
        c = estimate_cost(model, d["input_tokens"], d["output_tokens"], d["cache_read"], d["cache_create"])
        if c is not None:
            total_cost += c
            total_tokens += d["input_tokens"] + d["cache_read"] + d["cache_create"]
    return (total_cost / total_tokens) if total_tokens else 0.0


def _section_tool_cost(stats, top_n, f):
    rate = _blended_input_rate(stats)  # $/token

    rows = []
    for tool, d in sorted(stats["tool_result_cost"].items(),
                           key=lambda x: x[1]["total_chars"], reverse=True)[:top_n]:
        avg = d["total_chars"] // d["count"] if d["count"] else 0
        top_proj = max(d["by_project"].items(), key=lambda x: x[1])[0] if d["by_project"] else "-"
        tool_tokens = d["total_chars"] // 4
        tool_cost = tool_tokens * rate if rate else None
        rows.append([
            tool, d["count"],
            format_tokens(tool_tokens),
            format_tokens(avg // 4),
            format_tokens(d["max_single"] // 4),
            format_cost(tool_cost) if tool_cost else "-",
            top_proj[:25],
        ])
    _table(["Tool", "Calls", "Total Est Tok", "Avg/Call", "Max Single", "Est Cost", "Top Project"],
           rows, f"TOOL RESULT CONTEXT COST (top {top_n})\n  How much context each tool's results inject", f)

    raw = []
    for tool, d in stats["tool_result_cost"].items():
        for proj, chars in d["by_project"].items():
            raw.append((tool, proj[:30], chars))
    raw.sort(key=lambda x: x[2], reverse=True)
    rows = [(t, p, format_tokens(c // 4)) for t, p, c in raw[:top_n]]
    _table(["Tool", "Project", "Est Tokens Injected"],
           rows, f"TOOL RESULT COST BY PROJECT (top {top_n})", f)


def _section_tool_lifetime_cost(stats, top_n, f):
    """Show per-tool cost attribution based on how long results persist in context."""
    tool_lifetime = stats.get("tool_lifetime_cost", {})
    tool_result = stats.get("tool_result_cost", {})
    if not tool_lifetime:
        return

    # Total actual cost across all models
    total_actual_cost = 0.0
    for model, d in stats["by_model"].items():
        c = estimate_cost(model, d["input_tokens"], d["output_tokens"], d["cache_read"], d["cache_create"])
        if c is not None:
            total_actual_cost += c

    rows = []
    for tool, tl in sorted(tool_lifetime.items(),
                            key=lambda x: x[1]["attributed_cost"], reverse=True)[:top_n]:
        attributed = tl["attributed_cost"]
        accumulated_tok = tl["accumulated_tok"]
        injections = tl.get("injection_count", 0)
        injected_chars = tl.get("injected_chars", 0)

        avg_tok = (injected_chars // 4 // injections) if injections else 0
        total_tok = injected_chars // 4
        avg_turns = (accumulated_tok / total_tok) if total_tok else 0.0
        pct = (attributed / total_actual_cost * 100) if total_actual_cost else 0.0

        rows.append([
            tool,
            f"{injections:,}",
            format_tokens(avg_tok),
            f"{avg_turns:.1f}",
            format_tokens(accumulated_tok),
            format_cost(attributed),
            f"{pct:.1f}%",
        ])

    _table(
        ["Tool", "Injections", "Avg Size", "Avg Turns in Context", "Accumulated Tok",
         "Attributed Cost", "% of Bill"],
        rows,
        f"TOOL CONTEXT LIFETIME COST ATTRIBUTION (top {top_n})\n"
        "  How much of the actual API bill is attributable to each tool's results sitting in context",
        f,
    )


def _section_hooks(stats, top_n, f):
    if not stats["hook_injection_cost"]:
        return
    rows = []
    total_chars = 0
    for cat, d in sorted(stats["hook_injection_cost"].items(),
                          key=lambda x: x[1]["total_chars"], reverse=True)[:top_n]:
        avg = d["total_chars"] // d["count"] if d["count"] else 0
        total_chars += d["total_chars"]
        top_proj = max(d["by_project"].items(), key=lambda x: x[1])[0] if d["by_project"] else "-"
        rows.append([cat, d["count"], format_tokens(d["total_chars"] // 4),
                     format_tokens(avg // 4), top_proj[:25]])
    _table(["Hook/System Category", "Injections", "Total Est Tok", "Avg/Injection", "Top Project"],
           rows, f"HOOK & SYSTEM-REMINDER CONTEXT COST (top {top_n})\n  Extra context injected via <system-reminder> tags", f)
    print(f"\n  TOTAL hook/system injection: ~{format_tokens(total_chars // 4)} tokens", file=f)
    print("  Note: hooks that fire every turn compound across the conversation.", file=f)


def _section_skills(stats, top_n, f):
    if not stats["skill_cost"]:
        return
    rows = []
    for skill, d in sorted(stats["skill_cost"].items(),
                            key=lambda x: x[1]["total_chars"], reverse=True)[:top_n]:
        avg = d["total_chars"] // d["count"] if d["count"] else 0
        top_proj = max(d["by_project"].items(), key=lambda x: x[1])[0] if d["by_project"] else "-"
        rows.append([skill[:40], d["count"], format_tokens(d["total_chars"] // 4),
                     format_tokens(avg // 4), top_proj[:25]])
    _table(["Skill", "Loads", "Total Est Tok", "Avg/Load", "Top Project"],
           rows, f"SKILL CONTENT COST (top {top_n})\n  How much context each skill loads when invoked", f)


def _section_subagents(stats, top_n, f):
    if stats["by_subagent_type"]:
        rows = []
        for sa, d in sorted(stats["by_subagent_type"].items(),
                             key=lambda x: x[1]["count"], reverse=True)[:top_n]:
            rk = f"result|{sa}"
            rd = stats["subagent_cost"].get(rk, {})
            rt = rd.get("input_tokens", 0)
            top_proj = max(d["by_project"].items(), key=lambda x: x[1])[0] if d["by_project"] else "-"
            sample = d["descriptions"][0][:35] if d["descriptions"] else "-"
            rows.append([
                sa[:30], d["count"],
                format_tokens(rt) if rt else "-",
                format_tokens(rt // d["count"]) if rt and d["count"] else "-",
                top_proj[:20], sample,
            ])
        _table(["Subagent Type", "Dispatches", "Result Tok", "Avg Result", "Top Project", "Sample"],
               rows, f"SUBAGENT DISPATCHES & RESULT COST (top {top_n})", f)

    entries = {k: v for k, v in stats["subagent_cost"].items() if not k.startswith("result|")}
    if entries:
        rows = []
        for sa_key, d in sorted(entries.items(),
                                 key=lambda x: x[1]["cache_read"] + x[1]["output_tokens"],
                                 reverse=True)[:top_n]:
            parts = sa_key.split("|", 1)
            model, sa_type = (parts[0], parts[1]) if len(parts) > 1 else (sa_key, "unknown")
            total_ctx = d["input_tokens"] + d["cache_read"] + d["cache_create"]
            rows.append([model[:25], sa_type[:20], d.get("agent_count", 0),
                         d["requests"], format_tokens(total_ctx), format_tokens(d["output_tokens"])])
        _table(["Model", "Type", "Agents", "API Calls", "Total Context", "Output"],
               rows, "SUBAGENT FILE TOKEN USAGE (from agent-*.jsonl)", f)


def _section_bash(stats, top_n, f):
    if not stats["by_bash_command"]:
        return
    bash_result = stats["tool_result_cost"].get("Bash", {})
    rows = []
    for cmd, d in sorted(stats["by_bash_command"].items(),
                          key=lambda x: x[1]["count"], reverse=True)[:top_n]:
        top_proj = max(d["by_project"].items(), key=lambda x: x[1])[0] if d["by_project"] else "-"
        rows.append([cmd[:30], d["count"], top_proj[:25]])
    _table(["Command", "Count", "Top Project"], rows, f"BASH COMMANDS (top {top_n})", f)
    if bash_result:
        avg = bash_result["total_chars"] // bash_result["count"] if bash_result["count"] else 0
        print(f"\n  Bash overall: {bash_result['count']} calls, "
              f"~{format_tokens(bash_result['total_chars'] // 4)} total result tokens, "
              f"~{format_tokens(avg // 4)} avg/call, "
              f"~{format_tokens(bash_result['max_single'] // 4)} max single result", file=f)


def _section_heavy_sessions(stats, top_n, f):
    items = []
    for sid, sess in stats["sessions"].items():
        total = sess["input_tokens"] + sess["output_tokens"]
        if total > 0:
            items.append((sid, sess, total))
    items.sort(key=lambda x: x[2], reverse=True)

    rows = []
    for sid, sess, _ in items[:top_n]:
        models = ", ".join(sorted(sess["models"]))
        total_cost = sess.get("total_cost", 0.0)
        cost = total_cost if total_cost > 0 else _session_cost(sess)
        rows.append([
            sid[:20] + "...", sess["project"][:20],
            format_tokens(sess["input_tokens"]),
            format_tokens(sess["output_tokens"]),
            cache_hit_rate(sess["cache_read"], sess["cache_create"], sess["input_tokens"]),
            format_cost(cost),
            sess["tool_calls"], models[:25],
        ])
    _table(["Session", "Project", "Uncached", "Output", "Cache Hit", "Est Cost", "Tools", "Models"],
           rows, f"HEAVIEST SESSIONS (top {top_n})", f)


def _section_summary(stats, f):
    print(f"\n{'=' * 70}", file=f)
    print("  COST SUMMARY", file=f)
    print(f"{'=' * 70}", file=f)

    # Dollar cost totals across all models, with output broken into thinking vs text-to-user
    total_cost = 0.0
    total_no_cache = 0.0
    total_thinking_cost = 0.0
    total_text_cost = 0.0
    has_cost = False

    for model, d in stats["by_model"].items():
        rates = get_pricing(model)
        c = estimate_cost(model, d["input_tokens"], d["output_tokens"], d["cache_read"], d["cache_create"],
                          d.get("cache_create_5m", 0), d.get("cache_create_1h", 0))
        nc = estimate_cost_no_cache(model, d["input_tokens"], d["output_tokens"], d["cache_read"], d["cache_create"])
        if c is not None:
            total_cost += c
            has_cost = True
        if nc is not None:
            total_no_cache += nc
        # Thinking and text-to-user output cost
        if rates:
            M = 1_000_000
            total_thinking_cost += d.get("thinking_tokens", 0) * rates["output"] / M
            total_text_cost += d.get("text_tokens", 0) * rates["output"] / M

    if has_cost:
        savings = total_no_cache - total_cost
        tool_out_cost = total_cost - total_thinking_cost - total_text_cost  # remainder = input + cache + tool calls
        print(f"\n  Estimated API cost (with cache):     {format_cost(total_cost)}", file=f)
        print(f"  Cost without caching (hypothetical): {format_cost(total_no_cache)}", file=f)
        print(f"  Cache savings:                       {format_cost(savings)}", file=f)
        print(f"\n  Output cost breakdown:", file=f)
        print(f"    Thinking tokens:                   {format_cost(total_thinking_cost)}", file=f)
        print(f"    Text responses to user:            {format_cost(total_text_cost)}", file=f)

    tc = sum(d["total_chars"] for d in stats["tool_result_cost"].values())
    hc = sum(d["total_chars"] for d in stats["hook_injection_cost"].values())
    sc = sum(d["total_chars"] for d in stats["skill_cost"].values())

    print(f"\n  Context injected by tool results:    ~{format_tokens(tc // 4)} tokens", file=f)
    print(f"  Context injected by hooks/system:    ~{format_tokens(hc // 4)} tokens", file=f)
    print(f"  Context injected by skill loads:     ~{format_tokens(sc // 4)} tokens", file=f)
    print("  (Note: skill loads are a subset of tool results)", file=f)

    # Per-tool cost attribution using the blended rate.
    # The blended rate (total_cost / total_context_tokens) already reflects your
    # actual cache hit pattern, so multiplying tool injection tokens by it gives
    # a fair estimate of what each tool contributes to the bill.
    rate = _blended_input_rate(stats)  # $/token
    entries = []
    for tool, d in stats["tool_result_cost"].items():
        tok = d["total_chars"] // 4
        entries.append(("tool:" + tool, d["count"], tok, tok * rate if rate else None))
    for cat, d in stats["hook_injection_cost"].items():
        tok = d["total_chars"] // 4
        entries.append((cat, d["count"], tok, tok * rate if rate else None))
    entries.sort(key=lambda x: x[2], reverse=True)

    rows = [
        (name[:50], count, format_tokens(tok), format_cost(cost))
        for name, count, tok, cost in entries[:20]
    ]
    _table(
        ["Source", "Calls", "Est Tokens Injected", "Est Cost Attribution"],
        rows,
        "CONTEXT COST ATTRIBUTION BY SOURCE\n"
        "  Cost attribution uses blended $/token rate (reflects your actual cache hit pattern)",
        f,
    )
    print(file=f)


# ---- Public entry point ---------------------------------------------------

def write_report(stats: dict, output: str | None, top_n: int = 20):
    """Print the full ASCII report to stdout (output arg is ignored)."""
    f = sys.stdout
    print("\n" + "=" * 70, file=f)
    print("  CLAUDE CODE TOKEN COST ANALYSIS", file=f)
    print("=" * 70, file=f)
    print(f"  Total sessions:   {stats['total_sessions']}", file=f)
    print(f"  Total API calls:  {stats['total_requests']}", file=f)
    print(f"  Total tool calls: {stats['total_tool_calls']}", file=f)

    _section_models(stats, top_n, f)
    _section_projects(stats, top_n, f)
    _section_cache_daily(stats, top_n, f)
    _section_cache_projects(stats, top_n, f)
    _section_worst_cache_sessions(stats, top_n, f)
    _section_tool_cost(stats, top_n, f)
    _section_tool_lifetime_cost(stats, top_n, f)
    _section_hooks(stats, top_n, f)
    _section_skills(stats, top_n, f)
    _section_subagents(stats, top_n, f)
    _section_bash(stats, top_n, f)
    _section_heavy_sessions(stats, top_n, f)
    _section_summary(stats, f)


def write_daily_report(stats: dict, project_filter: str | None, top_n: int = 20):
    """Print a focused per-day breakdown for a single project."""
    f = sys.stdout

    # Collect matching projects from daily_by_project
    daily_data: dict[str, dict] = {}  # date -> aggregated day stats
    matched_project = None

    for dp_key, d in stats["daily_by_project"].items():
        date, project = dp_key.split("|", 1)
        if date == "unknown":
            continue
        if project_filter and project_filter.lower() not in project.lower():
            continue
        matched_project = project
        if date not in daily_data:
            daily_data[date] = {
                "input_tokens": 0, "output_tokens": 0,
                "cache_read": 0, "cache_create": 0,
                "requests": 0, "tool_calls": 0, "sessions": set(),
            }
        dd = daily_data[date]
        dd["input_tokens"] += d["input_tokens"]
        dd["output_tokens"] += d["output_tokens"]
        dd["cache_read"] += d["cache_read"]
        dd["cache_create"] += d["cache_create"]
        dd["requests"] += d["requests"]
        dd["tool_calls"] += d["tool_calls"]
        dd["sessions"] |= d["sessions"]

    if not daily_data:
        print("No data found.", file=f)
        if not project_filter:
            print("Hint: use --project NAME with --daily", file=f)
        return

    label = matched_project or project_filter or "all projects"
    print(f"\n{'=' * 80}", file=f)
    print(f"  DAILY BREAKDOWN: {label}", file=f)
    print(f"{'=' * 80}", file=f)

    # Totals
    total_in = sum(d["input_tokens"] for d in daily_data.values())
    total_out = sum(d["output_tokens"] for d in daily_data.values())
    total_cr = sum(d["cache_read"] for d in daily_data.values())
    total_cc = sum(d["cache_create"] for d in daily_data.values())
    total_reqs = sum(d["requests"] for d in daily_data.values())
    total_tools = sum(d["tool_calls"] for d in daily_data.values())
    total_sessions = len(set().union(*(d["sessions"] for d in daily_data.values())))

    # Cost for matched project(s) across all models
    proj_costs = _project_costs(stats)
    matched_proj_names = set()
    for dp_key in stats["daily_by_project"]:
        date, project = dp_key.split("|", 1)
        if project_filter and project_filter.lower() not in project.lower():
            continue
        matched_proj_names.add(project)

    proj_total_cost = sum(proj_costs.get(p, (0.0, 0.0))[0] for p in matched_proj_names)
    proj_no_cache = sum(proj_costs.get(p, (0.0, 0.0))[1] for p in matched_proj_names)
    proj_savings = proj_no_cache - proj_total_cost

    print(f"  Period:    {min(daily_data)} to {max(daily_data)}  ({len(daily_data)} days)", file=f)
    print(f"  Sessions:  {total_sessions}", file=f)
    print(f"  Requests:  {total_reqs:,}", file=f)
    print(f"  Tools:     {total_tools:,}", file=f)
    print(f"  Output:    {format_tokens(total_out)}", file=f)
    print(f"  Cache hit: {cache_hit_rate(total_cr, total_cc, total_in)}", file=f)
    if proj_total_cost:
        print(f"  Est cost:  {format_cost(proj_total_cost)}  (cache saved ~{format_cost(proj_savings)})", file=f)

    # Per-model daily breakdown for this project (from cache_by_date cross-ref)
    # Rebuild per-model view from the global cache_by_date, filtered
    date_model: dict[str, dict[str, dict]] = {}
    for dm_key, d in stats["cache_by_date"].items():
        date, model = dm_key.split("|", 1)
        if date not in daily_data:
            continue
        date_model.setdefault(date, {})[model] = d

    # Cost per day summed across models
    day_cost: dict[str, float] = {
        date: sum(
            estimate_cost(m, d["input_tokens"], d.get("output_tokens", 0), d["cache_read"], d["cache_create"]) or 0.0
            for m, d in models.items()
        )
        for date, models in date_model.items()
    }

    # Main daily table
    rows = []
    for date in sorted(daily_data)[-top_n:]:
        d = daily_data[date]
        rows.append([
            date,
            format_tokens(d["cache_read"]),
            format_tokens(d["cache_create"]),
            format_tokens(d["input_tokens"]),
            cache_hit_rate(d["cache_read"], d["cache_create"], d["input_tokens"]),
            format_tokens(d["output_tokens"]),
            d["requests"],
            d["tool_calls"],
            len(d["sessions"]),
            format_cost(day_cost.get(date)),
        ])
    _table(
        ["Date", "Cache Read", "Cache Create", "Uncached", "Hit Rate",
         "Output", "Reqs", "Tools", "Sessions", "Est Cost"],
        rows, "DAILY USAGE & CACHE", f,
    )

    # Figure out which models are active
    model_totals: dict[str, int] = {}
    for date, models in date_model.items():
        for model, d in models.items():
            model_totals[model] = model_totals.get(model, 0) + d["requests"]

    for model in sorted(model_totals, key=model_totals.get, reverse=True):
        if model_totals[model] < 5 or model == "<synthetic>":
            continue
        rows = []
        for date in sorted(date_model)[-top_n:]:
            if model not in date_model.get(date, {}):
                continue
            d = date_model[date][model]
            rows.append([
                date,
                format_tokens(d["cache_read"]),
                format_tokens(d["cache_create"]),
                format_tokens(d["input_tokens"]),
                cache_hit_rate(d["cache_read"], d["cache_create"], d["input_tokens"]),
                d["requests"],
            ])
        if rows:
            short = (model.replace("claude-", "")
                     .replace("-20251001", "").replace("-20251101", "")
                     .replace("-20250929", "").replace("-20250805", "")
                     .replace("-20250514", ""))
            _table(
                ["Date", "Cache Read", "Cache Create", "Uncached", "Hit Rate", "Reqs"],
                rows, f"BY MODEL: {short}", f,
            )

    # Worst cache sessions for this project
    all_sessions = set().union(*(d["sessions"] for d in daily_data.values()))
    session_cache = []
    for sid, sess in stats["sessions"].items():
        if sid not in all_sessions:
            continue
        total_ctx = sess["input_tokens"] + sess["cache_read"] + sess["cache_create"]
        if total_ctx >= 100_000:
            hit = sess["cache_read"] / total_ctx
            session_cache.append((sid, sess, hit))
    session_cache.sort(key=lambda x: x[2])

    if session_cache:
        rows = []
        for sid, sess, _ in session_cache[:top_n]:
            rows.append([
                sid[:20] + "...",
                format_tokens(sess["cache_read"]),
                format_tokens(sess["cache_create"]),
                format_tokens(sess["input_tokens"]),
                cache_hit_rate(sess["cache_read"], sess["cache_create"], sess["input_tokens"]),
                sess.get("start", "")[:10] if sess.get("start") else "-",
            ])
        _table(
            ["Session", "Cache Read", "Cache Create", "Uncached", "Hit Rate", "Date"],
            rows, "SESSIONS WITH WORST CACHE HIT RATE (min 100K context)", f,
        )

    print(file=f)
