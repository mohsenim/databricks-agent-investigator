import re

from test_collect import TARGET, Jobs, drop_column_history
from test_investigate import RecordingSql

from incident_agent.config import Config
from incident_agent.graph import CheckRequest, HypothesisList, LlmNarrator, Narrative
from incident_agent.investigate import Deps, investigate
from incident_agent.models import Hypothesis, Polarity
from incident_agent.store import Store
from incident_agent.taxonomy import FailureClass, Status


class FakeModel:
    def __init__(self, checks=(), narrative=None):
        self.checks = list(checks)
        self.usage = {"input": 10, "output": 5}
        self.calls = 0
        self.narrative = narrative

    def structured(self, schema, system, user):
        assert "untrusted" in system and "<data>" in user
        if schema is HypothesisList:
            return HypothesisList(hypotheses=[Hypothesis(
                failure_class=FailureClass.SCHEMA_CHANGE, rationale="column gone")])
        if self.narrative:
            return self.narrative
        ids = re.findall(r"\bE\d+\b", user)[:1]
        cite = f"[{ids[0]}]" if ids else ""
        return Narrative(root_cause=f"A column was dropped upstream {cite}", rejected="None",
                         recommendation="Restore the column")

    def request_check(self, system, user, tool_names):
        self.calls += 1
        return self.checks.pop(0) if self.checks else None


def make(tmp_path, model):
    sql = RecordingSql(drop_column_history(), [("id", "int"), ("amount", "int")], [("id", "int")],
                       producers=[{"job_id": "9"}])
    cfg = Config(workspace_id="1")
    jobs = Jobs("[UNRESOLVED_COLUMN] A column with name `amount` cannot be resolved")
    narrator = LlmNarrator(cfg, model=model).bind(jobs, sql)
    deps = Deps(jobs=jobs, sql=sql, store=Store(sql, cfg), reports_dir=tmp_path, config=cfg,
                narrator=narrator)
    return deps, sql


def test_graph_produces_narrative_and_report(tmp_path):
    deps, _ = make(tmp_path, FakeModel())
    inc = investigate(deps, TARGET, "push")
    assert inc.status is Status.DONE and "dropped upstream" in inc.summary
    assert inc.input_tokens == 10 and inc.output_tokens == 5
    assert (tmp_path / "1" / "2_transform.md").exists()


def test_model_check_is_run_and_added_as_second_source_only(tmp_path):
    req = CheckRequest(tool="get_table_history", arguments={"table": "agent_demo.raw.orders"},
                       failure_class=FailureClass.SCHEMA_CHANGE, polarity=Polarity.SUPPORTS)
    deps, _ = make(tmp_path, FakeModel(checks=[req]))
    inc = investigate(deps, TARGET, "push")
    added = [e for e in inc.evidence if e.arguments == {"table": "agent_demo.raw.orders"}
             and e.feature.value == "independent_second_source"]
    assert added and inc.status is Status.DONE


def test_disallowed_tool_ends_loop_without_running(tmp_path):
    req = CheckRequest(tool="drop_table", arguments={"table": "x.y.z"},
                       failure_class=FailureClass.SCHEMA_CHANGE, polarity=Polarity.SUPPORTS)
    model = FakeModel(checks=[req])
    deps, sql = make(tmp_path, model)
    inc = investigate(deps, TARGET, "push")
    assert inc.status is Status.DONE
    assert not any("drop_table" in s for s, _ in getattr(sql, "statements", []))


def test_iteration_budget_is_enforced(tmp_path):
    req = CheckRequest(tool="get_table_history", arguments={"table": "agent_demo.raw.orders"},
                       failure_class=FailureClass.SCHEMA_CHANGE, polarity=Polarity.SUPPORTS)
    model = FakeModel(checks=[req] * 50)
    deps, _ = make(tmp_path, model)
    deps.config = Config(workspace_id="1", max_iterations=3)
    deps.narrator.cfg = deps.config
    investigate(deps, TARGET, "push")
    assert model.calls == 3


def test_bad_arguments_from_model_do_not_crash(tmp_path):
    req = CheckRequest(tool="compare_table_schema", arguments={"table": "agent_demo.raw.orders"},
                       failure_class=FailureClass.SCHEMA_CHANGE, polarity=Polarity.SUPPORTS)
    deps, _ = make(tmp_path, FakeModel(checks=[req]))
    assert investigate(deps, TARGET, "push").status is Status.DONE


def test_fabricated_citation_falls_back_to_template(tmp_path):
    bad = Narrative(root_cause="Because of [E99].", rejected="", recommendation="")
    deps, _ = make(tmp_path, FakeModel(narrative=bad))
    inc = investigate(deps, TARGET, "push")
    assert inc.status is Status.DONE and "E99" not in inc.summary


class FakeClient:
    """Stands in for the OpenAI-compatible client returned by the SDK."""

    def __init__(self, content="", tool_args=None):
        self.content, self.tool_args, self.calls = content, tool_args, []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        from types import SimpleNamespace as NS

        self.calls.append(kwargs)
        calls = [NS(function=NS(arguments=self.tool_args))] if self.tool_args else None
        msg = NS(content=self.content, tool_calls=calls)
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=7, completion_tokens=3))


def test_databricks_chat_model_parses_json_with_surrounding_text():
    from incident_agent.graph import DatabricksChatModel

    client = FakeClient('Sure:\n{"root_cause": "r", "rejected": "x", "recommendation": "y"}\nDone')
    model = DatabricksChatModel(Config(model_endpoint="ep"), client=client)
    out = model.structured(Narrative, "system", "user")
    assert out.root_cause == "r"
    assert model.usage == {"input": 7, "output": 3}
    assert client.calls[0]["model"] == "ep"


def test_databricks_chat_model_request_check_and_no_call():
    from incident_agent.graph import DatabricksChatModel

    args = ('{"tool": "get_table_history", "arguments": {"table": "a.b.c"},'
            ' "failure_class": "upstream_schema_change", "polarity": "supports"}')
    model = DatabricksChatModel(Config(model_endpoint="ep"), client=FakeClient(tool_args=args))
    req = model.request_check("s", "u", ["get_table_history"])
    assert req is not None and req.tool == "get_table_history"
    assert DatabricksChatModel(Config(model_endpoint="ep"), client=FakeClient("x")).request_check(
        "s", "u", []) is None


class BrokenModel(FakeModel):
    """Raises errors like a model that gives malformed output."""

    def request_check(self, system, user, tool_names):
        raise ValueError("polarity: Input should be 'supports' or 'contradicts'")

    def structured(self, schema, system, user):
        raise ValueError("no JSON object")


def test_model_failures_fall_back_to_the_deterministic_report(tmp_path):
    deps, _ = make(tmp_path, BrokenModel())
    out = investigate(deps, TARGET, "push")
    assert out.status is not Status.ERROR
    assert out.root_cause_class is FailureClass.SCHEMA_CHANGE
    assert out.report_path
