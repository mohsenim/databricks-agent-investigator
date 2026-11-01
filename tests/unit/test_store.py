from incident_agent.models import IncidentKey
from incident_agent.store import Store


class FakeSql:
    """Records the statements. Returns the claim token it was given, or a different one."""

    def __init__(self, steal=False):
        self.calls = []
        self.token = None
        self.steal = steal

    def run(self, statement, params=None):
        self.calls.append((statement, params))
        if statement.lstrip().startswith("MERGE"):
            self.token = params["token"]
            return []
        if "claim_token" in statement:
            return [{"claim_token": "other" if self.steal else self.token}]
        return []


KEY = IncidentKey(job_id=1, run_id=2, task_key="t")


def test_claim_succeeds_when_token_matches():
    assert Store(FakeSql()).claim(KEY, "push") is True


def test_claim_fails_when_other_investigation_holds_row():
    assert Store(FakeSql(steal=True)).claim(KEY, "poll") is False


def test_claim_is_parameterised():
    sql = FakeSql()
    Store(sql).claim(KEY, "push")
    for statement, params in sql.calls:
        assert "t'" not in statement and params["task_key"] == "t"
