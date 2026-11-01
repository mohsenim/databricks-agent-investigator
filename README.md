# Incident Investigation Agent for Databricks

This agent runs inside a Databricks workspace. When a job run fails, it looks for the reason, writes a short report and tells you how sure it is. It only reads from your jobs, tables and permissions. It never changes them.

## What it does

When a task in a monitored job fails, the agent:

1. Collects evidence with read-only tools: the error and stack trace, earlier runs of the same task, recent job changes, table history and schema changes, permission changes, lineage, the status of upstream jobs, and the code around the failing line.
2. Checks the evidence against a fixed list of failure classes: code or config error, upstream schema change, upstream data problem, upstream job failure, cluster or infrastructure error, permission change, library install failure, resource limit, and transient or unknown.
3. Works out the confidence (LOW, MEDIUM or HIGH) in plain code from the evidence. The model does not set it.
4. Writes one row to the `incidents` table and one Markdown report to a volume. The app shows both.

Every claim in a report points to an evidence item. A check rejects reports that cite evidence that does not exist.

A model (a serving endpoint in your workspace) helps in three places: it suggests hypotheses, it can ask for extra checks, and it writes the report text. If the model fails or answers badly, the agent still gives you the result from the rules, with a template report.

## How an investigation starts

| Mode | How |
| --- | --- |
| Push | A handler task inside the job starts the investigation as soon as a watched task fails. |
| Pull, scheduled | The `incident-poller` job runs on a schedule (every 10 minutes by default). It looks at the monitored jobs and investigates failed runs that have no incident yet. It also finds runs that failed before any task started. |
| Pull, on request | In the app, choose a monitored job, enter a run ID and press Investigate. |

All three give the same incident for the same failure. A failure is never investigated twice.

## Requirements

- A Databricks workspace with Unity Catalog and system tables.
- A model endpoint with tool calling (for example `databricks-gpt-oss-120b`).
- A SQL warehouse.
- Databricks Apps.
- On your machine: Python 3.11 or newer and the Databricks CLI, logged in with `databricks auth login`.
- For a team setup, an admin who can create two service principals and give them read access to `system.lakeflow` and `system.access`. If you work alone, you can skip this and run everything as yourself (see step 3).

## Install

