# Databricks notebook source
# Writes raw.orders. The fault flag (set by demo/inject.py) changes what it writes.

# COMMAND ----------
dbutils.widgets.text("catalog", "agent_demo")
catalog = dbutils.widgets.get("catalog")

flag = spark.sql(f"SELECT fault FROM {catalog}.raw.fault_flag LIMIT 1").collect()
fault = flag[0]["fault"] if flag else "none"

# COMMAND ----------
if fault == "producer_fail":
    raise RuntimeError("generate_orders failed: source system unavailable")

rows = 0 if fault == "empty_data" else 1000
df = spark.sql(f"""
  SELECT id AS order_id,
         CAST(id % 50 AS BIGINT) AS customer_id,
         CAST((id % 97) + 0.99 AS DECIMAL(10,2)) AS amount,
         element_at(array('DE','FR','US','UK'), CAST(id % 4 AS INT) + 1) AS country,
         current_timestamp() AS order_ts
  FROM range({rows})
""")

if fault == "drop_column":
    df = df.drop("amount")
elif fault == "type_change":
    df = df.withColumn("amount", df["amount"].cast("string"))

(df.write.mode("overwrite").option("overwriteSchema", "true")
   .saveAsTable(f"{catalog}.raw.orders"))
