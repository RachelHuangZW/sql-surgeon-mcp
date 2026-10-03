import json
from unittest.mock import MagicMock, patch

import pytest

from sql_surgeon.db.client import UnsafeSQLError
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

@patch("sql_surgeon_mcp.tools.get_db_client")
def test_execute_query_select(mock_client):
    mock_client.return_value.run_query.return_value = [{"id": 1, "name": "alice"}]

    result = execute_query("SELECT * FROM users")
    assert json.loads(result) == [{"id": 1, "name": "alice"}]
    mock_client.return_value.run_query.assert_called_once_with("SELECT * FROM users")


WRITES = [
    "DELETE FROM users WHERE active = false",
    "UPDATE users SET name = 'x'",
    "CREATE TABLE pwned (x int)",
    "DROP TABLE users",
    "SELECT 1; COMMIT; DELETE FROM users",
    "CREATE TABLE evil AS SELECT 1",
]


@pytest.fixture
def no_db(monkeypatch):
    """Real DBClient guards, but fail the test if anything tries to reach a database."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@localhost/unused")
    monkeypatch.setattr("sql_surgeon.db.client._get_pool", MagicMock(side_effect=AssertionError("touched the DB")))
    monkeypatch.setattr("psycopg2.connect", MagicMock(side_effect=AssertionError("touched the DB")))


@pytest.mark.parametrize("sql", WRITES)
def test_execute_query_rejects_writes(no_db, sql):
    with pytest.raises(UnsafeSQLError):
        execute_query(sql)


# ── explain_query ────────────────────────────────────────────────────────────

@patch("sql_surgeon_mcp.tools.get_db_client")
def test_explain_query_no_analyze(mock_client):
    mock_client.return_value.explain_text.return_value = "Seq Scan on users  (cost=0.00..1.01)"

    result = explain_query("SELECT * FROM users")
    assert "Seq Scan" in result
    mock_client.return_value.explain_text.assert_called_once_with("SELECT * FROM users", False)


@patch("sql_surgeon_mcp.tools.get_db_client")
def test_explain_query_with_analyze(mock_client):
    mock_client.return_value.explain_text.return_value = "Seq Scan on users  (actual time=0.1..0.2)"

    result = explain_query("SELECT * FROM users", analyze=True)
    assert "actual time" in result
    mock_client.return_value.explain_text.assert_called_once_with("SELECT * FROM users", True)


@pytest.mark.parametrize("sql", WRITES)
@pytest.mark.parametrize("analyze", [False, True])
def test_explain_query_rejects_writes(no_db, sql, analyze):
    with pytest.raises(UnsafeSQLError):
        explain_query(sql, analyze=analyze)


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
