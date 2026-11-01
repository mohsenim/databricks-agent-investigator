from incident_agent.tools.base import ToolResult
from incident_agent.tools.jobs_tools import (
    failing_line,
    get_failed_run,
    get_run_history_api,
    get_task_error,
    get_task_source,
)

SENTINEL = "SENTINEL_9876543210"


class FakeJobs:
    def get_run(self, run_id):
        return {"job_id": 1, "run_id": run_id, "run_name": "demo", "state": {"result_state": "FAILED"},
                "tasks": [{"task_key": "transform", "run_id": 11,
                           "state": {"result_state": "FAILED", "life_cycle_state": "TERMINATED",
                                     "state_message": f"failed for customer {SENTINEL}"},
                           "notebook_task": {"notebook_path": "/demo/transform"},
                           "job_cluster_key": None}]}

    def get_run_output(self, task_run_id):
        if task_run_id == 99:
            raise RuntimeError("Retrieving the output of runs with multiple tasks is not supported")
        return {"error": "AnalysisException: cannot resolve 'amount'",
                "error_trace": f"File notebook, line 4\n  value {SENTINEL}",
                "notebook_output": {"result": f"row {SENTINEL}"}}

    def list_runs(self, job_id, start_time_from_ms, limit=25):
        return [{"run_id": 1, "tasks": [{"task_key": "transform", "state": {"result_state": "SUCCESS"}}]},
                {"run_id": 2, "tasks": [{"task_key": "other", "state": {"result_state": "FAILED"}}]}]

    def export_notebook(self, path):
        if path == "/missing":
            raise FileNotFoundError(path)
        body = ["import x"] * 3 + ["df = spark.table('agent_demo.raw.orders')", "df.select('amount')"]
        return "\n".join(body)

    def get_job(self, job_id):
        return {}


def test_failed_run_summary_is_redacted():
    r = get_failed_run(FakeJobs(), 5)
    assert r.ok and SENTINEL not in r.model_dump_json()
    assert r.data["tasks"][0]["task_key"] == "transform"


def test_task_error_drops_notebook_output_and_redacts():
    r = get_task_error(FakeJobs(), 11)
    assert SENTINEL not in r.model_dump_json()
    assert "cannot resolve" in r.data["error"] and "notebook_output" not in r.data


def test_multitask_parent_id_gives_unavailable_not_exception():
    r = get_task_error(FakeJobs(), 99)
    assert isinstance(r, ToolResult) and not r.ok and "multiple tasks" in r.unavailable_reason


def test_failing_line_takes_innermost_frame():
    assert failing_line("File a, line 10\nFile b, line 42") == 42
    assert failing_line("no numbers") is None


def test_task_source_excerpt_and_tables():
    r = get_task_source(FakeJobs(), "/demo/transform", "File x, line 5", context_lines=2)
    assert "5: df.select('amount')" in r.data["excerpt"]
    assert r.data["tables_referenced"] == ["agent_demo.raw.orders"]


def test_task_source_unavailable():
    assert not get_task_source(FakeJobs(), "/missing").ok
    assert not get_task_source(FakeJobs(), "").ok


def test_run_history_filters_by_task():
    r = get_run_history_api(FakeJobs(), 1, "transform", 0)
    assert r.data["history"] == [{"run_id": 1, "result_state": "SUCCESS",
                                  "start_time": None, "end_time": None}]


def test_tables_referenced_ignores_attribute_chains():
    from incident_agent.tools.jobs_tools import _TABLE_RE

    src = ('catalog = dbutils.widgets.get("c")\nsales.write.mode("overwrite")\n'
           'spark.table("a.raw.orders")\nspark.sql("SELECT x FROM b.silver.t JOIN c.gold.u ON 1=1")')
    assert sorted(set(_TABLE_RE.findall(src))) == ["a.raw.orders", "b.silver.t", "c.gold.u"]


def test_parameterised_table_names_are_resolved():
    from incident_agent.tools.jobs_tools import resolve_parameterised_tables

    src = ('dbutils.widgets.text("catalog", "agent_demo")\ncatalog = dbutils.widgets.get("catalog")\n'
           'orders = spark.table(f"{catalog}.raw.orders")\n')
    assert resolve_parameterised_tables(src, None) == ["agent_demo.raw.orders"]
    assert resolve_parameterised_tables(src, {"catalog": "prod"}) == ["prod.raw.orders"]
    assert resolve_parameterised_tables('x = f"{other}.a.b"', None) == []


def test_written_tables_are_not_inputs():
    from incident_agent.tools.jobs_tools import split_tables

    src = ('dbutils.widgets.text("catalog", "agent_demo")\ncatalog = dbutils.widgets.get("catalog")\n'
           'orders = spark.table(f"{catalog}.raw.orders")\n'
           'sales.write.mode("overwrite").saveAsTable(f"{catalog}.silver.sales")\n')
    assert split_tables(src, None) == (["agent_demo.raw.orders"], ["agent_demo.silver.sales"])
