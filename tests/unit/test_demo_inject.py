import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "inject", Path(__file__).parents[2] / "demo" / "inject.py")
inject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inject)

BASE = {"max_concurrent_runs": 5, "tasks": [
    {"task_key": "transform_sales"}, {"task_key": "publish_report"}]}


def test_guard_refuses_other_catalogs():
    with pytest.raises(SystemExit):
        inject.guard("prod")
    inject.guard("agent_demo")


def test_faults_change_only_the_transform_task():
    out = inject.settings_for("timeout", BASE)
    assert out["tasks"][0]["timeout_seconds"] == 1 and "timeout_seconds" not in out["tasks"][1]
    assert BASE["tasks"][0] == {"task_key": "transform_sales"}  # input not mutated


def test_reset_removes_all_task_faults():
    broken = inject.settings_for("bad_node", inject.settings_for("timeout", BASE))
    clean = inject.settings_for("reset", broken)
    assert clean["tasks"][0] == {"task_key": "transform_sales"}


def test_every_fault_is_documented_and_flag_sql_is_scoped_to_catalog():
    assert "drop_column" in inject.FAULTS and len(inject.FAULTS) == 10
    assert all("agent_demo.raw" in s or s.startswith("CREATE SCHEMA")
               for s in inject.set_flag_sql("agent_demo", "drop_column"))
