from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from pg_typed_py.sample.complex_queries import get_users_count
from pg_typed_py.sample.get_users import get_all_users

engine: Engine = create_engine(
    "postgresql+psycopg2://db_user:123@localhost:5432/postgres"
)

# create a session
with Session(engine) as session:
    # execute the query
    users = get_all_users(session)
    for user in users:
        print(user.id, user.email, user.created_at, user.updated_at)
