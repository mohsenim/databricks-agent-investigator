"""Pull trigger: find failed runs of monitored jobs that have no incident yet."""
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .config import Config
from .models import IncidentKey
from .tools.base import JobsApi, SqlApi

# Task results that are a root failure (not a result of another task failing).
FAILED_TASK_STATES = {"FAILED", "TIMEDOUT"}
FAILED_RUN_STATES = {"FAILED", "TIMEDOUT"}


@dataclass(frozen=True)
class Target:
    key: IncidentKey
    task_run_id: int | None
    job_name: str


def _state(obj: dict[str, Any]) -> tuple[str | None, str | None]:
    s = obj.get("state") or {}
    return s.get("life_cycle_state"), s.get("result_state")


def failed_targets(run: dict[str, Any]) -> list[Target]:
    """Incidents to open for one run.
    It looks at the task states, so a run that ended as "Succeeded with failures" is still
    found. A run that failed without any task result gets one run-level target (task_key '').
    """
    life, result = _state(run)
    if life not in {"TERMINATED", "INTERNAL_ERROR", "SKIPPED"}:
        return []  # still running or queued
    job_id, run_id = run["job_id"], run["run_id"]
    name = run.get("run_name", "")
    out: list[Target] = []
    for task in run.get("tasks") or []:
        _, t_result = _state(task)
        if t_result in FAILED_TASK_STATES:
            out.append(Target(IncidentKey(job_id=job_id, run_id=run_id, task_key=task["task_key"]),
                              task.get("run_id"), name))
    if not out and (result in FAILED_RUN_STATES or life == "INTERNAL_ERROR"):
        out.append(Target(IncidentKey(job_id=job_id, run_id=run_id, task_key=""), None, name))
    return out


def select_new(runs: list[dict[str, Any]], known: set[tuple[int, int, str]],
               limit: int) -> list[Target]:
    """Targets that have no incident yet, oldest first, at most `limit`."""
    chosen: list[Target] = []
    for run in sorted(runs, key=lambda r: r.get("end_time") or 0):
        for t in failed_targets(run):
            if t.key.as_tuple() not in known:
                chosen.append(t)
    return chosen[:limit]


class Starter(Protocol):
    def start_investigation(self, investigator_job_id: int, params: dict[str, str]) -> int: ...


def read_settings(sql: SqlApi, cfg: Config) -> dict[str, int | bool]:
    rows = sql.run(f"SELECT key, value FROM {cfg.table('agent_settings')}")
    raw = {r["key"]: r["value"] for r in rows}
    return {
        "max_per_cycle": int(raw.get("max_per_cycle", cfg.poll_max_per_cycle)),
        "lookback_hours": int(raw.get("lookback_hours", cfg.poll_lookback_hours)),
        "enabled": str(raw.get("poller_enabled", "true")).lower() == "true",
    }


def read_model_override(sql: SqlApi, cfg: Config) -> str:
    """The model endpoint chosen in the app (Settings). Empty if none was chosen."""
    try:
        rows = sql.run(
            f"SELECT value FROM {cfg.table('agent_settings')} WHERE key = 'model_endpoint'")
    except Exception:  # noqa: BLE001 - settings are optional, so use the deployed default
        return ""
    return str(rows[0]["value"]).strip() if rows and rows[0]["value"] else ""


def poll_once(jobs: JobsApi, starter: Starter, sql: SqlApi, cfg: Config,
              now_ms: int | None = None) -> list[Target]:
    """One poller cycle: find failed runs of monitored jobs that have no incident and start
    the investigator for at most max_per_cycle of them. Returns the targets it started.
    """
    settings = read_settings(sql, cfg)
    if not settings["enabled"]:
        return []
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    since = now - int(settings["lookback_hours"]) * 3_600_000

    monitored = sql.run(
        f"SELECT job_id FROM {cfg.table('monitored_jobs')} WHERE enabled = true")
    # The agent never investigates its own investigator job.
    job_ids = [int(r["job_id"]) for r in monitored
               if str(r["job_id"]) != str(cfg.investigator_job_id)]
    runs: list[dict[str, Any]] = []
    for job_id in job_ids:
        runs += jobs.list_runs(job_id, since, 100)
    if not runs:
        _touch_state(sql, cfg)
        return []

    ids = ",".join(str(j) for j in job_ids)  # only ints, made with int() above
    known_rows = sql.run(
        f"SELECT job_id, run_id, task_key FROM {cfg.table('incidents')} WHERE job_id IN ({ids})")
    known = {(int(r["job_id"]), int(r["run_id"]), r["task_key"]) for r in known_rows}

    started = []
    for target in select_new(runs, known, int(settings["max_per_cycle"])):
        starter.start_investigation(int(cfg.investigator_job_id), {
            "job_id": str(target.key.job_id), "run_id": str(target.key.run_id),
            "task_key": target.key.task_key, "task_run_id": str(target.task_run_id or ""),
            "trigger_source": "poll"})
        started.append(target)
    _touch_state(sql, cfg)
    return started


def _touch_state(sql: SqlApi, cfg: Config) -> None:
    table = cfg.table("poller_state")
    sql.run(f"DELETE FROM {table}")
    sql.run(f"INSERT INTO {table} VALUES (current_timestamp())")
