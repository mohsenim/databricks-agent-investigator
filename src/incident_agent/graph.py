"""Agent loop on LangGraph. The model is called in three steps, and the confidence
is computed in plain code.

hypothesize -> investigate (limited tool loop) -> rescore -> narrate
"""
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from . import prompts
from .collect import Bundle
from .config import Config
from .models import EvidenceItem, Feature, Hypothesis, Incident, Polarity
from .scoring import ScoreResult, score
from .taxonomy import FailureClass
from .tools import sql_tools
from .tools.base import JobsApi, SqlApi


class HypothesisList(BaseModel):
    hypotheses: list[Hypothesis]


class Narrative(BaseModel):
    root_cause: str
    rejected: str
    recommendation: str


class CheckRequest(BaseModel):
    tool: str
    arguments: dict[str, Any]
    failure_class: FailureClass
    polarity: Polarity


class ChatModel(Protocol):
    """What the graph needs from a model. Implemented below on top of the serving API."""

    def structured(self, schema: type[BaseModel], system: str, user: str) -> BaseModel: ...
    def request_check(self, system: str, user: str, tool_names: list[str]) -> CheckRequest | None: ...
    usage: dict[str, int]


@dataclass
class Runtime:
    model: ChatModel
    jobs: JobsApi
    sql: SqlApi
    bundle: Bundle
    incident: Incident
    result: ScoreResult
    cfg: Config
    calls_used: int = 0


class State(TypedDict, total=False):
    hypotheses: list[Hypothesis]
    iterations: int
    done: bool
    narrative: dict[str, str]


ALLOWED_TOOLS = ("get_table_history", "compare_table_schema", "get_permission_changes",
                 "get_task_source")


def _evidence_text(evidence: list[EvidenceItem]) -> str:
    return "\n".join(f"{e.id} [{e.failure_class.value}, {e.polarity.value}] {e.summary}"
                     for e in evidence) or "(none)"


def build_graph(rt: Runtime):
    def hypothesize(state: State) -> State:
        try:
            out = rt.model.structured(
                HypothesisList, prompts.SYSTEM,
                prompts.HYPOTHESIZE.format(evidence=_evidence_text(rt.bundle.evidence),
                                           unavailable="; ".join(rt.bundle.unavailable) or "none"))
            hypotheses = out.hypotheses  # type: ignore[attr-defined]
        except Exception as exc:
            rt.bundle.unavailable.append(f"model hypotheses: {_brief(exc)}")
            hypotheses = []
        return {"hypotheses": hypotheses, "iterations": 0, "done": False}

    def investigate(state: State) -> State:
        n = state.get("iterations", 0)
        if n >= rt.cfg.max_iterations or rt.calls_used >= rt.cfg.max_tool_calls:
            return {"done": True}
        try:
            req = rt.model.request_check(
                prompts.SYSTEM,
                prompts.CHOOSE_CHECK.format(best=rt.result.failure_class.value,
                                            evidence=_evidence_text(rt.bundle.evidence)),
                list(ALLOWED_TOOLS))
        except Exception as exc:  # a bad reply ends the check loop, not the whole investigation
            rt.bundle.unavailable.append(f"model check request: {_brief(exc)}")
            return {"done": True}
        if req is None or req.tool not in ALLOWED_TOOLS:
            return {"done": True}
        rt.calls_used += 1
        result = _run_check(rt, req)
        rt.bundle.record(result)
        if result.ok:
            # Checks that the model asks for only count as a second source.
            # They cannot add weight for a direct error match or a time correlation.
            rt.bundle.add(req.tool, req.arguments, _summarize(result.data), req.failure_class,
                          Feature.SECOND_SOURCE, result.source or req.tool, req.polarity)
        rt.result = score(rt.bundle.evidence, rt.bundle.missing_key_tool)
        return {"iterations": n + 1, "done": False}

    def after_investigate(state: State) -> str:
        return "narrate" if state.get("done") else "investigate"

    def narrate(state: State) -> State:
        rt.incident.evidence = rt.bundle.evidence
        try:
            out = rt.model.structured(
                Narrative, prompts.SYSTEM,
                prompts.NARRATE.format(cls=rt.result.failure_class.value,
                                       conf=rt.result.confidence.value,
                                       evidence=_evidence_text(rt.bundle.evidence)))
            return {"narrative": out.model_dump()}  # type: ignore[attr-defined]
        except Exception as exc:
            from .investigate import TemplateNarrator

            rt.bundle.unavailable.append(f"model narrative: {_brief(exc)}")
            return {"narrative": TemplateNarrator().narrate(rt.incident, rt.bundle, rt.result)}

    g = StateGraph(State)
    g.add_node("hypothesize", hypothesize)
    g.add_node("investigate", investigate)
    g.add_node("narrate", narrate)
    g.set_entry_point("hypothesize")
    g.add_edge("hypothesize", "investigate")
    g.add_conditional_edges("investigate", after_investigate,
                            {"investigate": "investigate", "narrate": "narrate"})
    g.add_edge("narrate", END)
    return g.compile()


