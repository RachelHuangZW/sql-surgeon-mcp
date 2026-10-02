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
db.py                            agent/graph.py
(Layer 1: direct tools)          (Layer 2: LangGraph pipeline)
  │                                      │
  ├── execute_query          ┌───────────┼───────────┐
  ├── explain_query          ▼           ▼           ▼
  ├── list_tables       run_explain  identify_issues  generate_advice
  ├── get_table_schema       │           │           │
  └── get_slow_queries       └───────────┴───────────┘
                                         │
                                   review_advice  ←── retry loop (max 2x)
                                         │
                            generate_benchmark_schema (optional)
```

## MCP Tools

| Tool | Parameters | Description |
|------|------------|-------------|
| `execute_query` | `sql` | Run any SQL; SELECT returns JSON rows, DML returns affected row count |
| `explain_query` | `sql`, `analyze` (bool, default `false`) | Get query execution plan; `analyze=true` runs `EXPLAIN (ANALYZE, BUFFERS)` |
| `list_tables` | `schema` (default `"public"`) | List all tables in a schema |
| `get_table_schema` | `table_name`, `schema` (default `"public"`) | List columns, types, nullability, defaults, and indexes |
| `get_slow_queries` | `limit` (default `5`) | Return the slowest queries by mean execution time from `pg_stat_statements` |
| `analyze_query` | `sql`, `ddl` (optional, auto-fetched if omitted), `table_name` (optional) | Run full SQL-Surgeon optimization pipeline; returns issues, advice, optimized SQL, and optional benchmark |

## SQL-Surgeon Pipeline

`analyze_query` invokes a 5-node LangGraph graph:

1. **run_explain** — executes `EXPLAIN (ANALYZE, COSTS, VERBOSE, BUFFERS, FORMAT JSON)` against the real database
2. **identify_issues** — sends the execution plan + DDL to Gemini 2.5 Pro; returns a JSON array of identified bottlenecks (missing indexes, sequential scans, row count misestimation, etc.)
3. **generate_advice** — generates specific optimization recommendations and a complete optimized SQL script (index DDL + rewritten query)
4. **review_advice** — a second LLM call acting as a senior DBA reviewer; returns `pass` or `retry` with feedback; retries up to 2 times
5. **generate_benchmark_schema** (optional) — clones the target table into a temporary schema, applies the suggested DDL, and re-runs EXPLAIN to compare plans

## Project Layout

```
src/sql_surgeon_mcp/
    server.py           # MCP entry point, tool registrations
    tools.py            # Tool logic; calls db.py and agent/
    db.py               # Connection helper (reads DATABASE_URL)
    db_client.py        # DBClient used by the agent pipeline
    agent/
        graph.py        # LangGraph graph definition
        nodes.py        # 5 node functions
        state.py        # AgentState TypedDict
        prompts.py      # System prompts for each LLM node
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

## Tech Stack

- **MCP framework:** [FastMCP](https://github.com/jlowin/fastmcp)
- **Agent framework:** [LangGraph](https://github.com/langchain-ai/langgraph)
- **LLM:** Gemini 2.5 Pro via `langchain-google-genai`
- **Database:** PostgreSQL via `psycopg2`
- **Package manager:** uv

## How this was built

This project was built with Claude Code as a pair-programming partner. I designed the architecture (two-layer tool exposure, separation of `db.py` vs `db_client.py`), made all technical decisions (MCP framework choice, LangGraph integration approach, security boundaries), and iterated on implementation with AI assistance. Every design decision documented in this README reflects my own thinking about MCP server design and enterprise database tool exposure.

## Security Note

`execute_query` runs arbitrary SQL. Use a read-only database role in production or restrict access to trusted users only.
