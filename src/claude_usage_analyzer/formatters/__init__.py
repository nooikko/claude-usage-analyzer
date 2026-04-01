"""Output formatters registry."""

from __future__ import annotations

FORMATS = ["table", "json", "csv", "html", "excel"]


def get_formatter(name: str):
    """Return the (write_report, file_extension) pair for *name*."""
    if name == "table":
        from .table import write_report
        return write_report, None
    if name == "json":
        from .json_fmt import write_report
        return write_report, ".json"
    if name == "csv":
        from .csv_fmt import write_report
        return write_report, ".csv"
    if name == "html":
        from .html import write_report
        return write_report, ".html"
    if name == "excel":
        from .excel import write_report
        return write_report, ".xlsx"
    raise ValueError(f"Unknown format: {name!r}. Choose from {FORMATS}")
