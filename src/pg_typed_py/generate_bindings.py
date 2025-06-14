import re
import subprocess
from typing import List, Dict
from dataclasses import dataclass
from sqlalchemy import create_engine
import sys

# We'll use subprocess to call uv run ruff format

# Mapping Postgres types to Python types
PG_TO_PYTHON = {
    "int2": "int",
    "int4": "int",
    "int8": "int",
    "float4": "float",
    "float8": "float",
    "numeric": "float",
    "text": "str",
    "varchar": "str",
    "char": "str",
    "bpchar": "str",
    "bool": "bool",
    "date": "datetime.date",
    "timestamp": "datetime.datetime",
    "timestamptz": "datetime.datetime",
    "json": "dict",
    "jsonb": "dict",
    "uuid": "uuid.UUID",
    # Add more as needed
}


def extract_params(sql: str) -> List[str]:
    """Find :param parameters in SQL."""
    return sorted(set(re.findall(r":(\w+)", sql)))


def parse_multi_query_file(content: str) -> List[Dict]:
    """
    Parse a SQL file that may contain multiple queries with name= comments.
    Returns list of dicts with 'name', 'sql', and 'query_type' keys.
    """
    queries = []

    # Split by comment blocks that contain name=
    parts = re.split(r"(/\*.*?name\s*=\s*[\w_]+.*?\*/)", content, flags=re.DOTALL)

    current_name = None
    current_query_type = "multi"  # default

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Check if this part is a comment with name=
        name_match = re.search(r"/\*.*?name\s*=\s*([\w_]+).*?\*/", part, re.DOTALL)
        if name_match:
            current_name = name_match.group(1)
            # Check for query_type in the same comment block
            query_type_match = re.search(
                r"query_type\s*=\s*(single|multi)", part, re.IGNORECASE
            )
            current_query_type = (
                query_type_match.group(1).lower() if query_type_match else "multi"
            )
        else:
            # This is SQL content
            if current_name and part:
                # Clean up the SQL (remove trailing semicolons)
                sql = part.strip().rstrip(";")
                if sql:
                    queries.append(
                        {
                            "name": current_name,
                            "sql": sql,
                            "query_type": current_query_type,
                        }
                    )
                current_name = None
                current_query_type = "multi"  # reset to default

    # If no named queries found, treat the entire content as a single query
    # and derive name from filename (handled in main function)
    if not queries:
        sql = content.strip().rstrip(";")
        if sql:
            queries.append(
                {
                    "name": None,  # Will be set by main function
                    "sql": sql,
                    "query_type": "multi",  # default
                }
            )

    return queries


def get_oid_type_map(connection) -> dict:
    """Get a mapping of Postgres type OID to type name."""
    cursor = connection.connection.cursor()
    cursor.execute("SELECT oid, typname FROM pg_type")
    return {row[0]: row[1] for row in cursor.fetchall()}


def pg_to_python(pg_type: str) -> str:
    return PG_TO_PYTHON.get(pg_type, "Any")


def get_query_result_columns(
    connection, sql: str, param_names: List[str]
) -> List[tuple]:
    """
    Prepare and execute a dummy query to get result columns and their Postgres OIDs.
    Returns list of (name, oid)
    """
    sql_for_psycopg2 = re.sub(r":\w+", "%s", sql)
    params = [None] * len(param_names)

    try:
        cursor = connection.connection.cursor()

        # Check if this is a SELECT statement
        sql_trimmed = sql.strip().upper()
        if not sql_trimmed.startswith("SELECT"):
            # For non-SELECT statements, return empty columns
            # These queries don't return data, just execute for side effects
            return []

        # Use LIMIT 0 to avoid scanning table for SELECT statements
        if not re.search(r"limit\s+\d+", sql_for_psycopg2, re.IGNORECASE):
            if sql_for_psycopg2.strip()[-1] == ";":
                sql_for_psycopg2 = sql_for_psycopg2.strip()[:-1]
            sql_for_psycopg2 += " LIMIT 0"
        cursor.execute(sql_for_psycopg2, params)
        desc = cursor.description
        return [(col.name, col.type_code) for col in desc]
    except Exception:
        # If there's an error, rollback and try again
        connection.rollback()
        cursor = connection.connection.cursor()

        # For non-SELECT, just return empty
        if not sql.strip().upper().startswith("SELECT"):
            return []

        cursor.execute(sql_for_psycopg2, params)
        desc = cursor.description
        return [(col.name, col.type_code) for col in desc]


