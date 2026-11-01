"""Renders the Markdown report and checks its citations."""
import re

from .models import EvidenceItem, Incident

CITATION = re.compile(r"\[(E\d+)\]")


class ReportError(ValueError):
    pass


def validate_citations(narrative: str, evidence: list[EvidenceItem]) -> None:
    """Every cited ID must exist. If there is any evidence, the narrative must cite some."""
    known = {e.id for e in evidence}
    cited = CITATION.findall(narrative)
    unknown = sorted(set(cited) - known)
    if unknown:
        raise ReportError(f"report cites unknown evidence: {', '.join(unknown)}")
    if narrative.strip() and not cited and evidence:
        raise ReportError("report narrative has no evidence citations")


def render(incident: Incident, narrative: dict[str, str], not_checked: list[str]) -> str:
    """narrative has the sections written by the model: root_cause, rejected, recommendation."""
    key = incident.key
    lines = [
        f"# Incident: {incident.job_name or key.job_id} / {key.task_key or 'run'}",
        "",
        "## 1. What failed",
        f"- Job {key.job_id}, run {key.run_id}, task `{key.task_key or '-'}`",
        f"- Trigger: {incident.trigger_source}",
        "",
        "## 2. Most likely root cause",
        (
            f"**{incident.root_cause_class.value if incident.root_cause_class else 'unknown'}** "
            f"(confidence {incident.confidence.value if incident.confidence else 'LOW'}; "
            f"features: {', '.join(incident.confidence_features) or 'none'})"
        ),
        "",
        narrative.get("root_cause", ""),
        "",
        "## 3. Evidence",
    ]
    for e in incident.evidence:
        lines.append(f"- **{e.id}** `{e.tool}` ({e.polarity.value}, {e.feature.value}): {e.summary}")
    lines += ["", "## 4. Hypotheses rejected", narrative.get("rejected", "None."), "",
              "## 5. Affected downstream assets (best effort, lineage has gaps)"]
    assets = sorted({str(a["asset"]) for a in incident.blast_radius if a.get("asset")})
    lines += [f"- `{a}`" for a in assets] or ["- none found"]
    lines += ["", "## 6. Recommended action", narrative.get("recommendation", ""), "",
              "## 7. Not checked or unavailable"]
    lines += [f"- {n}" for n in not_checked] or ["- nothing"]
    return "\n".join(lines) + "\n"


def validate_report(incident: Incident, narrative: dict[str, str]) -> None:
    validate_citations("\n".join(narrative.values()), incident.evidence)
