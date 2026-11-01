"""Reads and writes the tables for the app. No UI code here, so tests do not need Streamlit."""
import re
from typing import Any, Protocol

CLASSES = ["code_or_config_error", "upstream_schema_change", "upstream_data_problem",
           "upstream_job_failure", "cluster_or_infrastructure_error", "permission_change",
           "library_install_failure", "resource_limit", "transient_or_unknown"]
CONFIDENCE = ["LOW", "MEDIUM", "HIGH"]
STATUS = ["running", "done", "inconclusive", "error"]


class Sql(Protocol):
    def run(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...


class Data:
    def __init__(self, sql: Sql, catalog: str, schema: str):
        self.sql = sql
        self.t = lambda name: f"{catalog}.{schema}.{name}"

    # incidents
    def incidents(self, job: str = "", classes: list[str] | None = None,
                  confidence: list[str] | None = None, status: list[str] | None = None,
                  limit: int = 200) -> list[dict[str, Any]]:
        where, params = ["1 = 1"], {"limit": int(limit)}
        if job:
            where.append("lower(job_name) LIKE :job")
            params["job"] = f"%{job.lower()}%"
        for column, values, allowed in (("root_cause_class", classes, CLASSES),
                                        ("confidence", confidence, CONFIDENCE),
                                        ("status", status, STATUS)):
            picked = [v for v in (values or []) if v in allowed]  # only allowed values
            if picked:
                names = []
                for i, v in enumerate(picked):
                    params[f"{column}_{i}"] = v
                    names.append(f":{column}_{i}")
                where.append(f"{column} IN ({', '.join(names)})")
        return self.sql.run(
            f"""SELECT job_id, run_id, task_key, job_name, trigger_source, status,
                       root_cause_class, confidence, model_endpoint, summary, created_time,
                       duration_seconds, report_path
                FROM {self.t('incidents')} WHERE {' AND '.join(where)}
                ORDER BY created_time DESC LIMIT :limit""", params)

    def incident(self, job_id: int, run_id: int, task_key: str) -> dict[str, Any] | None:
        rows = self.sql.run(
            f"SELECT * FROM {self.t('incidents')} "
            "WHERE job_id = :job_id AND run_id = :run_id AND task_key = :task_key",
            {"job_id": job_id, "run_id": run_id, "task_key": task_key})
        return rows[0] if rows else None

    # dashboard
    def per_day(self, days: int = 30) -> list[dict[str, Any]]:
        return self.sql.run(
            f"""SELECT date(created_time) AS day, count(*) AS incidents
                FROM {self.t('incidents')}
                WHERE created_time > current_timestamp() - make_interval(0, 0, 0, :days)
                GROUP BY 1 ORDER BY 1""", {"days": int(days)})

    def mix(self, column: str) -> list[dict[str, Any]]:
        if column not in ("root_cause_class", "confidence", "status", "trigger_source"):
            raise ValueError(column)
        return self.sql.run(
            f"SELECT coalesce({column}, 'n/a') AS label, count(*) AS incidents "
            f"FROM {self.t('incidents')} GROUP BY 1 ORDER BY 2 DESC")

    def cost(self, days: int = 30) -> list[dict[str, Any]]:
        return self.sql.run(
            f"""SELECT date(created_time) AS day, sum(input_tokens) AS input_tokens,
                       sum(output_tokens) AS output_tokens, avg(duration_seconds) AS avg_seconds
                FROM {self.t('incidents')}
                WHERE created_time > current_timestamp() - make_interval(0, 0, 0, :days)
                GROUP BY 1 ORDER BY 1""", {"days": int(days)})

    # settings
    def monitored(self) -> list[dict[str, Any]]:
        return self.sql.run(
            f"SELECT job_id, job_name, enabled FROM {self.t('monitored_jobs')} ORDER BY job_name")

    def add_monitored(self, job_id: int, job_name: str) -> None:
        self.sql.run(
            f"""MERGE INTO {self.t('monitored_jobs')} t
                USING (SELECT :job_id AS job_id, :job_name AS job_name) s ON t.job_id = s.job_id
                WHEN MATCHED THEN UPDATE SET enabled = true, job_name = s.job_name
                WHEN NOT MATCHED THEN INSERT (job_id, job_name, enabled, added_time)
                  VALUES (s.job_id, s.job_name, true, current_timestamp())""",
            {"job_id": int(job_id), "job_name": job_name})

    def set_monitored_enabled(self, job_id: int, enabled: bool) -> None:
        self.sql.run(f"UPDATE {self.t('monitored_jobs')} SET enabled = :e WHERE job_id = :job_id",
                     {"e": bool(enabled), "job_id": int(job_id)})

    def is_monitored(self, job_id: int) -> bool:
        return bool(self.sql.run(
            f"SELECT 1 AS x FROM {self.t('monitored_jobs')} WHERE job_id = :job_id AND enabled = true",
            {"job_id": int(job_id)}))

    def settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in
                self.sql.run(f"SELECT key, value FROM {self.t('agent_settings')}")}

    def set_setting(self, key: str, value: str) -> None:
        if key not in ("max_per_cycle", "lookback_hours", "poller_enabled", "model_endpoint"):
            raise ValueError(key)
        self.sql.run(
            f"""MERGE INTO {self.t('agent_settings')} t USING (SELECT :k AS key, :v AS value) s
                ON t.key = s.key WHEN MATCHED THEN UPDATE SET value = s.value
                WHEN NOT MATCHED THEN INSERT (key, value) VALUES (s.key, s.value)""",
            {"k": key, "v": str(value)})

    def last_poll(self) -> Any:
        rows = self.sql.run(f"SELECT max(last_checked) AS t FROM {self.t('poller_state')}")
        return rows[0]["t"] if rows else None


def cron_for(minutes: int) -> str:
    """Cron for every `minutes` minutes (1-59), or every hour for 60."""
    return "0 0 * * * ?" if minutes >= 60 else f"0 0/{int(minutes)} * * * ?"


def minutes_from(cron: str) -> int | None:
    """The reverse of cron_for. Returns None for a cron the app did not write."""
    if cron == "0 0 * * * ?":
        return 60
    m = re.fullmatch(r"0 0/(\d{1,2}) \* \* \* \?", cron or "")
    return int(m.group(1)) if m else None


_HTML = re.compile(r"<[^>]*>")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def sanitize_markdown(text: str) -> str:
    """The report text comes from a model and is shown in the browser.
    Remove HTML tags and turn links and images into plain text, so a report cannot add
    markup or send users to another site.
    """
    text = _HTML.sub("", text or "")
    return _LINK.sub(lambda m: m.group(1), text)
