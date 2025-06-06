CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

create table if not exists "users" (
    "id" uuid primary key default uuid_generate_v4(),
    "email" text not null unique,
    "created_at" timestamp with time zone default now(),
    "updated_at" timestamp with time zone default now()
);

-- Generate 10 fake users
insert into "users" ("email")
select 'user_' || i || '@example.com'
from generate_series(1, 10) as i
on conflict do nothing;