def get_param_types(connection, sql: str, param_names: List[str]) -> dict:
    """
    Returns a dict mapping param name to Python type.
    Uses context-aware type inference.
    """
    if not param_names:
        return {}

    # First try the prepared statement approach
    sql_for_psycopg2 = re.sub(r":\w+", "%s", sql)

    try:
        cursor = connection.connection.cursor()
        # Prepare with temp_plan and get param types
        cursor.execute(f"PREPARE temp_plan AS {sql_for_psycopg2}")
        cursor.execute(
            "SELECT parameter_types FROM pg_prepared_statements WHERE name = 'temp_plan'"
        )
        row = cursor.fetchone()
        if row and row[0]:  # Check if we got actual parameter types
            oids = row[0]
            oid_type_map = get_oid_type_map(connection)
            param_types = {
                param: pg_to_python(oid_type_map.get(oid, "Any"))
                for param, oid in zip(param_names, oids)
            }
            # If we got meaningful types (not all 'Any'), return them
            if any(t != "Any" for t in param_types.values()):
                return param_types
    except Exception:
        pass
    finally:
        try:
            cursor = connection.connection.cursor()
            cursor.execute("DEALLOCATE temp_plan")
        except Exception:
            pass

    # Fall back to context-based inference
    return infer_param_types_from_context(connection, sql, param_names)


def infer_param_types_from_context(
    connection, sql: str, param_names: List[str]
) -> dict:
    """
    Infer parameter types by analyzing SQL context and column types.
    """
    param_types = {}

    # Get table schema information
    cursor = connection.connection.cursor()

    for param in param_names:
        param_type = "Any"  # default

        # Look for context clues in the SQL
        param_pattern = f":{param}\\b"

        # Check if parameter is used in comparison with a column
        # Look for patterns like "column_name = :param" or "column_name > :param"
        comparison_patterns = [
            rf"(\w+)\s*[=<>!]+\s*{param_pattern}",
            rf"{param_pattern}\s*[=<>!]+\s*(\w+)",
            rf"(\w+)\s+(?:IN|in)\s*\([^)]*{param_pattern}[^)]*\)",
            rf"(\w+)\s+(?:LIKE|like|ILIKE|ilike)\s+{param_pattern}",
        ]

        for pattern in comparison_patterns:
            matches = re.finditer(pattern, sql, re.IGNORECASE)
            for match in matches:
                column_name = (
                    match.group(1)
                    if match.group(1) != param
                    else match.group(2)
                    if len(match.groups()) > 1
                    else None
                )
                if column_name and column_name != param:
                    # Try to get the column type from information_schema
                    try:
                        cursor.execute(
                            """
                            SELECT data_type, udt_name
                            FROM information_schema.columns
                            WHERE column_name = %s
                            LIMIT 1
                        """,
                            (column_name,),
                        )
                        result = cursor.fetchone()
                        if result:
                            data_type = (
                                result[1] if result[1] else result[0]
                            )  # prefer udt_name
                            param_type = pg_to_python(data_type)
                            break
                    except Exception:
                        continue
            if param_type != "Any":
                break

        # Special cases based on common patterns
        if param_type == "Any":
            # Check for common timestamp/date patterns
            if re.search(
                rf"(?:created_at|updated_at|timestamp|date)\s*[><=]\s*{param_pattern}",
                sql,
                re.IGNORECASE,
            ):
                param_type = "datetime.datetime"
            # Check for ID patterns
            elif re.search(rf"(?:id|uuid)\s*[=]\s*{param_pattern}", sql, re.IGNORECASE):
                param_type = "uuid.UUID"
            # Check for email patterns
            elif re.search(rf"email\s*[=]\s*{param_pattern}", sql, re.IGNORECASE):
                param_type = "str"
            # Check for LIKE patterns (usually strings)
            elif re.search(
                rf"\w+\s+(?:LIKE|ILIKE)\s+{param_pattern}", sql, re.IGNORECASE
            ):
                param_type = "str"

        param_types[param] = param_type

    return param_types


def generate_dataclass(
    class_name: str, columns: List[tuple], oid_type_map: dict
) -> str:
    lines = ["@dataclass", f"class {class_name}:"]
    for colname, oid in columns:
        pg_type = oid_type_map.get(oid, "Any")
        py_type = pg_to_python(pg_type)
        lines.append(f"    {colname}: {py_type}")
    if len(lines) == 2:
        lines.append("    pass  # No columns")
    return "\n".join(lines)


