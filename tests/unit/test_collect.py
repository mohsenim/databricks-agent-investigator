from datetime import UTC, datetime, timedelta

from incident_agent.collect import collect
from incident_agent.models import IncidentKey
from incident_agent.poller import Target
from incident_agent.scoring import score
from incident_agent.taxonomy import Confidence, FailureClass

NOW = datetime.now(UTC)
FAIL_MS = int(NOW.timestamp() * 1000)
TS = lambda minutes_ago: str(NOW - timedelta(minutes=minutes_ago))


class Jobs:
    def __init__(self, error, code="RUN_EXECUTION_ERROR", state="FAILED", history_ok=True):
        self.error, self.code, self.state, self.history_ok = error, code, state, history_ok

    def get_run(self, run_id):
        return {"job_id": 1, "run_id": run_id, "run_name": "demo", "state": {"result_state": "FAILED"},
                "tasks": [{"task_key": "transform", "run_id": 11, "end_time": FAIL_MS,
                           "state": {"result_state": self.state, "life_cycle_state": "TERMINATED"},
                           "status": {"termination_details": {"code": self.code}},
                           "notebook_task": {"notebook_path": "/demo/transform"}}]}

    def get_run_output(self, task_run_id):
        return {"error": self.error, "error_trace": "File nb, line 2"}

    def list_runs(self, job_id, start_time_from_ms, limit=25):
        if not self.history_ok:
            return []
        return [{"run_id": 5, "tasks": [{"task_key": "transform", "state": {"result_state": "SUCCESS"}}]}]

    def export_notebook(self, path):
        return "df = spark.table('agent_demo.raw.orders')\ndf.select('amount')"

    def get_job(self, job_id):
        return {}


class Sql:
    def __init__(self, history, cols_old=None, cols_new=None, perm=None, producers=None):
        self.history, self.perm = history, perm or []
        self.cols = {"1": cols_old or [], "2": cols_new or []}
        self.producers = producers or []

    def run(self, statement, params=None):
        if statement.startswith("DESCRIBE HISTORY"):
            return self.history
        if "FROM system.access.audit" in statement:
            return self.perm
        if "AS source, target_table_full_name AS target" in statement:
            return [{"source": "agent_demo.raw.orders", "target": "agent_demo.silver.sales"}]
        if "AS asset" in statement:
            return [{"asset": "agent_demo.gold.report", "entity_type": "JOB", "entity_id": "9"}]
        if "FROM system.access.table_lineage" in statement:
            return self.producers
        if "FROM system.lakeflow.job_run_timeline" in statement:
            return [{"run_id": 3, "result_state": "FAILED", "termination_code": "X",
                     "period_end_time": TS(30)}]
        return []

    def columns(self, statement):
        return self.cols[statement.split("VERSION AS OF ")[1].split()[0]]


TARGET = Target(IncidentKey(job_id=1, run_id=2, task_key="transform"), 11, "demo")


def drop_column_history():
    return [{"version": 2, "timestamp": TS(30), "userName": "u", "operation": "DROP COLUMNS",
             "operationParameters": {}, "operationMetrics": {}},
            {"version": 1, "timestamp": TS(600), "userName": "u", "operation": "WRITE",
             "operationParameters": {}, "operationMetrics": {"numOutputRows": "1000"}}]


def test_dropped_column_scenario_reaches_high_confidence():
    jobs = Jobs("[UNRESOLVED_COLUMN] A column with name `amount` cannot be resolved")
    sql = Sql(drop_column_history(), [("id", "int"), ("amount", "decimal(10,2)")], [("id", "int")],
              producers=[{"job_id": "9"}])
    b = collect(jobs, sql, TARGET, "1")
    r = score(b.evidence, b.missing_key_tool)
    assert r.failure_class is FailureClass.SCHEMA_CHANGE
    assert r.confidence is Confidence.HIGH
    assert {"time_correlation_with_change", "independent_second_source"} <= set(r.features)
    assert any(x["asset"] == "agent_demo.gold.report" for x in b.blast_radius)


def test_missing_column_without_upstream_change_is_code_error():
    jobs = Jobs("[UNRESOLVED_COLUMN] A column with name `amount` cannot be resolved")
    b = collect(jobs, Sql([{"version": 1, "timestamp": TS(900), "userName": "u",
                            "operation": "WRITE", "operationParameters": {},
                            "operationMetrics": {"numOutputRows": "5"}}]), TARGET, "1")
    assert score(b.evidence).failure_class is FailureClass.CODE_OR_CONFIG


def test_table_history_unavailable_caps_confidence():
    class NoHistory(Sql):
        def run(self, statement, params=None):
            if statement.startswith("DESCRIBE HISTORY"):
                raise RuntimeError("PERMISSION_DENIED")
            return super().run(statement, params)
    jobs = Jobs("[UNRESOLVED_COLUMN] A column with name `amount` cannot be resolved")
    b = collect(jobs, NoHistory([]), TARGET, "1")
    assert b.missing_key_tool and any("get_table_history" in u for u in b.unavailable)
    assert score(b.evidence, b.missing_key_tool).confidence is not Confidence.HIGH


def test_library_failure_from_termination_code():
    jobs = Jobs("ERROR: No matching distribution found for nopkg==9", code="LIBRARY_INSTALLATION_ERROR")
    b = collect(jobs, Sql([]), TARGET, "1")
    assert score(b.evidence).failure_class is FailureClass.LIBRARY


def test_empty_upstream_write_is_data_problem():
    hist = [{"version": 3, "timestamp": TS(20), "userName": "u", "operation": "WRITE",
             "operationParameters": {}, "operationMetrics": {"numOutputRows": "0"}}]
    jobs = Jobs("ValueError: no rows to aggregate")
    b = collect(jobs, Sql(hist), TARGET, "1")
    classes = {e.failure_class for e in b.evidence}
    assert FailureClass.DATA_PROBLEM in classes


def test_evidence_ids_are_unique_and_sequential():
    jobs = Jobs("[UNRESOLVED_COLUMN] A column with name `amount` cannot be resolved")
    b = collect(jobs, Sql(drop_column_history(), [("amount", "int")], []), TARGET, "1")
    assert [e.id for e in b.evidence] == [f"E{i}" for i in range(1, len(b.evidence) + 1)]


def test_unknown_inputs_do_not_conclude_that_nothing_changed():
    from incident_agent.collect import Bundle, _finalize_codeerror
    from incident_agent.models import Feature
    from incident_agent.taxonomy import FailureClass

    b = Bundle()
    b.add("get_task_error", {}, "Error text shows a column the code reads is missing.",
          FailureClass.SCHEMA_CHANGE, Feature.DIRECT_ERROR_MATCH, "jobs_api")
    _finalize_codeerror(b)
    assert len(b.evidence) == 1 and b.missing_key_tool
    assert any("could not be determined" in u for u in b.unavailable)
