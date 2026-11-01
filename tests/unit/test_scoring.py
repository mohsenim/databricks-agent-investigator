from incident_agent.models import EvidenceItem, Feature, Polarity
from incident_agent.scoring import score
from incident_agent.taxonomy import Confidence, FailureClass, Status

SC = FailureClass.SCHEMA_CHANGE
PERM = FailureClass.PERMISSION
_n = 0


def ev(cls, feature, source, polarity=Polarity.SUPPORTS):
    global _n
    _n += 1
    return EvidenceItem(id=f"E{_n}", tool=source, arguments={}, summary="s", failure_class=cls,
                        polarity=polarity, feature=feature, source=source)


def test_no_evidence_is_inconclusive():
    r = score([])
    assert r.status is Status.INCONCLUSIVE and r.confidence is Confidence.LOW
    assert r.failure_class is FailureClass.TRANSIENT_UNKNOWN


def test_strong_multi_source_is_high():
    e = [ev(SC, Feature.DIRECT_ERROR_MATCH, "jobs_api"),
         ev(SC, Feature.TIME_CORRELATION, "table_history"),
         ev(SC, Feature.EARLIER_SUCCESS, "run_history"),
         ev(SC, Feature.UPSTREAM_SIGNAL, "lineage")]
    r = score(e)
    assert r.confidence is Confidence.HIGH and r.failure_class is SC and r.score == 8


def test_single_source_cannot_be_high():
    e = [ev(SC, Feature.DIRECT_ERROR_MATCH, "jobs_api"),
         ev(SC, Feature.DIRECT_ERROR_MATCH, "jobs_api"),
         ev(SC, Feature.TIME_CORRELATION, "jobs_api")]
    assert score(e).confidence is Confidence.MEDIUM


def test_contradiction_lowers_level():
    base = [ev(SC, Feature.DIRECT_ERROR_MATCH, "a"), ev(SC, Feature.TIME_CORRELATION, "b"),
            ev(SC, Feature.UPSTREAM_SIGNAL, "c"), ev(SC, Feature.EARLIER_SUCCESS, "d")]
    assert score(base).confidence is Confidence.HIGH
    r = score(base + [ev(SC, Feature.SECOND_SOURCE, "e", Polarity.CONTRADICTS)])
    assert r.confidence is not Confidence.HIGH


def test_close_competitor_blocks_high():
    e = [ev(SC, Feature.DIRECT_ERROR_MATCH, "a"), ev(SC, Feature.TIME_CORRELATION, "b"),
         ev(SC, Feature.UPSTREAM_SIGNAL, "c"),
         ev(PERM, Feature.DIRECT_ERROR_MATCH, "d"), ev(PERM, Feature.TIME_CORRELATION, "e")]
    r = score(e)
    assert r.confidence is not Confidence.HIGH


def test_missing_key_tool_caps_at_medium():
    e = [ev(SC, Feature.DIRECT_ERROR_MATCH, "a"), ev(SC, Feature.TIME_CORRELATION, "b"),
         ev(SC, Feature.UPSTREAM_SIGNAL, "c"), ev(SC, Feature.EARLIER_SUCCESS, "d")]
    assert score(e, missing_key_tool=True).confidence is Confidence.MEDIUM


def test_feature_counts_at_most_twice():
    e = [ev(SC, Feature.DIRECT_ERROR_MATCH, "a") for _ in range(5)]
    assert score(e).score == 6
