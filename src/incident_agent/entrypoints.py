"""Entry points for the jobs: `investigate` and `poll`."""
import argparse
import os
from pathlib import Path

from .config import Config

CONFIG_ARGS = {
    "--catalog": "AGENT_CATALOG", "--schema": "AGENT_SCHEMA", "--warehouse_id": "AGENT_WAREHOUSE_ID",
    "--model_endpoint": "AGENT_MODEL_ENDPOINT", "--investigator_job_id": "AGENT_INVESTIGATOR_JOB_ID",
}


def _add_config_args(p: argparse.ArgumentParser) -> None:
    for flag in CONFIG_ARGS:
        p.add_argument(flag, default=None)


def _apply_config_args(args: argparse.Namespace) -> Config:
    """Job parameters override environment variables. Empty values are ignored."""
    for flag, env in CONFIG_ARGS.items():
        value = getattr(args, flag.lstrip("-"))
        if value:
            os.environ[env] = value
    return Config()


def _clients(cfg: Config):
    from databricks.sdk import WorkspaceClient

    from .clients import SdkJobsApi, SdkSqlRunner

    w = WorkspaceClient()
    if not cfg.workspace_id:
        cfg = Config(**{**cfg.__dict__, "workspace_id": str(w.get_workspace_id())})
    return cfg, SdkJobsApi(w), SdkSqlRunner(w, cfg.warehouse_id)


def _job_name(jobs, job_id: int) -> str:
    """The poll and request triggers do not pass the job name."""
    try:
        return str((jobs.get_job(job_id).get("settings") or {}).get("name") or "")
    except Exception:  # noqa: BLE001 - a missing name must not stop the investigation
        return ""


def investigate_main(argv: list[str] | None = None) -> None:
    from .investigate import Deps, Narrator, TemplateNarrator, investigate
    from .models import IncidentKey
    from .poller import Target, read_model_override
    from .store import Store

    p = argparse.ArgumentParser()
    p.add_argument("--job_id", type=int, required=True)
    p.add_argument("--run_id", type=int, required=True)
    p.add_argument("--task_key", default="")
    p.add_argument("--task_run_id", default="")
    p.add_argument("--trigger_source", default="push")
    p.add_argument("--job_name", default="")
    _add_config_args(p)
    a = p.parse_args(argv)

    cfg, jobs, sql = _clients(_apply_config_args(a))
    override = read_model_override(sql, cfg)
    if override:
        cfg = Config(**{**cfg.__dict__, "model_endpoint": override})
    narrator: Narrator = TemplateNarrator()
    if cfg.model_endpoint:
        from .graph import LlmNarrator

        narrator = LlmNarrator(cfg).bind(jobs, sql)
    job_name = a.job_name or _job_name(jobs, a.job_id)
    target = Target(IncidentKey(job_id=a.job_id, run_id=a.run_id, task_key=a.task_key),
                    int(a.task_run_id) if a.task_run_id else None, job_name)
    reports = Path(f"/Volumes/{cfg.catalog}/{cfg.schema}/reports")
    deps = Deps(jobs=jobs, sql=sql, store=Store(sql, cfg), reports_dir=reports, config=cfg,
                narrator=narrator)
    result = investigate(deps, target, a.trigger_source)
    print("skipped: already running" if result is None else f"{result.status.value}: {result.summary}")


def poll_main(argv: list[str] | None = None) -> None:
    from .poller import poll_once

    p = argparse.ArgumentParser()
    _add_config_args(p)
    cfg, jobs, sql = _clients(_apply_config_args(p.parse_args(argv)))
    started = poll_once(jobs, jobs, sql, cfg)
    print(f"started {len(started)} investigation(s)")
