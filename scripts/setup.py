"""Create the agent's schema, tables and volume, and give out the grants.

usage: python scripts/setup.py --warehouse-id ID --investigator SP --app SP [--catalog agent_demo]
Run it as a workspace admin. The catalog must already exist.
"""
import argparse

from databricks.sdk import WorkspaceClient

from incident_agent.setup_sql import render


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--catalog", default="agent_demo")
    p.add_argument("--schema", default="incident_agent")
    p.add_argument("--investigator", help="application ID of the investigator service principal")
    p.add_argument("--app", help="application ID of the app service principal")
    p.add_argument("--skip-grants", action="store_true")
    a = p.parse_args()

    w = WorkspaceClient()
    base = {"catalog": a.catalog, "schema": a.schema}
    statements = render("create_objects.sql", base)
    if not a.skip_grants:
        if not (a.investigator and a.app):
            p.error("--investigator and --app are required unless --skip-grants is set")
        statements += render("grants.sql", {**base, "investigator": a.investigator, "app": a.app})
    for s in statements:
        r = w.statement_execution.execute_statement(statement=s, warehouse_id=a.warehouse_id,
                                                    wait_timeout="50s")
        state = r.status.state.value if r.status and r.status.state else "?"
        print(f"{state:10} {s.splitlines()[0][:90]}")
        if state != "SUCCEEDED":
            raise SystemExit(r.status.error.message if r.status and r.status.error else state)


if __name__ == "__main__":
    main()
