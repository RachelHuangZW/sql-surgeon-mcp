import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from sql_surgeon.db.client import DBClient

load_dotenv()


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL environment variable is not set")
    return url


def get_connection() -> psycopg2.extensions.connection:
    """Connection for the server's own fixed catalog queries (never for SQL that came from the LLM)."""
    return psycopg2.connect(_database_url())


def get_db_client() -> DBClient:
    """Client for SQL that came from the LLM: single read-only statement, READ ONLY transaction,
    statement_timeout, and SURGEON_READONLY_DATABASE_URL when set. Never commits."""
    return DBClient(_database_url())
