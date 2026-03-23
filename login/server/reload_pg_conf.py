import psycopg2
import os

try:
    conn = psycopg2.connect(
        host="localhost",
        database="trading_master_db",
        user="postgres",
        password="123456",
        port="5432"
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT pg_reload_conf()")
    print("PostgreSQL configuration reloaded successfully.")
    conn.close()
except Exception as e:
    print(f"Failed to reload config: {e}")
