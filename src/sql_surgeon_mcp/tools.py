import json

import psycopg2.extras

from sql_surgeon.agent.graph import app as agent_graph

from .db import get_connection

def execute_query(sql: str) -> str:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            if cur.description:
                rows = [dict(r) for r in cur.fetchall()]
                return json.dumps(rows, default=str, indent=2)
            conn.commit()
            return f"{cur.rowcount} rows affected"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def explain_query(sql: str, analyze: bool = False) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            prefix = (
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)"
                if analyze
                else "EXPLAIN (FORMAT TEXT)"
            )
            cur.execute(f"{prefix} {sql}")
            return "\n".join(row[0] for row in cur.fetchall())
    finally:
        conn.close()


def get_table_schema(table_name: str, schema: str = "public") -> str:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT column_name, data_type, character_maximum_length,
                       is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema, table_name),
            )
            columns = cur.fetchall()
            if not columns:
                return f"Table '{schema}.{table_name}' not found"

            cur.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
                (schema, table_name),
            )
            indexes = cur.fetchall()

        lines = [f"Table: {schema}.{table_name}\n", "Columns:"]
        for col in columns:
            dtype = col["data_type"]
            if col["character_maximum_length"]:
                dtype += f"({col['character_maximum_length']})"
            nullable = "NULL" if col["is_nullable"] == "YES" else "NOT NULL"
            default = f" DEFAULT {col['column_default']}" if col["column_default"] else ""
            lines.append(f"  {col['column_name']}: {dtype} {nullable}{default}")

        if indexes:
            lines.append("\nIndexes:")
            for idx in indexes:
                lines.append(f"  {idx['indexname']}: {idx['indexdef']}")

        return "\n".join(lines)
    finally:
        conn.close()


def list_tables(schema: str = "public") -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """,
                (schema,),
            )
            tables = [row[0] for row in cur.fetchall()]
            if not tables:
                return f"No tables found in schema '{schema}'"
            return "\n".join(tables)
    finally:
        conn.close()


def analyze_query(sql: str, ddl: str = "", table_name: str = "") -> str:
    """Run the SQL-Surgeon pipeline and return optimization advice."""
    # Empty ddl is fine: the pipeline's run_explain node fetches column definitions and
    # existing indexes for every table in the query (comma joins included).
    initial_state = {
        "original_sql": sql,
        "ddl": ddl,
        # Kept for MCP API compatibility: passing table_name has always meant "also run the sandbox benchmark".
        # The pipeline now finds the tables to copy from the SQL itself.
        "run_benchmark": bool(table_name),
        "issues": [],
        "advice": [],
        "retry_count": 0,
    }

    final_state = agent_graph.invoke(initial_state)

    return json.dumps({
        "issues": final_state.get("issues"),
        "advice": final_state.get("advice"),
        "warnings": final_state.get("rewrite_warnings"),
        "index_recommendations": final_state.get("filtered_indexes"),
        "optimized_sql": final_state.get("optimized_sql"),
        "benchmark_result": final_state.get("benchmark_result"),
        "error": final_state.get("error"),
    }, indent=2)


def get_slow_queries(limit: int = 5, include_system_queries: bool = False) -> str:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            filter_clause = "" if include_system_queries else """
                AND query NOT ILIKE 'CREATE EXTENSION%%'
                AND query NOT ILIKE '%%information_schema%%'
                AND query NOT ILIKE '%%pg_catalog%%'
                AND query NOT ILIKE 'EXPLAIN%%'
                AND query NOT ILIKE '%%pg_stat_statements%%'
            """
            cur.execute(f"""
                SELECT
                    query,
                    calls,
                    round(total_exec_time::numeric, 2) AS total_ms,
                    round(mean_exec_time::numeric, 2) AS mean_ms,
                    round(stddev_exec_time::numeric, 2) AS stddev_ms,
                    rows
                FROM pg_stat_statements
                WHERE calls > 0
                {filter_clause}
                ORDER BY mean_exec_time DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            if not rows:
                return "pg_stat_statements is empty or not enabled"
            return json.dumps([dict(r) for r in rows], default=str, indent=2)
    except Exception as e:
        if "pg_stat_statements" in str(e):
            return "pg_stat_statements extension is not enabled. Run: CREATE EXTENSION pg_stat_statements;"
        raise
    finally:
        conn.close()