def _brief(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"[:160]


def _run_check(rt: Runtime, req: CheckRequest):
    a = req.arguments
    try:
        if req.tool == "get_table_history":
            return sql_tools.get_table_history(rt.sql, str(a["table"]))
        if req.tool == "compare_table_schema":
            return sql_tools.compare_table_schema(rt.sql, str(a["table"]), int(a["old_version"]),
                                                  int(a["new_version"]))
        if req.tool == "get_permission_changes":
            return sql_tools.get_permission_changes(rt.sql, rt.cfg.workspace_id, str(a["table"]))
        from .tools import jobs_tools

        return jobs_tools.get_task_source(rt.jobs, str(a["notebook_path"]), rt.bundle.error_text)
    except (KeyError, ValueError, TypeError) as exc:  # the model gave bad arguments
        from .tools.base import ToolResult

        return ToolResult.unavailable(req.tool, f"invalid arguments: {exc}")


def _summarize(data: dict[str, Any]) -> str:
    from .redaction import redact

    return redact(json.dumps(data, default=str), 400)


@dataclass
class LlmNarrator:
    """Uses the graph to write the report.

    `model` defaults to DatabricksChatModel on cfg.model_endpoint.
    """

    cfg: Config
    model: ChatModel | None = None
    jobs: JobsApi | None = None
    sql: SqlApi | None = None
    last_state: dict[str, Any] = field(default_factory=dict)

    def bind(self, jobs: JobsApi, sql: SqlApi) -> "LlmNarrator":
        self.jobs, self.sql = jobs, sql
        return self

    def narrate(self, incident: Incident, bundle: Bundle, result: ScoreResult) -> dict[str, str]:
        model = self.model or DatabricksChatModel(self.cfg)
        rt = Runtime(model, self.jobs, self.sql, bundle, incident, result, self.cfg)  # type: ignore[arg-type]
        state = build_graph(rt).invoke({})
        self.last_state = dict(state)
        # The score may have changed during the investigation.
        incident.root_cause_class = rt.result.failure_class
        incident.confidence = rt.result.confidence
        incident.confidence_features = rt.result.features
        incident.status = rt.result.status
        incident.evidence = bundle.evidence
        incident.input_tokens = model.usage.get("input", 0)
        incident.output_tokens = model.usage.get("output", 0)
        return state["narrative"]


class DatabricksChatModel:
    """ChatModel on the workspace's OpenAI-compatible serving API (tool calling and JSON output)."""

    def __init__(self, cfg: Config, client: Any = None):
        if client is None:
            from databricks.sdk import WorkspaceClient

            client = WorkspaceClient().serving_endpoints.get_open_ai_client()
        self.client = client
        self.endpoint = cfg.model_endpoint
        self.usage = {"input": 0, "output": 0}

    def _complete(self, system: str, user: str, **kwargs: Any) -> Any:
        resp = self.client.chat.completions.create(
            model=self.endpoint, temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs)
        usage = getattr(resp, "usage", None)
        self.usage["input"] += getattr(usage, "prompt_tokens", 0) or 0
        self.usage["output"] += getattr(usage, "completion_tokens", 0) or 0
        return resp.choices[0].message

    def structured(self, schema: type[BaseModel], system: str, user: str) -> BaseModel:
        instruction = (f"{system}\n\nReply with one JSON object only, matching this JSON schema:\n"
                       f"{json.dumps(schema.model_json_schema())}")
        content = self._complete(instruction, user).content
        text = content if isinstance(content, str) else json.dumps(content)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("model reply contains no JSON object")
        return schema.model_validate_json(text[start:end + 1])

    def request_check(self, system: str, user: str, tool_names: list[str]) -> CheckRequest | None:
        tools = [{"type": "function", "function": {
            "name": "request_check",
            "description": "Request one read-only check. tool is one of: " + ", ".join(tool_names),
            "parameters": CheckRequest.model_json_schema()}}]
        msg = self._complete(system, user, tools=tools)
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            return None
        return CheckRequest.model_validate_json(calls[0].function.arguments)
