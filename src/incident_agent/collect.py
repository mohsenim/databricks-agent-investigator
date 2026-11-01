"""Collect evidence without a model: call the main tools in a fixed order and turn
their results into evidence items.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import EvidenceItem, Feature, Polarity
from .poller import Target
from .rules import classify_error, mentioned_columns
from .taxonomy import FailureClass, prior_for
from .tools import jobs_tools, sql_tools
from .tools.base import JobsApi, SqlApi, ToolResult

C = FailureClass
CHANGE_WINDOW = timedelta(days=7)
SCHEMA_ERROR_CLASSES = {C.SCHEMA_CHANGE}


@dataclass
class Bundle:
    evidence: list[EvidenceItem] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    input_tables: list[str] = field(default_factory=list)
    blast_radius: list[dict[str, Any]] = field(default_factory=list)
    missing_key_tool: bool = False
    tables_checked: int = 0
    output_tables: list[str] = field(default_factory=list)
    task_info: dict[str, Any] = field(default_factory=dict)
    error_text: str = ""
    job_name: str = ""

    def add(self, tool: str, args: dict[str, Any], summary: str, cls: FailureClass,
            feature: Feature, source: str, polarity: Polarity = Polarity.SUPPORTS) -> EvidenceItem:
        item = EvidenceItem(id=f"E{len(self.evidence) + 1}", tool=tool, arguments=args,
                            summary=summary, failure_class=cls, polarity=polarity,
                            feature=feature, source=source)
        self.evidence.append(item)
        return item

    def record(self, result: ToolResult) -> ToolResult:
        self.results.append(result)
        if not result.ok:
            self.unavailable.append(f"{result.tool}: {result.unavailable_reason}")
        return result


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def collect(jobs: JobsApi, sql: SqlApi, target: Target, workspace_id: str) -> Bundle:
    b = Bundle(job_name=target.job_name)
    key = target.key

    run = b.record(jobs_tools.get_failed_run(jobs, key.run_id))
    task = _find_task(run, key.task_key)
    b.task_info = task or {}
    failure_time = _parse_time(
        datetime.fromtimestamp(task["end_time"] / 1000, UTC) if task and task.get("end_time")
        else None) or datetime.now(UTC)

    if task:
        _termination_evidence(b, task)
    elif run.ok:
        _run_level_evidence(b, run.data)

    task_run_id = target.task_run_id or (task or {}).get("run_id")
    error = b.record(jobs_tools.get_task_error(jobs, task_run_id)) if task_run_id else None
    if error and error.ok:
        b.error_text = f"{error.data['error']}\n{error.data['error_trace']}"
        for cls, label in classify_error(b.error_text):
            b.add("get_task_error", {"task_run_id": task_run_id}, f"Error text shows {label}.",
                  cls, Feature.DIRECT_ERROR_MATCH, "jobs_api")

    if key.task_key:
        _history_evidence(b, jobs, sql, workspace_id, key.job_id, key.task_key)
    _job_change_evidence(b, sql, workspace_id, key.job_id, task, failure_time)

    notebook = (task or {}).get("notebook_path")
    if notebook:
        src = b.record(jobs_tools.get_task_source(
            jobs, notebook, b.error_text, parameters=(task or {}).get("parameters")))
        if src.ok:
            b.input_tables = src.data["tables_referenced"]
            b.output_tables = src.data.get("tables_written", [])

    _lineage_evidence(b, sql, workspace_id, key.job_id, failure_time)
    _finalize_codeerror(b)
    return b


def _find_task(run: ToolResult, task_key: str) -> dict[str, Any] | None:
    if not run.ok:
        return None
    for t in run.data.get("tasks", []):
        if t["task_key"] == task_key:
            return t
    return None


def _termination_evidence(b: Bundle, task: dict[str, Any]) -> None:
    cls = prior_for(task.get("result_state"), task.get("termination_code"))
    if cls:
        code = task.get("termination_code") or task.get("result_state")
        b.add("get_failed_run", {"task_key": task["task_key"]},
              f"Task ended with {code}.", cls, Feature.DIRECT_ERROR_MATCH, "jobs_api")


def _run_level_evidence(b: Bundle, data: dict[str, Any]) -> None:
    code = data.get("termination_code")
    cls = prior_for(data.get("result_state"), code)
    if cls:
        b.add("get_failed_run", {}, f"Run ended with {code or data.get('result_state')}.", cls,
              Feature.DIRECT_ERROR_MATCH, "jobs_api")
    msg = data.get("state_message") or ""
    for cls, label in classify_error(msg):
        b.add("get_failed_run", {}, f"Run-level message shows {label}.", cls,
              Feature.DIRECT_ERROR_MATCH, "jobs_api")


def _history_evidence(b: Bundle, jobs: JobsApi, sql: SqlApi, workspace_id: str, job_id: int,
                      task_key: str) -> None:
    since_ms = int((datetime.now(UTC) - timedelta(days=30)).timestamp() * 1000)
    res = b.record(jobs_tools.get_run_history_api(jobs, job_id, task_key, since_ms))
    history = res.data.get("history", []) if res.ok else []
    if not history:
        res = b.record(sql_tools.get_run_history_system(sql, workspace_id, job_id, task_key))
        history = res.data.get("history", []) if res.ok else []
    if any(h.get("result_state") in ("SUCCESS", "SUCCEEDED") for h in history):
        for cls in {e.failure_class for e in b.evidence
                    if e.feature is Feature.DIRECT_ERROR_MATCH}:
            b.add("get_run_history", {"job_id": job_id, "task_key": task_key},
                  "The same task succeeded in an earlier run.", cls, Feature.EARLIER_SUCCESS,
                  res.source)


def _job_change_evidence(b: Bundle, sql: SqlApi, workspace_id: str, job_id: int,
                         task: dict[str, Any] | None, failure_time: datetime) -> None:
    res = b.record(sql_tools.get_job_changes(sql, workspace_id, job_id))
    if not res.ok:
        return
    recent = []
    for change in res.data["job_changes"] + res.data["task_changes"]:
        when = _parse_time(change["change_time"])
        if when and failure_time - CHANGE_WINDOW <= when <= failure_time:
            recent.append(change)
    for change in recent:
        fields = set(change["changed"])
        if "timeout_seconds" in fields and (task or {}).get("result_state") == "TIMEDOUT":
            cls, text = C.RESOURCE_LIMIT, "The timeout setting was changed shortly before the failure."
        elif "run_as" in fields:
            cls, text = C.PERMISSION, "The run-as identity was changed shortly before the failure."
        else:
            cls = C.CODE_OR_CONFIG
            text = f"Job settings changed shortly before the failure: {', '.join(sorted(fields))}."
        b.add("get_job_changes", {"job_id": job_id}, text, cls, Feature.TIME_CORRELATION,
              "system_tables")


def _lineage_evidence(b: Bundle, sql: SqlApi, workspace_id: str, job_id: int,
                      failure_time: datetime) -> None:
    lin = b.record(sql_tools.get_lineage(sql, workspace_id, job_id))
    if lin.ok:
        inputs = sorted((set(b.input_tables) | set(lin.data["inputs"])) - set(b.output_tables))
        b.input_tables = inputs
        b.blast_radius = lin.data["downstream"] + [{"asset": t} for t in lin.data["outputs"]]
    tables = [t for t in b.input_tables if t.count(".") == 2]

    up = b.record(sql_tools.get_upstream_run_status(
        sql, workspace_id, tables, exclude_job_id=job_id)) if tables else None
    if up and up.ok:
        for p in up.data["producers"]:
            latest = p["latest"]
            if latest and latest.get("result_state") in ("FAILED", "ERROR", "TIMED_OUT"):
                b.add("get_upstream_run_status", {"table": p["table"]},
                      f"The job that writes {p['table']} last ended as {latest['result_state']}.",
                      C.UPSTREAM_JOB, Feature.UPSTREAM_SIGNAL, "lineage")

    for table in tables:
        _table_evidence(b, sql, workspace_id, table, failure_time)


def _table_evidence(b: Bundle, sql: SqlApi, workspace_id: str, table: str,
                    failure_time: datetime) -> None:
    hist = b.record(sql_tools.get_table_history(sql, table))
    schema_wanted = any(e.failure_class in SCHEMA_ERROR_CLASSES for e in b.evidence)
    if not hist.ok:
        # The name was only a guess from the code and is not a table, so it is not a missing tool.
        not_found = "TABLE_OR_VIEW_NOT_FOUND" in (hist.unavailable_reason or "")
        b.missing_key_tool = b.missing_key_tool or (schema_wanted and not not_found)
        return
    b.tables_checked += 1
    rows = hist.data["history"]
    recent = [r for r in rows if (t := _parse_time(r["timestamp"]))
              and failure_time - CHANGE_WINDOW <= t <= failure_time]

    for r in [r for r in recent if r["schema_change"] or r.get("rewrite")][:1]:
        confirmed = _confirm_with_time_travel(b, sql, table, r)
        if r["schema_change"] or confirmed:
            b.add("get_table_history", {"table": table},
                  f"{table} had a schema change ({r['operation']}) at {r['timestamp']}, "
                  "before the failure.", C.SCHEMA_CHANGE, Feature.TIME_CORRELATION,
                  "table_history")

    writes = [r for r in recent if r["operation"] in ("WRITE", "MERGE", "STREAMING UPDATE")]
    if writes and str(writes[0].get("rows_written")) == "0":
        b.add("get_table_history", {"table": table},
              f"The latest write to {table} produced zero rows.", C.DATA_PROBLEM,
              Feature.UPSTREAM_SIGNAL, "table_history")

    perm = b.record(sql_tools.get_permission_changes(sql, workspace_id, table))
    if perm.ok and perm.data["changes"] and any(e.failure_class is C.PERMISSION
                                                for e in b.evidence):
        b.add("get_permission_changes", {"table": table},
              f"Permissions on {table} were changed shortly before the failure.",
              C.PERMISSION, Feature.TIME_CORRELATION, "audit_log")


def _confirm_with_time_travel(b: Bundle, sql: SqlApi, table: str,
                              change: dict[str, Any]) -> bool:
    """Compare the table schema before and after `change`. Returns True if a column was
    dropped or retyped that the error is about (any column if the error names none).
    """
    new_v = change["version"]
    if new_v is None or new_v < 1:
        return False
    res = b.record(sql_tools.compare_table_schema(sql, table, new_v - 1, new_v))
    if not res.ok:
        return False
    changed = set(res.data["dropped"]) | {r["column"] for r in res.data["retyped"]}
    named = mentioned_columns(b.error_text)
    if not changed or (named and not changed & named):
        return False
    what = ", ".join(sorted(changed))
    b.add("compare_table_schema", {"table": table, "versions": [new_v - 1, new_v]},
          f"Between versions {new_v - 1} and {new_v}, column(s) {what} were dropped or retyped.",
          C.SCHEMA_CHANGE, Feature.SECOND_SOURCE, "time_travel")
    return True


def _finalize_codeerror(b: Bundle) -> None:
    """A missing-column error where no input table changed points at the code itself."""
    schema = [e for e in b.evidence if e.failure_class is C.SCHEMA_CHANGE]
    has_upstream = any(e.feature in (Feature.TIME_CORRELATION, Feature.SECOND_SOURCE)
                       for e in schema)
    if schema and not b.tables_checked:
        b.unavailable.append("input tables could not be determined, so table changes were not checked")
        b.missing_key_tool = True
    elif schema and not has_upstream and not b.missing_key_tool:
        b.add("collect", {}, "No schema change was found on the input tables.", C.SCHEMA_CHANGE,
              Feature.DIRECT_ERROR_MATCH, "table_history", Polarity.CONTRADICTS)
        b.add("collect", {}, "The error is a missing or mismatched column but the inputs did "
              "not change, so the code or config is the likely cause.", C.CODE_OR_CONFIG,
              Feature.DIRECT_ERROR_MATCH, "jobs_api")
