"""Adapters between the Databricks SDK and the small interfaces the tools use."""
import itertools
import json
from typing import Any

_INT_TYPES = {"INT", "SHORT", "BYTE", "LONG", "BIGINT", "SMALLINT", "TINYINT"}
_FLOAT_TYPES = {"DOUBLE", "FLOAT", "DECIMAL"}
_JSON_TYPES = {"MAP", "STRUCT", "ARRAY"}


def convert_value(value: Any, type_name: str) -> Any:
    """The Statement Execution API returns every value as a string. Convert them back to
    real types.
    """
    if value is None:
        return None
    t = (type_name or "").upper()
    if t in _INT_TYPES:
        return int(value)
    if t in _FLOAT_TYPES:
        return float(value)
    if t == "BOOLEAN":
        return str(value).lower() == "true"
    if t in _JSON_TYPES:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def rows_from_response(columns: list[tuple[str, str]], data: list[list[Any]]) -> list[dict[str, Any]]:
    return [{name: convert_value(v, typ) for (name, typ), v in zip(columns, row, strict=False)}
            for row in data]


def sql_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INT" if -(2**31) <= value < 2**31 else "BIGINT"
    if isinstance(value, float):
        return "DOUBLE"
    return "STRING"


class SdkSqlRunner:
    """SqlApi and SqlRunner on a SQL warehouse. Values are always passed as bound parameters."""

    def __init__(self, client: Any, warehouse_id: str):
        self.w = client
        self.warehouse_id = warehouse_id

    def _execute(self, statement: str, params: dict[str, Any] | None):
        from databricks.sdk.service.sql import StatementParameterListItem

        bound = [StatementParameterListItem(name=k, value=None if v is None else str(v),
                                            type=sql_type(v)) for k, v in (params or {}).items()]
        resp = self.w.statement_execution.execute_statement(
            statement=statement, warehouse_id=self.warehouse_id, parameters=bound or None,
            wait_timeout="50s")
        status = resp.status
        if status and status.state and status.state.value != "SUCCEEDED":
            message = status.error.message if status.error else status.state.value
            raise RuntimeError(message)
        return resp

    def run(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        resp = self._execute(statement, params)
        cols = [(c.name, c.type_name.value if c.type_name else "STRING")
                for c in (resp.manifest.schema.columns if resp.manifest and resp.manifest.schema
                          and resp.manifest.schema.columns else [])]
        data = resp.result.data_array if resp.result and resp.result.data_array else []
        return rows_from_response(cols, data)

    def columns(self, statement: str) -> list[tuple[str, str]]:
        resp = self._execute(statement, None)
        schema = resp.manifest.schema.columns if resp.manifest and resp.manifest.schema else []
        return [(c.name, c.type_text or (c.type_name.value if c.type_name else "")) for c in schema or []]


class SdkJobsApi:
    """JobsApi on top of the workspace client. Returns plain dicts like the REST API."""

    def __init__(self, client: Any):
        self.w = client

    def get_run(self, run_id: int) -> dict[str, Any]:
        return self.w.jobs.get_run(run_id=run_id, include_history=True).as_dict()

    def get_run_output(self, task_run_id: int) -> dict[str, Any]:
        return self.w.jobs.get_run_output(run_id=task_run_id).as_dict()

    def list_runs(self, job_id: int, start_time_from_ms: int, limit: int = 25) -> list[dict[str, Any]]:
        """Return up to `limit` runs in total. The API gives at most 25 per page, and the SDK
        fetches the next pages.
        """
        runs = self.w.jobs.list_runs(job_id=job_id, completed_only=True, expand_tasks=True,
                                     start_time_from=start_time_from_ms, limit=min(limit, 25))
        return [r.as_dict() for r in itertools.islice(runs, limit)]

    def export_notebook(self, path: str) -> str:
        import base64

        from databricks.sdk.service.workspace import ExportFormat

        resp = self.w.workspace.export(path, format=ExportFormat.SOURCE)
        return base64.b64decode(resp.content or "").decode("utf-8", errors="replace")

    def get_job(self, job_id: int) -> dict[str, Any]:
        return self.w.jobs.get(job_id=job_id).as_dict()

    def start_investigation(self, investigator_job_id: int, params: dict[str, str]) -> int:
        run = self.w.jobs.run_now(job_id=investigator_job_id, job_parameters=params)
        return run.run_id