def get_required_imports(columns, oid_type_map, param_types):
    types_used = {pg_to_python(oid_type_map.get(oid, "Any")) for _, oid in columns}
    types_used.update(param_types.values())
    imports = [
        "from dataclasses import dataclass",
        "from typing import List, Any",
        "from sqlalchemy import text",
    ]
    if "uuid.UUID" in types_used:
        imports.append("import uuid")
    if any(t.startswith("datetime.") for t in types_used):
        imports.append("import datetime")
    return "\n".join(imports)


def generate_query_function(
    func_name: str,
    class_name: str,
    sql: str,
    param_names: List[str],
    columns: List[tuple],
    oid_type_map: dict,
    param_types: dict,
    query_type: str = "multi",
) -> str:
    params_signature = ", ".join(
        [f"{p}: {param_types.get(p, 'Any')}" for p in param_names]
    )
    params_dict = "{" + ", ".join([f'"{p}": {p}' for p in param_names]) + "}"

    # Check if this query returns data (has columns)
    if not columns:
        # For non-SELECT queries (INSERT, UPDATE, DELETE, etc.)
        return f"""
def {func_name}(session, {params_signature}) -> None:
    \"\"\"Executes the query.\"\"\"
    session.execute(
        text({repr(sql)}),
        {params_dict}
    )
"""

    # For single query type with only one column, return the scalar value
    if query_type == "single" and len(columns) == 1:
        col_name, oid = columns[0]
        pg_type = oid_type_map.get(oid, "Any")
        py_type = pg_to_python(pg_type)

        if py_type == "uuid.UUID":
            return f"""
def {func_name}(session, {params_signature}) -> {py_type}:
    \"\"\"Executes the query and returns a single value.\"\"\"
    result = session.execute(
        text({repr(sql)}),
        {params_dict}
    )
    row = result.fetchone()
    if row is None:
        raise ValueError("Query returned no results")
    value = row[0]
    if isinstance(value, str):
        return uuid.UUID(value)
    return value
"""
        else:
            return f"""
def {func_name}(session, {params_signature}) -> {py_type}:
    \"\"\"Executes the query and returns a single value.\"\"\"
    result = session.execute(
        text({repr(sql)}),
        {params_dict}
    )
    row = result.fetchone()
    if row is None:
        raise ValueError("Query returned no results")
    return row[0]
"""

    # For single query type with multiple columns, return single row
    if query_type == "single":
        uuid_columns = [
            colname
            for colname, oid in columns
            if pg_to_python(oid_type_map.get(oid, "Any")) == "uuid.UUID"
        ]

        if uuid_columns:
            conversion = "    data = row._asdict()\n"
            for c in uuid_columns:
                conversion += f"    if isinstance(data['{c}'], str):\n        data['{c}'] = uuid.UUID(data['{c}'])\n"
            conversion += f"    return {class_name}(**data)\n"
            return f"""
def {func_name}(session, {params_signature}) -> {class_name}:
    \"\"\"Executes the query and returns a single result as dataclass.\"\"\"
    result = session.execute(
        text({repr(sql)}),
        {params_dict}
    )
    row = result.fetchone()
    if row is None:
        raise ValueError("Query returned no results")
{conversion}"""
        else:
            return f"""
def {func_name}(session, {params_signature}) -> {class_name}:
    \"\"\"Executes the query and returns a single result as dataclass.\"\"\"
    result = session.execute(
        text({repr(sql)}),
        {params_dict}
    )
    row = result.fetchone()
    if row is None:
        raise ValueError("Query returned no results")
    return {class_name}(**row._asdict())
"""

    # Multi query type (default behavior)
    uuid_columns = [
        colname
        for colname, oid in columns
        if pg_to_python(oid_type_map.get(oid, "Any")) == "uuid.UUID"
    ]
    conversion = ""
    if uuid_columns:
        conversion += "        data = row._asdict()\n"
        for c in uuid_columns:
            conversion += f"        if isinstance(data['{c}'], str):\n            data['{c}'] = uuid.UUID(data['{c}'])\n"
        conversion += f"        rows.append({class_name}(**data))\n"
        return f"""
def {func_name}(session, {params_signature}) -> List[{class_name}]:
    \"\"\"Executes the query and returns results as dataclasses.\"\"\"
    result = session.execute(
        text({repr(sql)}),
        {params_dict}
    )
    rows = []
    for row in result:
{conversion}    return rows
"""
    else:
        return f"""
def {func_name}(session, {params_signature}) -> List[{class_name}]:
    \"\"\"Executes the query and returns results as dataclasses.\"\"\"
    result = session.execute(
        text({repr(sql)}),
        {params_dict}
    )
    return [{class_name}(**row._asdict()) for row in result]
"""


