# pgpacer

**A Postgres MCP server with index tuning, explain plans, health checks, and safe sql execution.**

**[Overview](#overview)** • **[Quick Start](#quick-start)** • **[Configuration Reference](#configuration-reference)** • **[Technical Notes](#technical-notes)** • **[MCP API](#mcp-server-api)** • **[FAQ](#frequently-asked-questions)**

## Overview

**pgpacer** is an open source Model Context Protocol (MCP) server built to support you and your AI agents throughout the **entire development process**—from initial coding, through testing and deployment, and to production tuning and maintenance.

pgpacer does much more than wrap a database connection.

Features include:

- **🔍 Database Health** - analyze index health, connection utilization, buffer cache, vacuum health, sequence limits, replication lag, and more.
- **⚡ Index Tuning** - explore thousands of possible indexes to find the best solution for your workload, using industrial-strength algorithms.
- **📈 Query Plans** - validate and optimize performance by reviewing EXPLAIN plans and simulating the impact of hypothetical indexes.
- **🧠 Schema Intelligence** - context-aware SQL generation based on detailed understanding of the database schema.
- **🔎 Semantic Search** - knowledge maps for natural-language discovery of tables and columns, and golden queries (vetted, reusable business SQL), stored with the open-source [pgvector](https://github.com/pgvector/pgvector) extension.
- **🛡️ Safe SQL Execution** - configurable access control, including support for read-only mode and safe SQL parsing, making it usable for both development and production.
- **🔐 OAuth 2.0 Authentication** - optional token-based authentication supporting both RFC 7662 token introspection and local JWT validation (RS256, ES256), protecting all MCP tools with bearer tokens.

pgpacer supports the following MCP transports for flexibility in different environments:

- [Standard Input/Output (stdio)](https://modelcontextprotocol.io/docs/concepts/transports#standard-input%2Foutput-stdio)
- [Server-Sent Events (SSE)](https://modelcontextprotocol.io/docs/concepts/transports#server-sent-events-sse)
- [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#streamable-http)

### Why "pgpacer"?

In a marathon, a pacer runs at the front of a group and sets the pace so everyone behind them reaches their goal.
pgpacer does the same for AI agents working with PostgreSQL. It leads them through an unfamiliar database with knowledge maps of your tables and columns, vetted golden queries, and safe SQL execution.
And it keeps the database itself at pace with index tuning, query plans, and health checks.

The project was previously called PG Airman MCP; see [Migrating from pg-airman-mcp](#migrating-from-pg-airman-mcp).

## Quick Start

### Prerequisites

Before getting started, ensure you have:

1. Access credentials for your database.
2. Docker *or* Python 3.12 or higher.

#### Access Credentials

You can confirm your access credentials are valid by using `psql` or a GUI tool such as [pgAdmin](https://www.pgadmin.org/).

#### Docker or Python

The choice to use Docker or Python is yours.
We generally recommend Docker because Python users can encounter more environment-specific issues.
However, it often makes sense to use whichever method you are most familiar with.

### Installation

Choose one of the following methods to install pgpacer:

#### Option 1: Using Docker

Build the pgpacer server Docker image from a clone of this repository.
This image contains all necessary dependencies, providing a reliable way to run pgpacer in a variety of environments.

```bash
git clone https://github.com/aysebilgegunduz/pgpacer.git
cd pgpacer
docker build -t pgpacer .
```

#### Option 2: Using Python

If you have `pipx` installed you can install pgpacer with:

```bash
pipx install pgpacer
```

Otherwise, install pgpacer with `uv`:

```bash
uv pip install pgpacer
```

Either way, the command is `pgpacer`. To try unreleased changes, install from this repository instead: `pipx install git+https://github.com/aysebilgegunduz/pgpacer.git`.

If you need to install `uv`, see the [uv installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

> **IMPORTANT:** pgpacer requires **Python 3.12+** and the [`psycopg`](https://www.psycopg.org/) package for database connectivity.  
> If you install with Docker, all dependencies are included — no extra setup is needed.
>
> If you install with Python (`pipx` or `uv`), you may need:
>
> - A C compiler (e.g., `gcc`, `clang`)
> - PostgreSQL client libraries and headers (e.g., `libpq-dev` on Debian/Ubuntu)
> - Python development headers (e.g., `python3.12-dev` on Debian/Ubuntu)
> - The `pg_config` utility in your system `PATH`
>
> If you see errors about missing `libpq` or `pg_config`, install the relevant packages for your OS.  
> See the [psycopg installation docs](https://www.psycopg.org/psycopg3/docs/basic/install.html#install-pre) for details.

### Configure Your AI Assistant

We provide full instructions for configuring pgpacer with Claude Desktop.
Many MCP clients have similar configuration files, you can adapt these steps to work with the client of your choice.

#### Claude Desktop Configuration

You will need to edit the Claude Desktop configuration file to add pgpacer.
The location of this file depends on your operating system:

- MacOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%/Claude/claude_desktop_config.json`

You can also use `Settings` menu item in Claude Desktop to locate the configuration file.

You will now edit the `mcpServers` section of the configuration file.

##### If you are using Docker

```json
{
  "mcpServers": {
    "postgres": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "PGPACER_DATABASE_URL",
        "pgpacer",
        "--access-mode=unrestricted"
      ],
      "env": {
        "PGPACER_DATABASE_URL": "postgresql://username:password@localhost:5432/dbname"
      }
    }
  }
}
```

The pgpacer Docker image will automatically remap the hostname `localhost` to work from inside of the container.

- MacOS/Windows: Uses `host.docker.internal` automatically
- Linux: Uses `172.17.0.1` or the appropriate host address automatically

##### If you are using `pipx`

```json
{
  "mcpServers": {
    "postgres": {
      "command": "pgpacer",
      "args": [
        "--access-mode=unrestricted"
      ],
      "env": {
        "PGPACER_DATABASE_URL": "postgresql://username:password@localhost:5432/dbname"
      }
    }
  }
}
```

##### If you are using `uv`

```json
{
  "mcpServers": {
    "postgres": {
      "command": "uv",
      "args": [
        "run",
        "pgpacer",
        "--access-mode=unrestricted"
      ],
      "env": {
        "PGPACER_DATABASE_URL": "postgresql://username:password@localhost:5432/dbname"
      }
    }
  }
}
```

> **Note:** The examples above show basic configuration without authentication. To enable OAuth 2.0 authentication, see [Authentication](#authentication-optional) for Claude Desktop configuration examples with auth enabled.

##### Connection URI

Replace `postgresql://...` with your [Postgres database connection URI](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING-URIS).

##### Access Mode

pgpacer supports multiple *access modes* to give you control over the operations that the AI agent can perform on the database:

- **Unrestricted Mode**: Allows full read/write access to modify data and schema. It is suitable for development environments.
- **Restricted Mode**: Limits operations to read-only transactions and imposes constraints on resource utilization (presently only execution time). It is suitable for production environments.

To use restricted mode, replace `--access-mode=unrestricted` with `--access-mode=restricted` in the configuration examples above.

### Safe Metadata Writes (Allowlist)

In restricted mode the server enforces read-only execution for regular queries via `SafeSqlDriver`. Some lightweight metadata operations can be explicitly allowed through a controlled **safe metadata allowlist**. Currently only `COMMENT ON` statements are supported via the `add_comment_to_object` tool.

The behavior is controlled by the environment variable:

```bash
ALLOW_COMMENT_IN_RESTRICTED=true   # default: true
```

Set this variable to `false` to block comment writes when running with `--access-mode=restricted`.

Rationale:

- Keeps the semantics of restricted mode mostly read-only while permitting non-destructive documentation changes.
- Prevents ad-hoc unwrapping of the safe driver throughout the codebase—metadata writes route through a single helper (`execute_comment_on`).
- Provides a clear extension point for future safe metadata operations if needed.

When a comment is written in restricted mode the server logs:

```text
COMMENT ON <KIND> <qualified_identifier> length=<N> in restricted mode
```

If disabled you will receive an error: `COMMENT ON is not permitted in restricted mode`.

### Golden Query Writes (Allowlist)

Similarly, golden query write operations (`create_golden_query`, `delete_golden_query`) can be permitted in restricted mode via:

```bash
ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED=false   # default: false
```

Set this variable to `true` to allow creating and deleting golden queries when running with `--access-mode=restricted`. When enabled, golden query write operations bypass `SafeSqlDriver` and use the underlying driver directly (same pattern as comment writes). The query itself must still pass restricted-mode validation. Read-only operations (`search_golden_queries`, `execute_golden_query`, `list_golden_queries`) are always allowed regardless of this flag.

When a golden query write occurs in restricted mode the server logs:

```text
Golden query write in restricted mode (ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED=true)
```

If disabled you will receive an error: `Golden query writes are not permitted in restricted mode`.

#### Other MCP Clients

Many MCP clients have similar configuration files to Claude Desktop, and you can adapt the examples above to work with the client of your choice.

- If you are using Cursor, you can navigate from the `Command Palette` to `Cursor Settings`, then open the `MCP` tab to access the configuration file.
- If you are using Windsurf, you can navigate from the `Command Palette` to `Open Windsurf Settings Page` to access the configuration file.
- If you are using Goose run `goose configure`, then select `Add Extension`.

## SSE Transport

pgpacer supports the [SSE transport](https://modelcontextprotocol.io/docs/concepts/transports#server-sent-events-sse), which allows multiple MCP clients to share one server, possibly a remote server.
To use the SSE transport, you need to start the pgpacer server with the `--transport=sse` option.

For example, with Docker run:

```bash
docker run -p 8000:8000 \
  -e PGPACER_DATABASE_URL=postgresql://username:password@localhost:5432/dbname \
  pgpacer --access-mode=unrestricted --transport=sse
```

Then update your MCP client configuration to call the pgpacer server.
For example, in Cursor's `mcp.json` or Cline's `cline_mcp_settings.json` you can put:

```json
{
    "mcpServers": {
        "postgres": {
            "type": "sse",
            "url": "http://localhost:8000/sse"
        }
    }
}
```

For Windsurf, the format in `mcp_config.json` is slightly different:

```json
{
    "mcpServers": {
        "postgres": {
            "type": "sse",
            "serverUrl": "http://localhost:8000/sse"
        }
    }
}
```

## Streamable HTTP Transport

pgpacer supports the [Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#streamable-http), which allows multiple MCP clients to share one server, possibly a remote server.
To use the Streamable HTTP transport, you need to start the server with the `--transport=streamable-http` option.

For example, with Docker run:

```bash
docker run -p 8000:8000 \
  -e PGPACER_DATABASE_URL=postgresql://username:password@localhost:5432/dbname \
  pgpacer --access-mode=unrestricted --transport=streamable-http
```

Then update your MCP client configuration to call the MCP server.
For example, in Cursor's `mcp.json` you can put:

```json
{
    "mcpServers": {
        "pgpacer": {
            "url": "http://localhost:8000/mcp"
        }
    }
}
```

For Windsurf, the format in `mcp_config.json` is slightly different:

```json
{
    "mcpServers": {
        "postgres": {
            "serverUrl": "http://localhost:8000/mcp"
        }
    }
}
```

## DNS Rebinding Protection

When using SSE or Streamable HTTP transports, the MCP SDK includes [DNS rebinding protection](https://github.com/modelcontextprotocol/python-sdk/issues/1798) that validates incoming `Host` headers against an allowlist.
This can cause **HTTP 421 Misdirected Request** errors when the server runs behind a reverse proxy, load balancer, or Kubernetes service where the `Host` header differs from `localhost`.

By default, DNS rebinding protection is **disabled** to avoid breaking deployments behind proxies or Kubernetes services.
You can enable and configure it explicitly using environment variables or CLI arguments.

### Enabling Protection

To enable protection with the SDK's built-in localhost allowlist:

```bash
PGPACER_DNS_REBINDING_PROTECTION=true
```

Or via CLI:

```bash
pgpacer "postgresql://..." --transport=streamable-http --dns-rebinding-protection
```

### Custom Host Allowlist

To enable protection with a specific allowlist (setting `allowed_hosts` automatically enables protection):

```bash
PGPACER_ALLOWED_HOSTS="pgpacer-service:*,localhost:*"
PGPACER_ALLOWED_ORIGINS="http://pgpacer-service:*,http://localhost:*"
```

Wildcard port patterns (e.g., `myservice:*`) match any port on that host.

## Authentication (Optional)

pgpacer supports OAuth 2.0 authentication to protect all MCP tools with bearer tokens.
Two verification methods are available:

- **Token Introspection** (default): Validates tokens by calling the authorization server's [introspection endpoint (RFC 7662)](https://datatracker.ietf.org/doc/html/rfc7662). Best when you need real-time revocation checking.
- **JWT Verification**: Validates tokens locally using public keys from a [JWKS endpoint (RFC 7517)](https://datatracker.ietf.org/doc/html/rfc7517). Best for low-latency validation without per-request network calls.

Authentication is disabled by default.
When disabled, the server operates exactly as before with no authentication overhead.

For detailed guidance on choosing between introspection and JWT, provider-specific examples, and migration instructions, see [JWT Authentication Guide](docs/jwt-authentication.md).

### How It Works

1. An MCP client obtains an access token from the authorization server.
2. The client includes the token in requests to the MCP server via the `Authorization: Bearer {token}` header.
3. The MCP server validates the token using the configured method:
   - **Introspection**: Sends the token to the authorization server's introspection endpoint.
   - **JWT**: Verifies the token signature locally using cached public keys from the JWKS endpoint.
4. If the token is active and valid, the request proceeds; otherwise, a `401 Unauthorized` response is returned.

### Enabling Authentication

#### Token Introspection (default)

```bash
PGPACER_AUTH_ENABLED=true
PGPACER_AUTH_SERVER_URL=http://localhost:9000
```

Or via CLI:

```bash
pgpacer "postgresql://..." --auth-enabled --auth-server-url http://localhost:9000
```

#### JWT Verification

```bash
PGPACER_AUTH_ENABLED=true
PGPACER_AUTH_TYPE=jwt
PGPACER_AUTH_SERVER_URL=https://auth.example.com
PGPACER_JWT_ISSUER=https://auth.example.com
```

Or via CLI:

```bash
pgpacer "postgresql://..." --auth-enabled --auth-type jwt \
  --auth-server-url https://auth.example.com \
  --jwt-issuer https://auth.example.com
```

### Claude Desktop Configuration with Authentication

#### Docker with Authentication

```json
{
  "mcpServers": {
    "postgres": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "PGPACER_DATABASE_URL",
        "-e",
        "PGPACER_AUTH_ENABLED",
        "-e",
        "PGPACER_AUTH_SERVER_URL",
        "pgpacer",
        "--access-mode=unrestricted"
      ],
      "env": {
        "PGPACER_DATABASE_URL": "postgresql://username:password@localhost:5432/dbname",
        "PGPACER_AUTH_ENABLED": "true",
        "PGPACER_AUTH_SERVER_URL": "http://host.docker.internal:9000"
      }
    }
  }
}
```

> **Note:** Use `host.docker.internal` (MacOS/Windows) or `172.17.0.1` (Linux) to reach an authorization server running on the host.

#### If you are using `pipx` or `uv`

When using the `stdio` transport with authentication, you must set `PGPACER_SERVER_URL` because the stdio transport does not have a natural HTTP endpoint, but the OAuth flow needs a server URL for resource validation.

```json
{
  "mcpServers": {
    "postgres": {
      "command": "pgpacer",
      "args": [
        "--access-mode=unrestricted",
        "--auth-enabled",
        "--auth-server-url", "http://localhost:9000"
      ],
      "env": {
        "PGPACER_DATABASE_URL": "postgresql://username:password@localhost:5432/dbname",
        "PGPACER_SERVER_URL": "http://localhost:8000"
      }
    }
  }
}
```

### Authentication with SSE or Streamable HTTP

When using network transports, enable authentication via environment variables or CLI arguments:

```bash
docker run -p 8000:8000 \
  -e PGPACER_DATABASE_URL=postgresql://username:password@localhost:5432/dbname \
  -e PGPACER_AUTH_ENABLED=true \
  -e PGPACER_AUTH_SERVER_URL=http://auth-server:9000 \
  pgpacer --access-mode=unrestricted --transport=sse
```

The server URL is auto-detected from the host and port configuration for SSE and Streamable HTTP transports.

### Authentication Environment Variables

| Variable | Description | Default |
| -------- | ----------- | ------- |
| `PGPACER_AUTH_ENABLED` | Enable OAuth authentication | `false` |
| `PGPACER_AUTH_TYPE` | Authentication type: `introspection` or `jwt` | `introspection` |
| `PGPACER_AUTH_SERVER_URL` | Authorization server base URL | `http://localhost:9000` |
| `PGPACER_AUTH_INTROSPECTION_ENDPOINT` | Token introspection endpoint URL | `{auth-server-url}/introspect` |
| `PGPACER_AUTH_REQUIRED_SCOPES` | Comma-separated required OAuth scopes | `mcp:postgres:access` |
| `PGPACER_AUTH_VALIDATE_RESOURCE` | Enable RFC 8707 resource validation | `false` |
| `PGPACER_AUTH_INTROSPECTION_CLIENT_ID` | Client ID for introspection endpoint auth | *(none)* |
| `PGPACER_AUTH_INTROSPECTION_CLIENT_SECRET` | Client secret for introspection endpoint auth | *(none)* |
| `PGPACER_JWT_JWKS_URL` | JWKS endpoint URL (JWT mode) | `{auth-server-url}/.well-known/jwks.json` |
| `PGPACER_JWT_ISSUER` | Expected JWT `iss` claim (required for JWT mode) | *(none)* |
| `PGPACER_JWT_AUDIENCE` | Expected JWT `aud` claim | *(server URL)* |
| `PGPACER_JWT_ALGORITHMS` | Comma-separated signing algorithms | `RS256` |
| `PGPACER_JWT_CLOCK_SKEW` | Clock skew tolerance in seconds | `30` |
| `PGPACER_JWT_JWKS_CACHE_TTL` | JWKS cache TTL in seconds | `300` |
| `PGPACER_SERVER_URL` | This server's URL (required for stdio with auth) | *(auto-detected for SSE/HTTP)* |

### Security Features

- **SSRF prevention**: Introspection and JWKS endpoint URLs are validated to only allow `https://` URLs or `localhost`.
- **SSL verification**: TLS certificate verification is enforced for all outbound requests.
- **Algorithm restrictions** (JWT): Only asymmetric algorithms (RS256, RS384, RS512, ES256, ES384, ES512) are accepted. Symmetric algorithms (`HS*`) and `none` are explicitly rejected.
- **JWKS caching** (JWT): Public keys are cached with a configurable TTL (default: 5 minutes) and rate-limited refresh on unknown key IDs.
- **RFC 8707 resource validation**: Optionally validates that the token's resource claim matches the MCP server URL.
- **Scope validation**: Requires tokens to have the configured scopes (default: `mcp:postgres:access`).
- **Golden query role switching**: The `execute_golden_query` tool accepts an optional `execute_as_role` parameter, applied with `SET LOCAL ROLE` so it lasts only for that query's transaction and never leaks to pooled connections. The connecting database user must be a member of the target role. The role name is quoted as an identifier (SQL-injection safe), but operators should ensure proper database grants to control which roles can be assumed. See [Knowledge Maps and Golden Queries](#knowledge-maps-and-golden-queries-pgvector) for the other golden query safeguards.

## Governance

pgpacer provides governance features to help organizations track and audit AI agent interactions with their databases.

### Purpose Logging

The purpose feature allows you to label each pgpacer instance with a static identifier that appears in PostgreSQL's `application_name` connection parameter. This enables database administrators to identify and audit connections by their intended use case.

#### Enabling Purpose Logging

Set the purpose via environment variable or CLI argument:

```bash
PGPACER_PURPOSE=credit-scoring
```

Or via CLI:

```bash
pgpacer "postgresql://..." --purpose credit-scoring
```

#### Format Requirements

The purpose value must be lowercase alphanumeric with hyphens only (e.g., `credit-scoring`, `fraud-detection`, `analytics-pipeline`). This constraint ensures consistent, parseable values in PostgreSQL logs and monitoring systems.

#### How It Works

When a purpose is configured, every database connection from this pgpacer instance sets `application_name` to `pgpacer:<purpose>` (e.g., `pgpacer:credit-scoring`). Database administrators can then:

- Filter `pg_stat_activity` by `application_name` to see active connections by purpose
- Configure PostgreSQL logging to include `application_name` for audit trails
- Set up monitoring alerts based on connection purposes

### Session Token Tracing

The tracing feature propagates session identifiers from MCP clients through to PostgreSQL, enabling end-to-end request tracing across your AI agent infrastructure.

#### Enabling Session Token Tracing

Enable tracing via environment variable or CLI argument:

```bash
PGPACER_TRACING=true
```

Or via CLI:

```bash
pgpacer "postgresql://..." --tracing
```

#### How It Works

When tracing is enabled, the MCP server extracts the session identifier from the `_meta.sessionId` field in MCP tool calls and sets `application_name` at the PostgreSQL session level using `SET` (outside any transaction, via autocommit). This ensures the session token is visible in `pg_stat_activity` both during query execution and on idle connections.

If no `_meta.sessionId` is provided by the MCP client, pgpacer generates a fallback UUID at startup and uses that instead. The debug log indicates the source: `source=_meta` when received from the client, `source=fallback` when auto-generated.

The `application_name` format is `pgpacer:<purpose>/<session-short>`, where session is truncated to 8 characters (like git short hashes):

- **Tracing only**: `pgpacer:_/91c11602`
- **Both purpose and tracing**: `pgpacer:credit-scoring/91c11602`

This format preserves the app identity (`pgpacer`) for monitoring tools while providing purpose and session correlation.

#### Visibility in PostgreSQL

| Surface | Token visible? | Notes |
|---|---|---|
| `pg_stat_activity` (active query) | Yes | Shows `pgpacer:<purpose>/<session-short>` with the running query |
| `pg_stat_activity` (idle connection) | Yes | Token persists after query completes |
| JSON logs (`log_destination=jsonlog`) | Yes | `application_name` field on every logged statement |
| CSV logs (`log_destination=csvlog`) | Yes | `application_name` is always a column |
| stderr logs | Only if `%a` is in `log_line_prefix` | Add `%a` to include `application_name` |

#### Connection Pool Behavior

pgpacer uses a connection pool (`min_size=1, max_size=5`). The session token is applied per-connection on the first tool call that uses it. Connections that have not yet executed a tool call (e.g. the initial pool health-check connection) will show the baseline `pgpacer:<purpose>` without the session token suffix. After a connection executes its first tool call, the token persists on that connection for all subsequent queries and remains visible when idle.

#### Use Cases

- **Request tracing**: Correlate slow queries in `pg_stat_activity` back to specific MCP sessions
- **Audit logging**: Track which AI agent session executed each database operation
- **Debugging**: Identify the source of problematic queries in multi-tenant environments
- **Audit trail**: JSON/CSV logs record which session ran each query, useful for compliance reviews and incident investigation

## Postgres Extension Installation (Optional)

To enable index tuning and comprehensive performance analysis you need to load the `pg_stat_statements` and `hypopg` extensions on your database.

- The `pg_stat_statements` extension allows pgpacer to analyze query execution statistics.
For example, this allows it to understand which queries are running slow or consuming significant resources.
- The `hypopg` extension allows pgpacer to simulate the behavior of the Postgres query planner after adding indexes.

### Installing extensions on AWS RDS, Azure SQL, or Google Cloud SQL

If your Postgres database is running on a cloud provider managed service, the `pg_stat_statements` and `hypopg` extensions should already be available on the system.
In this case, you can just run `CREATE EXTENSION` commands using a role with sufficient privileges:

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
```

### Installing extensions on self-managed Postgres

If you are managing your own Postgres installation, you may need to do additional work.
Before loading the `pg_stat_statements` extension you must ensure that it is listed in the `shared_preload_libraries` in the Postgres configuration file.
The `hypopg` extension may also require additional system-level installation (e.g., via your package manager) because it does not always ship with Postgres.

## Knowledge Maps and Golden Queries (pgvector)

These tools help an agent write correct SQL against an unfamiliar database:

- A **knowledge map** is a searchable map of your tables, views and columns. The agent can find the right objects by meaning ("where are customer emails?").
- A **golden query** is vetted, reusable business SQL ("monthly revenue", "customer by email") that the agent can discover and run instead of writing its own.

Vectors are stored and compared with the open-source [pgvector](https://github.com/pgvector/pgvector) extension; no proprietary extension is required. pgvector stores vectors but does not create them, so **pgpacer computes embeddings itself** using a configured embedding provider.

### Requirements

1. The pgvector extension installed in your database (`CREATE EXTENSION vector;`). Tested with pgvector 0.8.
2. An embedding provider configured on the server (see below). Without one, the semantic search tools return a configuration error and every other tool keeps working.

### Embedding Providers

| Provider | Use for | Notes |
| -------- | ------- | ----- |
| `openai` | Production | Any service implementing the OpenAI `POST /embeddings` API: OpenAI, Ollama, vLLM, Hugging Face Text Embeddings Inference (TEI), LM Studio, LiteLLM (for Azure, Bedrock, Vertex AI, ...). |
| `hash` | Tests, offline / air-gapped use | Built-in and deterministic, with no network access. It matches on shared words and identifiers only, not meaning. |

```bash
# OpenAI
PGPACER_EMBEDDING_PROVIDER=openai
PGPACER_EMBEDDING_MODEL=text-embedding-3-small
PGPACER_EMBEDDING_API_KEY=sk-...

# Self-hosted Ollama (no API key needed)
PGPACER_EMBEDDING_PROVIDER=openai
PGPACER_EMBEDDING_MODEL=nomic-embed-text
PGPACER_EMBEDDING_BASE_URL=http://localhost:11434/v1

# Hugging Face TEI running as a sibling container
PGPACER_EMBEDDING_PROVIDER=openai
PGPACER_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
PGPACER_EMBEDDING_BASE_URL=http://tei:80/v1
```

Each knowledge map records the model and vector size it was built with. Searching with a different model is rejected, because vectors from different models are not comparable. To switch models, recreate the knowledge map.

### Typical Workflow

1. `create_knowledge_map` with a name and the schemas to index (unrestricted mode). Table, view and column names, types, constraints and **comments** are embedded, so good `COMMENT ON` descriptions (see `add_comment_to_object`) directly improve search quality.
2. `search_knowledge_map` / `discover_context` to find relevant schema elements for a question.
3. `create_golden_query` to save vetted, parameterized business SQL; `search_golden_queries` and `execute_golden_query` to reuse it.
4. After schema changes, `get_knowledge_map_stats` reports how many entities are `pending`, and `refresh_knowledge_map` re-embeds only what changed. Knowledge maps are not updated automatically.

### Storage and Permissions

On the first `create_knowledge_map`, the server creates a `pgpacer` schema with three tables (`knowledge_maps`, `knowledge_map_entries`, `golden_queries`). The DDL is in [`src/pgpacer/semantics/schema.sql`](src/pgpacer/semantics/schema.sql). DBAs who prefer to provision it themselves can run that script; every statement is idempotent, and the server then needs no `CREATE` privilege.

A restricted-mode server can use a read-only role:

```sql
GRANT USAGE ON SCHEMA pgpacer TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA pgpacer TO mcp_readonly;
```

| Operation | Unrestricted | Restricted |
| --------- | ------------ | ---------- |
| `search_knowledge_map`, `list_knowledge_maps`, `get_knowledge_map_stats`, `discover_context`, `search_golden_queries`, `list_golden_queries`, `execute_golden_query` | ✅ | ✅ |
| `create_knowledge_map`, `refresh_knowledge_map`, `delete_knowledge_map` | ✅ | ❌ |
| `create_golden_query`, `delete_golden_query` | ✅ | Only with `ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED=true` |

### Golden Query Safeguards

Golden queries are parameterized SQL such as `SELECT * FROM sales.customers WHERE email = ${email}`. They are hardened as follows:

- **Bound parameters, never interpolation.** Each `${name}` compiles to a typed bind parameter (for example `($1::text)`), so values are sent separately from the SQL and parsed by PostgreSQL's own type input functions. `param_type` must come from a fixed allowlist: `text`, `varchar`, `integer`, `bigint`, `smallint`, `numeric`, `real`, `double precision`, `boolean`, `date`, `timestamp`, `timestamptz`, `time`, `interval`, `uuid`, `json`, `jsonb`, or a scalar type followed by `[]`.
- **Read-only SQL only.** The query must be a single `SELECT` (including `WITH ... SELECT` and `VALUES`). Data-modifying CTEs, `SELECT INTO`, row locks (`FOR UPDATE`) and multiple statements are rejected. This check runs when the golden query is created **and every time it runs**, so a row edited directly in the database cannot smuggle in a write.
- **READ ONLY transaction.** Execution runs inside `SET TRANSACTION READ ONLY`, so PostgreSQL itself blocks writes, including side effects such as `nextval()`.
- **Restricted mode.** The SQL must also pass the same `SafeSqlDriver` validation as `execute_sql`, including its function allowlist, and it runs with a server-side `statement_timeout`.
- **No privilege escalation.** Golden queries run with the caller's privileges (or `execute_as_role`), never with those of their author.

### Embedding Service Security

- The API key is read from `PGPACER_EMBEDDING_API_KEY` only (no CLI flag, so it never appears in process listings) and is never logged.
- The server refuses to send an API key over plain `http://` to anything other than `localhost`. Keyless `http://` endpoints (for example an internal Ollama or TEI container) are allowed with a warning, since schema metadata is then sent unencrypted.
- Redirects are not followed, responses are validated (count, dimensions, finite values), and transient failures (HTTP 429/5xx, network errors) are retried with backoff.
- **Data sent to the provider:** schema metadata (object names, types, comments), golden query descriptions and search queries. Table *data* is never sent. Use a self-hosted provider if this metadata must not leave your network.

### Migrating from AIDB

Earlier versions used EDB's proprietary AIDB extension, and with it AIDB's names: "semantic knowledge base" and "semantic alias". Those features are now **knowledge maps** and **golden queries**, and the tools are renamed accordingly:

| Previous tool | New tool |
| ------------- | -------- |
| *(SQL: `aidb.create_semantic_kb`)* | `create_knowledge_map` |
| *(SQL: `aidb.delete_semantic_kb`)* | `delete_knowledge_map` |
| `search_kb` | `search_knowledge_map` |
| `list_kbs` | `list_knowledge_maps` |
| `get_kb_stats` | `get_knowledge_map_stats` |
| `refresh_kb` | `refresh_knowledge_map` |
| `create_alias` | `create_golden_query` |
| `delete_alias` | `delete_golden_query` |
| `search_aliases` | `search_golden_queries` |
| `execute_alias` | `execute_golden_query` |
| `list_aliases` | `list_golden_queries` |
| `discover_context` | `discover_context` *(unchanged)* |

Other changes to be aware of:

- The `kb_name` parameter is now `map_name`. In `discover_context`, `include_aliases` is now `include_golden_queries`, and the `existing_aliases` result field is now `golden_queries`. Results otherwise keep their fields, and some gain extras such as `parameters` or `dimensions`.
- `ALLOW_ALIAS_WRITE_IN_RESTRICTED` is replaced by `ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED`. The old variable is ignored (with a warning at startup), so golden query writes stay blocked in restricted mode until the new one is set.
- Golden queries must be read-only `SELECT` queries (see the safeguards above).
- Knowledge maps are only synchronised by `refresh_knowledge_map`. There is no AIDB-style live mode, and `auto_processing` is always reported as `Disabled`.
- Existing AIDB knowledge bases and aliases are not migrated. After upgrading, configure an embedding provider, then recreate them with `create_knowledge_map` and `create_golden_query`.

## Usage Examples

### Get Database Health Overview

Ask:
> Check the health of my database and identify any issues.

### Analyze Slow Queries

Ask:
> What are the slowest queries in my database? And how can I speed them up?

### Get Recommendations On How To Speed Things Up

Ask:
> My app is slow. How can I make it faster?

### Generate Index Recommendations

Ask:
> Analyze my database workload and suggest indexes to improve performance.

### Optimize a Specific Query

Ask:
> Help me optimize this query: SELECT \* FROM orders JOIN customers ON orders.customer_id = customers.id WHERE orders.created_at > '2023-01-01';

## MCP Server API

The [MCP standard](https://modelcontextprotocol.io/) defines various types of endpoints: Tools, Resources, Prompts, and others.

pgpacer provides functionality via [MCP tools](https://modelcontextprotocol.io/docs/concepts/tools) alone.
We chose this approach because the [MCP client ecosystem](https://modelcontextprotocol.io/clients) has widespread support for MCP tools.
This contrasts with the approach of other Postgres MCP servers, including the [Reference Postgres MCP Server](https://github.com/modelcontextprotocol/servers/tree/main/src/postgres), which use [MCP resources](https://modelcontextprotocol.io/docs/concepts/resources) to expose schema information.

pgpacer Tools:

| Tool Name | Description |
|-----------|-------------|
| `list_schemas` | Lists all database schemas available in the PostgreSQL instance. |
| `list_objects` | Lists database objects (tables, views, sequences, extensions) within a specified schema. |
| `get_object_details` | Provides information about a specific database object, for example, a table's columns, constraints, and indexes. |
| `execute_sql` | Executes SQL statements on the database, with read-only limitations when connected in restricted mode. |
| `explain_query` | Gets the execution plan for a SQL query describing how PostgreSQL will process it and exposing the query planner's cost model. Can be invoked with hypothetical indexes to simulate the behavior after adding indexes. |
| `get_top_queries` | Reports the slowest SQL queries based on total execution time using `pg_stat_statements` data. |
| `analyze_workload_indexes` | Analyzes the database workload to identify resource-intensive queries, then recommends optimal indexes for them. |
| `analyze_query_indexes` | Analyzes a list of specific SQL queries (up to 10) and recommends optimal indexes for them. |
| `analyze_db_health` | Performs comprehensive health checks including: buffer cache hit rates, connection health, constraint validation, index health (duplicate/unused/invalid), sequence limits, and vacuum health. |
| `add_comment_to_object` | Adds a comment to a table, view, or column (allowed in restricted mode only if `ALLOW_COMMENT_IN_RESTRICTED=true`). |
| `remove_comment` | Remove the comment from a table, view, or column. |
| `create_knowledge_map` | Create a knowledge map: embed the tables, views and columns of the given schemas for natural-language search (unrestricted mode only). |
| `delete_knowledge_map` | Delete a knowledge map and its embeddings (unrestricted mode only). |
| `search_knowledge_map` | Search a knowledge map for tables, views and columns matching a natural language query. |
| `list_knowledge_maps` | List all available knowledge maps. |
| `get_knowledge_map_stats` | Get statistics about a knowledge map (entity counts, and how many entities are pending a refresh). |
| `refresh_knowledge_map` | Refresh a knowledge map to sync with current schema metadata, re-embedding only changed entities (unrestricted mode only). |
| `create_golden_query` | Create a golden query: vetted, reusable business SQL (a parameterized read-only SELECT) with a description for discovery. Requires `map_name` to select the embedding model so the query is searchable. Allowed in restricted mode only if `ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED=true`. |
| `delete_golden_query` | Delete a golden query (allowed in restricted mode only if `ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED=true`). |
| `search_golden_queries` | Search golden queries by natural language query using the embedding model of the given `map_name`. |
| `execute_golden_query` | Execute a golden query with parameter values in a read-only transaction. Supports optional `execute_as_role` for role-based access control (requires database grants). |
| `list_golden_queries` | List all available golden queries with name, description, query text, parameters, and parameter count. |
| `discover_context` | Discover schema metadata and golden queries relevant to a business question in a single call (for SQL generation). Combines `search_knowledge_map` and `search_golden_queries` with configurable similarity threshold and result limits. |

## Migrating from pg-airman-mcp

This project was previously named PG Airman MCP (`pg-airman-mcp`). The rename changes these names:

| Before | After |
| ------ | ----- |
| Package, CLI command and Docker image `pg-airman-mcp` | `pgpacer` |
| Python package `pg_airman_mcp` | `pgpacer` |
| Environment variables `AIRMAN_MCP_*` | `PGPACER_*` |
| `application_name` prefix `airman:` (purpose and tracing) | `pgpacer:` |
| Knowledge map storage schema `airman_semantic` | `pgpacer` |
| Built-in hash embedding model `airman-hash-v1` | `pgpacer-hash-v1` |
| Comment tagging restricted-mode queries `/* enterprisedb */` | `/* pgpacer */` |
| Name prefix of recommended indexes `enterprisedb_idx_` | `pgpacer_idx_` |

- **Environment variables keep working.** An `AIRMAN_MCP_*` variable is still used when its `PGPACER_*` counterpart is not set, and the server logs a deprecation warning naming the replacement. The old names are deprecated, so rename them when convenient.
- **Update monitoring that matches `application_name`.** Filters, dashboards and log alerts that look for `airman:` should look for `pgpacer:` instead.
- **Purpose labels are limited to 46 characters** (previously 47). `pgpacer:` is one character longer than `airman:`, and PostgreSQL limits `application_name` to 63 bytes.
- **Recreate knowledge maps and golden queries.** Ones stored in the old `airman_semantic` schema are not read. Recreate them with `create_knowledge_map` and `create_golden_query`, then remove the old schema with `DROP SCHEMA airman_semantic CASCADE`.

## Frequently Asked Questions

*How is pgpacer different from other Postgres MCP servers?*
There are many MCP servers that allow an AI agent to run queries against a Postgres database.
pgpacer does that too, but also adds tools for understanding and improving the performance of your Postgres database.
For example, it implements a version of the [Anytime Algorithm of Database Tuning Advisor for Microsoft SQL Server](https://www.microsoft.com/en-us/research/wp-content/uploads/2020/06/Anytime-Algorithm-of-Database-Tuning-Advisor-for-Microsoft-SQL-Server.pdf), a modern industrial-strength algorithm for automatic index tuning.

pgpacer complements generative AI by adding deterministic tools and classical optimization algorithms.
The combination is both reliable and flexible.

*Why are MCP tools needed when the LLM can reason, generate SQL, etc?*
LLMs are invaluable for tasks that involve ambiguity, reasoning, or natural language.
When compared to procedural code, however, they can be slow, expensive, non-deterministic, and sometimes produce unreliable results.
In the case of database tuning, we have well established algorithms, developed over decades, that are proven to work.
pgpacer lets you combine the best of both worlds by pairing LLMs with classical optimization algorithms and other procedural tools.

*How do you test pgpacer?*
Testing is critical to ensuring that pgpacer is reliable and accurate.
We are building out a suite of AI-generated adversarial workloads designed to challenge pgpacer and ensure it performs under a broad variety of scenarios.

*What Postgres versions are supported?*
Our testing presently focuses on Postgres 15, 16, 17, and 18.
We plan to support Postgres versions 14 through 18.

*How do I run the knowledge map and golden query integration tests?*
They run with the rest of the suite and start a `pgvector/pgvector` container automatically (Docker required).
They use the built-in `hash` embedding provider, so no embedding service or API key is needed.
To use an existing database instead, point `PGVECTOR_TEST_DSN` at one where the test user can create extensions and roles.
`PGVECTOR_TEST_IMAGE` overrides the container image:

```bash
uv run pytest tests/integration/semantics/ -v
PGVECTOR_TEST_DSN="postgresql://postgres@localhost:5432/postgres" uv run pytest tests/integration/semantics/ -v
```

*Who created this project?*
This project was created by [Crystal DBA](https://www.crystaldba.ai).
Then evaluated and decided to use by EDB as their main MCP Server. But then they decided to not to support the project any longer. I’ve been using it internally and it’s been stable and useful. I was sad to see the original team move onto other
pursuits, so I've forked the project and will continue to add features.

## Roadmap

*TBD*

You and your needs are a critical driver for what we build.
Tell us what you want to see by opening an [issue](https://github.com/aysebilgegunduz/pgpacer/) or a [pull request](https://github.com/aysebilgegunduz/pgpacer/pulls).

## Configuration Reference

All settings follow a three-tier priority: CLI arguments > environment variables > defaults.
Environment variables use the `PGPACER_` prefix.

### Server Settings

| Variable | CLI Argument | Description | Default |
| -------- | ------------ | ----------- | ------- |
| `PGPACER_DATABASE_URL` | `database_url` (positional) | PostgreSQL connection URL | *(required)* |
| `PGPACER_ACCESS_MODE` | `--access-mode` | SQL access mode: `unrestricted` or `restricted` | `unrestricted` |
| `PGPACER_TRANSPORT` | `--transport` | MCP transport: `stdio`, `sse`, or `streamable-http` | `stdio` |
| `PGPACER_SSE_HOST` | `--sse-host` | SSE server host | `localhost` |
| `PGPACER_SSE_PORT` | `--sse-port` | SSE server port | `8000` |
| `PGPACER_STREAMABLE_HTTP_HOST` | `--streamable-http-host` | Streamable HTTP server host | `localhost` |
| `PGPACER_STREAMABLE_HTTP_PORT` | `--streamable-http-port` | Streamable HTTP server port | `8001` |
| `PGPACER_DNS_REBINDING_PROTECTION` | `--dns-rebinding-protection` | Enable DNS rebinding protection | `false` |
| `PGPACER_ALLOWED_HOSTS` | `--allowed-hosts` | Comma-separated allowed Host header values | *(empty)* |
| `PGPACER_PURPOSE` | `--purpose` | Static purpose label for governance logging | *(none)* |
| `PGPACER_TRACING` | `--tracing` | Enable session token tracing via application_name | `false` |
| `PGPACER_ALLOWED_ORIGINS` | `--allowed-origins` | Comma-separated allowed Origin header values | *(empty)* |

### Authentication Settings

| Variable | CLI Argument | Description | Default |
| -------- | ------------ | ----------- | ------- |
| `PGPACER_AUTH_ENABLED` | `--auth-enabled` | Enable OAuth authentication | `false` |
| `PGPACER_AUTH_TYPE` | `--auth-type` | Authentication type: `introspection` or `jwt` | `introspection` |
| `PGPACER_AUTH_SERVER_URL` | `--auth-server-url` | Authorization server base URL | `http://localhost:9000` |
| `PGPACER_AUTH_INTROSPECTION_ENDPOINT` | `--auth-introspection-endpoint` | Token introspection endpoint URL | `{auth-server-url}/introspect` |
| `PGPACER_AUTH_REQUIRED_SCOPES` | `--auth-required-scopes` | Comma-separated required OAuth scopes | `mcp:postgres:access` |
| `PGPACER_AUTH_VALIDATE_RESOURCE` | `--auth-validate-resource` | Enable RFC 8707 resource validation | `false` |
| `PGPACER_AUTH_INTROSPECTION_CLIENT_ID` | `--auth-introspection-client-id` | Client ID for introspection endpoint auth | *(none)* |
| `PGPACER_AUTH_INTROSPECTION_CLIENT_SECRET` | `--auth-introspection-client-secret` | Client secret for introspection endpoint auth | *(none)* |
| `PGPACER_JWT_JWKS_URL` | `--jwt-jwks-url` | JWKS endpoint URL | `{auth-server-url}/.well-known/jwks.json` |
| `PGPACER_JWT_ISSUER` | `--jwt-issuer` | Expected JWT `iss` claim (required for JWT) | *(none)* |
| `PGPACER_JWT_AUDIENCE` | `--jwt-audience` | Expected JWT `aud` claim | *(server URL)* |
| `PGPACER_JWT_ALGORITHMS` | `--jwt-algorithms` | Comma-separated signing algorithms | `RS256` |
| `PGPACER_JWT_CLOCK_SKEW` | `--jwt-clock-skew` | Clock skew tolerance in seconds | `30` |
| `PGPACER_JWT_JWKS_CACHE_TTL` | `--jwt-jwks-cache-ttl` | JWKS cache TTL in seconds | `300` |
| `PGPACER_SERVER_URL` | *(env only)* | This server's URL (required for stdio with auth) | *(auto-detected for SSE/HTTP)* |

### Semantic Search Settings

| Variable | CLI Argument | Description | Default |
| -------- | ------------ | ----------- | ------- |
| `PGPACER_EMBEDDING_PROVIDER` | `--embedding-provider` | Embedding provider: `openai` (any OpenAI-compatible API) or `hash` (offline, lexical) | *(none: semantic search disabled)* |
| `PGPACER_EMBEDDING_MODEL` | `--embedding-model` | Embedding model name (required for `openai`) | *(none)* |
| `PGPACER_EMBEDDING_BASE_URL` | `--embedding-base-url` | OpenAI-compatible API base URL | `https://api.openai.com/v1` |
| `PGPACER_EMBEDDING_API_KEY` | *(env only)* | API key for the embedding service | *(none)* |
| `PGPACER_EMBEDDING_DIMENSIONS` | `--embedding-dimensions` | Vector size to request (e.g. for `text-embedding-3-*`); `hash` defaults to 512 | *(model native)* |
| `PGPACER_EMBEDDING_TIMEOUT` | *(env only)* | Embedding request timeout in seconds | `30` |
| `PGPACER_EMBEDDING_BATCH_SIZE` | *(env only)* | Maximum texts per embedding request | `32` |

### Other Settings

| Variable | Description | Default |
| -------- | ----------- | ------- |
| `ALLOW_COMMENT_IN_RESTRICTED` | Allow `COMMENT ON` writes in restricted mode | `true` |
| `ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED` | Allow golden query create/delete in restricted mode | `false` |
| `OPENAI_API_KEY` | OpenAI API key for experimental LLM-based index tuning | *(none)* |

## Technical Notes

This section includes a high-level overview of technical considerations that influenced the design of pgpacer.

### Index Tuning

Developers know that missing indexes are one of the most common causes of database performance issues.
Indexes provide access methods that allow Postgres to quickly locate data that is required to execute a query.
When tables are small, indexes make little difference, but as the size of the data grows, the difference in algorithmic complexity between a table scan and an index lookup becomes significant (typically *O*(*n*) vs *O*(*log* *n*), potentially more if joins on multiple tables are involved).

Generating suggested indexes in pgpacer proceeds in several stages:

1. *Identify SQL queries in need of tuning*.
    If you know you are having problems with a specific SQL query you can provide it.
    pgpacer can also analyze the workload to identify index tuning targets.
    To do this, it relies on the `pg_stat_statements` extension, which records the runtime and resource consumption of each query.

    A query is a candidate for index tuning if it is a top resource consumer, either on a per-execution basis or an aggregate.
    At present, we use execution time as a proxy for cumulative resource consumption, but it may also make sense to look at specific resources, e.g., the number of blocks accessed or the number of blocks read from disk.
    The `analyze_query_workload` tool focuses on slow queries, using the mean time per execution with thresholds for execution count and mean execution time.
    Agents may also call `get_top_queries`, which accepts a parameter for mean vs. total execution time, then passes these queries to `analyze_query_indexes` to get index recommendations.

    Sophisticated index tuning systems use "workload compression" to produce a representative subset of queries that reflects the characteristics of the workload as a whole, reducing the problem for downstream algorithms.
    pgpacer performs a limited form of workload compression by normalizing queries so that those generated from the same template appear as one.
    It weights each query equally, a simplification that works when the benefits to indexing are large.

2. *Generate candidate indexes*
    Once we have a list of SQL queries that we want to improve through indexing, we generate a list of indexes that we might want to add.
    To do this, we parse the SQL and identify any columns used in filters, joins, grouping, or sorting.

    To generate all possible indexes we need to consider combinations of these columns, because Postgres supports [multicolumn indexes](https://www.postgresql.org/docs/current/indexes-multicolumn.html).
    In the present implementation, we include only one permutation of each possible multicolumn index, which is selected at random.
    We make this simplification to reduce the search space because permutations often have equivalent performance.
    However, we hope to improve in this area.

3. *Search for the optimal index configuration*.
    Our objective is to find the combination of indexes that optimally balances the performance benefits against the costs of storing and maintaining those indexes.
    We estimate the performance improvement by using the "what if?" capabilities provided by the `hypopg` extension.
    This simulates how the Postgres query optimizer will execute a query after the addition of indexes, and reports changes based on the actual Postgres cost model.

    One challenge is that generating query plans generally requires knowledge of the specific parameter values used in the query.
    Query normalization, which is necessary to reduce the queries under consideration, removes parameter constants.
    Parameter values provided via bind variables are similarly not available to us.

    To address this problem, we produce realistic constants that we can provide as parameters by sampling from the table statistics.
    In version 16, Postgres added [generic explain plan functionality](https://www.postgresql.org/docs/current/sql-explain.html), but it has limitations, for example around `LIKE` clauses, which our implementation does not have.

    Search strategy is critical because evaluating all possible index combinations is feasible only in simple situations.
    This is what most sets apart various indexing approaches.
    Adapting the approach of Microsoft's Anytime algorithm, we employ a greedy search strategy, i.e., find the best one-index solution, then find the best index to add to that to produce a two-index solution.
    Our search terminates when the time budget is exhausted or when a round of exploration fails to produce any gains above the minimum improvement threshold of 10%.

4. *Cost-benefit analysis*.
    When posed with two indexing alternatives, one which produces better performance and one which requires more space, how do we decide which to choose?
    Traditionally, index advisors ask for a storage budget and optimize performance with respect to that storage budget.
    We also take a storage budget, but perform a cost-benefit analysis throughout the optimization.

    We frame this as the problem of selecting a point along the [Pareto front](https://en.wikipedia.org/wiki/Pareto_front)—the set of choices for which improving one quality metric necessarily worsens another.
    In an ideal world, we might want to assess the cost of the storage and the benefit of improved performance in monetary terms.
    However, there is a simpler and more practical approach: to look at the changes in relative terms.
    Most people would agree that a 100x performance improvement is worth it, even if the storage cost is 2x.
    In our implementation, we use a configurable parameter to set this threshold.
    By default, we require the change in the log (base 10) of the performance improvement to be 2x the difference in the log of the space cost.
    This works out to allowing a maximum 10x increase in space for a 100x performance improvement.

Our implementation is most closely related to the [Anytime Algorithm](https://www.microsoft.com/en-us/research/wp-content/uploads/2020/06/Anytime-Algorithm-of-Database-Tuning-Advisor-for-Microsoft-SQL-Server.pdf) found in Microsoft SQL Server.
Compared to [Dexter](https://github.com/ankane/dexter/), an automatic indexing tool for Postgres, we search a larger space and use different heuristics.
This allows us to generate better solutions at the cost of longer runtime.

We also show the work done in each round of the search, including a comparison of the query plans before and after the addition of each index.
This gives the LLM additional context that it can use when responding to the indexing recommendations.

### Experimental: Index Tuning by LLM

pgpacer includes an experimental index tuning feature based on [Optimization by LLM](https://arxiv.org/abs/2309.03409).
Instead of using heuristics to explore possible index configurations, we provide the database schema and query plans to an LLM and ask it to propose index configurations.
We then use `hypopg` to predict performance with the proposed indexes, then feed those results back into the LLM to produce a new set of suggestions.
We repeat this process until multiple rounds of iteration produce no further improvements.

Index optimization by LLM has advantages when the index search space is large, or when indexes with many columns need to be considered.
Like traditional search-based approaches, it relies on the accuracy of the `hypopg` performance predictions.

In order to perform index optimization by LLM, you must provide an OpenAI API key by setting the `OPENAI_API_KEY` environment variable.

### Database Health

Database health checks identify tuning opportunities and maintenance needs before they lead to critical issues.
In the present release, pgpacer adapts the database health checks directly from [PgHero](https://github.com/ankane/pghero).
We are working to fully validate these checks and may extend them in the future.

- *Index Health*. Looks for unused indexes, duplicate indexes, and indexes that are bloated. Bloated indexes make inefficient use of database pages.
  Postgres autovacuum cleans up index entries pointing to dead tuples, and marks the entries as reusable. However, it does not compact the index pages and, eventually, index pages may contain few live tuple references.
- *Buffer Cache Hit Rate*. Measures the proportion of database reads that are served from the buffer cache instead of disk.
  A low buffer cache hit rate must be investigated as it is often not cost-optimal and leads to degraded application performance.
- *Connection Health*. Checks the number of connections to the database and reports on their utilization.
  The biggest risk is running out of connections, but a high number of idle or blocked connections can also indicate issues.
- *Vacuum Health*. Vacuum is important for many reasons.
  A critical one is preventing transaction id wraparound, which can cause the database to stop accepting writes.
  The Postgres multi-version concurrency control (MVCC) mechanism requires a unique transaction id for each transaction.
  However, because Postgres uses a 32-bit signed integer for transaction ids, it needs to reuse transaction ids after a maximum of 2 billion transactions.
  To do this it "freezes" the transaction ids of historical transactions, setting them all to a special value that indicates distant past.
  When records first go to disk, they are written visibility for a range of transaction ids.
  Before re-using these transaction ids, Postgres must update any on-disk records, "freezing" them to remove the references to the transaction ids to be reused.
  This check looks for tables that require vacuuming to prevent transaction id wraparound.
- *Replication Health*. Checks replication health by monitoring lag between primary and replicas, verifying replication status, and tracking usage of replication slots.
- *Constraint Health*. During normal operation, Postgres rejects any transactions that would cause a constraint violation.
  However, invalid constraints may occur after loading data or in recovery scenarios. This check looks for any invalid constraints.
- *Sequence Health*. Looks for sequences that are at risk of exceeding their maximum value.

### Postgres Client Library

pgpacer uses [psycopg3](https://www.psycopg.org/) to connect to Postgres using asynchronous I/O.
Under the hood, psycopg3 uses the [libpq](https://www.postgresql.org/docs/current/libpq.html) library to connect to Postgres, providing access to the full Postgres feature set and an underlying implementation fully supported by the Postgres community.

Some other Python-based MCP servers use [asyncpg](https://github.com/MagicStack/asyncpg), which may simplify installation by eliminating the `libpq` dependency.
Asyncpg is also probably [faster](https://fernandoarteaga.dev/blog/psycopg-vs-asyncpg/) than psycopg3, but we have not validated this ourselves.
[Older benchmarks](https://gistpreview.github.io/?0ed296e93523831ea0918d42dd1258c2) report a larger performance gap, suggesting that the newer psycopg3 has closed the gap as it matures.

Balancing these considerations, we selected `psycopg3` over `asyncpg`.
We remain open to revising this decision in the future.

### Connection Configuration

Like the [Reference PostgreSQL MCP Server](https://github.com/modelcontextprotocol/servers/tree/main/src/postgres), pgpacer takes Postgres connection information at startup.
This is convenient for users who always connect to the same database but can be cumbersome when users switch databases.

An alternative approach, taken by [PG-MCP](https://github.com/stuzero/pg-mcp-server), is to provide connection details via MCP tool calls at the time of use.
This is more convenient for users who switch databases, and allows a single MCP server to simultaneously support multiple end-users.

There must be a better approach than either of these.
Both have security weaknesses—few MCP clients store the MCP server configuration securely (an exception is Goose), and credentials provided via MCP tools are passed through the LLM and stored in the chat history.
Both also have usability issues in some scenarios.

### Schema Information

The purpose of the schema information tool is to provide the calling AI agent with the information it needs to generate correct and performant SQL.
For example, suppose a user asks, "How many flights took off from San Francisco and landed in Paris during the past year?"
The AI agent needs to find the table that stores the flights, the columns that store the origin and destinations, and perhaps a table that maps between airport codes and airport locations.

*Why provide schema information tools when LLMs are generally capable of generating the SQL to retrieve this information from Postgres directly?*

Our experience using Claude indicates that the calling LLM is very good at generating SQL to explore the Postgres schema by querying the [Postgres system catalog](https://www.postgresql.org/docs/current/catalogs.html) and the [information schema](https://www.postgresql.org/docs/current/information-schema.html) (an ANSI-standardized database metadata view).
However, we do not know whether other LLMs do so as reliably and capably.

*Would it be better to provide schema information using [MCP resources](https://modelcontextprotocol.io/docs/concepts/resources) rather than [MCP tools](https://modelcontextprotocol.io/docs/concepts/tools)?*

The [Reference PostgreSQL MCP Server](https://github.com/modelcontextprotocol/servers/tree/main/src/postgres) uses resources to expose schema information rather than tools.
Navigating resources is similar to navigating a file system, so this approach is natural in many ways.
However, resource support is less widespread than tool support in the MCP client ecosystem (see [example clients](https://modelcontextprotocol.io/clients)).
In addition, while the MCP standard says that resources can be accessed by either AI agents or end-user humans, some clients only support human navigation of the resource tree.

### Protected SQL Execution

AI amplifies longstanding challenges of protecting databases from a range of threats, ranging from simple mistakes to sophisticated attacks by malicious actors.
Whether the threat is accidental or malicious, a similar security framework applies, with aims that fall into three categories: confidentiality, integrity, and availability.
The familiar tension between convenience and safety is also evident and pronounced.

pgpacer's protected SQL execution mode focuses on integrity.
In the context of MCP, we are most concerned with LLM-generated SQL causing damage—for example, unintended data modification or deletion, or other changes that might circumvent an organization's change management process.

The simplest way to provide integrity is to ensure that all SQL executed against the database is read-only.
One way to do this is by creating a database user with read-only access permissions.
While this is a good approach, many find this cumbersome in practice.
Postgres does not provide a way to place a connection or session into read-only mode, so pgpacer uses a more complex approach to ensure read-only SQL execution on top of a read-write connection.

pgpacer provides a read-only transaction mode that prevents data and schema modifications.
Like the [Reference PostgreSQL MCP Server](https://github.com/modelcontextprotocol/servers/tree/main/src/postgres), we use read-only transactions to provide protected SQL execution.

To make this mechanism robust, we need to ensure that the SQL does not somehow circumvent the read-only transaction mode, say by issuing a `COMMIT` or `ROLLBACK` statement and then beginning a new transaction.

For example, the LLM can circumvent the read-only transaction mode by issuing a `ROLLBACK` statement and then beginning a new transaction.
For example:

```sql
ROLLBACK; DROP TABLE users;
```

To prevent cases like this, we parse the SQL before execution using the [pglast](https://pglast.readthedocs.io/) library.
We reject any SQL that contains `commit` or `rollback` statements.
Helpfully, the popular Postgres stored procedure languages, including PL/pgSQL and PL/Python, do not allow for `COMMIT` or `ROLLBACK` statements.
If you have unsafe stored procedure languages enabled on your database, then our read-only protections could be circumvented.

At present, pgpacer provides two levels of protection for the database, one at either extreme of the convenience/safety spectrum.

- "Unrestricted" provides maximum flexibility. It is suitable for development environments where speed and flexibility are paramount, and where there is no need to protect valuable or sensitive data.
- "Restricted" provides a balance between flexibility and safety. It is suitable for production environments where the database is exposed to untrusted users, and where it is important to protect valuable or sensitive data.

Unrestricted mode aligns with the approach of [Cursor's auto-run mode](https://docs.cursor.com/chat/tools#auto-run), where the AI agent operates with limited human oversight or approvals.
We expect auto-run to be deployed in development environments where the consequences of mistakes are low, where databases do not contain valuable or sensitive data, and where they can be recreated or restored from backups when needed.

We designed restricted mode to be conservative, erring on the side of safety even though it may be inconvenient.
Restricted mode is limited to read-only operations, and we limit query execution time to prevent long-running queries from impacting system performance.
We may add measures in the future to make sure that restricted mode is safe to use with production databases.

## pgpacer Development

The instructions below are for developers who want to work on pgpacer, or users who prefer to install pgpacer from source.

### Local Development Setup

1. **Install uv**:

   ```bash
   curl -sSL https://astral.sh/uv/install.sh | sh
   ```

2. **Clone the repository**:

   ```bash
   git clone https://github.com/aysebilgegunduz/pgpacer.git
   cd pgpacer
   ```

3. **Install dependencies**:

   ```bash
   uv pip install -e .
   uv sync
   ```

4. **Run the server**:

   ```bash
   uv run pgpacer "postgres://user:password@localhost:5432/dbname"
   ```
