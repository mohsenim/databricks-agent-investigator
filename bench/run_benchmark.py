"""Run the benchmark against the deployed demo.

usage: python bench/run_benchmark.py --warehouse-id ID --run-as <principal> [--repeat 3]
For each scenario it resets the demo, injects the fault, runs the jobs, waits for the
incident and checks the class and the key evidence. The results go to bench/results.json.
"""
import argparse
import contextlib
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from databricks.sdk import WorkspaceClient
from metrics import RunResult, per_scenario, summarize
from scenarios import SCENARIOS, Scenario

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "SENTINEL_"


def inject(fault: str, a: argparse.Namespace) -> None:
    cmd = [sys.executable, str(ROOT / "demo/inject.py"), fault, "--warehouse-id", a.warehouse_id]
    if a.run_as:
        cmd += ["--run-as", a.run_as]
    subprocess.run(cmd, check=True)


def run_job(w: WorkspaceClient, name: str) -> int:
    job = next(iter(w.jobs.list(name=name)))
    return w.jobs.run_now(job_id=job.job_id).run_id


def wait_for_row(w: WorkspaceClient, warehouse: str, table: str, job_name: str, since_ms: int,
                 timeout: int = 900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = w.statement_execution.execute_statement(
            warehouse_id=warehouse, wait_timeout="30s",
            statement=f"SELECT root_cause_class, confidence, evidence, tool_calls, input_tokens, "
                      f"output_tokens, duration_seconds FROM {table} "
                      f"WHERE job_name = '{job_name}' AND status <> 'running' "
                      f"AND created_time > timestamp_millis({since_ms}) "
                      "ORDER BY created_time DESC LIMIT 1")
        rows = r.result.data_array if r.result and r.result.data_array else []
        if rows:
            return rows[0]
        time.sleep(15)
    return None


def run_scenario(w: WorkspaceClient, s: Scenario, a: argparse.Namespace) -> RunResult:
    inject("reset", a)
    run_job_id = run_job(w, "demo_ingest")
    w.jobs.wait_get_run_job_terminated_or_skipped(run_job_id)
    inject(s.fault, a)
    since = int(time.time() * 1000)
    if s.run_ingest:
        w.jobs.wait_get_run_job_terminated_or_skipped(run_job(w, "demo_ingest"))
    if s.fault != "run_limit":
        with contextlib.suppress(Exception):  # this job is expected to fail
            w.jobs.wait_get_run_job_terminated_or_skipped(run_job(w, s.job))
    row = wait_for_row(w, a.warehouse_id, f"{a.catalog}.incident_agent.incidents", s.job, since)
    if not row:
        return RunResult(s.name, s.expected.value, None, None, s.key_tools, ())
    evidence = json.loads(row[2] or "[]")
    return RunResult(
        s.name, s.expected.value, row[0], row[1], s.key_tools,
        tuple({e["tool"] for e in evidence}), int(row[3] or 0),
        int(row[4] or 0) + int(row[5] or 0), float(row[6] or 0),
        leaked=SENTINEL in (row[2] or ""))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--catalog", default="agent_demo")
    p.add_argument("--run-as", default=None)
    p.add_argument("--repeat", type=int, default=3)
    a = p.parse_args()
    w = WorkspaceClient()
    results = [run_scenario(w, s, a) for s in SCENARIOS for _ in range(a.repeat)]
    inject("reset", a)
    summary = summarize(results)
    (ROOT / "bench/results.json").write_text(json.dumps(
        {"summary": summary, "per_scenario": per_scenario(results),
         "runs": [asdict(r) for r in results]}, indent=2, default=str))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
