import re
import subprocess
from typing import List
from dataclasses import dataclass
from sqlalchemy import create_engine, text
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
    cursor = connection.connection.cursor()
    # Use LIMIT 0 to avoid scanning table
    if not re.search(r"limit\s+\d+", sql_for_psycopg2, re.IGNORECASE):
        if sql_for_psycopg2.strip()[-1] == ";":
            sql_for_psycopg2 = sql_for_psycopg2.strip()[:-1]
        sql_for_psycopg2 += " LIMIT 0"
    cursor.execute(sql_for_psycopg2, params)
    desc = cursor.description
    return [(col.name, col.type_code) for col in desc]


def get_param_types(connection, sql: str, param_names: List[str]) -> dict:
    """
    Returns a dict mapping param name to Python type.
    """
    sql_for_psycopg2 = re.sub(r":\w+", "%s", sql)
    cursor = connection.connection.cursor()
    try:
        # Prepare with temp_plan and get param types
        cursor.execute(f"PREPARE temp_plan AS {sql_for_psycopg2}")
        cursor.execute(
            "SELECT parameter_types FROM pg_prepared_statements WHERE name = 'temp_plan'"
        )
        row = cursor.fetchone()
        if row:
            oids = row[0]
            oid_type_map = get_oid_type_map(connection)
            return {
                param: pg_to_python(oid_type_map.get(oid, "Any"))
                for param, oid in zip(param_names, oids)
            }
        return {param: "Any" for param in param_names}
    except Exception:
        # Fallback: default all to Any
        return {param: "Any" for param in param_names}
    finally:
        try:
            cursor.execute("DEALLOCATE temp_plan")
        except Exception:
            pass


def generate_dataclass(
    class_name: str, columns: List[tuple], oid_type_map: dict
) -> str:
    lines = [f"@dataclass", f"class {class_name}:"]
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
) -> str:
    params_signature = ", ".join(
        [f"{p}: {param_types.get(p, 'Any')}" for p in param_names]
    )
    params_dict = "{" + ", ".join([f'"{p}": {p}' for p in param_names]) + "}"
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
        sql = f.read().strip().rstrip(";")
    param_names = extract_params(sql)

    # Set up SQLAlchemy
    engine = create_engine(db_url, future=True)
    with engine.connect() as conn:
        oid_type_map = get_oid_type_map(conn)
        columns = get_query_result_columns(conn, sql, param_names)
        param_types = get_param_types(conn, sql, param_names)

    # Infer class and function names from SQL file name
    base_name = re.sub(r"\.sql$", "", sql_file_path.split("/")[-1])
    class_name = "".join([word.capitalize() for word in base_name.split("_")]) + "Row"
    func_name = base_name
    # Generate code
    imports_code = get_required_imports(columns, oid_type_map, param_types)
    dataclass_code = generate_dataclass(class_name, columns, oid_type_map)
    query_func_code = generate_query_function(
        func_name, class_name, sql, param_names, columns, oid_type_map, param_types
    )

    # Generate output file path
    output_file_path = re.sub(r"\.sql$", ".py", sql_file_path)

    # Write the generated code to the output file
    with open(output_file_path, "w") as f:
        f.write(imports_code)
        f.write("\n\n")
        f.write(dataclass_code)
        f.write(query_func_code)

    # Format the generated file with ruff
    try:
        subprocess.run(
            ["uv", "run", "ruff", "format", output_file_path],
            check=True,
            capture_output=True,
            cwd=".",
        )
        print(f"Generated and formatted Python bindings: {output_file_path}")
    except subprocess.CalledProcessError as e:
        print(f"Generated Python bindings: {output_file_path}")
        print(f"Warning: Failed to format with ruff: {e}")
    except FileNotFoundError:
        print(f"Generated Python bindings: {output_file_path}")
        print("Warning: uv or ruff not found - install ruff for automatic formatting")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python generate_types_sqlalchemy.py <sql_file> <db_url>")
        print(
            "Example: python generate_types_sqlalchemy.py get_users.sql postgresql://user:pass@localhost/dbname"
        )
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
