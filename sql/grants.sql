-- Placeholders: ${catalog}, ${schema}, ${investigator}, ${app}.
-- Run once as an admin. ${investigator} and ${app} are application IDs of service principals.

-- System tables (investigator). A metastore admin has to grant this.
GRANT USE CATALOG ON CATALOG system TO `${investigator}`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow TO `${investigator}`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access TO `${investigator}`;

-- The agent's own objects.
GRANT USE CATALOG ON CATALOG ${catalog} TO `${investigator}`;
GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA ${catalog}.${schema} TO `${investigator}`;
GRANT WRITE VOLUME, READ VOLUME ON VOLUME ${catalog}.${schema}.reports TO `${investigator}`;

-- The app reads the results and edits its settings tables.
GRANT USE CATALOG ON CATALOG ${catalog} TO `${app}`;
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.${schema} TO `${app}`;
GRANT MODIFY ON TABLE ${catalog}.${schema}.monitored_jobs TO `${app}`;
GRANT MODIFY ON TABLE ${catalog}.${schema}.agent_settings TO `${app}`;
GRANT READ VOLUME ON VOLUME ${catalog}.${schema}.reports TO `${app}`;

-- Tables of the monitored jobs: DESCRIBE HISTORY and schema reads. scripts/verify_env.py
-- shows which privilege is needed; SELECT is the usual one. No tool reads rows.
-- GRANT SELECT ON SCHEMA ${catalog}.raw TO `${investigator}`;
-- GRANT SELECT ON SCHEMA ${catalog}.silver TO `${investigator}`;
