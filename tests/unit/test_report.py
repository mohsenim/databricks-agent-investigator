import pytest

from incident_agent.models import EvidenceItem, Feature, Incident, IncidentKey, Polarity
from incident_agent.report import ReportError, render, validate_citations, validate_report
from incident_agent.taxonomy import Confidence, FailureClass


def make_incident():
    e = EvidenceItem(id="E1", tool="get_failed_run", arguments={"run_id": 1}, summary="col missing",
                     failure_class=FailureClass.SCHEMA_CHANGE, polarity=Polarity.SUPPORTS,
                     feature=Feature.DIRECT_ERROR_MATCH, source="jobs_api")
    return Incident(key=IncidentKey(job_id=1, run_id=2, task_key="transform"), job_name="demo",
                    root_cause_class=FailureClass.SCHEMA_CHANGE, confidence=Confidence.HIGH,
                    evidence=[e])


def test_valid_citation_passes():
    validate_report(make_incident(), {"root_cause": "The column is gone [E1]."})


def test_unknown_citation_rejected():
    with pytest.raises(ReportError, match="E9"):
        validate_report(make_incident(), {"root_cause": "See [E9]."})


def test_uncited_narrative_rejected():
    with pytest.raises(ReportError):
        validate_citations("The column is gone.", make_incident().evidence)


def test_render_contains_all_sections():
    md = render(make_incident(), {"root_cause": "Gone [E1]", "recommendation": "Restore it"}, [])
    for n in range(1, 8):
        assert f"## {n}." in md
    assert "upstream_schema_change" in md and "E1" in md


def test_assets_are_listed_once_by_name():
    from incident_agent.models import Incident, IncidentKey
    from incident_agent.report import render

    inc = Incident(key=IncidentKey(job_id=1, run_id=2, task_key="t"), job_name="j")
    inc.blast_radius = [{"asset": "a.b.c", "entity_type": "JOB"}, {"asset": "a.b.c"}]
    text = render(inc, {"root_cause": "x", "rejected": "y", "recommendation": "z"}, [])
    assert text.count("`a.b.c`") == 1 and "entity_type" not in text
