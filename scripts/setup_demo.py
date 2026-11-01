"""Create the demo schemas and import the notebook that the demo job runs.

usage: python scripts/setup_demo.py --warehouse-id ID
"""
import argparse
import base64
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import ImportFormat, Language

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--warehouse-id", required=True)
    a = p.parse_args()
    w = WorkspaceClient()
    for stmt in ["CREATE CATALOG IF NOT EXISTS agent_demo",
                 "CREATE SCHEMA IF NOT EXISTS agent_demo.raw",
                 "CREATE SCHEMA IF NOT EXISTS agent_demo.silver",
                 "CREATE SCHEMA IF NOT EXISTS agent_demo.gold",
                 "CREATE OR REPLACE TABLE agent_demo.raw.fault_flag AS SELECT 'none' AS fault"]:
        w.statement_execution.execute_statement(statement=stmt, warehouse_id=a.warehouse_id,
                                                wait_timeout="50s")
        print("ok", stmt)
    w.workspace.mkdirs("/Shared/agent_demo")
    content = base64.b64encode((ROOT / "demo/notebooks/transform_sales.py").read_bytes()).decode()
    w.workspace.import_("/Shared/agent_demo/transform_sales", content=content,
                        format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=True)
    print("ok imported /Shared/agent_demo/transform_sales")


if __name__ == "__main__":
    main()
