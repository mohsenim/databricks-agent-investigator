"""Settings from environment variables, with defaults for the demo."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Config:
    catalog: str = field(default_factory=lambda: _env("AGENT_CATALOG", "agent_demo"))
    schema: str = field(default_factory=lambda: _env("AGENT_SCHEMA", "incident_agent"))
    warehouse_id: str = field(default_factory=lambda: _env("AGENT_WAREHOUSE_ID", ""))
    model_endpoint: str = field(default_factory=lambda: _env("AGENT_MODEL_ENDPOINT", ""))
    investigator_job_id: str = field(default_factory=lambda: _env("AGENT_INVESTIGATOR_JOB_ID", ""))
    workspace_id: str = field(default_factory=lambda: _env("AGENT_WORKSPACE_ID", ""))

    # limits
    max_tool_calls: int = 25
    max_iterations: int = 8
    max_input_tokens: int = 60_000
    max_seconds: int = 600
    claim_ttl_minutes: int = 20

    # redaction and size limits
    max_text_chars: int = 4_000

    # poller defaults (the agent_settings table overrides them)
    poll_max_per_cycle: int = 5
    poll_lookback_hours: int = 12

    def table(self, name: str) -> str:
        return f"{self.catalog}.{self.schema}.{name}"
