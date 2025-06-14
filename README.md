# pg-typed-py

A Python tool for generating type-safe database bindings from SQL queries. This project automatically creates Python dataclasses and query functions from your SQL files, providing full type safety and IDE support for your database operations.

## Inspiration

This project is inspired by:
- **[pg-typed](https://github.com/adelsz/pgtyped)** - A TypeScript library that generates types from SQL queries
- **[pugsql](https://pugsql.org/)** - A Python library for organizing SQL files and executing queries, which influenced the approach of treating SQL files as first-class citizens

## Features

- **Multiple queries per file**: Define multiple named queries in a single SQL file
- **Intelligent parameter typing**: Automatic type inference for query parameters based on context
- Generate Python dataclasses from SQL query results
- Automatic type mapping from PostgreSQL types to Python types
- Support for UUID conversion and other complex types
- SQLAlchemy 2.0 compatible
- Support for SELECT, INSERT, UPDATE, DELETE queries
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
uv run python -m pg_typed_py.generate_bindings src/pg_typed_py/sample/multi_queries.sql postgresql://db_user:password@localhost:5432/postgres
```

This will:
- Read your SQL file (`multi_queries.sql`)
- Connect to the database to analyze the queries
- Generate a corresponding Python file (`multi_queries.py`) with:
  - Dataclasses representing each query result
  - Functions to execute each query and return typed results
- Automatically format the generated code with ruff

## SQL File Formats

### Multiple Queries

You can define multiple named queries in a single SQL file using comment blocks:

```sql
/*
name=get_all_users
*/
SELECT * FROM users;

/*
name=get_user_by_email
*/
SELECT * FROM users WHERE email = :email;

/*
name=get_users_created_after
*/
SELECT id, email, created_at
FROM users
WHERE created_at > :created_after
ORDER BY created_at DESC;
```

Each named query will generate:
- A separate dataclass (e.g., `GetAllUsersRow`, `GetUserByEmailRow`)
- A separate function (e.g., `get_all_users()`, `get_user_by_email()`)
- Proper parameter typing based on context analysis

### Parameter Type Inference

The tool automatically infers parameter types based on how they're used in your SQL:

- **UUID types**: `WHERE id = :user_id` → `user_id: uuid.UUID`
- **String types**: `WHERE email = :email` → `email: str`
- **String patterns**: `WHERE email LIKE :pattern` → `pattern: str`
- **Datetime types**: `WHERE created_at > :since` → `since: datetime.datetime`

### Non-SELECT Queries

The tool also supports INSERT, UPDATE, and DELETE queries:

```sql
/*
name=update_user_email
*/
UPDATE users SET email = :new_email WHERE id = :user_id;
```

These generate functions that return `None` instead of dataclass lists.

### 3. Use the Generated Code

```python
from sqlalchemy import create_engine, Session
from datetime import datetime
from src.pg_typed_py.sample.multi_queries import (
    get_all_users,
    get_user_by_email,
    get_users_created_after
)

engine = create_engine("postgresql://db_user:password@localhost:5432/postgres")

with Session(engine) as session:
    # Get all users
    all_users = get_all_users(session)

    # Get user by email (parameter automatically typed as str)
    user = get_user_by_email(session, "john@example.com")

    # Get users created after a date (parameter automatically typed as datetime)
    recent_users = get_users_created_after(session, datetime(2024, 1, 1))
```

## How It Works

1. **SQL Analysis**: The tool connects to your database and analyzes your SQL queries to determine:
   - The column names and types returned by each query
   - The parameter types required by each query (with intelligent context-based inference)
   - Whether queries return data (SELECT) or perform operations (INSERT/UPDATE/DELETE)

2. **Multi-Query Parsing**: Supports multiple named queries in a single file:
   - Parses `/* name=query_name */` comments to identify individual queries
   - Generates separate functions and dataclasses for each named query
   - Maintains backward compatibility with single-query files

3. **Intelligent Parameter Typing**: Uses advanced heuristics to infer parameter types:
   - **Database schema analysis**: Queries column types from `information_schema`
   - **Context analysis**: Examines how parameters are used (equality, comparisons, LIKE, etc.)
   - **Pattern recognition**: Recognizes common patterns (IDs, emails, timestamps)
   - **Fallback safety**: Defaults to `Any` type when inference is uncertain

4. **Code Generation**: Generates clean, type-safe Python code:
   - Dataclasses with properly typed fields for SELECT queries
   - Functions that return `List[DataClass]` for SELECT or `None` for operations
   - Proper imports and type annotations
   - Automatic UUID string conversion handling

5. **Type Safety**: The generated code provides full type safety, including:
   - Automatic UUID conversion from strings
   - Proper datetime handling
   - IDE autocompletion and type checking
   - SQLAlchemy 2.0 compatibility

## Project Structure

```
src/pg_typed_py/
├── generate_bindings.py      # Main code generation tool
├── sample/
│   ├── multi_queries.sql     # Example multi-query file
│   ├── multi_queries.py      # Generated Python bindings (multiple)
│   ├── complex_queries.sql   # Example with various parameter types
│   └── complex_queries.py    # Generated Python bindings (complex)
```

## Generated Code Examples

**From a multi-query SQL file:**

```sql
/*
name=get_user_by_email
*/
SELECT * FROM users WHERE email = :email;

/*
name=get_users_created_after
*/
SELECT id, email, created_at
FROM users
WHERE created_at > :created_after;
```

**Generates:**

```python
@dataclass
class GetUserByEmailRow:
    id: uuid.UUID
    email: str
    created_at: datetime.datetime
    updated_at: datetime.datetime

def get_user_by_email(session, email: str) -> List[GetUserByEmailRow]:
    """Executes the query and returns results as dataclasses."""
    # ... implementation

@dataclass
class GetUsersCreatedAfterRow:
    id: uuid.UUID
    email: str
    created_at: datetime.datetime

def get_users_created_after(session, created_after: datetime.datetime) -> List[GetUsersCreatedAfterRow]:
    """Executes the query and returns results as dataclasses."""
    # ... implementation
```

## CLI Usage

The basic command structure is:

```bash
uv run python -m pg_typed_py.generate_bindings <sql_file> <database_url>
```

**Examples:**

```bash
# Generate from multi-query file
uv run python -m pg_typed_py.generate_bindings src/pg_typed_py/sample/multi_queries.sql postgresql://db_user:password@localhost:5432/postgres

# Generate from complex queries with various parameter types
uv run python -m pg_typed_py.generate_bindings src/pg_typed_py/sample/complex_queries.sql postgresql://db_user:password@localhost:5432/postgres

# Output shows number of functions generated
# Generated and formatted Python bindings: src/pg_typed_py/sample/multi_queries.py
# Generated 3 query function(s)
```

The tool will:
1. Parse your SQL file(s) for named queries
2. Connect to the database to analyze schema and types
3. Generate a `.py` file with the same name as your `.sql` file
4. Format the generated code with ruff (if available)

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
uv run python -m pg_typed_py.generate_bindings <sql_file> <database_url>
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
