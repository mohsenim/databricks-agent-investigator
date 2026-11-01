# Databricks notebook source
# Builds gold.sales_report from silver.sales.

# COMMAND ----------
dbutils.widgets.text("catalog", "agent_demo")
catalog = dbutils.widgets.get("catalog")

# COMMAND ----------
spark.sql(f"""
  CREATE OR REPLACE TABLE {catalog}.gold.sales_report AS
  SELECT country, SUM(revenue) AS total_revenue FROM {catalog}.silver.sales GROUP BY country
""")
