"""Access to the incident table. All SQL goes through a SqlRunner, so tests can use a fake."""
import uuid
from typing import Any, Protocol

from .config import Config
from .models import Incident, IncidentKey


class SqlRunner(Protocol):
    def run(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run one statement with parameters and return the rows as dicts."""
        ...


class Store:
    def __init__(self, sql: SqlRunner, config: Config | None = None):
        self.sql = sql
        self.cfg = config or Config()
        self.incidents = self.cfg.table("incidents")

    def claim(self, key: IncidentKey, trigger_source: str, job_name: str = "") -> bool:
        """Insert a `running` row, or take over a stale or finished one.

        Returns False if another investigation of the same key is still running (younger
        than claim_ttl_minutes). A finished row is claimed again, so a repair or a new
        investigation updates the same incident.
        """
        token = uuid.uuid4().hex
        params = {
            "job_id": key.job_id, "run_id": key.run_id, "task_key": key.task_key,
            "token": token, "trigger": trigger_source, "job_name": job_name,
            "ttl": self.cfg.claim_ttl_minutes,
        }
        self.sql.run(
            f"""
            MERGE INTO {self.incidents} t
            USING (SELECT :job_id AS job_id, :run_id AS run_id, :task_key AS task_key) s
            ON t.job_id = s.job_id AND t.run_id = s.run_id AND t.task_key = s.task_key
            WHEN MATCHED AND (t.status <> 'running'
                 OR t.updated_time < current_timestamp() - make_interval(0, 0, 0, 0, 0, :ttl, 0))
              THEN UPDATE SET status = 'running', claim_token = :token, trigger_source = :trigger,
                   updated_time = current_timestamp(), repair_count = coalesce(t.repair_count, 0) + 1
            WHEN NOT MATCHED THEN INSERT (job_id, run_id, task_key, job_name, trigger_source,
                   status, claim_token, repair_count, created_time, updated_time)
              VALUES (:job_id, :run_id, :task_key, :job_name, :trigger, 'running', :token, 0,
                   current_timestamp(), current_timestamp())
            """,
            params,
        )
        rows = self.sql.run(
            f"SELECT claim_token FROM {self.incidents} "
            "WHERE job_id = :job_id AND run_id = :run_id AND task_key = :task_key",
            params,
        )
        return bool(rows) and rows[0]["claim_token"] == token

    def exists(self, key: IncidentKey) -> bool:
        rows = self.sql.run(
            f"SELECT 1 AS x FROM {self.incidents} "
            "WHERE job_id = :job_id AND run_id = :run_id AND task_key = :task_key",
            {"job_id": key.job_id, "run_id": key.run_id, "task_key": key.task_key},
        )
        return bool(rows)

    def finish(self, incident: Incident, model_endpoint: str = "", agent_version: str = "") -> None:
        import json

        k = incident.key
        self.sql.run(
            f"""
            UPDATE {self.incidents} SET
              status = :status, root_cause_class = :cls, confidence = :conf,
              confidence_features = :feats, summary = :summary, blast_radius = :blast,
              recommendation = :rec, evidence = :evidence, rejected_hypotheses = :rejected,
              report_path = :report, mlflow_trace_id = :trace, task_run_id = :trid,
              tool_calls = :calls, input_tokens = :tin, output_tokens = :tout,
              duration_seconds = :dur, agent_version = :ver, model_endpoint = :model,
              job_name = :job_name, updated_time = current_timestamp()
            WHERE job_id = :job_id AND run_id = :run_id AND task_key = :task_key
            """,
            {
                "status": incident.status.value,
                "cls": incident.root_cause_class.value if incident.root_cause_class else None,
                "conf": incident.confidence.value if incident.confidence else None,
                "feats": ",".join(incident.confidence_features), "summary": incident.summary,
                "blast": json.dumps(incident.blast_radius), "rec": incident.recommendation,
                "evidence": json.dumps([e.model_dump(mode="json") for e in incident.evidence]),
                "rejected": json.dumps(incident.rejected_hypotheses),
                "report": incident.report_path, "trace": incident.mlflow_trace_id,
                "trid": incident.task_run_id, "calls": incident.tool_calls,
                "tin": incident.input_tokens, "tout": incident.output_tokens,
                "dur": incident.duration_seconds, "ver": agent_version, "model": model_endpoint,
                "job_name": incident.job_name,
                "job_id": k.job_id, "run_id": k.run_id, "task_key": k.task_key,
            },
        )
