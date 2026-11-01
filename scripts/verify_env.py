"""Check the environment. Prints pass or fail for each check and writes docs/environment.md.

usage: python scripts/verify_env.py --warehouse-id ID [--catalog agent_demo] [--model-endpoint NAME]
Run it with the investigator's credentials to check its grants.
"""
import argparse
from collections.abc import Callable
from pathlib import Path

from databricks.sdk import WorkspaceClient

from incident_agent.clients import SdkJobsApi, SdkSqlRunner


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--catalog", default="agent_demo")
    p.add_argument("--model-endpoint", default="")
    p.add_argument("--failed-run-id", type=int, default=0, help="a failed run to read in full")
    a = p.parse_args()

    w = WorkspaceClient()
    sql = SdkSqlRunner(w, a.warehouse_id)
    jobs = SdkJobsApi(w)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn: Callable[[], str]) -> None:
        try:
            results.append((name, True, fn()))
        except Exception as exc:
            results.append((name, False, f"{type(exc).__name__}: {exc}"[:300]))
        print(f"{'PASS' if results[-1][1] else 'FAIL'}  {name}: {results[-1][2]}")

    wid = str(w.get_workspace_id())
    check("system.lakeflow.job_task_run_timeline readable", lambda: str(len(sql.run(
        "SELECT run_id FROM system.lakeflow.job_task_run_timeline "
        "WHERE workspace_id = :w AND period_start_time > current_timestamp() - INTERVAL 1 DAY "
        "LIMIT 1", {"w": wid}))) + " row(s)")
    check("system.lakeflow.jobs readable", lambda: str(len(sql.run(
        "SELECT job_id FROM system.lakeflow.jobs WHERE workspace_id = :w LIMIT 1",
        {"w": wid}))) + " row(s)")
    check("system.access.table_lineage readable", lambda: str(len(sql.run(
        "SELECT entity_id FROM system.access.table_lineage WHERE workspace_id = :w LIMIT 1",
        {"w": wid}))) + " row(s)")
    check("system.access.audit readable (permission changes)", lambda: str(len(sql.run(
        "SELECT action_name FROM system.access.audit WHERE workspace_id = :w "
        "AND service_name = 'unityCatalog' LIMIT 1", {"w": wid}))) + " row(s)")
    check("DESCRIBE HISTORY on demo table", lambda: str(len(sql.run(
        f"DESCRIBE HISTORY {a.catalog}.raw.orders LIMIT 3"))) + " row(s)")
    check("time travel VERSION AS OF ... LIMIT 0", lambda: str(sql.columns(
        f"SELECT * FROM {a.catalog}.raw.orders VERSION AS OF 0 LIMIT 0")))
    check("Jobs API runs/list", lambda: str(len(jobs.list_runs(
        next(iter(w.jobs.list())).job_id, 0, 5))) + " run(s)")
    if a.failed_run_id:
        def run_detail() -> str:
            run = jobs.get_run(a.failed_run_id)
            failed = [t for t in run.get("tasks", [])
                      if (t.get("state") or {}).get("result_state") == "FAILED"]
            out = jobs.get_run_output(failed[0]["run_id"])
            return f"task {failed[0]['task_key']}: error={bool(out.get('error'))}, " \
                   f"trace={bool(out.get('error_trace'))}"
        check("runs/get + runs/get-output on a failed task run", run_detail)
    if a.model_endpoint:
        def model() -> str:
            from incident_agent.config import Config
            from incident_agent.graph import DatabricksChatModel

            msg = DatabricksChatModel(Config(model_endpoint=a.model_endpoint))._complete(
                "You answer briefly.", "Reply with the word ok.")
            return f"reply: {str(msg.content)[:40]}"
        check("model endpoint reachable", model)

    lines = ["# Environment check", "", "| Check | Result | Detail |", "| --- | --- | --- |"]
    lines += [f"| {n} | {'pass' if ok else 'FAIL'} | {d.replace('|', '/')} |" for n, ok, d in results]
    out = Path("docs/environment.md")
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    raise SystemExit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == "__main__":
    main()
