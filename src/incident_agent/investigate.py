"""Entry point of the investigator job: claim, collect, score, narrate, check, store."""
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import __version__
from .collect import Bundle, collect
from .config import Config
from .models import Incident, IncidentKey
from .poller import Target
from .report import ReportError, render, validate_report
from .scoring import ScoreResult, score
from .store import Store
from .taxonomy import Status
from .tools.base import JobsApi, SqlApi


class Narrator(Protocol):
    def narrate(self, incident: Incident, bundle: Bundle, result: ScoreResult) -> dict[str, str]:
        """Return the report sections: root_cause, rejected and recommendation."""
        ...


class TemplateNarrator:
    """Used when there is no model, and when the model's report fails the check.
    It cites evidence by ID, so it passes the same check.
    """

    def narrate(self, incident: Incident, bundle: Bundle, result: ScoreResult) -> dict[str, str]:
        top = [e for e in incident.evidence
               if e.failure_class is result.failure_class and e.polarity.value == "supports"]
        cites = " ".join(f"[{e.id}]" for e in top) or ""
        cause = result.failure_class.value.replace("_", " ")
        body = f"The evidence points to: {cause} {cites}.".strip()
        if not cites:
            body = f"No evidence supports a specific cause ({cause})."
        rejected = [e for e in incident.evidence if e.failure_class is not result.failure_class]
        return {
            "root_cause": body,
            "rejected": "; ".join(f"{e.failure_class.value}: {e.summary} [{e.id}]"
                                  for e in rejected) or "None.",
            "recommendation": "Review the evidence above and fix the identified cause; "
                              "re-run the job after the fix.",
        }


@dataclass
class Deps:
    jobs: JobsApi
    sql: SqlApi
    store: Store
    reports_dir: Path
    config: Config
    narrator: Narrator


def investigate(deps: Deps, target: Target, trigger_source: str) -> Incident | None:
    """Returns None if another investigation of the same incident is already running."""
    started = time.time()
    key: IncidentKey = target.key
    store = deps.store
    if not store.claim(key, trigger_source, target.job_name):
        return None

    incident = Incident(key=key, job_name=target.job_name, trigger_source=trigger_source,
                        task_run_id=target.task_run_id)
    try:
        bundle = collect(deps.jobs, deps.sql, target, deps.config.workspace_id)
        result = score(bundle.evidence, bundle.missing_key_tool)
        incident.evidence = bundle.evidence
        incident.root_cause_class = result.failure_class
        incident.confidence = result.confidence
        incident.confidence_features = result.features
        incident.status = result.status
        incident.blast_radius = bundle.blast_radius
        incident.tool_calls = len(bundle.results)

        narrative = deps.narrator.narrate(incident, bundle, result)
        try:
            validate_report(incident, narrative)
        except ReportError:
            narrative = TemplateNarrator().narrate(incident, bundle, result)
            validate_report(incident, narrative)
        incident.summary = narrative["root_cause"]
        incident.recommendation = narrative.get("recommendation", "")
        incident.rejected_hypotheses = [{"text": narrative.get("rejected", "")}]

        markdown = render(incident, narrative, bundle.unavailable)
        incident.report_path = _write_report(deps.reports_dir, incident, markdown)
    except Exception as exc:  # the job must always leave a row behind
        incident.status = Status.ERROR
        incident.summary = f"{type(exc).__name__}: {exc}"[:500]

    incident.duration_seconds = time.time() - started
    store.finish(incident, deps.config.model_endpoint, __version__)
    return incident


def _write_report(directory: Path, incident: Incident, markdown: str) -> str:
    k = incident.key
    path = directory / str(k.job_id) / f"{k.run_id}_{k.task_key or 'run'}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return str(path)
