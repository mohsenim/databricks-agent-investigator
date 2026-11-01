from test_collect import TARGET, Jobs, Sql, drop_column_history

from incident_agent.config import Config
from incident_agent.investigate import Deps, TemplateNarrator, investigate
from incident_agent.models import IncidentKey
from incident_agent.poller import Target
from incident_agent.store import Store
from incident_agent.taxonomy import Confidence, FailureClass, Status


class RecordingSql(Sql):
    def __init__(self, *a, claim_ok=True, **k):
        super().__init__(*a, **k)
        self.claim_ok, self.writes, self.token = claim_ok, [], None

    def run(self, statement, params=None):
        s = statement.lstrip()
        if s.startswith("MERGE INTO"):
            self.token = params["token"]
            return []
        if s.startswith("SELECT claim_token"):
            return [{"claim_token": self.token if self.claim_ok else "other"}]
        if s.startswith("UPDATE"):
            self.writes.append(params)
            return []
        return super().run(statement, params)


def deps(sql, tmp_path, narrator=None):
    cfg = Config(workspace_id="1")
    return Deps(jobs=Jobs("[UNRESOLVED_COLUMN] A column with name `amount` cannot be resolved"),
                sql=sql, store=Store(sql, cfg), reports_dir=tmp_path, config=cfg,
                narrator=narrator or TemplateNarrator())


def make_sql(**kw):
    return RecordingSql(drop_column_history(), [("id", "int"), ("amount", "int")], [("id", "int")],
                        producers=[{"job_id": "9"}], **kw)


def test_end_to_end_without_model(tmp_path):
    sql = make_sql()
    inc = investigate(deps(sql, tmp_path), TARGET, "push")
    assert inc.status is Status.DONE
    assert inc.root_cause_class is FailureClass.SCHEMA_CHANGE and inc.confidence is Confidence.HIGH
    assert sql.writes and sql.writes[0]["status"] == "done"
    report = (tmp_path / "1" / "2_transform.md").read_text()
    assert "upstream_schema_change" in report and "[E" in report


def test_second_trigger_for_same_failure_does_nothing(tmp_path):
    sql = make_sql(claim_ok=False)
    assert investigate(deps(sql, tmp_path), TARGET, "poll") is None
    assert sql.writes == []


def test_bad_citation_from_model_falls_back_to_template(tmp_path):
    class BadNarrator:
        def narrate(self, incident, bundle, result):
            return {"root_cause": "It is the schema [E99].", "rejected": "", "recommendation": ""}
    inc = investigate(deps(make_sql(), tmp_path, BadNarrator()), TARGET, "push")
    assert inc.status is Status.DONE and "E99" not in inc.summary


def test_crash_leaves_error_row(tmp_path):
    class Boom:
        def narrate(self, *a):
            raise RuntimeError("model endpoint down")
    # the template fallback only covers validation failures, not crashes
    sql = make_sql()
    inc = investigate(deps(sql, tmp_path, Boom()), TARGET, "push")
    assert inc.status is Status.ERROR and "model endpoint down" in inc.summary
    assert sql.writes[0]["status"] == "error"


def test_run_level_failure_without_task(tmp_path):
    class NoTaskJobs(Jobs):
        def get_run(self, run_id):
            return {"job_id": 1, "run_id": run_id, "state": {
                "result_state": "FAILED", "state_message": "node type unavailable"},
                "status": {"termination_details": {"code": "INVALID_CLUSTER_REQUEST"}},
                "tasks": []}
    sql = RecordingSql([])
    d = deps(sql, tmp_path)
    d.jobs = NoTaskJobs("")
    t = Target(IncidentKey(job_id=1, run_id=3, task_key=""), None, "demo")
    inc = investigate(d, t, "poll")
    assert inc.status is Status.DONE
    assert inc.root_cause_class is FailureClass.CLUSTER_INFRA
    assert (tmp_path / "1" / "3_run.md").exists()
