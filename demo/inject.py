"""Break and repair the demo pipeline. It refuses to run outside the demo catalog.

usage: python demo/inject.py <fault|reset> [--catalog agent_demo] [--run-as <principal>]
"""
import argparse
import sys
from pathlib import Path

FAULTS = {
    "drop_column": "producer writes raw.orders without the amount column",
    "type_change": "producer writes amount as STRING",
    "empty_data": "producer writes zero rows",
    "producer_fail": "producer job fails",
    "revoke": "SELECT on raw.orders revoked from the pipeline's run-as principal",
    "bad_library": "transform_sales requires a package version that does not exist",
    "code_bug": "transform_sales references an undefined variable",
    "timeout": "transform_sales has a one-second timeout",
    "bad_node": "transform_sales runs on a node type that does not exist",
    "run_limit": "a second run is started while max concurrent runs is 1",
}
FLAG_FAULTS = {"drop_column", "type_change", "empty_data", "producer_fail"}
NOTEBOOK_DIR = Path(__file__).parent / "notebooks"
DEMO_JOB = "demo_daily_sales"
INGEST_JOB = "demo_ingest"
TRANSFORM_PATH = "/Shared/agent_demo/transform_sales"


def guard(catalog: str) -> None:
    """This script changes data, grants and job settings, so it only runs in the demo catalog."""
    if catalog != "agent_demo":
        sys.exit(f"refusing to run: catalog must be 'agent_demo', got {catalog!r}")


def set_flag_sql(catalog: str, fault: str) -> list[str]:
    return [
        f"CREATE SCHEMA IF NOT EXISTS {catalog}.raw",
        f"CREATE OR REPLACE TABLE {catalog}.raw.fault_flag AS SELECT '{fault}' AS fault",
    ]


def settings_for(fault: str, settings: dict) -> dict:
    """Job settings with the fault applied to the transform_sales task."""
    out = {**settings, "tasks": [dict(t) for t in settings["tasks"]]}
    for task in out["tasks"]:
        if task["task_key"] != "transform_sales":
            continue
        if fault == "bad_library":
            task["libraries"] = [{"pypi": {"package": "nonexistent-pkg-xyz==99.99.99"}}]
        elif fault == "timeout":
            task["timeout_seconds"] = 1
        elif fault == "bad_node":
            task["new_cluster"] = {"spark_version": "15.4.x-scala2.12",
                                   "node_type_id": "not-a-real-node-type", "num_workers": 1}
        elif fault == "reset":
            for key in ("libraries", "timeout_seconds", "new_cluster"):
                task.pop(key, None)
    if fault == "run_limit" or fault == "reset":
        out["max_concurrent_runs"] = 1
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("fault", choices=[*FAULTS, "reset"])
    p.add_argument("--catalog", default="agent_demo")
    p.add_argument("--warehouse-id", default=None)
    p.add_argument("--run-as", default=None, help="principal for the revoke fault")
    a = p.parse_args()
    guard(a.catalog)

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.jobs import JobSettings

    w = WorkspaceClient()
    warehouse = a.warehouse_id or next(iter(w.warehouses.list())).id

    def sql(statement: str) -> None:
        w.statement_execution.execute_statement(statement=statement, warehouse_id=warehouse,
                                                wait_timeout="50s")

    def job(name: str):
        return next(w.jobs.list(name=name))

    if a.fault in FLAG_FAULTS or a.fault == "reset":
        for stmt in set_flag_sql(a.catalog, a.fault if a.fault in FLAG_FAULTS else "none"):
            sql(stmt)
    if a.fault == "revoke":
        if not a.run_as:
            sys.exit("--run-as is required for revoke")
        sql(f"REVOKE SELECT ON TABLE {a.catalog}.raw.orders FROM `{a.run_as}`")
    if a.fault == "reset" and a.run_as:
        sql(f"GRANT SELECT ON TABLE {a.catalog}.raw.orders TO `{a.run_as}`")

    if a.fault in {"bad_library", "timeout", "bad_node", "run_limit", "reset"}:
        for name in (DEMO_JOB, DEMO_JOB + "_nohandler"):
            found = next(iter(w.jobs.list(name=name)), None)
            if not found:
                continue
            current = w.jobs.get(job_id=found.job_id).settings.as_dict()
            new = settings_for(a.fault, current)
            w.jobs.reset(job_id=found.job_id, new_settings=JobSettings.from_dict(new))

    if a.fault in {"code_bug", "reset"}:
        import base64

        from databricks.sdk.service.workspace import ImportFormat, Language

        src = "transform_sales_buggy.py" if a.fault == "code_bug" else "transform_sales.py"
        content = base64.b64encode((NOTEBOOK_DIR / src).read_bytes()).decode()
        w.workspace.import_(TRANSFORM_PATH, content=content, format=ImportFormat.SOURCE,
                            language=Language.PYTHON, overwrite=True)

    if a.fault == "run_limit":
        found = next(iter(w.jobs.list(name=DEMO_JOB + "_nohandler")))
        w.jobs.run_now(job_id=found.job_id)
        w.jobs.run_now(job_id=found.job_id)  # a second start while the first is still running
    print(f"{a.fault}: done" if a.fault == "reset" else f"{a.fault}: {FAULTS[a.fault]}")


if __name__ == "__main__":
    main()
