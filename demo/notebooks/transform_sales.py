# Databricks notebook source
# Aggregates revenue by country and day into silver.sales.

# COMMAND ----------
dbutils.widgets.text("catalog", "agent_demo")
catalog = dbutils.widgets.get("catalog")

# COMMAND ----------
orders = spark.table(f"{catalog}.raw.orders")
sales = (orders
         .selectExpr("country", "date(order_ts) AS day", "amount")
         .groupBy("country", "day")
         .sum("amount")
         .withColumnRenamed("sum(amount)", "revenue"))
sales.write.mode("overwrite").saveAsTable(f"{catalog}.silver.sales")
