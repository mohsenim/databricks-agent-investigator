"""Tools that read system tables and table metadata through the SQL warehouse.

Every system-table query filters by workspace_id, because system tables cover the
whole account and region.
"""
import itertools
import re
from typing import Any

from ..redaction import redact
from .base import SqlApi, ToolResult

_TABLE_NAME = re.compile(r"^[A-Za-z_][\w]*\.[A-Za-z_][\w]*\.[A-Za-z_][\w]*$")
# Operations that always change the schema, and operations that rewrite the table and may change it.
SCHEMA_OPS = {"ADD COLUMNS", "CHANGE COLUMN", "DROP COLUMNS", "REPLACE COLUMNS"}
REWRITE_OPS = {"CREATE OR REPLACE TABLE", "CREATE OR REPLACE TABLE AS SELECT",
               "CREATE TABLE AS SELECT", "REPLACE TABLE AS SELECT"}


def quote_table(name: str) -> str:
    """Identifiers cannot be bound as parameters, so check them strictly and quote them
    with backticks.
    """
    if not _TABLE_NAME.match(name):
        raise ValueError(f"not a three-part table name: {name!r}")
    return ".".join(f"`{part}`" for part in name.split("."))


def _fail(tool: str, source: str, exc: Exception) -> ToolResult:
    return ToolResult.unavailable(tool, f"{type(exc).__name__}: {exc}", source)


def get_run_history_system(sql: SqlApi, workspace_id: str, job_id: int, task_key: str,
                           days: int = 30, limit: int = 30) -> ToolResult:
    """Finished task runs from job_task_run_timeline. Long runs are split into hourly
    rows, and only the last row has a result_state.
    """
    try:
        rows = sql.run(
            """
            SELECT run_id, job_run_id, period_start_time, period_end_time, result_state,
                   termination_code
            FROM system.lakeflow.job_task_run_timeline
            WHERE workspace_id = :workspace_id AND job_id = :job_id AND task_key = :task_key
              AND period_start_time > current_timestamp() - make_interval(0, 0, 0, :days)
              AND result_state IS NOT NULL
            ORDER BY period_end_time DESC LIMIT :limit
            """,
            {"workspace_id": workspace_id, "job_id": job_id, "task_key": task_key,
             "days": days, "limit": limit},
        )
    except Exception as exc:
        return _fail("get_run_history", "system_tables", exc)
    return ToolResult(tool="get_run_history", source="system_tables",
                      data={"task_key": task_key, "history": [_clean(r) for r in rows]})


_TRACKED = ["name", "run_as", "paused", "timeout_seconds", "trigger_type", "health_rules"]


