import pytest

from incident_agent.tools.sql_tools import (
    compare_table_schema,
    diff_schemas,
    diff_versions,
    get_job_changes,
    get_lineage,
    get_permission_changes,
    get_run_history_system,
    get_table_history,
    get_upstream_run_status,
    quote_table,
)


class FakeSql:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.statements = []
        self.fail = fail
        self.cols = {}

    def run(self, statement, params=None):
        self.statements.append((statement, params))
        if self.fail:
            raise RuntimeError("PERMISSION_DENIED")
        if callable(self.rows):
            return self.rows(statement, params)
        return self.rows

    def columns(self, statement):
        self.statements.append((statement, {}))
        if self.fail:
            raise RuntimeError("VERSION_NOT_AVAILABLE")
        return self.cols[statement.split("VERSION AS OF ")[1].split()[0]]


def test_quote_table_accepts_three_part_and_rejects_injection():
    assert quote_table("a.b.c") == "`a`.`b`.`c`"
    for bad in ["a.b", "a.b.c; DROP TABLE x", "a.b.c`", "a b.c.d"]:
        with pytest.raises(ValueError):
            quote_table(bad)


def test_every_system_table_query_filters_by_workspace():
    sql = FakeSql()
    get_run_history_system(sql, "123", 1, "t")
    get_job_changes(sql, "123", 1)
    get_permission_changes(sql, "123", "a.b.c")
    get_lineage(sql, "123", 1)
    get_upstream_run_status(sql, "123", ["a.b.c"])
    assert sql.statements
    for statement, params in sql.statements:
        assert "workspace_id = :workspace_id" in statement and params["workspace_id"] == "123"


def test_diff_versions_reports_changed_fields():
    v = [{"change_time": 1, "timeout_seconds": 0, "run_as": "a"},
         {"change_time": 2, "timeout_seconds": 1, "run_as": "a"}]
    out = diff_versions(v, ["timeout_seconds", "run_as"])
    assert out == [{"change_time": "2", "changed": {"timeout_seconds": {"from": 0, "to": 1}}}]


def test_diff_schemas_added_dropped_retyped():
    old = [("id", "int"), ("amount", "decimal(10,2)"), ("country", "string")]
    new = [("id", "int"), ("amount", "string"), ("region", "string")]
    d = diff_schemas(old, new)
    assert d == {"added": ["region"], "dropped": ["country"],
                 "retyped": [{"column": "amount", "from": "decimal(10,2)", "to": "string"}]}


def test_compare_table_schema_uses_limit_zero_time_travel():
    sql = FakeSql()
    sql.cols = {"3": [("a", "int"), ("b", "int")], "4": [("a", "int")]}
    r = compare_table_schema(sql, "c.s.t", 3, 4)
    assert r.data["dropped"] == ["b"]
    assert len(sql.statements) == 2
    assert all("LIMIT 0" in s and "VERSION AS OF" in s for s, _ in sql.statements)


def test_compare_schema_old_version_unavailable():
    r = compare_table_schema(FakeSql(fail=True), "c.s.t", 1, 2)
    assert not r.ok and "VERSION_NOT_AVAILABLE" in r.unavailable_reason


def test_table_history_flags_schema_ops_and_row_counts():
    rows = [{"version": 5, "timestamp": "t", "userName": "u", "operation": "DROP COLUMNS",
             "operationParameters": {"columns": "amount"}, "operationMetrics": {}},
            {"version": 4, "timestamp": "t0", "userName": "u", "operation": "WRITE",
             "operationParameters": {}, "operationMetrics": {"numOutputRows": "0"}}]
    r = get_table_history(FakeSql(rows), "c.s.t")
    h = r.data["history"]
    assert h[0]["schema_change"] and not h[1]["schema_change"] and h[1]["rows_written"] == "0"


def test_tool_failure_becomes_unavailable():
    r = get_table_history(FakeSql(fail=True), "c.s.t")
    assert not r.ok and "PERMISSION_DENIED" in r.unavailable_reason


def test_lineage_collects_inputs_outputs_downstream():
    def rows(statement, params):
        if "DISTINCT source_table_full_name AS source" in statement:
            return [{"source": "c.raw.orders", "target": "c.silver.sales"}]
        return [{"asset": "c.gold.report", "entity_type": "JOB", "entity_id": "9"}]
    r = get_lineage(FakeSql(rows), "1", 7)
    assert r.data["inputs"] == ["c.raw.orders"] and r.data["outputs"] == ["c.silver.sales"]
    assert r.data["downstream"][0]["asset"] == "c.gold.report"
