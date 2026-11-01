"""Numbers computed from the results of a benchmark run."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RunResult:
    scenario: str
    expected: str
    actual: str | None
    confidence: str | None
    key_tools: tuple[str, ...]
    found_tools: tuple[str, ...]
    tool_calls: int = 0
    tokens: int = 0
    seconds: float = 0.0
    leaked: bool = False


def summarize(results: list[RunResult]) -> dict[str, float]:
    n = len(results) or 1
    correct = [r for r in results if r.actual == r.expected]
    high = [r for r in results if r.confidence == "HIGH"]
    wrong_high = [r for r in high if r.actual != r.expected]
    wanted = sum(len(r.key_tools) for r in results) or 1
    found = sum(len(set(r.key_tools) & set(r.found_tools)) for r in results)
    return {
        "runs": len(results),
        "accuracy": len(correct) / n,
        "confident_wrong_rate": len(wrong_high) / n,
        "evidence_recall": found / wanted,
        "avg_tool_calls": sum(r.tool_calls for r in results) / n,
        "avg_tokens": sum(r.tokens for r in results) / n,
        "avg_seconds": sum(r.seconds for r in results) / n,
        "leaks": sum(r.leaked for r in results),
    }


def per_scenario(results: list[RunResult]) -> dict[str, tuple[int, int]]:
    """Per scenario: (correct runs, total runs)."""
    out: dict[str, list[int]] = {}
    for r in results:
        c = out.setdefault(r.scenario, [0, 0])
        c[0] += r.actual == r.expected
        c[1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}