1. Install the project and build the wheel:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev,databricks,app]" build
   python -m build --wheel && cp dist/*.whl app/
   ```
2. Deploy the investigator job, the poller job and the app:
   ```bash
   databricks bundle deploy -t dev \
     --var warehouse_id=<SQL_WAREHOUSE_ID> --var model_endpoint=<ENDPOINT_NAME>
   ```
3. Create the tables and the volume, and give out the grants. As a team, run it as an admin. The investigator principal is the identity that runs the investigator job. You can see the app principal with `databricks apps get incident-agent-app`:
   ```bash
   python scripts/setup.py --warehouse-id <ID> --investigator <SP_APP_ID> --app <APP_SP_APP_ID>
   ```
   Working alone, use `--skip-grants` and then give the app principal access to the schema with the statements in `sql/grants.sql`.
4. Check the environment:
   ```bash
   python scripts/verify_env.py --warehouse-id <ID> --model-endpoint <ENDPOINT_NAME>
   ```
   The result is also written to `docs/environment.md`.
5. For push only: give the identities that run your monitored jobs CAN MANAGE RUN on `incident-investigator`.

## Targets

| Target | Use | Names | Data |
| --- | --- | --- | --- |
| `dev` (default) | Development and testing. Also deploys the sample project. | Jobs start with `[dev <user>]`. App: `incident-agent-app`. | schema `incident_agent` |
| `prod` | The real deployment, after you tested in dev. | Jobs `incident-investigator` and `incident-poller`. App: `incident-agent-prod`. | schema `incident_agent_prod` |

Both can live in one workspace. For prod, use the same order as in the install steps: run `databricks bundle deploy -t prod --var warehouse_id=<ID> --var model_endpoint=<NAME>` first, then `python scripts/setup.py --warehouse-id <ID> --schema incident_agent_prod ...`. The sample project is not deployed to prod.

## Use

**Add a job to monitor.** Open the app, go to Settings, Monitored jobs. Pick the job from the list, or type its job ID, and press Add job. The list shows only the jobs that the app's service principal can view. Give it CAN VIEW on a job if you do not see it. Untick a job to stop monitoring it. 

**Optional: start at once (push).** Add a handler task to the job. See the `investigate_on_failure` task in `demo/resources/demo_jobs.yml`. It is a Run Job task for `incident-investigator`. Set Run if to "at least one failed", make it depend on the tasks you watch, pass `{{job.id}}`, `{{job.run_id}}` and the task values as parameters, and set retries to zero. Without this task the poller still finds the failures, as long as it is running.

**Read the results.** Open the app:
- **Incidents:** a list you can filter by job, class, confidence and status. Pick an incident to see the report, the model that wrote it and the evidence.
- **Dashboard:** incidents per day, token cost, and the mix of classes, confidence and triggers.
- **Settings:** monitored jobs, the model, the poller settings, the poller schedule, and the Investigate form.

You can also query the `incidents` table or open the report in the volume.

**Change the model.** In the app, go to Settings, Model. Pick a serving endpoint, or type its name, and press Save model. The investigator reads the setting at the start of each investigation, so the next one uses it. The default is the `model_endpoint` value you gave to `databricks bundle deploy`. Pick an endpoint that supports tool calling. In my tests `databricks-gpt-oss-120b` wrote full reports, while `databricks-meta-llama-3-3-70b-instruct` fell back to the template report.

**Change the poller schedule.** In Settings, Poller schedule, you can pause or resume the poller and set the interval. Deploy with `--var poller_pause_status=PAUSED` if you want it to start off.

## Sample project

The repository has a small sales pipeline that you can break on purpose.

- Catalog `agent_demo` with the schemas `raw`, `silver` and `gold`.
- Job `demo_ingest` writes `raw.orders`.
- Job `demo_daily_sales` reads it, writes `silver.sales` and `gold.sales_report`, and has the push handler.
- Job `demo_daily_sales_nohandler` is the same job without the handler. It is for the pull path.

Deploy and load it:

```bash
python scripts/setup_demo.py --warehouse-id <ID>      # catalog, schemas, transform notebook
databricks bundle deploy -t dev --var warehouse_id=<ID> --var model_endpoint=<NAME>
python demo/inject.py reset --warehouse-id <ID>
databricks bundle run demo_ingest -t dev
databricks bundle run demo_daily_sales -t dev         # succeeds, no incident
```
Then add `demo_daily_sales` and `demo_daily_sales_nohandler` as monitored jobs in the app (Settings).

**Break it and watch it get investigated**

```bash
python demo/inject.py drop_column --warehouse-id <ID> # raw.orders is written without "amount"
databricks bundle run demo_ingest -t dev
databricks bundle run demo_daily_sales -t dev         # transform_sales fails
```

1. The handler starts the investigator. The incident shows up in the app after about a minute or two.
2. Open the app and go to Incidents. The incident for `demo_daily_sales` has the class `upstream_schema_change` and confidence HIGH.
3. Open it. The report shows: the error (the column `amount` cannot be resolved), the table change that dropped the column and when it happened, a comparison of the two table versions, the fact that the same task worked in an earlier run, the tables that are affected, and a recommended action.
4. To try the pull path, run `databricks bundle run demo_daily_sales_nohandler -t dev`. The poller creates the incident within its interval. In the app you can also press Investigate for an older run.
5. Open the Dashboard to see the incidents by class and confidence.

Other faults: `type_change`, `empty_data`, `producer_fail`, `revoke`, `bad_library`, `code_bug`, `timeout`, `bad_node`, `run_limit`. To restore the healthy state, run `python demo/inject.py reset --warehouse-id <ID>`. The injector refuses to run outside the `agent_demo` catalog.

