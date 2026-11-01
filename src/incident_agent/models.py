"""Records shared by the tools, the agent loop, the store and the app."""
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from .taxonomy import Confidence, FailureClass, Status


def utcnow() -> datetime:
    return datetime.now(UTC)


class Feature(str, Enum):
    DIRECT_ERROR_MATCH = "direct_error_match"
    TIME_CORRELATION = "time_correlation_with_change"
    EARLIER_SUCCESS = "same_task_succeeded_earlier"
    UPSTREAM_SIGNAL = "upstream_signal"
    SECOND_SOURCE = "independent_second_source"


class Polarity(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


class EvidenceItem(BaseModel):
    id: str  # E1, E2, ...
    tool: str
    arguments: dict
    queried_at: datetime = Field(default_factory=utcnow)
    summary: str
    failure_class: FailureClass
    polarity: Polarity
    feature: Feature
    source: str = ""  # which system gave the answer; used to count independent sources


class Hypothesis(BaseModel):
    failure_class: FailureClass
    rationale: str
    supporting_ids: list[str] = []
    contradicting_ids: list[str] = []
    needed_checks: list[str] = []


class IncidentKey(BaseModel):
    job_id: int
    run_id: int
    task_key: str = ""  # empty if the run failed before any task ran

    def as_tuple(self) -> tuple[int, int, str]:
        return (self.job_id, self.run_id, self.task_key)


class Incident(BaseModel):
    key: IncidentKey
    job_name: str = ""
    trigger_source: str = "push"  # push, poll or request
    status: Status = Status.RUNNING
    task_run_id: int | None = None
    repair_count: int = 0
    root_cause_class: FailureClass | None = None
    confidence: Confidence | None = None
    confidence_features: list[str] = []
    summary: str = ""
    blast_radius: list[dict] = []
    recommendation: str = ""
    evidence: list[EvidenceItem] = []
    rejected_hypotheses: list[dict] = []
    report_path: str = ""
    mlflow_trace_id: str = ""
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