def main(sql_file_path: str, db_url: str):
    # Read SQL file
    with open(sql_file_path) as f:
        content = f.read()

    # Parse multiple queries from the file
    queries = parse_multi_query_file(content)

    if not queries:
        print("No queries found in the file")
        return

    # Set up SQLAlchemy
    engine = create_engine(db_url, future=True)

    all_code_parts = []
    all_imports = set()

    # Infer base name from SQL file name for fallback
    base_name = re.sub(r"\.sql$", "", sql_file_path.split("/")[-1])

    # Get type map once
    with engine.connect() as conn:
        oid_type_map = get_oid_type_map(conn)

        for i, query in enumerate(queries):
            sql = query["sql"]
            query_name = query["name"]
            query_type = query.get("query_type", "multi")

            # If no name provided, use filename or numbered suffix
            if not query_name:
                if len(queries) == 1:
                    query_name = base_name
                else:
                    query_name = f"{base_name}_{i + 1}"

            param_names = extract_params(sql)

            # Use fresh connection for each query to avoid transaction issues
            try:
                columns = get_query_result_columns(conn, sql, param_names)
                param_types = get_param_types(conn, sql, param_names)
            except Exception:
                # If there's still an issue, try with a fresh connection
                with engine.connect() as fresh_conn:
                    oid_type_map = get_oid_type_map(fresh_conn)  # refresh this too
                    columns = get_query_result_columns(fresh_conn, sql, param_names)
                    param_types = get_param_types(fresh_conn, sql, param_names)

            # Generate class and function names
            class_name = (
                "".join([word.capitalize() for word in query_name.split("_")]) + "Row"
            )
            func_name = query_name

            # Generate code parts
            imports_needed = get_required_imports(columns, oid_type_map, param_types)
            for imp in imports_needed.split("\n"):
                if imp.strip():
                    all_imports.add(imp.strip())

            # Generate dataclass if needed
            # - Skip dataclass for non-SELECT queries (no columns)
            # - Skip dataclass for single-type queries with only one column
            dataclass_code = ""
            if columns and not (query_type == "single" and len(columns) == 1):
                dataclass_code = generate_dataclass(class_name, columns, oid_type_map)

            query_func_code = generate_query_function(
                func_name,
                class_name,
                sql,
                param_names,
                columns,
                oid_type_map,
                param_types,
                query_type,
            )

            all_code_parts.append((dataclass_code, query_func_code))

    # Generate output file path
    output_file_path = re.sub(r"\.sql$", ".py", sql_file_path)

    # Write the generated code to the output file
    with open(output_file_path, "w") as f:
        # Write imports
        f.write("\n".join(sorted(all_imports)))
        f.write("\n\n")

        # Write all dataclasses and functions
        for dataclass_code, query_func_code in all_code_parts:
            if dataclass_code:  # Only write dataclass if it exists
                f.write(dataclass_code)
                f.write("\n")
            f.write(query_func_code)
            f.write("\n")

    # Format the generated file with ruff
    try:
        subprocess.run(
            ["uv", "run", "ruff", "format", output_file_path],
            check=True,
            capture_output=True,
            cwd=".",
        )
        print(f"Generated and formatted Python bindings: {output_file_path}")
        print(f"Generated {len(queries)} query function(s)")
    except subprocess.CalledProcessError as e:
        print(f"Generated Python bindings: {output_file_path}")
        print(f"Generated {len(queries)} query function(s)")
        print(f"Warning: Failed to format with ruff: {e}")
    except FileNotFoundError:
        print(f"Generated Python bindings: {output_file_path}")
        print(f"Generated {len(queries)} query function(s)")
        print("Warning: uv or ruff not found - install ruff for automatic formatting")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python generate_types_sqlalchemy.py <sql_file> <db_url>")
        print(
            "Example: python generate_types_sqlalchemy.py get_users.sql postgresql://user:pass@localhost/dbname"
        )
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
