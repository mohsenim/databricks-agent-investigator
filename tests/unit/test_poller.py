from incident_agent.poller import failed_targets, select_new


def run(run_id, life="TERMINATED", result="FAILED", tasks=None, end=1):
    return {"job_id": 7, "run_id": run_id, "run_name": "demo", "end_time": end,
            "state": {"life_cycle_state": life, "result_state": result}, "tasks": tasks or []}


def task(key, result, rid=100):
    return {"task_key": key, "run_id": rid,
            "state": {"life_cycle_state": "TERMINATED", "result_state": result}}


def test_running_run_ignored():
    assert failed_targets(run(1, life="RUNNING", result=None)) == []


def test_failed_task_found():
    t = failed_targets(run(1, tasks=[task("a", "SUCCESS"), task("b", "FAILED", 55)]))
    assert [(x.key.task_key, x.task_run_id) for x in t] == [("b", 55)]


def test_upstream_failed_is_not_a_root_failure():
    t = failed_targets(run(1, tasks=[task("a", "FAILED"), task("b", "UPSTREAM_FAILED")]))
    assert [x.key.task_key for x in t] == ["a"]


def test_succeeded_with_failures_is_found_via_task_state():
    r = run(1, result="SUCCESS_WITH_FAILURES", tasks=[task("a", "FAILED"), task("h", "SUCCESS")])
    assert [x.key.task_key for x in failed_targets(r)] == ["a"]


def test_run_that_failed_without_tasks_gets_run_level_target():
    t = failed_targets(run(1, result="FAILED", tasks=[]))
    assert len(t) == 1 and t[0].key.task_key == "" and t[0].task_run_id is None


def test_successful_run_has_no_target():
    assert failed_targets(run(1, result="SUCCESS", tasks=[task("a", "SUCCESS")])) == []


def test_known_keys_skipped_and_limit_applied():
    runs = [run(1, tasks=[task("a", "FAILED")], end=1),
            run(2, tasks=[task("a", "FAILED")], end=2),
            run(3, tasks=[task("a", "FAILED")], end=3)]
    got = select_new(runs, known={(7, 1, "a")}, limit=1)
    assert [t.key.run_id for t in got] == [2]


# poll_once

from incident_agent.config import Config
from incident_agent.poller import poll_once


class PollSql:
    def __init__(self, settings=None, known=None, jobs=(7,)):
        self.settings, self.known, self.jobs, self.writes = settings or [], known or [], jobs, []

    def run(self, statement, params=None):
        if "agent_settings" in statement:
            return self.settings
        if "monitored_jobs" in statement:
            return [{"job_id": j} for j in self.jobs]
        if "FROM" in statement and "incidents" in statement:
            return self.known
        self.writes.append(statement)
        return []


class PollJobs:
    def __init__(self, runs):
        self.runs = runs
        self.asked = []

    def list_runs(self, job_id, since, limit=25):
        self.asked.append((job_id, since))
        return self.runs


class Starter:
    def __init__(self):
        self.started = []

    def start_investigation(self, job_id, params):
        self.started.append((job_id, params))
        return 1


CFG = Config(investigator_job_id="42")


def test_poll_starts_investigation_for_new_failure_only():
    runs = [run(1, tasks=[task("a", "FAILED", 55)]), run(2, tasks=[task("a", "FAILED", 66)])]
    sql = PollSql(known=[{"job_id": 7, "run_id": 1, "task_key": "a"}])
    st = Starter()
    out = poll_once(PollJobs(runs), st, sql, CFG, now_ms=10_000_000_000)
    assert [t.key.run_id for t in out] == [2]
    job_id, params = st.started[0]
    assert job_id == 42 and params["run_id"] == "2" and params["task_run_id"] == "66"
    assert params["trigger_source"] == "poll"
    assert any("INSERT INTO" in w for w in sql.writes)


def test_poll_respects_cap_and_disabled_flag():
    runs = [run(i, tasks=[task("a", "FAILED")], end=i) for i in range(1, 9)]
    st = Starter()
    poll_once(PollJobs(runs), st, PollSql(settings=[{"key": "max_per_cycle", "value": "2"}]), CFG)
    assert len(st.started) == 2
    st2 = Starter()
    poll_once(PollJobs(runs), st2, PollSql(settings=[{"key": "poller_enabled", "value": "false"}]), CFG)
    assert st2.started == []


def test_poll_lookback_comes_from_settings():
    jobs = PollJobs([])
    sql = PollSql(settings=[{"key": "lookback_hours", "value": "24"}])
    poll_once(jobs, Starter(), sql, CFG, now_ms=100 * 3_600_000)
    assert jobs.asked == [(7, 76 * 3_600_000)]


def test_model_override_is_read_from_settings():
    from incident_agent.poller import read_model_override

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def run(self, statement, params=None):
            return self.rows

    cfg = Config()
    assert read_model_override(Rows([{"value": " ep-2 "}]), cfg) == "ep-2"
    assert read_model_override(Rows([]), cfg) == ""

    class Broken:
        def run(self, statement, params=None):
            raise RuntimeError("no table")

    assert read_model_override(Broken(), cfg) == ""


def test_poller_never_investigates_its_own_investigator_job():
    jobs = PollJobs([run(1, tasks=[task("a", "FAILED")])])
    st = Starter()
    out = poll_once(jobs, st, PollSql(jobs=(42,)), CFG)
    assert out == [] and st.started == [] and jobs.asked == []
