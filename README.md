# sql-surgeon-mcp

An [MCP](https://modelcontextprotocol.io) server that connects Claude Desktop to a PostgreSQL database, exposing both direct query tools and an AI-powered query optimization pipeline built on [SQL-Surgeon](https://github.com/RachelHuangZW/SQL-Surgeon).

## What it does

This project wraps two layers of capability into a single MCP server:

**Layer 1 — Direct database tools:** Claude can execute SQL, inspect execution plans, and query table schemas against a live PostgreSQL database.

**Layer 2 — AI query optimization pipeline:** Claude can invoke a multi-step LangGraph agent (SQL-Surgeon) that analyzes a slow query, identifies performance bottlenecks, generates optimization advice, self-reviews the advice for quality, and optionally benchmarks the result in a sandbox schema.

The MCP interface means Claude decides which tool to use based on the user's question — no manual tool selection needed.

## Architecture

```
Claude Desktop
      │
      │  MCP protocol
      ▼
  server.py              ← tool registration + MCP entry point
      │
  tools.py               ← tool logic
      │
  ┌───┴──────────────────────────────────┐
  │                                      │
db.py                            sql_surgeon.agent.graph
(Layer 1: direct tools)          (Layer 2: LangGraph pipeline, from the
  │                               sql-surgeon package — see below)
  ├── execute_query                      │
  ├── explain_query          preprocess_sql → rewrite_sql → run_explain
  ├── list_tables                        │
  ├── get_table_schema           identify_issues → generate_advice
  └── get_slow_queries                   │              ▲
                                   review_advice ───────┘ retry loop (max 2x)
                                         │
                            generate_benchmark_schema (optional)
```

The pipeline is not copied into this repo. It comes from the [SQL-Surgeon](https://github.com/RachelHuangZW/SQL-Surgeon) project as the `sql-surgeon` package (`backend/` of that repo), pinned to a release tag in `pyproject.toml`. Fixes made in SQL-Surgeon reach this server by bumping that tag.

## MCP Tools

| Tool | Parameters | Description |
|------|------------|-------------|
| `execute_query` | `sql` | Run one read-only query (SELECT / WITH / VALUES / TABLE) and return JSON rows. Writes, DDL and multiple statements are rejected |
| `explain_query` | `sql`, `analyze` (bool, default `false`) | Get the execution plan of one read-only query; `analyze=true` runs `EXPLAIN (ANALYZE, BUFFERS)` in a read-only transaction |
| `list_tables` | `schema` (default `"public"`) | List all tables in a schema |
| `get_table_schema` | `table_name`, `schema` (default `"public"`) | List columns, types, nullability, defaults, and indexes |
| `get_slow_queries` | `limit` (default `5`) | Return the slowest queries by mean execution time from `pg_stat_statements` |
| `analyze_query` | `sql`, `ddl` (optional, auto-fetched if omitted), `table_name` (optional; any value turns on the sandbox benchmark) | Run full SQL-Surgeon optimization pipeline; returns issues, advice, warnings (SQL anti-patterns, tables without a primary key), index recommendations with reasons, optimized SQL, and optional benchmark |

## SQL-Surgeon Pipeline

`analyze_query` invokes the SQL-Surgeon LangGraph graph (7 nodes):

1. **preprocess_sql** — rewrites comma-style joins (`FROM a, b WHERE a.id = b.id`) into explicit `JOIN ... ON`
2. **rewrite_sql** — flags SQL anti-patterns (e.g. `SELECT *`) and drops redundant `DISTINCT`
3. **run_explain** — executes `EXPLAIN (ANALYZE, COSTS, VERBOSE, BUFFERS, FORMAT JSON)` in a read-only, time-limited transaction; fetches column definitions and existing indexes for every table in the query; flags tables without a primary key
4. **identify_issues** — sends the execution plan + DDL to Gemini 2.5 Pro; returns a JSON array of identified bottlenecks (missing indexes, sequential scans, row count misestimation, etc.)
5. **generate_advice** — generates specific optimization recommendations and a complete optimized SQL script (index DDL + rewritten query)
6. **review_advice** — a second LLM call acting as a senior DBA reviewer; returns `pass` or `retry` with feedback; a retry goes back to `generate_advice`, up to 2 times
7. **generate_benchmark_schema** (optional) — copies the query's tables into a temporary schema, applies the suggested DDL (only `CREATE INDEX` / `CREATE EXTENSION` / `ANALYZE` on the copies), and re-runs EXPLAIN to compare plans. Runs in one transaction that is always rolled back

## Project Layout

```
src/sql_surgeon_mcp/
    server.py           # MCP entry point, tool registrations
    tools.py            # Tool logic; calls db.py and the sql_surgeon pipeline
    db.py               # Connection helper (reads DATABASE_URL)
tests/
    test_tools.py       # Unit tests with mocked DB connections
examples/
    claude_desktop_config.json
```

## Setup

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) — `brew install uv`
- A running PostgreSQL instance
- [Claude Desktop](https://claude.ai/download)
- A Google API key (Gemini 2.5 Pro) for `analyze_query`

### Install

```bash
git clone https://github.com/RachelHuangZW/sql-surgeon-mcp
cd sql-surgeon-mcp
uv sync
```

### Configure environment

Create `.env` in the project root:

```
DATABASE_URL=postgresql://user:password@localhost:5432/dbname
GOOGLE_API_KEY=your-google-api-key
# Recommended: a SELECT-only role for SQL that comes from Claude (see Security below)
SURGEON_READONLY_DATABASE_URL=postgresql://sql_surgeon_readonly:password@localhost:5432/dbname
```

### Register with Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sql-surgeon-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/path/to/sql-surgeon-mcp",
        "--env-file", "/path/to/sql-surgeon-mcp/.env",
        "python", "-m", "sql_surgeon_mcp.server"
      ]
    }
  }
}
```

Fully quit and reopen Claude Desktop after saving.

### Verify

Open Claude Desktop and ask:

> "What MCP tools do you have available?"

Claude should list all six tools.

## Development

```bash
uv sync --group dev
uv run pytest
```

Tests use mocked database connections and do not require a live PostgreSQL instance.

### Updating the SQL-Surgeon engine

The pipeline (`sql_surgeon`) is a dependency, pinned to a tag of the SQL-Surgeon repo:

1. In SQL-Surgeon: commit the change, then `git tag vX.Y.Z && git push origin main --tags`
2. Here: change `tag = "..."` under `[tool.uv.sources]` in `pyproject.toml` to the new tag
3. `uv lock --upgrade-package sql-surgeon && uv sync`, then `uv run pytest`

To try an unreleased SQL-Surgeon change locally, temporarily point the source at your checkout instead of the tag:
`sql-surgeon = { path = "../SQL-Surgeon/backend", editable = true }` — and switch back before committing.

## Tech Stack

- **MCP framework:** [FastMCP](https://github.com/jlowin/fastmcp)
- **Agent framework:** [LangGraph](https://github.com/langchain-ai/langgraph)
- **LLM:** Gemini 2.5 Pro via `langchain-google-genai`
- **Database:** PostgreSQL via `psycopg2`
- **Package manager:** uv

## How this was built

This project was built with Claude Code as a pair-programming partner. I designed the architecture (two-layer tool exposure, separation of `db.py` vs `db_client.py`), made all technical decisions (MCP framework choice, LangGraph integration approach, security boundaries), and iterated on implementation with AI assistance. Every design decision documented in this README reflects my own thinking about MCP server design and enterprise database tool exposure.

## Security

SQL written by Claude never changes your data. `execute_query`, `explain_query` and `analyze_query` all go through the SQL-Surgeon DB client:

1. **One statement only.** Input such as `SELECT 1; COMMIT; DELETE ...` is rejected before it reaches the database.
2. **Queries only.** The statement must start with `SELECT`, `WITH`, `VALUES` or `TABLE`.
3. **READ ONLY transaction, never committed.** This also blocks data-modifying CTEs and `SELECT ... FOR UPDATE`.
4. **`statement_timeout` / `lock_timeout`.** Defaults are 5s / 2s. Set `SURGEON_STATEMENT_TIMEOUT_MS` / `SURGEON_LOCK_TIMEOUT_MS` to change them.
5. **Least-privilege role.** If `SURGEON_READONLY_DATABASE_URL` is set, these queries run as that role instead of `DATABASE_URL`. Create the role with [`scripts/setup_security_role.sql`](https://github.com/RachelHuangZW/SQL-Surgeon/blob/main/scripts/setup_security_role.sql) from SQL-Surgeon.

Two things still use `DATABASE_URL` directly:

- The sandbox benchmark, because it needs to create a schema. Only allowlisted index DDL runs there, and the transaction is always rolled back.
- The server's own fixed catalog queries (`list_tables`, `get_table_schema`, `get_slow_queries`).

Setting the read-only role is still recommended. The checks above are the first line of defense; the role is the backstop.
