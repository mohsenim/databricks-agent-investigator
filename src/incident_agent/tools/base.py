"""The common tool result type and the interfaces the tools use."""
from typing import Any, Protocol

from pydantic import BaseModel

from ..redaction import redact


class ToolResult(BaseModel):
    tool: str
    ok: bool = True
    data: dict[str, Any] = {}
    unavailable_reason: str = ""
    source: str = ""  # which system gave the answer: jobs_api, system_tables, table_history, ...
    truncated: bool = False

    @classmethod
    def unavailable(cls, tool: str, reason: str, source: str = "") -> "ToolResult":
        return cls(tool=tool, ok=False, unavailable_reason=redact(reason, 300), source=source)


class JobsApi(Protocol):
    """The part of the Jobs and Workspace APIs that the tools use. Returns plain dicts."""

    def get_run(self, run_id: int) -> dict[str, Any]: ...
    def get_run_output(self, task_run_id: int) -> dict[str, Any]: ...
    def list_runs(self, job_id: int, start_time_from_ms: int, limit: int = 25) -> list[dict[str, Any]]: ...
    def export_notebook(self, path: str) -> str: ...
    def get_job(self, job_id: int) -> dict[str, Any]: ...


class SqlApi(Protocol):
    def run(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def columns(self, statement: str) -> list[tuple[str, str]]:
        """Column names and types of a statement's result (used with LIMIT 0)."""
        ...
