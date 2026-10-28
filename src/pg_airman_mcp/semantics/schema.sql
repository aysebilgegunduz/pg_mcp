-- pg-airman-mcp semantic layer storage (pgvector backend).
--
-- The server applies this script automatically the first time a knowledge
-- map is created in unrestricted mode. DBAs who prefer to provision it
-- themselves can run it manually; every statement is idempotent.
--
-- Requires the pgvector extension:  CREATE EXTENSION vector;
--
-- Embedding columns use the unconstrained `vector` type because each
-- knowledge map may use a different model. The server enforces that every
-- row of a knowledge map has the dimensions recorded on that knowledge map.

CREATE SCHEMA IF NOT EXISTS airman_semantic;

COMMENT ON SCHEMA airman_semantic IS
    'pg-airman-mcp knowledge maps and golden queries (pgvector)';

CREATE TABLE IF NOT EXISTS airman_semantic.knowledge_maps (
    name         text PRIMARY KEY,
    model_name   text NOT NULL,
    dimensions   integer NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
    schemas      text[] NOT NULL CHECK (cardinality(schemas) > 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    refreshed_at timestamptz
);

CREATE TABLE IF NOT EXISTS airman_semantic.knowledge_map_entries (
    map_name      text NOT NULL
                  REFERENCES airman_semantic.knowledge_maps (name) ON DELETE CASCADE,
    schema_name   text NOT NULL,
    relation_name text NOT NULL,
    -- '' for Table/View rows. The empty string is never a valid identifier,
    -- and keeping the column NOT NULL lets it take part in the primary key.
    column_name   text NOT NULL DEFAULT '',
    entity_type   text NOT NULL CHECK (entity_type IN ('Table', 'View', 'Column')),
    definition    text NOT NULL,
    comment       text,
    content_hash  text NOT NULL,
    embedding     vector NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (map_name, schema_name, relation_name, column_name)
);

CREATE TABLE IF NOT EXISTS airman_semantic.golden_queries (
    name        text PRIMARY KEY,
    description text NOT NULL,
    query_text  text NOT NULL,
    parameters  jsonb NOT NULL DEFAULT '[]'::jsonb
                CHECK (jsonb_typeof(parameters) = 'array'),
    model_name  text NOT NULL,
    dimensions  integer NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
    embedding   vector NOT NULL,
    created_by  text NOT NULL DEFAULT current_user,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS golden_queries_model_idx
    ON airman_semantic.golden_queries (model_name, dimensions);
