"""Database helpers shared by the semantic integration tests."""

from pgpacer.sql import SqlDriver


async def run_sql(driver: SqlDriver, *statements: str) -> None:
    for statement in statements:
        await driver.execute_query(statement)  # type: ignore[arg-type]


CUSTOMER_SCHEMA_SQL = [
    """CREATE TABLE {schema}.customers (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) NOT NULL,
        first_name VARCHAR(100),
        last_name VARCHAR(100)
    )""",
    "COMMENT ON TABLE {schema}.customers IS 'Customer information table'",
    "COMMENT ON COLUMN {schema}.customers.email IS 'Customer email address'",
    """CREATE TABLE {schema}.orders (
        id SERIAL PRIMARY KEY,
        customer_id INTEGER REFERENCES {schema}.customers(id),
        total_amount NUMERIC(10, 2) NOT NULL,
        status VARCHAR(50) DEFAULT 'pending'
    )""",
    "COMMENT ON TABLE {schema}.orders IS 'Customer orders table'",
    """INSERT INTO {schema}.customers (email, first_name, last_name) VALUES
        ('alice@example.com', 'Alice', 'Smith'),
        ('bob@example.com', 'Bob', 'Jones')""",
    """INSERT INTO {schema}.orders (customer_id, total_amount, status) VALUES
        (1, 100.00, 'completed'),
        (1, 50.00, 'pending'),
        (2, 200.00, 'completed')""",
]


async def create_customer_schema(driver: SqlDriver, schema: str) -> None:
    await run_sql(
        driver,
        f"DROP SCHEMA IF EXISTS {schema} CASCADE",
        f"CREATE SCHEMA {schema}",
        *(sql.format(schema=schema) for sql in CUSTOMER_SCHEMA_SQL),
    )
