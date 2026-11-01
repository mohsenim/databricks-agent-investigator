-- ${catalog} and ${schema} are replaced by scripts/setup.py.
CREATE SCHEMA IF NOT EXISTS ${catalog}.${schema};

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.incidents (
  job_id BIGINT, run_id BIGINT, task_key STRING,
  task_run_id BIGINT, repair_count INT,
  job_name STRING, trigger_source STRING,
  created_time TIMESTAMP, updated_time TIMESTAMP,
  status STRING, claim_token STRING,
  root_cause_class STRING, confidence STRING,
  confidence_features STRING, summary STRING,
  blast_radius STRING, recommendation STRING,
  evidence STRING, rejected_hypotheses STRING,
  report_path STRING, mlflow_trace_id STRING,
  tool_calls INT, input_tokens INT, output_tokens INT, duration_seconds DOUBLE,
  agent_version STRING, model_endpoint STRING
);

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.monitored_jobs (
  job_id BIGINT, job_name STRING, enabled BOOLEAN, added_time TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.agent_settings (key STRING, value STRING);

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.poller_state (last_checked TIMESTAMP);

CREATE VOLUME IF NOT EXISTS ${catalog}.${schema}.reports;
