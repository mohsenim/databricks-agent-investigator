# Environment check

| Check | Result | Detail |
| --- | --- | --- |
| system.lakeflow.job_task_run_timeline readable | pass | 0 row(s) |
| system.lakeflow.jobs readable | pass | 0 row(s) |
| system.access.table_lineage readable | pass | 1 row(s) |
| system.access.audit readable (permission changes) | pass | 1 row(s) |
| DESCRIBE HISTORY on demo table | FAIL | RuntimeError: [TABLE_OR_VIEW_NOT_FOUND] The table or view `agent_demo`.`raw`.`orders` cannot be found. Verify the spelling and correctness of the schema and catalog.
Search path: [`system`.`session`, `workspace`.`default`].
If you did not qualify the name with a schema, verify the current_schema() o |
| time travel VERSION AS OF ... LIMIT 0 | FAIL | RuntimeError: [TABLE_OR_VIEW_NOT_FOUND] The table or view `agent_demo`.`raw`.`orders` cannot be found. Verify the spelling and correctness of the schema and catalog.
Search path: [`system`.`session`, `system`.`builtin`, `system`.`ai`, `workspace`.`default`].
If you did not qualify the name with a sc |
| Jobs API runs/list | FAIL | StopIteration:  |
| model endpoint reachable | FAIL | ModuleNotFoundError: No module named 'databricks_langchain' |
