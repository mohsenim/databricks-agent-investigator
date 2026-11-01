"""Web app: incidents, dashboard and settings."""
import json
import os

import pandas as pd
import streamlit as st
from data import CLASSES, CONFIDENCE, STATUS, Data, cron_for, minutes_from, sanitize_markdown
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import CronSchedule, PauseStatus

from incident_agent.clients import SdkSqlRunner

CATALOG = os.environ.get("AGENT_CATALOG", "agent_demo")
SCHEMA = os.environ.get("AGENT_SCHEMA", "incident_agent")
INVESTIGATOR = int(os.environ.get("AGENT_INVESTIGATOR_JOB_ID", "0") or 0)
POLLER = int(os.environ.get("AGENT_POLLER_JOB_ID", "0") or 0)
WAREHOUSE = os.environ.get("AGENT_WAREHOUSE_ID", "")
DEFAULT_MODEL = os.environ.get("AGENT_MODEL_ENDPOINT", "")

st.set_page_config(page_title="Incident Investigation Agent", layout="wide")
if not WAREHOUSE:
    st.error("AGENT_WAREHOUSE_ID is not set. Set it in the app configuration and redeploy.")
    st.stop()


@st.cache_resource
def clients():
    w = WorkspaceClient()
    return w, Data(SdkSqlRunner(w, WAREHOUSE), CATALOG, SCHEMA)


w, data = clients()


def read_report(path: str) -> str:
    try:
        return w.files.download(path).contents.read().decode("utf-8")
    except Exception as exc:  # the report is missing or cannot be read
        return f"Report not available: {exc}"


def incidents_page() -> None:
    st.header("Incidents")
    c1, c2, c3, c4 = st.columns(4)
    job = c1.text_input("Job name contains")
    classes = c2.multiselect("Root cause class", CLASSES)
    conf = c3.multiselect("Confidence", CONFIDENCE)
    status = c4.multiselect("Status", STATUS)
    rows = data.incidents(job, classes, conf, status)
    if not rows:
        st.info("No incidents match.")
        return
    df = pd.DataFrame(rows)
    st.dataframe(df.drop(columns=["report_path"]), use_container_width=True, hide_index=True)
    labels = [f"{r['job_name']} / {r['task_key'] or 'run'} / run {r['run_id']}" for r in rows]
    picked = st.selectbox("Open incident", range(len(rows)), format_func=lambda i: labels[i])
    row = rows[picked]
    inc = data.incident(int(row["job_id"]), int(row["run_id"]), row["task_key"])
    if not inc:
        return
    st.subheader(f"{inc['root_cause_class']}  ·  confidence {inc['confidence']}  ·  {inc['status']}")
    st.caption(f"Model: {inc.get('model_endpoint') or 'none (template report)'}")
    if inc.get("report_path"):
        st.markdown(sanitize_markdown(read_report(inc["report_path"])))
    with st.expander("Evidence (JSON)"):
        st.json(json.loads(inc["evidence"] or "[]"))


def dashboard_page() -> None:
    st.header("Dashboard")
    days = st.slider("Days", 7, 90, 30)
    a, b = st.columns(2)
    a.subheader("Incidents per day")
    per_day = data.per_day(days)
    if per_day:
        a.bar_chart(pd.DataFrame(per_day).set_index("day"))
    b.subheader("Cost per day (tokens)")
    cost = data.cost(days)
    if cost:
        b.line_chart(pd.DataFrame(cost).set_index("day")[["input_tokens", "output_tokens"]])
    c, d, e = st.columns(3)
    for col, name, title in ((c, "root_cause_class", "Root cause class"),
                             (d, "confidence", "Confidence"), (e, "trigger_source", "Trigger")):
        col.subheader(title)
        mix = data.mix(name)
        if mix:
            col.bar_chart(pd.DataFrame(mix).set_index("label"))


def _poller_settings(schedule, cron: str, run: bool):
    from databricks.sdk.service.jobs import JobSettings

    return JobSettings(schedule=CronSchedule(
        quartz_cron_expression=cron, timezone_id=schedule.timezone_id or "UTC",
        pause_status=PauseStatus.UNPAUSED if run else PauseStatus.PAUSED))