def diff_versions(versions: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    """Fields that changed between consecutive SCD2 versions (oldest first)."""
    changes = []
    for prev, cur in itertools.pairwise(versions):
        delta = {f: {"from": prev.get(f), "to": cur.get(f)}
                 for f in fields if prev.get(f) != cur.get(f)}
        if delta:
            changes.append({"change_time": str(cur.get("change_time")), "changed": delta})
    return changes


def get_job_changes(sql: SqlApi, workspace_id: str, job_id: int, days: int = 7) -> ToolResult:
    """Changes to job settings and the run-as identity. Edits to notebook code are not recorded."""
    try:
        jobs = sql.run(
            """
            SELECT change_time, name, run_as, paused, timeout_seconds, trigger_type
            FROM system.lakeflow.jobs
            WHERE workspace_id = :workspace_id AND job_id = :job_id
              AND change_time > current_timestamp() - make_interval(0, 0, 0, :days + 30)
            ORDER BY change_time ASC
            """,
            {"workspace_id": workspace_id, "job_id": job_id, "days": days},
        )
        tasks = sql.run(
            """
            SELECT change_time, task_key, depends_on_keys, timeout_seconds
            FROM system.lakeflow.job_tasks
            WHERE workspace_id = :workspace_id AND job_id = :job_id
              AND change_time > current_timestamp() - make_interval(0, 0, 0, :days + 30)
            ORDER BY change_time ASC
            """,
            {"workspace_id": workspace_id, "job_id": job_id, "days": days},
        )
    except Exception as exc:
        return _fail("get_job_changes", "system_tables", exc)
    task_changes = []
    for key in sorted({t["task_key"] for t in tasks}):
        mine = [t for t in tasks if t["task_key"] == key]
        for c in diff_versions(mine, ["depends_on_keys", "timeout_seconds"]):
            task_changes.append({"task_key": key, **c})
    return ToolResult(tool="get_job_changes", source="system_tables", data={
        "job_changes": diff_versions(jobs, _TRACKED),
        "task_changes": task_changes,
        "note": "Some columns are empty for rows written before early December 2025; "
                "notebook code edits are not recorded.",
    })


def get_table_history(sql: SqlApi, table: str, limit: int = 30) -> ToolResult:
    try:
        rows = sql.run(f"DESCRIBE HISTORY {quote_table(table)} LIMIT {int(limit)}")
    except Exception as exc:
        return _fail("get_table_history", "table_history", exc)
    history = []
    for r in rows:
        metrics = r.get("operationMetrics") or {}
        history.append({
            "version": r.get("version"),
            "timestamp": str(r.get("timestamp")),
            "user": r.get("userName"),
            "operation": r.get("operation"),
            "parameters": redact(str(r.get("operationParameters") or ""), 400),
            "rows_written": metrics.get("numOutputRows"),
            "schema_change": r.get("operation") in SCHEMA_OPS,
            "rewrite": r.get("operation") in REWRITE_OPS,
        })
    return ToolResult(tool="get_table_history", source="table_history",
                      data={"table": table, "history": history})


def diff_schemas(old: list[tuple[str, str]], new: list[tuple[str, str]]) -> dict[str, Any]:
    o, n = dict(old), dict(new)
    return {
        "added": sorted(set(n) - set(o)),
        "dropped": sorted(set(o) - set(n)),
        "retyped": [{"column": c, "from": o[c], "to": n[c]} for c in sorted(set(o) & set(n))
                    if o[c] != n[c]],
    }


def compare_table_schema(sql: SqlApi, table: str, old_version: int, new_version: int) -> ToolResult:
    """Schema of two versions through time travel with LIMIT 0 (no rows are read).
    It only works while the data files of the old version are kept (7 days by default).
    """
    t = quote_table(table)
    try:
        old = sql.columns(f"SELECT * FROM {t} VERSION AS OF {int(old_version)} LIMIT 0")
        new = sql.columns(f"SELECT * FROM {t} VERSION AS OF {int(new_version)} LIMIT 0")
    except Exception as exc:
        return _fail("compare_table_schema", "time_travel", exc)
    return ToolResult(tool="compare_table_schema", source="time_travel",
                      data={"table": table, "old_version": old_version,
                            "new_version": new_version, **diff_schemas(old, new)})


def get_permission_changes(sql: SqlApi, workspace_id: str, table: str, days: int = 7) -> ToolResult:
    """Grants and revokes on a table, from the audit log."""
    try:
        rows = sql.run(
            """
            SELECT event_time, user_identity.email AS actor, action_name,
                   request_params['changes'] AS changes
            FROM system.access.audit
            WHERE workspace_id = :workspace_id AND service_name = 'unityCatalog'
              AND action_name IN ('updatePermissions', 'grantPermission', 'revokePermission')
              AND request_params['full_name_arg'] = :table
              AND event_time > current_timestamp() - make_interval(0, 0, 0, :days)
            ORDER BY event_time DESC LIMIT 20
            """,
            {"workspace_id": workspace_id, "table": table, "days": days},
        )
    except Exception as exc:
        return _fail("get_permission_changes", "audit_log", exc)
    return ToolResult(tool="get_permission_changes", source="audit_log", data={
        "table": table,
        "changes": [{**_clean(r), "changes": redact(str(r.get("changes") or ""), 300)} for r in rows],
    })


def get_lineage(sql: SqlApi, workspace_id: str, job_id: int, days: int = 30) -> ToolResult:
    """Tables the job reads and writes, and who reads what it writes. Best effort."""
    try:
        io = sql.run(
            """
            SELECT DISTINCT source_table_full_name AS source, target_table_full_name AS target
            FROM system.access.table_lineage
            WHERE workspace_id = :workspace_id AND entity_type = 'JOB' AND entity_id = :job_id
              AND event_time > current_timestamp() - make_interval(0, 0, 0, :days)
            """,
            {"workspace_id": workspace_id, "job_id": str(job_id), "days": days},
        )
        inputs = sorted({r["source"] for r in io if r.get("source")})
        outputs = sorted({r["target"] for r in io if r.get("target")})
        downstream: list[dict[str, Any]] = []
        for table in outputs:
            downstream += sql.run(
                """
                SELECT DISTINCT target_table_full_name AS asset, entity_type, entity_id
                FROM system.access.table_lineage
                WHERE workspace_id = :workspace_id AND source_table_full_name = :table
                  AND event_time > current_timestamp() - make_interval(0, 0, 0, :days)
                """,
                {"workspace_id": workspace_id, "table": table, "days": days},
            )
    except Exception as exc:
        return _fail("get_lineage", "lineage", exc)
    return ToolResult(tool="get_lineage", source="lineage", data={
        "inputs": inputs, "outputs": outputs,
        "downstream": [_clean(d) for d in downstream],
        "note": "Best effort: runs-submit jobs and renamed objects are missing.",
    })


def get_upstream_run_status(sql: SqlApi, workspace_id: str, input_tables: list[str],
                            days: int = 30, exclude_job_id: int | None = None) -> ToolResult:
    """The latest finished run of each other job that writes one of the input tables."""
    results = []
    try:
        for table in input_tables:
            producers = sql.run(
                """
                SELECT DISTINCT entity_id AS job_id
                FROM system.access.table_lineage
                WHERE workspace_id = :workspace_id AND entity_type = 'JOB'
                  AND target_table_full_name = :table
                  AND event_time > current_timestamp() - make_interval(0, 0, 0, :days)
                """,
                {"workspace_id": workspace_id, "table": table, "days": days},
            )
            for p in producers:
                if exclude_job_id is not None and str(p["job_id"]) == str(exclude_job_id):
                    continue
                latest = sql.run(
                    """
                    SELECT run_id, result_state, termination_code, period_end_time
                    FROM system.lakeflow.job_run_timeline
                    WHERE workspace_id = :workspace_id AND job_id = :job_id
                      AND result_state IS NOT NULL
                    ORDER BY period_end_time DESC LIMIT 1
                    """,
                    {"workspace_id": workspace_id, "job_id": p["job_id"]},
                )
                results.append({"table": table, "producer_job_id": p["job_id"],
                                "latest": _clean(latest[0]) if latest else None})
    except Exception as exc:
        return _fail("get_upstream_run_status", "lineage", exc)
    return ToolResult(tool="get_upstream_run_status", source="lineage",
                      data={"producers": results,
                            "note": "Best effort: depends on lineage coverage."})


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if v is not None and not isinstance(v, (int, float, bool)) else v)
            for k, v in row.items()}
