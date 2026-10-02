import json
from unittest.mock import MagicMock, patch

import pytest

from sql_surgeon_mcp.tools import execute_query, explain_query, get_table_schema


def _make_cursor(rows=None, description=None, rowcount=0):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = description
    cur.rowcount = rowcount
    cur.fetchall.return_value = rows or []
    return cur


def _make_conn(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


# ── execute_query ────────────────────────────────────────────────────────────

@patch("sql_surgeon_mcp.tools.get_connection")
def test_execute_query_select(mock_conn):
    row = {"id": 1, "name": "alice"}
    cur = _make_cursor(rows=[row], description=["id", "name"])
    mock_conn.return_value = _make_conn(cur)

    result = execute_query("SELECT * FROM users")
    data = json.loads(result)
    assert data == [{"id": 1, "name": "alice"}]


@patch("sql_surgeon_mcp.tools.get_connection")
def test_execute_query_dml(mock_conn):
    cur = _make_cursor(description=None, rowcount=3)
    mock_conn.return_value = _make_conn(cur)

    result = execute_query("DELETE FROM users WHERE active = false")
    assert "3" in result


@patch("sql_surgeon_mcp.tools.get_connection")
def test_execute_query_error_rolls_back(mock_conn):
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.execute.side_effect = Exception("syntax error")
    conn.cursor.return_value = cur
    mock_conn.return_value = conn

    with pytest.raises(Exception, match="syntax error"):
        execute_query("BAD SQL")

    conn.rollback.assert_called_once()


# ── explain_query ────────────────────────────────────────────────────────────

@patch("sql_surgeon_mcp.tools.get_connection")
def test_explain_query_no_analyze(mock_conn):
    cur = _make_cursor(rows=[("Seq Scan on users",), ("  cost=0.00..1.01",)])
    mock_conn.return_value = _make_conn(cur)

    result = explain_query("SELECT * FROM users")
    assert "Seq Scan" in result
    cur.execute.assert_called_once()
    sql_used = cur.execute.call_args[0][0]
    assert sql_used.startswith("EXPLAIN (FORMAT TEXT)")
    assert "ANALYZE" not in sql_used


@patch("sql_surgeon_mcp.tools.get_connection")
def test_explain_query_with_analyze(mock_conn):
    cur = _make_cursor(rows=[("Seq Scan on users  (actual time=0.1..0.2)",)])
    mock_conn.return_value = _make_conn(cur)

    result = explain_query("SELECT * FROM users", analyze=True)
    assert "actual time" in result
    sql_used = cur.execute.call_args[0][0]
    assert "ANALYZE" in sql_used


# ── get_table_schema ─────────────────────────────────────────────────────────

@patch("sql_surgeon_mcp.tools.get_connection")
def test_get_table_schema_not_found(mock_conn):
    cur = _make_cursor(rows=[])
    mock_conn.return_value = _make_conn(cur)

    result = get_table_schema("nonexistent")
    assert "not found" in result


@patch("sql_surgeon_mcp.tools.get_connection")
def test_get_table_schema_columns_and_indexes(mock_conn):
    columns = [
        {
            "column_name": "id",
            "data_type": "integer",
            "character_maximum_length": None,
            "is_nullable": "NO",
            "column_default": "nextval('users_id_seq'::regclass)",
        },
        {
            "column_name": "email",
            "data_type": "character varying",
            "character_maximum_length": 255,
            "is_nullable": "NO",
            "column_default": None,
        },
    ]
    indexes = [{"indexname": "users_pkey", "indexdef": "CREATE UNIQUE INDEX users_pkey ON users USING btree (id)"}]

    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.side_effect = [columns, indexes]
    conn.cursor.return_value = cur
    mock_conn.return_value = conn

    result = get_table_schema("users")
    assert "id: integer NOT NULL" in result
    assert "email: character varying(255) NOT NULL" in result
    assert "users_pkey" in result
