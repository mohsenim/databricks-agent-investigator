"""Confidence from the evidence features. Plain code: nothing from the model can raise the level."""
from collections import defaultdict
from dataclasses import dataclass, field

from .models import EvidenceItem, Feature, Polarity
from .taxonomy import Confidence, FailureClass, Status

WEIGHTS: dict[Feature, int] = {
    Feature.DIRECT_ERROR_MATCH: 3,
    Feature.UPSTREAM_SIGNAL: 2,
    Feature.TIME_CORRELATION: 2,
    Feature.SECOND_SOURCE: 2,
    Feature.EARLIER_SUCCESS: 1,
}
CONTRADICTION_PENALTY = 3
MAX_PER_FEATURE = 2  # one feature type counts at most twice per class

# Starting values chosen by judgement, not calibrated. Change them to fit your own preference.
HIGH_SCORE = 7
HIGH_MARGIN = 3
HIGH_MIN_SOURCES = 2
MEDIUM_SCORE = 4
MEDIUM_MARGIN = 1
INCONCLUSIVE_BELOW = 2


@dataclass
class ScoreResult:
    failure_class: FailureClass
    confidence: Confidence
    status: Status
    score: int
    margin: int
    sources: int
    features: list[str] = field(default_factory=list)
    scores: dict[FailureClass, int] = field(default_factory=dict)


def class_scores(evidence: list[EvidenceItem]) -> dict[FailureClass, int]:
    counts: dict[tuple[FailureClass, Feature], int] = defaultdict(int)
    scores: dict[FailureClass, int] = defaultdict(int)
    for item in evidence:
        if item.polarity is Polarity.CONTRADICTS:
            scores[item.failure_class] -= CONTRADICTION_PENALTY
            continue
        key = (item.failure_class, item.feature)
        if counts[key] < MAX_PER_FEATURE:
            counts[key] += 1
            scores[item.failure_class] += WEIGHTS[item.feature]
    return dict(scores)


def score(evidence: list[EvidenceItem], missing_key_tool: bool = False) -> ScoreResult:
    """missing_key_tool: a tool that the best class depends on was not available.
    This caps the level at MEDIUM.
    """
    scores = class_scores(evidence)
    if not scores or max(scores.values()) < INCONCLUSIVE_BELOW:
        return ScoreResult(FailureClass.TRANSIENT_UNKNOWN, Confidence.LOW, Status.INCONCLUSIVE,
                           max(scores.values(), default=0), 0, 0, scores=scores)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    margin = best_score - runner_up

    supporting = [e for e in evidence if e.failure_class is best and e.polarity is Polarity.SUPPORTS]
    sources = len({e.source or e.tool for e in supporting})
    contradicted = any(
        e.failure_class is best and e.polarity is Polarity.CONTRADICTS for e in evidence
    )

    if (best_score >= HIGH_SCORE and margin >= HIGH_MARGIN and sources >= HIGH_MIN_SOURCES
            and not contradicted):
        level = Confidence.HIGH
    elif best_score >= MEDIUM_SCORE and margin >= MEDIUM_MARGIN:
        level = Confidence.MEDIUM
    else:
        level = Confidence.LOW
    if missing_key_tool and level is Confidence.HIGH:
        level = Confidence.MEDIUM

    features = sorted({e.feature.value for e in supporting})
    return ScoreResult(best, level, Status.DONE, best_score, margin, sources, features, scores)
