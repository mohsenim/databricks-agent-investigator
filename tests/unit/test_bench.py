import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "bench"))
from metrics import RunResult, per_scenario, summarize
from scenarios import SCENARIOS


def r(actual, conf, scenario="s", expected="a", found=("t1",), leaked=False):
    return RunResult(scenario, expected, actual, conf, ("t1", "t2"), found, 4, 100, 10.0, leaked)


def test_summary_accuracy_confident_wrong_and_recall():
    out = summarize([r("a", "HIGH"), r("b", "HIGH"), r("a", "LOW"), r("b", "MEDIUM")])
    assert out["accuracy"] == 0.5 and out["confident_wrong_rate"] == 0.25
    assert out["evidence_recall"] == 0.5 and out["leaks"] == 0


def test_leak_counted():
    assert summarize([r("a", "HIGH", leaked=True)])["leaks"] == 1


def test_per_scenario():
    assert per_scenario([r("a", "HIGH", "x"), r("b", "HIGH", "x")]) == {"x": (1, 2)}


def test_scenarios_cover_every_class_with_unique_names():
    names = [s.name for s in SCENARIOS]
    assert len(names) == len(set(names))
    assert len({s.expected for s in SCENARIOS}) >= 8
