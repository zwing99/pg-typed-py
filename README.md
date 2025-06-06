# pg-typed-py

A Python tool for generating type-safe database bindings from SQL queries. This project automatically creates Python dataclasses and query functions from your SQL files, providing full type safety and IDE support for your database operations.

## Inspiration

This project is inspired by:
- **[pg-typed](https://github.com/adelsz/pgtyped)** - A TypeScript library that generates types from SQL queries
- **[pugsql](https://pugsql.org/)** - A Python library for organizing SQL files and executing queries, which influenced the approach of treating SQL files as first-class citizens

## Features

- Generate Python dataclasses from SQL query results
- Automatic type mapping from PostgreSQL types to Python types
- Support for UUID conversion and other complex types
- SQLAlchemy 2.0 compatible
- Automatic code formatting with ruff

## Quick Start

### 1. Start the Database

Use Docker Compose to start the PostgreSQL database:

```bash
docker compose run --rm bring-up
```

This will start a PostgreSQL instance with sample data and wait for it to be healthy.

### 2. Generate Type-Safe Bindings

Generate Python bindings from your SQL files:

```bash
uv run src/pg_typed_py/generate_bindings.py src/pg_typed_py/sample/get_users.sql postgresql+psycopg2://db_user:123@localhost:5432/postgres
```

This will:
- Read your SQL file (`get_users.sql`)
- Connect to the database to analyze the query
- Generate a corresponding Python file (`get_users.py`) with:
  - A dataclass representing the query result
  - A function to execute the query and return typed results
- Automatically format the generated code with ruff

### 3. Use the Generated Code

```python
from sqlalchemy import create_engine, Session
from src.pg_typed_py.sample.get_users import get_users

engine = create_engine("postgresql+psycopg2://db_user:123@localhost:5432/postgres")

with Session(engine) as session:
    users = get_users(session)
    for user in users:
        print(f"User {user.id}: {user.email}")
```

## How It Works

1. **SQL Analysis**: The tool connects to your database and analyzes your SQL query to determine:
   - The column names and types returned by the query
   - The parameter types required by the query

2. **Code Generation**: It generates:
   - A dataclass with properly typed fields for each column
   - A function that executes the query and returns a list of dataclass instances
   - Proper imports and type annotations

3. **Type Safety**: The generated code provides full type safety, including:
   - Automatic UUID conversion from strings
   - Proper datetime handling
   - IDE autocompletion and type checking

## Project Structure

```
src/pg_typed_py/
├── generate_bindings.py    # Main code generation tool
├── sample/
│   ├── get_users.sql      # Example SQL query
│   └── get_users.py       # Generated Python bindings
```

## Requirements

- Python 3.12+
- PostgreSQL database
- uv (for dependency management)
- Docker (for local development)

## Development

The project uses uv for dependency management. Install dependencies with:

```bash
uv sync
```

Run the code generator with:

```bash
uv run src/pg_typed_py/generate_bindings.py <sql_file> <database_url>
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
