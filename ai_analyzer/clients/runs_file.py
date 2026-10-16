"""The agent runs log file (AGENT_RUNS_FILE, default agent_runs.log): every run's report, appended."""

from datetime import datetime, timezone
from pathlib import Path

from ai_analyzer.config import get_settings


def append(report: str) -> None:
    """Keep every run's report (summary, reply, tools, timing, tokens) in one file to read later."""
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with Path(get_settings().agent_runs_file).open("a") as runs_file:
        runs_file.write(f"\n# {timestamp}{report}\n")
