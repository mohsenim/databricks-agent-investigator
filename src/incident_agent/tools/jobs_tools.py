"""Tools that read the Jobs and Workspace APIs."""
import re
from typing import Any

from ..redaction import redact
from .base import JobsApi, ToolResult

SOURCE = "jobs_api"


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    state = task.get("state") or {}
    return {
        "task_key": task.get("task_key"),
        "run_id": task.get("run_id"),
        "life_cycle_state": state.get("life_cycle_state"),
        "result_state": state.get("result_state"),
        "termination_code": (task.get("status") or {}).get("termination_details", {}).get("code")
        or state.get("termination_code"),
        "state_message": redact(state.get("state_message") or "", 600),
        "attempt_number": task.get("attempt_number", 0),
        "start_time": task.get("start_time"),
        "end_time": task.get("end_time"),
        "notebook_path": (task.get("notebook_task") or {}).get("notebook_path"),
        "parameters": (task.get("notebook_task") or {}).get("base_parameters") or {},
        "serverless": not (task.get("existing_cluster_id") or task.get("new_cluster")
                           or task.get("job_cluster_key")),
    }


def get_failed_run(api: JobsApi, run_id: int) -> ToolResult:
    try:
        run = api.get_run(run_id)
    except Exception as exc:  # network error, no permission, not found
        return ToolResult.unavailable("get_failed_run", f"runs/get failed: {exc}", SOURCE)
    state = run.get("state") or {}
    return ToolResult(tool="get_failed_run", source=SOURCE, data={
        "job_id": run.get("job_id"),
        "run_id": run.get("run_id"),
        "run_name": run.get("run_name"),
        "result_state": state.get("result_state"),
        "state_message": redact(state.get("state_message") or "", 600),
        "termination_code": ((run.get("status") or {}).get("termination_details") or {}).get("code")
        or state.get("termination_code"),
        "repair_count": len(run.get("repair_history") or []) - 1
        if run.get("repair_history") else 0,
        "tasks": [_task_summary(t) for t in run.get("tasks") or []],
    })


def get_task_error(api: JobsApi, task_run_id: int) -> ToolResult:
    """Error and stack trace of one task run. The API rejects the parent run ID of a
    multi-task run, so the caller must pass a task run ID.
    """
    try:
        out = api.get_run_output(task_run_id)
    except Exception as exc:
        return ToolResult.unavailable("get_task_error", f"runs/get-output failed: {exc}", SOURCE)
    # notebook_output.result is user data and is never passed on.
    error = out.get("error") or ""
    trace = out.get("error_trace") or ""
    return ToolResult(tool="get_task_error", source=SOURCE, data={
        "task_run_id": task_run_id,
        "error": redact(error, 1000),
        "error_trace": redact(trace),
    })


_LINE_RE = re.compile(r"(?:line|Line|:)\s*(\d{1,5})\b")
# A three-part name only counts as a table inside a string or after a SQL keyword,
# so attribute chains like dbutils.widgets.get are ignored.
_TABLE_RE = re.compile(
    r"""(?:["'`]|\b(?:from|join|into|table|update)\s+)"""
    r"""([A-Za-z_]\w*\.[A-Za-z_]\w*\.[A-Za-z_]\w*)\b""", re.IGNORECASE)


def failing_line(trace: str) -> int | None:
    """The last line number in a stack trace (the innermost user frame)."""
    nums = _LINE_RE.findall(trace or "")
    return int(nums[-1]) if nums else None


_WIDGET_DEFAULT = re.compile(
    r"""widgets\.(?:text|dropdown|combobox|multiselect)\(\s*["'](\w+)["']\s*,\s*["']([^"']*)["']""")
_WIDGET_VAR = re.compile(r"""(\w+)\s*=\s*dbutils\.widgets\.get\(\s*["'](\w+)["']\s*\)""")
_FSTRING_TABLE = re.compile(r"\{(\w+)\}\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)")


def resolve_parameterised_tables(source: str, parameters: dict[str, Any] | None) -> list[str]:
    """Tables named like f"{catalog}.raw.orders". The variable comes from the task
    parameters, or from the widget default if there is none.
    """
    widgets = dict(_WIDGET_DEFAULT.findall(source))
    widgets.update({k: str(v) for k, v in (parameters or {}).items()})
    variables = {var: widgets[w] for var, w in _WIDGET_VAR.findall(source) if w in widgets}
    return [f"{variables[v]}.{schema}.{table}" for v, schema, table in _FSTRING_TABLE.findall(source)
            if v in variables]


_WRITE = re.compile(r"saveAsTable|insertInto|INSERT\s+(?:INTO|OVERWRITE)|MERGE\s+INTO|"
                    r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE|\.write\b", re.IGNORECASE)


def split_tables(source: str, parameters: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """(read, written) tables. A table on a line that writes is an output."""
    read: set[str] = set()
    written: set[str] = set()
    for line in source.splitlines():
        names = set(_TABLE_RE.findall(line)) | set(resolve_parameterised_tables(line, None))
        (written if _WRITE.search(line) else read).update(names)
    # f-string names depend on widget defaults that are set elsewhere in the notebook.
    resolved = set(resolve_parameterised_tables(source, parameters))
    for line in source.splitlines():
        for name in resolved:
            tail = name.split(".", 1)[1]
            if f"}}.{tail}" in line:
                (written if _WRITE.search(line) else read).add(name)
    return sorted(read - written)[:20], sorted(written)[:20]


def get_task_source(api: JobsApi, notebook_path: str, error_trace: str = "",
                    context_lines: int = 15, parameters: dict[str, Any] | None = None) -> ToolResult:
    """The code around the failing line. The notebook may have changed since the run."""
    if not notebook_path:
        return ToolResult.unavailable("get_task_source", "task has no notebook path", "workspace_api")
    try:
        source = api.export_notebook(notebook_path)
    except Exception as exc:
        return ToolResult.unavailable("get_task_source", f"export failed: {exc}", "workspace_api")
    lines = source.splitlines()
    line = failing_line(error_trace)
    if line and 0 < line <= len(lines):
        lo, hi = max(0, line - 1 - context_lines), min(len(lines), line + context_lines)
        excerpt = lines[lo:hi]
        start = lo + 1
    else:
        excerpt, start = lines[: context_lines * 2], 1
    numbered = "\n".join(f"{start + i}: {text}" for i, text in enumerate(excerpt))
    return ToolResult(tool="get_task_source", source="workspace_api", data={
        "notebook_path": notebook_path,
        "failing_line": line,
        "excerpt": redact(numbered, mask_literals=False),
        "tables_referenced": split_tables(source, parameters)[0],
        "tables_written": split_tables(source, parameters)[1],
        "note": "Current notebook content; it may differ from the version that ran.",
    })


def get_run_history_api(api: JobsApi, job_id: int, task_key: str, since_ms: int,
                        limit: int = 20) -> ToolResult:
    """Recent runs of the same task from the Jobs API (fresh data, last 60 days)."""
    try:
        runs = api.list_runs(job_id, since_ms, limit)
    except Exception as exc:
        return ToolResult.unavailable("get_run_history", f"runs/list failed: {exc}", SOURCE)
    history = []
    for run in runs:
        for task in run.get("tasks") or []:
            if task.get("task_key") == task_key:
                state = task.get("state") or {}
                history.append({"run_id": run.get("run_id"),
                                "result_state": state.get("result_state"),
                                "start_time": task.get("start_time"),
                                "end_time": task.get("end_time")})
    return ToolResult(tool="get_run_history", source=SOURCE,
                      data={"task_key": task_key, "history": history})
