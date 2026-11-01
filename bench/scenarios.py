"""Benchmark scenarios: the fault, the expected class and the evidence that must be found."""
from dataclasses import dataclass

from incident_agent.taxonomy import FailureClass as C


@dataclass(frozen=True)
class Scenario:
    name: str
    fault: str                       # fault name in demo/inject.py
    expected: C
    key_tools: tuple[str, ...]       # these tools must have produced evidence
    run_ingest: bool = False         # the producer job must run after the fault is set
    job: str = "demo_daily_sales"
    via: str = "push"                # push or poll


SCENARIOS = [
    Scenario("column_type_change", "type_change", C.SCHEMA_CHANGE,
             ("get_task_error", "get_table_history", "compare_table_schema"), run_ingest=True),
    Scenario("dropped_column", "drop_column", C.SCHEMA_CHANGE,
             ("get_task_error", "get_table_history", "compare_table_schema"), run_ingest=True),
    Scenario("empty_upstream_data", "empty_data", C.DATA_PROBLEM,
             ("get_table_history",), run_ingest=True),
    Scenario("upstream_job_failed", "producer_fail", C.UPSTREAM_JOB,
             ("get_upstream_run_status",), run_ingest=True),
    Scenario("permission_revoked", "revoke", C.PERMISSION,
             ("get_task_error", "get_permission_changes")),
    Scenario("bad_library_pin", "bad_library", C.LIBRARY, ("get_failed_run",)),
    Scenario("code_error", "code_bug", C.CODE_OR_CONFIG, ("get_task_error", "get_task_source")),
    Scenario("task_timeout", "timeout", C.RESOURCE_LIMIT, ("get_failed_run", "get_job_changes")),
    Scenario("invalid_cluster_config", "bad_node", C.CLUSTER_INFRA, ("get_failed_run",)),
    Scenario("run_limit_exceeded", "run_limit", C.TRANSIENT_UNKNOWN, ("get_failed_run",),
             job="demo_daily_sales_nohandler", via="poll"),
    Scenario("pull_path_schema_change", "drop_column", C.SCHEMA_CHANGE,
             ("get_task_error", "get_table_history"), run_ingest=True,
             job="demo_daily_sales_nohandler", via="poll"),
]