def settings_page() -> None:
    st.header("Settings")
    st.subheader("Monitored jobs")
    for r in data.monitored():
        on = st.checkbox(f"{r['job_name']} ({r['job_id']})", value=bool(r["enabled"]),
                         key=f"mon{r['job_id']}")
        if on != bool(r["enabled"]):
            data.set_monitored_enabled(int(r["job_id"]), on)
            st.rerun()
    monitored_ids = {int(r["job_id"]) for r in data.monitored()}
    try:
        visible = {j.settings.name: j.job_id for j in w.jobs.list(limit=100)
                   if j.settings and j.job_id not in monitored_ids
                   and j.job_id not in (INVESTIGATOR, POLLER)}
    except Exception as exc:  # the app may not be allowed to list jobs
        visible = {}
        st.caption(f"Cannot list jobs: {exc}")
    with st.form("add"):
        choice = st.selectbox("Job", ["(choose)"] + sorted(visible))
        typed = st.number_input("or a job ID (for jobs not in the list)", min_value=0, step=1)
        if st.form_submit_button("Add job"):
            job_id = visible.get(choice) or int(typed)
            if not job_id:
                st.error("Choose a job or enter a job ID.")
            elif job_id in (INVESTIGATOR, POLLER):
                st.error("The agent's own jobs cannot be monitored.")
            else:
                try:
                    name = w.jobs.get(job_id=int(job_id)).settings.name
                except Exception as exc:
                    st.error(f"Cannot read job {int(job_id)}: {exc}")
                else:
                    data.add_monitored(int(job_id), name)
                    st.rerun()
    st.caption("The list shows the jobs the app's service principal can view. Grant it CAN VIEW "
               "on a job to see it here.")

    st.subheader("Model")
    s = data.settings()
    current = s.get("model_endpoint") or DEFAULT_MODEL
    st.write(f"Current model endpoint: **{current or 'none (reports use a template)'}**"
             + ("" if s.get("model_endpoint") else "  (deployed default)"))
    try:
        endpoints = sorted(e.name for e in w.serving_endpoints.list() if e.name)
    except Exception as exc:  # the app may not be allowed to list endpoints
        endpoints = []
        st.caption(f"Cannot list serving endpoints: {exc}")
    with st.form("model"):
        pick = st.selectbox("Serving endpoint", ["(keep current)"] + endpoints)
        typed = st.text_input("or an endpoint name")
        if st.form_submit_button("Save model"):
            name = typed.strip() or (pick if pick != "(keep current)" else "")
            if name:
                data.set_setting("model_endpoint", name)
                st.success(f"The next investigation uses {name}.")
                st.rerun()
    st.caption("The endpoint needs tool calling. The investigator reads this setting at the "
               "start of every investigation, so no redeploy is needed.")

    st.subheader("Poller")
    cap = st.number_input("Max investigations per cycle", 1, 50, int(s.get("max_per_cycle", 5)))
    look = st.number_input("Lookback hours", 1, 168, int(s.get("lookback_hours", 12)))
    if st.button("Save poller settings"):
        data.set_setting("max_per_cycle", str(int(cap)))
        data.set_setting("lookback_hours", str(int(look)))
        st.success("Saved")
    st.caption(f"Last poller run: {data.last_poll()}")

    st.subheader("Poller schedule")
    schedule = None
    if POLLER:
        try:
            schedule = w.jobs.get(job_id=POLLER).settings.schedule
        except Exception as exc:  # the app needs CAN MANAGE on the poller job
            st.error(f"Cannot read the poller job: {exc}")
    if schedule is None:
        st.write("The poller job has no schedule, or it cannot be read.")
    else:
        paused = schedule.pause_status == PauseStatus.PAUSED
        every = minutes_from(schedule.quartz_cron_expression)
        st.write(f"Scheduled poller: **{'paused' if paused else 'running'}**, "
                 + (f"every {every} minutes" if every else f"cron `{schedule.quartz_cron_expression}`"))
        options = [5, 10, 15, 30, 60]
        with st.form("schedule"):
            minutes = st.selectbox("Interval (minutes)", options,
                                   index=options.index(every) if every in options else 1)
            run = st.checkbox("Run on this schedule", value=not paused)
            if st.form_submit_button("Save schedule"):
                try:
                    w.jobs.update(job_id=POLLER, new_settings=_poller_settings(
                        schedule, cron_for(int(minutes)), run))
                    st.rerun()
                except Exception as exc:
                    st.error(f"Cannot update the schedule: {exc}")

    st.subheader("Investigate a run")
    with st.form("investigate"):
        names = {f"{r['job_name']} ({r['job_id']})": int(r["job_id"]) for r in data.monitored()}
        jid = names.get(st.selectbox("Monitored job", sorted(names)), 0)
        rid = st.number_input("Run ID", min_value=1, step=1)
        tk = st.text_input("Task key (empty for the whole run)")
        if st.form_submit_button("Investigate"):
            if not jid or not data.is_monitored(int(jid)):
                st.error("Choose a monitored job.")
            else:
                params = {"job_id": str(int(jid)), "run_id": str(int(rid)), "task_key": tk,
                          "trigger_source": "request"}
                run = w.jobs.run_now(job_id=INVESTIGATOR, job_parameters=params)
                st.success(f"Started investigator run {run.run_id}")


page = st.sidebar.radio("Page", ["Incidents", "Dashboard", "Settings"])
{"Incidents": incidents_page, "Dashboard": dashboard_page, "Settings": settings_page}[page]()
