# claude-usage-analyzer

Token cost attribution and cache hit-rate analysis for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions.

Parses the JSONL session files that Claude Code writes to `~/.claude/projects/` and produces detailed breakdowns of:

- **Token usage** by model, project, and session
- **Cache hit rates** — daily trends, per-model, per-project, worst sessions
- **Tool result context cost** — how much each tool injects into the conversation
- **Hook / system-reminder injection cost**
- **Skill content cost** — context loaded when skills are invoked
- **Subagent cost** — dispatches, result sizes, agent file token usage
- **Bash command frequency**

## Quick start

No install required — just clone and run:

```bash
git clone https://github.com/nooikko/claude-usage-analyzer.git
cd claude-usage-analyzer
./claude-usage --days 7
```

Requires Python 3.11+.

### Optional: pip install

If you want the `claude-usage` command available globally:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .                     # basic install
pip install '.[excel]'            # with Excel export support
```

## Usage

```bash
# Full report to terminal
claude-usage

# Last 30 days only
claude-usage --days 30

# Date range
claude-usage --since 2026-03-01 --until 2026-03-31

# Filter to one project
claude-usage --project harness --days 7

# Show more rows per section
claude-usage --top 30

# Export formats
claude-usage --format json -o report.json
claude-usage --format csv -o report.csv
claude-usage --format html -o report.html
claude-usage --format excel -o report.xlsx   # requires: pip install '.[excel]'

# Logging
claude-usage -v          # verbose / debug
claude-usage -q          # quiet (suppress progress)
```

## Output formats

| Format  | Description                                       |
| ------- | ------------------------------------------------- |
| `table` | ASCII tables to stdout (default)                  |
| `json`  | Full stats dict as JSON                           |
| `csv`   | Multiple CSV files (tool cost, cache daily, etc.) |
| `html`  | Self-contained HTML report with dark-mode styling |
| `excel` | Multi-sheet `.xlsx` workbook (requires openpyxl)  |

## How it works

Claude Code stores each session as a JSONL file in `~/.claude/projects/<project-dir>/<session-id>.jsonl`. Each line is a record — assistant messages include API usage stats (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`), and user messages contain tool results whose size directly contributes to context growth.

This tool:
1. Walks all project directories and parses every JSONL file
2. Aggregates token usage across models, projects, sessions, and dates
3. Measures tool result content sizes to estimate per-tool context cost
4. Extracts `<system-reminder>` injections to quantify hook overhead
5. Correlates `agent-*.jsonl` files with parent sessions for subagent cost

## License

MIT
